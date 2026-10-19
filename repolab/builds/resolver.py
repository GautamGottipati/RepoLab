"""Executing one build plan.

    upload  -->  steps  -->  smoke test  -->  checkpoint

This is the machinery every rung of the ladder shares. It knows how to run a
plan and decide whether the result works; it does not know about fallbacks.
:class:`~repolab.builds.ladder.BuildLadder` owns the choice between candidates,
so the two concerns stay apart.

``resolve`` remains the single-strategy path -- cache, then an exact source
build, or an error -- which is what the Spark flow uses.

Every attempt is recorded, including failures, and nothing is cached or reported
ready until its smoke test passes.
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path, PurePosixPath

import structlog

from repolab.builds.cache import BuildCache, NullBuildCache
from repolab.builds.models import (
    BuildArtifact,
    BuildAttempt,
    BuildCapabilities,
    BuildRequest,
    BuildSource,
    BuildUnavailableError,
    SmokeTestResult,
    StepOutcome,
)
from repolab.builds.plan import BuildPlan
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.manifest.models import BuildKind
from repolab.sandbox.environment import SandboxEnvironment
from repolab.sandbox.models import Checkpoint
from repolab.sandbox.provider import SandboxProvider

log = structlog.get_logger(__name__)

STEP_LABEL = "build"

#: Directories never worth shipping into a build environment.
_ARCHIVE_EXCLUDES = frozenset({".git", "__pycache__", ".venv", "node_modules", ".mypy_cache"})

#: Guard against uploading an unreasonably large repository in one archive.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class ArchiveTooLargeError(BuildUnavailableError):
    """The repository archive exceeds what will be uploaded in one piece."""

    def __init__(self, repo_url: str, size: int) -> None:
        super().__init__(
            repo_url,
            f"archive is {size / 1_048_576:.0f} MiB, above the "
            f"{MAX_ARCHIVE_BYTES / 1_048_576:.0f} MiB upload limit",
        )


def points_inside(name: str, linkname: str) -> bool:
    """Whether a link at *name* pointing at *linkname* stays inside the archive.

    Resolved textually, without touching the filesystem: the question is where
    the link will point *once extracted somewhere else*, which is not where it
    points now.
    """
    target = PurePosixPath(linkname)
    if target.is_absolute():
        return False

    resolved: list[str] = []
    for part in (PurePosixPath(name).parent / target).parts:
        if part == "..":
            if not resolved:
                return False  # escapes above the archive root
            resolved.pop()
        elif part != ".":
            resolved.append(part)
    return True


def archive_repository(root: Path, *, limit: int = MAX_ARCHIVE_BYTES) -> bytes:
    """Tar and gzip a local clone, in memory.

    Purely local: this reads files, and never runs anything the repository
    contains. ``.git`` and build caches are excluded because a build does not need
    history.

    **Internal symlinks are kept; escaping ones are dropped.** Dropping every
    link was safe and wrong: Cassandra's build fails on a missing
    ``.build/git/git-hooks/post-switch``, which in the repository is a symlink to
    a sibling directory. Silently handing a build a different tree than the
    repository has is its own kind of unsafe.
    """
    buffer = io.BytesIO()
    root = Path(root)

    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        if any(part in _ARCHIVE_EXCLUDES for part in parts):
            return None
        if info.issym() or info.islnk():
            # A link out of the tree would read or clobber host files once
            # extracted; one that stays inside is part of the repository.
            return info if points_inside(info.name, info.linkname) else None
        return info

    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for entry in sorted(root.iterdir()):
            archive.add(entry, arcname=entry.name, filter=keep)

    data = buffer.getvalue()
    if len(data) > limit:
        raise ArchiveTooLargeError(str(root), len(data))
    return data


class BuildResolver:
    """Resolves a repository commit to a ready, checkpointed environment."""

    def __init__(
        self,
        provider: SandboxProvider,
        *,
        cache: BuildCache | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._provider = provider
        # Explicit None checks: InMemoryBuildCache defines __len__, so an *empty*
        # cache is falsy and `cache or default` would silently discard it.
        self._cache = cache if cache is not None else NullBuildCache()
        self._emitter = emitter if emitter is not None else NullEmitter()

    async def resolve(
        self, request: BuildRequest, plan: BuildPlan, repository_root: Path
    ) -> BuildArtifact:
        """Return a ready artifact for *request*, building one if necessary.

        Raises:
            BuildUnavailableError: the source build failed and no fallback exists.
        """
        if request.use_cache:
            cached = self._cache.get(request.repo_url, request.commit_sha)
            if cached is not None and cached.ready:
                self._emitter.emit(
                    EventType.STEP_DONE,
                    f"using the cached build for {request.commit_sha[:12]}",
                    step=STEP_LABEL,
                    data={
                        "source": BuildSource.CACHE.value,
                        "checkpoint_id": cached.checkpoint_id,
                        "fidelity": cached.fidelity.value,
                    },
                )
                log.info("builds.cache_hit", repo=request.repo_url)
                return cached

        artifact = await self._source_build(request, plan, repository_root)

        if not artifact.ready:
            # Single-strategy: there is no fallback from here. A caller that
            # wants the ladder uses BuildLadder, which catches this case by
            # having somewhere else to go.
            reason = self._failure_reason(artifact)
            self._emitter.emit(
                EventType.FAILED,
                f"build failed: {reason}",
                step=STEP_LABEL,
                data={"attempts": len(artifact.attempts)},
            )
            raise BuildUnavailableError(request.repo_url, reason)

        self._cache.put(artifact)
        return artifact

    @staticmethod
    def _failure_reason(artifact: BuildArtifact) -> str:
        attempt = artifact.attempts[-1] if artifact.attempts else None
        if attempt is None:
            return "no attempt was made"
        if attempt.error:
            return attempt.error
        failed = attempt.failed_step
        if failed is not None:
            return f"step {failed.name!r} exited {failed.exit_code}"
        if attempt.smoke is not None and not attempt.smoke.passed:
            return f"smoke test failed: {attempt.smoke.reason}"
        return "unknown failure"

    # -- building one candidate -------------------------------------------

    async def _source_build(
        self, request: BuildRequest, plan: BuildPlan, repository_root: Path
    ) -> BuildArtifact:
        return await self.build(request, plan, repository_root, kind=BuildKind.SOURCE_BUILD)

    async def build(
        self,
        request: BuildRequest,
        plan: BuildPlan,
        repository_root: Path,
        *,
        kind: BuildKind = BuildKind.SOURCE_BUILD,
    ) -> BuildArtifact:
        """Execute one build plan and return its artifact, ready or not.

        Returns rather than raises on failure: a candidate that did not work is
        an outcome the ladder has to see, complete with its attempts, not an
        exception that loses them.

        *kind* decides what the artifact claims to be, and therefore -- through
        :func:`fidelity_for` -- its fidelity. The same plan machinery produces
        every rung; only the label and the deviations differ.
        """
        attempt = BuildAttempt(attempt=1, provider=self._provider.name, base_image=plan.base_image)
        artifact = BuildArtifact(
            repo_url=request.repo_url,
            commit_sha=request.commit_sha,
            kind=kind,
            provider=self._provider.name,
            base_image=plan.base_image,
            capabilities=BuildCapabilities(
                capabilities=list(plan.capabilities),
                limitations=list(plan.limitations),
                deviations=list(plan.deviations),
            ),
            attempts=[attempt],
        )

        environment = SandboxEnvironment(self._provider)
        try:
            self._emitter.emit(
                EventType.STEP_STARTED,
                f"{kind.value}: building on {plan.base_image}",
                step=STEP_LABEL,
                data={
                    "kind": kind.value,
                    "provider": self._provider.name,
                    "base_image": plan.base_image,
                },
            )
            # The build runs in a plain container, never alongside the service.
            # A service started here would read its configuration *before* the
            # steps that adjust it -- measured on the partial build, whose
            # reduced config had no effect because the node was already up.
            await environment.start(plan.base_image, memory=plan.memory)

            archive = archive_repository(repository_root)
            await environment.put_files({plan.archive_path: archive})
            self._emitter.emit(
                EventType.PROGRESS,
                f"uploaded the repository ({len(archive) / 1024:.0f} KiB)",
                step=STEP_LABEL,
                data={"archive_bytes": len(archive)},
            )

            if not await self._run_steps(environment, plan, attempt):
                attempt.finish(error=None)
                return artifact

            # Tagged per rung: without the kind, two candidates of one commit
            # commit to the same name and the last one wins.
            tag = request.checkpoint_tag_for(kind)

            # A build with no service checkpoints *after* its smoke test, because
            # checkpointing a broken environment would cache a trap. A build with
            # a service has no choice: the server must be the main process of a
            # container started from the build, so the commit comes first. The
            # artifact still claims it only on success, and `ready` is computed
            # from the smoke result either way.
            checkpoint: Checkpoint | None = None
            service: SandboxEnvironment | None = None
            try:
                if plan.service_command is not None:
                    checkpoint = await environment.save(tag=tag)
                    service = await self._start_service(plan, checkpoint)
                    smoke = await self._run_smoke(service, plan)
                else:
                    smoke = await self._run_smoke(environment, plan)

                attempt.smoke = smoke
                artifact.smoke = smoke

                if not smoke.passed:
                    attempt.finish()
                    self._emitter.emit(
                        EventType.STEP_DONE,
                        f"smoke test failed: {smoke.reason}",
                        step=STEP_LABEL,
                        data={"passed": False},
                    )
                    return artifact

                if checkpoint is None:
                    checkpoint = await environment.save(tag=tag)

                artifact.checkpoint_id = checkpoint.id
                artifact.checkpoint_tag = checkpoint.tag
                # Carried onto the artifact so a later session can restore this
                # checkpoint into a running server without the plan.
                artifact.service_command = plan.service_command
                artifact.cli_command = plan.cli_command
                artifact.memory = plan.memory
                artifact.capabilities.versions = self._versions(attempt)
                artifact.version = self._reported_version(artifact)
                attempt.finish()
            finally:
                if service is not None:
                    await service.stop()

            self._emitter.emit(
                EventType.STEP_DONE,
                f"build ready at checkpoint {checkpoint.reference}",
                step=STEP_LABEL,
                data={
                    "checkpoint_id": checkpoint.id,
                    "checkpoint_tag": checkpoint.tag,
                    "fidelity": artifact.fidelity.value,
                },
            )
            log.info(
                "builds.ready",
                repo=request.repo_url,
                checkpoint=checkpoint.id,
                fidelity=artifact.fidelity.value,
            )
            return artifact

        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            attempt.finish(error=f"{type(exc).__name__}: {exc}")
            log.warning("builds.attempt_failed", repo=request.repo_url, error=str(exc))
            return artifact
        finally:
            # The environment is released either way; the checkpoint outlives it.
            await environment.stop()

    async def _run_steps(
        self, environment: SandboxEnvironment, plan: BuildPlan, attempt: BuildAttempt
    ) -> bool:
        """Run the plan's steps. Returns whether the build may continue."""
        for step in plan.all_steps:
            started = time.perf_counter()
            result = await environment.run(step.command, timeout=step.timeout_seconds)
            duration = round(time.perf_counter() - started, 3)

            ok = result.exit_code == 0
            outcome = StepOutcome(
                name=step.name,
                command=step.command,
                exit_code=result.exit_code,
                duration_seconds=duration,
                ok=ok or step.optional,
                output_tail=(result.stdout + result.stderr),
            )
            attempt.steps.append(outcome)

            self._emitter.emit(
                EventType.PROGRESS if outcome.ok else EventType.LOG_LINE,
                f"{step.name}: exit {result.exit_code}",
                step=STEP_LABEL,
                data={"step": step.name, "exit_code": result.exit_code, "ok": outcome.ok},
            )

            if not ok and not step.optional:
                # Preserve the log tail: build failures are never hidden.
                self._emitter.emit(
                    EventType.LOG_LINE,
                    (result.stderr or result.stdout or "no output")[-400:],
                    step=STEP_LABEL,
                    data={"step": step.name, "failed": True},
                )
                return False
        return True

    async def _start_service(self, plan: BuildPlan, checkpoint: Checkpoint) -> SandboxEnvironment:
        """Start the plan's server as a fresh container's main process.

        A long-running server cannot be launched from a build step: it dies with
        the shell that started it, leaving nothing listening and a smoke test
        with nothing to connect to. Measured on Cassandra, which exits silently
        when started that way.

        Starting from the *checkpoint* also means the build's own steps -- the
        partial build's reduced configuration, for instance -- are already in
        place when the server reads them, which they were not when the server
        ran alongside the steps that were adjusting it.
        """
        self._emitter.emit(
            EventType.PROGRESS,
            "starting the service from the build",
            step=STEP_LABEL,
            data={"checkpoint": checkpoint.reference},
        )

        service = SandboxEnvironment(self._provider)
        await service.start(checkpoint.reference, command=plan.service_command, memory=plan.memory)
        return service

    async def _run_smoke(self, environment: SandboxEnvironment, plan: BuildPlan) -> SmokeTestResult:
        """Run the smoke test. Its result is the only thing that makes a build ready."""
        smoke = plan.smoke
        self._emitter.emit(
            EventType.STEP_STARTED,
            f"smoke test: {smoke.name}",
            step=STEP_LABEL,
            data={"command": smoke.command},
        )

        started = time.perf_counter()
        result = await environment.run(smoke.command, timeout=smoke.timeout_seconds)
        duration = round(time.perf_counter() - started, 3)

        if result.exit_code != smoke.expect_exit_code:
            return SmokeTestResult.failure(
                smoke.name,
                smoke.command,
                f"exit {result.exit_code}, expected {smoke.expect_exit_code}",
                exit_code=result.exit_code,
                stdout=result.stdout[-2000:],
                stderr=result.stderr[-2000:],
                duration_seconds=duration,
            )

        # A zero exit code alone is not proof the job ran, so the marker is checked.
        if smoke.expect_stdout_contains and smoke.expect_stdout_contains not in result.stdout:
            return SmokeTestResult.failure(
                smoke.name,
                smoke.command,
                f"output did not contain {smoke.expect_stdout_contains!r}",
                exit_code=result.exit_code,
                stdout=result.stdout[-2000:],
                stderr=result.stderr[-2000:],
                duration_seconds=duration,
            )

        return SmokeTestResult(
            name=smoke.name,
            command=smoke.command,
            passed=True,
            exit_code=result.exit_code,
            stdout=result.stdout[-2000:],
            stderr=result.stderr[-2000:],
            duration_seconds=duration,
        )

    @staticmethod
    def _versions(attempt: BuildAttempt) -> dict[str, str]:
        """Pull recorded versions out of the step output.

        Reported so an experiment can cite the versions it actually measured.
        """
        versions: dict[str, str] = {}
        for step in attempt.steps:
            if step.name != "record versions":
                continue
            for line in step.output_tail.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] in {"python", "pyspark", "cassandra"}:
                    versions[parts[0]] = parts[1]
        return versions

    @staticmethod
    def _reported_version(artifact: BuildArtifact) -> str | None:
        """The application version this artifact provides.

        A recorded version if the build reported one; otherwise the commit for a
        source build, which *is* the version in that case. Never a guess -- an
        artifact with nothing to report says nothing.
        """
        for name in ("cassandra", "pyspark"):
            found = artifact.capabilities.versions.get(name)
            if found:
                return found
        if artifact.kind is BuildKind.SOURCE_BUILD and artifact.commit_sha:
            return artifact.commit_sha[:12]
        return None
