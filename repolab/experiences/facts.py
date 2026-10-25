"""What a repository says about how it is *used*, read once during investigation.

Experiences are decided later, from the manifest, without the clone. So the few
facts they need from the tree are gathered here, each with its file and line:

* whether the repository **ships a Spark runtime** -- ``bin/spark-submit`` and
  ``bin/pyspark`` in the tree, a build file, the README's own build command, the
  Java version the build declares, and the README's quickstart snippet;
* whether it is a **Python package** -- its distribution and import names, the
  Python it requires, and the README or ``examples/`` code that uses it.

Nothing here runs anything, and nothing is inferred beyond what the files say.
"""

from __future__ import annotations

import re
import tomllib
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from repolab.evidence.models import CodeReference

if TYPE_CHECKING:
    from repolab.ingest.detectors.base import DetectionContext

_FENCE = re.compile(r"^```(\w*)\s*\n(.*?)^```", re.M | re.S)
_MVN_BUILD = re.compile(r"^\s*(\./build/mvn[^\n`]*\bpackage\b[^\n`]*)$", re.M)
_JAVA_VERSION = re.compile(r"<java\.version>\s*([\d.]+)\s*</java\.version>")
_PROJECT_VERSION = re.compile(
    r"<artifactId>[\w.-]*parent[\w.-]*</artifactId>\s*<version>([^<]+)</version>"
)
_REQUIRES_PYTHON = re.compile(r"(\d)\.(\d+)")


class Snippet(BaseModel):
    code: str
    language: str
    reference: CodeReference


class SparkRuntimeFacts(BaseModel):
    """Evidence that the repository *is* a Spark distribution that can be built."""

    spark_submit: str
    pyspark: str | None = None
    build_command: str | None = None
    build_command_ref: CodeReference | None = None
    java_version: str | None = None
    java_version_ref: CodeReference | None = None
    version: str | None = None
    version_ref: CodeReference | None = None
    quickstart: Snippet | None = None
    compiler_heap_gb: float | None = None
    """The heap the build forks its Scala compiler with (``<jvmArg>-Xmx4g``)."""

    compiler_heap_ref: CodeReference | None = None


class PythonPackageFacts(BaseModel):
    distribution: str
    import_name: str
    package_path: str
    requires_python: str | None = None
    manifest: CodeReference
    snippets: list[Snippet] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class RepositoryFacts(BaseModel):
    spark: SparkRuntimeFacts | None = None
    python: PythonPackageFacts | None = None
    readme: str | None = None


def gather_facts(context: DetectionContext) -> RepositoryFacts:
    from repolab.ingest.content import ReadBudget, read_text

    budget = ReadBudget()
    paths = {entry.path for entry in context.tree.files}
    commit = context.commit_sha

    def read(path: str) -> str | None:
        if path not in paths:
            return None
        return read_text(context.path_for(path), budget=budget, reserved=True)

    readme_path = next(
        (p for p in sorted(paths) if "/" not in p and p.lower().startswith("readme")), None
    )
    readme = read(readme_path) if readme_path else None
    facts = RepositoryFacts(readme=readme_path)
    facts.spark = _spark(paths, read, readme, readme_path, commit)
    facts.python = _python(paths, read, readme, readme_path, commit)
    return facts


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _spark(paths, read, readme, readme_path, commit) -> SparkRuntimeFacts | None:
    if "bin/spark-submit" not in paths:
        return None
    build_files = {"pom.xml", "build.sbt"} & paths
    if not build_files:
        return None
    facts = SparkRuntimeFacts(
        spark_submit="bin/spark-submit",
        pyspark="bin/pyspark" if "bin/pyspark" in paths else None,
    )
    if readme:
        match = _MVN_BUILD.search(readme)
        if match:
            facts.build_command = match.group(1).strip()
            facts.build_command_ref = CodeReference(
                path=readme_path, start_line=_line(readme, match.start(1)), commit_sha=commit
            )
        for fence in _FENCE.finditer(readme):
            body = fence.group(2)
            if ">>> spark." in body or (fence.group(1) == "python" and "spark." in body):
                code = (
                    "\n".join(
                        line.split(">>>", 1)[1].strip()
                        for line in body.splitlines()
                        if ">>>" in line
                    )
                    or body.strip()
                )
                facts.quickstart = Snippet(
                    code=code,
                    language="python",
                    reference=CodeReference(
                        path=readme_path,
                        start_line=_line(readme, fence.start(2)),
                        commit_sha=commit,
                    ),
                )
                break
    pom = read("pom.xml")
    if pom:
        java = _JAVA_VERSION.search(pom)
        if java:
            facts.java_version = java.group(1)
            facts.java_version_ref = CodeReference(
                path="pom.xml", start_line=_line(pom, java.start(1)), commit_sha=commit
            )
        heap = re.search(r"<jvmArg>-Xmx(\d+)([gGmM])</jvmArg>", pom)
        if heap:
            amount = float(heap.group(1))
            facts.compiler_heap_gb = amount if heap.group(2).lower() == "g" else amount / 1024
            facts.compiler_heap_ref = CodeReference(
                path="pom.xml", start_line=_line(pom, heap.start()), commit_sha=commit
            )
        version = _PROJECT_VERSION.search(pom)
        if version:
            facts.version = version.group(1).strip()
            facts.version_ref = CodeReference(
                path="pom.xml", start_line=_line(pom, version.start(1)), commit_sha=commit
            )
    return facts


def _python(paths, read, readme, readme_path, commit) -> PythonPackageFacts | None:
    manifest_path = (
        "pyproject.toml"
        if "pyproject.toml" in paths
        else ("setup.py" if "setup.py" in paths else None)
    )
    if manifest_path is None:
        return None
    text = read(manifest_path) or ""
    distribution = None
    requires = None
    if manifest_path == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project") or {}
        distribution = project.get("name") or (data.get("tool", {}).get("poetry", {}) or {}).get(
            "name"
        )
        requires = project.get("requires-python")
    else:
        match = re.search(r"name\s*=\s*['\"]([\w.-]+)['\"]", text)
        distribution = match.group(1) if match else None
        req = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", text)
        requires = req.group(1) if req else None
    if not distribution:
        return None

    candidates = {distribution.replace("-", "_").lower(), distribution.lower()}
    package_path = None
    import_name = None
    for root in ("", "src/"):
        for name in candidates:
            if f"{root}{name}/__init__.py" in paths:
                package_path, import_name = f"{root}{name}", name
                break
        if import_name:
            break
    if import_name is None:
        return None

    snippets: list[Snippet] = []
    pattern = re.compile(rf"^\s*(import {import_name}\b|from {import_name}[\s.])", re.M)
    if readme:
        for fence in _FENCE.finditer(readme):
            if fence.group(1) in {"python", "py", "pycon", ""} and pattern.search(fence.group(2)):
                body = fence.group(2)
                if ">>>" in body:
                    body = "\n".join(
                        line.split(">>>", 1)[1].lstrip(" ") if ">>>" in line else ""
                        for line in body.splitlines()
                        if ">>>" in line or line.startswith("...")
                    )
                snippets.append(
                    Snippet(
                        code=body.strip(),
                        language="python",
                        reference=CodeReference(
                            path=readme_path,
                            start_line=_line(readme, fence.start(2)),
                            commit_sha=commit,
                        ),
                    )
                )
            if len(snippets) >= 3:
                break
    examples = sorted(p for p in paths if re.match(r"^(examples?|demo)/[\w.-]+\.py$", p))[:5]

    return PythonPackageFacts(
        distribution=distribution,
        import_name=import_name,
        package_path=package_path,
        requires_python=requires,
        manifest=CodeReference(path=manifest_path, commit_sha=commit),
        snippets=snippets,
        examples=examples,
    )


def python_image(requires: str | None) -> tuple[str, str]:
    """The python image to install into, and why."""
    if requires:
        found = _REQUIRES_PYTHON.findall(requires)
        if found:
            major, minor = found[0]
            minor_i = max(int(minor), 9)
            return f"python:{major}.{minor_i}-slim", f'requires-python is "{requires}"'
    return "python:3.12-slim", "no Python version is declared; using 3.12"
