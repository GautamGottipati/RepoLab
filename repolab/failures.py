"""Turning a failure into something a person can act on.

Every failure in RepoLab reached the browser as ``f"{type(exc).__name__}: {exc}"``
until this module existed. That is not a stack trace -- there have never been
stack traces in the UI -- but it is developer language:

    SchemaValidationError: nvidia/nemotron-3-super did not produce valid
    ExperimentPlan after 2 attempt(s)

    SandboxError: docker run failed: Cannot connect to the Docker daemon at
    unix:///var/run/docker.sock

Both of those are *true*, and neither tells a person watching a demo what
happened or what to do about it. :func:`explain` maps the failures RepoLab can
actually hit onto three sentences -- what happened, why, and what to do -- and
keeps the original text behind a disclosure for whoever wants it.

Two rules hold throughout:

* **Nothing is invented.** A failure RepoLab does not recognise gets a generic
  headline and its exception type in ``technical``; it never gets a confident
  diagnosis that might be wrong.
* **No traceback ever reaches a person.** The technical field is the exception's
  own message, truncated. Asserted by a test, because the easy way to break this
  is to pass a formatted traceback into it one day.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

#: Longest technical detail kept. Long enough for a stack-free message and the
#: tail of a build log line; short enough that nothing can hide in it.
MAX_TECHNICAL = 600


class FailureKind(StrEnum):
    """What broke, grouped the way a reader would group it."""

    MODEL = "model"
    """A language model did not answer, or answered unusably."""

    ENVIRONMENT = "environment"
    """Docker, a container or a sandbox."""

    BUILD = "build"
    """RepoLab could not produce a runnable environment."""

    EXECUTION = "execution"
    """The software ran and failed."""

    REPOSITORY = "repository"
    """Something about the repository itself."""

    RESEARCH = "research"
    """External documentation was unavailable."""

    INTERNAL = "internal"
    """RepoLab's own fault, or something unrecognised."""


class Failure(BaseModel):
    """A failure as a person should read it."""

    headline: str
    """One sentence, no jargon. What happened."""

    why: str = ""
    """What actually went wrong, in plain words."""

    next_step: str = ""
    """What to do about it."""

    kind: FailureKind = FailureKind.INTERNAL
    retryable: bool = False
    """Whether trying the same thing again is reasonable."""

    technical: str = ""
    """The exception's own type and message, for whoever wants it."""

    def model_post_init(self, _context: object) -> None:
        if len(self.technical) > MAX_TECHNICAL:
            object.__setattr__(self, "technical", self.technical[:MAX_TECHNICAL] + "…")

    @property
    def summary(self) -> str:
        """Headline and next step on one line, for a log or an event message."""
        return f"{self.headline} {self.next_step}".strip()

    def model_dump_public(self) -> dict[str, str | bool]:
        """What an API hands to a browser."""
        return {
            "headline": self.headline,
            "why": self.why,
            "next_step": self.next_step,
            "kind": self.kind.value,
            "retryable": self.retryable,
            "technical": self.technical,
        }


def _of(exc: BaseException) -> str:
    """The exception's own words: type and message, never a traceback."""
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


# ---------------------------------------------------------------------------
# The mapping
#
# Keyed by exception class name rather than by the class, so this module imports
# nothing from the packages it describes -- a failure explainer that drags in
# Docker, Spark and the model router would be a failure explainer that cannot be
# imported while any of them are broken.
# ---------------------------------------------------------------------------

_MODEL_SLOW = Failure(
    headline="The model took too long to answer.",
    why="RepoLab gives each model call a deadline, and this one passed it.",
    next_step="Try again. The first call after a quiet period is usually the slowest.",
    kind=FailureKind.MODEL,
    retryable=True,
)

_MODEL_UNUSABLE = Failure(
    headline="The model's answer could not be used.",
    why=(
        "RepoLab asks for a structured answer and checks it. This one did not "
        "match what was asked for, even after a second attempt."
    ),
    next_step=(
        "Try again. RepoLab will not act on an answer it cannot validate, so "
        "nothing was run on the strength of it."
    ),
    kind=FailureKind.MODEL,
    retryable=True,
)

_MODEL_EMPTY = Failure(
    headline="The model replied with nothing usable.",
    why="The response arrived but carried no answer text.",
    next_step="Try again.",
    kind=FailureKind.MODEL,
    retryable=True,
)

_MODEL_UNREACHABLE = Failure(
    headline="RepoLab could not reach the model service.",
    why="The request failed before an answer came back.",
    next_step="Check the network and that NEBIUS_API_KEY is set, then try again.",
    kind=FailureKind.MODEL,
    retryable=True,
)

_MODEL_MISSING = Failure(
    headline="No model is configured for this step.",
    why="RepoLab routes work to a model tier, and this tier has no model set.",
    next_step=(
        "Set MODEL_NANO, MODEL_SUPER and MODEL_ULTRA in .env. Everything "
        "deterministic still works without them."
    ),
    kind=FailureKind.MODEL,
)

_ENVIRONMENT_GONE = Failure(
    headline="The execution environment is not available.",
    why=("RepoLab runs repository code in a container and could not start or reach one."),
    next_step="Check that Docker is running, then try again.",
    kind=FailureKind.ENVIRONMENT,
    retryable=True,
)

_CHECKPOINT_GONE = Failure(
    headline="The prepared environment is no longer on this machine.",
    why=(
        "RepoLab had built this environment and remembered it, but the image it "
        "saved has since been removed -- `docker image prune` is enough to do it."
    ),
    next_step="Build it again. RepoLab has forgotten the stale one, so it will rebuild.",
    kind=FailureKind.BUILD,
    retryable=True,
)

_BUILD_EXHAUSTED = Failure(
    headline="RepoLab could not build a runnable environment.",
    why=(
        "Every rung of the ladder was tried -- the exact commit, the official "
        "release, a reduced build, a conceptual simulation -- and none passed "
        "its smoke test."
    ),
    next_step=(
        "The agent activity shows which rung failed and why. Docker needing more "
        "memory is the usual cause."
    ),
    kind=FailureKind.BUILD,
    retryable=True,
)

_BUILD_UNSUPPORTED = Failure(
    headline="RepoLab does not know how to build this repository yet.",
    why="No build strategy covers this repository's shape.",
    next_step=(
        "Spark data playgrounds and Cassandra are supported. Everything "
        "read-only -- the investigation, the architecture, the surfaces -- still "
        "works for any repository."
    ),
    kind=FailureKind.BUILD,
)

_RUN_FAILED = Failure(
    headline="The repository's own job failed when it ran.",
    why="The software started and exited with an error.",
    next_step="The log in the activity feed is the application's own output.",
    kind=FailureKind.EXECUTION,
    retryable=True,
)

_COMMIT_MOVED = Failure(
    headline="The repository has moved on since it was investigated.",
    why=(
        "Its default branch now points at a different commit, and measuring one "
        "commit while describing another would make every figure unattributable."
    ),
    next_step="Import the repository again to pick up the new commit.",
    kind=FailureKind.REPOSITORY,
)

_NOT_INVESTIGATED = Failure(
    headline="This repository has not been investigated yet.",
    why="RepoLab needs a manifest before it can build or teach anything.",
    next_step="Import the repository first, and wait for the investigation to finish.",
    kind=FailureKind.REPOSITORY,
)

_NO_ENVIRONMENT = Failure(
    headline="No environment has finished building for this repository.",
    why="A lesson and a terminal are written for an environment a learner can use.",
    next_step="Build an environment first, then try again.",
    kind=FailureKind.BUILD,
)

_RESEARCH_DOWN = Failure(
    headline="Official documentation could not be retrieved.",
    why="The research service did not answer.",
    next_step=(
        "Nothing is blocked: RepoLab's answers are grounded in repository code "
        "first, and will simply cite no external page."
    ),
    kind=FailureKind.RESEARCH,
    retryable=True,
)

_NOT_EQUIVALENT = Failure(
    headline="The experiment's runs did not start from equivalent state.",
    why=(
        "Every variant must begin from the same environment and the same data, "
        "or the comparison means nothing."
    ),
    next_step="Run the experiment again. RepoLab refuses the result rather than qualifying it.",
    kind=FailureKind.EXECUTION,
    retryable=True,
)

_NOT_APPROVED = Failure(
    headline="This experiment has not been approved.",
    why="Nothing that consumes compute runs before someone approves it.",
    next_step="Approve the plan on the experiment screen.",
    kind=FailureKind.INTERNAL,
)

_WORKER_RESTARTED = Failure(
    headline="The job stopped because RepoLab's worker restarted.",
    why="The work was interrupted part-way through; nothing was left half-saved.",
    next_step="Start it again.",
    kind=FailureKind.INTERNAL,
    retryable=True,
)

_UNKNOWN = Failure(
    headline="Something went wrong inside RepoLab.",
    why="This is not a failure RepoLab recognises, so it will not guess at the cause.",
    next_step="The technical detail below is the error itself. Trying again is reasonable.",
    kind=FailureKind.INTERNAL,
    retryable=True,
)

#: Exception class name -> the failure it means.
_BY_NAME: dict[str, Failure] = {
    "LLMTimeoutError": _MODEL_SLOW,
    "TimeoutError": _MODEL_SLOW,
    "SchemaValidationError": _MODEL_UNUSABLE,
    "EmptyResponseError": _MODEL_EMPTY,
    "LLMTransportError": _MODEL_UNREACHABLE,
    "ModelNotConfiguredError": _MODEL_MISSING,
    "CheckpointMissingError": _CHECKPOINT_GONE,
    "SandboxError": _ENVIRONMENT_GONE,
    "DockerUnavailableError": _ENVIRONMENT_GONE,
    "ExecutionError": _ENVIRONMENT_GONE,
    "BuildUnavailableError": _BUILD_EXHAUSTED,
    "NoCandidatesError": _BUILD_UNSUPPORTED,
    "UnsupportedRepositoryError": _BUILD_UNSUPPORTED,
    "NoEnvironmentError": _NO_ENVIRONMENT,
    "SparkRunFailed": _RUN_FAILED,
    "CommitMovedError": _COMMIT_MOVED,
    "ManifestMissingError": _NOT_INVESTIGATED,
    "InvalidRepoUrlError": Failure(
        headline="That does not look like a repository RepoLab can read.",
        why="RepoLab accepts public https repository URLs.",
        next_step="Check the URL and try again.",
        kind=FailureKind.REPOSITORY,
    ),
    "CloneError": Failure(
        headline="The repository could not be cloned.",
        why="Git could not fetch it -- it may be private, renamed, or unreachable.",
        next_step="Check the URL, and that the repository is public.",
        kind=FailureKind.REPOSITORY,
        retryable=True,
    ),
    "EquivalenceViolation": _NOT_EQUIVALENT,
    "ApprovalRequiredError": _NOT_APPROVED,
    "WorkerRestarted": _WORKER_RESTARTED,
    "TavilyError": _RESEARCH_DOWN,
    "InvalidAPIKeyError": Failure(
        headline="The research service rejected RepoLab's credentials.",
        why="TAVILY_API_KEY is missing or not valid.",
        next_step=(
            "Set it in .env, or leave it unset: repository code is RepoLab's "
            "primary evidence and works without it."
        ),
        kind=FailureKind.RESEARCH,
    ),
}


def explain(exc: BaseException) -> Failure:
    """What to tell a person about *exc*.

    Matched on the exception's class name and its bases, so a subclass of a
    known failure is explained as that failure rather than falling through to
    the generic case.

    An unrecognised exception is tried again against its ``__cause__``: a
    wrapper around a known failure should still produce the known failure's
    sentence rather than the generic one. The outer exception's own text is kept
    either way, because that is what actually happened.
    """
    known = _match(exc)
    if known is not None:
        return known.model_copy(update={"technical": _of(exc)})

    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        from_cause = _match(cause)
        if from_cause is not None:
            return from_cause.model_copy(update={"technical": _of(exc)})

    return _UNKNOWN.model_copy(update={"technical": _of(exc)})


def _match(exc: BaseException) -> Failure | None:
    """The mapping for *exc*'s own type or one of its bases."""
    for klass in type(exc).__mro__:
        known = _BY_NAME.get(klass.__name__)
        if known is not None:
            return known
    return None


def describe(exc: BaseException) -> str:
    """One line for a log or an event message, with the detail appended.

    What `f"{type(exc).__name__}: {exc}"` used to produce at these call sites,
    with a sentence in front of it that means something to a reader.
    """
    failure = explain(exc)
    return f"{failure.summary} ({failure.technical})"


__all__ = [
    "MAX_TECHNICAL",
    "Failure",
    "FailureKind",
    "describe",
    "explain",
]
