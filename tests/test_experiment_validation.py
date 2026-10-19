"""Plan validation.

This is the approval gate's teeth. A model proposes; the code here decides what
may run. The tests that matter most are the refusals: a variable off the
allowlist, a metric RepoLab cannot measure, and a plan that would cost more than
the budget allows.
"""

from __future__ import annotations

from repolab.experiences.spark.driver import SparkRunConfig
from repolab.experiments.models import ProposedPlan
from repolab.experiments.validation import (
    ALLOWED_VARIABLES,
    REQUIRED_METRICS,
    ExperimentBudget,
    canonical_metric,
    canonical_variable,
    fixed_settings_for,
    measurable_metrics,
    validate_plan,
)

IMAGE = "apache/spark:3.5.1-python3"


def proposal(**overrides) -> ProposedPlan:
    fields = {
        "question": "Why does this job use 200 shuffle partitions?",
        "hypothesis": "Most partitions are empty because the data has few keys.",
        "variable": "spark.sql.shuffle.partitions",
        "values": [50, 200],
        "metrics": ["total runtime", "partitions"],
        "repeats": 2,
    }
    fields.update(overrides)
    return ProposedPlan(**fields)


def validate(budget: ExperimentBudget | None = None, **overrides):
    return validate_plan(proposal(**overrides), budget=budget, image=IMAGE)


class TestTheVariableAllowlist:
    def test_an_allowlisted_variable_is_accepted(self) -> None:
        assert validate().ok is True

    def test_a_variable_not_on_the_allowlist_is_refused(self) -> None:
        """The whole point: a model cannot arrange to set an arbitrary key."""
        result = validate(variable="spark.executor.extraJavaOptions")

        assert result.ok is False
        assert result.plan is None
        assert result.rejection is not None
        assert "not allowed" in result.rejection.reason
        assert "spark.sql.shuffle.partitions" in result.rejection.detail

    def test_an_arbitrary_shell_looking_value_is_refused(self) -> None:
        result = validate(variable="rm -rf /")
        assert result.ok is False

    def test_a_shortened_spelling_resolves_to_the_real_key(self) -> None:
        result = validate(variable="shuffle partitions")
        assert result.ok is True
        assert result.plan is not None
        assert result.plan.variable == "spark.sql.shuffle.partitions"
        assert any("read" in note for note in result.plan.adjustments)

    def test_canonical_variable_rejects_the_unknown(self) -> None:
        assert canonical_variable("spark.sql.shuffle.partitions") in ALLOWED_VARIABLES
        assert canonical_variable("spark.anything.else") is None


class TestValues:
    def test_values_are_deduplicated_and_sorted(self) -> None:
        result = validate(values=[400, 50, 200, 50])
        assert result.plan is not None
        assert result.plan.values == [50, 200, 400]
        assert any("duplicate" in note for note in result.plan.adjustments)

    def test_out_of_range_values_are_dropped_and_reported(self) -> None:
        result = validate(values=[0, 50, 200, 99_999])
        assert result.plan is not None
        assert result.plan.values == [50, 200]
        assert any("out-of-range" in note for note in result.plan.adjustments)

    def test_the_baseline_is_added_when_missing(self) -> None:
        """A comparison with no anchor cannot say anything relative."""
        result = validate(values=[50, 100])
        assert result.plan is not None
        assert 200 in result.plan.values
        assert any("baseline" in note for note in result.plan.adjustments)

    def test_a_single_comparable_value_is_refused(self) -> None:
        result = validate(values=[200], budget=ExperimentBudget())
        assert result.ok is False
        assert result.rejection is not None
        assert "not enough comparable values" in result.rejection.reason

    def test_too_many_values_is_refused(self) -> None:
        result = validate(
            values=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 200],
            budget=ExperimentBudget(max_values=4, max_runs=100),
        )
        assert result.ok is False
        assert result.rejection is not None
        assert "too many values" in result.rejection.reason


class TestMetricReconciliation:
    def test_unmeasurable_metrics_are_dropped_and_named(self) -> None:
        """Ultra was observed proposing exactly these two.

        Accepting them would promise a figure no instrument in RepoLab produces.
        """
        result = validate(metrics=["gc_time_ms", "executor_cpu_time_ms", "runtime"])

        assert result.plan is not None
        assert result.plan.dropped_metrics == ["gc_time_ms", "executor_cpu_time_ms"]
        assert "total runtime" in result.plan.metrics
        assert "gc_time_ms" not in result.plan.metrics

    def test_aliases_resolve_to_the_measured_names(self) -> None:
        result = validate(metrics=["shuffle_read", "num_tasks", "wall clock"])
        assert result.plan is not None
        assert "shuffle read" in result.plan.metrics
        assert "task count" in result.plan.metrics
        assert "total runtime" in result.plan.metrics

    def test_the_always_measured_metrics_are_present(self) -> None:
        result = validate(metrics=["total runtime"])
        assert result.plan is not None
        for metric in REQUIRED_METRICS:
            assert metric in result.plan.metrics

    def test_nothing_measurable_is_refused(self) -> None:
        result = validate(metrics=["vibes", "elegance"])
        assert result.ok is False
        assert result.rejection is not None
        assert "no measurable metrics" in result.rejection.reason

    def test_every_measurable_metric_resolves_to_itself(self) -> None:
        """The catalogue must be closed: a listed metric has to be findable."""
        for metric in measurable_metrics():
            assert canonical_metric(metric) == metric


class TestRepeatsAndBudget:
    def test_repeats_are_clamped_to_the_budget(self) -> None:
        result = validate(repeats=99, budget=ExperimentBudget(max_repeats=3))
        assert result.plan is not None
        assert result.plan.repeats == 3
        assert any("repeats 99 -> 3" in note for note in result.plan.adjustments)

    def test_a_single_repeat_is_allowed_but_flagged(self) -> None:
        result = validate(repeats=1)
        assert result.plan is not None
        assert result.plan.repeats == 1
        assert any("no spread" in note for note in result.plan.adjustments)

    def test_runs_and_time_are_estimated_from_the_shape(self) -> None:
        result = validate(values=[50, 100, 200], repeats=3)
        assert result.plan is not None
        assert result.plan.estimated_runs == 9
        assert result.plan.estimated_seconds == ExperimentBudget().estimate_seconds(9)
        assert result.plan.estimated_minutes > 0

    def test_over_the_run_budget_is_refused_not_trimmed(self) -> None:
        """Silently trimming would mean approving one experiment and running another."""
        result = validate(
            values=[10, 20, 50, 100, 200, 400],
            repeats=5,
            budget=ExperimentBudget(max_runs=12, max_values=10),
        )
        assert result.ok is False
        assert result.rejection is not None
        assert "over the run budget" in result.rejection.reason
        assert "30 runs" in result.rejection.detail

    def test_over_the_time_budget_is_refused(self) -> None:
        result = validate(values=[50, 200], repeats=2, budget=ExperimentBudget(max_seconds=1.0))
        assert result.ok is False
        assert result.rejection is not None
        assert "over the time budget" in result.rejection.reason

    def test_concurrency_lowers_the_estimate_and_is_recorded(self) -> None:
        result = validate_plan(
            proposal(values=[50, 100, 200, 400], repeats=2),
            image=IMAGE,
            concurrency=2,
        )
        assert result.plan is not None
        assert result.plan.concurrency == 2
        assert result.plan.estimated_runs == 8
        assert result.plan.estimated_seconds == ExperimentBudget().estimate_seconds(
            8, concurrency=2
        )


class TestFixedSettings:
    def test_fixed_settings_come_from_the_real_configuration(self) -> None:
        """What is held fixed is a fact about the environment, not a proposal."""
        settings = fixed_settings_for(
            SparkRunConfig(), variable="spark.sql.shuffle.partitions", image=IMAGE
        )

        assert "spark.sql.shuffle.partitions" not in settings
        assert settings["spark.sql.adaptive.enabled"] == "false"
        assert settings["image"] == IMAGE
        assert settings["master"] == "local[4]"

    def test_event_log_paths_are_not_presented_as_fixed_settings(self) -> None:
        """They are plumbing, and the directory differs per run."""
        settings = fixed_settings_for(
            SparkRunConfig(), variable="spark.sql.shuffle.partitions", image=IMAGE
        )
        assert not any(key.startswith("spark.eventLog.") for key in settings)

    def test_a_validated_plan_carries_them(self) -> None:
        result = validate()
        assert result.plan is not None
        assert result.plan.fixed_settings["image"] == IMAGE


class TestValidationResult:
    def test_require_returns_the_plan(self) -> None:
        assert validate().require().variable == "spark.sql.shuffle.partitions"

    def test_require_raises_with_the_reason(self) -> None:
        import pytest

        result = validate(variable="nope")
        with pytest.raises(ValueError, match="not allowed"):
            result.require()
