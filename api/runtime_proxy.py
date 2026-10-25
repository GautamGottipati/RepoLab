"""Serving a live service on its own origin, through RepoLab.

A request whose Host is ``rt-<runtime>-<service>.<domain>`` never reaches the
API's routes: this middleware answers it, by forwarding to the one port the
runtime's record lists for that service -- the RepoLab sidecar on the host's
loopback -- and nowhere else. Unknown, stopped or unlisted services are 404.
HTTP is streamed both ways; WebSockets are relayed, because dev servers use them
for live reload.

The browser therefore talks only to RepoLab. It never learns a container
address, and nothing in a request can redirect the proxy elsewhere.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from repolab.runtime.models import RuntimeState
from repolab.runtime.proxy import (
    forward_request_headers,
    forward_response_headers,
    parse_host,
    target_port,
)

log = structlog.get_logger(__name__)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

#: Runtime records are re-read at most this often per runtime.
CACHE_SECONDS = 2.0
TOUCH_SECONDS = 60.0

_COOKIE_DOMAIN = re.compile(r";\s*domain=[^;]*", re.I)


StateLoader = Callable[[str], RuntimeState | None]


def _load_from_db(runtime_id: str) -> RuntimeState | None:
    import api.deps as deps
    from repolab.db.runtimes import load_runtime, touch_runtime

    with deps.session_factory()() as session:
        loaded = load_runtime(session, runtime_id)
        if loaded is None:
            return None
        touch_runtime(session, runtime_id)
        session.commit()
        return loaded[1]


class RuntimeProxyMiddleware:
    def __init__(self, app: Any, *, load: StateLoader | None = None) -> None:
        self.app = app
        self._load = load or _load_from_db
        self._cache: dict[str, tuple[float, RuntimeState | None]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host = _header(scope, "host")
        named = parse_host(host)
        if named is None:
            await self.app(scope, receive, send)
            return
        runtime_id, service = named
        state = await self._state(runtime_id)
        port = target_port(state, service) if state is not None else None
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, port)
            return
        if port is None:
            await _plain(send, 404, b"This RepoLab live app is not running.")
            return
        await self._http(scope, receive, send, port)

    async def _state(self, runtime_id: str) -> RuntimeState | None:
        cached = self._cache.get(runtime_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < CACHE_SECONDS and CACHE_SECONDS > 0:
            return cached[1]
        state = await asyncio.to_thread(self._load, runtime_id)
        self._cache[runtime_id] = (now, state)
        return state

    async def _http(self, scope: Scope, receive: Receive, send: Send, port: int) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        path = scope.get("raw_path", scope["path"].encode()).decode("latin-1")
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"http://127.0.0.1:{port}{path}" + (f"?{query}" if query else "")
        headers = forward_request_headers(
            [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"]],
            f"127.0.0.1:{port}",
        )
        # A client per request: it lives exactly as long as the streamed response.
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            try:
                request = client.build_request(scope["method"], url, headers=headers, content=body)
                response = await client.send(request, stream=True)
            except httpx.HTTPError as exc:
                log.info("runtime_proxy.upstream_failed", error=type(exc).__name__)
                await _plain(send, 502, b"The live app did not answer.")
                return
            try:
                out = []
                for key, value in forward_response_headers(list(response.headers.multi_items())):
                    if key.lower() == "set-cookie":
                        # Host-only: a live app may not set cookies for RepoLab's domain.
                        value = _COOKIE_DOMAIN.sub("", value)
                    out.append((key.encode("latin-1"), value.encode("latin-1")))
                await send(
                    {"type": "http.response.start", "status": response.status_code, "headers": out}
                )
                async for chunk in response.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            finally:
                await response.aclose()

    async def _websocket(
        self, scope: Scope, receive: Receive, send: Send, port: int | None
    ) -> None:
        first = await receive()  # websocket.connect
        if first.get("type") != "websocket.connect" or port is None:
            await send({"type": "websocket.close", "code": 1008})
            return
        import websockets

        path = scope.get("raw_path", scope["path"].encode()).decode("latin-1")
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"ws://127.0.0.1:{port}{path}" + (f"?{query}" if query else "")
        protocols = [
            p.strip()
            for p in (_header(scope, "sec-websocket-protocol") or "").split(",")
            if p.strip()
        ]
        try:
            upstream = await websockets.connect(
                url, subprotocols=protocols or None, open_timeout=10
            )
        except Exception:  # noqa: BLE001 - any failure is a refused upgrade
            await send({"type": "websocket.close", "code": 1011})
            return
        await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

        async def client_to_upstream() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    await upstream.close()
                    return
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])

        async def upstream_to_client() -> None:
            async for data in upstream:
                if isinstance(data, str):
                    await send({"type": "websocket.send", "text": data})
                else:
                    await send({"type": "websocket.send", "bytes": data})
            await send({"type": "websocket.close", "code": 1000})

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await upstream.close()


def _header(scope: Scope, name: str) -> str | None:
    wanted = name.encode()
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


async def _plain(send: Send, status_code: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})
