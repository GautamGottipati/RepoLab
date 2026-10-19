"""Repository file tree construction.

The walk is bounded in breadth, depth and total size, and skips directories that
hold dependencies or build output rather than source. Anything truncated is
recorded so downstream consumers know the picture is partial.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits, LimitReport

#: Directories never worth walking: vendored dependencies, build output, caches.
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".idea",
        ".vscode",
        "target",
        "build",
        "dist",
        "out",
        ".next",
        ".terraform",
        "site-packages",
        ".eggs",
    }
)


class FileEntry(BaseModel):
    """One file in the repository."""

    path: str
    """Repository-relative, forward-slash separated."""

    size: int
    extension: str
    """Lowercased suffix including the dot, or empty."""

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def depth(self) -> int:
        return self.path.count("/")


class FileTree(BaseModel):
    """The bounded view of a repository's files."""

    root: str
    files: list[FileEntry] = Field(default_factory=list)
    directories: list[str] = Field(default_factory=list)
    skipped_directories: list[str] = Field(default_factory=list)
    truncated: bool = False

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.files)

    def by_name(self, *names: str) -> list[FileEntry]:
        """Files whose basename matches any of *names*, case-insensitively."""
        wanted = {n.lower() for n in names}
        return [e for e in self.files if e.name.lower() in wanted]

    def by_extension(self, *extensions: str) -> list[FileEntry]:
        wanted = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
        return [e for e in self.files if e.extension in wanted]

    def matching(self, predicate: Callable[[FileEntry], bool]) -> list[FileEntry]:
        return [e for e in self.files if predicate(e)]

    def name_startswith(self, prefix: str) -> list[FileEntry]:
        lowered = prefix.lower()
        return [e for e in self.files if e.name.lower().startswith(lowered)]


def build_file_tree(
    root: Path,
    *,
    limits: IngestLimits = DEFAULT_LIMITS,
    report: LimitReport | None = None,
) -> FileTree:
    """Walk *root* and return a bounded :class:`FileTree`."""
    root = Path(root)
    report = report if report is not None else LimitReport()
    tree = FileTree(root=str(root))
    total = 0

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        depth = 0 if relative_dir == Path() else len(relative_dir.parts)

        skipped = [d for d in dirnames if d in SKIP_DIRECTORIES or d.endswith(".egg-info")]
        for name in skipped:
            tree.skipped_directories.append(str((relative_dir / name).as_posix()))

        if depth >= limits.max_depth:
            report.record("max_depth")
            dirnames[:] = []
        else:
            dirnames[:] = sorted(d for d in dirnames if d not in skipped)

        if relative_dir != Path():
            tree.directories.append(relative_dir.as_posix())

        for filename in sorted(filenames):
            if len(tree.files) >= limits.max_files:
                report.record("max_files")
                tree.truncated = True
                return tree

            file_path = current_path / filename
            # Symlinks are not followed: a link could point outside the clone.
            if file_path.is_symlink() or not file_path.is_file():
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue

            total += size
            if total > limits.max_tree_bytes:
                report.record("max_tree_bytes")
                tree.truncated = True
                return tree

            relative = (relative_dir / filename).as_posix()
            tree.files.append(
                FileEntry(path=relative, size=size, extension=file_path.suffix.lower())
            )

    return tree
