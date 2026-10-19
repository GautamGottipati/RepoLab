"""Which experiences a repository offers, and the honesty of what it claims.

The specification is unusually direct about this one:

> Each surface must include evidence explaining why it was detected.
> Unsupported experiences should clearly say: Detected, but not yet runnable in
> this RepoLab build.
> **Never pretend an unsupported experience works.**

So most of this file is about the second and third sentences. The fixtures are
recorded from real ingestions (`tests/support/manifests.py`), including the
awkward case the demonstration actually produces: Airflow's *primary* surface is
one RepoLab cannot run.
"""

from __future__ import annotations

import pytest

from repolab.classify.signals import HYBRID_CONFIDENCE
from repolab.experiences.catalog import (
    ADAPTERS,
    NO_ADAPTER,
    NO_LADDER,
    NO_LESSON,
    NOT_PRIMARY,
    NOT_RUNNABLE,
    SURFACE_ICONS,
    SURFACE_LABELS,
    ExperienceStatus,
    experiences_for,
)
from repolab.manifest.models import RepoManifest, SurfaceType
from tests.support.builds import cassandra_manifest
from tests.support.manifests import airflow_manifest, spark_manifest


@pytest.fixture
def airflow() -> RepoManifest:
    return airflow_manifest()


class TestHybridDetection:
    """The phase's subject: several surfaces in one repository."""

    def test_all_four_surfaces_are_offered(self, airflow: RepoManifest) -> None:
        catalog = experiences_for(airflow)

        assert [item.surface for item in catalog.experiences] == [
            SurfaceType.CLI,
            SurfaceType.WEB,
            SurfaceType.API,
            SurfaceType.NOTEBOOK,
        ]
        assert catalog.surface_count == 4

    def test_the_repository_is_marked_hybrid(self, airflow: RepoManifest) -> None:
        assert experiences_for(airflow).hybrid is True

    def test_a_single_surface_repository_is_not(self) -> None:
        assert experiences_for(spark_manifest()).hybrid is False

    def test_each_surface_carries_the_specifications_vocabulary(
        self, airflow: RepoManifest
    ) -> None:
        for item in experiences_for(airflow).experiences:
            assert item.icon == SURFACE_ICONS[item.surface]
            assert item.label == SURFACE_LABELS[item.surface]

    def test_the_icons_are_the_ones_claude_md_names(self) -> None:
        assert SURFACE_ICONS[SurfaceType.WEB] == "🌐"
        assert SURFACE_ICONS[SurfaceType.CLI] == ">_"
        assert SURFACE_ICONS[SurfaceType.API] == "🔌"
        assert SURFACE_ICONS[SurfaceType.NOTEBOOK] == "📓"

    def test_the_primary_and_its_rationale_are_carried_through(self, airflow: RepoManifest) -> None:
        catalog = experiences_for(airflow)

        assert catalog.primary_type is SurfaceType.CLI
        assert "scored highest" in catalog.rationale


class TestEverySurfaceExplainsItself:
    def test_each_one_says_why_it_was_detected(self, airflow: RepoManifest) -> None:
        for item in experiences_for(airflow).experiences:
            assert item.reason, f"{item.label} claims no reason"
            assert "detected from" in item.reason

    def test_each_one_cites_real_files(self, airflow: RepoManifest) -> None:
        for item in experiences_for(airflow).experiences:
            assert item.evidence, f"{item.label} cites nothing"

    def test_evidence_is_passed_through_unedited(self, airflow: RepoManifest) -> None:
        catalog = experiences_for(airflow)
        web = catalog.of(SurfaceType.WEB)
        source = next(s for s in airflow.interaction_surfaces if s.type is SurfaceType.WEB)

        assert web is not None
        assert web.evidence == source.evidence

    def test_a_thin_surface_is_flagged_rather_than_hidden(self, airflow: RepoManifest) -> None:
        """Airflow's notebook signal is a few .ipynb files and some packaging."""
        catalog = experiences_for(airflow)
        notebook = catalog.of(SurfaceType.NOTEBOOK)
        cli = catalog.of(SurfaceType.CLI)

        assert notebook is not None and notebook.weak is True
        assert notebook.confidence < HYBRID_CONFIDENCE
        assert cli is not None and cli.weak is False


class TestNothingPretendsToWork:
    def test_an_unsupported_surface_says_the_exact_sentence(self, airflow: RepoManifest) -> None:
        web = experiences_for(airflow).of(SurfaceType.WEB)

        assert web is not None
        assert web.status is ExperienceStatus.DETECTED_ONLY
        assert web.unavailable_reason.startswith(NOT_RUNNABLE)

    @pytest.mark.parametrize("surface", [SurfaceType.WEB, SurfaceType.API, SurfaceType.NOTEBOOK])
    def test_surfaces_with_no_adapter_say_so(
        self, airflow: RepoManifest, surface: SurfaceType
    ) -> None:
        item = experiences_for(airflow).of(surface)

        assert item is not None
        assert item.supported is False
        assert item.adapter is None
        assert NO_ADAPTER in item.unavailable_reason

    def test_the_primary_surface_is_not_runnable_just_for_being_primary(
        self, airflow: RepoManifest
    ) -> None:
        """Airflow's CLI is its strongest surface and RepoLab still cannot build it."""
        cli = experiences_for(airflow).of(SurfaceType.CLI)

        assert cli is not None
        assert cli.primary is True
        assert cli.supported is True, "the adapter exists"
        assert cli.status is ExperienceStatus.DETECTED_ONLY
        assert NO_LADDER in cli.unavailable_reason

    def test_nothing_unsupported_offers_a_route(self, airflow: RepoManifest) -> None:
        """A route is the thing a screen would link to. There must not be one."""
        for item in experiences_for(airflow).experiences:
            assert item.route is None
            assert item.runnable is False

    def test_no_experience_is_runnable_without_an_adapter(self, airflow: RepoManifest) -> None:
        for manifest in (airflow, spark_manifest(), cassandra_manifest()):
            for item in experiences_for(manifest).experiences:
                if item.runnable:
                    assert item.adapter in ADAPTERS.values()
                    assert item.route is not None

    def test_a_runnable_experience_claims_no_unavailable_reason(self) -> None:
        playground = experiences_for(spark_manifest()).of(SurfaceType.DATA_PLAYGROUND)

        assert playground is not None
        assert playground.status is ExperienceStatus.RUNNABLE
        assert playground.unavailable_reason == ""


class TestWhatThisBuildCanRun:
    def test_spark_offers_its_playground(self) -> None:
        catalog = experiences_for(spark_manifest())
        playground = catalog.of(SurfaceType.DATA_PLAYGROUND)

        assert playground is not None
        assert playground.route == "/playground"
        assert playground.adapter == "SparkDataExperience"
        assert catalog.runnable_surfaces == [SurfaceType.DATA_PLAYGROUND]

    def test_cassandra_offers_its_terminal_and_lesson(self) -> None:
        catalog = experiences_for(cassandra_manifest())
        cli = catalog.of(SurfaceType.CLI)

        assert cli is not None
        assert cli.route == "/learn"
        assert cli.adapter == "CLIExperience"

    def test_a_secondary_surface_with_an_adapter_is_still_refused(self) -> None:
        """Spark's weak CLI would be built as a Spark job, so it is not a terminal."""
        cli = experiences_for(spark_manifest()).of(SurfaceType.CLI)

        assert cli is not None
        assert cli.status is ExperienceStatus.DETECTED_ONLY
        assert NOT_PRIMARY in cli.unavailable_reason

    def test_a_cli_repository_without_the_lesson_says_which_is_missing(self) -> None:
        """Cassandra's ladder with nothing citing cqlsh: the build exists, the lesson does not."""
        manifest = cassandra_manifest()
        stripped = manifest.model_copy(
            update={
                "interaction_surfaces": [
                    surface.model_copy(update={"evidence": []})
                    for surface in manifest.interaction_surfaces
                ]
            }
        )

        cli = experiences_for(stripped).of(SurfaceType.CLI)

        assert cli is not None
        assert cli.status is ExperienceStatus.DETECTED_ONLY
        assert NO_LESSON in cli.unavailable_reason

    def test_runnable_experiences_are_listed_first(self) -> None:
        """A learner should reach what they can do without reading past what they cannot."""
        order = [item.runnable for item in experiences_for(spark_manifest()).experiences]

        assert order == sorted(order, reverse=True)

    def test_counts_describe_the_catalog(self) -> None:
        catalog = experiences_for(spark_manifest())

        assert catalog.surface_count == 2
        assert catalog.runnable_count == 1


class TestAnEmptyManifest:
    def test_a_repository_with_no_surfaces_offers_nothing(self) -> None:
        manifest = spark_manifest().model_copy(
            update={"interaction_surfaces": [], "classification": None}
        )
        catalog = experiences_for(manifest)

        assert catalog.experiences == []
        assert catalog.runnable_count == 0
        assert catalog.hybrid is False
        assert catalog.primary_type is None
