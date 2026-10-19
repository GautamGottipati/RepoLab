"""Interaction-surface classification.

Classification order, per CLAUDE.md:

1. deterministic rules
2. repository evidence
3. LLM reasoning **only** when signals conflict or are ambiguous

The result is one or more :class:`~repolab.manifest.models.InteractionSurface`
objects plus a recommended primary experience. A repository is never reduced to
exactly one type, and every surface carries the evidence and the reason behind it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repolab.classify.ambiguity import (
    explain_gate,
    has_strong_evidence,
    is_ambiguous,
    should_consult_model,
    tie_gap,
)
from repolab.classify.llm import SurfaceJudgement, break_tie
from repolab.classify.rules import classify_deterministic
from repolab.classify.signals import STRONG_CONFIDENCE, SURFACE_PRECEDENCE
from repolab.llm import ModelRouter
from repolab.manifest.models import Classification, EntryPoint, Service

if TYPE_CHECKING:
    # Annotation only -- see the note in repolab.classify.rules.
    from repolab.ingest.detectors.base import DetectorRun

__all__ = [
    "STRONG_CONFIDENCE",
    "SURFACE_PRECEDENCE",
    "Classification",
    "SurfaceJudgement",
    "break_tie",
    "classify",
    "classify_deterministic",
    "explain_gate",
    "has_strong_evidence",
    "is_ambiguous",
    "should_consult_model",
    "tie_gap",
]


async def classify(
    run: DetectorRun,
    *,
    entrypoints: list[EntryPoint] | None = None,
    services: list[Service] | None = None,
    router: ModelRouter | None = None,
) -> Classification:
    """Classify a repository from its detected signals.

    Deterministic rules decide the answer. A model is consulted only when those
    rules are genuinely ambiguous *and* a router is available; otherwise the
    deterministic result stands and the reason is recorded.
    """
    classification = classify_deterministic(run, entrypoints=entrypoints, services=services)
    classification.notes.append(explain_gate(classification))

    if not should_consult_model(classification):
        return classification

    if router is None:
        classification.notes.append(
            "tie-break skipped: no model router configured, so the deterministic result stands"
        )
        return classification

    resolved, error = await break_tie(router, classification)
    if error:
        resolved.notes.append(error)
    return resolved
