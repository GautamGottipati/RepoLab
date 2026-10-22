"""Questions, the approval gate, and results.

``POST /experiments`` accepts any question. A worker triages it first --
conceptual, about the repository, or testable -- and only a testable question,
on a repository with something real to test it against, becomes an experiment.
The others are answered from repository evidence and official documentation,
and come back with status ``answered``. The record is the same either way, so a
client polls one URL whatever the question turns out to be.

The gate is enforced **here**, server-side, not in the client and not in a
prompt. ``POST /experiments`` designs an experiment and stops; execution is only
ever queued by ``POST /experiments/{id}/approve``, and only from
``awaiting_approval``. An approval that arrives twice, or for an experiment that
was never presented, is a 409 rather than a second run.

Nothing slow happens in a handler. Designing an experiment means cloning and
investigating a repository, and running one means minutes of Spark, so both are
queued and watched through the existing job event stream -- which is why an
experiment owns a job id.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api import deps
from api.deps import get_session
from api.routes.jobs import stream_events
from repolab.db.experiments import (
    KIND_CLI,
    KIND_PENDING,
    get_experiment,
    list_experiments,
    load_any_experiment,
    save_any_experiment,
)
from repolab.db.jobs import create_job
from repolab.db.models import ExperimentRecord
from repolab.events import EventType, JobStatus
from repolab.experiments.models import ExperimentStatus
from repolab.ingest.url import InvalidRepoUrlError, validate_repo_url

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/experiments", tags=["experiments"])

#: Upper bound on opt-in parallelism. Two Spark variants already saturate a
#: typical development machine, so anything higher measures contention; the cap
#: exists so a client cannot request a number that would make every timing
#: meaningless.
MAX_CONCURRENCY = 4


class AskQuestion(BaseModel):
    """A question to answer by experiment rather than by assertion."""

    repo_url: str = Field(description="Public https repository URL")
    question: str = Field(min_length=8, max_length=500)
    concurrency: int = Field(
        default=1,
        ge=1,
        le=MAX_CONCURRENCY,
        description="Variants to run at once. Above 1, every timing is marked contended.",
    )
    rows: int | None = Field(default=None, gt=0, description="Dataset rows, for a quicker run")


class ExperimentCreated(BaseModel):
    experiment_id: str
    job_id: str
    status: ExperimentStatus
    question: str
    events_url: str
    approve_url: str
    reject_url: str


class ExperimentState(BaseModel):
    """Everything a client needs to render a question at any stage."""

    experiment_id: str
    job_id: str | None
    status: ExperimentStatus
    kind: str = "spark"
    """``pending`` until triaged; then ``answer``, ``spark`` or ``cli``."""

    triage: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    """What the answer or experiment rests on, including cqlsh's verbatim output."""

    question: str
    repo_url: str
    commit_sha: str

    awaiting_approval: bool
    plan: dict[str, Any] | None = None
    rejection: dict[str, Any] | None = None
    statistics: dict[str, Any] | None = None
    reproducibility: dict[str, Any] | None = None
    variant_runs: int = 0
    measurements: int = 0
    explanation_ready: bool = False
    explanation_error: str | None = None
    """Set when a model was configured but could not produce an explanation."""

    error: str | None = None


class ApprovalDecision(BaseModel):
    approved_by: str | None = Field(default=None, max_length=255)


class ApprovalResult(BaseModel):
    experiment_id: str
    status: ExperimentStatus
    job_id: str | None = None
    events_url: str | None = None
    message: str


def _state(record: ExperimentRecord) -> ExperimentState:
    experiment_status = ExperimentStatus(record.status)
    return ExperimentState(
        experiment_id=record.id,
        job_id=record.job_id,
        status=experiment_status,
        kind=record.kind or "spark",
        triage=record.triage,
        answer=record.answer,
        evidence=record.evidence if record.kind != "spark" else None,
        question=record.question,
        repo_url=record.repo_url,
        commit_sha=record.commit_sha,
        awaiting_approval=experiment_status is ExperimentStatus.AWAITING_APPROVAL,
        plan=record.plan,
        rejection=record.rejection,
        statistics=record.statistics,
        reproducibility=record.reproducibility,
        variant_runs=len(record.runs or []) if record.kind == KIND_CLI else len(record.variants),
        measurements=(
            len((record.evidence or {}).get("measurements", []))
            if record.kind == KIND_CLI
            else len(record.measurements)
        ),
        explanation_ready=record.explanation is not None,
        explanation_error=record.explanation_error,
        error=record.error,
    )


@router.post("", response_model=ExperimentCreated, status_code=status.HTTP_202_ACCEPTED)
def ask(
    payload: AskQuestion,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> ExperimentCreated:
    """Triage a question, then answer it or design an experiment. Returns 202.

    Nothing runs yet either way. The id is allocated now so the client can poll
    while the question is being triaged and the answer or design worked out.
    """
    import uuid

    try:
        repo_url = validate_repo_url(payload.repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    experiment_id = str(uuid.uuid4())
    job = create_job(session, repo_url.normalized, kind="experiment_design")
    session.add(
        ExperimentRecord(
            id=experiment_id,
            job_id=job.id,
            question=payload.question,
            repo_url=repo_url.normalized,
            status=ExperimentStatus.PROPOSED.value,
            kind=KIND_PENDING,
        )
    )
    session.commit()

    from repolab.analytics import AnalyticsEventType, DbAnalyticsWriter
    from repolab.analytics.events import AnalyticsEvent

    DbAnalyticsWriter(lambda: deps.session_factory()()).record(
        AnalyticsEvent(
            type=AnalyticsEventType.QUESTION_ASKED,
            job_id=job.id,
            repo_url=repo_url.normalized,
            properties={"experiment_id": experiment_id},
        )
    )

    try:
        from worker.jobs import enqueue_experiment_proposal

        enqueue_experiment_proposal(
            job.id,
            experiment_id,
            repo_url.normalized,
            payload.question,
            concurrency=payload.concurrency,
            rows=payload.rows,
        )
    except Exception as exc:  # noqa: BLE001 - reported; the rows survive
        log.warning("experiments.enqueue_failed", experiment_id=experiment_id, error=str(exc))

    response.headers["Location"] = f"/experiments/{experiment_id}"
    return ExperimentCreated(
        experiment_id=experiment_id,
        job_id=job.id,
        status=ExperimentStatus.PROPOSED,
        question=payload.question,
        events_url=f"/experiments/{experiment_id}/events",
        approve_url=f"/experiments/{experiment_id}/approve",
        reject_url=f"/experiments/{experiment_id}/reject",
    )


@router.get("", response_model=list[ExperimentState])
def index(
    session: Annotated[Session, Depends(get_session)],
    repo_url: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ExperimentState]:
    """Experiments, newest first."""
    records = list_experiments(session, repo_url=repo_url, limit=limit)
    return [_state(record) for record in records]


@router.get("/{experiment_id}", response_model=ExperimentState)
def experiment_state(
    experiment_id: str, session: Annotated[Session, Depends(get_session)]
) -> ExperimentState:
    """The experiment, including the plan awaiting approval."""
    return _state(_require(session, experiment_id))


@router.post("/{experiment_id}/approve", response_model=ApprovalResult)
def approve(
    experiment_id: str,
    payload: ApprovalDecision,
    session: Annotated[Session, Depends(get_session)],
) -> ApprovalResult:
    """Approve the experiment and queue its execution.

    Only from ``awaiting_approval``. Anything else is 409: approving twice must
    not run the variants twice, and approving something never presented must not
    run anything at all.
    """
    record = _require(session, experiment_id)
    experiment = load_any_experiment(session, experiment_id)
    if experiment is None or experiment.plan is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"experiment {experiment_id} has no plan to approve (status {record.status})",
        )

    if experiment.status is not ExperimentStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"experiment {experiment_id} is {experiment.status.value}, not awaiting approval",
        )

    experiment.approve(by=payload.approved_by)

    # Execution is a new job: the design job finished when it asked for approval,
    # and the run needs its own event stream and timeout.
    run_job = create_job(session, experiment.repo_url, kind="experiment_run")
    experiment.job_id = run_job.id

    save_any_experiment(session, experiment)
    session.commit()

    _emit_decision(run_job.id, experiment_id, approved=True, by=payload.approved_by)

    try:
        from worker.jobs import enqueue_experiment_run

        enqueue_experiment_run(run_job.id, experiment_id)
    except Exception as exc:  # noqa: BLE001 - reported; approval is recorded
        log.warning("experiments.run_enqueue_failed", experiment_id=experiment_id, error=str(exc))

    return ApprovalResult(
        experiment_id=experiment_id,
        status=experiment.status,
        job_id=run_job.id,
        events_url=f"/experiments/{experiment_id}/events",
        message=f"approved: {experiment.plan.summary()}",
    )


@router.post("/{experiment_id}/reject", response_model=ApprovalResult)
def reject(
    experiment_id: str,
    payload: ApprovalDecision,
    session: Annotated[Session, Depends(get_session)],
) -> ApprovalResult:
    """Decline the experiment. Nothing runs, and nothing can be run later."""
    record = _require(session, experiment_id)
    experiment = load_any_experiment(session, experiment_id)
    if experiment is None or experiment.status is not ExperimentStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"experiment {experiment_id} is {record.status}, not awaiting approval",
        )

    experiment.reject(by=payload.approved_by)

    save_any_experiment(session, experiment)
    session.commit()

    if experiment.job_id is not None:
        _emit_decision(experiment.job_id, experiment_id, approved=False, by=payload.approved_by)

    return ApprovalResult(
        experiment_id=experiment_id,
        status=experiment.status,
        message="rejected; no variants will run",
    )


@router.get("/{experiment_id}/explanation")
def explanation(
    experiment_id: str, session: Annotated[Session, Depends(get_session)]
) -> dict[str, Any]:
    """The explanation, with each claim's kind and what could not be corroborated.

    404 until the experiment has run: there is no honest explanation to give
    before there are measurements.
    """
    record = _require(session, experiment_id)
    if record.explanation is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"experiment {experiment_id} has no explanation yet (status {record.status})",
        )
    return {
        "experiment_id": record.id,
        "kind": record.kind or "spark",
        "question": record.question,
        "explanation": record.explanation,
        "statistics": record.statistics,
        "evidence": record.evidence,
        "reproducibility": record.reproducibility,
    }


@router.get("/{experiment_id}/events")
async def events(
    experiment_id: str,
    request: Request,
    after: Annotated[int, Query(ge=0, description="Resume after this sequence")] = 0,
    follow: Annotated[bool, Query(description="Keep the connection open")] = True,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream this experiment's progress.

    Delegates to the job stream, which already replays history and then follows
    without a gap, so an experiment needs no second streaming implementation.
    """
    with deps.session_factory()() as session:
        record = get_experiment(session, experiment_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no experiment {experiment_id}")
        job_id = record.job_id

    if job_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"experiment {experiment_id} has no event stream yet",
        )

    return await stream_events(
        job_id, request, after=after, follow=follow, last_event_id=last_event_id
    )


def _require(session: Session, experiment_id: str) -> ExperimentRecord:
    record = get_experiment(session, experiment_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no experiment {experiment_id}")
    return record


def _emit_decision(job_id: str, experiment_id: str, *, approved: bool, by: str | None) -> None:
    """Record the decision on the experiment's event stream.

    Written from the API rather than the worker, because the decision happens
    here: a client watching the stream sees the approval the moment it is given
    rather than when a worker picks the job up.
    """
    from repolab.db.jobs import get_job
    from repolab.events import DbEventEmitter

    factory = deps.session_factory()
    with factory() as session:
        job = get_job(session, job_id)
        start_seq = job.last_seq if job is not None else 0

    emitter = DbEventEmitter(job_id, lambda: factory(), start_seq=start_seq)
    emitter.emit(
        EventType.APPROVAL_RECEIVED,
        "experiment approved" if approved else "experiment rejected",
        step="experiment",
        data={"experiment_id": experiment_id, "approved": approved, "approved_by": by},
    )
    if not approved:
        # A rejected experiment's stream is finished; say so rather than leaving a
        # client reconnecting forever.
        with factory() as session:
            from repolab.db.jobs import mark_finished

            mark_finished(session, job_id, status=JobStatus.COMPLETED)
            session.commit()
