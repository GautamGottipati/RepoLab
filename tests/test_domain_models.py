"""Domain model invariants.

These models are the contract between the investigator, the build pipeline and
the frontend, so their invariants are pinned here rather than left to callers.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repolab.evidence.models import (
    CodeReference,
    DocumentationReference,
    EvidenceBundle,
    LogReference,
    Measurement,
    MeasurementSource,
)
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Concept,
    Dependency,
    EntryPoint,
    EntryPointKind,
    Fidelity,
    InteractionSurface,
    RepoManifest,
    Service,
    SmokeTest,
    SurfaceType,
)


def surface(
    surface_type: SurfaceType,
    name: str,
    confidence: float,
    *,
    primary: bool = False,
) -> InteractionSurface:
    return InteractionSurface(type=surface_type, name=name, confidence=confidence, primary=primary)


def manifest(**overrides: object) -> RepoManifest:
    defaults: dict[str, object] = {
        "repo_url": "https://github.com/apache/spark",
        "commit_sha": "abc123",
        "build_strategy": BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        "confidence": 0.8,
    }
    return RepoManifest(**{**defaults, **overrides})


class TestMultipleInteractionSurfaces:
    """A repository must never be forced into a single application type."""

    def test_a_hybrid_repository_keeps_every_surface(self) -> None:
        repo = manifest(
            interaction_surfaces=[
                surface(SurfaceType.WEB, "React frontend", 0.9),
                surface(SurfaceType.API, "FastAPI backend", 0.85),
                surface(SurfaceType.CLI, "Python CLI", 0.7),
                surface(SurfaceType.DATA_PLAYGROUND, "Spark job", 0.95),
            ]
        )
        assert len(repo.interaction_surfaces) == 4
        assert repo.surface_types == [
            SurfaceType.WEB,
            SurfaceType.API,
            SurfaceType.CLI,
            SurfaceType.DATA_PLAYGROUND,
        ]

    def test_surface_types_deduplicates_but_keeps_order(self) -> None:
        repo = manifest(
            interaction_surfaces=[
                surface(SurfaceType.CLI, "cqlsh", 0.9),
                surface(SurfaceType.CLI, "nodetool", 0.6),
                surface(SurfaceType.TERMINAL, "shell", 0.5),
            ]
        )
        assert repo.surface_types == [SurfaceType.CLI, SurfaceType.TERMINAL]


class TestPrimarySurface:
    def test_explicit_primary_wins_over_confidence(self) -> None:
        repo = manifest(
            interaction_surfaces=[
                surface(SurfaceType.WEB, "web", 0.99),
                surface(SurfaceType.CLI, "cli", 0.2, primary=True),
            ]
        )
        assert repo.primary_surface is not None
        assert repo.primary_surface.name == "cli"

    def test_falls_back_to_highest_confidence(self) -> None:
        repo = manifest(
            interaction_surfaces=[
                surface(SurfaceType.CLI, "cli", 0.4),
                surface(SurfaceType.DATA_PLAYGROUND, "spark", 0.9),
            ]
        )
        assert repo.primary_surface is not None
        assert repo.primary_surface.name == "spark"

    def test_none_when_there_are_no_surfaces(self) -> None:
        assert manifest().primary_surface is None

    def test_two_primaries_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at most one interaction surface"):
            manifest(
                interaction_surfaces=[
                    surface(SurfaceType.WEB, "web", 0.5, primary=True),
                    surface(SurfaceType.CLI, "cli", 0.5, primary=True),
                ]
            )


class TestConfidenceBounds:
    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_out_of_range_manifest_confidence_rejected(self, value: float) -> None:
        with pytest.raises(ValidationError):
            manifest(confidence=value)

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_in_range_accepted(self, value: float) -> None:
        assert surface(SurfaceType.CLI, "cli", value).confidence == value


class TestBuildStrategy:
    @pytest.mark.parametrize(
        ("fidelity", "expected"),
        [
            (Fidelity.EXACT, True),
            (Fidelity.HIGH, True),
            (Fidelity.PARTIAL, True),
            (Fidelity.CONCEPTUAL, False),
        ],
    )
    def test_only_conceptual_fidelity_forbids_experiments(
        self, fidelity: Fidelity, expected: bool
    ) -> None:
        strategy = BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=fidelity)
        assert strategy.supports_real_experiments is expected

    def test_there_is_no_cached_build_kind(self) -> None:
        """Cached builds are cached instances of a real version, not a kind."""
        assert "cached" not in {kind.value for kind in BuildKind}


class TestEvidenceBundle:
    def test_defaults_to_empty_and_unsimulated(self) -> None:
        bundle = EvidenceBundle()
        assert bundle.is_empty is True
        assert bundle.simulated is False

    def test_carries_all_four_evidence_kinds(self) -> None:
        bundle = EvidenceBundle(
            code_references=[CodeReference(path="src/main.py", start_line=10)],
            measurements=[
                Measurement(
                    name="runtime",
                    value=1.5,
                    unit="s",
                    source=MeasurementSource.PROCESS_TIMING,
                )
            ],
            logs=[LogReference(source="spark-submit", excerpt="INFO done")],
            documentation=[
                DocumentationReference(url="https://spark.apache.org/docs/latest/", version="3.5.0")
            ],
        )
        assert bundle.is_empty is False
        assert bundle.measurements[0].source is MeasurementSource.PROCESS_TIMING

    def test_simulated_evidence_cannot_carry_measurements(self) -> None:
        """Mini Lab must never produce measured results."""
        with pytest.raises(ValidationError, match="never produce measured results"):
            EvidenceBundle(
                simulated=True,
                measurements=[
                    Measurement(
                        name="runtime",
                        value=1.0,
                        unit="s",
                        source=MeasurementSource.PROCESS_TIMING,
                    )
                ],
            )

    def test_simulated_evidence_may_carry_code_references(self) -> None:
        """Mini Lab should still link simulated behaviour to real source code."""
        bundle = EvidenceBundle(simulated=True, code_references=[CodeReference(path="src/app.py")])
        assert bundle.simulated is True
        assert bundle.is_empty is False


class TestSupportingModels:
    def test_entrypoint(self) -> None:
        entry = EntryPoint(
            name="spark-job",
            kind=EntryPointKind.SPARK_JOB,
            command="spark-submit job.py",
            path="jobs/job.py",
        )
        assert entry.kind is EntryPointKind.SPARK_JOB

    def test_service_defaults_to_required_with_no_ports(self) -> None:
        service = Service(name="cassandra")
        assert service.required is True
        assert service.ports == []

    def test_dependency(self) -> None:
        assert Dependency(name="pyspark", ecosystem="pypi", version_spec=">=3.5").name == "pyspark"

    def test_smoke_test_defaults(self) -> None:
        test = SmokeTest(name="spark runs", command="spark-submit tiny.py")
        assert test.expect_exit_code == 0
        assert test.timeout_seconds == 120

    def test_smoke_test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SmokeTest(name="x", command="y", timeout_seconds=0)

    def test_concept_carries_grounding_references(self) -> None:
        concept = Concept(
            name="Shuffle partitions",
            summary="Controls post-shuffle parallelism.",
            code_references=[CodeReference(path="jobs/agg.py", start_line=12, end_line=18)],
        )
        assert concept.code_references[0].end_line == 18


class TestManifestSerialization:
    def test_round_trips_through_json(self) -> None:
        """The manifest crosses process boundaries (worker -> API -> web)."""
        original = manifest(
            languages=["Python", "Scala"],
            frameworks=["Spark"],
            entrypoints=[
                EntryPoint(name="job", kind=EntryPointKind.SPARK_JOB, command="spark-submit job.py")
            ],
            interaction_surfaces=[surface(SurfaceType.DATA_PLAYGROUND, "spark", 0.9)],
            smoke_tests=[SmokeTest(name="tiny", command="spark-submit tiny.py")],
        )
        restored = RepoManifest.model_validate_json(original.model_dump_json())
        assert restored == original
        assert restored.primary_surface is not None
        assert restored.primary_surface.type is SurfaceType.DATA_PLAYGROUND
