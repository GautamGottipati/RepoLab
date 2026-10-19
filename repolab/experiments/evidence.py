"""Assembling the evidence an experiment produced.

The bundle is what the UI shows next to the explanation and what the explanation
is checked against, so it has to contain the real thing: measurements tagged by
variant, the repository lines the investigation found, excerpts of the actual
event logs, and the official documentation that was retrieved.

``simulated`` is always False here. An experiment that executed produces measured
evidence by definition, and the ``EvidenceBundle`` validator already forbids the
combination of ``simulated`` with measurements.
"""

from __future__ import annotations

from pathlib import Path

from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
    Measurement,
)
from repolab.experiments.models import VariantRun

#: Lines of event log quoted per variant. Enough to show the header and the first
#: events; the whole log stays on disk and is referenced by hash.
EXCERPT_LINES = 4

#: How many variants contribute a log excerpt. The baseline and the extremes are
#: what a reader checks; quoting all of them would be noise.
MAX_LOG_VARIANTS = 3


def log_references(runs: list[VariantRun], *, baseline_value: int) -> list[LogReference]:
    """Excerpts of the raw event logs, for the baseline and the extreme variants.

    Reads from the preserved files on disk. A log that has since been removed is
    still referenced by path and hash, with the excerpt saying so rather than
    being silently empty.
    """
    if not runs:
        return []

    by_value: dict[int, VariantRun] = {}
    for run in runs:
        by_value.setdefault(run.value, run)

    values = sorted(by_value)
    chosen = {values[0], values[-1]}
    if baseline_value in by_value:
        chosen.add(baseline_value)

    references: list[LogReference] = []
    for value in sorted(chosen)[:MAX_LOG_VARIANTS]:
        run = by_value[value]
        path = Path(run.event_log_path)
        if path.is_file():
            lines = path.read_text(errors="replace").splitlines()[:EXCERPT_LINES]
            excerpt = "\n".join(lines)[:2000]
        else:
            excerpt = f"(preserved event log no longer on disk; sha256 {run.event_log_sha256})"

        references.append(
            LogReference(
                source=f"spark event log, {run.variant_id} ({path.name})",
                excerpt=excerpt,
            )
        )
    return references


def all_measurements(runs: list[VariantRun]) -> list[Measurement]:
    """Every measurement from every run, each tagged with its variant.

    Tagging is what lets a figure be traced to the run that produced it rather
    than to the experiment as a whole.
    """
    measurements: list[Measurement] = []
    for run in runs:
        measurements.extend(run.to_measurements())
    return measurements


def build_evidence(
    runs: list[VariantRun],
    *,
    baseline_value: int,
    code_references: list[CodeReference] | None = None,
    documentation: list[DocumentationReference] | None = None,
) -> EvidenceBundle:
    """The experiment's evidence bundle."""
    return EvidenceBundle(
        measurements=all_measurements(runs),
        code_references=list(code_references) if code_references is not None else [],
        logs=log_references(runs, baseline_value=baseline_value),
        documentation=list(documentation) if documentation is not None else [],
        simulated=False,
    )
