"""The ladder a repository produces.

Separate from racing them: the list can be shown to a user, or asserted here,
without anything being built.
"""

from __future__ import annotations

import pytest

from repolab.builds import NoCandidatesError, candidates_for, is_cassandra
from repolab.builds.cassandra import OFFICIAL_VERSION, SMOKE_MARKER
from repolab.manifest.models import BuildKind, Fidelity
from tests.support.builds import cassandra_manifest


class TestCassandra:
    @pytest.fixture
    def ladder(self):
        return candidates_for(cassandra_manifest())

    def test_all_four_rungs_in_descending_fidelity(self, ladder) -> None:
        assert [candidate.kind for candidate in ladder] == [
            BuildKind.SOURCE_BUILD,
            BuildKind.OFFICIAL_RELEASE,
            BuildKind.PARTIAL_BUILD,
            BuildKind.MINI_LAB,
        ]
        assert [candidate.expected_fidelity for candidate in ladder] == [
            Fidelity.EXACT,
            Fidelity.HIGH,
            Fidelity.PARTIAL,
            Fidelity.CONCEPTUAL,
        ]

    def test_ranks_strictly_decrease(self, ladder) -> None:
        ranks = [candidate.rank for candidate in ladder]
        assert ranks == sorted(ranks, reverse=True)
        assert len(set(ranks)) == len(ranks)

    def test_every_rung_says_why_it_exists(self, ladder) -> None:
        assert all(candidate.why.strip() for candidate in ladder)

    def test_only_the_mini_lab_builds_nothing(self, ladder) -> None:
        assert [candidate.builds_nothing for candidate in ladder] == [False, False, False, True]

    def test_every_buildable_rung_runs_a_real_select(self, ladder) -> None:
        """CLAUDE.md fixes the smoke test: cqlsh connects and a SELECT executes."""
        for candidate in ladder:
            if candidate.plan is None:
                continue
            smoke = candidate.plan.smoke
            assert "SELECT" in smoke.command
            assert "system.local" in smoke.command
            assert smoke.expect_stdout_contains == SMOKE_MARKER

    def test_the_smoke_test_waits_rather_than_assuming(self, ladder) -> None:
        """A fresh node takes 30-60s before it will accept a connection."""
        for candidate in ladder:
            if candidate.plan is not None:
                assert "sleep" in candidate.plan.smoke.command

    def test_the_source_build_uses_the_repositorys_own_target(self, ladder) -> None:
        commands = " ".join(step.command for step in ladder[0].plan.all_steps)
        assert "ant jar" in commands

    def test_the_source_build_substitutes_nothing(self, ladder) -> None:
        """Which is what makes it the exact rung."""
        assert ladder[0].plan.deviations == []
        assert ladder[0].expected_fidelity is Fidelity.EXACT

    def test_the_official_release_discloses_the_version_difference(self, ladder) -> None:
        deviations = ladder[1].plan.deviations
        assert deviations, "a release is never the commit that was asked for"
        assert OFFICIAL_VERSION in deviations[0]
        assert ladder[1].expected_fidelity is Fidelity.HIGH

    def test_the_official_release_pins_its_version(self, ladder) -> None:
        """A floating tag would make a recorded experiment unreproducible."""
        assert ladder[1].plan.base_image.endswith(f":{OFFICIAL_VERSION}")

    def test_the_partial_build_declares_what_it_cannot_do(self, ladder) -> None:
        limitations = " ".join(ladder[2].plan.limitations).lower()
        assert "heap" in limitations
        assert "not the requested commit" in limitations
        assert "reduced" in limitations

    def test_the_partial_build_warns_about_measuring_on_it(self, ladder) -> None:
        limitations = " ".join(ladder[2].plan.limitations).lower()
        assert "performance measurements" in limitations

    def test_every_rung_states_what_it_can_do(self, ladder) -> None:
        for candidate in ladder:
            if candidate.plan is not None:
                assert candidate.plan.capabilities

    def test_the_delay_knob_is_off_unless_asked_for(self) -> None:
        assert all(c.demo_delay_seconds == 0 for c in candidates_for(cassandra_manifest()))
        delayed = candidates_for(cassandra_manifest(), source_delay_seconds=30)
        assert delayed[0].demo_delay_seconds == 30
        assert all(c.demo_delay_seconds == 0 for c in delayed[1:]), "only the source build"


class TestRecognisingCassandra:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/apache/cassandra",
            "https://github.com/apache/cassandra.git",
            "https://github.com/apache/cassandra/",
        ],
    )
    def test_the_repository_is_recognised(self, url: str) -> None:
        assert is_cassandra(cassandra_manifest(repo_url=url)) is True

    def test_something_else_is_not(self) -> None:
        assert is_cassandra(cassandra_manifest(repo_url="https://github.com/a/b")) is False

    def test_it_matches_on_the_repository_not_on_frameworks(self) -> None:
        """Cassandra's build is Ant, so framework detection finds nothing for it.

        A known gap since Phase 3, which is why this cannot key off frameworks.
        """
        manifest = cassandra_manifest()
        assert manifest.frameworks == []
        assert is_cassandra(manifest) is True


class TestSpark:
    def test_spark_still_gets_exactly_one_candidate(self, make_repo) -> None:
        """Nothing P0 depends on changed: a Spark source build, or nothing."""
        from tests.test_build_resolver import manifest_for
        from tests.test_detectors import SPARK_REPO

        root, _ = make_repo(SPARK_REPO)
        ladder = candidates_for(manifest_for(root))

        assert len(ladder) == 1
        assert ladder[0].kind is BuildKind.SOURCE_BUILD


class TestUnsupported:
    def test_an_unknown_repository_shape_is_refused(self, make_repo) -> None:
        from tests.test_build_resolver import manifest_for
        from tests.test_detectors import WEB_REPO

        root, _ = make_repo(WEB_REPO)
        with pytest.raises(NoCandidatesError) as excinfo:
            candidates_for(manifest_for(root))

        assert "no build candidates" in str(excinfo.value)
        assert excinfo.value.surface


class TestSubmodules:
    def test_the_source_build_declares_that_it_needs_them(self) -> None:
        """`ant jar` invokes a gradle project in `modules/accord`, which a plain
        shallow clone leaves empty."""
        from repolab.builds import needs_submodules

        ladder = candidates_for(cassandra_manifest())
        assert ladder[0].plan.needs_submodules is True
        assert needs_submodules(ladder) is True

    def test_the_container_rungs_do_not(self) -> None:
        ladder = candidates_for(cassandra_manifest())
        assert all(
            candidate.plan.needs_submodules is False
            for candidate in ladder[1:]
            if candidate.plan is not None
        )

    def test_spark_does_not(self, make_repo) -> None:
        """Ingestion reads files; nothing P0 needs a submodule."""
        from repolab.builds import needs_submodules
        from tests.test_build_resolver import manifest_for
        from tests.test_detectors import SPARK_REPO

        root, _ = make_repo(SPARK_REPO)
        assert needs_submodules(candidates_for(manifest_for(root))) is False
