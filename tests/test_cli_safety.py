"""What a learner's terminal will and will not run.

A terminal is the most direct way to execute arbitrary code in this product, so
the rule is an allowlist: permitted CQL verbs, everything else refused. These
tests are mostly about the refusals, and two of them exist because an earlier
version of the allowlist really did let those statements through.
"""

from __future__ import annotations

import pytest

from repolab.experiences.cli.safety import (
    MAX_LENGTH,
    Refusal,
    Statement,
    check,
    split_statements,
)


def permitted(raw: str) -> Statement:
    outcome = check(raw)
    assert isinstance(outcome, Statement), f"expected {raw!r} to be permitted, got {outcome}"
    return outcome


def refused(raw: str) -> Refusal:
    outcome = check(raw)
    assert isinstance(outcome, Refusal), f"expected {raw!r} to be refused, got {outcome}"
    return outcome


class TestThePermittedLesson:
    """Every statement the lesson asks a learner to run must be permitted."""

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT release_version FROM system.local",
            "DESCRIBE KEYSPACES",
            "DESC KEYSPACES",
            "CREATE KEYSPACE IF NOT EXISTS repolab WITH replication = "
            "{'class': 'SimpleStrategy', 'replication_factor': 1}",
            "CREATE TABLE repolab.t (id text, at timestamp, c double, PRIMARY KEY (id, at))",
            "INSERT INTO repolab.t (id, at, c) VALUES ('kitchen', '2026-01-01 09:00:00', 21.5)",
            "SELECT id, c FROM repolab.t WHERE id = 'kitchen'",
            "SELECT * FROM repolab.t WHERE c = 21.5 ALLOW FILTERING",
            "USE repolab",
            "DROP TABLE repolab.t",
        ],
    )
    def test_the_lesson_flow_is_permitted(self, statement: str) -> None:
        permitted(statement)

    def test_the_leading_verb_is_what_a_step_matches_on(self) -> None:
        assert permitted("SELECT * FROM repolab.t").verb == "select"

    def test_a_read_is_not_mutating(self) -> None:
        assert permitted("SELECT * FROM repolab.t").is_mutating is False

    def test_a_write_is_mutating(self) -> None:
        assert permitted("INSERT INTO repolab.t (id) VALUES ('a')").is_mutating is True


class TestRefusals:
    def test_a_shell_command_is_refused(self) -> None:
        assert "CQL" in refused("ls -la /").reason or "not permitted" in refused("ls -la /").reason

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT * FROM t | cat",
            "SELECT $(whoami)",
            "SELECT ${HOME}",
            "SELECT `id`",
            "SELECT 1 && rm -rf /",
            "SELECT 1 || rm -rf /",
        ],
    )
    def test_shell_metacharacters_are_refused(self, statement: str) -> None:
        assert "shell" in refused(statement).reason

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT * FROM repolab.t WHERE at > '2026-01-01'",
            "SELECT * FROM repolab.t WHERE celsius < 30 ALLOW FILTERING",
        ],
    )
    def test_comparison_operators_are_not_mistaken_for_redirection(self, statement: str) -> None:
        """`>` and `<` are CQL operators, and a generated lesson will use them.

        Refusing them cost a Super-written lesson two of its eight steps on the
        first real run. What prevents a redirection is the shlex quoting in
        `cql_command` -- no shell ever sees the statement -- not the absence of
        the character.
        """
        permitted(statement)

    @pytest.mark.parametrize("meta", ["SOURCE './x.cql'", "COPY t TO 'out.csv'", "CAPTURE 'f'"])
    def test_cqlsh_file_commands_are_refused(self, meta: str) -> None:
        """These are real cqlsh features, and they turn a CQL console into a filesystem."""
        assert "not available" in refused(meta).reason

    def test_an_overlong_statement_is_refused(self) -> None:
        assert "too long" in refused("SELECT " + "x" * (MAX_LENGTH + 1)).reason

    def test_nothing_is_refused_as_nothing(self) -> None:
        assert refused("   ").reason == "nothing to run"


class TestEveryStatementIsChecked:
    """`cqlsh -e` runs a batch, so checking only the first verb lets anything through.

    An earlier version of this module checked the leading verb and *claimed in
    its docstring* to check them all. Both statements below passed it.
    """

    def test_a_refused_statement_hiding_behind_a_permitted_one(self) -> None:
        outcome = refused("SELECT * FROM repolab.t; TRUNCATE system_auth.roles")
        assert "system_auth" in outcome.reason

    def test_a_shell_command_hiding_behind_a_permitted_one(self) -> None:
        refused("SELECT 1; rm -rf /")

    def test_every_verb_is_recorded(self) -> None:
        statement = permitted("SELECT * FROM repolab.t; INSERT INTO repolab.t (id) VALUES ('a')")
        assert statement.verbs == ["select", "insert"]
        assert statement.is_mutating is True, "a batch is mutating if any statement is"


class TestSystemKeyspaces:
    def test_reading_a_system_table_is_permitted(self) -> None:
        """The lesson starts by looking at what Cassandra keeps about itself."""
        permitted("SELECT release_version FROM system.local")

    @pytest.mark.parametrize(
        "statement",
        [
            "TRUNCATE system_auth.roles",
            "DROP TABLE system_schema.tables",
            "INSERT INTO system.local (key) VALUES ('x')",
            "ALTER TABLE system_schema.columns ADD x text",
        ],
    )
    def test_writing_to_one_is_refused(self, statement: str) -> None:
        assert "managed by Cassandra" in refused(statement).reason

    def test_a_system_keyspace_named_without_a_dot_is_refused(self) -> None:
        """`DROP KEYSPACE system_schema` names one without ever writing a dot.

        An earlier version matched only `system_schema.`, so this passed.
        """
        assert "system_schema" in refused("DROP KEYSPACE system_schema").reason


class TestSplittingStatements:
    def test_a_semicolon_inside_a_literal_is_data(self) -> None:
        """Splitting naively would make the second half a statement with a nonsense verb."""
        assert split_statements("INSERT INTO t (s) VALUES ('a;b')") == [
            "INSERT INTO t (s) VALUES ('a;b')"
        ]

    def test_a_doubled_quote_is_an_escaped_quote(self) -> None:
        assert len(split_statements("INSERT INTO t (s) VALUES ('it''s; fine')")) == 1

    def test_trailing_semicolons_do_not_create_empty_statements(self) -> None:
        assert split_statements("SELECT 1;;;") == ["SELECT 1"]

    def test_the_stored_text_has_no_trailing_semicolon(self) -> None:
        """What reaches `cqlsh -e` should be the statement, not punctuation."""
        assert permitted("SELECT * FROM repolab.t;").text == "SELECT * FROM repolab.t"
