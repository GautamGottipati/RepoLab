"""Answering a question that is not one to test.

"What is Cassandra?" deserves an answer, not an experiment -- but not an answer
from whatever a model happens to remember either. The Answer Agent first
understands the repository the question was asked about:

    the manifest            what RepoLab already established about it
    its own overview        the README, in the project's words
    the question's terms    searched for in the code, and the best hits read
    official documentation  on the project's own domain, chosen here

and only then asks Super to answer **from that evidence**. Every claim is
labelled by what supports it -- a repository file, an official page, or an
inference -- and checked afterwards: a file the evidence does not contain, or a
URL that was never retrieved, is flagged on the answer rather than passed off.

Without a model the agent still returns the evidence it gathered, and says that
no narrative could be produced. It never writes one itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import BaseModel

from repolab.agents.models import AgentLimits, AgentRun
from repolab.agents.repository_tools import build_repository_tools
from repolab.agents.runtime import AgentRuntime
from repolab.agents.tools import ToolRegistry
from repolab.architecture.models import ArchitectureGraph
from repolab.architecture.runtime import RuntimeOverlay
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.evidence.models import CodeReference, EvidenceBundle
from repolab.experiments.investigation import code_references_from, documentation_from
from repolab.failures import explain
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.interfaces import ResearchProvider
from repolab.llm import LLMError, Purpose
from repolab.manifest.models import RepoManifest
from repolab.questions.context import (
    ArchitectureSlice,
    architecture_slice,
    follow_up_questions,
    runtime_measurements,
)
from repolab.questions.investigation import (
    QuestionInvestigationPlanner,
    official_domain,
    overview_file,
)
from repolab.questions.layout import Resolver, verify_layout
from repolab.questions.models import (
    MAX_FOLLOW_UPS,
    AnswerClaimKind,
    AnswerResult,
    ArchitectureFocus,
    ExplanationShape,
    GroundedAnswer,
    QuestionKind,
    UnsupportedAnswerClaim,
    VerifiedAnswer,
)

log = structlog.get_logger(__name__)

STEP_LABEL = "answer"

AGENT_NAME = "answer_agent"

#: Output budget. Super reasons before it answers, and the reasoning counts --
#: and a laid-out answer (steps, a diagram, sections) is longer than prose.
ANSWER_MAX_TOKENS = 8192

ANSWER_TIMEOUT_SECONDS = 150.0

#: How much of a read file reaches the prompt, per file.
EXCERPT_LINES = 60

#: Code references an answer carries: what the investigation read, then what the
#: architecture analysis had already cited for the components in question.
MAX_REFERENCES = 16

SYSTEM_PROMPT = (
    "You answer a developer's question about a software repository, using only "
    "the evidence you are given.\n"
    "\n"
    "The evidence is what RepoLab found by reading the repository and its official "
    "documentation. It is the only source you may cite. If it does not answer the "
    "question, say what it does show and what is missing -- do not fill the gap "
    "from memory and present it as fact.\n"
    "\n"
    "For a conceptual question, explain the concept plainly, then connect it to "
    "where it appears in this repository. For a question about the code, answer "
    "from the files.\n"
    "\n"
    "Label every claim:\n"
    "  repository - shown by a repository file. In 'support', copy one file path "
    "from the evidence exactly.\n"
    "  documented - stated by one of the documentation excerpts. Put its URL in "
    "'support'.\n"
    "  inferred   - a conclusion you draw. Say which claims it rests on.\n"
    "\n"
    "  measured   - a value from a recorded run. Only when measured values are "
    "listed; put the measurement's name in 'support'.\n"
    "\n"
    "Never cite a file or URL that is not in the evidence. Never state a number "
    "the evidence does not contain. Official documentation explains general "
    "behaviour; it never overrides what this repository's files show.\n"
    "\n"
    "Write for someone new to this repository. 'summary' is the direct answer "
    "in one to three short, plain sentences a beginner understands in seconds. "
    "When you use a project-specific term, say what it is the first time "
    '("a Mongoose model called Registration", not "the Registration model"). '
    "Then lay the answer out in the requested shape:\n"
    "  visual    - three to eight boxes and the arrows between them. Draw an "
    "arrow only when a file in the evidence or a listed relationship (edge:...) "
    "shows it; put that in 'support'. If you believe a relationship but nothing "
    "shows it, set support to 'inferred'. If you cannot establish a relationship, "
    "leave it out and say so in a caveat -- never draw a complete-looking flow "
    "the evidence does not support. Use component ids from the architecture map "
    "in 'component_id' when a box is one of them.\n"
    "  steps     - numbered, a few words of title and one or two sentences each, "
    "with the files that show each step in 'sources'.\n"
    "  sections  - with 'basis' evidenced (a cited file shows it), inferred (your "
    "reading of the evidence) or unknown (the evidence does not say). Never "
    "present design intent as evidenced unless a file or page states it.\n"
    "  locations - role and path for each place in the code, in order.\n"
    "  follow_ups - two to four next questions about this repository a learner "
    "would ask after this answer."
)

#: What each shape of answer should fill in, beyond the summary and claims.
SHAPE_GUIDANCE: dict[ExplanationShape, str] = {
    ExplanationShape.FLOW: (
        "flow: a visual of what talks to what -- including the intermediate layers "
        "the code shows between the parts asked about (the module, class or model "
        "each one calls), not only the endpoints -- then steps in the order data moves."
    ),
    ExplanationShape.EXECUTION: (
        "execution: steps in the order things happen, from the trigger to the "
        "result; a visual of the same sequence if the evidence connects it."
    ),
    ExplanationShape.DEFINITION: (
        "definition: sections with keys what (what it is), role (its role in this "
        "repository), connects (how it connects to other parts), where (where it "
        "is implemented); locations for the main files."
    ),
    ExplanationShape.WHY: (
        "why: sections with keys problem, choice, helps, tradeoffs -- each with an honest basis."
    ),
    ExplanationShape.COMPARISON: (
        "comparison: subjects and rows (purpose, used by, input, output, "
        "implemented in...), each row with its support; only attributes the "
        "evidence shows, or support 'inferred'."
    ),
    ExplanationShape.LOCATION: (
        "location: locations in order (entry, validation, logic, persistence...), "
        "each with the path copied exactly."
    ),
    ExplanationShape.ARCHITECTURE: (
        "architecture: a visual of the main parts and how they connect, then "
        "steps or sections explaining each part."
    ),
    ExplanationShape.DEBUGGING: (
        "debugging: sections with keys symptom, cause, check -- what the evidence "
        "shows, what it suggests, and how the reader could confirm it."
    ),
    ExplanationShape.GENERAL: "general: steps or sections, whichever explains it best.",
}

PROMPT_TEMPLATE = """\
Question ({kind}):
{question}

Repository: {repo_url} at commit {commit_sha}

What RepoLab established about this repository:
{manifest}

The repository's own overview ({overview_path}):
{overview}

Repository files and lines relevant to the question:
{code}

Official documentation retrieved:
{documentation}

How RepoLab's architecture analysis maps the parts involved (cite a
relationship by its edge id):
{architecture}

Measured values from recorded runs:
{measurements}
{note}
Answer shape: {shape}

Answer the question."""


class AnswerRouter(Protocol):
    async def super(self, **kwargs: Any) -> Any: ...


class AnswerAgent:
    """Understands the repository, then answers from what it found."""

    name = AGENT_NAME

    def __init__(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        repository_root: Path,
        manifest: RepoManifest | None = None,
        router: AnswerRouter | None = None,
        research: ResearchProvider | None = None,
        emitter: EventEmitter | None = None,
        limits: AgentLimits | None = None,
        focus: ArchitectureFocus | None = None,
        architecture: ArchitectureGraph | None = None,
        overlay: RuntimeOverlay | None = None,
    ) -> None:
        self.repo_url = repo_url
        self._focus = focus
        self._architecture = architecture
        self._overlay = overlay
        self.commit_sha = commit_sha
        self.repository_root = Path(repository_root)
        self._manifest = manifest
        self._router = router
        self._research = research
        self._emitter = emitter if emitter is not None else NullEmitter()
        # Focus files, the README, the question's terms, the files they hit and the
        # modules those import, then the docs: a bounded, read-only walk.
        self._limits = limits if limits is not None else AgentLimits(max_iterations=16)

    async def answer(
        self,
        question: str,
        *,
        kind: QuestionKind,
        downgraded: str | None = None,
        shape: ExplanationShape | None = None,
    ) -> AnswerResult:
        """Investigate, then answer. Never raises for a model failure.

        *shape* is the layout the question's phrasing settled, if it did; when it
        did not, the model picks one in the same call.
        """
        self._emitter.emit(
            EventType.AGENT_STARTED,
            "reading the repository to answer the question",
            step=STEP_LABEL,
            data={"question": question[:200], "kind": kind.value},
        )

        selected = architecture_slice(self._architecture, question, focus=self._focus)
        run, evidence, overview = await self._investigate(question, selected)
        suggestions = follow_up_questions(self._architecture, selected, asked=question)
        result = AnswerResult(
            question=question,
            kind=kind,
            shape=shape or ExplanationShape.GENERAL,
            evidence=evidence,
            findings=[step.status for step in run.steps if step.status][:12],
            downgraded=downgraded,
            follow_ups=suggestions,
        )

        if self._router is None:
            result.error = (
                "No model is configured, so RepoLab cannot write an answer. "
                "The evidence it gathered is below."
            )
            return result

        self._emitter.emit(EventType.AGENT_STATUS, "answering from the evidence", step=STEP_LABEL)
        try:
            completion = await self._router.super(
                purpose=Purpose.QUESTION_ANSWER,
                schema=GroundedAnswer,
                system=SYSTEM_PROMPT,
                prompt=self._prompt(
                    question, kind, evidence, overview, downgraded, selected, shape
                ),
                max_tokens=ANSWER_MAX_TOKENS,
                timeout_seconds=ANSWER_TIMEOUT_SECONDS,
            )
        except LLMError as exc:
            failure = explain(exc)
            log.warning("questions.answer_failed", error=failure.technical)
            result.error = f"{failure.headline} {failure.why}"[:500]
            return result

        result.answer = verify_answer(
            completion.value,
            evidence=evidence,
            repository_root=self.repository_root,
            architecture=selected,
        )
        # The phrasing's decision stands; the model's choice fills in only when
        # the rules left it open.
        if shape is not None:
            result.answer.answer.shape = shape
        result.shape = result.answer.answer.shape
        result.follow_ups = _merge(result.answer.answer.follow_ups, suggestions, question)
        if not result.answer.grounded:
            self._emitter.emit(
                EventType.AGENT_STATUS,
                f"{len(result.answer.unsupported)} claim(s) could not be traced to evidence "
                "and are flagged",
                step=STEP_LABEL,
            )
        return result

    # -- investigation ------------------------------------------------------

    async def _investigate(
        self, question: str, selected: ArchitectureSlice
    ) -> tuple[AgentRun, EvidenceBundle, tuple[str | None, str]]:
        tree = build_file_tree(self.repository_root)
        context = DetectionContext(root=self.repository_root, tree=tree, commit_sha=self.commit_sha)
        tools, tool_list = build_repository_tools(context, ReadBudget(), research=self._research)
        del tools

        overview = overview_file([entry.path for entry in tree.files])
        domain = official_domain(self._manifest, self.repo_url)
        planner = QuestionInvestigationPlanner(
            question,
            overview=overview,
            focus_paths=[ref.path for ref in self._focus.source_references] if self._focus else [],
            paths={entry.path for entry in tree.files},
            domain=domain,
            research_available=self._research is not None,
            subject=_subject(self.repo_url),
        )
        runtime = AgentRuntime(
            agent=self.name,
            objective=f"Gather the evidence needed to answer: {question[:200]}",
            registry=ToolRegistry(tool_list),
            planner=planner,
            emitter=self._emitter,
            limits=self._limits,
            step_label=STEP_LABEL,
        )
        run = await runtime.run(lambda: {})

        references = [*_read_references(run, self.commit_sha), *code_references_from(run)]
        # What the architecture analysis already cited for these components joins
        # the evidence as it is: its excerpts were read at the same commit.
        mapped = [ref for ref in selected.references() if ref.path in {e.path for e in tree.files}]
        evidence = EvidenceBundle(
            code_references=[*_dedupe(references)[:12], *_new(mapped, references)][:MAX_REFERENCES],
            documentation=documentation_from(run)[:4],
            measurements=runtime_measurements(
                self._overlay, self._architecture, selected.node_ids(), focus=self._focus
            ),
        )
        overview_text = next(
            (ref.excerpt or "" for ref in evidence.code_references if ref.path == overview),
            "",
        )
        return run, evidence, (overview, overview_text)

    # -- prompt ---------------------------------------------------------------

    def _prompt(
        self,
        question: str,
        kind: QuestionKind,
        evidence: EvidenceBundle,
        overview: tuple[str | None, str],
        downgraded: str | None,
        selected: ArchitectureSlice | None = None,
        shape: ExplanationShape | None = None,
    ) -> str:
        overview_path, overview_text = overview
        code = [
            f"- {ref.path}"
            + (f":{ref.start_line}" if ref.start_line else "")
            + "\n"
            + _indent((ref.excerpt or "").strip())
            for ref in evidence.code_references
            if ref.path != overview_path
        ]
        docs = [
            f"- {doc.url} ({doc.title or 'untitled'})\n" + _indent((doc.excerpt or "").strip())
            for doc in evidence.documentation
        ]
        note = (
            f"\nNote for the reader, to mention in a caveat: {downgraded}\n" if downgraded else ""
        )
        if self._focus is not None:
            note += f"\nAsked from the Architecture page. {self._focus.describe()}\n"
        measured = [
            f"- {m.name}: {m.value:g} {m.unit} ({m.source.value})" for m in evidence.measurements
        ]
        if shape is not None:
            shape_text = SHAPE_GUIDANCE[shape]
        else:
            shape_text = "choose the best of these and set 'shape' to it:\n" + "\n".join(
                f"  {text}" for text in SHAPE_GUIDANCE.values()
            )
        return PROMPT_TEMPLATE.format(
            kind=kind.value,
            question=question,
            repo_url=self.repo_url,
            commit_sha=self.commit_sha[:12],
            manifest=manifest_summary(self._manifest),
            overview_path=overview_path or "none",
            overview=_indent(overview_text.strip()[:4000]) or "  (no README found)",
            code="\n".join(code) or "- (no matches)",
            documentation="\n".join(docs) or "- (none retrieved)",
            architecture=(selected or ArchitectureSlice()).describe(),
            measurements="\n".join(measured)
            or "- (none: nothing has been run, so state no measured values)",
            note=note,
            shape=shape_text,
        )


def manifest_summary(manifest: RepoManifest | None) -> str:
    """The manifest, as lines a model can read. Facts RepoLab already holds."""
    if manifest is None:
        return "- (no manifest)"
    lines = [
        f"- languages: {', '.join(manifest.languages) or 'none detected'}",
        f"- frameworks: {', '.join(manifest.frameworks) or 'none detected'}",
    ]
    for surface in manifest.interaction_surfaces:
        lines.append(
            f"- interaction surface {surface.type.value} '{surface.name}': {surface.reason}"
        )
    for entry in manifest.entrypoints[:6]:
        where = f" ({entry.path})" if entry.path else ""
        lines.append(f"- entry point {entry.name}: {entry.command}{where}")
    for service in manifest.services[:6]:
        ports = f" ports {service.ports}" if service.ports else ""
        lines.append(f"- service {service.name}{ports}")
    for concept in manifest.concepts[:6]:
        lines.append(f"- concept {concept.name}: {concept.summary}")
    return "\n".join(lines)


def verify_answer(
    answer: GroundedAnswer,
    *,
    evidence: EvidenceBundle,
    repository_root: Path | None = None,
    architecture: ArchitectureSlice | None = None,
) -> VerifiedAnswer:
    """Check each claim's support against the evidence actually gathered.

    A ``repository`` claim must cite a file in the evidence -- or, failing that,
    a file that really exists in the checkout, since citing the right file the
    search did not happen to open is not a fabrication. A ``documented`` claim
    must cite a retrieved URL. Inferences are labelled as the weaker kind and are
    not checked.
    """
    pin_cited_lines(answer, evidence)
    paths = {ref.path for ref in evidence.code_references}
    urls = {doc.url for doc in evidence.documentation}
    unsupported: list[UnsupportedAnswerClaim] = []
    checked = 0

    for claim in answer.claims:
        support = claim.support.strip()
        if claim.kind is AnswerClaimKind.REPOSITORY:
            checked += 1
            cited = support.split(":", 1)[0].strip().lstrip("./")
            if cited not in paths and not _exists(repository_root, cited):
                unsupported.append(
                    UnsupportedAnswerClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"cites {support!r}, which is not a file in this repository",
                    )
                )
        elif claim.kind is AnswerClaimKind.DOCUMENTED:
            checked += 1
            if support not in urls:
                unsupported.append(
                    UnsupportedAnswerClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=(
                            f"cites {support!r}, which is not one of the documentation "
                            "pages retrieved for this question"
                        ),
                    )
                )

    layout = verify_layout(
        answer,
        Resolver(
            evidence=evidence,
            root=repository_root,
            slice=architecture or ArchitectureSlice(),
        ),
    )
    unsupported.extend(layout.unsupported)
    checked += sum(1 for claim in answer.claims if claim.kind is AnswerClaimKind.MEASURED)
    return VerifiedAnswer(
        answer=answer,
        unsupported=unsupported,
        checked_claims=checked,
        not_established=layout.not_established,
        dropped=layout.dropped,
    )


# ---------------------------------------------------------------------------


_CITED_LINE = re.compile(r"^(?P<path>[\w./@+-]+\.[\w]+):(?P<line>\d+)(?:-\d+)?$")
#: How far a cited line may sit from a range RepoLab read and still be "in" it.
_NEAR = 3


def pin_cited_lines(answer: GroundedAnswer, evidence: EvidenceBundle) -> None:
    """Keep a ``path:line`` only where RepoLab itself read that line.

    The model may name the file; a line number of its own is never trusted. A
    cited line outside every range the investigation read is reduced to the
    file, so the source viewer never highlights a line nothing verified.
    """
    ranges: dict[str, list[tuple[int, int]]] = {}
    for ref in evidence.code_references:
        if ref.start_line is not None:
            ranges.setdefault(ref.path, []).append((ref.start_line, ref.end_line or ref.start_line))

    def pin(value: str) -> str:
        match = _CITED_LINE.match(value.strip())
        if match is None:
            return value
        path, line = match["path"].removeprefix("./"), int(match["line"])
        for start, end in ranges.get(path, []):
            if start - _NEAR <= line <= end + _NEAR:
                return value
        return path

    def walk(model: BaseModel) -> None:
        for name in type(model).model_fields:
            value = getattr(model, name)
            if name in ("support", "source") and isinstance(value, str):
                setattr(model, name, pin(value))
            elif name == "sources" and isinstance(value, list):
                setattr(model, name, [pin(v) if isinstance(v, str) else v for v in value])
            elif isinstance(value, BaseModel):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, BaseModel):
                        walk(item)

    walk(answer)


def _read_references(run: AgentRun, commit_sha: str) -> list[CodeReference]:
    """The files the investigation read, each with the lines it saw."""
    references: list[CodeReference] = []
    for step in run.steps:
        if step.request is None or step.request.tool != "read_file":
            continue
        if step.result is None or not step.result.ok:
            continue
        path = step.result.data.get("path")
        lines = step.result.data.get("lines") or []
        if not isinstance(path, str):
            continue
        shown = [str(line) for line in lines[:EXCERPT_LINES]]
        references.append(
            CodeReference(
                path=path,
                start_line=1,
                end_line=max(1, len(shown)),
                commit_sha=commit_sha,
                excerpt="\n".join(shown),
            )
        )
    return references


def _new(candidates: list[CodeReference], existing: list[CodeReference]) -> list[CodeReference]:
    seen = {(ref.path, ref.start_line) for ref in existing}
    return [ref for ref in candidates if (ref.path, ref.start_line) not in seen]


def _merge(proposed: list[str], derived: list[str], asked: str) -> list[str]:
    """The model's next questions first, then ones from the architecture."""
    merged: list[str] = []
    for question in [*proposed, *derived]:
        if question.strip().lower() == asked.strip().lower():
            continue
        if question.lower() not in {q.lower() for q in merged}:
            merged.append(question)
    return merged[:MAX_FOLLOW_UPS]


def _dedupe(references: list[CodeReference]) -> list[CodeReference]:
    seen: set[tuple[str, int | None]] = set()
    kept: list[CodeReference] = []
    for ref in references:
        key = (ref.path, ref.start_line)
        if key in seen:
            continue
        seen.add(key)
        kept.append(ref)
    return kept


def _exists(root: Path | None, relative: str) -> bool:
    if root is None or not relative:
        return False
    try:
        target = (root / relative).resolve()
        return target.is_file() and target.is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _subject(repo_url: str) -> str:
    """The project's name, from its URL, to anchor a documentation search."""
    return repo_url.rstrip("/").split("/")[-1].removesuffix(".git")


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())
