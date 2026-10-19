"""Statistics over experiment variants.

Every number here is computed by Python from :class:`VariantRun` measurements,
which themselves come from Spark's own event logs. **No model is involved.**
CLAUDE.md: statistics are computed by deterministic code, and models never
calculate authoritative metrics.

What is computed, per metric per variant:

    median   min   max   spread (max - min)   standard deviation   mean

and, across variants, the relative difference of each variant's median against
the baseline's.

Two deliberate restraints:

* **No judgement.** The lowest median is reported as the lowest median, not as
  "the best": whether less shuffle read is better depends on the question.
* **Timing and structure stay labelled.** A metric's ``determinism`` travels with
  its statistics, so an explanation cannot present a wall-clock difference as
  though it were as solid as a task count.
"""

from __future__ import annotations

import statistics as stdlib_statistics

from pydantic import BaseModel, Field, computed_field

from repolab.experiences.spark.measurements import Determinism
from repolab.experiments.models import VariantRun


class Aggregate(BaseModel):
    """Statistics over repeated samples of one metric within one variant."""

    metric: str
    unit: str
    determinism: Determinism

    samples: list[float]
    median: float
    minimum: float
    maximum: float
    spread: float
    """``maximum - minimum``: how much this metric moved across repeats."""

    stddev: float
    """Population standard deviation. 0.0 for a single sample, where it is undefined."""

    mean: float

    @computed_field
    @property
    def count(self) -> int:
        return len(self.samples)

    @computed_field
    @property
    def stable(self) -> bool:
        """Whether the repeats agreed exactly.

        A structural metric that is not stable across repeats means the runs were
        not actually equivalent, which invalidates the comparison rather than
        merely adding noise.
        """
        return self.spread == 0.0


class VariantStatistics(BaseModel):
    """One variant value, aggregated across its repeats."""

    variant_id: str
    value: int
    runs: int
    contended_runs: int = 0
    aggregates: dict[str, Aggregate] = Field(default_factory=dict)

    @property
    def contended(self) -> bool:
        return self.contended_runs > 0

    def median_of(self, metric: str) -> float | None:
        aggregate = self.aggregates.get(metric)
        return None if aggregate is None else aggregate.median


class ComparisonPoint(BaseModel):
    """One variant's standing on one metric, relative to the baseline."""

    value: int
    median: float
    absolute_difference: float
    relative_difference_percent: float | None = None
    """``None`` when the baseline median is zero, where a ratio is undefined."""

    is_baseline: bool = False


class MetricComparison(BaseModel):
    """How one metric behaved across every variant."""

    metric: str
    unit: str
    determinism: Determinism

    baseline_value: int
    baseline_median: float
    points: list[ComparisonPoint] = Field(default_factory=list)

    @computed_field
    @property
    def lowest_value(self) -> int | None:
        """The variant value with the lowest median. A fact, not a recommendation."""
        if not self.points:
            return None
        return min(self.points, key=lambda point: point.median).value

    @computed_field
    @property
    def highest_value(self) -> int | None:
        if not self.points:
            return None
        return max(self.points, key=lambda point: point.median).value

    @computed_field
    @property
    def span_percent(self) -> float | None:
        """Spread of the medians as a percentage of the baseline median."""
        if self.baseline_median == 0 or not self.points:
            return None
        medians = [point.median for point in self.points]
        return round((max(medians) - min(medians)) / abs(self.baseline_median) * 100, 2)


class ExperimentStatistics(BaseModel):
    """Everything computed from the variant runs."""

    baseline_value: int
    total_runs: int = 0
    contended_runs: int = 0
    variants: list[VariantStatistics] = Field(default_factory=list)
    comparisons: list[MetricComparison] = Field(default_factory=list)

    @property
    def metrics(self) -> list[str]:
        return [comparison.metric for comparison in self.comparisons]

    def comparison_of(self, metric: str) -> MetricComparison | None:
        for comparison in self.comparisons:
            if comparison.metric == metric:
                return comparison
        return None

    def unstable_structural_metrics(self) -> list[str]:
        """Structural metrics that varied across repeats of the same value.

        These should be empty. A non-empty list means two runs of the *same*
        configuration disagreed on something that ought to be deterministic, so
        the runs were not equivalent and the comparison is unsound.
        """
        unstable: list[str] = []
        for variant in self.variants:
            for aggregate in variant.aggregates.values():
                if (
                    aggregate.determinism is Determinism.STRUCTURAL
                    and not aggregate.stable
                    and aggregate.metric not in unstable
                ):
                    unstable.append(aggregate.metric)
        return unstable


def aggregate_samples(
    metric: str,
    samples: list[float],
    *,
    unit: str,
    determinism: Determinism,
) -> Aggregate:
    """Summarise repeated samples of one metric.

    Raises:
        ValueError: on an empty sample list, because every statistic would be a
            fabrication.
    """
    if not samples:
        raise ValueError(f"cannot aggregate {metric!r}: no samples")

    ordered = sorted(samples)
    return Aggregate(
        metric=metric,
        unit=unit,
        determinism=determinism,
        samples=list(samples),
        median=round(stdlib_statistics.median(ordered), 4),
        minimum=ordered[0],
        maximum=ordered[-1],
        spread=round(ordered[-1] - ordered[0], 4),
        # Population, not sample: these are all the runs there were, not a sample
        # drawn from a larger set. Undefined for one run, reported as 0.0.
        stddev=round(stdlib_statistics.pstdev(ordered), 4) if len(ordered) > 1 else 0.0,
        mean=round(stdlib_statistics.fmean(ordered), 4),
    )


def relative_difference(value: float, baseline: float) -> float | None:
    """``value`` against ``baseline`` as a signed percentage.

    ``None`` when the baseline is zero: a relative change from nothing has no
    meaning, and reporting a large number there would be an artefact.
    """
    if baseline == 0:
        return None
    return round((value - baseline) / abs(baseline) * 100, 2)


def summarise_variant(runs: list[VariantRun], *, metrics: list[str]) -> VariantStatistics:
    """Aggregate every requested metric across the repeats of one variant."""
    if not runs:
        raise ValueError("cannot summarise a variant with no runs")

    per_metric: dict[str, list[float]] = {}
    units: dict[str, tuple[str, Determinism]] = {}
    for run in runs:
        available = run.metrics()
        for name in metrics:
            metric = available.get(name)
            if metric is None:
                continue
            per_metric.setdefault(name, []).append(metric.value)
            units.setdefault(name, (metric.unit, metric.determinism))

    aggregates = {
        name: aggregate_samples(name, samples, unit=units[name][0], determinism=units[name][1])
        for name, samples in per_metric.items()
    }

    first = runs[0]
    return VariantStatistics(
        variant_id=first.variant_id,
        value=first.value,
        runs=len(runs),
        contended_runs=sum(1 for run in runs if run.timing_contended),
        aggregates=aggregates,
    )


def compute_statistics(
    runs: list[VariantRun],
    *,
    metrics: list[str],
    baseline_value: int,
) -> ExperimentStatistics:
    """Aggregate per variant, then compare every variant to the baseline."""
    by_value: dict[int, list[VariantRun]] = {}
    for run in runs:
        by_value.setdefault(run.value, []).append(run)

    variants = [summarise_variant(by_value[value], metrics=metrics) for value in sorted(by_value)]

    return ExperimentStatistics(
        baseline_value=baseline_value,
        total_runs=len(runs),
        contended_runs=sum(1 for run in runs if run.timing_contended),
        variants=variants,
        comparisons=_compare(variants, metrics=metrics, baseline_value=baseline_value),
    )


def _compare(
    variants: list[VariantStatistics],
    *,
    metrics: list[str],
    baseline_value: int,
) -> list[MetricComparison]:
    """Build one comparison per metric, anchored on the baseline variant.

    A metric is only compared when the baseline variant measured it; without a
    baseline there is nothing to be relative to, and inventing one would be the
    kind of manufactured number this module exists to prevent.
    """
    baseline = next((v for v in variants if v.value == baseline_value), None)
    if baseline is None:
        return []

    comparisons: list[MetricComparison] = []
    for metric in metrics:
        anchor = baseline.aggregates.get(metric)
        if anchor is None:
            continue

        points: list[ComparisonPoint] = []
        for variant in variants:
            aggregate = variant.aggregates.get(metric)
            if aggregate is None:
                continue
            points.append(
                ComparisonPoint(
                    value=variant.value,
                    median=aggregate.median,
                    absolute_difference=round(aggregate.median - anchor.median, 4),
                    relative_difference_percent=relative_difference(
                        aggregate.median, anchor.median
                    ),
                    is_baseline=variant.value == baseline_value,
                )
            )

        comparisons.append(
            MetricComparison(
                metric=metric,
                unit=anchor.unit,
                determinism=anchor.determinism,
                baseline_value=baseline_value,
                baseline_median=anchor.median,
                points=points,
            )
        )
    return comparisons
