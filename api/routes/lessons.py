"""Lessons: fetching one, checking a step, and asking what just happened.

    GET  /lessons                    the lesson for a repository
    POST /lessons                    ask for one to be generated (202, worker)
    GET  /lessons/{id}/progress      how far a learner has got
    POST /lessons/{id}/check         decide whether a step worked
    POST /lessons/{id}/explain       "What Just Happened?"

Three things are deliberate here.

**Checking is deterministic first.** ``/check`` runs the step's declared
expectation against the real output in Python. Nano is consulted only when that
cannot reach a verdict, and the response says which decided -- so a learner can
tell a check from an opinion.

**Nothing is checked against invented output.** The handler does not take the
learner's word for what happened: it reads the command and its output from the
terminal session's own history, which is what the container actually printed.

**Explanations are verified.** ``/explain`` asks Super and then checks every
claim against the output, the cited files and the retrieved pages, returning
what could not be corroborated alongside the answer rather than instead of it.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_session, session_factory
from api.routes.cli import manager
from repolab.analytics import Analytics, AnalyticsEventType, DbAnalyticsWriter
from repolab.config import get_settings
from repolab.db.builds import load_builds
from repolab.db.jobs import create_job
from repolab.db.lessons import (
    load_lesson,
    load_lesson_by_id,
    load_progress,
    save_lesson,
    save_progress,
)
from repolab.db.store import load_latest_ingestion, load_manifest
from repolab.events import JobStatus
from repolab.experiences.cli.session import CommandRecord
from repolab.ingest.url import InvalidRepoUrlError, validate_repo_url
from repolab.lessons.cassandra import cassandra_lesson, teaches_cassandra
from repolab.lessons.explain import (
    VerifiedWhatHappened,
    build_explain_input,
    explain_command,
    explainable,
)
from repolab.lessons.minilab_lesson import SimulatedLesson, simulated_lesson
from repolab.lessons.models import Lesson, LessonProgress, LessonStep
from repolab.lessons.validation import check_step, resolve_with_model

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/lessons", tags=["lessons"])


class LessonView(BaseModel):
    """A lesson, and where it came from."""

    lesson: Lesson
    stored: bool
    """False when this is RepoLab's own lesson, served because none was generated."""

    explainable_steps: list[int] = Field(default_factory=list)
    """Steps with a "What Just Happened?" worth asking for."""

    simulated: SimulatedLesson | None = None
    """The Mini Lab rendering, when a Mini Lab is all that could be produced.

    Present instead of a terminal, never beside one: if this is set there is no
    server, nothing is executed, and the steps say so individually.
    """


class GenerateLesson(BaseModel):
    repo_url: str = Field(description="Public https repository URL")


class LessonGenerating(BaseModel):
    job_id: str
    status: JobStatus
    repo_url: str
    events_url: str
    lesson_url: str


class CheckStep(BaseModel):
    """A step to judge, identified by what the learner ran."""

    step: int = Field(ge=1)
    session_id: str = Field(description="The terminal session the statement ran in")
    sequence: int | None = Field(
        default=None,
        description="Which command in that session's history; the latest if omitted",
    )


class StepResult(BaseModel):
    """The verdict, and what it was reached from."""

    step: int
    passed: bool
    decided_by: str
    conclusive: bool
    reason: str
    command: str
    output: str
    completed: list[int] = Field(default_factory=list)
    lesson_complete: bool = False
    next_step: int | None = None
    hint: str = ""
    """Shown when the step did not pass. The step's own hint, not a generated one."""


class ExplainStep(BaseModel):
    step: int = Field(ge=1)
    session_id: str
    sequence: int | None = None


class ExplanationView(BaseModel):
    explanation: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    unsupported: list[dict[str, Any]] = Field(default_factory=list)
    grounded: bool = False
    available: bool = False
    reason: str = ""
    """Why there is no explanation, when there is none."""


def _lesson_for(session: Session, repo_url: str) -> tuple[Lesson, bool]:
    """The stored lesson, or RepoLab's own -- stored on the way out.

    A learner arriving before the generator has finished gets the deterministic
    eight-step lesson, which is a complete lesson rather than a placeholder. It
    is persisted here so it has an identity: a step cannot be checked against a
    lesson nothing can look up, and progress cannot be recorded against one.

    This is a write from a GET, which is worth being explicit about. It is one
    insert of a lesson RepoLab generated in memory -- no model, no clone,
    nothing slow -- and it is idempotent, because the next call finds the row.
    The returned flag says whether the lesson *predated* this call.
    """
    ingestion = load_latest_ingestion(session, repo_url)
    commit_sha = ingestion.commit_sha if ingestion is not None else ""

    stored = load_lesson(session, repo_url, commit_sha)
    if stored is not None:
        return stored, True

    # RepoLab's own lesson teaches CQL through cqlsh, so it is only served for a
    # repository whose evidence says that is what it has. Handing it to another
    # command-line repository would not be a thin lesson but a false one.
    manifest = load_manifest(session, repo_url)
    if manifest is None or not teaches_cassandra(manifest):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no lesson has been generated for this repository yet, and RepoLab has no "
            "built-in lesson for it; request one with POST /lessons",
        )

    own = cassandra_lesson(repo_url, commit_sha)
    record = save_lesson(session, own)
    session.commit()
    return own.model_copy(update={"id": record.id}), False


@router.get("", response_model=LessonView)
def read_lesson(
    session: Annotated[Session, Depends(get_session)],
    repo_url: Annotated[str, Query(description="Repository to fetch the lesson for")],
) -> LessonView:
    """The lesson for a repository."""
    try:
        url = validate_repo_url(repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    lesson, stored = _lesson_for(session, url.normalized)
    return LessonView(
        lesson=lesson,
        stored=stored,
        explainable_steps=[step.number for step in lesson.steps if explainable(step)],
        simulated=_simulated_for(session, url.normalized, lesson),
    )


def _simulated_for(session: Session, repo_url: str, lesson: Lesson) -> SimulatedLesson | None:
    """The Mini Lab rendering, when a Mini Lab is the best that exists.

    The lab's own entries are not stored with its artifact, so every step here
    says it requires a real environment. That is the truthful rendering rather
    than a degraded one: inventing what a statement would have printed is the
    exact failure the Mini Lab rules exist to prevent.
    """
    ingestion = load_latest_ingestion(session, repo_url)
    if ingestion is None:
        return None

    artifacts = load_builds(session, repo_url, ingestion.commit_sha)
    best = artifacts[0] if artifacts else None
    if best is None or not best.simulated:
        return None
    return simulated_lesson(repo_url, lesson.commit_sha)


@router.post("", response_model=LessonGenerating, status_code=status.HTTP_202_ACCEPTED)
def generate(
    payload: GenerateLesson,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> LessonGenerating:
    """Ask for a lesson to be written. Returns 202; it is generated in a worker."""
    try:
        url = validate_repo_url(payload.repo_url)
    except InvalidRepoUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    job = create_job(session, url.normalized, kind="lesson")
    session.commit()

    try:
        from worker.jobs import enqueue_lesson_generation

        enqueue_lesson_generation(job.id, url.normalized)
    except Exception as exc:  # noqa: BLE001 - reported; the job row survives
        log.warning("lessons.enqueue_failed", job_id=job.id, error=str(exc))

    response.headers["Location"] = f"/lessons?repo_url={url.normalized}"
    return LessonGenerating(
        job_id=job.id,
        status=JobStatus.QUEUED,
        repo_url=url.normalized,
        events_url=f"/jobs/{job.id}/events",
        lesson_url=f"/lessons?repo_url={url.normalized}",
    )


@router.get("/{lesson_id}/progress", response_model=LessonProgress)
def read_progress(
    lesson_id: str,
    session: Annotated[Session, Depends(get_session)],
    session_id: Annotated[str, Query(description="Terminal session")],
) -> LessonProgress:
    """How far this learner has got. Empty when they have not started."""
    return load_progress(session, lesson_id, session_id)


@router.post("/{lesson_id}/check", response_model=StepResult)
async def check(
    lesson_id: str,
    payload: CheckStep,
    session: Annotated[Session, Depends(get_session)],
) -> StepResult:
    """Decide whether a step worked, from what the container actually printed."""
    lesson, step = _require_step(session, lesson_id, payload.step)
    record = _require_command(payload.session_id, payload.sequence)

    verdict = check_step(step, record.command, record.output, record.exit_code)
    verdict = await resolve_with_model(step, record.command, record.output, verdict, _router())

    progress = load_progress(session, lesson.id, payload.session_id)
    first_attempt = not progress.attempts
    progress.record(verdict.to_attempt(step.number, record.command, record.output))
    finished_now = progress.is_complete(lesson) and progress.finished_at is None
    if finished_now:
        progress.finish()
    save_progress(session, progress)
    session.commit()

    # Recorded here because this is where it is known: the first checked step is
    # a lesson begun, and the step that completes the last one finishes it.
    analytics = Analytics(DbAnalyticsWriter(session_factory()), repo_url=lesson.repo_url)
    if first_attempt:
        analytics.record(
            AnalyticsEventType.LESSON_STARTED,
            lesson_id=lesson.id,
            generated_by=lesson.generated_by,
            steps=lesson.step_count,
        )
    if finished_now:
        analytics.record(
            AnalyticsEventType.LESSON_COMPLETED,
            lesson_id=lesson.id,
            attempts=len(progress.attempts),
        )
    if not verdict.passed and step.hint:
        analytics.record(
            AnalyticsEventType.HINT_SHOWN,
            lesson_id=lesson.id,
            step=step.number,
            decided_by=verdict.decided_by,
        )

    following = progress.next_step(lesson)
    return StepResult(
        step=step.number,
        passed=verdict.passed,
        decided_by=verdict.decided_by,
        conclusive=verdict.conclusive,
        reason=verdict.reason,
        command=record.command,
        output=record.output,
        completed=list(progress.completed),
        lesson_complete=progress.is_complete(lesson),
        next_step=following.number if following is not None else None,
        hint="" if verdict.passed else step.hint,
    )


@router.post("/{lesson_id}/explain", response_model=ExplanationView)
async def explain(
    lesson_id: str,
    payload: ExplainStep,
    session: Annotated[Session, Depends(get_session)],
) -> ExplanationView:
    """What the server did internally, grounded in output, code and documentation."""
    _, step = _require_step(session, lesson_id, payload.step)
    record = _require_command(payload.session_id, payload.sequence)

    router_instance = _router()
    if router_instance is None:
        return ExplanationView(
            available=False,
            reason=(
                "No model is configured, so RepoLab will not explain this. "
                "The step's own context describes what the statement does."
            ),
        )

    terminal = manager().get(payload.session_id)
    version = terminal.artifact.version or "" if terminal is not None else ""

    try:
        verified: VerifiedWhatHappened = await explain_command(
            router_instance,
            build_explain_input(
                step,
                record.command,
                record.output,
                exit_code=record.exit_code,
                version=version,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - reported to the learner, never hidden
        log.warning("lessons.explain_failed", lesson=lesson_id, error=str(exc))
        return ExplanationView(
            available=False, reason=f"the explanation could not be produced: {exc}"
        )

    return ExplanationView(
        explanation=verified.explanation.model_dump(mode="json"),
        evidence=verified.evidence.model_dump(mode="json"),
        unsupported=[item.model_dump(mode="json") for item in verified.unsupported],
        grounded=verified.grounded,
        available=True,
    )


def _require_step(session: Session, lesson_id: str, number: int) -> tuple[Lesson, LessonStep]:
    lesson = load_lesson_by_id(session, lesson_id)
    if lesson is None:
        # The deterministic lesson is served with its own generated id, which is
        # not in the table. Saying so is better than a bare 404.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"no stored lesson {lesson_id}; request one with POST /lessons",
        )
    step = lesson.step(number)
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"lesson has no step {number}")
    return lesson, step


def _require_command(session_id: str, sequence: int | None) -> CommandRecord:
    """The real command record from the terminal's history.

    Deliberately not taken from the request: a learner's claim about what they
    ran is not evidence, and a step must not be passed on output nobody executed.
    """
    terminal = manager().get(session_id)
    if terminal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id}")
    if not terminal.history:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing has been run in this session yet")

    if sequence is None:
        return terminal.history[-1]

    found = next((item for item in terminal.history if item.sequence == sequence), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no command {sequence} in this session")
    return found


def _router() -> object | None:
    """The model router, or ``None`` when no model is configured.

    Checking is deterministic without one and explanations are simply
    unavailable, which is said plainly rather than filled in.
    """
    settings = get_settings()
    if settings.nebius_api_key is None or not any(settings.configured_models.values()):
        return None

    from repolab.analytics.model_telemetry import db_model_telemetry
    from repolab.llm import build_router

    # The API records its own model calls too: a "What Just Happened?" is as much
    # a cost as an investigation, and a dashboard that counted only the worker's
    # calls would understate it.
    return build_router(settings, telemetry=db_model_telemetry(session_factory()))
