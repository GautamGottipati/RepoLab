"""A line an answer cites counts only where RepoLab itself read that line."""

from __future__ import annotations

from repolab.evidence.models import CodeReference, EvidenceBundle
from repolab.questions.answer import pin_cited_lines
from repolab.questions.models import AnswerClaim, AnswerClaimKind, AnswerStep, GroundedAnswer


def answer(*supports: str, steps: list[str] | None = None) -> GroundedAnswer:
    return GroundedAnswer(
        answer="x",
        claims=[
            AnswerClaim(kind=AnswerClaimKind.REPOSITORY, statement="s", support=support)
            for support in supports
        ],
        caveats=[],
        steps=[AnswerStep(title="t", description="d", sources=steps or [])],
    )


EVIDENCE = EvidenceBundle(
    code_references=[CodeReference(path="core/Scheduler.scala", start_line=120, end_line=184)]
)


def test_a_line_inside_what_repolab_read_is_kept() -> None:
    result = answer("core/Scheduler.scala:150", "core/Scheduler.scala:186")
    pin_cited_lines(result, EVIDENCE)
    assert [c.support for c in result.claims] == [
        "core/Scheduler.scala:150",
        "core/Scheduler.scala:186",
    ]


def test_a_line_the_model_chose_on_its_own_is_reduced_to_the_file() -> None:
    result = answer(
        "core/Scheduler.scala:900", "other/File.py:12", steps=["core/Scheduler.scala:3"]
    )
    pin_cited_lines(result, EVIDENCE)
    assert [c.support for c in result.claims] == ["core/Scheduler.scala", "other/File.py"]
    assert result.steps[0].sources == ["core/Scheduler.scala"]


def test_urls_and_plain_paths_are_untouched() -> None:
    result = answer("https://spark.apache.org/docs/latest/", "core/Scheduler.scala")
    pin_cited_lines(result, EVIDENCE)
    assert [c.support for c in result.claims] == [
        "https://spark.apache.org/docs/latest/",
        "core/Scheduler.scala",
    ]
