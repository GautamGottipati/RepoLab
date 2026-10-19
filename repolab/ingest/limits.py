"""Bounds on how much of a repository ingestion will read.

Repositories like ``apache/airflow`` hold tens of thousands of files. Ingestion
must stay tractable and must never ship a repository to a model, so every stage
is bounded and every bound that actually bites is reported rather than silently
applied.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestLimits(BaseModel):
    """Limits applied during one ingestion."""

    max_files: int = Field(default=60_000, gt=0)
    """Entries recorded in the file tree."""

    max_tree_bytes: int = Field(default=2_000_000_000, gt=0)
    """Total apparent size of tree entries before the walk stops."""

    max_depth: int = Field(default=24, gt=0)
    """Directory depth beyond which the walk does not descend."""

    max_read_bytes: int = Field(default=512_000, gt=0)
    """Largest single file ingestion will read."""

    max_total_read_bytes: int = Field(default=32_000_000, gt=0)
    """Total bytes read from disk across one ingestion."""

    manifest_reserve_bytes: int = Field(default=2_000_000, ge=0)
    """Part of the total held back for dependency manifests.

    Measured, not guessed. A fresh ingestion of `apache/airflow` (14,523 files)
    found 362 dependency manifests and read **none** of them: the general scan
    had spent all 32 MB first, so `frameworks` came back empty and a repository
    that ships a web UI, an HTTP API and a CLI was classified with two surfaces
    instead of four.

    A manifest is a few kilobytes and is the *only* evidence of a framework when
    a bounded scan never reaches the application module -- which is the reason
    `FRAMEWORK_SURFACES` exists at all. Airflow's 362 manifests are roughly
    1.8 MB in total, so 2 MB covers them with room to spare.

    This is a reservation inside the total, never an addition to it: the general
    scan simply stops this much earlier, and `max_total_read_bytes` remains a
    true upper bound.
    """

    max_scanned_files: int = Field(default=4_000, gt=0)
    """Source files whose content may be scanned by detectors."""

    # -- model exposure ---------------------------------------------------
    # These are the only numbers that decide what reaches an LLM.

    max_llm_files: int = Field(default=12, gt=0)
    """Files that may be summarised by Nano."""

    max_llm_file_bytes: int = Field(default=8_000, gt=0)
    """Bytes of any single file sent to a model."""

    max_llm_total_bytes: int = Field(default=64_000, gt=0)
    """Total bytes across all files sent to models."""

    clone_timeout_seconds: float = Field(default=600.0, gt=0)
    """Wall-clock budget for the clone subprocess."""


DEFAULT_LIMITS = IngestLimits()


class LimitReport(BaseModel):
    """Which limits were reached during an ingestion."""

    hit: list[str] = Field(default_factory=list)

    def record(self, name: str) -> None:
        if name not in self.hit:
            self.hit.append(name)

    @property
    def any_hit(self) -> bool:
        return bool(self.hit)
