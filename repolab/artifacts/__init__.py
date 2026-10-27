"""Progressive analysis: artifacts generated, verified, persisted and streamed one by one.

registry    every artifact type: priority, dependencies, version, queue, laziness
store       state in Postgres, atomic claims, transitions that emit events
"""

from repolab.artifacts.registry import (
    ARCH_COMPONENTS,
    ARCH_RUNTIME,
    ARCH_RUNTIME_NAMING,
    ARCH_SOURCE,
    ARCH_STRUCTURE,
    ARCH_SUMMARY,
    EAGER,
    REPO_FACTS,
    REPO_MANIFEST,
    SPECS,
    Priority,
    Queue,
    Status,
    spec,
)

__all__ = [
    "ARCH_COMPONENTS",
    "ARCH_RUNTIME",
    "ARCH_RUNTIME_NAMING",
    "ARCH_SOURCE",
    "ARCH_STRUCTURE",
    "ARCH_SUMMARY",
    "EAGER",
    "REPO_FACTS",
    "REPO_MANIFEST",
    "SPECS",
    "Priority",
    "Queue",
    "Status",
    "spec",
]
