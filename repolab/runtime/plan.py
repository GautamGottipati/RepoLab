"""From a cloned repository to a :class:`RuntimePlan`, deterministically.

Run during investigation, while the clone is on disk, and stored on the
manifest -- so the Experiences page can show *how* RepoLab would run something
before anyone presses Start, and so the decision "runnable" rests on a plan
that exists rather than on a surface that was merely detected.

One service per ``package.json`` that has a server script, in that package's
own directory: a repository with ``frontend/`` and ``backend/`` gets two
services, never one process for the whole tree.
"""

from __future__ import annotations

import re

from repolab.architecture.source import is_peripheral, is_test_path
from repolab.evidence.models import CodeReference
from repolab.ingest.content import ReadBudget, read_text
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.detectors.surfaces import is_generated_bundle
from repolab.manifest.models import RepoManifest, SurfaceType
from repolab.runtime import node, scan
from repolab.runtime.models import (
    DependencyState,
    EnvKind,
    EnvVar,
    PlanWarning,
    RuntimeDependency,
    RuntimePlan,
    ServicePlan,
)

#: Files read per package when looking for ports, env vars, routes and URLs.
MAX_FILES_PER_PACKAGE = 400

#: Frontend frameworks whose package is a web application in its own right.
_WEB_FRAMEWORKS = {"angular", "next", "nuxt", "cra", "vite", "sveltekit", "gatsby", "vue", "react"}

_HEALTH_PATHS = ("/health", "/healthz", "/api/health", "/status", "/ping")

_NODE_ENTRY = re.compile(r"\b(?:node|nodemon|ts-node|tsx)\s+(?:--\S+\s+)*([\w./-]+)")


def plan_runtime(context: DetectionContext, manifest: RepoManifest) -> RuntimePlan:
    """Every web or API service this repository can be started as."""
    detected = {s.type for s in manifest.interaction_surfaces} & {SurfaceType.WEB, SurfaceType.API}
    plan = RuntimePlan()
    if not detected:
        return plan

    paths = [entry.path for entry in context.tree.files]
    manifests = sorted(
        p
        for p in paths
        if p.rsplit("/", 1)[-1] == "package.json"
        and "node_modules/" not in p
        and not is_test_path(p)
        and not is_peripheral(p)
    )
    budget = ReadBudget()
    package_dirs = [p.rsplit("/", 1)[0] if "/" in p else "" for p in manifests]
    commit = context.commit_sha

    services: list[ServicePlan] = []
    packages: dict[str, node.NodePackage] = {}
    sources: dict[str, dict[str, str]] = {}
    for manifest_path, directory in zip(manifests, package_dirs, strict=True):
        text = read_text(context.path_for(manifest_path), budget=budget, reserved=True)
        if text is None:
            continue
        siblings = _siblings(context, directory, budget)
        package = node.read_package(manifest_path, text, siblings)
        if package is None:
            continue
        files = _package_files(context, paths, directory, package_dirs, budget)
        _add_entry_file(context, package, files, budget)
        service = _service(package, files, siblings, detected, commit)
        if service is not None:
            services.append(service)
            packages[service.name] = package
            sources[service.name] = files

    _name_uniquely(services)
    plan.services = services
    plan.dependencies = _dependencies(services, packages, sources, commit)
    _warn(plan, sources)

    for surface in detected:
        if not plan.for_surface(surface):
            plan.unplanned[surface.value] = _why_unplanned(surface, manifests, manifest)
    return plan


def _service(
    package: node.NodePackage,
    files: dict[str, str],
    siblings: dict[str, str],
    detected: set[SurfaceType],
    commit: str | None,
) -> ServicePlan | None:
    script = node.start_script(package)
    if script is None:
        return None
    framework = node.frontend_framework(package)
    surfaces: list[SurfaceType] = []
    if framework in _WEB_FRAMEWORKS:
        surfaces.append(SurfaceType.WEB)
    if node.is_server(package):
        surfaces.append(SurfaceType.API)
        if framework in {"react", "vue"}:
            # A server that merely lists a UI library is an API, not a web app.
            surfaces.remove(SurfaceType.WEB)
    surfaces = [s for s in surfaces if s in detected]
    if not surfaces:
        return None

    inferred: list[str] = []
    major, image_reason, image_inferred = node.node_major(package)
    if image_inferred:
        inferred.append("image")

    command = package.scripts[script]
    port, port_source = _port(package, script, command, files, siblings, framework, commit)
    if port is None:
        port, port_source = 3000, "nothing names a port; RepoLab sets PORT=3000 and checks"
        inferred.append("port")
    elif "default" in port_source:
        inferred.append("port")

    start = node.adapt_start(package, script, framework, port)
    reads = scan.env_reads(files, commit)
    examples = []
    for name in (".env.example", ".env.sample", ".env.template"):
        if name in siblings:
            examples += scan.example_env_names(siblings[name])
    env = scan.env_vars(reads, examples)
    for generated in ("PORT", "HOST", "NODE_ENV", "BROWSER"):
        existing = next((v for v in env if v.name == generated), None)
        if existing is None:
            env.append(EnvVar(name=generated, kind=EnvKind.GENERATED))
            existing = env[-1]
        existing.kind = EnvKind.GENERATED
        existing.value = start.env[generated]

    found_routes = scan.routes(files, commit) if SurfaceType.API in surfaces else []
    health = next(
        (r.path for r in found_routes if r.method == "GET" and r.path in _HEALTH_PATHS), "/"
    )

    evidence = [
        CodeReference(
            path=package.path, start_line=package.script_lines.get(script), commit_sha=commit
        )
    ]
    warnings: list[PlanWarning] = []
    if start.adapted:
        warnings.append(PlanWarning(message=f"To be reachable in the sandbox, {start.adapted}."))

    return ServicePlan(
        name=_slug(package.directory.rsplit("/", 1)[-1] or package.name),
        surfaces=surfaces,
        working_directory=package.directory,
        framework=framework or ("express" if package.has("express") else None),
        image=f"node:{major}",
        image_reason=image_reason,
        package_manager={"yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm"}.get(
            package.lockfile or "", "npm"
        ),
        install=node.install_command(package),
        build=f"{node.runner(package)} run build" if node.needs_build(package, script) else None,
        start=start.command,
        script=script,
        port=port,
        port_source=port_source,
        env=env,
        health_path=health,
        routes=found_routes,
        evidence=evidence,
        inferred_fields=inferred,
        warnings=warnings,
    )


def _port(
    package: node.NodePackage,
    script: str,
    command: str,
    files: dict[str, str],
    siblings: dict[str, str],
    framework: str | None,
    commit: str | None,
) -> tuple[int | None, str]:
    flagged = scan.port_from_script(command)
    if flagged is not None:
        return flagged, f'the "{script}" script passes port {flagged} ({package.path})'

    entry = _NODE_ENTRY.search(command)
    candidates = files
    if entry:
        target = _join(package.directory, entry.group(1))
        chosen = {p: t for p, t in files.items() if p == target or p.startswith(target + ".")}
        candidates = chosen or files
    from_code = scan.port_from_code(candidates, commit) or scan.port_from_code(files, commit)
    if from_code is not None:
        return from_code.port, from_code.source

    for name in ("Dockerfile", "dockerfile"):
        if name in siblings:
            exposed = scan.port_from_dockerfile(
                siblings[name], _join(package.directory, name), commit
            )
            if exposed is not None:
                return exposed.port, exposed.source

    if framework in node.FRAMEWORK_PORTS:
        port = node.FRAMEWORK_PORTS[framework]
        return port, f"the {framework} dev server's default port ({port})"
    if scan.reads_port(files):
        return 3000, "the code reads PORT; RepoLab sets it to 3000"
    return None, ""


def _dependencies(
    services: list[ServicePlan],
    packages: dict[str, node.NodePackage],
    sources: dict[str, dict[str, str]],
    commit: str | None,
) -> list[RuntimeDependency]:
    merged: dict[str, RuntimeDependency] = {}
    for service in services:
        package = packages[service.name]
        reads = scan.env_reads(sources[service.name], commit)
        for found in scan.dependencies(
            package.dependencies, sources[service.name], reads, commit, package.path
        ):
            existing = merged.get(found.kind.value)
            if existing is None or _rank(found.state) < _rank(existing.state):
                merged[found.kind.value] = RuntimeDependency(
                    kind=found.kind,
                    state=found.state,
                    env_var=found.env_var,
                    reason=found.reason,
                    evidence=found.references,
                    services=[service.name],
                )
            elif service.name not in existing.services:
                existing.services.append(service.name)
            if found.env_var:
                for var in service.env:
                    if var.name == found.env_var:
                        var.kind = EnvKind.DEPENDENCY
    return list(merged.values())


def _rank(state: DependencyState) -> int:
    return {
        DependencyState.PROVISIONABLE: 0,
        DependencyState.EXTERNAL_REQUIRED: 1,
        DependencyState.UNSUPPORTED: 2,
    }[state]


def _warn(plan: RuntimePlan, sources: dict[str, dict[str, str]]) -> None:
    api_ports = {s.port: s for s in plan.services if SurfaceType.API in s.surfaces}
    for service in plan.services:
        for dependency in plan.dependencies:
            if service.name in dependency.services and dependency.state is not (
                DependencyState.PROVISIONABLE
            ):
                service.warnings.append(
                    PlanWarning(
                        message=dependency.reason[0].upper() + dependency.reason[1:] + ".",
                        evidence=dependency.evidence[:2],
                    )
                )
        for var in service.env:
            if var.kind is EnvKind.REQUIRED and var.evidence:
                service.warnings.append(
                    PlanWarning(
                        message=f"The code reads {var.name}, which RepoLab cannot supply; it "
                        "will be unset.",
                        evidence=var.evidence[:1],
                    )
                )
        if SurfaceType.WEB not in service.surfaces:
            continue
        for port, refs in scan.localhost_calls(sources[service.name], None).items():
            target = api_ports.get(port)
            whom = f"the {target.name} service's port" if target else "a fixed local address"
            service.warnings.append(
                PlanWarning(
                    message=(
                        f"The frontend calls http://localhost:{port} directly ({whom}). In "
                        "RepoLab it is served from a different address, so those requests "
                        "will not reach the backend. RepoLab does not rewrite source."
                    ),
                    evidence=refs,
                )
            )


def _why_unplanned(surface: SurfaceType, manifests: list[str], manifest: RepoManifest) -> str:
    if not manifests:
        frameworks = ", ".join(manifest.frameworks) or "no recognised framework"
        return (
            "RepoLab can run Node.js web apps and APIs in this build; this repository has no "
            f"package.json ({frameworks})"
        )
    kind = "web application" if surface is SurfaceType.WEB else "HTTP server"
    return (
        f"no package.json here has a start or dev script that runs a {kind}, so RepoLab "
        "has no command from the repository to start it with"
    )


def _siblings(context: DetectionContext, directory: str, budget: ReadBudget) -> dict[str, str]:
    """Small files beside a package.json that say how it runs. Never ``.env``."""
    found: dict[str, str] = {}
    names = (
        "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
        ".nvmrc", ".node-version", ".env.example", ".env.sample", ".env.template",
        "Dockerfile", "dockerfile",
    )  # fmt: skip
    present = {entry.path for entry in context.tree.files}
    for name in names:
        path = _join(directory, name)
        if path in present:
            # Lockfiles can be large; their head carries the version we need.
            text = read_text(context.path_for(path), budget=budget, reserved=True)
            if text is not None:
                found[name] = text if not name.endswith(".json") else text[:4000]
            elif name.endswith((".json", ".lock", ".yaml")):
                found[name] = ""
    return found


def _package_files(
    context: DetectionContext,
    paths: list[str],
    directory: str,
    package_dirs: list[str],
    budget: ReadBudget,
) -> dict[str, str]:
    """The package's own source, not a nested package's, a dependency's or a build's."""
    prefix = f"{directory}/" if directory else ""
    nested = [d + "/" for d in package_dirs if d != directory and d.startswith(prefix)]
    files: dict[str, str] = {}
    for path in paths:
        if not path.startswith(prefix) or not path.endswith(scan.SOURCE_EXTENSIONS):
            continue
        if "node_modules/" in path or any(path.startswith(n) for n in nested):
            continue
        if is_test_path(path) or path.endswith((".spec.ts", ".spec.js", ".d.ts")):
            continue
        if _TOOLING_PATH.search(path[len(prefix) :]):
            continue
        if is_generated_bundle(path):
            continue
        text = read_text(context.path_for(path), budget=budget, reserved=True)
        if text is None or is_generated_bundle(path, text):
            continue
        files[path] = text
        if len(files) >= MAX_FILES_PER_PACKAGE:
            break
    return files


def _add_entry_file(
    context: DetectionContext, package: node.NodePackage, files: dict[str, str], budget: ReadBudget
) -> None:
    """The file a start script runs, read even without an extension (``bin/www``)."""
    script = node.start_script(package)
    entry = _NODE_ENTRY.search(package.scripts.get(script or "", ""))
    if entry is None:
        return
    path = _join(package.directory, entry.group(1))
    if path not in files and (context.root / path).is_file():
        text = read_text(context.path_for(path), budget=budget, reserved=True)
        if text is not None:
            files[path] = text


#: Test and tooling code inside a package: not the application.
_TOOLING_PATH = re.compile(
    r"(^|/)(e2e|cypress|playwright|__mocks__)/|(^|/)[\w.-]+\.(conf|config)\.(js|ts|cjs|mjs)$"
)


def _name_uniquely(services: list[ServicePlan]) -> None:
    seen: dict[str, int] = {}
    for service in services:
        if service.name in seen:
            seen[service.name] += 1
            service.name = f"{service.name}-{seen[service.name]}"
        else:
            seen[service.name] = 1


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:24] or "app"


def _join(directory: str, name: str) -> str:
    name = name.removeprefix("./")
    return f"{directory}/{name}" if directory else name
