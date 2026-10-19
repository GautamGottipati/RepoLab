"""Executing experiment variants.

Three things are being protected here, and each is a way a measured comparison
can quietly become meaningless:

* a run happening before the user approved it;
* variants starting from different state, so the comparison measures the inputs
  rather than the variable;
* contended timings presented as clean ones.

Everything runs against a fake runner, so this file needs no Docker.
"""

from __future__ import annotations

import asyncio

import pytest

from repolab.events import CollectingEmitter, EventType
from repolab.experiences.spark.driver import SparkRunConfig
from repolab.experiments.executor import (
    ApprovalRequiredError,
    EquivalenceViolation,
    SparkVariantRunner,
    VariantExecutor,
    verify_equivalent_state,
)
from repolab.experiments.models import ExperimentStatus
from tests.support.experiments import DATASET_HASH, IMAGE, plan, spark_run_result, variant_run


class FakeRunner:
    """Returns a scripted result per variant, recording what it was asked for."""

    def __init__(self, *, dataset_hashes: dict[int, str] | None = None, images=None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._dataset_hashes = dataset_hashes or {}
        self._images = images or {}
        self.delay = 0.0

    async def run_once(self, *, value: int, repeat: int):
        self.calls.append((value, repeat))
        if self.delay:
            await asyncio.sleep(self.delay)
        return spark_run_result(
            value=value,
            repeat=repeat,
            dataset_hash=self._dataset_hashes.get(value, DATASET_HASH),
            image=self._images.get(value, IMAGE),
            total_runtime_ms=1000 + value,
            partitions=value,
        )


class TestTheApprovalGate:
    @pytest.mark.parametrize(
        "status",
        [
            ExperimentStatus.PROPOSED,
            ExperimentStatus.AWAITING_APPROVAL,
            ExperimentStatus.REJECTED,
            ExperimentStatus.COMPLETED,
            ExperimentStatus.FAILED,
        ],
    )
    async def test_nothing_runs_without_approval(self, status: ExperimentStatus) -> None:
        """The gate is in the executor, so holding a plan is not permission."""
        runner = FakeRunner()
        executor = VariantExecutor(runner)

        with pytest.raises(ApprovalRequiredError):
            await executor.execute(plan(), status=status)

        assert runner.calls == [], "a variant was executed before approval"

    async def test_an_approved_experiment_runs(self) -> None:
        runner = FakeRunner()
        runs = await VariantExecutor(runner).execute(
            plan(values=[50, 200], repeats=2), status=ExperimentStatus.APPROVED
        )

        assert len(runs) == 4
        assert runner.calls == [(50, 1), (50, 2), (200, 1), (200, 2)]

    async def test_the_error_names_the_state_it_refused(self) -> None:
        with pytest.raises(ApprovalRequiredError) as caught:
            await VariantExecutor(FakeRunner()).execute(
                plan(), status=ExperimentStatus.AWAITING_APPROVAL
            )
        assert caught.value.status is ExperimentStatus.AWAITING_APPROVAL


class TestEquivalentState:
    async def test_runs_share_one_dataset_and_image(self) -> None:
        runs = await VariantExecutor(FakeRunner()).execute(
            plan(values=[50, 200]), status=ExperimentStatus.APPROVED
        )

        assert len({run.dataset_hash for run in runs}) == 1
        assert len({run.image for run in runs}) == 1

    async def test_a_differing_dataset_refuses_the_comparison(self) -> None:
        """Comparing runs that saw different data measures the data, not the variable."""
        runner = FakeRunner(dataset_hashes={50: "b" * 64})
        with pytest.raises(EquivalenceViolation, match="different datasets"):
            await VariantExecutor(runner).execute(
                plan(values=[50, 200]), status=ExperimentStatus.APPROVED
            )

    async def test_a_differing_image_refuses_the_comparison(self) -> None:
        runner = FakeRunner(images={50: "apache/spark:3.4.0-python3"})
        with pytest.raises(EquivalenceViolation, match="different images"):
            await VariantExecutor(runner).execute(
                plan(values=[50, 200]), status=ExperimentStatus.APPROVED
            )

    def test_the_check_is_callable_on_its_own(self) -> None:
        verify_equivalent_state([])
        verify_equivalent_state([variant_run(value=200, repeat=1)])

        with pytest.raises(EquivalenceViolation):
            verify_equivalent_state(
                [
                    variant_run(value=200, repeat=1),
                    variant_run(value=50, repeat=1, dataset_hash="b" * 64),
                ]
            )


class TestEvents:
    async def events(self, **plan_kwargs) -> CollectingEmitter:
        emitter = CollectingEmitter("job-1")
        await VariantExecutor(FakeRunner(), emitter=emitter).execute(
            plan(**plan_kwargs), status=ExperimentStatus.APPROVED
        )
        return emitter

    async def test_each_variant_is_announced_and_closed(self) -> None:
        emitter = await self.events(values=[50, 200], repeats=2)

        started = emitter.of_type(EventType.EXPERIMENT_VARIANT_STARTED)
        completed = emitter.of_type(EventType.EXPERIMENT_VARIANT_COMPLETED)
        assert [event.data["value"] for event in started] == [50, 200]
        assert [event.data["value"] for event in completed] == [50, 200]
        assert all(event.data["runs"] == 2 for event in completed)

    async def test_a_partial_result_is_streamed_per_repeat(self) -> None:
        emitter = await self.events(values=[50, 200], repeats=3)
        partials = emitter.of_type(EventType.PARTIAL_RESULT)

        assert len(partials) == 6
        assert [event.data["repeat"] for event in partials] == [1, 2, 3, 1, 2, 3]

    async def test_variants_start_before_they_complete(self) -> None:
        """Ordering is what makes the stream readable while it is still running."""
        emitter = await self.events(values=[50, 200])
        types = [
            event.type
            for event in emitter.events
            if event.type
            in {
                EventType.EXPERIMENT_VARIANT_STARTED,
                EventType.EXPERIMENT_VARIANT_COMPLETED,
            }
        ]
        assert types == [
            EventType.EXPERIMENT_VARIANT_STARTED,
            EventType.EXPERIMENT_VARIANT_COMPLETED,
            EventType.EXPERIMENT_VARIANT_STARTED,
            EventType.EXPERIMENT_VARIANT_COMPLETED,
        ]

    async def test_the_event_log_hash_is_carried_so_a_figure_can_be_traced(self) -> None:
        emitter = await self.events(values=[50, 200])
        for event in emitter.of_type(EventType.EXPERIMENT_VARIANT_COMPLETED):
            assert event.data["event_logs"]


class TestConcurrency:
    async def test_sequential_runs_are_not_marked_contended(self) -> None:
        runs = await VariantExecutor(FakeRunner()).execute(
            plan(values=[50, 200]), status=ExperimentStatus.APPROVED
        )
        assert all(run.timing_contended is False for run in runs)

    async def test_concurrent_runs_are_all_marked_contended(self) -> None:
        """Two Spark variants saturate the machine, so the durations measure contention.

        Marking them is what stops an explanation presenting those timings as
        clean ones.
        """
        runs = await VariantExecutor(FakeRunner()).execute(
            plan(values=[50, 100, 200], concurrency=2), status=ExperimentStatus.APPROVED
        )

        assert runs
        assert all(run.timing_contended is True for run in runs)

    async def test_concurrency_is_announced_before_anything_runs(self) -> None:
        emitter = CollectingEmitter("job-1")
        await VariantExecutor(FakeRunner(), emitter=emitter).execute(
            plan(values=[50, 200], concurrency=2), status=ExperimentStatus.APPROVED
        )

        statuses = emitter.of_type(EventType.AGENT_STATUS)
        assert any("contended" in event.message for event in statuses)
        assert statuses[0].seq < emitter.of_type(EventType.EXPERIMENT_VARIANT_STARTED)[0].seq

    async def test_every_variant_still_runs_every_repeat(self) -> None:
        runner = FakeRunner()
        runner.delay = 0.001
        await VariantExecutor(runner).execute(
            plan(values=[50, 100, 200], repeats=2, concurrency=3),
            status=ExperimentStatus.APPROVED,
        )
        assert sorted(runner.calls) == [(50, 1), (50, 2), (100, 1), (100, 2), (200, 1), (200, 2)]


class TestPerRunCallback:
    async def test_each_run_is_handed_over_as_it_finishes(self) -> None:
        """Used to persist measurements mid-experiment, so a crash loses nothing."""
        seen: list[int] = []

        async def record(run) -> None:
            seen.append(run.value)

        await VariantExecutor(FakeRunner(), on_run=record).execute(
            plan(values=[50, 200], repeats=2), status=ExperimentStatus.APPROVED
        )
        assert seen == [50, 50, 200, 200]


class TestSparkVariantRunner:
    def runner(self, variable: str = "spark.sql.shuffle.partitions") -> SparkVariantRunner:
        from pathlib import Path

        return SparkVariantRunner(
            repo_url="https://github.com/test/repo",
            commit_sha="c" * 40,
            repository_root=Path("/tmp/repolab-test"),
            image=IMAGE,
            variable=variable,
            base_config=SparkRunConfig(shuffle_partitions=200, driver_memory="2g"),
        )

    def test_only_the_varied_key_changes(self) -> None:
        """Everything else must be identical, or the comparison is confounded."""
        base = SparkRunConfig(shuffle_partitions=200, driver_memory="2g")
        config = self.runner().config_for(50)

        assert config.shuffle_partitions == 50
        assert config.master == base.master
        assert config.driver_memory == base.driver_memory
        assert config.steps_per_floor == base.steps_per_floor
        assert config.extra_conf == base.extra_conf

    def test_an_unsupported_variable_is_refused_rather_than_guessed(self) -> None:
        with pytest.raises(EquivalenceViolation, match="cannot apply variable"):
            self.runner(variable="spark.executor.memory").config_for(50)
