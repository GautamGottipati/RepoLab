"""The Spark driver RepoLab runs inside the environment.

The driver is generated as a script and executed by ``spark-submit``. It does
three things, in this order:

1. reads the deterministic synthetic dataset;
2. calls **the repository's own** ``transform_data()``;
3. adds a ``groupBy`` aggregation on the skewed key.

Step 3 is RepoLab's addition, and it is disclosed wherever these measurements are
reported. It exists because the demo repository's job is a ``select`` plus a
``concat`` with no shuffle at all, so shuffle read/write, spill and partition skew
would be structurally zero and the question "why 200 shuffle partitions?" would
have nothing to measure.

If the repository's function cannot be imported the driver exits non-zero rather
than substituting RepoLab's own transform: measurements that silently described
different code would be worse than no measurements.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Printed when the driver completes, so success is confirmed rather than inferred
#: from an exit code.
SUCCESS_MARKER = "REPOLAB_SPARK_RUN_OK"

#: Printed before the aggregation, making the addition visible in the log itself.
DISCLOSURE_MARKER = "REPOLAB_ADDED_AGGREGATION"

#: Spark's default, and the subject of the demo question.
DEFAULT_SHUFFLE_PARTITIONS = 200


class SparkRunConfig(BaseModel):
    """The configuration a run was executed with.

    Recorded alongside the measurements so a later comparison can state what
    changed between runs.
    """

    shuffle_partitions: int = DEFAULT_SHUFFLE_PARTITIONS
    master: str = "local[4]"
    app_name: str = "repolab-spark-demo"
    steps_per_floor: int = 21
    """The repository's own configuration value, from configs/etl_config.json."""

    driver_memory: str = "2g"
    extra_conf: dict[str, str] = Field(default_factory=dict)

    def spark_conf(self, event_log_dir: str) -> dict[str, str]:
        """Every Spark setting this run applies."""
        conf = {
            "spark.sql.shuffle.partitions": str(self.shuffle_partitions),
            # Event logging is what makes the measurements real.
            "spark.eventLog.enabled": "true",
            "spark.eventLog.dir": f"file://{event_log_dir}",
            "spark.ui.enabled": "false",
            # Adaptive execution would coalesce partitions and hide the very
            # distribution this run exists to measure.
            "spark.sql.adaptive.enabled": "false",
            "spark.driver.memory": self.driver_memory,
        }
        conf.update(self.extra_conf)
        return conf

    def submit_args(self, event_log_dir: str) -> list[str]:
        args = ["--master", self.master, "--name", self.app_name]
        for key, value in self.spark_conf(event_log_dir).items():
            args += ["--conf", f"{key}={value}"]
        return args


DRIVER_TEMPLATE = '''\
"""RepoLab Spark driver. Generated -- do not edit inside the container."""
import json
import sys

REPO_ROOT = {repo_root!r}
DATASET = {dataset!r}
OUTPUT = {output!r}
STEPS_PER_FLOOR = {steps_per_floor!r}
SKEW_KEY_COLUMN = {skew_column!r}

# The repository's own module must be importable, or this run is meaningless.
sys.path.insert(0, REPO_ROOT)

from pyspark.sql import SparkSession, functions as F

try:
    from jobs.etl_job import transform_data as repo_transform
except Exception as exc:  # the repository's code is the subject of measurement
    print("REPOLAB_IMPORT_FAILED", type(exc).__name__, exc, file=sys.stderr)
    raise SystemExit(3)

spark = SparkSession.builder.getOrCreate()
print("REPOLAB_SPARK_VERSION", spark.version)

schema = "id INT, first_name STRING, second_name STRING, floor INT, {skew_column} STRING"
frame = spark.read.csv(DATASET, header=True, schema=schema)

# The repository's transformation, run exactly as the repository defines it. It
# drops every column except id, name and steps_to_desk, so the grouping key is
# re-attached afterwards by joining on id -- the transform itself is untouched.
transformed = repo_transform(frame, STEPS_PER_FLOOR)
keys = frame.select("id", SKEW_KEY_COLUMN)
joined = transformed.join(keys, on="id", how="inner")

print("{disclosure}", "repartition + groupBy on", SKEW_KEY_COLUMN, "added by RepoLab")

# A bare groupBy is combined map-side, so only one partial row per key would
# cross the shuffle and the skew would be invisible. Repartitioning by the key
# first moves the actual rows, which is what produces measurable skew and the
# long tail of empty partitions that the shuffle partition count causes.
shuffled = joined.repartition(F.col(SKEW_KEY_COLUMN))
aggregated = shuffled.groupBy(SKEW_KEY_COLUMN).agg(
    F.count(F.lit(1)).alias("people"),
    F.sum("steps_to_desk").alias("total_steps"),
    F.avg("steps_to_desk").alias("avg_steps"),
)
aggregated.write.mode("overwrite").json(OUTPUT)

collected = aggregated.collect()
rows = sorted(
    (
        (row[SKEW_KEY_COLUMN], row["people"], int(row["total_steps"]))
        for row in collected
    ),
    key=lambda item: -item[1],
)
print("REPOLAB_RESULT", json.dumps(rows))
print("{success}")
spark.stop()
'''


def render_driver(
    *,
    repo_root: str,
    dataset_path: str,
    output_path: str,
    config: SparkRunConfig,
    skew_column: str = "dept",
) -> str:
    """Render the driver script for one run."""
    return DRIVER_TEMPLATE.format(
        repo_root=repo_root,
        dataset=dataset_path,
        output=output_path,
        steps_per_floor=config.steps_per_floor,
        skew_column=skew_column,
        disclosure=DISCLOSURE_MARKER,
        success=SUCCESS_MARKER,
    )


#: What RepoLab added to the repository's own pipeline, reported with the results.
ADDED_STEPS: tuple[str, ...] = (
    "repartition by the skewed key, added by RepoLab: it moves the real rows "
    "across the shuffle, where a bare groupBy would be combined map-side and "
    "leave the skew invisible",
    "groupBy on the skewed key with count/sum/avg aggregates, added by RepoLab: "
    "the repository's job performs no shuffle, so shuffle and skew metrics would "
    "otherwise be structurally zero",
    "a join back onto the id column to re-attach the grouping key, which the "
    "repository's transform_data drops",
)
