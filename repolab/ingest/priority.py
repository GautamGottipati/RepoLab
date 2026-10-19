"""Ordering source files by how much they are likely to reveal.

On a repository like ``apache/airflow`` -- 14,000+ files -- the read budget is
spent long before the walk reaches the end. Scanning in filesystem order means
whether the FastAPI application is discovered depends on where it happens to sort,
which is not acceptable. Files are therefore ranked by how informative they are
likely to be, so a bounded scan still sees the parts that define the system.
"""

from __future__ import annotations

from repolab.ingest.tree import FileEntry

#: Basenames that usually define how a system starts or is exposed.
HIGH_VALUE_STEMS: frozenset[str] = frozenset(
    {
        "main",
        "__main__",
        "app",
        "application",
        "server",
        "api",
        "cli",
        "command",
        "commands",
        "routes",
        "router",
        "urls",
        "views",
        "wsgi",
        "asgi",
        "settings",
        "config",
        "job",
        "etl",
        "pipeline",
        "dag",
        "tasks",
        "worker",
        "entrypoint",
        "manage",
        "index",
    }
)

#: Path segments that rarely define behaviour worth scanning first.
LOW_VALUE_SEGMENTS: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "testing",
        "spec",
        "specs",
        "docs",
        "doc",
        "example",
        "examples",
        "sample",
        "samples",
        "fixtures",
        "vendor",
        "third_party",
        "migrations",
        "generated",
        "contrib",
        "benchmark",
        "benchmarks",
    }
)


def priority(entry: FileEntry) -> tuple[int, int, str]:
    """A sort key: lower sorts earlier.

    Ordering is by (penalty, depth, path) so the most promising, shallowest files
    are scanned while budget remains.
    """
    segments = entry.path.lower().split("/")
    stem = segments[-1].rsplit(".", 1)[0]

    penalty = 2
    if stem in HIGH_VALUE_STEMS:
        penalty = 0
    elif any(part in HIGH_VALUE_STEMS for part in segments[:-1]):
        penalty = 1

    if any(part in LOW_VALUE_SEGMENTS for part in segments):
        penalty += 4
    if stem.startswith("test_") or stem.endswith("_test"):
        penalty += 4
    if segments[-1].startswith("."):
        penalty += 2

    return (penalty, entry.depth, entry.path)


def prioritized(entries: list[FileEntry]) -> list[FileEntry]:
    """Return *entries* ordered most-informative first."""
    return sorted(entries, key=priority)
