"""Explaining measured results, and checking the explanation against them.

The model's job here is narrow: read statistics it did not compute and say what
they mean. So the payload it receives is assembled explicitly rather than by
handing over whatever happens to be in scope -- it gets the question, the plan,
the computed statistics, code references, log excerpts where relevant, and
official documentation excerpts. Nothing else. No raw event log, no container
output, no repository contents.

Every claim must declare what kind of claim it is:

    measured     a number this experiment produced
    documented   behaviour stated by official documentation
    inferred     a conclusion drawn from the two above

That distinction is the point. An explanation that blurs them lets a plausible
inference pass as a measurement, which is exactly the failure CLAUDE.md forbids:
models reason about measurements, they never manufacture them.

:func:`verify_explanation` then checks the output against the statistics. It is a
*detector*, not a censor: it flags claims whose support does not exist and
numbers that appear in no measurement, and the flags travel with the explanation
so a reader sees what could not be corroborated.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
)
from repolab.experiences.spark.measurements import Determinism
from repolab.experiments.models import ExperimentPlan
from repolab.experiments.statistics import ExperimentStatistics

#: Numbers at or below this are ignored by the numeric guard.
#:
#: Small integers are overwhelmingly prose ("the three stages", "a 2x
#: difference", "200 partitions" is caught separately as a plan value), and
#: flagging them would bury real fabrications in noise.
SMALL_NUMBER_CEILING = 10.0

#: Relative tolerance when matching a claimed number to a measured one, which
#: covers a model rounding 11234 to "11200".
MATCH_TOLERANCE = 0.02

_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


class ClaimKind(StrEnum):
    """What kind of support a claim rests on."""

    MEASURED = "measured"
    """A number this experiment produced. Must trace to a computed statistic."""

    DOCUMENTED = "documented"
    """Behaviour stated by official documentation. Must cite a retrieved page."""

    INFERRED = "inferred"
    """A conclusion drawn from measurements and documentation. Not itself evidence."""


class Claim(BaseModel):
    """One statement, labelled with the kind of evidence behind it."""

    kind: ClaimKind = Field(description="measured, documented, or inferred")
    statement: str = Field(description="One sentence. No hedging, no restating the question.")
    support: str = Field(
        description=(
            "For 'measured': the exact metric name from the statistics. "
            "For 'documented': the URL of the cited page. "
            "For 'inferred': which measured or documented claims it follows from."
        )
    )


class Explanation(BaseModel):
    """What the model produced."""

    answer: str = Field(description="A direct answer to the question, two to four sentences")
    claims: list[Claim] = Field(description="The claims the answer rests on, each labelled")
    caveats: list[str] = Field(description="What this experiment does not establish")

    def of_kind(self, kind: ClaimKind) -> list[Claim]:
        return [claim for claim in self.claims if claim.kind is kind]


class UnsupportedClaim(BaseModel):
    """A claim the deterministic check could not corroborate."""

    statement: str
    kind: ClaimKind
    problem: str


class VerifiedExplanation(BaseModel):
    """An explanation together with what could and could not be corroborated."""

    explanation: Explanation
    unsupported: list[UnsupportedClaim] = Field(default_factory=list)
    checked_claims: int = 0

    @computed_field
    @property
    def grounded(self) -> bool:
        """Whether every checkable claim traced back to real evidence."""
        return not self.unsupported


# ---------------------------------------------------------------------------
# What the model is given
# ---------------------------------------------------------------------------


class PointSummary(BaseModel):
    """One variant's standing on one metric, as the model sees it."""

    value: int
    median: float
    relative_difference_percent: float | None = None
    spread: float
    """How much this metric moved across the repeats of this value."""

    stddev: float


class MetricSummary(BaseModel):
    """One metric across every variant."""

    metric: str
    unit: str
    determinism: Determinism
    """``structural`` repeats exactly across identical runs; ``timing`` does not."""

    baseline_median: float
    points: list[PointSummary] = Field(default_factory=list)


class StatisticsSummary(BaseModel):
    """The computed statistics, in the form the model is given.

    Deliberately *not* :class:`ExperimentStatistics`. That type carries every
    variant's raw sample array, and handing those over does two unwanted things:
    it invites the model to recombine them arithmetically, and -- measured on a
    real six-variant run -- it produced a 62 KB prompt that Ultra could not
    answer within its output budget at all.

    So the model receives what it needs to reason: one row per metric per value,
    with the median, the relative difference, and how much the repeats varied.
    The grounding check still runs against the *full* statistics, so what the
    model may claim is no looser than before -- it simply sees less.
    """

    baseline_value: int
    total_runs: int
    contended_runs: int

    repeats_per_value: int
    """Repeats that actually produced a measurement, at the value with fewest.

    The *planned* repeat count was reported here until a variant failure became
    survivable (Phase 13). A model told "3 repeats per value" about a value that
    managed two would describe runs that never happened -- and it receives this
    as a number, where prose disclosures are easier to overlook.
    """

    repeats_planned: int
    """What the plan asked for. Differs from the above when a run failed."""

    metrics: list[MetricSummary] = Field(default_factory=list)
    unstable_metrics: list[str] = Field(default_factory=list)
    """Structural metrics that disagreed across repeats of the same value."""


def summarise_for_model(statistics: ExperimentStatistics, *, repeats: int) -> StatisticsSummary:
    """Compress the statistics into what the model is allowed to see."""
    spreads: dict[tuple[str, int], tuple[float, float]] = {}
    for variant in statistics.variants:
        for name, aggregate in variant.aggregates.items():
            spreads[(name, variant.value)] = (aggregate.spread, aggregate.stddev)

    metrics = [
        MetricSummary(
            metric=comparison.metric,
            unit=comparison.unit,
            determinism=comparison.determinism,
            baseline_median=comparison.baseline_median,
            points=[
                PointSummary(
                    value=point.value,
                    median=point.median,
                    relative_difference_percent=point.relative_difference_percent,
                    spread=spreads.get((comparison.metric, point.value), (0.0, 0.0))[0],
                    stddev=spreads.get((comparison.metric, point.value), (0.0, 0.0))[1],
                )
                for point in comparison.points
            ],
        )
        for comparison in statistics.comparisons
    ]

    # What actually ran, not what was asked for: the fewest repeats any value
    # managed, so nothing the model is told overstates the evidence.
    measured = [variant.runs for variant in statistics.variants if variant.runs]
    return StatisticsSummary(
        baseline_value=statistics.baseline_value,
        total_runs=statistics.total_runs,
        contended_runs=statistics.contended_runs,
        repeats_per_value=min(measured) if measured else 0,
        repeats_planned=repeats,
        metrics=metrics,
        unstable_metrics=statistics.unstable_structural_metrics(),
    )


class ExplanationInput(BaseModel):
    """Exactly what Ultra receives. Nothing is added to this payload downstream.

    Assembled as a type rather than formatted inline so that what reaches the
    model is auditable, and so a test can assert that a raw event log never does.
    """

    question: str
    hypothesis: str
    variable: str
    values: list[int]
    fixed_settings: dict[str, str]
    repeats: int
    baseline_value: int

    metrics: list[str] = Field(default_factory=list)
    """The exact names a ``measured`` claim may cite.

    The names are already inside ``statistics``, nested under each comparison,
    but a model asked for "the exact metric name" has to go looking for them --
    and a live Ultra was observed citing ``baseline_value`` instead, which is a
    real field of the statistics but not a metric. Listing them here removes the
    guesswork rather than loosening the check that caught it.
    """

    statistics: StatisticsSummary
    code_references: list[CodeReference] = Field(default_factory=list)
    logs: list[LogReference] = Field(default_factory=list)
    documentation: list[DocumentationReference] = Field(default_factory=list)

    notes: list[str] = Field(default_factory=list)
    """Facts the model must not contradict: disclosures, contention, instability."""

    def to_prompt(self) -> str:
        """Render for a prompt. Deterministic, so the same input reads the same."""
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True)


SYSTEM_PROMPT = (
    "You explain the results of a controlled software experiment.\n"
    "\n"
    "Every number you are given was computed by deterministic code from the "
    "application's own event logs. You must not calculate, estimate, convert or "
    "invent any figure. Quote the numbers you are given, exactly as given.\n"
    "\n"
    "Label every claim:\n"
    "  measured   - a number present in the statistics. In 'support', put one "
    "name from the 'metrics' list, copied exactly. Nothing else is accepted "
    "there -- not a field of the statistics, not a description.\n"
    "  documented - behaviour stated by one of the supplied documentation "
    "excerpts. Put its URL in 'support'.\n"
    "  inferred   - a conclusion you draw. Say which measured or documented "
    "claims it rests on.\n"
    "\n"
    "Never label an inference as measured. A metric marked 'timing' varies "
    "between runs; a metric marked 'structural' does not, and should be "
    "preferred when both are available. If a measurement does not support the "
    "hypothesis, say so."
)


def build_explanation_input(
    *,
    plan: ExperimentPlan,
    statistics: ExperimentStatistics,
    evidence: EvidenceBundle,
    notes: list[str] | None = None,
    max_logs: int = 2,
    max_documentation: int = 4,
) -> ExplanationInput:
    """Assemble the permitted payload.

    Neither the raw measurements nor the raw samples are copied in: the model
    receives :class:`StatisticsSummary`, which is the computed form, rather than
    hundreds of figures it might recombine arithmetically.
    """
    return ExplanationInput(
        question=plan.question,
        hypothesis=plan.hypothesis,
        variable=plan.variable,
        values=plan.values,
        fixed_settings=plan.fixed_settings,
        repeats=plan.repeats,
        baseline_value=plan.baseline_value,
        metrics=list(statistics.metrics),
        statistics=summarise_for_model(statistics, repeats=plan.repeats),
        code_references=list(evidence.code_references)[:6],
        logs=list(evidence.logs)[:max_logs],
        documentation=list(evidence.documentation)[:max_documentation],
        notes=list(notes) if notes is not None else [],
    )


def default_notes(plan: ExperimentPlan, statistics: ExperimentStatistics) -> list[str]:
    """Facts the explanation must not contradict.

    Each of these would otherwise be invisible to the model and would let it
    overstate the result: that RepoLab added a shuffle to the repository's job,
    that timings were contended, or that something supposedly deterministic was
    not.
    """
    notes: list[str] = []

    from repolab.experiences.spark.driver import ADDED_STEPS

    notes.extend(f"RepoLab added this to the repository's job: {step}" for step in ADDED_STEPS)

    if statistics.contended_runs:
        notes.append(
            f"{statistics.contended_runs} of {statistics.total_runs} runs executed "
            "alongside another variant, so their timings reflect contention and must "
            "not be presented as clean durations"
        )
    measured = [variant.runs for variant in statistics.variants if variant.runs]
    fewest = min(measured) if measured else 0
    if fewest == 1:
        notes.append("at least one value ran once, so its timings have no error bar")
    if fewest and fewest < plan.repeats:
        notes.append(
            f"the plan asked for {plan.repeats} repeats per value; at least one "
            f"value produced {fewest}, so the comparison rests on fewer runs there"
        )

    unstable = statistics.unstable_structural_metrics()
    if unstable:
        notes.append(
            "these metrics should have been identical across repeats of the same "
            f"value but were not, which weakens the comparison: {unstable}"
        )
    if plan.dropped_metrics:
        notes.append(
            f"these were requested but cannot be measured by RepoLab: {plan.dropped_metrics}"
        )

    timing = [
        comparison.metric
        for comparison in statistics.comparisons
        if comparison.determinism is Determinism.TIMING
    ]
    if timing:
        notes.append(f"these metrics are timings and vary between runs: {timing}")

    return notes


# ---------------------------------------------------------------------------
# Checking the output against the evidence
# ---------------------------------------------------------------------------


def allowed_numbers(statistics: ExperimentStatistics, plan: ExperimentPlan) -> set[float]:
    """Every number an explanation is entitled to state.

    The samples, the aggregates computed from them, the comparison figures, and
    the plan's own values. Anything else in a measured claim came from somewhere
    other than this experiment.
    """
    numbers: set[float] = set()

    for variant in statistics.variants:
        numbers.add(float(variant.value))
        numbers.add(float(variant.runs))
        for aggregate in variant.aggregates.values():
            numbers.update(float(sample) for sample in aggregate.samples)
            numbers.update(
                {
                    aggregate.median,
                    aggregate.minimum,
                    aggregate.maximum,
                    aggregate.spread,
                    aggregate.stddev,
                    aggregate.mean,
                }
            )

    for comparison in statistics.comparisons:
        numbers.add(comparison.baseline_median)
        if comparison.span_percent is not None:
            numbers.add(comparison.span_percent)
        for point in comparison.points:
            numbers.add(point.median)
            numbers.add(point.absolute_difference)
            if point.relative_difference_percent is not None:
                numbers.add(point.relative_difference_percent)

    numbers.update(float(value) for value in plan.values)
    numbers.add(float(plan.repeats))
    numbers.add(float(plan.estimated_runs))
    numbers.add(float(statistics.total_runs))
    return numbers


def _matches(claimed: float, allowed: set[float]) -> bool:
    """Whether *claimed* corresponds to a real measurement.

    Accepts exact equality, a small relative tolerance for rounding, and the
    common unit rescalings -- milliseconds written as seconds, bytes written as
    KiB or MiB -- because a model quoting "11.2 s" for 11234 ms has not
    fabricated anything.
    """
    scales = (1.0, 1e-3, 1e3, 1 / 1024, 1024.0, 1 / 1024**2, 1024.0**2, 0.01, 100.0)
    for value in allowed:
        for scale in scales:
            scaled = value * scale
            if scaled == claimed:
                return True
            if scaled != 0 and abs(scaled - claimed) <= abs(scaled) * MATCH_TOLERANCE:
                return True
    return False


def numbers_in(text: str) -> list[float]:
    """Parse the numbers written in *text*, ignoring small ones."""
    found: list[float] = []
    for raw in _NUMBER_PATTERN.findall(text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if abs(value) > SMALL_NUMBER_CEILING:
            found.append(value)
    return found


def verify_explanation(
    explanation: Explanation,
    *,
    statistics: ExperimentStatistics,
    plan: ExperimentPlan,
    documentation: list[DocumentationReference] | None = None,
) -> VerifiedExplanation:
    """Check each claim against the evidence that was actually collected.

    Three checks, all deterministic:

    * a ``measured`` claim must name a metric present in the statistics;
    * a ``measured`` claim must not state a number no measurement produced;
    * a ``documented`` claim must cite one of the supplied documentation URLs.

    An ``inferred`` claim is not checked: an inference legitimately restates
    measured figures, and it is already labelled as the weaker kind of claim,
    which is what the reader needs to know.
    """
    metrics = set(statistics.metrics)
    for variant in statistics.variants:
        metrics.update(variant.aggregates)
    urls = {reference.url for reference in (documentation or [])}
    allowed = allowed_numbers(statistics, plan)

    unsupported: list[UnsupportedClaim] = []
    checked = 0

    for claim in explanation.claims:
        if claim.kind is ClaimKind.MEASURED:
            checked += 1
            if claim.support not in metrics:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=(
                            f"cites {claim.support!r}, which is not a metric this "
                            "experiment measured"
                        ),
                    )
                )
                continue
            fabricated = [
                number for number in numbers_in(claim.statement) if not _matches(number, allowed)
            ]
            if fabricated:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"states {fabricated}, which no measurement produced",
                    )
                )
        elif claim.kind is ClaimKind.DOCUMENTED:
            checked += 1
            if claim.support not in urls:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=(
                            f"cites {claim.support!r}, which is not one of the "
                            "documentation pages retrieved for this experiment"
                        ),
                    )
                )

    return VerifiedExplanation(
        explanation=explanation, unsupported=unsupported, checked_claims=checked
    )
