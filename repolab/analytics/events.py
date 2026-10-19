"""Product analytics.

Instrumented from the beginning so the KPIs in CLAUDE.md -- time to repo ready,
build success rate, questions-to-experiments conversion -- can be computed later
from real history rather than reconstructed. A writer only: no dashboard yet.

Analytics is best-effort by design. A failure to record an event must never fail
the work being measured.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


class AnalyticsEventType(StrEnum):
    """Product events RepoLab records."""

    REPO_SUBMITTED = "repo_submitted"
    REPO_MANIFEST_CREATED = "repo_manifest_created"
    REPO_READY = "repo_ready"

    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"

    TOOL_CALLED = "tool_called"

    # Learning
    LESSON_STARTED = "lesson_started"
    LESSON_COMPLETED = "lesson_completed"
    HINT_SHOWN = "hint_shown"
    """A step was judged failed, so its hint was returned.

    Named for what actually happens. A learner does not ask RepoLab for a hint;
    the hint comes back with a failed check, and calling this `hint_requested`
    would describe an interaction that does not exist.
    """

    COMMAND_EXECUTED = "command_executed"

    # Builds. Recorded per candidate, which is the only way a build success rate
    # can exist: the cache keeps successful builds only, so the `builds` table
    # can never report a failure.
    BUILD_STARTED = "build_started"
    BUILD_FAILED = "build_failed"
    BUILD_RECOVERED = "build_recovered"
    """A candidate failed an attempt and a later attempt of the same candidate
    passed its smoke test."""

    QUESTION_ASKED = "question_asked"
    EXPERIMENT_PROPOSED = "experiment_proposed"
    EXPERIMENT_APPROVED = "experiment_approved"
    EXPERIMENT_REJECTED = "experiment_rejected"
    EXPERIMENT_STARTED = "experiment_started"
    EXPERIMENT_COMPLETED = "experiment_completed"
    EXPERIMENT_FAILED = "experiment_failed"


class AnalyticsEvent(BaseModel):
    """One recorded product event."""

    type: AnalyticsEventType
    job_id: str | None = None
    repo_url: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class AnalyticsWriter(Protocol):
    """Records product events."""

    def record(self, event: AnalyticsEvent) -> None: ...


class CollectingAnalyticsWriter:
    """Keeps events in memory. Used by tests."""

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    def record(self, event: AnalyticsEvent) -> None:
        self.events.append(event)

    def types(self) -> list[AnalyticsEventType]:
        return [event.type for event in self.events]

    def of_type(self, event_type: AnalyticsEventType) -> list[AnalyticsEvent]:
        return [event for event in self.events if event.type is event_type]


class NullAnalyticsWriter:
    """Discards events."""

    def record(self, event: AnalyticsEvent) -> None:
        return None


class DbAnalyticsWriter:
    """Appends events to Postgres.

    Swallows its own failures: analytics is observation, and must not be able to
    break the thing it observes.
    """

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    def record(self, event: AnalyticsEvent) -> None:
        from repolab.db.models import AnalyticsEventRecord

        try:
            with self._session_factory() as session:
                session.add(
                    AnalyticsEventRecord(
                        type=event.type.value,
                        job_id=event.job_id,
                        repo_url=event.repo_url,
                        properties=event.properties or None,
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - never fail the measured work
            log.warning("analytics.write_failed", type=event.type.value, error=str(exc))


class Analytics:
    """Convenience wrapper binding a writer to one job."""

    def __init__(
        self,
        writer: AnalyticsWriter,
        *,
        job_id: str | None = None,
        repo_url: str | None = None,
    ) -> None:
        self._writer = writer
        self._job_id = job_id
        self._repo_url = repo_url

    def record(self, event_type: AnalyticsEventType, **properties: Any) -> None:
        self._writer.record(
            AnalyticsEvent(
                type=event_type,
                job_id=self._job_id,
                repo_url=self._repo_url,
                properties=properties,
            )
        )
