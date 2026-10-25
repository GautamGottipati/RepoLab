"""Experience adapters: each turns repository evidence into things to do.

An adapter proposes experiences from what the investigation found, says how far
along each one is (detected, supported, runnable, running), and -- for the ones
RepoLab runs itself -- how to prepare a runtime and execute an action in it.
Registered in :data:`ADAPTERS`; adding a kind of experience means adding one.
"""

from repolab.experiences.adapters.base import Adapter, DiscoveryContext
from repolab.experiences.adapters.cli import CliAdapter
from repolab.experiences.adapters.library import LibraryAdapter
from repolab.experiences.adapters.live import LiveAppAdapter
from repolab.experiences.adapters.spark import SparkAdapter

#: In proposal order. Ranking, not this order, decides what is shown first.
ADAPTERS: tuple[Adapter, ...] = (SparkAdapter(), LiveAppAdapter(), CliAdapter(), LibraryAdapter())

__all__ = ["ADAPTERS", "Adapter", "DiscoveryContext"]
