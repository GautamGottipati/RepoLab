"""Structured answers: laid out for a beginner, and still only what the evidence shows.

Offline, against a small web-and-database checkout and an architecture graph
built by hand. Super is scripted. What matters:

- each explanation shape keeps the parts a reader needs;
- an arrow is drawn only when a file or a cited relationship shows it -- an
  unsupported arrow is listed as not established, never drawn;
- every source chip, code-map entry and comparison cell points at something
  that exists;
- a measured value needs a recorded run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.architecture.models import (
    ArchitectureEdge,
    ArchitectureGraph,
    ArchitectureNode,
    EdgeType,
    NodeCategory,
    Provenance,
    SystemConcept,
)
from repolab.architecture.runtime import MeasuredValue, RuntimeOverlay
from repolab.evidence.models import CodeReference
from repolab.questions.answer import AnswerAgent
from repolab.questions.context import MAX_SLICE_NODES, architecture_slice, edge_id
from repolab.questions.layout import NOT_ESTABLISHED
from repolab.questions.models import (
    AnswerClaim,
    AnswerClaimKind,
    AnswerComparison,
    AnswerLocation,
    AnswerResult,
    AnswerSection,
    AnswerStep,
    AnswerVisual,
    ArchitectureFocus,
    ComparisonRow,
    ExplanationShape,
    GroundedAnswer,
    QuestionKind,
    SectionBasis,
    VisualEdge,
    VisualNode,
)
from repolab.questions.triage import explanation_shape
from tests.test_question_answer import Super

REPO = "https://github.com/example/events"
SHA = "a" * 40


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "events"
    (root / "backend/models").mkdir(parents=True)
    (root / "backend/routes").mkdir(parents=True)
    (root / "README.md").write_text("# Events\n\nAn events board with a REST backend.\n")
    (root / "backend/app.js").write_text(
        "const express = require('express');\n"
        "const mongoose = require('mongoose');\n"
        "const app = express();\n"
        "mongoose.connect(process.env.MONGO_URL);\n"
        "app.use('/api/posts', require('./routes/posts'));\n"
        "app.listen(3000);\n"
    )
    (root / "backend/routes/posts.js").write_text(
        "const Post = require('../models/post');\n"
        "router.get('/', async (req, res) => res.json(await Post.find()));\n"
    )
    (root / "backend/models/post.js").write_text(
        "const PostSchema = new mongoose.Schema({ title: String });\n"
        "module.exports = mongoose.model('Post', PostSchema);\n"
    )
    (root / "backend/models/user.js").write_text(
        "module.exports = mongoose.model('User', new mongoose.Schema({}));\n"
    )
    return root


def ref(path: str, line: int, excerpt: str = "") -> CodeReference:
    return CodeReference(path=path, start_line=line, commit_sha=SHA, excerpt=excerpt or None)


def graph() -> ArchitectureGraph:
    nodes = [
        ArchitectureNode(
            id="service:express",
            label="Express server",
            category=NodeCategory.SERVICE,
            technology="Express",
            source_references=[ref("backend/app.js", 3, "const app = express();")],
        ),
        ArchitectureNode(
            id="api:posts",
            label="Posts API",
            category=NodeCategory.API,
            properties={"routes": ["GET /api/posts"]},
            source_references=[ref("backend/routes/posts.js", 2)],
        ),
        ArchitectureNode(
            id="model:post",
            label="Post model",
            category=NodeCategory.MODEL,
            technology="Mongoose",
            source_references=[ref("backend/models/post.js", 2)],
        ),
        ArchitectureNode(
            id="db:mongo",
            label="MongoDB",
            category=NodeCategory.DATABASE,
            technology="MongoDB",
            source_references=[
                ref("backend/app.js", 4, "mongoose.connect(process.env.MONGO_URL);")
            ],
        ),
        ArchitectureNode(id="cli:seed", label="Seed script", category=NodeCategory.CLI),
    ]
    edges = [
        ArchitectureEdge(
            source="service:express",
            target="api:posts",
            type=EdgeType.ROUTES_TO,
            evidence=[ref("backend/app.js", 5)],
        ),
        ArchitectureEdge(
            source="api:posts",
            target="model:post",
            type=EdgeType.CALLS,
            evidence=[ref("backend/routes/posts.js", 1)],
        ),
        ArchitectureEdge(
            source="model:post",
            target="db:mongo",
            type=EdgeType.WRITES_TO,
            provenance=Provenance.INFERRED,
        ),
    ]
    return ArchitectureGraph(nodes=nodes, edges=edges)


def box(node_id: str, label: str, component: str | None = None) -> VisualNode:
    return VisualNode(id=node_id, label=label, component_id=component)


def arrow(source: str, target: str, support: str) -> VisualEdge:
    return VisualEdge(source=source, target=target, support=support)


def answer(**fields: object) -> GroundedAnswer:
    base: dict[str, object] = {
        "answer": "The APIs read and write their data through Mongoose models.",
        "summary": "Each API uses a Mongoose model to store its data in MongoDB.",
        "claims": [
            AnswerClaim(
                kind=AnswerClaimKind.REPOSITORY,
                statement="The server connects to MongoDB through Mongoose.",
                support="backend/app.js",
            )
        ],
        "caveats": [],
    }
    base.update(fields)
    return GroundedAnswer.model_validate(base)


def agent(
    checkout: Path,
    router: Super | None,
    *,
    architecture: ArchitectureGraph | None = None,
    overlay: RuntimeOverlay | None = None,
    focus: ArchitectureFocus | None = None,
) -> AnswerAgent:
    return AnswerAgent(
        repo_url=REPO,
        commit_sha=SHA,
        repository_root=checkout,
        router=router,
        architecture=architecture if architecture is not None else graph(),
        overlay=overlay,
        focus=focus,
    )


async def ask(
    checkout: Path,
    produced: GroundedAnswer,
    question: str = "How do the Posts API and the User API use MongoDB?",
    **kwargs: object,
) -> tuple[AnswerResult, Super]:
    router = Super(produced)
    result = await agent(checkout, router, **kwargs).answer(  # type: ignore[arg-type]
        question, kind=QuestionKind.REPOSITORY, shape=explanation_shape(question)
    )
    return result, router


FLOW = AnswerVisual(
    nodes=[
        box("express", "Express", "service:express"),
        box("posts", "Posts API", "api:posts"),
        box("model", "Post model", "model:post"),
        box("db", "MongoDB", "db:mongo"),
    ],
    edges=[
        arrow("express", "posts", "edge:service:express->api:posts"),
        arrow("posts", "model", "backend/routes/posts.js:1"),
        arrow("model", "db", "backend/models/post.js"),
    ],
)


class TestFlow:
    async def test_an_evidenced_flow_is_drawn_as_shown(self, checkout: Path) -> None:
        result, _ = await ask(checkout, answer(visual=FLOW.model_copy(deep=True)))
        assert result.shape is ExplanationShape.FLOW
        visual = result.answer.answer.visual
        assert visual is not None
        assert [n.label for n in visual.nodes] == ["Express", "Posts API", "Post model", "MongoDB"]
        assert {e.provenance for e in visual.edges} == {"static"}
        assert result.answer.not_established == []
        assert result.answer.grounded

    async def test_an_unsupported_arrow_is_not_drawn_and_is_said_so(self, checkout: Path) -> None:
        visual = FLOW.model_copy(deep=True)
        visual.nodes.append(box("users", "User API"))
        visual.edges.append(arrow("users", "db", "backend/routes/users.js"))
        result, _ = await ask(checkout, answer(visual=visual))
        drawn = result.answer.answer.visual
        assert drawn is not None
        assert "User API" not in [n.label for n in drawn.nodes]
        assert result.answer.not_established == ["User API → MongoDB"]
        assert result.answer.dropped["relationship without evidence"] == 1

    async def test_a_relationship_cited_with_its_whole_map_line_counts(
        self, checkout: Path
    ) -> None:
        visual = FLOW.model_copy(deep=True)
        visual.edges[0] = arrow(
            "express", "posts", "edge:service:express->api:posts (routes_to, static)"
        )
        result, _ = await ask(checkout, answer(visual=visual))
        assert result.answer.not_established == []

    async def test_a_cited_relationship_must_join_the_boxes_it_is_drawn_between(
        self, checkout: Path
    ) -> None:
        visual = FLOW.model_copy(deep=True)
        visual.edges[2] = arrow("model", "db", "edge:service:express->api:posts")
        result, _ = await ask(checkout, answer(visual=visual))
        assert result.answer.not_established == ["Post model → MongoDB"]

    async def test_an_inference_is_drawn_dashed_not_as_fact(self, checkout: Path) -> None:
        visual = FLOW.model_copy(deep=True)
        visual.edges[2] = arrow("model", "db", "inferred: Mongoose models persist to it")
        result, _ = await ask(checkout, answer(visual=visual))
        edges = {(e.source, e.target): e.provenance for e in result.answer.answer.visual.edges}
        assert edges[("model", "db")] == "inferred"
        # The analysis' own inferred relationship stays inferred too.
        visual.edges[2] = arrow("model", "db", "edge:model:post->db:mongo")
        result, _ = await ask(checkout, answer(visual=visual))
        edges = {(e.source, e.target): e.provenance for e in result.answer.answer.visual.edges}
        assert edges[("model", "db")] == "inferred"

    async def test_no_complete_looking_flow_without_evidence(self, checkout: Path) -> None:
        invented = AnswerVisual(
            nodes=[box("a", "Browser"), box("b", "Gateway"), box("c", "Queue")],
            edges=[arrow("a", "b", "src/gateway.ts"), arrow("b", "c", "edge:x->y")],
        )
        result, _ = await ask(checkout, answer(visual=invented))
        assert result.answer.answer.visual is None
        assert result.answer.not_established == ["Browser → Gateway", "Gateway → Queue"]

    async def test_a_diagram_of_mostly_guesses_is_not_drawn(self, checkout: Path) -> None:
        guessed = AnswerVisual(
            nodes=[box("a", "Express"), box("b", "Cache"), box("c", "Queue"), box("d", "DB")],
            edges=[
                arrow("a", "b", "inferred"),
                arrow("b", "c", "inferred"),
                arrow("c", "d", "backend/app.js"),
            ],
        )
        result, _ = await ask(checkout, answer(visual=guessed))
        assert result.answer.answer.visual is None
        assert result.answer.dropped["diagram without enough evidence"] == 1


class TestShapes:
    async def test_execution_steps_keep_only_real_sources(self, checkout: Path) -> None:
        steps = [
            AnswerStep(
                title="Request arrives",
                description="Express routes it.",
                sources=["backend/app.js:5"],
            ),
            AnswerStep(
                title="Model query",
                description="The route asks the Post model.",
                sources=["backend/routes/posts.js", "backend/routes/missing.js"],
            ),
            AnswerStep(title="Response", description="JSON goes back.", sources=[]),
        ]
        result, _ = await ask(
            checkout, answer(steps=steps), question="What happens when I call GET /api/posts?"
        )
        assert result.shape is ExplanationShape.EXECUTION
        kept = result.answer.answer.steps
        assert [s.title for s in kept] == ["Request arrives", "Model query", "Response"]
        assert kept[1].sources == ["backend/routes/posts.js"]
        assert result.answer.dropped["citation of a file or page that does not exist"] == 1

    async def test_definition_sections_are_honest_about_their_basis(self, checkout: Path) -> None:
        sections = [
            AnswerSection(
                key="what",
                title="What it is",
                body="A schema-based layer.",
                basis=SectionBasis.EVIDENCED,
            ),
            AnswerSection(
                key="where",
                title="Where",
                body="In the models.",
                sources=["backend/models/post.js"],
            ),
        ]
        result, _ = await ask(checkout, answer(sections=sections), question="What is Mongoose?")
        assert result.shape is ExplanationShape.DEFINITION
        what, where = result.answer.answer.sections
        assert what.basis is SectionBasis.INFERRED  # "evidenced" with nothing to open
        assert where.basis is SectionBasis.EVIDENCED

    async def test_why_keeps_unknown_design_intent_unknown(self, checkout: Path) -> None:
        sections = [
            AnswerSection(
                key="choice",
                title="Design choice",
                body="It uses MongoDB.",
                sources=["backend/app.js"],
            ),
            AnswerSection(
                key="helps",
                title="Why it helps",
                body="Nothing says why.",
                basis=SectionBasis.UNKNOWN,
            ),
        ]
        result, _ = await ask(
            checkout, answer(sections=sections), question="Why does this repository use MongoDB?"
        )
        assert result.shape is ExplanationShape.WHY
        assert [s.basis for s in result.answer.answer.sections] == [
            SectionBasis.EVIDENCED,
            SectionBasis.UNKNOWN,
        ]

    async def test_comparison_cells_without_evidence_are_not_established(
        self, checkout: Path
    ) -> None:
        comparison = AnswerComparison(
            subjects=["Post", "User"],
            rows=[
                ComparisonRow(
                    attribute="Defined in",
                    values=["post.js", "user.js"],
                    support="backend/models/user.js",
                ),
                ComparisonRow(
                    attribute="Indexed by",
                    values=["title", "email"],
                    support="backend/models/indexes.js",
                ),
                ComparisonRow(
                    attribute="Used by", values=["Posts API", "Users API"], support="inferred"
                ),
            ],
        )
        result, _ = await ask(
            checkout,
            answer(comparison=comparison),
            question="What is the difference between the Post model and the User model?",
        )
        assert result.shape is ExplanationShape.COMPARISON
        rows = result.answer.answer.comparison.rows
        assert [r.provenance for r in rows] == ["static", "unknown", "inferred"]
        assert rows[1].values == [NOT_ESTABLISHED, NOT_ESTABLISHED]

    async def test_a_code_map_points_only_at_real_paths(self, checkout: Path) -> None:
        locations = [
            AnswerLocation(role="Entry", path="backend/app.js", line=5),
            AnswerLocation(role="Persistence", path="backend/models/"),
            AnswerLocation(role="Validation", path="backend/middleware/auth.js"),
            AnswerLocation(role="Route", path="backend/routes/posts.js", line=900),
        ]
        result, _ = await ask(
            checkout, answer(locations=locations), question="Where is MongoDB configured?"
        )
        assert result.shape is ExplanationShape.LOCATION
        kept = [(loc.role, loc.path, loc.line) for loc in result.answer.answer.locations]
        assert kept == [
            ("Entry", "backend/app.js", 5),
            ("Persistence", "backend/models/", None),
            ("Route", "backend/routes/posts.js", None),
        ]

    async def test_general_answers_get_follow_ups_from_the_architecture(
        self, checkout: Path
    ) -> None:
        result, _ = await ask(
            checkout,
            answer(follow_ups=["Show me the Post model", "x"]),
            question="Tell me about the Posts API",
        )
        assert result.shape is ExplanationShape.GENERAL
        assert result.follow_ups[0] == "Show me the Post model"
        assert "What happens when a request reaches GET /api/posts?" in result.follow_ups
        assert len(result.follow_ups) <= 4

    async def test_the_rules_shape_stands_and_the_model_fills_in_otherwise(
        self, checkout: Path
    ) -> None:
        produced = answer(shape=ExplanationShape.COMPARISON)
        result, router = await ask(checkout, produced.model_copy(deep=True))
        assert result.shape is ExplanationShape.FLOW
        assert "Answer shape: flow:" in router.prompts[0]

        result, router = await ask(
            checkout, produced.model_copy(deep=True), question="Tell me about the Posts API"
        )
        assert result.shape is ExplanationShape.COMPARISON
        assert "set 'shape' to it" in router.prompts[0]


class TestMeasurements:
    async def test_a_measured_claim_needs_a_recorded_run(self, checkout: Path) -> None:
        claim = AnswerClaim(
            kind=AnswerClaimKind.MEASURED, statement="Queries take a while.", support="query time"
        )
        result, _ = await ask(checkout, answer(claims=[claim]))
        assert not result.answer.grounded
        assert "no run of this repository was recorded" in result.answer.unsupported[0].problem

    async def test_an_invented_number_is_flagged_but_a_quoted_one_is_not(
        self, checkout: Path
    ) -> None:
        result, _ = await ask(checkout, answer(summary="Each request takes about 40 ms."))
        assert any("40 ms" in u.problem for u in result.answer.unsupported)

        (checkout / "backend/app.js").write_text("server.timeout = 30 seconds;\n")
        result, _ = await ask(
            checkout,
            answer(summary="The server times out after 30 seconds."),
            question="How does the Express server use backend/app.js timeouts?",
        )
        assert not any("30 seconds" in u.problem for u in result.answer.unsupported)

    async def test_recorded_measurements_become_runtime_evidence(self, checkout: Path) -> None:
        overlay = RuntimeOverlay(
            measurements={
                "api:posts": [
                    MeasuredValue(
                        name="statements run",
                        value=3,
                        unit="statements",
                        source="cli experiment e1",
                    )
                ]
            }
        )
        claim = AnswerClaim(
            kind=AnswerClaimKind.MEASURED,
            statement="Three statements ran.",
            support="statements run",
        )
        result, router = await ask(checkout, answer(claims=[claim]), overlay=overlay)
        assert [m.name for m in result.evidence.measurements] == ["Posts API · statements run"]
        assert result.answer.grounded
        assert "Posts API · statements run: 3 statements" in router.prompts[0]


class TestArchitectureContext:
    def test_the_slice_is_the_named_parts_and_their_neighbours(self) -> None:
        selected = architecture_slice(graph(), "How does the Posts API use MongoDB?")
        ids = selected.node_ids()
        assert {"api:posts", "db:mongo"} <= ids
        assert "cli:seed" not in ids
        # Cited relationships first; the analysis' own inference last.
        assert edge_id(selected.edges[-1]) == "edge:model:post->db:mongo"

    def test_the_slice_is_bounded(self) -> None:
        big = graph()
        for i in range(40):
            big.nodes.append(
                ArchitectureNode(
                    id=f"api:posts{i}", label=f"Posts API {i}", category=NodeCategory.API
                )
            )
        assert len(architecture_slice(big, "posts api").nodes) == MAX_SLICE_NODES

    def test_a_focus_concept_brings_its_members(self) -> None:
        g = graph()
        g.system_model.concepts.append(
            SystemConcept(
                id="concept:storage", label="Storage", member_ids=["model:post", "db:mongo"]
            )
        )
        focus = ArchitectureFocus(node_id="concept:storage", label="Storage", category="concept")
        assert {"model:post", "db:mongo"} <= architecture_slice(g, "?", focus=focus).node_ids()

    async def test_the_map_reaches_the_prompt_and_its_lines_join_the_evidence(
        self, checkout: Path
    ) -> None:
        result, router = await ask(checkout, answer())
        prompt = router.prompts[0]
        assert "component api:posts: Posts API (api)" in prompt
        assert "edge:api:posts->model:post (calls, static)" in prompt
        assert ("backend/app.js", 4) in {
            (r.path, r.start_line) for r in result.evidence.code_references
        }

    async def test_no_model_still_returns_evidence_and_architecture_follow_ups(
        self, checkout: Path
    ) -> None:
        result = await agent(checkout, None).answer(
            "How does the Posts API use MongoDB?", kind=QuestionKind.REPOSITORY
        )
        assert result.answer is None
        assert result.follow_ups
        assert result.evidence.code_references


def test_an_answer_stored_before_the_layout_still_loads() -> None:
    stored = {
        "question": "What is Cassandra?",
        "kind": "conceptual",
        "answer": {
            "answer": {"answer": "A database.", "claims": [], "caveats": []},
            "unsupported": [],
            "checked_claims": 0,
            "grounded": True,
        },
        "evidence": {},
        "findings": [],
    }
    result = AnswerResult.model_validate(stored)
    assert result.shape is ExplanationShape.GENERAL
    assert result.answer.answer.summary == ""
    assert result.answer.answer.visual is None
    assert result.follow_ups == []


class TestReadingTheNextHop:
    def test_local_imports_resolve_against_the_real_tree(self) -> None:
        from repolab.questions.investigation import local_imports

        paths = {
            "backend/routes/posts.js",
            "backend/models/post.js",
            "web/src/api/index.ts",
            "pkg/models.py",
            "pkg/views.py",
        }
        js = ["const Post = require('../models/post');", "const express = require('express');"]
        assert local_imports("backend/routes/posts.js", js, paths) == ["backend/models/post.js"]
        es = ["import client from './api';", "import React from 'react';"]
        assert local_imports("web/src/app.ts", es, paths) == ["web/src/api/index.ts"]
        py = ["from .models import Post", "import os"]
        assert local_imports("pkg/views.py", py, paths) == ["pkg/models.py"]
        assert local_imports("pkg/views.py", ["from .missing import X"], paths) == []

    async def test_the_investigation_reads_what_the_read_files_import(self, checkout: Path) -> None:
        result, router = await ask(
            checkout, answer(), question="How does the posts route reach the database?"
        )
        read = [ref.path for ref in result.evidence.code_references]
        assert "backend/models/post.js" in read
        assert "const PostSchema" in router.prompts[0]


class TestCitations:
    async def test_a_real_path_wrapped_in_words_is_kept_and_normalised(
        self, checkout: Path
    ) -> None:
        steps = [
            AnswerStep(
                title="Mount",
                description="The server mounts the route.",
                sources=["backend/app.js (lines 5-6)", "inferred", "see the routes"],
            )
        ]
        result, _ = await ask(checkout, answer(steps=steps))
        assert result.answer.answer.steps[0].sources == ["backend/app.js:5"]
        # "inferred" is honest, not a missing file; only the vague one is counted.
        assert result.answer.dropped == {"citation of a file or page that does not exist": 1}
