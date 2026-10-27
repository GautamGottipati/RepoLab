"""Commit-pinned copies of the files RepoLab cites.

The investigation's clone lives only as long as the investigation. Before it is
deleted, every file a citation points at is copied here -- by repository,
commit and path, with a sha256 -- so the source viewer shows the exact lines a
claim was made against, long after, and never a newer version.

Three snapshots share one interface (:class:`repolab.sources.resolve.Snapshot`):

    CloneSnapshot    a live checkout, while it exists
    MemorySnapshot   texts in memory (tests, the architecture's source index)
    StoredSnapshot   these copies, read back from Postgres
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import SourceFileRecord
from repolab.sources.symbols import normalize

log = structlog.get_logger(__name__)

#: A file larger than this is not copied; its citation stays, the viewer says so.
MAX_FILE_BYTES = 1_000_000
#: At most this much per repository and commit.
MAX_FILES_PER_COMMIT = 3000
MAX_BYTES_PER_COMMIT = 80_000_000


def _safe(root: Path, path: str) -> Path | None:
    """*path* inside *root*, or ``None`` -- never a file outside the checkout."""
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _read_text(file: Path) -> str | None:
    try:
        data = file.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES or b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


@dataclass
class CloneSnapshot:
    """A checkout on disk at *commit_sha*."""

    root: Path
    commit_sha: str
    paths: set[str] | None = None
    """The tree's files, when known: a cheaper existence check than the disk."""

    def exists(self, path: str) -> bool:
        if self.paths is not None:
            return path in self.paths
        file = _safe(self.root, path)
        return file is not None and file.is_file()

    def text(self, path: str) -> str | None:
        file = _safe(self.root, path)
        return _read_text(file) if file is not None and file.is_file() else None


@dataclass
class MemorySnapshot:
    commit_sha: str
    files: dict[str, str] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)
    """Files known to exist whose text was not loaded."""

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.paths

    def text(self, path: str) -> str | None:
        return self.files.get(path)


@dataclass
class StoredSnapshot:
    """The files captured for one repository at one commit."""

    session: Session
    repo_url: str
    commit_sha: str

    def exists(self, path: str) -> bool:
        return read(self.session, self.repo_url, self.commit_sha, path) is not None

    def text(self, path: str) -> str | None:
        record = read(self.session, self.repo_url, self.commit_sha, path)
        return record.content if record is not None else None


@dataclass
class CaptureReport:
    captured: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    """Path -> why it was not copied (missing, binary or too large, over budget)."""


def capture(
    session: Session,
    *,
    repo_url: str,
    commit_sha: str,
    snapshot: CloneSnapshot | MemorySnapshot,
    paths: Iterable[str],
    by: str = "architecture",
) -> CaptureReport:
    """Copy *paths* from a live *snapshot* into the store, within the caps.

    Idempotent: a file already stored for this commit is left as it is (the
    same commit cannot have different content).
    """
    report = CaptureReport()
    if snapshot.commit_sha != commit_sha:
        raise ValueError("refusing to store files under a different commit than the snapshot's")
    existing = {
        row.path: row.size
        for row in session.execute(
            select(SourceFileRecord.path, SourceFileRecord.size).where(
                SourceFileRecord.repo_url == repo_url, SourceFileRecord.commit_sha == commit_sha
            )
        )
    }
    count, total = len(existing), sum(existing.values())
    for path in dict.fromkeys(normalize(p) for p in paths if p):
        if path in existing:
            report.already.append(path)
            continue
        if count >= MAX_FILES_PER_COMMIT:
            report.skipped[path] = "over the per-commit file limit"
            continue
        text = snapshot.text(path) if snapshot.exists(path) else None
        if text is None:
            report.skipped[path] = "missing, binary or larger than 1 MB"
            continue
        size = len(text.encode("utf-8"))
        if total + size > MAX_BYTES_PER_COMMIT:
            report.skipped[path] = "over the per-commit size limit"
            continue
        session.add(
            SourceFileRecord(
                repo_url=repo_url,
                commit_sha=commit_sha,
                path=path,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                size=size,
                content=text,
                captured_by=by,
            )
        )
        existing[path] = size
        count, total = count + 1, total + size
        report.captured.append(path)
    session.flush()
    if report.skipped:
        log.info(
            "sources.capture_skipped",
            repo=repo_url,
            commit=commit_sha[:12],
            count=len(report.skipped),
        )
    return report


def read(session: Session, repo_url: str, commit_sha: str, path: str) -> SourceFileRecord | None:
    """The stored file at exactly this commit, or ``None``. Never another commit's."""
    return session.execute(
        select(SourceFileRecord).where(
            SourceFileRecord.repo_url == repo_url,
            SourceFileRecord.commit_sha == commit_sha,
            SourceFileRecord.path == normalize(path),
        )
    ).scalar_one_or_none()
