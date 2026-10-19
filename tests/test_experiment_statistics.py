"""Statistics over experiment variants.

These are the numbers an explanation is allowed to quote, so they are asserted
against hand-computed values rather than against whatever the code happens to
produce. If this file is wrong, every explanation downstream is wrong with it.
"""

from __future__ import annotations

import pytest

from repolab.experiences.spark.measurements import Determinism
from repolab.experiments.statistics import (
    aggregate_samples,
    compute_statistics,
    relative_difference,
    summarise_variant,
)
from tests.support.experiments import variant_run


class TestAggregate:
    def test_every_statistic_on_a_known_sample(self) -> None:
        # sum 23 over 5 -> mean 4.6; median 4; min 1; max 9; spread 8
        # pstdev = sqrt(mean((x-4.6)^2))
        #        = sqrt((12.96 + 2.56 + 0.36 + 1.96 + 19.36)/5) = sqrt(7.44) = 2.7276
        aggregate = aggregate_samples(
            "total runtime",
            [1.0, 3.0, 4.0, 6.0, 9.0],
            unit="ms",
            determinism=Determinism.TIMING,
        )

        assert aggregate.count == 5
        assert aggregate.median == 4.0
        assert aggregate.minimum == 1.0
        assert aggregate.maximum == 9.0
        assert aggregate.spread == 8.0
        assert aggregate.mean == 4.6
        assert aggregate.stddev == pytest.approx(2.7276, abs=1e-4)

    def test_an_even_sample_count_takes_the_midpoint(self) -> None:
        aggregate = aggregate_samples(
            "task count", [10.0, 20.0], unit="tasks", determinism=Determinism.STRUCTURAL
        )
        assert aggregate.median == 15.0

    def test_a_single_sample_reports_zero_stddev_rather_than_failing(self) -> None:
        """Standard deviation is undefined for one observation.

        Reported as 0.0 with ``count == 1`` alongside it, so a reader can see
        there was nothing to vary -- rather than raising, which would lose the
        measurement entirely.
        """
        aggregate = aggregate_samples(
            "total runtime", [11.0], unit="ms", determinism=Determinism.TIMING
        )
        assert aggregate.count == 1
        assert aggregate.stddev == 0.0
        assert aggregate.spread == 0.0
        assert aggregate.median == aggregate.mean == 11.0

    def test_no_samples_is_refused(self) -> None:
        """Every statistic over an empty set would be a fabrication."""
        with pytest.raises(ValueError, match="no samples"):
            aggregate_samples("x", [], unit="ms", determinism=Determinism.TIMING)

    def test_identical_repeats_are_reported_as_stable(self) -> None:
        aggregate = aggregate_samples(
            "partitions",
            [200.0, 200.0, 200.0],
            unit="partitions",
            determinism=Determinism.STRUCTURAL,
        )
        assert aggregate.stable is True
        assert aggregate.spread == 0.0

    def test_differing_repeats_are_reported_as_unstable(self) -> None:
        aggregate = aggregate_samples(
            "partitions",
            [200.0, 199.0],
            unit="partitions",
            determinism=Determinism.STRUCTURAL,
        )
        assert aggregate.stable is False


class TestRelativeDifference:
    def test_a_signed_percentage(self) -> None:
        assert relative_difference(150.0, 100.0) == 50.0
        assert relative_difference(50.0, 100.0) == -50.0
        assert relative_difference(100.0, 100.0) == 0.0

    def test_a_zero_baseline_has_no_relative_difference(self) -> None:
        """A change from nothing has no meaningful ratio.

        Returning a large number here would be an artefact of the arithmetic, and
        an explanation would read it as a finding.
        """
        assert relative_difference(5.0, 0.0) is None

    def test_the_denominator_is_the_baseline_magnitude(self) -> None:
        """A negative baseline must not flip the sign of the change.

        Dividing by the signed baseline would report -50 against -100 as a *fall*
        when it is a rise. No RepoLab metric is negative today, but a sign error
        here would read as a finding rather than as arithmetic.
        """
        assert relative_difference(-50.0, -100.0) == 50.0


class TestVariantSummary:
    def test_metrics_are_aggregated_across_repeats(self) -> None:
        runs = [
            variant_run(value=200, repeat=1, total_runtime_ms=1000, task_count=10),
            variant_run(value=200, repeat=2, total_runtime_ms=1200, task_count=10),
            variant_run(value=200, repeat=3, total_runtime_ms=1100, task_count=10),
        ]
        summary = summarise_variant(runs, metrics=["total runtime", "task count"])

        assert summary.runs == 3
        assert summary.aggregates["total runtime"].median == 1100.0
        assert summary.aggregates["total runtime"].samples == [1000.0, 1200.0, 1100.0]
        assert summary.aggregates["task count"].stable is True

    def test_a_metric_no_run_measured_is_absent_rather_than_zero(self) -> None:
        """Absent is not the same as zero, and must not be reported as it."""
        summary = summarise_variant(
            [variant_run(value=200, repeat=1)], metrics=["total runtime", "invented metric"]
        )
        assert "invented metric" not in summary.aggregates

    def test_contended_runs_are_counted(self) -> None:
        runs = [
            variant_run(value=200, repeat=1, contended=True),
            variant_run(value=200, repeat=2, contended=False),
        ]
        summary = summarise_variant(runs, metrics=["total runtime"])
        assert summary.contended_runs == 1
        assert summary.contended is True

    def test_no_runs_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no runs"):
            summarise_variant([], metrics=["total runtime"])


class TestComparisons:
    def statistics(self):
        runs = [
            variant_run(value=50, repeat=1, total_runtime_ms=800, partitions=50, empty=43),
            variant_run(value=50, repeat=2, total_runtime_ms=900, partitions=50, empty=43),
            variant_run(value=200, repeat=1, total_runtime_ms=1000, partitions=200, empty=193),
            variant_run(value=200, repeat=2, total_runtime_ms=1200, partitions=200, empty=193),
        ]
        return compute_statistics(
            runs,
            metrics=["total runtime", "partitions", "empty partitions"],
            baseline_value=200,
        )

    def test_variants_are_ordered_by_value(self) -> None:
        assert [v.value for v in self.statistics().variants] == [50, 200]

    def test_every_point_is_relative_to_the_baseline_median(self) -> None:
        comparison = self.statistics().comparison_of("total runtime")
        assert comparison is not None
        assert comparison.baseline_median == 1100.0

        by_value = {point.value: point for point in comparison.points}
        assert by_value[200].is_baseline is True
        assert by_value[200].relative_difference_percent == 0.0
        # 850 vs 1100 -> -22.73%
        assert by_value[50].median == 850.0
        assert by_value[50].relative_difference_percent == pytest.approx(-22.73, abs=0.01)
        assert by_value[50].absolute_difference == -250.0

    def test_determinism_travels_with_the_comparison(self) -> None:
        statistics = self.statistics()
        assert statistics.comparison_of("total runtime").determinism is Determinism.TIMING
        assert statistics.comparison_of("partitions").determinism is Determinism.STRUCTURAL

    def test_lowest_and_highest_are_reported_as_facts_not_recommendations(self) -> None:
        comparison = self.statistics().comparison_of("empty partitions")
        assert comparison is not None
        assert comparison.lowest_value == 50
        assert comparison.highest_value == 200

    def test_without_a_baseline_variant_nothing_is_compared(self) -> None:
        """A comparison needs an anchor; inventing one would manufacture a number."""
        runs = [variant_run(value=50, repeat=1), variant_run(value=100, repeat=1)]
        statistics = compute_statistics(runs, metrics=["total runtime"], baseline_value=200)

        assert statistics.variants
        assert statistics.comparisons == []

    def test_structural_instability_across_repeats_is_surfaced(self) -> None:
        """Two runs of the same value disagreeing on a count invalidates the comparison."""
        runs = [
            variant_run(value=200, repeat=1, partitions=200, task_count=10),
            variant_run(value=200, repeat=2, partitions=199, task_count=10),
        ]
        statistics = compute_statistics(
            runs, metrics=["partitions", "task count"], baseline_value=200
        )
        assert statistics.unstable_structural_metrics() == ["partitions"]

    def test_timing_variation_is_not_treated_as_instability(self) -> None:
        runs = [
            variant_run(value=200, repeat=1, total_runtime_ms=1000),
            variant_run(value=200, repeat=2, total_runtime_ms=2000),
        ]
        statistics = compute_statistics(runs, metrics=["total runtime"], baseline_value=200)
        assert statistics.unstable_structural_metrics() == []
