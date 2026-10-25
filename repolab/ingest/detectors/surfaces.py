"""Surface *signal* detection.

This module finds concrete, citable evidence in source files -- a pyspark import,
a route decorator, an argparse call, a REPL loop -- and records each one with the
file and line that produced it.

It deliberately does not decide anything. Turning signals into interaction
surfaces, scoring them, and recommending a primary experience is the classifier's
job, in :mod:`repolab.classify`.
"""

from __future__ import annotations

import re

from repolab.ingest.content import ReadBudget, excerpt_at, read_text, reference
from repolab.ingest.detectors.base import DetectionContext, DetectorRun
from repolab.ingest.priority import prioritized

#: Source extensions worth scanning for surface signals.
SCANNABLE_EXTENSIONS = frozenset({".py", ".scala", ".java", ".kt", ".js", ".ts", ".tsx", ".sh"})

SPARK_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("spark_import", re.compile(r"^\s*(?:from|import)\s+pyspark", re.M)),
    ("spark_session", re.compile(r"SparkSession\s*(?:\.\s*)?builder", re.S)),
    ("spark_context", re.compile(r"\bSparkContext\s*\(")),
    ("spark_scala_import", re.compile(r"import\s+org\.apache\.spark")),
    ("spark_config", re.compile(r"spark\.(?:sql|executor|driver|default)\.[A-Za-z.]+")),
)

HTTP_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fastapi_app", re.compile(r"FastAPI\s*\(")),
    ("flask_app", re.compile(r"Flask\s*\(\s*__name__")),
    ("route_decorator", re.compile(r"@\w+\.(?:get|post|put|patch|delete|route)\s*\(")),
    ("django_urls", re.compile(r"urlpatterns\s*=")),
    ("express_app", re.compile(r"express\s*\(\s*\)")),
    ("uvicorn_run", re.compile(r"uvicorn\.run\s*\(")),
    (
        "frontend_app",
        re.compile(
            r"\.bootstrapModule\s*\(|\bbootstrapApplication\s*\(|"
            r"ReactDOM\.(?:createRoot|render|hydrateRoot)\s*\(|\bcreateRoot\s*\(\s*document\b|"
            r"\bcreateApp\s*\([^)]*\)\s*\.mount\s*\("
        ),
    ),
)

CLI_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("argparse", re.compile(r"argparse\.ArgumentParser\s*\(")),
    ("click_command", re.compile(r"@click\.(?:command|group)\s*\(")),
    ("typer_app", re.compile(r"typer\.Typer\s*\(")),
    ("sys_argv", re.compile(r"\bsys\.argv\b")),
)

#: An interactive shell is a *different* thing from a command: it holds a session
#: open and reads successive input, which is what makes a CLI playground possible.
SHELL_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("interactive_shell", re.compile(r"\bcmd\.Cmd\b|\(\s*cmd\.Cmd\s*\)")),
    ("interactive_shell", re.compile(r"^\s*import\s+readline|from\s+prompt_toolkit", re.M)),
    (
        "interactive_shell",
        re.compile(r"while\s+True\s*:[^\n]*\n(?:[^\n]*\n){0,4}?[^\n]*input\s*\("),
    ),
)

#: A long-running server, as opposed to a batch job that exits.
SERVER_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("server_daemon", re.compile(r"class\s+\w*Daemon\b")),
    # A socket bound to an address or a server listening on a port. Not
    # JavaScript's ``fn.bind(this)``, which binds a function, not a socket.
    (
        "server_daemon",
        re.compile(
            r"\.bind\s*\(\s*\(\s*['\"]|\.listen\s*\(\s*(?:\d|port\b|PORT\b|process\.env)"
            r"|ServerSocket\s*\("
        ),
    ),
    ("server_daemon", re.compile(r"\bserve_forever\s*\(|\brun_server\s*\(")),
)

#: Ports commonly meaning an HTTP surface, used only as corroboration.
#: Built output checked into the repository: ``main-es2015.33497bada5.js``,
#: ``vendor.min.js``. Its code is a bundler's, not the project's -- a minified
#: framework's ``.listen(`` says nothing about what this repository ships.
_BUNDLE_NAME = re.compile(r"(\.min\.(?:js|mjs|css)$)|([.-][0-9a-f]{8,}\.(?:js|mjs|css)$)", re.I)

#: Lines this long are generated, not written.
_GENERATED_LINE = 1000


def is_generated_bundle(path: str, text: str | None = None) -> bool:
    """Whether *path* is bundled or minified output rather than source."""
    if _BUNDLE_NAME.search(path.rsplit("/", 1)[-1]):
        return True
    if text is None:
        return False
    lines = text.splitlines()[:20]
    return any(len(line) > _GENERATED_LINE for line in lines)


_TOOL_CONFIG = re.compile(
    r"^(karma|protractor|jest|vitest|playwright|cypress|webpack|babel|rollup|vite)\.conf(ig)?\.",
    re.I,
)

_PORT = re.compile(r"(?:port\s*[=:]\s*|EXPOSE\s+)(?P<port>\d{2,5})", re.I)


def detect_surfaces(context: DetectionContext, budget: ReadBudget) -> DetectorRun:
    """Scan a bounded set of source files for interaction-surface signals."""
    run = DetectorRun()
    scanned = 0

    candidates = prioritized(
        [
            e
            for e in context.tree.files
            if e.extension in SCANNABLE_EXTENSIONS and not is_generated_bundle(e.path)
        ]
    )
    for entry in candidates:
        if scanned >= budget.limits.max_scanned_files:
            budget.report.record("max_scanned_files")
            break

        text = read_text(context.path_for(entry.path), budget=budget)
        if text is None or is_generated_bundle(entry.path, text):
            continue
        scanned += 1

        for group in (SPARK_SIGNALS, HTTP_SIGNALS, CLI_SIGNALS, SHELL_SIGNALS, SERVER_SIGNALS):
            for kind, pattern in group:
                match = pattern.search(text)
                if match is None:
                    continue
                line = text[: match.start()].count("\n") + 1
                run.add(
                    kind,
                    match.group(0).strip()[:120],
                    detail=entry.path,
                    references=[
                        reference(
                            entry.path,
                            commit_sha=context.commit_sha,
                            line=line,
                            excerpt=excerpt_at(text, line),
                        )
                    ],
                )

        # A test runner's own port (karma, protractor...) is not the application's.
        ports = () if _TOOL_CONFIG.search(entry.name) else _PORT.finditer(text)
        for port_match in ports:
            run.add(
                "port",
                port_match.group("port"),
                detail=entry.path,
                references=[reference(entry.path, commit_sha=context.commit_sha)],
            )
            break

    return run
