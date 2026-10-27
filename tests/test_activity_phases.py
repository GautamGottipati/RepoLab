"""The user-facing activity vocabulary.

A person watching a repository being investigated should see four phases, not
twenty tool names. These tests pin the vocabulary and, more importantly, pin
what happens to anything *outside* it: an unrecognised tool must leave the
tracker alone rather than invent a phase for itself.
"""

from __future__ import annotations

import pytest

from repolab.events import (
    PHASE_COUNT,
    PHASES,
    ActivityReporter,
    CollectingEmitter,
    EventType,
    phase_by_key,
    phase_for,
    phase_index,
)

#: The labels the Phase 8 specification names, in order.
REQUIRED_LABELS = [
    "Inspecting repository",
    "Finding entry points",
    "Understanding data flow",
    "Preparing environment",
]


class TestTheVocabulary:
    def test_the_phases_are_the_ones_the_specification_names(self) -> None:
        assert [phase.label for phase in PHASES] == REQUIRED_LABELS
        assert PHASE_COUNT == 4

    def test_keys_are_unique_and_indexed_from_one(self) -> None:
        keys = [phase.key for phase in PHASES]
        assert len(set(keys)) == len(keys)
        assert [phase_index(phase) for phase in PHASES] == [1, 2, 3, 4]

    def test_every_phase_is_reachable_by_key(self) -> None:
        for phase in PHASES:
            assert phase_by_key(phase.key) is phase
        assert phase_by_key("not-a-phase") is None

    def test_no_tool_belongs_to_two_phases(self) -> None:
        """An ambiguous tool would make the tracker jump around."""
        seen: set[str] = set()
        for phase in PHASES:
            overlap = seen & phase.tools
            assert not overlap, f"{overlap} appears in more than one phase"
            seen |= phase.tools

    def test_no_step_belongs_to_two_phases(self) -> None:
        seen: set[str] = set()
        for phase in PHASES:
            overlap = seen & phase.steps
            assert not overlap, f"{overlap} appears in more than one phase"
            seen |= phase.steps

    def test_every_phase_can_be_entered_by_something(self) -> None:
        for phase in PHASES:
            assert phase.tools or phase.steps, f"{phase.key} has no trigger"


class TestMapping:
    @pytest.mark.parametrize(
        ("tool", "expected"),
        [
            ("list_files", "inspecting_repository"),
            ("inspect_dependencies", "inspecting_repository"),
            ("detect_entrypoints", "finding_entrypoints"),
            ("detect_surfaces", "finding_entrypoints"),
            ("search_code", "understanding_data_flow"),
            ("read_file", "understanding_data_flow"),
        ],
    )
    def test_tools_map_to_their_phase(self, tool: str, expected: str) -> None:
        phase = phase_for(tool=tool)
        assert phase is not None
        assert phase.key == expected

    def test_steps_map_to_their_phase(self) -> None:
        assert phase_for(step="clone").key == "inspecting_repository"
        assert phase_for(step="classify").key == "understanding_data_flow"
        assert phase_for(step="environment").key == "preparing_environment"

    def test_an_unknown_tool_maps_to_nothing(self) -> None:
        """It must leave the tracker alone rather than guess."""
        assert phase_for(tool="some_future_tool") is None

    def test_an_unknown_step_maps_to_nothing(self) -> None:
        assert phase_for(step="investigate") is None

    def test_a_tool_outranks_a_step(self) -> None:
        """Every tool event carries the same `investigate` step, so the tool decides."""
        phase = phase_for(step="clone", tool="detect_entrypoints")
        assert phase is not None
        assert phase.key == "finding_entrypoints"

    def test_nothing_given_maps_to_nothing(self) -> None:
        assert phase_for() is None


class TestTheReporter:
    def test_entering_a_phase_emits_one_user_facing_status(self) -> None:
        emitter = CollectingEmitter("job-1")
        ActivityReporter(emitter).enter("inspecting_repository")

        assert len(emitter.events) == 1
        event = emitter.events[0]
        assert event.type is EventType.AGENT_STATUS
        assert event.message == "Inspecting repository"
        assert event.data == {
            "phase": "inspecting_repository",
            "label": "Inspecting repository",
            "index": 1,
            "total": 4,
        }

    def test_a_phase_is_announced_once(self) -> None:
        """Several tools belong to one phase; the tracker must not flicker."""
        emitter = CollectingEmitter("job-1")
        reporter = ActivityReporter(emitter)

        assert reporter.enter("inspecting_repository") is True
        assert reporter.enter("inspecting_repository") is False
        assert len(emitter.events) == 1

    def test_observing_tools_walks_the_phases(self) -> None:
        emitter = CollectingEmitter("job-1")
        reporter = ActivityReporter(emitter)

        for tool in ("list_files", "inspect_config", "detect_entrypoints", "search_code"):
            reporter.observe(tool=tool)

        assert reporter.entered == [
            "inspecting_repository",
            "finding_entrypoints",
            "understanding_data_flow",
        ]
        assert [event.message for event in emitter.events] == REQUIRED_LABELS[:3]

    def test_an_unknown_tool_emits_nothing(self) -> None:
        emitter = CollectingEmitter("job-1")
        reporter = ActivityReporter(emitter)

        assert reporter.observe(tool="some_future_tool") is False
        assert emitter.events == []
        assert reporter.current is None

    def test_an_unknown_phase_key_emits_nothing(self) -> None:
        emitter = CollectingEmitter("job-1")
        assert ActivityReporter(emitter).enter("not-a-phase") is False
        assert emitter.events == []

    def test_the_current_phase_is_the_most_recent(self) -> None:
        reporter = ActivityReporter(CollectingEmitter("job-1"))
        reporter.enter("inspecting_repository")
        reporter.enter("understanding_data_flow")

        assert reporter.current is not None
        assert reporter.current.key == "understanding_data_flow"

    def test_a_detail_is_appended_without_replacing_the_label(self) -> None:
        emitter = CollectingEmitter("job-1")
        ActivityReporter(emitter).enter("preparing_environment", detail="pulling the image")

        assert emitter.events[0].message == "Preparing environment — pulling the image"
        assert emitter.events[0].data["label"] == "Preparing environment"

    def test_labels_are_constants_not_interpolated_model_text(self) -> None:
        """The tracker's vocabulary cannot become a channel for model output."""
        for phase in PHASES:
            assert phase.label in REQUIRED_LABELS
