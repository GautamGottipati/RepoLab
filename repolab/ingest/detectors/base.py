"""Shared types for deterministic detectors.

Every detector reports findings *with the file references that justify them*.
That is what lets the manifest claim things about a repository without a model
having been involved, and what lets the UI show the evidence beside each claim.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from repolab.evidence.models import CodeReference
from repolab.ingest.content import ReadBudget
from repolab.ingest.tree import FileTree


class DetectionContext(BaseModel):
    """Everything a detector needs, and nothing it does not.

    Detectors receive the clone path but are expected to read only through
    :mod:`repolab.ingest.content`, so every read stays inside the budget.
    """

    root: Path
    tree: FileTree
    commit_sha: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    def path_for(self, relative: str) -> Path:
        return self.root / relative


class Finding(BaseModel):
    """One deterministic observation about the repository."""

    kind: str
    """What was observed, e.g. ``framework``, ``spark_config``, ``http_route``."""

    value: str
    """The observed value, e.g. ``pyspark``, ``spark.sql.shuffle.partitions=8``."""

    detail: str | None = None
    references: list[CodeReference] = Field(default_factory=list)

    @property
    def is_supported(self) -> bool:
        """Whether this finding cites at least one file."""
        return bool(self.references)


class DetectorRun(BaseModel):
    """What one detector produced."""

    findings: list[Finding] = Field(default_factory=list)

    def add(
        self,
        kind: str,
        value: str,
        *,
        detail: str | None = None,
        references: list[CodeReference] | None = None,
    ) -> Finding:
        finding = Finding(kind=kind, value=value, detail=detail, references=references or [])
        self.findings.append(finding)
        return finding

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def values_of(self, kind: str) -> list[str]:
        return [f.value for f in self.of_kind(kind)]

    def extend(self, other: DetectorRun) -> None:
        self.findings.extend(other.findings)

    @property
    def all_references(self) -> list[CodeReference]:
        seen: dict[tuple[str, int | None], CodeReference] = {}
        for finding in self.findings:
            for ref in finding.references:
                seen.setdefault((ref.path, ref.start_line), ref)
        return list(seen.values())


__all__ = ["DetectionContext", "DetectorRun", "Finding", "ReadBudget"]
