"""The fidelity fallback ladder.

    repo + commit
         |
         v
    exact cached artifact?  ---->  the highest-fidelity ready one wins
         |
         no
         v
    run the candidates, concurrently where it helps
         |
         v
    serve the highest-fidelity candidate that PASSED ITS SMOKE TEST
         |
         v
    a better one finishes later  ---->  offer an upgrade (never switch)

Two things here are easy to get subtly wrong, and both are the point of the
phase:

**Served is not "first to finish".** The cheap candidates finish first by
design. Serving one of them is right -- a learner should not wait twenty minutes
for a compile -- but the thing *served* is always the best that has passed so
far, and the moment something better passes it becomes an offer.

**Passing is not finishing.** A candidate that built and then failed its smoke
test is not a candidate. It never becomes ready, it is never served, it is never
cached, and the ladder falls through to the next rung with its logs preserved.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from repolab.analytics import Analytics, AnalyticsEventType, NullAnalyticsWriter
from repolab.builds.cache import BuildCache, NullBuildCache
from repolab.builds.candidates import BuildCandidate
from repolab.builds.diagnosis import (
    BuildLimits,
    Diagnosis,
    FailureSummary,
    describe_failure,
    diagnose,
    summarise,
)
from repolab.builds.models import (
    BuildArtifact,
    BuildRequest,
    BuildSource,
    BuildUnavailableError,
)
from repolab.events import EventEmitter, EventType, NullEmitter
from repolab.llm import ModelRouter
from repolab.manifest.models import BuildKind, RepoManifest
from repolab.sandbox.provider import SandboxProvider

log = structlog.get_logger(__name__)

STEP_LABEL = "build"


class CandidateOutcome(BaseModel):
    """What happened to one rung of the ladder."""

    kind: BuildKind
    artifact: BuildArtifact | None = None
    attempts: int = 0
    seconds: float = 0.0

    failure: str = ""
    summary: FailureSummary | None = None
    """Nano's reading of the failure, when there was one."""

    diagnosis: Diagnosis | None = None

    @property
    def ready(self) -> bool:
        return self.artifact is not None and self.artifact.ready

    def describe(self) -> str:
        if self.ready and self.artifact is not None:
            return (
                f"{self.kind.value}: ready in {self.seconds:.0f}s ({self.artifact.fidelity.value})"
            )
        return f"{self.kind.value}: failed after {self.seconds:.0f}s — {self.failure}"


class LadderResult(BaseModel):
    """Everything the ladder produced, served or not."""

    repo_url: str
    commit_sha: str
    served: BuildArtifact | None = None
    outcomes: list[CandidateOutcome] = Field(default_factory=list)
    from_cache: bool = False

    @property
    def ready(self) -> list[BuildArtifact]:
        """Every candidate that passed, best first."""
        found = [outcome.artifact for outcome in self.outcomes if outcome.ready]
        return sorted(
            (artifact for artifact in found if artifact is not None),
            key=lambda artifact: artifact.fidelity_rank,
            reverse=True,
        )

    @property
    def failures(self) -> list[CandidateOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.ready]

    def summary(self) -> str:
        lines = [outcome.describe() for outcome in self.outcomes]
        if self.served is not None:
            lines.append(f"serving {self.served.summary()}")
        return "\n".join(lines)


#: Called when a candidate becomes ready, best-first ordering not guaranteed --
#: that is the caller's cue to consider an upgrade.
ReadyCallback = Callable[[BuildArtifact], Awaitable[None]]

#: Assumed memory for a candidate whose plan does not say.
DEFAULT_CANDIDATE_GB = 1.0


def _memory_gb(candidate: BuildCandidate) -> float:
    """How much memory this candidate will ask for.

    A Mini Lab builds nothing and therefore costs nothing.
    """
    if candidate.plan is None:
        return 0.0
    return _parse_memory(candidate.plan.memory)


def _parse_memory(value: str | None) -> float:
    """Docker's memory notation (``4g``, ``512m``) as gigabytes."""
    if not value:
        return DEFAULT_CANDIDATE_GB
    text = value.strip().lower().rstrip("ib")
    try:
        if text.endswith("g"):
            return float(text[:-1])
        if text.endswith("m"):
            return float(text[:-1]) / 1024
        if text.endswith("k"):
            return float(text[:-1]) / 1024 / 1024
        return float(text) / 1024**3
    except ValueError:
        return DEFAULT_CANDIDATE_GB


class _MemoryBudget:
    """Admits candidates while their combined memory fits.

    A candidate larger than the whole budget still runs -- alone. Refusing it
    would mean never building the thing at all, which is worse than running it
    with nothing beside it.
    """

    def __init__(self, total_gb: float, max_concurrent: int) -> None:
        self._total = total_gb
        self._max = max_concurrent
        self._used = 0.0
        self._running = 0
        self._free = asyncio.Condition()

    def admit(self, gb: float):
        return _Admission(self, gb)


class _Admission:
    """Holds a slice of the budget for the duration of one candidate."""

    def __init__(self, budget: _MemoryBudget, gb: float) -> None:
        self._budget = budget
        self._gb = gb

    async def __aenter__(self) -> None:
        budget = self._budget
        async with budget._free:
            while True:
                fits = budget._used + self._gb <= budget._total or budget._running == 0
                if fits and budget._running < budget._max:
                    budget._used += self._gb
                    budget._running += 1
                    return
                await budget._free.wait()

    async def __aexit__(self, *exc: object) -> None:
        budget = self._budget
        async with budget._free:
            budget._used -= self._gb
            budget._running -= 1
            budget._free.notify_all()


class BuildLadder:
    """Resolves a repository commit through the full fidelity ladder."""

    def __init__(
        self,
        provider: SandboxProvider,
        *,
        cache: BuildCache | None = None,
        emitter: EventEmitter | None = None,
        router: ModelRouter | None = None,
        limits: BuildLimits | None = None,
        analytics: Analytics | None = None,
    ) -> None:
        self._provider = provider
        # Explicit None checks: InMemoryBuildCache defines __len__, so an *empty*
        # cache is falsy and `cache or default` would silently discard it.
        self._cache = cache if cache is not None else NullBuildCache()
        self._emitter = emitter if emitter is not None else NullEmitter()
        self._router = router
        self._limits = limits if limits is not None else BuildLimits()

        # Recorded per candidate. The build cache keeps successful builds only,
        # so the `builds` table can never report a failure -- these events are
        # the only thing from which a build success rate can be computed.
        self._analytics = analytics if analytics is not None else Analytics(NullAnalyticsWriter())

    async def resolve(
        self,
        request: BuildRequest,
        candidates: list[BuildCandidate],
        repository_root: Path,
        *,
        manifest: RepoManifest | None = None,
        on_ready: ReadyCallback | None = None,
    ) -> LadderResult:
        """Work the ladder until something is ready, or nothing is left.

        Raises:
            BuildUnavailableError: every rung failed. The error names each one,
                because "the build failed" without saying which four things were
                tried is not a useful thing to tell anyone.
        """
        result = LadderResult(repo_url=request.repo_url, commit_sha=request.commit_sha)

        if request.use_cache:
            cached = self._best_cached(request)
            if cached is not None:
                self._emitter.emit(
                    EventType.STEP_DONE,
                    f"using the cached {cached.fidelity.value} build",
                    step=STEP_LABEL,
                    data={"source": BuildSource.CACHE.value, **cached.declaration()},
                )
                log.info("builds.cache_hit", repo=request.repo_url)
                result.served = cached
                result.from_cache = True
                return result

        ordered = sorted(candidates, key=lambda candidate: candidate.rank, reverse=True)
        self._announce(ordered)

        outcomes = await self._race(request, ordered, repository_root, manifest, on_ready)
        result.outcomes = outcomes
        result.served = self._best(outcomes)

        if result.served is None:
            reasons = (
                "; ".join(f"{outcome.kind.value}: {outcome.failure}" for outcome in outcomes)
                or "no candidates were produced"
            )
            self._emitter.emit(
                EventType.FAILED,
                f"every build candidate failed: {reasons}",
                step=STEP_LABEL,
                data={"candidates": len(outcomes)},
            )
            raise BuildUnavailableError(request.repo_url, reasons)

        self._emitter.emit(
            EventType.STEP_DONE,
            f"serving the {result.served.fidelity.value} build",
            step=STEP_LABEL,
            data=result.served.declaration(),
        )
        return result

    # -- the race ---------------------------------------------------------

    async def _race(
        self,
        request: BuildRequest,
        candidates: list[BuildCandidate],
        repository_root: Path,
        manifest: RepoManifest | None,
        on_ready: ReadyCallback | None,
    ) -> list[CandidateOutcome]:
        """Build the candidates, bounded, reporting each as it lands.

        Concurrent because that is what makes the ladder useful: the official
        release finishes in a minute while the source build compiles, and the
        learner starts working immediately rather than waiting for the best
        possible environment to be the first one tried.

        Bounded by **memory**, not just by count. Two candidates that each want
        4 GB do not fit on a machine with 7.7 GB, and the failure that produces
        is the worst kind: a healthy container killed by something else, with no
        error of its own.
        """
        budget = _MemoryBudget(self._limits.memory_budget_gb, self._limits.max_concurrent)

        # Started cheapest first, reported and selected best first.
        #
        # They are different orders for different reasons. With memory to spare
        # everything starts at once and the order is irrelevant; when the budget
        # forces a queue it matters a great deal, because whatever starts first
        # is what the learner gets to work on first -- and waiting twenty
        # minutes for a compile before being allowed to type anything is the
        # outcome this ladder exists to avoid.
        starting = sorted(candidates, key=lambda candidate: candidate.rank)

        async def run(candidate: BuildCandidate) -> CandidateOutcome:
            async with budget.admit(_memory_gb(candidate)):
                outcome = await self._build_candidate(request, candidate, repository_root, manifest)
            if outcome.ready and outcome.artifact is not None:
                self._cache.put(outcome.artifact)
                if on_ready is not None:
                    await on_ready(outcome.artifact)
            return outcome

        tasks = [asyncio.create_task(run(candidate)) for candidate in starting]
        outcomes: list[CandidateOutcome] = []

        # Gathered rather than awaited in order, so a slow candidate does not
        # hold back the reporting of a fast one.
        for finished in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(finished, BaseException):
                log.warning("builds.candidate_crashed", error=str(finished))
                continue
            outcomes.append(finished)

        # Reported in ladder order regardless of the order they finished in.
        order = {candidate.kind: index for index, candidate in enumerate(candidates)}
        outcomes.sort(key=lambda outcome: order.get(outcome.kind, len(order)))
        return outcomes

    async def _build_candidate(
        self,
        request: BuildRequest,
        candidate: BuildCandidate,
        repository_root: Path,
        manifest: RepoManifest | None,
    ) -> CandidateOutcome:
        """Produce one candidate, retrying within the limit."""
        outcome = CandidateOutcome(kind=candidate.kind)
        started = time.perf_counter()

        self._analytics.record(
            AnalyticsEventType.BUILD_STARTED,
            kind=candidate.kind.value,
            expected_fidelity=candidate.expected_fidelity.value,
        )
        self._emitter.emit(
            EventType.STEP_STARTED,
            f"{candidate.kind.value}: {candidate.why}",
            step=STEP_LABEL,
            data={
                "kind": candidate.kind.value,
                "expected_fidelity": candidate.expected_fidelity.value,
                "demo_delay_seconds": candidate.demo_delay_seconds,
            },
        )

        if candidate.demo_delay_seconds > 0:
            # Disclosed, never silent: a deliberately slowed build must not read
            # as a slow one.
            self._emitter.emit(
                EventType.LOG_LINE,
                (
                    f"{candidate.kind.value}: delayed by "
                    f"{candidate.demo_delay_seconds:.0f}s for demonstration"
                ),
                step=STEP_LABEL,
                data={"kind": candidate.kind.value, "demo_delay": True},
            )
            await asyncio.sleep(candidate.demo_delay_seconds)

        try:
            artifact = await asyncio.wait_for(
                self._attempt_with_retries(request, candidate, repository_root, manifest, outcome),
                timeout=self._limits.candidate_timeout_seconds,
            )
        except TimeoutError:
            outcome.failure = f"abandoned after {self._limits.candidate_timeout_seconds:.0f}s"
            outcome.seconds = round(time.perf_counter() - started, 2)
            self._analytics.record(
                AnalyticsEventType.BUILD_FAILED,
                kind=candidate.kind.value,
                reason="timed out",
                attempts=outcome.attempts,
            )
            self._emitter.emit(
                EventType.LOG_LINE,
                f"{candidate.kind.value}: {outcome.failure}",
                step=STEP_LABEL,
                data={"kind": candidate.kind.value, "timed_out": True},
            )
            return outcome

        outcome.artifact = artifact
        outcome.seconds = round(time.perf_counter() - started, 2)

        if artifact is not None and artifact.ready:
            artifact.demo_delay_seconds = candidate.demo_delay_seconds
            if outcome.attempts > 1:
                # A candidate that failed and then passed. The Build Agent's
                # recovery rate is computed from exactly this.
                self._analytics.record(
                    AnalyticsEventType.BUILD_RECOVERED,
                    kind=candidate.kind.value,
                    attempts=outcome.attempts,
                    fidelity=artifact.fidelity.value,
                )
            self._emitter.emit(
                EventType.PARTIAL_RESULT,
                f"{candidate.kind.value} is ready ({artifact.fidelity.value})",
                step=STEP_LABEL,
                data={"seconds": outcome.seconds, **artifact.declaration()},
            )
        else:
            self._analytics.record(
                AnalyticsEventType.BUILD_FAILED,
                kind=candidate.kind.value,
                reason=outcome.failure or "no artifact produced",
                attempts=outcome.attempts,
            )
        return outcome

    async def _attempt_with_retries(
        self,
        request: BuildRequest,
        candidate: BuildCandidate,
        repository_root: Path,
        manifest: RepoManifest | None,
        outcome: CandidateOutcome,
    ) -> BuildArtifact | None:
        """Attempt a candidate, retrying only where a diagnosis says it is worth it."""
        artifact: BuildArtifact | None = None

        for attempt_number in range(1, self._limits.max_attempts + 1):
            outcome.attempts = attempt_number
            artifact = await self._produce(request, candidate, repository_root, manifest)

            if artifact is not None and artifact.ready:
                return artifact

            last = artifact.attempts[-1] if artifact and artifact.attempts else None
            if last is None:
                outcome.failure = "no attempt was made"
                return artifact

            # The logs are already on the attempt. Everything below adds to them.
            outcome.failure = describe_failure(last)
            outcome.summary = await summarise(last, router=self._router)
            self._emitter.emit(
                EventType.LOG_LINE,
                f"{candidate.kind.value} failed: {outcome.summary.headline}",
                step=STEP_LABEL,
                data={
                    "kind": candidate.kind.value,
                    "attempt": attempt_number,
                    "reason": outcome.failure,
                    "summarised_by": outcome.summary.produced_by,
                },
            )

            if attempt_number >= self._limits.max_attempts or candidate.plan is None:
                break

            outcome.diagnosis = await diagnose(last, candidate.plan, router=self._router)
            if not outcome.diagnosis.retry_worthwhile:
                break

            self._emitter.emit(
                EventType.PROGRESS,
                f"retrying {candidate.kind.value}: {outcome.diagnosis.suggestion[:160]}",
                step=STEP_LABEL,
                data={"kind": candidate.kind.value, "attempt": attempt_number + 1},
            )

        return artifact

    async def _produce(
        self,
        request: BuildRequest,
        candidate: BuildCandidate,
        repository_root: Path,
        manifest: RepoManifest | None,
    ) -> BuildArtifact | None:
        """Run one candidate once."""
        if candidate.kind is BuildKind.MINI_LAB:
            from repolab.builds.minilab_build import build_mini_lab

            if manifest is None:
                return None
            return await build_mini_lab(manifest, router=self._router)

        if candidate.plan is None:
            return None

        from repolab.builds.resolver import BuildResolver

        resolver = BuildResolver(self._provider, cache=NullBuildCache(), emitter=self._emitter)
        return await resolver.build(request, candidate.plan, repository_root, kind=candidate.kind)

    # -- selection --------------------------------------------------------

    def _best_cached(self, request: BuildRequest) -> BuildArtifact | None:
        """The highest-fidelity ready artifact already cached for this commit."""
        cached = self._cache.get_all(request.repo_url, request.commit_sha)
        ready = [artifact for artifact in cached if artifact.ready]
        if not ready:
            return None
        return max(ready, key=lambda artifact: artifact.fidelity_rank)

    @staticmethod
    def _best(outcomes: list[CandidateOutcome]) -> BuildArtifact | None:
        """The highest-fidelity candidate that passed its smoke test.

        Not the first to finish. A build that has not passed is not a candidate
        at all, so this cannot accidentally serve one.
        """
        ready = [
            outcome.artifact
            for outcome in outcomes
            if outcome.ready and outcome.artifact is not None
        ]
        if not ready:
            return None
        return max(ready, key=lambda artifact: artifact.fidelity_rank)

    def _announce(self, candidates: list[BuildCandidate]) -> None:
        self._emitter.emit(
            EventType.AGENT_STATUS,
            f"resolving a build from {len(candidates)} candidate(s)",
            step=STEP_LABEL,
            data={
                "candidates": [
                    {
                        "kind": candidate.kind.value,
                        "expected_fidelity": candidate.expected_fidelity.value,
                        "why": candidate.why,
                    }
                    for candidate in candidates
                ]
            },
        )
