"""Entry point and test detection.

Entry points are found by reading files as text and matching known patterns.
Nothing is imported or invoked, so a repository cannot run code through being
ingested.
"""

from __future__ import annotations

import json
import re
import tomllib

from repolab.ingest.content import ReadBudget, excerpt_at, find_lines, read_text, reference
from repolab.ingest.detectors.base import DetectionContext, DetectorRun
from repolab.ingest.priority import prioritized
from repolab.manifest.models import EntryPoint, EntryPointKind

_MAIN_GUARD = re.compile(r"""if\s+__name__\s*==\s*['"]__main__['"]""")
_JVM_MAIN = re.compile(r"(?:public\s+static\s+void\s+main|def\s+main\s*\(args)")
# Handles both the setup.py dict form, `{"console_scripts": [...]}`, and the
# setup.cfg form, `console_scripts =\n    tool = tool.cli:main`.
_CONSOLE_SCRIPT = re.compile(r"""console_scripts['"]?\s*[:=]\s*\[?(?P<body>[^\]}]*)""", re.S)
# Entries appear quoted in setup.py and bare in setup.cfg.
_SCRIPT_ENTRY = re.compile(r"""['"]?(?P<name>[\w.-]+)\s*=\s*(?P<target>[\w.:_]+)['"]?""")
_SPARK_SUBMIT = re.compile(r"spark-submit[^\n]*", re.I)

#: Directory or filename markers that indicate tests.
TEST_DIR_NAMES = frozenset({"tests", "test", "testing", "spec", "specs", "it"})
TEST_CONFIG_FILES = frozenset(
    {"pytest.ini", "tox.ini", "conftest.py", "jest.config.js", "noxfile.py"}
)


def detect_entrypoints(
    context: DetectionContext, budget: ReadBudget
) -> tuple[DetectorRun, list[EntryPoint]]:
    """Find how the software is intended to be started."""
    run = DetectorRun()
    entrypoints: dict[str, EntryPoint] = {}

    def remember(entry: EntryPoint) -> None:
        entrypoints.setdefault(entry.name, entry)

    for entry in context.tree.files:
        lowered = entry.name.lower()

        # package.json scripts and bin declarations
        if lowered == "package.json":
            text = read_text(context.path_for(entry.path), budget=budget)
            if text:
                for name, command, kind in _package_json_entries(text):
                    remember(EntryPoint(name=name, kind=kind, command=command, path=entry.path))
                    run.add(
                        "entrypoint",
                        name,
                        detail=command,
                        references=[reference(entry.path, commit_sha=context.commit_sha)],
                    )
            continue

        # Python console_scripts, declared in pyproject or setup.py
        if lowered in {"pyproject.toml", "setup.py", "setup.cfg"}:
            text = read_text(context.path_for(entry.path), budget=budget)
            if text:
                for name, target in _console_scripts(lowered, text):
                    remember(
                        EntryPoint(
                            name=name,
                            kind=EntryPointKind.CLI_SCRIPT,
                            command=name,
                            path=entry.path,
                        )
                    )
                    run.add(
                        "entrypoint",
                        name,
                        detail=f"console_scripts -> {target}",
                        references=[reference(entry.path, commit_sha=context.commit_sha)],
                    )

        # Shell scripts are entry points and CLI surface evidence.
        if entry.extension in {".sh", ".bash"}:
            remember(
                EntryPoint(
                    name=entry.name,
                    kind=EntryPointKind.CLI_SCRIPT,
                    command=f"./{entry.path}",
                    path=entry.path,
                )
            )
            run.add(
                "shell_script",
                entry.path,
                references=[reference(entry.path, commit_sha=context.commit_sha)],
            )

    # Python and JVM main entry points, over a bounded set of source files scanned
    # most-informative-first so a capped budget still finds the real entry points.
    scanned = 0
    candidates = prioritized(
        [
            e
            for e in context.tree.files
            if e.extension in {".py", ".java", ".scala", ".kt"} and not _looks_like_test(e.path)
        ]
    )
    for entry in candidates:
        if scanned >= budget.limits.max_scanned_files:
            budget.report.record("max_scanned_files")
            break

        text = read_text(context.path_for(entry.path), budget=budget)
        if text is None:
            continue
        scanned += 1

        if entry.extension == ".py":
            match = _MAIN_GUARD.search(text)
            if match:
                line = text[: match.start()].count("\n") + 1
                name = entry.path.rsplit("/", 1)[-1].removesuffix(".py")
                remember(
                    EntryPoint(
                        name=name,
                        kind=EntryPointKind.MODULE,
                        command=f"python {entry.path}",
                        path=entry.path,
                    )
                )
                run.add(
                    "entrypoint",
                    name,
                    detail="__main__ guard",
                    references=[
                        reference(
                            entry.path,
                            commit_sha=context.commit_sha,
                            line=line,
                            excerpt=excerpt_at(text, line),
                        )
                    ],
                )
        elif _JVM_MAIN.search(text):
            line = text[: _JVM_MAIN.search(text).start()].count("\n") + 1
            name = entry.name.rsplit(".", 1)[0]
            remember(
                EntryPoint(
                    name=name,
                    kind=EntryPointKind.MODULE,
                    command=f"run {name}",
                    path=entry.path,
                )
            )
            run.add(
                "entrypoint",
                name,
                detail="main method",
                references=[reference(entry.path, commit_sha=context.commit_sha, line=line)],
            )

    # spark-submit invocations documented in READMEs or scripts.
    for entry in context.tree.files:
        if entry.extension not in {".md", ".rst", ".sh", ".bash", ".txt"}:
            continue
        text = read_text(context.path_for(entry.path), budget=budget)
        if text is None:
            continue
        for line, content in find_lines(text, ("spark-submit",), limit=2):
            command = _SPARK_SUBMIT.search(content)
            remember(
                EntryPoint(
                    name=f"spark-submit ({entry.name})",
                    kind=EntryPointKind.SPARK_JOB,
                    command=(command.group(0) if command else content)[:200],
                    path=entry.path,
                )
            )
            run.add(
                "spark_submit",
                content[:160],
                detail=entry.path,
                references=[
                    reference(entry.path, commit_sha=context.commit_sha, line=line, excerpt=content)
                ],
            )

    return run, list(entrypoints.values())


def detect_tests(context: DetectionContext) -> DetectorRun:
    """Record the repository's tests, from paths alone."""
    run = DetectorRun()

    test_files = [e for e in context.tree.files if _looks_like_test(e.path)]
    if test_files:
        run.add(
            "tests",
            f"{len(test_files)} test files",
            detail=test_files[0].path,
            references=[reference(e.path, commit_sha=context.commit_sha) for e in test_files[:5]],
        )

    for entry in context.tree.files:
        if entry.name.lower() in TEST_CONFIG_FILES:
            run.add(
                "test_config",
                entry.path,
                references=[reference(entry.path, commit_sha=context.commit_sha)],
            )
    return run


def _looks_like_test(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or name.endswith("Test.java")
        or name.endswith(".test.ts")
        or name.endswith(".spec.ts")
    )


def _package_json_entries(text: str) -> list[tuple[str, str, EntryPointKind]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    found: list[tuple[str, str, EntryPointKind]] = []
    for name, command in (data.get("scripts") or {}).items():
        if not isinstance(command, str):
            continue
        kind = (
            EntryPointKind.WEB_SERVER
            if any(hint in command for hint in ("dev", "start", "serve", "next"))
            else EntryPointKind.CLI_SCRIPT
        )
        found.append((f"npm run {name}", command, kind))

    bins = data.get("bin")
    if isinstance(bins, dict):
        for name, target in bins.items():
            found.append((str(name), str(target), EntryPointKind.CLI_SCRIPT))
    elif isinstance(bins, str):
        found.append((str(data.get("name", "bin")), bins, EntryPointKind.CLI_SCRIPT))
    return found


def _console_scripts(filename: str, text: str) -> list[tuple[str, str]]:
    """Extract ``console_scripts`` without executing the file."""
    found: list[tuple[str, str]] = []

    if filename == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError):
            return []
        scripts = data.get("project", {}).get("scripts", {}) or {}
        poetry_scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {}) or {}
        for name, target in {**scripts, **poetry_scripts}.items():
            found.append((str(name), str(target)))
        return found

    match = _CONSOLE_SCRIPT.search(text)
    if match:
        for entry in _SCRIPT_ENTRY.finditer(match.group("body")):
            found.append((entry.group("name"), entry.group("target")))
    return found
