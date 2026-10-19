"""Read-only tools for investigating a repository.

Each tool wraps code that already exists in :mod:`repolab.ingest`, so the agentic
route uses the same detection logic as the deterministic pipeline rather than a
parallel implementation.

All seven are read-only, so none requires approval. The filesystem boundary is
enforced here, in deterministic code: ``read_file`` and ``search_code`` resolve
every path against the clone root and refuse anything that escapes it, so no
prompt can talk an agent into reading outside the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from repolab.agents.models import ToolResult
from repolab.ingest.content import ReadBudget, find_lines, read_text, reference
from repolab.ingest.detectors.base import DetectionContext, DetectorRun
from repolab.ingest.detectors.dependencies import detect_configuration, detect_dependencies
from repolab.ingest.detectors.entrypoints import detect_entrypoints, detect_tests
from repolab.ingest.detectors.services import detect_services
from repolab.ingest.detectors.structure import detect_structure
from repolab.ingest.detectors.surfaces import detect_surfaces
from repolab.ingest.priority import prioritized
from repolab.interfaces import ResearchProvider
from repolab.manifest.models import Dependency, EntryPoint, Service

#: Files listed in one `list_files` result.
MAX_LISTING = 200

#: Lines returned from one `read_file` call.
MAX_FILE_LINES = 400

#: Matches returned from one `search_code` call.
MAX_MATCHES = 40

#: Official documentation domains, so research stays on primary sources.
OFFICIAL_DOMAINS: tuple[str, ...] = (
    "spark.apache.org",
    "cassandra.apache.org",
    "airflow.apache.org",
    "docs.python.org",
    "kafka.apache.org",
)


class PathEscapesRepositoryError(ValueError):
    """A tool was asked for a path outside the repository clone."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"refusing to read {path!r}: outside the repository")


def resolve_within(root: Path, candidate: str) -> Path:
    """Resolve *candidate* under *root*, refusing anything that escapes.

    Covers absolute paths, ``..`` traversal and symlinks that point outside the
    clone, because ``resolve()`` follows links before the containment check.
    """
    if not candidate or candidate.strip() in {"", "."}:
        raise PathEscapesRepositoryError(candidate)

    root_resolved = root.resolve()
    target = (root_resolved / candidate).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise PathEscapesRepositoryError(candidate)
    return target


class RepositoryToolContext:
    """Shared state for one investigation.

    Holds the detection context and the read budget so every tool contributes to
    the same bounded set of findings.
    """

    def __init__(
        self,
        context: DetectionContext,
        budget: ReadBudget,
        *,
        research: ResearchProvider | None = None,
    ) -> None:
        self.context = context
        self.budget = budget
        self.research = research
        self.run = DetectorRun()
        """Accumulated findings, in the same shape the classifier expects."""

        # Structured results the detectors produce alongside their findings. The
        # investigator assembles these into a DetectionOutcome once the run ends.
        self.dependencies: list[Dependency] = []
        self.entrypoints: list[EntryPoint] = []
        self.services: list[Service] = []

    @property
    def root(self) -> Path:
        return Path(self.context.root)

    def absorb(self, run: DetectorRun) -> DetectorRun:
        self.run.extend(run)
        return run


# ---------------------------------------------------------------------------
# Argument schemas
# ---------------------------------------------------------------------------


class ListFilesArgs(BaseModel):
    directory: str = Field(default="", description="Repository-relative directory")
    extension: str | None = Field(default=None, description="Filter, e.g. '.py'")
    limit: int = Field(default=MAX_LISTING, gt=0, le=MAX_LISTING)


class ReadFileArgs(BaseModel):
    path: str = Field(description="Repository-relative file path")
    max_lines: int = Field(default=MAX_FILE_LINES, gt=0, le=MAX_FILE_LINES)


class SearchCodeArgs(BaseModel):
    query: str = Field(min_length=2, description="Literal text to find")
    extension: str | None = None
    limit: int = Field(default=MAX_MATCHES, gt=0, le=MAX_MATCHES)


class NoArgs(BaseModel):
    """For tools that inspect the whole repository."""


class SearchDocsArgs(BaseModel):
    query: str = Field(min_length=2)
    domain: str | None = Field(default=None, description="Official domain to restrict to")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ListFilesTool:
    name = "list_files"
    description = "List repository files, optionally filtered by directory or extension."
    requires_approval = False
    arguments_model = ListFilesArgs

    def __init__(self, tools: RepositoryToolContext) -> None:
        self._tools = tools

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = ListFilesArgs.model_validate(arguments)
        entries = self._tools.context.tree.files

        if args.directory:
            prefix = args.directory.strip("/").lower()
            entries = [e for e in entries if e.path.lower().startswith(prefix)]
        if args.extension:
            wanted = args.extension if args.extension.startswith(".") else f".{args.extension}"
            entries = [e for e in entries if e.extension == wanted.lower()]

        selected = prioritized(entries)[: args.limit]
        return ToolResult(
            summary=f"listed {len(selected)} of {len(entries)} matching files",
            data={
                "paths": [entry.path for entry in selected],
                "total_matching": len(entries),
                "truncated": len(entries) > len(selected),
            },
        )


class ReadFileTool:
    name = "read_file"
    description = "Read one repository file as text, bounded by the read budget."
    requires_approval = False
    arguments_model = ReadFileArgs

    def __init__(self, tools: RepositoryToolContext) -> None:
        self._tools = tools

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = ReadFileArgs.model_validate(arguments)
        try:
            target = resolve_within(self._tools.root, args.path)
        except PathEscapesRepositoryError as exc:
            return ToolResult.failure(str(exc))

        if not target.is_file():
            return ToolResult.failure(f"{args.path} is not a file in this repository")

        text = read_text(target, budget=self._tools.budget)
        if text is None:
            return ToolResult.failure(
                f"{args.path} could not be read (binary, too large, or budget spent)"
            )

        lines = text.splitlines()
        shown = lines[: args.max_lines]
        return ToolResult(
            summary=f"read {args.path} ({len(shown)} of {len(lines)} lines)",
            data={
                "path": args.path,
                "lines": shown,
                "total_lines": len(lines),
                "truncated": len(lines) > len(shown),
            },
        )


class SearchCodeTool:
    name = "search_code"
    description = "Find literal text across repository source files."
    requires_approval = False
    arguments_model = SearchCodeArgs

    def __init__(self, tools: RepositoryToolContext) -> None:
        self._tools = tools

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = SearchCodeArgs.model_validate(arguments)
        entries = self._tools.context.tree.files
        if args.extension:
            wanted = args.extension if args.extension.startswith(".") else f".{args.extension}"
            entries = [e for e in entries if e.extension == wanted.lower()]

        matches: list[dict[str, Any]] = []
        run = DetectorRun()

        for entry in prioritized(entries):
            if len(matches) >= args.limit:
                break
            try:
                target = resolve_within(self._tools.root, entry.path)
            except PathEscapesRepositoryError:
                continue

            text = read_text(target, budget=self._tools.budget)
            if text is None:
                continue
            for line, content in find_lines(text, (args.query,), limit=2):
                matches.append({"path": entry.path, "line": line, "text": content[:200]})
                run.add(
                    "search_match",
                    content[:120],
                    detail=entry.path,
                    references=[
                        reference(
                            entry.path,
                            commit_sha=self._tools.context.commit_sha,
                            line=line,
                            excerpt=content,
                        )
                    ],
                )
                if len(matches) >= args.limit:
                    break

        self._tools.absorb(run)
        return ToolResult(
            summary=f"found {len(matches)} match(es) for {args.query!r}",
            data={"query": args.query, "matches": matches},
        )


class _DetectorTool:
    """Base for tools that delegate to an existing detector."""

    name = ""
    description = ""
    requires_approval = False
    arguments_model: type[BaseModel] = NoArgs

    def __init__(self, tools: RepositoryToolContext) -> None:
        self._tools = tools

    def _detect(self) -> tuple[DetectorRun, dict[str, Any]]:
        raise NotImplementedError

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.arguments_model.model_validate(arguments)
        run, extra = self._detect()
        self._tools.absorb(run)
        return ToolResult(
            summary=f"{self.name}: {len(run.findings)} finding(s)",
            data={
                "findings": len(run.findings),
                "kinds": sorted({f.kind for f in run.findings}),
                **extra,
            },
        )


class InspectDependenciesTool(_DetectorTool):
    name = "inspect_dependencies"
    description = "Parse dependency manifests and build files. Never executes them."

    def _detect(self) -> tuple[DetectorRun, dict[str, Any]]:
        run, dependencies = detect_dependencies(self._tools.context, self._tools.budget)
        self._tools.dependencies = dependencies
        return run, {
            "dependencies": len(dependencies),
            "frameworks": sorted(set(run.values_of("framework"))),
        }


class InspectConfigTool(_DetectorTool):
    name = "inspect_config"
    description = "Read configuration files and note significant settings."

    def _detect(self) -> tuple[DetectorRun, dict[str, Any]]:
        run = detect_configuration(self._tools.context, self._tools.budget)
        return run, {"config_files": run.values_of("configuration")[:20]}


class DetectEntrypointsTool(_DetectorTool):
    name = "detect_entrypoints"
    description = "Find how the software is intended to be started, and its tests."

    def _detect(self) -> tuple[DetectorRun, dict[str, Any]]:
        run, entrypoints = detect_entrypoints(self._tools.context, self._tools.budget)
        run.extend(detect_tests(self._tools.context))
        self._tools.entrypoints = entrypoints
        return run, {"entrypoints": [e.name for e in entrypoints][:20]}


class DetectServicesTool(_DetectorTool):
    name = "detect_services"
    description = "Find external services from Dockerfiles and compose files."

    def _detect(self) -> tuple[DetectorRun, dict[str, Any]]:
        run, services = detect_services(self._tools.context, self._tools.budget)
        self._tools.services = services
        return run, {"services": [s.name for s in services]}


class DetectSurfacesTool(_DetectorTool):
    name = "detect_surfaces"
    description = "Scan source files for interaction-surface signals."

    def _detect(self) -> tuple[DetectorRun, dict[str, Any]]:
        run = detect_surfaces(self._tools.context, self._tools.budget)
        run.extend(detect_structure(self._tools.context))
        return run, {}


class SearchOfficialDocsTool:
    name = "search_official_docs"
    description = "Search official documentation, restricted to primary-source domains."
    requires_approval = False
    arguments_model = SearchDocsArgs

    def __init__(self, tools: RepositoryToolContext) -> None:
        self._tools = tools

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = SearchDocsArgs.model_validate(arguments)
        if self._tools.research is None:
            return ToolResult.failure("no research provider configured")

        domains = [args.domain] if args.domain else list(OFFICIAL_DOMAINS)
        try:
            hits = await self._tools.research.search(
                args.query, include_domains=domains, max_results=3
            )
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")

        return ToolResult(
            summary=f"found {len(hits)} documentation result(s)",
            data={
                "results": [
                    {"url": hit.url, "title": hit.title, "excerpt": (hit.content or "")[:300]}
                    for hit in hits
                ]
            },
        )


def build_repository_tools(
    context: DetectionContext,
    budget: ReadBudget,
    *,
    research: ResearchProvider | None = None,
) -> tuple[RepositoryToolContext, list[Any]]:
    """The tool set a repository investigator is allowed to use."""
    tools = RepositoryToolContext(context, budget, research=research)
    return tools, [
        ListFilesTool(tools),
        ReadFileTool(tools),
        SearchCodeTool(tools),
        InspectDependenciesTool(tools),
        InspectConfigTool(tools),
        DetectEntrypointsTool(tools),
        DetectServicesTool(tools),
        DetectSurfacesTool(tools),
        SearchOfficialDocsTool(tools),
    ]
