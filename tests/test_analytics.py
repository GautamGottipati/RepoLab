"""Product analytics.

A writer only, instrumented from the beginning so the KPIs in CLAUDE.md can be
computed from real history later rather than reconstructed.
"""

from __future__ import annotations

from repolab.analytics import (
    Analytics,
    AnalyticsEvent,
    AnalyticsEventType,
    CollectingAnalyticsWriter,
    NullAnalyticsWriter,
)

#: Every event the specification requires at minimum.
REQUIRED = {
    "repo_submitted",
    "repo_manifest_created",
    "repo_ready",
    "agent_started",
    "agent_completed",
    "agent_failed",
    "tool_called",
}


class TestVocabulary:
    def test_every_required_event_exists(self) -> None:
        assert {member.value for member in AnalyticsEventType} >= REQUIRED

    def test_each_member_is_a_plain_string_value(self) -> None:
        assert AnalyticsEventType.REPO_READY == "repo_ready"


class TestRecording:
    def test_an_event_is_captured_with_its_context(self) -> None:
        writer = CollectingAnalyticsWriter()
        Analytics(writer, job_id="job-1", repo_url="https://github.com/a/b").record(
            AnalyticsEventType.REPO_SUBMITTED, source="api"
        )

        event = writer.events[0]
        assert event.type is AnalyticsEventType.REPO_SUBMITTED
        assert event.job_id == "job-1"
        assert event.repo_url == "https://github.com/a/b"
        assert event.properties == {"source": "api"}

    def test_the_job_association_is_applied_to_every_event(self) -> None:
        writer = CollectingAnalyticsWriter()
        analytics = Analytics(writer, job_id="job-9")
        for event_type in AnalyticsEventType:
            analytics.record(event_type)

        assert all(event.job_id == "job-9" for event in writer.events)

    def test_the_whole_required_set_can_be_recorded(self) -> None:
        writer = CollectingAnalyticsWriter()
        analytics = Analytics(writer, job_id="job-1")
        for name in sorted(REQUIRED):
            analytics.record(AnalyticsEventType(name))

        assert {event.type.value for event in writer.events} == REQUIRED

    def test_filtering_by_type(self) -> None:
        writer = CollectingAnalyticsWriter()
        analytics = Analytics(writer)
        analytics.record(AnalyticsEventType.TOOL_CALLED, tool="read_file")
        analytics.record(AnalyticsEventType.TOOL_CALLED, tool="list_files")
        analytics.record(AnalyticsEventType.REPO_READY)

        called = writer.of_type(AnalyticsEventType.TOOL_CALLED)
        assert [event.properties["tool"] for event in called] == ["read_file", "list_files"]

    def test_properties_are_optional(self) -> None:
        writer = CollectingAnalyticsWriter()
        Analytics(writer).record(AnalyticsEventType.REPO_READY)
        assert writer.events[0].properties == {}

    def test_the_null_writer_discards(self) -> None:
        writer = NullAnalyticsWriter()
        writer.record(AnalyticsEvent(type=AnalyticsEventType.REPO_READY))

    def test_an_event_can_stand_alone_without_a_job(self) -> None:
        writer = CollectingAnalyticsWriter()
        writer.record(AnalyticsEvent(type=AnalyticsEventType.REPO_SUBMITTED))
        assert writer.events[0].job_id is None
