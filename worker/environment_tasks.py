"""Resolving an environment through the fidelity ladder, for the browser.

Phase 9 built the ladder and drove it from a script. This is the same ladder,
reached from a repository URL a person typed into a web page: the browser submits
the repository, this task resolves it, and every candidate that passes its smoke
test is **written to the build cache as soon as it is ready**.

That last detail is what makes the upgrade sequence work across processes. The
API holds the terminal sessions; the worker holds the builds. The worker does not
tell the API that a better environment exists -- it records the artifact, and the
API, which is already asking the cache what the best ready build is, sees it on
the next ask and offers the learner an upgrade. Neither process has to know the
other is running.

Builds take minutes, so this is a worker task and never a request handler.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.analytics import Analytics, DbAnalyticsWriter
from repolab.builds import (
    BuildLadder,
    BuildLimits,
    BuildRequest,
    DbBuildCache,
    candidates_for,
    needs_submodules,
)
from repolab.builds.models import BuildArtifact
from repolab.config import get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.db.store import load_manifest
from repolab.events import DbEventEmitter, EventType, JobStatus
from repolab.failures import describe, explain
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.url import validate_repo_url
from repolab.sandbox.docker import docker_available
from repolab.sandbox.docker_provider import DockerSandboxProvider
from repolab.sandbox.models import SandboxError

log = structlog.get_logger(__name__)

STEP_LABEL = "environment"

#: Attempts per candidate before falling through to the next rung.
MAX_ATTEMPTS = 2


class ManifestMissingError(RuntimeError):
    """The repository has not been investigated, so there is nothing to build."""


class CommitMovedError(RuntimeError):
    """The default branch moved between the investigation and this build.

    Worth failing on rather than carrying on. The artifact would be cached under
    the commit that was just cloned, while the API looks a build up by the commit
    the repository was *investigated* at -- so the build would succeed, cost
    minutes, and then be invisible to the terminal that asked for it.
    """


def resolve_environment(job_id: str, repo_url: str, slow_source_seconds: float = 0.0) -> str:
    """Resolve *repo_url* through the ladder and cache every ready artifact.

    *slow_source_seconds* delays the source build on purpose, for demonstrating
    the upgrade sequence. It is recorded on the artifact, so a build that was
    slowed deliberately can never be mistaken for one that was slow.
    """
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)

    def session_factory() -> AbstractContextManager[Session]:
        return session_scope(engine)

    with factory() as session:
        job = get_job(session, job_id)
        start_seq = job.last_seq if job is not None else 0
        mark_running(session, job_id)
        session.commit()

    emitter = DbEventEmitter(job_id, session_factory, start_seq=start_seq)

    try:
        served = asyncio.run(
            _resolve(repo_url, slow_source_seconds, emitter, factory, session_factory)
        )
    except Exception as exc:
        failure = explain(exc)
        detail = describe(exc)
        emitter.emit(
            EventType.FAILED,
            failure.summary,
            step=STEP_LABEL,
            data=failure.model_dump_public(),
        )
        with factory() as session:
            mark_finished(session, job_id, status=JobStatus.FAILED, error=detail)
            session.commit()
        log.warning("environment.failed", job_id=job_id, error=detail)
        engine.dispose()
        raise

    with factory() as session:
        mark_finished(session, job_id, status=JobStatus.COMPLETED)
        session.commit()

    engine.dispose()
    return served.checkpoint_tag or served.kind.value


async def _resolve(
    repo_url: str,
    slow_source_seconds: float,
    emitter: DbEventEmitter,
    factory: sessionmaker[Session],
    session_factory,
) -> BuildArtifact:
    url = validate_repo_url(repo_url)

    # Environment, not build: the ladder was never reached, so saying "every
    # rung failed" would be false.
    if not docker_available():
        raise SandboxError(
            "Docker is not available, and repository code must never run on the API host"
        )

    with factory() as session:
        manifest = load_manifest(session, url.normalized)
    if manifest is None:
        raise ManifestMissingError(
            f"{url.slug} has not been investigated yet, so RepoLab does not know how to build it"
        )

    # Deliberately not wrapped in BuildUnavailableError: "no rung covers this
    # repository" and "every rung was tried and failed" are different facts, and
    # the second is what a user would be told if this were flattened into it.
    candidates = candidates_for(manifest, source_delay_seconds=slow_source_seconds)

    emitter.emit(
        EventType.STEP_STARTED,
        f"resolving an environment for {url.slug} from {len(candidates)} candidate(s)",
        step=STEP_LABEL,
        data={
            "candidates": [
                {
                    "kind": candidate.kind.value,
                    "fidelity": candidate.expected_fidelity.value,
                    "why": candidate.why,
                }
                for candidate in candidates
            ]
        },
    )

    cache = DbBuildCache(session_factory)

    async def on_ready(artifact: BuildArtifact) -> None:
        """Announce each rung the moment it passes its smoke test.

        The first one is what lets a learner start; the later ones are what the
        API turns into an upgrade offer. Both are the same event, because from
        here they are the same fact.
        """
        emitter.emit(
            EventType.PARTIAL_RESULT,
            f"a {artifact.fidelity.value}-fidelity environment is ready",
            step=STEP_LABEL,
            data=artifact.declaration(),
        )

    submodules = needs_submodules(candidates)
    with TemporaryClone(url, limits=DEFAULT_LIMITS, submodules=submodules) as clone:
        if clone.commit_sha != manifest.commit_sha:
            raise CommitMovedError(
                f"this repository was investigated at {manifest.commit_sha[:12]} but the "
                f"default branch is now {clone.commit_sha[:12]}; import it again"
            )

        ladder = BuildLadder(
            DockerSandboxProvider(),
            cache=cache,
            emitter=emitter,
            limits=BuildLimits(max_attempts=MAX_ATTEMPTS),
            analytics=Analytics(DbAnalyticsWriter(session_factory), repo_url=url.normalized),
        )
        result = await ladder.resolve(
            BuildRequest(repo_url=url.normalized, commit_sha=clone.commit_sha),
            candidates,
            Path(clone.path),
            manifest=manifest,
            on_ready=on_ready,
        )

    emitter.emit(
        EventType.STEP_DONE,
        f"serving the {result.served.fidelity.value}-fidelity environment",
        step=STEP_LABEL,
        data=result.served.declaration(),
    )
    log.info(
        "environment.resolved",
        repo=url.slug,
        kind=result.served.kind.value,
        fidelity=result.served.fidelity.value,
    )
    return result.served
