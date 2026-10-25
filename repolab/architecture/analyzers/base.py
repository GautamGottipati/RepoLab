"""The analyzer protocol.

An analyzer looks for one *role* a component can play -- serving HTTP, parsing
commands, moving data, scheduling tasks -- in whatever language the repository
is written in, and adds the components and relationships it can evidence to the
shared graph. It never removes anything another analyzer found.

Framework adapters use the same protocol but only *enrich*: they set a subtype
or technology on nodes the generic analyzers already produced. Deleting every
adapter must still leave a useful graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from repolab.architecture.models import ArchitectureGraph, EdgeType
from repolab.architecture.source import SourceIndex
from repolab.ingest.detectors import DetectionOutcome
from repolab.manifest.models import RepoManifest


class Usage(NamedTuple):
    """A file -- or, when known, a specific component in it -- that uses a component."""

    target: str
    path: str
    line: int | None
    edge: EdgeType
    user: str | None = None


@dataclass
class AnalysisContext:
    """What every analyzer may read. Nothing here executes repository code."""

    index: SourceIndex
    outcome: DetectionOutcome
    manifest: RepoManifest | None
    graph: ArchitectureGraph
    notes: list[str] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    """Uses of one component by code elsewhere.

    Analyzers record these; assembly turns each into an edge from whichever
    component that file implements, so a relationship is only drawn where a
    real file both implements one end and uses the other.
    """

    @property
    def dependency_names(self) -> set[str]:
        return {dep.name.lower() for dep in self.outcome.dependencies}

    @property
    def frameworks(self) -> set[str]:
        values = set(self.outcome.run.values_of("framework"))
        if self.manifest is not None:
            values.update(self.manifest.frameworks)
        return {value.lower() for value in values}


class ArchitectureAnalyzer(Protocol):
    name: str

    def analyze(self, context: AnalysisContext) -> None: ...


def slug(text: str) -> str:
    """A stable, readable id fragment."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:60] or "x"


def stem(path: str) -> str:
    """``api/routes/orders.py`` -> ``orders``."""
    name = path.rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def title(text: str) -> str:
    """``order_service`` -> ``Order Service``."""
    words = re.split(r"[_\-\s]+", text.strip())
    return " ".join(word[:1].upper() + word[1:] for word in words if word)
