"""Repository URL validation.

Ingestion accepts public HTTPS repository URLs only. Anything that could reach
the local network, execute a transport helper, or carry credentials is refused
before a clone is attempted.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel

ALLOWED_SCHEMES = frozenset({"https"})

#: Hosts that must never be cloned from: they would make ingestion a vector for
#: reaching the machine or network RepoLab runs on.
BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*$")


class InvalidRepoUrlError(ValueError):
    """The supplied URL is not an acceptable public repository URL."""


class RepoUrl(BaseModel):
    """A validated, normalised repository URL."""

    normalized: str
    host: str
    owner: str
    name: str

    @property
    def slug(self) -> str:
        """``owner/name``, useful for logs and directory names."""
        return f"{self.owner}/{self.name}"


def _reject_private_host(host: str) -> None:
    """Refuse loopback, private, link-local and otherwise internal hosts."""
    lowered = host.lower()
    if lowered in BLOCKED_HOSTNAMES or lowered.endswith(".localhost"):
        raise InvalidRepoUrlError(f"refusing to clone from internal host {host!r}")

    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return  # A DNS name; resolution is left to git.

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise InvalidRepoUrlError(f"refusing to clone from non-public address {host!r}")


def validate_repo_url(raw: str) -> RepoUrl:
    """Validate and normalise *raw*.

    Raises:
        InvalidRepoUrlError: if the URL is not a public HTTPS repository URL.
    """
    if not raw or not raw.strip():
        raise InvalidRepoUrlError("repository URL is empty")

    candidate = raw.strip()

    # scp-style (``git@host:owner/repo``) has no scheme and would be treated as a
    # relative path by urlparse, so it is rejected explicitly.
    if "://" not in candidate:
        raise InvalidRepoUrlError(
            f"expected an https:// URL, got {candidate!r}; ssh and scp-style URLs are not supported"
        )

    parsed = urlparse(candidate)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidRepoUrlError(f"unsupported scheme {parsed.scheme!r}; only https is accepted")
    if parsed.username or parsed.password or "@" in (parsed.netloc.rsplit(":", 1)[0]):
        raise InvalidRepoUrlError("repository URL must not contain credentials")
    if not parsed.hostname:
        raise InvalidRepoUrlError("repository URL has no host")

    _reject_private_host(parsed.hostname)

    if parsed.query or parsed.fragment:
        raise InvalidRepoUrlError("repository URL must not contain a query or fragment")

    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2:
        raise InvalidRepoUrlError("repository URL must include an owner and a repository name")

    owner, name = segments[0], segments[1]
    if name.endswith(".git"):
        name = name[: -len(".git")]

    for segment in (owner, name):
        if segment in {".", ".."} or not _PATH_SEGMENT.match(segment):
            raise InvalidRepoUrlError(f"invalid path segment {segment!r}")

    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    normalized = urlunparse(("https", f"{host}{port}", f"/{owner}/{name}", "", "", ""))

    return RepoUrl(normalized=normalized, host=host, owner=owner, name=name)
