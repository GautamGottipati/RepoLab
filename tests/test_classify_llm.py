"""Super tie-break.

The model's authority is deliberately narrow: it may only choose among surfaces
that were already detected from repository evidence. Anything else it says is
discarded, so classification stays grounded even when a model is involved.
"""

from __future__ import annotations

import json

from repolab.classify import classify
from repolab.classify.llm import break_tie
from repolab.evidence.models import CodeReference
from repolab.ingest.detectors.base import DetectorRun
from repolab.llm import ModelRouter, ModelTier
from repolab.manifest.models import ClassificationSource, SurfaceType
from repolab.testing.fakes import FakeLLMProvider, FakeMessage

#: Two weak, evenly-matched signals: exactly the case the gate opens for.
AMBIGUOUS_SIGNALS = (("sys_argv", "cli.py"), ("spark_config", "conf.json"))


def run_with(*signals: tuple[str, str]) -> DetectorRun:
    run = DetectorRun()
    for index, (kind, path) in enumerate(signals, start=1):
        run.add(kind, kind, references=[CodeReference(path=path, start_line=index)])
    return run


def judgement(surface: str, reason: str = "the evidence points here") -> str:
    return json.dumps({"primary_surface": surface, "reason": reason})


def router_with(*payloads: str, **kwargs) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(
        messages=[FakeMessage(content=p) for p in payloads] or None, **kwargs
    )
    return ModelRouter(provider, {ModelTier.SUPER: "fake/super"}), provider


class TestAValidJudgementIsApplied:
    async def test_it_becomes_the_primary(self) -> None:
        router, _ = router_with(judgement("data_playground"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        assert result.primary_type is SurfaceType.DATA_PLAYGROUND
        assert result.resolved_by is ClassificationSource.SUPER

    async def test_exactly_one_surface_stays_primary(self) -> None:
        router, _ = router_with(judgement("cli"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)
        assert sum(1 for s in result.surfaces if s.primary) == 1

    async def test_the_chosen_surface_is_listed_first(self) -> None:
        router, _ = router_with(judgement("cli"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)
        assert result.surfaces[0].type is SurfaceType.CLI

    async def test_the_model_reason_is_recorded_in_the_rationale(self) -> None:
        router, _ = router_with(judgement("cli", "the shell is the natural way in"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        assert "tie-break" in result.rationale
        assert "the shell is the natural way in" in result.rationale

    async def test_the_candidates_and_their_evidence_are_shown_to_the_model(self) -> None:
        router, provider = router_with(judgement("cli"))
        await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        prompt = " ".join(m["content"] for m in provider.calls[0]["messages"])
        assert "cli.py" in prompt
        assert "conf.json" in prompt
        assert "Do not invent a surface" in prompt

    async def test_surfaces_keep_their_evidence_after_a_tie_break(self) -> None:
        router, _ = router_with(judgement("cli"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)
        for surface in result.surfaces:
            assert surface.evidence


class TestAnUngroundedJudgementIsDiscarded:
    async def test_a_surface_that_was_never_detected_is_rejected(self) -> None:
        """The model may choose among candidates, not introduce one."""
        router, _ = router_with(judgement("web"))
        deterministic = await classify(run_with(*AMBIGUOUS_SIGNALS))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        assert result.primary_type is deterministic.primary_type
        assert result.resolved_by is ClassificationSource.DETERMINISTIC
        assert any("not a candidate" in note for note in result.notes)

    async def test_a_nonsense_surface_name_is_rejected(self) -> None:
        router, _ = router_with(judgement("telepathy"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        assert result.resolved_by is ClassificationSource.DETERMINISTIC
        assert any("not a candidate" in note for note in result.notes)

    async def test_no_surface_is_invented(self) -> None:
        router, _ = router_with(judgement("web"))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)
        assert SurfaceType.WEB not in {s.type for s in result.surfaces}


class TestFailuresDegrade:
    async def test_a_model_failure_leaves_the_deterministic_answer(self) -> None:
        router, _ = router_with(fail_with=RuntimeError("503 unavailable"))
        deterministic = await classify(run_with(*AMBIGUOUS_SIGNALS))
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        assert result.primary_type is deterministic.primary_type
        assert result.resolved_by is ClassificationSource.DETERMINISTIC
        assert any("tie-break failed" in note for note in result.notes)

    async def test_unparseable_output_leaves_the_deterministic_answer(self) -> None:
        router, _ = router_with("this is not json")
        result = await classify(run_with(*AMBIGUOUS_SIGNALS), router=router)

        assert result.resolved_by is ClassificationSource.DETERMINISTIC
        assert result.surfaces

    async def test_a_tie_break_with_one_candidate_does_nothing(self) -> None:
        """Called directly: a single candidate offers no choice."""
        router, provider = router_with(judgement("cli"))
        single = await classify(run_with(("sys_argv", "cli.py")))

        result, note = await break_tie(router, single)
        assert result is single
        assert note is not None and "fewer than two" in note
        assert provider.call_count == 0

    async def test_the_input_classification_is_not_mutated_on_failure(self) -> None:
        router, _ = router_with(judgement("web"))
        before = await classify(run_with(*AMBIGUOUS_SIGNALS))
        snapshot = before.model_dump_json()

        await break_tie(router, before)
        assert before.model_dump_json() == snapshot
