"""Bounded, binary-safe reading of repository files.

Every detector finding must be able to cite where it came from, so this module
pairs file reading with :class:`~repolab.evidence.models.CodeReference`
construction. Reads are capped per file and in total, and tracked by a budget so
one enormous file cannot consume an ingestion.
"""

from __future__ import annotations

from pathlib import Path

from repolab.evidence.models import CodeReference
from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits, LimitReport

#: Bytes inspected when deciding whether a file is binary.
_SNIFF_BYTES = 4096

#: Lines of context included either side of a matched line in an excerpt.
DEFAULT_CONTEXT_LINES = 2


class ReadBudget:
    """Tracks how much has been read during one ingestion."""

    def __init__(
        self,
        limits: IngestLimits = DEFAULT_LIMITS,
        report: LimitReport | None = None,
    ) -> None:
        self.limits = limits
        self.report = report if report is not None else LimitReport()
        self.bytes_read = 0
        self.files_read = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limits.max_total_read_bytes - self.bytes_read)

    @property
    def exhausted(self) -> bool:
        if self.bytes_read >= self.limits.max_total_read_bytes:
            self.report.record("max_total_read_bytes")
            return True
        return False

    @property
    def reserve_remaining(self) -> int:
        """How much of the manifest reservation is still unspent."""
        return max(0, min(self.limits.manifest_reserve_bytes, self.remaining))

    def can_afford(self, size: int, *, reserved: bool = False) -> bool:
        """Whether reading *size* more bytes stays within the total budget.

        Checked against the file's actual size rather than after the fact, so the
        cap is a genuine upper bound instead of one the last read can overshoot.

        *reserved* marks a read that may draw on
        :attr:`IngestLimits.manifest_reserve_bytes`. An ordinary read must leave
        that reservation intact, so a large source scan can no longer starve the
        dependency manifests -- the files whose evidence nothing else can supply.
        """
        available = self.remaining if reserved else self.remaining - self.reserve_remaining
        if size > available:
            self.report.record("max_total_read_bytes")
            return False
        return True

    def charge(self, amount: int) -> None:
        self.bytes_read += amount
        self.files_read += 1


def looks_binary(path: Path) -> bool:
    """Whether *path* appears to be binary.

    A NUL byte in the first few kilobytes is the conventional, cheap signal.
    """
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(_SNIFF_BYTES)
    except OSError:
        return True


def read_text(
    path: Path,
    *,
    budget: ReadBudget | None = None,
    limits: IngestLimits = DEFAULT_LIMITS,
    reserved: bool = False,
) -> str | None:
    """Read *path* as text, or return ``None`` if it cannot or should not be read.

    Returns ``None`` for binary files, unreadable files, files larger than
    ``max_read_bytes``, and once the ingestion's total read budget is spent.

    *reserved* permits the read to draw on the manifest reservation; see
    :meth:`ReadBudget.can_afford`.
    """
    budget = budget if budget is not None else ReadBudget(limits)
    if budget.exhausted:
        return None

    try:
        size = path.stat().st_size
    except OSError:
        return None

    if size > budget.limits.max_read_bytes:
        budget.report.record("max_read_bytes")
        return None
    if not budget.can_afford(size, reserved=reserved):
        return None
    if looks_binary(path):
        return None

    try:
        data = path.read_bytes()
    except OSError:
        return None

    budget.charge(len(data))
    # errors="replace" keeps a mostly-text file usable instead of discarding it.
    return data.decode("utf-8", errors="replace")


def reference(
    relative_path: str,
    *,
    commit_sha: str | None = None,
    line: int | None = None,
    end_line: int | None = None,
    excerpt: str | None = None,
) -> CodeReference:
    """Build a :class:`CodeReference` for a repository-relative path."""
    return CodeReference(
        path=relative_path,
        start_line=line,
        end_line=end_line if end_line is not None else line,
        commit_sha=commit_sha,
        excerpt=excerpt,
    )


def find_lines(text: str, needles: tuple[str, ...], *, limit: int = 3) -> list[tuple[int, str]]:
    """Return up to *limit* ``(line_number, line)`` pairs containing any needle.

    Line numbers are 1-based so they match what an editor shows.
    """
    matches: list[tuple[int, str]] = []
    lowered = tuple(n.lower() for n in needles)
    for index, line in enumerate(text.splitlines(), start=1):
        candidate = line.lower()
        if any(needle in candidate for needle in lowered):
            matches.append((index, line.strip()))
            if len(matches) >= limit:
                break
    return matches


def excerpt_at(text: str, line_number: int, *, context: int = DEFAULT_CONTEXT_LINES) -> str:
    """Return *text* around *line_number* with surrounding context."""
    lines = text.splitlines()
    start = max(0, line_number - 1 - context)
    end = min(len(lines), line_number + context)
    return "\n".join(lines[start:end]).strip()
