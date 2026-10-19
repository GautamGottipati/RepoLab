"""The KPI dashboard.

    GET /metrics

One endpoint, one aggregate read. Every figure is computed from recorded
history by :mod:`repolab.analytics.metrics`, and every figure that cannot be is
labelled rather than filled in — including the north-star metric, which is named
and deliberately never given a number.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_session
from repolab.analytics.metrics import Dashboard, build_dashboard

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=Dashboard)
def dashboard(session: Annotated[Session, Depends(get_session)]) -> Dashboard:
    """Developer learning activity and RepoLab's own operational performance.

    Read-only aggregates over indexed columns. Nothing here is slow enough to
    need a worker, and nothing here writes.
    """
    return build_dashboard(session)
