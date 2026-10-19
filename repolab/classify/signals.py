"""The signal vocabulary the classifier reasons over.

Weights exist because signals are not equal. One ``SparkSession.builder`` says
far more about a repository than fifty mentions of ``sys.argv``, and a framework
named in a dependency manifest is weaker evidence than the same framework being
constructed in code. Scoring by weighted *distinct kinds* rather than raw hit
count is what keeps a large repository from saturating every surface at once.
"""

from __future__ import annotations

from repolab.manifest.models import SurfaceType

#: Evidence strong enough to identify a surface on its own.
STRONG = 0.45
#: Evidence that clearly points at a surface but could be incidental.
MEDIUM = 0.25
#: Corroborating evidence only. Never enough by itself.
WEAK = 0.10

#: Confidence at or above which a surface is considered strongly supported. This
#: governs the model gate: strong evidence is never sent to a model.
STRONG_CONFIDENCE = 0.80

#: How close two surfaces must be before the choice between them counts as a tie.
TIE_MARGIN = 0.15

#: Confidence at or above which a surface is substantial enough to be worth
#: offering as its own experience. Deliberately lower than STRONG_CONFIDENCE:
#: "is this worth building an experience for?" is a different question from "is
#: this certain enough that a model cannot help?". Airflow's web UI sits here.
HYBRID_CONFIDENCE = 0.60

#: Ceiling on any single surface's confidence: deterministic rules alone should
#: never claim certainty.
MAX_CONFIDENCE = 0.95

#: Detector finding kind -> (surface it supports, weight of that evidence).
SIGNAL_SURFACES: dict[str, tuple[SurfaceType, float]] = {
    # Spark / data pipeline
    "spark_session": (SurfaceType.DATA_PLAYGROUND, STRONG),
    "spark_context": (SurfaceType.DATA_PLAYGROUND, STRONG),
    "spark_import": (SurfaceType.DATA_PLAYGROUND, MEDIUM),
    "spark_scala_import": (SurfaceType.DATA_PLAYGROUND, MEDIUM),
    "spark_submit": (SurfaceType.DATA_PLAYGROUND, MEDIUM),
    "spark_config": (SurfaceType.DATA_PLAYGROUND, WEAK),
    # HTTP
    "fastapi_app": (SurfaceType.API, STRONG),
    "express_app": (SurfaceType.API, STRONG),
    "flask_app": (SurfaceType.WEB, STRONG),
    "django_urls": (SurfaceType.WEB, STRONG),
    "route_decorator": (SurfaceType.API, MEDIUM),
    "uvicorn_run": (SurfaceType.API, MEDIUM),
    "port": (SurfaceType.API, WEAK),
    "exposed_ports": (SurfaceType.API, WEAK),
    # Command line
    "click_command": (SurfaceType.CLI, STRONG),
    "typer_app": (SurfaceType.CLI, STRONG),
    "argparse": (SurfaceType.CLI, MEDIUM),
    "bin_executable": (SurfaceType.CLI, MEDIUM),
    "shell_script": (SurfaceType.CLI, WEAK),
    "sys_argv": (SurfaceType.CLI, WEAK),
    # Interactive shell and long-running servers. Together these make a CLI
    # playground rather than a one-shot command.
    "interactive_shell": (SurfaceType.CLI, STRONG),
    "server_daemon": (SurfaceType.CLI, WEAK),
    # Notebook / library
    "notebook_file": (SurfaceType.NOTEBOOK, MEDIUM),
    "library_package": (SurfaceType.NOTEBOOK, WEAK),
}

#: Frameworks named in a dependency manifest. Real, citable evidence -- a bounded
#: code scan may never reach the application module on a huge repository -- but
#: weaker than seeing the framework constructed.
FRAMEWORK_SURFACES: dict[str, tuple[tuple[SurfaceType, float], ...]] = {
    "Spark": ((SurfaceType.DATA_PLAYGROUND, MEDIUM),),
    "FastAPI": ((SurfaceType.API, MEDIUM),),
    "Starlette": ((SurfaceType.API, WEAK),),
    "Flask": ((SurfaceType.WEB, MEDIUM),),
    "Django": ((SurfaceType.WEB, MEDIUM),),
    "Tornado": ((SurfaceType.WEB, WEAK),),
    "Express": ((SurfaceType.API, MEDIUM),),
    "Next.js": ((SurfaceType.WEB, MEDIUM),),
    "React": ((SurfaceType.WEB, WEAK),),
    "Vue": ((SurfaceType.WEB, WEAK),),
    # Airflow genuinely ships both a web UI and a CLI.
    "Airflow": ((SurfaceType.WEB, MEDIUM), (SurfaceType.CLI, MEDIUM)),
    "Click": ((SurfaceType.CLI, MEDIUM),),
    "Typer": ((SurfaceType.CLI, MEDIUM),),
}

#: Entry-point kinds that corroborate a surface already suggested by a signal.
ENTRYPOINT_SURFACES: dict[str, tuple[SurfaceType, ...]] = {
    "spark_job": (SurfaceType.DATA_PLAYGROUND,),
    "web_server": (SurfaceType.WEB, SurfaceType.API),
    "cli_script": (SurfaceType.CLI,),
}

#: Bonus applied once per surface when an entry point of a matching kind exists.
CORROBORATION_BONUS = 0.10

#: Which experience is the richer thing to learn, used only to break a genuine
#: tie. Earlier is preferred.
SURFACE_PRECEDENCE: tuple[SurfaceType, ...] = (
    SurfaceType.DATA_PLAYGROUND,
    SurfaceType.WEB,
    SurfaceType.API,
    SurfaceType.CLI,
    SurfaceType.NOTEBOOK,
    SurfaceType.TERMINAL,
)

#: Signal kinds that mean the repository ships something runnable, so it is not
#: "a library without a primary executable".
EXECUTABLE_SIGNAL_KINDS: frozenset[str] = frozenset(
    {
        "entrypoint",
        "bin_executable",
        "shell_script",
        "spark_submit",
        "interactive_shell",
        "server_daemon",
        "fastapi_app",
        "flask_app",
        "django_urls",
        "express_app",
        "uvicorn_run",
        "click_command",
        "typer_app",
        "argparse",
    }
)


def precedence_rank(surface: SurfaceType) -> int:
    """Position of *surface* in :data:`SURFACE_PRECEDENCE`; lower wins."""
    try:
        return SURFACE_PRECEDENCE.index(surface)
    except ValueError:
        return len(SURFACE_PRECEDENCE)
