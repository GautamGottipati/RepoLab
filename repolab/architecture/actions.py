"""What a reader can do with each component: ask about it, and sometimes test it.

Both are decided deterministically.

**Experiment** is offered only where RepoLab has a real execution capability for
the repository -- the same backend gate Ask RepoLab uses
(:mod:`repolab.questions.triage`) -- and only on components that backend
actually exercises. A Spark sweep measures the pipeline and its steps; CQL
through a built node exercises the shell, its commands and the database. Nothing
else is offered an experiment, however interesting it looks.

**Suggested questions** are templates per category, filled only with values
read from the component itself -- a route, a command name, a setting such as
``spark.sql.shuffle.partitions=200``. No number is ever made up for a question.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from repolab.architecture.models import ArchitectureGraph, ArchitectureNode, NodeCategory
from repolab.builds.models import BuildArtifact
from repolab.manifest.models import RepoManifest
from repolab.questions.models import ExperimentBackend
from repolab.questions.triage import cli_artifact, spark_backend_available

#: The components each backend exercises when it runs.
EXERCISED: dict[ExperimentBackend, set[NodeCategory]] = {
    ExperimentBackend.SPARK: {
        NodeCategory.PIPELINE,
        NodeCategory.TRANSFORM,
        NodeCategory.PROCESSOR,
        NodeCategory.INPUT,
        NodeCategory.OUTPUT,
    },
    ExperimentBackend.CLI: {
        NodeCategory.CLI,
        NodeCategory.COMMAND,
        NodeCategory.DATABASE,
        NodeCategory.SERVICE,
    },
}

#: The Spark sweep varies shuffle partitions, so its suggestion names that
#: setting -- with the repository's own value when the code sets one.
_SPARK_SETTING = "spark.sql.shuffle.partitions"

MAX_SUGGESTIONS = 3

NEEDS_BUILD = "no environment has been built yet; build one from the Learn tab to experiment"


class ExperimentAvailability(BaseModel):
    available: bool
    backend: ExperimentBackend | None = None
    reason: str
    """Why it is, or is not, offered -- shown to the reader either way."""

    question: str | None = None
    """A testable question to start from, when available."""

    needs_environment: bool = False
    """Unavailable only because nothing has been built yet -- shown quietly."""


class NodeActions(BaseModel):
    ask: bool = True
    experiment: ExperimentAvailability
    suggested_questions: list[str] = Field(default_factory=list)


def node_actions(
    graph: ArchitectureGraph,
    manifest: RepoManifest | None,
    artifacts: list[BuildArtifact],
) -> dict[str, NodeActions]:
    """Actions for every component, keyed by node id."""
    backend, why_not = _backend(manifest, artifacts)
    actions: dict[str, NodeActions] = {}
    for node in graph.nodes:
        suggestions = suggested_questions(graph, node)
        experiment = _experiment(node, backend, why_not, suggestions, graph)
        if experiment.question and experiment.question not in suggestions:
            suggestions = [experiment.question, *suggestions][:MAX_SUGGESTIONS]
        actions[node.id] = NodeActions(experiment=experiment, suggested_questions=suggestions)
    return actions


def _backend(
    manifest: RepoManifest | None, artifacts: list[BuildArtifact]
) -> tuple[ExperimentBackend | None, str]:
    if spark_backend_available(manifest):
        return ExperimentBackend.SPARK, ""
    if cli_artifact(artifacts) is not None:
        return ExperimentBackend.CLI, ""
    if artifacts and all(a.simulated for a in artifacts):
        return (
            None,
            "only a simulated Mini Lab exists for this repository, and it is never measured",
        )
    if manifest is not None and any(s.type.value == "cli" for s in manifest.interaction_surfaces):
        return None, NEEDS_BUILD
    return None, "RepoLab cannot run this repository yet, so there is nothing to measure"


def _experiment(
    node: ArchitectureNode,
    backend: ExperimentBackend | None,
    why_not: str,
    suggestions: list[str],
    graph: ArchitectureGraph,
) -> ExperimentAvailability:
    if backend is None:
        return ExperimentAvailability(
            available=False, reason=why_not, needs_environment=why_not == NEEDS_BUILD
        )
    if node.category not in EXERCISED[backend]:
        return ExperimentAvailability(
            available=False,
            backend=backend,
            reason=(
                f"the {backend.value} experiment does not exercise "
                f"a {node.category.value.replace('_', ' ')}"
            ),
        )
    if backend is ExperimentBackend.SPARK:
        value = _setting(graph, node, _SPARK_SETTING)
        question = (
            f"Why does this use {_SPARK_SETTING}={value}?"
            if value
            else f"How does {_SPARK_SETTING} affect {node.label}?"
        )
        reason = (
            "RepoLab can run this Spark job at different shuffle partition counts and measure it"
        )
    else:
        question = f"What happens when a statement sent through {node.label} is invalid?"
        reason = (
            "RepoLab can run statements on this repository's built node and record what comes back"
        )
    return ExperimentAvailability(available=True, backend=backend, reason=reason, question=question)


def suggested_questions(graph: ArchitectureGraph, node: ArchitectureNode) -> list[str]:
    """Questions worth asking about *node*, from what it actually contains."""
    label = node.label
    incoming, outgoing = graph.neighbours(node.id)
    upstream = [graph.node(e.source) for e in incoming]
    downstream = [graph.node(e.target) for e in outgoing]
    up = ", ".join(n.label for n in upstream if n is not None)[:120]
    down = ", ".join(n.label for n in downstream if n is not None)[:120]
    props = node.properties
    questions: list[str] = []
    category = node.category

    if category is NodeCategory.API:
        routes = props.get("routes")
        if isinstance(routes, list) and routes:
            questions.append(f"What happens when a request reaches {routes[0]}?")
        questions.append(f"Which components does {label} call to answer a request?")
    elif category in {NodeCategory.CLI, NodeCategory.COMMAND}:
        questions.append(f"What happens internally when {label} runs?")
    elif category is NodeCategory.TASK:
        if up:
            questions.append(f"Why does {label} depend on {up}?")
        questions.append(f"What does {label} do when the workflow runs?")
    elif category in {NodeCategory.TRANSFORM, NodeCategory.PROCESSOR}:
        operations = props.get("operations")
        if isinstance(operations, list) and "join" in operations:
            questions.append(f"What happens to rows that do not match in {label}?")
        questions.append(f"Why does {label} happen at this point in the flow?")
    elif category is NodeCategory.PIPELINE:
        settings = props.get("settings")
        if isinstance(settings, list) and settings:
            key, _, value = str(settings[0]).partition("=")
            questions.append(f"Why does this use {key} = {value}?")
        questions.append(f"What happens to the data as it moves through {label}?")
    elif category in {NodeCategory.INPUT, NodeCategory.OUTPUT}:
        questions.append(
            f"Where does {label} {'read' if category is NodeCategory.INPUT else 'write'} "
            "its data, and in what format?"
        )
    elif category in {
        NodeCategory.DATABASE,
        NodeCategory.CACHE,
        NodeCategory.QUEUE,
        NodeCategory.STORAGE,
    }:
        users = up or "the code"
        questions.append(f"How does {users} use {label}?")
    elif category is NodeCategory.SCHEDULER:
        questions.append(f"When does {label} run each workflow, and why?")
    elif category is NodeCategory.ENTRY_POINT and down:
        questions.append(f"What happens after {label} starts {down}?")

    questions.append(f"What does {label} do, and why does it exist?")
    return list(dict.fromkeys(questions))[:MAX_SUGGESTIONS]


def _setting(graph: ArchitectureGraph, node: ArchitectureNode, key: str) -> str | None:
    """The value the code sets for *key*, on this node or its pipeline."""
    candidates = [
        node,
        *[n for n in graph.nodes if n.category is NodeCategory.PIPELINE and n.paths & node.paths],
    ]
    for candidate in candidates:
        settings = candidate.properties.get("settings")
        if isinstance(settings, list):
            for setting in settings:
                name, _, value = str(setting).partition("=")
                if name == key and value:
                    return value
    return None


def concept_actions(
    graph: ArchitectureGraph, actions: dict[str, NodeActions]
) -> dict[str, NodeActions]:
    """Actions for the system model's concepts and the subsystems, keyed by id.

    A concept or subsystem can be experimented on only through a component in
    it that can be -- the experiment is that component's, never a new one.
    """
    name = graph.understanding.name if graph.understanding else "this project"
    result: dict[str, NodeActions] = {}
    groups: list[tuple[str, str, list[str], bool]] = [
        (c.id, c.label, c.member_ids, True) for c in graph.system_model.concepts
    ]
    groups += [(s.id, s.label, s.component_ids, False) for s in graph.subsystems]
    for group_id, label, members, is_concept in groups:
        member_actions = [actions[m] for m in members if m in actions]
        offered = next((a.experiment for a in member_actions if a.experiment.available), None)
        if offered is None:
            offered = ExperimentAvailability(
                available=False,
                reason="nothing RepoLab can run exercises this part"
                if member_actions
                else "RepoLab found no runnable component here",
                needs_environment=any(a.experiment.needs_environment for a in member_actions),
            )
        questions = (
            [
                f"What does {label} do?",
                f"Why does {name} need {label}?",
                f"Where is {label} implemented?",
                f"What talks to {label}?",
            ]
            if is_concept
            else [
                f"What is {label} responsible for?",
                f"Where should I start reading {label}?",
                f"How does {label} connect to the rest of {name}?",
            ]
        )
        if offered.question:
            questions = [offered.question, *questions]
        result[group_id] = NodeActions(experiment=offered, suggested_questions=questions[:4])
    return result
