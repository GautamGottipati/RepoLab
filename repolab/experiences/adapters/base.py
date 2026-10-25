"""The adapter contract, and what every adapter is given to decide with."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from repolab.architecture.models import ArchitectureGraph
from repolab.experiences.catalog import ExperienceCatalog
from repolab.experiences.model import ExperienceDefinition
from repolab.manifest.models import RepoManifest


@dataclass
class DiscoveryContext:
    """Everything known without touching a container."""

    manifest: RepoManifest
    catalog: ExperienceCatalog
    architecture: ArchitectureGraph | None = None
    builds: dict[tuple[str, str], str] = field(default_factory=dict)
    """(adapter, strategy) -> build status for this commit: building, ready, failed."""

    build_images: dict[tuple[str, str], str] = field(default_factory=dict)
    build_reasons: dict[tuple[str, str], str] = field(default_factory=dict)
    """Why a build failed, as the worker recorded it -- shown on the option."""

    sessions: dict[str, str] = field(default_factory=dict)
    """experience id -> the id of a live session for it."""

    live_services: dict[str, str] = field(default_factory=dict)
    """Phase 18 runtime: service name -> status."""

    surface_confidence: dict[str, float] = field(default_factory=dict)


class Adapter(Protocol):
    name: str

    def propose(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        """The experiences this adapter can offer for the repository. May be empty."""
        ...


def readme_mentions(context: DiscoveryContext, *words: str) -> float:
    """1.0 when the README's quickstart material names one of *words*."""
    facts = context.manifest.facts
    text = " ".join(
        [
            facts.spark.quickstart.code if facts and facts.spark and facts.spark.quickstart else "",
            " ".join(s.code for s in facts.python.snippets) if facts and facts.python else "",
        ]
    ).lower()
    return 1.0 if any(word.lower() in text for word in words) else 0.0


def experience_id(adapter: str, slug: str) -> str:
    return f"{adapter}:{slug}"


def as_dict(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value
