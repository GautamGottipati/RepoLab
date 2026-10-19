"""Turning a Mini Lab into a build artifact.

The Mini Lab is the only rung of the ladder that builds nothing, which raises a
real question: the specification says *a candidate is never ready before its
smoke test passes*, and there is no environment here to run a smoke test in.

The answer is that a Mini Lab's smoke test checks the thing that makes it safe to
show. A real build's smoke test asks "does this environment work?"; a Mini Lab's
asks "does this simulation obey the rules?" -- every entry cites real code, and
nothing in it reads as a measurement. If that fails, the artifact is not ready,
exactly like a Cassandra node that would not accept a connection.

Kept apart from :mod:`repolab.builds.minilab` so the lab itself stays a plain
description with no build machinery in it.
"""

from __future__ import annotations

import time

from repolab.builds.minilab import SIMULATED_LABEL, MiniLab, generate, validate
from repolab.builds.models import (
    BuildArtifact,
    BuildAttempt,
    BuildCapabilities,
    SmokeTestResult,
)
from repolab.llm import ModelRouter
from repolab.manifest.models import BuildKind, RepoManifest

#: What a Mini Lab's smoke test is called, wherever it is reported.
SMOKE_NAME = "the simulation obeys the Mini Lab rules"

#: Not a shell command -- there is no shell. Named so a reader of the artifact
#: sees immediately that nothing was executed.
SMOKE_COMMAND = "validate(mini_lab)  # no environment; nothing is executed"


def capabilities_for(lab: MiniLab) -> BuildCapabilities:
    """What a Mini Lab can and cannot do.

    The limitations are not boilerplate: they are the Mini Lab restrictions from
    CLAUDE.md, stated as properties of this artifact so that anything displaying
    it shows them.
    """
    return BuildCapabilities(
        capabilities=[
            f"explain {len(lab.grounded_entries)} concept(s) from this repository",
            "link each explanation to the real source file behind it",
        ],
        limitations=[
            f"{SIMULATED_LABEL}: nothing here was executed",
            "no command actually runs; anything unsupported says it needs a real environment",
            "no measurements of any kind — a Mini Lab must never produce one",
            "simulated behaviour is never experimental evidence",
        ],
        deviations=["no environment could be built, so this is a conceptual simulation"],
    )


def smoke_for(lab: MiniLab) -> SmokeTestResult:
    """Run the Mini Lab's smoke test: does the simulation obey the rules?"""
    started = time.perf_counter()
    problem = validate(lab)
    duration = round(time.perf_counter() - started, 4)

    if problem is not None:
        return SmokeTestResult.failure(
            SMOKE_NAME,
            SMOKE_COMMAND,
            str(problem),
            duration_seconds=duration,
        )

    return SmokeTestResult(
        name=SMOKE_NAME,
        command=SMOKE_COMMAND,
        passed=True,
        exit_code=0,
        stdout=(
            f"{len(lab.grounded_entries)} grounded entries, "
            f"{len(lab.entries) - len(lab.grounded_entries)} dropped for citing no file"
        ),
        duration_seconds=duration,
    )


async def build_mini_lab(
    manifest: RepoManifest, *, router: ModelRouter | None = None
) -> BuildArtifact:
    """Produce the Mini Lab artifact for a repository nobody could build.

    The artifact carries ``simulated=True`` and ``CONCEPTUAL`` fidelity, so
    ``supports_real_experiments`` is false and anything that would measure here
    is refused upstream rather than politely declined.
    """
    lab = await generate(manifest, router=router)
    smoke = smoke_for(lab)

    attempt = BuildAttempt(attempt=1, provider="mini_lab", base_image="")
    attempt.smoke = smoke
    attempt.finish(error=None if smoke.passed else smoke.reason)

    return BuildArtifact(
        repo_url=manifest.repo_url,
        # "Commit if applicable": a simulation is built from no commit, and
        # claiming one would imply it reflects that code.
        commit_sha="",
        kind=BuildKind.MINI_LAB,
        provider="mini_lab",
        version=None,
        # There is no environment, so there is no checkpoint to restore -- but
        # `ready` requires one, and a Mini Lab that passed validation genuinely
        # is usable. Its "checkpoint" is the lab itself, named as such.
        checkpoint_id=f"mini-lab:{lab.generated_by}" if smoke.passed else None,
        checkpoint_tag=f"mini-lab/{manifest.repo_url.rsplit('/', 1)[-1]}",
        capabilities=capabilities_for(lab),
        smoke=smoke,
        attempts=[attempt],
        simulated=True,
    )
