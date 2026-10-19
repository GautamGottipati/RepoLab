#!/usr/bin/env python
"""Warm everything the demo needs, so nothing is built in front of an audience.

    make demo-prepare              # whatever is missing
    make demo-prepare ARGS=--force # redo it all

Four things, in the order that makes the next one cheaper:

1. pull the container images (a cold pull is minutes of silence);
2. ingest the three demo repositories (clone, investigate, manifest);
3. resolve the Cassandra ladder into the build cache (the longest step);
4. generate the lesson for the environment that resolved.

Idempotent. Anything already warm is reported and skipped, so this is safe to
run immediately before a demo as well as long before one.

Does its work in-process rather than through the API, so it needs Postgres,
Redis and Docker but no running server.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.demo_check import DEMO_IMAGES, DEMO_REPOS  # noqa: E402

CASSANDRA = "https://github.com/apache/cassandra"

#: Cassandra is large; ingestion stays bounded, as the ladder script does.
LARGE_REPO_LIMITS_FILES = 1_500
LARGE_REPO_LIMITS_BYTES = 24_000_000


def say(step: str, detail: str = "") -> None:
    print(f"  {step}{': ' + detail if detail else ''}", flush=True)


def pull_images(force: bool) -> None:
    from repolab.sandbox.docker import ensure_image, image_present

    print("images")
    for image in DEMO_IMAGES:
        if image_present(image) and not force:
            say("cached", image)
            continue
        started = time.perf_counter()
        status = ensure_image(image)
        elapsed = time.perf_counter() - started
        say("pulled" if status.ready else "FAILED", f"{image} in {elapsed:.0f}s")
    print()


async def ingest_repositories(force: bool) -> None:
    from repolab.config import get_settings
    from repolab.db import build_engine, session_scope
    from repolab.db.store import load_latest_ingestion, save_ingestion
    from repolab.ingest import ingest_repository
    from repolab.ingest.limits import IngestLimits

    print("demo repositories")
    engine = build_engine(get_settings())
    limits = IngestLimits(
        max_scanned_files=LARGE_REPO_LIMITS_FILES,
        max_total_read_bytes=LARGE_REPO_LIMITS_BYTES,
    )

    try:
        for url, purpose in DEMO_REPOS:
            with session_scope(engine) as session:
                existing = load_latest_ingestion(session, url)
                commit = existing.commit_sha[:12] if existing is not None else None

            if commit is not None and not force:
                say("ingested", f"{purpose} at {commit}")
                continue

            started = time.perf_counter()
            result = await ingest_repository(url, limits=limits)
            with session_scope(engine) as session:
                save_ingestion(session, result)
            say(
                "investigated",
                f"{purpose} at {result.commit_sha[:12]} in {time.perf_counter() - started:.0f}s",
            )
    finally:
        engine.dispose()
    print()


async def resolve_cassandra(force: bool) -> None:
    """Build the Cassandra ladder into the cache. The slow one."""
    from repolab.builds import (
        BuildLadder,
        BuildLimits,
        BuildRequest,
        BuildUnavailableError,
        DbBuildCache,
        candidates_for,
        needs_submodules,
    )
    from repolab.config import get_settings
    from repolab.db import build_engine, session_scope
    from repolab.db.builds import load_builds
    from repolab.db.store import load_latest_ingestion, load_manifest
    from repolab.ingest.clone import TemporaryClone
    from repolab.ingest.limits import DEFAULT_LIMITS
    from repolab.ingest.url import validate_repo_url
    from repolab.sandbox.docker_provider import DockerSandboxProvider

    print("cassandra environment")
    engine = build_engine(get_settings())
    try:
        with session_scope(engine) as session:
            ingestion = load_latest_ingestion(session, CASSANDRA)
            manifest = load_manifest(session, CASSANDRA)
            cached = (
                load_builds(session, CASSANDRA, ingestion.commit_sha)
                if ingestion is not None
                else []
            )

        if manifest is None or ingestion is None:
            say("skipped", "Cassandra is not ingested yet")
            print()
            return

        if cached and not force:
            rungs = ", ".join(sorted(artifact.kind.value for artifact in cached))
            say("cached", f"{len(cached)} rung(s) at {ingestion.commit_sha[:12]}: {rungs}")
            print()
            return

        url = validate_repo_url(CASSANDRA)
        candidates = candidates_for(manifest)
        say("building", f"{len(candidates)} candidate(s) — this is the slow step")

        started = time.perf_counter()
        with TemporaryClone(
            url, limits=DEFAULT_LIMITS, submodules=needs_submodules(candidates)
        ) as clone:
            ladder = BuildLadder(
                DockerSandboxProvider(),
                cache=DbBuildCache(lambda: session_scope(engine)),
                limits=BuildLimits(max_attempts=1),
            )
            try:
                result = await ladder.resolve(
                    BuildRequest(repo_url=url.normalized, commit_sha=clone.commit_sha),
                    candidates,
                    Path(clone.path),
                    manifest=manifest,
                )
            except BuildUnavailableError as exc:
                say("FAILED", str(exc)[:160])
                print()
                return

        say(
            "ready",
            f"serving {result.served.kind.value} ({result.served.fidelity.value}) "
            f"in {time.perf_counter() - started:.0f}s",
        )
    finally:
        engine.dispose()
    print()


async def generate_lesson(force: bool) -> None:
    from repolab.analytics.model_telemetry import db_model_telemetry
    from repolab.config import get_settings
    from repolab.db import build_engine, session_scope
    from repolab.db.builds import load_builds
    from repolab.db.lessons import load_lesson, save_lesson
    from repolab.db.store import load_latest_ingestion, load_manifest
    from repolab.ingest.clone import TemporaryClone
    from repolab.ingest.limits import DEFAULT_LIMITS
    from repolab.ingest.url import validate_repo_url
    from repolab.lessons.agent import LearningAgent

    print("cassandra lesson")
    settings = get_settings()
    engine = build_engine(settings)
    try:
        with session_scope(engine) as session:
            ingestion = load_latest_ingestion(session, CASSANDRA)
            manifest = load_manifest(session, CASSANDRA)
            existing = (
                load_lesson(session, CASSANDRA, ingestion.commit_sha)
                if ingestion is not None
                else None
            )
            artifacts = (
                load_builds(session, CASSANDRA, ingestion.commit_sha)
                if ingestion is not None
                else []
            )

        if manifest is None or not artifacts:
            say("skipped", "no ready environment to write a lesson for")
            print()
            return

        if existing is not None and existing.generated_by == "super" and not force:
            say("cached", f"{existing.step_count} steps, written by {existing.generated_by}")
            print()
            return

        router = None
        research = None
        if settings.nebius_api_key is not None and any(settings.configured_models.values()):
            from repolab.llm import build_router

            router = build_router(
                settings,
                telemetry=db_model_telemetry(lambda: session_scope(engine)),
            )
        if settings.tavily_api_key is not None:
            from repolab.research.tavily import TavilyResearchProvider

            research = TavilyResearchProvider(api_key=settings.tavily_api_key.get_secret_value())

        started = time.perf_counter()
        with TemporaryClone(validate_repo_url(CASSANDRA), limits=DEFAULT_LIMITS) as clone:
            lesson = await LearningAgent(router=router, research=research).generate(
                manifest, artifacts[0], Path(clone.path)
            )
        with session_scope(engine) as session:
            save_lesson(session, lesson)

        say(
            "written",
            f"{lesson.step_count} steps by {lesson.generated_by} "
            f"in {time.perf_counter() - started:.0f}s",
        )
    finally:
        engine.dispose()
    print()


async def run(args: argparse.Namespace) -> int:
    from repolab.sandbox.docker import docker_available

    print("RepoLab demo preparation\n")

    if not docker_available():
        print("Docker is not available. Start Docker Desktop and try again.")
        return 2

    pull_images(args.force)
    await ingest_repositories(args.force)
    await resolve_cassandra(args.force)
    await generate_lesson(args.force)

    print("Now run `make demo-check` to confirm, then `make dev`.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="redo every step, even the ones already warm",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
