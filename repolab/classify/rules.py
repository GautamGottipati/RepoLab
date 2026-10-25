"""Deterministic classification.

Repository evidence decides the answer; a model is consulted only when these
rules cannot (see :mod:`repolab.classify.ambiguity`). The output is one or more
interaction surfaces -- never exactly one type forced onto the repository -- each
carrying its confidence, the files that justify it, and a reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from repolab.classify.ambiguity import is_ambiguous
from repolab.classify.signals import (
    CORROBORATION_BONUS,
    ENTRYPOINT_SURFACES,
    EXECUTABLE_SIGNAL_KINDS,
    FRAMEWORK_SURFACES,
    HYBRID_CONFIDENCE,
    MAX_CONFIDENCE,
    SIGNAL_SURFACES,
    STRONG,
    precedence_rank,
)
from repolab.evidence.models import CodeReference
from repolab.manifest.models import (
    Classification,
    EntryPoint,
    EntryPointKind,
    InteractionSurface,
    Service,
    SurfaceType,
)

if TYPE_CHECKING:
    # Imported for annotations only: the classifier consumes findings but must not
    # depend on the ingestion package at runtime, or the two form an import cycle.
    from repolab.ingest.detectors.base import DetectorRun, Finding

#: Presentation for each surface. The name is what a learner sees.
SURFACE_NAMES: dict[SurfaceType, str] = {
    SurfaceType.DATA_PLAYGROUND: "Spark data playground",
    SurfaceType.WEB: "Web application",
    SurfaceType.API: "HTTP API",
    SurfaceType.CLI: "Command line interface",
    SurfaceType.NOTEBOOK: "Notebook / library exploration",
    SurfaceType.TERMINAL: "Terminal",
}

#: Evidence cited per surface. Enough to justify the claim without flooding the UI.
MAX_EVIDENCE_PER_SURFACE = 6


class SurfaceScore(BaseModel):
    """Working state for one candidate surface."""

    type: SurfaceType
    score: float = 0.0
    kinds: dict[str, float] = Field(default_factory=dict)
    """Weight contributed by each distinct signal kind."""

    cited: list[tuple[float, CodeReference]] = Field(default_factory=list)
    """Every candidate reference with the weight of the signal that produced it."""

    corroborated_by: list[str] = Field(default_factory=list)

    def contribute(self, kind: str, weight: float, references: list[CodeReference]) -> None:
        """Record *kind* as supporting this surface.

        A kind contributes only once, at its highest observed weight, so repeated
        hits of the same weak signal cannot outweigh one strong signal.
        """
        self.kinds[kind] = max(self.kinds.get(kind, 0.0), weight)
        for ref in references:
            if not any(
                existing.path == ref.path and existing.start_line == ref.start_line
                for _, existing in self.cited
            ):
                self.cited.append((weight, ref))

    @property
    def evidence(self) -> list[CodeReference]:
        """The strongest evidence first, capped.

        Ordering by the contributing signal's weight matters: on a large
        repository the best reference -- Cassandra's ``bin/cqlsh``, say -- would
        otherwise be crowded out by whichever weak signal happened to be scanned
        first.
        """
        ranked = sorted(self.cited, key=lambda item: item[0], reverse=True)
        return [ref for _, ref in ranked[:MAX_EVIDENCE_PER_SURFACE]]

    @property
    def signal_score(self) -> float:
        return sum(self.kinds.values())

    @property
    def has_strong_signal(self) -> bool:
        return any(weight >= STRONG for weight in self.kinds.values())


def _collect(
    run: DetectorRun, launchers: frozenset[str] = frozenset()
) -> dict[SurfaceType, SurfaceScore]:
    """Group every citable signal under the surface it supports.

    *launchers* are files a server start script runs (``nodemon ./bin/www``). An
    executable under ``bin/`` that exists to start the web server is how the
    server is launched, not a command line the project ships.
    """
    scores: dict[SurfaceType, SurfaceScore] = {}

    def score_for(surface: SurfaceType) -> SurfaceScore:
        return scores.setdefault(surface, SurfaceScore(type=surface))

    for finding in run.findings:
        if not finding.is_supported:
            # A signal RepoLab cannot cite is not evidence, so it does not count.
            continue

        references = finding.references
        if finding.kind == "bin_executable" and launchers:
            references = [ref for ref in references if ref.path not in launchers]
            if not references:
                continue

        mapped = SIGNAL_SURFACES.get(finding.kind)
        if mapped is not None:
            surface, weight = mapped
            score_for(surface).contribute(finding.kind, weight, references)
            continue

        if finding.kind == "framework":
            for surface, weight in FRAMEWORK_SURFACES.get(finding.value, ()):
                score_for(surface).contribute(
                    f"framework:{finding.value}", weight, finding.references
                )

    return scores


def server_launchers(entrypoints: list[EntryPoint]) -> frozenset[str]:
    """Repository files that a web-server entry point's command runs."""
    found: set[str] = set()
    for entrypoint in entrypoints:
        if entrypoint.kind is not EntryPointKind.WEB_SERVER:
            continue
        path = entrypoint.path or ""
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        for token in entrypoint.command.split():
            token = token.strip("'\"")
            if "/" not in token or token.startswith("-"):
                continue
            relative = token.removeprefix("./")
            found.add(f"{base}/{relative}" if base else relative)
    return frozenset(found)


def _corroborate(
    scores: dict[SurfaceType, SurfaceScore],
    entrypoints: list[EntryPoint],
    services: list[Service],
) -> None:
    """Add a bonus where an entry point or service backs up an existing signal.

    Corroboration never *creates* a surface: something must already have been
    detected in the code or dependencies.
    """
    for entrypoint in entrypoints:
        for surface in ENTRYPOINT_SURFACES.get(entrypoint.kind.value, ()):
            score = scores.get(surface)
            if score is not None and entrypoint.kind.value not in score.corroborated_by:
                score.corroborated_by.append(entrypoint.kind.value)

    if services:
        # Services imply something long-running to talk to, which supports a
        # session-based surface rather than a one-shot command.
        for surface in (SurfaceType.CLI, SurfaceType.API, SurfaceType.WEB):
            score = scores.get(surface)
            if score is not None and "services" not in score.corroborated_by:
                score.corroborated_by.append("services")


def _apply_compound_rules(
    scores: dict[SurfaceType, SurfaceScore], run: DetectorRun
) -> dict[SurfaceType, str]:
    """Rules that depend on a *combination* of signals.

    Returns any surface-specific name overrides.
    """
    overrides: dict[SurfaceType, str] = {}
    kinds = {f.kind for f in run.findings if f.is_supported}

    # A server you can also open an interactive session against is a CLI
    # playground, not merely a command: that is the Cassandra shape.
    if {"interactive_shell", "server_daemon"} <= kinds:
        cli = scores.get(SurfaceType.CLI)
        if cli is not None:
            cli.corroborated_by.append("server+shell")
            overrides[SurfaceType.CLI] = "CLI playground (interactive shell against a server)"

    # A library with nothing runnable is explored by writing code against it.
    if "library_package" in kinds and not (kinds & EXECUTABLE_SIGNAL_KINDS):
        notebook = scores.setdefault(SurfaceType.NOTEBOOK, SurfaceScore(type=SurfaceType.NOTEBOOK))
        notebook.corroborated_by.append("library-without-executable")
        overrides[SurfaceType.NOTEBOOK] = "Notebook / library exploration"

    return overrides


def _reason(score: SurfaceScore) -> str:
    """A one-line explanation naming the signals responsible."""
    ranked = sorted(score.kinds.items(), key=lambda item: item[1], reverse=True)
    named = ", ".join(kind for kind, _ in ranked[:4]) or "no direct signal"
    reason = f"detected from {named}"
    if score.corroborated_by:
        reason += f"; corroborated by {', '.join(sorted(set(score.corroborated_by)))}"
    return reason


def _finalize(
    scores: dict[SurfaceType, SurfaceScore], overrides: dict[SurfaceType, str]
) -> list[InteractionSurface]:
    surfaces: list[InteractionSurface] = []

    for score in scores.values():
        bonus = CORROBORATION_BONUS * len(set(score.corroborated_by))
        score.score = score.signal_score + bonus
        if score.score <= 0:
            continue
        surfaces.append(
            InteractionSurface(
                type=score.type,
                name=overrides.get(score.type, SURFACE_NAMES[score.type]),
                description=overrides.get(score.type),
                confidence=min(MAX_CONFIDENCE, round(score.score, 2)),
                reason=_reason(score),
                evidence=score.evidence,
            )
        )

    # Highest confidence first; a genuine tie is broken by which experience is
    # the richer thing to learn, never by name.
    return sorted(surfaces, key=lambda s: (-s.confidence, precedence_rank(s.type), s.type.value))


def _terminal_fallback() -> InteractionSurface:
    """What RepoLab offers when it recognised nothing specific."""
    return InteractionSurface(
        type=SurfaceType.TERMINAL,
        name=SURFACE_NAMES[SurfaceType.TERMINAL],
        description="No specific surface detected; a shell is the fallback.",
        confidence=0.3,
        reason="no interaction-surface signals were found",
        primary=True,
    )


def classify_deterministic(
    run: DetectorRun,
    *,
    entrypoints: list[EntryPoint] | None = None,
    services: list[Service] | None = None,
) -> Classification:
    """Classify a repository from its detected signals alone.

    Produces one or more surfaces and recommends a primary experience. Never
    consults a model.
    """
    scores = _collect(run, server_launchers(entrypoints or []))
    _corroborate(scores, entrypoints or [], services or [])
    overrides = _apply_compound_rules(scores, run)
    surfaces = _finalize(scores, overrides)

    if not surfaces:
        fallback = _terminal_fallback()
        return Classification(
            surfaces=[fallback],
            primary_type=fallback.type,
            rationale=(
                "No interaction-surface signals were found, so a terminal is "
                "offered as the fallback."
            ),
            ambiguous=True,
        )

    primary = surfaces[0]
    primary.primary = True

    substantial = [s for s in surfaces if s.confidence >= HYBRID_CONFIDENCE]
    return Classification(
        surfaces=surfaces,
        primary_type=primary.type,
        rationale=_primary_rationale(primary, surfaces),
        hybrid=len(substantial) >= 2,
        ambiguous=is_ambiguous(surfaces),
    )


def _primary_rationale(primary: InteractionSurface, surfaces: list[InteractionSurface]) -> str:
    """Why this primary was chosen over the alternatives."""
    others = [s for s in surfaces if s is not primary]
    if not others:
        return (
            f"{primary.type.value} is the only surface detected "
            f"(confidence {primary.confidence}); {primary.reason}."
        )

    runner_up = others[0]
    if primary.confidence > runner_up.confidence:
        return (
            f"{primary.type.value} scored highest ({primary.confidence} vs "
            f"{runner_up.confidence} for {runner_up.type.value}); {primary.reason}."
        )
    return (
        f"{primary.type.value} and {runner_up.type.value} tied at "
        f"{primary.confidence}; {primary.type.value} was preferred as the richer "
        f"experience to learn."
    )


def merge_evidence(
    findings: list[Finding], limit: int = MAX_EVIDENCE_PER_SURFACE
) -> list[CodeReference]:
    """Deduplicated references from *findings*, capped at *limit*."""
    merged: dict[tuple[str, int | None], CodeReference] = {}
    for finding in findings:
        for ref in finding.references:
            merged.setdefault((ref.path, ref.start_line), ref)
    return list(merged.values())[:limit]
