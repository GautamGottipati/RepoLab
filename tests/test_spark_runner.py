"""The Spark run orchestration.

Exercised offline against a fake environment and a real captured event log, so
the whole path -- dataset, upload, submit, parse, measure, evidence -- is covered
without Docker.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repolab.events import CollectingEmitter, EventType
from repolab.experiences.spark import SparkDemoRunner, SparkRunConfig, SparkRunFailed
from repolab.experiences.spark.driver import DISCLOSURE_MARKER, SUCCESS_MARKER
from repolab.interfaces import CommandResult
from repolab.sandbox.models import Checkpoint, SandboxCapabilities
from tests.test_detectors import SPARK_REPO

FIXTURE = Path(__file__).parent / "fixtures" / "spark" / "real_event_log.jsonl"
REPO = "https://github.com/AlexIoannides/pyspark-example-project"
COMMIT = "e" * 40


class FakeSparkEnvironment:
    """An environment that produces a real event log without running Spark.

    It copies the captured log into the mounted directory when spark-submit is
    "run", which is exactly what the real environment does -- so the runner's
    collect-and-parse path is genuinely exercised.
    """

    kind = "fake-spark"

    def __init__(
        self,
        host_workdir: Path,
        *,
        submit_exit_code: int = 0,
        submit_stdout: str | None = None,
        produce_log: bool = True,
    ) -> None:
        self.host_workdir = host_workdir
        self.container_workdir = "/work"
        self._submit_exit_code = submit_exit_code
        self._submit_stdout = submit_stdout
        self._produce_log = produce_log

        self.commands: list[str] = []
        self.uploaded: dict[str, bytes] = {}
        self.started_image: str | None = None
        self.stopped = False

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities()

    async def start(self, image: str, *, from_checkpoint: str | None = None) -> None:
        self.started_image = image
        self.host_workdir.mkdir(parents=True, exist_ok=True)

    async def put_files(self, files: dict[str, bytes]) -> None:
        self.uploaded.update(files)

    async def run(self, command: str, *, timeout=None, cwd=None, env=None) -> CommandResult:
        self.commands.append(command)

        if "spark-submit" in command:
            if self._produce_log:
                events = self.host_workdir / "events"
                events.mkdir(parents=True, exist_ok=True)
                shutil.copy2(FIXTURE, events / "local-1790831454261")
            stdout = self._submit_stdout
            if stdout is None:
                stdout = f"REPOLAB_SPARK_VERSION 3.5.1\n{DISCLOSURE_MARKER} ok\n{SUCCESS_MARKER}\n"
            return CommandResult(stdout=stdout, exit_code=self._submit_exit_code)

        return CommandResult(stdout="", exit_code=0)

    async def save(self, *, tag: str | None = None) -> Checkpoint:
        return Checkpoint(id="fake-ckpt", tag=tag)

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def repo(make_repo):
    root, _ = make_repo(SPARK_REPO)
    return root


async def run_demo(tmp_path: Path, repo: Path, **environment_kwargs):
    environment = FakeSparkEnvironment(tmp_path / "work", **environment_kwargs)
    emitter = CollectingEmitter("job-1")
    runner = SparkDemoRunner(environment, emitter=emitter, runs_root=tmp_path / "runs")
    result = await runner.run(
        repo_url=REPO,
        commit_sha=COMMIT,
        repository_root=repo,
        image="apache/spark:3.5.1-python3",
        rows=1000,
    )
    return result, environment, emitter


class TestTheHappyPath:
    async def test_it_produces_measurements(self, tmp_path: Path, repo: Path) -> None:
        result, _, _ = await run_demo(tmp_path, repo)

        assert result.measurements.task_count > 0
        assert result.measurements.stage_count > 0
        assert result.measurements.shuffle_bytes_read > 0

    async def test_the_dataset_hash_is_recorded(self, tmp_path: Path, repo: Path) -> None:
        result, _, _ = await run_demo(tmp_path, repo)
        assert len(result.dataset_hash) == 64
        assert result.dataset.rows == 1000

    async def test_the_environment_is_recorded(self, tmp_path: Path, repo: Path) -> None:
        result, _, _ = await run_demo(tmp_path, repo)

        assert result.environment.kind == "fake-spark"
        assert result.environment.image == "apache/spark:3.5.1-python3"
        assert result.environment.spark_version == "3.5.1"

    async def test_the_configuration_is_recorded(self, tmp_path: Path, repo: Path) -> None:
        result, _, _ = await run_demo(tmp_path, repo)
        assert result.config.shuffle_partitions == 200

    async def test_the_environment_is_always_stopped(self, tmp_path: Path, repo: Path) -> None:
        _, environment, _ = await run_demo(tmp_path, repo)
        assert environment.stopped is True


class TestWhatIsPlacedInTheEnvironment:
    async def test_the_dataset_is_uploaded(self, tmp_path: Path, repo: Path) -> None:
        _, environment, _ = await run_demo(tmp_path, repo)
        assert "/work/dataset.csv" in environment.uploaded

    async def test_the_repository_is_uploaded(self, tmp_path: Path, repo: Path) -> None:
        _, environment, _ = await run_demo(tmp_path, repo)
        assert any(path.startswith("/work/repo/jobs/") for path in environment.uploaded)

    async def test_git_metadata_is_not_uploaded(self, tmp_path: Path, repo: Path) -> None:
        _, environment, _ = await run_demo(tmp_path, repo)
        assert not any("/.git/" in path for path in environment.uploaded)

    async def test_the_driver_is_uploaded_and_calls_the_repository(
        self, tmp_path: Path, repo: Path
    ) -> None:
        """The measurements must describe the repository's own code."""
        _, environment, _ = await run_demo(tmp_path, repo)
        driver = environment.uploaded["/work/repolab_driver.py"].decode()

        assert "from jobs.etl_job import transform_data as repo_transform" in driver
        assert "repo_transform(frame, STEPS_PER_FLOOR)" in driver

    async def test_event_logging_is_enabled_on_submit(self, tmp_path: Path, repo: Path) -> None:
        """Without this there would be no measurement source at all."""
        _, environment, _ = await run_demo(tmp_path, repo)
        submit = next(c for c in environment.commands if "spark-submit" in c)

        assert "spark.eventLog.enabled=true" in submit
        assert "spark.eventLog.dir=file:///work/events" in submit

    async def test_adaptive_execution_is_disabled(self, tmp_path: Path, repo: Path) -> None:
        """Adaptive coalescing would hide the distribution being measured."""
        _, environment, _ = await run_demo(tmp_path, repo)
        submit = next(c for c in environment.commands if "spark-submit" in c)
        assert "spark.sql.adaptive.enabled=false" in submit

    async def test_the_partition_count_is_passed_through(self, tmp_path: Path, repo: Path) -> None:
        environment = FakeSparkEnvironment(tmp_path / "work")
        runner = SparkDemoRunner(environment, runs_root=tmp_path / "runs")
        await runner.run(
            repo_url=REPO,
            commit_sha=COMMIT,
            repository_root=repo,
            image="img",
            config=SparkRunConfig(shuffle_partitions=12),
            rows=500,
        )
        submit = next(c for c in environment.commands if "spark-submit" in c)
        assert "spark.sql.shuffle.partitions=12" in submit


class TestTheRawSourceIsPreserved:
    async def test_the_event_log_is_copied_out_and_kept(self, tmp_path: Path, repo: Path) -> None:
        """Raw logs are never discarded."""
        result, _, _ = await run_demo(tmp_path, repo)
        preserved = Path(result.event_log_path)

        assert preserved.is_file()
        assert preserved.stat().st_size == result.event_log_bytes

    async def test_the_preserved_log_is_hashed(self, tmp_path: Path, repo: Path) -> None:
        import hashlib

        result, _, _ = await run_demo(tmp_path, repo)
        content = Path(result.event_log_path).read_bytes()
        assert result.event_log_sha256 == hashlib.sha256(content).hexdigest()

    async def test_it_survives_the_environment_being_stopped(
        self, tmp_path: Path, repo: Path
    ) -> None:
        result, environment, _ = await run_demo(tmp_path, repo)
        assert environment.stopped is True
        assert Path(result.event_log_path).is_file()


class TestFailureHandling:
    async def test_a_failing_submit_is_reported(self, tmp_path: Path, repo: Path) -> None:
        with pytest.raises(SparkRunFailed, match="exited 1"):
            await run_demo(tmp_path, repo, submit_exit_code=1)

    async def test_a_missing_success_marker_fails_the_run(self, tmp_path: Path, repo: Path) -> None:
        """A zero exit code is not proof the driver finished its work."""
        with pytest.raises(SparkRunFailed, match="did not report success"):
            await run_demo(tmp_path, repo, submit_stdout="started and then nothing\n")

    async def test_no_event_log_fails_the_run(self, tmp_path: Path, repo: Path) -> None:
        """Measurements with no source would be worthless."""
        with pytest.raises(SparkRunFailed, match="no Spark event log"):
            await run_demo(tmp_path, repo, produce_log=False)

    async def test_the_environment_is_stopped_even_on_failure(
        self, tmp_path: Path, repo: Path
    ) -> None:
        environment = FakeSparkEnvironment(tmp_path / "work", submit_exit_code=1)
        runner = SparkDemoRunner(environment, runs_root=tmp_path / "runs")
        with pytest.raises(SparkRunFailed):
            await runner.run(
                repo_url=REPO,
                commit_sha=COMMIT,
                repository_root=repo,
                image="img",
                rows=100,
            )
        assert environment.stopped is True


class TestEvidenceAndProgress:
    async def test_evidence_is_assembled(self, tmp_path: Path, repo: Path) -> None:
        result, _, _ = await run_demo(tmp_path, repo)

        assert result.evidence.measurements
        assert result.evidence.code_references
        assert result.evidence.logs
        assert result.evidence.simulated is False

    async def test_the_additions_are_disclosed(self, tmp_path: Path, repo: Path) -> None:
        """What RepoLab added beyond the repository's own code must be stated."""
        result, _, _ = await run_demo(tmp_path, repo)
        disclosure = " ".join(result.added_steps)

        assert "repartition" in disclosure
        assert "groupBy" in disclosure
        assert "added by RepoLab" in disclosure

    async def test_progress_is_reported(self, tmp_path: Path, repo: Path) -> None:
        _, _, emitter = await run_demo(tmp_path, repo)
        types = set(emitter.types())

        assert EventType.STEP_STARTED in types
        assert EventType.PROGRESS in types
        assert EventType.PARTIAL_RESULT in types
        assert EventType.STEP_DONE in types

    async def test_the_dataset_is_reported_before_the_run(self, tmp_path: Path, repo: Path) -> None:
        _, _, emitter = await run_demo(tmp_path, repo)
        first = emitter.events[0]
        assert "dataset" in first.message
        assert first.data["dataset_sha256"]


class TestStructuralReproducibility:
    async def test_two_runs_of_the_same_log_agree_structurally(
        self, tmp_path: Path, repo: Path
    ) -> None:
        """The property the command's two-run check relies on."""
        first, _, _ = await run_demo(tmp_path / "one", repo)
        second, _, _ = await run_demo(tmp_path / "two", repo)

        assert first.dataset_hash == second.dataset_hash
        assert (
            first.measurements.structural_fingerprint()
            == second.measurements.structural_fingerprint()
        )
