"""The Learning Agent, offline.

Four inputs are named by the specification: repository evidence, repository
documentation, version-matched official documentation, and the environment's
capabilities. Each one is asserted to reach the prompt, because an input that is
gathered and then dropped looks exactly like one that was never gathered.

The rest is about what the agent refuses to put in front of a learner: a step
whose statement would be refused, a step nothing can check, and a citation to a
file that is not in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.builds.models import BuildArtifact, BuildCapabilities, SmokeTestResult
from repolab.interfaces import SearchResultItem
from repolab.lessons.agent import (
    DOC_DOMAINS,
    MAX_GENERATION_ATTEMPTS,
    GeneratedLesson,
    GeneratedStep,
    LearningAgent,
)
from repolab.lessons.cassandra import CQLSH_DOC, gather_repository_evidence
from repolab.lessons.models import CheckKind
from repolab.manifest.models import BuildKind
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest

CQLSH = "/opt/cassandra/bin/cqlsh"


def artifact(kind: BuildKind = BuildKind.OFFICIAL_RELEASE) -> BuildArtifact:
    return BuildArtifact(
        repo_url=CASSANDRA_REPO,
        commit_sha=SHA,
        kind=kind,
        version="5.0.9",
        checkpoint_id="ckpt",
        checkpoint_tag="tag",
        cli_command=CQLSH,
        service_command="cassandra -f",
        smoke=SmokeTestResult(name="cqlsh connects", command="x", passed=True),
        capabilities=BuildCapabilities(
            capabilities=["execute CQL through the official cqlsh"],
            limitations=["a single node, so replication cannot be observed"],
            deviations=["official Cassandra 5.0 substituted for the requested commit"],
        ),
        simulated=kind is BuildKind.MINI_LAB,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout holding two of the files the lesson's hints look for."""
    root = tmp_path / "cassandra"
    (root / "bin").mkdir(parents=True)
    # The real launcher's own line: `bin/cqlsh.py` imports main from pylib and
    # calls it, which is what the lesson's source hint looks for.
    (root / "bin" / "cqlsh.py").write_text(
        "from cqlshlib.cqlshmain import main\n\nmain(sys.argv[1:], CASSANDRA_PATH)\n"
    )
    doc = root / Path(CQLSH_DOC).parent
    doc.mkdir(parents=True)
    (doc / Path(CQLSH_DOC).name).write_text("= cqlsh: the CQL shell\n\nThe cqlsh utility...\n")
    restrictions = root / "src/java/org/apache/cassandra/cql3/restrictions"
    restrictions.mkdir(parents=True)
    (restrictions / "StatementRestrictions.java").write_text(
        "class StatementRestrictions {\n"
        '  static final String REQUIRES_ALLOW_FILTERING_MESSAGE = "use ALLOW FILTERING";\n'
        "}\n"
    )
    return root


class ScriptedRouter:
    """Returns generated lessons in order, and records the prompts it was given.

    More than one so a corrective second attempt can be exercised: the real
    generator left an expectation blank on its first real run, and what matters
    is that the retry is *told why* rather than simply asked again.
    """

    def __init__(self, *lessons: GeneratedLesson) -> None:
        self.lessons = list(lessons)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def super(self, *, purpose, schema, prompt, system=None, **_: object):
        self.prompts.append(prompt)
        self.systems.append(system or "")
        lesson = self.lessons[min(len(self.prompts), len(self.lessons)) - 1]

        class Completion:
            value = lesson

        return Completion()


class BrokenRouter:
    async def super(self, **_: object):
        raise RuntimeError("the model is unavailable")


class Research:
    """A research provider returning one official page, recording the query."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, list[str] | None]] = []

    async def search(self, query, *, include_domains=None, max_results=5):
        self.queries.append((query, include_domains))
        return [
            SearchResultItem(
                url="https://cassandra.apache.org/doc/5.0/cassandra/developing/cql/ddl.html",
                title="Data Definition",
                content="A primary key is composed of a partition key and clustering columns.",
            )
        ]


def generated(**overrides: object) -> GeneratedLesson:
    step = GeneratedStep(
        concept="keyspaces",
        goal="see what exists",
        context="Cassandra keeps its own schema in system keyspaces",
        action="DESCRIBE KEYSPACES",
        expected_observation="a list including system_schema",
        check_kind=CheckKind.OUTPUT_CONTAINS,
        check_expected="system_schema",
        check_describe="the list includes Cassandra's own schema keyspace",
        hint="DESC KEYSPACES is the same command",
        code_paths=["bin/cqlsh.py"],
        documentation_urls=[
            "https://cassandra.apache.org/doc/5.0/cassandra/developing/cql/ddl.html"
        ],
    )
    payload: dict[str, object] = {
        "title": "Exploring Cassandra",
        "summary": "A short tour of cqlsh",
        "steps": [step],
    }
    payload.update(overrides)
    return GeneratedLesson(**payload)  # type: ignore[arg-type]


class TestGatheringTheFourInputs:
    async def test_repository_evidence_is_resolved_against_the_real_checkout(
        self, checkout: Path
    ) -> None:
        agent = LearningAgent()
        inputs, evidence = await agent.gather(cassandra_manifest(), artifact(), checkout)

        assert "cqlsh" in evidence.code
        reference = evidence.code["cqlsh"]
        assert reference.path == "bin/cqlsh.py"
        assert reference.start_line == 1, "the line is read, not asserted"
        assert any(ref.path == "bin/cqlsh.py" for ref in inputs.repository_files)

    async def test_a_hint_that_does_not_resolve_is_dropped_not_invented(
        self, checkout: Path
    ) -> None:
        """An unverifiable citation is worse than none: a learner cannot tell."""
        _, evidence = await LearningAgent().gather(cassandra_manifest(), artifact(), checkout)

        assert "describe" in evidence.unresolved
        assert all("DescribeStatement" not in ref.path for ref in evidence.code.values())

    async def test_the_repositorys_own_documentation_reaches_the_prompt(
        self, checkout: Path
    ) -> None:
        router = ScriptedRouter(generated())
        agent = LearningAgent(router=router)

        await agent.generate(cassandra_manifest(), artifact(), checkout)

        assert "the CQL shell" in router.prompts[0]
        assert CQLSH_DOC in router.prompts[0]

    async def test_official_documentation_is_version_matched_and_domain_restricted(
        self, checkout: Path
    ) -> None:
        research = Research()
        router = ScriptedRouter(generated())

        await LearningAgent(router=router, research=research).generate(
            cassandra_manifest(), artifact(), checkout
        )

        query, domains = research.queries[0]
        assert "5.0.9" in query, "the running version, not a guess"
        assert domains == list(DOC_DOMAINS)
        assert "cassandra.apache.org" in router.prompts[0]

    async def test_the_environments_capabilities_and_limits_reach_the_prompt(
        self, checkout: Path
    ) -> None:
        router = ScriptedRouter(generated())

        await LearningAgent(router=router).generate(cassandra_manifest(), artifact(), checkout)

        prompt = router.prompts[0]
        assert "execute CQL through the official cqlsh" in prompt
        assert "a single node, so replication cannot be observed" in prompt
        assert CQLSH in prompt

    async def test_the_permitted_statements_reach_the_prompt(self, checkout: Path) -> None:
        """So the generator knows the allowlist rather than discovering it by rejection."""
        router = ScriptedRouter(generated())

        await LearningAgent(router=router).generate(cassandra_manifest(), artifact(), checkout)

        assert "DESCRIBE" in router.prompts[0]
        assert "STATEMENTS THE TERMINAL PERMITS" in router.prompts[0]

    async def test_no_research_provider_means_no_documentation_citations(self) -> None:
        """Citing a URL RepoLab never retrieved would be guessing at an external API."""
        inputs, _ = await LearningAgent().gather(cassandra_manifest(), artifact(), None)

        assert inputs.official_documentation == []


class TestWhatIsPutInFrontOfALearner:
    async def test_a_generated_lesson_is_used_when_it_passes_the_gate(self, checkout: Path) -> None:
        lesson = await LearningAgent(router=ScriptedRouter(generated())).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert lesson.generated_by == "super"
        assert lesson.steps[0].action == "DESCRIBE KEYSPACES"
        assert lesson.steps[0].number == 1

    async def test_only_verifiable_references_survive(self, checkout: Path) -> None:
        """A path the model invented is dropped rather than shown as a citation."""
        invented = generated()
        invented.steps[0].code_paths = ["bin/cqlsh.py", "src/invented/Nowhere.java"]

        lesson = await LearningAgent(router=ScriptedRouter(invented)).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert [ref.path for ref in lesson.steps[0].code_references] == ["bin/cqlsh.py"]

    async def test_a_step_citing_nothing_gets_repolabs_own_citation(self, checkout: Path) -> None:
        """Observed on the real generator: eight correct steps, not one citation.

        The replacement is a lookup, not a claim: the statement says what it
        does, and the reference is a file RepoLab already read at this commit.
        """
        uncited = generated()
        uncited.steps[0].code_paths = []

        lesson = await LearningAgent(router=ScriptedRouter(uncited)).generate(
            cassandra_manifest(), artifact(), checkout
        )

        step = lesson.steps[0]
        assert step.action.lower().startswith("describe")
        assert step.grounded is True
        assert any("cqlsh.adoc" in ref.path for ref in step.code_references)

    async def test_a_derived_citation_is_still_only_a_resolved_file(self, checkout: Path) -> None:
        """With nothing resolved, nothing is attached. No file is conjured."""
        uncited = generated()
        uncited.steps[0].code_paths = []

        lesson = await LearningAgent(router=ScriptedRouter(uncited)).generate(
            cassandra_manifest(), artifact(), None
        )

        assert lesson.steps[0].code_references == []

    async def test_an_unretrieved_documentation_url_is_dropped(self, checkout: Path) -> None:
        lesson = await LearningAgent(router=ScriptedRouter(generated())).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert lesson.steps[0].documentation_references == [], "nothing was retrieved"

    async def test_a_retrieved_documentation_url_is_kept(self, checkout: Path) -> None:
        lesson = await LearningAgent(
            router=ScriptedRouter(generated()), research=Research()
        ).generate(cassandra_manifest(), artifact(), checkout)

        urls = [ref.url for ref in lesson.steps[0].documentation_references]
        assert urls == ["https://cassandra.apache.org/doc/5.0/cassandra/developing/cql/ddl.html"]

    async def test_a_lesson_with_an_unsafe_statement_is_rejected(self, checkout: Path) -> None:
        unsafe = generated()
        unsafe.steps[0].action = "DROP KEYSPACE system_auth"

        lesson = await LearningAgent(router=ScriptedRouter(unsafe)).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert lesson.generated_by == "deterministic", "RepoLab's own lesson, not the rejected one"

    async def test_a_lesson_with_an_uncheckable_step_is_rejected(self, checkout: Path) -> None:
        uncheckable = generated()
        uncheckable.steps[0].check_expected = ""
        router = ScriptedRouter(uncheckable)

        lesson = await LearningAgent(router=router).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert lesson.generated_by == "deterministic"
        assert len(router.prompts) == MAX_GENERATION_ATTEMPTS, "it was given one chance to fix it"

    async def test_a_rejected_attempt_is_told_exactly_what_was_wrong(self, checkout: Path) -> None:
        """Observed on the real generator: eight good steps, one blank expectation."""
        uncheckable = generated()
        uncheckable.steps[0].check_expected = ""
        router = ScriptedRouter(uncheckable, generated())

        lesson = await LearningAgent(router=router).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert lesson.generated_by == "super", "the corrected lesson was used"
        second = router.prompts[1]
        assert "PREVIOUS ATTEMPT WAS REJECTED" in second
        assert "cannot be checked" in second

    async def test_a_lesson_that_passes_first_time_is_asked_for_once(self, checkout: Path) -> None:
        router = ScriptedRouter(generated())

        await LearningAgent(router=router).generate(cassandra_manifest(), artifact(), checkout)

        assert len(router.prompts) == 1

    async def test_a_model_failure_falls_back_rather_than_failing(self, checkout: Path) -> None:
        lesson = await LearningAgent(router=BrokenRouter()).generate(
            cassandra_manifest(), artifact(), checkout
        )

        assert lesson.generated_by == "deterministic"
        assert lesson.step_count == 8

    async def test_the_deterministic_lesson_is_complete_with_no_model_at_all(
        self, checkout: Path
    ) -> None:
        """The reason the offline suite can exercise the whole flow."""
        lesson = await LearningAgent().generate(cassandra_manifest(), artifact(), checkout)

        assert lesson.generated_by == "deterministic"
        assert lesson.step_count == 8
        assert any(step.expects_failure for step in lesson.steps), "one safe error, on purpose"

    async def test_the_lesson_records_the_environment_it_was_written_for(
        self, checkout: Path
    ) -> None:
        lesson = await LearningAgent().generate(cassandra_manifest(), artifact(), checkout)

        assert lesson.fidelity == "high"
        assert lesson.simulated is False

    async def test_steps_are_grounded_when_the_checkout_resolves(self, checkout: Path) -> None:
        lesson = await LearningAgent().generate(cassandra_manifest(), artifact(), checkout)

        assert 7 not in lesson.ungrounded_steps, "the error step cites the code that raises it"


class TestEvidenceWithoutACheckout:
    def test_nothing_resolves_and_that_is_recorded(self) -> None:
        evidence = gather_repository_evidence(None, SHA)

        assert evidence.code == {}
        assert len(evidence.unresolved) > 0, "a thin lesson should be explicable"

    def test_the_lesson_still_works(self) -> None:
        from repolab.lessons.cassandra import cassandra_lesson

        lesson = cassandra_lesson(CASSANDRA_REPO, SHA)
        assert lesson.step_count == 8
