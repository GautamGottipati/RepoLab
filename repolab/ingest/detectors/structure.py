"""Structural signals taken from paths alone.

These are cheap -- no file is read -- and they capture how a project is *shaped*:
the executables it ships, whether it runs as a daemon, whether it is a library
rather than an application. On a repository like ``apache/cassandra`` this is
where the strongest evidence lives: ``bin/cqlsh`` next to ``bin/cassandra`` says
"server plus interactive shell" more clearly than any single source file.
"""

from __future__ import annotations

from repolab.ingest.content import reference
from repolab.ingest.detectors.base import DetectionContext, DetectorRun

#: Directories that conventionally hold shipped executables.
EXECUTABLE_DIRECTORIES: frozenset[str] = frozenset({"bin", "sbin", "scripts", "tools"})

#: Basenames of interactive shells, keyed by the software they belong to. A file
#: called ``cqlsh`` is a REPL, not a script that happens to end in "sh".
INTERACTIVE_SHELL_NAMES: frozenset[str] = frozenset(
    {
        "cqlsh",
        "psql",
        "mysql",
        "redis-cli",
        "mongosh",
        "mongo",
        "beeline",
        "spark-shell",
        "pyspark",
        "spark-sql",
        "hbase-shell",
        "cypher-shell",
        "clickhouse-client",
        "sqlplus",
        "repl",
        "console",
        "shell",
    }
)

#: Suffixes that mark a file as an interactive client rather than a batch script.
INTERACTIVE_SHELL_SUFFIXES: tuple[str, ...] = ("sh", "-cli", "-shell", "_shell", "-repl")

#: Filename fragments that indicate a long-running server process.
DAEMON_NAME_FRAGMENTS: tuple[str, ...] = ("daemon", "server", "service")

#: Script names that start or stop a service.
SERVICE_CONTROL_PREFIXES: tuple[str, ...] = ("start", "stop", "restart", "run-server")

#: Extensions of files that are run directly: scripts and extensionless executables.
_SCRIPT_EXTENSIONS: frozenset[str] = frozenset({"", ".sh", ".bash", ".cmd", ".bat", ".ps1"})


def _names_daemon(stem: str, extension: str) -> bool:
    """Whether a file's name says it is, or starts, a long-running service.

    "daemon" does so anywhere (``CassandraDaemon.java``). "server" and "service"
    only on a script or executable: in source code they name classes and
    modules -- an Angular ``auth.service.ts`` is dependency injection, not a
    daemon.
    """
    if "daemon" in stem:
        return True
    if extension not in _SCRIPT_EXTENSIONS:
        return False
    return any(fragment in stem for fragment in DAEMON_NAME_FRAGMENTS) or stem.startswith(
        SERVICE_CONTROL_PREFIXES
    )


#: Extensions that are never shipped executables even inside bin/.
_NON_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".rst", ".xml", ".json", ".yaml", ".yml", ".sample", ".template", ".in"}
)


def _in_executable_directory(path: str) -> bool:
    segments = path.lower().split("/")[:-1]
    return any(segment in EXECUTABLE_DIRECTORIES for segment in segments)


def _looks_interactive(stem: str) -> bool:
    """Whether *stem* names an interactive shell."""
    if stem in INTERACTIVE_SHELL_NAMES:
        return True
    # `cqlsh` matches by name; `mytoolsh` by suffix. A bare "sh" is not enough on
    # its own -- "bash" and "publish" would both match -- so require length.
    return len(stem) > 3 and stem.endswith(INTERACTIVE_SHELL_SUFFIXES)


def detect_structure(context: DetectionContext) -> DetectorRun:
    """Record structural signals for the classifier.

    Reads no file contents, so this runs regardless of the read budget.
    """
    run = DetectorRun()
    tree = context.tree
    sha = context.commit_sha

    notebooks = tree.by_extension("ipynb")
    for entry in notebooks[:5]:
        run.add(
            "notebook_file",
            entry.path,
            references=[reference(entry.path, commit_sha=sha)],
        )

    executables: list[str] = []
    for entry in tree.files:
        name = entry.name.lower()
        stem = name.rsplit(".", 1)[0] if "." in name else name

        if _looks_interactive(stem):
            run.add(
                "interactive_shell",
                entry.path,
                detail=f"{stem} is an interactive client",
                references=[reference(entry.path, commit_sha=sha)],
            )

        if _names_daemon(stem, entry.extension):
            run.add(
                "server_daemon",
                entry.path,
                detail=f"{entry.name} names a long-running service",
                references=[reference(entry.path, commit_sha=sha)],
            )

        if entry.extension == ".service":
            run.add(
                "server_daemon",
                entry.path,
                detail="systemd unit",
                references=[reference(entry.path, commit_sha=sha)],
            )

        # Extensionless files under bin/ are the project's shipped commands.
        if (
            _in_executable_directory(entry.path)
            and not entry.extension
            and entry.extension not in _NON_EXECUTABLE_EXTENSIONS
        ):
            executables.append(entry.path)

    if executables:
        run.add(
            "bin_executable",
            f"{len(executables)} shipped executables",
            detail=", ".join(e.rsplit("/", 1)[-1] for e in executables[:6]),
            references=[reference(path, commit_sha=sha) for path in executables[:5]],
        )

    _detect_library_shape(context, run)
    return run


def _detect_library_shape(context: DetectionContext, run: DetectorRun) -> None:
    """Record whether the repository looks like an importable library.

    Packaging metadata plus package directories, with no shipped command, is the
    shape of something you import rather than run.
    """
    tree = context.tree
    sha = context.commit_sha

    packaging = [
        entry
        for entry in tree.files
        if entry.name.lower() in {"pyproject.toml", "setup.py", "setup.cfg", "package.json"}
    ]
    if not packaging:
        return

    packages = [entry for entry in tree.files if entry.name == "__init__.py"]
    if not packages:
        return

    run.add(
        "library_package",
        f"{len(packages)} package(s) with packaging metadata",
        detail=packaging[0].path,
        references=[
            reference(packaging[0].path, commit_sha=sha),
            *[reference(entry.path, commit_sha=sha) for entry in packages[:2]],
        ],
    )
