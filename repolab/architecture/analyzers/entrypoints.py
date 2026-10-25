"""Where execution starts, from the manifest the investigation already built."""

from __future__ import annotations

import re

from repolab.architecture.analyzers.base import AnalysisContext, slug
from repolab.architecture.models import ArchitectureNode, NodeCategory
from repolab.architecture.source import is_peripheral
from repolab.manifest.models import EntryPoint, EntryPointKind

_MAIN = re.compile(
    r"if\s+__name__\s*==\s*['\"]__main__['\"]|public\s+static\s+void\s+main\s*\(|^func\s+main\s*\(|"
    r"^def\s+main\s*\(",
    re.M,
)


#: A package script, as the manifest names it (``npm run build``).
_PACKAGE_SCRIPT = re.compile(r"^(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?(?P<script>\S+)$")

#: The package scripts that run the system. The rest build, lint, test or
#: package it, which is tooling, not architecture.
_RUNS_THE_SYSTEM = frozenset({"start", "dev", "serve"})

_DOCUMENTS = (".md", ".rst", ".txt", ".adoc", ".asciidoc", ".asc")


class EntryPointAnalyzer:
    name = "entrypoints"

    def analyze(self, context: AnalysisContext) -> None:
        entries: list[EntryPoint] = (
            list(context.manifest.entrypoints) if context.manifest else []
        ) or list(context.outcome.entrypoints)

        for entry in entries:
            # A test suite is how the code is checked, not how it runs; a command
            # quoted in documentation is an example, not code.
            if entry.kind is EntryPointKind.TEST_SUITE:
                continue
            if entry.path and (
                entry.path.lower().endswith(_DOCUMENTS) or is_peripheral(entry.path)
            ):
                continue
            script = _PACKAGE_SCRIPT.match(entry.name.strip())
            if script and script.group("script") not in _RUNS_THE_SYSTEM:
                continue
            references = []
            if entry.path:
                text = context.index.text(entry.path)
                match = _MAIN.search(text) if text else None
                line = text.count("\n", 0, match.start()) + 1 if (text and match) else None
                references.append(context.index.ref(entry.path, line))
            if not references:
                continue
            node = context.graph.add_node(
                ArchitectureNode(
                    id=f"entry:{slug(entry.name)}",
                    label=entry.name,
                    category=NodeCategory.ENTRY_POINT,
                    subtype=entry.kind.value,
                    description=f"Started with `{entry.command}`",
                    source_references=references,
                    properties={"command": entry.command},
                    confidence=0.85,
                    detected_because=f"declared as an entry point: {entry.command}",
                )
            )
            if node.id not in context.graph.entry_points:
                context.graph.entry_points.append(node.id)
