"""What may be run in a learner's terminal.

A terminal is the most direct way to execute arbitrary code in this product, so
it gets the same treatment as the experiment variable allowlist: **a list of
permitted CQL verbs, in this file**, and a refusal for everything else.

The rule is not "block dangerous things" -- a denylist's failure mode is running
something nobody considered. It is "permit these, refuse the rest", whose failure
mode is a learner being told they cannot do something yet.

Two kinds of refusal, kept distinct because they mean different things:

* a statement that is not CQL at all -- a shell command, a cqlsh meta-command
  that reads or writes host files -- is refused outright;
* a statement that is CQL but touches Cassandra's own system keyspaces is refused
  with a different message, because the learner was probably curious rather than
  malicious.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

#: CQL statements a learner may run.
#:
#: Enough for the lesson flow the specification describes -- inspect, create,
#: insert, query, and make a safe mistake -- and nothing that reaches outside the
#: keyspace they are working in.
ALLOWED_VERBS: frozenset[str] = frozenset(
    {
        "select",
        "insert",
        "update",
        "delete",
        "create",
        "drop",
        "alter",
        "truncate",
        "use",
        "describe",
        "desc",
        "show",
        "consistency",
        "begin",
        "apply",
    }
)

#: cqlsh meta-commands that read or write files on the server, or run a shell.
#:
#: These are legitimate cqlsh features and exactly the ones that turn a CQL
#: console into a filesystem. Named individually so the refusal can say which.
REFUSED_META: frozenset[str] = frozenset(
    {"source", "capture", "copy", "clear", "cls", "login", "exit", "quit", "serial"}
)

#: Keyspaces Cassandra manages itself. Readable -- the lesson starts by looking at
#: them -- but never writable.
SYSTEM_KEYSPACES: frozenset[str] = frozenset(
    {
        "system",
        "system_schema",
        "system_auth",
        "system_distributed",
        "system_traces",
        "system_views",
        "system_virtual_schema",
    }
)

#: Verbs that change things, and therefore must not touch a system keyspace.
MUTATING_VERBS: frozenset[str] = frozenset(
    {"insert", "update", "delete", "create", "drop", "alter", "truncate"}
)

#: Longest statement accepted. A terminal is not a file upload.
MAX_LENGTH = 2000


class Refusal(BaseModel):
    """Why a statement will not be run."""

    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


class Statement(BaseModel):
    """Text that has been checked and may be run.

    Possibly several CQL statements -- `cqlsh -e` runs a batch -- in which case
    every one of them passed.
    """

    text: str
    verbs: list[str]

    @property
    def verb(self) -> str:
        """The leading verb, which is what a lesson step matches on."""
        return self.verbs[0]

    @property
    def is_mutating(self) -> bool:
        return any(verb in MUTATING_VERBS for verb in self.verbs)


def check(raw: str) -> Statement | Refusal:
    """Decide whether *raw* may be run.

    Returns the text to execute, or a refusal explaining why not. A result type
    rather than an exception: a refusal is something the terminal shows the
    learner, not an error condition.

    **Every statement is checked, not just the first.** `cqlsh -e` happily runs
    `SELECT 1; DROP TABLE users` as a batch, so checking the leading verb alone
    would let anything through behind a permitted one.
    """
    text = raw.strip()
    if not text:
        return Refusal(reason="nothing to run")

    if len(text) > MAX_LENGTH:
        return Refusal(
            reason="statement is too long",
            detail=f"{len(text)} characters, limit {MAX_LENGTH}",
        )

    # A terminal that forwards shell metacharacters into a command is a shell.
    escape = _shell_escape(text)
    if escape is not None:
        return Refusal(
            reason="this looks like shell, not CQL",
            detail=f"{escape!r} is not part of a CQL statement",
        )

    statements = split_statements(text)
    if not statements:
        return Refusal(reason="nothing to run")

    verbs: list[str] = []
    for statement in statements:
        outcome = _check_one(statement)
        if isinstance(outcome, Refusal):
            return outcome
        verbs.append(outcome)

    return Statement(text=text.rstrip(";").strip(), verbs=verbs)


def _check_one(statement: str) -> str | Refusal:
    """Check one statement, returning its verb or a refusal."""
    first = statement.split(maxsplit=1)[0].lower().lstrip("\\")

    if first in REFUSED_META:
        return Refusal(
            reason=f"cqlsh's {first.upper()} is not available here",
            detail=(
                "it reads or writes files on the server, which a lesson terminal does not allow"
            ),
        )

    if first not in ALLOWED_VERBS:
        return Refusal(
            reason=f"{first.upper()} is not permitted",
            detail=f"allowed: {', '.join(sorted(v.upper() for v in ALLOWED_VERBS))}",
        )

    keyspace = _targets_system_keyspace(statement)
    if keyspace is not None and first in MUTATING_VERBS:
        return Refusal(
            reason=f"{keyspace} is managed by Cassandra",
            detail="you can read its tables, but changing them would break the node",
        )

    return first


def split_statements(text: str) -> list[str]:
    """Split on semicolons that actually end a statement.

    Quote-aware: a semicolon inside a string literal is data, not a separator,
    and splitting naively would mangle ``INSERT INTO t (s) VALUES ('a;b')`` into
    two statements -- the second of which would then be refused for having a
    nonsense verb.

    A doubled quote inside a literal is CQL's escape for a quote, so ``''`` is
    consumed as a pair and leaves the literal open. Handling it one character at
    a time closed the literal on the second quote, which split
    ``VALUES ('it''s; fine')`` in half and refused a legitimate statement.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0

    while index < len(text):
        char = text[index]

        if quote is not None:
            current.append(char)
            if char == quote:
                if text[index + 1 : index + 2] == quote:
                    # An escaped quote: take both, stay inside the literal.
                    current.append(quote)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in "'\"":
            quote = char
            current.append(char)
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1

    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


#: Tokens that mean something to a shell and nothing to CQL.
#:
#: Deliberately **not** ``>`` or ``<``. They are CQL comparison operators --
#: ``WHERE reading_time > '2026-01-01'`` is an ordinary query, and a lesson about
#: Cassandra will contain one. Refusing them cost a generated lesson two of its
#: steps on the first real run, which is a false refusal rather than a safe one.
#:
#: What actually prevents a redirection is :func:`~repolab.experiences.cli.session.
#: cql_command`, which passes the statement to ``cqlsh -e`` as a single
#: ``shlex.quote``-ed argument: no shell ever sees it. This list is the second
#: layer, for tokens that could only be an attempt at one.
#:
#: Semicolons are not listed either: CQL ends statements with them, and
#: :func:`split_statements` makes every statement in a batch face the allowlist
#: on its own.
_SHELL_METACHARACTERS = ("`", "$(", "${", "&&", "||", "|", "\n;", "\\n")


def _shell_escape(text: str) -> str | None:
    """The first shell metacharacter in *text*, if any."""
    return next((token for token in _SHELL_METACHARACTERS if token in text), None)


def _targets_system_keyspace(text: str) -> str | None:
    """The system keyspace a statement names, if it names one.

    Matched as a bare word anywhere in the statement, not only before a dot:
    ``DROP KEYSPACE system_schema`` names one without ever writing
    ``system_schema.``, and an earlier version of this check let it through.
    """
    lowered = text.lower()
    for keyspace in sorted(SYSTEM_KEYSPACES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(keyspace)}\b", lowered):
            return keyspace
    return None
