"""The experiment domain model.

An experiment is a question turned into a controlled comparison. The types here
keep three things strictly apart, because conflating them is how a measurement
becomes a guess:

    ProposedPlan     what a model suggested      -- not authoritative
    ExperimentPlan   what deterministic code approved and will run
    VariantRun       what actually happened, with its raw measurement source

Statistics are never carried on a model's output. They are computed from
:class:`VariantRun` by :mod:`repolab.experiments.statistics`, which is Python
only -- CLAUDE.md is explicit that models may reason about measurements but never
produce them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import Measurement
from repolab.experiences.spark.dataset import DatasetSpec
from repolab.experiences.spark.measurements import SparkMetric, SparkRunMeasurements

#: Bound on a question, so a prompt cannot arrive as a wall of text.
MAX_QUESTION_LENGTH = 500

#: Bound on a hypothesis.
MAX_HYPOTHESIS_LENGTH = 1000


def _now() -> datetime:
    return datetime.now(UTC)


class ExperimentStatus(StrEnum):
    """Where an experiment stands.

    ``AWAITING_APPROVAL`` is a real state rather than a flag: nothing may execute
    while an experiment sits in it, and the only transitions out are approval and
    rejection.
    """

    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    ANSWERED = "answered"
    """The question was not one to test, so it was answered from evidence instead."""

    @property
    def is_terminal(self) -> bool:
        return self in {
            ExperimentStatus.COMPLETED,
            ExperimentStatus.FAILED,
            ExperimentStatus.REJECTED,
            ExperimentStatus.ANSWERED,
        }

    @property
    def may_execute(self) -> bool:
        """Whether execution is permitted from this state.

        The approval gate lives here and in :mod:`repolab.experiments.executor`,
        in deterministic code -- never in a prompt, because an agent must not be
        able to talk its way past it.
        """
        return self in {ExperimentStatus.APPROVED, ExperimentStatus.RUNNING}


class ProposedPlan(BaseModel):
    """An experiment a model has suggested.

    Deliberately *not* the type anything executes. Its values are untrusted: a
    model may name a variable RepoLab will not set, metrics RepoLab cannot
    measure, or a repeat count that would take an hour.

    ``fixed_settings`` is absent on purpose. What is held fixed is a fact about
    the environment RepoLab will build, not something to propose, so it is filled
    in by :mod:`repolab.experiments.validation` from the real run configuration.
    """

    question: str = Field(description="The user's question, restated precisely")
    hypothesis: str = Field(description="A falsifiable statement this experiment would test")
    variable: str = Field(description="The single configuration key to vary")
    values: list[int] = Field(description="The values to try for that key")
    metrics: list[str] = Field(description="The quantities to compare across values")
    repeats: int = Field(description="How many times to run each value")

    def model_post_init(self, _context: object) -> None:
        if len(self.question) > MAX_QUESTION_LENGTH:
            object.__setattr__(self, "question", self.question[:MAX_QUESTION_LENGTH])
        if len(self.hypothesis) > MAX_HYPOTHESIS_LENGTH:
            object.__setattr__(self, "hypothesis", self.hypothesis[:MAX_HYPOTHESIS_LENGTH])


class ExperimentPlan(BaseModel):
    """A validated, executable experiment plan.

    Only deterministic validation produces one of these, so holding an
    ``ExperimentPlan`` means the variable is on the allowlist, every value is in
    range, every metric is actually measurable, and the cost is known in advance.
    """

    question: str
    hypothesis: str

    variable: str
    values: list[int]
    fixed_settings: dict[str, str] = Field(default_factory=dict)
    """Everything held constant across variants, as it will actually be applied."""

    metrics: list[str]
    """Metric names RepoLab can measure, after reconciliation."""

    repeats: int = Field(ge=1)

    estimated_runs: int = Field(ge=1)
    estimated_seconds: float = Field(ge=0)

    baseline_value: int
    """The value the repository runs today; every comparison is relative to it."""

    concurrency: int = Field(default=1, ge=1)

    dropped_metrics: list[str] = Field(default_factory=list)
    """Metrics that were proposed but cannot be measured. Reported, not hidden."""

    adjustments: list[str] = Field(default_factory=list)
    """What validation changed about the proposal, and why."""

    @computed_field
    @property
    def estimated_minutes(self) -> float:
        return round(self.estimated_seconds / 60.0, 1)

    def summary(self) -> str:
        """A one-line description, for an approval prompt."""
        values = ", ".join(str(value) for value in self.values)
        return (
            f"vary {self.variable} over [{values}], {self.repeats} repeat(s) "
            f"= {self.estimated_runs} runs, about {self.estimated_minutes} min"
        )


class VariantRun(BaseModel):
    """One execution of one variant value.

    Carries its own raw measurement source: the preserved Spark event log and its
    hash. Every statistic computed later traces back to one of these.
    """

    variant_id: str
    value: int
    repeat: int = Field(ge=1)

    run_id: str
    dataset_hash: str
    image: str
    checkpoint_id: str | None = None

    measurements: SparkRunMeasurements
    event_log_path: str
    event_log_sha256: str
    wall_clock_seconds: float = 0.0

    timing_contended: bool = False
    """True when this ran alongside another variant, so its timings are not clean."""

    ran_at: datetime = Field(default_factory=_now)

    def metrics(self) -> dict[str, SparkMetric]:
        """This run's metrics by name."""
        return {metric.name: metric for metric in self.measurements.metrics()}

    def to_measurements(self) -> list[Measurement]:
        """Evidence-bundle measurements, tagged with this variant."""
        return self.measurements.to_measurements(variant_id=self.variant_id)


class ResourceProfile(BaseModel):
    """The compute a variant was given.

    Recorded because a timing comparison is only meaningful between runs that had
    the same resources, and because concurrency changes what a timing means.
    """

    master: str
    driver_memory: str
    container_memory: str | None = None
    host_cpus: int | None = None
    concurrency: int = 1

    @property
    def contended(self) -> bool:
        return self.concurrency > 1


class ReproducibilityRecord(BaseModel):
    """Everything needed to interpret or repeat this experiment.

    CLAUDE.md lists what must be persisted; this is that list, as a type, so a
    stored experiment cannot be missing part of it.
    """

    repo_url: str
    commit_sha: str

    image: str
    checkpoint_id: str | None = None
    application_version: str | None = None
    """The Spark version actually measured, which may differ from the pin."""

    dataset: DatasetSpec
    command: str = ""
    base_configuration: dict[str, str] = Field(default_factory=dict)
    resources: ResourceProfile

    repeats: int = Field(ge=1)
    added_steps: list[str] = Field(default_factory=list)
    """What RepoLab added to the repository's own job, disclosed here."""

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def dataset_hash(self) -> str:
        return self.dataset.sha256

    @computed_field
    @property
    def reproducible(self) -> bool:
        """Whether a later run could be set up identically.

        Requires a pinned image, a hashed dataset and a recorded commit. Timings
        will still vary; the structural measurements should not.
        """
        return bool(self.image and self.dataset.sha256 and len(self.commit_sha) >= 7)
