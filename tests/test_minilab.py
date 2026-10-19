"""The Mini Lab, and the restrictions CLAUDE.md puts on it.

A simulation that reads like a real result is worse than no result, so these are
the strictest rules in the project:

    always labelled SIMULATED
    link simulated behaviour to real source code
    unsupported commands must state they require a real environment
    never invent output
    never produce performance measurements
    never present simulated results as experimental evidence

Each one is tested as a property of the code rather than of a prompt, because a
prompt is not a guarantee.
"""

from __future__ import annotations

import json

import pytest

from repolab.builds.minilab import (
    NEEDS_REAL_ENVIRONMENT,
    SIMULATED_LABEL,
    MiniLab,
    MiniLabEntry,
    generate,
    validate,
)
from repolab.builds.minilab_build import build_mini_lab
from repolab.evidence.models import CodeReference, Measurement, MeasurementSource
from repolab.llm import ModelRouter, ModelTier
from repolab.manifest.models import BuildKind, Fidelity
from repolab.testing.fakes import FakeLLMProvider, FakeMessage
from tests.support.builds import cassandra_manifest


def entry(**overrides) -> MiniLabEntry:
    fields = {
        "command": "DESCRIBE KEYSPACES;",
        "concept": "keyspaces",
        "explanation": "Lists the keyspaces defined on this node.",
        "code_references": [CodeReference(path="bin/cqlsh")],
    }
    fields.update(overrides)
    return MiniLabEntry(**fields)


def lab(**overrides) -> MiniLab:
    fields = {"repo_url": "https://github.com/apache/cassandra", "entries": [entry()]}
    fields.update(overrides)
    return MiniLab(**fields)


class TestGrounding:
    def test_an_entry_citing_no_file_is_not_answered_with(self) -> None:
        """ "Link simulated behaviour to real source code" — or do not show it."""
        subject = lab(entries=[entry(code_references=[]), entry()])

        assert len(subject.entries) == 2
        assert len(subject.grounded_entries) == 1

    def test_an_ungrounded_entry_is_never_matched(self) -> None:
        subject = lab(entries=[entry(command="SELECT 1;", code_references=[])])
        assert subject.respond("SELECT 1;").supported is False

    def test_a_lab_with_nothing_grounded_fails_validation(self) -> None:
        problem = validate(lab(entries=[entry(code_references=[])]))
        assert problem is not None
        assert "grounded" in problem.reason


class TestUnsupportedCommands:
    def test_an_unknown_command_says_it_needs_a_real_environment(self) -> None:
        """The single most important behaviour here: no invented output."""
        response = lab().respond("DROP KEYSPACE production;")

        assert response.supported is False
        assert response.explanation == NEEDS_REAL_ENVIRONMENT
        assert "real environment" in response.explanation

    def test_the_response_is_still_marked_simulated(self) -> None:
        assert lab().respond("anything").simulated is True
        assert lab().respond("anything").label == SIMULATED_LABEL

    def test_a_supported_command_answers_with_its_concept_and_code(self) -> None:
        response = lab().respond("DESCRIBE KEYSPACES;")

        assert response.supported is True
        assert response.concept == "keyspaces"
        assert response.code_references[0].path == "bin/cqlsh"

    @pytest.mark.parametrize(
        "typed", ["DESCRIBE KEYSPACES;", "describe keyspaces", "  DESCRIBE   KEYSPACES  "]
    )
    def test_matching_tolerates_how_it_was_typed(self, typed: str) -> None:
        assert lab().respond(typed).supported is True


class TestNoMeasurements:
    @pytest.mark.parametrize(
        "explanation",
        [
            "This completes in about 40ms.",
            "Reads roughly 2.5 MB from disk.",
            "Around 1000 ops/s on a single node.",
            "Takes 3 seconds on a warm cache.",
            "Roughly 2x faster than a full scan.",
        ],
    )
    def test_anything_reading_as_a_measurement_is_refused(self, explanation: str) -> None:
        """A Mini Lab must never produce a performance measurement."""
        problem = validate(lab(entries=[entry(explanation=explanation)]))

        assert problem is not None
        assert "measurement" in problem.reason

    def test_an_ordinary_explanation_passes(self) -> None:
        assert validate(lab()) is None

    def test_the_evidence_bundle_cannot_carry_one(self) -> None:
        """Enforced by the type: simulated evidence with a measurement will not construct."""
        from repolab.evidence.models import EvidenceBundle

        with pytest.raises(ValueError, match="simulated evidence cannot contain measurements"):
            EvidenceBundle(
                simulated=True,
                measurements=[
                    Measurement(
                        name="runtime",
                        value=1.0,
                        unit="ms",
                        source=MeasurementSource.PROCESS_TIMING,
                    )
                ],
            )

    def test_a_labs_evidence_is_simulated_and_measurement_free(self) -> None:
        evidence = lab().evidence()

        assert evidence.simulated is True
        assert evidence.measurements == []
        assert evidence.code_references


class TestTheArtifact:
    async def test_it_is_conceptual_and_simulated(self) -> None:
        artifact = await build_mini_lab(cassandra_manifest())

        assert artifact.kind is BuildKind.MINI_LAB
        assert artifact.fidelity is Fidelity.CONCEPTUAL
        assert artifact.simulated is True
        assert SIMULATED_LABEL in artifact.summary()

    async def test_it_never_supports_real_experiments(self) -> None:
        artifact = await build_mini_lab(cassandra_manifest())
        assert artifact.supports_real_experiments is False

    async def test_it_declares_no_commit(self) -> None:
        """ "Commit if applicable" — a simulation is built from no commit."""
        artifact = await build_mini_lab(cassandra_manifest())
        assert artifact.commit_sha == ""
        assert artifact.declaration()["commit_sha"] is None

    async def test_its_limitations_state_the_mini_lab_rules(self) -> None:
        artifact = await build_mini_lab(cassandra_manifest())
        limitations = " ".join(artifact.capabilities.limitations).lower()

        assert "simulated" in limitations
        assert "measurements" in limitations
        assert "real environment" in limitations

    async def test_it_is_not_ready_until_validation_passes(self, monkeypatch) -> None:
        """The invariant holds even here: no candidate is ready before its check.

        A Mini Lab has no environment to smoke-test, so what it checks is that
        the simulation obeys its rules.
        """
        from repolab.builds import minilab_build

        async def ungrounded(manifest, *, router=None):
            return lab(entries=[entry(code_references=[])])

        monkeypatch.setattr(minilab_build, "generate", ungrounded)
        artifact = await build_mini_lab(cassandra_manifest())

        assert artifact.ready is False
        assert artifact.smoke is not None
        assert artifact.smoke.passed is False

    async def test_a_valid_lab_is_ready(self) -> None:
        artifact = await build_mini_lab(cassandra_manifest())
        assert artifact.smoke is not None
        assert artifact.smoke.passed is True
        assert artifact.ready is True

    async def test_the_smoke_command_says_nothing_was_executed(self) -> None:
        artifact = await build_mini_lab(cassandra_manifest())
        assert "nothing is executed" in (artifact.smoke.command if artifact.smoke else "")


class TestGeneration:
    async def test_without_a_model_it_builds_from_the_manifest(self) -> None:
        subject = await generate(cassandra_manifest())
        assert subject.generated_by == "deterministic"

    async def test_a_model_entry_naming_an_unknown_file_is_dropped(self) -> None:
        """A reference to a file that does not exist is not evidence."""
        manifest = cassandra_manifest()
        manifest.entrypoints = []
        manifest.interaction_surfaces[0].evidence = [CodeReference(path="bin/cqlsh")]

        provider = FakeLLMProvider(
            message=FakeMessage(
                content=json.dumps(
                    {
                        "summary": "s",
                        "entries": [
                            {
                                "command": "real",
                                "concept": "c",
                                "explanation": "e",
                                "file_path": "bin/cqlsh",
                            },
                            {
                                "command": "invented",
                                "concept": "c",
                                "explanation": "e",
                                "file_path": "src/does/not/exist.java",
                            },
                        ],
                    }
                )
            )
        )
        subject = await generate(
            manifest, router=ModelRouter(provider, {ModelTier.SUPER: "fake/super"})
        )

        assert [item.command for item in subject.entries] == ["real"]
        assert subject.generated_by == "super"

    async def test_a_model_failure_falls_back_rather_than_failing(self) -> None:
        provider = FakeLLMProvider(fail_with=RuntimeError("upstream is down"))
        subject = await generate(
            cassandra_manifest(), router=ModelRouter(provider, {ModelTier.SUPER: "fake/super"})
        )
        assert subject.generated_by == "deterministic"

    async def test_the_prompt_forbids_output_and_measurements(self) -> None:
        from repolab.builds.minilab import SYSTEM_PROMPT

        assert "Never write example output" in SYSTEM_PROMPT
        assert "Never state a duration" in SYSTEM_PROMPT
