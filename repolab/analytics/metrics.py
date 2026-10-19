"""The KPI dashboard: what the recorded data can and cannot say.

Analytics has been written since Phase 4 and never read. This module reads it,
together with the domain tables, and computes the KPIs CLAUDE.md names.

The hard part is not the arithmetic. It is that several of the metrics asked for
have no honest source, and a dashboard that filled them in with zeros would be
worse than one that left them out:

* **Only successful builds are cached**, so a build success rate computed from
  the ``builds`` table reads 100% however many candidates failed. It is computed
  from ``build_started`` / ``build_failed`` events instead, which is why those
  events exist.
* **Challenges do not exist in RepoLab**, so a challenge success rate has
  neither numerator nor denominator.
* **Estimated cost is null unless ``MODEL_PRICING`` is configured**, so cost per
  repository is unanswerable on an unpriced deployment. Tokens are the
  measurable analogue and are shown instead.
* **Time to First Meaningful Contribution** is a longitudinal outcome measured
  on real teams over months. It is named, defined, and never given a number.

So every metric carries a status and the definition it was computed by, and a
metric that is not ``MEASURED`` carries no value at all -- enforced by a
validator, in the way :class:`~repolab.evidence.models.EvidenceBundle` refuses
to hold a measurement while simulated.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, model_validator
from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session

from repolab.analytics.events import AnalyticsEventType
from repolab.db.models import (
    AgentRunRecord,
    AgentStepRecord,
    AnalyticsEventRecord,
    ExperimentRecord,
    ExperimentVariantRecord,
    Job,
    LessonProgressRecord,
    LessonRecord,
    ModelCallRecord,
    SparkRun,
    ToolCallRecord,
)

#: The north star, named and never computed. CLAUDE.md calls it the long-term
#: enterprise metric; it needs months of real team data, which RepoLab does not
#: have and must not imply it has.
NORTH_STAR_LABEL = "Time to First Meaningful Contribution"
NORTH_STAR_DEFINITION = (
    "How long it takes a developer joining an unfamiliar repository to land a "
    "change that matters. The outcome RepoLab exists to improve."
)
NORTH_STAR_WHY_UNMEASURED = (
    "Requires longitudinal data from real teams using RepoLab on their own "
    "repositories. RepoLab has none, so no value, trend or improvement is shown."
)


class MetricStatus(StrEnum):
    """Whether a metric has a number, and if not, why not."""

    MEASURED = "measured"
    """Computed from recorded data."""

    NO_DATA = "no_data"
    """Instrumented, and nothing has been recorded yet."""

    NOT_INSTRUMENTED = "not_instrumented"
    """Nothing in RepoLab records this."""

    NORTH_STAR = "north_star"
    """A long-term outcome metric, deliberately never computed here."""


class Metric(BaseModel):
    """One number, or an honest account of why there is none."""

    key: str
    label: str
    value: float | None = None
    unit: str = ""
    status: MetricStatus = MetricStatus.MEASURED

    definition: str = ""
    """How this was computed, in one sentence. Shown beside the number."""

    source: str = ""
    """The tables or events behind it."""

    detail: str = ""
    """Anything a reader needs in order not to over-read the number."""

    samples: int | None = None
    """How many observations the value rests on.

    Shown beside it, because a median over one observation and a median over a
    hundred look identical otherwise -- and on a five-day development dataset,
    several of these genuinely are n=1.
    """

    @model_validator(mode="after")
    def _only_measured_metrics_carry_values(self) -> Metric:
        """A metric that was not measured must not display a number.

        The whole point of the status. Without this, "0" and "we do not know"
        become the same thing on the screen -- and "0%" of something unmeasured
        is a claim RepoLab has no business making.
        """
        if self.status is not MetricStatus.MEASURED and self.value is not None:
            raise ValueError(
                f"{self.key} is {self.status.value} and must not carry a value (got {self.value})"
            )
        return self

    @property
    def measured(self) -> bool:
        return self.status is MetricStatus.MEASURED


class MetricGroup(BaseModel):
    """A titled set of metrics."""

    title: str
    subtitle: str = ""
    metrics: list[Metric] = Field(default_factory=list)


class DatasetProvenance(BaseModel):
    """Whose activity these numbers describe.

    Stated on the dashboard because it changes how every figure should be read:
    this is the traffic of building RepoLab, not of people using it.
    """

    repositories: int = 0
    jobs: int = 0
    analytics_events: int = 0
    first_event: datetime | None = None
    last_event: datetime | None = None

    @computed_field
    @property
    def days(self) -> float:
        if self.first_event is None or self.last_event is None:
            return 0.0
        return round((self.last_event - self.first_event).total_seconds() / 86_400, 1)

    @computed_field
    @property
    def summary(self) -> str:
        """The provenance sentence, served so the screen cannot reword it."""
        return self.describe()

    def describe(self) -> str:
        if not self.analytics_events:
            return "No activity has been recorded yet."
        return (
            f"{self.analytics_events} events from {self.jobs} jobs across "
            f"{self.repositories} repositories over {self.days} days. This is "
            f"development traffic from building RepoLab, not usage by learners."
        )


class Dashboard(BaseModel):
    """Everything the KPI screen shows."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: DatasetProvenance = Field(default_factory=DatasetProvenance)

    learning: list[MetricGroup] = Field(default_factory=list)
    """Developer learning activity."""

    operations: list[MetricGroup] = Field(default_factory=list)
    """RepoLab's own operational and agent performance."""

    north_star: Metric = Field(
        default_factory=lambda: Metric(
            key="time_to_first_meaningful_contribution",
            label=NORTH_STAR_LABEL,
            status=MetricStatus.NORTH_STAR,
            definition=NORTH_STAR_DEFINITION,
            detail=NORTH_STAR_WHY_UNMEASURED,
            source="not measured",
        )
    )

    @property
    def all_metrics(self) -> list[Metric]:
        return [m for group in (*self.learning, *self.operations) for m in group.metrics]

    @computed_field
    @property
    def metric_count(self) -> int:
        return len(self.all_metrics)

    @computed_field
    @property
    def measured_count(self) -> int:
        """How many of the metrics have a number behind them.

        Shown in the header. A dashboard where two thirds of the figures are
        measured should say so rather than let a reader assume all of them are.
        """
        return sum(1 for metric in self.all_metrics if metric.measured)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metric(
    key: str,
    label: str,
    value: float | None,
    *,
    unit: str = "",
    definition: str,
    source: str,
    detail: str = "",
    samples: int | None = None,
    status: MetricStatus | None = None,
) -> Metric:
    """A metric, defaulting to ``NO_DATA`` when the value is unknown.

    Centralised so "there is nothing to show" is expressed one way everywhere,
    rather than as a zero in some places and a null in others.
    """
    resolved = status or (MetricStatus.MEASURED if value is not None else MetricStatus.NO_DATA)
    return Metric(
        key=key,
        label=label,
        value=value if resolved is MetricStatus.MEASURED else None,
        unit=unit,
        status=resolved,
        definition=definition,
        source=source,
        detail=detail,
        samples=samples,
    )


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    """A percentage, or ``None`` when there is nothing to divide.

    Returning ``None`` rather than 0.0 is the point: no approved experiments and
    a 0% completion rate are different facts.
    """
    if not denominator:
        return None
    return round(100.0 * (numerator or 0) / denominator, 1)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _percentile(values: list[float], fraction: float) -> float | None:
    """A simple nearest-rank percentile. Honest about being approximate."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _not_instrumented(key: str, label: str, *, definition: str, why: str) -> Metric:
    return Metric(
        key=key,
        label=label,
        status=MetricStatus.NOT_INSTRUMENTED,
        definition=definition,
        detail=why,
        source="nothing records this",
    )


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def build_dashboard(session: Session) -> Dashboard:
    """Compute the whole dashboard from one session.

    Read-only, aggregate queries over indexed columns. Nothing here writes, and
    nothing here is slow enough to need a worker.
    """
    return Dashboard(
        provenance=_provenance(session),
        learning=[
            _repository_activity(session),
            _time_to_value(session),
            _lesson_activity(session),
            _experiment_activity(session),
        ],
        operations=[
            _agent_performance(session),
            _tool_performance(session),
            _model_usage(session),
            _compute_and_cost(session),
        ],
    )


def _provenance(session: Session) -> DatasetProvenance:
    return DatasetProvenance(
        repositories=session.scalar(
            select(func.count(func.distinct(Job.repo_url))).where(Job.repo_url.isnot(None))
        )
        or 0,
        jobs=session.scalar(select(func.count()).select_from(Job)) or 0,
        analytics_events=session.scalar(select(func.count()).select_from(AnalyticsEventRecord))
        or 0,
        first_event=session.scalar(select(func.min(AnalyticsEventRecord.created_at))),
        last_event=session.scalar(select(func.max(AnalyticsEventRecord.created_at))),
    )


def _count_events(session: Session, event_type: AnalyticsEventType) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(AnalyticsEventRecord)
            .where(AnalyticsEventRecord.type == event_type.value)
        )
        or 0
    )


def _repository_activity(session: Session) -> MetricGroup:
    investigations = (
        session.scalar(select(func.count()).select_from(Job).where(Job.kind == "investigate")) or 0
    )
    completed = (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "investigate", Job.status == "completed")
        )
        or 0
    )
    analysed = (
        session.scalar(
            select(func.count(func.distinct(Job.repo_url))).where(
                Job.kind == "investigate", Job.status == "completed"
            )
        )
        or 0
    )

    started = _count_events(session, AnalyticsEventType.BUILD_STARTED)
    failed = _count_events(session, AnalyticsEventType.BUILD_FAILED)
    recovered = _count_events(session, AnalyticsEventType.BUILD_RECOVERED)

    return MetricGroup(
        title="Repositories",
        subtitle="What RepoLab has been pointed at, and whether it could run it",
        metrics=[
            _metric(
                "repositories_analyzed",
                "Repositories analyzed",
                float(analysed) if investigations else None,
                definition="Distinct repositories with at least one completed investigation.",
                source="jobs",
            ),
            _metric(
                "investigations",
                "Investigations run",
                float(investigations) if investigations else None,
                definition="Investigation jobs created, including repeats of the same repository.",
                source="jobs",
            ),
            _metric(
                "investigation_success_rate",
                "Investigation success rate",
                _rate(completed, investigations),
                unit="%",
                samples=investigations,
                definition="Investigation jobs that completed, over all investigation jobs.",
                source="jobs",
                detail="A job still running counts against the rate until it finishes.",
            ),
            _metric(
                "build_candidates_started",
                "Build candidates attempted",
                float(started) if started else None,
                definition="Ladder rungs RepoLab began building, counted per candidate.",
                source="build_started events",
            ),
            _metric(
                "build_success_rate",
                "Build success rate",
                _rate(started - failed, started),
                unit="%",
                samples=started,
                definition=(
                    "Build candidates that recorded no failure, over all candidates started. "
                    "Computed from events because only successful builds are cached: the "
                    "builds table cannot report a failure."
                ),
                source="build_started / build_failed events",
                detail="A worker killed mid-build would be counted as a success.",
            ),
            _metric(
                "build_recovery_rate",
                "Build recovery rate",
                _rate(recovered, failed),
                unit="%",
                samples=failed,
                definition=(
                    "Candidates that failed an attempt and then passed, over candidates "
                    "that recorded a failure."
                ),
                source="build_recovered / build_failed events",
            ),
        ],
    )


def _paired_durations(
    session: Session, start: AnalyticsEventType, end: AnalyticsEventType
) -> list[float]:
    """Seconds between two events **of the same job**.

    Pairing by job is not a detail. Taking the earliest start and the latest end
    across all jobs produced a five-day "time to repo ready" while this module
    was being written, which is the kind of number a dashboard should never be
    able to show.
    """
    starts = session.execute(
        select(AnalyticsEventRecord.job_id, func.min(AnalyticsEventRecord.created_at))
        .where(AnalyticsEventRecord.type == start.value, AnalyticsEventRecord.job_id.isnot(None))
        .group_by(AnalyticsEventRecord.job_id)
    ).all()
    ends = dict(
        session.execute(
            select(AnalyticsEventRecord.job_id, func.max(AnalyticsEventRecord.created_at))
            .where(AnalyticsEventRecord.type == end.value, AnalyticsEventRecord.job_id.isnot(None))
            .group_by(AnalyticsEventRecord.job_id)
        ).all()
    )

    durations: list[float] = []
    for job_id, began in starts:
        finished = ends.get(job_id)
        if finished is None or began is None:
            continue
        seconds = (finished - began).total_seconds()
        if seconds >= 0:
            durations.append(seconds)
    return durations


def _first_per_repo(session: Session, column, table, *, where=None) -> dict[str, datetime]:
    """The earliest *column* per repository in *table*."""
    query = select(table.repo_url, func.min(column)).group_by(table.repo_url)
    if where is not None:
        query = query.where(where)
    return {repo: at for repo, at in session.execute(query).all() if repo and at}


def _ready_times(session: Session) -> dict[str, list[datetime]]:
    """Every moment each repository became ready, in order."""
    rows = session.execute(
        select(AnalyticsEventRecord.repo_url, AnalyticsEventRecord.created_at)
        .where(
            AnalyticsEventRecord.type == AnalyticsEventType.REPO_READY.value,
            AnalyticsEventRecord.repo_url.isnot(None),
        )
        .order_by(AnalyticsEventRecord.created_at)
    ).all()

    ready: dict[str, list[datetime]] = {}
    for repo, at in rows:
        if repo and at:
            ready.setdefault(repo, []).append(at)
    return ready


def _since_last_ready(firsts: dict[str, datetime], ready: dict[str, list[datetime]]) -> list[float]:
    """Seconds from the **most recent** ready at or before each event.

    Not from the repository's first ever ready. Measuring that way gave a
    four-day "time to first experiment" on this dataset -- true as arithmetic
    and worthless as a metric, because the gap was days of building RepoLab
    rather than a person waiting for anything. Pairing with the preceding ready
    measures what the metric is named for.
    """
    gaps: list[float] = []
    for repo, at in firsts.items():
        preceding = [moment for moment in ready.get(repo, []) if moment <= at]
        if not preceding:
            continue
        gaps.append((at - preceding[-1]).total_seconds())
    return gaps


def _time_to_value(session: Session) -> MetricGroup:
    ready_to = _paired_durations(
        session, AnalyticsEventType.REPO_SUBMITTED, AnalyticsEventType.REPO_READY
    )
    ready = _ready_times(session)

    first_run = _since_last_ready(_first_per_repo(session, SparkRun.created_at, SparkRun), ready)
    first_experiment = _since_last_ready(
        _first_per_repo(session, ExperimentRecord.created_at, ExperimentRecord), ready
    )

    paired = (
        "Paired with the preceding repo_ready for the same repository, never with "
        "its first ever one."
    )

    return MetricGroup(
        title="Time to value",
        subtitle="Measured per job and per repository, never across unrelated ones",
        metrics=[
            _metric(
                "time_to_repo_ready_median",
                "Time to Repo Ready (median)",
                _median(ready_to),
                unit="s",
                definition=(
                    "Median seconds from repo_submitted to repo_ready, paired within the same job."
                ),
                source="analytics events",
                samples=len(ready_to),
            ),
            _metric(
                "time_to_repo_ready_p90",
                "Time to Repo Ready (p90)",
                _percentile(ready_to, 0.9),
                unit="s",
                definition="Nearest-rank 90th percentile of the same measurements.",
                source="analytics events",
                samples=len(ready_to),
            ),
            _metric(
                "time_to_first_run",
                "Time to First Successful Run (median)",
                _median(first_run),
                unit="s",
                definition=(
                    "Median seconds from a repository being ready to its first measured "
                    "pipeline run."
                ),
                source="analytics events, spark_runs",
                samples=len(first_run),
                detail=paired,
            ),
            _metric(
                "time_to_first_experiment",
                "Time to First Experiment (median)",
                _median(first_experiment),
                unit="s",
                definition=(
                    "Median seconds from a repository being ready to its first experiment "
                    "being created."
                ),
                source="analytics events, experiments",
                samples=len(first_experiment),
                detail=paired,
            ),
        ],
    )


def _lesson_activity(session: Session) -> MetricGroup:
    started = session.scalar(select(func.count()).select_from(LessonProgressRecord)) or 0
    finished = (
        session.scalar(
            select(func.count())
            .select_from(LessonProgressRecord)
            .where(LessonProgressRecord.finished.is_(True))
        )
        or 0
    )
    steps_completed = session.scalar(select(func.sum(LessonProgressRecord.completed_count))) or 0
    lessons = session.scalar(select(func.count()).select_from(LessonRecord)) or 0
    hints = _count_events(session, AnalyticsEventType.HINT_SHOWN)
    commands = _count_events(session, AnalyticsEventType.COMMAND_EXECUTED)

    return MetricGroup(
        title="Lessons",
        subtitle="Learning activity in the CLI experience",
        metrics=[
            _metric(
                "lessons_available",
                "Lessons generated",
                float(lessons) if lessons else None,
                definition="Lessons stored, one per repository commit.",
                source="lessons",
            ),
            _metric(
                "lessons_started",
                "Lessons started",
                float(started) if started else None,
                definition="Learner sessions with at least one checked step.",
                source="lesson_progress",
            ),
            _metric(
                "lessons_completed",
                "Lessons completed",
                float(finished) if started else None,
                definition="Learner sessions that completed every step.",
                source="lesson_progress",
            ),
            _metric(
                "lesson_completion_rate",
                "Lesson completion rate",
                _rate(finished, started),
                unit="%",
                samples=started,
                definition="Completed lesson sessions over started ones.",
                source="lesson_progress",
            ),
            _metric(
                "lesson_steps_completed",
                "Lesson steps completed",
                float(steps_completed) if steps_completed else None,
                definition="Steps passed across all learner sessions.",
                source="lesson_progress",
            ),
            _metric(
                "hints_shown",
                "Hints shown",
                float(hints) if hints else None,
                definition=(
                    "Times a step was judged failed and its hint returned. Named for "
                    "what happens: a learner does not request a hint, it arrives with "
                    "a failed check."
                ),
                source="hint_shown events",
            ),
            _metric(
                "commands_executed",
                "Terminal commands executed",
                float(commands) if commands else None,
                definition="Statements submitted to a real terminal, refusals included.",
                source="command_executed events",
            ),
            _not_instrumented(
                "challenge_success_rate",
                "Challenge success rate",
                definition="Challenges passed over challenges attempted.",
                why="RepoLab has no challenges. Nothing starts, passes or fails one.",
            ),
        ],
    )


def _experiment_activity(session: Session) -> MetricGroup:
    total = session.scalar(select(func.count()).select_from(ExperimentRecord)) or 0
    approved = (
        session.scalar(
            select(func.count())
            .select_from(ExperimentRecord)
            .where(ExperimentRecord.approved_at.isnot(None))
        )
        or 0
    )
    completed = (
        session.scalar(
            select(func.count())
            .select_from(ExperimentRecord)
            .where(ExperimentRecord.status == "completed")
        )
        or 0
    )
    rejected = (
        session.scalar(
            select(func.count())
            .select_from(ExperimentRecord)
            .where(ExperimentRecord.status == "rejected")
        )
        or 0
    )
    questions = _count_events(session, AnalyticsEventType.QUESTION_ASKED)
    variants = session.scalar(select(func.count()).select_from(ExperimentVariantRecord)) or 0

    return MetricGroup(
        title="Experiments",
        subtitle="Questions turned into measured evidence",
        metrics=[
            _metric(
                "questions_asked",
                "Questions asked",
                float(questions) if questions else None,
                definition="Questions submitted to Ask RepoLab.",
                source="question_asked events",
            ),
            _metric(
                "experiments_proposed",
                "Experiments proposed",
                float(total) if total else None,
                definition="Experiments created, each with a plan presented for approval.",
                source="experiments",
            ),
            _metric(
                "experiments_approved",
                "Experiments approved",
                float(approved) if total else None,
                definition="Experiments a user approved. Nothing runs before this.",
                source="experiments.approved_at",
            ),
            _metric(
                "experiments_rejected",
                "Experiments declined",
                float(rejected) if total else None,
                definition="Experiments a user declined. The approval gate working.",
                source="experiments",
            ),
            _metric(
                "experiments_completed",
                "Experiments completed",
                float(completed) if total else None,
                definition="Experiments that ran every variant and produced statistics.",
                source="experiments",
            ),
            _metric(
                "experiment_completion_rate",
                "Experiment completion rate",
                _rate(completed, approved),
                unit="%",
                samples=approved,
                definition="Completed experiments over approved ones.",
                source="experiments",
                detail="Declined experiments are excluded: they were never meant to run.",
            ),
            _metric(
                "question_to_experiment_rate",
                "Questions → experiments",
                _rate(total, questions),
                unit="%",
                samples=questions,
                definition="Experiments proposed per question asked.",
                source="experiments, question_asked events",
                detail="Can exceed 100%: an experiment can be requested without a question.",
            ),
            _metric(
                "variant_runs",
                "Variant runs measured",
                float(variants) if variants else None,
                definition="Individual variant executions recorded with measurements.",
                source="experiment_variants",
            ),
        ],
    )


def _agent_performance(session: Session) -> MetricGroup:
    runs = session.scalar(select(func.count()).select_from(AgentRunRecord)) or 0
    completed = (
        session.scalar(
            select(func.count())
            .select_from(AgentRunRecord)
            .where(AgentRunRecord.status == "completed")
        )
        or 0
    )
    exhausted = (
        session.scalar(
            select(func.count())
            .select_from(AgentRunRecord)
            .where(AgentRunRecord.status == "exhausted")
        )
        or 0
    )
    mean_steps = session.scalar(select(func.avg(cast(AgentRunRecord.iterations, Float))))
    mean_steps_completed = session.scalar(
        select(func.avg(cast(AgentRunRecord.iterations, Float))).where(
            AgentRunRecord.status == "completed"
        )
    )

    # Recovery: a run that had at least one failed step and still completed.
    failed_steps = (
        select(AgentStepRecord.run_id)
        .where(AgentStepRecord.ok.is_(False))
        .group_by(AgentStepRecord.run_id)
        .subquery()
    )
    with_failures = (
        session.scalar(
            select(func.count())
            .select_from(AgentRunRecord)
            .join(failed_steps, failed_steps.c.run_id == AgentRunRecord.id)
        )
        or 0
    )
    recovered = (
        session.scalar(
            select(func.count())
            .select_from(AgentRunRecord)
            .join(failed_steps, failed_steps.c.run_id == AgentRunRecord.id)
            .where(AgentRunRecord.status == "completed")
        )
        or 0
    )

    return MetricGroup(
        title="Agent performance",
        subtitle="How the investigator actually behaves",
        metrics=[
            _metric(
                "agent_runs",
                "Agent runs",
                float(runs) if runs else None,
                definition="Recorded agent runs, one per investigation.",
                source="agent_runs",
            ),
            _metric(
                "agent_task_success_rate",
                "Task success rate",
                _rate(completed, runs),
                unit="%",
                samples=runs,
                definition="Runs that finished their objective, over all runs.",
                source="agent_runs.status",
                detail=(
                    f"{exhausted} run(s) hit the iteration limit instead of finishing. "
                    "A manifest is still produced, deterministically."
                ),
            ),
            _metric(
                "agent_steps_mean",
                "Average agent steps",
                round(float(mean_steps), 1) if mean_steps is not None else None,
                definition="Mean tool-using iterations per agent run.",
                source="agent_runs.iterations",
            ),
            _metric(
                "agent_steps_mean_completed",
                "Average steps when it finished",
                round(float(mean_steps_completed), 1) if mean_steps_completed is not None else None,
                definition="Mean iterations across runs that completed.",
                source="agent_runs.iterations",
                detail=(
                    "Lower than the overall mean because an exhausted run spends its "
                    "whole budget. Fewer steps is not the goal; correctness is."
                ),
            ),
            _metric(
                "agent_recovery_rate",
                "Agent recovery rate",
                _rate(recovered, with_failures),
                unit="%",
                samples=with_failures,
                definition=(
                    "Runs that had at least one failed tool call and still completed, "
                    "over runs with a failed tool call."
                ),
                source="agent_runs, agent_steps",
                detail="RepoLab's own definition, not an industry standard.",
            ),
        ],
    )


def _tool_performance(session: Session) -> MetricGroup:
    total = session.scalar(select(func.count()).select_from(ToolCallRecord)) or 0
    ok = (
        session.scalar(
            select(func.count()).select_from(ToolCallRecord).where(ToolCallRecord.ok.is_(True))
        )
        or 0
    )
    mean_ms = session.scalar(select(func.avg(cast(ToolCallRecord.duration_ms, Float))))

    worst = session.execute(
        select(
            ToolCallRecord.tool,
            func.count().label("calls"),
            func.sum(case((ToolCallRecord.ok.is_(False), 1), else_=0)).label("failures"),
        )
        .group_by(ToolCallRecord.tool)
        .order_by(func.sum(case((ToolCallRecord.ok.is_(False), 1), else_=0)).desc())
        .limit(3)
    ).all()
    worst_detail = (
        ", ".join(
            f"{tool} {failures}/{calls} failed" for tool, calls, failures in worst if failures
        )
        or "no tool has failed"
    )

    return MetricGroup(
        title="Tool layer",
        subtitle="Every action an agent takes passes through here",
        metrics=[
            _metric(
                "tool_calls",
                "Tool calls",
                float(total) if total else None,
                definition="Tool invocations recorded across all agent runs.",
                source="tool_calls",
            ),
            _metric(
                "tool_success_rate",
                "Tool success rate",
                _rate(ok, total),
                unit="%",
                samples=total,
                definition="Tool calls that succeeded, over all tool calls.",
                source="tool_calls.ok",
                detail=worst_detail,
            ),
            _metric(
                "tool_duration_mean",
                "Average tool duration",
                round(float(mean_ms), 1) if mean_ms is not None else None,
                unit="ms",
                definition="Mean wall-clock duration of a tool call.",
                source="tool_calls.duration_ms",
            ),
        ],
    )


def _model_usage(session: Session) -> MetricGroup:
    calls = session.scalar(select(func.count()).select_from(ModelCallRecord)) or 0
    succeeded = (
        session.scalar(
            select(func.count())
            .select_from(ModelCallRecord)
            .where(ModelCallRecord.success.is_(True))
        )
        or 0
    )
    latencies = [
        float(value)
        for (value,) in session.execute(select(ModelCallRecord.latency_ms)).all()
        if value is not None
    ]
    prompt_tokens = session.scalar(select(func.sum(ModelCallRecord.prompt_tokens))) or 0
    completion_tokens = session.scalar(select(func.sum(ModelCallRecord.completion_tokens))) or 0
    retries = session.scalar(select(func.sum(ModelCallRecord.retries))) or 0

    return MetricGroup(
        title="Model usage",
        subtitle="Every routed call, recorded without prompts or outputs",
        metrics=[
            _metric(
                "model_calls",
                "Model calls",
                float(calls) if calls else None,
                definition="Routed model calls recorded since telemetry was persisted.",
                source="model_calls",
                detail="Earlier calls were logged only, so they are not counted here.",
            ),
            _metric(
                "model_success_rate",
                "Model call success rate",
                _rate(succeeded, calls),
                unit="%",
                samples=calls,
                definition="Calls that returned a schema-valid answer, over all calls.",
                source="model_calls.success",
            ),
            _metric(
                "model_latency_median",
                "Model latency (median)",
                _median(latencies),
                unit="ms",
                definition="Median round-trip latency of a routed call.",
                source="model_calls.latency_ms",
            ),
            _metric(
                "model_latency_p90",
                "Model latency (p90)",
                _percentile(latencies, 0.9),
                unit="ms",
                definition="Nearest-rank 90th percentile latency.",
                source="model_calls.latency_ms",
            ),
            _metric(
                "model_tokens_prompt",
                "Prompt tokens",
                float(prompt_tokens) if prompt_tokens else None,
                definition="Total prompt tokens reported by the provider.",
                source="model_calls.prompt_tokens",
            ),
            _metric(
                "model_tokens_completion",
                "Completion tokens",
                float(completion_tokens) if completion_tokens else None,
                definition="Total completion tokens reported by the provider.",
                source="model_calls.completion_tokens",
            ),
            _metric(
                "model_schema_retries",
                "Schema retries",
                float(retries) if calls else None,
                definition="Calls that needed a second attempt to produce valid output.",
                source="model_calls.retries",
            ),
        ],
    )


def _compute_and_cost(session: Session) -> MetricGroup:
    spark_seconds = session.scalar(select(func.sum(SparkRun.wall_clock_seconds))) or 0.0
    variant_seconds = (
        session.scalar(select(func.sum(ExperimentVariantRecord.wall_clock_seconds))) or 0.0
    )
    container_seconds = float(spark_seconds) + float(variant_seconds)

    priced = (
        session.scalar(
            select(func.count())
            .select_from(ModelCallRecord)
            .where(ModelCallRecord.estimated_cost_usd.isnot(None))
        )
        or 0
    )
    total_cost = session.scalar(select(func.sum(ModelCallRecord.estimated_cost_usd)))
    repos_ready = _count_events(session, AnalyticsEventType.REPO_READY)
    experiments = session.scalar(select(func.count()).select_from(ExperimentRecord)) or 0

    cost_detail = (
        "MODEL_PRICING is not configured, so no call has a price. Tokens above are "
        "the measurable analogue; RepoLab does not guess a rate."
    )

    return MetricGroup(
        title="Compute and cost",
        subtitle="Measured container time, and what the models cost",
        metrics=[
            _metric(
                "container_compute_seconds",
                "Container compute time",
                round(container_seconds, 1) if container_seconds else None,
                unit="s",
                definition=("Summed wall-clock of measured pipeline runs and experiment variants."),
                source="spark_runs, experiment_variants",
                detail=(
                    "Measured container wall-clock, not billed sandbox time: experiments "
                    "run in Docker on this machine."
                ),
            ),
            _metric(
                "model_cost_total",
                "Estimated model cost",
                round(float(total_cost), 4) if priced and total_cost else None,
                unit="USD",
                definition="Summed per-call estimates from the configured price list.",
                source="model_calls.estimated_cost_usd",
                detail="" if priced else cost_detail,
            ),
            _metric(
                "cost_per_ready_repo",
                "Cost per repository made ready",
                round(float(total_cost) / repos_ready, 4)
                if priced and total_cost and repos_ready
                else None,
                unit="USD",
                definition="Estimated model cost divided by repositories made ready.",
                source="model_calls, repo_ready events",
                detail="" if priced else cost_detail,
            ),
            _metric(
                "cost_per_experiment",
                "Cost per experiment",
                round(float(total_cost) / experiments, 4)
                if priced and total_cost and experiments
                else None,
                unit="USD",
                definition="Estimated model cost divided by experiments proposed.",
                source="model_calls, experiments",
                detail="" if priced else cost_detail,
            ),
        ],
    )


__all__ = [
    "NORTH_STAR_DEFINITION",
    "NORTH_STAR_LABEL",
    "Dashboard",
    "DatasetProvenance",
    "Metric",
    "MetricGroup",
    "MetricStatus",
    "build_dashboard",
]
