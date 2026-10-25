"""The source files architecture analysis reads, read once.

Every analyzer looks at the same bounded set of files -- the detectors' own
priority order, capped -- through one cache, so adding an analyzer never means
reading the repository again. The budget is separate from detection's: detection
spends its budget on manifests and signals, and analysis should not inherit
whatever was left.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from repolab.evidence.models import CodeReference
from repolab.ingest.content import ReadBudget, excerpt_at, read_text, reference
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.priority import prioritized

#: Source files worth reading for architecture, by extension.
SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".scala",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".rb",
        ".sql",
    }
)

#: Upper bound on files read. Enough for the code that defines a system's
#: structure, in priority order; not a full scan of a 14,000-file repository.
MAX_SOURCE_FILES = 400

#: Root files that describe the project or declare its modules. Always read.
ROOT_METADATA = frozenset(
    {
        "pom.xml",
        "settings.gradle",
        "settings.gradle.kts",
        "build.sbt",
        "package.json",
        "pnpm-workspace.yaml",
        "lerna.json",
        "cargo.toml",
        "go.mod",
        "go.work",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
    }
)

#: Further files read individually because evidence pointed at them.
MAX_ON_DEMAND = 60

#: Directories that hold build tooling, documentation, examples and utilities
#: around a system rather than the system itself. Their files are read last, and
#: only if budget remains -- a CI script with an argparse parser is not the
#: repository's command-line interface.
_PERIPHERAL = re.compile(
    r"(^|/)(\.[\w-]+|ci|dev|hack|build|builds|dist|docs?|examples?|samples?|demos?|tools?|scripts?|"
    r"bench(mark)?s?|contrib|third_party|vendor|node_modules|fixtures|test[-_]?data|"
    r"migrations|generated)(/|$)",
    re.I,
)

#: Basename fragments that usually name a system component. A Java repository's
#: ``CassandraDaemon.java`` should be read before its 3,000 utility classes.
_COMPONENT_NAME = re.compile(
    r"daemon|server|service|main|app|handler|controller|router|routes|api|pipeline|job|"
    r"dag|flow|worker|consumer|producer|cli|shell|command|engine|scheduler|client",
    re.I,
)

_TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|__tests__|testing)(/|$)|(^|/)test_|_test\.\w+$|\.spec\."
)


@dataclass
class SourceIndex:
    """Text of the files analysis may look at, keyed by repository-relative path."""

    commit_sha: str
    texts: dict[str, str] = field(default_factory=dict)
    all_paths: set[str] = field(default_factory=set)
    """Every file in the tree, read or not -- for checking a cited path exists."""

    root: Path | None = None
    truncated: bool = False
    """True when more source files existed than the up-front read allowed."""
    _on_demand: int = 0
    _budget: ReadBudget | None = None

    @classmethod
    def build(cls, context: DetectionContext, *, max_files: int = MAX_SOURCE_FILES) -> SourceIndex:
        index = cls(
            commit_sha=context.commit_sha or "",
            all_paths={entry.path for entry in context.tree.files},
            root=context.root,
        )
        budget = ReadBudget()
        # What the repository says about itself and how its build is split into
        # modules is read first, whatever the size of the repository: the
        # overview and the subsystems depend on it.
        for entry in context.tree.files:
            if "/" not in entry.path and (
                entry.name.lower() in ROOT_METADATA or entry.name.lower().startswith("readme")
            ):
                text = read_text(context.path_for(entry.path), budget=budget)
                if text is not None:
                    index.texts[entry.path] = text
        candidates = [
            entry
            for entry in context.tree.files
            if entry.path not in index.texts
            and (
                entry.extension in SOURCE_EXTENSIONS
                or entry.name.lower() in {"package.json", "pyproject.toml", "setup.py", "readme.md"}
            )
        ]
        for entry in sorted(prioritized(candidates), key=_architecture_rank):
            if len(index.texts) >= max_files:
                index.truncated = True
                break
            text = read_text(context.path_for(entry.path), budget=budget)
            if text is not None:
                index.texts[entry.path] = text
        return index

    def text(self, path: str) -> str | None:
        """A file's text, reading it on demand if an analyzer names one not yet read.

        The up-front read is bounded and prioritised, so a specific file another
        piece of evidence points at -- an entry point, a shell launcher -- may not
        be in it. Such files are read individually, under their own small budget.
        """
        if path in self.texts:
            return self.texts[path]
        if self.root is None or path not in self.all_paths or self._on_demand >= MAX_ON_DEMAND:
            return None
        self._budget = self._budget or ReadBudget()
        self._on_demand += 1
        text = read_text(self.root / path, budget=self._budget)
        if text is not None:
            self.texts[path] = text
        return text

    def source_files(self, *, include_tests: bool = False) -> Iterator[tuple[str, str]]:
        """The system's source files and their text.

        Tests, tooling, documentation and examples are excluded unless asked for:
        they describe how the system is built and checked, not how it works.
        """
        for path, text in list(self.texts.items()):
            if not include_tests and (is_test_path(path) or is_peripheral(path)):
                continue
            yield path, text

    def ref(
        self, path: str, line: int | None = None, *, end_line: int | None = None
    ) -> CodeReference:
        """A reference with an excerpt from the real file, so a citation shows the code."""
        text = self.text(path) if line else None
        excerpt = excerpt_at(text, line, context=1) if (text and line) else None
        return reference(
            path, commit_sha=self.commit_sha, line=line, end_line=end_line, excerpt=excerpt
        )

    def line_exists(self, path: str, line: int | None) -> bool:
        """Whether *path* is in the repository and, if read, has *line*."""
        if path not in self.all_paths:
            return False
        if line is None:
            return True
        text = self.texts.get(path)
        return text is None or 1 <= line <= text.count("\n") + 1


def is_peripheral(path: str) -> bool:
    """Whether *path* sits in tooling, docs or examples rather than the system."""
    if "/" not in path:
        return False
    directory = path.rsplit("/", 1)[0] + "/"
    # Below a JVM source root the directories are a package name: ``com/example``
    # is code, not an examples folder.
    marker = re.search(r"(^|/)src/(main|test)/", directory)
    if marker:
        directory = directory[: marker.start() + 1]
    return bool(_PERIPHERAL.search(directory))


def _architecture_rank(entry: object) -> tuple[int, int]:
    path: str = entry.path  # type: ignore[attr-defined]
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    peripheral = 1 if (is_peripheral(path) or is_test_path(path)) else 0
    component = 0 if _COMPONENT_NAME.search(name) else 1
    return (peripheral, component)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def line_of(text: str, position: int) -> int:
    """1-based line number of a character offset."""
    return text.count("\n", 0, position) + 1


def find_all(pattern: re.Pattern[str], text: str) -> Iterator[tuple[int, re.Match[str]]]:
    """Every match, with its line number."""
    for match in pattern.finditer(text):
        yield line_of(text, match.start()), match
