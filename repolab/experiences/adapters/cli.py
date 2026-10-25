"""Command lines: an interactive terminal against the repository's own build.

Re-expresses the existing CLI experience (a real terminal over a WebSocket, in
the learner's own container, with a guided lesson) as *Try the CLI*. Where the
catalog says no build strategy or lesson covers this repository's command line,
it is *supported* but not runnable, and says why.
"""

from __future__ import annotations

from repolab.experiences.adapters.base import DiscoveryContext, experience_id
from repolab.experiences.model import (
    ExperienceDefinition,
    ExperienceState,
    ExperienceType,
    Reality,
    RuntimeOption,
    Strategy,
)
from repolab.manifest.models import SurfaceType

NAME = "cli"


class CliAdapter:
    name = NAME

    def propose(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        item = context.catalog.of(SurfaceType.CLI)
        if item is None:
            return []
        return [
            ExperienceDefinition(
                id=experience_id(NAME, "terminal"),
                title="Try the CLI",
                experience_type=ExperienceType.CLI,
                adapter=NAME,
                summary="Type real commands into the repository's own command-line tool.",
                what_you_do="Open a terminal on a running build and follow a short lesson.",
                what_you_learn="How people actually use this software day to day.",
                what_to_notice="Errors are the software's own, explained from its source.",
                surfaces=["cli"],
                state=ExperienceState.RUNNABLE if item.runnable else ExperienceState.SUPPORTED,
                why_not=None if item.runnable else item.unavailable_reason,
                options=[
                    RuntimeOption(
                        strategy=Strategy.EXISTING,
                        reality=Reality.REAL_REPOSITORY,
                        label="A build of this repository (see the Learn tab for its fidelity)",
                        ready=True,
                        estimate="under a minute once built",
                    )
                ]
                if item.runnable
                else [],
                evidence=list(item.evidence),
                route=item.route,
            )
        ]
