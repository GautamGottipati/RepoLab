"""Request-serving components: HTTP APIs, the servers that host them, frontends.

Routes are found by the shapes every web framework shares -- a decorator or a
method call naming an HTTP verb and a path -- in Python, JavaScript/TypeScript,
Java and Go. Routes are grouped by the file that defines them and named by the
resource their paths share (``/orders/...`` becomes *Orders API*), because a
reader thinks in resources, not in handler functions.

A frontend is claimed from a declared UI library, cited at its line in
``package.json``, and its calls to the backend are found from ``fetch`` and
client calls in its own source.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from repolab.architecture.analyzers.base import AnalysisContext, slug, stem, title
from repolab.architecture.models import ArchitectureEdge, ArchitectureNode, EdgeType, NodeCategory
from repolab.architecture.source import find_all, line_of

_ROUTES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"@(?P<obj>\w+)\.(?P<verb>get|post|put|patch|delete|route|api_route|websocket)\(\s*[rf]?['\"](?P<path>[^'\"]*)['\"]"
    ),
    re.compile(
        r"\b(?P<obj>app|router|server|api|routes)\.(?P<verb>get|post|put|patch|delete|all)\(\s*['\"`](?P<path>/[^'\"`]*)['\"`]"
    ),
    re.compile(
        r"@(?P<verb>Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?\"(?P<path>[^\"]*)\""
    ),
    re.compile(r"\b(?:re_)?path\(\s*r?['\"](?P<path>[^'\"]*)['\"]\s*,\s*(?P<view>[\w.]+)"),
    re.compile(r"\.(?P<verb>HandleFunc|Handle|GET|POST|PUT|DELETE)\(\s*\"(?P<path>/[^\"]*)\""),
)

_SERVER = re.compile(
    r"\bFastAPI\s*\(|\bFlask\s*\(\s*__name__|\bexpress\s*\(\s*\)|@SpringBootApplication|"
    r"http\.ListenAndServe\s*\(|\bnew\s+Koa\s*\(|\bnew\s+Hono\s*\(|\bget_wsgi_application\s*\(|"
    r"\bNestFactory\.create\s*\("
)
_MOUNT = re.compile(
    r"include_router\s*\(|register_blueprint\s*\(|\bapp\.use\s*\(\s*['\"`]?/|\binclude\s*\(\s*['\"]"
)

#: UI libraries, as declared in package.json, and how they are named.
_UI_LIBRARIES: dict[str, str] = {
    "next": "Next.js",
    "react": "React",
    "vue": "Vue",
    "svelte": "Svelte",
    "@angular/core": "Angular",
    "solid-js": "Solid",
    "preact": "Preact",
}
_FRONTEND_ENTRIES = (
    "src/main.tsx",
    "src/main.ts",
    "src/main.jsx",
    "src/main.js",
    "src/index.tsx",
    "src/index.jsx",
    "src/index.js",
    "app/page.tsx",
    "app/layout.tsx",
    "pages/index.tsx",
    "pages/index.js",
    "src/App.tsx",
    "src/App.vue",
)
_CLIENT_CALL = re.compile(
    r"\bfetch\(\s*[`'\"](?P<url>[^`'\"]*)|axios\.(?:get|post|put|patch|delete)\(\s*[`'\"](?P<url2>[^`'\"]*)|"
    r"\b(?:api|client|http)\.(?:get|post|put|patch|delete)\(\s*[`'\"](?P<url3>[^`'\"]*)"
)

#: File stems that say nothing about the resource they serve.
_GENERIC_STEMS = frozenset(
    {"main", "app", "routes", "urls", "views", "server", "index", "api", "router", "handlers"}
)


class HttpAnalyzer:
    name = "http"

    def analyze(self, context: AnalysisContext) -> None:
        self._apis(context)
        self._frontends(context)

    # -- APIs --------------------------------------------------------------------

    def _apis(self, context: AnalysisContext) -> None:
        servers: list[ArchitectureNode] = []
        apis: list[ArchitectureNode] = []

        for path, text in context.index.source_files():
            routes: list[tuple[int, str, str]] = []
            for pattern in _ROUTES:
                for line, match in find_all(pattern, text):
                    verb = (match.groupdict().get("verb") or "ANY").upper().replace("MAPPING", "")
                    if verb in {"ROUTE", "API_ROUTE", "HANDLEFUNC", "HANDLE", "REQUEST", "ALL"}:
                        verb = "ANY"
                    routes.append((line, verb, match.group("path") or "/"))

            server = _SERVER.search(text)
            if not routes and server is None:
                continue

            if routes:
                routes.sort()
                first_line = routes[0][0]
                node = context.graph.add_node(
                    ArchitectureNode(
                        id=f"api:{slug(path)}",
                        label=_api_label(path, [r[2] for r in routes]),
                        category=NodeCategory.API,
                        source_references=[
                            context.index.ref(path, first_line, end_line=routes[-1][0])
                        ],
                        properties={
                            "routes": [f"{verb} {route}" for _, verb, route in routes[:12]],
                            "route_count": len(routes),
                        },
                        confidence=0.85,
                        detected_because=f"{len(routes)} HTTP route(s) are defined in {path}",
                    )
                )
                apis.append(node)

            if server is not None:
                line = line_of(text, server.start())
                if routes:
                    # One file both creates the app and routes it: one component.
                    node = context.graph.node(f"api:{slug(path)}")
                    if node is not None:
                        node.properties["creates_server"] = True
                        servers.append(node)
                    continue
                server_node = context.graph.add_node(
                    ArchitectureNode(
                        id=f"server:{slug(path)}",
                        label="HTTP server",
                        category=NodeCategory.SERVICE,
                        subtype="http_server",
                        source_references=[context.index.ref(path, line)],
                        confidence=0.85,
                        detected_because=f"{path} creates the web application",
                    )
                )
                servers.append(server_node)

        # A server routes to the APIs its own file mounts -- or that a router
        # module it imports mounts (main.py -> api/main.py -> routes/items.py).
        # Each edge cites the mount line that names the API's module.
        for server in [s for s in servers if s.category is NodeCategory.SERVICE]:
            for path in server.paths:
                for aggregator in _mounting_files(context, path):
                    text = context.index.text(aggregator) or ""
                    for api in apis:
                        module = stem(api.source_references[0].path)
                        line = _mount_line(text, module)
                        if line is not None:
                            context.graph.add_edge(
                                ArchitectureEdge(
                                    source=server.id,
                                    target=api.id,
                                    type=EdgeType.ROUTES_TO,
                                    evidence=[context.index.ref(aggregator, line)],
                                    confidence=0.75,
                                )
                            )

    # -- frontends ----------------------------------------------------------------

    def _frontends(self, context: AnalysisContext) -> None:
        for manifest_path in sorted(
            p for p in context.index.all_paths if p.endswith("package.json")
        ):
            if "node_modules/" in manifest_path:
                continue
            text = context.index.text(manifest_path)
            if not text:
                continue
            try:
                declared = json.loads(text)
            except ValueError:
                continue
            deps = {**declared.get("dependencies", {}), **declared.get("devDependencies", {})}
            library = next((name for name in _UI_LIBRARIES if name in deps), None)
            if library is None:
                continue

            root = manifest_path.rsplit("/", 1)[0] + "/" if "/" in manifest_path else ""
            dep_line = next(
                (i for i, line in enumerate(text.splitlines(), start=1) if f'"{library}"' in line),
                None,
            )
            references = [context.index.ref(manifest_path, dep_line)]
            entry = next(
                (root + e for e in _FRONTEND_ENTRIES if root + e in context.index.all_paths), None
            )
            if entry is None:
                # A package that depends on a UI library but has no application
                # entry (email templates, a component kit) is not a frontend users load.
                continue
            references.append(context.index.ref(entry, 1))

            technology = _UI_LIBRARIES[library]
            frontend = context.graph.add_node(
                ArchitectureNode(
                    id=f"frontend:{slug(root or 'app')}",
                    label=f"{technology} frontend",
                    category=NodeCategory.FRONTEND,
                    technology=technology,
                    source_references=references,
                    properties={"package": declared.get("name", manifest_path)},
                    confidence=0.85,
                    detected_because=f"{manifest_path} declares {library}",
                )
            )
            browser = context.graph.add_node(
                ArchitectureNode(
                    id=f"input:browser-{slug(root or 'app')}",
                    label="User's browser",
                    category=NodeCategory.INPUT,
                    source_references=[references[-1]],
                    properties={"actor": True},
                    confidence=0.7,
                    detected_because="a browser frontend is defined, so requests start there",
                )
            )
            context.graph.add_edge(
                ArchitectureEdge(
                    source=browser.id,
                    target=frontend.id,
                    type=EdgeType.CALLS,
                    evidence=[references[-1]],
                    description="loads the frontend",
                )
            )
            self._client_calls(context, frontend, root)

    def _client_calls(
        self, context: AnalysisContext, frontend: ArchitectureNode, root: str
    ) -> None:
        apis = [n for n in context.graph.nodes if n.category is NodeCategory.API]
        servers = [n for n in context.graph.nodes if n.subtype == "http_server"]
        for path, text in context.index.source_files():
            if root and not path.startswith(root):
                continue
            for line, match in find_all(_CLIENT_CALL, text):
                url = match.group("url") or match.group("url2") or match.group("url3") or ""
                target = _match_api(url, apis) or (apis[0] if len(apis) == 1 else None)
                target = target or (servers[0] if len(servers) == 1 else None)
                if target is None:
                    continue
                context.graph.add_edge(
                    ArchitectureEdge(
                        source=frontend.id,
                        target=target.id,
                        type=EdgeType.CALLS,
                        evidence=[context.index.ref(path, line)],
                        description=f"requests {url}" if url else "calls the backend",
                    )
                )


_IMPORT = re.compile(
    r"^\s*(?:from\s+(?P<from>[\w.]+)\s+import\s+(?P<names>[^\n]+)|import\s+(?P<mod>[\w.]+))", re.M
)


def _mounting_files(context: AnalysisContext, path: str, *, depth: int = 2) -> list[str]:
    """*path*, plus the router modules it imports that themselves mount routers."""
    found = [path]
    frontier = [path]
    for _ in range(depth):
        next_frontier: list[str] = []
        for current in frontier:
            text = context.index.text(current) or ""
            for match in _IMPORT.finditer(text):
                module = match.group("from") or match.group("mod") or ""
                candidate = module.replace(".", "/") + ".py"
                for path_option in _module_paths(context, candidate):
                    body = context.index.text(path_option) or ""
                    if path_option not in found and _MOUNT.search(body):
                        found.append(path_option)
                        next_frontier.append(path_option)
        frontier = next_frontier
    return found


def _module_paths(context: AnalysisContext, candidate: str) -> list[str]:
    """Repository paths a dotted import may refer to, wherever the package root is."""
    return [p for p in context.index.all_paths if p == candidate or p.endswith("/" + candidate)]


def _mount_line(text: str, module: str) -> int | None:
    """The line that mounts *module*'s routes, if *text* has one."""
    for number, line in enumerate(text.splitlines(), start=1):
        if _MOUNT.search(line) and re.search(rf"\b{re.escape(module)}\b", line):
            return number
    return None


def _resource(route: str) -> str | None:
    for part in route.strip("/").split("/"):
        if (
            part
            and not part.startswith(("{", ":", "<", "(", "$"))
            and part not in {"api", "v1", "v2", "v3"}
        ):
            return part
    return None


def _api_label(path: str, routes: list[str]) -> str:
    resources = Counter(r for r in (_resource(route) for route in routes) if r)
    if resources:
        common, count = resources.most_common(1)[0]
        if count * 2 >= len(routes):
            return f"{title(common)} API"
    name = stem(path)
    return "HTTP API" if name.lower() in _GENERIC_STEMS else f"{title(name)} API"


def _match_api(url: str, apis: list[ArchitectureNode]) -> ArchitectureNode | None:
    resource = _resource(url.split("?", 1)[0].replace("${", "{"))
    if not resource:
        return None
    for api in apis:
        routes = api.properties.get("routes", [])
        if isinstance(routes, list) and any(
            _resource(r.split(" ", 1)[-1]) == resource for r in routes
        ):
            return api
    return None
