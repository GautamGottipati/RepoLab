"""From a repository to the few things worth doing with it, best first.

Every adapter proposes what it can, from evidence. Ranking is deterministic and
its signals are kept on each experience, so "why is this first?" has an answer:

    representative   how strongly the evidence says this *is* the software
    runnable now     running > runnable > supported > detected
    quick            a ready runtime needs no build before the first result
    recommended      the README's own material points at this workflow

At most four runnable experiences are offered; the first is the **primary
experience**, the answer to "I have five minutes -- what should I try?". Anything
detected but not runnable is kept, collapsed, with the reason. When nothing real
can run, a Mini Lab is offered instead -- labelled as a simulation.
"""

from __future__ import annotations

from repolab.experiences.adapters import ADAPTERS, DiscoveryContext
from repolab.experiences.adapters.base import experience_id, readme_mentions
from repolab.experiences.model import (
    ExperienceDefinition,
    ExperienceList,
    ExperienceState,
    ExperienceType,
    Reality,
    RuntimeOption,
    Strategy,
)

MAX_EXPERIENCES = 4

#: How representative each kind is when its own evidence is present.
_CLAIM = {
    ExperienceType.WEB_APP: 2.6,
    ExperienceType.DATA_PLAYGROUND: 2.5,
    ExperienceType.HTTP_API: 2.3,
    ExperienceType.LIBRARY_PLAYGROUND: 2.0,
    ExperienceType.CLI: 2.0,
    ExperienceType.QUERY_CONSOLE: 1.8,
}

_STATE = {
    ExperienceState.RUNNING: 3.0,
    ExperienceState.RUNNABLE: 2.0,
    ExperienceState.SUPPORTED: 0.5,
    ExperienceState.DETECTED: 0.0,
}

SUPPORTED_KINDS = [
    "Spark data playgrounds (a learner's PySpark, measured from Spark's event log)",
    "Node.js web applications and HTTP APIs",
    "Python libraries (an editable, runnable example)",
    "The Cassandra command line, with a guided lesson",
]


def discover(context: DiscoveryContext) -> ExperienceList:
    proposed: list[ExperienceDefinition] = []
    for adapter in ADAPTERS:
        for position, experience in enumerate(adapter.propose(context)):
            _rank(context, experience, position)
            proposed.append(experience)

    proposed.sort(key=lambda e: -e.rank)
    main = [e for e in proposed if e.state in {ExperienceState.RUNNABLE, ExperienceState.RUNNING}]
    also = [e for e in proposed if e not in main]
    main = main[:MAX_EXPERIENCES]
    if main:
        main[0].primary = True

    understanding = context.architecture.understanding if context.architecture else None
    name = (understanding.name if understanding else None) or context.manifest.repo_url.rstrip(
        "/"
    ).rsplit("/", 1)[-1]
    listing = ExperienceList(
        repo_url=context.manifest.repo_url,
        name=name,
        headline=(
            f"Experience {name}: do something real with it, then see how it works."
            if main
            else f"{name} cannot run in RepoLab yet."
        ),
        experiences=main,
        also=also,
        supported_kinds=SUPPORTED_KINDS,
    )
    if not main:
        listing.nothing_runs_because = [f"{e.title}: {e.why_not}" for e in also if e.why_not] or [
            "RepoLab found no way of using this repository that it can run."
        ]
        listing.fallback = mini_lab(context)
    return listing


def mini_lab(context: DiscoveryContext) -> ExperienceDefinition:
    eid = experience_id("minilab", "lab")
    return ExperienceDefinition(
        id=eid,
        title="Guided Mini Lab",
        experience_type=ExperienceType.MINI_LAB,
        adapter="minilab",
        summary="An educational simulation of the core ideas, tied to the real source.",
        what_you_do="Type the commands the lab suggests and read what they would do.",
        what_you_learn="The concepts the repository is built around, and where they live in code.",
        what_to_notice="Nothing here runs the repository. Unknown commands say so.",
        state=ExperienceState.RUNNING if eid in context.sessions else ExperienceState.RUNNABLE,
        options=[
            RuntimeOption(
                strategy=Strategy.MINI_LAB,
                reality=Reality.MINI_LAB,
                label="Simulated / educational -- does not run this repository",
                ready=True,
                estimate="~30 seconds to generate",
            )
        ],
        session_id=context.sessions.get(eid),
    )


def _rank(context: DiscoveryContext, experience: ExperienceDefinition, position: int) -> None:
    confidence = max(
        (context.surface_confidence.get(s, 0.0) for s in experience.surfaces), default=0.0
    )
    signals = {
        "representative": _CLAIM.get(experience.experience_type, 1.0) + confidence,
        "state": _STATE[experience.state],
        "quick": 1.0 if any(o.ready for o in experience.options) else 0.0,
        "recommended": readme_mentions(context, *_README_WORDS.get(experience.adapter, ())),
        "order": -0.3 * position,
    }
    signals.update(experience.rank_signals)
    experience.rank_signals = {k: round(v, 2) for k, v in signals.items()}
    experience.rank = round(sum(signals.values()), 2)


_README_WORDS = {"spark": ("spark.",), "library": ("import",)}
