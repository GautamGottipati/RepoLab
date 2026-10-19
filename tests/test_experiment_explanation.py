"""The explanation and the check against the measurements.

This is the most important file in the phase. Two failures are being prevented:

* the model receiving something it should never see -- a raw event log, which it
  could read numbers out of and present as its own;
* the model stating a number no instrument produced, or citing a document that
  was never retrieved.

The check is a detector, not a censor: it flags, and the flags travel with the
explanation so a reader sees exactly which claims the evidence did not support.
"""

from __future__ import annotations

from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
)
from repolab.experiences.spark.measurements import Determinism
from repolab.experiments.explanation import (
    Claim,
    ClaimKind,
    Explanation,
    allowed_numbers,
    build_explanation_input,
    default_notes,
    numbers_in,
    verify_explanation,
)
from repolab.experiments.statistics import compute_statistics
from tests.support.experiments import plan, variant_run

DOC_URL = "https://spark.apache.org/docs/3.5.1/sql-performance-tuning.html"


def statistics(**kwargs):
    runs = [
        variant_run(value=50, repeat=1, total_runtime_ms=800, partitions=50, empty=43),
        variant_run(value=50, repeat=2, total_runtime_ms=900, partitions=50, empty=43),
        variant_run(value=200, repeat=1, total_runtime_ms=1000, partitions=200, empty=193),
        variant_run(value=200, repeat=2, total_runtime_ms=1200, partitions=200, empty=193),
    ]
    return compute_statistics(
        runs,
        metrics=["total runtime", "partitions", "empty partitions"],
        baseline_value=200,
        **kwargs,
    )


def evidence(*, with_documentation: bool = True) -> EvidenceBundle:
    return EvidenceBundle(
        measurements=variant_run(value=200, repeat=1).to_measurements(),
        code_references=[CodeReference(path="jobs/etl_job.py", start_line=30)],
        logs=[
            LogReference(
                source="spark event log",
                excerpt='{"Event":"SparkListenerTaskEnd","Task Metrics":{"Shuffle Read Metrics"}}',
            )
        ],
        documentation=(
            [
                DocumentationReference(
                    url=DOC_URL,
                    title="Performance Tuning",
                    excerpt="spark.sql.shuffle.partitions defaults to 200.",
                    version="3.5.1",
                )
            ]
            if with_documentation
            else []
        ),
    )


class TestWhatTheModelReceives:
    def payload(self):
        return build_explanation_input(plan=plan(), statistics=statistics(), evidence=evidence())

    def test_it_carries_the_question_the_plan_and_the_statistics(self) -> None:
        payload = self.payload()
        assert payload.question
        assert payload.variable == "spark.sql.shuffle.partitions"
        assert payload.statistics.metrics
        assert payload.statistics.baseline_value == 200

    def test_it_names_the_metrics_a_measured_claim_may_cite(self) -> None:
        """Listed at the top level, not left buried in the nested comparisons."""
        payload = self.payload()
        assert payload.metrics == [m.metric for m in payload.statistics.metrics]
        assert "empty partitions" in payload.metrics

    def test_it_carries_no_raw_per_run_measurements(self) -> None:
        """The model gets the computed form, not hundreds of figures to recombine.

        The evidence bundle holds every measurement from every run; handing those
        over invites arithmetic, which is exactly what the model must not do. Log
        *excerpts* are permitted and deliberately included -- but they are quoted
        text to cite, not a measurement table, and any number taken from one and
        labelled ``measured`` is caught by :func:`verify_explanation`.
        """
        from repolab.experiments.explanation import ExplanationInput

        assert "measurements" not in ExplanationInput.model_fields

        bundle = evidence()
        assert bundle.measurements, "the fixture should carry raw measurements"

        # `source` is a field only a dumped Measurement has, so its absence means
        # no raw measurement was serialised into the payload.
        rendered = self.payload().to_prompt()
        assert "spark_event_log" not in rendered

    def test_the_permitted_fields_are_exactly_these(self) -> None:
        """Pinned deliberately: adding a field here changes what a model can see."""
        from repolab.experiments.explanation import ExplanationInput

        assert set(ExplanationInput.model_fields) == {
            "question",
            "hypothesis",
            "variable",
            "values",
            "fixed_settings",
            "repeats",
            "baseline_value",
            "metrics",
            "statistics",
            "code_references",
            "logs",
            "documentation",
            "notes",
        }

    def test_the_prompt_is_deterministic(self) -> None:
        assert self.payload().to_prompt() == self.payload().to_prompt()

    def test_log_and_documentation_counts_are_bounded(self) -> None:
        bundle = EvidenceBundle(
            logs=[LogReference(source=f"log {i}", excerpt="x") for i in range(10)],
            documentation=[DocumentationReference(url=f"https://x/{i}") for i in range(10)],
        )
        payload = build_explanation_input(
            plan=plan(), statistics=statistics(), evidence=bundle, max_logs=2
        )
        assert len(payload.logs) == 2
        assert len(payload.documentation) == 4


class TestNotes:
    def test_repolabs_own_additions_are_disclosed(self) -> None:
        notes = default_notes(plan(), statistics())
        assert any("RepoLab added" in note for note in notes)

    def test_timing_metrics_are_named_as_timings(self) -> None:
        notes = default_notes(plan(), statistics())
        assert any("vary between runs" in note and "total runtime" in note for note in notes)

    def test_contention_is_disclosed(self) -> None:
        runs = [
            variant_run(value=50, repeat=1, contended=True),
            variant_run(value=200, repeat=1, contended=True),
        ]
        stats = compute_statistics(runs, metrics=["total runtime"], baseline_value=200)
        notes = default_notes(plan(concurrency=2), stats)
        assert any("contention" in note for note in notes)

    def test_a_single_repeat_is_disclosed(self) -> None:
        """One run per value means no error bar, and the note says so.

        The statistics are built with one run per value rather than reusing the
        two-run fixture: the note now follows what *ran* rather than what was
        planned, so a plan claiming one repeat beside statistics containing two
        was describing an experiment that never happened.
        """
        runs = [
            variant_run(value=50, repeat=1, total_runtime_ms=800),
            variant_run(value=200, repeat=1, total_runtime_ms=1000),
        ]
        stats = compute_statistics(runs, metrics=["total runtime"], baseline_value=200)

        notes = default_notes(plan(repeats=1), stats)

        assert any("error bar" in note for note in notes)

    def test_fewer_repeats_than_planned_is_disclosed(self) -> None:
        """A value that lost a run must not be described as having had them all."""
        runs = [
            variant_run(value=50, repeat=1, total_runtime_ms=800),
            variant_run(value=200, repeat=1, total_runtime_ms=1000),
            variant_run(value=200, repeat=2, total_runtime_ms=1100),
        ]
        stats = compute_statistics(runs, metrics=["total runtime"], baseline_value=200)

        notes = default_notes(plan(repeats=2), stats)

        assert any("rests on fewer runs" in note for note in notes)

    def test_structural_instability_is_disclosed(self) -> None:
        runs = [
            variant_run(value=200, repeat=1, partitions=200),
            variant_run(value=200, repeat=2, partitions=199),
            variant_run(value=50, repeat=1, partitions=50),
        ]
        stats = compute_statistics(runs, metrics=["partitions"], baseline_value=200)
        notes = default_notes(plan(), stats)
        assert any("should have been identical" in note for note in notes)

    def test_dropped_metrics_are_disclosed(self) -> None:
        notes = default_notes(plan(metrics=["runtime", "gc_time_ms"]), statistics())
        assert any("cannot be measured" in note for note in notes)


class TestAllowedNumbers:
    def test_every_sample_and_aggregate_is_allowed(self) -> None:
        allowed = allowed_numbers(statistics(), plan())

        assert 800.0 in allowed and 1200.0 in allowed, "raw samples"
        assert 1100.0 in allowed, "the baseline median"
        assert 193.0 in allowed, "a measured count"

    def test_relative_differences_are_allowed(self) -> None:
        allowed = allowed_numbers(statistics(), plan())
        assert -22.73 in allowed

    def test_an_unmeasured_number_is_not_allowed(self) -> None:
        assert 987_654.0 not in allowed_numbers(statistics(), plan())


class TestNumberExtraction:
    def test_thousands_separators_are_understood(self) -> None:
        assert numbers_in("shuffle read was 2,000,000 bytes") == [2_000_000.0]

    def test_small_numbers_are_ignored(self) -> None:
        """Prose is full of them; flagging them would bury the real fabrications."""
        assert numbers_in("there were 3 stages and 2 variants") == []

    def test_negatives_and_decimals(self) -> None:
        assert numbers_in("a -22.73% change") == [-22.73]


class TestVerification:
    def verify(self, *claims: Claim, with_documentation: bool = True):
        return verify_explanation(
            Explanation(answer="an answer", claims=list(claims), caveats=[]),
            statistics=statistics(),
            plan=plan(),
            documentation=evidence(with_documentation=with_documentation).documentation,
        )

    def test_a_well_supported_measured_claim_passes(self) -> None:
        result = self.verify(
            Claim(
                kind=ClaimKind.MEASURED,
                statement="193 of the 200 shuffle partitions received no records.",
                support="empty partitions",
            )
        )
        assert result.grounded is True
        assert result.checked_claims == 1

    def test_a_measured_claim_citing_an_unmeasured_metric_is_flagged(self) -> None:
        result = self.verify(
            Claim(
                kind=ClaimKind.MEASURED,
                statement="Garbage collection took most of the time.",
                support="gc time",
            )
        )
        assert result.grounded is False
        assert "not a metric this experiment measured" in result.unsupported[0].problem

    def test_a_fabricated_number_in_a_measured_claim_is_flagged(self) -> None:
        """The single most important check in the phase."""
        result = self.verify(
            Claim(
                kind=ClaimKind.MEASURED,
                statement="The job spilled 48,000,000 bytes to disk.",
                support="total runtime",
            )
        )
        assert result.grounded is False
        assert "no measurement produced" in result.unsupported[0].problem
        assert "48000000" in result.unsupported[0].problem.replace(".0", "")

    def test_a_rounded_number_is_accepted(self) -> None:
        """A model writing 1,100 for a median of 1100.0 has fabricated nothing."""
        result = self.verify(
            Claim(
                kind=ClaimKind.MEASURED,
                statement="The median runtime at 200 partitions was about 1,100 ms.",
                support="total runtime",
            )
        )
        assert result.grounded is True

    def test_milliseconds_restated_as_seconds_are_accepted(self) -> None:
        result = self.verify(
            Claim(
                kind=ClaimKind.MEASURED,
                statement="The median runtime was 1.1 seconds at the baseline.",
                support="total runtime",
            )
        )
        assert result.grounded is True

    def test_a_documented_claim_must_cite_a_retrieved_page(self) -> None:
        result = self.verify(
            Claim(
                kind=ClaimKind.DOCUMENTED,
                statement="The default is 200.",
                support=DOC_URL,
            )
        )
        assert result.grounded is True

    def test_a_documented_claim_citing_nothing_retrieved_is_flagged(self) -> None:
        result = self.verify(
            Claim(
                kind=ClaimKind.DOCUMENTED,
                statement="Spark recommends 128 MB per partition.",
                support="https://example.invalid/made-up",
            )
        )
        assert result.grounded is False
        assert "not one of the documentation pages" in result.unsupported[0].problem

    def test_a_documented_claim_when_nothing_was_retrieved_is_flagged(self) -> None:
        result = self.verify(
            Claim(kind=ClaimKind.DOCUMENTED, statement="x", support=DOC_URL),
            with_documentation=False,
        )
        assert result.grounded is False

    def test_an_inferred_claim_is_not_checked_numerically(self) -> None:
        """An inference legitimately restates measured figures.

        It is already labelled as the weaker kind of claim, which is what the
        reader needs to know.
        """
        result = self.verify(
            Claim(
                kind=ClaimKind.INFERRED,
                statement="200 is a poor fit for seven distinct keys.",
                support="follows from 'empty partitions'",
            )
        )
        assert result.grounded is True
        assert result.checked_claims == 0

    def test_claims_are_retrievable_by_kind(self) -> None:
        explanation = Explanation(
            answer="a",
            claims=[
                Claim(kind=ClaimKind.MEASURED, statement="m", support="partitions"),
                Claim(kind=ClaimKind.INFERRED, statement="i", support="m"),
                Claim(kind=ClaimKind.DOCUMENTED, statement="d", support=DOC_URL),
            ],
            caveats=["c"],
        )
        assert len(explanation.of_kind(ClaimKind.MEASURED)) == 1
        assert len(explanation.of_kind(ClaimKind.INFERRED)) == 1

    def test_several_problems_are_all_reported(self) -> None:
        result = self.verify(
            Claim(kind=ClaimKind.MEASURED, statement="x", support="invented"),
            Claim(kind=ClaimKind.DOCUMENTED, statement="y", support="https://nope.invalid"),
        )
        assert len(result.unsupported) == 2


class TestClaimSchema:
    def test_the_schema_demands_a_kind_and_support(self) -> None:
        """A model cannot emit an unlabelled claim; the field is required."""
        schema = Explanation.model_json_schema()
        claim = schema["$defs"]["Claim"]
        assert set(claim["required"]) == {"kind", "statement", "support"}

    def test_the_three_kinds_are_the_only_ones(self) -> None:
        assert {kind.value for kind in ClaimKind} == {"measured", "documented", "inferred"}

    def test_determinism_reaches_the_model_with_each_comparison(self) -> None:
        payload = build_explanation_input(plan=plan(), statistics=statistics(), evidence=evidence())
        rendered = payload.to_prompt()
        assert Determinism.TIMING.value in rendered
        assert Determinism.STRUCTURAL.value in rendered

    def test_the_raw_samples_are_not_given_to_the_model(self) -> None:
        """They invite arithmetic, and on a real run they made the prompt unanswerable.

        A six-variant explanation payload carrying every sample came to 62 KB, and
        Ultra exhausted its output budget on reasoning before producing any JSON.
        The grounding check still runs against the full statistics, so the model
        may claim no more than before -- it simply sees less.
        """
        payload = build_explanation_input(plan=plan(), statistics=statistics(), evidence=evidence())
        assert "samples" not in payload.to_prompt()
        for metric in payload.statistics.metrics:
            for point in metric.points:
                # Spread and stddev travel; the samples they came from do not.
                assert hasattr(point, "spread")
                assert not hasattr(point, "samples")
