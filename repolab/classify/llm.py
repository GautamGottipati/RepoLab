"""Super tie-break for ambiguous classifications.

Reached only through :func:`repolab.classify.ambiguity.should_consult_model`, so
a model never classifies something the repository states plainly.

The model's authority is deliberately narrow. It may only *choose among the
candidate surfaces already detected*: it cannot introduce a surface, and a choice
that is not a candidate is discarded with the deterministic answer left standing.
Classification therefore stays grounded in repository evidence even when a model
is involved.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from repolab.llm import LLMError, ModelRouter, Purpose
from repolab.manifest.models import (
    Classification,
    ClassificationSource,
    InteractionSurface,
    SurfaceType,
)

log = structlog.get_logger(__name__)

#: Evidence paths shown per candidate. Enough to judge, small enough to stay cheap.
_EVIDENCE_PER_CANDIDATE = 4


class SurfaceJudgement(BaseModel):
    """Which detected surface should be primary, and why."""

    primary_surface: str = Field(
        description="Exactly one of the candidate surface types listed in the prompt"
    )
    reason: str = Field(description="One sentence, citing the evidence given")


def _describe(surfaces: list[InteractionSurface]) -> str:
    lines: list[str] = []
    for surface in surfaces:
        paths = ", ".join(ref.path for ref in surface.evidence[:_EVIDENCE_PER_CANDIDATE])
        lines.append(
            f"- {surface.type.value} (confidence {surface.confidence}): "
            f"{surface.reason}. Files: {paths or 'none'}"
        )
    return "\n".join(lines)


async def break_tie(
    router: ModelRouter, classification: Classification
) -> tuple[Classification, str | None]:
    """Ask Super which candidate should be primary.

    Returns the classification and an error note, if anything went wrong. The
    input is never mutated on failure.
    """
    candidates = classification.surfaces
    if len(candidates) < 2:
        return classification, "tie-break skipped: fewer than two candidates"

    valid = {s.type.value for s in candidates}
    prompt = (
        "A repository was analysed and these interaction surfaces were detected, "
        "each with the files that justify it. The evidence is too close to decide "
        "which should be the primary way to explore the repository.\n\n"
        f"{_describe(candidates)}\n\n"
        f"Choose exactly one of: {', '.join(sorted(valid))}. "
        "Base the choice only on the evidence above. Do not invent a surface."
    )

    try:
        completion = await router.super(
            purpose=Purpose.CLASSIFICATION_TIE_BREAK,
            schema=SurfaceJudgement,
            system=(
                "You choose between interaction surfaces that were already detected "
                "in a repository. You may only pick from the given candidates."
            ),
            prompt=prompt,
            max_tokens=512,
        )
    except LLMError as exc:
        return classification, f"tie-break failed: {type(exc).__name__}"

    judgement = completion.value
    chosen = judgement.primary_surface.strip().lower()
    if chosen not in valid:
        # The model named something that was never detected, so it is discarded.
        log.info("classify.tie_break_rejected", chosen=chosen)
        return (
            classification,
            f"tie-break discarded: model chose {chosen!r}, which was not a candidate",
        )

    surface_type = SurfaceType(chosen)
    updated = classification.model_copy(deep=True)
    for surface in updated.surfaces:
        surface.primary = surface.type is surface_type

    updated.primary_type = surface_type
    updated.rationale = (
        f"{surface_type.value} chosen by tie-break over "
        f"{', '.join(sorted(valid - {chosen}))}: {judgement.reason.strip()}"
    )
    updated.resolved_by = ClassificationSource.SUPER
    # Re-rank so the chosen surface leads.
    updated.surfaces.sort(key=lambda s: (not s.primary, -s.confidence))

    log.info("classify.tie_break_applied", primary=surface_type.value)
    return updated, None
