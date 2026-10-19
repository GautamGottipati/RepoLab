"""Shallow, hardened repository cloning.

Ingestion is static inspection. Nothing from the repository is ever executed: no
``pip install``, ``npm install``, ``mvn``, ``gradle``, ``make``, or setup script.
The only subprocess run is ``git`` itself, and it is configured so the remote
cannot influence what executes locally:

* ``core.hooksPath`` is pointed at nothing, so no hook can run.
* ``protocol.ext`` and ``protocol.file`` are denied, blocking transport helpers
  that would execute a command supplied by a URL.
* Submodules are not initialised by default, so a malicious ``.gitmodules``
  fetches nothing. A caller that genuinely needs them -- a source build of a
  repository whose own build invokes a submodule -- asks for them explicitly.
* Credential prompting is disabled, so a private repository fails fast instead of
  blocking on stdin.
* ``GIT_CONFIG_NOSYSTEM`` and an empty ``HOME`` keep ambient developer config --
  aliases, filters, ``insteadOf`` rewrites -- out of the operation.

The clone follows the remote's own default branch (verified during planning:
``apache/cassandra`` uses ``trunk``, not ``main``), so no branch is assumed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog
from pydantic import BaseModel

from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits
from repolab.ingest.url import RepoUrl

log = structlog.get_logger(__name__)

#: git -c options applied to every invocation. Each one closes a path by which a
#: remote repository could cause local execution.
SAFETY_CONFIG: tuple[str, ...] = (
    "core.hooksPath=/dev/null",
    "protocol.ext.allow=never",
    "protocol.file.allow=never",
    "core.symlinks=false",
    "core.fsmonitor=false",
    "advice.detachedHead=false",
)

#: Options relaxed only when cloning a deliberately supplied local path, which
#: happens in tests. Production never sets ``allow_local_source``, and
#: :func:`repolab.ingest.url.validate_repo_url` rejects ``file://`` URLs anyway,
#: so this is defence in depth rather than the only barrier.
_LOCAL_SOURCE_OVERRIDES: tuple[str, ...] = ("protocol.file.allow=always",)

CLONE_FLAGS: tuple[str, ...] = (
    "--depth",
    "1",
    "--single-branch",
    "--no-tags",
    "--quiet",
)

#: Appended when submodules are *not* wanted, which is the default.
NO_SUBMODULES: tuple[str, ...] = ("--recurse-submodules=no",)

#: Appended when they are. Shallow, so a submodule's history is no larger than
#: the repository's own.
WITH_SUBMODULES: tuple[str, ...] = ("--recurse-submodules", "--shallow-submodules")


class CloneError(RuntimeError):
    """The repository could not be cloned."""


class CloneResult(BaseModel):
    """Where a repository was cloned and at which commit."""

    path: Path
    commit_sha: str
    branch: str | None = None

    model_config = {"arbitrary_types_allowed": True}


def _git_env(home: Path, *, allow_local_source: bool = False) -> dict[str, str]:
    """A minimal environment for git: no prompting, no ambient config."""
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "https:file" if allow_local_source else "https",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GCM_INTERACTIVE": "never",
    }


def build_clone_command(
    url: str,
    destination: Path,
    *,
    allow_local_source: bool = False,
    submodules: bool = False,
) -> list[str]:
    """The exact argv used to clone. Exposed so tests can assert the hardening.

    *submodules* fetches declared submodules, shallowly. Off by default: static
    inspection never needs them, and fetching them means running more of the
    repository's own configuration and downloading code nothing will read.
    A build that genuinely cannot compile without them asks for them -- Cassandra
    trunk is one, since ``modules/accord`` supplies a gradle build its ant target
    invokes.
    """
    command = ["git"]
    for option in SAFETY_CONFIG:
        command += ["-c", option]
    if allow_local_source:
        for option in _LOCAL_SOURCE_OVERRIDES:
            command += ["-c", option]
    command.append("clone")
    command += list(CLONE_FLAGS)
    # Stated explicitly either way, rather than relying on a later flag winning
    # over an earlier one.
    command += list(WITH_SUBMODULES if submodules else NO_SUBMODULES)
    command += ["--", url, str(destination)]
    return command


def _run_git(
    command: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        # Fixed argv, never a shell, so repository content cannot be interpreted.
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"git timed out after {timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise CloneError("git is not installed or not on PATH") from exc


def shallow_clone(
    repo_url: RepoUrl,
    destination: Path,
    *,
    limits: IngestLimits = DEFAULT_LIMITS,
    allow_local_source: bool = False,
    submodules: bool = False,
) -> CloneResult:
    """Clone *repo_url* into *destination* at depth 1 and record the commit.

    Args:
        allow_local_source: permit cloning a local filesystem path. Off by
            default, which is why a ``file`` transport is refused outright; tests
            set it to exercise the clone without a network.
        submodules: also fetch declared submodules, shallowly. Off by default --
            ingestion reads files and never needs them.

    Raises:
        CloneError: if the clone or the commit lookup fails.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # An isolated HOME so no developer git config reaches the subprocess.
    with tempfile.TemporaryDirectory(prefix="repolab-githome-") as home:
        env = _git_env(Path(home), allow_local_source=allow_local_source)

        log.info("ingest.clone_started", repo=repo_url.slug, depth=1)
        result = _run_git(
            build_clone_command(
                repo_url.normalized,
                destination,
                allow_local_source=allow_local_source,
                submodules=submodules,
            ),
            cwd=None,
            env=env,
            timeout=limits.clone_timeout_seconds,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise CloneError(
                f"git clone failed ({result.returncode}): {detail[-1] if detail else 'no output'}"
            )

        commit_sha = _rev_parse(destination, env, limits)
        branch = _current_branch(destination, env, limits)

    log.info("ingest.clone_finished", repo=repo_url.slug, commit=commit_sha, branch=branch)
    return CloneResult(path=destination, commit_sha=commit_sha, branch=branch)


def _rev_parse(path: Path, env: dict[str, str], limits: IngestLimits) -> str:
    result = _run_git(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        env=env,
        timeout=min(60.0, limits.clone_timeout_seconds),
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise CloneError("could not determine the cloned commit SHA")
    return result.stdout.strip()


def _current_branch(path: Path, env: dict[str, str], limits: IngestLimits) -> str | None:
    """The checked-out branch, or ``None`` for a detached HEAD."""
    result = _run_git(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        env=env,
        timeout=min(60.0, limits.clone_timeout_seconds),
    )
    branch = result.stdout.strip()
    return None if result.returncode != 0 or branch in {"", "HEAD"} else branch


class TemporaryClone:
    """Context manager owning an isolated clone directory.

    The clone lives in its own temporary directory and is removed on exit, so
    repository contents never accumulate on the host.
    """

    def __init__(
        self,
        repo_url: RepoUrl,
        *,
        limits: IngestLimits = DEFAULT_LIMITS,
        allow_local_source: bool = False,
        submodules: bool = False,
    ) -> None:
        self._repo_url = repo_url
        self._limits = limits
        self._allow_local_source = allow_local_source
        self._submodules = submodules
        self._root: Path | None = None

    def __enter__(self) -> CloneResult:
        self._root = Path(tempfile.mkdtemp(prefix="repolab-ingest-"))
        try:
            return shallow_clone(
                self._repo_url,
                self._root / "repo",
                limits=self._limits,
                allow_local_source=self._allow_local_source,
                submodules=self._submodules,
            )
        except BaseException:
            self._cleanup()
            raise

    def __exit__(self, *exc: object) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._root is not None and self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
        self._root = None
