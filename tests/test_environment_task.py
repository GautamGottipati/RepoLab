"""The worker task that resolves an environment for the browser.

Exercised through its two refusals, because those are what a browser depends on:
a repository nobody investigated cannot be built, and a repository whose default
branch has moved since the investigation must not be built silently — the
artifact would be cached under a commit the API never looks up, so the build
would cost minutes and then be invisible to the terminal that asked for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repolab.ingest.clone import CloneResult
from tests.support.builds import CASSANDRA_REPO, SHA, cassandra_manifest
from worker.environment_tasks import CommitMovedError, ManifestMissingError, _resolve


class Emitter:
    """Just enough emitter to record what the task announced."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def emit(self, event_type, message="", *, step=None, data=None):
        self.events.append((str(event_type), message))
        return None


class Factory:
    """A session factory whose session answers the two lookups the task makes."""

    def __init__(self, manifest) -> None:
        self.manifest = manifest

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> None:
    import worker.environment_tasks as task

    monkeypatch.setattr(task, "docker_available", lambda: True)


async def test_an_uninvestigated_repository_is_refused(
    monkeypatch: pytest.MonkeyPatch, docker: None
) -> None:
    import worker.environment_tasks as task

    monkeypatch.setattr(task, "load_manifest", lambda _session, _url: None)

    with pytest.raises(ManifestMissingError, match="has not been investigated"):
        await _resolve(CASSANDRA_REPO, 0.0, Emitter(), Factory(None), None)


async def test_a_moved_default_branch_is_refused_before_anything_is_built(
    monkeypatch: pytest.MonkeyPatch, docker: None, tmp_path: Path
) -> None:
    """The build would succeed and then be unfindable, which is worse than failing."""
    import worker.environment_tasks as task

    manifest = cassandra_manifest(sha=SHA)
    monkeypatch.setattr(task, "load_manifest", lambda _session, _url: manifest)

    class Clone:
        def __enter__(self):
            return CloneResult(path=tmp_path, commit_sha="d" * 40, branch="trunk")

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(task, "TemporaryClone", lambda *a, **k: Clone())

    resolved: list[object] = []
    monkeypatch.setattr(
        task, "BuildLadder", lambda *a, **k: resolved.append(a) or pytest.fail("built anyway")
    )

    with pytest.raises(CommitMovedError, match="import it again"):
        await _resolve(CASSANDRA_REPO, 0.0, Emitter(), Factory(manifest), None)

    assert resolved == [], "nothing was built"


async def test_docker_being_absent_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repository code must never run on the API host, so there is no fallback.

    Raised as an environment failure rather than a build failure: the ladder is
    never reached, so telling a user "every rung was tried" would be false.
    """
    import worker.environment_tasks as task
    from repolab.failures import FailureKind, explain
    from repolab.sandbox.models import SandboxError

    monkeypatch.setattr(task, "docker_available", lambda: False)

    with pytest.raises(SandboxError, match="never run on the API host") as raised:
        await _resolve(CASSANDRA_REPO, 0.0, Emitter(), Factory(None), None)

    assert explain(raised.value).kind is FailureKind.ENVIRONMENT
