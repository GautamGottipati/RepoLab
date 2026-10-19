"""External documentation research via Tavily.

Verified against Tavily documentation and the installed client:
  endpoint  POST https://api.tavily.com/search
  domains   `include_domains` restricts results (max 300 domains)
  results   each item carries `url`, `title`, `content`
  timeout   `search(timeout=...)` seconds, capped at 120 by the client, which
            raises `TimeoutError` when it expires

Repository code remains RepoLab's primary evidence; research supplements it with
version-matched official documentation.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tavily import TavilyClient

from repolab.interfaces import SearchResultItem

#: Seconds before a research call is abandoned.
#:
#: Research supplements repository evidence and never gates it, so the right
#: deadline is "shorter than a person's patience": an investigation that waits a
#: minute for a documentation page it will cite in passing has already cost more
#: than the citation is worth.
DEFAULT_TIMEOUT_SECONDS = 20.0


class TavilyResearchProvider:
    """:class:`~repolab.interfaces.ResearchProvider` backed by Tavily."""

    def __init__(self, api_key: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._client = TavilyClient(api_key=api_key)
        self._timeout = timeout

    async def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> list[SearchResultItem]:
        # TavilyClient is synchronous; keep the event loop free.
        #
        # The deadline is passed to the client rather than wrapped around the
        # thread: `asyncio.to_thread` cannot be cancelled, so a `wait_for` here
        # would return while the request kept running. The client's own timeout
        # ends the request, which is the only thing that actually does.
        #
        # Unbounded, a hanging research call stalls an investigation with nothing
        # reported -- worse than an error, because an error degrades gracefully
        # and silence does not.
        response: dict[str, Any] = await asyncio.to_thread(
            self._client.search,
            query=query,
            include_domains=include_domains or [],
            max_results=max_results,
            timeout=self._timeout,
        )
        return [
            SearchResultItem(
                url=item.get("url", ""),
                title=item.get("title"),
                content=item.get("content"),
            )
            for item in response.get("results", [])
            if item.get("url")
        ]
