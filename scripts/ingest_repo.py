#!/usr/bin/env python
"""Ingest a public repository and report what was found.

Usage:
    make ingest URL=https://github.com/owner/name
    uv run python scripts/ingest_repo.py URL [--json out.json] [--persist] [--llm]

Ingestion is static inspection only: the repository is cloned shallowly and read.
No dependency install, build tool or setup script is ever run.

LLM enrichment is opt-in via --llm and requires NEBIUS_API_KEY plus MODEL_*; the
manifest and architecture are produced deterministically either way.
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

from repolab.config import get_settings  # noqa: E402
from repolab.ingest import (  # noqa: E402
    CloneError,
    IngestLimits,
    InvalidRepoUrlError,
    ingest_repository,
    summarize,
)
from repolab.llm import ModelRouter  # noqa: E402


def build_router_if_requested(enabled: bool) -> tuple[ModelRouter | None, str | None]:
    """Return a router, or the reason there isn't one."""
    if not enabled:
        return None, "enrichment not requested (pass --llm to enable)"

    settings = get_settings()
    if settings.nebius_api_key is None:
        return None, "NEBIUS_API_KEY is not set"
    if not any(settings.configured_models.values()):
        return None, "no MODEL_NANO/MODEL_SUPER/MODEL_ULTRA configured"

    from repolab.llm import build_router

    return build_router(settings), None


async def run(args: argparse.Namespace) -> int:
    router, reason = build_router_if_requested(args.llm)
    if reason:
        print(f"note: {reason}", file=sys.stderr)

    limits = IngestLimits()
    if args.max_files:
        limits = limits.model_copy(update={"max_files": args.max_files})

    try:
        result = await ingest_repository(args.url, router=router, limits=limits)
    except InvalidRepoUrlError as exc:
        print(f"invalid repository URL: {exc}", file=sys.stderr)
        return 2
    except CloneError as exc:
        print(f"clone failed: {exc}", file=sys.stderr)
        return 1

    print()
    print(summarize(result))
    print()
    print("classification")
    for surface in result.classification.surfaces:
        marker = "*" if surface.primary else " "
        print(f"  {marker} {surface.type.value:16s} {surface.confidence:<5} {surface.name}")
        print(f"      reason:   {surface.reason}")
        paths = ", ".join(ref.path for ref in surface.evidence[:4]) or "none"
        print(f"      evidence: {paths}")
    print()

    if args.json:
        args.json.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
        print(f"full result written to {args.json}")

    if args.persist:
        from repolab.db import build_engine, save_ingestion, session_scope

        engine = build_engine()
        try:
            with session_scope(engine) as session:
                ingestion = save_ingestion(session, result)
                print(f"persisted ingestion id={ingestion.id} commit={result.commit_sha[:12]}")
        finally:
            engine.dispose()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a public repository.")
    parser.add_argument("url", help="public https repository URL")
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--persist", action="store_true", help="save to Postgres")
    parser.add_argument("--llm", action="store_true", help="enable bounded Nano/Super enrichment")
    parser.add_argument("--max-files", type=int, default=None, help="override the file limit")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
