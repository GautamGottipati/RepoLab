"""The generic architecture model.

An architecture answers *"how does this software actually work?"* -- so its nodes
are runtime and system concepts (an API, a pipeline step, a scheduler, a
database), not files, and its edges are what happens between them (a call, a
read, data flowing on), not imports.

The model is deliberately framework-neutral. ``category`` is one of a small set
of broad roles every repository can be described in; ``subtype`` is where a
framework adapter may say something more specific (``spark_join``,
``postgres``), and nothing in the generic graph depends on it.

Every fact carries its **provenance**:

    static     read directly from source or configuration, with a reference
    inferred   interpreted -- by RepoLab or Nemotron -- from that evidence

Measurements are not stored here at all. They are overlaid on request from real
runs (:mod:`repolab.architecture.runtime`), so an inferred value and a measured
one can never be confused by having lived in the same field.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from repolab.evidence.models import CodeReference
from repolab.manifest.architecture import GraphSource

SCHEMA_VERSION = 4

#: The oldest version :func:`load_graph` reads directly; newer fields default.
DIRECTLY_READABLE = 2

#: Deterministic evidence never claims certainty.
MAX_CONFIDENCE = 0.95

#: The first view shows about this many components; the rest are grouped.
MAX_NODES = 20


class NodeCategory(StrEnum):
    """Broad architectural roles. Every repository is described in these."""

    ENTRY_POINT = "entry_point"
    INPUT = "input"
    OUTPUT = "output"
    FRONTEND = "frontend"
    API = "api"
    SERVICE = "service"
    WORKER = "worker"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    STORAGE = "storage"
    PIPELINE = "pipeline"
    TRANSFORM = "transform"
    PROCESSOR = "processor"
    SCHEDULER = "scheduler"
    TASK = "task"
    CLI = "cli"
    COMMAND = "command"
    MODEL = "model"
    INFERENCE = "inference"
    EXTERNAL_SERVICE = "external_service"
    LIBRARY = "library"
    CORE_ENGINE = "core_engine"
    CONFIGURATION = "configuration"
    OTHER = "other"


class EdgeType(StrEnum):
    """What happens between two components at runtime."""

    DATA_FLOW = "data_flow"
    CONTROL_FLOW = "control_flow"
    CALLS = "calls"
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    PUBLISHES_TO = "publishes_to"
    CONSUMES_FROM = "consumes_from"
    TRIGGERS = "triggers"
    DEPENDS_ON = "depends_on"
    ROUTES_TO = "routes_to"
    CREATES = "creates"


class Provenance(StrEnum):
    STATIC = "static"
    """Read directly from source or configuration."""

    INFERRED = "inferred"
    """Interpreted from evidence, by RepoLab's rules or by Nemotron."""


class ArchitectureViewKind(StrEnum):
    """Which presentation suits the repository best. Chosen from categories present."""

    DATA_FLOW = "data_flow"
    REQUEST_FLOW = "request_flow"
    SERVICE_FLOW = "service_flow"
    WORKFLOW = "workflow"
    COMMAND_FLOW = "command_flow"
    COMPONENT_FLOW = "component_flow"
    SYSTEM_OVERVIEW = "system_overview"


class ArchitectureLevel(StrEnum):
    """Where something sits in the progressive explanation of a repository."""

    SYSTEM = "system"
    """A major concept of how the software works (3-8 of them)."""

    SUBSYSTEM = "subsystem"
    """A major part of the repository -- a module, package or top-level area."""

    COMPONENT = "component"
    """A runtime component the analyzers found inside a subsystem."""

    IMPLEMENTATION = "implementation"
    """Classes, functions and files -- reached through source references."""


class RepositoryKind(StrEnum):
    """What a repository *is*, separate from the ways it can be used.

    A large framework with a launcher script is not a ``cli_application``: its
    interaction surfaces (Python API, SQL, CLI...) are recorded separately.
    """

    DISTRIBUTED_SYSTEM = "distributed_system"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    WEB_APPLICATION = "web_application"
    API_SERVICE = "api_service"
    DATA_PLATFORM = "data_platform"
    WORKFLOW_SYSTEM = "workflow_system"
    DATABASE = "database"
    CLI_APPLICATION = "cli_application"
    ML_SYSTEM = "ml_system"
    DEVELOPER_TOOL = "developer_tool"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class EvidenceKind(StrEnum):
    """What kind of thing backs a claim -- the answer to "how do you know?"."""

    README = "readme"
    SOURCE = "source"
    CONFIGURATION = "configuration"
    BUILD = "build"
    DOCUMENTATION = "documentation"
    """Official documentation the investigation retrieved. Never invented."""

    COMPONENT = "component"
    """A component RepoLab detected, which carries its own references."""

    SUBSYSTEM = "subsystem"
    """A part of the repository, which carries its own paths."""


class Evidence(BaseModel):
    """One citation behind an explanation, of whatever kind."""

    kind: EvidenceKind
    label: str
    """What to show: ``README.md:3``, a URL, a component or subsystem name."""

    path: str | None = None
    line: int | None = None
    url: str | None = None
    target_id: str | None = None
    excerpt: str | None = None
    end_line: int | None = None
    symbol: str | None = None
    verified: bool = False
    """True once RepoLab re-read ``path`` at the investigated commit and found the anchor."""


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    """Some of the architecture could not be determined; ``gaps`` says what."""


#: Values a node property may hold. Small and serialisable on purpose.
PropertyValue = str | int | float | bool | list[str]


class ArchitectureNode(BaseModel):
    """One meaningful component of the system."""

    id: str
    label: str
    category: NodeCategory
    subtype: str | None = None
    """A framework adapter's refinement. The generic graph never depends on it."""

    technology: str | None = None
    description: str | None = None
    runtime_significance: str | None = None

    source_references: list[CodeReference] = Field(default_factory=list)
    properties: dict[str, PropertyValue] = Field(default_factory=dict)
    """Static facts read from the source, e.g. a route list or a join key."""

    confidence: float = Field(default=0.7, ge=0.0, le=MAX_CONFIDENCE)
    provenance: Provenance = Provenance.STATIC
    detected_because: str = ""
    """Why RepoLab thinks this exists, in one sentence a reader can check."""

    inferred_fields: list[str] = Field(default_factory=list)
    """Fields a model wrote (``label``, ``description``...), shown as inferred."""

    children: list[str] = Field(default_factory=list)
    """Labels of the lower-level details grouped into this component."""

    surface: str | None = None
    """The manifest interaction surface this component belongs to, if any."""

    level: ArchitectureLevel = ArchitectureLevel.COMPONENT
    subsystem: str | None = None
    """Id of the subsystem whose paths contain this component."""

    learning_importance: float = 0.0
    """How useful this is for understanding the repository. Deterministic."""

    @property
    def is_supported(self) -> bool:
        return bool(self.source_references)

    @property
    def paths(self) -> set[str]:
        return {ref.path for ref in self.source_references}


class ArchitectureEdge(BaseModel):
    """What happens between two components."""

    source: str
    target: str
    type: EdgeType
    description: str | None = None
    provenance: Provenance = Provenance.STATIC
    evidence: list[CodeReference] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=MAX_CONFIDENCE)


class ImportantFile(BaseModel):
    """A file that helps someone understand the system, and why."""

    path: str
    role: str
    node_ids: list[str] = Field(default_factory=list)


class ArchitectureFlow(BaseModel):
    """One important path through the system. Need not be linear across flows."""

    name: str
    node_ids: list[str]
    description: str | None = None
    provenance: Provenance = Provenance.STATIC


class StartingPoint(BaseModel):
    """A place a person should open first, and what they will learn there."""

    path: str
    is_directory: bool = True
    reason: str
    learn_about: list[str] = Field(default_factory=list)
    """Child areas worth knowing, e.g. ``scheduler``, ``storage`` -- from the tree."""


class ModuleDependency(BaseModel):
    """One subsystem needing another, as its build file or an import says."""

    target: str
    """The subsystem depended on."""
    via: str
    """``maven``, ``gradle``, ``sbt``, ``npm``, ``cargo``, ``go`` or ``import``."""
    evidence: CodeReference


class Subsystem(BaseModel):
    """A major part of the repository. Membership comes from paths, never a model."""

    id: str
    label: str
    description: str | None = None
    paths: list[str]
    """Directory prefixes, without a trailing slash. ``""`` is the repository root."""

    declared_by: str
    """Why this is a subsystem, e.g. ``pom.xml module`` or ``top-level directory``."""

    file_count: int = 0
    languages: list[str] = Field(default_factory=list)
    learning_importance: float = 0.0
    importance_signals: dict[str, float] = Field(default_factory=dict)
    """Each deterministic signal and what it added, so the ranking is checkable."""

    component_ids: list[str] = Field(default_factory=list)
    starting_points: list[StartingPoint] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)
    """The subsystem's main child directories, largest first. From the tree."""

    evidence: list[Evidence] = Field(default_factory=list)
    provenance: Provenance = Provenance.STATIC
    inferred_fields: list[str] = Field(default_factory=list)

    # -- schema 4: module relationships, from build files and imports -------------
    depends_on: list[ModuleDependency] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)
    """Ids of subsystems whose build or imports depend on this one."""
    key_packages: list[str] = Field(default_factory=list)

    def contains(self, path: str) -> bool:
        return any(p == "" or path == p or path.startswith(p + "/") for p in self.paths)

    def depth(self, path: str) -> int:
        """Length of the longest of this subsystem's prefixes that contains *path*."""
        return max(
            (len(p) for p in self.paths if p == "" or path == p or path.startswith(p + "/")),
            default=-1,
        )


class SystemConcept(BaseModel):
    """One major idea of how the software works, explained for a newcomer."""

    id: str
    label: str
    term: str | None = None
    """The project's own name for it, when that differs: "Internal name: Catalyst"."""

    what_it_does: str = ""
    why_it_matters: str = ""
    role: str = ""
    subsystem_ids: list[str] = Field(default_factory=list)
    member_ids: list[str] = Field(default_factory=list)
    """Components that implement this concept."""

    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=MAX_CONFIDENCE)
    provenance: Provenance = Provenance.STATIC
    learning_importance: float = 0.0


class ConceptLink(BaseModel):
    """How two concepts relate. Derived from component edges, or cited."""

    source: str
    target: str
    type: EdgeType
    description: str | None = None
    provenance: Provenance = Provenance.STATIC
    evidence: list[Evidence] = Field(default_factory=list)


class SystemModel(BaseModel):
    """Level 1: how the software works, in a handful of concepts."""

    concepts: list[SystemConcept] = Field(default_factory=list)
    links: list[ConceptLink] = Field(default_factory=list)
    provenance: Provenance = Provenance.STATIC
    """``inferred`` when Nemotron proposed the concepts; static when they are subsystems."""

    def concept(self, concept_id: str) -> SystemConcept | None:
        return next((c for c in self.concepts if c.id == concept_id), None)


class LearningTargetKind(StrEnum):
    CONCEPT = "concept"
    SUBSYSTEM = "subsystem"
    NODE = "node"
    WALKTHROUGH = "walkthrough"
    QUESTION = "question"
    """No place to go to: the step is a question for Ask RepoLab."""


class LearningStep(BaseModel):
    title: str
    why: str = ""
    target_kind: LearningTargetKind = LearningTargetKind.QUESTION
    target_id: str | None = None
    question: str | None = None
    provenance: Provenance = Provenance.STATIC


class WalkthroughKind(StrEnum):
    REQUEST = "request"
    JOB = "job"
    COMMAND = "command"
    DAG_RUN = "dag_run"
    CALL = "call"
    EVENT = "event"


class WalkthroughStep(BaseModel):
    target_id: str
    """A concept id (system level) or a node id (component level)."""

    label: str
    detail: str | None = None


class Walkthrough(BaseModel):
    """ "Follow one request / job / command" -- only along evidenced relationships."""

    id: str
    name: str
    kind: WalkthroughKind
    level: ArchitectureLevel
    steps: list[WalkthroughStep]
    description: str | None = None
    provenance: Provenance = Provenance.STATIC
    subsystem: str | None = None
    """Set when the path runs inside one subsystem of a larger repository: it is
    shown when that subsystem is explored, not on the repository overview."""


class RepositoryUnderstanding(BaseModel):
    """Level 0: what this repository is, grounded in what it says about itself."""

    name: str
    one_line_description: str | None = None
    repository_type: RepositoryKind = RepositoryKind.UNKNOWN
    type_provenance: Provenance = Provenance.STATIC
    type_candidates: list[RepositoryKind] = Field(default_factory=list)
    type_reasons: list[str] = Field(default_factory=list)
    """The deterministic signals behind the type, one sentence each."""

    beginner_explanation: str | None = None
    primary_purpose: str | None = None
    primary_users: str | None = None
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    main_interaction_surfaces: list[str] = Field(default_factory=list)
    recommended_start: str | None = None
    """A first question worth asking, opened in Ask RepoLab."""

    confidence: float = Field(default=0.5, ge=0.0, le=MAX_CONFIDENCE)
    evidence: list[Evidence] = Field(default_factory=list)
    inferred_fields: list[str] = Field(default_factory=list)
    """Fields Nemotron wrote. Anything else was quoted or computed."""


# -- schema 4: the runtime architecture, from verified code ---------------------


class KeyAbstraction(BaseModel):
    """A central type in the code, ranked by how much of the repository uses it.

    Its definition range was computed and re-read by :mod:`repolab.sources`.
    """

    id: str
    name: str
    kind: str
    definition: CodeReference
    referenced_by: int = 0
    """How many other scanned files mention it."""
    subsystem: str | None = None
    usages: list[CodeReference] = Field(default_factory=list)
    """A few verified places that use it."""


class CodeRelationType(StrEnum):
    CREATES = "creates"
    CALLS = "calls"
    EXTENDS = "extends"
    REFERENCES = "references"
    """Named in the code, without a call or construction visible on that line."""


class CodeRelation(BaseModel):
    """One abstraction's code naming another, at a verified line."""

    source: str
    target: str
    type: CodeRelationType
    evidence: CodeReference


class Responsibility(BaseModel):
    text: str
    reference: CodeReference
    provenance: Provenance = Provenance.INFERRED


class RuntimeComponent(BaseModel):
    """A part of the running system, made of verified definitions.

    Not a directory: "Scheduler", "API server", "Executor" -- whatever the
    evidence supports. Its members are :class:`KeyAbstraction` ids, and every
    responsibility points at a verified range.
    """

    id: str
    name: str
    description: str | None = None
    members: list[str] = Field(default_factory=list)
    responsibilities: list[Responsibility] = Field(default_factory=list)
    source_references: list[CodeReference] = Field(default_factory=list)
    subsystem_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    """Detected components (analyzers) whose code lives in the same files."""
    provenance: Provenance = Provenance.STATIC
    inferred_fields: list[str] = Field(default_factory=list)


class RuntimeLink(BaseModel):
    """What passes between two runtime components, and where the code shows it."""

    source: str
    target: str
    type: CodeRelationType
    label: str | None = None
    """What moves along the arrow ("tasks", "HTTP request"), when it can be said."""
    label_provenance: Provenance = Provenance.INFERRED
    evidence: list[CodeReference] = Field(default_factory=list)


class RuntimeModel(BaseModel):
    components: list[RuntimeComponent] = Field(default_factory=list)
    links: list[RuntimeLink] = Field(default_factory=list)
    provenance: Provenance = Provenance.STATIC


class FlowStep(BaseModel):
    sequence: int
    component: str
    action: str
    input: str | None = None
    output: str | None = None
    references: list[CodeReference] = Field(default_factory=list)
    """The verified line where this step hands over to the next."""


class Flow(BaseModel):
    """An end-to-end path through runtime components, along verified links."""

    id: str
    name: str
    description: str | None = None
    steps: list[FlowStep]
    ordered: bool = False
    """True only when every hand-over is a call or construction RepoLab found
    in the caller's own code -- the condition for drawing a sequence diagram."""
    provenance: Provenance = Provenance.STATIC


class ReadingItem(BaseModel):
    title: str
    why: str
    reference: CodeReference
    component: str | None = None


class ArchitectureGraph(BaseModel):
    """A repository's architecture, every part tied to evidence."""

    schema_version: int = SCHEMA_VERSION

    nodes: list[ArchitectureNode] = Field(default_factory=list)
    edges: list[ArchitectureEdge] = Field(default_factory=list)

    entry_points: list[str] = Field(default_factory=list)
    """Ids of the nodes where execution starts."""

    important_files: list[ImportantFile] = Field(default_factory=list)
    interaction_surfaces: list[str] = Field(default_factory=list)

    summary: str | None = None
    summary_provenance: Provenance = Provenance.INFERRED
    primary_flows: list[ArchitectureFlow] = Field(default_factory=list)
    default_view: ArchitectureViewKind = ArchitectureViewKind.SYSTEM_OVERVIEW

    completeness: Completeness = Completeness.COMPLETE
    gaps: list[str] = Field(default_factory=list)
    """What could not be determined, in sentences a reader can act on."""

    generated_by: GraphSource = GraphSource.DETERMINISTIC
    dropped: dict[str, int] = Field(default_factory=dict)
    """What validation refused, by reason. Reported, never hidden."""

    # -- progressive levels (schema 3) -----------------------------------------
    understanding: RepositoryUnderstanding | None = None
    system_model: SystemModel = Field(default_factory=SystemModel)
    subsystems: list[Subsystem] = Field(default_factory=list)
    learning_path: list[LearningStep] = Field(default_factory=list)
    walkthroughs: list[Walkthrough] = Field(default_factory=list)

    # -- the runtime architecture, from verified code (schema 4) --------------
    abstractions: list[KeyAbstraction] = Field(default_factory=list)
    relations: list[CodeRelation] = Field(default_factory=list)
    runtime: RuntimeModel = Field(default_factory=RuntimeModel)
    flows: list[Flow] = Field(default_factory=list)
    reading_path: list[ReadingItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_resolve(self) -> ArchitectureGraph:
        known = {node.id for node in self.nodes}
        concepts = {c.id for c in self.system_model.concepts}
        for link in self.system_model.links:
            missing = {link.source, link.target} - concepts
            if missing:
                raise ValueError(f"concept link names unknown concept(s) {sorted(missing)}")
        for edge in self.edges:
            missing = {edge.source, edge.target} - known
            if missing:
                raise ValueError(f"edge {edge.source}->{edge.target} names {sorted(missing)}")
        for flow in self.primary_flows:
            missing = set(flow.node_ids) - known
            if missing:
                raise ValueError(f"flow {flow.name!r} names unknown node(s) {sorted(missing)}")
        components = {c.id for c in self.runtime.components}
        for link in self.runtime.links:
            missing = {link.source, link.target} - components
            if missing:
                raise ValueError(f"runtime link names unknown component(s) {sorted(missing)}")
        for flow in self.flows:
            missing = {step.component for step in flow.steps} - components
            if missing:
                raise ValueError(f"flow {flow.name!r} names unknown component(s) {sorted(missing)}")
        return self

    # -- building -------------------------------------------------------------

    def node(self, node_id: str) -> ArchitectureNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def add_node(self, node: ArchitectureNode) -> ArchitectureNode:
        """Add *node*, merging evidence and properties if the id already exists."""
        existing = self.node(node.id)
        if existing is None:
            self.nodes.append(node)
            return node
        for ref in node.source_references:
            if ref not in existing.source_references:
                existing.source_references.append(ref)
        for key, value in node.properties.items():
            existing.properties.setdefault(key, value)
        existing.description = existing.description or node.description
        existing.subtype = existing.subtype or node.subtype
        existing.technology = existing.technology or node.technology
        return existing

    def add_edge(self, edge: ArchitectureEdge) -> bool:
        """Add *edge* if both ends exist, it is not a self-loop and not a duplicate."""
        known = {node.id for node in self.nodes}
        if edge.source not in known or edge.target not in known or edge.source == edge.target:
            return False
        for existing in self.edges:
            if (existing.source, existing.target) == (edge.source, edge.target):
                for ref in edge.evidence:
                    if ref not in existing.evidence:
                        existing.evidence.append(ref)
                return False
        self.edges.append(edge)
        return True

    def remove_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [e for e in self.edges if node_id not in (e.source, e.target)]
        self.entry_points = [n for n in self.entry_points if n != node_id]
        self.primary_flows = [f for f in self.primary_flows if node_id not in f.node_ids]

    def neighbours(self, node_id: str) -> tuple[list[ArchitectureEdge], list[ArchitectureEdge]]:
        """Incoming and outgoing edges of a node."""
        incoming = [edge for edge in self.edges if edge.target == node_id]
        outgoing = [edge for edge in self.edges if edge.source == node_id]
        return incoming, outgoing

    @property
    def all_paths(self) -> set[str]:
        return {path for node in self.nodes for path in node.paths}

    def subsystem(self, subsystem_id: str) -> Subsystem | None:
        return next((s for s in self.subsystems if s.id == subsystem_id), None)

    def subsystem_for(self, path: str) -> Subsystem | None:
        """The most specific subsystem containing *path*."""
        best = max(self.subsystems, key=lambda s: s.depth(path), default=None)
        return best if best is not None and best.depth(path) >= 0 else None


# ---------------------------------------------------------------------------
# Reading what is stored
# ---------------------------------------------------------------------------

#: The Phase 2 graph's node kinds, mapped onto the generic categories.
_V1_CATEGORY: dict[str, NodeCategory] = {
    "entrypoint": NodeCategory.ENTRY_POINT,
    "module": NodeCategory.CORE_ENGINE,
    "service": NodeCategory.SERVICE,
    "config": NodeCategory.CONFIGURATION,
    "datastore": NodeCategory.DATABASE,
    "external": NodeCategory.LIBRARY,
}

_V1_EDGE: dict[str, EdgeType] = {
    "depends_on": EdgeType.DEPENDS_ON,
    "invokes": EdgeType.CALLS,
    "configures": EdgeType.DEPENDS_ON,
    "reads": EdgeType.READS_FROM,
    "writes": EdgeType.WRITES_TO,
    "exposes": EdgeType.ROUTES_TO,
}


def from_v1(data: dict[str, Any]) -> ArchitectureGraph:
    """Convert a stored Phase 2 graph so older investigations still render.

    The old builder joined every entry point to every dependency, so its edges
    are carried over as ``inferred`` -- they were never detected -- and the graph
    is marked partial with a gap saying to investigate again.
    """
    graph = ArchitectureGraph(
        generated_by=GraphSource(data.get("generated_by", "deterministic")),
        completeness=Completeness.PARTIAL,
        gaps=[
            "This architecture was produced by an earlier version of RepoLab. "
            "Investigate the repository again for components, flows and evidence."
        ],
    )
    for raw in data.get("nodes", []):
        references = [CodeReference.model_validate(ref) for ref in raw.get("file_references", [])]
        if not references:
            continue
        graph.add_node(
            ArchitectureNode(
                id=raw["id"],
                label=raw.get("name") or raw["id"],
                category=_V1_CATEGORY.get(raw.get("kind", ""), NodeCategory.OTHER),
                description=raw.get("description"),
                source_references=references,
                detected_because="found by the earlier file-level detector",
            )
        )
    for raw in data.get("edges", []):
        graph.add_edge(
            ArchitectureEdge(
                source=raw["source"],
                target=raw["target"],
                type=_V1_EDGE.get(raw.get("kind", ""), EdgeType.DEPENDS_ON),
                description=raw.get("description"),
                provenance=Provenance.INFERRED,
                confidence=0.3,
            )
        )
    graph.entry_points = [n.id for n in graph.nodes if n.category is NodeCategory.ENTRY_POINT]
    return graph


def load_graph(data: dict[str, Any]) -> ArchitectureGraph:
    """Whatever version was stored, as the current model."""
    if data.get("schema_version", 1) >= DIRECTLY_READABLE:
        return ArchitectureGraph.model_validate(data)
    return from_v1(data)
