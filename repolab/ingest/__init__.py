"""Repository ingestion.

Static inspection only: a repository is cloned shallowly and read, never
executed. See :mod:`repolab.ingest.pipeline` for the end-to-end flow.
"""

from __future__ import annotations

from repolab.ingest.clone import CloneError, CloneResult, TemporaryClone, shallow_clone
from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits, LimitReport
from repolab.ingest.pipeline import (
    IngestionResult,
    ingest_clone,
    ingest_repository,
    summarize,
)
from repolab.ingest.tree import FileTree, build_file_tree
from repolab.ingest.url import InvalidRepoUrlError, RepoUrl, validate_repo_url

__all__ = [
    "DEFAULT_LIMITS",
    "CloneError",
    "CloneResult",
    "FileTree",
    "IngestLimits",
    "IngestionResult",
    "InvalidRepoUrlError",
    "LimitReport",
    "RepoUrl",
    "TemporaryClone",
    "build_file_tree",
    "ingest_clone",
    "ingest_repository",
    "shallow_clone",
    "summarize",
    "validate_repo_url",
]
