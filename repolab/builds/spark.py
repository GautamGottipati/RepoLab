"""The Spark build plan.

The demo repository pins ``pyspark==2.4.0`` and Python 3.6, a combination that
cannot be installed on a current base image. Rather than fail, the build
substitutes a modern Spark and **discloses the substitution**: the artifact records
each change as a deviation, which drops its fidelity below ``EXACT``. A later
experiment therefore reports the version it actually measured rather than the one
the repository names.

The smoke test is a real Spark job -- build a small DataFrame, aggregate it, print
a marker -- because CLAUDE.md requires that a build is ready only once a tiny Spark
job completes.
"""

from __future__ import annotations

import os

from repolab.builds.plan import BuildPlan, BuildStep, SmokeTest
from repolab.manifest.models import RepoManifest

#: Base image. Overridable so a prepared image carrying a JVM, Python and Spark can
#: be used where the sandbox has no outbound network -- egress is undocumented.
DEFAULT_BASE_IMAGE = os.environ.get("REPOLAB_SPARK_BASE_IMAGE", "docker://apache/spark-py:v3.4.0")

#: The Spark version RepoLab installs when it has to substitute one.
SUBSTITUTE_PYSPARK = "3.5.1"
SUBSTITUTE_PYTHON = "3.11"

#: Printed by the smoke job on success. Checked for, so a zero exit code alone is
#: not taken as proof the job ran.
SMOKE_MARKER = "REPOLAB_SPARK_OK"

SMOKE_SCRIPT = f"""
from pyspark.sql import SparkSession, functions as F

spark = (
    SparkSession.builder.appName("repolab-smoke")
    .master("local[1]")
    .config("spark.ui.enabled", "false")
    .getOrCreate()
)
rows = [("a", 1), ("a", 2), ("b", 3)]
frame = spark.createDataFrame(rows, ["key", "value"])
totals = frame.groupBy("key").agg(F.sum("value").alias("total")).collect()
by_key = {{row["key"]: row["total"] for row in totals}}
assert by_key == {{"a": 3, "b": 3}}, by_key
print("{SMOKE_MARKER}", spark.version)
spark.stop()
"""

#: Dependency pins the repository declares that cannot be honoured.
UNSUPPORTED_PINS = {"pyspark": "2.4.0"}


def _declared_pyspark(manifest: RepoManifest) -> str | None:
    for dependency in manifest.dependencies:
        if dependency.name.lower() == "pyspark":
            return dependency.version_spec
    return None


def spark_plan(manifest: RepoManifest, *, base_image: str | None = None) -> BuildPlan:
    """A build plan for a Spark repository."""
    image = base_image or DEFAULT_BASE_IMAGE
    declared = _declared_pyspark(manifest)

    deviations: list[str] = []
    limitations: list[str] = []
    if declared and any(pin in declared for pin in UNSUPPORTED_PINS.values()):
        # Render the pin readably rather than echoing the raw specifier ("==2.4.0").
        pinned = declared.lstrip("=<>~!").strip()
        deviations.append(
            f"pyspark {pinned} -> {SUBSTITUTE_PYSPARK} (the pinned version requires Python 3.6)"
        )
        deviations.append(f"python 3.6 -> {SUBSTITUTE_PYTHON}")
        limitations.append(
            "not the dependency versions the repository pins; measurements describe "
            f"pyspark {SUBSTITUTE_PYSPARK}"
        )

    steps = [
        BuildStep(
            name="check the toolchain",
            command="python3 --version && java -version 2>&1 | head -1",
            timeout_seconds=120.0,
        ),
        BuildStep(
            name="install pyspark",
            command=(
                f"python3 -m pip install --no-input --quiet 'pyspark=={SUBSTITUTE_PYSPARK}' "
                "|| python3 -c 'import pyspark; print(pyspark.__version__)'"
            ),
            timeout_seconds=900.0,
        ),
        BuildStep(
            name="record versions",
            command=(
                'python3 -c "import sys, pyspark; '
                "print('python', sys.version.split()[0]); "
                "print('pyspark', pyspark.__version__)\""
            ),
            timeout_seconds=120.0,
        ),
        BuildStep(
            name="write the smoke job",
            # A heredoc, so nothing from the repository is interpolated.
            command=(
                "mkdir -p /work && cat > /work/repolab_smoke.py <<'REPOLAB_EOF'\n"
                f"{SMOKE_SCRIPT}"
                "REPOLAB_EOF"
            ),
            timeout_seconds=120.0,
        ),
    ]

    return BuildPlan(
        base_image=image,
        setup=[
            BuildStep(
                name="prepare the workspace",
                command="mkdir -p /work",
                timeout_seconds=60.0,
            )
        ],
        steps=steps,
        smoke=SmokeTest(
            name="a tiny Spark job completes",
            command="python3 /work/repolab_smoke.py",
            expect_stdout_contains=SMOKE_MARKER,
            timeout_seconds=600.0,
        ),
        deviations=deviations,
        capabilities=[
            "run pyspark jobs locally",
            "inspect Spark configuration at runtime",
        ],
        limitations=limitations,
    )
