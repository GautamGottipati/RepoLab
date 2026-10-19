"""The tool layer.

Two properties matter most: an agent cannot name a capability that does not
exist, and it cannot read outside the repository it was given. Both are enforced
by deterministic code, not by asking a model nicely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.agents.models import ToolRequest
from repolab.agents.repository_tools import (
    PathEscapesRepositoryError,
    build_repository_tools,
    resolve_within,
)
from repolab.agents.tools import (
    ApprovalRequiredError,
    InvalidArgumentsError,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
)
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.testing.fakes import FakeResearchProvider
from tests.test_detectors import SPARK_REPO


@pytest.fixture
def tools(make_repo):
    root, sha = make_repo(SPARK_REPO)
    tree = build_file_tree(root)
    context = DetectionContext(root=root, tree=tree, commit_sha=sha)
    shared, tool_list = build_repository_tools(
        context, ReadBudget(), research=FakeResearchProvider()
    )
    return shared, ToolRegistry(tool_list), root


class TestTheRepositoryBoundary:
    """A tool must not read outside the clone, however it is asked."""

    @pytest.mark.parametrize(
        "path",
        [
            "../secret.txt",
            "../../etc/passwd",
            "/etc/passwd",
            "jobs/../../outside.py",
            "",
            ".",
        ],
    )
    def test_escaping_paths_are_refused(self, tmp_path: Path, path: str) -> None:
        with pytest.raises(PathEscapesRepositoryError):
            resolve_within(tmp_path, path)

    def test_a_path_inside_the_repository_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1\n")
        assert resolve_within(tmp_path, "src/app.py").name == "app.py"

    def test_a_symlink_pointing_outside_is_refused(self, tmp_path: Path) -> None:
        """resolve() follows the link, so containment is checked on the target."""
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("sensitive\n")
        (repo / "link.txt").symlink_to(outside)

        with pytest.raises(PathEscapesRepositoryError):
            resolve_within(repo, "link.txt")

    async def test_read_file_refuses_traversal(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="read_file", arguments={"path": "../../../etc/passwd"})
        )
        assert result.ok is False
        assert "outside the repository" in (result.error or "")


class TestRegistryValidation:
    async def test_an_unregistered_tool_is_refused(self, tools) -> None:
        """An agent cannot invent a capability."""
        _, registry, _ = tools
        with pytest.raises(UnknownToolError, match="unknown tool"):
            registry.validate(ToolRequest(tool="run_shell_command"))

    def test_the_error_lists_what_is_available(self, tools) -> None:
        _, registry, _ = tools
        with pytest.raises(UnknownToolError) as excinfo:
            registry.validate(ToolRequest(tool="nope"))
        assert "read_file" in str(excinfo.value)

    def test_malformed_arguments_are_refused(self, tools) -> None:
        _, registry, _ = tools
        with pytest.raises(InvalidArgumentsError, match="path"):
            registry.validate(ToolRequest(tool="read_file", arguments={}))

    def test_out_of_range_arguments_are_refused(self, tools) -> None:
        _, registry, _ = tools
        with pytest.raises(InvalidArgumentsError):
            registry.validate(ToolRequest(tool="list_files", arguments={"limit": 100_000}))

    def test_validation_happens_before_execution(self, tools) -> None:
        """Nothing runs until the request has been checked."""
        _, registry, _ = tools
        with pytest.raises(UnknownToolError):
            registry.validate(ToolRequest(tool="delete_everything"))

    def test_registering_a_duplicate_name_is_rejected(self, tools) -> None:
        shared, registry, _ = tools
        from repolab.agents.repository_tools import ReadFileTool

        with pytest.raises(ValueError, match="already registered"):
            registry.register(ReadFileTool(shared))

    def test_every_investigation_tool_is_read_only(self, tools) -> None:
        """Read-only investigation needs no approval."""
        _, registry, _ = tools
        for name in registry.names:
            assert registry.get(name).requires_approval is False

    async def test_an_approval_gated_tool_cannot_run_unapproved(self) -> None:
        class DestructiveTool:
            name = "reset_environment"
            description = "would destroy state"
            requires_approval = True
            arguments_model = __import__(
                "repolab.agents.repository_tools", fromlist=["NoArgs"]
            ).NoArgs

            async def execute(self, arguments: dict) -> ToolResult:  # pragma: no cover
                raise AssertionError("must not run without approval")

        registry = ToolRegistry([DestructiveTool()])

        # Refused without approval, and the tool asserts if it is ever reached.
        with pytest.raises(ApprovalRequiredError):
            await registry.execute(ToolRequest(tool="reset_environment"))

        # With approval it is permitted through validation, which is the gate's
        # only job; the tool itself is never actually run here.
        assert (
            registry.validate(ToolRequest(tool="reset_environment"), approved=True).name
            == "reset_environment"
        )


class TestToolCatalogue:
    def test_all_seven_named_tools_exist(self, tools) -> None:
        """The specification names these explicitly."""
        _, registry, _ = tools
        required = {
            "list_files",
            "read_file",
            "search_code",
            "inspect_dependencies",
            "inspect_config",
            "detect_entrypoints",
            "search_official_docs",
        }
        assert required <= set(registry.names)

    def test_descriptions_are_available_for_a_planner(self, tools) -> None:
        _, registry, _ = tools
        described = registry.describe()
        assert all(entry["description"] for entry in described)
        assert all("arguments" in entry for entry in described)


class TestListFiles:
    async def test_it_lists_repository_files(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(ToolRequest(tool="list_files"))
        assert result.ok
        assert "jobs/etl_job.py" in result.data["paths"]

    async def test_it_filters_by_extension(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="list_files", arguments={"extension": "py"})
        )
        assert all(path.endswith(".py") for path in result.data["paths"])

    async def test_it_filters_by_directory(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="list_files", arguments={"directory": "jobs"})
        )
        assert all(path.startswith("jobs") for path in result.data["paths"])

    async def test_truncation_is_reported(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(ToolRequest(tool="list_files", arguments={"limit": 1}))
        assert result.data["truncated"] is True


class TestReadFile:
    async def test_it_reads_a_file(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="read_file", arguments={"path": "jobs/etl_job.py"})
        )
        assert result.ok
        assert any("SparkSession" in line for line in result.data["lines"])

    async def test_a_missing_file_fails_cleanly(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="read_file", arguments={"path": "nope.py"})
        )
        assert result.ok is False

    async def test_line_limits_are_respected(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="read_file", arguments={"path": "jobs/etl_job.py", "max_lines": 2})
        )
        assert len(result.data["lines"]) == 2
        assert result.data["truncated"] is True


class TestSearchCode:
    async def test_it_finds_matches_with_locations(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="search_code", arguments={"query": "SparkSession"})
        )
        assert result.data["matches"]
        first = result.data["matches"][0]
        assert first["line"] >= 1
        assert first["path"]

    async def test_matches_are_recorded_as_citable_findings(self, tools) -> None:
        shared, registry, root = tools
        await registry.execute(ToolRequest(tool="search_code", arguments={"query": "SparkSession"}))
        findings = shared.run.of_kind("search_match")
        assert findings
        for finding in findings:
            assert finding.references
            assert (root / finding.references[0].path).exists()

    async def test_a_query_with_no_matches_is_not_a_failure(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="search_code", arguments={"query": "zzzznotpresent"})
        )
        assert result.ok is True
        assert result.data["matches"] == []


class TestDetectorTools:
    async def test_inspect_dependencies_finds_them(self, tools) -> None:
        shared, registry, _ = tools
        result = await registry.execute(ToolRequest(tool="inspect_dependencies"))
        assert result.data["dependencies"] >= 1
        assert "Spark" in result.data["frameworks"]
        assert shared.dependencies

    async def test_inspect_config_finds_config_files(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(ToolRequest(tool="inspect_config"))
        assert any("etl_config" in path for path in result.data["config_files"])

    async def test_detect_entrypoints_finds_them(self, tools) -> None:
        shared, registry, _ = tools
        await registry.execute(ToolRequest(tool="detect_entrypoints"))
        assert shared.entrypoints

    async def test_detect_services_runs_and_reports(self, tools) -> None:
        """The Spark fixture declares no services, so an empty result is correct."""
        shared, registry, _ = tools
        result = await registry.execute(ToolRequest(tool="detect_services"))
        assert result.ok
        assert shared.services == []

    async def test_detect_services_finds_compose_services(self, make_repo) -> None:
        from tests.test_detectors import WEB_REPO

        root, sha = make_repo(WEB_REPO)
        tree = build_file_tree(root)
        context = DetectionContext(root=root, tree=tree, commit_sha=sha)
        shared, tool_list = build_repository_tools(context, ReadBudget())

        await ToolRegistry(tool_list).execute(ToolRequest(tool="detect_services"))
        assert {s.name for s in shared.services} >= {"PostgreSQL", "Redis"}

    async def test_findings_accumulate_across_tools(self, tools) -> None:
        """Every tool contributes to one shared, citable set of findings."""
        shared, registry, _ = tools
        await registry.execute(ToolRequest(tool="inspect_dependencies"))
        before = len(shared.run.findings)
        await registry.execute(ToolRequest(tool="detect_entrypoints"))
        assert len(shared.run.findings) > before

    async def test_every_accumulated_finding_is_citable(self, tools) -> None:
        shared, registry, _ = tools
        for tool in ("inspect_dependencies", "inspect_config", "detect_entrypoints"):
            await registry.execute(ToolRequest(tool=tool))
        assert shared.run.findings
        assert all(finding.references for finding in shared.run.findings)


class TestSearchOfficialDocs:
    async def test_it_returns_documentation_results(self, tools) -> None:
        _, registry, _ = tools
        result = await registry.execute(
            ToolRequest(tool="search_official_docs", arguments={"query": "shuffle partitions"})
        )
        assert result.ok
        assert result.data["results"][0]["url"]

    async def test_it_restricts_to_official_domains(self, tools) -> None:
        shared, registry, _ = tools
        await registry.execute(
            ToolRequest(tool="search_official_docs", arguments={"query": "partitions"})
        )
        _, domains = shared.research.queries[0]
        assert "spark.apache.org" in domains

    async def test_without_a_provider_it_fails_cleanly(self, make_repo) -> None:
        root, sha = make_repo(SPARK_REPO)
        tree = build_file_tree(root)
        context = DetectionContext(root=root, tree=tree, commit_sha=sha)
        _, tool_list = build_repository_tools(context, ReadBudget(), research=None)
        registry = ToolRegistry(tool_list)

        result = await registry.execute(
            ToolRequest(tool="search_official_docs", arguments={"query": "xy"})
        )
        assert result.ok is False
        assert "no research provider" in (result.error or "")
