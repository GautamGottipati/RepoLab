"""Workflows: a scheduler, the workflows it runs, and their tasks in order.

Found from the constructs workflow systems share -- a DAG or flow object, tasks
created inside it, and dependencies declared between tasks (``a >> b``,
``set_downstream``). Task edges are only drawn where the file declares them.
"""

from __future__ import annotations

import re
from itertools import pairwise

from repolab.architecture.analyzers.base import AnalysisContext, slug, title
from repolab.architecture.models import ArchitectureEdge, ArchitectureNode, EdgeType, NodeCategory
from repolab.architecture.source import find_all

_DAG = re.compile(
    r"\bDAG\s*\(\s*(?:dag_id\s*=\s*)?['\"](?P<id>[\w.\-]+)['\"]|"
    r"@(?:dag|flow|job)\b[^\n]*\n(?:\s*@[^\n]*\n)*\s*def\s+(?P<fn>\w+)"
)
_SCHEDULE = re.compile(r"\bschedule(?:_interval)?\s*=\s*(?P<value>[^,\n)]+)")
_OPERATOR_TASK = re.compile(
    r"(?P<var>\w+)\s*=\s*(?P<op>\w+(?:Operator|Sensor))\s*\((?P<args>[^)]{0,400}?)task_id\s*=\s*['\"](?P<id>[\w.\-]+)['\"]",
    re.S,
)
_DECORATED_TASK = re.compile(
    r"@(?:task|op)\b(?:\.\w+)?(?:\([^)]*\))?\s*\n(?:\s*@[^\n]*\n)*\s*def\s+(?P<fn>\w+)"
)
_CHAIN = re.compile(r"^[ \t]*(?P<chain>[\w\[\], ]+(?:\s*>>\s*[\w\[\], ]+)+)\s*$", re.M)
_DOWNSTREAM = re.compile(r"(?P<a>\w+)\.set_(?P<dir>downstream|upstream)\(\s*(?P<b>\w+)\s*\)")

_TECHNOLOGY = (
    (re.compile(r"^\s*(?:from|import)\s+airflow", re.M), "Airflow"),
    (re.compile(r"^\s*(?:from|import)\s+prefect", re.M), "Prefect"),
    (re.compile(r"^\s*(?:from|import)\s+dagster", re.M), "Dagster"),
)

MAX_TASKS = 10


class WorkflowAnalyzer:
    name = "workflow"

    def analyze(self, context: AnalysisContext) -> None:
        scheduler: ArchitectureNode | None = None
        for path, text in context.index.source_files():
            dag_match = next(find_all(_DAG, text), None)
            if dag_match is None:
                continue
            line, match = dag_match
            dag_id = match.group("id") or match.group("fn") or "workflow"
            technology = next((name for pattern, name in _TECHNOLOGY if pattern.search(text)), None)
            schedule = _SCHEDULE.search(text)

            if scheduler is None:
                scheduler = context.graph.add_node(
                    ArchitectureNode(
                        id="scheduler:workflow",
                        label=f"{technology} scheduler" if technology else "Workflow scheduler",
                        category=NodeCategory.SCHEDULER,
                        technology=technology,
                        source_references=[context.index.ref(path, line)],
                        properties={"schedules": []},
                        confidence=0.75,
                        detected_because="the repository defines workflows that a scheduler runs",
                    )
                )
            if schedule:
                schedules = scheduler.properties.setdefault("schedules", [])
                if isinstance(schedules, list):
                    schedules.append(f"{dag_id}: {schedule.group('value').strip()}")

            dag = context.graph.add_node(
                ArchitectureNode(
                    id=f"workflow:{slug(dag_id)}",
                    label=f"{title(dag_id)} workflow",
                    category=NodeCategory.PIPELINE,
                    subtype="workflow",
                    technology=technology,
                    source_references=[context.index.ref(path, line)],
                    properties={
                        "workflow_id": dag_id,
                        **({"schedule": schedule.group("value").strip()} if schedule else {}),
                    },
                    confidence=0.85,
                    detected_because=f"{path}:{line} defines the workflow '{dag_id}'",
                )
            )
            context.graph.add_edge(
                ArchitectureEdge(
                    source=scheduler.id,
                    target=dag.id,
                    type=EdgeType.TRIGGERS,
                    evidence=[context.index.ref(path, line)],
                    description=f"on schedule {schedule.group('value').strip()}"
                    if schedule
                    else None,
                )
            )

            tasks: dict[str, ArchitectureNode] = {}
            for task_line, task in find_all(_OPERATOR_TASK, text):
                tasks[task.group("var")] = self._task(
                    context, path, dag_id, task.group("id"), task_line, task.group("op"), technology
                )
            for task_line, task in find_all(_DECORATED_TASK, text):
                tasks.setdefault(
                    task.group("fn"),
                    self._task(
                        context, path, dag_id, task.group("fn"), task_line, None, technology
                    ),
                )
            if len(tasks) > MAX_TASKS:
                tasks = dict(list(tasks.items())[:MAX_TASKS])

            upstream: set[str] = set()
            for dep_line, chain in find_all(_CHAIN, text):
                stages = [
                    [t.strip() for t in part.strip(" []").split(",") if t.strip()]
                    for part in chain.group("chain").split(">>")
                ]
                for before, after in pairwise(stages):
                    for a in before:
                        for b in after:
                            if a in tasks and b in tasks:
                                self._depends(context, tasks[a], tasks[b], path, dep_line)
                                upstream.add(b)
            for dep_line, dep in find_all(_DOWNSTREAM, text):
                a, b = dep.group("a"), dep.group("b")
                if dep.group("dir") == "upstream":
                    a, b = b, a
                if a in tasks and b in tasks:
                    self._depends(context, tasks[a], tasks[b], path, dep_line)
                    upstream.add(b)

            for var, task in tasks.items():
                if var not in upstream:
                    context.graph.add_edge(
                        ArchitectureEdge(
                            source=dag.id,
                            target=task.id,
                            type=EdgeType.CONTROL_FLOW,
                            evidence=list(task.source_references[:1]),
                            description="starts",
                        )
                    )

    @staticmethod
    def _task(
        context: AnalysisContext,
        path: str,
        dag_id: str,
        task_id: str,
        line: int,
        operator: str | None,
        technology: str | None = None,
    ) -> ArchitectureNode:
        return context.graph.add_node(
            ArchitectureNode(
                id=f"task:{slug(dag_id)}:{slug(task_id)}",
                label=title(task_id),
                category=NodeCategory.TASK,
                subtype=operator,
                technology=technology,
                source_references=[context.index.ref(path, line)],
                properties={"task_id": task_id, **({"operator": operator} if operator else {})},
                confidence=0.85,
                detected_because=f"{path}:{line} defines task '{task_id}'",
            )
        )

    @staticmethod
    def _depends(
        context: AnalysisContext, a: ArchitectureNode, b: ArchitectureNode, path: str, line: int
    ) -> None:
        context.graph.add_edge(
            ArchitectureEdge(
                source=a.id,
                target=b.id,
                type=EdgeType.TRIGGERS,
                evidence=[context.index.ref(path, line)],
                description="runs before",
                confidence=0.85,
            )
        )
