"""The experiment aggregate.

One object carrying a question from asked to answered: the plan, the state it is
in, the runs that produced measurements, the statistics computed from them, the
evidence bundle and the verified explanation -- plus the reproducibility record
that says what it would take to do it again.

The state machine lives here. ``approve`` and ``reject`` are the only ways out of
``AWAITING_APPROVAL``, and :meth:`may_execute` is what the executor consults, so
there is exactly one place where the approval gate can be opened.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, computed_field

from repolab.evidence.models import EvidenceBundle
from repolab.experiments.explanation import VerifiedExplanation
from repolab.experiments.models import (
    ExperimentPlan,
    ExperimentStatus,
    ProposedPlan,
    ReproducibilityRecord,
    VariantRun,
)
from repolab.experiments.statistics import ExperimentStatistics
from repolab.experiments.validation import PlanRejected


def _now() -> datetime:
    return datetime.now(UTC)


class IllegalTransitionError(RuntimeError):
    """An experiment was moved to a state it cannot reach from where it is."""

    def __init__(self, current: ExperimentStatus, target: ExperimentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move an experiment from {current.value} to {target.value}")


class Experiment(BaseModel):
    """One question, investigated by measurement."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str | None = None
    """The job whose event stream carries this experiment's progress."""

    question: str
    repo_url: str
    commit_sha: str = ""

    status: ExperimentStatus = ExperimentStatus.PROPOSED

    proposal: ProposedPlan | None = None
    """What the model suggested, kept so the audit trail shows what was asked for."""

    plan: ExperimentPlan | None = None
    rejection: PlanRejected | None = None
    """Why validation refused the proposal, when it did."""

    runs: list[VariantRun] = Field(default_factory=list)
    statistics: ExperimentStatistics | None = None
    evidence: EvidenceBundle | None = None
    explanation: VerifiedExplanation | None = None
    reproducibility: ReproducibilityRecord | None = None

    error: str | None = None
    explanation_error: str | None = None
    """Why the explanation is missing, when a model was configured but failed.

    Distinct from ``error``: the experiment itself succeeded and its
    measurements are real. Only the narrative is missing, and saying which is
    the honest version of an empty explanation panel.
    """

    approved_by: str | None = None

    created_at: datetime = Field(default_factory=_now)
    approved_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @computed_field
    @property
    def may_execute(self) -> bool:
        """Whether variants may run. The single source of truth for the gate."""
        return self.status.may_execute and self.plan is not None

    @computed_field
    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    # -- transitions ------------------------------------------------------

    def await_approval(self) -> None:
        """Present the plan and stop.

        Raises:
            IllegalTransitionError: there is no validated plan to approve.
        """
        if self.plan is None:
            raise IllegalTransitionError(self.status, ExperimentStatus.AWAITING_APPROVAL)
        self.status = ExperimentStatus.AWAITING_APPROVAL

    def approve(self, *, by: str | None = None) -> None:
        """Permit execution.

        Only from ``AWAITING_APPROVAL``: approving twice, or approving something
        that was never presented, is a bug rather than a no-op.

        Raises:
            IllegalTransitionError: the experiment is not awaiting approval.
        """
        if self.status is not ExperimentStatus.AWAITING_APPROVAL:
            raise IllegalTransitionError(self.status, ExperimentStatus.APPROVED)
        self.status = ExperimentStatus.APPROVED
        self.approved_by = by
        self.approved_at = _now()

    def reject(self, *, by: str | None = None) -> None:
        """Decline the experiment. Nothing runs.

        Raises:
            IllegalTransitionError: the experiment is not awaiting approval.
        """
        if self.status is not ExperimentStatus.AWAITING_APPROVAL:
            raise IllegalTransitionError(self.status, ExperimentStatus.REJECTED)
        self.status = ExperimentStatus.REJECTED
        self.approved_by = by
        self.finished_at = _now()

    def start(self) -> None:
        """Begin executing.

        Raises:
            IllegalTransitionError: the experiment has not been approved.
        """
        if self.status is not ExperimentStatus.APPROVED:
            raise IllegalTransitionError(self.status, ExperimentStatus.RUNNING)
        self.status = ExperimentStatus.RUNNING
        self.started_at = _now()

    def complete(self) -> None:
        self.status = ExperimentStatus.COMPLETED
        self.finished_at = _now()

    def fail(self, error: str) -> None:
        self.status = ExperimentStatus.FAILED
        self.error = error[:2000]
        self.finished_at = _now()

    def refuse(self, rejection: PlanRejected) -> None:
        """Record that the proposal did not survive validation."""
        self.rejection = rejection
        self.status = ExperimentStatus.FAILED
        self.error = str(rejection)
        self.finished_at = _now()

    # -- views ------------------------------------------------------------

    def approval_prompt(self) -> dict[str, object]:
        """What the user is shown before approving.

        Includes the adjustments validation made and the metrics it had to drop,
        so approval is given to the experiment that will actually run rather than
        to the one that was proposed.
        """
        if self.plan is None:
            return {"question": self.question, "status": self.status.value}
        return {
            "question": self.plan.question,
            "hypothesis": self.plan.hypothesis,
            "variable": self.plan.variable,
            "values": self.plan.values,
            "repeats": self.plan.repeats,
            "metrics": self.plan.metrics,
            "estimated_runs": self.plan.estimated_runs,
            "estimated_seconds": self.plan.estimated_seconds,
            "estimated_minutes": self.plan.estimated_minutes,
            "concurrency": self.plan.concurrency,
            "dropped_metrics": self.plan.dropped_metrics,
            "adjustments": self.plan.adjustments,
            "summary": self.plan.summary(),
        }
