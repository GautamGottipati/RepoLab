"""Plan validation: the teeth of the approval gate.

A model proposes an experiment. **Deterministic code decides what may run.**
That split is the whole point of this module: without it, "agentic" would mean a
language model choosing what to execute and how much compute to spend.

Four things are checked, in this order:

1. **The variable must be on an allowlist.** Not "looks like a Spark setting" --
   on a list, in this file. A proposal naming anything else is rejected, so no
   prompt can arrange for an arbitrary configuration key to be set.
2. **Values must be in range and comparable.** Deduplicated, sorted, bounded, and
   the baseline is always included, because a comparison needs something to be
   relative to.
3. **Metrics must be measurable.** Reconciled against what
   :class:`~repolab.experiences.spark.measurements.SparkRunMeasurements` actually
   extracts from a Spark event log. Ultra has been observed proposing ``gc_time_ms``
   and ``executor_cpu_time_ms``, neither of which RepoLab parses; accepting those
   would mean promising a measurement that could never arrive.
4. **The cost must be known and affordable.** Runs and seconds are estimated from
   a measured per-run baseline, and a plan over budget is rejected with a reason
   rather than quietly trimmed.

Adjustments are never silent: everything validation changes is recorded on the
plan and shown in the approval prompt, so the user approves what will actually
run.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, computed_field

from repolab.experiences.spark.driver import DEFAULT_SHUFFLE_PARTITIONS, SparkRunConfig
from repolab.experiments.models import ExperimentPlan, ProposedPlan

#: Configuration keys an experiment is allowed to vary.
#:
#: An allowlist rather than a denylist: the failure mode of a missing denylist
#: entry is executing something nobody vetted. Each entry carries the bounds that
#: make a value sane for that key.
ALLOWED_VARIABLES: dict[str, tuple[int, int]] = {
    "spark.sql.shuffle.partitions": (1, 2000),
}

#: Accepted spellings of the one allowed variable. Models shorten it.
VARIABLE_ALIASES: dict[str, str] = {
    "shuffle partitions": "spark.sql.shuffle.partitions",
    "shuffle partition count": "spark.sql.shuffle.partitions",
    "sql shuffle partitions": "spark.sql.shuffle.partitions",
    "partitions": "spark.sql.shuffle.partitions",
    "partition count": "spark.sql.shuffle.partitions",
    "num partitions": "spark.sql.shuffle.partitions",
}

#: Metric name -> the measurement RepoLab actually extracts.
#:
#: Keys are normalised (lowercase, underscores and hyphens as spaces). The
#: canonical names on the right are the ones
#: :meth:`SparkRunMeasurements.metrics` emits, so a validated metric is
#: guaranteed to be findable on a real run.
METRIC_ALIASES: dict[str, str] = {
    # timing
    "total runtime": "total runtime",
    "runtime": "total runtime",
    "duration": "total runtime",
    "job duration": "total runtime",
    "execution time": "total runtime",
    "wall clock": "total runtime",
    "wall clock time": "total runtime",
    "elapsed time": "total runtime",
    "slowest task": "slowest task",
    "max task duration": "slowest task",
    "slowest task duration": "slowest task",
    "median task": "median task",
    "median task duration": "median task",
    "task skew ratio": "task skew ratio",
    "task skew": "task skew ratio",
    # structure
    "stage count": "stage count",
    "stages": "stage count",
    "num stages": "stage count",
    "task count": "task count",
    "tasks": "task count",
    "num tasks": "task count",
    "number of tasks": "task count",
    "shuffle read": "shuffle read",
    "shuffle read bytes": "shuffle read",
    "shuffle bytes read": "shuffle read",
    "shuffle write": "shuffle write",
    "shuffle write bytes": "shuffle write",
    "shuffle bytes written": "shuffle write",
    "shuffle records read": "shuffle records read",
    "memory spilled": "memory spilled",
    "spill": "memory spilled",
    "memory bytes spilled": "memory spilled",
    "disk spilled": "disk spilled",
    "disk bytes spilled": "disk spilled",
    "partitions": "partitions",
    "shuffle partitions": "partitions",
    "partition count": "partitions",
    "empty partitions": "empty partitions",
    "empty partition count": "empty partitions",
    "heaviest partition records": "heaviest partition records",
    "max partition records": "heaviest partition records",
    "partition skew ratio": "partition skew ratio",
    "partition skew": "partition skew ratio",
    "data skew": "partition skew ratio",
}

#: Always measured, whatever was proposed.
#:
#: All of these come from the same event log at no additional cost, and together
#: they are what makes the shuffle-partition question answerable at all: how many
#: partitions there were, how many did nothing, how long it took, and how much
#: data moved.
REQUIRED_METRICS: tuple[str, ...] = (
    "total runtime",
    "task count",
    "partitions",
    "empty partitions",
    "shuffle read",
)


class ExperimentBudget(BaseModel):
    """What an experiment is allowed to cost."""

    max_runs: int = Field(default=24, ge=1)
    max_seconds: float = Field(default=900.0, gt=0)
    max_repeats: int = Field(default=5, ge=1)
    max_values: int = Field(default=8, ge=2)

    seconds_per_run: float = Field(default=14.0, gt=0)
    """Measured in Phase 6: ~11 s of Spark plus container start, stop and upload.

    An estimate, and labelled as one wherever it is shown. It exists so the user
    approves a known cost rather than an open-ended one.
    """

    def estimate_seconds(self, runs: int, *, concurrency: int = 1) -> float:
        """Estimated wall clock for *runs* runs at *concurrency*."""
        effective = max(1, concurrency)
        return round(runs * self.seconds_per_run / effective, 1)


class PlanRejected(BaseModel):
    """Why a proposal will not be run."""

    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


class ValidationResult(BaseModel):
    """The outcome of validating a proposal.

    A result rather than an exception: the API has to report *why* a plan was
    refused, and the reason is as much a part of the answer as the plan.
    """

    plan: ExperimentPlan | None = None
    rejection: PlanRejected | None = None

    @computed_field
    @property
    def ok(self) -> bool:
        return self.plan is not None

    def require(self) -> ExperimentPlan:
        """The plan, or raise.

        Raises:
            ValueError: the proposal was rejected.
        """
        if self.plan is None:
            raise ValueError(str(self.rejection or "plan was rejected"))
        return self.plan


def normalise(name: str) -> str:
    """Lowercase, with underscores and hyphens treated as spaces."""
    collapsed = re.sub(r"[\s_\-]+", " ", name.strip().lower())
    return re.sub(r"\s+", " ", collapsed)


def canonical_variable(name: str) -> str | None:
    """The allowlisted variable *name* refers to, or ``None``."""
    raw = name.strip()
    if raw in ALLOWED_VARIABLES:
        return raw

    lowered = raw.lower()
    if lowered in ALLOWED_VARIABLES:
        return lowered

    return VARIABLE_ALIASES.get(normalise(raw))


def canonical_metric(name: str) -> str | None:
    """The measurable metric *name* refers to, or ``None`` if RepoLab cannot measure it."""
    return METRIC_ALIASES.get(normalise(name))


def variable_in_question(question: str) -> str | None:
    """The allowlisted variable a question is about, if it names one.

    Matching the *whole* question against the alias table never succeeds, so this
    scans for an allowlisted key or alias appearing within it -- longest first, so
    "shuffle partitions" wins over the broader "partitions". Deterministic, and it
    only ever returns something already on the allowlist.
    """
    normalised = normalise(question)
    candidates = sorted([*ALLOWED_VARIABLES, *VARIABLE_ALIASES], key=len, reverse=True)
    for candidate in candidates:
        if candidate in normalised or normalise(candidate) in normalised:
            return canonical_variable(candidate)
    return None


def measurable_metrics() -> list[str]:
    """Every metric RepoLab can measure, for a prompt or an error message."""
    return sorted(set(METRIC_ALIASES.values()))


def fixed_settings_for(config: SparkRunConfig, *, variable: str, image: str) -> dict[str, str]:
    """What is held constant across variants, as it will actually be applied.

    Taken from the real run configuration rather than from the proposal: what is
    fixed is a fact about the environment RepoLab builds, not something for a
    model to assert. The varied key is excluded, since it is the one thing that
    is not fixed.
    """
    settings = {
        key: value
        for key, value in config.spark_conf("/work/events").items()
        if key != variable and not key.startswith("spark.eventLog.")
    }
    settings["image"] = image
    settings["master"] = config.master
    settings["steps_per_floor"] = str(config.steps_per_floor)
    return dict(sorted(settings.items()))


def validate_plan(
    proposal: ProposedPlan,
    *,
    budget: ExperimentBudget | None = None,
    baseline_value: int = DEFAULT_SHUFFLE_PARTITIONS,
    config: SparkRunConfig | None = None,
    image: str = "",
    concurrency: int = 1,
) -> ValidationResult:
    """Turn an untrusted proposal into an executable plan, or refuse it."""
    budget = budget if budget is not None else ExperimentBudget()
    config = config if config is not None else SparkRunConfig()
    adjustments: list[str] = []

    variable = canonical_variable(proposal.variable)
    if variable is None:
        return ValidationResult(
            rejection=PlanRejected(
                reason="variable is not allowed",
                detail=(
                    f"{proposal.variable!r} is not on the allowlist; "
                    f"permitted: {', '.join(sorted(ALLOWED_VARIABLES))}"
                ),
            )
        )
    if variable != proposal.variable:
        adjustments.append(f"read {proposal.variable!r} as {variable}")

    low, high = ALLOWED_VARIABLES[variable]
    values, value_notes = _clean_values(
        proposal.values, low=low, high=high, baseline=baseline_value
    )
    adjustments.extend(value_notes)

    if len(values) < 2:
        return ValidationResult(
            rejection=PlanRejected(
                reason="not enough comparable values",
                detail=(
                    f"need at least two distinct values in [{low}, {high}]; "
                    f"got {sorted(set(proposal.values))}"
                ),
            )
        )
    if len(values) > budget.max_values:
        return ValidationResult(
            rejection=PlanRejected(
                reason="too many values",
                detail=f"{len(values)} values exceeds the limit of {budget.max_values}",
            )
        )

    requested, dropped, metric_notes = _reconcile_metrics(proposal.metrics)
    adjustments.extend(metric_notes)

    # Checked before the always-measured set is added. A proposal where *nothing*
    # requested can be measured is a failed design -- the model ignored the list
    # it was given -- and quietly substituting RepoLab's own metrics would hide
    # that behind a plausible-looking plan.
    if not requested:
        return ValidationResult(
            rejection=PlanRejected(
                reason="no measurable metrics",
                detail=(
                    f"none of {proposal.metrics} can be measured; "
                    f"available: {', '.join(measurable_metrics())}"
                ),
            )
        )

    metrics, added_notes = _add_required_metrics(requested)
    adjustments.extend(added_notes)

    repeats = max(1, min(proposal.repeats, budget.max_repeats))
    if repeats != proposal.repeats:
        adjustments.append(f"repeats {proposal.repeats} -> {repeats} (budget)")
    if repeats == 1:
        adjustments.append("a single repeat gives no spread, so timings carry no error bar")

    runs = len(values) * repeats
    seconds = budget.estimate_seconds(runs, concurrency=concurrency)

    if runs > budget.max_runs:
        return ValidationResult(
            rejection=PlanRejected(
                reason="over the run budget",
                detail=(
                    f"{len(values)} values x {repeats} repeats = {runs} runs, "
                    f"over the limit of {budget.max_runs}"
                ),
            )
        )
    if seconds > budget.max_seconds:
        return ValidationResult(
            rejection=PlanRejected(
                reason="over the time budget",
                detail=(
                    f"an estimated {seconds:.0f}s exceeds the limit of {budget.max_seconds:.0f}s"
                ),
            )
        )

    return ValidationResult(
        plan=ExperimentPlan(
            question=proposal.question,
            hypothesis=proposal.hypothesis,
            variable=variable,
            values=values,
            fixed_settings=fixed_settings_for(config, variable=variable, image=image),
            metrics=metrics,
            repeats=repeats,
            estimated_runs=runs,
            estimated_seconds=seconds,
            baseline_value=baseline_value,
            concurrency=max(1, concurrency),
            dropped_metrics=dropped,
            adjustments=adjustments,
        )
    )


def _clean_values(
    proposed: list[int], *, low: int, high: int, baseline: int
) -> tuple[list[int], list[str]]:
    """Deduplicate, bound and sort the values, always including the baseline."""
    notes: list[str] = []

    out_of_range = sorted({value for value in proposed if not low <= value <= high})
    if out_of_range:
        notes.append(f"dropped out-of-range value(s) {out_of_range}; allowed [{low}, {high}]")

    kept = {value for value in proposed if low <= value <= high}
    if len(proposed) != len(set(proposed)):
        notes.append("removed duplicate value(s)")

    # The baseline is what the repository does today. Without it in the set there
    # is nothing to compare against, so it is added rather than assumed.
    if baseline not in kept and low <= baseline <= high:
        kept.add(baseline)
        notes.append(f"added the baseline {baseline} so the comparison has an anchor")

    return sorted(kept), notes


def _reconcile_metrics(proposed: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Map proposed metrics onto measurable ones.

    Returns the canonical metrics, the proposals that had to be dropped, and notes
    describing both. A dropped metric is *reported*: the alternative is promising
    a figure that no instrument in RepoLab produces.
    """
    canonical: list[str] = []
    dropped: list[str] = []
    notes: list[str] = []

    for name in proposed:
        resolved = canonical_metric(name)
        if resolved is None:
            dropped.append(name)
        elif resolved not in canonical:
            canonical.append(resolved)

    if dropped:
        notes.append(f"cannot measure {dropped}; dropped from the plan")
    return canonical, dropped, notes


def _add_required_metrics(metrics: list[str]) -> tuple[list[str], list[str]]:
    """Add the metrics RepoLab always records.

    Free: they come from the same event log, and without them the shuffle
    question has no evidence behind it.
    """
    added = [metric for metric in REQUIRED_METRICS if metric not in metrics]
    if not added:
        return list(metrics), []
    return [*metrics, *added], [f"added always-measured metric(s) {added}"]
