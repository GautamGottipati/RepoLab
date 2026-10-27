"""Reading the architecture together with the artifacts that refine it.

The investigation owns the architecture graph and is its only writer. On-demand
refinements (``architecture.runtime_naming``) keep their result in their own
artifact payload, applied here on read -- so the two never race.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from repolab.architecture.models import ArchitectureGraph, Flow, ReadingItem, RuntimeModel
from repolab.artifacts import store
from repolab.artifacts.registry import ARCH_RUNTIME_NAMING, SPECS, Status

PREFIX = "architecture."


def overlay_runtime_naming(
    graph: ArchitectureGraph, payload: dict[str, Any] | None
) -> ArchitectureGraph:
    """*graph* with a ready runtime-naming result applied. ``payload`` may be empty."""
    if not payload or "runtime" not in payload:
        return graph
    graph.runtime = RuntimeModel.model_validate(payload["runtime"])
    graph.flows = [Flow.model_validate(f) for f in payload.get("flows", [])]
    graph.reading_path = [ReadingItem.model_validate(r) for r in payload.get("reading_path", [])]
    return graph


def architecture_sections(session: Session, repo_url: str, commit: str) -> dict[str, str]:
    """``{"structure": "ready", "runtime_naming": "not_requested", ...}``."""
    return {
        r.artifact_type.removeprefix(PREFIX): r.status
        for r in store.all_for(session, repo_url, commit)
        if r.artifact_type.startswith(PREFIX) and r.artifact_type in SPECS
    }


def with_refinements(
    session: Session, repo_url: str, commit: str, graph: ArchitectureGraph
) -> ArchitectureGraph:
    record = store.get(session, repo_url, commit, ARCH_RUNTIME_NAMING)
    if record is not None and Status(record.status).usable:
        return overlay_runtime_naming(graph, record.payload)
    return graph
