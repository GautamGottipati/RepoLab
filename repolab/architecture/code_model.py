"""The runtime architecture, read from the code itself.

The pattern analyzers find *surfaces* -- routes, CLI parsers, pipeline steps.
What a newcomer needs as well is the system's *machinery*: the handful of types
the rest of the code is built around, and how they use each other. That is
read here, deterministically, from definitions and the lines that name them:

    corpus            the repository's source files (non-test, budgeted)
        |
    symbols           every definition, with the lines it spans
        |
    key abstractions  top-level types, ranked by how many other files use them
        |
    relations         A's own code naming B -- `new B`, `b.call(`, `extends B`
        |             -- each at a verified line
    runtime model     components made of abstractions; links only where a
        |             relation exists between their members
    flows             chains of calls/constructions, in order
    reading path      where execution starts, then the components in flow order

Every line number here is computed by :mod:`repolab.sources` and re-read; no
model is involved. Nemotron may later *name and describe* what this finds
(:mod:`repolab.architecture.runtime_synthesis`); it cannot add a member, a
link or a step that is not here.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from repolab.architecture.analyzers.base import slug
from repolab.architecture.models import (
    ArchitectureGraph,
    CodeRelation,
    CodeRelationType,
    Flow,
    FlowStep,
    KeyAbstraction,
    Provenance,
    ReadingItem,
    RuntimeComponent,
    RuntimeLink,
    RuntimeModel,
)
from repolab.architecture.source import is_peripheral, is_test_path
from repolab.evidence.models import CodeReference
from repolab.sources import MemorySnapshot, SymbolIndex, verify_reference
from repolab.sources.symbols import language_of

#: The corpus: enough of a large repository to find its machinery.
MAX_CORPUS_FILES = 8000
MAX_CORPUS_BYTES = 60_000_000
MAX_FILE_BYTES = 1_000_000

#: How many key abstractions are kept, and how many runtime components shown.
MAX_ABSTRACTIONS = 60
MAX_COMPONENTS = 7
MAX_FLOWS = 4
MAX_FLOW_STEPS = 5
MAX_READING = 8

#: Names that say what a type *is for* in general, not what this system does.
_GENERIC = re.compile(
    r"^(?:Test\w*|\w+(?:Test|Tests|Suite|Spec|Exception|Error|Utils?|Helpers?|Constants)|"
    r"Main|App|Object|String|Int|Long|List|Map|Set|Option|Some|None|Unit|Any|Logging|Logger|"
    r"API|URL|URI|UUID|XML|JSON|SQL|CSV|IO|Log|Row|Seq|Array|Tuple\d*|Utils?|Helpers?|Common|"
    r"Optional|Either|Try|Future|Promise|Iterator|Iterable|Collection|Stream|Callable|Function\d*)$"
)
#: Generic role vocabulary: a type named for what it *does at runtime*. Not
#: specific to any project -- the same words name parts of a web server, a
#: database, a scheduler or a compiler.
_ROLE = re.compile(
    r"(?:Scheduler|Manager|Server|Service|Executor|Engine|Planner|Optimizer|Context|Session|"
    r"Driver|Worker|Master|Backend|Handler|Controller|Router|Store|Cache|Queue|Client|Runner|"
    r"Coordinator|Tracker|Endpoint|Listener|Catalog|Registry|Pool|Dispatcher|Broker|Producer|"
    r"Consumer|Agent|Daemon|Loader|Compiler|Interpreter|Analyzer|Parser|Repository|Gateway|"
    r"Proxy|Supervisor|Monitor|Allocator|Launcher|Application|Processor|Resolver|Executor\w*|"
    r"Pipeline|Job|Task|Stage|Plan|Planner|Writer|Reader|Transport|Channel|Connection)$"
)
#: Types that hold values, settings or messages rather than doing work.
_PASSIVE = re.compile(
    r"(?:Type|Types|Errors|Error|Conf|Config|Keys|Key|Implicits|Pattern|Patterns|Constants|"
    r"Options|Literal|Value|Values|Annotation|Since|Evolving|Stable|Unstable)$"
)
_PASSIVE_KINDS = frozenset({"enum", "@interface", "record"})
_IDENTIFIER = re.compile(r"\b[A-Z][A-Za-z0-9_]{3,}\b")


@dataclass
class Corpus:
    """The source files the code model reads, and their definitions."""

    commit_sha: str
    texts: dict[str, str] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)
    symbols: SymbolIndex = field(default_factory=SymbolIndex)
    truncated: bool = False

    @property
    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(commit_sha=self.commit_sha, files=self.texts, paths=self.paths)

    @classmethod
    def read(
        cls,
        root: Path,
        commit_sha: str,
        paths: Iterable[str],
        *,
        max_files: int = MAX_CORPUS_FILES,
        max_bytes: int = MAX_CORPUS_BYTES,
    ) -> Corpus:
        """Read the non-test, non-peripheral source files under *root*, within budget."""
        all_paths = list(paths)
        corpus = cls(commit_sha=commit_sha, paths=set(all_paths))
        total = 0
        for path in all_paths:
            if language_of(path) is None or is_test_path(path) or is_peripheral(path):
                continue
            if len(corpus.texts) >= max_files:
                corpus.truncated = True
                break
            file = root / path
            try:
                if file.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if total + len(text) > max_bytes:
                corpus.truncated = True
                break
            corpus.texts[path] = text
            total += len(text)
        corpus.symbols = SymbolIndex.build(
            corpus.texts.items(), max_files=max_files, max_bytes=max_bytes
        )
        return corpus

    @classmethod
    def from_texts(cls, commit_sha: str, texts: dict[str, str]) -> Corpus:
        corpus = cls(commit_sha=commit_sha, texts=dict(texts), paths=set(texts))
        corpus.symbols = SymbolIndex.build(texts.items())
        return corpus


# -- key abstractions ---------------------------------------------------------------


def key_abstractions(
    corpus: Corpus, graph: ArchitectureGraph, readme: str = "", *, limit: int = MAX_ABSTRACTIONS
) -> list[KeyAbstraction]:
    """The types the rest of the code is built around, each with a verified range."""
    candidates: dict[str, list] = defaultdict(list)
    for symbol in corpus.symbols.types():
        top_level = symbol.container is None and len(symbol.name) >= 3
        if top_level and symbol.name[0].isupper() and not _GENERIC.match(symbol.name):
            candidates[symbol.name].append(symbol)
    # A name defined in many places is a convention, not a component. A few
    # definitions are usually one concept with parallel APIs (JVM and Python).
    candidates = {
        name: defs for name, defs in candidates.items() if len({d.path for d in defs}) <= 6
    }
    if not candidates:
        return []

    names = set(candidates)
    used_in: Counter[str] = Counter()
    first_use: dict[str, list[str]] = defaultdict(list)
    for path, text in corpus.texts.items():
        for name in names.intersection(_IDENTIFIER.findall(text)):
            if all(d.path != path for d in candidates[name]):
                used_in[name] += 1
                if len(first_use[name]) < 3:
                    first_use[name].append(path)

    entry_files = {
        ref.path
        for node_id in graph.entry_points
        if (node := graph.node(node_id)) is not None
        for ref in node.source_references
    }
    importance = {s.id: s.learning_importance for s in graph.subsystems}

    def home(path: str) -> str | None:
        owners = [s for s in graph.subsystems if s.contains(path)]
        return max(owners, key=lambda s: s.depth(path)).id if owners else None

    scored: list[tuple[float, str]] = []
    for name, defs in candidates.items():
        definition = canonical(defs)
        uses = used_in[name]
        if uses == 0:
            continue
        score = math.log2(1 + uses)
        score += 3.0 if _ROLE.search(name) else 0.0
        score -= 4.0 if _PASSIVE.search(name) or definition.kind in _PASSIVE_KINDS else 0.0
        score += 1.5 if definition.path in entry_files else 0.0
        score += 0.15 * importance.get(home(definition.path) or "", 0.0)
        score += 1.0 if re.search(rf"\b{re.escape(name)}\b", readme) else 0.0
        score += 0.3 * min(4.0, (definition.end_line - definition.start_line) / 200)
        scored.append((score, name))
    scored.sort(reverse=True)

    result: list[KeyAbstraction] = []
    for _, name in scored:
        if len(result) >= limit:
            break
        definition = canonical(candidates[name])
        ref = verify_reference(
            corpus.snapshot,
            definition.path,
            symbol=name,
            line=definition.start_line,
            index=corpus.symbols,
        )
        if not ref.verified:
            continue
        usages = [u for u in (_usage(corpus, path, name) for path in first_use[name]) if u]
        result.append(
            KeyAbstraction(
                id=f"sym:{slug(definition.path)}:{name}",
                name=name,
                kind=definition.kind,
                definition=ref,
                referenced_by=used_in[name],
                subsystem=home(definition.path),
                usages=usages,
            )
        )
    return result


def canonical(defs: list) -> object:
    """The definition a name stands for: a class or trait over its companion
    object, then the largest body."""
    return max(
        defs,
        key=lambda d: (
            d.kind in {"class", "trait", "interface", "struct"},
            d.end_line - d.start_line,
        ),
    )


def _usage(corpus: Corpus, path: str, name: str) -> CodeReference | None:
    text = corpus.texts.get(path)
    if not text:
        return None
    match = re.search(rf"\b{re.escape(name)}\b", text)
    if match is None:
        return None
    line = text.count("\n", 0, match.start()) + 1
    ref = verify_reference(corpus.snapshot, path, line=line, expect=name)
    return ref if ref.verified else None


# -- relations -------------------------------------------------------------------


_CODE_NOISE = {
    "python": re.compile(
        r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|#[^\n]*'
    ),
    "js": re.compile(
        r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/'
    ),
}
_C_NOISE = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')


def code_only(text: str, path: str) -> str:
    """*text* with strings and comments blanked, newlines kept, so line numbers hold."""
    language = language_of(path) or ""
    pattern = _CODE_NOISE.get(language, _C_NOISE)
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


_NOT_NAMES = frozenset(
    {"val", "var", "def", "let", "const", "return", "new", "case", "final", "self", "this"}
)


def declared_as(text: str, type_name: str) -> set[str]:
    """Names *text* declares with type *type_name*.

    ``x: T``, ``T x``, ``x = new T(`` or ``x = T(`` -- the forms Scala, Kotlin,
    TypeScript, Python, Java and Go use."""
    name = re.escape(type_name)
    found = set(re.findall(rf"\b([a-z_]\w*)\s*:\s*{name}\b", text))
    found |= set(re.findall(rf"\b{name}\b(?:<[^>\n]*>)?\s+([a-z_]\w*)\b", text))
    found |= set(re.findall(rf"\b([a-z_]\w*)\s*=\s*(?:new\s+)?{name}\s*[\(\{{]", text))
    return found - _NOT_NAMES


def relations(corpus: Corpus, abstractions: list[KeyAbstraction]) -> list[CodeRelation]:
    """Where one key abstraction's own code names another, at a verified line.

    The kind is read from that line: ``new B`` / ``B(`` creates, ``extends B``
    extends, ``B.f(`` -- or ``b.f(`` where A declares ``b`` with type B --
    calls; anything else is a plain reference. Each pair is recorded once, with the
    strongest kind found.
    """
    found: list[CodeRelation] = []
    rank = {
        CodeRelationType.CALLS: 0,
        CodeRelationType.CREATES: 1,
        CodeRelationType.EXTENDS: 2,
        CodeRelationType.REFERENCES: 3,
    }
    for a in abstractions:
        text = corpus.texts.get(a.definition.path)
        if text is None or a.definition.start_line is None or a.definition.end_line is None:
            continue
        lines = code_only(text, a.definition.path).splitlines()[
            a.definition.start_line - 1 : a.definition.end_line
        ]
        range_text = "\n".join(lines)
        for b in abstractions:
            if b.id == a.id:
                continue
            best: tuple[CodeRelationType, int, str] | None = None
            name = re.escape(b.name)
            # A field or variable *declared* with B's type: only then is a call
            # through it a call on B. A field merely named like B is not enough
            # (``resolver`` may well hold some other resolver).
            members = sorted(declared_as(range_text, b.name))
            callee = "|".join([*(re.escape(m) for m in members), name])
            patterns = [
                (CodeRelationType.CALLS, re.compile(rf"\b(?:{callee})\.[a-z_]\w*\s*\(")),
                (CodeRelationType.CREATES, re.compile(rf"\bnew\s+{name}\b|\b{name}\s*[\(\{{]")),
                (
                    CodeRelationType.EXTENDS,
                    re.compile(rf"\b(?:extends|with|implements)\s+{name}\b"),
                ),
                (CodeRelationType.REFERENCES, re.compile(rf"\b{name}\b")),
            ]
            for offset, content in enumerate(lines):
                stripped = content.strip()
                if not stripped or stripped.startswith(
                    ("import ", "from ", "package ", "...", ">>>")
                ):
                    continue
                for kind, pattern in patterns:
                    match = pattern.search(content)
                    if match and (best is None or rank[kind] < rank[best[0]]):
                        best = (kind, a.definition.start_line + offset, match.group().strip())
                        break
                if best is not None and best[0] is CodeRelationType.CALLS:
                    break
            if best is None:
                continue
            kind, line, anchor = best
            ref = verify_reference(corpus.snapshot, a.definition.path, line=line, expect=anchor)
            if ref.verified:
                found.append(CodeRelation(source=a.id, target=b.id, type=kind, evidence=ref))
    return found


# -- the runtime model -------------------------------------------------------------


_ORDERING = (CodeRelationType.CALLS, CodeRelationType.CREATES)


def deterministic_runtime(
    abstractions: list[KeyAbstraction],
    found: list[CodeRelation],
    graph: ArchitectureGraph,
    *,
    limit: int = MAX_COMPONENTS,
) -> RuntimeModel:
    """One component per most-connected abstraction; links only along relations.

    No names or descriptions are invented: a component is called what its type
    is called, and has no responsibilities until something can cite them.
    """
    degree: Counter[str] = Counter()
    for relation in found:
        weight = 2 if relation.type in _ORDERING else 1
        degree[relation.source] += weight
        degree[relation.target] += weight
    position = {a.id: i for i, a in enumerate(abstractions)}
    chosen: list[KeyAbstraction] = []
    per_subsystem: Counter[str | None] = Counter()
    for abstraction in sorted(abstractions, key=lambda a: (-degree[a.id], position[a.id])):
        if len(chosen) >= limit:
            break
        if per_subsystem[abstraction.subsystem] >= 3:
            continue
        if degree[abstraction.id] == 0 and len(chosen) >= 3:
            continue
        chosen.append(abstraction)
        per_subsystem[abstraction.subsystem] += 1
    components = [
        RuntimeComponent(
            id=f"rt:{slug(a.name)}",
            name=a.name,
            members=[a.id],
            source_references=[a.definition],
            subsystem_ids=[a.subsystem] if a.subsystem else [],
            node_ids=_nodes_in(graph, [a.definition.path]),
        )
        for a in chosen
    ]
    model = RuntimeModel(components=components)
    model.links = links_between(model.components, found)
    return model


def _nodes_in(graph: ArchitectureGraph, paths: list[str]) -> list[str]:
    wanted = set(paths)
    return [n.id for n in graph.nodes if any(r.path in wanted for r in n.source_references)][:6]


def links_between(
    components: list[RuntimeComponent], found: list[CodeRelation]
) -> list[RuntimeLink]:
    """A link wherever some member of one component names a member of another."""
    owner = {member: c.id for c in components for member in c.members}
    grouped: dict[tuple[str, str], list[CodeRelation]] = defaultdict(list)
    for relation in found:
        source, target = owner.get(relation.source), owner.get(relation.target)
        if source and target and source != target:
            grouped[(source, target)].append(relation)
    order = list(CodeRelationType)
    links: list[RuntimeLink] = []
    for (source, target), group in grouped.items():
        group.sort(key=lambda r: order.index(r.type))
        links.append(
            RuntimeLink(
                source=source,
                target=target,
                type=group[0].type,
                evidence=[r.evidence for r in group[:3]],
            )
        )
    return links


# -- flows ---------------------------------------------------------------------------


def chains(model: RuntimeModel, *, starts: Iterable[str] = ()) -> list[list[RuntimeLink]]:
    """Paths of calls and constructions, from where control enters.

    Greedy rather than longest: each hop takes the strongest hand-over (a call
    before a construction) to a component not yet visited, so a flow reads as
    one thing happening, not a tour of every part. Flows that cover the same
    hand-overs as an earlier one are skipped.
    """
    order = {CodeRelationType.CALLS: 0, CodeRelationType.CREATES: 1}
    outgoing: dict[str, list[RuntimeLink]] = defaultdict(list)
    incoming: Counter[str] = Counter()
    for link in model.links:
        if link.type in _ORDERING:
            outgoing[link.source].append(link)
            incoming[link.target] += 1
    for links in outgoing.values():
        links.sort(key=lambda link: (order[link.type], -len(outgoing.get(link.target, []))))
    first = list(dict.fromkeys(starts))
    rest = sorted(
        (c.id for c in model.components if c.id not in first),
        key=lambda cid: (incoming[cid], -len(outgoing.get(cid, []))),
    )
    found: list[list[RuntimeLink]] = []
    covered: set[tuple[str, str]] = set()
    for origin in [*first, *rest]:
        path: list[RuntimeLink] = []
        seen = {origin}
        node = origin
        while len(path) < MAX_FLOW_STEPS - 1:
            step = next((link for link in outgoing.get(node, []) if link.target not in seen), None)
            if step is None:
                break
            path.append(step)
            seen.add(step.target)
            node = step.target
        pairs = {(link.source, link.target) for link in path}
        if path and len(pairs - covered) * 2 > len(pairs):
            found.append(path)
            covered |= pairs
        if len(found) >= MAX_FLOWS:
            break
    return found


def deterministic_flows(model: RuntimeModel, graph: ArchitectureGraph) -> list[Flow]:
    names = {c.id: c.name for c in model.components}
    entry_components = [c.id for c in model.components if set(c.node_ids) & set(graph.entry_points)]
    flows: list[Flow] = []
    for index, path in enumerate(chains(model, starts=entry_components)):
        steps = [
            FlowStep(
                sequence=i + 1,
                component=link.source,
                action=f"{link.type.value} {names[link.target]}",
                references=link.evidence[:1],
            )
            for i, link in enumerate(path)
        ]
        steps.append(
            FlowStep(sequence=len(path) + 1, component=path[-1].target, action="does its part")
        )
        flows.append(
            Flow(
                id=f"flow:{index + 1}",
                name=f"{names[path[0].source]} → {names[path[-1].target]}",
                steps=steps,
                ordered=all(link.type in _ORDERING for link in path),
            )
        )
    return flows


# -- small or function-based repositories --------------------------------------------

#: How a detected relationship reads as a runtime hand-over.
_EDGE_AS_RELATION = {
    "calls": CodeRelationType.CALLS,
    "control_flow": CodeRelationType.CALLS,
    "data_flow": CodeRelationType.CALLS,
    "routes_to": CodeRelationType.CALLS,
    "triggers": CodeRelationType.CALLS,
    "creates": CodeRelationType.CREATES,
}

MIN_RUNTIME_COMPONENTS = 3


def runtime_from_components(
    graph: ArchitectureGraph, *, limit: int = MAX_COMPONENTS
) -> RuntimeModel:
    """The runtime model of a repository whose parts are functions, not types.

    A data pipeline's ``extract -> transform -> load`` or a web app's
    ``route -> handler -> database`` are what the analyzers already detected,
    each at a verified line, with evidenced edges between them. They become the
    runtime components; nothing is added.
    """
    nodes = sorted(
        (n for n in graph.nodes if n.category.value != "configuration" and n.source_references),
        key=lambda n: -n.learning_importance,
    )[:limit]
    chosen = {n.id for n in nodes}
    components = [
        RuntimeComponent(
            id=f"rt:{slug(n.id)}",
            name=n.label,
            description=n.description,
            source_references=n.source_references[:3],
            subsystem_ids=[n.subsystem] if n.subsystem else [],
            node_ids=[n.id],
            provenance=n.provenance,
        )
        for n in nodes
    ]
    ids = {n.id: f"rt:{slug(n.id)}" for n in nodes}
    links: list[RuntimeLink] = []
    for edge in graph.edges:
        if edge.source in chosen and edge.target in chosen and edge.evidence:
            links.append(
                RuntimeLink(
                    source=ids[edge.source],
                    target=ids[edge.target],
                    type=_EDGE_AS_RELATION.get(edge.type.value, CodeRelationType.REFERENCES),
                    label=edge.description or edge.type.value.replace("_", " "),
                    label_provenance=edge.provenance,
                    evidence=edge.evidence[:3],
                )
            )
    return RuntimeModel(components=components, links=links)


def flows_from_components(graph: ArchitectureGraph, model: RuntimeModel) -> list[Flow]:
    """The detected primary flows, kept only along links that carry evidence."""
    ids = {n.id: f"rt:{slug(n.id)}" for n in graph.nodes}
    known = {c.id for c in model.components}
    names = {c.id: c.name for c in model.components}
    flows: list[Flow] = []
    for index, detected in enumerate(graph.primary_flows):
        path = [ids[n] for n in detected.node_ids if ids.get(n) in known]
        steps: list[FlowStep] = []
        ordered = True
        for position, component in enumerate(path):
            following = path[position + 1] if position + 1 < len(path) else None
            link = next(
                (x for x in model.links if x.source == component and x.target == following), None
            )
            if following is not None and link is None:
                steps.append(
                    FlowStep(sequence=len(steps) + 1, component=component, action="hands over")
                )
                break
            if link is not None and link.type not in _ORDERING:
                ordered = False
            steps.append(
                FlowStep(
                    sequence=len(steps) + 1,
                    component=component,
                    action=f"{link.label or link.type.value} → {names[link.target]}"
                    if link
                    else "does its part",
                    references=link.evidence[:1] if link else [],
                )
            )
        if len(steps) >= 2:
            flows.append(
                Flow(
                    id=f"flow:{index + 1}",
                    name=detected.name,
                    description=detected.description,
                    steps=steps,
                    ordered=ordered and all(s.references for s in steps[:-1]),
                    provenance=detected.provenance,
                )
            )
    return flows


# -- reading path ------------------------------------------------------------------


def reading_path(graph: ArchitectureGraph, *, limit: int = MAX_READING) -> list[ReadingItem]:
    """Where execution starts, then the runtime components in the order a flow uses them."""
    items: list[ReadingItem] = []
    seen: set[str] = set()

    def add(item: ReadingItem) -> None:
        key = f"{item.reference.path}:{item.reference.start_line}"
        if key in seen or len(items) >= limit or not item.reference.verified:
            return
        seen.add(key)
        items.append(item)

    for node_id in graph.entry_points[:2]:
        node = graph.node(node_id)
        if node is None or not node.source_references:
            continue
        ref = node.source_references[0]
        if ref.verified:
            add(ReadingItem(title=node.label, why="Execution starts here.", reference=ref))

    components = {c.id: c for c in graph.runtime.components}
    ordered: list[str] = []
    for flow in graph.flows:
        for step in flow.steps:
            if step.component not in ordered:
                ordered.append(step.component)
    ordered += [c for c in components if c not in ordered]
    for component_id in ordered:
        component = components.get(component_id)
        if component is None:
            continue
        for ref in component.source_references[:1]:
            why = (
                component.description
                or f"Defines {component.name}, a part the rest of the code is built around."
            )
            add(ReadingItem(title=component.name, why=why, reference=ref, component=component_id))
    return items


# -- putting it together ----------------------------------------------------------------


def build_code_model(
    graph: ArchitectureGraph, corpus: Corpus, *, readme: str = ""
) -> ArchitectureGraph:
    """Add abstractions, relations, a deterministic runtime model, flows and a reading path."""
    graph.abstractions = key_abstractions(corpus, graph, readme)
    graph.relations = relations(corpus, graph.abstractions)
    graph.runtime = deterministic_runtime(graph.abstractions, graph.relations, graph)
    graph.runtime.provenance = Provenance.STATIC
    graph.flows = deterministic_flows(graph.runtime, graph)
    if len(graph.runtime.components) < MIN_RUNTIME_COMPONENTS:
        # Too few types carry the system: it is built from functions. Its
        # detected components are its runtime parts.
        model = runtime_from_components(graph)
        if len(model.components) >= len(graph.runtime.components):
            graph.runtime = model
            graph.flows = flows_from_components(graph, model)
    graph.reading_path = reading_path(graph)
    if corpus.truncated:
        graph.gaps.append(
            "The repository is larger than RepoLab reads for its runtime model; "
            "types defined in the unread files are not in it."
        )
    return graph
