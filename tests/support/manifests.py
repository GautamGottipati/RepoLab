"""Manifest fixtures, recorded from real ingestions.

The Airflow manifest below is not invented: it is what RepoLab actually detected
in `apache/airflow` at `a8a62573f54f` — the same four surfaces, the same
confidences, the same reasons, and real files from its own evidence. A fixture
that made up a cleaner repository would prove the catalog works on repositories
that do not exist.
"""

from __future__ import annotations

from repolab.evidence.models import CodeReference
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Classification,
    EntryPoint,
    EntryPointKind,
    Fidelity,
    InteractionSurface,
    RepoManifest,
    SurfaceType,
)

AIRFLOW_REPO = "https://github.com/apache/airflow"
AIRFLOW_SHA = "a8a62573f54f0000000000000000000000000000"

SPARK_REPO = "https://github.com/AlexIoannides/pyspark-example-project"


def airflow_surfaces() -> list[InteractionSurface]:
    """The four surfaces `apache/airflow` really produced, in confidence order."""
    return [
        InteractionSurface(
            type=SurfaceType.CLI,
            name="CLI playground (interactive shell against a server)",
            confidence=0.95,
            primary=True,
            reason=(
                "detected from interactive_shell, framework:Click, framework:Typer, "
                "framework:Airflow; corroborated by cli_script, server+shell"
            ),
            evidence=[
                CodeReference(path="airflow-core/docs/howto/usage-cli.rst"),
                CodeReference(path=".github/workflows/backport-cli.yml"),
                CodeReference(path="dev/breeze/doc/images/output_k8s_shell.svg"),
            ],
        ),
        InteractionSurface(
            type=SurfaceType.WEB,
            name="Web application",
            confidence=0.70,
            reason=(
                "detected from framework:Airflow, framework:Flask, framework:React; "
                "corroborated by web_server"
            ),
            evidence=[
                CodeReference(path="airflow-core/pyproject.toml"),
                CodeReference(path="providers/amazon/pyproject.toml"),
            ],
        ),
        InteractionSurface(
            type=SurfaceType.API,
            name="HTTP API",
            confidence=0.45,
            reason=(
                "detected from framework:FastAPI, framework:Starlette; corroborated by web_server"
            ),
            evidence=[
                CodeReference(path="airflow-core/pyproject.toml"),
                CodeReference(
                    path="providers/informatica/dev/informatica_simulator/requirements.txt",
                    start_line=2,
                ),
            ],
        ),
        InteractionSurface(
            type=SurfaceType.NOTEBOOK,
            name="Notebook / library exploration",
            confidence=0.35,
            reason="detected from notebook_file, library_package",
            evidence=[
                CodeReference(path="dev/stats/explore_pr_candidates.ipynb"),
                CodeReference(path="airflow-core/src/airflow/__init__.py"),
            ],
        ),
    ]


def airflow_manifest(repo_url: str = AIRFLOW_REPO, sha: str = AIRFLOW_SHA) -> RepoManifest:
    """A hybrid repository: four surfaces, marked hybrid, none of them buildable."""
    surfaces = airflow_surfaces()
    return RepoManifest(
        repo_url=repo_url,
        commit_sha=sha,
        languages=["Python", "TypeScript"],
        frameworks=["FastAPI", "Starlette", "React", "Click", "Typer", "Airflow", "Flask"],
        entrypoints=[
            EntryPoint(name="airflow", command="airflow", kind=EntryPointKind.CLI_SCRIPT),
            EntryPoint(name="npm run dev", command="npm run dev", kind=EntryPointKind.CLI_SCRIPT),
        ],
        interaction_surfaces=surfaces,
        classification=Classification(
            surfaces=surfaces,
            primary_type=SurfaceType.CLI,
            rationale="cli scored highest (0.95 vs 0.7 for web); detected from interactive_shell",
            hybrid=True,
        ),
        build_strategy=BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        confidence=0.62,
    )


def spark_manifest(repo_url: str = SPARK_REPO, sha: str = "a" * 40) -> RepoManifest:
    """A Spark repository: a data playground RepoLab can build, and a weak CLI."""
    surfaces = [
        InteractionSurface(
            type=SurfaceType.DATA_PLAYGROUND,
            name="Spark data playground",
            confidence=0.95,
            primary=True,
            reason="detected from spark_session, spark_import; corroborated by spark_job",
            evidence=[CodeReference(path="jobs/etl_job.py", start_line=42)],
        ),
        InteractionSurface(
            type=SurfaceType.CLI,
            name="Command line interface",
            confidence=0.20,
            reason="detected from sys_argv",
            evidence=[CodeReference(path="jobs/etl_job.py", start_line=110)],
        ),
    ]
    return RepoManifest(
        repo_url=repo_url,
        commit_sha=sha,
        languages=["Python"],
        frameworks=["Spark"],
        entrypoints=[
            EntryPoint(
                name="etl_job",
                command="spark-submit jobs/etl_job.py",
                path="jobs/etl_job.py",
                kind=EntryPointKind.SPARK_JOB,
            )
        ],
        interaction_surfaces=surfaces,
        classification=Classification(
            surfaces=surfaces,
            primary_type=SurfaceType.DATA_PLAYGROUND,
            rationale="data_playground scored highest (0.95 vs 0.2 for cli)",
        ),
        build_strategy=BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        confidence=0.7,
    )
