"""The CLI experience backend: a real terminal on a real environment.

    ready artifact --> restore its checkpoint, running its server
                   --> wait for the CLI to answer
                   --> check each statement against an allowlist
                   --> execute it inside the container
                   --> return exactly what came back

Nothing here synthesises output. The allowlist is the one place a learner's text
is examined, and it permits CQL verbs rather than blocking dangerous ones, so an
unforeseen statement is refused rather than run.
"""

from __future__ import annotations

from repolab.experiences.cli.experience import (
    CLIExperience,
    NoCliSurfaceError,
    build_cli_experience,
    cli_surface,
)
from repolab.experiences.cli.safety import (
    ALLOWED_VERBS,
    MUTATING_VERBS,
    SYSTEM_KEYSPACES,
    Refusal,
    Statement,
    check,
    split_statements,
)
from repolab.experiences.cli.session import (
    CliSession,
    CliSessionManager,
    CommandRecord,
    SessionNotReady,
    SessionStatus,
    cql_command,
)

__all__ = [
    "ALLOWED_VERBS",
    "MUTATING_VERBS",
    "SYSTEM_KEYSPACES",
    "CLIExperience",
    "CliSession",
    "CliSessionManager",
    "CommandRecord",
    "NoCliSurfaceError",
    "Refusal",
    "SessionNotReady",
    "SessionStatus",
    "Statement",
    "build_cli_experience",
    "check",
    "cli_surface",
    "cql_command",
    "split_statements",
]
