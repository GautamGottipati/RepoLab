"""Python libraries: run a real example, edit it, run it again.

A library has no server and no UI, and is not "non-runnable" for that: people
use it by writing code against it. This adapter installs the package from the
investigated commit into an image (``pip install .``, cached per commit) and runs
a learner's snippet in a fresh, network-less container. The first snippet comes
from the README or ``examples/``, with its line; failing that, RepoLab's own
minimal import, labelled as such.
"""

from __future__ import annotations

from repolab.evidence.models import CodeReference
from repolab.experiences.adapters.base import DiscoveryContext, experience_id
from repolab.experiences.facts import python_image
from repolab.experiences.model import (
    ExperienceDefinition,
    ExperienceState,
    ExperienceType,
    Reality,
    RuntimeOption,
    StarterAction,
    Strategy,
)

NAME = "library"

#: Where the package is unpacked and installed inside its image.
SOURCE_ROOT = "/opt/repolab/repo"


class LibraryAdapter:
    name = NAME

    def propose(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        facts = context.manifest.facts
        if facts is None or facts.python is None or facts.spark is not None:
            # A Spark distribution's Python API is the Spark adapter's to offer.
            return []
        package = facts.python
        build = context.builds.get((NAME, Strategy.SOURCE_BUILD.value))
        image, why = python_image(package.requires_python)
        starters = [
            StarterAction(
                id=f"readme-{i}",
                title="From the README",
                code=snippet.code,
                source="readme",
                evidence=[snippet.reference],
            )
            for i, snippet in enumerate(package.snippets[:2])
        ]
        starters += [
            StarterAction(
                id=f"example-{i}",
                title=f"examples: {path.rsplit('/', 1)[-1]}",
                code=f'import runpy\n\nrunpy.run_path("{SOURCE_ROOT}/{path}", run_name="__main__")',
                source="examples",
                description="The repository's own example script, run as it is.",
                evidence=[CodeReference(path=path)],
            )
            for i, path in enumerate(package.examples[:2])
        ]
        if not starters:
            starters.append(
                StarterAction(
                    id="import",
                    title="Import it",
                    code=f"import {package.import_name}\nprint({package.import_name}.__name__)\n"
                    f"print([n for n in dir({package.import_name}) if not n.startswith('_')][:40])",
                    source="repolab",
                    description="RepoLab's starter: no README example imports the package.",
                )
            )
        eid = experience_id(NAME, "playground")
        running = eid in context.sessions
        return [
            ExperienceDefinition(
                id=eid,
                title="Try the Library",
                experience_type=ExperienceType.LIBRARY_PLAYGROUND,
                adapter=NAME,
                summary=f"Write Python against {package.distribution}, installed from this commit.",
                what_you_do="Run an example, change it, run it again.",
                what_you_learn=f"What {package.import_name} gives you, in its own API.",
                what_to_notice="Errors and output are the library's own.",
                surfaces=[s for s in ("notebook", "cli") if s in context.surface_confidence],
                state=ExperienceState.RUNNING if running else ExperienceState.RUNNABLE,
                options=[
                    RuntimeOption(
                        strategy=Strategy.SOURCE_BUILD,
                        reality=Reality.REAL_REPOSITORY,
                        label=f"{package.distribution} installed from this commit ({image}; {why})",
                        ready=build == "ready",
                        estimate="~10 seconds"
                        if build == "ready"
                        else "about a minute to install the first time",
                        disclosure="The last install failed; its log is in the session."
                        if build == "failed"
                        else None,
                        evidence=[package.manifest],
                    )
                ],
                starter_actions=starters[:4],
                evidence=[package.manifest],
                session_id=context.sessions.get(eid),
            )
        ]
