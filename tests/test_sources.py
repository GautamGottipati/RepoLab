"""Source references RepoLab can stand behind.

Line numbers come from the snapshot, never from a model: a definition's range
is computed, re-read and checked, a wrong hint is corrected or rejected, and a
reference is only ever resolved against the commit it was made at.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repolab.evidence.models import EvidenceStrength
from repolab.sources import (
    MemorySnapshot,
    StoredSnapshot,
    SymbolIndex,
    capture,
    parse_citation,
    read,
    scan,
    strength_of,
    verify_reference,
)

COMMIT = "a" * 40
OTHER = "b" * 40

SCHEDULER = """package org.example.scheduler

import org.example.SparkContext

/** Turns jobs into stages. A } in a comment is not a brace. */
private[example] class DAGScheduler(
    sc: SparkContext,
    taskScheduler: TaskScheduler)
  extends Logging {

  private val label = "stage { not a block"

  def submitJob(rdd: RDD[_]): JobWaiter = {
    val waiter = new JobWaiter()
    taskScheduler.submitTasks(new TaskSet())
    waiter
  }

  def stageCount: Int = 3
}

case class JobSubmitted(jobId: Int)

object DAGScheduler {
  val DEFAULT = 1
}
"""

TASKS = """class TaskScheduler:
    \"\"\"Hands task sets to executors.\"\"\"

    def submit_tasks(self, task_set):
        for task in task_set:
            self.launch(task)

    def launch(self, task):
        return task


def helper():
    return 1
"""


def snapshot() -> MemorySnapshot:
    return MemorySnapshot(
        commit_sha=COMMIT,
        files={
            "core/DAGScheduler.scala": SCHEDULER,
            "py/tasks.py": TASKS,
            "pom.xml": "<project><modules><module>core</module></modules></project>\n",
        },
    )


def index() -> SymbolIndex:
    return SymbolIndex.build(snapshot().files.items())


# -- symbols -----------------------------------------------------------------


class TestSymbols:
    def test_a_scala_class_spans_its_body_not_its_neighbours(self) -> None:
        found = {(s.name, s.kind): s for s in scan("core/DAGScheduler.scala", SCHEDULER).symbols}
        cls = found[("DAGScheduler", "class")]
        assert (cls.start_line, cls.end_line) == (6, 20)
        method = found[("submitJob", "def")]
        assert (method.start_line, method.end_line, method.container) == (13, 17, "DAGScheduler")
        # A body-less case class ends on its own line, not at the next block.
        case = found[("JobSubmitted", "class")]
        assert case.end_line == case.start_line == 22
        assert (
            found[("DAGScheduler", "object")].start_line,
            found[("DAGScheduler", "object")].end_line,
        ) == (24, 26)

    def test_python_blocks_end_by_indentation(self) -> None:
        found = {s.name: s for s in scan("py/tasks.py", TASKS).symbols}
        assert (found["TaskScheduler"].start_line, found["TaskScheduler"].end_line) == (1, 9)
        assert found["launch"].container == "TaskScheduler"
        assert (found["helper"].start_line, found["helper"].end_line) == (12, 13)

    @pytest.mark.parametrize(
        ("path", "text", "name", "span"),
        [
            (
                "A.java",
                'public class A {\n  public void run() {\n    String s = "}";\n  }\n}\n',
                "run",
                (2, 4),
            ),
            ("a.ts", "export class Api {\n  get() { return `x ${y} }` }\n}\n", "Api", (1, 3)),
            ("a.go", "func (s *S) Serve() {\n\tif x { }\n}\n", "Serve", (1, 3)),
            (
                "a.rs",
                "impl<'a> Ctx<'a> {\n    fn new() -> Self {\n        let c = '}';\n    }\n}\n",
                "new",
                (2, 4),
            ),
            ("A.kt", "class A {\n    fun go(): Int {\n        return 1\n    }\n}\n", "go", (2, 4)),
        ],
    )
    def test_each_language(self, path, text, name, span) -> None:
        found = {s.name: s for s in scan(path, text).symbols}
        assert (found[name].start_line, found[name].end_line) == span

    def test_unknown_languages_have_no_symbols(self) -> None:
        assert scan("README.md", "# class Foo {}") is None


# -- the verifier ----------------------------------------------------------------


class TestVerify:
    def test_a_valid_file_and_symbol_gives_the_computed_range(self) -> None:
        ref = verify_reference(
            snapshot(), "core/DAGScheduler.scala", symbol="DAGScheduler", index=index()
        )
        assert ref.verified
        assert (ref.start_line, ref.end_line, ref.symbol, ref.commit_sha) == (
            6,
            20,
            "DAGScheduler",
            COMMIT,
        )
        assert "class DAGScheduler" in ref.excerpt
        assert ref.strength is EvidenceStrength.DIRECT_SOURCE

    def test_a_models_wrong_line_is_corrected_not_trusted(self) -> None:
        ref = verify_reference(snapshot(), "core/DAGScheduler.scala", symbol="submitJob", line=2)
        assert ref.verified and (ref.start_line, ref.end_line) == (13, 17)
        assert ref.verification == "corrected from line 2"

    def test_the_hint_picks_between_same_named_definitions(self) -> None:
        ref = verify_reference(
            snapshot(), "core/DAGScheduler.scala", symbol="DAGScheduler", line=25
        )
        assert ref.verified and ref.start_line == 24

    def test_a_wrong_file_fails(self) -> None:
        ref = verify_reference(
            snapshot(), "core/Missing.scala", symbol="NoSuchThing", index=index()
        )
        assert not ref.verified and ref.verification == "file not in the investigated snapshot"
        assert ref.start_line is None

    def test_a_wrong_file_with_a_unique_symbol_resolves_to_where_it_is(self) -> None:
        ref = verify_reference(
            snapshot(), "core/Wrong.scala", symbol="TaskScheduler", index=index()
        )
        assert ref.verified and ref.path == "py/tasks.py" and ref.start_line == 1
        assert "not core/Wrong.scala" in ref.verification

    def test_a_missing_symbol_fails(self) -> None:
        ref = verify_reference(snapshot(), "core/DAGScheduler.scala", symbol="BlockManager")
        assert not ref.verified and "no definition of BlockManager" in ref.verification

    def test_an_invalid_line_range_fails(self) -> None:
        ref = verify_reference(snapshot(), "py/tasks.py", line=400)
        assert not ref.verified and "outside this file" in ref.verification

    def test_a_line_without_its_anchor_is_rejected(self) -> None:
        ref = verify_reference(snapshot(), "py/tasks.py", line=12, expect="launch_everything")
        assert not ref.verified and ref.start_line is None

    def test_an_anchor_near_the_hinted_line_is_moved_to(self) -> None:
        ref = verify_reference(snapshot(), "py/tasks.py", line=4, expect="self.launch(task)")
        assert ref.verified and ref.start_line == 6 and ref.verification == "corrected from line 4"

    def test_a_reference_from_another_commit_is_never_resolved_here(self) -> None:
        ref = verify_reference(
            snapshot(), "core/DAGScheduler.scala", symbol="DAGScheduler", commit_sha=OTHER
        )
        assert not ref.verified and ref.commit_sha == OTHER
        assert "snapshot is" in ref.verification

    def test_a_deleted_file_fails_gracefully(self) -> None:
        shot = snapshot()
        del shot.files["py/tasks.py"]
        ref = verify_reference(shot, "py/tasks.py", symbol="TaskScheduler")
        assert not ref.verified

    def test_an_ambiguous_symbol_is_not_guessed(self) -> None:
        text = (
            "class A:\n    def run(self):\n        pass\n\n"
            "class B:\n    def run(self):\n        pass\n"
        )
        shot = MemorySnapshot(commit_sha=COMMIT, files={"m.py": text})
        ref = verify_reference(shot, "m.py", symbol="run")
        assert not ref.verified and "more than once" in ref.verification

    def test_strength_follows_the_kind_of_file(self) -> None:
        assert strength_of("pom.xml") is EvidenceStrength.BUILD_METADATA
        assert strength_of("tests/test_x.py") is EvidenceStrength.TEST
        assert strength_of("docs/arch.md") is EvidenceStrength.IN_REPO_DOCUMENTATION
        assert strength_of("conf/app.yaml") is EvidenceStrength.CONFIGURATION

    @pytest.mark.parametrize(
        ("text", "parsed"),
        [
            ("a/b.py:12", ("a/b.py", 12, None)),
            ("a/b.py#Foo", ("a/b.py", None, "Foo")),
            ("a/b.scala:DAGScheduler", ("a/b.scala", None, "DAGScheduler")),
            ("a/b.py:3-9", ("a/b.py", 3, None)),
            ("a/b.py", ("a/b.py", None, None)),
        ],
    )
    def test_citations_parse(self, text, parsed) -> None:
        assert parse_citation(text) == parsed


# -- the store ------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    from repolab.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


class TestStore:
    def test_captured_files_read_back_at_exactly_their_commit(self, session) -> None:
        report = capture(
            session,
            repo_url="https://github.com/x/y",
            commit_sha=COMMIT,
            snapshot=snapshot(),
            paths=["core/DAGScheduler.scala", "./py/tasks.py", "nope.py"],
        )
        assert report.captured == ["core/DAGScheduler.scala", "py/tasks.py"]
        assert "nope.py" in report.skipped
        stored = read(session, "https://github.com/x/y", COMMIT, "py/tasks.py")
        assert stored is not None and stored.content == TASKS and len(stored.sha256) == 64
        assert read(session, "https://github.com/x/y", OTHER, "py/tasks.py") is None
        # Capturing again is a no-op.
        again = capture(
            session,
            repo_url="https://github.com/x/y",
            commit_sha=COMMIT,
            snapshot=snapshot(),
            paths=["py/tasks.py"],
        )
        assert again.already == ["py/tasks.py"] and not again.captured

    def test_the_stored_snapshot_verifies_like_the_clone(self, session) -> None:
        capture(
            session,
            repo_url="r",
            commit_sha=COMMIT,
            snapshot=snapshot(),
            paths=["core/DAGScheduler.scala"],
        )
        ref = verify_reference(
            StoredSnapshot(session, "r", COMMIT), "core/DAGScheduler.scala", symbol="submitJob"
        )
        assert ref.verified and (ref.start_line, ref.end_line) == (13, 17)

    def test_a_snapshot_cannot_be_stored_under_another_commit(self, session) -> None:
        with pytest.raises(ValueError):
            capture(session, repo_url="r", commit_sha=OTHER, snapshot=snapshot(), paths=["pom.xml"])

    def test_binary_and_oversized_files_are_skipped_not_truncated(self, session, tmp_path) -> None:
        from repolab.sources import CloneSnapshot

        (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
        (tmp_path / "big.txt").write_text("x" * 1_000_001)
        (tmp_path / "ok.txt").write_text("hello\n")
        shot = CloneSnapshot(root=tmp_path, commit_sha=COMMIT)
        report = capture(
            session,
            repo_url="r",
            commit_sha=COMMIT,
            snapshot=shot,
            paths=["bin.dat", "big.txt", "ok.txt", "../escape"],
        )
        assert report.captured == ["ok.txt"]
        assert set(report.skipped) == {"bin.dat", "big.txt", "../escape"}
