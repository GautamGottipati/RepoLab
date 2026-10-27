"""Which parts of the repository depend on which, from their build files.

Read deterministically from the files that *declare* dependencies -- Maven
``<dependency>`` on a sibling module's artifact, Gradle ``project(':x')``, npm
workspace packages depending on each other, Cargo ``path =`` dependencies --
and cited at the verified line. Nothing is inferred from names.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from repolab.architecture.models import ArchitectureGraph, ModuleDependency, Subsystem
from repolab.architecture.source import is_test_path
from repolab.sources import Snapshot, verify_reference

_BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json", "Cargo.toml")
MAX_BUILD_FILES = 400


def _owner(subsystems: list[Subsystem], path: str) -> Subsystem | None:
    owners = [s for s in subsystems if s.contains(path)]
    return max(owners, key=lambda s: s.depth(path)) if owners else None


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _artifact_key(name: str) -> str:
    """An artifact id without its Scala-version suffix (``_2.13`` or ``_${scala...}``),
    which modules write either literally or as a property."""
    return re.sub(r"_(?:\$\{[^}]+\}|\d+(?:\.\d+)*)$", "", name)


def _maven_artifact(text: str) -> str | None:
    """A pom's own artifactId: the first one outside <parent> and <dependencies>."""
    body = re.sub(r"<parent>[\s\S]*?</parent>", "", text)
    body = re.sub(r"<dependencies>[\s\S]*?</dependencies>", "", body)
    body = re.sub(r"<dependencyManagement>[\s\S]*?</dependencyManagement>", "", body)
    match = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", body)
    return match.group(1) if match else None


def module_dependencies(
    graph: ArchitectureGraph, snapshot: Snapshot, paths: Iterable[str]
) -> ArchitectureGraph:
    """Fill ``depends_on`` / ``used_by`` on the graph's subsystems."""
    subsystems = [s for s in graph.subsystems if s.id != "sub:other-modules"]
    if len(subsystems) < 2:
        return graph
    build = [
        p
        for p in paths
        if p.rsplit("/", 1)[-1] in _BUILD_FILES and not is_test_path(p) and "node_modules/" not in p
    ][:MAX_BUILD_FILES]
    texts = {p: t for p in build if (t := snapshot.text(p)) is not None}

    found: dict[tuple[str, str], ModuleDependency] = {}

    def add(
        source: Subsystem | None,
        target: Subsystem | None,
        via: str,
        path: str,
        offset: int,
        anchor: str,
    ) -> None:
        if source is None or target is None or source.id == target.id:
            return
        if (source.id, target.id) in found:
            return
        ref = verify_reference(snapshot, path, line=_line(texts[path], offset), expect=anchor)
        if ref.verified:
            found[(source.id, target.id)] = ModuleDependency(
                target=target.id, via=via, evidence=ref
            )

    # Maven: a module depending on a sibling module's artifact.
    artifacts: dict[str, Subsystem] = {}
    for path, text in texts.items():
        if path.endswith("pom.xml") and "/" in path:
            artifact = _maven_artifact(text)
            owner = _owner(subsystems, path)
            if artifact and owner:
                artifacts.setdefault(_artifact_key(artifact), owner)
    for path, text in texts.items():
        if not path.endswith("pom.xml"):
            continue
        source = _owner(subsystems, path)
        for block in re.finditer(r"<dependency>([\s\S]*?)</dependency>", text):
            if re.search(r"<scope>\s*test\s*</scope>", block.group(1)):
                continue  # a test-only dependency is not how the system is built
            match = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", block.group(1))
            if match and _artifact_key(match.group(1)) in artifacts:
                add(
                    source,
                    artifacts[_artifact_key(match.group(1))],
                    "maven",
                    path,
                    block.start(1) + match.start(),
                    match.group(1),
                )

    # Gradle: project(':core') / project(":libs:core").
    for path, text in texts.items():
        if not path.endswith((".gradle", ".gradle.kts")):
            continue
        source = _owner(subsystems, path)
        for match in re.finditer(r"project\(\s*['\"]:?([\w:.-]+)['\"]\s*\)", text):
            target_path = match.group(1).replace(":", "/")
            add(
                source,
                _owner(subsystems, f"{target_path}/x"),
                "gradle",
                path,
                match.start(),
                match.group(),
            )

    # npm workspaces: packages depending on each other by name.
    packages: dict[str, Subsystem] = {}
    manifests: dict[str, dict] = {}
    for path, text in texts.items():
        if path.endswith("package.json") and "/" in path:
            try:
                data = json.loads(text)
            except ValueError:
                continue
            manifests[path] = data
            owner = _owner(subsystems, path)
            if isinstance(data.get("name"), str) and owner:
                packages.setdefault(data["name"], owner)
    for path, data in manifests.items():
        source = _owner(subsystems, path)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for name in (data.get(section) or {}) if isinstance(data.get(section), dict) else {}:
                if name in packages:
                    offset = texts[path].find(f'"{name}"', texts[path].find(f'"{section}"'))
                    if offset >= 0:
                        add(source, packages[name], "npm", path, offset, f'"{name}"')

    # Cargo: path dependencies.
    for path, text in texts.items():
        if not path.endswith("Cargo.toml"):
            continue
        source = _owner(subsystems, path)
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        for match in re.finditer(r'path\s*=\s*"([^"]+)"', text):
            target = _normalise(f"{base}/{match.group(1)}" if base else match.group(1))
            add(
                source,
                _owner(subsystems, f"{target}/x"),
                "cargo",
                path,
                match.start(),
                match.group(),
            )

    by_id = {s.id: s for s in graph.subsystems}
    for (source, target), dependency in found.items():
        by_id[source].depends_on.append(dependency)
        if source not in by_id[target].used_by:
            by_id[target].used_by.append(source)
    return graph


def _normalise(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)
