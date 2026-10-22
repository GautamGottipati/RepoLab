"""The Super planner, against a scripted model.

The regression: investigating ``AlexIoannides/pyspark-example-project``, Super
asked for ``list_files()`` again and again -- its prompt said "listed 21 of 21
matching files" but never which files -- until the runtime's stall guard failed
the whole job. Two things are proved here: the model is shown what each tool
returned, and a repeat of a request that already succeeded is never sent.
"""

from __future__ import annotations

from typing import Any

from repolab.agents.models import AgentRun, AgentStep, ToolRequest, ToolResult
from repolab.agents.planner import DeterministicPlanner, PlannedAction, SuperPlanner

TOOLS = [
    {"name": name, "arguments": [], "description": name}
    for name in (
        "list_files",
        "read_file",
        "inspect_dependencies",
        "detect_entrypoints",
        "detect_services",
        "detect_surfaces",
        "inspect_config",
    )
]


class Super:
    """Always proposes the same action, recording each prompt."""

    def __init__(self, action: PlannedAction) -> None:
        self.action = action
        self.prompts: list[str] = []

    async def super(self, *, prompt: str, **_: Any):
        self.prompts.append(prompt)
        value = self.action

        class Completion:
            pass

        Completion.value = value  # type: ignore[attr-defined]
        return Completion()


def listed(*paths: str) -> AgentStep:
    return AgentStep(
        index=1,
        request=ToolRequest(tool="list_files", arguments={}),
        result=ToolResult(summary=f"listed {len(paths)} files", data={"paths": list(paths)}),
        status=f"listed {len(paths)} of {len(paths)} matching files",
    )


def run_with(*steps: AgentStep) -> AgentRun:
    return AgentRun(agent="repository_investigator", objective="understand it", steps=list(steps))


async def test_the_model_sees_the_paths_a_listing_returned() -> None:
    router = Super(PlannedAction(action="read_file", arguments={"path": "jobs/etl_job.py"}))
    planner = SuperPlanner(router, TOOLS)  # type: ignore[arg-type]

    await planner.next_request(run_with(listed("README.md", "jobs/etl_job.py")), {})

    assert "jobs/etl_job.py" in router.prompts[0]


async def test_a_request_that_already_succeeded_is_not_sent_again() -> None:
    router = Super(PlannedAction(action="list_files", arguments={}))
    planner = SuperPlanner(router, TOOLS, fallback=DeterministicPlanner())  # type: ignore[arg-type]

    request = await planner.next_request(run_with(listed("README.md")), {})

    assert request is not None
    assert request.signature != "list_files()"
    assert request.tool == "inspect_dependencies"


async def test_with_nothing_left_to_do_the_investigation_finishes() -> None:
    router = Super(PlannedAction(action="list_files", arguments={}))
    planner = SuperPlanner(router, TOOLS, fallback=DeterministicPlanner())  # type: ignore[arg-type]
    done = [
        AgentStep(
            index=i,
            request=ToolRequest(tool=tool, arguments={}),
            result=ToolResult(summary="ok"),
            status="ok",
        )
        for i, (tool, _, _) in enumerate(DeterministicPlanner.BASE_PLAN, start=1)
    ]

    assert await planner.next_request(run_with(*done), {}) is None


async def test_retrying_a_failed_request_is_still_allowed() -> None:
    router = Super(PlannedAction(action="read_file", arguments={"path": "README.md"}))
    planner = SuperPlanner(router, TOOLS)  # type: ignore[arg-type]
    failed = AgentStep(
        index=1,
        request=ToolRequest(tool="read_file", arguments={"path": "README.md"}),
        result=ToolResult.failure("timed out"),
        status="read_file failed: timed out",
        ok=False,
    )

    request = await planner.next_request(run_with(failed), {})
    assert request is not None and request.tool == "read_file"
