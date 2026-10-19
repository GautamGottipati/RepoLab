"""The report's status semantics decide whether CI passes, so they are pinned here."""

from __future__ import annotations

from repolab.capability import (
    CapabilityReport,
    ProbeResult,
    ProbeStatus,
    failed,
    skipped,
    worst_status,
)


def row(capability: str, status: ProbeStatus, *, required: bool = True) -> ProbeResult:
    return ProbeResult(capability=capability, status=status, required=required)


class TestWorstStatus:
    def test_empty_is_pass(self) -> None:
        assert worst_status([]) is ProbeStatus.PASS

    def test_fail_dominates_everything(self) -> None:
        statuses = [ProbeStatus.PASS, ProbeStatus.SKIP, ProbeStatus.WARN, ProbeStatus.FAIL]
        assert worst_status(statuses) is ProbeStatus.FAIL

    def test_warn_beats_skip_and_pass(self) -> None:
        assert worst_status([ProbeStatus.PASS, ProbeStatus.SKIP, ProbeStatus.WARN]) is (
            ProbeStatus.WARN
        )

    def test_skip_beats_pass(self) -> None:
        assert worst_status([ProbeStatus.PASS, ProbeStatus.SKIP]) is ProbeStatus.SKIP


class TestExitCode:
    def test_all_pass_succeeds(self) -> None:
        report = CapabilityReport(results=[row("a", ProbeStatus.PASS)])
        assert report.exit_code() == 0

    def test_warn_does_not_fail_the_run(self) -> None:
        report = CapabilityReport(results=[row("a", ProbeStatus.WARN)])
        assert report.exit_code() == 0

    def test_skip_does_not_fail_the_run(self) -> None:
        """An untested capability is unknown, not broken."""
        report = CapabilityReport(results=[skipped("a", "no key")])
        assert report.exit_code() == 0

    def test_required_failure_fails_the_run(self) -> None:
        report = CapabilityReport(results=[row("a", ProbeStatus.FAIL)])
        assert report.exit_code() == 1
        assert [r.capability for r in report.blocking_failures()] == ["a"]

    def test_optional_failure_does_not_fail_the_run(self) -> None:
        """Native tool calling is optional; its absence must never block RepoLab."""
        report = CapabilityReport(results=[row("tools", ProbeStatus.FAIL, required=False)])
        assert report.exit_code() == 0
        assert report.blocking_failures() == []


class TestRendering:
    def test_table_aligns_and_includes_every_row(self) -> None:
        report = CapabilityReport(
            results=[
                ProbeResult(capability="Nano inference", status=ProbeStatus.PASS, latency_ms=812.4),
                skipped("Tavily official search", "TAVILY_API_KEY not set"),
            ]
        )
        table = report.render_table()
        lines = table.splitlines()

        assert lines[0].startswith("Capability")
        assert "Nano inference" in table
        assert "PASS" in table
        assert "812ms" in table
        assert "TAVILY_API_KEY not set" in table

        # Status column starts at the same offset on every data line.
        offsets = {line.index("PASS") for line in lines if "PASS" in line and "Status" not in line}
        assert len(offsets) == 1

    def test_optional_non_pass_rows_are_marked(self) -> None:
        report = CapabilityReport(
            results=[row("Super native tool calling", ProbeStatus.WARN, required=False)]
        )
        assert "[optional]" in report.render_table()

    def test_markdown_render_is_a_table(self) -> None:
        report = CapabilityReport(results=[row("Redis connection", ProbeStatus.PASS)])
        markdown = report.render_markdown()
        assert markdown.splitlines()[0] == "| Capability | Status | Notes |"
        assert "| Redis connection | `PASS` |" in markdown


class TestHelpers:
    def test_failed_records_the_exception_type_and_message(self) -> None:
        result = failed("Sandbox creation", RuntimeError("connection refused"))
        assert result.status is ProbeStatus.FAIL
        assert result.notes == "RuntimeError: connection refused"

    def test_failed_handles_an_empty_message(self) -> None:
        assert failed("x", ValueError()).notes == "ValueError"

    def test_skipped_is_not_required_when_asked(self) -> None:
        assert skipped("x", "reason", required=False).required is False
