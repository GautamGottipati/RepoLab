"""Triaging a question before anything is designed.

The bug this exists for: "What is Cassandra?" asked on the Cassandra repository
produced a Spark shuffle-partition experiment, because every question went
straight to the Spark experiment designer. A question is now classified first,
and deterministic code -- not the model -- decides whether this repository has
anything a testable question could be run against.
"""

from __future__ import annotations

import pytest

from repolab.builds.models import BuildArtifact, SmokeTestResult
from repolab.llm import LLMError
from repolab.manifest.models import BuildKind
from repolab.questions import ExperimentBackend, QuestionKind, Route, triage
from repolab.questions.models import TriageVerdict
from repolab.questions.triage import (
    cli_artifact,
    explanation_shape,
    rule_kind,
    spark_backend_available,
)
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest
from tests.support.manifests import spark_manifest

CQLSH = "/opt/cassandra/bin/cqlsh"


def cassandra_build(
    kind: BuildKind = BuildKind.OFFICIAL_RELEASE, *, cli: str | None = CQLSH
) -> BuildArtifact:
    return BuildArtifact(
        repo_url=CASSANDRA_REPO,
        commit_sha=SHA,
        kind=kind,
        version="5.0.9",
        checkpoint_id="ckpt",
        checkpoint_tag="repolab/apache-cassandra:abc-official-release",
        service_command="cassandra -f",
        cli_command=cli,
        memory="4g",
        smoke=SmokeTestResult(name="cqlsh connects", command="x", passed=True),
        simulated=kind is BuildKind.MINI_LAB,
    )


class Nano:
    """A router whose Nano returns one verdict, recording that it was asked."""

    def __init__(self, kind: QuestionKind, *, fail: bool = False) -> None:
        self.kind = kind
        self.fail = fail
        self.calls = 0

    async def nano(self, **_: object):
        self.calls += 1
        if self.fail:
            raise LLMError("the model is unavailable")
        verdict = TriageVerdict(kind=self.kind, reason="the model said so")

        class Completion:
            value = verdict

        return Completion()


AMBIGUOUS = "Tell me about tombstones and compaction in this thing"


class TestTheReportedBug:
    async def test_what_is_cassandra_is_answered_not_experimented_on(self) -> None:
        decision = await triage(
            "What is cassandra",
            manifest=cassandra_manifest(),
            artifacts=[cassandra_build()],
        )

        assert decision.kind is QuestionKind.CONCEPTUAL
        assert decision.route is Route.ANSWER
        assert decision.backend is None

    async def test_a_cassandra_question_about_partitions_never_reaches_spark(self) -> None:
        """'partitions' is a Spark variable alias. On Cassandra it means something else."""
        decision = await triage(
            "What are partitions in Cassandra?", manifest=cassandra_manifest(), artifacts=[]
        )

        assert decision.route is Route.ANSWER
        assert decision.backend is not ExperimentBackend.SPARK


class TestRules:
    @pytest.mark.parametrize(
        ("question", "kind"),
        [
            ("What is Cassandra?", QuestionKind.CONCEPTUAL),
            ("Explain the gossip protocol", QuestionKind.CONCEPTUAL),
            ("Where is the CQL parser implemented?", QuestionKind.REPOSITORY),
            ("Which class handles compaction?", QuestionKind.REPOSITORY),
            (
                "What happens if I filter on a column that is not in the primary key?",
                QuestionKind.TESTABLE,
            ),
            ("Why does this job use 200 shuffle partitions?", QuestionKind.TESTABLE),
        ],
    )
    def test_clear_phrasings_are_settled_without_a_model(
        self, question: str, kind: QuestionKind
    ) -> None:
        assert rule_kind(question) is kind

    def test_an_ambiguous_question_is_left_to_the_model(self) -> None:
        assert rule_kind(AMBIGUOUS) is None


class TestTheModel:
    async def test_the_model_decides_only_what_the_rules_do_not(self) -> None:
        router = Nano(QuestionKind.REPOSITORY)
        decision = await triage(
            AMBIGUOUS, manifest=cassandra_manifest(), artifacts=[], router=router
        )

        assert router.calls == 1
        assert decision.kind is QuestionKind.REPOSITORY
        assert decision.decided_by == "model"

    async def test_rules_settle_without_asking(self) -> None:
        router = Nano(QuestionKind.TESTABLE)
        decision = await triage("What is Cassandra?", manifest=cassandra_manifest(), router=router)

        assert router.calls == 0
        assert decision.kind is QuestionKind.CONCEPTUAL

    async def test_a_failed_model_falls_back_rather_than_failing(self) -> None:
        decision = await triage(
            AMBIGUOUS,
            manifest=cassandra_manifest(),
            router=Nano(QuestionKind.TESTABLE, fail=True),
        )

        assert decision.decided_by == "fallback"
        assert decision.route is Route.ANSWER

    async def test_the_model_cannot_send_a_question_to_an_environment_that_does_not_exist(
        self,
    ) -> None:
        decision = await triage(
            AMBIGUOUS,
            manifest=cassandra_manifest(),
            artifacts=[],
            router=Nano(QuestionKind.TESTABLE),
        )

        assert decision.kind is QuestionKind.TESTABLE
        assert decision.route is Route.ANSWER
        assert decision.downgraded and "Learn" in decision.downgraded


class TestTheBackendGate:
    TESTABLE = "What happens if I query a column that is not part of the primary key?"

    async def test_a_testable_cassandra_question_runs_on_the_built_node(self) -> None:
        decision = await triage(
            self.TESTABLE, manifest=cassandra_manifest(), artifacts=[cassandra_build()]
        )

        assert decision.route is Route.EXPERIMENT
        assert decision.backend is ExperimentBackend.CLI

    async def test_without_a_build_it_is_answered_and_says_why(self) -> None:
        decision = await triage(self.TESTABLE, manifest=cassandra_manifest(), artifacts=[])

        assert decision.kind is QuestionKind.TESTABLE
        assert decision.route is Route.ANSWER
        assert "no environment has been built" in (decision.downgraded or "")

    async def test_a_mini_lab_is_never_experimented_on(self) -> None:
        decision = await triage(
            self.TESTABLE,
            manifest=cassandra_manifest(),
            artifacts=[cassandra_build(BuildKind.MINI_LAB)],
        )

        assert decision.route is Route.ANSWER
        assert "simulated" in (decision.downgraded or "")

    async def test_without_a_model_a_cli_experiment_is_not_designed(self) -> None:
        decision = await triage(
            self.TESTABLE,
            manifest=cassandra_manifest(),
            artifacts=[cassandra_build()],
            design_available=False,
        )

        assert decision.route is Route.ANSWER
        assert "needs a model" in (decision.downgraded or "")

    def test_only_a_cqlsh_build_can_be_driven(self) -> None:
        assert cli_artifact([cassandra_build(cli="/usr/bin/some-other-cli")]) is None
        assert cli_artifact([cassandra_build()]) is not None

    async def test_the_spark_demo_question_still_designs_the_spark_experiment(self) -> None:
        manifest = spark_manifest()
        assert spark_backend_available(manifest)

        decision = await triage(
            "Why does this job use 200 shuffle partitions?", manifest=manifest, artifacts=[]
        )

        assert decision.route is Route.EXPERIMENT
        assert decision.backend is ExperimentBackend.SPARK

    async def test_a_conceptual_question_on_spark_is_answered(self) -> None:
        decision = await triage("What is Apache Spark?", manifest=spark_manifest())

        assert decision.route is Route.ANSWER


class TestExplanationShape:
    """How an answer is laid out is read from the phrasing -- no model call."""

    @pytest.mark.parametrize(
        ("question", "shape"),
        [
            ("How does the backend server and the User API use the database?", "flow"),
            ("How do the frontend and the worker communicate?", "flow"),
            ("What happens when I call POST /register?", "execution"),
            ("How does a request flow through the application?", "execution"),
            ("What is the scheduler?", "definition"),
            ("What does CommandRunner do?", "definition"),
            ("Why does this repository use a message queue?", "why"),
            ("Why is this query slow?", "debugging"),
            ("Where is authentication implemented?", "location"),
            ("Which file defines the routes?", "location"),
            ("What is the difference between the reader and the writer?", "comparison"),
            ("Why use threads instead of processes here?", "comparison"),
            ("How is this repository organized?", "architecture"),
            ("Tell me something interesting", None),
        ],
    )
    def test_shapes_from_phrasing(self, question: str, shape: str | None) -> None:
        settled = explanation_shape(question)
        assert (settled.value if settled else None) == shape

    async def test_triage_carries_the_shape(self) -> None:
        decision = await triage("Where is the parser implemented?", manifest=None)
        assert decision.shape is not None and decision.shape.value == "location"
