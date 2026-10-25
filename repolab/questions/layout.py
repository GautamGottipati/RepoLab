"""Checking the structured parts of an answer before anyone sees them.

A diagram persuades more than a sentence does, so it is held to the stricter
standard: an arrow is drawn only when something real shows it -- a file in the
evidence or the checkout, or a relationship the architecture analysis already
cited. An arrow the model could not support is not drawn; it is listed as not
established. An arrow the model itself calls an inference is drawn dashed and
labelled. A diagram made mostly of inferences is not drawn at all.

The same rule reaches every source chip, code-map entry and comparison cell: a
path that does not exist is removed and counted, never shown as a link. And
without a recorded run in the evidence, a number with a unit is a measurement
nobody made, so it is flagged.

Deterministic throughout; no model is asked anything here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from repolab.architecture.models import ArchitectureEdge
from repolab.architecture.synthesis import _MEASUREMENT
from repolab.evidence.models import EvidenceBundle
from repolab.questions.context import ArchitectureSlice
from repolab.questions.models import (
    MAX_FOLLOW_UPS,
    MAX_STEPS,
    MAX_VISUAL_NODES,
    AnswerClaimKind,
    AnswerComparison,
    AnswerVisual,
    GroundedAnswer,
    SectionBasis,
    UnsupportedAnswerClaim,
    VisualNode,
)

log = structlog.get_logger(__name__)

NOT_ESTABLISHED = "not established"

_INFERRED = re.compile(r"^\s*(inferred|inference|reasonable inference)\b", re.I)
_LINE_SUFFIX = re.compile(r":(\d+)(?:-\d+)?$")

#: A repository path inside a longer citation: "backend/app.js (lines 54-56)".
_PATH_TOKEN = re.compile(r"[\w@.-]+(?:/[\w@.-]+)+/?|[\w-]+\.\w{1,5}")


@dataclass
class Resolver:
    """What a citation may point at: files and pages that really exist."""

    evidence: EvidenceBundle
    root: Path | None = None
    slice: ArchitectureSlice = field(default_factory=ArchitectureSlice)

    def __post_init__(self) -> None:
        self.paths = {ref.path for ref in self.evidence.code_references}
        self.paths |= {ref.path for ref in self.slice.references()}
        self.urls = {doc.url for doc in self.evidence.documentation}

    def path(self, cited: str, *, directories: bool = False) -> str | None:
        """The repository path *cited* names (``path`` or ``path:line``), if it exists."""
        text = cited.strip().strip("`'\"").removeprefix("./")
        text = _LINE_SUFFIX.sub("", text)
        if not text or text.startswith(("http://", "https://")):
            return None
        if text in self.paths or _exists(self.root, text, directories=directories):
            return text
        # Models sometimes wrap a real path in words; the path still has to exist.
        for token in _PATH_TOKEN.findall(cited):
            token = token.rstrip(".,;:)").removeprefix("./")
            if token != text and (
                token in self.paths or _exists(self.root, token, directories=directories)
            ):
                return token
        return None

    def source(self, cited: str) -> bool:
        return self.normalise(cited) is not None

    def normalise(self, cited: str) -> str | None:
        """The citation as a retrieved URL or ``path[:line]``, if it points at something real."""
        cited = cited.strip()
        if cited in self.urls:
            return cited
        path = self.path(cited)
        if path is None:
            return None
        line = re.search(r"(?::|\blines?\s+)(\d+)", cited[cited.find(path) + len(path) :])
        return f"{path}:{line.group(1)}" if line else path

    @property
    def measured(self) -> bool:
        return bool(self.evidence.measurements)


@dataclass
class LayoutCheck:
    unsupported: list[UnsupportedAnswerClaim] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int = 1) -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count


def verify_layout(answer: GroundedAnswer, resolver: Resolver) -> LayoutCheck:
    """Check, and repair in place, everything an answer lays out for a reader."""
    check = LayoutCheck()
    answer.visual = _visual(answer.visual, resolver, check)
    _steps(answer, resolver, check)
    _sections(answer, resolver, check)
    answer.comparison = _comparison(answer.comparison, resolver, check)
    _locations(answer, resolver, check)
    answer.follow_ups = _follow_ups(answer.follow_ups, check)
    _measured_claims(answer, resolver, check)
    if not resolver.measured:
        _invented_numbers(answer, resolver, check)
    return check


# -- the diagram ---------------------------------------------------------------


def _visual(
    visual: AnswerVisual | None, resolver: Resolver, check: LayoutCheck
) -> AnswerVisual | None:
    if visual is None:
        return None
    components = resolver.slice.node_ids()
    nodes = {}
    for node in visual.nodes:
        if not node.id.strip() or not node.label.strip() or node.id in nodes:
            check.drop("diagram node without an id or label")
            continue
        if node.component_id and node.component_id not in components:
            node.component_id = None
        if node.source:
            node.source = resolver.path(node.source)
        node.label = node.label.strip()[:60]
        nodes[node.id] = node

    edges = []
    static = inferred = 0
    for edge in visual.edges:
        if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
            check.drop("diagram arrow to an unknown box")
            continue
        named = f"{nodes[edge.source].label} → {nodes[edge.target].label}"
        support = edge.support.strip()
        cited = resolver.slice.edge(support)
        if cited is not None and _matches(cited, nodes[edge.source], nodes[edge.target]):
            edge.provenance = cited.provenance.value
        elif cited is None and resolver.normalise(support) is not None:
            edge.support = resolver.normalise(support) or support
            edge.provenance = "static"
        elif _INFERRED.match(support):
            edge.provenance = "inferred"
        else:
            log.info("questions.relationship_not_established", support=support[:200])
            check.not_established.append(named)
            check.drop("relationship without evidence")
            continue
        if edge.provenance == "inferred":
            inferred += 1
        else:
            static += 1
        edge.label = edge.label.strip()[:60]
        edges.append(edge)

    connected = {edge.source for edge in edges} | {edge.target for edge in edges}
    kept = [node for node in nodes.values() if node.id in connected]
    if len(kept) > MAX_VISUAL_NODES:
        check.drop("diagram too large", len(kept) - MAX_VISUAL_NODES)
        kept = kept[:MAX_VISUAL_NODES]
        allowed = {node.id for node in kept}
        edges = [edge for edge in edges if edge.source in allowed and edge.target in allowed]
    if len(kept) < 2 or not edges:
        if visual.nodes:
            check.drop("diagram without an evidenced relationship")
        return None
    if static == 0 or static < inferred:
        # Mostly guesswork is not a diagram of the repository.
        check.drop("diagram without enough evidence")
        return None
    return AnswerVisual(nodes=kept, edges=edges)


def _matches(cited: ArchitectureEdge, source: VisualNode, target: VisualNode) -> bool:
    """A cited relationship must be between the components the arrow joins."""
    ends = {source.component_id, target.component_id}
    if None in ends:
        return True
    return ends == {cited.source, cited.target}


# -- the text around it ----------------------------------------------------------


def _clean_sources(sources: list[str], resolver: Resolver, check: LayoutCheck) -> list[str]:
    kept: list[str] = []
    for cited in sources:
        if _INFERRED.match(cited):
            continue  # an honest "inferred" is not a citation, and not a missing one
        normalised = resolver.normalise(cited)
        if normalised is not None:
            if normalised not in kept:
                kept.append(normalised)
        else:
            log.info("questions.citation_rejected", cited=cited[:200])
            check.drop("citation of a file or page that does not exist")
    return kept[:4]


def _steps(answer: GroundedAnswer, resolver: Resolver, check: LayoutCheck) -> None:
    if len(answer.steps) > MAX_STEPS:
        check.drop("steps beyond the limit", len(answer.steps) - MAX_STEPS)
    steps = []
    for step in answer.steps[:MAX_STEPS]:
        if not step.title.strip() and not step.description.strip():
            continue
        step.sources = _clean_sources(step.sources, resolver, check)
        steps.append(step)
    answer.steps = steps


def _sections(answer: GroundedAnswer, resolver: Resolver, check: LayoutCheck) -> None:
    sections = []
    for section in answer.sections[:8]:
        if not section.body.strip():
            continue
        section.sources = _clean_sources(section.sources, resolver, check)
        if section.basis is SectionBasis.EVIDENCED and not section.sources:
            # "Evidenced" with nothing to open is an inference by another name.
            section.basis = SectionBasis.INFERRED
            check.drop("section marked evidenced without a source, relabelled inferred")
        sections.append(section)
    answer.sections = sections


def _comparison(
    comparison: AnswerComparison | None, resolver: Resolver, check: LayoutCheck
) -> AnswerComparison | None:
    if comparison is None or len(comparison.subjects) < 2:
        return None
    subjects = comparison.subjects[:3]
    rows = []
    for row in comparison.rows[:8]:
        values = (row.values + [""] * len(subjects))[: len(subjects)]
        if resolver.source(row.support):
            row.provenance = "static"
        elif _INFERRED.match(row.support):
            row.provenance = "inferred"
        else:
            values = [NOT_ESTABLISHED] * len(subjects)
            row.provenance = "unknown"
            check.drop("comparison row without evidence")
        row.values = values
        rows.append(row)
    return AnswerComparison(subjects=subjects, rows=rows) if rows else None


def _locations(answer: GroundedAnswer, resolver: Resolver, check: LayoutCheck) -> None:
    kept = []
    for location in answer.locations[:8]:
        path = resolver.path(location.path, directories=True)
        if path is None:
            check.drop("code-map entry for a path that does not exist")
            continue
        location.path = path
        if location.line is not None and not _has_line(resolver.root, path, location.line):
            location.line = None
        kept.append(location)
    answer.locations = kept


def _follow_ups(questions: list[str], check: LayoutCheck) -> list[str]:
    kept: list[str] = []
    for question in questions:
        text = " ".join(question.split())
        if not 8 <= len(text) <= 160 or _MEASUREMENT.search(text):
            check.drop("follow-up question out of bounds")
            continue
        if text.lower() not in {q.lower() for q in kept}:
            kept.append(text)
    return kept[:MAX_FOLLOW_UPS]


# -- measurements ----------------------------------------------------------------


def _measured_claims(answer: GroundedAnswer, resolver: Resolver, check: LayoutCheck) -> None:
    names = [m.name.lower() for m in resolver.evidence.measurements]
    for claim in answer.claims:
        if claim.kind is not AnswerClaimKind.MEASURED:
            continue
        support = claim.support.strip().lower()
        if not names:
            problem = "states a measured value, but no run of this repository was recorded"
        elif not support or not any(support in name or name in support for name in names):
            problem = f"cites {claim.support!r}, which is not a recorded measurement"
        else:
            continue
        check.unsupported.append(
            UnsupportedAnswerClaim(statement=claim.statement, kind=claim.kind, problem=problem)
        )


def _invented_numbers(answer: GroundedAnswer, resolver: Resolver, check: LayoutCheck) -> None:
    """Flag unit-bearing numbers the evidence does not itself contain.

    A timeout of "30 seconds" copied from a configuration file is a fact about
    the repository; the same phrase with nothing behind it is a measurement
    nobody made.
    """
    shown = _squash(
        " ".join(
            [ref.excerpt or "" for ref in resolver.evidence.code_references]
            + [ref.excerpt or "" for ref in resolver.slice.references()]
            + [doc.excerpt or "" for doc in resolver.evidence.documentation]
        )
    )
    texts: list[str] = [answer.summary, answer.answer]
    texts += [f"{step.title}. {step.description}" for step in answer.steps]
    texts += [section.body for section in answer.sections]
    for text in texts:
        match = next(
            (m for m in _MEASUREMENT.finditer(text or "") if _squash(m.group(0)) not in shown),
            None,
        )
        if match is None:
            continue
        check.unsupported.append(
            UnsupportedAnswerClaim(
                statement=text[:300],
                kind=AnswerClaimKind.MEASURED,
                problem=(
                    f"states {match.group(0)!r} as if measured, but nothing was run for this answer"
                ),
            )
        )


# ---------------------------------------------------------------------------


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _exists(root: Path | None, relative: str, *, directories: bool = False) -> bool:
    if root is None or not relative:
        return False
    try:
        base = root.resolve()
        target = (root / relative.rstrip("/")).resolve()
        if not target.is_relative_to(base):
            return False
        return target.is_file() or (directories and target.is_dir())
    except (OSError, ValueError):
        return False


def _has_line(root: Path | None, relative: str, line: int) -> bool:
    if line < 1:
        return False
    if root is None:
        return True
    try:
        with (root / relative).open("rb") as handle:
            return sum(1 for _ in handle) + 1 >= line
    except OSError:
        return False
