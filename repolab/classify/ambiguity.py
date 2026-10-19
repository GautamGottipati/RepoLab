"""The gate that decides whether a model may be consulted.

CLAUDE.md is explicit: deterministic rules first, repository evidence second, and
LLM reasoning *only* when signals conflict. This module is that rule, isolated so
it can be read and tested on its own:

    ambiguous  <=>  no surface is strongly supported
                    AND (the top two are within TIE_MARGIN
                         OR nothing specific was detected at all)

The first clause is the important one. A repository with one strong signal is
never sent to a model, because asking a model to confirm something the
repository already states plainly costs money, adds latency, and can only make
the answer less certain.
"""

from __future__ import annotations

from repolab.classify.signals import STRONG_CONFIDENCE, TIE_MARGIN
from repolab.manifest.models import Classification, InteractionSurface, SurfaceType


def strongest(surfaces: list[InteractionSurface]) -> InteractionSurface | None:
    return max(surfaces, key=lambda s: s.confidence, default=None)


def has_strong_evidence(surfaces: list[InteractionSurface]) -> bool:
    """Whether any surface is supported well enough to stand alone."""
    return any(s.confidence >= STRONG_CONFIDENCE for s in surfaces)


def is_only_fallback(surfaces: list[InteractionSurface]) -> bool:
    """Whether nothing beyond the terminal fallback was detected."""
    return bool(surfaces) and all(s.type is SurfaceType.TERMINAL for s in surfaces)


def tie_gap(surfaces: list[InteractionSurface]) -> float | None:
    """Confidence gap between the top two surfaces, or ``None`` if there is one."""
    if len(surfaces) < 2:
        return None
    ranked = sorted(surfaces, key=lambda s: s.confidence, reverse=True)
    return round(ranked[0].confidence - ranked[1].confidence, 4)


#: A model can only *choose between* candidates, so fewer than this many makes a
#: call pointless however uncertain the result is.
MIN_CANDIDATES_FOR_TIE_BREAK = 2


def is_ambiguous(surfaces: list[InteractionSurface]) -> bool:
    """Whether the evidence is too weak or too close to decide confidently.

    The single definition of ambiguity: both the flag RepoLab reports and the gate
    it acts on come from here, so the two cannot drift apart.
    """
    if has_strong_evidence(surfaces):
        return False
    if len(surfaces) < 2:
        return True
    gap = tie_gap(surfaces)
    return gap is not None and gap <= TIE_MARGIN


def should_consult_model(classification: Classification) -> bool:
    """Whether a model may break this tie.

    Two conditions, both necessary: the evidence must be ambiguous -- a repository
    that speaks for itself is never sent to a model -- and there must be at least
    two candidates, since the model's only permitted action is choosing one of them.
    """
    surfaces = classification.surfaces
    if not surfaces or not is_ambiguous(surfaces):
        return False
    return len(surfaces) >= MIN_CANDIDATES_FOR_TIE_BREAK


def explain_gate(classification: Classification) -> str:
    """A short, user-safe explanation of the gate's decision."""
    surfaces = classification.surfaces
    if has_strong_evidence(surfaces):
        strong = strongest(surfaces)
        assert strong is not None
        return (
            f"resolved deterministically: {strong.type.value} is strongly "
            f"supported (confidence {strong.confidence})"
        )
    if is_only_fallback(surfaces):
        return "no specific surface was detected, so the evidence is inconclusive"
    if len(surfaces) < MIN_CANDIDATES_FOR_TIE_BREAK:
        return (
            "only one surface was detected and it is not strongly supported; there "
            "is nothing for a model to choose between"
        )
    gap = tie_gap(surfaces)
    if gap is not None and gap <= TIE_MARGIN:
        return f"the top two surfaces are within {gap} confidence of each other"
    return "resolved deterministically: one surface leads clearly enough"
