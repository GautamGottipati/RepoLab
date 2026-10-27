"""Progressive-analysis metrics, computed from persisted artifact events only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.analytics.metrics import EAGER_ARTIFACTS, _progressive_analysis
from repolab.db.models import Job, JobEventRecord

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def session():
    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def job(
    session: Session, job_id: str, timeline: dict[str, float], *, cached=False, failed=()
) -> None:
    session.add(
        Job(
            id=job_id, repo_url="https://github.com/apache/spark", status="completed", created_at=T0
        )
    )
    for seq, (artifact_type, seconds) in enumerate(timeline.items(), start=1):
        session.add(
            JobEventRecord(
                job_id=job_id,
                seq=seq,
                type="artifact_failed" if artifact_type in failed else "artifact_ready",
                message="",
                step="artifacts",
                data={"artifact_type": artifact_type, "cache_hit": cached},
                created_at=T0 + timedelta(seconds=seconds),
            )
        )
    session.commit()


def value(group, key: str):
    return next(m for m in group.metrics if m.key == key)


def test_milestones_are_medians_over_cold_jobs_and_reuse_is_kept_apart(session) -> None:
    cold = {t: 10.0 + i * 10 for i, t in enumerate(EAGER_ARTIFACTS)}  # facts 10s ... summary 70s
    job(session, "cold-1", cold)
    job(session, "cold-2", {t: s * 2 for t, s in cold.items()})
    job(session, "warm", {t: 2.0 for t in EAGER_ARTIFACTS}, cached=True)
    group = _progressive_analysis(session)

    assert value(group, "time_to_manifest").value == 30.0  # median of 20 and 40
    assert value(group, "time_to_overview").value == 45.0  # structure: 30 and 60
    assert value(group, "time_to_architecture_summary").value == 105.0
    assert value(group, "time_to_full_analysis").samples == 2
    assert value(group, "time_to_full_analysis_cached").value == 2.0
    hit_rate = value(group, "artifact_cache_hit_rate")
    assert hit_rate.value == round(100 * 7 / 21, 1) and hit_rate.samples == 21
    assert value(group, "artifact_failure_rate").value == 0.0


def test_failures_count_and_an_empty_database_measures_nothing(session) -> None:
    group = _progressive_analysis(session)
    assert not any(m.measured for m in group.metrics)
    job(
        session,
        "j",
        {"repo.facts": 1, "repo.manifest": 2, "architecture.summary": 9},
        failed={"architecture.summary"},
    )
    group = _progressive_analysis(session)
    assert value(group, "artifact_failure_rate").value == round(100 / 3, 1)
