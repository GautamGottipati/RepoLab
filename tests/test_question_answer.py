"""Answering a question from the repository, offline.

What matters: the repository's own words and the project's official docs reach
the prompt, the documentation search is restricted to the project's own domain,
and a claim citing a file or page that was never part of the evidence is
flagged rather than shown as if it were supported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.evidence.models import EvidenceBundle
from repolab.interfaces import SearchResultItem
from repolab.llm import LLMError
from repolab.questions.answer import AnswerAgent, verify_answer
from repolab.questions.investigation import official_domain
from repolab.questions.models import (
    AnswerClaim,
    AnswerClaimKind,
    GroundedAnswer,
    QuestionKind,
)
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest

DOC_URL = "https://cassandra.apache.org/_/cassandra-basics.html"


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "cassandra"
    (root / "src/java/org/apache/cassandra/service").mkdir(parents=True)
    (root / "README.asciidoc").write_text(
        "Apache Cassandra\n----------------\n\n"
        "Apache Cassandra is a highly-scalable partitioned row store.\n"
    )
    (root / "src/java/org/apache/cassandra/service/CassandraDaemon.java").write_text(
        "public class CassandraDaemon {\n  // The Cassandra server entry point\n}\n"
    )
    return root


class Research:
    def __init__(self) -> None:
        self.queries: list[tuple[str, list[str] | None]] = []

    async def search(self, query, *, include_domains=None, max_results=5):
        self.queries.append((query, include_domains))
        return [
            SearchResultItem(
                url=DOC_URL,
                title="Cassandra Basics",
                content="Apache Cassandra is an open source NoSQL distributed database.",
            )
        ]


class Super:
    def __init__(self, answer: GroundedAnswer | None = None, *, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.prompts: list[str] = []

    async def super(self, *, prompt: str, **_: object):
        self.prompts.append(prompt)
        if self.fail:
            raise LLMError("the model is unavailable")
        value = self.answer

        class Completion:
            pass

        Completion.value = value  # type: ignore[attr-defined]
        return Completion()


def grounded(*claims: AnswerClaim) -> GroundedAnswer:
    return GroundedAnswer(
        answer="Cassandra is a distributed, partitioned row store.",
        claims=list(claims),
        caveats=[],
    )


def agent(checkout: Path, router: Super | None, research: Research | None) -> AnswerAgent:
    return AnswerAgent(
        repo_url=CASSANDRA_REPO,
        commit_sha=SHA,
        repository_root=checkout,
        manifest=cassandra_manifest(),
        router=router,
        research=research,
    )


class TestUnderstandingTheRepositoryFirst:
    async def test_the_readme_and_the_docs_reach_the_prompt(self, checkout: Path) -> None:
        router = Super(grounded())
        research = Research()

        await agent(checkout, router, research).answer(
            "What is cassandra", kind=QuestionKind.CONCEPTUAL
        )

        prompt = router.prompts[0]
        assert "highly-scalable partitioned row store" in prompt
        assert DOC_URL in prompt
        assert "cqlsh" in prompt, "the manifest's findings are part of the evidence"

    async def test_documentation_is_searched_only_on_the_projects_own_domain(
        self, checkout: Path
    ) -> None:
        research = Research()
        await agent(checkout, Super(grounded()), research).answer(
            "What is cassandra", kind=QuestionKind.CONCEPTUAL
        )

        assert research.queries
        assert all(domains == ["cassandra.apache.org"] for _, domains in research.queries)

    def test_the_domain_comes_from_the_repository_when_no_framework_names_it(self) -> None:
        assert official_domain(cassandra_manifest(), CASSANDRA_REPO) == "cassandra.apache.org"
        assert official_domain(None, "https://github.com/someone/unknown") is None


class TestGrounding:
    async def test_supported_claims_pass_and_an_invented_file_is_flagged(
        self, checkout: Path
    ) -> None:
        router = Super(
            grounded(
                AnswerClaim(
                    kind=AnswerClaimKind.REPOSITORY,
                    statement="The README calls it a partitioned row store.",
                    support="README.asciidoc",
                ),
                AnswerClaim(
                    kind=AnswerClaimKind.DOCUMENTED,
                    statement="It is a NoSQL distributed database.",
                    support=DOC_URL,
                ),
                AnswerClaim(
                    kind=AnswerClaimKind.REPOSITORY,
                    statement="The storage engine lives in Engine.java.",
                    support="src/Engine.java",
                ),
            )
        )

        result = await agent(checkout, router, Research()).answer(
            "What is cassandra", kind=QuestionKind.CONCEPTUAL
        )

        assert result.answer is not None
        assert result.answer.checked_claims == 3
        assert [claim.statement for claim in result.answer.unsupported] == [
            "The storage engine lives in Engine.java."
        ]

    def test_a_real_file_the_search_did_not_open_is_not_a_fabrication(self, checkout: Path) -> None:
        verified = verify_answer(
            grounded(
                AnswerClaim(
                    kind=AnswerClaimKind.REPOSITORY,
                    statement="The daemon is the server entry point.",
                    support="src/java/org/apache/cassandra/service/CassandraDaemon.java:1",
                )
            ),
            evidence=EvidenceBundle(),
            repository_root=checkout,
        )
        assert verified.grounded

    def test_a_path_outside_the_checkout_is_never_accepted(self, checkout: Path) -> None:
        verified = verify_answer(
            grounded(
                AnswerClaim(
                    kind=AnswerClaimKind.REPOSITORY, statement="x", support="../../etc/passwd"
                )
            ),
            evidence=EvidenceBundle(),
            repository_root=checkout,
        )
        assert not verified.grounded


class TestWithoutAModel:
    async def test_no_model_means_evidence_and_no_invented_answer(self, checkout: Path) -> None:
        result = await agent(checkout, None, Research()).answer(
            "What is cassandra", kind=QuestionKind.CONCEPTUAL
        )

        assert result.answer is None
        assert result.error and "No model" in result.error
        assert any(ref.path == "README.asciidoc" for ref in result.evidence.code_references)

    async def test_a_failed_model_is_reported(self, checkout: Path) -> None:
        result = await agent(checkout, Super(fail=True), None).answer(
            "What is cassandra", kind=QuestionKind.CONCEPTUAL
        )

        assert result.answer is None
        assert result.error

    async def test_a_downgrade_travels_with_the_answer(self, checkout: Path) -> None:
        router = Super(grounded())
        result = await agent(checkout, router, None).answer(
            "What happens if I drop a table?",
            kind=QuestionKind.TESTABLE,
            downgraded="no environment has been built yet",
        )

        assert result.downgraded == "no environment has been built yet"
        assert "no environment has been built yet" in router.prompts[0]


class TestFindingTheOverview:
    @pytest.mark.parametrize(
        ("paths", "expected"),
        [
            (["README.asc", "build.xml"], "README.asc"),
            (["readme.txt", "README.md"], "README.md"),
            (["docs/README.md", "setup.py"], None),
            (["README"], "README"),
        ],
    )
    def test_any_top_level_readme_is_found(self, paths: list[str], expected: str | None) -> None:
        from repolab.questions.investigation import overview_file

        assert overview_file(paths) == expected

    def test_the_projects_own_name_is_not_searched_for(self) -> None:
        from repolab.questions.investigation import QuestionInvestigationPlanner

        planner = QuestionInvestigationPlanner("What is cassandra", subject="cassandra")
        assert "cassandra" not in planner._terms
