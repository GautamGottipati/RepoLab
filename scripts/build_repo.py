#!/usr/bin/env python
"""Resolve or build a runnable environment for a repository, then report READY.

    repo -> resolve/build -> smoke test -> checkpoint -> READY

Usage:
    make build URL=https://github.com/owner/name
    uv run python scripts/build_repo.py URL [--provider auto|nebius|fake]
                                            [--no-cache] [--json out.json]

The provider is always named in the output. A `READY` produced by the fake provider
says so explicitly, because a simulated build must never be mistaken for a real
one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repolab.builds import (  # noqa: E402
    BuildRequest,
    BuildResolver,
    BuildUnavailableError,
    DbBuildCache,
    InMemoryBuildCache,
    NullBuildCache,
    UnsupportedRepositoryError,
    plan_for,
)
from repolab.classify import classify  # noqa: E402
from repolab.config import Settings, get_settings  # noqa: E402
from repolab.ingest.clone import TemporaryClone  # noqa: E402
from repolab.ingest.content import ReadBudget  # noqa: E402
from repolab.ingest.detectors import DetectionContext, detect_all  # noqa: E402
from repolab.ingest.languages import detect_languages  # noqa: E402
from repolab.ingest.limits import DEFAULT_LIMITS, LimitReport  # noqa: E402
from repolab.ingest.tree import build_file_tree  # noqa: E402
from repolab.ingest.url import InvalidRepoUrlError, validate_repo_url  # noqa: E402
from repolab.manifest.builder import build_manifest  # noqa: E402
from repolab.manifest.models import RepoManifest  # noqa: E402

#: Scripted responses that let the fake provider model a plausible Spark build.
FAKE_SCRIPT_MARKER = "REPOLAB_SPARK_OK"


def build_provider(choice: str, settings: Settings) -> tuple[object, str]:
    """Pick a provider, returning it and why it was chosen."""
    from repolab.sandbox import FakeSandboxProvider, ScriptedCommand

    def fake(reason: str) -> tuple[object, str]:
        from repolab.builds.spark import SMOKE_MARKER

        provider = FakeSandboxProvider(
            scripted={
                "python3 /work/repolab_smoke.py": ScriptedCommand(stdout=f"{SMOKE_MARKER} 3.5.1\n"),
            }
        )
        return provider, reason

    if choice == "fake":
        return fake("requested with --provider fake")

    missing = []
    if settings.nebius_api_key is None:
        missing.append("NEBIUS_API_KEY")
    if settings.nebius_project_id is None:
        missing.append("NEBIUS_PROJECT_ID")

    if choice == "nebius":
        if missing:
            raise SystemExit(f"--provider nebius needs {' and '.join(missing)}")
    elif missing:
        return fake(f"no {' or '.join(missing)}")

    from repolab.sandbox.nebius import NebiusSandboxProvider

    assert settings.nebius_api_key is not None
    assert settings.nebius_project_id is not None
    return (
        NebiusSandboxProvider(
            api_key=settings.nebius_api_key.get_secret_value(),
            project_id=settings.nebius_project_id,
        ),
        "credentials configured",
    )


async def investigate(url: str, clone_path: Path, commit_sha: str) -> RepoManifest:
    """Produce the manifest the build plan is derived from."""
    report = LimitReport()
    budget = ReadBudget(DEFAULT_LIMITS, report)
    tree = build_file_tree(clone_path, limits=DEFAULT_LIMITS, report=report)
    languages = detect_languages(tree)
    context = DetectionContext(root=clone_path, tree=tree, commit_sha=commit_sha)

    outcome = detect_all(context, budget)
    classification = await classify(
        outcome.run, entrypoints=outcome.entrypoints, services=outcome.services
    )
    return build_manifest(
        repo_url=url,
        commit_sha=commit_sha,
        outcome=outcome,
        languages=languages,
        classification=classification,
    )


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()

    try:
        repo_url = validate_repo_url(args.url)
    except InvalidRepoUrlError as exc:
        print(f"invalid repository URL: {exc}", file=sys.stderr)
        return 2

    provider, reason = build_provider(args.provider, settings)
    is_fake = getattr(provider, "name", "") == "fake"

    choice = args.cache
    if args.no_cache:
        choice = "none"
    if choice == "auto":
        # By default a fake build never reaches the shared Postgres cache, or a
        # later real run would find a simulated artifact sitting there.
        choice = "memory" if is_fake else "postgres"

    if choice == "none":
        cache = NullBuildCache()
        cache_label = "disabled"
    elif choice == "memory":
        cache = InMemoryBuildCache()
        cache_label = "in-memory" + (" (fake provider)" if is_fake else "")
    else:
        from repolab.db import build_engine, session_scope

        engine = build_engine(settings)
        cache = DbBuildCache(lambda: session_scope(engine))
        cache_label = "postgres" + (" [SIMULATED build]" if is_fake else "")

    print(f"provider       {getattr(provider, 'name', '?')} ({reason})")
    print(f"cache          {cache_label}")

    with TemporaryClone(repo_url, limits=DEFAULT_LIMITS) as clone:
        print(f"commit         {clone.commit_sha[:12]} ({clone.branch or 'detached'})")
        manifest = await investigate(repo_url.normalized, Path(clone.path), clone.commit_sha)
        primary = manifest.primary_surface
        print(f"primary        {primary.type.value if primary else 'none'}")

        try:
            plan = plan_for(manifest)
        except UnsupportedRepositoryError as exc:
            # A shape RepoLab cannot build yet is a clear message, not a traceback.
            print(f"NOT SUPPORTED  {exc}", file=sys.stderr)
            await provider.close()  # type: ignore[attr-defined]
            return 3

        print()
        print(plan.describe())
        print()

        request = BuildRequest(
            repo_url=repo_url.normalized,
            commit_sha=clone.commit_sha,
            use_cache=not args.no_cache,
        )
        resolver = BuildResolver(provider, cache=cache)  # type: ignore[arg-type]

        try:
            artifact = await resolver.resolve(request, plan, Path(clone.path))
        except BuildUnavailableError as exc:
            print(f"BUILD FAILED   {exc}", file=sys.stderr)
            return 1
        finally:
            await provider.close()  # type: ignore[attr-defined]

    print(f"source         {artifact.source.value}")
    print(f"fidelity       {artifact.fidelity.value}")
    if artifact.capabilities.deviations:
        print("deviations:")
        for item in artifact.capabilities.deviations:
            print(f"  - {item}")
    if artifact.capabilities.versions:
        print(f"versions       {artifact.capabilities.versions}")
    smoke = artifact.smoke
    print(
        f"smoke          {'passed' if smoke and smoke.passed else 'failed'}"
        + (f" ({smoke.name})" if smoke else "")
    )
    print(f"checkpoint     {artifact.checkpoint_tag or artifact.checkpoint_id}")
    print()

    if args.json:
        args.json.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2))
        print(f"artifact written to {args.json}")

    if not artifact.ready:
        print("NOT READY      the smoke test did not pass", file=sys.stderr)
        return 1

    label = "fake provider - SIMULATED, not a real build" if is_fake else provider.name  # type: ignore[attr-defined]
    print(f"READY          ({label}, checkpoint {artifact.checkpoint_id})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a runnable environment.")
    parser.add_argument("url", help="public https repository URL")
    parser.add_argument(
        "--provider",
        choices=("auto", "nebius", "fake"),
        default="auto",
        help="auto uses Nebius when credentials exist, otherwise the fake",
    )
    parser.add_argument("--no-cache", action="store_true", help="force a rebuild")
    parser.add_argument(
        "--cache",
        choices=("auto", "postgres", "memory", "none"),
        default="auto",
        help="auto keeps simulated builds out of the shared Postgres cache",
    )
    parser.add_argument("--json", type=Path, help="write the artifact here")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
