"""The Experiment Agent, end to end, offline.

The whole loop runs against fakes: a scripted model, a scripted research
provider and a fake variant runner. Nothing touches the network and nothing
starts a container, which is the point -- the flow has to be testable without
credentials or Docker.

The assertions that matter are the boundaries: the agent stops at the approval
gate, it cannot execute past it, the model never sees what it should not, and a
figure the model invents does not pass as a measurement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repolab.events import CollectingEmitter, EventType
from repolab.experiments import ExperimentAgent, ExperimentBudget
from repolab.experiments.executor import ApprovalRequiredError
from repolab.experiments.models import ExperimentStatus
from repolab.llm import ModelRouter, ModelTier
from repolab.testing.fakes import FakeLLMProvider, FakeMessage, FakeResearchProvider
from tests.support.experiments import DATASET_HASH, IMAGE, spark_run_result

QUESTION = "Why does this job use 200 shuffle partitions?"

DESIGN_RESPONSE = {
    "question": QUESTION,
    "hypothesis": "Most of the 200 partitions are empty because the data has seven keys.",
    "variable": "spark.sql.shuffle.partitions",
    "values": [8, 50, 200],
    "metrics": ["runtime", "partitions", "empty partitions", "gc_time_ms"],
    "repeats": 2,
}

EXPLANATION_RESPONSE = {
    "answer": "200 is Spark's default, and it is a poor fit for this job's seven keys.",
    "claims": [
        {
            "kind": "measured",
            "statement": "At 200 partitions, 193 of them received no records.",
            "support": "empty partitions",
        },
        {
            "kind": "inferred",
            "statement": "The default was not chosen for this dataset.",
            "support": "follows from the empty partitions measurement",
        },
    ],
    "caveats": ["Timings vary between runs."],
}


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A minimal repository shaped like the demo one, so the tools find something."""
    root = tmp_path / "repo"
    (root / "jobs").mkdir(parents=True)
    (root / "configs").mkdir()
    (root / "jobs" / "etl_job.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "\n"
        "def transform_data(df, steps_per_floor):\n"
        '    """Transform the frame."""\n'
        "    return df\n"
        "\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "spark.conf.set('spark.sql.shuffle.partitions', 200)\n"
    )
    (root / "configs" / "etl_config.json").write_text(json.dumps({"steps_per_floor": 21}))
    (root / "requirements.txt").write_text("pyspark==2.4.0\n")
    return root


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def run_once(self, *, value: int, repeat: int):
        self.calls.append((value, repeat))
        return spark_run_result(
            value=value,
            repeat=repeat,
            total_runtime_ms=1000 + value * 2,
            partitions=value,
            empty=max(0, value - 7),
        )


def router(*responses: dict) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(
        messages=[FakeMessage(content=json.dumps(payload)) for payload in responses]
    )
    return (
        ModelRouter(
            provider,
            {ModelTier.ULTRA: "fake/ultra", ModelTier.SUPER: "fake/super"},
        ),
        provider,
    )


def agent(
    repository: Path,
    *,
    model_router: ModelRouter | None = None,
    runner: FakeRunner | None = None,
    emitter: CollectingEmitter | None = None,
    research: FakeResearchProvider | None = None,
    concurrency: int = 1,
) -> ExperimentAgent:
    return ExperimentAgent(
        repo_url="https://github.com/test/repo",
        commit_sha="c" * 40,
        repository_root=repository,
        image=IMAGE,
        router=model_router,
        research=research,
        emitter=emitter,
        budget=ExperimentBudget(),
        runner=runner,
        concurrency=concurrency,
    )


class TestProposing:
    async def test_it_stops_at_the_approval_gate(self, repository: Path) -> None:
        """The whole design happens, and then nothing runs."""
        model, _ = router(DESIGN_RESPONSE)
        runner = FakeRunner()
        experiment = await agent(repository, model_router=model, runner=runner).propose(QUESTION)

        assert experiment.status is ExperimentStatus.AWAITING_APPROVAL
        assert experiment.plan is not None
        assert experiment.may_execute is False
        assert runner.calls == [], "a variant ran before approval"

    async def test_the_proposal_is_validated_not_trusted(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE)
        experiment = await agent(repository, model_router=model).propose(QUESTION)

        assert experiment.plan is not None
        assert experiment.proposal is not None
        # The model asked for a metric RepoLab cannot measure.
        assert "gc_time_ms" in experiment.proposal.metrics
        assert experiment.plan.dropped_metrics == ["gc_time_ms"]
        assert "gc_time_ms" not in experiment.plan.metrics

    async def test_the_fixed_settings_come_from_repolab_not_the_model(
        self, repository: Path
    ) -> None:
        model, _ = router(DESIGN_RESPONSE)
        experiment = await agent(repository, model_router=model).propose(QUESTION)

        assert experiment.plan is not None
        assert experiment.plan.fixed_settings["image"] == IMAGE
        assert experiment.plan.fixed_settings["spark.sql.adaptive.enabled"] == "false"

    async def test_approval_required_is_emitted_with_the_cost(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE)
        emitter = CollectingEmitter("job-1")
        await agent(repository, model_router=model, emitter=emitter).propose(QUESTION)

        required = emitter.of_type(EventType.APPROVAL_REQUIRED)
        assert len(required) == 1
        assert required[0].data["estimated_runs"] == 6
        assert required[0].data["estimated_seconds"] > 0
        assert required[0].data["dropped_metrics"] == ["gc_time_ms"]

    async def test_the_investigation_reads_the_repository(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE)
        subject = agent(repository, model_router=model)
        experiment = await subject.propose(QUESTION)

        assert subject.investigation is not None
        tools = {
            step.request.tool for step in subject.investigation.steps if step.request is not None
        }
        assert "search_code" in tools
        assert "inspect_dependencies" in tools
        assert experiment.evidence is not None
        assert experiment.evidence.code_references

    async def test_the_design_prompt_names_only_measurable_metrics(self, repository: Path) -> None:
        """The model cannot choose well from a list it was never given."""
        model, provider = router(DESIGN_RESPONSE)
        await agent(repository, model_router=model).propose(QUESTION)

        prompt = provider.calls[-1]["messages"][-1]["content"]
        assert "empty partitions" in prompt
        assert "spark.sql.shuffle.partitions (allowed range" in prompt
        assert "gc_time_ms" not in prompt

    async def test_official_documentation_is_searched_when_available(
        self, repository: Path
    ) -> None:
        model, _ = router(DESIGN_RESPONSE)
        research = FakeResearchProvider()
        experiment = await agent(repository, model_router=model, research=research).propose(
            QUESTION
        )

        assert research.queries, "no documentation search was made"
        query, domains = research.queries[0]
        assert "spark.sql.shuffle.partitions" in query
        assert "3.5.1" in query, "the search must be version-matched to the image"
        assert domains == ["spark.apache.org"]
        assert experiment.evidence is not None
        assert experiment.evidence.documentation
        assert experiment.evidence.documentation[0].version == "3.5.1"

    async def test_a_refused_proposal_is_reported_and_nothing_runs(self, repository: Path) -> None:
        bad = {**DESIGN_RESPONSE, "variable": "spark.executor.extraJavaOptions"}
        model, _ = router(bad)
        emitter = CollectingEmitter("job-1")
        runner = FakeRunner()

        experiment = await agent(
            repository, model_router=model, runner=runner, emitter=emitter
        ).propose(QUESTION)

        assert experiment.status is ExperimentStatus.FAILED
        assert experiment.rejection is not None
        assert "not allowed" in experiment.rejection.reason
        assert emitter.of_type(EventType.FAILED)
        assert emitter.of_type(EventType.APPROVAL_REQUIRED) == []
        assert runner.calls == []

    async def test_without_a_model_a_deterministic_sweep_is_proposed(
        self, repository: Path
    ) -> None:
        """The demo must work without credentials, and must say that it is doing so."""
        experiment = await agent(repository).propose(QUESTION)

        assert experiment.status is ExperimentStatus.AWAITING_APPROVAL
        assert experiment.plan is not None
        assert 200 in experiment.plan.values
        assert "Deterministic fallback" in experiment.plan.hypothesis

    async def test_a_model_failure_falls_back_rather_than_failing(self, repository: Path) -> None:
        provider = FakeLLMProvider(fail_with=RuntimeError("upstream is down"))
        model = ModelRouter(provider, {ModelTier.ULTRA: "fake/ultra"})
        experiment = await agent(repository, model_router=model).propose(QUESTION)

        assert experiment.status is ExperimentStatus.AWAITING_APPROVAL
        assert experiment.plan is not None

    async def test_the_caller_may_fix_the_experiment_id(self, repository: Path) -> None:
        experiment = await agent(repository).propose(QUESTION, experiment_id="fixed-id")
        assert experiment.id == "fixed-id"


class TestExecuting:
    async def test_executing_without_approval_is_refused(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE)
        runner = FakeRunner()
        subject = agent(repository, model_router=model, runner=runner)
        experiment = await subject.propose(QUESTION)

        from repolab.experiments.experiment import IllegalTransitionError

        with pytest.raises((IllegalTransitionError, ApprovalRequiredError)):
            await subject.execute(experiment)
        assert runner.calls == []

    async def test_the_approved_flow_produces_statistics_and_an_explanation(
        self, repository: Path
    ) -> None:
        model, _ = router(DESIGN_RESPONSE, EXPLANATION_RESPONSE)
        runner = FakeRunner()
        emitter = CollectingEmitter("job-1")
        subject = agent(repository, model_router=model, runner=runner, emitter=emitter)

        experiment = await subject.propose(QUESTION)
        experiment.approve(by="tester")
        experiment = await subject.execute(experiment)

        assert experiment.status is ExperimentStatus.COMPLETED
        assert experiment.run_count == 6
        assert len(runner.calls) == 6

        assert experiment.statistics is not None
        assert experiment.statistics.baseline_value == 200
        assert "empty partitions" in experiment.statistics.metrics

        assert experiment.explanation is not None
        assert experiment.explanation.grounded is True
        assert experiment.explanation.explanation.claims

    async def test_the_evidence_bundle_carries_the_real_thing(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE, EXPLANATION_RESPONSE)
        subject = agent(repository, model_router=model, runner=FakeRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment)

        evidence = experiment.evidence
        assert evidence is not None
        assert evidence.simulated is False
        assert evidence.measurements, "no measurements"
        assert {m.variant_id for m in evidence.measurements} != {None}
        assert evidence.code_references
        assert evidence.logs

    async def test_the_reproducibility_record_is_complete(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE, EXPLANATION_RESPONSE)
        subject = agent(repository, model_router=model, runner=FakeRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment)

        record = experiment.reproducibility
        assert record is not None
        assert record.commit_sha == "c" * 40
        assert record.image == IMAGE
        assert record.dataset_hash
        assert record.base_configuration
        assert record.resources.master == "local[4]"
        assert record.application_version == "3.5.1", "the version actually measured"
        assert record.started_at is not None and record.finished_at is not None
        assert record.reproducible is True
        assert record.added_steps, "RepoLab's additions must be disclosed"

    async def test_variant_events_are_streamed_as_they_finish(self, repository: Path) -> None:
        model, _ = router(DESIGN_RESPONSE, EXPLANATION_RESPONSE)
        emitter = CollectingEmitter("job-1")
        subject = agent(repository, model_router=model, runner=FakeRunner(), emitter=emitter)
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        await subject.execute(experiment)

        assert len(emitter.of_type(EventType.EXPERIMENT_VARIANT_STARTED)) == 3
        assert len(emitter.of_type(EventType.EXPERIMENT_VARIANT_COMPLETED)) == 3
        assert len(emitter.of_type(EventType.PARTIAL_RESULT)) == 6

    async def test_the_explanation_payload_excludes_raw_measurements(
        self, repository: Path
    ) -> None:
        model, provider = router(DESIGN_RESPONSE, EXPLANATION_RESPONSE)
        subject = agent(repository, model_router=model, runner=FakeRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        await subject.execute(experiment)

        prompt = provider.calls[-1]["messages"][-1]["content"]
        assert "spark_event_log" not in prompt, "raw measurements reached the model"
        assert "samples" not in prompt, "raw samples reached the model"
        assert "baseline_median" in prompt, "the computed statistics must be there"

    async def test_a_fabricated_number_is_flagged_not_published_as_fact(
        self, repository: Path
    ) -> None:
        """The failure this phase exists to prevent."""
        fabricated = {
            "answer": "The job spilled heavily.",
            "claims": [
                {
                    "kind": "measured",
                    "statement": "The job spilled 48,000,000 bytes to disk.",
                    "support": "total runtime",
                }
            ],
            "caveats": [],
        }
        model, _ = router(DESIGN_RESPONSE, fabricated)
        emitter = CollectingEmitter("job-1")
        subject = agent(repository, model_router=model, runner=FakeRunner(), emitter=emitter)

        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment)

        assert experiment.explanation is not None
        assert experiment.explanation.grounded is False
        assert experiment.explanation.unsupported
        assert any(
            "could not be traced" in event.message
            for event in emitter.of_type(EventType.AGENT_STATUS)
        )

    async def test_a_failed_explanation_is_recorded_as_such(self, repository: Path) -> None:
        """ "No model configured" and "the model failed" are different situations.

        Reporting the second as the first would misdescribe what happened on a
        screen whose whole purpose is not misdescribing things.
        """
        provider = FakeLLMProvider(messages=[FakeMessage(content=json.dumps(DESIGN_RESPONSE))])
        model = ModelRouter(provider, {ModelTier.ULTRA: "fake/ultra"})
        subject = agent(repository, model_router=model, runner=FakeRunner())

        experiment = await subject.propose(QUESTION)
        experiment.approve()

        # The design succeeded; the explanation call then returns unusable output.
        provider.scripted = [FakeMessage(content="not json at all")]
        provider.calls.clear()
        experiment = await subject.execute(experiment)

        assert experiment.status is ExperimentStatus.COMPLETED
        assert experiment.explanation is None
        assert experiment.explanation_error is not None
        # What the screen shows, so it is checked as a reader would read it:
        # a sentence, not an exception class.
        assert "could not be used" in experiment.explanation_error
        assert "Error" not in experiment.explanation_error, "no exception class names"
        assert "Traceback" not in experiment.explanation_error

    async def test_without_a_model_nothing_is_recorded_as_a_failure(self, repository: Path) -> None:
        subject = agent(repository, runner=FakeRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment)

        assert experiment.explanation is None
        assert experiment.explanation_error is None, "no model configured is not a failure"

    async def test_without_a_model_the_measurements_stand_alone(self, repository: Path) -> None:
        """No model means no narrative, not an invented one."""
        subject = agent(repository, runner=FakeRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment)

        assert experiment.status is ExperimentStatus.COMPLETED
        assert experiment.statistics is not None
        assert experiment.explanation is None

    async def test_runs_appear_on_the_experiment_as_they_finish(self, repository: Path) -> None:
        """So a mid-experiment save persists what has already been measured.

        Observed against the real API: ``variant_runs`` stayed 0 for the whole of
        a fifteen-run experiment, because the runs were only assigned once every
        variant had finished.
        """
        seen: list[int] = []

        async def observe(run) -> None:
            seen.append(len(experiment.runs))

        model, _ = router(DESIGN_RESPONSE, EXPLANATION_RESPONSE)
        subject = agent(repository, model_router=model, runner=FakeRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment, on_run=observe)

        assert experiment.run_count == 6
        assert seen == [1, 2, 3, 4, 5, 6], "runs were not visible as they completed"

    async def test_an_equivalence_violation_fails_the_experiment(self, repository: Path) -> None:
        class DriftingRunner(FakeRunner):
            async def run_once(self, *, value: int, repeat: int):
                self.calls.append((value, repeat))
                return spark_run_result(
                    value=value,
                    repeat=repeat,
                    dataset_hash=DATASET_HASH if value == 200 else "b" * 64,
                )

        subject = agent(repository, runner=DriftingRunner())
        experiment = await subject.propose(QUESTION)
        experiment.approve()

        from repolab.experiments.executor import EquivalenceViolation

        with pytest.raises(EquivalenceViolation):
            await subject.execute(experiment)
        assert experiment.status is ExperimentStatus.FAILED
