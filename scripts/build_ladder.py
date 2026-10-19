#!/usr/bin/env python
"""Resolve a repository through the full fidelity ladder, and watch the upgrade.

    cache -> source build -> official release -> partial build -> Mini Lab
                  |                  |
             (slow, real)      (ready first)
                  |                  |
                  |                  +--> a learner starts working
                  |
                  +--> finishes later --> upgrade OFFERED, never forced

Usage:
    make build-ladder URL=https://github.com/apache/cassandra
    uv run python scripts/build_ladder.py [--url URL] [--slow-source N] [--persist]

The source build is genuinely expensive -- Cassandra's `ant jar` resolves
dependencies over the network and compiles for minutes. ``--slow-source`` adds a
*further*, deliberate delay so the ordering is the same every time the
demonstration runs. It is disclosed on the artifact and in this output, so a
build that was slowed on purpose is never mistaken for one that was slow.

Nothing is upgraded while the simulated learner is mid-command. That is the point
of the exercise, and the script prints the refusal when it happens.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repolab.builds import (  # noqa: E402
    BuildLadder,
    BuildLimits,
    BuildRequest,
    BuildUnavailableError,
    DbBuildCache,
    EnvironmentSession,
    InMemoryBuildCache,
    NullBuildCache,
    UpgradeRefused,
    candidates_for,
    needs_submodules,
)
from repolab.builds.models import BuildArtifact  # noqa: E402
from repolab.config import get_settings  # noqa: E402
from repolab.events import CollectingEmitter, EventType  # noqa: E402
from repolab.ingest import ingest_repository  # noqa: E402
from repolab.ingest.clone import TemporaryClone  # noqa: E402
from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits  # noqa: E402
from repolab.ingest.url import validate_repo_url  # noqa: E402
from repolab.sandbox.docker import docker_available  # noqa: E402
from repolab.sandbox.docker_provider import DockerSandboxProvider  # noqa: E402

DEMO_REPO = "https://github.com/apache/cassandra"

#: Cassandra is large; ingestion stays bounded.
LARGE_REPO_LIMITS = IngestLimits(max_scanned_files=1_500, max_total_read_bytes=24_000_000)


class Printer(CollectingEmitter):
    """Prints the ladder's progress as it happens."""

    SHOWN: ClassVar[set[EventType]] = {
        EventType.AGENT_STATUS,
        EventType.STEP_STARTED,
        EventType.STEP_DONE,
        EventType.PARTIAL_RESULT,
        EventType.LOG_LINE,
        EventType.FAILED,
        EventType.UPGRADE_AVAILABLE,
        EventType.UPGRADE_APPLIED,
    }
    MARKS: ClassVar[dict[EventType, str]] = {
        EventType.PARTIAL_RESULT: "+",
        EventType.STEP_DONE: "=",
        EventType.FAILED: "!",
        EventType.LOG_LINE: " ",
        EventType.UPGRADE_AVAILABLE: "?",
        EventType.UPGRADE_APPLIED: "^",
    }

    def emit(self, event_type, message="", *, step=None, data=None):  # type: ignore[override]
        event = super().emit(event_type, message, step=step, data=data)
        if event_type in self.SHOWN and message:
            print(f"  {self.MARKS.get(event_type, '>')} {message}", flush=True)
        return event


def describe(artifact: BuildArtifact) -> None:
    """Everything an artifact is required to declare about itself."""
    declaration = artifact.declaration()
    print()
    print(f"  source         {declaration['source']}")
    print(f"  version        {declaration['version'] or '—'}")
    print(f"  commit         {declaration['commit_sha'] or '— (not applicable)'}")
    print(f"  fidelity       {declaration['fidelity']}")
    print(f"  ready          {declaration['ready']}")
    print(f"  simulated      {declaration['simulated']}")
    print(f"  real experiments {declaration['supports_real_experiments']}")
    if artifact.demo_delay_seconds:
        print(f"  DELAYED        {artifact.demo_delay_seconds:.0f}s, for demonstration only")
    print("  capabilities")
    for item in declaration["capabilities"]:
        print(f"    - {item}")
    print("  limitations")
    for item in declaration["limitations"] or ["(none declared)"]:
        print(f"    - {item}")
    if declaration["deviations"]:
        print("  deviations (disclosed)")
        for item in declaration["deviations"]:
            print(f"    - {item}")
    smoke = declaration["smoke_test"]
    print(f"  smoke test     {'PASSED' if smoke and smoke['passed'] else 'did not pass'}")
    if smoke:
        print(f"                 {smoke['name']}")


async def run(args: argparse.Namespace) -> int:
    if not docker_available():
        print("docker is not available; the ladder builds real environments", file=sys.stderr)
        return 2

    repo_url = validate_repo_url(args.url)
    print(f"repository     {repo_url.slug}")
    print(f"slow source    +{args.slow_source}s (demonstration only)")
    print()
    print("investigating")

    ingestion = await ingest_repository(repo_url.normalized, limits=LARGE_REPO_LIMITS)
    manifest = ingestion.manifest
    print(
        f"  = {manifest.commit_sha[:12]}, primary surface "
        f"{manifest.primary_surface.type.value if manifest.primary_surface else 'none'}"
    )

    candidates = candidates_for(manifest, source_delay_seconds=args.slow_source)
    print()
    print(f"ladder ({len(candidates)} candidates, best first)")
    for candidate in candidates:
        print(
            f"  {candidate.expected_fidelity.value:<11} {candidate.kind.value:<17} {candidate.why}"
        )

    cache = _cache(args)
    emitter = Printer("ladder")
    session = EnvironmentSession(repo_url=repo_url.normalized)

    # The learner starts as soon as *something* is ready, and keeps working while
    # the better candidate finishes. That is the whole sequence.
    async def on_ready(artifact: BuildArtifact) -> None:
        if session.artifact is None:
            session.use(artifact, emitter=emitter)
            print()
            print(f"  >> a learner can begin on the {artifact.fidelity.value} environment")
            session.begin("CREATE KEYSPACE demo")
            print("  >> they are mid-command: CREATE KEYSPACE demo")
            return

        offer = session.propose(artifact, emitter=emitter)
        if offer is None:
            return
        try:
            session.accept(emitter=emitter)
        except UpgradeRefused as refused:
            print(f"  >> NOT switching: {refused}")

    submodules = needs_submodules(candidates)
    print()
    print(f"building{' (with submodules, as the source build requires)' if submodules else ''}")
    with TemporaryClone(repo_url, limits=DEFAULT_LIMITS, submodules=submodules) as clone:
        ladder = BuildLadder(
            DockerSandboxProvider(),
            cache=cache,
            emitter=emitter,
            limits=BuildLimits(max_attempts=args.max_attempts),
        )
        request = BuildRequest(repo_url=repo_url.normalized, commit_sha=clone.commit_sha)
        try:
            result = await ladder.resolve(
                request,
                candidates,
                Path(clone.path),
                manifest=manifest,
                on_ready=on_ready,
            )
        except BuildUnavailableError as exc:
            print(f"\nEVERY CANDIDATE FAILED\n  {exc}", file=sys.stderr)
            return 1

    print()
    print("outcomes")
    for outcome in result.outcomes:
        print(f"  {outcome.describe()}")
        if outcome.summary is not None and not outcome.ready:
            print(f"      summary ({outcome.summary.produced_by}): {outcome.summary.headline}")

    print()
    print("=" * 72)
    print(f"SERVING  {result.served.summary()}")
    print("=" * 72)
    describe(result.served)

    # The learner was mid-command throughout. Finishing it is what makes the
    # upgrade applicable.
    if session.upgrade_pending:
        print()
        print("the learner finishes their command")
        session.end()
        applied = session.accept(emitter=emitter)
        print(f"  ^ now on the {applied.fidelity.value} environment")

    print()
    print("session history")
    for line in session.history:
        print(f"  {line}")

    if args.persist:
        print()
        print(f"persisted {sum(1 for o in result.outcomes if o.ready)} ready artifact(s)")
    return 0


def _cache(args: argparse.Namespace):
    if args.no_cache:
        return NullBuildCache()
    if not args.persist:
        return InMemoryBuildCache()

    from repolab.db import build_engine, session_scope

    engine = build_engine(get_settings())
    return DbBuildCache(lambda: session_scope(engine))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEMO_REPO, help="repository to resolve")
    parser.add_argument(
        "--slow-source",
        type=float,
        default=90.0,
        help="extra seconds before the source build starts, for demonstration",
    )
    parser.add_argument("--max-attempts", type=int, default=2, help="attempts per candidate")
    parser.add_argument("--persist", action="store_true", help="cache in Postgres")
    parser.add_argument("--no-cache", action="store_true", help="force a full rebuild")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
