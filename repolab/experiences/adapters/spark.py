"""Spark: transform a dataset, query it with SQL, see how it is partitioned.

Two kinds of repository, told apart by evidence, never by name:

* **one that ships Spark itself** -- ``bin/spark-submit`` and a build file in the
  tree (:class:`~repolab.experiences.facts.SparkRuntimeFacts`). Its experiences
  run a learner's PySpark against a sample dataset, on Spark *built from the
  investigated commit* (REAL REPOSITORY) or, explicitly and disclosed, on an
  official release (OFFICIAL RUNTIME);
* **one that has a Spark job** -- the existing data playground, which runs the
  repository's own pipeline.

The starter snippets are RepoLab's and say so; the README's own quickstart is
offered with its line.
"""

from __future__ import annotations

import os

from repolab.experiences.adapters.base import DiscoveryContext, experience_id, readme_mentions
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

NAME = "spark"

#: The official Spark release used when the commit is not (yet) built.
OFFICIAL_IMAGE = os.environ.get("REPOLAB_SPARK_IMAGE", "apache/spark:3.5.1-python3")
OFFICIAL_SPARK_SUBMIT = "/opt/spark/bin/spark-submit"

#: Where a source build places the repository inside its image. Not under /work,
#: which a run mounts over.
SOURCE_ROOT = "/opt/repolab/repo"

TRANSFORM = """result = (
    df.filter(F.col("age") > 18)
      .groupBy("country")
      .agg(F.sum("amount").alias("total"), F.count("*").alias("orders"))
      .orderBy(F.desc("total"))
)"""

SQL = '''df.createOrReplaceTempView("sales")
result = spark.sql("""
    SELECT category, COUNT(*) AS orders, ROUND(AVG(amount), 1) AS avg_amount
    FROM sales
    WHERE age >= 18
    GROUP BY category
    ORDER BY orders DESC
""")'''

PARTITIONS = """# Change "Shuffle partitions" above and run again: the result stays the same,
# the work Spark does to produce it does not.
result = df.groupBy("country").agg(F.count("*").alias("orders"))"""


def official_version() -> str | None:
    import re

    match = re.search(r"(\d+\.\d+\.\d+)", OFFICIAL_IMAGE)
    return match.group(1) if match else None


class SparkAdapter:
    name = NAME

    def propose(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        facts = context.manifest.facts.spark if context.manifest.facts else None
        if facts is not None:
            return self._framework(context)
        return self._job(context)

    # -- a repository that ships Spark ---------------------------------------------

    def _framework(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        facts = context.manifest.facts.spark  # type: ignore[union-attr]
        assert facts is not None
        build = context.builds.get((NAME, Strategy.SOURCE_BUILD.value))
        version = official_version()
        source = RuntimeOption(
            strategy=Strategy.SOURCE_BUILD,
            reality=Reality.REAL_REPOSITORY,
            label=f"Spark built from this commit ({facts.version or 'this version'})",
            ready=build == "ready",
            estimate=(
                "~20 seconds"
                if build == "ready"
                else "building now"
                if build == "building"
                else "the first build takes 45-90 minutes; later runs take seconds"
            ),
            disclosure=(
                (
                    context.build_reasons.get((NAME, Strategy.SOURCE_BUILD.value))
                    or "The last source build failed; its log is in the session."
                )
                if build == "failed"
                else None
            ),
            evidence=[r for r in (facts.build_command_ref, facts.java_version_ref) if r],
        )
        official = RuntimeOption(
            strategy=Strategy.OFFICIAL_RELEASE,
            reality=Reality.OFFICIAL_RUNTIME,
            label=f"Official Apache Spark {version or 'release'}",
            ready=True,
            estimate="~20 seconds",
            disclosure=(
                f"An official Spark {version} release, not built from this commit"
                + (f" ({facts.version})" if facts.version else "")
                + ". Behaviour of unreleased changes will not show."
            ),
        )
        options = [source, official] if source.ready else [official, source]

        readme: list[StarterAction] = []
        if facts.quickstart is not None:
            readme.append(
                StarterAction(
                    id="readme",
                    title="The README's first example",
                    description="Exactly what the project's README suggests trying first, at a "
                    "size that finishes quickly.",
                    code="result = " + _smaller(facts.quickstart.code),
                    source="readme",
                    evidence=[facts.quickstart.reference],
                )
            )

        def define(
            slug: str, title: str, kind: ExperienceType, code: str, **text: str
        ) -> ExperienceDefinition:
            eid = experience_id(NAME, slug)
            running = eid in context.sessions
            starters = [
                StarterAction(
                    id=slug,
                    title=title,
                    code=code,
                    source="repolab",
                    description="RepoLab's starter, on a sample dataset RepoLab generates.",
                ),
                *(readme if slug == "transform" else []),
            ]
            return ExperienceDefinition(
                id=eid,
                title=title,
                experience_type=kind,
                adapter=NAME,
                surfaces=[
                    s
                    for s in ("data_playground", "cli", "notebook")
                    if s in context.surface_confidence
                ],
                state=ExperienceState.RUNNING if running else ExperienceState.RUNNABLE,
                options=options,
                starter_actions=starters,
                evidence=[r for r in (facts.version_ref, facts.build_command_ref) if r],
                session_id=context.sessions.get(eid),
                **text,
            )

        return [
            define(
                "transform",
                "Transform a Dataset",
                ExperienceType.DATA_PLAYGROUND,
                TRANSFORM,
                summary="Run a real Spark transformation and see what Spark does to compute it.",
                what_you_do="Filter and group a small sales table with the DataFrame API.",
                what_you_learn="A transformation describes a computation; Spark turns it into "
                "jobs, stages and tasks when you ask for a result.",
                what_to_notice="One groupBy becomes two stages, with a shuffle between them.",
            ),
            define(
                "sql",
                "Try Spark SQL",
                ExperienceType.QUERY_CONSOLE,
                SQL,
                summary="Query the same data with SQL and read the plan Spark chose.",
                what_you_do="Register the table and run a SQL query against it.",
                what_you_learn="SQL and the DataFrame API are planned by the same engine.",
                what_to_notice="The physical plan under the result is what actually ran.",
            ),
            define(
                "partitions",
                "Explore Partitions & Shuffles",
                ExperienceType.DATA_PLAYGROUND,
                PARTITIONS,
                summary="Change how many partitions a shuffle uses and measure what changes.",
                what_you_do="Run the same aggregation with different shuffle partition counts.",
                what_you_learn="Partitions are the unit of parallel work; their count is a "
                "trade-off, not a free setting.",
                what_to_notice="Tasks and runtime change while the answer does not.",
            ),
        ]

    # -- a repository with a Spark job ---------------------------------------------

    def _job(self, context: DiscoveryContext) -> list[ExperienceDefinition]:
        item = context.catalog.of(SurfaceType.DATA_PLAYGROUND)
        if item is None:
            return []
        eid = experience_id(NAME, "pipeline")
        if item.runnable:
            return [
                ExperienceDefinition(
                    id=eid,
                    title="Run the Repository's Pipeline",
                    experience_type=ExperienceType.DATA_PLAYGROUND,
                    adapter=NAME,
                    summary="Run this repository's own Spark job on a dataset, and measure it.",
                    what_you_do="Choose a configuration and run the repository's pipeline.",
                    what_you_learn="How this job reads, transforms and writes data in Spark.",
                    what_to_notice="Where the shuffle happens, and how evenly work is spread.",
                    surfaces=["data_playground"],
                    state=ExperienceState.RUNNABLE,
                    options=[
                        RuntimeOption(
                            strategy=Strategy.EXISTING,
                            reality=Reality.REAL_REPOSITORY,
                            label="The repository's own job",
                            ready=True,
                            estimate="~30 seconds",
                        )
                    ],
                    evidence=list(item.evidence),
                    route=item.route,
                    rank_signals={"readme": readme_mentions(context, "spark")},
                )
            ]
        return [
            ExperienceDefinition(
                id=eid,
                title="Run Spark",
                experience_type=ExperienceType.DATA_PLAYGROUND,
                adapter=NAME,
                summary="This repository uses Spark.",
                what_you_do="",
                what_you_learn="",
                surfaces=["data_playground"],
                state=ExperienceState.SUPPORTED,
                why_not=item.unavailable_reason.split(". ", 1)[-1] or None,
                evidence=list(item.evidence),
            )
        ]


def _smaller(code: str) -> str:
    """The README's example at a size a laptop sandbox finishes in seconds.

    ``spark.range(1000 * 1000 * 1000)`` becomes ``spark.range(1000 * 1000)``; the
    change is shown to the learner, since the code is theirs to read.
    """
    return code.replace("1000 * 1000 * 1000", "1000 * 1000")
