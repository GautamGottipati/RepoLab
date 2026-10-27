"""The KPI dashboard's arithmetic, and its refusals.

Two things are being tested, and the second matters more. The first is that the
numbers are right. The second is that a number RepoLab cannot honestly produce
is **absent** rather than zero — because on a dashboard "0%" and "we do not
record this" look identical, and only one of them is true.

The dataset is seeded, not mocked: the computation runs against real rows in a
real (in-memory) database, the way it will against Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.analytics.events import AnalyticsEventType
from repolab.analytics.metrics import (
    NORTH_STAR_LABEL,
    Metric,
    MetricStatus,
    build_dashboard,
)
from repolab.db import Base
from repolab.db.models import (
    AgentRunRecord,
    AgentStepRecord,
    AnalyticsEventRecord,
    Job,
    LessonProgressRecord,
    LessonRecord,
    ModelCallRecord,
    SparkRun,
    ToolCallRecord,
)

REPO = "https://github.com/AlexIoannides/pyspark-example-project"
OTHER = "https://github.com/apache/cassandra"
START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def event(
    session: Session,
    event_type: AnalyticsEventType,
    *,
    job_id: str | None = None,
    repo_url: str | None = REPO,
    at: datetime = START,
) -> None:
    session.add(
        AnalyticsEventRecord(type=event_type.value, job_id=job_id, repo_url=repo_url, created_at=at)
    )


def investigation(session: Session, job_id: str, *, status: str = "completed") -> Job:
    job = Job(id=job_id, kind="investigate", status=status, repo_url=REPO)
    session.add(job)
    return job


@pytest.fixture
def seeded(db: sessionmaker[Session]) -> sessionmaker[Session]:
    """A small, deliberate history: two investigations, one of each outcome."""
    with db() as session:
        investigation(session, "job-1")
        investigation(session, "job-2", status="failed")

        # job-1: submitted at 12:00:00, ready 60s later.
        event(session, AnalyticsEventType.REPO_SUBMITTED, job_id="job-1", at=START)
        event(
            session,
            AnalyticsEventType.REPO_READY,
            job_id="job-1",
            at=START + timedelta(seconds=60),
        )
        # job-2: submitted, never ready.
        event(session, AnalyticsEventType.REPO_SUBMITTED, job_id="job-2", at=START)
        session.commit()
    return db


class TestMetricHonesty:
    def test_a_metric_that_was_not_measured_cannot_carry_a_value(self) -> None:
        """The rule the whole dashboard rests on, enforced rather than trusted."""
        with pytest.raises(ValidationError, match="must not carry a value"):
            Metric(
                key="x",
                label="X",
                value=0.0,
                status=MetricStatus.NOT_INSTRUMENTED,
            )

    def test_the_north_star_is_never_given_a_number(self, seeded: sessionmaker[Session]) -> None:
        with seeded() as session:
            dashboard = build_dashboard(session)

        assert dashboard.north_star.label == NORTH_STAR_LABEL
        assert dashboard.north_star.status is MetricStatus.NORTH_STAR
        assert dashboard.north_star.value is None
        assert "longitudinal" in dashboard.north_star.detail

    def test_challenges_are_declared_uninstrumented(self, seeded: sessionmaker[Session]) -> None:
        """RepoLab has no challenges, so a success rate would be an invention."""
        with seeded() as session:
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "challenge_success_rate")
        assert metric.status is MetricStatus.NOT_INSTRUMENTED
        assert metric.value is None
        assert "no challenges" in metric.detail

    def test_an_empty_database_reports_no_data_rather_than_zeros(
        self, db: sessionmaker[Session]
    ) -> None:
        with db() as session:
            dashboard = build_dashboard(session)

        assert dashboard.measured_count == 0
        assert all(metric.value is None for metric in dashboard.all_metrics)
        assert dashboard.provenance.describe() == "No activity has been recorded yet."

    def test_every_metric_states_its_definition_and_source(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            dashboard = build_dashboard(session)

        for metric in dashboard.all_metrics:
            assert metric.definition, f"{metric.key} has no definition"
            assert metric.source, f"{metric.key} names no source"


class TestTimeToValue:
    def test_time_to_ready_is_paired_within_one_job(self, seeded: sessionmaker[Session]) -> None:
        """The mistake this guards against produced a five-day time to ready.

        Taking the earliest submission and the latest ready across all jobs gives
        an arithmetically correct number that measures nothing.
        """
        with seeded() as session:
            # A second job days later, ready quickly. Pairing by job keeps the
            # two apart; not pairing would subtract across them.
            later = START + timedelta(days=5)
            investigation(session, "job-3")
            event(session, AnalyticsEventType.REPO_SUBMITTED, job_id="job-3", at=later)
            event(
                session,
                AnalyticsEventType.REPO_READY,
                job_id="job-3",
                at=later + timedelta(seconds=40),
            )
            session.commit()
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "time_to_repo_ready_median")
        assert metric.value == 50.0, "the median of 60s and 40s, not five days"
        assert metric.samples == 2

    def test_a_job_that_never_became_ready_is_not_counted(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "time_to_repo_ready_median")
        assert metric.samples == 1, "job-2 was submitted and never became ready"
        assert metric.value == 60.0

    def test_first_run_is_measured_from_the_preceding_ready(
        self, seeded: sessionmaker[Session]
    ) -> None:
        """Not from the repository's first ever ready, which spans days here."""
        with seeded() as session:
            # Made ready again five days later, then run 30s after that.
            again = START + timedelta(days=5)
            event(session, AnalyticsEventType.REPO_READY, job_id="job-9", at=again)
            session.add(
                SparkRun(
                    run_id="run-1",
                    repo_url=REPO,
                    commit_sha="a" * 40,
                    dataset_hash="h",
                    event_log_path="/tmp/x",
                    event_log_sha256="s",
                    event_log_bytes=1,
                    task_count=1,
                    stage_count=1,
                    wall_clock_seconds=12.0,
                    created_at=again + timedelta(seconds=30),
                )
            )
            session.commit()
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "time_to_first_run")
        assert metric.value == 30.0, "five days would be the first-ever-ready answer"
        assert metric.samples == 1


class TestRates:
    def test_a_rate_with_no_denominator_is_no_data_not_zero(
        self, seeded: sessionmaker[Session]
    ) -> None:
        """No approved experiments and a 0% completion rate are different facts."""
        with seeded() as session:
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "experiment_completion_rate")
        assert metric.status is MetricStatus.NO_DATA
        assert metric.value is None

    def test_investigation_success_rate_counts_every_job(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "investigation_success_rate")
        assert metric.value == 50.0, "one completed, one failed"
        assert metric.samples == 2

    def test_build_success_rate_comes_from_events_not_the_builds_table(
        self, seeded: sessionmaker[Session]
    ) -> None:
        """The builds table holds successes only, so it can never report a failure."""
        with seeded() as session:
            for _ in range(4):
                event(session, AnalyticsEventType.BUILD_STARTED, job_id="job-1")
            event(session, AnalyticsEventType.BUILD_FAILED, job_id="job-1")
            event(session, AnalyticsEventType.BUILD_RECOVERED, job_id="job-1")
            session.commit()
            dashboard = build_dashboard(session)

        rate = next(m for m in dashboard.all_metrics if m.key == "build_success_rate")
        recovery = next(m for m in dashboard.all_metrics if m.key == "build_recovery_rate")
        assert rate.value == 75.0, "three of four candidates recorded no failure"
        assert recovery.value == 100.0, "the one failure was followed by a pass"


class TestLearningActivity:
    def test_lessons_started_and_completed(self, seeded: sessionmaker[Session]) -> None:
        with seeded() as session:
            session.add(
                LessonRecord(
                    id="lesson-1",
                    repo_url=OTHER,
                    commit_sha="c" * 40,
                    title="Querying Cassandra",
                    step_count=8,
                    lesson={},
                )
            )
            session.add_all(
                [
                    LessonProgressRecord(
                        lesson_id="lesson-1", session_id="s1", completed_count=8, finished=True
                    ),
                    LessonProgressRecord(
                        lesson_id="lesson-1", session_id="s2", completed_count=3, finished=False
                    ),
                ]
            )
            session.commit()
            dashboard = build_dashboard(session)

        by_key = {m.key: m for m in dashboard.all_metrics}
        assert by_key["lessons_started"].value == 2
        assert by_key["lessons_completed"].value == 1
        assert by_key["lesson_completion_rate"].value == 50.0
        assert by_key["lesson_steps_completed"].value == 11

    def test_hints_are_named_for_what_happens(self, seeded: sessionmaker[Session]) -> None:
        with seeded() as session:
            event(session, AnalyticsEventType.HINT_SHOWN, job_id="job-1")
            session.commit()
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "hints_shown")
        assert metric.value == 1
        assert "does not request a hint" in metric.definition


class TestAgentPerformance:
    def test_recovery_rate_counts_runs_that_survived_a_failed_tool_call(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            recovered = AgentRunRecord(
                id="run-recovered",
                job_id="job-1",
                agent="repository_investigator",
                status="completed",
                iterations=10,
            )
            gave_up = AgentRunRecord(
                id="run-exhausted",
                job_id="job-1",
                agent="repository_investigator",
                status="exhausted",
                iterations=24,
            )
            clean = AgentRunRecord(
                id="run-clean",
                job_id="job-1",
                agent="repository_investigator",
                status="completed",
                iterations=4,
            )
            session.add_all([recovered, gave_up, clean])
            session.flush()
            session.add_all(
                [
                    AgentStepRecord(run_id=recovered.id, index=1, tool="read_file", ok=False),
                    AgentStepRecord(run_id=gave_up.id, index=1, tool="read_file", ok=False),
                    AgentStepRecord(run_id=clean.id, index=1, tool="list_files", ok=True),
                ]
            )
            session.commit()
            dashboard = build_dashboard(session)

        by_key = {m.key: m for m in dashboard.all_metrics}
        assert by_key["agent_recovery_rate"].value == 50.0, "one of two runs with a failure"
        assert by_key["agent_recovery_rate"].samples == 2
        assert by_key["agent_task_success_rate"].value == pytest.approx(66.7, abs=0.1)
        assert by_key["agent_steps_mean_completed"].value == 7.0

    def test_the_recovery_definition_is_stated_as_repolabs_own(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "agent_recovery_rate")
        assert "not an industry standard" in metric.detail

    def test_tool_success_rate_names_the_worst_offenders(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            run = AgentRunRecord(
                id="run-tools", job_id="job-1", agent="a", status="completed", iterations=1
            )
            session.add(run)
            session.flush()
            step = AgentStepRecord(run_id=run.id, index=1, tool="read_file", ok=False)
            session.add(step)
            session.flush()
            session.add_all(
                [
                    ToolCallRecord(step_id=step.id, tool="read_file", ok=False, duration_ms=5),
                    ToolCallRecord(step_id=step.id, tool="read_file", ok=True, duration_ms=5),
                    ToolCallRecord(step_id=step.id, tool="list_files", ok=True, duration_ms=5),
                ]
            )
            session.commit()
            dashboard = build_dashboard(session)

        metric = next(m for m in dashboard.all_metrics if m.key == "tool_success_rate")
        assert metric.value == pytest.approx(66.7, abs=0.1)
        assert "read_file 1/2 failed" in metric.detail


class TestModelUsage:
    def test_latency_and_tokens_come_from_persisted_calls(
        self, seeded: sessionmaker[Session]
    ) -> None:
        with seeded() as session:
            session.add_all(
                [
                    ModelCallRecord(
                        tier="super",
                        model="nvidia/nemotron",
                        purpose="repo_investigation",
                        latency_ms=100.0,
                        prompt_tokens=500,
                        completion_tokens=200,
                        success=True,
                        retries=0,
                    ),
                    ModelCallRecord(
                        tier="nano",
                        model="nvidia/nemotron-nano",
                        purpose="file_summary",
                        latency_ms=300.0,
                        prompt_tokens=100,
                        completion_tokens=50,
                        success=False,
                        retries=1,
                    ),
                ]
            )
            session.commit()
            dashboard = build_dashboard(session)

        by_key = {m.key: m for m in dashboard.all_metrics}
        assert by_key["model_calls"].value == 2
        assert by_key["model_latency_median"].value == 200.0
        assert by_key["model_tokens_prompt"].value == 600
        assert by_key["model_success_rate"].value == 50.0
        assert by_key["model_schema_retries"].value == 1

    def test_cost_is_withheld_when_no_call_was_priced(self, seeded: sessionmaker[Session]) -> None:
        """MODEL_PRICING unset means no price exists. RepoLab does not guess one."""
        with seeded() as session:
            session.add(
                ModelCallRecord(
                    tier="super",
                    model="m",
                    purpose="p",
                    latency_ms=10.0,
                    success=True,
                    estimated_cost_usd=None,
                )
            )
            session.commit()
            dashboard = build_dashboard(session)

        for key in ("model_cost_total", "cost_per_ready_repo", "cost_per_experiment"):
            metric = next(m for m in dashboard.all_metrics if m.key == key)
            assert metric.status is MetricStatus.NO_DATA
            assert metric.value is None
            assert "MODEL_PRICING" in metric.detail


class TestProvenance:
    def test_the_dataset_is_described_as_development_traffic(
        self, seeded: sessionmaker[Session]
    ) -> None:
        """These numbers come from building RepoLab, and must not read as adoption."""
        with seeded() as session:
            dashboard = build_dashboard(session)

        described = dashboard.provenance.describe()
        assert "development traffic" in described
        assert "not usage by learners" in described

    def test_both_sections_are_populated(self, seeded: sessionmaker[Session]) -> None:
        """The phase's done-when: learning activity *and* operational performance."""
        with seeded() as session:
            dashboard = build_dashboard(session)

        assert [group.title for group in dashboard.learning] == [
            "Repositories",
            "Time to value",
            "Lessons",
            "Experiments",
        ]
        assert [group.title for group in dashboard.operations] == [
            "Progressive analysis",
            "Agent performance",
            "Tool layer",
            "Model usage",
            "Compute and cost",
        ]
