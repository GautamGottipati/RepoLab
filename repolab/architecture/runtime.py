"""Measurements from real runs, overlaid on the architecture.

Nothing here is computed from the graph or asked of a model. Each value comes
from a stored run -- a Spark run's event-log measurements, a CLI experiment's
recorded statements -- and carries the id of the run it came from. With no run
recorded, the overlay is empty, and the page shows nothing measured.

Attaching a measurement to a component is also evidence-based: a Spark stage is
placed on a step only when Spark's own call site for that stage names a line
inside the step; otherwise run-level figures go on the pipeline, which is what
was actually run.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from repolab.architecture.models import ArchitectureGraph, ArchitectureNode, NodeCategory

_CALLSITE = re.compile(r"\bat\s+(?P<file>[\w./\\-]+):(?P<line>\d+)")


class MeasuredValue(BaseModel):
    name: str
    value: float
    unit: str
    source: str
    """Which run produced it, e.g. ``spark run 1a2b3c``."""

    provenance: str = "measured"


class RuntimeOverlay(BaseModel):
    """Measured values per component. Only real, recorded runs contribute."""

    measurements: dict[str, list[MeasuredValue]] = Field(default_factory=dict)
    by_group: dict[str, list[MeasuredValue]] = Field(default_factory=dict)
    """The same values, shown on the concept and subsystem that contain each
    component. Attributed by membership -- never summed, averaged or estimated."""

    runs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def add(self, node_id: str, value: MeasuredValue) -> None:
        self.measurements.setdefault(node_id, []).append(value)


def spark_overlay(graph: ArchitectureGraph, run: dict[str, Any], overlay: RuntimeOverlay) -> None:
    """Place one Spark run's measurements on the components it ran."""
    measurements = run.get("measurements") or {}
    run_id = str(run.get("run_id", ""))[:8]
    source = f"spark run {run_id}"
    pipelines = [n for n in graph.nodes if n.category is NodeCategory.PIPELINE]
    if not pipelines:
        overlay.notes.append("a Spark run exists, but no pipeline component to attach it to")
        return
    overlay.runs.append(source)
    pipeline = pipelines[0]

    for name, key, unit in (
        ("total runtime", "total_runtime_ms", "ms"),
        ("stages", "stage_count", "stages"),
        ("tasks", "task_count", "tasks"),
        ("shuffle read", "shuffle_bytes_read", "bytes"),
        ("shuffle write", "shuffle_bytes_written", "bytes"),
    ):
        value = measurements.get(key)
        if isinstance(value, (int, float)):
            overlay.add(
                pipeline.id, MeasuredValue(name=name, value=float(value), unit=unit, source=source)
            )

    placed = 0
    for stage in measurements.get("stages", []) or []:
        node = _node_for_callsite(graph, str(stage.get("name", "")))
        if node is None:
            continue
        placed += 1
        label = f"stage {stage.get('stage_id')}"
        if isinstance(stage.get("duration_ms"), (int, float)):
            overlay.add(
                node.id,
                MeasuredValue(
                    name=f"{label} duration",
                    value=float(stage["duration_ms"]),
                    unit="ms",
                    source=source,
                ),
            )
        if stage.get("shuffle_bytes_read"):
            overlay.add(
                node.id,
                MeasuredValue(
                    name=f"{label} shuffle read",
                    value=float(stage["shuffle_bytes_read"]),
                    unit="bytes",
                    source=source,
                ),
            )
    if measurements.get("stages") and not placed:
        overlay.notes.append(
            "Spark's stage call sites point at RepoLab's driver rather than repository "
            "lines, so stage figures are shown on the pipeline as a whole"
        )


def cli_overlay(
    graph: ArchitectureGraph, experiment: dict[str, Any], overlay: RuntimeOverlay
) -> None:
    """A completed CLI experiment: how many statements ran, and how many succeeded."""
    runs = experiment.get("runs") or []
    statements = [s for run in runs for s in run.get("statements", [])]
    if not statements:
        return
    targets = [n for n in graph.nodes if n.category is NodeCategory.CLI] or [
        n for n in graph.nodes if n.category is NodeCategory.DATABASE
    ]
    if not targets:
        return
    source = f"experiment {str(experiment.get('id', ''))[:8]}"
    overlay.runs.append(source)
    succeeded = sum(1 for s in statements if s.get("ok"))
    target = targets[0]
    overlay.add(
        target.id,
        MeasuredValue(
            name="statements run", value=float(len(statements)), unit="statements", source=source
        ),
    )
    overlay.add(
        target.id,
        MeasuredValue(
            name="statements accepted", value=float(succeeded), unit="statements", source=source
        ),
    )


def _node_for_callsite(graph: ArchitectureGraph, stage_name: str) -> ArchitectureNode | None:
    match = _CALLSITE.search(stage_name)
    if match is None:
        return None
    filename, line = match.group("file").rsplit("/", 1)[-1], int(match.group("line"))
    for node in graph.nodes:
        start, end = node.properties.get("line_start"), node.properties.get("line_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if any(path.rsplit("/", 1)[-1] == filename for path in node.paths) and start <= line <= end:
            return node
    return None


def roll_up(graph: ArchitectureGraph, overlay: RuntimeOverlay) -> None:
    """Show each measured value on the concept and subsystem its component belongs to."""
    labels = {n.id: n.label for n in graph.nodes}
    homes = {n.id: n.subsystem for n in graph.nodes}
    for node_id, values in overlay.measurements.items():
        groups = [c.id for c in graph.system_model.concepts if node_id in c.member_ids]
        home = homes.get(node_id)
        if home:
            groups.append(home)
            groups += [
                c.id
                for c in graph.system_model.concepts
                if home in c.subsystem_ids and c.id not in groups
            ]
        for group in groups:
            for value in values:
                overlay.by_group.setdefault(group, []).append(
                    value.model_copy(
                        update={"name": f"{labels.get(node_id, node_id)} · {value.name}"}
                    )
                )
