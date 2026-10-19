"""Isolated execution environments.

User code runs only inside a provider-managed environment, never on the API host.
Callers depend on :class:`ExecutionEnvironment` so nothing above this layer needs
to know whether work happens in a sandbox or on a VM.
"""

from __future__ import annotations

from repolab.sandbox.docker_provider import DockerSandboxProvider
from repolab.sandbox.environment import (
    EnvironmentNotStartedError,
    ExecutionEnvironment,
    SandboxEnvironment,
)
from repolab.sandbox.fake import FakeSandboxProvider, ScriptedCommand
from repolab.sandbox.models import (
    Checkpoint,
    SandboxCapabilities,
    SandboxError,
    SandboxHandle,
    UnsupportedOperationError,
)
from repolab.sandbox.provider import SandboxProvider

__all__ = [
    "Checkpoint",
    "DockerSandboxProvider",
    "EnvironmentNotStartedError",
    "ExecutionEnvironment",
    "FakeSandboxProvider",
    "SandboxCapabilities",
    "SandboxEnvironment",
    "SandboxError",
    "SandboxHandle",
    "SandboxProvider",
    "ScriptedCommand",
    "UnsupportedOperationError",
]
