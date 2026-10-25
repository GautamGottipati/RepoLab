"""Explaining a run from what actually happened in it.

Super is given the learner's code, the real output, the measurements Spark's
event log recorded, the operators of the physical plan, the architecture steps
the run was mapped to, and which runtime it ran on. It explains; it does not
measure. The answer goes through the same verification as Ask RepoLab: a
``measured`` claim must name a recorded measurement, a number with a unit that
appears nowhere in the evidence is flagged, and a cited file must be one of the
mapped source paths.
"""

from __future__ import annotations

from typing import Any

from repolab.evidence.models import CodeReference, EvidenceBundle
from repolab.llm import LLMError, Purpose
from repolab.questions.answer import verify_answer
from repolab.questions.models import GroundedAnswer, VerifiedAnswer

SYSTEM = (
    "You explain to a beginner what just happened when they ran a small program "
    "against real software, using only the evidence given: their code, the real "
    "output, the measurements recorded from the run, the execution plan, and the "
    "parts of the repository the run was mapped to.\n"
    "\n"
    "Answer four things, plainly: what happened, why it happened that way, which "
    "parts of the repository were involved, and one thing they could change to "
    "see different behaviour.\n"
    "\n"
    "Label every claim. measured: a value from the listed measurements, with its "
    "name in 'support'. repository: shown by one of the listed source paths, "
    "copied exactly. inferred: your reasoning. Never state a number that is not "
    "in the measurements or the output. If the run used an official release "
    "rather than this commit, say so when you name repository parts."
)

PROMPT = """\
Runtime: {runtime}

The learner's code:
{code}

Output (first rows):
{output}

Measurements recorded from this run (Spark event log):
{measurements}

Physical plan operators: {operators}

What the run was mapped to in the repository:
{mapping}

Explain this run. Fill 'summary' (one or two sentences), 'steps' (in the order
things happened), 'sections' with keys why and try (what to change), and
'follow_ups'."""


async def explain_run(
    *,
    router: Any,
    code: str,
    result: dict[str, Any],
    mapping: list[dict[str, Any]],
    measurements: EvidenceBundle,
    runtime_label: str,
) -> VerifiedAnswer:
    """A verified explanation. Raises :class:`LLMError` if the model fails."""
    from repolab.experiences.spark.understanding import plan_operators

    columns = result.get("columns") or []
    rows = (result.get("rows") or [])[:10]
    output = (
        "\n".join([" | ".join(columns), *(" | ".join(str(v) for v in row) for row in rows)])
        if columns
        else str(result.get("value"))
    )
    lines = [f"- {m.name}: {m.value:g} {m.unit}" for m in measurements.measurements]
    mapped = [
        f"- {step.get('title')}: {step.get('run_evidence')}"
        + (f" → {step.get('architecture_label')}" if step.get("architecture_label") else "")
        + (
            f" [source: {', '.join(step.get('source_paths') or [])}]"
            if step.get("source_paths")
            else ""
        )
        for step in mapping
    ]
    completion = await router.super(
        purpose=Purpose.QUESTION_ANSWER,
        schema=GroundedAnswer,
        system=SYSTEM,
        prompt=PROMPT.format(
            runtime=runtime_label,
            code=code,
            output=output or "(none)",
            measurements="\n".join(lines) or "- (none recorded)",
            operators=", ".join(plan_operators(result.get("plan"))) or "(not available)",
            mapping="\n".join(mapped) or "- (no mapping)",
        ),
        max_tokens=8192,
        timeout_seconds=150.0,
    )
    return verify_answer(completion.value, evidence=measurements, repository_root=None)


def evidence_for(measurements: list, mapping: list[dict[str, Any]], excerpt: str) -> EvidenceBundle:
    paths = {p for step in mapping for p in (step.get("source_paths") or [])}
    return EvidenceBundle(
        measurements=measurements,
        code_references=[
            CodeReference(path=p, excerpt=excerpt[:2000] or None) for p in sorted(paths)
        ],
    )


__all__ = ["LLMError", "evidence_for", "explain_run"]
