"""The gate deciding whether a model may be consulted.

The rule that matters most is negative: RepoLab must never ask a model to
classify something the repository already states plainly.
"""

from __future__ import annotations

import pytest

from repolab.classify import (
    classify,
    classify_deterministic,
    explain_gate,
    has_strong_evidence,
    should_consult_model,
    tie_gap,
)
from repolab.classify.signals import STRONG_CONFIDENCE, TIE_MARGIN
from repolab.evidence.models import CodeReference
from repolab.ingest.detectors.base import DetectorRun
from repolab.llm import ModelRouter, ModelTier
from repolab.manifest.models import (
    Classification,
    ClassificationSource,
    InteractionSurface,
    SurfaceType,
)
from repolab.testing.fakes import FakeLLMProvider, FakeMessage

JUDGEMENT = '{"primary_surface": "cli", "reason": "the shell is the way in"}'


def run_with(*signals: tuple[str, str]) -> DetectorRun:
    run = DetectorRun()
    for index, (kind, path) in enumerate(signals, start=1):
        run.add(kind, kind, references=[CodeReference(path=path, start_line=index)])
    return run


def classification_of(*confidences: tuple[SurfaceType, float]) -> Classification:
    surfaces = [
        InteractionSurface(
            type=surface_type,
            name=surface_type.value,
            confidence=confidence,
            reason="constructed for this test",
            evidence=[CodeReference(path=f"{surface_type.value}.py")],
        )
        for surface_type, confidence in confidences
    ]
    surfaces.sort(key=lambda s: s.confidence, reverse=True)
    if surfaces:
        surfaces[0].primary = True
    return Classification(
        surfaces=surfaces,
        primary_type=surfaces[0].type if surfaces else None,
    )


def router_with(*payloads: str) -> tuple[ModelRouter, FakeLLMProvider]:
    provider = FakeLLMProvider(messages=[FakeMessage(content=p) for p in payloads])
    return ModelRouter(provider, {ModelTier.SUPER: "fake/super"}), provider


class TestStrongEvidenceIsNeverAmbiguous:
    def test_a_single_strong_surface_closes_the_gate(self) -> None:
        result = classification_of((SurfaceType.DATA_PLAYGROUND, 0.9))
        assert has_strong_evidence(result.surfaces) is True
        assert should_consult_model(result) is False

    def test_two_strong_surfaces_still_close_the_gate(self) -> None:
        """Two strong signals is a hybrid, not a question for a model."""
        result = classification_of((SurfaceType.WEB, 0.9), (SurfaceType.CLI, 0.88))
        assert should_consult_model(result) is False

    def test_the_threshold_is_the_documented_one(self) -> None:
        at_threshold = classification_of(
            (SurfaceType.CLI, STRONG_CONFIDENCE), (SurfaceType.API, STRONG_CONFIDENCE - 0.02)
        )
        below_threshold = classification_of(
            (SurfaceType.CLI, STRONG_CONFIDENCE - 0.01),
            (SurfaceType.API, STRONG_CONFIDENCE - 0.03),
        )
        assert should_consult_model(at_threshold) is False
        assert should_consult_model(below_threshold) is True

    def test_the_gate_explains_itself(self) -> None:
        result = classification_of((SurfaceType.DATA_PLAYGROUND, 0.9))
        assert "strongly supported" in explain_gate(result)


class TestWeakEvidenceOpensTheGate:
    def test_two_close_weak_candidates_are_ambiguous(self) -> None:
        result = classification_of((SurfaceType.CLI, 0.35), (SurfaceType.API, 0.30))
        assert should_consult_model(result) is True
        assert tie_gap(result.surfaces) == pytest.approx(0.05)

    def test_a_clear_weak_leader_is_not_ambiguous(self) -> None:
        result = classification_of((SurfaceType.CLI, 0.60), (SurfaceType.API, 0.20))
        assert should_consult_model(result) is False

    def test_the_tie_margin_is_the_documented_one(self) -> None:
        just_inside = classification_of(
            (SurfaceType.CLI, 0.50), (SurfaceType.API, 0.50 - TIE_MARGIN)
        )
        just_outside = classification_of(
            (SurfaceType.CLI, 0.50), (SurfaceType.API, 0.50 - TIE_MARGIN - 0.01)
        )
        assert should_consult_model(just_inside) is True
        assert should_consult_model(just_outside) is False

    def test_only_the_terminal_fallback_is_reported_inconclusive(self) -> None:
        """Nothing was recognised, and a model has no candidates to pick from."""
        result = classify_deterministic(DetectorRun())
        assert result.ambiguous is True
        assert should_consult_model(result) is False
        assert "inconclusive" in explain_gate(result)

    def test_a_lone_weak_surface_is_reported_uncertain_but_not_sent_to_a_model(
        self,
    ) -> None:
        """A model may only choose between candidates; one candidate is not a choice."""
        run = run_with(("sys_argv", "a.py"))
        assert classify_deterministic(run).ambiguous is True
        assert should_consult_model(classification_of((SurfaceType.CLI, 0.10))) is False
        assert "nothing for a model to choose between" in explain_gate(
            classification_of((SurfaceType.CLI, 0.10))
        )

    def test_no_surfaces_at_all_is_not_sent_to_a_model(self) -> None:
        assert should_consult_model(Classification()) is False


class TestTheGateGovernsRealCalls:
    async def test_a_strong_signal_never_reaches_the_model(self) -> None:
        """The decisive assertion: no model call for an obvious repository."""
        router, provider = router_with(JUDGEMENT)
        result = await classify(
            run_with(
                ("spark_session", "jobs/etl.py"),
                ("spark_import", "jobs/etl.py"),
                ("spark_submit", "run.sh"),
            ),
            router=router,
        )

        assert provider.call_count == 0
        assert result.resolved_by is ClassificationSource.DETERMINISTIC
        assert result.primary_type is SurfaceType.DATA_PLAYGROUND

    async def test_an_ambiguous_repository_does_reach_the_model(self) -> None:
        router, provider = router_with(JUDGEMENT)
        result = await classify(
            run_with(("sys_argv", "cli.py"), ("spark_config", "conf.json")), router=router
        )

        assert provider.call_count == 1
        assert result.resolved_by is ClassificationSource.SUPER

    async def test_without_a_router_the_deterministic_answer_stands(self) -> None:
        result = await classify(run_with(("sys_argv", "cli.py"), ("spark_config", "conf.json")))

        assert result.resolved_by is ClassificationSource.DETERMINISTIC
        assert any("no model router configured" in note for note in result.notes)

    async def test_the_gate_decision_is_always_recorded(self) -> None:
        """Whatever the gate decides, the reason is auditable."""
        strong = await classify(
            run_with(
                ("spark_session", "jobs/etl.py"),
                ("spark_import", "jobs/etl.py"),
                ("spark_submit", "run.sh"),
            )
        )
        assert any("strongly supported" in note for note in strong.notes)

        weak = await classify(run_with(("sys_argv", "cli.py")))
        assert weak.notes
        assert weak.resolved_by is ClassificationSource.DETERMINISTIC
