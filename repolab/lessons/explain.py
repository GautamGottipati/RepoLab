"""\"What Just Happened?\"

After a step that is worth it, a learner can ask what the server actually did.
The answer is assembled from three things and nothing else: the command's real
output, the repository lines that implement the behaviour, and version-matched
official documentation.

Every sentence is labelled with the kind of support behind it, and the labels are
then **checked by deterministic code**, as the experiment explanations are:

* ``observed``  -- must quote text that really is in the output;
* ``source``    -- must cite a repository path that was actually supplied;
* ``documented``-- must cite a page that was actually retrieved;
* ``inferred``  -- not checked, and labelled as the weaker kind.

Two prohibitions are enforced here rather than requested in a prompt. An
``observed`` claim may not state a number the output does not contain, and no
claim may state a duration at all -- a terminal shows no timings, so a sentence
about how long something took could only have been invented. CLAUDE.md is
unambiguous: models reason about measurements and never manufacture them.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
)
from repolab.lessons.models import LessonStep
from repolab.llm.tiers import Purpose

EXPLANATION_MAX_TOKENS = 2048
EXPLANATION_TIMEOUT_SECONDS = 120.0

#: How much output the model is shown. A terminal result is small; a runaway
#: SELECT is not, and the explanation does not improve past the first screenful.
MAX_OUTPUT_CHARS = 4000

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

#: Performance language a terminal result cannot support. Two shapes: a number
#: next to a time unit, and words that are *only* about speed. Deliberately not
#: the bare word "second" -- "the second column" is a legitimate sentence, and a
#: check that rejects it teaches a learner to ignore the warnings.
_TIMING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d[\d.,]*\s*(?:ms|s|secs?|seconds?|minutes?|mins?|hours?)\b"),
    re.compile(r"\b(?:milli|micro|nano)seconds?\b"),
    re.compile(r"\b(?:latency|throughput|faster|slower|slowest|fastest|elapsed)\b"),
    re.compile(r"\btook\s+(?:about\s+)?\d"),
    re.compile(r"\bper\s+second\b"),
)

SYSTEM_PROMPT = """\
You explain what a database server did internally in response to one statement a \
learner just ran.

You are given the statement, the real output, repository source lines, and \
official documentation. Explain only from those.

Label every sentence:
- observed: quote text that appears in the output. Support is the quoted text.
- source: behaviour implemented in a supplied file. Support is that file's path.
- documented: behaviour stated by a supplied page. Support is that page's URL.
- inferred: a conclusion from the above. Support names what it follows from.

Never state a timing, a duration, a rate, or any performance figure: you were \
given none. Never state a number that is not in the output. Never cite a path or \
URL you were not given. Three to six claims.
"""


class SupportKind(StrEnum):
    """What a claim about a command rests on."""

    OBSERVED = "observed"
    """Text the server really printed."""

    SOURCE = "source"
    """Behaviour implemented in a cited repository file."""

    DOCUMENTED = "documented"
    """Behaviour stated by a cited official page."""

    INFERRED = "inferred"
    """A conclusion from the above. Not itself evidence."""


class Claim(BaseModel):
    """One labelled sentence."""

    kind: SupportKind = Field(description="observed, source, documented, or inferred")
    statement: str = Field(description="One sentence about what the server did.")
    support: str = Field(
        description=(
            "For 'observed': the exact text quoted from the output. "
            "For 'source': the file path. For 'documented': the page URL. "
            "For 'inferred': which claims it follows from."
        )
    )


class WhatHappened(BaseModel):
    """The model's answer."""

    summary: str = Field(description="Two or three sentences on what the server did.")
    claims: list[Claim] = Field(description="The claims the summary rests on, each labelled.")

    def of_kind(self, kind: SupportKind) -> list[Claim]:
        return [claim for claim in self.claims if claim.kind is kind]


class UnsupportedClaim(BaseModel):
    """A claim deterministic checking could not corroborate."""

    statement: str
    kind: SupportKind
    problem: str


class VerifiedWhatHappened(BaseModel):
    """An explanation, what could not be corroborated, and its evidence."""

    explanation: WhatHappened
    evidence: EvidenceBundle
    unsupported: list[UnsupportedClaim] = Field(default_factory=list)
    checked_claims: int = 0

    @computed_field
    @property
    def grounded(self) -> bool:
        """Whether every checkable claim traced back to real evidence."""
        return not self.unsupported


class ExplainInput(BaseModel):
    """Exactly what the model is shown."""

    command: str
    output: str
    exit_code: int | None = None
    concept: str = ""
    version: str = ""
    code_references: list[CodeReference] = Field(default_factory=list)
    documentation: list[DocumentationReference] = Field(default_factory=list)

    def prompt(self) -> str:
        lines = [
            f"Cassandra version: {self.version or 'unknown'}",
            f"The concept this step teaches: {self.concept or 'unstated'}",
            "",
            "THE STATEMENT THE LEARNER RAN",
            self.command,
            "",
            f"WHAT THE SERVER PRINTED (exit code {self.exit_code})",
            self.output[:MAX_OUTPUT_CHARS] or "(no output)",
        ]
        if self.code_references:
            lines += ["", "REPOSITORY SOURCE YOU MAY CITE"]
            for ref in self.code_references:
                location = f"{ref.path}:{ref.start_line}" if ref.start_line else ref.path
                lines.append(f"- {location}")
                if ref.excerpt:
                    lines.append(f"  {ref.excerpt}")
        if self.documentation:
            lines += ["", "OFFICIAL DOCUMENTATION YOU MAY CITE"]
            for doc in self.documentation:
                lines.append(f"- {doc.title or doc.url} <{doc.url}>")
                if doc.excerpt:
                    lines.append(f"  {doc.excerpt}")
        lines += ["", "Explain what the server did internally."]
        return "\n".join(lines)


def explainable(step: LessonStep) -> bool:
    """Whether this step is one of the "selected commands" worth explaining.

    Two cases earn it: a step grounded in repository source, where there is
    something internal to say, and a step that provokes an error, where the
    message itself is the lesson. Everything else would produce a paraphrase of
    the step's own context.
    """
    return bool(step.code_references) or step.expects_failure


def build_explain_input(
    step: LessonStep,
    command: str,
    output: str,
    *,
    exit_code: int | None = None,
    version: str = "",
    documentation: list[DocumentationReference] | None = None,
) -> ExplainInput:
    """Assemble the explanation input from the step and the real result."""
    return ExplainInput(
        command=command,
        output=output,
        exit_code=exit_code,
        concept=step.concept,
        version=version,
        code_references=list(step.code_references),
        documentation=list(documentation or step.documentation_references),
    )


async def explain_command(
    router: object,
    data: ExplainInput,
) -> VerifiedWhatHappened:
    """Ask Super what happened, then check the answer against the evidence."""
    completion = await router.super(  # type: ignore[attr-defined]
        purpose=Purpose.INTERNAL_BEHAVIOR_EXPLANATION,
        schema=WhatHappened,
        prompt=data.prompt(),
        system=SYSTEM_PROMPT,
        max_tokens=EXPLANATION_MAX_TOKENS,
        timeout_seconds=EXPLANATION_TIMEOUT_SECONDS,
    )
    return verify_what_happened(completion.value, data)


def verify_what_happened(
    explanation: WhatHappened,
    data: ExplainInput,
    *,
    simulated: bool = False,
) -> VerifiedWhatHappened:
    """Check every checkable claim against what was actually available.

    *simulated* is the Mini Lab case: there is no real output, so an
    ``observed`` claim is unsupportable by definition and is reported as such
    rather than quietly accepted.
    """
    paths = {ref.path for ref in data.code_references}
    urls = {doc.url for doc in data.documentation}
    output_numbers = set(_numbers_in(data.output))
    normalised_output = _normalise(data.output)

    unsupported: list[UnsupportedClaim] = []
    checked = 0

    for claim in explanation.claims:
        timing = _timing_language(claim.statement)
        if timing:
            # Applies to every kind, including inferred: there is no timing in a
            # terminal result, so this can only have been invented.
            unsupported.append(
                UnsupportedClaim(
                    statement=claim.statement,
                    kind=claim.kind,
                    problem=f"states timing ({timing!r}); a terminal result contains none",
                )
            )
            continue

        if claim.kind is SupportKind.OBSERVED:
            checked += 1
            if simulated:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem="nothing was executed, so nothing was observed",
                    )
                )
                continue
            if _normalise(claim.support) not in normalised_output:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"quotes {claim.support!r}, which is not in the output",
                    )
                )
                continue
            invented = [n for n in _numbers_in(claim.statement) if n not in output_numbers]
            if invented:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"states {invented}, which the output does not contain",
                    )
                )
        elif claim.kind is SupportKind.SOURCE:
            checked += 1
            if _cited_path(claim.support) not in paths:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"cites {claim.support!r}, which was not supplied",
                    )
                )
        elif claim.kind is SupportKind.DOCUMENTED:
            checked += 1
            if claim.support not in urls:
                unsupported.append(
                    UnsupportedClaim(
                        statement=claim.statement,
                        kind=claim.kind,
                        problem=f"cites {claim.support!r}, which was not retrieved",
                    )
                )

    return VerifiedWhatHappened(
        explanation=explanation,
        evidence=_evidence(data, simulated=simulated),
        unsupported=unsupported,
        checked_claims=checked,
    )


def _evidence(data: ExplainInput, *, simulated: bool) -> EvidenceBundle:
    """The evidence bundle shown beside the explanation.

    The command's output is a log reference rather than a measurement: it is
    real, it is quotable, and it is not a number anything measured. A simulated
    bundle carries no measurements, which the bundle itself enforces.
    """
    logs = (
        []
        if simulated
        else [
            LogReference(
                source=data.command,
                excerpt=data.output[:MAX_OUTPUT_CHARS],
            )
        ]
    )
    return EvidenceBundle(
        code_references=list(data.code_references),
        logs=logs,
        documentation=list(data.documentation),
        simulated=simulated,
    )


def _cited_path(support: str) -> str:
    """The path in a source citation, with any line number removed.

    The prompt lists each file as ``path:line``, so a model citing
    ``StatementRestrictions.java:81`` is quoting exactly what it was shown. An
    earlier version compared the whole string against bare paths and reported a
    correct citation as unsupported -- a false alarm in the one place false
    alarms are most expensive, since the point of the check is that a real one
    can be trusted.
    """
    return re.sub(r":\d+(?:-\d+)?$", "", support.strip())


def _numbers_in(text: str) -> list[str]:
    return [match.group().replace(",", "") for match in _NUMBER.finditer(text)]


def _normalise(text: str) -> str:
    """Collapse whitespace, so a quote from a padded table still matches."""
    return " ".join(text.lower().split())


def _timing_language(statement: str) -> str | None:
    """The performance language in *statement*, if any."""
    lowered = statement.lower()
    for pattern in _TIMING_PATTERNS:
        match = pattern.search(lowered)
        if match:
            return match.group()
    return None
