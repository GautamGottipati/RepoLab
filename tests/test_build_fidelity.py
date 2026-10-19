"""Fidelity, and how it is decided.

Fidelity is the thing a learner is trusting when they read a measurement, so it
must never be something a caller can assert. These tests pin the table CLAUDE.md
gives, and the ordering the ladder uses to choose between candidates.
"""

from __future__ import annotations

import pytest

from repolab.builds import (
    FIDELITY_ORDER,
    BuildArtifact,
    BuildCapabilities,
    SmokeTestResult,
    fidelity_for,
    fidelity_rank,
)
from repolab.manifest.models import BuildKind, Fidelity


def ready(kind: BuildKind, **overrides) -> BuildArtifact:
    fields = {
        "repo_url": "https://github.com/apache/cassandra",
        "commit_sha": "c" * 40,
        "kind": kind,
        "checkpoint_id": "cp-1",
        "smoke": SmokeTestResult(name="s", command="c", passed=True),
    }
    fields.update(overrides)
    return BuildArtifact(**fields)


class TestTheTable:
    @pytest.mark.parametrize(
        ("kind", "deviations", "expected"),
        [
            (BuildKind.SOURCE_BUILD, [], Fidelity.EXACT),
            (BuildKind.SOURCE_BUILD, ["pyspark 2.4.0 -> 3.5.1"], Fidelity.HIGH),
            (BuildKind.OFFICIAL_RELEASE, [], Fidelity.HIGH),
            (BuildKind.OFFICIAL_RELEASE, ["version differs"], Fidelity.HIGH),
            (BuildKind.PARTIAL_BUILD, [], Fidelity.PARTIAL),
            (BuildKind.MINI_LAB, [], Fidelity.CONCEPTUAL),
        ],
    )
    def test_fidelity_follows_the_kind_and_the_deviations(
        self, kind: BuildKind, deviations: list[str], expected: Fidelity
    ) -> None:
        assert fidelity_for(kind, deviations) is expected

    def test_only_a_clean_source_build_is_exact(self) -> None:
        """An official release has no deviations of its own and is still not exact.

        It is a released version, not the commit that was asked for, and no
        amount of clean building changes that.
        """
        for kind in BuildKind:
            if kind is BuildKind.SOURCE_BUILD:
                continue
            assert fidelity_for(kind) is not Fidelity.EXACT

    def test_the_artifact_derives_its_own_fidelity(self) -> None:
        artifact = ready(
            BuildKind.SOURCE_BUILD,
            capabilities=BuildCapabilities(deviations=["python 3.6 -> 3.11"]),
        )
        assert artifact.fidelity is Fidelity.HIGH


class TestOrdering:
    def test_the_ladder_runs_worst_to_best(self) -> None:
        assert FIDELITY_ORDER == (
            Fidelity.CONCEPTUAL,
            Fidelity.PARTIAL,
            Fidelity.HIGH,
            Fidelity.EXACT,
        )

    def test_rank_increases_with_fidelity(self) -> None:
        ranks = [fidelity_rank(fidelity) for fidelity in FIDELITY_ORDER]
        assert ranks == sorted(ranks)
        assert fidelity_rank(Fidelity.EXACT) > fidelity_rank(Fidelity.CONCEPTUAL)

    def test_a_source_build_outranks_an_official_release(self) -> None:
        source = ready(BuildKind.SOURCE_BUILD)
        official = ready(BuildKind.OFFICIAL_RELEASE)
        assert source.fidelity_rank > official.fidelity_rank


class TestWhatAnArtifactDeclares:
    def test_it_declares_everything_the_specification_requires(self) -> None:
        artifact = ready(
            BuildKind.OFFICIAL_RELEASE,
            version="5.0.2",
            capabilities=BuildCapabilities(
                capabilities=["run CQL"],
                limitations=["single node"],
                deviations=["5.0 substituted for the requested commit"],
            ),
        )
        declaration = artifact.declaration()

        assert declaration["source"] == "official_release"
        assert declaration["version"] == "5.0.2"
        assert declaration["commit_sha"] == "c" * 40
        assert declaration["fidelity"] == "high"
        assert declaration["capabilities"] == ["run CQL"]
        assert declaration["limitations"] == ["single node"]
        assert declaration["smoke_test"] is not None
        assert declaration["ready"] is True

    def test_a_mini_lab_declares_no_commit(self) -> None:
        """ "Commit if applicable" — a simulation is built from no commit."""
        lab = ready(BuildKind.MINI_LAB, commit_sha="", simulated=True)
        assert lab.declaration()["commit_sha"] is None
        assert lab.declaration()["simulated"] is True


class TestRealExperiments:
    def test_conceptual_fidelity_never_supports_them(self) -> None:
        """A Mini Lab must never produce a performance measurement."""
        lab = ready(BuildKind.MINI_LAB, simulated=True)
        assert lab.ready is True
        assert lab.supports_real_experiments is False

    @pytest.mark.parametrize(
        "kind",
        [BuildKind.SOURCE_BUILD, BuildKind.OFFICIAL_RELEASE, BuildKind.PARTIAL_BUILD],
    )
    def test_every_real_environment_supports_them(self, kind: BuildKind) -> None:
        assert ready(kind).supports_real_experiments is True

    def test_an_unready_build_supports_nothing(self) -> None:
        assert ready(BuildKind.SOURCE_BUILD, smoke=None).supports_real_experiments is False


class TestBetterThan:
    def test_anything_ready_beats_nothing(self) -> None:
        assert ready(BuildKind.MINI_LAB).better_than(None) is True

    def test_higher_fidelity_wins(self) -> None:
        assert ready(BuildKind.SOURCE_BUILD).better_than(ready(BuildKind.PARTIAL_BUILD)) is True

    def test_equal_fidelity_is_not_an_upgrade(self) -> None:
        """Switching environments costs the learner their state, for no gain."""
        assert (
            ready(BuildKind.OFFICIAL_RELEASE).better_than(ready(BuildKind.OFFICIAL_RELEASE))
            is False
        )

    def test_lower_fidelity_is_never_an_upgrade(self) -> None:
        assert ready(BuildKind.MINI_LAB).better_than(ready(BuildKind.SOURCE_BUILD)) is False

    def test_an_unready_build_is_never_better(self) -> None:
        unready = ready(BuildKind.SOURCE_BUILD, smoke=None)
        assert unready.better_than(ready(BuildKind.MINI_LAB)) is False
