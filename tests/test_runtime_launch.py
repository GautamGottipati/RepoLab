"""The launch lifecycle against a scripted backend. Offline; nothing runs.

RUNNING has to be earned: an install that worked, a process that stayed up, a
port reachable from outside its container, and an HTTP answer. Each way of not
earning it ends in its own state, with the reason and the log it came from.
"""

from __future__ import annotations

import asyncio

import pytest

from repolab.interfaces import CommandResult
from repolab.runtime.backend import Listener, parse_proc_net
from repolab.runtime.fake import FakeRuntimeBackend, ServiceScript
from repolab.runtime.launcher import Launcher, cache_tag, stop
from repolab.runtime.models import (
    DependencyKind,
    DependencyState,
    EnvKind,
    EnvVar,
    HealthResult,
    RuntimeDependency,
    RuntimePlan,
    RuntimeState,
    RuntimeStatus,
    ServicePlan,
)


def service(name: str, surfaces: list[str], port: int, **extra) -> ServicePlan:
    return ServicePlan(
        name=name,
        surfaces=surfaces,
        working_directory=extra.pop("working_directory", name),
        image="node:20",
        image_reason="test",
        package_manager="npm",
        install="npm ci --no-audit --no-fund",
        start=extra.pop("start", "npm run start"),
        script="start",
        port=port,
        port_source="test",
        env=extra.pop(
            "env",
            [EnvVar(name="PORT", kind=EnvKind.GENERATED, value=str(port))],
        ),
        **extra,
    )


async def answers(port: int, path: str) -> HealthResult:
    return HealthResult(path=path, status_code=200, ok=True, detail="HTTP 200")


async def silent(port: int, path: str) -> HealthResult:
    return HealthResult(path=path, ok=False, detail="ConnectError")


def launch(
    plan: RuntimePlan, backend: FakeRuntimeBackend, probe=answers, **kw
) -> tuple[RuntimeState, list]:
    events: list[tuple[str, str, dict]] = []
    saved: list[RuntimeState] = []
    launcher = Launcher(
        backend,
        emit=lambda kind, message, data: events.append((kind, message, data)),
        save=lambda state: saved.append(state.model_copy(deep=True)),
        probe=probe,
        origin=lambda name: f"http://rt-x-{name}.localhost:8000",
        poll_seconds=0,
        start_timeout=kw.pop("start_timeout", 0.2),
        health_timeout=0.05,
    )
    state = RuntimeState(
        id="abcdef123456", repo_url="https://github.com/e/app", commit_sha="c" * 40
    )
    return asyncio.run(launcher.launch(state, plan, b"archive")), events


def listening(port: int, address: str = "0.0.0.0") -> ServiceScript:
    return ServiceScript(listeners=[Listener(address, port)])


class TestGoingLive:
    def test_a_simple_express_api(self) -> None:
        backend = FakeRuntimeBackend(services={"api": listening(3000)})
        state, events = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        (api,) = state.services
        assert api.status is RuntimeStatus.RUNNING
        assert state.status is RuntimeStatus.RUNNING
        assert api.host_port is not None and api.health.status_code == 200
        assert [s.name for s in api.steps] == ["install", "port", "health"]
        # It ran on the runtime's own network, from the installed image, as the
        # unprivileged user, in its own directory.
        (spec,) = backend.started
        assert spec.network == "repolab-rt-abcdef123456"
        assert spec.image.startswith("repolab/runtime-app:") and spec.user == "node"
        assert spec.cwd == "/work/repo/api"
        assert backend.sidecars[api.sidecar] == ("api", 3000)
        assert any("api is running" in message for _, message, _ in events)

    def test_a_web_app_and_a_backend_start_backend_first_and_are_wired(self) -> None:
        web = service(
            "web",
            ["web"],
            4200,
            env=[
                EnvVar(name="PORT", kind=EnvKind.GENERATED, value="4200"),
                EnvVar(name="VITE_API_URL", kind=EnvKind.REQUIRED),
            ],
        )
        plan = RuntimePlan(services=[web, service("backend", ["api"], 3000)])
        backend = FakeRuntimeBackend(services={"web": listening(4200), "backend": listening(3000)})
        state, _ = launch(plan, backend)
        assert [s.alias for s in backend.started] == ["backend", "web"]
        assert backend.started[1].env["VITE_API_URL"] == "http://rt-x-backend.localhost:8000"
        assert {s.name: s.status for s in state.services} == {
            "web": RuntimeStatus.RUNNING,
            "backend": RuntimeStatus.RUNNING,
        }

    def test_multiple_ports_pick_the_planned_one(self) -> None:
        script = ServiceScript(listeners=[Listener("0.0.0.0", 9229), Listener("0.0.0.0", 3000)])
        backend = FakeRuntimeBackend(services={"api": script})
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        assert state.services[0].port == 3000

    def test_a_port_found_at_run_time_is_adopted_and_says_so(self) -> None:
        backend = FakeRuntimeBackend(services={"api": listening(8080)})
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        api = state.services[0]
        assert api.status is RuntimeStatus.RUNNING and api.port == 8080
        assert "found listening at run time" in (api.port_source or "")

    def test_a_cached_install_is_reused(self) -> None:
        plan = RuntimePlan(services=[service("api", ["api"], 3000)])
        tag = cache_tag(
            "https://github.com/e/app", "c" * 40, plan.services[0], plan.adapter_version
        )
        backend = FakeRuntimeBackend(services={"api": listening(3000)}, images={tag})
        state, _ = launch(plan, backend)
        assert state.services[0].steps[0].status == "skipped"
        assert not any("npm ci" in command for _, command, _, _ in backend.executed)

    def test_secrets_are_ephemeral_and_never_saved(self) -> None:
        api = service(
            "api",
            ["api"],
            3000,
            env=[EnvVar(name="JWT_SECRET", kind=EnvKind.SECRET)],
        )
        backend = FakeRuntimeBackend(services={"api": listening(3000)})
        state, events = launch(RuntimePlan(services=[api]), backend)
        secret = backend.started[0].env["JWT_SECRET"]
        assert len(secret) >= 24
        assert secret not in state.model_dump_json()
        assert all(secret not in message for _, message, _ in events)


class TestDependencies:
    def test_mongodb_is_provisioned_and_its_url_set(self) -> None:
        api = service(
            "api",
            ["api"],
            3000,
            env=[EnvVar(name="MONGODB_URI", kind=EnvKind.DEPENDENCY)],
        )
        dependency = RuntimeDependency(
            kind=DependencyKind.MONGODB,
            state=DependencyState.PROVISIONABLE,
            env_var="MONGODB_URI",
            reason="reads it",
            services=["api"],
        )
        backend = FakeRuntimeBackend(services={"api": listening(3000)})
        state, _ = launch(RuntimePlan(services=[api], dependencies=[dependency]), backend)
        assert backend.dependencies == [("mongodb", "mongo:7")]
        assert backend.started[0].env["MONGODB_URI"] == "mongodb://mongodb:27017/app"
        assert state.services[0].status is RuntimeStatus.RUNNING

    def test_a_dependency_that_never_becomes_ready_blocks_its_service(self) -> None:
        dependency = RuntimeDependency(
            kind=DependencyKind.REDIS,
            state=DependencyState.PROVISIONABLE,
            env_var="REDIS_URL",
            reason="reads it",
            services=["api"],
        )
        backend = FakeRuntimeBackend(
            commands={"redis-cli ping": CommandResult(exit_code=1, stdout="", stderr="refused")},
        )
        import repolab.runtime.launcher as launcher_module

        original = launcher_module.DEPENDENCY_TIMEOUT
        launcher_module.DEPENDENCY_TIMEOUT = 0.01
        try:
            state, _ = launch(
                RuntimePlan(services=[service("api", ["api"], 3000)], dependencies=[dependency]),
                backend,
            )
        finally:
            launcher_module.DEPENDENCY_TIMEOUT = original
        api = state.services[0]
        assert api.status is RuntimeStatus.DEPENDENCY_MISSING
        assert backend.started == []

    def test_an_external_database_does_not_stop_the_start_but_is_warned(self) -> None:
        dependency = RuntimeDependency(
            kind=DependencyKind.MONGODB,
            state=DependencyState.EXTERNAL_REQUIRED,
            reason="hard-coded external host",
            services=["api"],
        )
        api = service("api", ["api"], 3000)
        api.warnings = []
        backend = FakeRuntimeBackend(services={"api": listening(3000)})
        state, _ = launch(RuntimePlan(services=[api], dependencies=[dependency]), backend)
        assert state.services[0].status is RuntimeStatus.RUNNING
        assert backend.dependencies == []


class TestFailures:
    def test_build_failure_keeps_the_exit_code_and_log(self) -> None:
        backend = FakeRuntimeBackend(
            commands={"npm ci": CommandResult(exit_code=1, stdout="", stderr="npm ERR! gyp failed")}
        )
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        api = state.services[0]
        assert api.status is RuntimeStatus.BUILD_FAILED
        assert "exited with code 1" in api.failure.reason
        assert "gyp failed" in api.failure.log_tail
        assert api.failure.next_step
        assert backend.started == []

    def test_an_install_timeout_is_a_timeout(self) -> None:
        backend = FakeRuntimeBackend(
            commands={
                "npm ci": CommandResult(exit_code=124, stdout="", stderr="timed out after 1200s")
            }
        )
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        assert state.services[0].status is RuntimeStatus.TIMEOUT

    def test_startup_failure_shows_the_servers_own_last_words(self) -> None:
        backend = FakeRuntimeBackend(
            services={"api": ServiceScript(exits=True, logs="Error: Cannot find module 'dotenv'")}
        )
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        api = state.services[0]
        assert api.status is RuntimeStatus.START_FAILED
        assert "Cannot find module" in api.failure.log_tail

    def test_a_server_bound_to_localhost_is_named_as_such(self) -> None:
        backend = FakeRuntimeBackend(services={"api": listening(3000, "127.0.0.1")})
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        api = state.services[0]
        assert api.status is RuntimeStatus.PORT_NOT_FOUND
        assert "listens only on 127.0.0.1:3000" in api.failure.reason

    def test_nothing_listening_is_port_not_found(self) -> None:
        backend = FakeRuntimeBackend(services={"api": ServiceScript()})
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        assert state.services[0].status is RuntimeStatus.PORT_NOT_FOUND

    def test_listening_but_not_answering_http_fails_its_health_check(self) -> None:
        backend = FakeRuntimeBackend(services={"api": listening(3000)})
        state, _ = launch(
            RuntimePlan(services=[service("api", ["api"], 3000)]), backend, probe=silent
        )
        api = state.services[0]
        assert api.status is RuntimeStatus.HEALTH_CHECK_FAILED
        assert "nothing answered HTTP" in api.failure.reason

    def test_one_failure_does_not_hide_the_other_service(self) -> None:
        plan = RuntimePlan(services=[service("web", ["web"], 4200), service("api", ["api"], 3000)])
        backend = FakeRuntimeBackend(
            services={"api": listening(3000), "web": ServiceScript(exits=True, logs="boom")}
        )
        state, _ = launch(plan, backend)
        statuses = {s.name: s.status for s in state.services}
        assert statuses == {"web": RuntimeStatus.START_FAILED, "api": RuntimeStatus.RUNNING}

    def test_a_command_outside_the_allowlist_is_refused_before_anything_runs(self) -> None:
        bad = service("api", ["api"], 3000, start="npm run start; curl http://169.254.169.254")
        backend = FakeRuntimeBackend()
        state, _ = launch(RuntimePlan(services=[bad]), backend)
        assert state.services[0].status is RuntimeStatus.UNSUPPORTED
        assert backend.executed == [] and backend.started == []

    def test_a_backend_that_cannot_hold_a_server_is_unsupported(self) -> None:
        backend = FakeRuntimeBackend(name="nebius", long_running=False)
        state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
        assert state.services[0].status is RuntimeStatus.UNSUPPORTED
        assert "cannot keep a server running" in state.services[0].failure.reason
        assert backend.networks == set()


def test_stop_removes_everything_it_created() -> None:
    backend = FakeRuntimeBackend(services={"api": listening(3000)})
    state, _ = launch(RuntimePlan(services=[service("api", ["api"], 3000)]), backend)
    stopped = asyncio.run(stop(state, backend))
    assert stopped.status is RuntimeStatus.STOPPED
    assert set(backend.removed) >= {state.services[0].container, state.services[0].sidecar}
    assert backend.networks == set()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("   1: 00000000:0BB8 00000000:0000 0A 0", ("0.0.0.0", 3000)),
        ("   1: 0100007F:0BB8 00000000:0000 0A 0", ("127.0.0.1", 3000)),
    ],
)
def test_proc_net_tcp_is_read_without_tools(line: str, expected: tuple[str, int]) -> None:
    (listener,) = parse_proc_net("header\n" + line)
    assert (listener.address, listener.port) == expected


def test_dockers_own_resolver_is_not_mistaken_for_the_app() -> None:
    assert parse_proc_net("h\n   0: 0B00007F:9E61 00000000:0000 0A 0") == []
