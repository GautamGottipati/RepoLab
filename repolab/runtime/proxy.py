"""Addresses of live services, and the rules the proxy enforces.

Each running service gets its own origin, ``rt-<runtime>-<service>.<domain>``,
so a single-page app's root-relative assets (``/main.js``) resolve against it --
a path prefix would break nearly every frontend. The API recognises those hosts
and forwards a request only when the runtime exists, is running, lists that
service, and has a sidecar port for it. Anything else is a 404: this is not an
open proxy, and nothing in a request can choose where it goes.
"""

from __future__ import annotations

import re

from repolab.config import get_settings
from repolab.runtime.models import RuntimeState, RuntimeStatus

_HOST = re.compile(
    r"^rt-(?P<runtime>[a-f0-9]{12})-(?P<service>[a-z0-9][a-z0-9-]{0,23})\.(?P<domain>[^:]+)(?::\d+)?$"
)

#: Hop-by-hop headers, never forwarded (RFC 9110 §7.6.1), plus ones the proxy
#: sets itself.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

#: Headers a live app may not set on RepoLab's origin family.
_STRIPPED_RESPONSE = frozenset({"set-cookie2", "strict-transport-security"})


def proxy_origin(runtime_id: str, service: str) -> str:
    settings = get_settings()
    return (
        f"http://rt-{runtime_id}-{service}.{settings.runtime_proxy_domain}"
        f":{settings.runtime_proxy_origin_port}"
    )


def parse_host(host: str | None) -> tuple[str, str] | None:
    """The runtime and service a Host header names, or ``None`` if it names none."""
    if not host:
        return None
    match = _HOST.match(host.lower())
    if match is None or match.group("domain") != get_settings().runtime_proxy_domain:
        return None
    return match.group("runtime"), match.group("service")


def target_port(state: RuntimeState, service: str) -> int | None:
    """The only port a request for *service* may reach: its sidecar's, if running."""
    current = state.service(service)
    if current is None or current.status is not RuntimeStatus.RUNNING:
        return None
    return current.host_port


def forward_request_headers(headers: list[tuple[str, str]], upstream_host: str) -> dict[str, str]:
    kept = {
        k: v for k, v in headers if k.lower() not in HOP_BY_HOP and not k.lower().startswith("cf-")
    }
    kept["host"] = upstream_host
    return kept


def forward_response_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in headers
        if k.lower() not in HOP_BY_HOP and k.lower() not in _STRIPPED_RESPONSE
    ]
