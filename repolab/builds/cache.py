"""Build artifact caching.

Keyed by repository URL and commit SHA, so the same commit resolves to the same
environment rather than being rebuilt. Cache lookup is deliberately separate from
fidelity: a cached artifact is a cached instance of a real build, not a lower
fidelity level (CLAUDE.md).

**Only a ready artifact is ever stored.** Caching a build whose smoke test failed
would hand out a broken environment on the next lookup.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

from repolab.builds.models import BuildArtifact

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


@runtime_checkable
class BuildCache(Protocol):
    """Somewhere ready build artifacts are remembered."""

    def get(self, repo_url: str, commit_sha: str) -> BuildArtifact | None:
        """The best cached artifact for this commit, if any."""
        ...

    def get_all(self, repo_url: str, commit_sha: str) -> list[BuildArtifact]:
        """Every cached artifact for this commit, best fidelity first.

        A commit can have several: the official release that served while the
        source build compiled is still worth keeping once the source build has
        replaced it, because the next resolution may find the source build gone
        and the release still good.
        """
        ...

    def put(self, artifact: BuildArtifact) -> bool:
        """Store *artifact*. Returns whether it was stored."""
        ...

    def forget(self, repo_url: str, commit_sha: str, kind: str) -> bool:
        """Drop one cached artifact. Returns whether anything was dropped.

        For a checkpoint that is no longer on the machine: the row says "ready"
        and nothing can start from it, so every attempt fails identically until
        it is forgotten.
        """
        ...


def best(artifacts: list[BuildArtifact]) -> BuildArtifact | None:
    """The highest-fidelity ready artifact in *artifacts*."""
    ready = [artifact for artifact in artifacts if artifact.ready]
    return max(ready, key=lambda artifact: artifact.fidelity_rank) if ready else None


class InMemoryBuildCache:
    """A process-local cache. Used by tests and by one-off runs.

    Keyed by commit *and kind*, so the rungs of one ladder do not evict each
    other.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], BuildArtifact] = {}

    def get(self, repo_url: str, commit_sha: str) -> BuildArtifact | None:
        return best(self.get_all(repo_url, commit_sha))

    def get_all(self, repo_url: str, commit_sha: str) -> list[BuildArtifact]:
        found = [
            # Marked as cache-sourced so the caller can report it honestly.
            artifact.model_copy(update={"cached": True})
            for (url, sha, _kind), artifact in self._entries.items()
            if url == repo_url and sha == commit_sha
        ]
        return sorted(found, key=lambda artifact: artifact.fidelity_rank, reverse=True)

    def put(self, artifact: BuildArtifact) -> bool:
        if not artifact.ready:
            return False
        self._entries[(*artifact.cache_key, artifact.kind.value)] = artifact
        return True

    def forget(self, repo_url: str, commit_sha: str, kind: str) -> bool:
        return self._entries.pop((repo_url, commit_sha, kind), None) is not None

    def __len__(self) -> int:
        return len(self._entries)


class NullBuildCache:
    """Remembers nothing. For forcing a rebuild."""

    def get(self, repo_url: str, commit_sha: str) -> BuildArtifact | None:
        return None

    def get_all(self, repo_url: str, commit_sha: str) -> list[BuildArtifact]:
        return []

    def put(self, artifact: BuildArtifact) -> bool:
        return False

    def forget(self, repo_url: str, commit_sha: str, kind: str) -> bool:
        return False


class DbBuildCache:
    """Postgres-backed cache.

    Postgres is the source of truth, so a build produced by a worker is visible to
    the API and to a later run of the command.
    """

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    def get(self, repo_url: str, commit_sha: str) -> BuildArtifact | None:
        return best(self.get_all(repo_url, commit_sha))

    def get_all(self, repo_url: str, commit_sha: str) -> list[BuildArtifact]:
        from repolab.db.builds import load_builds

        try:
            with self._session_factory() as session:
                return load_builds(session, repo_url, commit_sha)
        except Exception as exc:  # noqa: BLE001 - a cache miss is always safe
            log.warning("builds.cache_read_failed", repo=repo_url, error=str(exc))
            return []

    def put(self, artifact: BuildArtifact) -> bool:
        if not artifact.ready:
            return False

        from repolab.db.builds import save_build

        try:
            with self._session_factory() as session:
                save_build(session, artifact)
                session.commit()
        except Exception as exc:  # noqa: BLE001 - failing to cache must not fail a build
            log.warning("builds.cache_write_failed", repo=artifact.repo_url, error=str(exc))
            return False
        return True

    def forget(self, repo_url: str, commit_sha: str, kind: str) -> bool:
        """Drop a cached build whose checkpoint has gone.

        Best-effort, like the rest of this class: failing to forget a stale row
        leaves the status quo, which is a build that cannot start -- bad, but no
        worse than before trying.
        """
        from repolab.db.builds import delete_build

        try:
            with self._session_factory() as session:
                dropped = delete_build(session, repo_url, commit_sha, kind)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("builds.cache_forget_failed", repo=repo_url, error=str(exc))
            return False
        return dropped
