"""The Repository Investigator agent.

The load-bearing test is equivalence: the agentic route must reach the same
conclusions as the deterministic pipeline on the same repository. Making
investigation agentic should change *how* findings are gathered, not what they are.
"""

from __future__ import annotations

import pytest

from repolab.agents import AgentLimits, AgentStatus, RepositoryInvestigatorAgent
from repolab.classify import classify_deterministic
from repolab.events import CollectingEmitter, EventType
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionContext, detect_all
from repolab.ingest.tree import build_file_tree
from repolab.llm import ModelRouter, ModelTier
from repolab.testing.fakes import FakeLLMProvider, FakeMessage, FakeResearchProvider
from tests.test_detectors import CLI_REPO, SPARK_REPO, WEB_REPO


def context_for(root, sha: str = "abc123") -> DetectionContext:
    return DetectionContext(root=root, tree=build_file_tree(root), commit_sha=sha)


async def investigate(root, **kwargs):
    agent = RepositoryInvestigatorAgent(context_for(root), ReadBudget(), **kwargs)
    emitter = CollectingEmitter("job-1")
    return await agent.investigate(emitter), emitter, agent


class TestEquivalenceWithTheDeterministicPipeline:
    @pytest.mark.parametrize("files", [SPARK_REPO, WEB_REPO, CLI_REPO], ids=["spark", "web", "cli"])
    async def test_the_agent_classifies_a_repository_identically(
        self, make_repo, files: dict[str, str]
    ) -> None:
        root, _ = make_repo(files)
        result, _, _ = await investigate(root)

        pipeline = detect_all(context_for(root), ReadBudget())
        agentic = classify_deterministic(
            result.outcome.run,
            entrypoints=result.outcome.entrypoints,
            services=result.outcome.services,
        )
        expected = classify_deterministic(
            pipeline.run, entrypoints=pipeline.entrypoints, services=pipeline.services
        )

        assert [(s.type, s.confidence) for s in agentic.surfaces] == [
            (s.type, s.confidence) for s in expected.surfaces
        ]
        assert agentic.primary_type is expected.primary_type

    async def test_the_agent_finds_the_same_dependencies(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        result, _, _ = await investigate(root)
        pipeline = detect_all(context_for(root), ReadBudget())

        assert {d.name for d in result.outcome.dependencies} == {
            d.name for d in pipeline.dependencies
        }

    async def test_every_finding_remains_citable(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        result, _, _ = await investigate(root)

        assert result.outcome.run.findings
        assert result.outcome.unsupported_findings == []
        for ref in result.outcome.run.all_references:
            assert (root / ref.path).exists()


class TestTheRunItself:
    async def test_it_completes_within_its_budget(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        result, _, _ = await investigate(root)

        assert result.run.status is AgentStatus.COMPLETED
        assert 0 < result.run.iterations <= result.run.limits.max_iterations

    async def test_it_uses_read_only_tools_only(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        _, _, agent = await investigate(root)

        for name in agent.registry.names:
            assert agent.registry.get(name).requires_approval is False

    async def test_a_tight_budget_exhausts_rather_than_hangs(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        agent = RepositoryInvestigatorAgent(
            context_for(root), ReadBudget(), limits=AgentLimits(max_iterations=2)
        )
        result = await agent.investigate(CollectingEmitter())

        assert result.run.status is AgentStatus.EXHAUSTED
        assert result.run.iterations == 2

    async def test_progress_is_reported_throughout(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        _, emitter, _ = await investigate(root)

        types = set(emitter.types())
        assert EventType.AGENT_STARTED in types
        assert EventType.TOOL_REQUESTED in types
        assert EventType.TOOL_COMPLETED in types
        assert EventType.PROGRESS in types
        assert EventType.AGENT_STATUS in types

    async def test_every_tool_request_carries_a_reason(self, make_repo) -> None:
        """A user watching should see why each action was taken."""
        root, _ = make_repo(SPARK_REPO)
        result, _, _ = await investigate(root)

        for step in result.run.steps:
            assert step.request is not None
            assert step.request.reason.strip()


class TestEvidenceDrivenPlanning:
    async def test_it_reads_a_config_file_it_discovered(self, make_repo) -> None:
        """Following up on a finding is what makes this more than a fixed script."""
        root, _ = make_repo(SPARK_REPO)
        result, _, _ = await investigate(root)

        read_paths = {
            step.request.arguments.get("path")
            for step in result.run.steps
            if step.request is not None and step.request.tool == "read_file"
        }
        assert "configs/etl_config.json" in read_paths

    async def test_docs_are_searched_only_when_a_framework_was_found(self, make_repo) -> None:
        with_framework, _ = make_repo(SPARK_REPO)
        without, _ = make_repo({"notes.txt": "nothing to see\n"})

        found, _, _ = await investigate(with_framework, research=FakeResearchProvider())
        none, _, _ = await investigate(without, research=FakeResearchProvider())

        def searched(result) -> bool:
            return any(
                step.request is not None and step.request.tool == "search_official_docs"
                for step in result.run.steps
            )

        assert searched(found) is True
        assert searched(none) is False

    async def test_docs_are_not_searched_without_a_provider(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        result, _, _ = await investigate(root)

        assert not any(
            step.request is not None and step.request.tool == "search_official_docs"
            for step in result.run.steps
        )


class TestPlannerSelection:
    async def test_without_a_router_planning_is_deterministic(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        _, _, agent = await investigate(root)
        assert agent.planner_name == "deterministic"

    async def test_with_a_router_super_plans(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        provider = FakeLLMProvider(
            message=FakeMessage(
                content='{"action": "list_files", "arguments": {}, "reason": "look", "done": false}'
            )
        )
        router = ModelRouter(provider, {ModelTier.SUPER: "fake/super"})
        result, _, agent = await investigate(root, router=router)

        assert agent.planner_name == "super"
        assert provider.call_count > 0
        # The loop protection stops a model that keeps asking for the same thing.
        assert result.run.status in {AgentStatus.FAILED, AgentStatus.EXHAUSTED}

    async def test_a_model_failure_falls_back_to_deterministic_planning(self, make_repo) -> None:
        """Investigation must make progress even when the model is unavailable."""
        root, _ = make_repo(SPARK_REPO)
        router = ModelRouter(
            FakeLLMProvider(fail_with=RuntimeError("503")),
            {ModelTier.SUPER: "fake/super"},
        )
        result, _, _ = await investigate(root, router=router)

        assert result.run.status is AgentStatus.COMPLETED
        assert result.findings > 0

    async def test_a_model_naming_an_unknown_tool_falls_back(self, make_repo) -> None:
        root, _ = make_repo(SPARK_REPO)
        provider = FakeLLMProvider(
            message=FakeMessage(
                content='{"action": "rm_rf", "arguments": {}, "reason": "no", "done": false}'
            )
        )
        router = ModelRouter(provider, {ModelTier.SUPER: "fake/super"})
        result, _, _ = await investigate(root, router=router)

        # It fell back and finished properly rather than passing an invalid request on.
        assert result.run.status is AgentStatus.COMPLETED
        assert not any(
            step.request is not None and step.request.tool == "rm_rf" for step in result.run.steps
        )
