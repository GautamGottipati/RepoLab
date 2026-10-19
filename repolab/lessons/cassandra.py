"""The Cassandra lesson, built deterministically.

This is the lesson the specification describes -- connect, inspect keyspaces,
create a keyspace and table, insert, query, cause a safe error, understand it --
expressed in code rather than generated. It exists for three reasons:

* it runs with no model configured, which is what the offline test suite does;
* it is the shape a generated lesson is validated against, so a model has
  something concrete to beat rather than a blank page;
* the error it provokes is a *chosen* one.

That last point is the interesting one. The error is a ``WHERE`` clause on a
non-key column, which Cassandra rejects with a message telling the learner to add
``ALLOW FILTERING``. It is safe -- nothing is written, nothing breaks -- and it
teaches the single most important thing about Cassandra data modelling: queries
are answered from the partition key, and a query that is not is a query the
server refuses to guess at. A model asked for "a safe error" could produce a
syntax typo instead, which teaches nothing.

Code references are **resolved against the real repository**, never asserted. A
reference whose file or text cannot be found is dropped rather than shown, so a
learner clicking through to the source always lands on something real.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from repolab.builds.models import BuildArtifact
from repolab.evidence.models import CodeReference
from repolab.lessons.models import CheckKind, Lesson, LessonStep, StepCheck
from repolab.manifest.models import RepoManifest

#: The keyspace the lesson works in. Named after RepoLab so it cannot collide
#: with anything a learner's own Cassandra already has.
KEYSPACE = "repolab"
TABLE = f"{KEYSPACE}.sensor_readings"

#: The repository's own cqlsh documentation. Version-matched by construction: it
#: is in the commit being taught.
CQLSH_DOC = "doc/modules/cassandra/pages/managing/tools/cqlsh.adoc"

#: Longest excerpt kept from a resolved reference.
MAX_EXCERPT = 240


class SourceHint(BaseModel):
    """Where in the repository a concept is implemented, and how to find it.

    The needle is what makes this a lookup rather than a claim: the reference is
    only produced if that text is actually in that file at this commit.
    """

    key: str
    path: str
    needle: str
    note: str = ""


#: What each step is grounded in. Paths and needles chosen from Cassandra's own
#: layout; any that do not resolve at the commit under study are simply omitted.
SOURCE_HINTS: tuple[SourceHint, ...] = (
    SourceHint(
        key="cqlsh",
        path="bin/cqlsh.py",
        # The launcher, not the implementation: `bin/cqlsh.py` imports `main`
        # from `cqlshlib.cqlshmain` and calls it. Checked against the real
        # checkout -- a needle of "def main(" matched nothing, because the
        # definition is in pylib.
        needle="from cqlshlib.cqlshmain import main",
        note="the CQL shell a learner's statements are executed by",
    ),
    SourceHint(
        key="local_table",
        path="src/java/org/apache/cassandra/db/SystemKeyspace.java",
        needle="release_version",
        note="the node's own row in system.local, where its version is recorded",
    ),
    SourceHint(
        key="describe",
        path="src/java/org/apache/cassandra/cql3/statements/DescribeStatement.java",
        needle="keyspaces",
        note="how DESCRIBE answers from the schema rather than from disk",
    ),
    SourceHint(
        key="create_keyspace",
        path="src/java/org/apache/cassandra/cql3/statements/schema/CreateKeyspaceStatement.java",
        needle="replication",
        note="where a keyspace's replication settings are validated",
    ),
    SourceHint(
        key="create_table",
        path="src/java/org/apache/cassandra/cql3/statements/schema/CreateTableStatement.java",
        needle="PRIMARY KEY",
        note="where the primary key is parsed into partition and clustering columns",
    ),
    SourceHint(
        key="insert",
        path="src/java/org/apache/cassandra/cql3/statements/ModificationStatement.java",
        needle="class ModificationStatement",
        note="the path every INSERT, UPDATE and DELETE takes",
    ),
    SourceHint(
        key="select",
        path="src/java/org/apache/cassandra/cql3/statements/SelectStatement.java",
        needle="class SelectStatement",
        note="how a SELECT becomes a read of specific partitions",
    ),
    SourceHint(
        key="allow_filtering",
        path="src/java/org/apache/cassandra/cql3/restrictions/StatementRestrictions.java",
        needle="ALLOW FILTERING",
        note="the restriction check that produces the error, and its message",
    ),
    SourceHint(
        key="cqlsh_doc",
        path=CQLSH_DOC,
        needle="cqlsh",
        note="the repository's own cqlsh documentation",
    ),
)


class RepositoryEvidence(BaseModel):
    """Resolved references into the repository, keyed by concept."""

    commit_sha: str = ""
    code: dict[str, CodeReference] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    """Hints whose file or text was not found. Kept so a thin lesson is explicable."""

    def refs(self, *keys: str) -> list[CodeReference]:
        """The references for *keys* that actually resolved, in the order given."""
        return [self.code[key] for key in keys if key in self.code]


def gather_repository_evidence(
    root: Path | None,
    commit_sha: str = "",
    hints: tuple[SourceHint, ...] = SOURCE_HINTS,
) -> RepositoryEvidence:
    """Resolve *hints* against a real checkout.

    With no checkout, nothing resolves and the lesson is grounded only in what
    the manifest already proved. That is the honest outcome: an unverifiable
    reference is worse than none, because a learner cannot tell the difference
    until they click it.
    """
    evidence = RepositoryEvidence(commit_sha=commit_sha)
    if root is None:
        evidence.unresolved = [hint.key for hint in hints]
        return evidence

    for hint in hints:
        reference = _resolve(root, hint, commit_sha)
        if reference is None:
            evidence.unresolved.append(hint.key)
        else:
            evidence.code[hint.key] = reference
    return evidence


def _resolve(root: Path, hint: SourceHint, commit_sha: str) -> CodeReference | None:
    path = root / hint.path
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    needle = hint.needle.lower()
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line.lower():
            return CodeReference(
                path=hint.path,
                start_line=number,
                commit_sha=commit_sha or None,
                excerpt=line.strip()[:MAX_EXCERPT],
            )
    return None


def teaches_cassandra(manifest: RepoManifest) -> bool:
    """Whether this repository is one this lesson is actually about.

    Decided from evidence rather than from the repository's name: the lesson
    teaches CQL through cqlsh, so what makes it applicable is a CLI surface whose
    cited files include cqlsh itself. A Python CLI handed this lesson would be
    told to create a keyspace, which is not a thin lesson but a false one.
    """
    return any(
        "cqlsh" in reference.path.lower()
        for surface in manifest.interaction_surfaces
        for reference in surface.evidence
    )


def cassandra_lesson(
    repo_url: str,
    commit_sha: str = "",
    *,
    artifact: BuildArtifact | None = None,
    evidence: RepositoryEvidence | None = None,
) -> Lesson:
    """The eight-step Cassandra lesson, grounded in whatever *evidence* resolved."""
    found = evidence or RepositoryEvidence(commit_sha=commit_sha)
    steps = [
        LessonStep(
            number=1,
            concept="A Cassandra node answers questions about itself",
            goal="Confirm you are talking to a running server, and find out which version",
            context=(
                "Every Cassandra node keeps a single row describing itself in the "
                "system.local table. Reading it is the smallest possible proof that "
                "your statements are reaching a real server rather than a transcript."
            ),
            action="SELECT release_version FROM system.local",
            expected_observation=(
                "One row, with a version number such as 5.0.x. That number is the "
                "software actually running, not the version the repository pins."
            ),
            validation=StepCheck(
                kind=CheckKind.OUTPUT_MATCHES,
                expected=r"\d+\.\d+",
                describe="the output contains a version number",
            ),
            hint="Type it exactly as shown. The semicolon is optional here.",
            code_references=found.refs("local_table", "cqlsh"),
        ),
        LessonStep(
            number=2,
            concept="Keyspaces are Cassandra's top-level namespace",
            goal="See what already exists on this node before you add anything",
            context=(
                "A keyspace holds tables and decides how their data is replicated. "
                "The ones beginning with system_ are Cassandra's own: it stores its "
                "schema, its users and its internal state in exactly the same kind "
                "of structure it gives you."
            ),
            action="DESCRIBE KEYSPACES",
            expected_observation=(
                "A list including system, system_schema and system_auth. On a fresh "
                "node, nothing of your own yet."
            ),
            validation=StepCheck(
                kind=CheckKind.OUTPUT_CONTAINS,
                expected="system_schema",
                describe="the list includes Cassandra's own schema keyspace",
            ),
            hint="DESC KEYSPACES is the same command, abbreviated.",
            code_references=found.refs("describe", "cqlsh_doc"),
        ),
        LessonStep(
            number=3,
            concept="Replication is chosen per keyspace, not per cluster",
            goal="Create your own keyspace",
            context=(
                "Creating a keyspace means deciding how many copies of its data to "
                "keep. SimpleStrategy with a replication factor of 1 means one copy "
                "— correct for this single node, and the thing you would change "
                "first on a real cluster."
            ),
            action=(
                f"CREATE KEYSPACE IF NOT EXISTS {KEYSPACE} WITH replication = "
                "{'class': 'SimpleStrategy', 'replication_factor': 1}"
            ),
            expected_observation=(
                "No output at all. Cassandra accepts a successful schema change in "
                "silence; the absence of an error is the result."
            ),
            validation=StepCheck(
                kind=CheckKind.SUCCEEDS,
                describe="the server accepted the keyspace",
            ),
            hint=(
                "The replication map needs both keys. Leaving out replication_factor "
                "is itself an informative error, if you would rather see it."
            ),
            code_references=found.refs("create_keyspace"),
        ),
        LessonStep(
            number=4,
            concept="The primary key decides where a row lives",
            goal="Create a table whose key you have chosen deliberately",
            context=(
                "PRIMARY KEY (sensor_id, reading_time) means two different things at "
                "once: sensor_id is the partition key, which decides which node and "
                "which file the row lives in, and reading_time orders the rows inside "
                "that partition. This single decision determines which queries will "
                "be fast and which Cassandra will refuse outright — as you are about "
                "to find out."
            ),
            action=(
                f"CREATE TABLE IF NOT EXISTS {TABLE} (sensor_id text, "
                "reading_time timestamp, celsius double, "
                "PRIMARY KEY (sensor_id, reading_time))"
            ),
            expected_observation="Silence again: the table was created.",
            validation=StepCheck(
                kind=CheckKind.SUCCEEDS,
                describe="the server accepted the table",
            ),
            hint="Run DESCRIBE TABLE repolab.sensor_readings afterwards to see it back.",
            code_references=found.refs("create_table"),
        ),
        LessonStep(
            number=5,
            concept="A write is an insert into one partition",
            goal="Put a reading in the table",
            context=(
                "This INSERT names a sensor and a time, so Cassandra knows exactly "
                "which partition it belongs to without looking anything up. Writes "
                "are cheap here for the same reason reads will be: the key answers "
                "the question of where."
            ),
            action=(
                f"INSERT INTO {TABLE} (sensor_id, reading_time, celsius) "
                "VALUES ('kitchen', '2026-01-01 09:00:00', 21.5)"
            ),
            expected_observation="No output. The row is there.",
            validation=StepCheck(
                kind=CheckKind.SUCCEEDS,
                describe="the server accepted the write",
            ),
            hint="Timestamps are quoted strings in CQL; the format above is accepted.",
            code_references=found.refs("insert"),
        ),
        LessonStep(
            number=6,
            concept="A query that names the partition key is a direct read",
            goal="Read the row back",
            context=(
                "Because this query names sensor_id, Cassandra can go straight to the "
                "one partition holding the answer. This is the query shape the table "
                "was designed for."
            ),
            action=f"SELECT sensor_id, celsius FROM {TABLE} WHERE sensor_id = 'kitchen'",
            expected_observation="One row: kitchen, 21.5.",
            validation=StepCheck(
                kind=CheckKind.OUTPUT_CONTAINS,
                expected="kitchen",
                describe="the row you inserted comes back",
            ),
            hint="If nothing comes back, re-run the previous step and watch for an error.",
            code_references=found.refs("select"),
        ),
        LessonStep(
            number=7,
            concept="Cassandra refuses queries it cannot answer efficiently",
            goal="Deliberately ask a question the key cannot answer, and read the refusal",
            context=(
                "This query filters on celsius, which is not part of the primary key. "
                "To answer it, Cassandra would have to read every partition in the "
                "table — and rather than do that silently, it refuses and tells you "
                "why. Nothing is written and nothing breaks: this is the safest "
                "mistake in Cassandra, and the most instructive."
            ),
            action=f"SELECT * FROM {TABLE} WHERE celsius = 21.5",
            expected_observation=(
                "An InvalidRequest error saying the query might involve data "
                "filtering and may have unpredictable performance, suggesting "
                "ALLOW FILTERING."
            ),
            validation=StepCheck(
                kind=CheckKind.FAILS_WITH,
                expected="ALLOW FILTERING",
                describe="the server refuses, and names ALLOW FILTERING",
            ),
            hint=(
                "This step is meant to fail. Run it as written and read the message "
                "carefully — the next step is about what it means."
            ),
            code_references=found.refs("allow_filtering", "select"),
        ),
        LessonStep(
            number=8,
            concept="ALLOW FILTERING is a permission, not a fix",
            goal="Get the answer the hard way, and understand what you just agreed to",
            context=(
                "Adding ALLOW FILTERING tells Cassandra you accept the cost it warned "
                "you about. On one row, it is instant. On a hundred million, the same "
                "statement reads the whole table. The lesson is not that the flag is "
                "forbidden — it is that the previous error was Cassandra asking you to "
                "confirm you meant it, and that the real fix is usually a different "
                "table whose key matches the question."
            ),
            action=f"SELECT * FROM {TABLE} WHERE celsius = 21.5 ALLOW FILTERING",
            expected_observation="The kitchen row, returned this time.",
            validation=StepCheck(
                kind=CheckKind.OUTPUT_CONTAINS,
                expected="kitchen",
                describe="the row comes back once filtering is permitted",
            ),
            hint="Append ALLOW FILTERING to the statement from the previous step.",
            code_references=found.refs("allow_filtering"),
        ),
    ]

    return Lesson(
        repo_url=repo_url,
        commit_sha=commit_sha,
        title="Querying Cassandra through its own cqlsh",
        summary=(
            "Eight statements against a real Cassandra node, ending with the error "
            "that explains how Cassandra thinks about queries."
        ),
        steps=steps,
        generated_by="deterministic",
        fidelity=artifact.fidelity.value if artifact else "unknown",
        simulated=bool(artifact and artifact.simulated),
    )
