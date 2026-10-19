"""Product and agent-evaluation instrumentation, and the KPIs read back from it.

Recorded since Phase 4 so the KPIs could be computed from real history rather
than reconstructed afterwards; read since Phase 12 by
:mod:`repolab.analytics.metrics`, which is as careful about what the data cannot
say as about what it can.
"""

from __future__ import annotations

from repolab.analytics.events import (
    Analytics,
    AnalyticsEvent,
    AnalyticsEventType,
    AnalyticsWriter,
    CollectingAnalyticsWriter,
    DbAnalyticsWriter,
    NullAnalyticsWriter,
)
from repolab.analytics.metrics import (
    Dashboard,
    DatasetProvenance,
    Metric,
    MetricGroup,
    MetricStatus,
    build_dashboard,
)
from repolab.analytics.model_telemetry import DbModelTelemetry, db_model_telemetry

__all__ = [
    "Analytics",
    "AnalyticsEvent",
    "AnalyticsEventType",
    "AnalyticsWriter",
    "CollectingAnalyticsWriter",
    "Dashboard",
    "DatasetProvenance",
    "DbAnalyticsWriter",
    "DbModelTelemetry",
    "Metric",
    "MetricGroup",
    "MetricStatus",
    "NullAnalyticsWriter",
    "build_dashboard",
    "db_model_telemetry",
]
