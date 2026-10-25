"""Reading a Node.js package the way its own files say it runs.

Everything here is read, never executed: ``package.json`` scripts and
dependencies, the lockfile beside it, ``engines`` and ``.nvmrc``. The only
knowledge RepoLab adds is how each well-known dev server is told which address
and port to listen on -- because a server bound to ``localhost`` inside a
container cannot be reached by anything outside it. That is applied through
flags and environment variables the framework already accepts, never by
editing the repository's source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from repolab.ingest.detectors.entrypoints import _script_kind
from repolab.manifest.models import EntryPointKind

#: Script names tried in order when choosing how to start a package.
START_SCRIPTS = ("start", "dev", "serve", "develop", "preview")

#: Node majors by npm lockfile version, when nothing declares one: lockfile v1
#: was written by npm 6 (Node <= 14), v2 by npm 7-8 (Node 16), v3 by npm 9+.
LOCKFILE_NODE = {1: 14, 2: 16, 3: 20}

#: The current long-term-support major, used when nothing else says.
DEFAULT_NODE = 20

#: Packages whose presence makes a package an HTTP server.
SERVER_PACKAGES = frozenset(
    {"express", "koa", "fastify", "@hapi/hapi", "hapi", "restify", "@nestjs/core", "polka"}
)

#: Packages whose presence makes a package a browser application, by framework.
FRONTEND_PACKAGES: dict[str, str] = {
    "@angular/core": "angular",
    "next": "next",
    "nuxt": "nuxt",
    "react-scripts": "cra",
    "vite": "vite",
    "@sveltejs/kit": "sveltekit",
    "gatsby": "gatsby",
    "vue": "vue",
    "react": "react",
}


@dataclass
class NodePackage:
    """One ``package.json``, read."""

    path: str
    """The package.json's repository path."""

    directory: str
    data: dict
    scripts: dict[str, str]
    dependencies: dict[str, str]
    lockfile: str | None = None
    lockfile_version: int | None = None
    nvmrc: str | None = None
    script_lines: dict[str, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.directory or "app")

    def has(self, package: str) -> bool:
        return package in self.dependencies


def read_package(path: str, text: str, siblings: dict[str, str]) -> NodePackage | None:
    """Parse *text* as the package.json at *path*. *siblings* are files beside it."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scripts = {str(k): str(v) for k, v in (data.get("scripts") or {}).items() if isinstance(v, str)}
    dependencies: dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        section = data.get(key) or {}
        if isinstance(section, dict):
            dependencies.update({str(k): str(v) for k, v in section.items()})
    directory = path.rsplit("/", 1)[0] if "/" in path else ""

    lockfile = None
    lockfile_version = None
    for name in ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"):
        if name in siblings:
            lockfile = name
            if name.endswith(".json"):
                match = re.search(r'"lockfileVersion"\s*:\s*(\d+)', siblings[name][:2000])
                lockfile_version = int(match.group(1)) if match else None
            break

    lines = text.splitlines()
    script_lines = {}
    for name in scripts:
        for number, line in enumerate(lines, start=1):
            if f'"{name}"' in line and ":" in line:
                script_lines[name] = number
                break

    return NodePackage(
        path=path,
        directory=directory,
        data=data,
        scripts=scripts,
        dependencies=dependencies,
        lockfile=lockfile,
        lockfile_version=lockfile_version,
        nvmrc=(siblings.get(".nvmrc") or siblings.get(".node-version") or "").strip() or None,
        script_lines=script_lines,
    )


def start_script(package: NodePackage) -> str | None:
    """The script that starts a server, by the project's own naming."""
    for name in START_SCRIPTS:
        command = package.scripts.get(name)
        if command and _script_kind(name, command) is EntryPointKind.WEB_SERVER:
            return name
    return None


def frontend_framework(package: NodePackage) -> str | None:
    for dependency, framework in FRONTEND_PACKAGES.items():
        if package.has(dependency):
            return framework
    return None


def is_server(package: NodePackage) -> bool:
    return any(package.has(name) for name in SERVER_PACKAGES)


def install_command(package: NodePackage) -> str:
    """How to install exactly what the lockfile pins, or what package.json asks."""
    if package.lockfile in {"package-lock.json", "npm-shrinkwrap.json"}:
        return "npm ci --no-audit --no-fund"
    if package.lockfile == "yarn.lock":
        return "yarn install --frozen-lockfile --non-interactive"
    if package.lockfile == "pnpm-lock.yaml":
        return "corepack enable && pnpm install --frozen-lockfile"
    return "npm install --no-audit --no-fund"


def node_major(package: NodePackage) -> tuple[int, str, bool]:
    """The Node major to run, why, and whether that was inferred."""
    engines = package.data.get("engines") or {}
    declared = engines.get("node") if isinstance(engines, dict) else None
    if isinstance(declared, str):
        numbers = [int(n) for n in re.findall(r"(?<![.\d])(\d{1,2})(?=[.x*\s]|$)", declared)]
        if numbers:
            return max(numbers), f'engines.node is "{declared}" in {package.path}', False
    if package.nvmrc:
        match = re.search(r"(\d{1,2})", package.nvmrc)
        if match:
            return int(match.group(1)), f"the Node version file says {package.nvmrc}", False
    if package.lockfile_version in LOCKFILE_NODE:
        major = LOCKFILE_NODE[package.lockfile_version]
        return (
            major,
            f"no Node version is declared; {package.lockfile} v{package.lockfile_version} "
            f"was written by the npm that ships with Node {major}",
            True,
        )
    return DEFAULT_NODE, "no Node version is declared; using the current LTS", True


#: Default ports of well-known dev servers, used only when nothing in the
#: repository names one.
FRAMEWORK_PORTS = {"angular": 4200, "vite": 5173, "next": 3000, "cra": 3000, "nuxt": 3000}


@dataclass
class StartCommand:
    command: str
    env: dict[str, str]
    adapted: str | None
    """What RepoLab added to make it reachable, in words. ``None`` if nothing."""


def adapt_start(
    package: NodePackage, script: str, framework: str | None, port: int
) -> StartCommand:
    """Run the project's own script, told to listen on every address.

    Flags are only appended when the script *is* the framework's server command;
    a script that chains several commands gets environment variables instead,
    which every Node server here honours.
    """
    command = package.scripts[script]
    runner_cmd = runner(package)
    base = f"{runner_cmd} run {script}"
    env = {"PORT": str(port), "HOST": "0.0.0.0", "NODE_ENV": "development", "BROWSER": "none"}
    single = "&&" not in command and ";" not in command and "|" not in command

    if framework == "angular" and single and re.search(r"\bng\s+serve\b", command):
        return StartCommand(
            f"{base} -- --host 0.0.0.0 --port {port} --disable-host-check",
            env,
            "ng serve is told to listen on 0.0.0.0 and accept the proxy's Host header",
        )
    if framework == "vite" and single and re.search(r"\bvite(\s+(dev|preview|serve))?\b", command):
        return StartCommand(
            f"{base} -- --host 0.0.0.0 --port {port}", env, "vite is told to listen on 0.0.0.0"
        )
    if framework == "next" and single and re.search(r"\bnext\s+(dev|start)\b", command):
        return StartCommand(
            f"{base} -- -H 0.0.0.0 -p {port}", env, "next is told to listen on 0.0.0.0"
        )
    return StartCommand(base, env, "PORT and HOST=0.0.0.0 are set in the environment")


def needs_build(package: NodePackage, script: str) -> bool:
    """Whether the start script serves output a build step produces."""
    command = package.scripts.get(script, "")
    return "build" in package.scripts and bool(
        re.search(r"\bnext\s+start\b|\bvite\s+preview\b|\bserve\s+-s\b|\bnuxt\s+start\b", command)
    )


def runner(package: NodePackage) -> str:
    if package.lockfile == "yarn.lock":
        return "yarn"
    if package.lockfile == "pnpm-lock.yaml":
        return "pnpm"
    return "npm"


#: Every command a plan may contain. A launch refuses anything else.
_ALLOWED = (
    re.compile(r"^npm ci --no-audit --no-fund$"),
    re.compile(r"^npm install --no-audit --no-fund$"),
    re.compile(r"^yarn install --frozen-lockfile --non-interactive$"),
    re.compile(r"^corepack enable && pnpm install --frozen-lockfile$"),
    re.compile(r"^(npm|yarn|pnpm) run [\w:.-]+( -- [\w .=:/-]+)?$"),
)


def allowed(command: str) -> bool:
    return any(pattern.match(command) for pattern in _ALLOWED)
