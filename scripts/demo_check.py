#!/usr/bin/env python
"""Is this machine ready to run the demo?

    make demo-check

Read-only. Prints a checklist and exits non-zero if anything required is
missing, so it can be run immediately before presenting. Everything it reports
is observed now, not remembered from a previous run.

Optional rows are marked and never fail the check: RepoLab's deterministic path
works without credentials, and saying so is more useful than refusing to start.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repolab.config import get_settings  # noqa: E402

#: The repositories the demo uses, and what each one demonstrates.
DEMO_REPOS: tuple[tuple[str, str], ...] = (
    ("https://github.com/AlexIoannides/pyspark-example-project", "P0 — Spark playground"),
    ("https://github.com/apache/cassandra", "P1 — CLI lesson"),
    ("https://github.com/apache/airflow", "P3 — hybrid detection"),
)

#: Images the demo needs locally. Pulling one mid-demo costs minutes.
DEMO_IMAGES: tuple[str, ...] = ("apache/spark:3.5.1-python3", "cassandra:5.0")

OK, WARN, BAD = "ok", "--", "!!"


class Check:
    """One line of the checklist."""

    def __init__(self, name: str, *, required: bool = True) -> None:
        self.name = name
        self.required = required
        self.state = BAD
        self.detail = ""

    def report(self, state: str, detail: str = "") -> Check:
        self.state = state
        self.detail = detail
        return self

    @property
    def failed(self) -> bool:
        return self.required and self.state == BAD

    def line(self) -> str:
        tag = "" if self.required else " (optional)"
        return f"  {self.state} {self.name}{tag}{': ' + self.detail if self.detail else ''}"


def check_infrastructure() -> list[Check]:
    from sqlalchemy import text as sql

    from repolab.db import build_engine

    settings = get_settings()
    checks: list[Check] = []

    postgres = Check("Postgres reachable")
    engine = None
    try:
        engine = build_engine(settings)
        with engine.connect() as connection:
            connection.execute(sql("select 1"))
        postgres.report(OK)
    except Exception as exc:  # noqa: BLE001 - the whole point is to report it
        postgres.report(BAD, f"{type(exc).__name__} — is `make up` running?")
    checks.append(postgres)

    migrations = Check("Migrations at head")
    if engine is not None and postgres.state == OK:
        try:
            with engine.connect() as connection:
                current = connection.execute(
                    sql("select version_num from alembic_version")
                ).scalar()
            expected = _expected_revision()
            if current == expected:
                migrations.report(OK, str(current))
            else:
                migrations.report(BAD, f"at {current}, head is {expected} — run `make db-upgrade`")
        except Exception as exc:  # noqa: BLE001
            migrations.report(BAD, f"{type(exc).__name__} — run `make db-upgrade`")
    else:
        migrations.report(BAD, "unknown while Postgres is unreachable")
    checks.append(migrations)

    redis = Check("Redis reachable")
    try:
        from redis import Redis

        Redis.from_url(settings.redis_url).ping()
        redis.report(OK)
    except Exception as exc:  # noqa: BLE001
        redis.report(BAD, f"{type(exc).__name__} — is `make up` running?")
    checks.append(redis)

    docker = Check("Docker available")
    from repolab.sandbox.docker import docker_available

    docker.report(OK) if docker_available() else docker.report(BAD, "start Docker Desktop")
    checks.append(docker)

    if engine is not None:
        engine.dispose()
    return checks


def _expected_revision() -> str | None:
    """The newest migration on disk, by reading the revision graph's tip."""
    versions = REPO_ROOT / "alembic" / "versions"
    revisions: dict[str, str | None] = {}
    for path in versions.glob("*.py"):
        text = path.read_text()
        revision = _literal(text, "revision: str = ")
        down = _literal(text, "down_revision: str | None = ")
        if revision:
            revisions[revision] = down
    children = {down for down in revisions.values() if down}
    heads = [revision for revision in revisions if revision not in children]
    return heads[0] if len(heads) == 1 else None


def _literal(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            return None if value == "None" else value.strip("\"'")
    return None


def check_images() -> list[Check]:
    from repolab.sandbox.docker import image_present

    checks = []
    for image in DEMO_IMAGES:
        check = Check(f"Image {image}")
        if image_present(image):
            check.report(OK)
        else:
            check.report(BAD, "run `make demo-prepare` — pulling it mid-demo costs minutes")
        checks.append(check)
    return checks


def check_repositories() -> list[Check]:
    from repolab.db import build_engine, session_scope
    from repolab.db.builds import load_builds
    from repolab.db.lessons import load_lesson
    from repolab.db.store import load_latest_ingestion

    checks: list[Check] = []
    engine = build_engine(get_settings())
    try:
        for url, purpose in DEMO_REPOS:
            check = Check(f"{purpose}")
            with session_scope(engine) as session:
                ingestion = load_latest_ingestion(session, url)
                if ingestion is None:
                    check.report(BAD, "not ingested — run `make demo-prepare`")
                    checks.append(check)
                    continue

                commit = ingestion.commit_sha[:12]
                builds = load_builds(session, url, ingestion.commit_sha)
                lesson = load_lesson(session, url, ingestion.commit_sha)

            detail = f"ingested at {commit}"
            if builds:
                rungs = ", ".join(sorted(artifact.kind.value for artifact in builds))
                detail += f"; cached builds: {rungs}"
            if lesson is not None:
                detail += f"; lesson by {lesson.generated_by}"
            check.report(OK, detail)
            checks.append(check)
    finally:
        engine.dispose()
    return checks


def check_credentials() -> list[Check]:
    settings = get_settings()

    model = Check("Model credentials", required=False)
    if settings.nebius_api_key is not None and any(settings.configured_models.values()):
        configured = [tier for tier, value in settings.configured_models.items() if value]
        model.report(OK, f"tiers configured: {', '.join(configured)}")
    else:
        model.report(WARN, "unset — every deterministic path still works")

    research = Check("Research credentials", required=False)
    if settings.tavily_api_key is not None:
        research.report(OK)
    else:
        research.report(WARN, "unset — answers will cite repository code only")

    pricing = Check("Model pricing", required=False)
    if settings.model_pricing:
        pricing.report(OK)
    else:
        pricing.report(WARN, "unset — cost metrics will read 'no data'")

    return [model, research, pricing]


def main() -> int:
    print("RepoLab demo preflight\n")

    sections = (
        ("infrastructure", check_infrastructure),
        ("images", check_images),
        ("demo repositories", check_repositories),
        ("credentials", check_credentials),
    )

    failures = 0
    for title, run in sections:
        print(title)
        try:
            checks = run()
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed check
            print(f"  {BAD} could not be checked: {type(exc).__name__}: {exc}")
            failures += 1
            print()
            continue
        for check in checks:
            print(check.line())
            failures += 1 if check.failed else 0
        print()

    if failures:
        print(f"NOT READY — {failures} required check(s) failed.")
        return 1

    print("READY — `make dev`, then http://localhost:3000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
