"""What happens when a build candidate fails.

CLAUDE.md sets the order, and it matters that it is this order:

    preserve the logs
    Nano summarises the relevant failure
    the Build Agent may diagnose and retry, within limits
    fall through to the next candidate

The logs come first and are never conditional. Everything after them is an
attempt to be *more* helpful, and a model being unavailable or wrong must not
cost a reader the raw output -- which is why the summary is an addition to the
attempt rather than a replacement for it.

"Never hide build failures" is the rule this module exists to keep.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from repolab.builds.models import BuildAttempt
from repolab.builds.plan import BuildPlan
from repolab.llm import LLMError, ModelRouter, Purpose

log = structlog.get_logger(__name__)

#: How much log to send. Enough for a stack trace or a dependency error, bounded
#: so a runaway build log cannot become an expensive prompt.
MAX_LOG_CHARS = 4000


class BuildLimits(BaseModel):
    """Bounds on how hard RepoLab tries to make one candidate work."""

    max_attempts: int = Field(default=2, ge=1)
    """Attempts per candidate, including the first.

    Two by default: one retry is enough to recover from a diagnosable mistake,
    and more than that is usually a candidate that should fall through to the
    next rung rather than keep spending.
    """

    max_concurrent: int = Field(default=3, gt=0)
    """Candidates built at once. The expensive one does not block the cheap ones."""

    memory_budget_gb: float = Field(default=6.0, gt=0)
    """Total container memory the ladder may have in flight.

    **Count is the wrong limit for build concurrency; memory is the real one.**
    Measured: two 4 GB Cassandra nodes on a 7.7 GB Docker killed the first one
    the moment the second started -- the official release failed at 48s having
    been healthy, with no error of its own, because something else took its
    memory.

    The default leaves headroom below a typical 8 GB Docker for the daemon and
    for whatever else is already running (Postgres and Redis, here).
    """

    candidate_timeout_seconds: float = Field(default=3600.0, gt=0)
    """Wall clock for one candidate, after which it is abandoned and falls through."""


class FailureSummary(BaseModel):
    """Nano's reading of why a build failed."""

    headline: str
    """One sentence, user-facing."""

    likely_cause: str = ""
    log_excerpt: str = ""
    """The lines the summary is about, kept so a reader can check it."""

    produced_by: str = "nano"


class Diagnosis(BaseModel):
    """Super's proposal for what to do about a failure."""

    cause: str
    retry_worthwhile: bool = False
    """Whether another attempt could plausibly succeed."""

    suggestion: str = ""
    """What to change. Advisory -- nothing here is executed automatically."""

    produced_by: str = "super"


def failing_output(attempt: BuildAttempt) -> str:
    """The log that matters, from whichever part of the attempt failed.

    Preserved on the attempt either way; this just finds the relevant piece so a
    summary is about the failure rather than about the build's first step.
    """
    if attempt.smoke is not None and not attempt.smoke.passed:
        return f"{attempt.smoke.stdout}\n{attempt.smoke.stderr}".strip()[-MAX_LOG_CHARS:]

    failed = attempt.failed_step
    if failed is not None:
        return failed.output_tail[-MAX_LOG_CHARS:]

    return (attempt.error or "")[-MAX_LOG_CHARS:]


def describe_failure(attempt: BuildAttempt) -> str:
    """A deterministic one-line reason, with no model involved.

    Always available, which is what makes the model-written summary optional.
    """
    if attempt.error:
        return attempt.error
    failed = attempt.failed_step
    if failed is not None:
        return f"step {failed.name!r} exited {failed.exit_code}"
    if attempt.smoke is not None and not attempt.smoke.passed:
        return f"smoke test failed: {attempt.smoke.reason}"
    return "unknown failure"


class _Summary(BaseModel):
    headline: str
    likely_cause: str


async def summarise(attempt: BuildAttempt, *, router: ModelRouter | None = None) -> FailureSummary:
    """Summarise a failed attempt, with Nano when one is available.

    Falls back to the deterministic description rather than to nothing: the
    point of this call is that a reader learns why the build failed, and a model
    being unavailable is not a reason to stop telling them.
    """
    excerpt = failing_output(attempt)
    deterministic = describe_failure(attempt)

    if router is None or not excerpt.strip():
        return FailureSummary(
            headline=deterministic, log_excerpt=excerpt, produced_by="deterministic"
        )

    try:
        completion = await router.nano(
            purpose=Purpose.LOG_PARSING,
            schema=_Summary,
            system=(
                "You summarise build failures. One sentence on what went wrong, and "
                "one on the likely cause. Quote nothing you were not given, and do "
                "not speculate beyond the log."
            ),
            prompt=f"The build step failed with:\n\n{excerpt}",
            max_tokens=512,
        )
    except LLMError as exc:
        log.warning("builds.summary_failed", error=str(exc))
        return FailureSummary(
            headline=deterministic, log_excerpt=excerpt, produced_by="deterministic"
        )

    return FailureSummary(
        headline=completion.value.headline,
        likely_cause=completion.value.likely_cause,
        log_excerpt=excerpt,
    )


async def diagnose(
    attempt: BuildAttempt, plan: BuildPlan, *, router: ModelRouter | None = None
) -> Diagnosis:
    """Ask Super whether another attempt is worth making.

    Advisory only. Nothing it returns is executed: a model deciding to rerun a
    build is a model spending compute, and the limit on that lives in
    :class:`BuildLimits`, not in the model's answer.

    Without a router, the answer is no -- RepoLab does not retry on a guess.
    """
    if router is None:
        return Diagnosis(
            cause=describe_failure(attempt),
            retry_worthwhile=False,
            suggestion="no model configured, so the failure was not diagnosed",
            produced_by="deterministic",
        )

    try:
        completion = await router.super(
            purpose=Purpose.BUILD_DIAGNOSIS,
            schema=Diagnosis,
            system=(
                "You diagnose software build failures. Decide whether retrying the "
                "same build could plausibly succeed -- a transient network error "
                "might, a missing dependency or a compile error will not. Be "
                "conservative: a pointless retry costs minutes."
            ),
            prompt=(
                f"Base image: {plan.base_image}\n"
                f"Failed: {describe_failure(attempt)}\n\n"
                f"Log:\n{failing_output(attempt)}"
            ),
            max_tokens=512,
        )
    except LLMError as exc:
        log.warning("builds.diagnosis_failed", error=str(exc))
        return Diagnosis(
            cause=describe_failure(attempt), retry_worthwhile=False, produced_by="deterministic"
        )

    return completion.value
