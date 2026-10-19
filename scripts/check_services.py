#!/usr/bin/env python
"""Probe the external capabilities RepoLab depends on and print a report.

Usage:
    make check
    uv run python scripts/check_services.py [--json out.json] [--markdown]

The script is deliberately honest about what it did and did not test. With no
credentials configured, external rows report SKIP rather than PASS or FAIL: an
untested capability is unknown, not working and not broken. Only a *required*
capability that actually failed produces a non-zero exit code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repolab.capability import (  # noqa: E402
    CapabilityReport,
    ProbeStatus,
    failed,
    guarded,
    probe_inference,
    probe_model_catalog,
    probe_postgres,
    probe_redis,
    probe_research,
    probe_sandbox,
    probe_structured_output,
    probe_tool_calling,
    skipped,
)
from repolab.config import Settings, get_settings  # noqa: E402

NO_NEBIUS_KEY = "NEBIUS_API_KEY not set"
NO_PROJECT_ID = "NEBIUS_PROJECT_ID not set"
NO_TAVILY_KEY = "TAVILY_API_KEY not set"

SANDBOX_CAPABILITIES = (
    ("Sandbox creation", True),
    ("Sandbox command", True),
    ("Sandbox state preserved", False),
    ("Sandbox python present", False),
)


async def check_local_infra(report: CapabilityReport, settings: Settings) -> None:
    report.add(
        await guarded(
            "Postgres connection",
            lambda: probe_postgres(settings.database_url),
        )
    )
    report.add(
        await guarded(
            "Redis connection",
            lambda: probe_redis(settings.redis_url),
        )
    )


async def check_inference(report: CapabilityReport, settings: Settings) -> None:
    """Catalog, per-model inference, structured output, and tool calling."""
    tiers = settings.configured_models

    if settings.nebius_api_key is None:
        report.add(skipped("Model catalog listing", NO_NEBIUS_KEY))
        for tier in tiers:
            name = tier.capitalize()
            report.add(skipped(f"{name} model ID resolves", NO_NEBIUS_KEY))
            report.add(skipped(f"{name} inference", NO_NEBIUS_KEY))
            report.add(skipped(f"{name} structured JSON", NO_NEBIUS_KEY))
            report.add(skipped(f"{name} native tool calling", NO_NEBIUS_KEY, required=False))
        return

    from repolab.llm.nebius import NebiusLLMProvider

    llm = NebiusLLMProvider(
        api_key=settings.nebius_api_key.get_secret_value(),
        base_url=settings.nebius_base_url,
    )
    try:
        # The catalog probe emits one row per tier plus the listing row itself,
        # so it is awaited directly rather than through `guarded`.
        try:
            report.extend(await probe_model_catalog(llm, tiers))
        except Exception as exc:  # noqa: BLE001 - recorded as a finding
            report.add(failed("Model catalog listing", exc))
            for tier in tiers:
                report.add(skipped(f"{tier.capitalize()} model ID resolves", "catalog unavailable"))

        for tier, model_id in tiers.items():
            name = tier.capitalize()
            if not model_id:
                unset = f"MODEL_{tier.upper()} not set"
                report.add(skipped(f"{name} inference", unset))
                report.add(skipped(f"{name} structured JSON", unset))
                report.add(skipped(f"{name} native tool calling", unset, required=False))
                continue

            report.add(
                await guarded(
                    f"{name} inference",
                    lambda t=tier, m=model_id: probe_inference(llm, t, m),
                )
            )
            report.add(
                await guarded(
                    f"{name} structured JSON",
                    lambda t=tier, m=model_id: probe_structured_output(llm, t, m),
                )
            )
            report.add(
                await guarded(
                    f"{name} native tool calling",
                    lambda t=tier, m=model_id: probe_tool_calling(llm, t, m),
                    required=False,
                )
            )
    finally:
        await llm.close()


async def check_sandbox(report: CapabilityReport, settings: Settings) -> None:
    # Sandboxes need both credentials; inference needs only the key.
    reason = (
        NO_NEBIUS_KEY
        if settings.nebius_api_key is None
        else NO_PROJECT_ID
        if settings.nebius_project_id is None
        else None
    )
    if reason is not None:
        for capability, required in SANDBOX_CAPABILITIES:
            report.add(skipped(capability, reason, required=required))
        return

    from repolab.sandbox.nebius import NebiusSandboxProvider

    assert settings.nebius_api_key is not None  # narrowed above
    sandbox: NebiusSandboxProvider | None = None
    try:
        sandbox = NebiusSandboxProvider(
            api_key=settings.nebius_api_key.get_secret_value(),
            project_id=settings.nebius_project_id,
        )
        rows = await probe_sandbox(sandbox)
        report.extend(rows)
        report.add(
            skipped(
                "Sandbox timeout controls",
                (
                    "per-call timeouts are passed on execute; the provider exposes no "
                    "separate timeout surface to probe"
                ),
                required=False,
            )
        )
    except Exception as exc:  # noqa: BLE001 - recorded as a finding
        # Keep the table shape stable: report the failure once, then mark the
        # capabilities it prevented from being tested.
        reported = {r.capability for r in report.results}
        report.add(failed("Sandbox creation", exc))
        for capability, required in SANDBOX_CAPABILITIES:
            if capability != "Sandbox creation" and capability not in reported:
                report.add(skipped(capability, "sandbox unavailable", required=required))
    finally:
        if sandbox is not None:
            # Cleanup must never mask the findings above.
            with suppress(Exception):
                await sandbox.close()


async def check_research(report: CapabilityReport, settings: Settings) -> None:
    if settings.tavily_api_key is None:
        report.add(skipped("Tavily official search", NO_TAVILY_KEY))
        return

    from repolab.research.tavily import TavilyResearchProvider

    research = TavilyResearchProvider(api_key=settings.tavily_api_key.get_secret_value())
    report.add(await guarded("Tavily official search", lambda: probe_research(research)))


async def run_all() -> CapabilityReport:
    settings = get_settings()
    report = CapabilityReport()
    await check_local_infra(report, settings)
    await check_inference(report, settings)
    await check_sandbox(report, settings)
    await check_research(report, settings)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check RepoLab external capabilities.")
    parser.add_argument("--json", type=Path, help="write the full report (with detail) here")
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="also print the table as Markdown for docs/PROGRESS.md",
    )
    args = parser.parse_args()

    report = asyncio.run(run_all())

    print()
    print(report.render_table())
    print()

    if args.markdown:
        print(report.render_markdown())
        print()

    skips = [r for r in report.results if r.status is ProbeStatus.SKIP]
    if skips:
        print(f"{len(skips)} capability/capabilities not tested (see Notes).")

    failures = report.blocking_failures()
    if failures:
        print("Required capabilities failed:")
        for failure in failures:
            print(f"  - {failure.capability}: {failure.notes}")
    else:
        print("No required capability failed.")

    if args.json:
        args.json.write_text(json.dumps(report.model_dump(mode="json"), indent=2))
        print(f"Full report written to {args.json}")

    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
