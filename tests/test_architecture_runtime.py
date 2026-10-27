"""The runtime architecture, read from code -- and only what the code shows.

Fixtures are small repositories of different kinds, written to disk. Nothing in
the code model knows any of them: it finds the types the rest of the code uses,
the lines where one uses another, and builds components, links and flows from
those alone. Nemotron, faked here, may only name and label what was found.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.architecture.code_model import Corpus, build_code_model, code_only
from repolab.architecture.models import (
    ArchitectureGraph,
    CodeRelationType,
    Provenance,
    Subsystem,
)
from repolab.architecture.modules import module_dependencies
from repolab.architecture.runtime_synthesis import (
    ProposedComponent,
    ProposedFlow,
    ProposedLink,
    ProposedResponsibility,
    ProposedStep,
    RuntimeProposal,
    apply_proposal,
)
from repolab.architecture.verify import cited_paths, verify_graph
from repolab.evidence.models import CodeReference
from repolab.sources import CloneSnapshot, MemorySnapshot

COMMIT = "c" * 40

WEB = {
    "server/ApiServer.js": """
export class ApiServer {
  constructor() {
    this.userService = new UserService();
  }
  handle(request) {
    // UserRepository is mentioned in a comment only
    return this.userService.findUser(request.id);
  }
}
""",
    "server/UserService.js": """
export class UserService {
  constructor() {
    this.userRepository = new UserRepository();
  }
  findUser(id) {
    return this.userRepository.queryById(id);
  }
}
""",
    "server/UserRepository.js": """
export class UserRepository {
  queryById(id) {
    return db.select("SELECT * FROM users WHERE ApiServer = 1", id);
  }
}
""",
    "server/routes.js": """
import { ApiServer } from './ApiServer';
const server = new ApiServer();
server.handle({ id: 1 });
export const UserServiceName = 'UserService';
""",
    "client/app.js": "const s = new UserService(); const r = new UserRepository();\n",
}

SCHEDULER = {
    "core/Engine.scala": """package x

class EngineContext(conf: Conf) {
  private val jobScheduler = new JobScheduler(this)
  def runJob(job: Job): Unit = {
    jobScheduler.submitJob(job)
  }
}
""",
    "core/JobScheduler.scala": """package x

class JobScheduler(ctx: EngineContext) {
  private val taskRunner = new TaskRunner()
  def submitJob(job: Job): Unit = {
    val stages = job.split()
    taskRunner.launchTasks(stages)
  }
}
""",
    "core/TaskRunner.scala": """package x

class TaskRunner {
  def launchTasks(stages: Seq[Stage]): Unit = {
    stages.foreach(s => s.run())
  }
}
""",
    "core/DataType.scala": "package x\n\nclass DataType {\n}\n",
    "api/Client.scala": (
        "class Client { val c = new EngineContext(null); val s: JobScheduler = null; "
        "val t: TaskRunner = null; val d: DataType = null }\n"
    ),
    "api/Other.scala": (
        "object Other { val e: EngineContext = null; val j: JobScheduler = null; "
        "val r: TaskRunner = null; val d: DataType = null; val d2: DataType = null }\n"
    ),
}


def write(root: Path, files: dict[str, str]) -> list[str]:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return list(files)


def model(
    root: Path, files: dict[str, str], subsystems: list[Subsystem] | None = None
) -> ArchitectureGraph:
    paths = write(root, files)
    corpus = Corpus.read(root, COMMIT, paths)
    graph = ArchitectureGraph(subsystems=subsystems or [])
    return build_code_model(graph, corpus)


class TestKeyAbstractionsAndRelations:
    def test_a_web_app_reads_as_server_service_repository(self, tmp_path: Path) -> None:
        graph = model(tmp_path, WEB)
        names = {a.name for a in graph.abstractions}
        assert {"ApiServer", "UserService", "UserRepository"} <= names
        # Every definition is a verified range at this commit.
        for abstraction in graph.abstractions:
            ref = abstraction.definition
            assert ref.verified and ref.commit_sha == COMMIT and ref.symbol == abstraction.name
            assert ref.start_line <= ref.end_line

    def test_relations_come_from_code_lines_not_comments_or_strings(self, tmp_path: Path) -> None:
        graph = model(tmp_path, WEB)
        name = {a.id: a.name for a in graph.abstractions}
        pairs = {(name[r.source], name[r.target]): r for r in graph.relations}
        calls = pairs[("ApiServer", "UserService")]
        assert calls.type is CodeRelationType.CALLS
        assert "this.userService.findUser(" in calls.evidence.excerpt
        assert calls.evidence.verified and calls.evidence.start_line == 8
        # Named only in a comment, or only inside a string: no relation.
        assert ("ApiServer", "UserRepository") not in pairs
        assert ("UserRepository", "ApiServer") not in pairs

    def test_code_only_blanks_strings_and_comments_but_keeps_lines(self) -> None:
        text = 'a = "B(" # C(\nd = E(1)\n'
        cleaned = code_only(text, "x.py")
        assert cleaned.count("\n") == 2 and "B(" not in cleaned and "E(1)" in cleaned


class TestRuntimeModelAndFlows:
    def test_a_scheduler_chain_becomes_an_ordered_flow(self, tmp_path: Path) -> None:
        graph = model(tmp_path, SCHEDULER)
        components = {c.name: c for c in graph.runtime.components}
        assert {"EngineContext", "JobScheduler", "TaskRunner"} <= set(components)
        link = next(
            candidate
            for candidate in graph.runtime.links
            if candidate.source == components["JobScheduler"].id
            and candidate.target == components["TaskRunner"].id
        )
        assert link.type is CodeRelationType.CALLS
        assert "taskRunner.launchTasks(" in link.evidence[0].excerpt
        flow = graph.flows[0]
        assert [
            graph.runtime.components[
                [c.id for c in graph.runtime.components].index(s.component)
            ].name
            for s in flow.steps
        ] == [
            "EngineContext",
            "JobScheduler",
            "TaskRunner",
        ]
        assert flow.ordered is True
        assert all(step.references for step in flow.steps[:-1])

    def test_no_link_without_a_relation_in_the_code(self, tmp_path: Path) -> None:
        graph = model(tmp_path, SCHEDULER)
        ids = {c.name: c.id for c in graph.runtime.components}
        if "DataType" in ids:
            assert not any(
                ids["DataType"] in (link.source, link.target) for link in graph.runtime.links
            )

    def test_the_reading_path_follows_the_flow_and_cites_verified_ranges(
        self, tmp_path: Path
    ) -> None:
        graph = model(tmp_path, SCHEDULER)
        titles = [item.title for item in graph.reading_path]
        assert titles[:3] == ["EngineContext", "JobScheduler", "TaskRunner"]
        assert all(item.reference.verified for item in graph.reading_path)


class TestNemotronMayOnlyNameWhatWasFound:
    def graph(self, tmp_path: Path) -> tuple[ArchitectureGraph, dict[str, str]]:
        graph = model(tmp_path, SCHEDULER)
        aliases = {a.id: f"T{i + 1}" for i, a in enumerate(graph.abstractions)}
        return graph, aliases

    def alias(self, graph: ArchitectureGraph, aliases: dict[str, str], name: str) -> str:
        return aliases[next(a.id for a in graph.abstractions if a.name == name)]

    def test_names_descriptions_and_labels_are_kept_as_inferred(self, tmp_path: Path) -> None:
        graph, aliases = self.graph(tmp_path)
        a = lambda name: self.alias(graph, aliases, name)  # noqa: E731
        proposal = RuntimeProposal(
            components=[
                ProposedComponent(
                    name="Driver",
                    description="Coordinates jobs.",
                    members=[a("EngineContext")],
                    responsibilities=[
                        ProposedResponsibility(text="Runs jobs", member=a("EngineContext"))
                    ],
                ),
                ProposedComponent(
                    name="Scheduler",
                    description="Splits jobs.",
                    members=[a("JobScheduler")],
                    responsibilities=[
                        ProposedResponsibility(text="Not its member", member=a("TaskRunner"))
                    ],
                ),
                ProposedComponent(
                    name="Executor", description="Runs tasks.", members=[a("TaskRunner"), "T999"]
                ),
            ],
            links=[
                ProposedLink(source="Scheduler", target="Executor", label="task sets"),
                ProposedLink(source="Executor", target="Driver", label="invented"),
            ],
            flows=[
                ProposedFlow(
                    name="Running a job",
                    steps=[
                        ProposedStep(component="Driver", action="submits the job"),
                        ProposedStep(component="Scheduler", action="plans stages"),
                        ProposedStep(component="Executor", action="runs tasks"),
                    ],
                ),
                ProposedFlow(
                    name="Backwards",
                    steps=[
                        ProposedStep(component="Executor", action="x"),
                        ProposedStep(component="Driver", action="y"),
                    ],
                ),
            ],
        )
        graph = apply_proposal(graph, proposal, aliases)
        components = {c.name: c for c in graph.runtime.components}
        assert set(components) == {"Driver", "Scheduler", "Executor"}
        assert components["Driver"].provenance is Provenance.INFERRED
        assert [r.text for r in components["Driver"].responsibilities] == ["Runs jobs"]
        assert components["Driver"].responsibilities[0].reference.verified
        assert components["Scheduler"].responsibilities == []
        label = {(link.source, link.target): link.label for link in graph.runtime.links}
        assert label[(components["Scheduler"].id, components["Executor"].id)] == "task sets"
        assert (components["Executor"].id, components["Driver"].id) not in label
        assert graph.dropped["responsibility not tied to one of the component's members"] == 1
        assert graph.dropped["link label for components the code does not relate"] == 1
        # The forward flow is ordered; the backwards one has no hand-over and is dropped.
        assert [f.name for f in graph.flows] == ["Running a job"]
        assert graph.flows[0].ordered is True
        assert graph.dropped["flow with fewer than two verified steps"] == 1

    def test_a_proposal_of_unknown_types_changes_nothing(self, tmp_path: Path) -> None:
        graph, aliases = self.graph(tmp_path)
        before = [c.name for c in graph.runtime.components]
        proposal = RuntimeProposal(
            components=[ProposedComponent(name="Magic", description="", members=["T999"])]
        )
        graph = apply_proposal(graph, proposal, aliases)
        assert [c.name for c in graph.runtime.components] == before
        assert graph.dropped["runtime component without a verified member"] == 1


class TestVerification:
    def test_a_component_whose_file_is_gone_is_removed_with_its_links(self, tmp_path: Path) -> None:
        graph = model(tmp_path, SCHEDULER)
        files = {p: (tmp_path / p).read_text() for p in SCHEDULER}
        del files["core/TaskRunner.scala"]
        graph = verify_graph(graph, MemorySnapshot(commit_sha=COMMIT, files=files))
        assert "TaskRunner" not in {c.name for c in graph.runtime.components}
        assert graph.dropped["runtime component whose source could not be verified"] == 1

    def test_a_reference_from_another_commit_is_not_reresolved(self, tmp_path: Path) -> None:
        graph = model(tmp_path, SCHEDULER)
        graph.runtime.components[0].source_references[0] = CodeReference(
            path="core/Engine.scala", start_line=3, commit_sha="d" * 40, verified=True
        )
        snapshot = CloneSnapshot(root=tmp_path, commit_sha=COMMIT)
        graph = verify_graph(graph, snapshot)
        assert all(
            ref.commit_sha == COMMIT
            for c in graph.runtime.components
            for ref in c.source_references
        )

    def test_cited_paths_are_what_the_store_must_keep(self, tmp_path: Path) -> None:
        graph = model(tmp_path, SCHEDULER)
        paths = cited_paths(graph)
        assert {"core/Engine.scala", "core/JobScheduler.scala", "core/TaskRunner.scala"} <= set(
            paths
        )


class TestModules:
    def subsystems(self, *names: str) -> list[Subsystem]:
        return [Subsystem(id=f"sub:{n}", label=n, paths=[n], declared_by="test") for n in names]

    def test_maven_modules_depend_on_sibling_artifacts_but_not_test_scope(
        self, tmp_path: Path
    ) -> None:
        files = {
            "core/pom.xml": (
                "<project><parent><artifactId>root</artifactId></parent>\n"
                "<artifactId>app-core_2.13</artifactId>\n</project>\n"
            ),
            "sql/pom.xml": (
                "<project>\n<artifactId>app-sql_2.13</artifactId>\n<dependencies>\n"
                "<dependency>\n<artifactId>app-core_${scala.binary.version}</artifactId>\n</dependency>\n"
                "</dependencies>\n</project>\n"
            ),
            "ml/pom.xml": (
                "<project>\n<artifactId>app-ml</artifactId>\n<dependencies>\n"
                "<dependency>\n<artifactId>app-sql_2.13</artifactId>\n<scope>test</scope>\n</dependency>\n"
                "</dependencies>\n</project>\n"
            ),
        }
        paths = write(tmp_path, files)
        graph = ArchitectureGraph(subsystems=self.subsystems("core", "sql", "ml"))
        module_dependencies(graph, CloneSnapshot(root=tmp_path, commit_sha=COMMIT), paths)
        by_id = {s.id: s for s in graph.subsystems}
        sql = by_id["sub:sql"].depends_on
        assert [(d.target, d.via) for d in sql] == [("sub:core", "maven")]
        assert sql[0].evidence.verified and sql[0].evidence.start_line == 5
        assert by_id["sub:core"].used_by == ["sub:sql"]
        assert by_id["sub:ml"].depends_on == []

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            (
                {
                    "web/package.json": ('{"name": "@app/web", "dependencies": {"@app/api": "*"}}'),
                    "api/package.json": '{"name": "@app/api"}',
                },
                ("sub:web", "sub:api", "npm"),
            ),
            (
                {
                    "cli/Cargo.toml": '[dependencies]\ncore = { path = "../core" }\n',
                    "core/Cargo.toml": "[package]\n",
                },
                ("sub:cli", "sub:core", "cargo"),
            ),
            (
                {
                    "app/build.gradle": "dependencies {\n  implementation project(':lib')\n}\n",
                    "lib/build.gradle": "\n",
                },
                ("sub:app", "sub:lib", "gradle"),
            ),
        ],
    )
    def test_other_build_systems(self, tmp_path: Path, files, expected) -> None:
        paths = write(tmp_path, files)
        names = sorted({p.split("/")[0] for p in files})
        graph = ArchitectureGraph(subsystems=self.subsystems(*names))
        module_dependencies(graph, CloneSnapshot(root=tmp_path, commit_sha=COMMIT), paths)
        source, target, via = expected
        dependency = next(s for s in graph.subsystems if s.id == source).depends_on[0]
        assert (dependency.target, dependency.via) == (target, via)
        assert dependency.evidence.verified


def test_regrouped_components_never_leave_flows_pointing_at_old_ones(tmp_path: Path) -> None:
    """Nemotron may merge components; flows and the reading path must follow."""
    graph = model(tmp_path, SCHEDULER)
    aliases = {a.id: f"T{i + 1}" for i, a in enumerate(graph.abstractions)}
    by_name = {a.name: aliases[a.id] for a in graph.abstractions}
    proposal = RuntimeProposal(
        components=[
            ProposedComponent(name="Driver", description="", members=[by_name["EngineContext"]]),
            ProposedComponent(name="Scheduler", description="", members=[by_name["JobScheduler"]]),
            ProposedComponent(name="Executor", description="", members=[by_name["TaskRunner"]]),
        ],
        flows=[],  # none proposed, or none that held
    )
    graph = apply_proposal(graph, proposal, aliases)
    ids = {c.id for c in graph.runtime.components}
    assert graph.flows, "the code's own chains are used when no proposed flow holds"
    assert all(step.component in ids for flow in graph.flows for step in flow.steps)
    assert all(item.component in ids for item in graph.reading_path if item.component)
    ArchitectureGraph.model_validate(graph.model_dump())  # still a consistent graph


def test_a_field_named_like_a_type_is_not_a_call_on_it_unless_declared_so(tmp_path: Path) -> None:
    files = {
        "core/Store.scala": (
            "class BlockStore(resolver: ShuffleResolver) {\n"
            "  def read(): Unit = {\n"
            "    resolver.getChecksumFile(1)\n"
            "  }\n"
            "}\n"
        ),
        "core/Resolver.scala": "class Resolver {\n  def resolve(): Unit = {}\n}\n",
        "core/ShuffleResolver.scala": (
            "class ShuffleResolver {\n  def getChecksumFile(i: Int) = i\n}\n"
        ),
        "core/Use.scala": (
            "class Use { val a: BlockStore = null; val b: Resolver = null; "
            "val c: ShuffleResolver = null }\n"
        ),
        "core/Use2.scala": (
            "class Use2 { val a: BlockStore = null; val b: Resolver = null; "
            "val c: ShuffleResolver = null }\n"
        ),
    }
    graph = model(tmp_path, files)
    name = {a.id: a.name for a in graph.abstractions}
    calls = {
        (name[r.source], name[r.target])
        for r in graph.relations
        if r.type is CodeRelationType.CALLS
    }
    assert ("BlockStore", "ShuffleResolver") in calls  # declared `resolver: ShuffleResolver`
    assert ("BlockStore", "Resolver") not in calls  # only the *name* matches


def test_a_function_based_pipeline_uses_its_detected_steps(tmp_path: Path) -> None:
    from repolab.architecture.models import (
        ArchitectureEdge,
        ArchitectureFlow,
        ArchitectureNode,
        EdgeType,
        NodeCategory,
    )

    def ref(line: int) -> CodeReference:
        return CodeReference(
            path="jobs/etl.py", start_line=line, end_line=line, commit_sha=COMMIT, verified=True
        )

    graph = ArchitectureGraph(
        nodes=[
            ArchitectureNode(
                id="extract",
                label="Extract",
                category=NodeCategory.INPUT,
                source_references=[ref(5)],
                learning_importance=3,
            ),
            ArchitectureNode(
                id="transform",
                label="Transform",
                category=NodeCategory.TRANSFORM,
                source_references=[ref(9)],
                learning_importance=3,
            ),
            ArchitectureNode(
                id="load",
                label="Load",
                category=NodeCategory.OUTPUT,
                source_references=[ref(13)],
                learning_importance=2,
            ),
        ],
        edges=[
            ArchitectureEdge(
                source="extract", target="transform", type=EdgeType.DATA_FLOW, evidence=[ref(20)]
            ),
            ArchitectureEdge(
                source="transform", target="load", type=EdgeType.DATA_FLOW, evidence=[ref(21)]
            ),
        ],
        primary_flows=[ArchitectureFlow(name="ETL", node_ids=["extract", "transform", "load"])],
    )
    corpus = Corpus.from_texts(COMMIT, {"jobs/etl.py": "def extract():\n    pass\n"})
    graph = build_code_model(graph, corpus)
    assert [c.name for c in graph.runtime.components] == ["Extract", "Transform", "Load"]
    flow = graph.flows[0]
    assert flow.name == "ETL" and flow.ordered is True
    assert [s.references[0].start_line for s in flow.steps[:-1]] == [20, 21]


def test_the_reading_path_is_built_from_references_once_they_are_verified() -> None:
    from repolab.architecture.code_model import reading_path, runtime_from_components
    from repolab.architecture.models import ArchitectureNode, NodeCategory

    text = "def main():\n    run()\n\ndef run():\n    pass\n"
    detector = CodeReference(path="jobs/etl.py", start_line=1, end_line=1)  # not yet verified
    graph = ArchitectureGraph(
        nodes=[
            ArchitectureNode(
                id=f"n{i}",
                label=label,
                category=NodeCategory.PIPELINE,
                source_references=[detector],
            )
            for i, label in enumerate(["Main", "Run", "Write"])
        ],
        entry_points=["n0"],
    )
    graph.runtime = runtime_from_components(graph)
    assert reading_path(graph) == []  # nothing is recommended before it is checked
    graph = verify_graph(graph, MemorySnapshot(commit_sha=COMMIT, files={"jobs/etl.py": text}))
    items = reading_path(graph)
    assert items and items[0].why == "Execution starts here."
    assert items[0].reference.verified and items[0].reference.commit_sha == COMMIT


def test_a_proposal_with_sections_misplaced_into_components_is_repaired() -> None:
    raw = {
        "components": [
            {"name": "Driver", "members": ["T1"]},
            'links": [{"source": "Driver", "target": "Executor", "label": "tasks"}], '
            '"flows": [{"name": "Job", "steps": [{"component": "Driver"}]}]',
        ]
    }
    proposal = RuntimeProposal.model_validate(raw)
    assert [c.name for c in proposal.components] == ["Driver"]
    assert proposal.links[0].label == "tasks" and proposal.flows[0].name == "Job"
