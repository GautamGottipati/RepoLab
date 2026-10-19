"""Dependency and build-file inspection.

Manifest files are parsed, never executed. ``setup.py`` in particular is read as
text and pattern-matched: importing or running it would execute repository code.
"""

from __future__ import annotations

import json
import re
import tomllib

from repolab.ingest.content import ReadBudget, find_lines, read_text, reference
from repolab.ingest.detectors.base import DetectionContext, DetectorRun
from repolab.ingest.priority import prioritized
from repolab.manifest.models import Dependency

#: Dependency manifests by filename, with the ecosystem each implies.
MANIFEST_ECOSYSTEMS: dict[str, str] = {
    "requirements.txt": "pypi",
    "pyproject.toml": "pypi",
    "setup.py": "pypi",
    "setup.cfg": "pypi",
    "pipfile": "pypi",
    "package.json": "npm",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "build.sbt": "sbt",
    "go.mod": "go",
    "cargo.toml": "cargo",
    "gemfile": "rubygems",
}

#: Build tooling by filename. Recorded as signals only -- nothing is ever run.
BUILD_FILES: dict[str, str] = {
    "makefile": "make",
    "dockerfile": "docker",
    "docker-compose.yml": "docker-compose",
    "docker-compose.yaml": "docker-compose",
    "compose.yml": "docker-compose",
    "compose.yaml": "docker-compose",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "gradlew": "gradle",
    "build.sbt": "sbt",
    "setup.py": "setuptools",
    "pyproject.toml": "python-build",
    "cmakelists.txt": "cmake",
    "pipfile": "pipenv",
}

#: Dependency name -> framework label, used to populate manifest frameworks.
FRAMEWORK_PACKAGES: dict[str, str] = {
    "pyspark": "Spark",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "airflow": "Airflow",
    "apache-airflow": "Airflow",
    "cassandra-driver": "Cassandra",
    "kafka-python": "Kafka",
    "confluent-kafka": "Kafka",
    "next": "Next.js",
    "react": "React",
    "express": "Express",
    "vue": "Vue",
    "click": "Click",
    "typer": "Typer",
    "pandas": "pandas",
    "sqlalchemy": "SQLAlchemy",
    "celery": "Celery",
    "dbt-core": "dbt",
}

_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<spec>(==|>=|<=|~=|!=|>|<).*)?"
)
_SETUP_INSTALL_REQUIRES = re.compile(r"install_requires\s*=\s*\[(?P<body>[^\]]*)\]", re.S)
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")
_MAVEN_ARTIFACT = re.compile(
    r"<dependency>\s*<groupId>(?P<group>[^<]+)</groupId>\s*"
    r"<artifactId>(?P<artifact>[^<]+)</artifactId>"
    r"(?:\s*<version>(?P<version>[^<]+)</version>)?",
    re.S,
)
_GRADLE_DEP = re.compile(
    r"""(?:implementation|api|compile|testImplementation)\s*\(?['"]"""
    r"""(?P<coord>[^'"]+)['"]"""
)


def _dependency(name: str, ecosystem: str, spec: str | None = None) -> Dependency:
    return Dependency(
        name=name.strip(), ecosystem=ecosystem, version_spec=(spec or None) and spec.strip()
    )


def detect_dependencies(
    context: DetectionContext, budget: ReadBudget
) -> tuple[DetectorRun, list[Dependency]]:
    """Parse dependency manifests and build files.

    Returns the findings and the deduplicated dependency list.
    """
    run = DetectorRun()
    dependencies: dict[tuple[str, str], Dependency] = {}

    def remember(dep: Dependency, ref_path: str, line: int | None = None) -> None:
        key = (dep.ecosystem, dep.name.lower())
        dependencies.setdefault(key, dep)
        framework = FRAMEWORK_PACKAGES.get(dep.name.lower())
        if framework:
            run.add(
                "framework",
                framework,
                detail=f"declared as {dep.name}",
                references=[reference(ref_path, commit_sha=context.commit_sha, line=line)],
            )

    # Prioritised, so the reservation is spent on the manifests that define the
    # system rather than on whichever sorted first. On Airflow that is the root
    # and `airflow-core` manifests ahead of 200-odd provider packages.
    for entry in prioritized(context.tree.files):
        lowered = entry.name.lower()

        if lowered in BUILD_FILES or lowered.startswith("dockerfile"):
            tool = BUILD_FILES.get(lowered, "docker")
            run.add(
                "build_file",
                tool,
                detail=entry.path,
                references=[reference(entry.path, commit_sha=context.commit_sha)],
            )

        ecosystem = MANIFEST_ECOSYSTEMS.get(lowered)
        if ecosystem is None and lowered.startswith("requirements") and entry.extension == ".txt":
            ecosystem = "pypi"
        if ecosystem is None:
            continue

        run.add(
            "dependency_manifest",
            entry.path,
            detail=ecosystem,
            references=[reference(entry.path, commit_sha=context.commit_sha)],
        )

        # Reserved: a manifest is the only evidence of a framework when the
        # scan never reaches the application module, so it must not be the read
        # that loses to a large source tree.
        text = read_text(context.path_for(entry.path), budget=budget, reserved=True)
        if text is None:
            continue

        for dep, line in _parse_manifest(lowered, text):
            remember(dep, entry.path, line)

    return run, list(dependencies.values())


def _parse_manifest(filename: str, text: str) -> list[tuple[Dependency, int | None]]:
    """Dispatch to the right parser. Unknown shapes yield nothing rather than guesses."""
    if filename.startswith("requirements"):
        return _parse_requirements(text)
    if filename == "pyproject.toml":
        return _parse_pyproject(text)
    if filename == "pipfile":
        return _parse_pipfile(text)
    if filename in {"setup.py", "setup.cfg"}:
        return _parse_setup(text)
    if filename == "package.json":
        return _parse_package_json(text)
    if filename == "pom.xml":
        return _parse_pom(text)
    if filename.startswith("build.gradle"):
        return _parse_gradle(text)
    if filename == "go.mod":
        return _parse_go_mod(text)
    return []


def _parse_requirements(text: str) -> list[tuple[Dependency, int | None]]:
    found: list[tuple[Dependency, int | None]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match:
            found.append((_dependency(match.group("name"), "pypi", match.group("spec")), number))
    return found


def _parse_pyproject(text: str) -> list[tuple[Dependency, int | None]]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    found: list[tuple[Dependency, int | None]] = []
    project = data.get("project", {})
    for raw in project.get("dependencies", []) or []:
        if isinstance(raw, str):
            match = _REQUIREMENT.match(raw)
            if match:
                found.append((_dependency(match.group("name"), "pypi", match.group("spec")), None))

    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, spec in (poetry or {}).items():
        if name.lower() == "python":
            continue
        found.append((_dependency(name, "pypi", spec if isinstance(spec, str) else None), None))
    return found


def _parse_pipfile(text: str) -> list[tuple[Dependency, int | None]]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    found: list[tuple[Dependency, int | None]] = []
    for section in ("packages", "dev-packages"):
        for name, spec in (data.get(section) or {}).items():
            version = spec if isinstance(spec, str) and spec not in {"*"} else None
            found.append((_dependency(name, "pypi", version), None))
    return found


def _parse_setup(text: str) -> list[tuple[Dependency, int | None]]:
    """Read ``setup.py``/``setup.cfg`` as *text*. Never import or execute it."""
    found: list[tuple[Dependency, int | None]] = []
    match = _SETUP_INSTALL_REQUIRES.search(text)
    if match:
        for raw in _QUOTED.findall(match.group("body")):
            requirement = _REQUIREMENT.match(raw)
            if requirement:
                found.append(
                    (
                        _dependency(requirement.group("name"), "pypi", requirement.group("spec")),
                        None,
                    )
                )
    return found


def _parse_package_json(text: str) -> list[tuple[Dependency, int | None]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    found: list[tuple[Dependency, int | None]] = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            found.append((_dependency(name, "npm", spec if isinstance(spec, str) else None), None))
    return found


def _parse_pom(text: str) -> list[tuple[Dependency, int | None]]:
    found: list[tuple[Dependency, int | None]] = []
    for match in _MAVEN_ARTIFACT.finditer(text):
        name = f"{match.group('group').strip()}:{match.group('artifact').strip()}"
        found.append((_dependency(name, "maven", match.group("version")), None))
    return found


def _parse_gradle(text: str) -> list[tuple[Dependency, int | None]]:
    found: list[tuple[Dependency, int | None]] = []
    for match in _GRADLE_DEP.finditer(text):
        coordinate = match.group("coord")
        parts = coordinate.split(":")
        if len(parts) >= 2:
            found.append(
                (
                    _dependency(
                        f"{parts[0]}:{parts[1]}",
                        "gradle",
                        parts[2] if len(parts) > 2 else None,
                    ),
                    None,
                )
            )
    return found


def _parse_go_mod(text: str) -> list[tuple[Dependency, int | None]]:
    found: list[tuple[Dependency, int | None]] = []
    in_block = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        candidate = (
            line[len("require ") :].strip()
            if line.startswith("require ")
            else (line if in_block else "")
        )
        if not candidate or candidate.startswith("//"):
            continue
        parts = candidate.split()
        if len(parts) >= 2:
            found.append((_dependency(parts[0], "go", parts[1]), number))
    return found


def detect_configuration(context: DetectionContext, budget: ReadBudget) -> DetectorRun:
    """Record configuration files, favouring dedicated config directories."""
    run = DetectorRun()
    config_extensions = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".properties", ".conf"}

    for entry in context.tree.files:
        lowered = entry.name.lower()
        in_config_dir = any(
            part in {"config", "configs", "conf", "settings"}
            for part in entry.path.lower().split("/")[:-1]
        )
        is_env_example = lowered.startswith(".env") and any(
            hint in lowered for hint in ("example", "sample", "template")
        )

        if not (in_config_dir and entry.extension in config_extensions) and not is_env_example:
            continue
        if lowered in MANIFEST_ECOSYSTEMS:
            continue

        run.add(
            "configuration",
            entry.path,
            detail="config directory" if in_config_dir else "environment template",
            references=[reference(entry.path, commit_sha=context.commit_sha)],
        )

        text = read_text(context.path_for(entry.path), budget=budget)
        if text is None:
            continue
        for line, content in find_lines(text, ("partition", "shuffle", "memory", "port"), limit=2):
            run.add(
                "configuration_setting",
                content[:160],
                detail=entry.path,
                references=[
                    reference(entry.path, commit_sha=context.commit_sha, line=line, excerpt=content)
                ],
            )

    return run
