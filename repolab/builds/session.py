"""The environment a learner is currently using, and how it gets upgraded.

The rule this module exists for is one sentence in the specification:

> If the learner is using a lower-fidelity environment and a better version
> becomes ready: offer an upgrade. **Do not switch mid-command or
> mid-lesson-step.**

That second half is the hard part, and it needs something that knows whether the
learner is busy. This is the smallest honest version of that: a session holding
the artifact in use, whether a command is in flight, and any upgrade waiting.

Like the experiment approval gate, the rule lives in deterministic code rather
than in whatever client happens to call the API. ``offer`` never switches, and
``accept`` refuses while busy -- so an upgrade cannot land in the middle of
something no matter who asks for it or when.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, computed_field

from repolab.builds.models import BuildArtifact
from repolab.events import EventEmitter, EventType, NullEmitter

STEP_LABEL = "environment"


class UpgradeRefused(RuntimeError):
    """An upgrade was attempted at a moment it must not be applied."""


class SessionBusy(RuntimeError):
    """Something is already in flight in this session.

    Named rather than a bare ``RuntimeError`` so a caller can catch exactly this.
    The terminal's socket handler caught ``RuntimeError`` to report it, which
    also caught ``SandboxError`` -- a subclass -- and told a learner "a statement
    is already running" when the container had gone away.
    """


class UpgradeOffer(BaseModel):
    """A better environment, waiting for a safe moment."""

    artifact: BuildArtifact
    from_fidelity: str
    to_fidelity: str
    offered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    accepted_at: datetime | None = None
    declined_at: datetime | None = None

    @computed_field
    @property
    def pending(self) -> bool:
        return self.accepted_at is None and self.declined_at is None

    def describe(self) -> str:
        return (
            f"a {self.to_fidelity} environment is ready "
            f"(currently {self.from_fidelity}): {self.artifact.summary()}"
        )


class EnvironmentSession(BaseModel):
    """One learner's environment, and its upgrade state."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    repo_url: str
    artifact: BuildArtifact | None = None

    busy: bool = False
    """Whether a command or lesson step is in flight.

    The whole reason this class exists. Nothing switches environments while it
    is true.
    """

    current_activity: str = ""
    offer: UpgradeOffer | None = None
    history: list[str] = Field(default_factory=list)
    """What the session has done, in order, for the demonstration to show."""

    model_config = {"arbitrary_types_allowed": True}

    # -- activity ---------------------------------------------------------

    def begin(self, activity: str) -> None:
        """Mark the start of a command or lesson step.

        Raises:
            RuntimeError: something is already in flight. Overlapping activities
                would make "busy" meaningless, and the gate with it.
        """
        if self.busy:
            raise SessionBusy(f"session {self.id} is already running {self.current_activity!r}")
        self.busy = True
        self.current_activity = activity
        self.history.append(f"began {activity}")

    def end(self, activity: str | None = None) -> None:
        """Mark the end of the current command or lesson step."""
        finished = activity or self.current_activity
        self.busy = False
        self.current_activity = ""
        if finished:
            self.history.append(f"finished {finished}")

    @property
    def fidelity(self) -> str:
        return self.artifact.fidelity.value if self.artifact else "none"

    @computed_field
    @property
    def upgrade_pending(self) -> bool:
        return self.offer is not None and self.offer.pending

    @computed_field
    @property
    def can_upgrade_now(self) -> bool:
        """Whether an accepted upgrade could be applied this instant."""
        return self.upgrade_pending and not self.busy

    # -- upgrades ---------------------------------------------------------

    def use(self, artifact: BuildArtifact, *, emitter: EventEmitter | None = None) -> None:
        """Start using *artifact*. Only valid when nothing is in flight."""
        if self.busy:
            raise UpgradeRefused(
                f"session {self.id} is running {self.current_activity!r}; "
                "the environment must not change mid-command"
            )
        self.artifact = artifact
        self.history.append(f"using {artifact.fidelity.value} environment")
        (emitter or NullEmitter()).emit(
            EventType.STEP_DONE,
            f"using the {artifact.fidelity.value} environment",
            step=STEP_LABEL,
            data={"session": self.id, **artifact.declaration()},
        )

    @property
    def best_known(self) -> BuildArtifact | None:
        """The best environment this session has been told about.

        What is in use, or a better one already waiting. An offer is measured
        against this rather than against the current artifact alone -- otherwise
        a `partial` arriving after a `high` would look like an improvement on
        `conceptual`, replace the better pending offer, and the learner would
        end up accepting a *downgrade* from what they had been offered.

        (Observed on the real Cassandra ladder, where the partial build finishes
        moments after the official release.)
        """
        pending = self.offer.artifact if self.offer is not None and self.offer.pending else None
        if pending is not None and pending.better_than(self.artifact):
            return pending
        return self.artifact

    def propose(
        self, artifact: BuildArtifact, *, emitter: EventEmitter | None = None
    ) -> UpgradeOffer | None:
        """Offer *artifact* as an upgrade. **Never switches.**

        Returns the offer, or ``None`` when there is nothing to offer -- an
        artifact that is not ready, or one no better than what the session
        already has or has already been offered. Switching environments costs
        the learner their state, so it is only worth a strict improvement.
        """
        if not artifact.better_than(self.best_known):
            return None

        offer = UpgradeOffer(
            artifact=artifact,
            # From what is actually in use, which is what the learner would be
            # leaving -- not from a previous offer they never accepted.
            from_fidelity=self.fidelity,
            to_fidelity=artifact.fidelity.value,
        )
        self.offer = offer
        self.history.append(f"offered an upgrade to {offer.to_fidelity}")

        (emitter or NullEmitter()).emit(
            EventType.UPGRADE_AVAILABLE,
            offer.describe(),
            step=STEP_LABEL,
            data={
                "session": self.id,
                "from_fidelity": offer.from_fidelity,
                "to_fidelity": offer.to_fidelity,
                "busy": self.busy,
                **artifact.declaration(),
            },
        )
        return offer

    def accept(self, *, emitter: EventEmitter | None = None) -> BuildArtifact:
        """Apply the pending upgrade.

        Raises:
            UpgradeRefused: nothing is pending, or the session is busy. Waiting
                is the correct behaviour, not an error to work around: the
                offer stays pending and can be accepted the moment the current
                step finishes.
        """
        if self.offer is None or not self.offer.pending:
            raise UpgradeRefused(f"session {self.id} has no pending upgrade")
        if self.busy:
            raise UpgradeRefused(
                f"session {self.id} is running {self.current_activity!r}; "
                "the upgrade will be applied once it finishes"
            )

        offer = self.offer
        offer.accepted_at = datetime.now(UTC)
        self.artifact = offer.artifact
        self.history.append(f"upgraded to {offer.to_fidelity}")

        (emitter or NullEmitter()).emit(
            EventType.UPGRADE_APPLIED,
            f"upgraded from {offer.from_fidelity} to {offer.to_fidelity}",
            step=STEP_LABEL,
            data={
                "session": self.id,
                "from_fidelity": offer.from_fidelity,
                "to_fidelity": offer.to_fidelity,
                **offer.artifact.declaration(),
            },
        )
        return offer.artifact

    def decline(self) -> None:
        """Keep the current environment. The offer is closed, not deferred."""
        if self.offer is not None and self.offer.pending:
            self.offer.declined_at = datetime.now(UTC)
            self.history.append(f"declined the upgrade to {self.offer.to_fidelity}")
