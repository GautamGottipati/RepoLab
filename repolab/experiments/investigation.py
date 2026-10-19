"""Question-driven investigation, before any experiment is designed.

The Repository Investigator asks "what is this repository and how does it run?".
This planner asks something narrower: "what does this repository say about the
thing the user asked about?" Same tool layer, same runtime, same events -- a
different plan.

It is deterministic and evidence-driven, which is the pattern Phase 3 and Phase 4
settled on: the model is asked to reason where reasoning is actually needed
(designing the experiment, interpreting the result), not to decide which file to
open next when the question already says.
"""

from __future__ import annotations

import re
from typing import Any

from repolab.agents.models import AgentRun, ToolRequest
from repolab.agents.tools import ToolRegistry
from repolab.evidence.models import CodeReference, DocumentationReference
from repolab.experiments.validation import canonical_variable, variable_in_question

#: Words that carry no search value.
_STOPWORDS = frozenset(
    (
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "because",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doesn",
        "for",
        "from",
        "get",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "job",
        "just",
        "like",
        "make",
        "of",
        "on",
        "or",
        "our",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "use",
        "using",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "your",
    )
)

#: Spark settings worth searching for by name whatever the wording, because they
#: are what the demo question is actually about.
_SPARK_TERMS: tuple[str, ...] = (
    "spark.sql.shuffle.partitions",
    "shuffle",
    "partition",
    "groupBy",
    "SparkSession",
)

#: Documentation page most likely to document the variable, version-matched.
_DOC_QUERY = "{variable} default {version} performance tuning"


def question_terms(question: str, *, limit: int = 4) -> list[str]:
    """Search terms derived from the question.

    Dotted configuration keys are kept whole -- ``spark.sql.shuffle.partitions``
    is one term, not four -- because that is how it appears in code.
    """
    dotted = re.findall(r"[a-zA-Z][\w]*(?:\.[\w]+){2,}", question)
    words = [
        word
        for word in re.findall(r"[a-zA-Z_][\w]*", question.lower())
        if len(word) > 3 and word not in _STOPWORDS
    ]

    terms: list[str] = []
    for term in [*dotted, *words]:
        if term not in terms:
            terms.append(term)
    return terms[:limit]


class ExperimentInvestigationPlanner:
    """Plans a focused, read-only investigation of one question."""

    name = "experiment_investigation"

    def __init__(
        self,
        question: str,
        *,
        research_available: bool = False,
        version: str | None = None,
        variable_hint: str | None = None,
    ) -> None:
        self.question = question
        self._research = research_available
        self._version = version or "latest"
        self._variable = (
            canonical_variable(variable_hint or "")
            or variable_in_question(variable_hint or question)
            or ""
        )
        self._terms = question_terms(question)

    def _search_terms(self) -> list[str]:
        """What to look for in the repository.

        The question's own words first, then the Spark settings the demo question
        is about, so an unhelpfully phrased question still finds the shuffle
        configuration rather than nothing.
        """
        terms = list(self._terms)
        for term in _SPARK_TERMS:
            if term not in terms:
                terms.append(term)
        return terms[:6]

    async def next_request(self, run: AgentRun, context: dict[str, Any]) -> ToolRequest | None:
        done = {step.request.tool for step in run.steps if step.request is not None}

        # What the project declares: this is where the Spark version comes from,
        # and the version determines which documentation is relevant.
        if "inspect_dependencies" not in done:
            return ToolRequest(
                tool="inspect_dependencies",
                arguments={},
                reason="Find the declared version of the framework in question",
            )
        if "inspect_config" not in done:
            return ToolRequest(
                tool="inspect_config",
                arguments={},
                reason="Find configuration that governs how the job runs",
            )

        searched = {
            str(step.request.arguments.get("query"))
            for step in run.steps
            if step.request is not None and step.request.tool == "search_code"
        }
        for term in self._search_terms():
            if term not in searched:
                return ToolRequest(
                    tool="search_code",
                    arguments={"query": term},
                    reason=f"Find where {term} appears in the repository",
                )

        # Read the files the searches actually hit, rather than guessing at names.
        read_already = {
            str(step.request.arguments.get("path"))
            for step in run.steps
            if step.request is not None and step.request.tool == "read_file"
        }
        for path in _hit_paths(run)[:3]:
            if path not in read_already:
                return ToolRequest(
                    tool="read_file",
                    arguments={"path": path},
                    reason=f"Read {path}, where the search matched",
                )

        # Official documentation last, and only version-matched: the default value
        # of a setting is a documented fact, not something to infer from code.
        if self._research and "search_official_docs" not in done and self._variable:
            return ToolRequest(
                tool="search_official_docs",
                arguments={
                    "query": _DOC_QUERY.format(variable=self._variable, version=self._version),
                    "domain": "spark.apache.org",
                },
                reason=f"Check what official Spark {self._version} docs say",
            )

        return None


def _hit_paths(run: AgentRun) -> list[str]:
    """Repository paths the code searches matched, most-matched first."""
    counts: dict[str, int] = {}
    for step in run.steps:
        if step.request is None or step.request.tool != "search_code" or step.result is None:
            continue
        for match in step.result.data.get("matches", []):
            path = match.get("path")
            if isinstance(path, str):
                counts[path] = counts.get(path, 0) + 1
    return sorted(counts, key=lambda path: (-counts[path], path))


def tool_descriptions(registry: ToolRegistry) -> list[dict[str, Any]]:
    return registry.describe()


def code_references_from(run: AgentRun, *, limit: int = 8) -> list[CodeReference]:
    """Code references for the places the investigation actually looked.

    Built from the search matches rather than from the whole detection run, so a
    citation points at a line that mentions the thing being investigated.
    """
    references: list[CodeReference] = []
    seen: set[tuple[str, int | None]] = set()

    for step in run.steps:
        if step.request is None or step.result is None:
            continue
        if step.request.tool != "search_code":
            continue
        for match in step.result.data.get("matches", []):
            path, line = match.get("path"), match.get("line")
            if not isinstance(path, str):
                continue
            key = (path, line if isinstance(line, int) else None)
            if key in seen:
                continue
            seen.add(key)
            references.append(
                CodeReference(
                    path=path,
                    start_line=key[1],
                    excerpt=str(match.get("text", ""))[:200],
                )
            )
            if len(references) >= limit:
                return references
    return references


def documentation_from(
    run: AgentRun, *, version: str | None = None
) -> list[DocumentationReference]:
    """Documentation the investigation retrieved, with the version it describes."""
    documents: list[DocumentationReference] = []
    seen: set[str] = set()

    for step in run.steps:
        if step.request is None or step.result is None:
            continue
        if step.request.tool != "search_official_docs":
            continue
        for hit in step.result.data.get("results", []):
            url = hit.get("url")
            if not isinstance(url, str) or url in seen:
                continue
            seen.add(url)
            documents.append(
                DocumentationReference(
                    url=url,
                    title=hit.get("title"),
                    excerpt=str(hit.get("excerpt", ""))[:600],
                    version=version,
                )
            )
    return documents


def investigation_summary(run: AgentRun, *, limit: int = 12) -> list[str]:
    """Concise, user-facing lines describing what the investigation found.

    Statuses only. No model output, and no file contents beyond the matched line
    excerpts already recorded as code references.
    """
    return [step.status for step in run.steps if step.status][:limit]
