"""How a repository's services are run, and where each one stands.

Three states are kept apart on purpose, because the screen must never blur them:

    DETECTED   the investigation found evidence of a web app or an HTTP API
               (``RepoManifest.interaction_surfaces``)
    RUNNABLE   a :class:`RuntimePlan` exists for it, built from repository
               evidence, and RepoLab has an adapter that can attempt it
    RUNNING    it was built, started, found listening, and answered HTTP
               (:class:`ServiceState` with status ``running``)

A plan is deterministic and inspectable before anything runs. Every command in
it is either the repository's own script or a flag an adapter adds to make a
dev server reachable -- never something a model wrote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from repolab.evidence.models import CodeReference

#: A surface, by its ``SurfaceType`` value (``web``, ``api``). Kept as text so the
#: manifest can hold a plan without the two modules importing each other.
Surface = str


class DependencyKind(StrEnum):
    MONGODB = "mongodb"
    POSTGRES = "postgres"
    REDIS = "redis"


class DependencyState(StrEnum):
    """Whether a service a repository needs can be satisfied in the sandbox."""

    PROVISIONABLE = "provisionable"
    """The code reads its connection from an environment variable, so RepoLab can
    start an ephemeral instance on the runtime's private network and set it."""

    EXTERNAL_REQUIRED = "external_required"
    """The code connects to a fixed external address. The sandbox has no egress,
    and credentials found in source are never used, so it cannot be reached."""

    UNSUPPORTED = "unsupported"
    """RepoLab has no way to provide it."""


class RuntimeDependency(BaseModel):
    kind: DependencyKind
    state: DependencyState
    env_var: str | None = None
    """The variable the code reads its connection from, when it does."""

    reason: str
    evidence: list[CodeReference] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    """Which planned services need it."""


class EnvKind(StrEnum):
    GENERATED = "generated"
    """A safe value RepoLab sets: PORT, HOST, NODE_ENV."""

    SECRET = "secret"
    """A secret the code reads. Given an ephemeral random value, sandbox-only."""

    DEPENDENCY = "dependency"
    """A connection string for a provisioned dependency."""

    REQUIRED = "required"
    """Read by the code, with no value RepoLab can safely supply."""


class EnvVar(BaseModel):
    name: str
    kind: EnvKind
    value: str | None = None
    """Only for generated and dependency values. Secrets are generated at launch
    and never stored; required ones have none."""

    evidence: list[CodeReference] = Field(default_factory=list)


class RouteRef(BaseModel):
    """An HTTP route, as the source declares it. Never an invented schema."""

    method: str
    path: str
    file: str
    line: int


class PlanWarning(BaseModel):
    """Something evidenced that will limit what works once it runs."""

    message: str
    evidence: list[CodeReference] = Field(default_factory=list)


class ServicePlan(BaseModel):
    """How one process of the repository is built and started."""

    name: str
    surfaces: list[Surface]
    working_directory: str
    """Relative to the repository root; ``""`` for the root."""

    runtime: str = "node"
    framework: str | None = None
    image: str
    image_reason: str

    package_manager: str
    install: str
    build: str | None = None
    start: str
    script: str
    """The package.json script ``start`` comes from."""

    port: int
    port_source: str
    """Why this port: ``PORT || 3000 in backend/bin/www:15``, or an adapter default."""

    env: list[EnvVar] = Field(default_factory=list)
    health_path: str = "/"
    routes: list[RouteRef] = Field(default_factory=list)
    evidence: list[CodeReference] = Field(default_factory=list)
    inferred_fields: list[str] = Field(default_factory=list)
    """Fields RepoLab inferred rather than read (e.g. ``image`` from a lockfile)."""

    warnings: list[PlanWarning] = Field(default_factory=list)


class RuntimePlan(BaseModel):
    """Every service a repository runs as, and what they need."""

    services: list[ServicePlan] = Field(default_factory=list)
    dependencies: list[RuntimeDependency] = Field(default_factory=list)
    unplanned: dict[str, str] = Field(default_factory=dict)
    """Surface -> why no service could be planned for it, in plain words."""

    adapter_version: str = "node-1"

    def for_surface(self, surface: str) -> list[ServicePlan]:
        return [service for service in self.services if surface in service.surfaces]

    def service(self, name: str) -> ServicePlan | None:
        return next((s for s in self.services if s.name == name), None)


class RuntimeStatus(StrEnum):
    """The lifecycle, and the ways it ends badly. Each failure keeps its reason."""

    PLANNING = "planning"
    PREPARING = "preparing"
    INSTALLING = "installing"
    BUILDING = "building"
    STARTING = "starting"
    HEALTH_CHECKING = "health_checking"
    RUNNING = "running"
    STOPPED = "stopped"

    BUILD_FAILED = "build_failed"
    START_FAILED = "start_failed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    DEPENDENCY_MISSING = "dependency_missing"
    PORT_NOT_FOUND = "port_not_found"
    UNSUPPORTED = "unsupported"
    TIMEOUT = "timeout"

    @property
    def failed(self) -> bool:
        return self in _FAILED

    @property
    def active(self) -> bool:
        return self not in _FAILED and self is not RuntimeStatus.STOPPED


_FAILED = frozenset(
    {
        RuntimeStatus.BUILD_FAILED,
        RuntimeStatus.START_FAILED,
        RuntimeStatus.HEALTH_CHECK_FAILED,
        RuntimeStatus.DEPENDENCY_MISSING,
        RuntimeStatus.PORT_NOT_FOUND,
        RuntimeStatus.UNSUPPORTED,
        RuntimeStatus.TIMEOUT,
    }
)


class StepRecord(BaseModel):
    """One step a service went through, for the progress list and the failure."""

    name: str
    status: str
    """``done``, ``failed`` or ``skipped``."""

    detail: str = ""
    command: str | None = None
    seconds: float = 0.0
    log_tail: str = ""


class HealthResult(BaseModel):
    path: str
    status_code: int | None = None
    ok: bool = False
    detail: str = ""


class Failure(BaseModel):
    """Why a service is not running, from what actually happened."""

    status: RuntimeStatus
    reason: str
    evidence: list[CodeReference] = Field(default_factory=list)
    next_step: str | None = None
    log_tail: str = ""


class ServiceState(BaseModel):
    name: str
    surfaces: list[Surface]
    status: RuntimeStatus = RuntimeStatus.PLANNING
    steps: list[StepRecord] = Field(default_factory=list)
    port: int | None = None
    port_source: str | None = None
    host_port: int | None = None
    """The RepoLab sidecar's port on the host's loopback. The proxy's only target."""

    container: str | None = None
    sidecar: str | None = None
    health: HealthResult | None = None
    failure: Failure | None = None
    warnings: list[PlanWarning] = Field(default_factory=list)
    log_tail: str = ""


class RuntimeState(BaseModel):
    """Everything about one launch, as persisted and as shown."""

    id: str
    repo_url: str
    commit_sha: str
    status: RuntimeStatus = RuntimeStatus.PLANNING
    services: list[ServiceState] = Field(default_factory=list)
    dependencies: list[RuntimeDependency] = Field(default_factory=list)
    network: str | None = None
    dependency_containers: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def service(self, name: str) -> ServiceState | None:
        return next((s for s in self.services if s.name == name), None)

    def overall(self) -> RuntimeStatus:
        """Running when every service runs; otherwise the first failure, or the
        furthest a service has got."""
        statuses = [service.status for service in self.services]
        if not statuses:
            return RuntimeStatus.UNSUPPORTED
        if all(s is RuntimeStatus.RUNNING for s in statuses):
            return RuntimeStatus.RUNNING
        failed = [s for s in statuses if s.failed]
        if failed and all(s.failed or s is RuntimeStatus.RUNNING for s in statuses):
            return RuntimeStatus.RUNNING if RuntimeStatus.RUNNING in statuses else failed[0]
        active = [s for s in statuses if s.active and s is not RuntimeStatus.RUNNING]
        return active[0] if active else statuses[0]
