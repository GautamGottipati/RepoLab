"""The ingestion pipeline.

    URL -> validate -> shallow clone -> file tree -> languages
        -> deterministic detection -> manifest + architecture + evidence
        -> (optional, bounded) LLM enrichment

Ingestion is static inspection only. No repository code is executed at any point:
no dependency install, no build tool, no setup script. The single subprocess is a
hardened ``git clone`` (see :mod:`repolab.ingest.clone`).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from repolab.classify import classify
from repolab.evidence.models import EvidenceBundle
from repolab.ingest.clone import CloneResult, TemporaryClone
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionContext, DetectionOutcome, detect_all
from repolab.ingest.enrich import EnrichmentResult, enrich
from repolab.ingest.languages import LanguageStat, detect_languages
from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits, LimitReport
from repolab.ingest.tree import build_file_tree
from repolab.ingest.url import RepoUrl, validate_repo_url
from repolab.llm import ModelRouter
from repolab.manifest.architecture import ArchitectureGraph
from repolab.manifest.builder import build_architecture, build_evidence, build_manifest
from repolab.manifest.models import Classification, RepoManifest

log = structlog.get_logger(__name__)


class IngestionResult(BaseModel):
    """Everything one ingestion produced."""

    repo_url: RepoUrl
    commit_sha: str
    branch: str | None = None

    manifest: RepoManifest
    architecture: ArchitectureGraph
    evidence: EvidenceBundle
    classification: Classification

    languages: list[LanguageStat] = Field(default_factory=list)
    file_count: int = 0
    total_size: int = 0
    tree_truncated: bool = False
    limits_hit: list[str] = Field(default_factory=list)

    bytes_read: int = 0
    files_read: int = 0

    enrichment: EnrichmentResult = Field(default_factory=EnrichmentResult)
    notes: list[str] = Field(default_factory=list)

    duration_seconds: float = 0.0
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def enriched(self) -> bool:
        return self.enrichment.ran


async def ingest_repository(
    url: str,
    *,
    router: ModelRouter | None = None,
    limits: IngestLimits = DEFAULT_LIMITS,
) -> IngestionResult:
    """Ingest the repository at *url*.

    The clone is removed before this returns, so the result must be
    self-contained: excerpts and references are captured during detection.

    Raises:
        InvalidRepoUrlError: the URL is not an acceptable public repository URL.
        CloneError: the repository could not be cloned.
    """
    repo_url = validate_repo_url(url)
    started = time.perf_counter()

    with TemporaryClone(repo_url, limits=limits) as clone:
        result = await ingest_clone(repo_url, clone, router=router, limits=limits)

    result.duration_seconds = round(time.perf_counter() - started, 3)
    log.info(
        "ingest.finished",
        repo=repo_url.slug,
        commit=result.commit_sha,
        files=result.file_count,
        surfaces=[s.type.value for s in result.manifest.interaction_surfaces],
        duration=result.duration_seconds,
    )
    return result


async def ingest_clone(
    repo_url: RepoUrl,
    clone: CloneResult,
    *,
    router: ModelRouter | None = None,
    limits: IngestLimits = DEFAULT_LIMITS,
) -> IngestionResult:
    """Inspect an already-cloned repository.

    Separated from :func:`ingest_repository` so tests can drive the whole
    inspection against a local fixture repository without any network.
    """
    report = LimitReport()
    budget = ReadBudget(limits, report)

    tree = build_file_tree(clone.path, limits=limits, report=report)
    languages = detect_languages(tree)
    context = DetectionContext(root=Path(clone.path), tree=tree, commit_sha=clone.commit_sha)

    outcome: DetectionOutcome = detect_all(context, budget)

    # Detection gathers evidence; classification decides what it means. A model is
    # consulted only if the deterministic rules are genuinely ambiguous.
    classification = await classify(
        outcome.run,
        entrypoints=outcome.entrypoints,
        services=outcome.services,
        router=router,
    )

    manifest = build_manifest(
        repo_url=repo_url.normalized,
        commit_sha=clone.commit_sha,
        outcome=outcome,
        languages=languages,
        classification=classification,
    )
    architecture = build_architecture(outcome)
    evidence = build_evidence(outcome)

    enrichment = await enrich(router, context, outcome, architecture, budget)

    notes = list(enrichment.notes)
    if tree.truncated:
        notes.append(
            f"file tree truncated at {tree.file_count} files; detection saw a partial repository"
        )
    unsupported = outcome.unsupported_findings
    if unsupported:
        notes.append(f"{len(unsupported)} finding(s) carry no file reference")

    return IngestionResult(
        repo_url=repo_url,
        commit_sha=clone.commit_sha,
        branch=clone.branch,
        manifest=manifest,
        architecture=architecture,
        evidence=evidence,
        classification=classification,
        languages=languages,
        file_count=tree.file_count,
        total_size=tree.total_size,
        tree_truncated=tree.truncated,
        limits_hit=list(report.hit),
        bytes_read=budget.bytes_read,
        files_read=budget.files_read,
        enrichment=enrichment,
        notes=notes,
    )


def summarize(result: IngestionResult) -> str:
    """A short human-readable report of one ingestion."""
    manifest = result.manifest
    build = manifest.build_strategy
    surfaces = ", ".join(f"{s.type.value}({s.confidence})" for s in manifest.interaction_surfaces)
    primary = manifest.primary_surface.type.value if manifest.primary_surface else "none"
    lines = [
        f"repository     {result.repo_url.slug}",
        f"commit         {result.commit_sha[:12]} ({result.branch or 'detached'})",
        f"files          {result.file_count} ({result.total_size / 1_048_576:.1f} MiB)"
        + (" [truncated]" if result.tree_truncated else ""),
        f"read           {result.files_read} files, {result.bytes_read / 1024:.0f} KiB",
        f"languages      {', '.join(manifest.languages) or 'none detected'}",
        f"frameworks     {', '.join(manifest.frameworks) or 'none detected'}",
        f"surfaces       {surfaces or 'none'}",
        f"primary        {primary}" + (" [hybrid]" if result.classification.hybrid else ""),
        f"resolved by    {result.classification.resolved_by.value}"
        + (" (ambiguous)" if result.classification.ambiguous else ""),
        f"rationale      {result.classification.rationale}",
        f"entrypoints    {len(manifest.entrypoints)}",
        f"services       {', '.join(s.name for s in manifest.services) or 'none'}",
        f"dependencies   {len(manifest.dependencies)}",
        f"build          {build.kind.value} / {build.fidelity.value}",
        f"smoke tests    {len(manifest.smoke_tests)}",
        f"concepts       {', '.join(c.name for c in manifest.concepts) or 'none'}",
        f"architecture   {len(result.architecture.nodes)} nodes, "
        f"{len(result.architecture.edges)} edges ({result.architecture.generated_by.value})",
        f"evidence       {len(result.evidence.code_references)} code references",
        f"confidence     {manifest.confidence}",
        f"limits hit     {', '.join(result.limits_hit) or 'none'}",
        f"enrichment     {'ran' if result.enriched else 'skipped'}",
        f"duration       {result.duration_seconds}s",
    ]
    notes = [*result.classification.notes, *result.notes]
    if notes:
        lines.append("notes:")
        lines.extend(f"  - {note}" for note in notes)
    return "\n".join(lines)
