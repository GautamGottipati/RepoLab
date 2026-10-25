"""Web applications and HTTP APIs: use the real thing, through RepoLab.

Re-expresses the Phase 18 live runtime as experiences -- *Use the Application*,
*Try the API* -- without rebuilding it: the plan, the launch, the sidecar and the
proxy are :mod:`repolab.runtime`'s. Starter actions are the routes the source
declares, never invented ones.
"""

from __future__ import annotations

from repolab.evidence.models import CodeReference
from repolab.experiences.adapters.base import DiscoveryContext, experience_id
from repolab.experiences.model import (
    ExperienceDefinition,
    ExperienceState,
    ExperienceType,
    Reality,
    RuntimeOption,
    StarterAction,
    Strategy,
)
from repolab.manifest.models import SurfaceType

NAME = "live"


class LiveAppAdapter:
    name = NAME

    def propose(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        proposed: list[ExperienceDefinition] = []
        plan = context.manifest.runtime
        for surface, slug, title, kind in (
            (SurfaceType.WEB, "app", "Use the Application", ExperienceType.WEB_APP),
            (SurfaceType.API, "api", "Try the API", ExperienceType.HTTP_API),
        ):
            item = context.catalog.of(surface)
            if item is None:
                continue
            services = plan.for_surface(surface) if plan else []
            running = any(context.live_services.get(s.name) == "running" for s in services)
            starters: list[StarterAction] = []
            for service in services:
                for route in [r for r in service.routes if r.method == "GET"][:3] + [
                    r for r in service.routes if r.method != "GET"
                ][:1]:
                    starters.append(
                        StarterAction(
                            id=f"{route.method} {route.path}",
                            title=f"{route.method} {route.path}",
                            code=f"{route.method} {route.path}",
                            source="routes",
                            evidence=[CodeReference(path=route.file, start_line=route.line)],
                        )
                    )
            if surface is SurfaceType.WEB:
                summary = "Run the real application in an isolated sandbox and use it."
                do = "Open the running application and click around."
                learn = "What the software does for its users, before reading how."
                notice = "Which screens need the backend, and which work on their own."
            else:
                summary = "Send real requests to the running API and read its responses."
                do = "Pick a route the code declares, send a request, read the response."
                learn = "What the API offers, and how its routes map to the code."
                notice = "Which routes answer at once, and which wait on a dependency."
            proposed.append(
                ExperienceDefinition(
                    id=experience_id(NAME, slug),
                    title=title,
                    experience_type=kind,
                    adapter=NAME,
                    summary=summary,
                    what_you_do=do,
                    what_you_learn=learn,
                    what_to_notice=notice,
                    surfaces=[surface.value],
                    state=(
                        ExperienceState.RUNNING
                        if running
                        else ExperienceState.RUNNABLE
                        if item.runnable
                        else ExperienceState.SUPPORTED
                    ),
                    why_not=None if item.runnable else item.unavailable_reason,
                    options=[
                        RuntimeOption(
                            strategy=Strategy.SOURCE_BUILD,
                            reality=Reality.REAL_REPOSITORY,
                            label="This commit, installed and started from its own scripts",
                            ready=False,
                            estimate="a few minutes the first time; seconds once installed",
                        )
                    ]
                    if item.runnable
                    else [],
                    starter_actions=starters[:4],
                    evidence=list(item.evidence),
                    route="#live" if item.runnable else None,
                )
            )
        return proposed
