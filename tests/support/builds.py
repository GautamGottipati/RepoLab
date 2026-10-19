"""Builders for build-ladder tests.

The fake sandbox matches commands **exactly**, so scripting a plan's outcome
means scripting the plan's own command strings. These helpers take them from the
plan rather than restating them, which means a test cannot drift from the plan it
is exercising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repolab.builds.plan import BuildPlan
from repolab.evidence.models import CodeReference
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Fidelity,
    InteractionSurface,
    RepoManifest,
    SurfaceType,
)
from repolab.sandbox import FakeSandboxProvider, ScriptedCommand

CASSANDRA_REPO = "https://github.com/apache/cassandra"
SHA = "c" * 40


def cassandra_manifest(repo_url: str = CASSANDRA_REPO, sha: str = SHA) -> RepoManifest:
    """A manifest shaped like Cassandra's: a CLI surface, Java, Ant."""
    return RepoManifest(
        repo_url=repo_url,
        commit_sha=sha,
        languages=["Java", "Python"],
        frameworks=[],
        interaction_surfaces=[
            InteractionSurface(
                type=SurfaceType.CLI,
                name="cqlsh",
                confidence=0.85,
                reason="server+shell: bin/cqlsh alongside a server entry point.",
                primary=True,
                evidence=[CodeReference(path="bin/cqlsh")],
            )
        ],
        build_strategy=BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        confidence=0.7,
    )


def repository(tmp_path: Path) -> Path:
    """A minimal checkout, so archiving has something real to read."""
    root = tmp_path / "cassandra"
    root.mkdir(parents=True, exist_ok=True)
    (root / "build.xml").write_text('<project name="cassandra" default="jar"/>')
    (root / "bin").mkdir(exist_ok=True)
    (root / "bin" / "cqlsh").write_text("#!/bin/sh\necho cqlsh\n")
    return root


def passing(plan: BuildPlan, marker: str) -> dict[str, ScriptedCommand]:
    """Script *plan* so every step succeeds and the smoke test passes."""
    return {plan.smoke.command: ScriptedCommand(stdout=f"{marker}\nrelease_version 5.0.2")}


def failing_smoke(plan: BuildPlan) -> dict[str, ScriptedCommand]:
    """Script *plan* so it builds and then fails its smoke test.

    The case that matters most: a candidate that *finished* is still not a
    candidate until it has passed.
    """
    return {plan.smoke.command: ScriptedCommand(exit_code=1, stderr="connection refused")}


def failing_step(plan: BuildPlan, name: str, **overrides: Any) -> dict[str, ScriptedCommand]:
    """Script the named step of *plan* to fail."""
    for step in plan.all_steps:
        if step.name == name:
            return {step.command: ScriptedCommand(exit_code=1, **overrides)}
    raise AssertionError(f"{name!r} is not a step of this plan: {[s.name for s in plan.all_steps]}")


def provider(*scripts: dict[str, ScriptedCommand]) -> FakeSandboxProvider:
    """A fake provider with every script applied."""
    merged: dict[str, ScriptedCommand] = {}
    for script in scripts:
        merged.update(script)
    return FakeSandboxProvider(scripted=merged)
