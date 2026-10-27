"""Nemotron names the runtime architecture; it cannot invent any of it.

Super is shown only what the code model verified -- key abstractions with
their definitions, and the relations between them with the line that shows
each -- and asked to group them into a handful of runtime components, say what
each does, label what passes along each link, and name the flows a newcomer
should follow.

Everything it returns is checked against that evidence:

    component       its members must be known abstractions
    responsibility  must name one of the component's own members, whose
                    verified definition becomes the citation
    link            only between components whose members have a relation;
                    the model supplies the *label* only
    flow            consecutive steps must be joined by such a link; a flow is
                    "ordered" (drawable as a sequence) only if every hand-over
                    is a verified call or construction

Anything that fails is dropped and counted in ``graph.dropped``. Line numbers
never come from the model -- it is not shown any to repeat.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import BaseModel, Field, model_validator

from repolab.architecture.analyzers.base import slug
from repolab.architecture.code_model import (
    _ORDERING,
    deterministic_flows,
    links_between,
    reading_path,
)
from repolab.architecture.models import (
    ArchitectureGraph,
    Flow,
    FlowStep,
    Provenance,
    Responsibility,
    RuntimeComponent,
    RuntimeModel,
)
from repolab.llm import LLMError, Purpose

log = structlog.get_logger(__name__)

MIN_COMPONENTS = 3
MAX_COMPONENTS = 8
MAX_RESPONSIBILITIES = 4
TIMEOUT_SECONDS = 300
MAX_TOKENS = 16384

SYSTEM_PROMPT = (
    "You explain how a software system works at runtime, to an engineer new to it. "
    "You are given the repository's key types, found and verified in its source, and the "
    "verified relations between them. Group them into the system's runtime components, "
    "using ONLY the type ids given. Never invent a type, file, line or relationship. "
    "Describe only what the names, kinds, locations and relations support; when unsure, "
    "say less."
)


class ProposedResponsibility(BaseModel):
    text: str = Field(default="", description="One short sentence: something this component does")
    member: str = Field(
        default="", description="The id (T1, T2...) of the member type that does it"
    )


class ProposedComponent(BaseModel):
    name: str = Field(description="A runtime role, e.g. 'Driver', 'Scheduler', 'API server'")
    description: str = Field(default="", description="One or two sentences for a newcomer")
    members: list[str] = Field(description="Type ids (T1, T2...) that make up this component")
    responsibilities: list[ProposedResponsibility] = Field(default_factory=list)


class ProposedLink(BaseModel):
    source: str = Field(description="Component name")
    target: str = Field(description="Component name")
    label: str = Field(default="", description="What passes from source to target, 2-4 words")


class ProposedStep(BaseModel):
    component: str
    action: str = Field(
        default="", description="What this component does at this step, a short phrase"
    )
    input: str = ""
    output: str = ""


class ProposedFlow(BaseModel):
    name: str = Field(description="e.g. 'Submitting a job', 'Handling a request'")
    description: str = ""
    steps: list[ProposedStep] = Field(default_factory=list)


class RuntimeProposal(BaseModel):
    components: list[ProposedComponent]
    links: list[ProposedLink] = Field(default_factory=list)
    flows: list[ProposedFlow] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _lift_misplaced_sections(cls, data: Any) -> Any:
        """Repair one observed malformation: the model closes ``components``
        early and writes the rest of the object (``links``, ``flows``) as a
        string inside it. That string is parsed and its keys lifted; anything
        that does not parse is dropped. The result is validated as usual."""
        if not isinstance(data, dict) or not isinstance(data.get("components"), list):
            return data
        kept: list[Any] = []
        for item in data["components"]:
            if not isinstance(item, str):
                kept.append(item)
                continue
            text = item.strip()
            for candidate in (text, "{" + text, '{"' + text, '{"' + text + "}", '{"' + text + "]}"):
                try:
                    extra = json.loads(candidate)
                except ValueError:
                    continue
                if isinstance(extra, dict):
                    for key in ("links", "flows"):
                        if key in extra and not data.get(key):
                            data[key] = extra[key]
                break
        return {**data, "components": kept}


def build_prompt(graph: ArchitectureGraph, aliases: dict[str, str]) -> str:
    labels = {s.id: s.label for s in graph.subsystems}
    lines = ["TYPES (id | name | kind | part of the repository | file | used by N other files)"]
    for abstraction in graph.abstractions:
        lines.append(
            f"{aliases[abstraction.id]} | {abstraction.name} | {abstraction.kind} | "
            f"{labels.get(abstraction.subsystem or '', '-')} | {abstraction.definition.path} | "
            f"{abstraction.referenced_by}"
        )
    lines += ["", "VERIFIED RELATIONS (source -kind-> target: the line that shows it)"]
    for relation in graph.relations[:150]:
        excerpt = (relation.evidence.excerpt or "").splitlines()[0].strip()[:100]
        lines.append(
            f"{aliases[relation.source]} -{relation.type.value}-> {aliases[relation.target]}: "
            f"{excerpt}"
        )
    if graph.understanding and graph.understanding.beginner_explanation:
        lines += ["", "WHAT THE REPOSITORY IS", graph.understanding.beginner_explanation[:1200]]
    lines += [
        "",
        f"Propose {MIN_COMPONENTS}-{MAX_COMPONENTS} runtime components (a type may belong to "
        "at most one), up to 3 responsibilities each tied to a member, a label for links "
        "between components that have relations, and 2-4 flows a newcomer should follow, each "
        "step a component in the order work passes between them.",
    ]
    return "\n".join(lines)


async def name_runtime(graph: ArchitectureGraph, router: Any | None) -> ArchitectureGraph:
    """Refine the deterministic runtime model with Super, or leave it as it is."""
    if router is None or len(graph.abstractions) < MIN_COMPONENTS:
        return graph
    if not any(c.members for c in graph.runtime.components):
        return graph  # built from detected components, which are already named
    aliases = {a.id: f"T{i + 1}" for i, a in enumerate(graph.abstractions)}
    try:
        completion = await router.super(
            purpose=Purpose.REPO_ARCHITECTURE,
            schema=RuntimeProposal,
            system=SYSTEM_PROMPT,
            prompt=build_prompt(graph, aliases),
            max_tokens=MAX_TOKENS,
            timeout_seconds=TIMEOUT_SECONDS,
        )
    except LLMError as exc:
        log.warning("architecture.runtime_naming_failed", error=str(exc))
        graph.gaps.append(
            "Nemotron could not name the runtime components; they keep their type names."
        )
        return graph
    return apply_proposal(graph, completion.value, aliases)


def apply_proposal(
    graph: ArchitectureGraph, proposal: RuntimeProposal, aliases: dict[str, str]
) -> ArchitectureGraph:
    def drop(reason: str) -> None:
        graph.dropped[reason] = graph.dropped.get(reason, 0) + 1

    by_alias = {alias: graph.abstractions[i] for i, alias in enumerate(aliases.values())}
    taken: set[str] = set()
    components: list[RuntimeComponent] = []
    for proposed in proposal.components[:MAX_COMPONENTS]:
        members = [
            by_alias[m.strip()].id
            for m in proposed.members
            if m.strip() in by_alias and by_alias[m.strip()].id not in taken
        ]
        if not members:
            drop("runtime component without a verified member")
            continue
        name = proposed.name.strip()[:60]
        if not name or any(c.name.lower() == name.lower() for c in components):
            drop("runtime component without a distinct name")
            continue
        taken.update(members)
        member_set = set(members)
        responsibilities: list[Responsibility] = []
        for item in proposed.responsibilities:
            abstraction = by_alias.get(item.member.strip())
            if abstraction is None or abstraction.id not in member_set:
                drop("responsibility not tied to one of the component's members")
                continue
            text = item.text.strip()[:200]
            if text:
                responsibilities.append(Responsibility(text=text, reference=abstraction.definition))
        definitions = [by_id.definition for by_id in graph.abstractions if by_id.id in member_set]
        components.append(
            RuntimeComponent(
                id=f"rt:{slug(name)}",
                name=name,
                description=proposed.description.strip()[:400] or None,
                members=members,
                responsibilities=responsibilities[:MAX_RESPONSIBILITIES],
                source_references=definitions,
                subsystem_ids=sorted(
                    {a.subsystem for a in graph.abstractions if a.id in member_set and a.subsystem}
                ),
                node_ids=[
                    n.id
                    for n in graph.nodes
                    if any(r.path in {d.path for d in definitions} for r in n.source_references)
                ][:6],
                provenance=Provenance.INFERRED,
                inferred_fields=["name", "description", "responsibilities"],
            )
        )
    if len(components) < MIN_COMPONENTS:
        drop("runtime proposal with too few verified components")
        return graph

    model = RuntimeModel(components=components, provenance=Provenance.INFERRED)
    model.links = links_between(components, graph.relations)
    by_name = {c.name.lower(): c.id for c in components}
    for proposed_link in proposal.links:
        source = by_name.get(proposed_link.source.strip().lower())
        target = by_name.get(proposed_link.target.strip().lower())
        link = next((x for x in model.links if x.source == source and x.target == target), None)
        if link is None:
            drop("link label for components the code does not relate")
            continue
        label = proposed_link.label.strip()[:40]
        if label:
            link.label = label
            link.label_provenance = Provenance.INFERRED

    flows: list[Flow] = []
    for proposed_flow in proposal.flows[:4]:
        steps: list[FlowStep] = []
        ordered = True
        ids = [by_name.get(step.component.strip().lower()) for step in proposed_flow.steps]
        for index, (step, component) in enumerate(zip(proposed_flow.steps, ids, strict=False)):
            if component is None:
                drop("flow step through an unknown component")
                break
            following = ids[index + 1] if index + 1 < len(ids) else None
            link = next(
                (x for x in model.links if x.source == component and x.target == following), None
            )
            if following is not None and link is None:
                # The hand-over is not in the code: the flow stops here.
                drop("flow step with no verified hand-over")
                steps.append(_step(len(steps) + 1, component, step, []))
                break
            if link is not None and link.type not in _ORDERING:
                ordered = False
            references = link.evidence[:1] if link is not None else []
            steps.append(_step(len(steps) + 1, component, step, references))
        if len(steps) >= 2:
            flows.append(
                Flow(
                    id=f"flow:{slug(proposed_flow.name)}",
                    name=proposed_flow.name.strip()[:80],
                    description=proposed_flow.description.strip()[:300] or None,
                    steps=steps,
                    ordered=ordered and all(s.references for s in steps[:-1]),
                    provenance=Provenance.INFERRED,
                )
            )
        else:
            drop("flow with fewer than two verified steps")

    graph.runtime = model
    # Flows always refer to *these* components: Nemotron's where they held,
    # otherwise the chains the code shows between the new components.
    # Nemotron's flows where they held, then the code's own chains that cover
    # hand-overs those flows do not.
    covered = {
        (a.component, b.component)
        for flow in flows
        for a, b in zip(flow.steps, flow.steps[1:], strict=False)
    }
    for chain in deterministic_flows(model, graph):
        pairs = {
            (a.component, b.component) for a, b in zip(chain.steps, chain.steps[1:], strict=False)
        }
        if len(flows) < 4 and pairs - covered:
            flows.append(chain)
            covered |= pairs
    graph.flows = flows
    graph.reading_path = reading_path(graph)
    return graph


def _step(sequence: int, component: str, step: ProposedStep, references: list) -> FlowStep:
    return FlowStep(
        sequence=sequence,
        component=component,
        action=step.action.strip()[:120] or "takes part",
        input=step.input.strip()[:80] or None,
        output=step.output.strip()[:80] or None,
        references=references,
    )
