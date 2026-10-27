"""Preparing experiences, running what people try in them, and explaining runs.

The worker orchestrates; containers execute. Each task emits progress on the
session's job stream and writes the session's state to Postgres -- the source of
truth a refreshed browser reconnects to.

    prepare   the chosen rung of the ladder: a cached build, a source build of
              the commit, an official release, or a generated Mini Lab -- recorded
              on the session, never switched silently
    run       one action in a fresh, network-less container from that runtime
    explain   Super, from the run's own evidence, verified like Ask RepoLab
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session

from repolab.config import get_settings
from repolab.db import build_engine, build_session_factory, session_scope
from repolab.db.jobs import get_job, mark_finished, mark_running
from repolab.db.store import load_architecture, load_manifest
from repolab.events import DbEventEmitter, EventType, JobStatus
from repolab.events.liveness import ActivitySink, NoLiveness, liveness_for, session_key
from repolab.experiences import sessions as store
from repolab.experiences.adapters import spark as spark_adapter
from repolab.experiences.adapters.library import SOURCE_ROOT as LIBRARY_ROOT
from repolab.experiences.facts import python_image
from repolab.experiences.model import Strategy
from repolab.failures import describe, explain

log = structlog.get_logger(__name__)

STEP = "experience"

#: A Spark source build of a large commit, on a laptop-sized VM.
SOURCE_BUILD_TIMEOUT = 3 * 3600.0
LIBRARY_INSTALL_TIMEOUT = 1800.0

#: The only build command a Spark source build may run: the README's own form.
_MVN = re.compile(r"^\./build/mvn( -[A-Za-z][\w.=:,-]*)+( clean)? package$")

SMOKE_CODE = "result = spark.range(1000).count()"

#: Maven's own heap during a Spark source build (the forked compiler keeps its own).
MAVEN_HEAP = "1500m"


class _Context:
    def __init__(self, job_id: str) -> None:
        settings = get_settings()
        self.settings = settings
        self.engine = build_engine(settings)
        self.factory = build_session_factory(self.engine)
        self.job_id = job_id

        def sessions() -> AbstractContextManager[Session]:
            return session_scope(self.engine)

        with self.factory() as session:
            job = get_job(session, job_id)
            start_seq = job.last_seq if job is not None else 0
            mark_running(session, job_id)
            session.commit()
        self.emitter = DbEventEmitter(job_id, sessions, start_seq=start_seq)
        self.liveness: ActivitySink = NoLiveness()

    def emit(self, kind: EventType, message: str, **data: Any) -> None:
        self.emitter.emit(kind, message, step=STEP, data=data)

    def finish(self, status: JobStatus, error: str | None = None) -> None:
        with self.factory() as session:
            mark_finished(session, self.job_id, status=status, error=error)
            session.commit()
        self.engine.dispose()


def _router(settings: Any) -> Any:
    if settings.nebius_api_key is None or not any(settings.configured_models.values()):
        return None
    from repolab.llm import build_router

    return build_router(settings)


def _guard(task):
    """Run *task*, recording any failure on the session and the job."""

    def wrapper(job_id: str, session_id: str, *args: Any) -> str:
        context = _Context(job_id)
        # Preparation (no extra args) can run for many minutes -- an image pull,
        # a source build -- so it reports a heartbeat and its current step.
        if not args:
            context.liveness = liveness_for(session_key(session_id))
        try:
            with context.liveness:
                outcome = asyncio.run(task(context, session_id, *args))
        except Exception as exc:
            failure = explain(exc)
            context.emit(EventType.FAILED, failure.summary, **failure.model_dump_public())
            with context.factory() as session:
                loaded = store.load_session(session, session_id)
                if loaded is not None:
                    _, state = loaded
                    if not args:  # a failed preparation fails the session
                        state.failure = failure.summary
                        store.save_state(session, session_id, state, status=store.FAILED)
                    session.commit()
            context.finish(JobStatus.FAILED, describe(exc))
            raise
        context.finish(JobStatus.COMPLETED)
        return outcome

    wrapper.__name__ = task.__name__
    return wrapper


# -- prepare -----------------------------------------------------------------------


async def _prepare(context: _Context, session_id: str) -> str:
    with context.factory() as session:
        record, state = store.load_session(session, session_id)  # type: ignore[misc]
        manifest = load_manifest(session, record.repo_url)
        adapter, strategy = record.adapter, record.strategy
        repo_url, commit = record.repo_url, record.commit_sha
    if manifest is None:
        raise RuntimeError("the repository must be investigated first")

    def save(status: str | None = None) -> None:
        with context.factory() as session:
            store.save_state(session, session_id, state, status=status)
            session.commit()

    def step(name: str, detail: str, status: str = "done") -> None:
        context.liveness.set_activity("experience", name)
        state.steps.append({"name": name, "status": status, "detail": detail})
        context.emit(EventType.STEP_DONE if status == "done" else EventType.STEP_STARTED, detail)
        save()

    if adapter == "minilab":
        from repolab.builds.minilab import generate, validate

        step("generate", "writing a Mini Lab from the repository's own concepts", "started")
        lab = await generate(manifest, router=_router(context.settings))
        problem = validate(lab)
        if problem is not None:
            state.failure = str(problem)
            save(store.FAILED)
            return "failed"
        state.minilab = lab.model_dump(mode="json")
        state.runtime.label = "Simulated / educational -- does not run this repository"
        step("ready", f"Mini Lab ready: {len(lab.entries)} grounded commands")
        save(store.READY)
        return "ready"

    if adapter == "spark" and strategy == Strategy.OFFICIAL_RELEASE.value:
        image = spark_adapter.OFFICIAL_IMAGE
        step("runtime", f"selected the official release {image}")
        await _ensure(context, image)
        state.runtime.image = image
        state.runtime.spark_submit = spark_adapter.OFFICIAL_SPARK_SUBMIT
        return await _spark_smoke(context, state, save, step, official=True, manifest=manifest)

    if adapter == "spark" and strategy == Strategy.SOURCE_BUILD.value:
        image = await _source_build(context, repo_url, commit, manifest, step)
        if image is None:
            state.failure = "the source build of this commit failed; its log is below"
            save(store.FAILED)
            return "failed"
        state.runtime.image = image
        state.runtime.spark_submit = f"{spark_adapter.SOURCE_ROOT}/bin/spark-submit"
        return await _spark_smoke(context, state, save, step, official=False, manifest=manifest)

    if adapter == "library":
        image = await _library_build(context, repo_url, commit, manifest, step)
        if image is None:
            state.failure = "pip could not install this package; its log is below"
            save(store.FAILED)
            return "failed"
        state.runtime.image = image
        state.runtime.python = "python"
        state.runtime.label = f"{manifest.facts.python.distribution} installed from this commit"  # type: ignore[union-attr]
        from repolab.experiences.library_runner import run_snippet
        from repolab.sandbox.docker import DockerEnvironment

        smoke = await run_snippet(
            DockerEnvironment(hardened=True),
            image=image,
            code=f"import {manifest.facts.python.import_name}; print('ok')",  # type: ignore[union-attr]
        )
        if not smoke.ok:
            state.failure = "the installed package cannot be imported"
            state.failure_log = (smoke.stderr or smoke.stdout)[-4000:]
            save(store.FAILED)
            return "failed"
        step("ready", "the package imports in a network-less sandbox")
        save(store.READY)
        return "ready"

    raise RuntimeError(f"no preparation for {adapter}/{strategy}")


async def _spark_smoke(context, state, save, step, *, official: bool, manifest) -> str:
    from repolab.experiences.spark.snippet import SnippetRunner, sample_dataset
    from repolab.sandbox.docker import DockerEnvironment

    state.dataset = sample_dataset()[1].model_dump(mode="json")
    step("smoke", "running a tiny Spark job to prove the runtime works", "started")
    result = await SnippetRunner(DockerEnvironment(hardened=True)).run(
        image=state.runtime.image,
        code=SMOKE_CODE,
        spark_submit=state.runtime.spark_submit,
    )
    if not result.ok or result.value != 1000:
        state.failure = "the runtime did not complete a tiny Spark job"
        state.failure_log = result.error or result.stdout_tail
        save(store.FAILED)
        return "failed"
    state.runtime.version = result.spark_version
    facts = manifest.facts.spark if manifest.facts else None
    if official:
        state.runtime.label = f"Official Apache Spark {result.spark_version}"
        state.runtime.disclosure = (
            f"This is an official Spark {result.spark_version} release, not this commit"
            + (f" ({facts.version})" if facts and facts.version else "")
            + "."
        )
    else:
        state.runtime.label = f"Spark {result.spark_version}, built from this commit"
    step("ready", f"Spark {result.spark_version} answered: spark.range(1000).count() = 1000")
    save(store.READY)
    return "ready"


async def _ensure(context: _Context, image: str) -> None:
    from repolab.sandbox.docker import ensure_image

    context.liveness.set_activity("experience", "image")
    context.emit(EventType.STEP_STARTED, f"preparing the image {image}")
    status = await asyncio.to_thread(ensure_image, image)
    if not status.ready:
        raise RuntimeError(status.describe())


async def _clone_archive(repo_url: str, commit: str) -> tuple[bytes, str]:
    from repolab.builds.resolver import archive_repository
    from repolab.ingest.clone import TemporaryClone
    from repolab.ingest.limits import DEFAULT_LIMITS
    from repolab.ingest.url import validate_repo_url

    with TemporaryClone(validate_repo_url(repo_url), limits=DEFAULT_LIMITS) as clone:
        return archive_repository(Path(clone.path), limit=512 * 1024 * 1024), clone.commit_sha


async def _in_container_build(
    context: _Context,
    *,
    image: str,
    memory: str,
    archive: bytes,
    root: str,
    commands: list[tuple[str, str, float]],
    tag: str,
    step,
) -> tuple[bool, str]:
    """Unpack into *root*, run each (name, command, timeout), commit to *tag*.

    Each command runs in the background inside the container and its log is
    tailed, so a build that takes an hour reports where it is, not just that it
    is still going.
    """
    from repolab.sandbox.docker_provider import DockerSandboxProvider

    provider = DockerSandboxProvider(memory=memory)
    handle = await provider.create(image)
    try:
        handle = await provider.upload(handle, {f"{root}.tar.gz": archive})
        _, result = await provider.execute(
            handle, f"mkdir -p {root} && tar -xzf {root}.tar.gz -C {root} && rm {root}.tar.gz"
        )
        if result.exit_code != 0:
            return False, result.stderr[-3000:]
        for name, command, timeout in commands:
            step(name, f"running `{command}`", "started")
            launched = (
                f"cd {root} && nohup sh -c '{command} > /opt/repolab/{name}.log 2>&1; "
                f"echo $? > /opt/repolab/{name}.exit' >/dev/null 2>&1 &"
            )
            await provider.execute(handle, f"mkdir -p /opt/repolab && {launched}")
            started = time.monotonic()
            last = ""
            while True:
                await asyncio.sleep(15)
                _, done = await provider.execute(
                    handle, f"cat /opt/repolab/{name}.exit 2>/dev/null"
                )
                _, tail = await provider.execute(
                    handle,
                    f"grep -E '\\[INFO\\] Building |Successfully|Installing|ERROR' "
                    f"/opt/repolab/{name}.log | tail -1",
                )
                line = (tail.stdout or "").strip()[:200]
                if line and line != last:
                    context.emit(EventType.LOG_LINE, line)
                    last = line
                if (done.stdout or "").strip():
                    break
                if time.monotonic() - started > timeout:
                    return False, f"`{command}` ran past {timeout / 60:.0f} minutes"
            if (done.stdout or "").strip() != "0":
                _, log_tail = await provider.execute(handle, f"tail -60 /opt/repolab/{name}.log")
                return False, log_tail.stdout[-6000:]
            minutes = (time.monotonic() - started) / 60
            step(name, f"`{command}` finished in {minutes:.0f} min")
        await provider.checkpoint(handle, tag=tag)
        return True, ""
    finally:
        await provider.terminate(handle)


async def _source_build(context, repo_url, commit, manifest, step) -> str | None:
    facts = manifest.facts.spark if manifest.facts else None
    if facts is None or not facts.build_command or not _MVN.match(facts.build_command):
        raise RuntimeError("the README's build command is missing or not one RepoLab will run")
    with context.factory() as session:
        existing = store.find_build(session, repo_url, commit, "spark", "source_build")
        if existing is not None and existing.status == "ready" and existing.image:
            step("cache", f"reusing the source build of {commit[:12]}")
            return existing.image
        store.record_build(
            session, repo_url=repo_url, commit_sha=commit, adapter="spark",
            strategy="source_build", status="building", job_id=context.job_id,
        )  # fmt: skip
        session.commit()

    needed, available = _memory_check(facts)
    if needed and available and available < needed:
        reason = (
            f"Building this commit needs about {needed:.0f} GB of memory: the build forks its "
            f"Scala compiler with {facts.compiler_heap_gb:g} GB of heap "
            f"(pom.xml:{facts.compiler_heap_ref.start_line if facts.compiler_heap_ref else '?'}), "
            "and compiling it was killed for memory (exit 137) in a 7 GB sandbox. Docker has "
            f"{available:.1f} GB in total. Give Docker at least {needed + 2:.0f} GB "
            "(Docker Desktop → Settings → Resources) to build it; the official runtime "
            "works meanwhile."
        )
        with context.factory() as session:
            store.record_build(
                session, repo_url=repo_url, commit_sha=commit, adapter="spark",
                strategy="source_build", status="failed", detail={"log_tail": reason},
            )  # fmt: skip
            session.commit()
        step("preflight", reason, "failed")
        context.emit(EventType.LOG_LINE, reason)
        return None
    step("clone", f"cloning {repo_url} to build it", "started")
    archive, cloned = await _clone_archive(repo_url, commit)
    if cloned != commit:
        step(
            "clone",
            f"the default branch moved: building {cloned[:12]} (investigated {commit[:12]})",
        )
    java = facts.java_version or "17"
    command = facts.build_command
    if not _MVN.match(command):
        raise RuntimeError(f"refusing an unexpected build command: {command}")
    # The README's command, unchanged. One disclosed setting: Maven's own heap is
    # capped so the Scala compiler the build forks (with its own -Xmx, from the
    # repository's pom) fits in the sandbox beside it.
    step(
        "plan",
        f"build with the README's `{command}` (MAVEN_OPTS -Xmx{MAVEN_HEAP}: Maven's own "
        "heap, so the build's forked Scala compiler fits in the sandbox)",
    )
    tag = f"repolab/experience-spark:{cloned[:12]}-source"
    ok, log_tail = await _in_container_build(
        context,
        image=f"eclipse-temurin:{java}-jdk",
        memory=_container_memory(facts),
        archive=archive,
        root=spark_adapter.SOURCE_ROOT,
        commands=[
            ("python", "apt-get update -qq && apt-get install -y -qq python3", 900.0),
            (
                "build",
                f'MAVEN_OPTS="-Xss128m -Xmx{MAVEN_HEAP} -XX:ReservedCodeCacheSize=128m" {command}',
                SOURCE_BUILD_TIMEOUT,
            ),
        ],
        tag=tag,
        step=step,
    )
    if not ok and re.search(r"Exit value: 137|exit code 137|\bKilled\b", log_tail):
        # The kernel's OOM killer, not a code error: say so in plain words, with the
        # build's own memory settings, and keep the log underneath.
        heap = facts.compiler_heap_gb or 4
        log_tail = (
            f"The build was killed for running out of memory (exit 137) in a "
            f"{_container_memory(facts)} sandbox. It forks its Scala compiler with "
            f"{heap:g} GB of heap (pom.xml), beside Maven. Give Docker more memory "
            "(Docker Desktop → Settings → Resources, at least "
            f"{heap + BUILD_OVERHEAD_GB + 2:.0f} GB) to build this commit; the official "
            "runtime works meanwhile.\n\n" + log_tail
        )
    with context.factory() as session:
        store.record_build(
            session, repo_url=repo_url, commit_sha=commit, adapter="spark", strategy="source_build",
            status="ready" if ok else "failed", image=tag if ok else None,
            detail={"log_tail": log_tail, "built_commit": cloned, "java": java},
        )  # fmt: skip
        session.commit()
    if not ok:
        context.emit(EventType.LOG_LINE, log_tail[-1500:])
        return None
    return tag


#: Memory beside the forked compiler: Maven's capped heap, the compiler's
#: metaspace, the OS. Measured, not estimated: building apache/spark's SQL module
#: (whose pom forks scalac with -Xms4g -Xmx4g) was killed with exit 137 in a 6 GB
#: and again in a 7 GB container with Maven capped at 1.5 GB.
BUILD_OVERHEAD_GB = 4.0


def _memory_check(facts) -> tuple[float | None, float | None]:
    """(needed, available) in GB, from the build's own settings and Docker's VM."""
    import subprocess

    if not facts.compiler_heap_gb:
        return None, None
    try:
        out = subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}}"],
            capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
        available = int((out.stdout or "0").strip()) / 1024**3
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None
    return facts.compiler_heap_gb + BUILD_OVERHEAD_GB, available or None


def _container_memory(facts) -> str:
    heap = facts.compiler_heap_gb or 4.0
    return f"{heap + BUILD_OVERHEAD_GB:g}g"


async def _library_build(context, repo_url, commit, manifest, step) -> str | None:
    package = manifest.facts.python if manifest.facts else None
    if package is None:
        raise RuntimeError("no Python package was found in this repository")
    with context.factory() as session:
        existing = store.find_build(session, repo_url, commit, "library", "source_build")
        if existing is not None and existing.status == "ready" and existing.image:
            step("cache", f"reusing {package.distribution} installed from {commit[:12]}")
            return existing.image
    image, why = python_image(package.requires_python)
    step("runtime", f"{image} ({why})")
    archive, cloned = await _clone_archive(repo_url, commit)
    slug = re.sub(r"[^a-z0-9]+", "-", package.distribution.lower())[:40]
    tag = f"repolab/experience-lib-{slug}:{cloned[:12]}"
    ok, log_tail = await _in_container_build(
        context,
        image=image,
        memory="2g",
        archive=archive,
        root=LIBRARY_ROOT,
        commands=[("install", "pip install --no-cache-dir .", LIBRARY_INSTALL_TIMEOUT)],
        tag=tag,
        step=step,
    )
    with context.factory() as session:
        store.record_build(
            session, repo_url=repo_url, commit_sha=commit, adapter="library",
            strategy="source_build", status="ready" if ok else "failed",
            image=tag if ok else None, detail={"log_tail": log_tail},
        )  # fmt: skip
        session.commit()
    return tag if ok else None


# -- run ----------------------------------------------------------------------------


async def _run(context: _Context, session_id: str, run_id: str) -> str:
    from repolab.sandbox.docker import DockerEnvironment

    with context.factory() as session:
        record, state = store.load_session(session, session_id)  # type: ignore[misc]
        adapter = record.adapter
        architecture = load_architecture(session, record.repo_url)
    run = state.run(run_id)
    if run is None or state.runtime.image is None:
        raise RuntimeError("this session has no runtime to run in")

    context.emit(EventType.STEP_STARTED, "running your code in a fresh, network-less sandbox")
    if adapter == "spark":
        from repolab.experiences.spark.snippet import SnippetConfig, SnippetRunner
        from repolab.experiences.spark.understanding import map_run

        result = await SnippetRunner(DockerEnvironment(hardened=True)).run(
            image=state.runtime.image,
            code=run.code,
            spark_submit=state.runtime.spark_submit or spark_adapter.OFFICIAL_SPARK_SUBMIT,
            config=SnippetConfig(shuffle_partitions=run.shuffle_partitions or 4),
        )
        run.result = result.model_dump(mode="json")
        run.mapping = [step.model_dump() for step in map_run(run.result, architecture)]
        ok = result.ok
        if ok and result.measurements:
            m = result.measurements
            context.emit(
                EventType.STEP_DONE,
                f"Spark ran {result.jobs} job(s), {m.stage_count} stage(s), {m.task_count} "
                f"task(s) in {(m.total_runtime_ms or 0) / 1000:.1f} s (from its event log)",
            )
    else:
        from repolab.experiences.library_runner import run_snippet

        outcome = await run_snippet(
            DockerEnvironment(hardened=True), image=state.runtime.image, code=run.code
        )
        run.result = outcome.model_dump(mode="json")
        ok = outcome.ok
        context.emit(EventType.STEP_DONE, f"python exited {outcome.exit_code}")

    run.status = "done" if ok else "failed"
    with context.factory() as session:
        _, latest = store.load_session(session, session_id)  # type: ignore[misc]
        latest.runs = [run if r.id == run_id else r for r in latest.runs]
        store.save_state(session, session_id, latest)
        session.commit()
    return run.status


async def _explain(context: _Context, session_id: str, run_id: str) -> str:
    from repolab.experiences.explain import evidence_for, explain_run
    from repolab.experiences.spark.understanding import run_measurements

    with context.factory() as session:
        _, state = store.load_session(session, session_id)  # type: ignore[misc]
    run = state.run(run_id)
    if run is None or run.result is None:
        raise RuntimeError("nothing has run to explain")
    router = _router(context.settings)
    if router is None:
        run.explanation_error = "No model is configured, so RepoLab cannot explain this run."
    else:
        context.emit(EventType.STEP_STARTED, "explaining the run from its own evidence")
        evidence = evidence_for(
            run_measurements(run.result), run.mapping, run.result.get("stdout_tail", "")
        )
        label = state.runtime.label + (
            f" ({state.runtime.disclosure})" if state.runtime.disclosure else ""
        )
        verified = await explain_run(
            router=router,
            code=run.code,
            result=run.result,
            mapping=run.mapping,
            measurements=evidence,
            runtime_label=label,
        )
        run.explanation = verified.model_dump(mode="json")
    with context.factory() as session:
        _, latest = store.load_session(session, session_id)  # type: ignore[misc]
        latest.runs = [run if r.id == run_id else r for r in latest.runs]
        store.save_state(session, session_id, latest)
        session.commit()
    return "explained"


prepare_experience = _guard(_prepare)
run_experience = _guard(_run)
explain_experience = _guard(_explain)
