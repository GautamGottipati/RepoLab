"""The Repository Investigator agent.

Purpose: understand what a repository contains and how it is intended to run.

This is repository investigation expressed as an agentic workflow. The agent
requests read-only tools one at a time; the tools wrap the same detectors the
deterministic pipeline uses, so the agentic route produces the same findings
rather than a second, divergent implementation.

Read-only investigation needs no user approval, so every tool here is ungated.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from repolab.agents.models import AgentLimits, AgentRun
from repolab.agents.planner import DeterministicPlanner, Planner, SuperPlanner
from repolab.agents.repository_tools import build_repository_tools
from repolab.agents.runtime import AgentRuntime
from repolab.agents.tools import ToolRegistry
from repolab.events import ActivityReporter
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionOutcome
from repolab.ingest.detectors.base import DetectionContext
from repolab.interfaces import ResearchProvider
from repolab.llm import ModelRouter

OBJECTIVE = (
    "Determine what this repository contains, how it is intended to run, and which "
    "interaction surfaces it exposes."
)

STEP_LABEL = "investigate"


class InvestigationResult(BaseModel):
    """What the investigation produced."""

    run: AgentRun
    outcome: DetectionOutcome

    @property
    def findings(self) -> int:
        return len(self.outcome.run.findings)


class RepositoryInvestigatorAgent:
    """Investigates a repository through read-only tools."""

    name = "repository_investigator"

    def __init__(
        self,
        context: DetectionContext,
        budget: ReadBudget,
        *,
        research: ResearchProvider | None = None,
        router: ModelRouter | None = None,
        limits: AgentLimits | None = None,
    ) -> None:
        self._tools, tool_list = build_repository_tools(context, budget, research=research)
        self._registry = ToolRegistry(tool_list)
        self._limits = limits or AgentLimits()
        self._planner = self._build_planner(router, research is not None)

    def _build_planner(self, router: ModelRouter | None, research: bool) -> Planner:
        """Super plans when a router exists; otherwise deterministic rules do.

        Either way the same tool layer validates and executes, and the same events
        are emitted, so investigation works with or without credentials.
        """
        deterministic = DeterministicPlanner(research_available=research)
        if router is None:
            return deterministic
        return SuperPlanner(router, self._registry.describe(), fallback=deterministic)

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def planner_name(self) -> str:
        return self._planner.name

    def _learned(self) -> dict[str, Any]:
        """What the agent knows so far, for evidence-driven planning."""
        run = self._tools.run
        return {
            "frameworks": sorted(set(run.values_of("framework"))),
            "config_paths": run.values_of("configuration"),
            "entrypoints": [entry.name for entry in self._tools.entrypoints][:10],
            "services": [service.name for service in self._tools.services],
            "findings": len(run.findings),
        }

    async def investigate(
        self, emitter: Any = None, *, activity: ActivityReporter | None = None
    ) -> InvestigationResult:
        """Run the investigation and assemble its findings.

        *activity* turns tool calls into the user-facing phases the browser shows.
        Optional, so the CLI and the tests are unaffected.
        """
        runtime = AgentRuntime(
            agent=self.name,
            objective=OBJECTIVE,
            registry=self._registry,
            planner=self._planner,
            emitter=emitter,
            limits=self._limits,
            step_label=STEP_LABEL,
            activity=activity,
        )
        run = await runtime.run(self._learned)

        # The findings the tools accumulated are exactly what the classifier and
        # manifest builder already consume.
        outcome = DetectionOutcome(
            run=self._tools.run,
            dependencies=self._tools.dependencies,
            entrypoints=self._tools.entrypoints,
            services=self._tools.services,
        )
        return InvestigationResult(run=run, outcome=outcome)
