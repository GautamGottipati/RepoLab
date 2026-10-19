"""Postgres persistence. Postgres remains RepoLab's source of truth."""

from __future__ import annotations

from repolab.db.base import Base, build_engine, build_session_factory, session_scope
from repolab.db.jobs import (
    append_event,
    create_job,
    get_job,
    last_event_at,
    mark_finished,
    mark_running,
    read_events_after,
    save_agent_run,
)
from repolab.db.models import (
    AgentRunRecord,
    AgentStepRecord,
    AnalyticsEventRecord,
    ArchitectureRecord,
    Ingestion,
    IngestionEvidence,
    Job,
    JobEventRecord,
    ModelCallRecord,
    RepoManifestRecord,
    Repository,
    ToolCallRecord,
)
from repolab.db.store import load_latest_ingestion, save_ingestion

__all__ = [
    "AgentRunRecord",
    "AgentStepRecord",
    "AnalyticsEventRecord",
    "ArchitectureRecord",
    "Base",
    "Ingestion",
    "IngestionEvidence",
    "Job",
    "JobEventRecord",
    "ModelCallRecord",
    "RepoManifestRecord",
    "Repository",
    "ToolCallRecord",
    "append_event",
    "build_engine",
    "build_session_factory",
    "create_job",
    "get_job",
    "last_event_at",
    "load_latest_ingestion",
    "mark_finished",
    "mark_running",
    "read_events_after",
    "save_agent_run",
    "save_ingestion",
    "session_scope",
]
