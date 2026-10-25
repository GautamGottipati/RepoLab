"""Evidence a runtime plan rests on, read from a package's own source.

Ports, environment variables, databases, HTTP routes and the URLs a frontend
calls are all taken from what the code says, each with its file and line. A
credential found in source is never kept: a connection string is reduced to its
scheme and host before it reaches any evidence a person or model will see.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from repolab.evidence.models import CodeReference
from repolab.runtime.models import (
    DependencyKind,
    DependencyState,
    EnvKind,
    EnvVar,
    RouteRef,
)

SOURCE_EXTENSIONS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")

_ENV_READ = re.compile(
    r"process\.env\.([A-Z][A-Z0-9_]*)|process\.env\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]"
)
_IMPORT_META = re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]*)")
_PORT_DEFAULT = re.compile(r"process\.env\.PORT\s*(?:\|\||\?\?)\s*['\"]?(\d{2,5})['\"]?")
_LISTEN_LITERAL = re.compile(r"\.listen\s*\(\s*(\d{2,5})\b")
_SCRIPT_PORT = re.compile(r"(?:--port[= ]|-p[= ]?|\bPORT=)(\d{2,5})\b")
_EXPOSE = re.compile(r"^\s*EXPOSE\s+(\d{2,5})", re.M | re.I)

_GENERATED = {"PORT", "HOST", "NODE_ENV", "BROWSER", "HOSTNAME"}
_SECRET = re.compile(r"SECRET|TOKEN|KEY|PASSWORD|PASSWD|PASS\b|PRIVATE|JWT|SALT|CREDENTIAL")

#: Connection URIs, captured only to classify them; credentials are dropped.
_URI = re.compile(
    r"\b(mongodb(?:\+srv)?|postgres(?:ql)?|redis|rediss)://(?:[^@\s'\"`/]*@)?([^/\s'\"`:?]+)"
)

#: Packages that mean a repository needs a database, by kind.
DEPENDENCY_PACKAGES: dict[str, DependencyKind] = {
    "mongoose": DependencyKind.MONGODB,
    "mongodb": DependencyKind.MONGODB,
    "pg": DependencyKind.POSTGRES,
    "pg-promise": DependencyKind.POSTGRES,
    "postgres": DependencyKind.POSTGRES,
    "redis": DependencyKind.REDIS,
    "ioredis": DependencyKind.REDIS,
}

#: Env-var names that carry each kind's connection string.
_DEPENDENCY_VARS: dict[DependencyKind, re.Pattern[str]] = {
    DependencyKind.MONGODB: re.compile(r"^(MONGO\w*|DB_URI|DATABASE_URL|DB_URL)$"),
    DependencyKind.POSTGRES: re.compile(r"^(DATABASE_URL|POSTGRES\w*|PG\w*|DB_URL)$"),
    DependencyKind.REDIS: re.compile(r"^(REDIS\w*|CACHE_URL)$"),
}

_SCHEMES = {
    "mongodb": DependencyKind.MONGODB,
    "mongodb+srv": DependencyKind.MONGODB,
    "postgres": DependencyKind.POSTGRES,
    "postgresql": DependencyKind.POSTGRES,
    "redis": DependencyKind.REDIS,
    "rediss": DependencyKind.REDIS,
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

_LOCALHOST_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d{2,5})[^\s'\"`]*")


def ref(path: str, text: str, position: int, commit: str | None) -> CodeReference:
    """A file and line, **without** an excerpt: the line may hold a credential."""
    line = text.count("\n", 0, position) + 1
    return CodeReference(path=path, start_line=line, end_line=line, commit_sha=commit)


@dataclass
class PortEvidence:
    port: int
    source: str
    reference: CodeReference | None
    inferred: bool = False


def port_from_script(command: str) -> int | None:
    match = _SCRIPT_PORT.search(command)
    return int(match.group(1)) if match else None


def port_from_code(files: dict[str, str], commit: str | None) -> PortEvidence | None:
    """``process.env.PORT || 3000`` first, then a literal ``listen(3000)``."""
    for pattern, what in ((_PORT_DEFAULT, "PORT || {port}"), (_LISTEN_LITERAL, "listen({port})")):
        for path, text in files.items():
            match = pattern.search(text)
            if match:
                port = int(match.group(1))
                reference = ref(path, text, match.start(), commit)
                where = f"{path}:{reference.start_line}"
                return PortEvidence(port, f"{what.format(port=port)} in {where}", reference)
    return None


def port_from_dockerfile(text: str, path: str, commit: str | None) -> PortEvidence | None:
    match = _EXPOSE.search(text)
    if not match:
        return None
    reference = ref(path, text, match.start(), commit)
    return PortEvidence(int(match.group(1)), f"EXPOSE in {path}:{reference.start_line}", reference)


def reads_port(files: dict[str, str]) -> bool:
    return any("process.env.PORT" in text for text in files.values())


def env_reads(files: dict[str, str], commit: str | None) -> dict[str, list[CodeReference]]:
    """Every environment variable the code reads, with where."""
    found: dict[str, list[CodeReference]] = {}
    for path, text in files.items():
        for pattern in (_ENV_READ, _IMPORT_META):
            for match in pattern.finditer(text):
                name = next(group for group in match.groups() if group)
                refs = found.setdefault(name, [])
                if len(refs) < 3:
                    refs.append(ref(path, text, match.start(), commit))
    return found


def example_env_names(text: str) -> list[str]:
    """Variable names in a ``.env.example``. Values are ignored, never read."""
    return re.findall(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", text, re.M)


def classify_env(name: str) -> EnvKind:
    if name in _GENERATED:
        return EnvKind.GENERATED
    for pattern in _DEPENDENCY_VARS.values():
        if pattern.match(name):
            return EnvKind.DEPENDENCY
    if _SECRET.search(name):
        return EnvKind.SECRET
    return EnvKind.REQUIRED


def env_vars(reads: dict[str, list[CodeReference]], extra: list[str]) -> list[EnvVar]:
    names = list(dict.fromkeys([*reads, *extra]))
    return [
        EnvVar(name=name, kind=classify_env(name), evidence=reads.get(name, [])) for name in names
    ]


@dataclass
class DependencyEvidence:
    kind: DependencyKind
    state: DependencyState
    env_var: str | None
    reason: str
    references: list[CodeReference]


def dependencies(
    packages: dict[str, str],
    files: dict[str, str],
    reads: dict[str, list[CodeReference]],
    commit: str | None,
    package_path: str,
) -> list[DependencyEvidence]:
    """What databases this package needs, and whether RepoLab can provide them."""
    kinds: dict[DependencyKind, list[str]] = {}
    for name in packages:
        kind = DEPENDENCY_PACKAGES.get(name)
        if kind is not None:
            kinds.setdefault(kind, []).append(name)

    uris: dict[DependencyKind, list[tuple[str, CodeReference]]] = {}
    for path, text in files.items():
        for match in _URI.finditer(text):
            kind = _SCHEMES[match.group(1)]
            host = match.group(2)
            uris.setdefault(kind, []).append((host, ref(path, text, match.start(), commit)))
            kinds.setdefault(kind, [])

    found: list[DependencyEvidence] = []
    for kind, names in kinds.items():
        declared = [CodeReference(path=package_path, commit_sha=commit)] if names else []
        var = next(
            (n for n in reads if _DEPENDENCY_VARS[kind].match(n)),
            None,
        )
        literal = uris.get(kind, [])
        external = [(h, r) for h, r in literal if h not in _LOCAL_HOSTS]
        local = [(h, r) for h, r in literal if h in _LOCAL_HOSTS]
        label = _LABEL[kind]
        if var is not None:
            found.append(
                DependencyEvidence(
                    kind,
                    DependencyState.PROVISIONABLE,
                    var,
                    f"the code reads its {label} connection from {var}; RepoLab can start an "
                    f"ephemeral {label} and set it",
                    [*reads[var][:2], *declared],
                )
            )
        elif external:
            host, where = external[0]
            found.append(
                DependencyEvidence(
                    kind,
                    DependencyState.EXTERNAL_REQUIRED,
                    None,
                    f"the {label} connection string is written into the code "
                    f"({where.path}:{where.start_line}) and points at an external host "
                    f"({_host_hint(host)}). The sandbox has no internet access and RepoLab "
                    "never uses credentials found in source, so anything that needs the "
                    "database will fail",
                    [where, *declared],
                )
            )
        elif local:
            _, where = local[0]
            found.append(
                DependencyEvidence(
                    kind,
                    DependencyState.UNSUPPORTED,
                    None,
                    f"the code connects to a {label} on localhost ({where.path}:"
                    f"{where.start_line}) rather than reading an address from the "
                    "environment, so a separate database cannot be connected to it",
                    [where, *declared],
                )
            )
        elif names:
            found.append(
                DependencyEvidence(
                    kind,
                    DependencyState.UNSUPPORTED,
                    None,
                    f"{', '.join(names)} is a dependency, but RepoLab could not find how the "
                    f"code connects to {label}",
                    declared,
                )
            )
    return found


_LABEL = {
    DependencyKind.MONGODB: "MongoDB",
    DependencyKind.POSTGRES: "PostgreSQL",
    DependencyKind.REDIS: "Redis",
}


def _host_hint(host: str) -> str:
    """A host named well enough to recognise, without echoing an identifier in full."""
    parts = host.split(".")
    return ".".join(["…", *parts[-2:]]) if len(parts) > 2 else host


# -- routes --------------------------------------------------------------------

_ROUTE = re.compile(
    r"\b(app|router|server|api|\w+Router|routes)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*['\"`]([^'\"`]+)['\"`]",
    re.I,
)
_REQUIRE_BIND = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(\s*['\"](\.{1,2}/[^'\"]+)['\"]\s*\)"
)
_IMPORT_BIND = re.compile(r"import\s+(\w+)\s+from\s+['\"](\.{1,2}/[^'\"]+)['\"]")
_MOUNT = re.compile(
    r"\.use\s*\(\s*['\"`]([^'\"`]+)['\"`]\s*,\s*(?:require\s*\(\s*['\"](\.{1,2}/[^'\"]+)['\"]\s*\)|(\w+))"
)


def routes(files: dict[str, str], commit: str | None) -> list[RouteRef]:
    """HTTP routes with the prefixes their routers are mounted at.

    ``app.use('/events', require('./routes/events'))`` plus ``router.get('/api/posts')``
    in that file gives ``GET /events/api/posts``. A router mounted nowhere keeps its
    own paths.
    """
    prefixes: dict[str, str] = {}
    for path, text in files.items():
        bound = {
            name: _resolve(path, spec, files)
            for pattern in (_REQUIRE_BIND, _IMPORT_BIND)
            for name, spec in pattern.findall(text)
        }
        for match in _MOUNT.finditer(text):
            prefix, spec, name = match.groups()
            target = _resolve(path, spec, files) if spec else bound.get(name or "")
            if target:
                prefixes[target] = prefix.rstrip("/")

    found: list[RouteRef] = []
    for path, text in files.items():
        prefix = prefixes.get(path, "")
        for match in _ROUTE.finditer(text):
            route = match.group(3)
            if not route.startswith("/"):
                continue
            full = (prefix + route) if route != "/" or not prefix else prefix
            line = text.count("\n", 0, match.start()) + 1
            found.append(
                RouteRef(method=match.group(2).upper(), path=full or "/", file=path, line=line)
            )
    unique = {(r.method, r.path): r for r in found}
    return sorted(unique.values(), key=lambda r: (r.path, r.method))[:60]


def _resolve(base: str, spec: str | None, files: dict[str, str]) -> str | None:
    if not spec:
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), spec))
    for candidate in (joined, *(joined + ext for ext in SOURCE_EXTENSIONS), joined + "/index.js"):
        if candidate in files:
            return candidate
    return None


# -- frontend ------------------------------------------------------------------


def localhost_calls(files: dict[str, str], commit: str | None) -> dict[int, list[CodeReference]]:
    """``http://localhost:<port>`` URLs written into frontend code, by port."""
    found: dict[int, list[CodeReference]] = {}
    for path, text in files.items():
        for match in _LOCALHOST_URL.finditer(text):
            refs = found.setdefault(int(match.group(1)), [])
            if len(refs) < 3:
                refs.append(ref(path, text, match.start(), commit))
    return found


#: Frontend env vars that conventionally hold a backend base URL.
API_BASE_VAR = re.compile(
    r"^(VITE_|NEXT_PUBLIC_|REACT_APP_|NG_APP_)\w*(API|BACKEND|SERVER|BASE)\w*$"
)
