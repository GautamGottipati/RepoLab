"""The CLI experiment domain model.

A Spark experiment varies one integer setting and reads Spark's event log. A
question about Cassandra is answered differently: by running *different
statements* against the repository's real, built node and comparing what came
back. "What happens if I filter on a column that is not part of the key?" is
two variants -- filter on the key, filter off it -- and the answer is in cqlsh's
own output.

The same three-way split as the Spark model applies, for the same reason:

    CliProposedPlan    what a model suggested            -- not authoritative
    CliExperimentPlan  what validation approved to run
    CliVariantRun      what actually came back, statement by statement

Every variant runs in a **fresh keyspace RepoLab creates for it**, on one node
restored from the build's checkpoint. Variants therefore start from equivalent
schema and data state; they share the node's JVM, which is disclosed on the plan
rather than glossed over.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import EvidenceBundle, Measurement, MeasurementSource
from repolab.experiments.experiment import ExperimentLifecycle
from repolab.experiments.explanation import VerifiedExplanation
from repolab.experiments.models import MAX_HYPOTHESIS_LENGTH, MAX_QUESTION_LENGTH

#: How much of a statement's output is kept on its record. cqlsh output for a
#: small experiment is short; the bound is for the statement that is not.
MAX_OUTPUT_CHARS = 4000


def _now() -> datetime:
    return datetime.now(UTC)


class CliVariantSpec(BaseModel):
    """One variant, as proposed: a label and the statements that make it differ."""

    label: str = Field(description="A short name, e.g. 'filter on partition key'")
    statements: list[str] = Field(description="CQL statements, run in order")


class CliProposedPlan(BaseModel):
    """An experiment a model has suggested. Untrusted until validated."""

    question: str = Field(description="The user's question, restated precisely")
    hypothesis: str = Field(description="A falsifiable statement the outputs could contradict")
    setup: list[str] = Field(description="CQL run before every variant: create tables, insert rows")
    variants: list[CliVariantSpec] = Field(description="Two to four variants to compare")
    repeats: int = Field(description="How many times to run each variant")

    def model_post_init(self, _context: object) -> None:
        if len(self.question) > MAX_QUESTION_LENGTH:
            object.__setattr__(self, "question", self.question[:MAX_QUESTION_LENGTH])
        if len(self.hypothesis) > MAX_HYPOTHESIS_LENGTH:
            object.__setattr__(self, "hypothesis", self.hypothesis[:MAX_HYPOTHESIS_LENGTH])


class CliExperimentPlan(BaseModel):
    """A validated CLI experiment. Only validation produces one."""

    kind: Literal["cli"] = "cli"

    question: str
    hypothesis: str

    setup: list[str]
    variants: list[CliVariantSpec]
    repeats: int = Field(ge=1)

    environment: dict[str, str] = Field(default_factory=dict)
    """The build every variant runs on, as it will actually be used."""

    fixed_settings: dict[str, str] = Field(default_factory=dict)
    """What is held constant across variants, including how they are isolated."""

    estimated_runs: int = Field(ge=1)
    estimated_statements: int = Field(ge=1)
    estimated_seconds: float = Field(ge=0)

    concurrency: int = 1
    adjustments: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def estimated_minutes(self) -> float:
        return round(self.estimated_seconds / 60.0, 1)

    @computed_field
    @property
    def labels(self) -> list[str]:
        return [variant.label for variant in self.variants]

    def summary(self) -> str:
        """A one-line description, for an approval prompt."""
        return (
            f"compare {len(self.variants)} variants ({', '.join(self.labels)}), "
            f"{self.repeats} repeat(s) = {self.estimated_runs} runs of "
            f"{self.estimated_statements} CQL statements, about {self.estimated_minutes} min"
        )


class StatementOutcome(BaseModel):
    """One statement and exactly what cqlsh returned for it."""

    index: int = Field(ge=1)
    statement: str
    phase: Literal["setup", "variant"]

    exit_code: int | None = None
    refused: str | None = None
    duration_ms: float = 0.0
    """Wall clock for the whole ``cqlsh -e`` invocation, including cqlsh's start."""

    rows: int | None = None
    """Parsed from cqlsh's own ``(N rows)`` footer, when it printed one."""

    error_class: str | None = None
    """The server's error class, e.g. ``InvalidRequest``, parsed from stderr."""

    error_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    @computed_field
    @property
    def ok(self) -> bool:
        return self.refused is None and self.exit_code == 0 and self.error_class is None


class CliVariantRun(BaseModel):
    """One execution of one variant, in its own keyspace."""

    variant_id: str
    label: str
    variant_index: int = Field(ge=0)
    repeat: int = Field(ge=1)
    keyspace: str
    checkpoint_tag: str | None = None

    setup: list[StatementOutcome] = Field(default_factory=list)
    statements: list[StatementOutcome] = Field(default_factory=list)
    wall_clock_seconds: float = 0.0
    ran_at: datetime = Field(default_factory=_now)

    def to_measurements(self) -> list[Measurement]:
        """Evidence-bundle measurements for the variant's own statements."""
        measurements: list[Measurement] = []
        for outcome in self.statements:
            prefix = metric_prefix(self.label, outcome.index)
            measurements.append(
                Measurement(
                    name=f"{prefix} duration_ms",
                    value=outcome.duration_ms,
                    unit="ms",
                    source=MeasurementSource.PROCESS_TIMING,
                    variant_id=self.variant_id,
                )
            )
            measurements.append(
                Measurement(
                    name=f"{prefix} succeeded",
                    value=1.0 if outcome.ok else 0.0,
                    unit="bool",
                    source=MeasurementSource.CLI_OUTPUT,
                    variant_id=self.variant_id,
                )
            )
            if outcome.rows is not None:
                measurements.append(
                    Measurement(
                        name=f"{prefix} rows",
                        value=float(outcome.rows),
                        unit="rows",
                        source=MeasurementSource.CLI_OUTPUT,
                        variant_id=self.variant_id,
                    )
                )
        return measurements


def metric_prefix(label: str, index: int) -> str:
    """How one variant statement's metrics are named, everywhere they appear."""
    return f"{label} / statement {index}"


class Spread(BaseModel):
    """Repeated samples of one quantity, summarised. Computed by Python only."""

    samples: list[float]
    median: float
    minimum: float
    maximum: float
    spread: float
    stddev: float
    """Population standard deviation; 0.0 for a single sample."""

    mean: float

    @computed_field
    @property
    def count(self) -> int:
        return len(self.samples)


class StatementStatistics(BaseModel):
    """One variant statement, across its repeats."""

    index: int
    statement: str
    runs: int
    succeeded_runs: int
    duration_ms: Spread
    rows: list[int | None] = Field(default_factory=list)
    rows_median: float | None = None
    rows_stable: bool = True
    """Whether every repeat returned the same row count. Structural, so it should."""

    error_classes: list[str] = Field(default_factory=list)
    error_message: str | None = None
    """The server's first error line, verbatim, when the statement failed."""


class CliVariantStatistics(BaseModel):
    variant_id: str
    label: str
    runs: int
    failed_statements: int
    total_duration_ms: Spread
    statements: list[StatementStatistics] = Field(default_factory=list)


class CliStatistics(BaseModel):
    """Everything computed from the runs. The model is given this, never asked for it."""

    variants: list[CliVariantStatistics] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    """Every metric name a ``measured`` claim may cite."""

    setup_failures: int = 0


class CliReproducibilityRecord(BaseModel):
    """What it would take to run this experiment again."""

    repo_url: str
    commit_sha: str

    build_kind: str
    fidelity: str
    application_version: str | None = None
    checkpoint_tag: str | None = None
    cli_command: str | None = None
    memory: str | None = None

    repeats: int = Field(ge=1)
    isolation: str = ""

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @computed_field
    @property
    def reproducible(self) -> bool:
        """A pinned checkpoint and a recorded commit; outputs should match again."""
        return bool(self.checkpoint_tag and len(self.commit_sha) >= 7)


class CliExperiment(ExperimentLifecycle):
    """One question, investigated by running statements on the real node."""

    kind: Literal["cli"] = "cli"

    proposal: CliProposedPlan | None = None
    plan: CliExperimentPlan | None = None

    runs: list[CliVariantRun] = Field(default_factory=list)
    statistics: CliStatistics | None = None
    evidence: EvidenceBundle | None = None
    explanation: VerifiedExplanation | None = None
    reproducibility: CliReproducibilityRecord | None = None

    @computed_field
    @property
    def run_count(self) -> int:
        return len(self.runs)

    def approval_prompt(self) -> dict[str, object]:
        if self.plan is None:
            return {"question": self.question, "status": self.status.value}
        return {
            "kind": "cli",
            "question": self.plan.question,
            "hypothesis": self.plan.hypothesis,
            "variants": [variant.model_dump() for variant in self.plan.variants],
            "setup": self.plan.setup,
            "repeats": self.plan.repeats,
            "estimated_runs": self.plan.estimated_runs,
            "estimated_seconds": self.plan.estimated_seconds,
            "estimated_minutes": self.plan.estimated_minutes,
            "adjustments": self.plan.adjustments,
            "summary": self.plan.summary(),
        }
