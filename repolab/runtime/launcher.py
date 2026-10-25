"""Bringing a :class:`RuntimePlan` up, one evidenced step at a time.

    PLANNING → PREPARING → INSTALLING → BUILDING → STARTING → HEALTH_CHECKING → RUNNING

Every step records what ran, how long it took and the tail of its output, so a
failure says what *actually* went wrong -- an exit code and a log line, a port
nothing listened on, a server bound to localhost -- rather than anything a model
supposes. A service is RUNNING only when its process is alive, its port is
listening on an address the sidecar can reach, and it answered an HTTP request.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable

import httpx
import structlog

from repolab.runtime.backend import DEPENDENCY_IMAGES, Limits, Listener, RunSpec, RuntimeBackend
from repolab.runtime.models import (
    DependencyState,
    EnvKind,
    Failure,
    HealthResult,
    RuntimeDependency,
    RuntimePlan,
    RuntimeState,
    RuntimeStatus,
    ServicePlan,
    ServiceState,
    StepRecord,
)
from repolab.runtime.node import allowed
from repolab.runtime.scan import API_BASE_VAR

log = structlog.get_logger(__name__)

ARCHIVE = "/work/repo.tar.gz"
ROOT = "/work/repo"

INSTALL_TIMEOUT = 1200.0
BUILD_TIMEOUT = 900.0
START_TIMEOUT = 300.0
HEALTH_TIMEOUT = 90.0
DEPENDENCY_TIMEOUT = 120.0
POLL_SECONDS = 2.0

Emit = Callable[[str, str, dict], None]
"""(kind, message, data): kind is ``step_started``, ``step_done``, ``log_line`` or ``failed``."""

Probe = Callable[[int, str], Awaitable[HealthResult]]
Save = Callable[[RuntimeState], None]


async def http_probe(host_port: int, path: str) -> HealthResult:
    """Any HTTP response proves a server is answering; the status is recorded."""
    url = f"http://127.0.0.1:{host_port}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return HealthResult(path=path, ok=False, detail=f"{type(exc).__name__}: {exc}"[:300])
    return HealthResult(
        path=path,
        status_code=response.status_code,
        ok=True,
        detail=f"HTTP {response.status_code}",
    )


def cache_tag(repo_url: str, commit: str, service: ServicePlan, adapter: str) -> str:
    slug = repo_url.rstrip("/").split("/")[-1].lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug)[:30] or "repo"
    return f"repolab/runtime-{slug}:{commit[:12]}-{service.name}-{adapter}"[:128].lower()


class Launcher:
    """Runs a plan on a backend. One instance per launch."""

    def __init__(
        self,
        backend: RuntimeBackend,
        *,
        emit: Emit,
        save: Save,
        probe: Probe = http_probe,
        origin: Callable[[str], str] | None = None,
        limits: Limits | None = None,
        poll_seconds: float = POLL_SECONDS,
        start_timeout: float = START_TIMEOUT,
        health_timeout: float = HEALTH_TIMEOUT,
    ) -> None:
        self._backend = backend
        self._emit = emit
        self._save = save
        self._probe = probe
        self._origin = origin or (lambda service: "")
        self._limits = limits or Limits()
        self._poll = poll_seconds
        self._start_timeout = start_timeout
        self._health_timeout = health_timeout

    async def launch(self, state: RuntimeState, plan: RuntimePlan, archive: bytes) -> RuntimeState:
        """Bring every planned service up. Never raises for a service's failure."""
        self._archive = archive
        state.services = [
            ServiceState(name=s.name, surfaces=s.surfaces, warnings=list(s.warnings))
            for s in plan.services
        ]
        state.dependencies = plan.dependencies
        if not self._backend.long_running:
            for service in state.services:
                self._fail(
                    state,
                    service,
                    RuntimeStatus.UNSUPPORTED,
                    f"the {self._backend.name} environment cannot keep a server running with a "
                    "reachable port",
                )
            state.status = state.overall()
            self._save(state)
            return state

        state.network = f"repolab-rt-{state.id}"
        await self._backend.create_network(state.network)
        provisioned = await self._provision(state, plan)

        # Backends first: a frontend may be configured with a backend's address.
        ordered = sorted(plan.services, key=lambda s: 0 if _is_api(s) else 1)
        for service in ordered:
            current = state.service(service.name)
            assert current is not None
            missing = [
                d
                for d in plan.dependencies
                if service.name in d.services
                and d.state is DependencyState.PROVISIONABLE
                and d.kind.value not in provisioned
            ]
            if missing:
                self._fail(
                    state,
                    current,
                    RuntimeStatus.DEPENDENCY_MISSING,
                    f"{missing[0].kind.value} could not be started for it",
                    evidence=missing[0].evidence,
                )
                continue
            try:
                await self._service(state, plan, service, current, provisioned)
            except Exception as exc:  # noqa: BLE001 - recorded on the service, never hidden
                log.warning("runtime.service_failed", service=service.name, error=str(exc))
                if not current.status.failed:
                    self._fail(state, current, RuntimeStatus.START_FAILED, str(exc)[:400])
        state.status = state.overall()
        self._save(state)
        return state

    # -- dependencies ---------------------------------------------------------

    async def _provision(self, state: RuntimeState, plan: RuntimePlan) -> dict[str, str]:
        """Start each provisionable database; returns kind -> connection string."""
        ready: dict[str, str] = {}
        for dependency in plan.dependencies:
            if dependency.state is not DependencyState.PROVISIONABLE:
                continue
            url = await self._start_dependency(state, dependency)
            if url is not None:
                ready[dependency.kind.value] = url
        return ready

    async def _start_dependency(
        self, state: RuntimeState, dependency: RuntimeDependency
    ) -> str | None:
        kind = dependency.kind.value
        image, readiness, template, env_template = DEPENDENCY_IMAGES[kind]
        password = secrets.token_hex(12)
        env = {k: v.format(password=password) for k, v in env_template.items()}
        name = f"repolab-rt-{state.id}-{kind}"
        self._emit("step_started", f"starting an ephemeral {kind}", {"dependency": kind})
        try:
            container = await self._backend.run_dependency(
                name, image, state.network or "", kind, env
            )
        except Exception as exc:  # noqa: BLE001
            self._emit("failed", f"{kind} could not start: {exc}", {"dependency": kind})
            return None
        state.dependency_containers.append(container)
        deadline = time.monotonic() + DEPENDENCY_TIMEOUT
        while time.monotonic() < deadline:
            result = await self._backend.exec(container, readiness, timeout=30)
            if result.exit_code == 0:
                self._emit("step_done", f"{kind} is ready", {"dependency": kind})
                return template.format(host=kind, password=password)
            await asyncio.sleep(self._poll)
        self._emit("failed", f"{kind} did not become ready", {"dependency": kind})
        return None

    # -- one service ------------------------------------------------------------

    async def _service(
        self,
        state: RuntimeState,
        plan: RuntimePlan,
        service: ServicePlan,
        current: ServiceState,
        provisioned: dict[str, str],
    ) -> None:
        commands = [c for c in (service.install, service.build) if c] + [service.start]
        refused = [c for c in commands if not allowed(c)]
        if refused:
            self._fail(
                state,
                current,
                RuntimeStatus.UNSUPPORTED,
                f"refused a command not in the plan's allowlist: {refused[0]}",
            )
            return

        workdir = f"{ROOT}/{service.working_directory}".rstrip("/")
        tag = cache_tag(state.repo_url, state.commit_sha, service, plan.adapter_version)
        self._set(state, current, RuntimeStatus.PREPARING, f"preparing {service.image}")

        if await self._backend.image_exists(tag):
            self._step(
                current, "install", "skipped", "reused the environment installed for this commit"
            )
        else:
            built = await self._build(state, current, service, workdir, tag)
            if not built:
                return

        self._set(state, current, RuntimeStatus.STARTING, f"starting: {service.start}")
        env = self._environment(state, plan, service, provisioned)
        container = await self._backend.run_service(
            f"repolab-rt-{state.id}-{service.name}",
            RunSpec(
                image=tag,
                command=service.start,
                cwd=workdir,
                env=env,
                network=state.network or "",
                alias=service.name,
                limits=self._limits,
            ),
        )
        current.container = container
        self._save(state)

        listener = await self._wait_for_port(state, current, service, container)
        if listener is None:
            return
        current.port = listener.port

        current.sidecar = f"repolab-rt-{state.id}-{service.name}-proxy"
        current.host_port = await self._backend.run_sidecar(
            current.sidecar, state.network or "", service.name, listener.port
        )
        self._set(state, current, RuntimeStatus.HEALTH_CHECKING, f"GET {service.health_path}")
        health = await self._health(current, service)
        current.health = health
        if not health.ok:
            current.log_tail = await self._backend.logs(container)
            self._fail(
                state,
                current,
                RuntimeStatus.HEALTH_CHECK_FAILED,
                f"nothing answered HTTP on port {listener.port}: {health.detail}",
                log_tail=current.log_tail,
            )
            return
        self._step(current, "health", "done", f"GET {health.path} → HTTP {health.status_code}")
        self._set(state, current, RuntimeStatus.RUNNING, f"{service.name} is running")

    async def _build(
        self,
        state: RuntimeState,
        current: ServiceState,
        service: ServicePlan,
        workdir: str,
        tag: str,
    ) -> bool:
        container = await self._backend.build_container(service.image, self._limits)
        try:
            await self._backend.upload(container, ARCHIVE, self._archive)
            prepare = (
                f"mkdir -p {ROOT} && tar -xzf {ARCHIVE} -C {ROOT} && rm {ARCHIVE} "
                f"&& chown -R node:node {ROOT}"
            )
            result = await self._backend.exec(container, prepare, user="root", timeout=600)
            if result.exit_code != 0:
                self._fail(
                    state,
                    current,
                    RuntimeStatus.BUILD_FAILED,
                    "the repository could not be unpacked",
                    log_tail=_tail(result),
                )
                return False

            for phase, status, command, timeout in (
                ("install", RuntimeStatus.INSTALLING, service.install, INSTALL_TIMEOUT),
                ("build", RuntimeStatus.BUILDING, service.build, BUILD_TIMEOUT),
            ):
                if not command:
                    continue
                self._set(state, current, status, command)
                started = time.monotonic()
                result = await self._backend.exec(
                    container, command, cwd=workdir, user="node", timeout=timeout,
                    env={"CI": "true", "npm_config_update_notifier": "false"},
                )  # fmt: skip
                seconds = time.monotonic() - started
                if result.exit_code != 0:
                    tail = _tail(result)
                    timed_out = result.exit_code == 124 and "timed out" in result.stderr
                    self._step(
                        current,
                        phase,
                        "failed",
                        command,
                        seconds=seconds,
                        log=tail,
                        command=command,
                    )
                    self._fail(
                        state,
                        current,
                        RuntimeStatus.TIMEOUT if timed_out else RuntimeStatus.BUILD_FAILED,
                        f"`{command}` exited with code {result.exit_code}",
                        log_tail=tail,
                    )
                    return False
                self._step(
                    current,
                    phase,
                    "done",
                    command,
                    seconds=seconds,
                    log=_tail(result, 6),
                    command=command,
                )
                self._emit(
                    "log_line", _tail(result, 3) or f"{command} finished", {"service": current.name}
                )
            await self._backend.commit(container, tag)
            return True
        finally:
            await self._backend.remove(container)

    def _environment(
        self,
        state: RuntimeState,
        plan: RuntimePlan,
        service: ServicePlan,
        provisioned: dict[str, str],
    ) -> dict[str, str]:
        env: dict[str, str] = {}
        api = next((s for s in plan.services if _is_api(s) and s.name != service.name), None)
        for var in service.env:
            if var.kind is EnvKind.GENERATED and var.value is not None:
                env[var.name] = var.value
            elif var.kind is EnvKind.SECRET:
                # Ephemeral, sandbox-only, never stored or logged.
                env[var.name] = secrets.token_urlsafe(24)
            elif var.kind is EnvKind.DEPENDENCY:
                for dependency in plan.dependencies:
                    if dependency.env_var == var.name and dependency.kind.value in provisioned:
                        env[var.name] = provisioned[dependency.kind.value]
            elif API_BASE_VAR.match(var.name) and api is not None:
                env[var.name] = self._origin(api.name)
        return env

    async def _wait_for_port(
        self, state: RuntimeState, current: ServiceState, service: ServicePlan, container: str
    ) -> Listener | None:
        deadline = time.monotonic() + self._start_timeout
        seen: list[Listener] = []
        while time.monotonic() < deadline:
            if not await self._backend.alive(container):
                current.log_tail = await self._backend.logs(container)
                self._fail(
                    state,
                    current,
                    RuntimeStatus.START_FAILED,
                    f"`{service.start}` exited before it listened on a port",
                    log_tail=current.log_tail,
                )
                return None
            seen = await self._backend.listeners(container)
            planned = [x for x in seen if x.port == service.port]
            reachable = [x for x in planned if not x.loopback]
            if reachable:
                current.port_source = service.port_source
                self._step(
                    current, "port", "done", f"listening on {reachable[0].address}:{service.port}"
                )
                return reachable[0]
            others = [x for x in seen if not x.loopback and x.port != service.port]
            if others and not planned and time.monotonic() > deadline - self._start_timeout / 2:
                chosen = others[0]
                current.port_source = (
                    f"found listening at run time (the plan expected {service.port}: "
                    f"{service.port_source})"
                )
                self._step(current, "port", "done", f"listening on {chosen.address}:{chosen.port}")
                return chosen
            await asyncio.sleep(self._poll)

        current.log_tail = await self._backend.logs(container)
        loopback = [x for x in seen if x.loopback]
        if loopback:
            self._fail(
                state,
                current,
                RuntimeStatus.PORT_NOT_FOUND,
                f"it listens only on {loopback[0].address}:{loopback[0].port}, which nothing "
                "outside its container can reach, and its start command offers no way to change "
                "that",
                log_tail=current.log_tail,
            )
        else:
            self._fail(
                state,
                current,
                RuntimeStatus.PORT_NOT_FOUND,
                f"nothing listened on port {service.port} ({service.port_source}) within "
                f"{self._start_timeout:.0f}s",
                log_tail=current.log_tail,
            )
        return None

    async def _health(self, current: ServiceState, service: ServicePlan) -> HealthResult:
        assert current.host_port is not None
        deadline = time.monotonic() + self._health_timeout
        result = HealthResult(path=service.health_path, detail="not checked")
        while time.monotonic() < deadline:
            result = await self._probe(current.host_port, service.health_path)
            if result.ok:
                return result
            await asyncio.sleep(self._poll)
        return result

    # -- bookkeeping -------------------------------------------------------------

    _archive: bytes = b""

    def _set(
        self, state: RuntimeState, current: ServiceState, status: RuntimeStatus, detail: str
    ) -> None:
        current.status = status
        state.status = state.overall()
        self._emit(
            "step_started" if status is not RuntimeStatus.RUNNING else "step_done",
            f"{current.name}: {detail}",
            {"service": current.name, "status": status.value},
        )
        self._save(state)

    def _step(
        self,
        current: ServiceState,
        name: str,
        status: str,
        detail: str,
        *,
        seconds: float = 0.0,
        log: str = "",
        command: str | None = None,
    ) -> None:
        current.steps.append(
            StepRecord(
                name=name,
                status=status,
                detail=detail,
                seconds=round(seconds, 1),
                log_tail=log,
                command=command,
            )
        )

    def _fail(
        self,
        state: RuntimeState,
        current: ServiceState,
        status: RuntimeStatus,
        reason: str,
        *,
        evidence: list | None = None,
        log_tail: str = "",
    ) -> None:
        current.status = status
        current.failure = Failure(
            status=status,
            reason=reason,
            evidence=evidence or [],
            log_tail=log_tail[-4000:],
            next_step=_NEXT_STEP.get(status),
        )
        state.status = state.overall()
        self._emit(
            "failed", f"{current.name}: {reason}", {"service": current.name, "status": status.value}
        )
        self._save(state)


_NEXT_STEP = {
    RuntimeStatus.BUILD_FAILED: (
        "Read the install log below: the failing package and its error are usually in the "
        "last lines."
    ),
    RuntimeStatus.TIMEOUT: "The install ran past RepoLab's limit; a cached retry may be faster.",
    RuntimeStatus.START_FAILED: "The server stopped on its own; its last log lines say why.",
    RuntimeStatus.PORT_NOT_FOUND: (
        "Check which port the start script uses; RepoLab sets PORT and HOST."
    ),
    RuntimeStatus.HEALTH_CHECK_FAILED: (
        "The process is listening but not answering HTTP; it may not be a web server."
    ),
    RuntimeStatus.DEPENDENCY_MISSING: (
        "Retry; if the database image cannot be pulled, nothing that needs it can start."
    ),
}


async def stop(state: RuntimeState, backend: RuntimeBackend) -> RuntimeState:
    """Remove every container, sidecar and network this runtime created."""
    for service in state.services:
        for container in (service.sidecar, service.container):
            if container:
                await backend.remove(container)
        if service.status.active:
            service.status = RuntimeStatus.STOPPED
    for container in state.dependency_containers:
        await backend.remove(container)
    if state.network:
        await backend.remove_network(state.network)
    state.status = RuntimeStatus.STOPPED
    return state


def _is_api(service: ServicePlan) -> bool:
    return "api" in service.surfaces


def _tail(result: object, lines: int = 25) -> str:
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    text = (stdout + "\n" + stderr).strip()
    return "\n".join(text.splitlines()[-lines:])
