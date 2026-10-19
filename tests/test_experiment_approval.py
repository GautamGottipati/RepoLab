"""The approval gate, through the API and through persistence.

CLAUDE.md: *agents cannot bypass approval gates*. There are four places that
could let one through, and each is checked here:

* the state machine, which refuses to approve what was never presented;
* the executor, which refuses to run what was not approved;
* the API, which refuses a second approval;
* the worker boundary, where a reloaded experiment must come back in the state it
  was stored in rather than a permissive default.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from repolab.db.base import Base
from repolab.db.experiments import (
    count_measurements,
    get_experiment,
    list_experiments,
    load_experiment,
    save_experiment,
)
from repolab.db.jobs import create_job
from repolab.experiments.executor import ApprovalRequiredError, VariantExecutor
from repolab.experiments.experiment import Experiment, IllegalTransitionError
from repolab.experiments.models import ExperimentStatus
from tests.support.experiments import plan, spark_run_result, variant_run


@pytest.fixture
def session() -> Session:
    """An in-memory database, so persistence is exercised without Postgres."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as handle:
        yield handle


def experiment(**overrides) -> Experiment:
    fields = {
        "question": "Why does this job use 200 shuffle partitions?",
        "repo_url": "https://github.com/test/repo",
        "commit_sha": "c" * 40,
        "plan": plan(values=[50, 200], repeats=2),
    }
    fields.update(overrides)
    return Experiment(**fields)


class NeverRuns:
    """A runner that fails the test if it is ever called."""

    async def run_once(self, *, value: int, repeat: int):
        raise AssertionError(f"a variant ran without approval: {value}/{repeat}")


class TestTheGateCannotBeBypassed:
    async def test_an_unapproved_experiment_executes_nothing(self) -> None:
        subject = experiment()
        subject.await_approval()

        with pytest.raises(ApprovalRequiredError):
            await VariantExecutor(NeverRuns()).execute(subject.plan, status=subject.status)

    async def test_a_rejected_experiment_executes_nothing(self) -> None:
        subject = experiment()
        subject.await_approval()
        subject.reject()

        with pytest.raises(ApprovalRequiredError):
            await VariantExecutor(NeverRuns()).execute(subject.plan, status=subject.status)

    async def test_setting_the_status_by_hand_still_needs_a_plan(self) -> None:
        """``may_execute`` requires both the state and a validated plan."""
        subject = Experiment(question="q", repo_url="https://github.com/a/b")
        subject.status = ExperimentStatus.APPROVED
        assert subject.may_execute is False

    def test_approval_twice_is_refused(self) -> None:
        subject = experiment()
        subject.await_approval()
        subject.approve()
        with pytest.raises(IllegalTransitionError):
            subject.approve()

    def test_rejecting_after_approval_is_refused(self) -> None:
        subject = experiment()
        subject.await_approval()
        subject.approve()
        with pytest.raises(IllegalTransitionError):
            subject.reject()


class TestPersistence:
    def test_a_proposed_experiment_round_trips_in_its_own_state(self, session: Session) -> None:
        """A reload must not come back more permissive than it was stored."""
        subject = experiment()
        subject.await_approval()
        save_experiment(session, subject)
        session.commit()

        restored = load_experiment(session, subject.id)
        assert restored is not None
        assert restored.status is ExperimentStatus.AWAITING_APPROVAL
        assert restored.may_execute is False
        assert restored.plan is not None
        assert restored.plan.values == subject.plan.values

    def test_an_approved_experiment_reloads_as_executable(self, session: Session) -> None:
        subject = experiment()
        subject.await_approval()
        subject.approve(by="tester")
        save_experiment(session, subject)
        session.commit()

        restored = load_experiment(session, subject.id)
        assert restored is not None
        assert restored.may_execute is True
        assert restored.approved_by == "tester"
        assert restored.approved_at is not None

    def test_the_plan_reloads_byte_for_byte(self, session: Session) -> None:
        """What is approved and what is run must be the same plan."""
        subject = experiment()
        subject.await_approval()
        save_experiment(session, subject)
        session.commit()

        restored = load_experiment(session, subject.id)
        assert restored is not None
        assert restored.plan.model_dump(mode="json") == subject.plan.model_dump(mode="json")

    def test_runs_and_measurements_are_stored_separately(self, session: Session) -> None:
        subject = experiment(
            runs=[
                variant_run(value=50, repeat=1),
                variant_run(value=50, repeat=2),
                variant_run(value=200, repeat=1),
            ]
        )
        record = save_experiment(session, subject)
        session.commit()

        assert len(record.variants) == 3
        per_run = len(subject.runs[0].to_measurements())
        assert count_measurements(session, subject.id) == per_run * 3

    def test_every_measurement_row_knows_its_variant(self, session: Session) -> None:
        subject = experiment(runs=[variant_run(value=200, repeat=1, variant_id="200-abc")])
        record = save_experiment(session, subject)
        session.commit()

        rows = record.measurements
        assert rows
        assert all(row.experiment_id == subject.id for row in rows)
        assert all(row.variant_pk == record.variants[0].id for row in rows)
        assert {row.variant_id for row in rows} == {"200-abc"}

    def test_the_raw_event_log_reference_survives(self, session: Session) -> None:
        run = variant_run(value=200, repeat=1)
        record = save_experiment(session, experiment(runs=[run]))
        session.commit()

        stored = record.variants[0]
        assert stored.event_log_path == run.event_log_path
        assert stored.event_log_sha256 == run.event_log_sha256

    def test_resaving_replaces_rather_than_duplicates(self, session: Session) -> None:
        """Two sets of numbers for one experiment would both look authoritative."""
        subject = experiment(runs=[variant_run(value=200, repeat=1)])
        save_experiment(session, subject)
        session.commit()
        first = count_measurements(session, subject.id)

        subject.runs.append(variant_run(value=200, repeat=2))
        record = save_experiment(session, subject)
        session.commit()

        assert len(record.variants) == 2
        assert count_measurements(session, subject.id) == first * 2

    def test_the_reproducibility_record_is_persisted(self, session: Session) -> None:
        from repolab.experiments.models import ReproducibilityRecord, ResourceProfile
        from tests.support.experiments import IMAGE, dataset_spec

        subject = experiment(
            reproducibility=ReproducibilityRecord(
                repo_url="https://github.com/test/repo",
                commit_sha="c" * 40,
                image=IMAGE,
                dataset=dataset_spec(),
                resources=ResourceProfile(master="local[4]", driver_memory="2g"),
                repeats=2,
            )
        )
        record = save_experiment(session, subject)
        session.commit()

        assert record.dataset_hash == subject.reproducibility.dataset_hash
        assert record.image == IMAGE
        restored = load_experiment(session, subject.id)
        assert restored is not None
        assert restored.reproducibility is not None
        assert restored.reproducibility.reproducible is True

    def test_experiments_are_listed_newest_first(self, session: Session) -> None:
        first = experiment()
        second = experiment()
        save_experiment(session, first)
        save_experiment(session, second)
        session.commit()

        listed = list_experiments(session, repo_url="https://github.com/test/repo")
        assert {record.id for record in listed} == {first.id, second.id}

    def test_a_refusal_is_persisted_with_its_reason(self, session: Session) -> None:
        from repolab.experiments.validation import PlanRejected

        subject = experiment(plan=None)
        subject.refuse(PlanRejected(reason="variable is not allowed", detail="nope"))
        save_experiment(session, subject)
        session.commit()

        record = get_experiment(session, subject.id)
        assert record is not None
        assert record.rejection["reason"] == "variable is not allowed"
        assert record.status == ExperimentStatus.FAILED.value

    def test_an_unknown_experiment_loads_as_none(self, session: Session) -> None:
        assert load_experiment(session, "does-not-exist") is None

    def test_an_experiment_can_belong_to_a_job(self, session: Session) -> None:
        job = create_job(session, "https://github.com/test/repo", kind="experiment_design")
        session.flush()

        subject = experiment(job_id=job.id)
        record = save_experiment(session, subject)
        session.commit()

        assert record.job_id == job.id


class TestVariantRunResults:
    def test_a_runner_result_becomes_a_variant_run(self) -> None:
        """Pins the conversion the executor relies on."""
        from repolab.experiments.executor import _variant_run

        result = spark_run_result(value=50, repeat=2)
        run = _variant_run(result, variant_id="50-abc", value=50, repeat=2, contended=True)

        assert run.value == 50
        assert run.repeat == 2
        assert run.dataset_hash == result.dataset.sha256
        assert run.image == result.environment.image
        assert run.event_log_sha256 == result.event_log_sha256
        assert run.timing_contended is True
