"""Progress events.

The correctness crux is the sequence number: it is what lets a browser refresh
mid-run, replay its history, and continue without a gap or a duplicate.
"""

from __future__ import annotations

import json

import pytest

from repolab.events import (
    CollectingEmitter,
    EventType,
    JobEvent,
    JobStatus,
    NullEmitter,
)
from repolab.events.models import MAX_MESSAGE_LENGTH

#: Every event type the specification requires.
REQUIRED_EVENTS = {
    "agent_started",
    "agent_status",
    "tool_requested",
    "tool_started",
    "tool_completed",
    "step_started",
    "progress",
    "log_line",
    "partial_result",
    "step_done",
    "failed",
    "heartbeat",
    "approval_required",
    "approval_received",
    "experiment_variant_started",
    "experiment_variant_completed",
    "upgrade_available",
    "upgrade_applied",
    # Progressive analysis: one event per artifact transition.
    "artifact_queued",
    "artifact_started",
    "artifact_verifying",
    "artifact_ready",
    "artifact_updated",
    "artifact_failed",
    "artifact_blocked",
}


class TestEventVocabulary:
    def test_every_required_event_type_exists(self) -> None:
        assert {member.value for member in EventType} == REQUIRED_EVENTS

    def test_job_statuses_and_terminality(self) -> None:
        assert JobStatus.QUEUED.is_terminal is False
        assert JobStatus.RUNNING.is_terminal is False
        assert JobStatus.COMPLETED.is_terminal is True
        assert JobStatus.FAILED.is_terminal is True


class TestSequenceNumbers:
    def test_sequences_start_at_one_and_are_contiguous(self) -> None:
        emitter = CollectingEmitter("job-1")
        for index in range(5):
            emitter.emit(EventType.PROGRESS, f"step {index}")

        assert [event.seq for event in emitter.events] == [1, 2, 3, 4, 5]

    def test_a_resumed_emitter_continues_the_sequence(self) -> None:
        """A retried job must not restart numbering and overwrite history."""
        emitter = CollectingEmitter("job-1", start_seq=7)
        emitter.emit(EventType.PROGRESS, "after retry")
        assert emitter.events[0].seq == 8

    def test_last_seq_tracks_the_tail(self) -> None:
        emitter = CollectingEmitter("job-1")
        emitter.emit(EventType.PROGRESS, "a")
        emitter.emit(EventType.PROGRESS, "b")
        assert emitter.last_seq == 2

    def test_a_sequence_below_one_is_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            JobEvent(job_id="j", seq=0, type=EventType.PROGRESS)


class TestEventContent:
    def test_a_long_message_is_truncated(self) -> None:
        """A status line must not become a channel for pasting model output."""
        event = JobEvent(job_id="j", seq=1, type=EventType.PROGRESS, message="x" * 5000)
        assert len(event.message) == MAX_MESSAGE_LENGTH

    def test_structured_data_is_preserved(self) -> None:
        event = JobEvent(job_id="j", seq=1, type=EventType.PROGRESS, data={"files": 21, "ok": True})
        assert event.data == {"files": 21, "ok": True}

    def test_step_labels_group_related_events(self) -> None:
        emitter = CollectingEmitter()
        emitter.emit(EventType.STEP_STARTED, "cloning", step="clone")
        assert emitter.events[0].step == "clone"


class TestSseRendering:
    def test_the_frame_carries_id_event_and_data(self) -> None:
        event = JobEvent(job_id="j", seq=42, type=EventType.PROGRESS, message="halfway")
        frame = event.to_sse()

        assert frame.startswith("id: 42\n")
        assert "event: progress\n" in frame
        assert frame.endswith("\n\n")

    def test_the_id_is_what_enables_browser_resumption(self) -> None:
        """EventSource returns this id as Last-Event-ID after a reconnect."""
        frame = JobEvent(job_id="j", seq=7, type=EventType.HEARTBEAT).to_sse()
        assert "id: 7" in frame

    def test_the_payload_is_parseable_json(self) -> None:
        event = JobEvent(job_id="j", seq=1, type=EventType.LOG_LINE, message="hello")
        payload = event.to_sse().split("data: ", 1)[1].strip()
        assert json.loads(payload)["message"] == "hello"


class TestEmitters:
    def test_the_collecting_emitter_records_types_in_order(self) -> None:
        emitter = CollectingEmitter()
        emitter.emit(EventType.AGENT_STARTED)
        emitter.emit(EventType.TOOL_REQUESTED)
        assert emitter.types() == [EventType.AGENT_STARTED, EventType.TOOL_REQUESTED]

    def test_filtering_by_type(self) -> None:
        emitter = CollectingEmitter()
        emitter.emit(EventType.PROGRESS, "a")
        emitter.emit(EventType.HEARTBEAT, "b")
        emitter.emit(EventType.PROGRESS, "c")
        assert len(emitter.of_type(EventType.PROGRESS)) == 2

    def test_the_null_emitter_still_sequences(self) -> None:
        """Code paths that discard events must behave identically otherwise."""
        emitter = NullEmitter()
        assert emitter.emit(EventType.PROGRESS).seq == 1
        assert emitter.emit(EventType.PROGRESS).seq == 2

    def test_elapsed_time_is_tracked(self) -> None:
        assert CollectingEmitter().elapsed_seconds >= 0
