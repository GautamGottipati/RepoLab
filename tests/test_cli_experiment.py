"""A testable Cassandra question, run on the real node -- here, the fake sandbox.

Three things are proved:

* **validation decides what may run** -- a statement that would leave the
  experiment's keyspace, or that the terminal allowlist refuses, rejects the plan;
* **the gate holds** -- nothing reaches the sandbox before approval;
* **results are cqlsh's own output** -- the row count and error class come from
  what cqlsh printed, and an explanation citing anything else is flagged.
"""

from __future__ import annotations

import pytest

from repolab.builds.models import BuildArtifact, SmokeTestResult
from repolab.experiences.cli.session import cql_command
from repolab.experiments.cli.agent import CliExperimentAgent, verify_cli_explanation
from repolab.experiments.cli.models import CliProposedPlan, CliVariantSpec
from repolab.experiments.cli.runner import NodeStoppedError, keyspace_for
from repolab.experiments.cli.statistics import (
    compute_cli_statistics,
    parse_error,
    parse_rows,
)
from repolab.experiments.cli.validation import statement_problem, validate_cli_plan
from repolab.experiments.executor import ApprovalRequiredError
from repolab.experiments.experiment import IllegalTransitionError
from repolab.experiments.explanation import Claim, ClaimKind, Explanation
from repolab.experiments.models import ExperimentStatus
from repolab.manifest.models import BuildKind
from repolab.sandbox.fake import FakeSandboxProvider, ScriptedCommand
from tests.support.builds import CASSANDRA_REPO, SHA

CQLSH = "/opt/cassandra/bin/cqlsh"
TAG = "repolab/apache-cassandra:abc-official-release"
EXPERIMENT_ID = "0123456789ab-cdef-0000-0000-000000000000"
QUESTION = "What happens if I filter on a column that is not part of the primary key?"

SETUP = [
    "CREATE TABLE readings (sensor text, at int, value int, PRIMARY KEY (sensor, at))",
    "INSERT INTO readings (sensor, at, value) VALUES ('a', 1, 10)",
]
ON_KEY = "SELECT * FROM readings WHERE sensor = 'a'"
OFF_KEY = "SELECT * FROM readings WHERE value = 10"

FILTERING_ERROR = (
    '<stdin>:1:InvalidRequest: Error from server: code=2200 [Invalid query] message="Cannot '
    "execute this query as it might involve data filtering and thus may have unpredictable "
    "performance. If you want to execute this query despite the performance unpredictability, "
    'use ALLOW FILTERING"\n'
)
ONE_ROW = "\n sensor | at | value\n--------+----+-------\n      a |  1 |    10\n\n(1 rows)\n"


def artifact() -> BuildArtifact:
    return BuildArtifact(
        repo_url=CASSANDRA_REPO,
        commit_sha=SHA,
        kind=BuildKind.OFFICIAL_RELEASE,
        version="5.0.9",
        checkpoint_id="ckpt",
        checkpoint_tag=TAG,
        service_command="cassandra -f",
        cli_command=CQLSH,
        memory="4g",
        smoke=SmokeTestResult(name="cqlsh connects", command="x", passed=True),
    )


def proposal(**overrides: object) -> CliProposedPlan:
    fields: dict[str, object] = {
        "question": QUESTION,
        "hypothesis": "Filtering on a non-key column is refused without ALLOW FILTERING.",
        "setup": SETUP,
        "variants": [
            CliVariantSpec(label="filter on partition key", statements=[ON_KEY]),
            CliVariantSpec(label="filter on non-key column", statements=[OFF_KEY]),
        ],
        "repeats": 2,
    }
    fields.update(overrides)
    return CliProposedPlan(**fields)  # type: ignore[arg-type]


class Ultra:
    """Returns a design, then an explanation, recording each prompt."""

    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.prompts: list[str] = []

    async def ultra(self, *, prompt: str, **_: object):
        self.prompts.append(prompt)
        value = self.values[min(len(self.prompts), len(self.values)) - 1]

        class Completion:
            pass

        Completion.value = value  # type: ignore[attr-defined]
        return Completion()


async def provider(**scripted: ScriptedCommand) -> FakeSandboxProvider:
    fake = FakeSandboxProvider(scripted=dict(scripted))
    handle = await fake.create("cassandra:5.0")
    await fake.checkpoint(handle, tag=TAG)
    return fake


def scripted_outputs(repeats: int = 2) -> dict[str, ScriptedCommand]:
    """cqlsh's real behaviour for the two variant queries, in every run's keyspace."""
    scripts: dict[str, ScriptedCommand] = {}
    for repeat in range(1, repeats + 1):
        on = keyspace_for(EXPERIMENT_ID, 0, repeat)
        off = keyspace_for(EXPERIMENT_ID, 1, repeat)
        scripts[cql_command(CQLSH, ON_KEY, on)] = ScriptedCommand(stdout=ONE_ROW)
        scripts[cql_command(CQLSH, OFF_KEY, off)] = ScriptedCommand(
            stderr=FILTERING_ERROR, exit_code=2
        )
    return scripts


class TestValidation:
    def test_a_sound_plan_is_accepted_and_costed(self) -> None:
        result = validate_cli_plan(proposal(), artifact=artifact())

        assert result.plan is not None
        assert result.plan.estimated_runs == 4
        assert result.plan.environment["checkpoint"] == TAG
        assert "fresh keyspace" in result.plan.fixed_settings["isolation"]

    @pytest.mark.parametrize(
        "statement",
        [
            "USE system",
            "CREATE KEYSPACE other WITH replication = {'class': 'SimpleStrategy'}",
            "DROP KEYSPACE production",
            "SELECT * FROM production.users",
            "INSERT INTO system_schema.tables (x) VALUES (1)",
            "COPY readings TO '/etc/passwd'",
            "SELECT * FROM readings; DROP TABLE x | rm -rf /",
            "CONSISTENCY ALL",
        ],
    )
    def test_a_statement_that_would_escape_the_experiment_is_refused(self, statement: str) -> None:
        assert statement_problem(statement) is not None
        result = validate_cli_plan(
            proposal(
                variants=[
                    CliVariantSpec(label="a", statements=[ON_KEY]),
                    CliVariantSpec(label="b", statements=[statement]),
                ]
            ),
            artifact=artifact(),
        )
        assert result.plan is None

    def test_reading_system_tables_is_allowed(self) -> None:
        assert statement_problem("SELECT release_version FROM system.local") is None

    def test_a_dotted_string_literal_is_data_not_a_keyspace(self) -> None:
        assert statement_problem("INSERT INTO t (k) VALUES ('other.table')") is None

    def test_one_variant_is_not_a_comparison(self) -> None:
        result = validate_cli_plan(
            proposal(variants=[CliVariantSpec(label="a", statements=[ON_KEY])]),
            artifact=artifact(),
        )
        assert result.rejection is not None
        assert result.rejection.reason == "wrong number of variants"

    def test_identical_variants_are_not_a_comparison(self) -> None:
        result = validate_cli_plan(
            proposal(
                variants=[
                    CliVariantSpec(label="a", statements=[ON_KEY]),
                    CliVariantSpec(label="b", statements=[ON_KEY]),
                ]
            ),
            artifact=artifact(),
        )
        assert result.rejection is not None

    def test_repeats_are_clamped_and_the_change_is_disclosed(self) -> None:
        result = validate_cli_plan(proposal(repeats=50), artifact=artifact())

        assert result.plan is not None
        assert result.plan.repeats == 3
        assert any("repeats 50 -> 3" in note for note in result.plan.adjustments)


class TestParsingCqlshOutput:
    def test_the_row_footer_is_the_row_count(self) -> None:
        assert parse_rows(ONE_ROW) == 1
        assert parse_rows("no footer") is None

    def test_the_server_error_class_and_code_are_parsed(self) -> None:
        cls, code, line = parse_error("", FILTERING_ERROR)
        assert (cls, code) == ("InvalidRequest", 2200)
        assert line and "ALLOW FILTERING" in line


class TestTheGate:
    async def test_nothing_runs_before_approval(self) -> None:
        fake = await provider()
        agent = CliExperimentAgent(
            repo_url=CASSANDRA_REPO, commit_sha=SHA, artifact=artifact(), router=Ultra(proposal())
        )
        experiment = await agent.propose(QUESTION, experiment_id=EXPERIMENT_ID)
        assert experiment.status is ExperimentStatus.AWAITING_APPROVAL

        with pytest.raises(IllegalTransitionError):
            await agent.execute(experiment, provider=fake)
        assert fake.executed == []

    async def test_the_runner_refuses_an_unapproved_status(self) -> None:
        from repolab.experiments.cli.runner import CliVariantRunner

        plan = validate_cli_plan(proposal(), artifact=artifact()).require()
        fake = await provider()
        with pytest.raises(ApprovalRequiredError):
            await CliVariantRunner(fake, artifact(), repo_url=CASSANDRA_REPO).run(
                plan, experiment_id=EXPERIMENT_ID, status=ExperimentStatus.AWAITING_APPROVAL
            )
        assert fake.executed == []

    async def test_without_a_model_nothing_is_designed(self) -> None:
        agent = CliExperimentAgent(repo_url=CASSANDRA_REPO, commit_sha=SHA, artifact=artifact())
        experiment = await agent.propose(QUESTION)

        assert experiment.status is ExperimentStatus.FAILED
        assert experiment.plan is None


class TestARealRun:
    async def run(self, *, explanation: Explanation | None = None):
        explanation = explanation or Explanation(
            answer="Filtering on a non-key column was refused; on the key it returned a row.",
            claims=[
                Claim(
                    kind=ClaimKind.MEASURED,
                    statement="The non-key filter never succeeded.",
                    support="filter on non-key column / statement 1 succeeded",
                ),
                Claim(
                    kind=ClaimKind.MEASURED,
                    statement="The key filter returned 1 row.",
                    support="filter on partition key / statement 1 rows",
                ),
            ],
            caveats=["a single node"],
        )
        router = Ultra(proposal(), explanation)
        fake = await provider(**scripted_outputs())
        agent = CliExperimentAgent(
            repo_url=CASSANDRA_REPO, commit_sha=SHA, artifact=artifact(), router=router
        )
        experiment = await agent.propose(QUESTION, experiment_id=EXPERIMENT_ID)
        experiment.approve(by="test")
        experiment = await agent.execute(experiment, provider=fake, ready_timeout_seconds=5)
        return experiment, fake, router

    async def test_every_run_gets_its_own_keyspace_on_the_restored_node(self) -> None:
        _, fake, _ = await self.run()

        assert fake.restored and fake.restored[0][0] == TAG
        creates = [command for _, command in fake.executed if "CREATE KEYSPACE" in command]
        assert len(creates) == 4
        assert len({keyspace_for(EXPERIMENT_ID, v, r) for v in (0, 1) for r in (1, 2)}) == 4
        assert fake.terminated, "the node is stopped afterwards"

    async def test_results_are_what_cqlsh_printed(self) -> None:
        experiment, _, _ = await self.run()

        assert experiment.status is ExperimentStatus.COMPLETED
        stats = experiment.statistics
        assert stats is not None
        on, off = stats.variants
        assert on.statements[0].succeeded_runs == 2
        assert on.statements[0].rows_median == 1
        assert off.statements[0].succeeded_runs == 0
        assert off.statements[0].error_classes == ["InvalidRequest"]
        message = off.statements[0].error_message
        assert message and "ALLOW FILTERING" in message

        assert experiment.evidence is not None
        assert any("ALLOW FILTERING" in log.excerpt for log in experiment.evidence.logs)
        assert experiment.evidence.measurements

    async def test_the_explanation_is_given_the_verbatim_output(self) -> None:
        experiment, _, router = await self.run()

        assert "ALLOW FILTERING" in router.prompts[-1]
        assert experiment.explanation is not None
        assert experiment.explanation.grounded

    async def test_an_invented_metric_or_number_is_flagged(self) -> None:
        experiment, _, _ = await self.run()
        assert experiment.statistics is not None

        verified = verify_cli_explanation(
            Explanation(
                answer="x",
                claims=[
                    Claim(kind=ClaimKind.MEASURED, statement="It used 512 MB.", support="memory"),
                    Claim(
                        kind=ClaimKind.MEASURED,
                        statement="The key filter returned 4242 rows.",
                        support="filter on partition key / statement 1 rows",
                    ),
                ],
                caveats=[],
            ),
            statistics=experiment.statistics,
            documentation=[],
        )
        assert len(verified.unsupported) == 2

    async def test_a_node_that_dies_fails_the_experiment_honestly(self) -> None:
        scripts = scripted_outputs()
        scripts[cql_command(CQLSH, OFF_KEY, keyspace_for(EXPERIMENT_ID, 1, 1))] = ScriptedCommand(
            stderr="Error response from daemon: container 9b41 is not running", exit_code=1
        )
        fake = await provider(**scripts)
        agent = CliExperimentAgent(
            repo_url=CASSANDRA_REPO,
            commit_sha=SHA,
            artifact=artifact(),
            router=Ultra(proposal()),
        )
        experiment = await agent.propose(QUESTION, experiment_id=EXPERIMENT_ID)
        experiment.approve()

        with pytest.raises(NodeStoppedError):
            await agent.execute(experiment, provider=fake, ready_timeout_seconds=5)
        assert experiment.status is ExperimentStatus.FAILED
        assert fake.terminated


def test_statistics_need_no_model() -> None:
    assert compute_cli_statistics([]).variants == []
