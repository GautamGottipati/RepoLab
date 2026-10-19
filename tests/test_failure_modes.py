"""The ten ways a demo goes wrong, and what the user is told.

One class per failure mode the hardening phase names. Each asserts the
*user-facing* outcome rather than the internals: the sentence a person reads,
that nothing claims to have succeeded, and that no Python traceback or exception
class name reaches them.

Everything here runs offline against fakes, so the suite that proves the demo
degrades well does not itself need Docker, credentials or a network.

Two of the ten modes live elsewhere because that is where their machinery is:
**SSE reconnect** and **browser refresh** are asserted on sequence continuity in
`tests/test_api_jobs.py` (`Last-Event-ID` resumes with no gap and no duplicate)
and in the browser in `web/e2e/p0.spec.ts`, which reloads mid-investigation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repolab.builds import (
    BuildCandidate,
    BuildLadder,
    BuildLimits,
    BuildRequest,
    BuildUnavailableError,
    InMemoryBuildCache,
)
from repolab.builds.cassandra import SMOKE_MARKER
from repolab.db.jobs import create_job, fail_abandoned_jobs
from repolab.db.models import Job, JobEventRecord
from repolab.events import CollectingEmitter, EventType, JobStatus
from repolab.experiences.spark.runner import SparkRunFailed
from repolab.experiments import ExperimentAgent, ExperimentBudget
from repolab.experiments.executor import NotEnoughRuns, VariantExecutor
from repolab.experiments.models import ExperimentStatus
from repolab.failures import FailureKind, describe, explain
from repolab.llm import ModelRouter, ModelTier
from repolab.llm.errors import LLMTimeoutError, SchemaValidationError
from repolab.manifest.models import BuildKind
from repolab.sandbox import FakeSandboxProvider
from repolab.sandbox.models import SandboxError
from repolab.testing.fakes import FakeLLMProvider, FakeMessage
from tests.support.builds import (
    CASSANDRA_REPO,
    SHA,
    cassandra_manifest,
    failing_smoke,
    failing_step,
    passing,
    provider,
    repository,
)
from tests.support.experiments import IMAGE, plan, spark_run_result

QUESTION = "Why does this job use 200 shuffle partitions?"

DESIGN = {
    "question": QUESTION,
    "hypothesis": "Most partitions are empty.",
    "variable": "spark.sql.shuffle.partitions",
    "values": [8, 50, 200],
    "metrics": ["runtime", "partitions", "empty partitions"],
    "repeats": 2,
}

#: Anything that would mean a traceback or an exception class reached a user.
LEAKY = ("Traceback", '  File "', "raise ", "self._")


def assert_reads_like_prose(text: str) -> None:
    """A message a person is shown must be sentences, not Python."""
    for marker in LEAKY:
        assert marker not in text, f"{marker!r} reached a user-facing message: {text!r}"
    assert text.strip(), "an empty failure message tells nobody anything"
    assert text[0].isupper(), f"not a sentence: {text!r}"


@pytest.fixture
def db_session():
    """A session on an in-memory database, for the job-reaper tests."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def spark_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "jobs").mkdir(parents=True)
    (root / "jobs" / "etl_job.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "def transform_data(df, steps):\n    return df\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "spark.conf.set('spark.sql.shuffle.partitions', 200)\n"
    )
    (root / "requirements.txt").write_text("pyspark==2.4.0\n")
    return root


def agent_for(repository_root: Path, *, router: ModelRouter | None = None, runner=None):
    return ExperimentAgent(
        repo_url="https://github.com/test/repo",
        commit_sha="c" * 40,
        repository_root=repository_root,
        image=IMAGE,
        router=router,
        budget=ExperimentBudget(),
        runner=runner,
    )


class TestModelTimeout:
    """1. The model does not answer in time."""

    def test_the_user_is_told_to_try_again(self) -> None:
        failure = explain(LLMTimeoutError("nvidia/nemotron-3-super", 180.0))

        assert failure.kind is FailureKind.MODEL
        assert failure.retryable is True
        assert_reads_like_prose(failure.headline)
        assert "too long" in failure.headline

    def test_the_model_id_is_not_the_headline(self) -> None:
        """A model name means nothing to a person watching a demo."""
        failure = explain(LLMTimeoutError("nvidia/nemotron-3-super-120b-a12b", 180.0))

        assert "nvidia" not in failure.headline
        assert "nvidia" in failure.technical, "but it is still available"

    async def test_an_investigation_survives_it(self, spark_repository: Path) -> None:
        """A timed-out model leaves the deterministic path, which is the whole path."""
        provider_ = FakeLLMProvider(fail_with=TimeoutError("too slow"))
        router = ModelRouter(provider_, {ModelTier.ULTRA: "fake/ultra"}, timeout_seconds=0.01)
        subject = agent_for(spark_repository, router=router)

        experiment = await subject.propose(QUESTION)

        # A plan was still produced -- deterministically -- and it still waits
        # for approval.
        assert experiment.plan is not None
        assert experiment.status is ExperimentStatus.AWAITING_APPROVAL


class TestMalformedModelJson:
    """2. The model answers with something that is not the agreed shape."""

    def test_the_user_is_told_nothing_was_acted_on(self) -> None:
        failure = explain(
            SchemaValidationError("m", "ExperimentPlan", retries=1, errors=[], excerpt="{")
        )

        assert_reads_like_prose(failure.headline)
        assert "could not be used" in failure.headline
        assert "nothing was run" in failure.next_step

    async def test_a_plan_is_still_produced(self, spark_repository: Path) -> None:
        """Prose instead of JSON, twice: the deterministic fallback takes over."""
        provider_ = FakeLLMProvider(
            messages=[FakeMessage(content="{"), FakeMessage(content="I think about 200.")]
        )
        router = ModelRouter(provider_, {ModelTier.ULTRA: "fake/ultra"})

        experiment = await agent_for(spark_repository, router=router).propose(QUESTION)

        assert experiment.plan is not None
        assert experiment.plan.values, "a sweep was still designed"
        assert experiment.status is ExperimentStatus.AWAITING_APPROVAL


class TestSandboxFailure:
    """3. The environment cannot be created."""

    def test_the_user_is_pointed_at_docker(self) -> None:
        failure = explain(SandboxError("Cannot connect to the Docker daemon at unix:///var/run"))

        assert failure.kind is FailureKind.ENVIRONMENT
        assert "Docker" in failure.next_step
        assert_reads_like_prose(failure.headline)
        assert "unix:///var/run" not in failure.headline
        assert "unix:///var/run" in failure.technical

    async def test_a_build_reports_it_without_claiming_readiness(self, tmp_path: Path) -> None:
        manifest = cassandra_manifest()
        from repolab.builds import cassandra_official_plan

        plan = cassandra_official_plan(manifest)
        sandbox = FakeSandboxProvider(fail_on_create=SandboxError("docker is not running"))

        with pytest.raises(BuildUnavailableError) as raised:
            await BuildLadder(sandbox, cache=InMemoryBuildCache()).resolve(
                BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
                [BuildCandidate(kind=manifest.build_strategy.kind, plan=plan, why="official")],
                repository(tmp_path),
                manifest=manifest,
            )

        assert_reads_like_prose(explain(raised.value).headline)


class TestBuildFailure:
    """4. Every rung of the ladder fails."""

    async def test_the_user_is_told_which_rungs_were_tried(self, tmp_path: Path) -> None:
        manifest = cassandra_manifest()
        from repolab.builds import cassandra_official_plan, cassandra_partial_plan

        plans = [cassandra_official_plan(manifest), cassandra_partial_plan(manifest)]
        sandbox = provider(*(failing_smoke(plan) for plan in plans))
        emitter = CollectingEmitter("job-1")

        with pytest.raises(BuildUnavailableError) as raised:
            await BuildLadder(
                sandbox,
                cache=InMemoryBuildCache(),
                emitter=emitter,
                limits=BuildLimits(max_attempts=1),
            ).resolve(
                BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
                [
                    BuildCandidate(kind=manifest.build_strategy.kind, plan=plans[0], why="a"),
                    BuildCandidate(kind=manifest.build_strategy.kind, plan=plans[1], why="b"),
                ],
                repository(tmp_path),
                manifest=manifest,
            )

        failure = explain(raised.value)
        assert failure.kind is FailureKind.BUILD
        assert "ladder" in failure.why
        assert "memory" in failure.next_step, "the usual cause, named"
        assert_reads_like_prose(failure.headline)

    async def test_nothing_is_cached_or_served(self, tmp_path: Path) -> None:
        """A failed build must not leave something that looks usable behind."""
        manifest = cassandra_manifest()
        from repolab.builds import cassandra_official_plan

        plan = cassandra_official_plan(manifest)
        cache = InMemoryBuildCache()

        with pytest.raises(BuildUnavailableError):
            await BuildLadder(
                provider(failing_smoke(plan)),
                cache=cache,
                limits=BuildLimits(max_attempts=1),
            ).resolve(
                BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
                [BuildCandidate(kind=manifest.build_strategy.kind, plan=plan, why="official")],
                repository(tmp_path),
                manifest=manifest,
            )

        assert len(cache) == 0

    async def test_a_lower_rung_serves_when_a_higher_one_fails(self, tmp_path: Path) -> None:
        """Degradation, not collapse: this is the ladder working as designed.

        The two container rungs share a smoke test -- CLAUDE.md fixes it as
        "cqlsh connects and a SELECT executes" -- so the higher rung is broken at
        a step only it has, which is also how it would fail in practice.
        """
        manifest = cassandra_manifest()
        from repolab.builds import cassandra_official_plan, cassandra_partial_plan

        broken, working = cassandra_official_plan(manifest), cassandra_partial_plan(manifest)
        sandbox = provider(
            passing(working, SMOKE_MARKER),
            failing_step(broken, "record versions", stderr="no such file"),
        )

        result = await BuildLadder(
            sandbox, cache=InMemoryBuildCache(), limits=BuildLimits(max_attempts=1)
        ).resolve(
            BuildRequest(repo_url=CASSANDRA_REPO, commit_sha=SHA),
            [
                BuildCandidate(kind=BuildKind.OFFICIAL_RELEASE, plan=broken, why="a"),
                BuildCandidate(kind=BuildKind.PARTIAL_BUILD, plan=working, why="b"),
            ],
            repository(tmp_path),
            manifest=manifest,
        )

        assert result.served.ready is True
        assert result.served.kind is BuildKind.PARTIAL_BUILD, "the lower rung served"
        assert result.served.fidelity.value == "partial"


class TestSparkJobFailure:
    """5. The repository's own job fails."""

    def test_the_user_is_told_it_is_the_applications_own_output(self) -> None:
        failure = explain(SparkRunFailed("spark-submit exited 1: py4j.protocol.Py4JJavaError"))

        assert failure.kind is FailureKind.EXECUTION
        assert "job failed" in failure.headline
        assert "application's own output" in failure.next_step
        assert "py4j" not in failure.headline
        assert_reads_like_prose(failure.headline)


class TestTavilyUnavailable:
    """6. External documentation cannot be retrieved."""

    def test_the_user_is_told_nothing_is_blocked(self) -> None:
        class TavilyError(RuntimeError):
            pass

        failure = explain(TavilyError("503 Service Unavailable"))

        assert failure.kind is FailureKind.RESEARCH
        assert "Nothing is blocked" in failure.next_step
        assert failure.retryable is True

    async def test_an_investigation_completes_without_it(self, spark_repository: Path) -> None:
        """Repository code is the primary evidence, so research is a supplement."""

        class Broken:
            async def search(self, *args: object, **kwargs: object):
                raise RuntimeError("tavily is down")

        from repolab.agents.investigator import RepositoryInvestigatorAgent
        from repolab.ingest.content import ReadBudget
        from repolab.ingest.detectors.base import DetectionContext
        from repolab.ingest.tree import build_file_tree

        tree = build_file_tree(spark_repository)
        context = DetectionContext(root=spark_repository, tree=tree, commit_sha="c" * 40)
        agent = RepositoryInvestigatorAgent(context, ReadBudget(), research=Broken())

        investigation = await agent.investigate(CollectingEmitter("job-1"))

        # The investigation finished and found something, with no documentation
        # to cite: repository code is the primary evidence.
        assert investigation.findings > 0
        assert investigation.outcome.entrypoints or investigation.outcome.run.findings


class TestExperimentVariantFailure:
    """7. One variant fails while the others succeed."""

    class OneBadValue:
        """A runner that fails every repeat of one value."""

        def __init__(self, bad: int) -> None:
            self.bad = bad
            self.calls: list[tuple[int, int]] = []

        async def run_once(self, *, value: int, repeat: int):
            self.calls.append((value, repeat))
            if value == self.bad:
                raise SparkRunFailed(f"spark-submit exited 1 at {value}")
            return spark_run_result(value=value, repeat=repeat, total_runtime_ms=1000)

    async def test_the_other_variants_still_run(self) -> None:
        """The defect this phase fixed: one failure used to abort everything."""
        subject_plan = plan(values=[8, 50, 200], repeats=1, baseline=200)
        runner = self.OneBadValue(bad=50)
        executor = VariantExecutor(runner)

        runs = await executor.execute(subject_plan, status=ExperimentStatus.APPROVED)

        assert {run.value for run in runs} == {8, 200}
        assert len(executor.failures) == 1
        assert executor.failures[0].value == 50
        assert_reads_like_prose(executor.failures[0].reason)

    async def test_too_few_surviving_values_is_refused(self) -> None:
        """Two values are the minimum for a comparison, however many repeats ran."""
        subject_plan = plan(values=[8, 50], repeats=1, baseline=50)
        executor = VariantExecutor(self.OneBadValue(bad=50))

        with pytest.raises(NotEnoughRuns, match="not enough to compare"):
            await executor.execute(subject_plan, status=ExperimentStatus.APPROVED)

    async def test_the_failure_is_announced_on_the_stream(self) -> None:
        subject_plan = plan(values=[8, 50, 200], repeats=1, baseline=200)
        emitter = CollectingEmitter("job-1")

        await VariantExecutor(self.OneBadValue(bad=50), emitter=emitter).execute(
            subject_plan, status=ExperimentStatus.APPROVED
        )

        announced = [
            event.message for event in emitter.events if "did not complete" in event.message
        ]
        assert announced, "a run that did not happen must be reported"

    async def test_the_explanation_is_told_the_statistics_rest_on_fewer_runs(
        self, spark_repository: Path
    ) -> None:
        """A conclusion drawn from four runs when six were planned is weaker."""
        provider_ = FakeLLMProvider(messages=[FakeMessage(content=json.dumps(DESIGN))])
        router = ModelRouter(provider_, {ModelTier.ULTRA: "fake/ultra"})
        subject = agent_for(spark_repository, router=router, runner=self.OneBadValue(bad=50))

        experiment = await subject.propose(QUESTION)
        experiment.approve()
        experiment = await subject.execute(experiment)

        assert experiment.status is ExperimentStatus.COMPLETED
        notes = subject._failure_notes()
        assert notes and "did not complete" in notes[0]


class TestWorkerRestart:
    """8. The worker stops while a job is running."""

    def test_an_abandoned_job_is_failed_with_a_sentence(self, db_session) -> None:
        job = create_job(db_session, "https://github.com/test/repo")
        job.status = JobStatus.RUNNING.value
        db_session.add(
            JobEventRecord(
                job_id=job.id,
                seq=1,
                type="step_started",
                message="cloning",
                created_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        db_session.flush()

        abandoned = fail_abandoned_jobs(db_session, reason="The job stopped. Start it again.")

        assert abandoned == [job.id]
        refreshed = db_session.get(Job, job.id)
        assert refreshed.status == JobStatus.FAILED.value
        assert_reads_like_prose(refreshed.error)

    def test_a_live_job_is_left_alone(self, db_session) -> None:
        """A second worker starting must not kill the first one's work."""
        job = create_job(db_session, "https://github.com/test/repo")
        job.status = JobStatus.RUNNING.value
        db_session.add(JobEventRecord(job_id=job.id, seq=1, type="progress", message="working"))
        db_session.flush()

        assert fail_abandoned_jobs(db_session, reason="restarted") == []
        assert db_session.get(Job, job.id).status == JobStatus.RUNNING.value

    def test_a_finished_job_is_untouched(self, db_session) -> None:
        job = create_job(db_session, "https://github.com/test/repo")
        job.status = JobStatus.COMPLETED.value
        db_session.flush()

        assert fail_abandoned_jobs(db_session, reason="restarted") == []
        assert db_session.get(Job, job.id).status == JobStatus.COMPLETED.value


class TestTheMessagesThemselves:
    """Across every mode: prose, not Python."""

    @pytest.mark.parametrize(
        "exc",
        [
            LLMTimeoutError("m", 1.0),
            SchemaValidationError("m", "S", retries=1, errors=[], excerpt=""),
            SandboxError("x"),
            SparkRunFailed("x"),
            BuildUnavailableError("r", "x"),
            RuntimeError("something nobody mapped"),
            ValueError(""),
        ],
    )
    def test_every_failure_reads_like_prose(self, exc: Exception) -> None:
        failure = explain(exc)

        assert_reads_like_prose(failure.headline)
        assert_reads_like_prose(failure.why)
        if failure.next_step:
            assert_reads_like_prose(failure.next_step)

    def test_an_unrecognised_failure_does_not_guess(self) -> None:
        failure = explain(RuntimeError("the disk is on fire"))

        assert failure.kind is FailureKind.INTERNAL
        assert "will not guess" in failure.why
        assert "the disk is on fire" in failure.technical

    def test_a_traceback_is_never_passed_through(self) -> None:
        """The easy way to break this rule is to format an exception with its trace."""
        try:
            raise ValueError("deep inside")
        except ValueError as exc:
            failure = explain(exc)

        assert "Traceback" not in failure.technical
        assert "\n" not in failure.headline

    def test_describe_puts_the_sentence_first(self) -> None:
        text = describe(SandboxError("docker is not running"))

        assert text.startswith("The execution environment is not available.")
        assert "docker is not running" in text


class TestTheMessageMustMatchWhatHappened:
    """Found by provoking a real failure: a true sentence about the wrong thing.

    Asking RepoLab to build `pallets/click` reported *"every rung of the ladder
    was tried and none passed"*, because the worker wrapped "no rung covers this
    repository" in the error that means "they all failed". A confident message
    that misdescribes the failure is worse than a vague one.
    """

    def test_no_build_strategy_is_not_reported_as_every_rung_failing(self) -> None:
        from repolab.builds.candidates import NoCandidatesError

        failure = explain(NoCandidatesError("https://github.com/pallets/click", "cli"))

        assert "does not know how to build" in failure.headline
        assert "ladder" not in failure.why, "nothing was tried, so nothing failed"
        assert "still works for any repository" in failure.next_step

    def test_a_wrapped_known_failure_keeps_its_own_sentence(self) -> None:
        """An unrecognised wrapper must not hide a failure RepoLab can explain."""

        class Wrapper(RuntimeError):
            pass

        try:
            try:
                raise SandboxError("docker is not running")
            except SandboxError as inner:
                raise Wrapper("while preparing the environment") from inner
        except Wrapper as exc:
            failure = explain(exc)

        assert failure.kind is FailureKind.ENVIRONMENT
        assert "Docker" in failure.next_step
        # The outer exception's own words are what actually happened.
        assert "while preparing the environment" in failure.technical

    def test_the_streams_terminal_frame_is_not_a_failure_explanation(self) -> None:
        """The browser reads the last `failed` event; one of them is bookkeeping.

        The SSE endpoint closes a stream with a `failed` frame carrying only
        `{status, terminal}`. Taking the last event of that type finds the frame
        and loses the explanation, which is what the browser used to do.
        """
        emitter = CollectingEmitter("job-1")
        failure = explain(SandboxError("docker is not running"))

        emitter.emit(
            EventType.FAILED,
            failure.summary,
            step="environment",
            data=failure.model_dump_public(),
        )
        emitter.emit(EventType.FAILED, "job failed", step="job", data={"terminal": True})

        explained = [
            event for event in emitter.of_type(EventType.FAILED) if event.data.get("headline")
        ]
        assert len(explained) == 1
        assert explained[-1].data["headline"] == failure.headline


class TestStaleCheckpoints:
    """A cached build whose checkpoint has been removed from the machine.

    The pre-demo audit's worst finding. `docker image prune` is enough to cause
    it, the cache row still claims `ready`, and before this:

    * the learner was told *"The execution environment is not available. Check
      that Docker is running"* -- while Docker was running perfectly;
    * the dead row was served again on every retry, so the failure was permanent
      until someone cleared the database by hand.
    """

    def test_a_missing_checkpoint_is_its_own_failure(self) -> None:
        from repolab.sandbox.models import CheckpointMissingError

        failure = explain(
            CheckpointMissingError("the prepared environment 'x:y' is no longer on this machine")
        )

        assert failure.kind is FailureKind.BUILD
        assert "no longer on this machine" in failure.headline
        assert "prune" in failure.why, "the usual cause, named"
        assert "Build it again" in failure.next_step
        assert "Check that Docker is running" not in failure.next_step
        assert_reads_like_prose(failure.headline)

    def test_it_is_not_confused_with_docker_being_down(self) -> None:
        """Two different remedies, so two different messages."""
        from repolab.sandbox.models import CheckpointMissingError

        gone = explain(CheckpointMissingError("x"))
        down = explain(SandboxError("Cannot connect to the Docker daemon"))

        assert gone.headline != down.headline
        assert "Docker is running" in down.next_step

    def test_a_forgotten_row_lets_the_next_build_succeed(self) -> None:
        """The cache must be able to drop a build nothing can start from."""
        from repolab.builds.models import BuildArtifact, SmokeTestResult

        cache = InMemoryBuildCache()
        artifact = BuildArtifact(
            repo_url=CASSANDRA_REPO,
            commit_sha=SHA,
            kind=BuildKind.OFFICIAL_RELEASE,
            checkpoint_id="ckpt",
            checkpoint_tag="repolab/gone:tag",
            smoke=SmokeTestResult(name="s", command="c", passed=True),
        )
        cache.put(artifact)
        assert cache.get_all(CASSANDRA_REPO, SHA)

        assert cache.forget(CASSANDRA_REPO, SHA, BuildKind.OFFICIAL_RELEASE.value) is True
        assert cache.get_all(CASSANDRA_REPO, SHA) == []
        assert cache.forget(CASSANDRA_REPO, SHA, BuildKind.OFFICIAL_RELEASE.value) is False

    async def test_a_session_on_a_missing_checkpoint_fails_with_the_right_sentence(self) -> None:
        from repolab.builds.models import BuildArtifact, SmokeTestResult
        from repolab.experiences.cli.session import CliSessionManager, SessionStatus

        artifact = BuildArtifact(
            repo_url=CASSANDRA_REPO,
            commit_sha=SHA,
            kind=BuildKind.OFFICIAL_RELEASE,
            checkpoint_id="ckpt",
            checkpoint_tag="repolab/gone:tag",
            service_command="cassandra -f",
            cli_command="/opt/cassandra/bin/cqlsh",
            smoke=SmokeTestResult(name="s", command="c", passed=True),
        )
        # The fake provider holds no such checkpoint, which is the situation.
        session = await CliSessionManager(FakeSandboxProvider()).start(
            artifact, repo_url=CASSANDRA_REPO
        )

        assert session.status is SessionStatus.FAILED
        assert session.error is not None
        assert_reads_like_prose(session.error)


class TestEveryExternalCallHasADeadline:
    """A hang is worse than an error: an error degrades, silence does not.

    The pre-demo audit found research alone unbounded. Tavily's client is
    synchronous and run in a thread, and `asyncio.to_thread` cannot be
    cancelled, so a hanging request would have stalled an investigation with
    nothing reported.
    """

    def test_research_passes_a_deadline_to_the_client(self) -> None:
        import asyncio as _asyncio

        from repolab.research.tavily import DEFAULT_TIMEOUT_SECONDS, TavilyResearchProvider

        recorded: dict[str, object] = {}

        class Client:
            def search(self, **kwargs: object) -> dict[str, object]:
                recorded.update(kwargs)
                return {"results": []}

        provider = TavilyResearchProvider.__new__(TavilyResearchProvider)
        provider._client = Client()
        provider._timeout = DEFAULT_TIMEOUT_SECONDS

        _asyncio.run(provider.search("cassandra", include_domains=["cassandra.apache.org"]))

        assert recorded["timeout"] == DEFAULT_TIMEOUT_SECONDS
        assert DEFAULT_TIMEOUT_SECONDS <= 30, "shorter than a person's patience"

    def test_a_research_timeout_is_explained_as_harmless(self) -> None:
        failure = explain(TimeoutError("timed out after 20s"))

        # TimeoutError maps to the model-slow sentence, which is the honest
        # reading for a call that did not answer in time.
        assert failure.retryable is True
        assert_reads_like_prose(failure.headline)

    @pytest.mark.parametrize(
        ("name", "limit"),
        [
            ("LIFECYCLE_TIMEOUT", 600.0),
            ("DEFAULT_COMMAND_TIMEOUT", 1800.0),
            ("PULL_TIMEOUT", 3600.0),
        ],
    )
    def test_the_container_timeouts_are_bounded(self, name: str, limit: float) -> None:
        """Every docker operation has a deadline, and none of them is a whole demo."""
        from repolab.sandbox import docker

        value = getattr(docker, name)
        assert 0 < value <= limit

    def test_a_terminal_statement_cannot_run_forever(self) -> None:
        from repolab.experiences.cli.session import (
            COMMAND_TIMEOUT_SECONDS,
            IDLE_TIMEOUT_SECONDS,
            READY_TIMEOUT_SECONDS,
        )

        assert 0 < COMMAND_TIMEOUT_SECONDS <= 300
        assert 0 < READY_TIMEOUT_SECONDS <= 600
        assert 0 < IDLE_TIMEOUT_SECONDS <= 3600, "a forgotten tab must not hold a container"
