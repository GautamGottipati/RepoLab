"""Background tasks for Ask RepoLab and the experiment loop.

Two tasks, one per side of the approval gate:

``propose_experiment``  triages the question; answers it from evidence, or
                        clones, investigates, designs, validates, and stops --
                        emitting ``approval_required`` and going no further
``run_experiment``      runs the approved variants, computes the statistics and
                        asks for the explanation

Keeping them separate is what makes the gate real. The second task is only ever
enqueued by an approval, so a worker cannot drift from proposing into executing,
and a restart between them leaves an experiment waiting for a human rather than
half-run.

Each task re-clones the repository. The second one **verifies the commit** it got
is the one that was approved: if the default branch has moved, the experiment
fails rather than quietly measuring different code than the user agreed to.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractContextManager
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.analytics import Analytics, AnalyticsEventType, DbAnalyticsWriter
from repolab.config import Settings, get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.builds import load_builds
from repolab.db.experiments import (
    KIND_CLI,
    get_experiment,
    load_cli_experiment,
    load_experiment,
    save_answer,
    save_cli_experiment,
    save_experiment,
    save_triage,
)
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.db.store import load_latest_ingestion, load_manifest
from repolab.events import DbEventEmitter, EventType, JobStatus
from repolab.experiences.spark.driver import SparkRunConfig
from repolab.experiments import ExperimentAgent, ExperimentBudget
from repolab.experiments.cli.agent import CliExperimentAgent
from repolab.experiments.cli.models import CliExperiment
from repolab.experiments.experiment import Experiment
from repolab.failures import describe, explain
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.url import validate_repo_url
from repolab.questions import ExperimentBackend, Route, Triage, triage
from repolab.questions.answer import AnswerAgent
from repolab.questions.triage import cli_artifact

log = structlog.get_logger(__name__)

STEP_LABEL = "experiment"

#: Pinned: the event-log field names were verified against this Spark version.
DEFAULT_IMAGE = os.environ.get("REPOLAB_SPARK_IMAGE", "apache/spark:3.5.1-python3")


class CommitMovedError(RuntimeError):
    """The repository's HEAD is no longer the commit the user approved."""


def propose_experiment(
    job_id: str,
    experiment_id: str,
    repo_url: str,
    question: str,
    concurrency: int = 1,
    rows: int | None = None,
) -> str:
    """Triage the question; answer it, or design an experiment and stop at the gate."""
    context = _TaskContext(job_id)
    with context.factory() as session:
        mark_running(session, job_id)
        session.commit()

    try:
        asyncio.run(_propose(context, experiment_id, repo_url, question, concurrency, rows))
    except Exception as exc:
        context.fail(describe(exc), explain(exc).summary)
        raise
    finally:
        context.close()

    return experiment_id


def run_experiment(job_id: str, experiment_id: str, rows: int | None = None) -> str:
    """Execute an approved experiment.

    Raises:
        PermissionError: the experiment is not approved. The executor enforces
            this independently; the check here is so the job fails before cloning.
    """
    context = _TaskContext(job_id)
    try:
        experiment = asyncio.run(_run(context, experiment_id, rows))
    except Exception as exc:
        context.fail(describe(exc), explain(exc).summary)
        raise
    finally:
        context.close()

    return experiment.id


# ---------------------------------------------------------------------------


class _TaskContext:
    """The per-task plumbing both tasks need: engine, sessions, emitter, analytics."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.settings: Settings = get_settings()
        self.engine = build_engine(self.settings)
        self.factory: sessionmaker[Session] = build_session_factory(self.engine)
        self.emitter = DbEventEmitter(job_id, self._session_factory, start_seq=self._resume_seq())
        self.analytics = Analytics(DbAnalyticsWriter(self._session_factory), job_id=job_id)

    def _session_factory(self) -> AbstractContextManager[Session]:
        return session_scope(self.engine)

    def _resume_seq(self) -> int:
        with self.factory() as session:
            job = get_job(session, self.job_id)
            return job.last_seq if job is not None else 0

    def fail(self, detail: str, message: str) -> None:
        self.emitter.emit(EventType.FAILED, f"{message}: {detail}", step=STEP_LABEL)
        with self.factory() as session:
            mark_finished(session, self.job_id, status=JobStatus.FAILED, error=detail)
            session.commit()
        log.warning("experiment.failed", job_id=self.job_id, error=detail)

    def finish(self, status: JobStatus) -> None:
        with self.factory() as session:
            mark_finished(session, self.job_id, status=status)
            session.commit()

    def save(self, experiment: Experiment | CliExperiment) -> None:
        with self.factory() as session:
            if isinstance(experiment, CliExperiment):
                save_cli_experiment(session, experiment)
            else:
                save_experiment(session, experiment)
            session.commit()

    def close(self) -> None:
        self.engine.dispose()


def _providers(settings: Settings, session_factory: object | None = None):
    """The optional model router and research provider.

    Both optional: without credentials the agent falls back to a deterministic
    sweep and reports the measurements without a narrative, which is still a real
    experiment.
    """
    router = None
    research = None
    if settings.nebius_api_key is not None and any(settings.configured_models.values()):
        from repolab.analytics.model_telemetry import db_model_telemetry
        from repolab.llm import build_router

        router = build_router(
            settings,
            telemetry=db_model_telemetry(session_factory),  # type: ignore[arg-type]
        )
    if settings.tavily_api_key is not None:
        from repolab.research.tavily import TavilyResearchProvider

        research = TavilyResearchProvider(api_key=settings.tavily_api_key.get_secret_value())
    return router, research


async def _propose(
    context: _TaskContext,
    experiment_id: str,
    repo_url: str,
    question: str,
    concurrency: int,
    rows: int | None,
) -> None:
    url = validate_repo_url(repo_url)
    router, research = _providers(context.settings, context.factory)

    with context.factory() as session:
        manifest = load_manifest(session, url.normalized)
        ingestion = load_latest_ingestion(session, url.normalized)
        artifacts = load_builds(session, url.normalized, ingestion.commit_sha) if ingestion else []

    context.emitter.emit(EventType.STEP_STARTED, "understanding the question", step="triage")
    decision = await triage(
        question,
        manifest=manifest,
        artifacts=artifacts,
        router=router,
        design_available=router is not None,
    )
    with context.factory() as session:
        save_triage(session, experiment_id, decision)
        session.commit()
    context.emitter.emit(
        EventType.STEP_DONE,
        _triage_message(decision),
        step="triage",
        data=decision.model_dump(mode="json"),
    )

    context.emitter.emit(
        EventType.STEP_STARTED,
        f"cloning {url.slug} to investigate the question",
        step="clone",
        data={"repo": url.slug},
    )
    with TemporaryClone(url, limits=DEFAULT_LIMITS) as clone:
        context.emitter.emit(
            EventType.STEP_DONE,
            f"cloned at {clone.commit_sha[:12]}",
            step="clone",
            data={"commit_sha": clone.commit_sha},
        )

        if decision.route is Route.ANSWER:
            agent = AnswerAgent(
                repo_url=url.normalized,
                commit_sha=clone.commit_sha,
                repository_root=Path(clone.path),
                manifest=manifest,
                router=router,
                research=research,
                emitter=context.emitter,
            )
            result = await agent.answer(
                question, kind=decision.kind, downgraded=decision.downgraded
            )
            with context.factory() as session:
                save_answer(session, experiment_id, result, commit_sha=clone.commit_sha)
                session.commit()
            context.emitter.emit(
                EventType.STEP_DONE,
                "answered" if result.answer is not None else "evidence gathered; no answer text",
                step="answer",
                data={"experiment_id": experiment_id, "grounded": _grounded(result)},
            )
            context.finish(JobStatus.COMPLETED)
            return

        experiment: Experiment | CliExperiment
        if decision.backend is ExperimentBackend.CLI:
            artifact = cli_artifact(artifacts)
            assert artifact is not None  # triage routes to CLI only when one exists
            cli_agent = CliExperimentAgent(
                repo_url=url.normalized,
                # The build is what will be measured, so its commit is the one recorded.
                commit_sha=artifact.commit_sha,
                artifact=artifact,
                repository_root=Path(clone.path),
                manifest=manifest,
                router=router,
                research=research,
                emitter=context.emitter,
            )
            experiment = await cli_agent.propose(
                question, job_id=context.job_id, experiment_id=experiment_id
            )
        else:
            agent_spark = ExperimentAgent(
                repo_url=url.normalized,
                commit_sha=clone.commit_sha,
                repository_root=Path(clone.path),
                image=DEFAULT_IMAGE,
                router=router,
                research=research,
                emitter=context.emitter,
                budget=ExperimentBudget(),
                base_config=SparkRunConfig(),
                concurrency=concurrency,
                rows=rows,
                host_cpus=os.cpu_count(),
            )
            experiment = await agent_spark.propose(
                question, job_id=context.job_id, experiment_id=experiment_id
            )

    context.save(experiment)
    context.analytics.record(
        AnalyticsEventType.EXPERIMENT_PROPOSED,
        experiment_id=experiment.id,
        status=experiment.status.value,
    )

    # The job completes here. Approval starts a *new* job, because an experiment
    # waiting for a human is not a job that is still running.
    context.finish(JobStatus.FAILED if experiment.status.is_terminal else JobStatus.COMPLETED)


def _triage_message(decision: Triage) -> str:
    message = f"a {decision.kind.value} question: {decision.reason}"
    if decision.route is Route.EXPERIMENT:
        return f"{message} -- designing an experiment on the {decision.backend} environment"
    if decision.downgraded:
        return f"{message} -- answering from evidence. {decision.downgraded}"
    return f"{message} -- answering from the repository and its documentation"


def _grounded(result: object) -> bool | None:
    answer = getattr(result, "answer", None)
    return None if answer is None else bool(answer.grounded)


async def _run(
    context: _TaskContext, experiment_id: str, rows: int | None
) -> Experiment | CliExperiment:
    with context.factory() as session:
        mark_running(session, context.job_id)
        record = get_experiment(session, experiment_id)
        kind = record.kind if record is not None else None
        session.commit()

    if kind == KIND_CLI:
        return await _run_cli(context, experiment_id)

    with context.factory() as session:
        experiment = load_experiment(session, experiment_id)

    if experiment is None:
        raise LookupError(f"no experiment {experiment_id}")
    if not experiment.may_execute:
        raise PermissionError(
            f"experiment {experiment_id} is {experiment.status.value}; it has not been approved"
        )

    plan = experiment.plan
    assert plan is not None  # may_execute implies a plan
    router, research = _providers(context.settings, context.factory)
    url = validate_repo_url(experiment.repo_url)

    context.emitter.emit(
        EventType.APPROVAL_RECEIVED,
        f"approved: {plan.summary()}",
        step=STEP_LABEL,
        data={
            "experiment_id": experiment.id,
            "approved_by": experiment.approved_by,
            "estimated_runs": plan.estimated_runs,
            "estimated_seconds": plan.estimated_seconds,
        },
    )
    context.analytics.record(AnalyticsEventType.EXPERIMENT_APPROVED, experiment_id=experiment.id)

    with TemporaryClone(url, limits=DEFAULT_LIMITS) as clone:
        if clone.commit_sha != experiment.commit_sha:
            # Measuring a different commit than the one approved would make the
            # result unattributable, so this stops rather than adapting.
            raise CommitMovedError(
                f"approved {experiment.commit_sha[:12]} but the default branch is now "
                f"{clone.commit_sha[:12]}; propose the experiment again"
            )

        agent = ExperimentAgent(
            repo_url=experiment.repo_url,
            commit_sha=experiment.commit_sha,
            repository_root=Path(clone.path),
            image=DEFAULT_IMAGE,
            router=router,
            research=research,
            emitter=context.emitter,
            base_config=SparkRunConfig(),
            baseline_value=plan.baseline_value,
            concurrency=plan.concurrency,
            rows=rows,
            host_cpus=os.cpu_count(),
        )
        context.analytics.record(AnalyticsEventType.EXPERIMENT_STARTED, experiment_id=experiment.id)

        async def persist(_run: object) -> None:
            # Saved after every run, so a crash mid-experiment leaves the
            # measurements that were already taken rather than losing them.
            context.save(experiment)

        experiment = await agent.execute(experiment, on_run=persist)

    context.save(experiment)
    context.analytics.record(
        AnalyticsEventType.EXPERIMENT_COMPLETED,
        experiment_id=experiment.id,
        runs=experiment.run_count,
    )
    context.emitter.emit(
        EventType.STEP_DONE,
        "experiment complete",
        step=STEP_LABEL,
        data={
            "experiment_id": experiment.id,
            "runs": experiment.run_count,
            "grounded": (
                experiment.explanation.grounded if experiment.explanation is not None else None
            ),
        },
    )
    context.finish(JobStatus.COMPLETED)
    return experiment


async def _run_cli(context: _TaskContext, experiment_id: str) -> CliExperiment:
    """Run an approved CLI experiment on the build it was approved against."""
    with context.factory() as session:
        experiment = load_cli_experiment(session, experiment_id)
    if experiment is None:
        raise LookupError(f"no experiment {experiment_id}")
    if not experiment.may_execute or experiment.plan is None:
        raise PermissionError(
            f"experiment {experiment_id} is {experiment.status.value}; it has not been approved"
        )
    plan = experiment.plan

    # The plan was approved for one specific checkpoint. A rebuild since then is
    # different software, so it is not silently substituted.
    wanted = plan.environment.get("checkpoint")
    with context.factory() as session:
        artifacts = load_builds(session, experiment.repo_url, experiment.commit_sha)
    artifact = next((item for item in artifacts if item.checkpoint_tag == wanted), None)
    if artifact is None:
        raise LookupError(
            f"the environment this experiment was approved for ({wanted}) is no longer "
            "available; rebuild it from the Learn tab and ask again"
        )

    router, research = _providers(context.settings, context.factory)
    context.emitter.emit(
        EventType.APPROVAL_RECEIVED,
        f"approved: {plan.summary()}",
        step=STEP_LABEL,
        data={
            "experiment_id": experiment.id,
            "approved_by": experiment.approved_by,
            "estimated_runs": plan.estimated_runs,
            "estimated_seconds": plan.estimated_seconds,
        },
    )
    context.analytics.record(AnalyticsEventType.EXPERIMENT_APPROVED, experiment_id=experiment.id)
    context.analytics.record(AnalyticsEventType.EXPERIMENT_STARTED, experiment_id=experiment.id)

    from repolab.sandbox.docker_provider import DockerSandboxProvider

    agent = CliExperimentAgent(
        repo_url=experiment.repo_url,
        commit_sha=experiment.commit_sha,
        artifact=artifact,
        router=router,
        research=research,
        emitter=context.emitter,
    )

    async def persist(_run: object) -> None:
        context.save(experiment)

    experiment = await agent.execute(experiment, provider=DockerSandboxProvider(), on_run=persist)

    context.save(experiment)
    context.analytics.record(
        AnalyticsEventType.EXPERIMENT_COMPLETED,
        experiment_id=experiment.id,
        runs=experiment.run_count,
    )
    context.emitter.emit(
        EventType.STEP_DONE,
        "experiment complete",
        step=STEP_LABEL,
        data={
            "experiment_id": experiment.id,
            "runs": experiment.run_count,
            "grounded": (
                experiment.explanation.grounded if experiment.explanation is not None else None
            ),
        },
    )
    context.finish(JobStatus.COMPLETED)
    return experiment
