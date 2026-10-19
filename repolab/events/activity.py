"""User-facing activity phases.

The event stream is detailed -- a tool request, a tool start, a tool completion,
a finding count -- which is right for an audit trail and wrong for someone
watching a repository get investigated. This module is the translation: a small,
ordered set of phases, each owning the steps and tools that belong to it.

The vocabulary lives **here, in the backend**, because CLAUDE.md requires
``agent_status`` to carry concise user-facing status and names the kind of
phrasing it wants. A reporter emits one such event when a phase is entered, so
the browser renders its progress tracker from the stream rather than keeping its
own copy of this table -- there is one place to change a label, and no way for
the two to disagree.

Phase labels are constants in this file. No model output reaches them, so no
private chain-of-thought can arrive in a status line through this path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from repolab.events.models import EventType

if TYPE_CHECKING:
    from repolab.events.emitter import EventEmitter


class ActivityPhase(BaseModel):
    """One stage of work, as a person following along would describe it."""

    key: str
    label: str
    """Shown verbatim. Deliberately a constant, never interpolated model text."""

    steps: frozenset[str] = Field(default_factory=frozenset)
    """``JobEvent.step`` values that belong to this phase."""

    tools: frozenset[str] = Field(default_factory=frozenset)
    """Tool names that belong to this phase."""

    model_config = {"frozen": True}


#: The phases, in the order they happen. The labels are the ones the Phase 8
#: specification names.
PHASES: tuple[ActivityPhase, ...] = (
    ActivityPhase(
        key="inspecting_repository",
        label="Inspecting repository",
        steps=frozenset({"clone"}),
        tools=frozenset({"list_files", "inspect_dependencies", "inspect_config"}),
    ),
    ActivityPhase(
        key="finding_entrypoints",
        label="Finding Spark entry points",
        tools=frozenset({"detect_entrypoints", "detect_services", "detect_surfaces"}),
    ),
    ActivityPhase(
        key="understanding_data_flow",
        label="Understanding data flow",
        steps=frozenset({"classify", "manifest"}),
        tools=frozenset({"search_code", "read_file", "find_symbol", "search_official_docs"}),
    ),
    ActivityPhase(
        key="preparing_environment",
        label="Preparing environment",
        steps=frozenset({"environment"}),
    ),
)

PHASE_COUNT = len(PHASES)

_BY_KEY: dict[str, ActivityPhase] = {phase.key: phase for phase in PHASES}


def phase_by_key(key: str) -> ActivityPhase | None:
    return _BY_KEY.get(key)


def phase_index(phase: ActivityPhase) -> int:
    """Where *phase* sits in the sequence, counting from 1."""
    return PHASES.index(phase) + 1


def phase_for(*, step: str | None = None, tool: str | None = None) -> ActivityPhase | None:
    """The phase a step or tool belongs to, or ``None``.

    A tool takes precedence over a step: the agent runtime labels every tool
    event with the same ``investigate`` step, so the step alone cannot tell
    "listing files" from "finding entry points".

    Returning ``None`` for something unrecognised is deliberate. An unmapped tool
    should leave the tracker alone rather than invent a phase for itself, and a
    test asserts that stays true.
    """
    if tool is not None:
        for phase in PHASES:
            if tool in phase.tools:
                return phase
        return None
    if step is not None:
        for phase in PHASES:
            if step in phase.steps:
                return phase
    return None


class ActivityReporter:
    """Emits one ``agent_status`` per phase entered, and never repeats itself.

    Wraps an emitter rather than replacing it: the detailed events still flow, so
    the audit trail is unchanged and the browser can show both a four-step
    tracker and the full activity feed from the same stream.
    """

    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter
        self._entered: list[str] = []

    @property
    def entered(self) -> list[str]:
        """Phase keys entered so far, in order."""
        return list(self._entered)

    @property
    def current(self) -> ActivityPhase | None:
        if not self._entered:
            return None
        return _BY_KEY[self._entered[-1]]

    def enter(self, key: str, *, detail: str | None = None) -> bool:
        """Announce the phase named *key*. Returns False if already announced.

        Idempotent because the phases are driven by tool calls, and a planner may
        legitimately call several tools from the same phase.
        """
        phase = _BY_KEY.get(key)
        if phase is None or key in self._entered:
            return False

        self._entered.append(key)
        self._emitter.emit(
            EventType.AGENT_STATUS,
            phase.label if detail is None else f"{phase.label} — {detail}",
            step="activity",
            data=phase_payload(phase),
        )
        return True

    def observe(self, *, step: str | None = None, tool: str | None = None) -> bool:
        """Enter whichever phase a step or tool implies, if any."""
        phase = phase_for(step=step, tool=tool)
        return False if phase is None else self.enter(phase.key)


def phase_payload(phase: ActivityPhase) -> dict[str, Any]:
    """The structured facts a status event carries about its phase."""
    return {
        "phase": phase.key,
        "label": phase.label,
        "index": phase_index(phase),
        "total": PHASE_COUNT,
    }
