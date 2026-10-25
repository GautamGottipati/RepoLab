"""What a repository *is*, how it works, and where to start -- levels 0 and 1.

The component graph answers "what did RepoLab find?". A newcomer first needs
"what is this?", "how does it roughly work?" and "where do I start?". Those are
built here, in two layers:

**Deterministic**, always:

    fact sheet         README title and opening, package descriptions, docs
                       pages, languages, frameworks, surfaces -- each cited
    repository type    scored from structural evidence and the project's own
                       self-description; decided here when one kind clearly wins
    system model       the most important subsystems (or components, in a small
                       repository) as concepts, linked where component edges cross
    walkthrough        the component graph's primary flow
    learning path      built from the above

**Nemotron Super**, once, when a router is available: plain-language
explanation, a type choice *among the tied candidates only*, names for
subsystems, 3-8 concepts, cited links, a learning path and a walkthrough.
:func:`apply_orientation` accepts only what cites a real file, directory,
subsystem, component or retrieved documentation page; everything accepted is
marked inferred and every refusal is counted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import structlog
from pydantic import BaseModel, Field

from repolab.architecture.analyzers.base import slug
from repolab.architecture.models import (
    ArchitectureGraph,
    ArchitectureLevel,
    ArchitectureNode,
    ArchitectureViewKind,
    ConceptLink,
    EdgeType,
    Evidence,
    EvidenceKind,
    LearningStep,
    LearningTargetKind,
    NodeCategory,
    Provenance,
    RepositoryKind,
    RepositoryUnderstanding,
    SystemConcept,
    SystemModel,
    Walkthrough,
    WalkthroughKind,
    WalkthroughStep,
)
from repolab.architecture.source import SourceIndex
from repolab.architecture.synthesis import _MEASUREMENT, SynthesisRouter
from repolab.evidence.models import DocumentationReference
from repolab.llm import LLMError, Purpose
from repolab.manifest.architecture import GraphSource
from repolab.manifest.models import RepoManifest

log = structlog.get_logger(__name__)

ORIENTATION_MAX_TOKENS = 8192
ORIENTATION_TIMEOUT_SECONDS = 180.0

MIN_CONCEPTS = 3
MAX_CONCEPTS = 8
MAX_LEARNING_STEPS = 6
MAX_PROMPT_COMPONENTS = 30
MAX_PROMPT_DOCS = 6

_BUILD_FILES = {
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "build.sbt",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "cargo.toml",
    "go.mod",
    "go.work",
}
_CONFIG_EXTENSIONS = (".yaml", ".yml", ".json", ".toml", ".ini", ".conf", ".properties", ".cfg")

#: What runs, as opposed to what is used. A library has none of these.
_RUNTIME = {
    NodeCategory.API,
    NodeCategory.FRONTEND,
    NodeCategory.CLI,
    NodeCategory.PIPELINE,
    NodeCategory.SCHEDULER,
    NodeCategory.SERVICE,
    NodeCategory.WORKER,
}

_ML_DEPENDENCIES = {
    "torch",
    "pytorch",
    "tensorflow",
    "keras",
    "scikit-learn",
    "sklearn",
    "xgboost",
    "lightgbm",
    "transformers",
    "jax",
}

#: How a project describes itself, kind by kind. Matched against its own README
#: opening and package descriptions, never against a repository name.
_SELF_DESCRIPTION: tuple[tuple[RepositoryKind, float, re.Pattern[str]], ...] = (
    (
        RepositoryKind.DISTRIBUTED_SYSTEM,
        2,
        re.compile(r"\bdistributed\b|\bclusters?\b|across (many|multiple) (machines|nodes)", re.I),
    ),
    (
        RepositoryKind.DATA_PLATFORM,
        2,
        re.compile(
            r"data processing|analytics engine|data pipelines?|\betl\b|stream processing|"
            r"big data|large-scale data",
            re.I,
        ),
    ),
    (
        RepositoryKind.WORKFLOW_SYSTEM,
        2,
        re.compile(r"\bworkflows?\b|orchestrat|\bdags?\b", re.I),
    ),
    (
        RepositoryKind.DATABASE,
        2,
        re.compile(r"\bdatabase\b|data ?store|key[- ]value|\bnosql\b|storage engine", re.I),
    ),
    (RepositoryKind.FRAMEWORK, 1, re.compile(r"\bframework\b", re.I)),
    (RepositoryKind.LIBRARY, 1, re.compile(r"\blibrary\b|\bsdk\b", re.I)),
    (RepositoryKind.CLI_APPLICATION, 1, re.compile(r"command[- ]line|\bcli\b", re.I)),
    (
        RepositoryKind.DEVELOPER_TOOL,
        2,
        re.compile(r"\blinter\b|\bformatter\b|developer tool|\bdevtools?\b|build tool", re.I),
    ),
    (
        RepositoryKind.ML_SYSTEM,
        2,
        re.compile(r"machine learning|deep learning|neural network|model training", re.I),
    ),
    (
        RepositoryKind.WEB_APPLICATION,
        2,
        re.compile(r"web app(lication)?|full[- ]stack|\bwebsite\b|\bdashboard\b", re.I),
    ),
    (
        RepositoryKind.API_SERVICE,
        2,
        re.compile(r"\b(rest|http|graphql) api\b|\bweb service\b|microservice", re.I),
    ),
)


# ---------------------------------------------------------------------------
# The fact sheet
# ---------------------------------------------------------------------------


@dataclass
class Fact:
    text: str
    evidence: Evidence


@dataclass
class FactSheet:
    """What the repository says about itself, each statement with its line."""

    name: str
    name_evidence: Evidence | None = None
    descriptions: list[Fact] = field(default_factory=list)
    paragraphs: list[Fact] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    readme: str = ""
    doc_paths: list[str] = field(default_factory=list)
    docs: list[DocumentationReference] = field(default_factory=list)

    @property
    def self_description(self) -> list[Fact]:
        return [*self.descriptions, *self.paragraphs[:2]]


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    text = re.sub(r"image:\S+\[[^\]]*\]", "", text)  # AsciiDoc images
    text = re.sub(r"(https?://\S+)\[([^\]]+)\]", r"\2", text)  # AsciiDoc links
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML.sub("", text)
    text = re.sub(r"[*_`]{1,3}", "", text)
    return re.sub(r"\s+", " ", text).strip()


def evidence_kind(path: str) -> EvidenceKind:
    name = path.rsplit("/", 1)[-1].lower()
    if name.startswith("readme"):
        return EvidenceKind.README
    if name in _BUILD_FILES:
        return EvidenceKind.BUILD
    if name.endswith(_CONFIG_EXTENSIONS):
        return EvidenceKind.CONFIGURATION
    if path.split("/", 1)[0].lower() in {"doc", "docs", "documentation"}:
        return EvidenceKind.DOCUMENTATION
    return EvidenceKind.SOURCE


def line_evidence(path: str, line: int, text: str) -> Evidence:
    lines = text.splitlines()
    excerpt = lines[line - 1].strip()[:200] if 0 < line <= len(lines) else None
    return Evidence(
        kind=evidence_kind(path), label=f"{path}:{line}", path=path, line=line, excerpt=excerpt
    )


def readme_path(index: SourceIndex) -> str | None:
    candidates = sorted(
        (p for p in index.all_paths if "/" not in p and p.lower().startswith("readme")),
        key=lambda p: (not p.lower().endswith(".md"), len(p)),
    )
    return candidates[0] if candidates else None


def gather_facts(
    index: SourceIndex,
    manifest: RepoManifest | None,
    docs: list[DocumentationReference] | None = None,
) -> FactSheet:
    repo_name = manifest.repo_url.rstrip("/").rsplit("/", 1)[-1] if manifest else "repository"
    facts = FactSheet(name=repo_name, docs=list(docs or [])[:MAX_PROMPT_DOCS])
    facts.doc_paths = sorted(
        p
        for p in index.all_paths
        if p.split("/", 1)[0].lower() in {"doc", "docs", "documentation"}
        and p.lower().endswith((".md", ".rst", ".txt", ".adoc"))
    )[:200]

    path = readme_path(index)
    text = index.text(path) if path else None
    if path and text:
        facts.readme = text
        _read_readme(facts, path, text)

    for file, pattern in (
        ("pyproject.toml", r"^description\s*=\s*[\"'](.+?)[\"']"),
        ("setup.py", r"description\s*=\s*[\"'](.+?)[\"']"),
        ("setup.cfg", r"^description\s*=\s*(.+)$"),
        ("Cargo.toml", r"^description\s*=\s*\"(.+?)\""),
        ("pom.xml", r"<description>\s*(.+?)\s*</description>"),
    ):
        content = index.text(file) if file in index.all_paths else None
        match = re.search(pattern, content, re.M | re.S) if content else None
        if content and match and len(_clean(match.group(1))) >= 10:
            line = content.count("\n", 0, match.start()) + 1
            facts.descriptions.append(
                Fact(_clean(match.group(1))[:300], line_evidence(file, line, content))
            )

    content = index.text("package.json") if "package.json" in index.all_paths else None
    if content:
        try:
            data = json.loads(content)
        except ValueError:
            data = {}
        description = data.get("description") if isinstance(data, dict) else None
        if isinstance(description, str) and len(description) >= 10:
            line = content.count("\n", 0, max(content.find('"description"'), 0)) + 1
            facts.descriptions.append(
                Fact(description[:300], line_evidence("package.json", line, content))
            )
    return facts


#: Sections whose prose describes the project rather than how to use it.
_ABOUT = re.compile(r"about|overview|introduction|what is|description|summary", re.I)
#: AsciiDoc inline images and attribute lines, which are not prose.
_ASCIIDOC_NOISE = re.compile(r"^(?:image:|:\w[\w-]*:|\[\w|ifdef::|endif::|toc::)")


def _heading(lines: list[str], index: int) -> tuple[str, int] | None:
    """The heading at *index* and its level (1 = title), in Markdown, AsciiDoc or setext."""
    line = lines[index]
    match = re.match(r"^(#{1,3}|={1,3})\s+(.+)$", line)
    if match:
        return _clean(match.group(2)), len(match.group(1))
    following = lines[index + 1] if index + 1 < len(lines) else ""
    if (
        line.strip()
        and not line.lstrip().startswith(("-", "*", "+"))
        and re.match(r"^(={3,}|-{3,})\s*$", following)
    ):
        return _clean(line), 1 if following.startswith("=") else 2
    return None


def _read_readme(facts: FactSheet, path: str, text: str) -> None:
    """The title, section headings and the project's own opening description.

    Prose is taken only from under the title, or from a section about the
    project: instructions further down ("Click Use this template") describe how
    to use the repository, not what it is.
    """
    lines = text.splitlines()
    headings = [(i, h) for i in range(len(lines)) if (h := _heading(lines, i))]
    title_line = next((i for i, (t, _) in headings if t and len(t) <= 80), None)
    if title_line is not None:
        facts.name = dict(headings)[title_line][0]
        facts.name_evidence = line_evidence(path, title_line + 1, text)
    facts.headings = [t for i, (t, _) in headings if i != title_line][:15]
    heading_at = dict(headings)

    block: list[str] = []
    start = 0
    in_code = False
    describing = True  # before the first section, or inside an "about" one
    skip_underline = False
    for index, line in enumerate([*lines, ""]):
        stripped = line.strip()
        if skip_underline:
            skip_underline = False
            continue
        if stripped.startswith(("```", "----", "....")) and index not in heading_at:
            in_code = not in_code
            block = []
            continue
        if in_code:
            continue
        if index in heading_at:
            if index != title_line:
                describing = bool(_ABOUT.search(heading_at[index][0]))
            skip_underline = not re.match(r"^(#|=)", line)
            block = []
            continue
        prose_line = (
            stripped
            and not line.startswith(("  ", "\t"))
            and not stripped.startswith(("#", "|", "<", "[!", "!", ">"))
            and not _ASCIIDOC_NOISE.match(stripped)
        )
        if prose_line:
            if not block:
                start = index + 1
            block.append(line)
            continue
        if block and describing:
            prose = _clean(" ".join(block))
            if len(prose) >= 40 and not prose.startswith(("-", "*", "+")):
                facts.paragraphs.append(Fact(prose[:700], line_evidence(path, start, text)))
        block = []
        if len(facts.paragraphs) >= 3:
            break


# ---------------------------------------------------------------------------
# Repository type
# ---------------------------------------------------------------------------


@dataclass
class TypeDecision:
    kind: RepositoryKind
    provenance: Provenance
    candidates: list[RepositoryKind]
    reasons: list[str]
    evidence: list[Evidence]


def classify_repository(
    graph: ArchitectureGraph, facts: FactSheet, manifest: RepoManifest | None
) -> TypeDecision:
    """Score every kind from evidence. Decided here only when one clearly wins."""
    scores: dict[RepositoryKind, float] = {}
    reasons: dict[RepositoryKind, list[str]] = {}
    evidence: list[Evidence] = []

    def vote(kind: RepositoryKind, weight: float, reason: str) -> None:
        scores[kind] = scores.get(kind, 0) + weight
        reasons.setdefault(kind, []).append(reason)

    categories = {n.category for n in graph.nodes}
    subtypes = {n.subtype for n in graph.nodes if n.subtype}
    api = NodeCategory.API in categories or "http_server" in subtypes
    if NodeCategory.FRONTEND in categories:
        vote(
            RepositoryKind.WEB_APPLICATION,
            3 if api else 2,
            "a browser frontend" + (" that calls HTTP APIs" if api else "") + " was found",
        )
    elif api:
        vote(RepositoryKind.API_SERVICE, 3, "HTTP APIs were found, with no frontend")
    if categories & {NodeCategory.SCHEDULER, NodeCategory.TASK}:
        vote(RepositoryKind.WORKFLOW_SYSTEM, 3, "workflow definitions and tasks were found")
    if NodeCategory.PIPELINE in categories:
        vote(RepositoryKind.DATA_PLATFORM, 2, "data pipelines were found")
    commands = [
        n
        for n in graph.nodes
        if n.category in {NodeCategory.CLI, NodeCategory.COMMAND}
        and n.subtype != "interactive_shell"
    ]
    if commands:
        vote(RepositoryKind.CLI_APPLICATION, 2, "a command-line parser was found")
    if "daemon" in subtypes and "interactive_shell" in subtypes:
        vote(
            RepositoryKind.DATABASE, 2, "a long-running server and an interactive shell were found"
        )
    if NodeCategory.LIBRARY in categories and not categories & _RUNTIME:
        vote(RepositoryKind.LIBRARY, 3, "a public API was found and nothing that runs on its own")
    deps = {d.name.lower() for d in manifest.dependencies} if manifest else set()
    if categories & {NodeCategory.MODEL, NodeCategory.INFERENCE}:
        vote(RepositoryKind.ML_SYSTEM, 2, "model training or inference code was found")
    elif deps & _ML_DEPENDENCIES:
        vote(RepositoryKind.ML_SYSTEM, 1, "it depends on a machine-learning library")

    modules = [
        s
        for s in graph.subsystems
        if "module" in s.declared_by
        or "project" in s.declared_by
        or "workspace" in s.declared_by
        or "member" in s.declared_by
    ]
    declared = sum(
        int(s.declared_by.split(" ", 1)[0]) if s.declared_by[:1].isdigit() else 1 for s in modules
    )
    if declared >= 6:
        vote(RepositoryKind.FRAMEWORK, 1, f"its build declares {declared} modules")
        vote(RepositoryKind.DISTRIBUTED_SYSTEM, 1, f"its build declares {declared} modules")

    # What the code shows, before what the project says about itself: a hybrid
    # is several kinds of application *built here*, not several words in a README.
    structural = dict(scores)

    for kind, weight, pattern in _SELF_DESCRIPTION:
        for fact in facts.self_description:
            match = pattern.search(fact.text)
            if match:
                vote(
                    kind,
                    weight,
                    f"it describes itself with “{match.group(0)}” ({fact.evidence.label})",
                )
                if fact.evidence not in evidence:
                    evidence.append(fact.evidence)
                break

    if not scores:
        return TypeDecision(RepositoryKind.UNKNOWN, Provenance.STATIC, [], [], [])

    ranked = sorted(
        scores.items(), key=lambda item: (-item[1], list(RepositoryKind).index(item[0]))
    )
    top_kind, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= 3 and top - second >= 2:
        return TypeDecision(top_kind, Provenance.STATIC, [top_kind], reasons[top_kind], evidence)

    candidates = [kind for kind, value in ranked if value >= top - 1][:4]
    if sum(1 for value in structural.values() if value >= 3) >= 2:
        candidates.append(RepositoryKind.HYBRID)
    all_reasons = [r for kind in candidates for r in reasons.get(kind, [])]
    return TypeDecision(top_kind, Provenance.INFERRED, candidates, all_reasons, evidence)


# ---------------------------------------------------------------------------
# Deterministic levels
# ---------------------------------------------------------------------------


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return (match.group(1) if match else text)[:240]


def baseline_understanding(
    facts: FactSheet,
    decision: TypeDecision,
    graph: ArchitectureGraph,
    manifest: RepoManifest | None,
) -> RepositoryUnderstanding:
    """What can be said without a model: the project's own words, quoted."""
    description = facts.descriptions[0] if facts.descriptions else None
    paragraph = facts.paragraphs[0] if facts.paragraphs else None
    one_line = (
        description.text
        if description
        else (_first_sentence(paragraph.text) if paragraph else None)
    )
    evidence = [
        e
        for e in (
            facts.name_evidence,
            description.evidence if description else None,
            paragraph.evidence if paragraph else None,
            *decision.evidence,
        )
        if e is not None
    ]
    return RepositoryUnderstanding(
        name=facts.name,
        one_line_description=one_line,
        repository_type=decision.kind,
        type_provenance=decision.provenance,
        type_candidates=decision.candidates,
        type_reasons=decision.reasons,
        beginner_explanation=paragraph.text if paragraph else None,
        languages=list(manifest.languages) if manifest else [],
        frameworks=list(manifest.frameworks) if manifest else [],
        main_interaction_surfaces=list(graph.interaction_surfaces),
        confidence=0.5 if paragraph or description else 0.25,
        evidence=_unique(evidence),
    )


def _unique(items: list[Evidence]) -> list[Evidence]:
    seen: list[Evidence] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _concept_for_subsystem(graph: ArchitectureGraph, subsystem_id: str) -> SystemConcept:
    subsystem = graph.subsystem(subsystem_id)
    assert subsystem is not None
    where = ", ".join(f"{p}/" for p in subsystem.paths[:2] if p) or "the repository root"
    languages = f" ({', '.join(subsystem.languages)})" if subsystem.languages else ""
    # The part's own areas, from the tree -- not the components a detector
    # matched inside it, which belong to the drill-down.
    contains = f" Main areas: {', '.join(subsystem.outline[:4])}." if subsystem.outline else ""
    return SystemConcept(
        id=f"concept:{subsystem.id.removeprefix('sub:')}",
        label=subsystem.label,
        what_it_does=subsystem.description
        or f"The code under {where}: {subsystem.file_count} source files{languages}.{contains}",
        subsystem_ids=[subsystem.id],
        member_ids=subsystem.component_ids[:8],
        evidence=[
            Evidence(kind=EvidenceKind.SUBSYSTEM, label=subsystem.label, target_id=subsystem.id),
            *subsystem.evidence[:2],
        ],
        provenance=subsystem.provenance,
        learning_importance=subsystem.learning_importance,
    )


def _concept_for_node(node: ArchitectureNode) -> SystemConcept:
    return SystemConcept(
        id=f"concept:{slug(node.id)}",
        label=node.label,
        what_it_does=node.description or node.detected_because,
        why_it_matters=node.runtime_significance or "",
        subsystem_ids=[node.subsystem] if node.subsystem else [],
        member_ids=[node.id],
        evidence=[Evidence(kind=EvidenceKind.COMPONENT, label=node.label, target_id=node.id)],
        provenance=Provenance.INFERRED
        if "description" in node.inferred_fields
        else Provenance.STATIC,
        learning_importance=node.learning_importance,
    )


def baseline_system_model(graph: ArchitectureGraph) -> SystemModel:
    """The most important subsystems as concepts -- or, in a small repository, components."""
    real = [s for s in graph.subsystems if s.id != "sub:other-modules" and s.file_count]
    if len(real) >= MIN_CONCEPTS:
        concepts = [_concept_for_subsystem(graph, s.id) for s in real[:6]]
    else:
        nodes = sorted(
            (n for n in graph.nodes if n.category is not NodeCategory.CONFIGURATION),
            key=lambda n: -n.learning_importance,
        )
        concepts = [_concept_for_node(n) for n in nodes[:MAX_CONCEPTS]]
    model = SystemModel(concepts=concepts)
    model.links = derive_links(graph, model)
    return model


def _membership(graph: ArchitectureGraph, model: SystemModel) -> dict[str, str]:
    """Component id -> the concept that contains it (explicit members first)."""
    owner: dict[str, str] = {}
    for concept in model.concepts:
        for member in concept.member_ids:
            owner.setdefault(member, concept.id)
    for node in graph.nodes:
        if node.id in owner:
            continue
        for concept in model.concepts:
            if node.subsystem and node.subsystem in concept.subsystem_ids:
                owner[node.id] = concept.id
                break
    return owner


def derive_links(graph: ArchitectureGraph, model: SystemModel) -> list[ConceptLink]:
    """A link wherever a component edge crosses from one concept into another."""
    owner = _membership(graph, model)
    labels = {n.id: n.label for n in graph.nodes}
    links: dict[tuple[str, str], ConceptLink] = {}
    for edge in graph.edges:
        a, b = owner.get(edge.source), owner.get(edge.target)
        if not a or not b or a == b:
            continue
        link = links.setdefault(
            (a, b),
            ConceptLink(source=a, target=b, type=edge.type, provenance=edge.provenance),
        )
        if len(link.evidence) < 3:
            link.evidence.append(
                Evidence(
                    kind=EvidenceKind.COMPONENT,
                    label=f"{labels[edge.source]} → {labels[edge.target]}",
                    target_id=edge.source,
                    path=edge.evidence[0].path if edge.evidence else None,
                    line=edge.evidence[0].start_line if edge.evidence else None,
                )
            )
        if edge.provenance is Provenance.STATIC:
            link.provenance = Provenance.STATIC
    return list(links.values())


def walkthrough_kind(graph: ArchitectureGraph, kind: RepositoryKind) -> WalkthroughKind:
    by_view = {
        ArchitectureViewKind.REQUEST_FLOW: WalkthroughKind.REQUEST,
        ArchitectureViewKind.SERVICE_FLOW: WalkthroughKind.REQUEST,
        ArchitectureViewKind.DATA_FLOW: WalkthroughKind.JOB,
        ArchitectureViewKind.COMMAND_FLOW: WalkthroughKind.COMMAND,
        ArchitectureViewKind.WORKFLOW: WalkthroughKind.DAG_RUN,
        ArchitectureViewKind.COMPONENT_FLOW: WalkthroughKind.CALL,
    }
    if graph.default_view in by_view:
        return by_view[graph.default_view]
    return {
        RepositoryKind.WEB_APPLICATION: WalkthroughKind.REQUEST,
        RepositoryKind.API_SERVICE: WalkthroughKind.REQUEST,
        RepositoryKind.DATA_PLATFORM: WalkthroughKind.JOB,
        RepositoryKind.DISTRIBUTED_SYSTEM: WalkthroughKind.JOB,
        RepositoryKind.CLI_APPLICATION: WalkthroughKind.COMMAND,
        RepositoryKind.DEVELOPER_TOOL: WalkthroughKind.COMMAND,
        RepositoryKind.WORKFLOW_SYSTEM: WalkthroughKind.DAG_RUN,
    }.get(kind, WalkthroughKind.CALL)


_FOLLOW = {
    WalkthroughKind.REQUEST: "Follow a request",
    WalkthroughKind.JOB: "Follow a job",
    WalkthroughKind.COMMAND: "Follow a command",
    WalkthroughKind.DAG_RUN: "Follow a workflow run",
    WalkthroughKind.CALL: "Follow a call",
    WalkthroughKind.EVENT: "Follow an event",
}


_FLOW_KIND: tuple[tuple[set[NodeCategory], WalkthroughKind], ...] = (
    ({NodeCategory.FRONTEND, NodeCategory.API}, WalkthroughKind.REQUEST),
    ({NodeCategory.SCHEDULER, NodeCategory.TASK}, WalkthroughKind.DAG_RUN),
    ({NodeCategory.PIPELINE, NodeCategory.TRANSFORM}, WalkthroughKind.JOB),
    ({NodeCategory.CLI, NodeCategory.COMMAND}, WalkthroughKind.COMMAND),
    ({NodeCategory.QUEUE}, WalkthroughKind.EVENT),
    ({NodeCategory.LIBRARY, NodeCategory.CORE_ENGINE}, WalkthroughKind.CALL),
)


def _flow_kind(graph: ArchitectureGraph, node_ids: list[str]) -> WalkthroughKind | None:
    """What a flow follows, from the components on it: a request, a job, a command..."""
    categories = {n.category for i in node_ids if (n := graph.node(i)) is not None}
    return next((kind for cats, kind in _FLOW_KIND if categories & cats), None)


def baseline_walkthroughs(graph: ArchitectureGraph, kind: WalkthroughKind) -> list[Walkthrough]:
    """The component graph's primary flows, as walkthroughs. Nothing new is claimed."""
    walks: list[Walkthrough] = []
    # In a repository with several major parts, a component path belongs to the
    # part it runs in; the overview then shows the parts, not one part's detail.
    scoped = len([s for s in graph.subsystems if s.file_count]) >= MIN_CONCEPTS
    for index, flow in enumerate(graph.primary_flows[:2]):
        homes = {n.subsystem for i in flow.node_ids if (n := graph.node(i)) is not None}
        home = homes.pop() if scoped and len(homes) == 1 else None
        flow_kind = _flow_kind(graph, flow.node_ids) or kind
        steps = []
        for node_id in flow.node_ids:
            node = graph.node(node_id)
            if node is not None:
                steps.append(
                    WalkthroughStep(target_id=node.id, label=node.label, detail=node.description)
                )
        walks.append(
            Walkthrough(
                id=f"walk:{index}",
                name=_FOLLOW[flow_kind]
                if all(w.name != _FOLLOW[flow_kind] for w in walks)
                else flow.name,
                kind=flow_kind,
                level=ArchitectureLevel.COMPONENT,
                steps=steps,
                description=flow.description or flow.name,
                provenance=flow.provenance,
                subsystem=home,
            )
        )
    return walks


def overview_walkthroughs(graph: ArchitectureGraph) -> list[Walkthrough]:
    """Walkthroughs that belong on the repository overview rather than inside a part."""
    return [w for w in graph.walkthroughs if w.subsystem is None]


def baseline_learning_path(graph: ArchitectureGraph, name: str) -> list[LearningStep]:
    steps = [
        LearningStep(
            title=f"What problem does {name} solve?",
            why="Start with what the project says about itself.",
            question=f"What problem does {name} solve, and who uses it?",
        )
    ]
    concepts = graph.system_model.concepts
    if concepts:
        steps.append(
            LearningStep(
                title="How it works at a high level",
                why=f"{len(concepts)} major parts and how they connect.",
                target_kind=LearningTargetKind.CONCEPT,
                target_id=concepts[0].id,
            )
        )
    if overview_walkthroughs(graph):
        walk = overview_walkthroughs(graph)[0]
        steps.append(
            LearningStep(
                title=walk.name,
                why="One concrete path through the code, step by step.",
                target_kind=LearningTargetKind.WALKTHROUGH,
                target_id=walk.id,
            )
        )
    for subsystem in [s for s in graph.subsystems if s.id != "sub:other-modules"][:2]:
        steps.append(
            LearningStep(
                title=f"Explore {subsystem.label}",
                why=subsystem.description or f"{subsystem.file_count} source files.",
                target_kind=LearningTargetKind.SUBSYSTEM,
                target_id=subsystem.id,
            )
        )
    return steps[:MAX_LEARNING_STEPS]


def recommended_start(graph: ArchitectureGraph, name: str) -> str:
    if overview_walkthroughs(graph):
        walk = overview_walkthroughs(graph)[0]
        return f"What happens, step by step, from {walk.steps[0].label} to {walk.steps[-1].label}?"
    if graph.system_model.concepts:
        return f"How do the main parts of {name} fit together?"
    return f"What does {name} do?"


# ---------------------------------------------------------------------------
# The Nemotron pass
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You orient a developer who has never seen this repository and may not know "
    "its field. You are given what the repository says about itself, its major "
    "parts (subsystems), components RepoLab detected, and any official "
    "documentation retrieved.\n"
    "\n"
    "Explain in plain language: what it is, what problem it solves, who uses it, "
    "and how it roughly works.\n"
    "\n"
    "Then give a mental model: 3 to 8 concepts of how the software works when it "
    "runs, connected by relationships, and one walkthrough that follows a single "
    "request, job, command or run through them. Concepts are runtime ideas, not a "
    "list of the subsystems -- the subsystems are shown separately. For example a "
    "web application might be browser -> frontend -> API -> database; a data "
    "engine might be application -> planner -> scheduler -> workers; a CLI might "
    "be command -> parser -> handler -> output. Use whatever this repository's "
    "evidence actually shows. When the project has its own term for a concept, "
    "give a plain label and put the term in 'term' (label 'Query optimizer', "
    "term 'Catalyst'). Explain jargon instead of assuming it.\n"
    "\n"
    "Rules:\n"
    "- Every concept must list subsystem ids, component ids, or evidence copied "
    "exactly from the input: a 'path' (a file or a directory listed), a "
    "'path:line', or a documentation URL.\n"
    "- Every relationship must cite one such piece of evidence.\n"
    "- The walkthrough's consecutive steps must be concepts you related.\n"
    "- Only use ids that appear in the input. Never invent files, components, "
    "timings, sizes or counts.\n"
    "- repository_type must be one of the candidates listed.\n"
    "- Write prose without citations in it; put them in the evidence lists.\n"
    "- Prefer a simple, accurate explanation over a complete one. Leave out what "
    "the evidence does not show."
)


class OrientationSubsystem(BaseModel):
    id: str
    label: str = Field(default="", description="A friendly name, e.g. 'Machine learning (MLlib)'")
    description: str = Field(default="", description="What this part contains, one sentence")


class OrientationConcept(BaseModel):
    id: str = Field(description="A short id you choose, e.g. 'scheduler'")
    label: str
    term: str = Field(default="", description="The project's own name for it, if different")
    what_it_does: str
    why_it_matters: str = ""
    role: str = Field(default="", description="Its role in the system, one short sentence")
    subsystem_ids: list[str] = Field(default_factory=list)
    member_ids: list[str] = Field(default_factory=list, description="Component ids")
    evidence: list[str] = Field(default_factory=list, description="'path', 'path:line' or URL")


class OrientationLink(BaseModel):
    source: str = Field(description="A concept id you defined")
    target: str
    type: str = Field(description="One of the edge types listed")
    description: str = ""
    evidence: str = Field(description="'path', 'path:line', URL, or a component id")


class OrientationStep(BaseModel):
    title: str
    why: str = ""
    target: str = Field(default="", description="A concept, subsystem or component id, or empty")
    question: str = Field(default="", description="A question to ask about this step")


class OrientationWalkStep(BaseModel):
    target: str = Field(description="A concept id you defined")
    detail: str = ""


class OrientationWalkthrough(BaseModel):
    name: str
    description: str = ""
    steps: list[OrientationWalkStep] = Field(default_factory=list)


class RepositoryOrientation(BaseModel):
    name: str = ""
    one_line_description: str
    repository_type: str = ""
    beginner_explanation: str = Field(description="Three to five plain sentences")
    primary_purpose: str = ""
    primary_users: str = ""
    recommended_start: str = Field(default="", description="A first question to ask")
    evidence: list[str] = Field(default_factory=list, description="What the explanation rests on")
    subsystems: list[OrientationSubsystem] = Field(default_factory=list)
    concepts: list[OrientationConcept] = Field(default_factory=list)
    links: list[OrientationLink] = Field(default_factory=list)
    learning_path: list[OrientationStep] = Field(default_factory=list)
    walkthrough: OrientationWalkthrough | None = None


def build_orientation_prompt(
    graph: ArchitectureGraph, facts: FactSheet, decision: TypeDecision
) -> str:
    lines = [f"NAME: {facts.name}"]
    if facts.self_description:
        lines += ["", "WHAT THE REPOSITORY SAYS ABOUT ITSELF"]
        lines += [f"- [{f.evidence.label}] {f.text}" for f in facts.self_description]
    if facts.headings:
        lines += ["", "README SECTIONS: " + " | ".join(facts.headings)]
    if decision.provenance is Provenance.STATIC:
        lines += ["", f"REPOSITORY TYPE (decided from evidence): {decision.kind.value}"]
    else:
        lines += [
            "",
            "REPOSITORY TYPE CANDIDATES: " + ", ".join(k.value for k in decision.candidates),
        ]
    lines += [f"- {r}" for r in decision.reasons[:8]]

    lines += [
        "",
        "SUBSYSTEMS (id | directory | source files | languages | main subdirectories | "
        "starting files)",
    ]
    for s in graph.subsystems:
        starts = ", ".join(p.path for p in s.starting_points if not p.is_directory)
        root = next((p.path for p in s.starting_points if p.is_directory), None)
        outline = ", ".join(f"{root}/{name}" if root else name for name in s.outline)
        lines.append(
            f"- {s.id} | {', '.join(s.paths[:3]) or '(root)'} | {s.file_count} | "
            f"{', '.join(s.languages)} | {outline} | {starts}"
        )

    ranked = sorted(graph.nodes, key=lambda n: -n.learning_importance)[:MAX_PROMPT_COMPONENTS]
    lines += ["", "COMPONENTS (id | category | label | subsystem | evidence)"]
    for node in ranked:
        ref = node.source_references[0]
        lines.append(
            f"- {node.id} | {node.category.value} | {node.label} | {node.subsystem or '-'} | "
            f"{ref.path}:{ref.start_line or ''}"
        )
    if graph.primary_flows:
        lines += ["", "FLOWS DETECTED"]
        lines += [f"- {' -> '.join(f.node_ids)}" for f in graph.primary_flows]
    if facts.docs:
        lines += ["", "OFFICIAL DOCUMENTATION RETRIEVED"]
        lines += [f"- {d.url} :: {(d.excerpt or '')[:300]}" for d in facts.docs]
    lines += ["", f"EDGE TYPES: {', '.join(t.value for t in EdgeType)}", "", "Orient a newcomer."]
    return "\n".join(lines)


class _Citations:
    """Resolves what the model cited into evidence, or nothing."""

    def __init__(self, graph: ArchitectureGraph, index: SourceIndex, facts: FactSheet) -> None:
        self.graph = graph
        self.index = index
        self.docs = {d.url.split("#", 1)[0].rstrip("/"): d for d in facts.docs}
        self.directories = {
            "/".join(p.split("/")[:depth])
            for p in index.all_paths
            for depth in range(1, p.count("/") + 1)
        }

    def resolve(self, text: str, concepts: dict[str, str] | None = None) -> Evidence | None:
        value = text.strip().strip("`'\"")
        if not value:
            return None
        if value.startswith(("http://", "https://")):
            doc = self.docs.get(value.split("#", 1)[0].rstrip("/"))
            if doc is None:
                return None
            return Evidence(
                kind=EvidenceKind.DOCUMENTATION,
                label=doc.title or doc.url,
                url=doc.url,
                excerpt=(doc.excerpt or "")[:300] or None,
            )
        subsystem = self.graph.subsystem(value)
        if subsystem is not None:
            return Evidence(
                kind=EvidenceKind.SUBSYSTEM, label=subsystem.label, target_id=subsystem.id
            )
        node = self.graph.node(value)
        if node is not None:
            return Evidence(kind=EvidenceKind.COMPONENT, label=node.label, target_id=node.id)
        path, _, line_text = value.rpartition(":")
        if not (path and line_text.isdigit()):
            path, line_text = value, ""
        path = path.rstrip("/")
        line = int(line_text) if line_text else None
        if path in self.directories and line is None:
            return Evidence(kind=evidence_kind(path + "/x"), label=f"{path}/", path=path)
        if not self.index.line_exists(path, line):
            return None
        text_of = self.index.text(path) if line else None
        if line and text_of:
            return line_evidence(path, line, text_of)
        return Evidence(kind=evidence_kind(path), label=path, path=path, line=line)


async def orient(
    graph: ArchitectureGraph,
    index: SourceIndex,
    manifest: RepoManifest | None,
    router: SynthesisRouter | None,
    *,
    docs: list[DocumentationReference] | None = None,
) -> ArchitectureGraph:
    """Add levels 0 and 1 to *graph*: deterministically, then refined by Super."""
    facts = gather_facts(index, manifest, docs)
    decision = classify_repository(graph, facts, manifest)
    graph.understanding = baseline_understanding(facts, decision, graph, manifest)
    graph.system_model = baseline_system_model(graph)
    graph.walkthroughs = baseline_walkthroughs(graph, walkthrough_kind(graph, decision.kind))
    graph.learning_path = baseline_learning_path(graph, facts.name)
    graph.understanding.recommended_start = recommended_start(graph, facts.name)

    if router is None:
        return graph
    try:
        completion = await router.super(
            purpose=Purpose.REPO_ARCHITECTURE,
            schema=RepositoryOrientation,
            system=SYSTEM_PROMPT,
            prompt=build_orientation_prompt(graph, facts, decision),
            max_tokens=ORIENTATION_MAX_TOKENS,
            timeout_seconds=ORIENTATION_TIMEOUT_SECONDS,
        )
    except LLMError as exc:
        log.warning("architecture.orientation_failed", error=str(exc))
        graph.gaps.append(
            "Nemotron could not be reached, so the overview uses the repository's own words "
            "and its structure."
        )
        return graph
    return apply_orientation(graph, completion.value, index, facts, decision)


def _text(value: str, limit: int, drop: Any) -> str | None:
    value = value.strip()
    if not value:
        return None
    if _MEASUREMENT.search(value):
        drop("invented measurement")
        return None
    return value[:limit]


def apply_orientation(
    graph: ArchitectureGraph,
    orientation: RepositoryOrientation,
    index: SourceIndex,
    facts: FactSheet,
    decision: TypeDecision,
) -> ArchitectureGraph:
    """Merge Super's orientation, keeping only what cites real evidence."""
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    citations = _Citations(graph, index, facts)
    accepted = False
    understanding = graph.understanding
    assert understanding is not None

    # Level 0 -- what this repository is.
    cited = [e for e in (citations.resolve(c) for c in orientation.evidence) if e is not None]
    if orientation.evidence and len(cited) < len(orientation.evidence):
        drop("citation of a file, line or page that does not exist")
    if cited or understanding.evidence:
        for name, limit in (
            ("one_line_description", 240),
            ("beginner_explanation", 900),
            ("primary_purpose", 300),
            ("primary_users", 300),
            ("recommended_start", 200),
        ):
            value = _text(getattr(orientation, name), limit, drop)
            if value:
                setattr(understanding, name, value)
                understanding.inferred_fields.append(name)
                accepted = True
        understanding.evidence = _unique([*understanding.evidence, *cited])
        understanding.confidence = min(0.8, understanding.confidence + 0.2)
    else:
        drop("orientation without evidence")
    if decision.provenance is Provenance.INFERRED and orientation.repository_type:
        try:
            chosen = RepositoryKind(orientation.repository_type)
        except ValueError:
            chosen = None
        if chosen is not None and chosen in decision.candidates:
            understanding.repository_type = chosen
            understanding.type_provenance = Provenance.INFERRED
            understanding.inferred_fields.append("repository_type")
        else:
            drop("repository type outside the evidenced candidates")

    # Subsystem names -- never membership.
    for proposal in orientation.subsystems:
        subsystem = graph.subsystem(proposal.id)
        if subsystem is None:
            drop("unknown subsystem")
            continue
        for name, limit in (("label", 60), ("description", 240)):
            value = _text(getattr(proposal, name), limit, drop)
            if value:
                setattr(subsystem, name, value)
                if name not in subsystem.inferred_fields:
                    subsystem.inferred_fields.append(name)
                accepted = True

    # Level 1 -- concepts, each backed by something real.
    concepts: list[SystemConcept] = []
    for proposal in orientation.concepts:
        concept_id = f"concept:{slug(proposal.id or proposal.label)}"
        if any(c.id == concept_id for c in concepts):
            continue
        subsystems = [s for s in proposal.subsystem_ids if graph.subsystem(s) is not None]
        members = [m for m in proposal.member_ids if graph.node(m) is not None]
        evidence = [e for e in (citations.resolve(c) for c in proposal.evidence) if e is not None]
        if len(evidence) < len(proposal.evidence):
            drop("citation of a file, line or page that does not exist")
        if not (subsystems or members or evidence):
            drop("concept without evidence")
            continue
        label = _text(proposal.label, 60, drop)
        what = _text(proposal.what_it_does, 300, drop)
        if not label or not what:
            continue
        evidence = [
            *(
                Evidence(kind=EvidenceKind.SUBSYSTEM, label=graph.subsystem(s).label, target_id=s)  # type: ignore[union-attr]
                for s in subsystems
            ),
            *(
                Evidence(kind=EvidenceKind.COMPONENT, label=graph.node(m).label, target_id=m)  # type: ignore[union-attr]
                for m in members[:4]
            ),
            *evidence,
        ]
        importance = (
            max(
                (graph.subsystem(s).learning_importance for s in subsystems),  # type: ignore[union-attr]
                default=0.0,
            )
            + sum(graph.node(m).learning_importance for m in members) * 0.1
        )  # type: ignore[union-attr]
        concepts.append(
            SystemConcept(
                id=concept_id,
                label=label,
                term=_text(proposal.term, 60, drop)
                if proposal.term.strip().lower() != label.lower()
                else None,
                what_it_does=what,
                why_it_matters=_text(proposal.why_it_matters, 300, drop) or "",
                role=_text(proposal.role, 200, drop) or "",
                subsystem_ids=subsystems,
                member_ids=members,
                evidence=_unique(evidence)[:6],
                confidence=0.6 if (subsystems or members) else 0.5,
                provenance=Provenance.INFERRED,
                learning_importance=round(importance, 2),
            )
        )
        if len(concepts) >= MAX_CONCEPTS:
            break
    if len(concepts) >= MIN_CONCEPTS:
        model = SystemModel(concepts=concepts, provenance=Provenance.INFERRED)
        model.links = derive_links(graph, model)
        ids = {c.id for c in concepts}
        by_proposed = {
            slug(p.id or p.label): f"concept:{slug(p.id or p.label)}" for p in orientation.concepts
        }
        for proposal in orientation.links:
            source = by_proposed.get(slug(proposal.source), f"concept:{slug(proposal.source)}")
            target = by_proposed.get(slug(proposal.target), f"concept:{slug(proposal.target)}")
            if source not in ids or target not in ids or source == target:
                drop("relationship to an unknown concept")
                continue
            try:
                edge_type = EdgeType(proposal.type)
            except ValueError:
                drop("unknown relationship type")
                continue
            evidence = citations.resolve(proposal.evidence)
            if evidence is None:
                drop("relationship citing evidence that does not exist")
                continue
            if any((link.source, link.target) == (source, target) for link in model.links):
                continue
            model.links.append(
                ConceptLink(
                    source=source,
                    target=target,
                    type=edge_type,
                    description=_text(proposal.description, 200, drop),
                    provenance=Provenance.INFERRED,
                    evidence=[evidence],
                )
            )
        graph.system_model = model
        accepted = True
    elif orientation.concepts:
        drop("too few evidence-backed concepts")

    # A walkthrough through the concepts, only along links that exist.
    walk = orientation.walkthrough
    if walk is not None and graph.system_model.provenance is Provenance.INFERRED:
        concept_ids = {c.id for c in graph.system_model.concepts}
        lookup = {slug(c.id.removeprefix("concept:")): c.id for c in graph.system_model.concepts}
        targets = [lookup.get(slug(step.target), "") for step in walk.steps]
        links = graph.system_model.links
        linked = {(k.source, k.target) for k in links} | {(k.target, k.source) for k in links}
        if len(targets) < 2 or any(t not in concept_ids for t in targets):
            drop("walkthrough through unknown concepts")
        elif any((a, b) not in linked for a, b in pairwise(targets)):
            drop("walkthrough between unrelated concepts")
        else:
            labels = {c.id: c.label for c in graph.system_model.concepts}
            kind = walkthrough_kind(graph, understanding.repository_type)
            graph.walkthroughs = [
                Walkthrough(
                    id="walk:system",
                    name=_text(walk.name, 80, drop) or _FOLLOW[kind],
                    kind=kind,
                    level=ArchitectureLevel.SYSTEM,
                    steps=[
                        WalkthroughStep(
                            target_id=t, label=labels[t], detail=_text(s.detail, 240, drop)
                        )
                        for t, s in zip(targets, walk.steps, strict=True)
                    ],
                    description=_text(walk.description, 300, drop),
                    provenance=Provenance.INFERRED,
                ),
                *graph.walkthroughs,
            ][:3]
            accepted = True

    # The learning path, pointing only at things that exist.
    steps: list[LearningStep] = []
    concept_lookup = {
        slug(c.id.removeprefix("concept:")): c.id for c in graph.system_model.concepts
    }
    for proposal in orientation.learning_path[:MAX_LEARNING_STEPS]:
        title = _text(proposal.title, 100, drop)
        if not title:
            continue
        question = _text(proposal.question, 200, drop)
        target = proposal.target.strip()
        kind, target_id = LearningTargetKind.QUESTION, None
        if target:
            if slug(target) in concept_lookup:
                kind, target_id = LearningTargetKind.CONCEPT, concept_lookup[slug(target)]
            elif graph.subsystem(target) is not None:
                kind, target_id = LearningTargetKind.SUBSYSTEM, target
            elif graph.node(target) is not None:
                kind, target_id = LearningTargetKind.NODE, target
            elif any(w.id == target for w in graph.walkthroughs):
                kind, target_id = LearningTargetKind.WALKTHROUGH, target
            else:
                drop("learning step pointing at something that does not exist")
        if kind is LearningTargetKind.QUESTION and not question:
            continue
        steps.append(
            LearningStep(
                title=title,
                why=_text(proposal.why, 200, drop) or "",
                target_kind=kind,
                target_id=target_id,
                question=question,
                provenance=Provenance.INFERRED,
            )
        )
    if len(steps) >= 2:
        graph.learning_path = steps
        accepted = True

    if accepted:
        graph.generated_by = GraphSource.SUPER_ENRICHED
    for reason, count in dropped.items():
        graph.dropped[reason] = graph.dropped.get(reason, 0) + count
    return graph
