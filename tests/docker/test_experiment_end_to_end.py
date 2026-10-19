"""The Spark question, answered by real measurement.

Marked ``docker``: this runs Spark in containers and measures it. Run with
``make test-docker``.

This is the phase's done-when criterion, reduced to something that finishes in a
couple of minutes: ask the question, design, validate, approve, run the variants,
compute the statistics and assemble the evidence -- with every figure derived
from an event log Spark itself wrote.

Two variants, two repeats, a small dataset. The *claim* being tested is not
"fewer partitions are faster" -- that is a timing, and timings vary -- but that
the structural measurements differ in the way the partition count dictates, and
that they are reproducible across repeats.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.events import CollectingEmitter, EventType
from repolab.experiences.spark.driver import SparkRunConfig
from repolab.experiments import ExperimentAgent, ExperimentBudget
from repolab.experiments.models import ExperimentStatus, ProposedPlan
from repolab.experiments.validation import validate_plan
from repolab.ingest.clone import TemporaryClone
from repolab.ingest.limits import DEFAULT_LIMITS
from repolab.ingest.url import validate_repo_url
from repolab.sandbox.docker import DockerEnvironment, docker_available

pytestmark = pytest.mark.docker

DEMO_REPO = "https://github.com/AlexIoannides/pyspark-example-project"
IMAGE = "apache/spark:3.5.1-python3"
QUESTION = "Why does this job use 200 shuffle partitions?"

#: Small enough to keep four Spark runs quick, large enough to produce real skew.
ROWS = 40_000

#: Two values and two repeats: four runs, around a minute.
VALUES = [8, 200]
REPEATS = 2


@pytest.fixture(scope="module")
def clone():
    if not docker_available():
        pytest.skip("docker is not available")
    repo_url = validate_repo_url(DEMO_REPO)
    with TemporaryClone(repo_url, limits=DEFAULT_LIMITS) as cloned:
        yield repo_url, cloned


@pytest.fixture(scope="module")
def experiment(clone, tmp_path_factory):
    """Run the experiment once; every test reads the same result."""
    import asyncio

    repo_url, cloned = clone
    tmp_path = tmp_path_factory.mktemp("experiment")
    emitter = CollectingEmitter("docker-test")

    def environment_factory():
        # A fresh container and a fresh work directory per run, which is the
        # equivalent-state guarantee.
        return DockerEnvironment(host_workdir=tmp_path / f"work-{len(emitter.events)}")

    agent = ExperimentAgent(
        repo_url=repo_url.normalized,
        commit_sha=cloned.commit_sha,
        repository_root=Path(cloned.path),
        image=IMAGE,
        router=None,  # no credentials needed; the deterministic plan is replaced below
        emitter=emitter,
        budget=ExperimentBudget(max_runs=6),
        base_config=SparkRunConfig(),
        rows=ROWS,
        runs_root=tmp_path / "runs",
        environment_factory=environment_factory,
    )

    async def execute():
        # The plan is fixed so the test is deterministic, but it still passes
        # through real validation and a real approval before anything runs.
        proposal = ProposedPlan(
            question=QUESTION,
            hypothesis="Most of the 200 partitions are empty; 8 leaves almost none empty.",
            variable="spark.sql.shuffle.partitions",
            values=VALUES,
            metrics=["runtime", "partitions", "empty partitions", "shuffle read"],
            repeats=REPEATS,
        )
        validated = validate_plan(
            proposal, budget=ExperimentBudget(max_runs=6), image=IMAGE
        ).require()

        subject = await agent.propose(QUESTION)
        subject.plan = validated
        subject.reproducibility = agent.reproducibility_for(validated)
        subject.approve(by="docker-test")
        return await agent.execute(subject)

    return asyncio.run(execute()), emitter


class TestTheFlowCompletes:
    def test_the_experiment_completed(self, experiment) -> None:
        subject, _ = experiment
        assert subject.status is ExperimentStatus.COMPLETED
        assert subject.error is None

    def test_every_variant_ran_every_repeat(self, experiment) -> None:
        subject, _ = experiment
        assert subject.run_count == len(VALUES) * REPEATS
        assert sorted({run.value for run in subject.runs}) == sorted(VALUES)

    def test_variant_events_were_streamed(self, experiment) -> None:
        _, emitter = experiment
        assert len(emitter.of_type(EventType.EXPERIMENT_VARIANT_STARTED)) == len(VALUES)
        assert len(emitter.of_type(EventType.EXPERIMENT_VARIANT_COMPLETED)) == len(VALUES)
        assert len(emitter.of_type(EventType.PARTIAL_RESULT)) >= len(VALUES) * REPEATS


class TestTheMeasurementsAreReal:
    def test_every_run_preserved_its_event_log(self, experiment) -> None:
        """The raw measurement source must outlive the container."""
        subject, _ = experiment
        for run in subject.runs:
            path = Path(run.event_log_path)
            assert path.is_file(), f"{path} was not preserved"
            assert path.stat().st_size > 0
            assert len(run.event_log_sha256) == 64

    def test_the_partition_count_is_what_was_configured(self, experiment) -> None:
        """Measured from the event log, not read back from the configuration."""
        subject, _ = experiment
        for run in subject.runs:
            assert run.measurements.distribution.partitions == run.value, (
                f"configured {run.value} but the event log shows "
                f"{run.measurements.distribution.partitions}"
            )

    def test_two_hundred_partitions_leaves_most_of_them_empty(self, experiment) -> None:
        """The finding the question is really about.

        The dataset has seven distinct keys, so 200 shuffle partitions cannot be
        anything but mostly empty. Structural, so it holds exactly.
        """
        subject, _ = experiment
        baseline = [run for run in subject.runs if run.value == 200]
        assert baseline

        for run in baseline:
            distribution = run.measurements.distribution
            assert distribution.empty_partitions > 150
            assert distribution.empty_fraction > 0.9

    def test_no_partition_count_can_exceed_the_distinct_keys(self, experiment) -> None:
        """At most seven partitions can hold data, whatever the count is set to.

        The dataset has seven keys, so this is derivable rather than observed --
        which makes it the right thing to assert. The measured numbers are less
        tidy than a guess would be: at eight partitions, four come back empty,
        because hashing seven keys into eight buckets collides.
        """
        subject, _ = experiment
        for run in subject.runs:
            distribution = run.measurements.distribution
            non_empty = distribution.partitions - distribution.empty_partitions
            assert 0 < non_empty <= 7, f"{non_empty} non-empty at {run.value} partitions"

    def test_fewer_partitions_waste_a_far_smaller_fraction(self, experiment) -> None:
        """The comparison the question is about, as a fraction rather than a count."""
        subject, _ = experiment
        small = [run for run in subject.runs if run.value == 8]
        baseline = [run for run in subject.runs if run.value == 200]
        assert small and baseline

        worst_small = max(run.measurements.distribution.empty_fraction for run in small)
        best_baseline = min(run.measurements.distribution.empty_fraction for run in baseline)
        assert worst_small < best_baseline / 1.5

    def test_the_skew_is_visible_at_every_partition_count(self, experiment) -> None:
        """The data is 72% one key, so one partition must carry far more."""
        subject, _ = experiment
        for run in subject.runs:
            assert run.measurements.distribution.record_skew_ratio > 2.0

    def test_data_really_crossed_the_shuffle(self, experiment) -> None:
        subject, _ = experiment
        for run in subject.runs:
            assert run.measurements.shuffle_bytes_read > 0
            assert run.measurements.shuffle_records_read > 0


class TestEquivalentState:
    def test_every_run_saw_the_same_dataset(self, experiment) -> None:
        subject, _ = experiment
        assert len({run.dataset_hash for run in subject.runs}) == 1

    def test_every_run_used_the_same_image(self, experiment) -> None:
        subject, _ = experiment
        assert {run.image for run in subject.runs} == {IMAGE}

    def test_no_run_was_contended(self, experiment) -> None:
        subject, _ = experiment
        assert all(run.timing_contended is False for run in subject.runs)


class TestStatistics:
    def test_structural_measurements_repeat_exactly(self, experiment) -> None:
        """Two runs of one configuration must agree on every count.

        If they do not, the runs were not equivalent and the comparison between
        variants means nothing.
        """
        subject, _ = experiment
        assert subject.statistics is not None
        assert subject.statistics.unstable_structural_metrics() == []

    def test_each_metric_is_compared_against_the_baseline(self, experiment) -> None:
        subject, _ = experiment
        statistics = subject.statistics
        assert statistics is not None
        assert statistics.baseline_value == 200

        comparison = statistics.comparison_of("empty partitions")
        assert comparison is not None
        assert comparison.baseline_median > 150
        assert comparison.lowest_value == 8

    def test_every_sample_is_present(self, experiment) -> None:
        subject, _ = experiment
        for variant in subject.statistics.variants:
            assert variant.runs == REPEATS
            for aggregate in variant.aggregates.values():
                assert len(aggregate.samples) == REPEATS

    def test_timings_were_measured_even_though_they_vary(self, experiment) -> None:
        subject, _ = experiment
        comparison = subject.statistics.comparison_of("total runtime")
        assert comparison is not None
        assert comparison.baseline_median > 0


class TestEvidenceAndReproducibility:
    def test_the_evidence_is_measured_not_simulated(self, experiment) -> None:
        subject, _ = experiment
        assert subject.evidence is not None
        assert subject.evidence.simulated is False
        assert subject.evidence.measurements

    def test_every_measurement_is_attributable_to_a_variant(self, experiment) -> None:
        subject, _ = experiment
        variants = {run.variant_id for run in subject.runs}
        assert {m.variant_id for m in subject.evidence.measurements} <= variants

    def test_the_evidence_quotes_the_real_event_logs(self, experiment) -> None:
        subject, _ = experiment
        assert subject.evidence.logs
        for log in subject.evidence.logs:
            assert "SparkListener" in log.excerpt, "the excerpt is not a real event log"

    def test_the_evidence_cites_the_repository(self, experiment) -> None:
        subject, _ = experiment
        assert subject.evidence.code_references

    def test_the_reproducibility_record_names_the_version_measured(self, experiment) -> None:
        subject, _ = experiment
        record = subject.reproducibility
        assert record is not None
        assert record.reproducible is True
        assert record.application_version is not None
        assert record.application_version.startswith("3.5")
        assert record.dataset_hash
        assert record.added_steps, "RepoLab's additions must be disclosed"
