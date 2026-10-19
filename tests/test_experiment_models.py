"""The experiment domain model and its state machine.

The state machine is the approval gate. These tests exist to make it impossible
to reach a running experiment without passing through an approval, and to pin the
reproducibility record to what CLAUDE.md requires be persisted.
"""

from __future__ import annotations

import pytest

from repolab.experiments.experiment import Experiment, IllegalTransitionError
from repolab.experiments.models import (
    MAX_QUESTION_LENGTH,
    ExperimentStatus,
    ProposedPlan,
    ReproducibilityRecord,
    ResourceProfile,
)
from repolab.experiments.validation import PlanRejected
from tests.support.experiments import IMAGE, dataset_spec, plan, variant_run


def experiment(**overrides) -> Experiment:
    fields = {
        "question": "Why does this job use 200 shuffle partitions?",
        "repo_url": "https://github.com/test/repo",
        "commit_sha": "c" * 40,
    }
    fields.update(overrides)
    return Experiment(**fields)


class TestProposedPlan:
    def test_an_overlong_question_is_truncated_rather_than_rejected(self) -> None:
        """A model restating a long question should not fail the whole design."""
        proposal = ProposedPlan(
            question="x" * (MAX_QUESTION_LENGTH + 500),
            hypothesis="h",
            variable="spark.sql.shuffle.partitions",
            values=[50, 200],
            metrics=["runtime"],
            repeats=2,
        )
        assert len(proposal.question) == MAX_QUESTION_LENGTH

    def test_a_proposal_carries_no_fixed_settings_field(self) -> None:
        """What is held fixed is RepoLab's fact to state, not the model's."""
        assert "fixed_settings" not in ProposedPlan.model_fields


class TestExperimentPlan:
    def test_estimated_runs_matches_values_times_repeats(self) -> None:
        validated = plan(values=[50, 100, 200], repeats=3)
        assert validated.estimated_runs == len(validated.values) * validated.repeats == 9

    def test_the_summary_states_the_cost(self) -> None:
        summary = plan(values=[50, 200], repeats=2).summary()
        assert "4 runs" in summary
        assert "spark.sql.shuffle.partitions" in summary


class TestStatusSemantics:
    @pytest.mark.parametrize(
        ("status", "may_execute"),
        [
            (ExperimentStatus.PROPOSED, False),
            (ExperimentStatus.AWAITING_APPROVAL, False),
            (ExperimentStatus.APPROVED, True),
            (ExperimentStatus.RUNNING, True),
            (ExperimentStatus.COMPLETED, False),
            (ExperimentStatus.FAILED, False),
            (ExperimentStatus.REJECTED, False),
        ],
    )
    def test_only_approved_and_running_may_execute(
        self, status: ExperimentStatus, may_execute: bool
    ) -> None:
        assert status.may_execute is may_execute

    def test_terminal_states(self) -> None:
        assert ExperimentStatus.COMPLETED.is_terminal
        assert ExperimentStatus.REJECTED.is_terminal
        assert not ExperimentStatus.AWAITING_APPROVAL.is_terminal


class TestTheStateMachine:
    def test_a_fresh_experiment_may_not_execute(self) -> None:
        assert experiment().may_execute is False

    def test_approval_requires_a_plan(self) -> None:
        with pytest.raises(IllegalTransitionError):
            experiment().await_approval()

    def test_the_happy_path(self) -> None:
        subject = experiment(plan=plan())
        subject.await_approval()
        assert subject.status is ExperimentStatus.AWAITING_APPROVAL
        assert subject.may_execute is False

        subject.approve(by="tester")
        assert subject.status is ExperimentStatus.APPROVED
        assert subject.may_execute is True
        assert subject.approved_by == "tester"
        assert subject.approved_at is not None

        subject.start()
        assert subject.status is ExperimentStatus.RUNNING
        subject.complete()
        assert subject.status is ExperimentStatus.COMPLETED
        assert subject.duration_seconds is not None

    def test_approving_without_being_asked_is_refused(self) -> None:
        """Approval is a reply to a presented plan, not a flag that can be set."""
        subject = experiment(plan=plan())
        with pytest.raises(IllegalTransitionError):
            subject.approve()

    def test_approving_twice_is_refused(self) -> None:
        subject = experiment(plan=plan())
        subject.await_approval()
        subject.approve()
        with pytest.raises(IllegalTransitionError):
            subject.approve()

    def test_a_rejected_experiment_cannot_be_started(self) -> None:
        subject = experiment(plan=plan())
        subject.await_approval()
        subject.reject(by="tester")

        assert subject.status is ExperimentStatus.REJECTED
        assert subject.may_execute is False
        with pytest.raises(IllegalTransitionError):
            subject.start()

    def test_starting_without_approval_is_refused(self) -> None:
        subject = experiment(plan=plan())
        subject.await_approval()
        with pytest.raises(IllegalTransitionError):
            subject.start()

    def test_a_refused_proposal_records_why(self) -> None:
        subject = experiment()
        subject.refuse(PlanRejected(reason="variable is not allowed", detail="nope"))

        assert subject.status is ExperimentStatus.FAILED
        assert subject.rejection is not None
        assert "not allowed" in (subject.error or "")


class TestApprovalPrompt:
    def test_it_shows_what_will_actually_run(self) -> None:
        """Approval must be given to the adjusted plan, not the proposed one."""
        validated = plan(values=[50, 100], metrics=["runtime", "gc_time_ms"])
        prompt = experiment(plan=validated).approval_prompt()

        assert prompt["values"] == validated.values
        assert 200 in prompt["values"], "the baseline was added, and must be shown"
        assert prompt["dropped_metrics"] == ["gc_time_ms"]
        assert prompt["adjustments"]
        assert prompt["estimated_runs"] == validated.estimated_runs

    def test_without_a_plan_it_says_only_what_is_known(self) -> None:
        prompt = experiment().approval_prompt()
        assert prompt["status"] == ExperimentStatus.PROPOSED.value


class TestReproducibility:
    def record(self, **overrides) -> ReproducibilityRecord:
        fields = {
            "repo_url": "https://github.com/test/repo",
            "commit_sha": "c" * 40,
            "image": IMAGE,
            "dataset": dataset_spec(),
            "resources": ResourceProfile(master="local[4]", driver_memory="2g"),
            "repeats": 2,
        }
        fields.update(overrides)
        return ReproducibilityRecord(**fields)

    def test_everything_the_specification_requires_is_present(self) -> None:
        record = self.record(
            base_configuration={"spark.sql.adaptive.enabled": "false"},
            command="spark-submit ...",
            application_version="3.5.1",
        )

        assert record.repo_url and record.commit_sha
        assert record.image and record.dataset_hash
        assert record.command and record.base_configuration
        assert record.resources.master and record.repeats
        assert record.created_at is not None

    def test_it_is_reproducible_when_image_dataset_and_commit_are_known(self) -> None:
        assert self.record().reproducible is True

    def test_an_unpinned_image_is_not_reproducible(self) -> None:
        assert self.record(image="").reproducible is False

    def test_an_unknown_commit_is_not_reproducible(self) -> None:
        assert self.record(commit_sha="").reproducible is False

    def test_contention_is_recorded_on_the_resources(self) -> None:
        record = self.record(
            resources=ResourceProfile(master="local[4]", driver_memory="2g", concurrency=2)
        )
        assert record.resources.contended is True

    def test_it_round_trips_through_json(self) -> None:
        record = self.record(application_version="3.5.1")
        restored = ReproducibilityRecord.model_validate(record.model_dump(mode="json"))

        assert restored.dataset_hash == record.dataset_hash
        assert restored.application_version == "3.5.1"
        assert restored.reproducible is record.reproducible


class TestVariantRun:
    def test_metrics_are_keyed_by_name(self) -> None:
        run = variant_run(value=200, repeat=1, total_runtime_ms=1234)
        metrics = run.metrics()

        assert metrics["total runtime"].value == 1234.0
        assert metrics["partitions"].value == 200.0

    def test_measurements_are_tagged_with_the_variant(self) -> None:
        """Untagged, a figure could not be traced to the run that produced it."""
        run = variant_run(value=200, repeat=1, variant_id="200-abc")
        assert {m.variant_id for m in run.to_measurements()} == {"200-abc"}

    def test_the_raw_measurement_source_is_recorded(self) -> None:
        run = variant_run(value=200, repeat=1)
        assert run.event_log_path
        assert len(run.event_log_sha256) == 64

    def test_an_experiment_round_trips_through_json(self) -> None:
        subject = experiment(plan=plan(), runs=[variant_run(value=200, repeat=1)])
        restored = Experiment.model_validate(subject.model_dump(mode="json"))

        assert restored.run_count == 1
        assert restored.runs[0].measurements.task_count == subject.runs[0].measurements.task_count
