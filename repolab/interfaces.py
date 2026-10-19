"""Protocols for the external services Phase 0 depends on.

Every external system sits behind one of these so that RepoLab is not coupled to
a vendor SDK and so the test suite can run entirely offline against the fakes in
:mod:`repolab.testing.fakes`.

Only the three providers the capability checker actually uses are defined here.
Further abstractions are added when a milestone needs them.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class ChatResult(BaseModel):
    """Outcome of a single chat completion request."""

    model: str
    """The model ID the request was sent to."""

    raw_message: Any = None
    """The provider's message object, preserved for shape inspection."""

    text: str | None = None
    """Assistant text, when the provider populated ``content``."""

    tool_calls: list[Any] = []
    """Tool requests the model produced, if any. RepoLab never auto-executes these."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class SearchResultItem(BaseModel):
    """One external documentation hit."""

    url: str
    title: str | None = None
    content: str | None = None


class CommandResult(BaseModel):
    """Outcome of one command executed inside an isolated environment."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    state_id: str | None = None
    """Identifier of the resulting environment state, when it was preserved."""

    elapsed_seconds: float | None = None
    """Execution time reported by the environment, when it measures one."""

    cost: float | None = None
    """Compute cost reported by the environment, when it reports one."""


@runtime_checkable
class LLMProvider(Protocol):
    """Chat inference over an OpenAI-compatible endpoint."""

    async def list_models(self) -> list[str]: ...

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult: ...


@runtime_checkable
class ResearchProvider(Protocol):
    """External documentation search, preferably restricted to official domains."""

    async def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> list[SearchResultItem]: ...


# ``SandboxProvider`` now lives in :mod:`repolab.sandbox.provider`, defined against
# the lifecycle the real service supports -- create, upload, execute, checkpoint,
# restore, terminate -- rather than the narrower sketch that was here.
