"""The Learning Agent.

    repository evidence          (read from the real checkout, line by line)
       |
       v
    repository documentation     (the commit's own cqlsh.adoc)
       |
       v
    official documentation       (Tavily, cassandra.apache.org, version-matched)
       |
       v
    build capabilities           (what the resolved environment can actually do)
       |
       v
    Super writes a lesson        (untrusted output)
       |
       v
    deterministic gate           (the allowlist and the checkability rules)
       |
       v
    references verified          (a path that is not in the repository is dropped)
       |
       v
    a lesson, or the deterministic one

Super proposes; deterministic code decides. The same division as the Experiment
Agent, for the same reason: a generated step that runs an unpermitted statement,
declares an expectation nothing can check, or cites a file that does not exist is
worse than no lesson at all, because a learner cannot tell. All three are caught
here, and a lesson that fails the gate is replaced by
:func:`~repolab.lessons.cassandra.cassandra_lesson`, which is complete and runs
with no model at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from repolab.builds.models import BuildArtifact
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.evidence.models import CodeReference, DocumentationReference
from repolab.interfaces import ResearchProvider
from repolab.lessons.cassandra import (
    CQLSH_DOC,
    RepositoryEvidence,
    cassandra_lesson,
    gather_repository_evidence,
)
from repolab.lessons.models import MAX_STEPS, CheckKind, Lesson, LessonStep, StepCheck
from repolab.lessons.validation import LessonRejected, ensure_usable, lesson_problems
from repolab.llm.tiers import Purpose
from repolab.manifest.models import RepoManifest

log = structlog.get_logger(__name__)

#: Generating eight fully written steps is a long structured answer. Phase 7
#: measured the same statistics producing 1030, 2848 and over 3142 tokens across
#: attempts, so a tightly tuned cap is a cap that eventually fails.
LESSON_MAX_TOKENS = 8192
LESSON_TIMEOUT_SECONDS = 240.0

#: Attempts allowed at generating a usable lesson.
#:
#: Two, not one, and the second is told exactly what was wrong with the first.
#: Observed on the real generator: Super wrote eight good steps and left one
#: expectation blank, and the whole lesson was discarded for a field it could
#: have filled if asked. The same shape as the router's own schema retry -- one
#: corrective round trip, then the deterministic lesson.
MAX_GENERATION_ATTEMPTS = 2

#: Official documentation only, per CLAUDE.md.
DOC_DOMAINS: tuple[str, ...] = ("cassandra.apache.org",)
MAX_DOC_RESULTS = 4
DOC_EXCERPT_CHARS = 700

#: How much of the repository's own documentation is shown to the model.
REPO_DOC_CHARS = 4000

SYSTEM_PROMPT = """\
You write short, interactive lessons about real software, to be completed in a \
terminal connected to a real running server.

Rules you must follow:
- Every step is one CQL statement the learner types. No step is reading-only.
- Use only the statements and keyspaces the capabilities below permit.
- Never state what a command outputs unless the step's expectation is how you \
would recognise it; you have not run anything.
- Never invent file paths, line numbers, versions, timings or measurements.
- cqlsh prints NOTHING when CREATE, ALTER, DROP, TRUNCATE, INSERT, UPDATE, \
DELETE or USE succeeds. For such a step the expected observation is "no output", \
never a success message such as "Keyspace created".
- Cite at least one file from the repository listing in EVERY step, copying \
the path exactly. A path that is not in that listing is discarded, leaving \
the step with nothing behind it.
- One step must deliberately provoke a safe error, and the next must explain it.
"""


class GeneratedStep(BaseModel):
    """One step as Super returns it.

    Flat on purpose: a nested check object is one more thing for a model to get
    structurally wrong, and the fields are mapped onto :class:`StepCheck` here.
    """

    concept: str
    goal: str
    context: str
    action: str
    expected_observation: str
    check_kind: CheckKind
    check_expected: str = ""
    check_describe: str = ""
    hint: str = ""
    code_paths: list[str] = Field(default_factory=list)
    documentation_urls: list[str] = Field(default_factory=list)


class GeneratedLesson(BaseModel):
    """A lesson as Super returns it, before anything has been checked."""

    title: str
    summary: str
    steps: list[GeneratedStep]


class LessonInputs(BaseModel):
    """Everything the generator is allowed to know.

    Assembled explicitly so the four inputs the specification names are visible
    rather than implied, and so a test can assert each one reached the prompt.
    """

    repo_url: str
    commit_sha: str = ""
    version: str = ""
    fidelity: str = ""
    cli_command: str | None = None

    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    allowed_statements: list[str] = Field(default_factory=list)

    repository_files: list[CodeReference] = Field(default_factory=list)
    repository_documentation: str = ""
    official_documentation: list[DocumentationReference] = Field(default_factory=list)

    def prompt(self, problems: list[str] | None = None) -> str:
        """What the generator is shown.

        *problems* are the reasons a previous attempt was rejected, listed
        verbatim. Telling the model what deterministic code objected to is the
        only honest way to ask for a correction -- RepoLab will not fill the
        missing field in itself, because an expectation it invented would be a
        check on behaviour nobody verified.
        """
        lines = [
            f"Repository: {self.repo_url}",
            f"Commit: {self.commit_sha or 'unknown'}",
            f"Running version: {self.version or 'unknown'} (environment fidelity: {self.fidelity})",
            f"Statements run through: {self.cli_command or 'unknown'}",
            "",
            "WHAT THIS ENVIRONMENT CAN DO",
            *(f"- {item}" for item in self.capabilities),
            "",
            "WHAT IT CANNOT DO",
            *(f"- {item}" for item in self.limitations),
            "",
            "STATEMENTS THE TERMINAL PERMITS",
            ", ".join(self.allowed_statements),
            "",
            "REPOSITORY FILES YOU MAY CITE (path, line, excerpt)",
            *(
                f"- {ref.path}:{ref.start_line}  {ref.excerpt or ''}".rstrip()
                for ref in self.repository_files
            ),
        ]
        if self.repository_documentation:
            lines += [
                "",
                f"THE REPOSITORY'S OWN DOCUMENTATION ({CQLSH_DOC})",
                self.repository_documentation,
            ]
        if self.official_documentation:
            lines += ["", "OFFICIAL DOCUMENTATION (version-matched)"]
            for doc in self.official_documentation:
                lines.append(f"- {doc.title or doc.url} <{doc.url}>")
                if doc.excerpt:
                    lines.append(f"  {doc.excerpt}")
        lines += [
            "",
            f"Write between 6 and {MAX_STEPS} steps, in the order a learner would do them: "
            "connect, inspect what exists, create a keyspace, create a table, insert, "
            "query, provoke one safe error, then explain that error.",
        ]
        if problems:
            lines += [
                "",
                "YOUR PREVIOUS ATTEMPT WAS REJECTED. Fix exactly these problems:",
                *(f"- {problem}" for problem in problems),
                "",
                "A step whose expectation is empty cannot be checked. For a step that "
                "must fail, give the text the server's own error message will contain. "
                "For a step that must succeed and prints nothing, use the succeeds check.",
            ]
        return "\n".join(lines)


class LearningAgent:
    """Generates one lesson for one repository and one resolved environment."""

    def __init__(
        self,
        *,
        router: object | None = None,
        research: ResearchProvider | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._router = router
        self._research = research
        self._emitter = emitter if emitter is not None else NullEmitter()

    async def gather(
        self,
        manifest: RepoManifest,
        artifact: BuildArtifact,
        root: Path | None = None,
    ) -> tuple[LessonInputs, RepositoryEvidence]:
        """Collect the four inputs. No model is called here."""
        from repolab.experiences.cli.safety import ALLOWED_VERBS

        self._status("Reading the repository for lesson material")
        evidence = gather_repository_evidence(root, artifact.commit_sha or manifest.commit_sha)

        surface_files = [
            ref
            for surface in manifest.interaction_surfaces
            for ref in surface.evidence
            if surface.type.value == "cli"
        ]

        self._status("Reading the repository's own cqlsh documentation")
        repo_doc = _read_repository_documentation(root)

        official = await self._official_documentation(artifact.version)

        inputs = LessonInputs(
            repo_url=manifest.repo_url,
            commit_sha=artifact.commit_sha or manifest.commit_sha,
            version=artifact.version or "",
            fidelity=artifact.fidelity.value,
            cli_command=artifact.cli_command,
            capabilities=list(artifact.capabilities.capabilities),
            limitations=list(artifact.capabilities.limitations),
            allowed_statements=sorted(verb.upper() for verb in ALLOWED_VERBS),
            repository_files=_dedupe_refs([*evidence.code.values(), *surface_files]),
            repository_documentation=repo_doc,
            official_documentation=official,
        )
        return inputs, evidence

    async def generate(
        self,
        manifest: RepoManifest,
        artifact: BuildArtifact,
        root: Path | None = None,
    ) -> Lesson:
        """The lesson to put in front of a learner.

        Never raises because generation failed: a rejected or impossible lesson
        falls back to the deterministic one, which is a complete lesson rather
        than a placeholder.
        """
        inputs, evidence = await self.gather(manifest, artifact, root)
        fallback = cassandra_lesson(
            manifest.repo_url,
            inputs.commit_sha,
            artifact=artifact,
            evidence=evidence,
        )
        if self._router is None:
            self._status("Assembling the lesson from repository evidence")
            return fallback

        self._status("Writing a lesson from the repository and its documentation")

        problems: list[str] = []
        lesson: Lesson | None = None
        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            try:
                generated = await self._ask_super(inputs, problems)
            except Exception as exc:  # noqa: BLE001 - a model failure is not a lesson failure
                log.warning("lesson.generation_failed", repo=manifest.repo_url, error=str(exc))
                self._status("Using RepoLab's own Cassandra lesson")
                return fallback

            candidate = _to_lesson(generated, inputs, artifact, evidence)
            problems = lesson_problems(candidate)
            if not problems:
                lesson = candidate
                break

            # Logged, never silently swallowed: a generator that keeps producing
            # refused statements is something we want to know about.
            log.warning(
                "lesson.rejected",
                repo=manifest.repo_url,
                attempt=attempt,
                problems=problems[:5],
                steps=candidate.step_count,
            )
            if attempt < MAX_GENERATION_ATTEMPTS:
                self._status("Revising the lesson: some steps could not be checked")

        if lesson is None:
            self._status("Using RepoLab's own Cassandra lesson")
            return fallback

        if lesson.ungrounded_steps:
            # Not a rejection: a lesson can be correct and thin. Logged because a
            # generator that never cites the repository is one worth noticing,
            # and the screen says so to the learner as well.
            log.info(
                "lesson.ungrounded_steps",
                repo=manifest.repo_url,
                steps=lesson.ungrounded_steps,
                offered=len(inputs.repository_files),
            )

        self._emitter.emit(
            EventType.STEP_DONE,
            f"Lesson ready: {lesson.title}",
            step="lesson_generation",
            data={
                "steps": lesson.step_count,
                "generated_by": lesson.generated_by,
                "ungrounded_steps": lesson.ungrounded_steps,
            },
        )
        return lesson

    async def _ask_super(
        self, inputs: LessonInputs, problems: list[str] | None = None
    ) -> GeneratedLesson:
        completion = await self._router.super(  # type: ignore[attr-defined]
            purpose=Purpose.LESSON_GENERATION,
            schema=GeneratedLesson,
            prompt=inputs.prompt(problems),
            system=SYSTEM_PROMPT,
            max_tokens=LESSON_MAX_TOKENS,
            timeout_seconds=LESSON_TIMEOUT_SECONDS,
        )
        return completion.value

    async def _official_documentation(self, version: str | None) -> list[DocumentationReference]:
        """Version-matched official pages, or nothing.

        Nothing is the correct result with no research provider. A lesson citing
        a documentation URL RepoLab did not retrieve would be a guess at an
        external API, which is precisely what is not allowed here.
        """
        if self._research is None:
            return []

        label = version or "5.0"
        self._status("Finding official Cassandra documentation")
        query = f"Apache Cassandra {label} cqlsh CREATE TABLE primary key ALLOW FILTERING"
        try:
            results = await self._research.search(
                query,
                include_domains=list(DOC_DOMAINS),
                max_results=MAX_DOC_RESULTS,
            )
        except Exception as exc:  # noqa: BLE001 - research is a supplement, not a dependency
            log.warning("lesson.research_failed", error=str(exc))
            return []

        return [
            DocumentationReference(
                url=item.url,
                title=item.title,
                excerpt=(item.content or "")[:DOC_EXCERPT_CHARS] or None,
                version=label,
            )
            for item in results
        ]

    def _status(self, message: str) -> None:
        """User-facing progress only. Never model reasoning (CLAUDE.md)."""
        self._emitter.emit(EventType.AGENT_STATUS, message, step="lesson_generation")


#: How a statement maps to the part of the repository that implements it.
#:
#: Checked in order, first match wins. These are **RepoLab's** citations, not a
#: model's: each one points at a file :func:`gather_repository_evidence` already
#: found and read at this commit, so attaching one is a lookup rather than a
#: claim. Super was asked to cite a file per step and, on the real generator,
#: cited none at all -- a correct lesson with nothing behind any step. Rather
#: than leave it ungrounded, the statement itself says which code ran.
_STATEMENT_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("allow filtering", ("allow_filtering", "select")),
    ("system.local", ("local_table", "cqlsh")),
    ("describe", ("describe", "cqlsh_doc")),
    ("create keyspace", ("create_keyspace",)),
    ("create table", ("create_table",)),
    ("insert into", ("insert",)),
    ("update ", ("insert",)),
    ("delete ", ("insert",)),
    ("select", ("select",)),
    ("use ", ("cqlsh", "cqlsh_doc")),
)


def _derived_references(action: str, evidence: RepositoryEvidence) -> list[CodeReference]:
    """The repository lines behind *action*, when the generator cited none."""
    lowered = action.lower()
    for marker, keys in _STATEMENT_SOURCES:
        if marker in lowered:
            found = evidence.refs(*keys)
            if found:
                return found
    return evidence.refs("cqlsh_doc")


def _to_lesson(
    generated: GeneratedLesson,
    inputs: LessonInputs,
    artifact: BuildArtifact,
    evidence: RepositoryEvidence,
) -> Lesson:
    """Turn Super's answer into a lesson, keeping only verifiable references."""
    known_paths = {ref.path: ref for ref in inputs.repository_files}
    known_docs = {doc.url: doc for doc in inputs.official_documentation}

    steps = [
        LessonStep(
            number=index,
            concept=step.concept,
            goal=step.goal,
            context=step.context,
            action=step.action.strip().rstrip(";"),
            expected_observation=step.expected_observation,
            validation=StepCheck(
                kind=step.check_kind,
                expected=step.check_expected,
                describe=step.check_describe,
            ),
            hint=step.hint,
            # Only references RepoLab itself resolved survive. A path the model
            # invented is dropped rather than shown to a learner as a citation.
            # Only references RepoLab itself resolved survive: a path the model
            # invented is dropped rather than shown to a learner as a citation.
            # A step left with none gets RepoLab's own, derived from what the
            # statement does.
            code_references=(
                [known_paths[path] for path in step.code_paths if path in known_paths]
                or _derived_references(step.action, evidence)
            ),
            documentation_references=[
                known_docs[url] for url in step.documentation_urls if url in known_docs
            ],
        )
        for index, step in enumerate(generated.steps[:MAX_STEPS], start=1)
    ]

    return Lesson(
        repo_url=inputs.repo_url,
        commit_sha=inputs.commit_sha,
        title=generated.title.strip() or "Exploring Cassandra through cqlsh",
        summary=generated.summary.strip(),
        steps=steps,
        generated_by="super",
        fidelity=artifact.fidelity.value,
        simulated=artifact.simulated,
    )


def _read_repository_documentation(root: Path | None) -> str:
    """The commit's own cqlsh documentation, truncated.

    Version-matched by construction: it is the documentation shipped with the
    code being taught, which is a stronger guarantee than anything retrieved
    externally.
    """
    if root is None:
        return ""
    path = root / CQLSH_DOC
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:REPO_DOC_CHARS]
    except OSError:
        return ""


def _dedupe_refs(refs: Sequence[CodeReference]) -> list[CodeReference]:
    """One reference per path, preferring the first (which carries a line number)."""
    seen: dict[str, CodeReference] = {}
    for ref in refs:
        seen.setdefault(ref.path, ref)
    return list(seen.values())


__all__ = [
    "GeneratedLesson",
    "GeneratedStep",
    "LearningAgent",
    "LessonInputs",
    "LessonRejected",
    "ensure_usable",
]
