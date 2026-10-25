"""What a Spark run did, connected to the repository that implements it.

Two kinds of evidence meet here, and a step is shown only when both exist:

* the **run's** own: its job, stage and task counts from the event log, and the
  operators in Spark's physical plan (an ``Exchange`` *is* a shuffle);
* the **repository's**: an architecture part, concept or component whose name or
  path is that machinery (SQL / Catalyst, the scheduler, the executor), from the
  investigation's graph -- so every link opens something that exists.

Nothing is drawn because it usually happens in Spark; only what this run shows.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from repolab.architecture.models import ArchitectureGraph
from repolab.evidence.models import Measurement, MeasurementSource


class MappedStep(BaseModel):
    title: str
    run_evidence: str
    """What this run showed for the step, in its own numbers or plan operators."""

    architecture_id: str | None = None
    architecture_label: str | None = None
    subsystem_id: str | None = None
    source_paths: list[str] = Field(default_factory=list)


#: The machinery a PySpark run passes through, with the words that name it in a
#: repository's architecture.
_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DataFrame / SQL API", ("spark sql", "dataframe", "sql api", "sql")),
    ("Query planning", ("catalyst", "optimizer", "planner", "query planning", "sql")),
    ("Scheduler", ("scheduler", "dag", "job submission")),
    ("Executors run the tasks", ("executor", "task execution", "core engine", "core")),
)


def plan_operators(plan: str | None) -> list[str]:
    if not plan:
        return []
    names = re.findall(r"(?:^|[\s+\-*()])([A-Z][A-Za-z]+)(?=[\s(\[])", plan)
    seen: list[str] = []
    for name in names:
        # Operators are CamelCase (HashAggregate, Exchange); ALL-CAPS words are
        # expression keywords (AND, DESC) inside them.
        if name not in seen and not name.isupper():
            seen.append(name)
    return seen[:12]


def map_run(result: dict, graph: ArchitectureGraph | None) -> list[MappedStep]:
    """Your code → API → planning → scheduler → executors → result, evidenced."""
    measurements = result.get("measurements") or {}
    jobs = int(result.get("jobs") or 0)
    stages = int(measurements.get("stage_count") or 0)
    tasks = int(measurements.get("task_count") or 0)
    operators = plan_operators(result.get("plan"))
    if not result.get("ok"):
        return []

    run_evidence = {
        "DataFrame / SQL API": "your code built a DataFrame query (lazily; nothing ran yet)",
        "Query planning": (
            "Spark chose a physical plan: "
            + ", ".join(operators[:6])
            + (" — the Exchange is a shuffle" if "Exchange" in operators else "")
        )
        if operators
        else "",
        "Scheduler": f"{_plural(jobs, 'job')} split into {_plural(stages, 'stage')}"
        if stages
        else "",
        "Executors run the tasks": f"{_plural(tasks, 'task')} ran" if tasks else "",
    }

    steps = [MappedStep(title="Your code", run_evidence="the snippet you ran")]
    used: set[str] = set()
    for title, words in _STEPS:
        evidence = run_evidence[title]
        if not evidence:
            continue
        step = MappedStep(title=title, run_evidence=evidence)
        match = _find(graph, words, used) if graph is not None else None
        if match is not None:
            step.architecture_id, step.architecture_label, step.subsystem_id, step.source_paths = (
                match
            )
            used.add(match[0])
        steps.append(step)
    rows = len(result.get("rows") or [])
    steps.append(
        MappedStep(
            title="Result",
            run_evidence=f"{rows} row{'s' if rows != 1 else ''} came back to the driver"
            if result.get("columns")
            else "a value came back to the driver",
        )
    )
    return steps


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _find(
    graph: ArchitectureGraph, words: tuple[str, ...], used: set[str]
) -> tuple[str, str, str | None, list[str]] | None:
    def hit(text: str) -> bool:
        lowered = text.lower()
        return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words)

    for concept in graph.system_model.concepts:
        if concept.id in used:
            continue
        if hit(f"{concept.label} {concept.term or ''}"):
            subsystem = concept.subsystem_ids[0] if concept.subsystem_ids else None
            return concept.id, concept.label, subsystem, _paths(graph, subsystem, words)
    for subsystem in graph.subsystems:
        if subsystem.id in used:
            continue
        if hit(f"{subsystem.label} {' '.join(subsystem.paths)}"):
            return subsystem.id, subsystem.label, subsystem.id, _paths(graph, subsystem.id, words)
    return None


def _paths(graph: ArchitectureGraph, subsystem_id: str | None, words: tuple[str, ...]) -> list[str]:
    """Directories the investigation recorded, most specific first."""
    if subsystem_id is None:
        return []
    subsystem = graph.subsystem(subsystem_id)
    if subsystem is None:
        return []
    specific = [
        point.path
        for point in subsystem.starting_points
        if any(word in point.path.lower() for word in words)
    ]
    return (specific + list(subsystem.paths))[:3]


def run_measurements(result: dict) -> list[Measurement]:
    """The run's numbers as evidence. Only what the event log recorded."""
    m = result.get("measurements") or {}
    found: list[tuple[str, float, str]] = [("jobs", float(result.get("jobs") or 0), "count")]
    for key, unit in (
        ("stage_count", "count"),
        ("task_count", "count"),
        ("total_runtime_ms", "ms"),
        ("shuffle_bytes_read", "bytes"),
        ("shuffle_bytes_written", "bytes"),
        ("shuffle_records_read", "records"),
    ):
        if m.get(key) is not None:
            found.append((key.replace("_count", "s").replace("_", " "), float(m[key]), unit))
    distribution = m.get("distribution") or {}
    if distribution.get("partitions"):
        found.append(("shuffle partitions measured", float(distribution["partitions"]), "count"))
        found.append(
            ("empty partitions", float(distribution.get("empty_partitions") or 0), "count")
        )
    tasks = m.get("tasks") or {}
    if tasks.get("slowest_ms"):
        found.append(("slowest task", float(tasks["slowest_ms"]), "ms"))
    return [
        Measurement(name=name, value=value, unit=unit, source=MeasurementSource.SPARK_EVENT_LOG)
        for name, value, unit in found
    ]
