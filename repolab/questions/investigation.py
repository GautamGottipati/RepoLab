"""A read-only investigation shaped by the question rather than by Spark.

The experiment investigator searches for shuffle settings because that is what
the Spark question is about. A question about Cassandra, or about where a parser
lives, needs the same tools pointed elsewhere: the repository's own overview
first, then the question's words in the code, then the files those searches hit,
then the project's official documentation -- and only its official
documentation, on a domain this module chose, never one a model named.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from repolab.agents.models import AgentRun, ToolRequest
from repolab.agents.repository_tools import OFFICIAL_DOMAINS
from repolab.experiments.investigation import question_terms
from repolab.manifest.models import RepoManifest

#: Framework names, as detectors report them, to their official documentation.
_FRAMEWORK_DOMAINS: dict[str, str] = {
    "spark": "spark.apache.org",
    "pyspark": "spark.apache.org",
    "cassandra": "cassandra.apache.org",
    "airflow": "airflow.apache.org",
    "kafka": "kafka.apache.org",
}

#: Preferred README formats, best first. Any other top-level ``README*`` is
#: still used -- Cassandra's is ``README.asc``, which a fixed list once missed.
_OVERVIEW_PREFERENCE = (".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".asc", ".txt", "")


#: Local imports in a file the investigation read: ``require('../models/post')``,
#: ``import x from './db'``, ``from .models import Post``. Package imports are
#: left alone -- they are not this repository's code.
_LOCAL_IMPORT = re.compile(
    r"""require\(\s*['"](?P<js>\.{1,2}/[^'"]+)['"]\s*\)"""
    r"""|\bfrom\s+['"](?P<es>\.{1,2}/[^'"]+)['"]"""
    r"""|\bimport\s+['"](?P<side>\.{1,2}/[^'"]+)['"]"""
    r"""|^\s*from\s+(?P<py>\.+[\w.]*)\s+import\b""",
    re.M,
)

_MODULE_SUFFIXES = ("", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", "/index.js", "/index.ts")


def local_imports(path: str, lines: list[str], paths: set[str]) -> list[str]:
    """Repository files that *path* imports, resolved against the real tree."""
    found: list[str] = []
    base = posixpath.dirname(path)
    for match in _LOCAL_IMPORT.finditer("\n".join(lines)):
        spec = match.group("js") or match.group("es") or match.group("side")
        candidates: list[str] = []
        if spec:
            joined = posixpath.normpath(posixpath.join(base, spec))
            candidates = [joined + suffix for suffix in _MODULE_SUFFIXES]
        elif match.group("py"):
            dotted = match.group("py")
            level = len(dotted) - len(dotted.lstrip("."))
            parent = base
            for _ in range(level - 1):
                parent = posixpath.dirname(parent)
            module = dotted.lstrip(".").replace(".", "/")
            if module:
                stem = posixpath.join(parent, module)
                candidates = [stem + ".py", stem + "/__init__.py"]
        for candidate in candidates:
            if candidate in paths and candidate != path and candidate not in found:
                found.append(candidate)
                break
    return found


def official_domain(manifest: RepoManifest | None, repo_url: str) -> str | None:
    """The official documentation domain for this repository, if it has one.

    From a detected framework first, then from the repository itself: Cassandra's
    manifest names no framework, because Cassandra *is* the framework, but
    ``github.com/apache/cassandra`` plainly documents itself on
    ``cassandra.apache.org``. Only domains on the official list are returned.
    """
    if manifest is not None:
        for framework in manifest.frameworks:
            domain = _FRAMEWORK_DOMAINS.get(framework.strip().lower())
            if domain is not None:
                return domain

    parts = repo_url.rstrip("/").lower().split("/")
    if len(parts) >= 2 and parts[-2] == "apache":
        candidate = f"{parts[-1].removesuffix('.git')}.apache.org"
        if candidate in OFFICIAL_DOMAINS:
            return candidate
    return None


def overview_file(paths: list[str]) -> str | None:
    """The repository's top-level README, if it has one."""
    candidates = [path for path in paths if "/" not in path and path.lower().startswith("readme")]

    def rank(path: str) -> tuple[int, str]:
        suffix = path[len("readme") :].lower()
        known = suffix if suffix in _OVERVIEW_PREFERENCE else None
        return (_OVERVIEW_PREFERENCE.index(known) if known is not None else 99, path)

    return min(candidates, key=rank) if candidates else None


class QuestionInvestigationPlanner:
    """Plans a short, read-only investigation for one question."""

    name = "question_investigation"

    def __init__(
        self,
        question: str,
        *,
        overview: str | None = None,
        domain: str | None = None,
        research_available: bool = False,
        subject: str = "",
        max_reads: int = 2,
        focus_paths: list[str] | None = None,
        paths: set[str] | None = None,
        max_imports: int = 6,
    ) -> None:
        self.question = question
        self._overview = overview
        self._domain = domain if research_available else None
        self._subject = subject
        # The project's own name matches nearly every file in the project, so it
        # finds nothing in particular; the README and the docs cover it instead.
        self._terms = [
            term for term in question_terms(question, limit=5) if term.lower() != subject.lower()
        ][:4]
        self._max_reads = max_reads
        self._focus = list(dict.fromkeys(focus_paths or []))[:4]
        """Files of the component the question was asked about: read first."""

        self._paths = paths or set()
        self._all_terms = question_terms(question, limit=12)
        self._max_imports = max_imports
        """Local modules the read files import -- the model behind a route, the
        client behind a service -- read so an answer can show the next hop."""

    async def next_request(self, run: AgentRun, context: dict[str, Any]) -> ToolRequest | None:
        done = [step.request for step in run.steps if step.request is not None]
        tools = {request.tool for request in done}

        read = {
            str(request.arguments.get("path")) for request in done if request.tool == "read_file"
        }
        for path in self._focus:
            if path not in read:
                return ToolRequest(
                    tool="read_file",
                    arguments={"path": path, "max_lines": 160},
                    reason=f"Read {path}, which implements the component asked about",
                )
        if self._overview and self._overview not in read:
            return ToolRequest(
                tool="read_file",
                arguments={"path": self._overview, "max_lines": 120},
                reason="Read what the repository says about itself",
            )

        searched = {
            str(request.arguments.get("query")) for request in done if request.tool == "search_code"
        }
        for term in self._terms:
            if term not in searched:
                return ToolRequest(
                    tool="search_code",
                    arguments={"query": term},
                    reason=f"Find where {term} appears in the repository",
                )

        hits = [path for path in hit_paths(run) if path not in read]
        reads_done = len(read - {self._overview} - set(self._focus))
        if hits and reads_done < self._max_reads:
            return ToolRequest(
                tool="read_file",
                arguments={"path": hits[0], "max_lines": 120},
                reason=f"Read {hits[0]}, where the search matched",
            )

        imports_read = sum(
            1 for request in done if request.tool == "read_file" and "imports" in request.reason
        )
        if imports_read < self._max_imports:
            candidates = imported_paths(run, self._paths)
            # The modules the question names first: asked about posts and users,
            # the post and user models matter more than the logger.
            candidates.sort(key=lambda pair: -_term_hits(pair[0], self._all_terms))
            for path, importer in candidates:
                if path not in read:
                    return ToolRequest(
                        tool="read_file",
                        arguments={"path": path, "max_lines": 80},
                        reason=f"Read {path}, which {importer} imports",
                    )

        if self._domain and "search_official_docs" not in tools:
            query = f"{self._subject} {self.question}".strip()
            return ToolRequest(
                tool="search_official_docs",
                arguments={"query": query[:300], "domain": self._domain},
                reason=f"Check what the official documentation on {self._domain} says",
            )
        return None


def _term_hits(path: str, terms: list[str]) -> int:
    parts = [part for part in re.split(r"[^a-z0-9]+", path.lower()) if len(part) > 2]
    return sum(
        1
        for term in terms
        if any(part.startswith(term.lower()) or term.lower().startswith(part) for part in parts)
    )


def imported_paths(run: AgentRun, paths: set[str]) -> list[tuple[str, str]]:
    """Repository files imported by the files the investigation read, with the
    file that imports each, in the order they were read."""
    found: dict[str, str] = {}
    for step in run.steps:
        if step.request is None or step.request.tool != "read_file" or step.result is None:
            continue
        if not step.result.ok:
            continue
        path = step.result.data.get("path")
        lines = step.result.data.get("lines") or []
        if not isinstance(path, str):
            continue
        for target in local_imports(path, [str(line) for line in lines], paths):
            found.setdefault(target, path)
    return list(found.items())


def hit_paths(run: AgentRun) -> list[str]:
    """Repository paths the code searches matched, most-matched first."""
    counts: dict[str, int] = {}
    for step in run.steps:
        if step.request is None or step.request.tool != "search_code" or step.result is None:
            continue
        for match in step.result.data.get("matches", []):
            path = match.get("path")
            if isinstance(path, str):
                counts[path] = counts.get(path, 0) + 1
    return sorted(counts, key=lambda path: (-counts[path], path))
