"""The major parts of a repository, found from its structure.

A beginner's second question -- after "what is this?" -- is "what are its
pieces?". Those pieces are rarely the components a detector matches first; they
are the modules the project itself declares, or failing that its top-level
source directories. Both are read here deterministically:

    declared modules    Maven <modules>, Gradle includes, sbt file(...),
                        npm/pnpm/lerna workspaces, Cargo members, go.work
    source directories  top-level directories holding source, descending
                        through a single wrapper such as ``src/``

Nemotron may later *name and describe* a subsystem; it never adds one, merges
two, or changes which files belong to which. Membership is a path prefix.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from dataclasses import dataclass
from math import log10

from repolab.architecture.analyzers.base import slug
from repolab.architecture.models import (
    ArchitectureGraph,
    ArchitectureNode,
    Evidence,
    EvidenceKind,
    StartingPoint,
    Subsystem,
)
from repolab.architecture.source import SourceIndex, is_peripheral, is_test_path, line_of
from repolab.ingest.languages import EXTENSION_LANGUAGES
from repolab.manifest.models import RepoManifest

#: A repository map should fit on a screen. Beyond this, the least important are
#: folded into one "Other modules" entry -- listed, never hidden.
MAX_SUBSYSTEMS = 12

#: Extensions counted as source when sizing a subsystem.
_SOURCE = frozenset(EXTENSION_LANGUAGES) - {".sh", ".bash", ".zsh", ".ipynb", ".h"}

#: A child holding this share of a directory's source *is* that directory, for
#: the purpose of finding where its code really starts (``core/src/main/scala``).
_DOMINANT_SHARE = 0.8

#: Names a module conventionally gives to its central code.
_CENTRAL_NAME = re.compile(r"^(core|engine|server|runtime|kernel)$", re.I)

_NOTABLE_FILE = re.compile(
    r"(main|app|server|service|engine|scheduler|context|session|api|client|cli|core|"
    r"router|handler|controller|daemon|worker|pipeline|job|dag|executor|driver|manager)",
    re.I,
)

MAX_STARTING_POINTS = 3
MAX_OUTLINE = 6


@dataclass(frozen=True)
class Declared:
    """One module a build file declares."""

    path: str
    declared_by: str
    evidence: Evidence


def extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_source(path: str) -> bool:
    return extension(path) in _SOURCE and not is_test_path(path)


def _peripheral_dir(directory: str) -> bool:
    return is_peripheral(f"{directory}/x") or is_test_path(f"{directory}/x")


def _dirs(paths: list[str]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            found.add("/".join(parts[:depth]))
    return found


# -- declared modules -------------------------------------------------------------


def _evidence(index: SourceIndex, path: str, text: str, position: int, what: str) -> Evidence:
    line = line_of(text, position)
    return Evidence(
        kind=EvidenceKind.BUILD,
        label=f"{path}:{line}",
        path=path,
        line=line,
        excerpt=text.splitlines()[line - 1].strip()[:160] if text else None,
    )


def _expand(pattern: str, directories: set[str]) -> list[str]:
    pattern = pattern.strip().strip("'\"").removeprefix("./").rstrip("/")
    if not pattern or pattern.startswith("!"):
        return []
    if any(ch in pattern for ch in "*?["):
        pattern = pattern.removesuffix("/**")
        return sorted(d for d in directories if fnmatch.fnmatch(d, pattern))
    return [pattern] if pattern in directories else []


def declared_modules(index: SourceIndex, directories: set[str]) -> list[Declared]:
    """Modules the repository's root build files declare, each with its line."""
    found: list[Declared] = []

    def add(path: str, file: str, text: str, position: int, what: str) -> None:
        for module in _expand(path, directories):
            found.append(Declared(module, what, _evidence(index, file, text, position, what)))

    text = index.text("pom.xml")
    if text:
        for match in re.finditer(r"<module>\s*([^<\s]+)\s*</module>", text):
            add(match.group(1), "pom.xml", text, match.start(), "Maven module")

    for file in ("settings.gradle", "settings.gradle.kts"):
        text = index.text(file)
        if not text:
            continue
        for statement in re.finditer(r"^\s*include\b(.*)$", text, re.M):
            for name in re.finditer(r"['\"]:?([\w\-.:/]+)['\"]", statement.group(1)):
                add(
                    name.group(1).replace(":", "/"), file, text, statement.start(), "Gradle project"
                )

    text = index.text("build.sbt")
    if text:
        for match in re.finditer(r"\bfile\(\s*\"([^\"]+)\"\s*\)", text):
            add(match.group(1), "build.sbt", text, match.start(), "sbt project")

    text = index.text("package.json")
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        workspaces = data.get("workspaces") if isinstance(data, dict) else None
        if isinstance(workspaces, dict):
            workspaces = workspaces.get("packages")
        if isinstance(workspaces, list):
            position = max(text.find('"workspaces"'), 0)
            for pattern in workspaces:
                if isinstance(pattern, str):
                    add(pattern, "package.json", text, position, "npm workspace")

    for file, what in (("pnpm-workspace.yaml", "pnpm workspace"), ("lerna.json", "lerna package")):
        text = index.text(file)
        if not text:
            continue
        for match in re.finditer(r"^\s*-\s*['\"]?([^'\"\n#]+)['\"]?\s*$", text, re.M):
            add(match.group(1), file, text, match.start(), what)
        if file == "lerna.json":
            for match in re.finditer(r"\"([^\"]+/\*)\"", text):
                add(match.group(1), file, text, match.start(), what)

    text = index.text("Cargo.toml")
    if text:
        block = re.search(r"members\s*=\s*\[(.*?)\]", text, re.S)
        if block:
            for match in re.finditer(r"\"([^\"]+)\"", block.group(1)):
                add(match.group(1), "Cargo.toml", text, block.start(), "Cargo workspace member")

    text = index.text("go.work")
    if text:
        for match in re.finditer(r"^\s*(?:use\s+)?(\./[\w\-./]+)\s*$", text, re.M):
            add(match.group(1), "go.work", text, match.start(), "Go workspace module")

    unique: dict[str, Declared] = {}
    for item in found:
        unique.setdefault(item.path, item)
    return list(unique.values())


# -- discovery ------------------------------------------------------------------


def _group_depth(modules: list[str]) -> int:
    """The shallowest grouping depth that yields more than one group."""
    for depth in (1, 2, 3):
        if len({"/".join(m.split("/")[:depth]) for m in modules}) > 1:
            return depth
    return 1


def _source_dirs(source: list[str], prefix: str) -> Counter[str]:
    """Immediate child directories of *prefix* holding source, with file counts."""
    counts: Counter[str] = Counter()
    base = f"{prefix}/" if prefix else ""
    for path in source:
        if not path.startswith(base):
            continue
        rest = path[len(base) :]
        if "/" in rest:
            child = base + rest.split("/", 1)[0]
            if not _peripheral_dir(child):
                counts[child] += 1
    return counts


def discover_subsystems(index: SourceIndex) -> list[Subsystem]:
    """The repository's major parts, before any component is assigned to them."""
    paths = sorted(index.all_paths)
    source = [p for p in paths if is_source(p) and not is_peripheral(p)]
    directories = _dirs(paths)
    declared = [
        d
        for d in declared_modules(index, directories)
        if not _peripheral_dir(d.path) and any(p.startswith(d.path + "/") for p in source)
    ]

    subsystems: list[Subsystem] = []
    if declared:
        depth = _group_depth([d.path for d in declared])
        groups: dict[str, list[Declared]] = {}
        for item in declared:
            groups.setdefault("/".join(item.path.split("/")[:depth]), []).append(item)
        for key, members in groups.items():
            what = members[0].declared_by
            subsystems.append(
                Subsystem(
                    id=f"sub:{slug(key)}",
                    label=key.rsplit("/", 1)[-1],
                    paths=[key],
                    declared_by=what if len(members) == 1 else f"{len(members)} {what}s",
                    evidence=[m.evidence for m in members[:4]],
                )
            )
    else:
        # Descend through a single wrapper (``src/``, ``packages/``, the one
        # package of a library) to where the parts actually are.
        prefix = ""
        children = _source_dirs(source, prefix)
        while (
            len(children) == 1
            and prefix.count("/") < 3
            and len(_loose_files(source, prefix)) <= 2
            and _source_dirs(source, next(iter(children)))
        ):
            prefix = next(iter(children))
            children = _source_dirs(source, prefix)
        for child, count in children.most_common():
            subsystems.append(
                Subsystem(
                    id=f"sub:{slug(child)}",
                    label=child.rsplit("/", 1)[-1],
                    paths=[child],
                    declared_by="source directory",
                    evidence=[
                        Evidence(
                            kind=EvidenceKind.SOURCE,
                            label=f"{count} source files under {child}/",
                            path=child,
                        )
                    ],
                )
            )
        loose = _loose_files(source, prefix)
        if loose and (prefix or not subsystems):
            subsystems.append(_loose(prefix, loose))

    for subsystem in subsystems:
        describe_structure(subsystem, source)
    return subsystems


def _loose_files(source: list[str], prefix: str) -> list[str]:
    """Source files directly inside *prefix*, not in a subdirectory."""
    base = f"{prefix}/" if prefix else ""
    return [p for p in source if p.startswith(base) and "/" not in p[len(base) :]]


def _loose(prefix: str, files: list[str]) -> Subsystem:
    label = f"{prefix.rsplit('/', 1)[-1]} (top-level files)" if prefix else "Top level"
    return Subsystem(
        id=f"sub:{slug(prefix or 'top-level')}-files",
        label=label,
        paths=files,
        declared_by="source files outside any directory" if not prefix else "package files",
        evidence=[Evidence(kind=EvidenceKind.SOURCE, label=f, path=f) for f in files[:3]],
    )


def content_root(directory: str, source: list[str]) -> str:
    """Where a directory's code really starts: ``core`` -> ``core/src/main/scala/...``."""
    current = directory
    for _ in range(8):
        base = f"{current}/" if current else ""
        inside = [p for p in source if p.startswith(base)]
        if not inside or any("/" not in p[len(base) :] for p in inside):
            return current
        children = _source_dirs(inside, current)
        if not children:
            return current
        child, count = children.most_common(1)[0]
        if count < _DOMINANT_SHARE * len(inside):
            return current
        current = child
    return current


def describe_structure(subsystem: Subsystem, source: list[str]) -> None:
    """File count, languages and outline -- all from the tree, nothing read."""
    inside = [p for p in source if subsystem.contains(p)]
    subsystem.file_count = len(inside)
    languages = Counter(EXTENSION_LANGUAGES[extension(p)] for p in inside)
    subsystem.languages = [name for name, _ in languages.most_common(3)]
    if len(subsystem.paths) == 1 and subsystem.paths[0] and subsystem.paths[0] not in inside:
        root = content_root(subsystem.paths[0], inside)
        subsystem.outline = [
            child.rsplit("/", 1)[-1]
            for child, _ in _source_dirs(inside, root).most_common(MAX_OUTLINE)
        ]


# -- components, importance and starting points -----------------------------------


def attach_components(graph: ArchitectureGraph, index: SourceIndex) -> None:
    """Put every component in the subsystem whose paths contain its code.

    A component outside every subsystem (a launcher in ``bin/``, a root file)
    gets one for its top-level directory, so nothing found is unreachable.
    """
    source = [p for p in index.all_paths if is_source(p)]
    for node in graph.nodes:
        path = _home(node)
        if path is None:
            continue
        subsystem = graph.subsystem_for(path)
        if subsystem is None:
            top = path.split("/", 1)[0] if "/" in path else ""
            subsystem = (
                Subsystem(
                    id=f"sub:{slug(top)}",
                    label=top,
                    paths=[top],
                    declared_by="contains detected components",
                )
                if top
                else _loose("", [path])
            )
            existing = graph.subsystem(subsystem.id)
            if existing is not None:
                existing.paths.extend(p for p in subsystem.paths if p not in existing.paths)
                subsystem = existing
            else:
                describe_structure(subsystem, sorted(source))
                graph.subsystems.append(subsystem)
        node.subsystem = subsystem.id
    for subsystem in graph.subsystems:
        subsystem.component_ids = [n.id for n in graph.nodes if n.subsystem == subsystem.id]


def _home(node: ArchitectureNode) -> str | None:
    """The path that best says where a component lives: its first non-config reference."""
    refs = node.source_references
    if not refs:
        return None
    return next((r.path for r in refs if is_source(r.path)), refs[0].path)


def _mentions(text: str, name: str) -> int:
    if len(name) < 3:
        return 0
    return len(re.findall(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.I))


def score(
    graph: ArchitectureGraph,
    readme: str,
    doc_paths: list[str],
    entry_paths: list[str],
) -> None:
    """Rank subsystems and components by how much they help someone understand the code.

    Every signal is deterministic and recorded on the subsystem, so the order can
    be checked. Size is log-scaled: a big directory is not automatically the
    place to start.
    """
    by_id = {s.id: s for s in graph.subsystems}
    neighbours: dict[str, set[str]] = {s.id: set() for s in graph.subsystems}
    nodes = {n.id: n for n in graph.nodes}
    for edge in graph.edges:
        a, b = nodes[edge.source].subsystem, nodes[edge.target].subsystem
        if a and b and a != b:
            neighbours[a].add(b)
            neighbours[b].add(a)

    for subsystem in graph.subsystems:
        name = subsystem.label.split(" ", 1)[0]
        members = [nodes[i] for i in subsystem.component_ids if i in nodes]
        signals = {
            "entry point": 2.0
            if any(subsystem.contains(p) for p in entry_paths)
            or any(n.category.value == "entry_point" for n in members)
            else 0.0,
            "README mentions": 0.6 * min(_mentions(readme, name), 5),
            "documentation pages": 0.3
            * min(sum(1 for d in doc_paths if name.lower() in d.lower()), 5),
            "connected subsystems": 0.8 * min(len(neighbours.get(subsystem.id, ())), 5),
            # Deliberately small: many detector matches is not the same as central.
            "components": 0.1 * min(len(members), 5),
            "interaction surface": 1.0 if any(n.surface for n in members) else 0.0,
            "central name": 1.0 if _CENTRAL_NAME.match(name) else 0.0,
            "size": round(0.8 * log10(subsystem.file_count + 1), 2),
            "no source files": -2.0 if not subsystem.file_count else 0.0,
        }
        subsystem.importance_signals = {k: round(v, 2) for k, v in signals.items() if v}
        subsystem.learning_importance = round(sum(signals.values()), 2)

    flow_members = {i for flow in graph.primary_flows for i in flow.node_ids}
    for node in graph.nodes:
        incoming, outgoing = graph.neighbours(node.id)
        home = by_id.get(node.subsystem or "")
        value = (
            (2.0 if node.id in flow_members else 0.0)
            + 0.4 * min(len(incoming) + len(outgoing), 8)
            + (1.0 if node.surface else 0.0)
            + (1.0 if node.id in graph.entry_points else 0.0)
            + (1.0 if _mentions(readme, node.label) else 0.0)
            + (0.1 * home.learning_importance if home else 0.0)
            - (0.5 if node.category.value == "configuration" else 0.0)
        )
        node.learning_importance = round(value, 2)

    graph.subsystems.sort(key=lambda s: (-s.learning_importance, s.label))


def fold(graph: ArchitectureGraph, limit: int = MAX_SUBSYSTEMS) -> None:
    """Keep the *limit* most important subsystems; fold the rest into one, listed."""
    if len(graph.subsystems) <= limit:
        return
    kept, rest = graph.subsystems[: limit - 1], graph.subsystems[limit - 1 :]
    other = Subsystem(
        id="sub:other-modules",
        label="Other modules",
        description="Smaller parts of the repository: " + ", ".join(s.label for s in rest) + ".",
        paths=[p for s in rest for p in s.paths],
        declared_by=f"{len(rest)} smaller subsystems",
        file_count=sum(s.file_count for s in rest),
        languages=list(dict.fromkeys(lang for s in rest for lang in s.languages))[:3],
        learning_importance=max((s.learning_importance for s in rest), default=0.0),
        component_ids=[i for s in rest for i in s.component_ids],
        outline=[s.label for s in rest][:MAX_OUTLINE],
        evidence=[e for s in rest for e in s.evidence][:4],
    )
    for node in graph.nodes:
        if node.subsystem in {s.id for s in rest}:
            node.subsystem = other.id
    graph.subsystems = [*kept, other]


def starting_points(graph: ArchitectureGraph, index: SourceIndex) -> None:
    """Where to open each subsystem first, each with the reason."""
    source = sorted(p for p in index.all_paths if is_source(p))
    nodes = {n.id: n for n in graph.nodes}
    for subsystem in graph.subsystems:
        points: list[StartingPoint] = []
        for prefix in subsystem.paths[:1]:
            readme = next(
                (
                    f"{prefix}/{name}" if prefix else name
                    for name in ("README.md", "README.rst", "README")
                    if (f"{prefix}/{name}" if prefix else name) in index.all_paths
                ),
                None,
            )
            if readme and prefix:
                points.append(
                    StartingPoint(
                        path=readme, is_directory=False, reason="This module's own introduction."
                    )
                )
            inside = [p for p in source if subsystem.contains(p)]
            if prefix and prefix not in index.all_paths:
                root = content_root(prefix, inside)
                points.append(
                    StartingPoint(
                        path=root,
                        reason="Where this part's code starts.",
                        learn_about=subsystem.outline,
                    )
                )
        members = sorted(
            (nodes[i] for i in subsystem.component_ids if i in nodes),
            key=lambda n: -n.learning_importance,
        )
        for member in members:
            if len(points) >= MAX_STARTING_POINTS:
                break
            path = _home(member)
            if path and all(p.path != path for p in points):
                points.append(
                    StartingPoint(
                        path=path,
                        is_directory=False,
                        reason=f"Implements {member.label}.",
                    )
                )
        if len(points) < MAX_STARTING_POINTS:
            notable = sorted(
                (
                    p
                    for p in source
                    if subsystem.contains(p) and _NOTABLE_FILE.search(p.rsplit("/", 1)[-1])
                ),
                key=lambda p: (p.count("/"), len(p)),
            )
            for path in notable:
                if len(points) >= MAX_STARTING_POINTS:
                    break
                if all(p.path != path for p in points):
                    points.append(
                        StartingPoint(
                            path=path,
                            is_directory=False,
                            reason="Its name suggests a central piece of this part.",
                        )
                    )
        subsystem.starting_points = points


def rank(graph: ArchitectureGraph, index: SourceIndex, manifest: RepoManifest | None) -> None:
    """Score, fold and give starting points to the subsystems. Safe to repeat."""
    readme_path = next(
        (p for p in sorted(index.all_paths) if "/" not in p and p.lower().startswith("readme")),
        None,
    )
    readme = (index.text(readme_path) or "") if readme_path else ""
    doc_paths = [
        p for p in index.all_paths if p.split("/", 1)[0].lower() in {"doc", "docs", "documentation"}
    ]
    entry_paths = [e.path for e in manifest.entrypoints if e.path] if manifest else []
    for subsystem in graph.subsystems:
        subsystem.component_ids = [n.id for n in graph.nodes if n.subsystem == subsystem.id]
    score(graph, readme, doc_paths, entry_paths)
    fold(graph)
    for subsystem in graph.subsystems:
        subsystem.component_ids = [n.id for n in graph.nodes if n.subsystem == subsystem.id]
    starting_points(graph, index)
