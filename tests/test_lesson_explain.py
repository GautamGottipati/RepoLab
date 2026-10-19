"""\"What Just Happened?\", and what it refuses to say.

The explanation is produced by a model and then **checked by code**: a claim that
quotes output that is not there, cites a file that was not supplied, or states a
timing is reported as unsupported rather than printed as fact. A terminal result
contains no measurements, so an explanation that produces one has invented it --
which is the single thing CLAUDE.md is most insistent about.
"""

from __future__ import annotations

import pytest

from repolab.evidence.models import CodeReference, DocumentationReference
from repolab.lessons.cassandra import cassandra_lesson
from repolab.lessons.explain import (
    Claim,
    ExplainInput,
    SupportKind,
    WhatHappened,
    build_explain_input,
    explain_command,
    explainable,
    verify_what_happened,
)
from repolab.lessons.models import CheckKind, StepCheck

OUTPUT = " sensor_id | celsius\n-----------+---------\n   kitchen |    21.5\n\n(1 rows)\n"
SOURCE = "src/java/org/apache/cassandra/cql3/statements/SelectStatement.java"
DOC = "https://cassandra.apache.org/doc/5.0/cassandra/developing/cql/dml.html"

LESSON = cassandra_lesson("https://github.com/apache/cassandra", "c" * 40)


def data(**overrides: object) -> ExplainInput:
    payload: dict[str, object] = {
        "command": (
            "SELECT sensor_id, celsius FROM repolab.sensor_readings WHERE sensor_id = 'kitchen'"
        ),
        "output": OUTPUT,
        "exit_code": 0,
        "concept": "a query naming the partition key is a direct read",
        "version": "5.0.9",
        "code_references": [CodeReference(path=SOURCE, start_line=120)],
        "documentation": [DocumentationReference(url=DOC, title="Data Manipulation")],
    }
    payload.update(overrides)
    return ExplainInput(**payload)  # type: ignore[arg-type]


def answer(*claims: Claim) -> WhatHappened:
    return WhatHappened(summary="Cassandra read one partition.", claims=list(claims))


class ScriptedRouter:
    def __init__(self, value: WhatHappened) -> None:
        self.value = value
        self.prompts: list[str] = []

    async def super(self, *, purpose, schema, prompt, system=None, **_: object):
        self.prompts.append(prompt)
        value = self.value

        class Completion:
            pass

        Completion.value = value  # type: ignore[attr-defined]
        return Completion()


class TestWhichStepsAreWorthExplaining:
    def test_a_step_grounded_in_source_is(self) -> None:
        step = LESSON.step(1)
        assert step is not None
        grounded = step.model_copy(update={"code_references": [CodeReference(path=SOURCE)]})
        assert explainable(grounded) is True

    def test_the_error_step_is(self) -> None:
        """The message is the lesson, so there is always something to explain."""
        step = LESSON.step(7)
        assert step is not None and explainable(step) is True

    def test_a_bare_step_is_not(self) -> None:
        step = LESSON.step(3)
        assert step is not None
        bare = step.model_copy(update={"code_references": [], "documentation_references": []})
        assert explainable(bare) is False


class TestVerification:
    def test_an_observed_claim_quoting_real_output_is_supported(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="One row was returned, for the kitchen sensor.",
                    support="kitchen |    21.5",
                )
            ),
            data(),
        )

        assert verified.grounded is True
        assert verified.checked_claims == 1

    def test_whitespace_differences_do_not_break_a_real_quote(self) -> None:
        """cqlsh pads its columns; a quote should survive being re-spaced."""
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="The row came back.",
                    support="kitchen | 21.5",
                )
            ),
            data(),
        )
        assert verified.grounded is True

    def test_an_observed_claim_quoting_absent_text_is_unsupported(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="Two rows were returned.",
                    support="bedroom |    19.0",
                )
            ),
            data(),
        )

        assert verified.grounded is False
        assert "not in the output" in verified.unsupported[0].problem

    def test_an_observed_claim_stating_an_absent_number_is_unsupported(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="The query scanned 4096 partitions.",
                    support="(1 rows)",
                )
            ),
            data(),
        )

        assert verified.grounded is False
        assert "4096" in verified.unsupported[0].problem

    def test_a_source_claim_may_carry_the_line_it_was_shown(self) -> None:
        """The prompt lists files as `path:line`, so a model will cite one that way.

        Observed on the real explainer: a correct citation of
        `StatementRestrictions.java:81` was reported as unsupported.
        """
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.SOURCE,
                    statement="The message comes from the restriction check.",
                    support=f"{SOURCE}:120",
                )
            ),
            data(),
        )

        assert verified.grounded is True

    def test_a_source_claim_must_cite_a_supplied_file(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.SOURCE,
                    statement="The read is planned by SelectStatement.",
                    support="src/java/org/apache/cassandra/invented/Nowhere.java",
                )
            ),
            data(),
        )

        assert verified.grounded is False
        assert "was not supplied" in verified.unsupported[0].problem

    def test_a_documented_claim_must_cite_a_retrieved_page(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.DOCUMENTED,
                    statement="Reads are answered from the partition key.",
                    support="https://example.invalid/docs",
                )
            ),
            data(),
        )

        assert verified.grounded is False
        assert "was not retrieved" in verified.unsupported[0].problem

    def test_an_inferred_claim_is_not_checked(self) -> None:
        """An inference legitimately restates what is already labelled; it is the weaker kind."""
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.INFERRED,
                    statement="This table is shaped for queries by sensor.",
                    support="the observed and documented claims",
                )
            ),
            data(),
        )

        assert verified.grounded is True
        assert verified.checked_claims == 0


class TestNoMeasurementsAreEverProduced:
    @pytest.mark.parametrize(
        "statement",
        [
            "The query completed in 4 ms.",
            "It took about 12 seconds to return.",
            "Reading one partition has lower latency than a full scan.",
            "This is faster than a filtering query.",
            "Cassandra sustained 10000 writes per second.",
        ],
    )
    def test_a_claim_stating_performance_is_refused(self, statement: str) -> None:
        verified = verify_what_happened(
            answer(Claim(kind=SupportKind.OBSERVED, statement=statement, support="(1 rows)")),
            data(),
        )

        assert verified.grounded is False
        assert "timing" in verified.unsupported[0].problem

    def test_timing_language_is_refused_even_when_merely_inferred(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.INFERRED,
                    statement="So this query has lower latency.",
                    support="the observed claim",
                )
            ),
            data(),
        )
        assert verified.grounded is False

    def test_an_ordinary_sentence_containing_second_is_not(self) -> None:
        """ "The second column" is a legitimate sentence, and must not be rejected."""
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="The second column is celsius.",
                    support="sensor_id | celsius",
                )
            ),
            data(),
        )
        assert verified.grounded is True

    def test_the_evidence_bundle_carries_no_measurements(self) -> None:
        verified = verify_what_happened(answer(), data())

        assert verified.evidence.measurements == []
        assert verified.evidence.logs[0].excerpt.startswith(" sensor_id")
        assert verified.evidence.simulated is False


class TestSimulated:
    def test_nothing_can_be_observed_when_nothing_ran(self) -> None:
        verified = verify_what_happened(
            answer(
                Claim(
                    kind=SupportKind.OBSERVED,
                    statement="One row was returned.",
                    support="kitchen |    21.5",
                )
            ),
            data(output=""),
            simulated=True,
        )

        assert verified.grounded is False
        assert "nothing was observed" in verified.unsupported[0].problem

    def test_a_simulated_bundle_is_labelled_and_carries_no_log(self) -> None:
        verified = verify_what_happened(answer(), data(output=""), simulated=True)

        assert verified.evidence.simulated is True
        assert verified.evidence.logs == []


class TestWhatTheModelIsShown:
    async def test_the_prompt_carries_the_output_the_code_and_the_docs(self) -> None:
        router = ScriptedRouter(answer())

        await explain_command(router, data())

        prompt = router.prompts[0]
        assert "kitchen" in prompt
        assert SOURCE in prompt
        assert DOC in prompt
        assert "5.0.9" in prompt

    def test_the_input_is_built_from_the_step_and_the_real_result(self) -> None:
        step = LESSON.step(7)
        assert step is not None
        built = build_explain_input(
            step,
            step.action,
            "InvalidRequest: use ALLOW FILTERING",
            exit_code=2,
            version="5.0.9",
        )

        assert built.concept == step.concept
        assert built.exit_code == 2
        assert built.code_references == step.code_references

    def test_a_step_check_is_not_part_of_the_explanation_input(self) -> None:
        """The explanation explains; it does not decide whether the step passed."""
        step = LESSON.step(1)
        assert step is not None
        built = build_explain_input(step, step.action, "5.0.9")
        assert not hasattr(built, "validation")
        assert StepCheck(kind=CheckKind.SUCCEEDS) not in built.model_dump().values()
