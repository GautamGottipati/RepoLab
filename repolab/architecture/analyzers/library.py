"""Libraries, and configuration.

A library has no server, command or pipeline: its architecture is its public
API and the modules behind it. Only used when nothing more specific was found,
so an application is never redescribed as a library because it has a package.

Configuration files are linked to the components whose source names them.
"""

from __future__ import annotations

import re

from repolab.architecture.analyzers.base import AnalysisContext, Usage, slug, title
from repolab.architecture.models import (
    ArchitectureEdge,
    ArchitectureNode,
    EdgeType,
    NodeCategory,
)
from repolab.architecture.source import find_all, is_test_path

_RUNTIME = {
    NodeCategory.API,
    NodeCategory.CLI,
    NodeCategory.PIPELINE,
    NodeCategory.SCHEDULER,
    NodeCategory.FRONTEND,
    NodeCategory.SERVICE,
    NodeCategory.WORKER,
}
_RELATIVE_IMPORT = re.compile(
    r"^\s*from\s+\.(?P<mod>\w+)\s+import\b|^\s*from\s+(?P<pkg>\w+)\.(?P<mod2>\w+)\s+import\b", re.M
)
_ALL = re.compile(r"^__all__\s*=", re.M)
MAX_MODULES = 6
MAX_CONFIGS = 3


class LibraryAnalyzer:
    name = "library"

    def analyze(self, context: AnalysisContext) -> None:
        if any(node.category in _RUNTIME for node in context.graph.nodes):
            return

        package = _primary_package(context)
        if package is None:
            return
        init = f"{package}/__init__.py"
        text = context.index.text(init) or ""
        exported = _ALL.search(text)
        api = context.graph.add_node(
            ArchitectureNode(
                id=f"library:{slug(package)}",
                label=f"Public API ({package.rsplit('/', 1)[-1]})",
                category=NodeCategory.LIBRARY,
                source_references=[
                    context.index.ref(
                        init, (text.count("\n", 0, exported.start()) + 1) if exported else 1
                    )
                ],
                confidence=0.75,
                detected_because=f"{init} is the package users import",
            )
        )
        name = package.rsplit("/", 1)[-1]
        seen: set[str] = set()
        for line, match in find_all(_RELATIVE_IMPORT, text):
            module = match.group("mod") or (
                match.group("mod2") if match.group("pkg") == name else None
            )
            if not module or module in seen or len(seen) >= MAX_MODULES:
                continue
            seen.add(module)
            module_path = next(
                (
                    p
                    for p in (f"{package}/{module}.py", f"{package}/{module}/__init__.py")
                    if p in context.index.all_paths
                ),
                None,
            )
            references = (
                [context.index.ref(module_path, 1)]
                if module_path
                else [context.index.ref(init, line)]
            )
            core = context.graph.add_node(
                ArchitectureNode(
                    id=f"core:{slug(package)}:{slug(module)}",
                    label=title(module),
                    category=NodeCategory.CORE_ENGINE,
                    source_references=references,
                    confidence=0.7,
                    detected_because=f"the public API in {init} exposes it",
                )
            )
            context.graph.add_edge(
                ArchitectureEdge(
                    source=api.id,
                    target=core.id,
                    type=EdgeType.DEPENDS_ON,
                    evidence=[context.index.ref(init, line)],
                    description="exposes",
                )
            )

        # Modules that use each other, cited at the import.
        cores = {
            n.id.rsplit(":", 1)[-1]: n
            for n in context.graph.nodes
            if n.category is NodeCategory.CORE_ENGINE
        }
        for core in list(cores.values()):
            for path in core.paths:
                for line, match in find_all(_RELATIVE_IMPORT, context.index.text(path) or ""):
                    module = match.group("mod") or match.group("mod2")
                    other = cores.get(slug(module or ""))
                    if other is not None and other.id != core.id:
                        context.graph.add_edge(
                            ArchitectureEdge(
                                source=core.id,
                                target=other.id,
                                type=EdgeType.DEPENDS_ON,
                                evidence=[context.index.ref(path, line)],
                                description="uses",
                            )
                        )


class ConfigurationAnalyzer:
    name = "configuration"

    def analyze(self, context: AnalysisContext) -> None:
        for finding in context.outcome.run.of_kind("configuration")[:MAX_CONFIGS]:
            references = finding.references[:1]
            if not references or is_test_path(references[0].path):
                continue
            path = references[0].path
            filename = path.rsplit("/", 1)[-1]
            node = context.graph.add_node(
                ArchitectureNode(
                    id=f"config:{slug(path)}",
                    label=f"{filename} configuration",
                    category=NodeCategory.CONFIGURATION,
                    source_references=list(references),
                    confidence=0.8,
                    detected_because=f"{path} is a configuration file",
                )
            )
            for user_path, text in context.index.source_files():
                index = text.find(filename)
                if index != -1 and user_path != path:
                    context.usages.append(
                        Usage(
                            node.id, user_path, text.count("\n", 0, index) + 1, EdgeType.DEPENDS_ON
                        )
                    )


def _primary_package(context: AnalysisContext) -> str | None:
    """The importable package this repository publishes, if there is one."""
    candidates = sorted(
        p.rsplit("/", 1)[0]
        for p in context.index.all_paths
        if p.endswith("/__init__.py") and p.count("/") <= 2 and not is_test_path(p)
    )
    top = [
        c
        for c in candidates
        if not any(c != other and c.startswith(other + "/") for other in candidates)
    ]
    if not top:
        return None
    repo = (
        (context.manifest.repo_url.rstrip("/").rsplit("/", 1)[-1] if context.manifest else "")
        .lower()
        .replace("-", "_")
    )
    named = [c for c in top if c.rsplit("/", 1)[-1].lower() == repo]
    if named:
        return named[0]
    return max(top, key=lambda c: sum(1 for p in context.index.all_paths if p.startswith(c + "/")))
