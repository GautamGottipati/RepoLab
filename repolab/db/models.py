"""Persistence for repository ingestion.

Five tables cover what Phase 2 produces: the repository, one row per ingestion
run (carrying the commit), and the manifest, architecture and evidence it
produced. Pydantic objects are stored as JSON via ``model_dump(mode="json")``,
which keeps the schema stable while the models evolve.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from repolab.db.base import Base, JsonColumn


def _now() -> datetime:
    return datetime.now(UTC)


class Repository(Base):
    """A repository RepoLab has seen, identified by its normalised URL."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255))
    owner: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    ingestions: Mapped[list[Ingestion]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class Ingestion(Base):
    """One ingestion run of one commit."""

    __tablename__ = "ingestions"
    __table_args__ = (UniqueConstraint("repository_id", "commit_sha", name="uq_ingestion_commit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    commit_sha: Mapped[str] = mapped_column(String(64), index=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="completed")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_size: Mapped[int] = mapped_column(Integer, default=0)
    files_read: Mapped[int] = mapped_column(Integer, default=0)
    bytes_read: Mapped[int] = mapped_column(Integer, default=0)
    tree_truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    limits_hit: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    notes: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    languages: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    llm_enriched: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repository: Mapped[Repository] = relationship(back_populates="ingestions")
    manifest: Mapped[RepoManifestRecord | None] = relationship(
        back_populates="ingestion", cascade="all, delete-orphan", uselist=False
    )
    architecture: Mapped[ArchitectureRecord | None] = relationship(
        back_populates="ingestion", cascade="all, delete-orphan", uselist=False
    )
    evidence: Mapped[list[IngestionEvidence]] = relationship(
        back_populates="ingestion", cascade="all, delete-orphan"
    )


class RepoManifestRecord(Base):
    """The ``RepoManifest`` produced by an ingestion."""

    __tablename__ = "repo_manifests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_id: Mapped[int] = mapped_column(
        ForeignKey("ingestions.id", ondelete="CASCADE"), unique=True, index=True
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    primary_surface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    ingestion: Mapped[Ingestion] = relationship(back_populates="manifest")


class ArchitectureRecord(Base):
    """The architecture representation produced by an ingestion."""

    __tablename__ = "architectures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_id: Mapped[int] = mapped_column(
        ForeignKey("ingestions.id", ondelete="CASCADE"), unique=True, index=True
    )
    graph: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    generated_by: Mapped[str] = mapped_column(String(32), default="deterministic")
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    ingestion: Mapped[Ingestion] = relationship(back_populates="architecture")


class IngestionEvidence(Base):
    """An evidence bundle supporting one ingestion's claims."""

    __tablename__ = "ingestion_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_id: Mapped[int] = mapped_column(
        ForeignKey("ingestions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64), default="ingestion")
    bundle: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    ingestion: Mapped[Ingestion] = relationship(back_populates="evidence")


# ---------------------------------------------------------------------------
# Jobs, agent runs and analytics
#
# Long-running work is a job; a job runs agents; agents take steps; steps call
# tools. Everything an agent does is auditable, which is what makes approval
# gates and experiment reproducibility possible later.
# ---------------------------------------------------------------------------


class Job(Base):
    """One unit of background work."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), default="investigate")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    repo_url: Mapped[str] = mapped_column(String(1024))

    ingestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestions.id", ondelete="SET NULL"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    """How many times this job has been attempted, for retry state."""

    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    """Sequence of the most recent event, so a reader knows where the tail is."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[JobEventRecord]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list[AgentRunRecord]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobEventRecord(Base):
    """One progress event.

    ``seq`` is assigned by the emitter and unique per job: a database serial could
    become visible out of order relative to its value, which would let a polling
    reader skip an event permanently.
    """

    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped[Job] = relationship(back_populates="events")


class AgentRunRecord(Base):
    """One agent execution within a job."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="running")
    objective: Mapped[str] = mapped_column(Text, default="")

    iterations: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=0)
    planner: Mapped[str] = mapped_column(String(64), default="deterministic")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="agent_runs")
    steps: Mapped[list[AgentStepRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentStepRecord(Base):
    """One iteration of an agent loop.

    ``status`` is a concise user-facing line. Model chain-of-thought is never
    stored here.
    """

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped[AgentRunRecord] = relationship(back_populates="steps")
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )


class ToolCallRecord(Base):
    """One deterministic tool invocation.

    Arguments and results are stored as summaries: repository file contents are
    never copied into the audit trail.
    """

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id: Mapped[int] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="CASCADE"), index=True
    )
    tool: Mapped[str] = mapped_column(String(128), index=True)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    step: Mapped[AgentStepRecord] = relationship(back_populates="tool_calls")


class AnalyticsEventRecord(Base):
    """One product analytics event.

    A writer only: RepoLab records these from the beginning so KPIs are available
    later, but builds no dashboard yet.
    """

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    repo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    properties: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Build(Base):
    """A built environment for one repository commit.

    Keyed by repository and commit so the same commit resolves to the same
    checkpoint instead of being rebuilt. Only ready builds are stored, so a cache
    hit can always be trusted.
    """

    __tablename__ = "builds"
    __table_args__ = (
        # One row per *rung*, not per commit. A ladder can leave several ready
        # artifacts for one commit -- the official release that served while the
        # source build compiled is still worth keeping afterwards.
        UniqueConstraint("repo_url", "commit_sha", "kind", name="uq_build_commit_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), index=True)

    kind: Mapped[str] = mapped_column(String(32), default="source_build")
    fidelity: Mapped[str] = mapped_column(String(32), default="exact")
    provider: Mapped[str] = mapped_column(String(64), default="unknown")
    base_image: Mapped[str] = mapped_column(String(512), default="")

    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    """The application version this environment provides, when it reported one."""

    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    """True only for a Mini Lab. Stored so a query can exclude simulated rows."""

    checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_tag: Mapped[str | None] = mapped_column(String(256), nullable=True)

    service_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What makes this checkpoint a running server, so a session can restore it."""

    cli_command: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """The binary a terminal session drives, which differs per rung."""

    memory: Mapped[str | None] = mapped_column(String(32), nullable=True)

    ready: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    smoke: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    attempts: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExperimentRecord(Base):
    """One experiment: a question, the plan approved for it, and what it measured.

    Carries the reproducibility record CLAUDE.md requires -- commit, environment,
    dataset hash, configuration, resources, repeats, timestamps -- plus the
    aggregated statistics and the verified explanation. The raw measurements live
    in ``experiment_variants`` and ``measurements``, and the raw event logs stay
    on disk, referenced by hash.
    """

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    question: Mapped[str] = mapped_column(Text)
    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)

    kind: Mapped[str] = mapped_column(String(16), default="spark", server_default="spark")
    """``pending`` until triaged, then ``answer``, ``spark`` or ``cli``."""

    triage: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    """How the question was classified, and why it was answered or tested."""

    answer: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    """An answered question's grounded answer and evidence."""

    runs: Mapped[list[Any] | None] = mapped_column(JsonColumn, nullable=True)
    """A CLI experiment's runs, statement by statement, as cqlsh returned them."""

    variable: Mapped[str | None] = mapped_column(String(255), nullable=True)
    baseline_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repeats: Mapped[int] = mapped_column(Integer, default=0)
    estimated_runs: Mapped[int] = mapped_column(Integer, default=0)
    estimated_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    image: Mapped[str] = mapped_column(String(512), default="")
    dataset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    proposal: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    """What the model suggested, kept so the audit trail shows what was asked for."""

    plan: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    rejection: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    statistics: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    reproducibility: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Why the explanation is missing, when a model was configured but failed."""

    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    variants: Mapped[list[ExperimentVariantRecord]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    measurements: Mapped[list[MeasurementRecord]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class ExperimentVariantRecord(Base):
    """One execution of one variant value.

    Unique on (experiment, variant, repeat) so re-saving an experiment updates its
    runs instead of duplicating them. ``timing_contended`` is stored rather than
    derived: whether a duration was measured under contention is a property of
    that run and must survive into any later reading of it.
    """

    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "variant_id", "repeat", name="uq_experiment_variant_repeat"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )

    variant_id: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[int] = mapped_column(Integer)
    repeat: Mapped[int] = mapped_column(Integer, default=1)

    run_id: Mapped[str] = mapped_column(String(36), index=True)
    dataset_hash: Mapped[str] = mapped_column(String(64))
    image: Mapped[str] = mapped_column(String(512), default="")
    checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    measurements: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    # The raw measurement source. Preserved on disk; never discarded.
    event_log_path: Mapped[str] = mapped_column(String(1024), default="")
    event_log_sha256: Mapped[str] = mapped_column(String(64), default="")

    task_count: Mapped[int] = mapped_column(Integer, default=0)
    total_runtime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wall_clock_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    timing_contended: Mapped[bool] = mapped_column(Boolean, default=False)

    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    experiment: Mapped[ExperimentRecord] = relationship(back_populates="variants")
    measurement_rows: Mapped[list[MeasurementRecord]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )


class MeasurementRecord(Base):
    """One measured quantity from one run.

    Stored individually as well as inside the variant's JSON because a measurement
    is the unit an explanation cites, and because querying across experiments for
    one metric should not mean unpacking JSON.
    """

    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    variant_pk: Mapped[int | None] = mapped_column(
        ForeignKey("experiment_variants.id", ondelete="CASCADE"), nullable=True, index=True
    )

    variant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    """Which instrument produced it, e.g. ``spark_event_log``."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    experiment: Mapped[ExperimentRecord] = relationship(back_populates="measurements")
    variant: Mapped[ExperimentVariantRecord | None] = relationship(
        back_populates="measurement_rows"
    )


class SparkRun(Base):
    """One measured Spark run.

    Records everything needed to interpret or reproduce the measurements: the
    commit, the dataset hash, the configuration, the environment, and a pointer to
    the preserved raw event log that every figure was derived from.
    """

    __tablename__ = "spark_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)

    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    """The job that produced this run, so a browser watching a job can find it.

    Nullable: a run started from the command line has no job.
    """

    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), index=True)
    dataset_hash: Mapped[str] = mapped_column(String(64), index=True)

    configuration: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    environment: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    dataset: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    measurements: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    # The raw measurement source. Preserved on disk; never discarded.
    event_log_path: Mapped[str] = mapped_column(String(1024))
    event_log_sha256: Mapped[str] = mapped_column(String(64))
    event_log_bytes: Mapped[int] = mapped_column(Integer, default=0)

    task_count: Mapped[int] = mapped_column(Integer, default=0)
    stage_count: Mapped[int] = mapped_column(Integer, default=0)
    total_runtime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wall_clock_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LessonRecord(Base):
    """One generated lesson for one repository commit.

    Stored because the worker writes it and the API serves it: generating a
    lesson involves a model call and a repository read, neither of which belongs
    in a request. One row per (repo, commit), so re-generating replaces rather
    than accumulates -- a learner should get the current lesson, not a pile of
    historical ones.
    """

    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("repo_url", "commit_sha", name="uq_lesson_commit"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), default="", index=True)

    title: Mapped[str] = mapped_column(String(512), default="")
    generated_by: Mapped[str] = mapped_column(String(32), default="deterministic")
    """``super`` when a model wrote it, ``deterministic`` when RepoLab did."""

    fidelity: Mapped[str] = mapped_column(String(32), default="unknown")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    step_count: Mapped[int] = mapped_column(Integer, default=0)

    lesson: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    """The whole lesson, as its model serialises. The steps are the lesson."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    progress: Mapped[list[LessonProgressRecord]] = relationship(
        back_populates="lesson_record", cascade="all, delete-orphan"
    )


class LessonProgressRecord(Base):
    """How far one learner got, and every attempt they made.

    Keyed by terminal session as well as lesson, so a browser reload finds the
    same progress rather than starting over. Attempts are kept in full: what a
    learner tried, and whether a deterministic check or a model decided it, is
    the record of their work and not a detail to discard once a step passes.
    """

    __tablename__ = "lesson_progress"
    __table_args__ = (
        UniqueConstraint("lesson_id", "session_id", name="uq_lesson_progress_session"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)

    completed: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    attempts: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    finished: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    lesson_record: Mapped[LessonRecord] = relationship(back_populates="progress")


class ModelCallRecord(Base):
    """One routed model call, as `CallRecord` describes it.

    Stored so the operational metrics CLAUDE.md names -- model latency, token
    usage, estimated cost, schema-validation failures -- can be answered by a
    query rather than by reading logs. The router built one of these on every
    call from Phase 1 and only ever logged it.

    **No prompt and no output text, by construction.** `CallRecord` holds none,
    and this table has no column one could be written to: what a model was asked
    is not a metric, and a telemetry table is a poor place to accumulate it.
    """

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    tier: Mapped[str] = mapped_column(String(16), index=True)
    model: Mapped[str] = mapped_column(String(255), index=True)
    purpose: Mapped[str] = mapped_column(String(64), index=True)
    schema_name: Mapped[str] = mapped_column(String(128), default="")

    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Null unless MODEL_PRICING is configured. Never guessed."""

    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class RuntimeRecord(Base):
    """One launch of a repository's services: its plan, and where each stands.

    The proxy reads only this row to decide where a request may go: a runtime
    that is not running, or a service it does not list, is not reachable.
    """

    __tablename__ = "runtimes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    """The job whose event stream carries this launch's progress."""

    status: Mapped[str] = mapped_column(String(32), default="planning", index=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    state: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    """Touched by the proxy, so an idle runtime can be reaped."""


class ExperienceBuildRecord(Base):
    """An environment an experience runs in, built once per commit and reused.

    A Spark source build of the investigated commit, or a Python package
    installed into an image. Only the checkpoint is kept; the build log's tail is
    kept for the failure screen.
    """

    __tablename__ = "experience_builds"
    __table_args__ = (
        UniqueConstraint(
            "repo_url", "commit_sha", "adapter", "strategy", name="uq_experience_build"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64))
    adapter: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="building")
    """``building``, ``ready`` or ``failed``."""

    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """The checkpoint image runs start from, once ready."""

    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExperienceSessionRecord(Base):
    """One person's use of one experience: its runtime, and every run they made.

    Persisted so a refresh reconnects to the same session rather than starting
    over, and so an experiment can be run against exactly what they ran.
    """

    __tablename__ = "experience_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    """The investigation this session belongs to."""

    repo_url: Mapped[str] = mapped_column(String(1024), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    experience_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(32))
    reality: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="preparing", index=True)
    events_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """The job whose event stream carries this session's progress."""

    state: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    """Runtime details, runs, explanations, failures."""

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
