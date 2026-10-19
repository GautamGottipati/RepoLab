"""Build artifact persistence.

Postgres holds the build cache, so an environment produced by a worker is visible
to the API and to a later run of the command. Only ready builds are written --
caching a failed build would hand out a broken environment on the next lookup.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.builds.models import BuildArtifact
from repolab.db.models import Build


def save_build(session: Session, artifact: BuildArtifact) -> Build | None:
    """Persist *artifact*, replacing any previous build of the same commit.

    Returns ``None`` without writing anything when the artifact is not ready.
    """
    if not artifact.ready:
        return None

    # Keyed by kind as well as commit: the rungs of one ladder coexist, so
    # storing an official release must not evict the source build beside it.
    record = session.scalar(
        select(Build).where(
            Build.repo_url == artifact.repo_url,
            Build.commit_sha == artifact.commit_sha,
            Build.kind == artifact.kind.value,
        )
    )
    if record is None:
        record = Build(
            repo_url=artifact.repo_url,
            commit_sha=artifact.commit_sha,
            kind=artifact.kind.value,
        )
        session.add(record)

    record.kind = artifact.kind.value
    record.fidelity = artifact.fidelity.value
    record.provider = artifact.provider
    record.base_image = artifact.base_image
    record.version = artifact.version
    record.simulated = artifact.simulated
    record.checkpoint_id = artifact.checkpoint_id
    record.checkpoint_tag = artifact.checkpoint_tag
    record.service_command = artifact.service_command
    record.cli_command = artifact.cli_command
    record.memory = artifact.memory
    record.ready = artifact.ready
    record.capabilities = artifact.capabilities.model_dump(mode="json")
    record.smoke = artifact.smoke.model_dump(mode="json") if artifact.smoke else None
    record.attempts = {
        "attempts": [attempt.model_dump(mode="json") for attempt in artifact.attempts]
    }
    record.updated_at = datetime.now(UTC)
    session.flush()
    return record


def load_build(session: Session, repo_url: str, commit_sha: str) -> BuildArtifact | None:
    """The best cached build for this commit, if one is recorded and ready."""
    found = load_builds(session, repo_url, commit_sha)
    return found[0] if found else None


def load_builds(session: Session, repo_url: str, commit_sha: str) -> list[BuildArtifact]:
    """Every ready build recorded for this commit, highest fidelity first.

    The ladder needs all of them: knowing an official release is already cached
    is what lets it skip straight to attempting the source build.
    """
    records = session.scalars(
        select(Build).where(
            Build.repo_url == repo_url,
            Build.commit_sha == commit_sha,
            Build.ready.is_(True),
        )
    ).all()
    artifacts = [_to_artifact(record) for record in records]
    return sorted(artifacts, key=lambda artifact: artifact.fidelity_rank, reverse=True)


def _to_artifact(record: Build) -> BuildArtifact:
    from repolab.builds.models import BuildAttempt, BuildCapabilities, SmokeTestResult
    from repolab.manifest.models import BuildKind

    attempts_payload = (record.attempts or {}).get("attempts", [])
    return BuildArtifact(
        repo_url=record.repo_url,
        commit_sha=record.commit_sha,
        kind=BuildKind(record.kind),
        # Loaded from the cache, so it reports itself as such. Fidelity is derived
        # from the kind and the stored deviations, not from the fact that it was
        # cached.
        cached=True,
        provider=record.provider,
        base_image=record.base_image,
        version=record.version,
        simulated=record.simulated,
        checkpoint_id=record.checkpoint_id,
        checkpoint_tag=record.checkpoint_tag,
        service_command=record.service_command,
        cli_command=record.cli_command,
        memory=record.memory,
        capabilities=BuildCapabilities.model_validate(record.capabilities or {}),
        smoke=SmokeTestResult.model_validate(record.smoke) if record.smoke else None,
        attempts=[BuildAttempt.model_validate(item) for item in attempts_payload],
        created_at=record.created_at,
    )


def count_builds(session: Session) -> int:
    return len(session.scalars(select(Build.id)).all())


def delete_build(session: Session, repo_url: str, commit_sha: str, kind: str) -> bool:
    """Remove one recorded build. Returns whether a row was removed.

    Used when a checkpoint has been removed from the machine: the row claims
    `ready` and nothing can start from it, so keeping it means every attempt
    fails the same way.
    """
    record = session.scalar(
        select(Build).where(
            Build.repo_url == repo_url,
            Build.commit_sha == commit_sha,
            Build.kind == kind,
        )
    )
    if record is None:
        return False

    session.delete(record)
    session.flush()
    return True
