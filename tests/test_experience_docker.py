"""Experiences against real containers. Opt-in: ``pytest -m docker``.

A learner's PySpark on the official Spark image, and a Python snippet in a
network-less container, both measured from what really ran.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repolab.experiences.adapters.spark import OFFICIAL_IMAGE, OFFICIAL_SPARK_SUBMIT
from repolab.experiences.library_runner import run_snippet
from repolab.experiences.spark.snippet import SnippetConfig, SnippetRunner
from repolab.experiences.spark.understanding import map_run
from repolab.sandbox.docker import DockerEnvironment, docker_available

pytestmark = [pytest.mark.docker, pytest.mark.skipif(not docker_available(), reason="needs Docker")]


def test_a_learners_pyspark_runs_and_is_measured_from_the_event_log(tmp_path: Path) -> None:
    code = (
        'result = df.filter(F.col("age") > 18).groupBy("country")'
        '.agg(F.sum("amount").alias("total")).orderBy(F.desc("total"))'
    )
    result = asyncio.run(
        SnippetRunner(DockerEnvironment(hardened=True), runs_root=tmp_path).run(
            image=OFFICIAL_IMAGE,
            code=code,
            spark_submit=OFFICIAL_SPARK_SUBMIT,
            config=SnippetConfig(shuffle_partitions=4),
        )
    )
    assert result.ok, result.error
    assert result.columns == ["country", "total"]
    assert result.rows[0][0] == "US"  # the seeded dataset is US-heavy
    assert result.measurements is not None
    assert result.jobs >= 1 and result.measurements.stage_count >= 2
    assert result.measurements.distribution.partitions == 4
    assert result.event_log_sha256
    steps = [step.title for step in map_run(result.model_dump(mode="json"), None)]
    assert "Query planning" in steps and "Scheduler" in steps


def test_a_python_snippet_has_no_network(tmp_path: Path) -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('pypi.org', 443), timeout=3)\n"
        "    print('NETWORK')\n"
        "except OSError as exc:\n"
        "    print('no network:', type(exc).__name__)\n"
    )
    result = asyncio.run(
        run_snippet(DockerEnvironment(hardened=True), image="python:3.12-slim", code=code)
    )
    assert result.ok
    assert result.stdout.startswith("no network")
