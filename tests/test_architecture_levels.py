"""Progressive architecture: repository -> system model -> subsystems -> components.

Offline throughout. The orientation pass is driven by a scripted Super that
does what real models do -- including overreaching -- and every refusal is
asserted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repolab.architecture.actions import ExperimentAvailability, NodeActions, concept_actions
from repolab.architecture.models import (
    ArchitectureLevel,
    EvidenceKind,
    LearningTargetKind,
    Provenance,
    RepositoryKind,
    load_graph,
)
from repolab.architecture.runtime import MeasuredValue, RuntimeOverlay, roll_up
from repolab.architecture.source import is_peripheral
from repolab.architecture.subsystems import MAX_SUBSYSTEMS
from repolab.architecture.understanding import (
    OrientationConcept,
    OrientationLink,
    OrientationStep,
    OrientationSubsystem,
    OrientationWalkStep,
    OrientationWalkthrough,
    RepositoryOrientation,
    overview_walkthroughs,
)
from repolab.evidence.models import DocumentationReference
from repolab.llm import LLMError
from repolab.manifest.architecture import GraphSource
from tests.support import architecture_fixtures as fx

DOC = DocumentationReference(
    url="https://engine.example.org/docs/latest/cluster-overview.html",
    title="Cluster overview",
    excerpt="The driver splits work into tasks that executors run.",
)


class Super:
    def __init__(self, value: RepositoryOrientation | None = None, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.prompts: list[str] = []

    async def super(self, *, prompt: str, **_: Any):
        self.prompts.append(prompt)
        if self.fail:
            raise LLMError("the model is unavailable")

        class Completion:
            pass

        Completion.value = self.value  # type: ignore[attr-defined]
        return Completion()


async def build(tmp_path: Path, files: dict[str, str], **kwargs: Any):
    return await fx.analyse(fx.write_repo(tmp_path, files), **kwargs)


def subsystem_labels(graph) -> list[str]:
    return [s.label for s in graph.subsystems]


# ---------------------------------------------------------------------------
# Subsystems
# ---------------------------------------------------------------------------


class TestSubsystemDiscovery:
    async def test_declared_modules_group_by_their_top_directory(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        labels = subsystem_labels(graph)
        assert set(labels) == {"core", "sql", "mllib", "streaming", "connector"}
        sql = next(s for s in graph.subsystems if s.label == "sql")
        assert sql.declared_by == "2 Maven modules"
        assert sql.outline == ["catalyst", "core"]
        assert all(e.kind is EvidenceKind.BUILD and e.path == "pom.xml" for e in sql.evidence)
        assert "examples" not in labels  # declared, but peripheral

    async def test_a_java_package_named_example_is_still_code(self, tmp_path: Path) -> None:
        assert not is_peripheral("core/src/main/scala/org/example/engine/EngineContext.scala")
        assert is_peripheral("examples/src/main/python/pi.py")

    @pytest.mark.parametrize(
        ("build_file", "text"),
        [
            ("settings.gradle", "rootProject.name = 'x'\ninclude ':api', ':worker'\n"),
            ("package.json", '{"name": "x", "workspaces": ["packages/*"]}\n'),
            ("Cargo.toml", '[workspace]\nmembers = ["crates/api", "crates/worker"]\n'),
            ("go.work", "go 1.22\n\nuse (\n    ./api\n    ./worker\n)\n"),
        ],
    )
    async def test_every_build_file_style(self, tmp_path: Path, build_file: str, text: str) -> None:
        prefix = {"package.json": "packages/", "Cargo.toml": "crates/"}.get(build_file, "")
        ext = {"package.json": "ts", "Cargo.toml": "rs", "go.work": "go"}.get(build_file, "java")
        files = {
            build_file: text,
            f"{prefix}api/src/main.{ext}": "x\n",
            f"{prefix}worker/src/main.{ext}": "x\n",
        }
        graph, _, _ = await build(tmp_path, files)
        assert set(subsystem_labels(graph)) == {"api", "worker"}
        assert all(s.declared_by != "source directory" for s in graph.subsystems)

    async def test_without_modules_it_descends_through_a_wrapper(self, tmp_path: Path) -> None:
        files = {
            "src/app/orders/service.py": "x = 1\n",
            "src/app/billing/service.py": "x = 1\n",
            "tests/test_orders.py": "x = 1\n",
        }
        graph, _, _ = await build(tmp_path, files)
        assert set(subsystem_labels(graph)) == {"orders", "billing"}

    async def test_too_many_parts_are_folded_and_listed(self, tmp_path: Path) -> None:
        files = {f"part{i:02d}/code.py": "x = 1\n" for i in range(MAX_SUBSYSTEMS + 4)}
        graph, _, _ = await build(tmp_path, files)
        assert len(graph.subsystems) == MAX_SUBSYSTEMS
        other = graph.subsystem("sub:other-modules")
        assert other is not None and len(other.paths) == 5 and "part15" in other.description

    async def test_every_component_belongs_to_a_subsystem(self, tmp_path: Path) -> None:
        for files in (fx.WEB_API, fx.CLI_TOOL, fx.WORKFLOW, fx.HYBRID, fx.MULTI_MODULE):
            graph, _, _ = await build(tmp_path / str(len(files)), files)
            ids = {s.id for s in graph.subsystems}
            assert all(n.subsystem in ids for n in graph.nodes)
            for subsystem in graph.subsystems:
                assert subsystem.component_ids == [
                    n.id for n in graph.nodes if n.subsystem == subsystem.id
                ]

    async def test_importance_is_evidence_and_not_size_or_detector_hits(
        self, tmp_path: Path
    ) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        core, mllib = graph.subsystem("sub:core"), graph.subsystem("sub:mllib")
        assert core is not None and mllib is not None
        # mllib has four times the detected components; core is still first.
        assert len(mllib.component_ids) > len(core.component_ids)
        assert graph.subsystems[0].id == "sub:core"
        assert "central name" in core.importance_signals
        assert mllib.importance_signals["components"] <= 0.5

    async def test_starting_points_go_where_the_code_starts(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        core = graph.subsystem("sub:core")
        assert core is not None
        first = core.starting_points[0]
        assert first.is_directory and first.path == "core/src/main/scala/org/example/engine"
        assert first.learn_about == ["scheduler", "executor"]


# ---------------------------------------------------------------------------
# Repository understanding and the deterministic levels
# ---------------------------------------------------------------------------


class TestUnderstandingWithoutAModel:
    async def test_it_quotes_the_repository_about_itself(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        understanding = graph.understanding
        assert understanding is not None
        assert understanding.name == "Example Engine"
        assert understanding.one_line_description == "A distributed data processing engine."
        assert understanding.beginner_explanation.startswith("Example Engine is a distributed")
        assert understanding.inferred_fields == []
        labels = {e.label for e in understanding.evidence}
        assert {"README.md:1", "pom.xml:2"} <= labels

    @pytest.mark.parametrize(
        ("files", "kind", "provenance"),
        [
            (fx.WEB_API, RepositoryKind.WEB_APPLICATION, Provenance.STATIC),
            (fx.LIBRARY, RepositoryKind.LIBRARY, Provenance.STATIC),
            (fx.WORKFLOW, RepositoryKind.WORKFLOW_SYSTEM, Provenance.INFERRED),
            (fx.CLI_TOOL, RepositoryKind.CLI_APPLICATION, Provenance.INFERRED),
            (fx.UNKNOWN, RepositoryKind.UNKNOWN, Provenance.STATIC),
        ],
    )
    async def test_repository_type_comes_from_evidence(
        self, tmp_path: Path, files, kind, provenance
    ) -> None:
        graph, _, _ = await build(tmp_path, files)
        assert graph.understanding.repository_type is kind
        assert graph.understanding.type_provenance is provenance

    async def test_a_framework_with_a_launcher_is_not_called_a_cli(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        understanding = graph.understanding
        assert understanding.repository_type is not RepositoryKind.CLI_APPLICATION
        assert RepositoryKind.DISTRIBUTED_SYSTEM in understanding.type_candidates
        assert any("distributed" in r for r in understanding.type_reasons)

    async def test_system_model_is_bounded_and_backed(self, tmp_path: Path) -> None:
        for files in (fx.SPARK_PIPELINE, fx.WEB_API, fx.CLI_TOOL, fx.HYBRID, fx.MULTI_MODULE):
            graph, _, _ = await build(tmp_path / str(len(files)), files)
            concepts = graph.system_model.concepts
            assert 3 <= len(concepts) <= 8
            assert all(c.evidence for c in concepts)

    async def test_an_unknown_repository_gets_no_invented_concepts(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.UNKNOWN)
        assert graph.system_model.concepts == []
        assert graph.walkthroughs == []
        assert graph.completeness.value == "partial"

    async def test_a_web_app_walkthrough_follows_one_request(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WEB_API)
        walk = overview_walkthroughs(graph)[0]
        assert walk.name == "Follow a request"
        assert [s.label for s in walk.steps] == [
            "User's browser",
            "React frontend",
            "Orders API",
            "PostgreSQL",
        ]

    async def test_learning_path_points_at_real_things(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.HYBRID)
        concepts = {c.id for c in graph.system_model.concepts}
        subsystems = {s.id for s in graph.subsystems}
        walks = {w.id for w in graph.walkthroughs}
        for step in graph.learning_path:
            if step.target_kind is LearningTargetKind.CONCEPT:
                assert step.target_id in concepts
            elif step.target_kind is LearningTargetKind.SUBSYSTEM:
                assert step.target_id in subsystems
            elif step.target_kind is LearningTargetKind.WALKTHROUGH:
                assert step.target_id in walks
            else:
                assert step.question

    async def test_stored_v2_graphs_still_load(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.WEB_API)
        data = graph.model_dump(mode="json")
        for key in ("understanding", "system_model", "subsystems", "learning_path", "walkthroughs"):
            data.pop(key)
        data["schema_version"] = 2
        loaded = load_graph(data)
        assert loaded.understanding is None and loaded.subsystems == []
        assert len(loaded.nodes) == len(graph.nodes)


class TestTheBeginnerSeesThePartsFirst:
    """The acceptance case: a large engine with ML wrappers deep in one module."""

    async def test_wrappers_appear_only_inside_their_subsystem(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        wrappers = [n for n in graph.nodes if "Wrapper" in n.label]
        assert wrappers, "the analyzers still find the wrappers"
        assert {n.subsystem for n in wrappers} == {"sub:mllib"}

        overview = [c.label for c in graph.system_model.concepts]
        overview += [s.label for s in graph.subsystems]
        overview += [step.label for w in overview_walkthroughs(graph) for step in w.steps]
        overview += [step.title for step in graph.learning_path]
        assert not any("Wrapper" in text for text in overview)

        mllib_walks = [w for w in graph.walkthroughs if w.subsystem == "sub:mllib"]
        assert mllib_walks and all(w.level is ArchitectureLevel.COMPONENT for w in mllib_walks)


# ---------------------------------------------------------------------------
# The Nemotron orientation pass
# ---------------------------------------------------------------------------


def orientation(**overrides: Any) -> RepositoryOrientation:
    base = {
        "one_line_description": "An engine that runs data processing on many machines at once.",
        "repository_type": "distributed_system",
        "beginner_explanation": (
            "You write a program against its APIs. It plans the work and splits it into "
            "small tasks that run on many machines."
        ),
        "primary_purpose": "Process large datasets in parallel.",
        "primary_users": "Data engineers.",
        "recommended_start": "How does a program become work on many machines?",
        "evidence": ["README.md:5", DOC.url],
        "subsystems": [
            OrientationSubsystem(
                id="sub:mllib", label="Machine learning (MLlib)", description="ML algorithms."
            )
        ],
        "concepts": [
            OrientationConcept(
                id="app",
                label="Your application",
                what_it_does="The program a developer writes using the APIs.",
                subsystem_ids=["sub:sql"],
            ),
            OrientationConcept(
                id="scheduler",
                label="Scheduler",
                term="DAGScheduler",
                what_it_does="Decides which pieces of work run, and where.",
                evidence=["core/src/main/scala/org/example/engine/scheduler"],
            ),
            OrientationConcept(
                id="executors",
                label="Executors",
                what_it_does="Processes on worker machines that run tasks.",
                evidence=["core/src/main/scala/org/example/engine/executor/Executor.scala:3"],
            ),
            OrientationConcept(
                id="invented",
                label="Quantum planner",
                what_it_does="Plans work with quantum annealing.",
                evidence=["core/src/main/scala/Quantum.scala"],
            ),
        ],
        "links": [
            OrientationLink(source="app", target="scheduler", type="calls", evidence=DOC.url),
            OrientationLink(
                source="scheduler",
                target="executors",
                type="control_flow",
                evidence="core/src/main/scala/org/example/engine/scheduler/DAGScheduler.scala:3",
            ),
            OrientationLink(
                source="scheduler", target="executors", type="calls", evidence="nowhere.py:1"
            ),
        ],
        "learning_path": [
            OrientationStep(title="What problem does it solve?", question="Why use it?"),
            OrientationStep(title="The scheduler", target="scheduler"),
            OrientationStep(title="A ghost", target="sub:ghost"),
        ],
        "walkthrough": OrientationWalkthrough(
            name="Follow a job",
            steps=[
                OrientationWalkStep(target="app", detail="You call an action."),
                OrientationWalkStep(target="scheduler", detail="It splits the job."),
                OrientationWalkStep(target="executors", detail="They run the tasks."),
            ],
        ),
    }
    base.update(overrides)
    return RepositoryOrientation(**base)


class TestOrientationPass:
    async def test_cited_claims_are_accepted_and_marked_inferred(self, tmp_path: Path) -> None:
        model = Super(orientation())
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE, router=model, docs=[DOC])
        understanding = graph.understanding
        assert understanding.repository_type is RepositoryKind.DISTRIBUTED_SYSTEM
        assert understanding.type_provenance is Provenance.INFERRED
        assert "beginner_explanation" in understanding.inferred_fields
        assert any(e.kind is EvidenceKind.DOCUMENTATION for e in understanding.evidence)
        assert graph.generated_by is GraphSource.SUPER_ENRICHED

        concepts = {c.label: c for c in graph.system_model.concepts}
        assert set(concepts) == {"Your application", "Scheduler", "Executors"}
        assert concepts["Scheduler"].term == "DAGScheduler"
        assert all(c.provenance is Provenance.INFERRED for c in concepts.values())
        assert graph.dropped["concept without evidence"] == 1

        mllib = graph.subsystem("sub:mllib")
        assert mllib.label == "Machine learning (MLlib)" and "label" in mllib.inferred_fields
        assert mllib.paths == ["mllib"]  # naming never changes membership

    async def test_relationships_need_real_citations(self, tmp_path: Path) -> None:
        graph, _, _ = await build(
            tmp_path, fx.MULTI_MODULE, router=Super(orientation()), docs=[DOC]
        )
        links = {(link.source, link.target): link for link in graph.system_model.links}
        assert ("concept:app", "concept:scheduler") in links
        assert links[("concept:app", "concept:scheduler")].evidence[0].url == DOC.url
        assert graph.dropped["relationship citing evidence that does not exist"] == 1

    async def test_walkthrough_follows_only_existing_links(self, tmp_path: Path) -> None:
        graph, _, _ = await build(
            tmp_path, fx.MULTI_MODULE, router=Super(orientation()), docs=[DOC]
        )
        walk = overview_walkthroughs(graph)[0]
        assert walk.level is ArchitectureLevel.SYSTEM
        assert [s.label for s in walk.steps] == ["Your application", "Scheduler", "Executors"]

        unlinked = orientation(
            walkthrough=OrientationWalkthrough(
                name="Skip",
                steps=[OrientationWalkStep(target="app"), OrientationWalkStep(target="executors")],
            )
        )
        graph, _, _ = await build(
            tmp_path / "b", fx.MULTI_MODULE, router=Super(unlinked), docs=[DOC]
        )
        assert all(w.level is not ArchitectureLevel.SYSTEM for w in graph.walkthroughs)
        assert graph.dropped["walkthrough between unrelated concepts"] == 1

    async def test_documentation_only_counts_if_it_was_retrieved(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE, router=Super(orientation()))
        links = {(link.source, link.target) for link in graph.system_model.links}
        assert ("concept:app", "concept:scheduler") not in links
        assert not any(e.kind is EvidenceKind.DOCUMENTATION for e in graph.understanding.evidence)

    async def test_learning_steps_to_nowhere_are_dropped(self, tmp_path: Path) -> None:
        graph, _, _ = await build(
            tmp_path, fx.MULTI_MODULE, router=Super(orientation()), docs=[DOC]
        )
        titles = [s.title for s in graph.learning_path]
        assert titles == ["What problem does it solve?", "The scheduler"]
        assert graph.learning_path[1].target_kind is LearningTargetKind.CONCEPT
        assert graph.dropped["learning step pointing at something that does not exist"] == 1

    async def test_type_outside_the_candidates_is_refused(self, tmp_path: Path) -> None:
        model = Super(orientation(repository_type="web_application"))
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE, router=model, docs=[DOC])
        assert graph.understanding.repository_type is not RepositoryKind.WEB_APPLICATION
        assert graph.dropped["repository type outside the evidenced candidates"] == 1

    async def test_a_decided_type_is_not_overridden(self, tmp_path: Path) -> None:
        model = Super(orientation(repository_type="library", evidence=["shop/main.py"]))
        graph, _, _ = await build(tmp_path, fx.WEB_API, router=model)
        assert graph.understanding.repository_type is RepositoryKind.WEB_APPLICATION
        assert graph.understanding.type_provenance is Provenance.STATIC

    async def test_invented_numbers_are_refused(self, tmp_path: Path) -> None:
        model = Super(orientation(one_line_description="Runs jobs in 200 ms on average."))
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE, router=model, docs=[DOC])
        assert graph.understanding.one_line_description == "A distributed data processing engine."
        assert graph.dropped["invented measurement"] >= 1

    async def test_too_few_backed_concepts_keep_the_structural_model(self, tmp_path: Path) -> None:
        model = Super(
            orientation(
                concepts=[
                    OrientationConcept(id="x", label="X", what_it_does="x", evidence=["nope.py"])
                ]
            )
        )
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE, router=model, docs=[DOC])
        assert graph.system_model.provenance is Provenance.STATIC
        assert {c.label for c in graph.system_model.concepts} >= {"core", "sql"}

    async def test_a_failing_model_leaves_the_deterministic_overview(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE, router=Super(fail=True))
        assert graph.understanding.name == "Example Engine"
        assert graph.system_model.concepts
        assert any("Nemotron could not be reached" in gap for gap in graph.gaps)

    async def test_the_prompt_is_bounded_and_names_only_real_ids(self, tmp_path: Path) -> None:
        model = Super(orientation())
        await build(tmp_path, fx.MULTI_MODULE, router=model, docs=[DOC])
        prompt = model.prompts[0]
        assert "sub:core" in prompt and "REPOSITORY TYPE CANDIDATES" in prompt
        assert DOC.url in prompt
        assert len(prompt) < 20_000


# ---------------------------------------------------------------------------
# Actions and measurements at the higher levels
# ---------------------------------------------------------------------------


class TestHigherLevelActions:
    async def test_a_concept_offers_only_its_components_experiment(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        wrapper = next(n for n in graph.nodes if n.label == "Kmeans Wrapper pipeline")
        actions = {
            n.id: NodeActions(
                experiment=ExperimentAvailability(
                    available=False, reason="no", needs_environment=True
                )
            )
            for n in graph.nodes
        }
        actions[wrapper.id] = NodeActions(
            experiment=ExperimentAvailability(
                available=True, backend="spark", reason="runs", question="How do partitions matter?"
            )
        )
        result = concept_actions(graph, actions)
        assert result["sub:mllib"].experiment.available
        assert result["sub:mllib"].suggested_questions[0] == "How do partitions matter?"
        assert not result["sub:sql"].experiment.available
        assert result["concept:core"].experiment.needs_environment
        assert "Why does Example Engine need core?" in result["concept:core"].suggested_questions

    async def test_measurements_roll_up_unchanged(self, tmp_path: Path) -> None:
        graph, _, _ = await build(tmp_path, fx.MULTI_MODULE)
        wrapper = next(n for n in graph.nodes if n.label == "Kmeans Wrapper pipeline")
        overlay = RuntimeOverlay(runs=["spark run abc"])
        overlay.add(
            wrapper.id,
            MeasuredValue(name="total runtime", value=8200, unit="ms", source="spark run abc"),
        )
        roll_up(graph, overlay)
        rolled = overlay.by_group["sub:mllib"]
        assert [(v.value, v.unit, v.source, v.provenance) for v in rolled] == [
            (8200, "ms", "spark run abc", "measured")
        ]
        assert rolled[0].name == "Kmeans Wrapper pipeline · total runtime"
        assert overlay.by_group["concept:mllib"] == rolled
        assert "sub:core" not in overlay.by_group


class TestReadmeReading:
    async def test_asciidoc_with_badges_and_a_setext_title(self, tmp_path: Path) -> None:
        files = {
            "README.asc": (
                "image:https://img.shields.io/badge/x.svg[License, link=https://x]\n\n"
                "Example Store\n-------------\n\n"
                "Example Store is a partitioned row store that spreads data over machines.\n\n"
                "Getting started\n---------------\n\n"
                "This short guide walks you through starting a single node quickly.\n"
            ),
            "store/server.py": "x = 1\n",
        }
        graph, _, _ = await build(tmp_path, files)
        understanding = graph.understanding
        assert understanding.name == "Example Store"
        assert understanding.one_line_description.startswith("Example Store is a partitioned")
        assert "guide" not in (understanding.beginner_explanation or "")

    async def test_instructions_are_not_mistaken_for_a_description(self, tmp_path: Path) -> None:
        files = {
            "README.md": (
                "# Some Template\n\n<a href='x'><img src='badge.svg'></a>\n\n"
                "## How to use it\n\n"
                "Click the Use this template button to create a new repository.\n"
            ),
            "app/main.py": "x = 1\n",
        }
        graph, _, _ = await build(tmp_path, files)
        assert graph.understanding.name == "Some Template"
        assert graph.understanding.beginner_explanation is None


def test_only_retrieved_documentation_is_passed_on() -> None:
    from repolab.agents.models import AgentRun, AgentStep, ToolRequest, ToolResult
    from worker.tasks import retrieved_docs

    hit = {"url": DOC.url, "title": "Cluster overview", "excerpt": "Executors run tasks."}
    run = AgentRun(
        agent="repository_investigator",
        steps=[
            AgentStep(
                index=0,
                request=ToolRequest(tool="search_official_docs", arguments={"query": "x"}),
                result=ToolResult(data={"results": [hit, hit, {"url": "javascript:x"}]}),
            ),
            AgentStep(
                index=1,
                request=ToolRequest(tool="read_file", arguments={"path": "README.md"}),
                result=ToolResult(data={"results": [{"url": "https://elsewhere.example"}]}),
            ),
        ],
    )
    docs = retrieved_docs(run)
    assert [(d.url, d.title) for d in docs] == [(DOC.url, "Cluster overview")]
