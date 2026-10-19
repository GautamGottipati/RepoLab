"""Saving and loading ingestion results.

Re-ingesting the same commit updates that ingestion rather than accumulating
duplicates, so a repository has one current record per commit.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import (
    ArchitectureRecord,
    Ingestion,
    IngestionEvidence,
    RepoManifestRecord,
    Repository,
)
from repolab.ingest.pipeline import IngestionResult
from repolab.manifest.models import RepoManifest


def get_or_create_repository(session: Session, result: IngestionResult) -> Repository:
    """Find the repository by normalised URL, creating it when new."""
    existing = session.scalar(
        select(Repository).where(Repository.url == result.repo_url.normalized)
    )
    if existing is not None:
        return existing

    repository = Repository(
        url=result.repo_url.normalized,
        host=result.repo_url.host,
        owner=result.repo_url.owner,
        name=result.repo_url.name,
    )
    session.add(repository)
    session.flush()
    return repository


def save_ingestion(session: Session, result: IngestionResult) -> Ingestion:
    """Persist *result*, replacing any previous ingestion of the same commit."""
    repository = get_or_create_repository(session, result)

    ingestion = session.scalar(
        select(Ingestion).where(
            Ingestion.repository_id == repository.id,
            Ingestion.commit_sha == result.commit_sha,
        )
    )
    if ingestion is None:
        ingestion = Ingestion(repository_id=repository.id, commit_sha=result.commit_sha)
        session.add(ingestion)

    ingestion.branch = result.branch
    ingestion.status = "completed"
    ingestion.file_count = result.file_count
    ingestion.total_size = result.total_size
    ingestion.files_read = result.files_read
    ingestion.bytes_read = result.bytes_read
    ingestion.tree_truncated = result.tree_truncated
    ingestion.limits_hit = {"hit": result.limits_hit}
    ingestion.notes = {"notes": result.notes}
    ingestion.languages = {"languages": [stat.model_dump(mode="json") for stat in result.languages]}
    ingestion.llm_enriched = result.enriched
    ingestion.duration_seconds = result.duration_seconds
    ingestion.ingested_at = result.ingested_at
    session.flush()

    # Clear and flush before reassigning: each child row holds a unique
    # ingestion_id, and SQLAlchemy would otherwise insert the replacement before
    # deleting the orphan and trip the constraint.
    ingestion.manifest = None
    ingestion.architecture = None
    ingestion.evidence = []
    session.flush()

    # Assigned through the relationships so the ORM keeps both sides in sync.
    ingestion.manifest = RepoManifestRecord(
        manifest=result.manifest.model_dump(mode="json"),
        confidence=result.manifest.confidence,
        primary_surface=_primary_surface_name(result),
    )
    ingestion.architecture = ArchitectureRecord(
        graph=result.architecture.model_dump(mode="json"),
        generated_by=result.architecture.generated_by.value,
        node_count=len(result.architecture.nodes),
        edge_count=len(result.architecture.edges),
        summary=result.enrichment.architecture_summary,
    )
    ingestion.evidence = [
        IngestionEvidence(
            kind="ingestion",
            bundle=result.evidence.model_dump(mode="json"),
            simulated=result.evidence.simulated,
        )
    ]

    session.flush()
    return ingestion


def _primary_surface_name(result: IngestionResult) -> str | None:
    """The primary surface type, denormalised so the API can filter on it."""
    primary = result.manifest.primary_surface
    return primary.type.value if primary is not None else None


def load_latest_ingestion(session: Session, repo_url: str) -> Ingestion | None:
    """The most recent ingestion recorded for *repo_url*."""
    return session.scalar(
        select(Ingestion)
        .join(Repository)
        .where(Repository.url == repo_url)
        .order_by(Ingestion.ingested_at.desc())
        .limit(1)
    )


def load_manifest(session: Session, repo_url: str) -> RepoManifest | None:
    """The most recent manifest recorded for *repo_url*, rebuilt as a model.

    Downstream components consume ``RepoManifest``, not a JSON column: the
    Learning Agent and the build ladder both take the model, so the single place
    that knows how the manifest is stored is here rather than at each caller.
    """
    ingestion = load_latest_ingestion(session, repo_url)
    if ingestion is None or ingestion.manifest is None:
        return None
    return RepoManifest.model_validate(ingestion.manifest.manifest)


def load_manifest_for_job(session: Session, job_id: str) -> RepoManifest | None:
    """The manifest the given job produced, if it produced one."""
    from repolab.db.jobs import get_job

    job = get_job(session, job_id)
    if job is None or job.ingestion_id is None:
        return None
    record = session.scalar(
        select(RepoManifestRecord).where(RepoManifestRecord.ingestion_id == job.ingestion_id)
    )
    return None if record is None else RepoManifest.model_validate(record.manifest)
