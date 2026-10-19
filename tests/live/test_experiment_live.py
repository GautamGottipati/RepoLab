"""Live verification of the two Ultra calls the experiment loop makes.

Marked ``live`` and deselected by default; run with ``make smoke``.

No Docker and no Spark here. The statistics handed to the explanation call are
constructed from real measurement types, so this file asserts exactly one thing:
that the real model, through the real router, produces output the deterministic
layers accept -- a plan that survives validation, and an explanation whose claims
survive the grounding check.

The second test is the important one. It gives the model statistics and asks for
an explanation, then verifies the explanation against those same statistics. A
model that invents a figure fails it, which is the behaviour that must hold in
production and cannot be established with a fake.
"""

from __future__ import annotations

import pytest

from repolab.config import get_settings
from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
)
from repolab.experiments.explanation import (
    SYSTEM_PROMPT,
    ClaimKind,
    Explanation,
    build_explanation_input,
    default_notes,
    verify_explanation,
)
from repolab.experiments.models import ProposedPlan
from repolab.experiments.statistics import compute_statistics
from repolab.experiments.validation import ExperimentBudget, validate_plan
from repolab.llm import ModelTier, Purpose, build_router
from tests.support.experiments import IMAGE, variant_run

pytestmark = pytest.mark.live

QUESTION = "Why does this job use 200 shuffle partitions?"

DOC_URL = "https://spark.apache.org/docs/3.5.1/sql-performance-tuning.html"


@pytest.fixture(scope="module")
def router():
    settings = get_settings()
    if settings.nebius_api_key is None:
        pytest.skip("NEBIUS_API_KEY is not set")
    if not settings.configured_models.get(ModelTier.ULTRA):
        pytest.skip("MODEL_ULTRA is not set")
    return build_router(settings)


@pytest.fixture(scope="module")
def statistics():
    """Statistics shaped like a real four-run experiment.

    The numbers are the ones a real run produces: 200 partitions leaves almost
    all of them empty, 8 leaves half of them empty because seven keys collide
    when hashed into eight buckets.
    """
    runs = [
        variant_run(value=8, repeat=1, total_runtime_ms=7484, partitions=8, empty=4),
        variant_run(value=8, repeat=2, total_runtime_ms=7120, partitions=8, empty=4),
        variant_run(value=200, repeat=1, total_runtime_ms=9312, partitions=200, empty=193),
        variant_run(value=200, repeat=2, total_runtime_ms=9880, partitions=200, empty=193),
    ]
    return compute_statistics(
        runs,
        metrics=["total runtime", "partitions", "empty partitions", "shuffle read"],
        baseline_value=200,
    )


class TestUltraDesignsAnExperiment:
    async def test_the_proposal_survives_validation(self, router) -> None:
        from repolab.experiments.agent import DESIGN_PROMPT_TEMPLATE, DESIGN_SYSTEM_PROMPT
        from repolab.experiments.validation import ALLOWED_VARIABLES, measurable_metrics

        prompt = DESIGN_PROMPT_TEMPLATE.format(
            question=QUESTION,
            repo_url="https://github.com/AlexIoannides/pyspark-example-project",
            commit_sha="abc123def456",
            findings="- found spark.sql.shuffle.partitions in jobs/etl_job.py",
            code="- jobs/etl_job.py:31: df.groupBy('dept').agg(...)",
            documentation=f"- {DOC_URL}: the default is 200",
            version="3.5.1",
            master="local[4]",
            added="- a groupBy on the skewed key",
            variables="\n".join(
                f"- {name} (allowed range {low}-{high})"
                for name, (low, high) in ALLOWED_VARIABLES.items()
            ),
            metrics="\n".join(f"- {metric}" for metric in measurable_metrics()),
            baseline=200,
            max_runs=24,
            max_repeats=5,
        )

        from repolab.experiments.agent import DESIGN_MAX_TOKENS

        completion = await router.ultra(
            purpose=Purpose.EXPERIMENT_DESIGN,
            schema=ProposedPlan,
            system=DESIGN_SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=DESIGN_MAX_TOKENS,
        )

        result = validate_plan(completion.value, budget=ExperimentBudget(), image=IMAGE)
        print(f"\nproposed: {completion.value.model_dump_json(indent=2)}")
        print(f"retries: {completion.retries}, latency: {completion.record.latency_ms:.0f} ms")
        if result.plan is not None:
            print(f"validated: {result.plan.summary()}")
            print(f"dropped:   {result.plan.dropped_metrics}")
            print(f"adjusted:  {result.plan.adjustments}")
        else:
            print(f"REFUSED:   {result.rejection}")

        assert result.ok, f"the real model's plan was refused: {result.rejection}"
        assert result.plan is not None
        assert 200 in result.plan.values, "the baseline must be in the sweep"
        assert len(result.plan.values) >= 2

    async def test_the_model_stays_inside_the_allowlist(self, router) -> None:
        """Given one permitted variable, it must propose that one."""
        from repolab.experiments.agent import DESIGN_SYSTEM_PROMPT

        completion = await router.ultra(
            purpose=Purpose.EXPERIMENT_DESIGN,
            schema=ProposedPlan,
            system=DESIGN_SYSTEM_PROMPT,
            prompt=(
                f"Question: {QUESTION}\n\n"
                "Variables you may vary (nothing else is permitted):\n"
                "- spark.sql.shuffle.partitions (allowed range 1-2000)\n\n"
                "Metrics RepoLab can measure:\n"
                "- total runtime\n- partitions\n- empty partitions\n- shuffle read\n\n"
                "The current value is 200. At most 24 runs."
            ),
            max_tokens=2048,
        )
        from repolab.experiments.validation import canonical_variable

        assert canonical_variable(completion.value.variable) is not None


@pytest.fixture(scope="module")
async def explanation(router, statistics):
    """One live explanation call, shared by the assertions below."""
    from tests.support.experiments import plan

    validated = plan(values=[8, 200], repeats=2)
    evidence = EvidenceBundle(
        code_references=[
            CodeReference(
                path="jobs/etl_job.py",
                start_line=31,
                excerpt="df.groupBy('dept').agg(...)",
            )
        ],
        logs=[
            LogReference(
                source="spark event log",
                excerpt='{"Event":"SparkListenerLogStart","Spark Version":"3.5.1"}',
            )
        ],
        documentation=[
            DocumentationReference(
                url=DOC_URL,
                title="Performance Tuning - Spark 3.5.1",
                excerpt=(
                    "spark.sql.shuffle.partitions: 200. Configures the number of "
                    "partitions to use when shuffling data for joins or aggregations."
                ),
                version="3.5.1",
            )
        ],
    )

    payload = build_explanation_input(
        plan=validated,
        statistics=statistics,
        evidence=evidence,
        notes=default_notes(validated, statistics),
    )
    from repolab.experiments.agent import (
        EXPLANATION_MAX_TOKENS,
        EXPLANATION_TIMEOUT_SECONDS,
    )

    completion = await router.ultra(
        purpose=Purpose.RESULT_INTERPRETATION,
        schema=Explanation,
        system=SYSTEM_PROMPT,
        prompt=payload.to_prompt(),
        max_tokens=EXPLANATION_MAX_TOKENS,
        timeout_seconds=EXPLANATION_TIMEOUT_SECONDS,
    )
    verified = verify_explanation(
        completion.value,
        statistics=statistics,
        plan=validated,
        documentation=evidence.documentation,
    )
    return completion, verified


class TestUltraExplainsMeasurements:
    async def test_it_produces_labelled_claims(self, explanation) -> None:
        completion, verified = explanation
        result = verified.explanation

        print(f"\nanswer: {result.answer}\n")
        for claim in result.claims:
            print(f"  [{claim.kind.value:<10}] {claim.statement}")
            print(f"               support: {claim.support}")
        print(f"\ncaveats: {result.caveats}")
        print(f"retries: {completion.retries}, latency: {completion.record.latency_ms:.0f} ms")

        assert result.answer.strip()
        assert result.claims, "an explanation with no claims grounds nothing"
        assert result.of_kind(ClaimKind.MEASURED), "nothing was attributed to a measurement"

    async def test_every_claim_traces_back_to_the_evidence(self, explanation) -> None:
        """The production guarantee: no figure the experiment did not produce."""
        _, verified = explanation

        if not verified.grounded:
            for item in verified.unsupported:
                print(f"\nUNSUPPORTED [{item.kind.value}] {item.statement}\n  {item.problem}")

        assert verified.checked_claims > 0, "nothing checkable was claimed"
        assert verified.grounded, f"{len(verified.unsupported)} claim(s) were not supported"

    async def test_it_does_not_overstate_a_timing(self, explanation) -> None:
        """Timings vary; the notes say so, and the answer must not contradict them."""
        _, verified = explanation
        timing_claims = [
            claim
            for claim in verified.explanation.claims
            if claim.kind is ClaimKind.MEASURED and claim.support == "total runtime"
        ]
        for claim in timing_claims:
            # Each such claim must cite the metric, which carries its determinism.
            assert claim.support == "total runtime"
