#!/usr/bin/env python
"""Scan everything RepoLab has written for a configured credential.

    make check-secrets

The test suite proves no *code path* leaks a secret. This proves no secret is
sitting in data RepoLab has already produced: job errors, event messages and
payloads, analytics properties, stored lessons and experiments, and the browser
artifacts Playwright leaves behind.

Run before a demo, and especially before sharing a screen recording, a database
dump or a test-results directory.

Nothing is printed except counts and locations. A tool that reports a leak by
echoing the leaked value is not much of an improvement.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Settings fields holding something that must never be written down.
SECRET_FIELDS = ("nebius_api_key", "tavily_api_key")

#: Directories whose contents are shared by accident more often than on purpose.
ARTIFACT_DIRS = ("web/test-results", "web/playwright-report")

#: Shapes that are credentials whoever configured them: a long opaque token.
#: Checked in addition to the configured values, so a *former* key left in the
#: data is still found.
SUSPICIOUS = ("sk-", "tvly-", "Bearer ", "eyJ")

#: Minimum length before a suspicious prefix is treated as a real value.
MIN_TOKEN = 20


def configured_secrets() -> list[str]:
    """The secret values currently configured, read once and never printed."""
    from repolab.config import get_settings

    settings = get_settings()
    found: list[str] = []
    for field in SECRET_FIELDS:
        value = getattr(settings, field, None)
        if value is not None:
            revealed = value.get_secret_value()
            if revealed:
                found.append(revealed)
    return found


def _hits(text: str, secrets: list[str]) -> list[str]:
    """What was found in *text*, named rather than quoted."""
    found = ["a configured credential" for secret in secrets if secret and secret in text]
    for prefix in SUSPICIOUS:
        index = text.find(prefix)
        if index >= 0 and len(text) - index >= MIN_TOKEN:
            candidate = text[index : index + 120].split()[0]
            if len(candidate) >= MIN_TOKEN:
                found.append(f"a {prefix!r}-shaped token")
    return found


def scan_database(secrets: list[str]) -> list[str]:
    """Every column RepoLab writes free text or arbitrary payloads into."""
    from sqlalchemy import text as sql

    from repolab.config import get_settings
    from repolab.db import build_engine

    problems: list[str] = []
    queries = {
        "jobs.error": "select id, error from jobs where error is not null",
        "job_events.message": "select job_id, message from job_events",
        "job_events.data": "select job_id, data::text from job_events where data is not null",
        "analytics_events.properties": (
            "select type, properties::text from analytics_events where properties is not null"
        ),
        "experiments.error": "select id, error from experiments where error is not null",
        "experiments.explanation": (
            "select id, explanation::text from experiments where explanation is not null"
        ),
        "lessons.lesson": "select id, lesson::text from lessons",
        "builds.smoke": "select id, smoke::text from builds where smoke is not null",
        "builds.attempts": "select id, attempts::text from builds where attempts is not null",
    }

    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            for where, query in queries.items():
                try:
                    rows = connection.execute(sql(query)).all()
                except Exception as exc:  # noqa: BLE001 - a missing table is not a leak
                    print(f"  ?  {where}: could not read ({type(exc).__name__})")
                    continue

                count = 0
                for identifier, value in rows:
                    for hit in _hits(str(value or ""), secrets):
                        problems.append(f"{where} (row {identifier}): {hit}")
                        count += 1
                print(f"  {'!!' if count else 'ok'} {where}: {len(rows)} row(s), {count} hit(s)")
    finally:
        engine.dispose()

    return problems


def scan_artifacts(secrets: list[str]) -> list[str]:
    """Playwright's screenshots, traces and error contexts."""
    problems: list[str] = []
    for relative in ARTIFACT_DIRS:
        directory = REPO_ROOT / relative
        if not directory.exists():
            print(f"  -- {relative}: absent")
            continue

        files = [path for path in directory.rglob("*") if path.is_file()]
        count = 0
        for path in files:
            if path.suffix in {".png", ".zip", ".webm"}:
                continue  # binary: a secret could only get there through text
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for hit in _hits(text, secrets):
                problems.append(f"{path.relative_to(REPO_ROOT)}: {hit}")
                count += 1
        print(f"  {'!!' if count else 'ok'} {relative}: {len(files)} file(s), {count} hit(s)")

    return problems


def main() -> int:
    secrets = configured_secrets()
    print(f"configured secrets to look for: {len(secrets)}")
    if not secrets:
        print("  (none configured; the shape-based checks still run)")

    print("\ndatabase")
    problems = scan_database(secrets)

    print("\nbrowser artifacts")
    problems += scan_artifacts(secrets)

    print()
    if problems:
        print(f"FOUND {len(problems)} possible exposure(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("CLEAN — no configured credential and no credential-shaped token found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
