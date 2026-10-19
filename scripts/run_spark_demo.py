#!/usr/bin/env python
"""Run the Spark demo and print a metrics summary backed by real event logs.

    repo -> dataset -> real Spark run -> event log -> measurements

Usage:
    make run-spark-demo
    uv run python scripts/run_spark_demo.py [--rows N] [--partitions N]
                                            [--json out.json] [--persist]

Every figure printed is computed by Python from the event log Spark wrote. No
model is involved in producing a number.

The summary separates **deterministic** figures -- counts, bytes, the partition
distribution -- from **timings**, which legitimately vary between runs. Two runs
over the same inputs should agree exactly on the former and will not on the latter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repolab.experiences.spark import (  # noqa: E402
    ADDED_STEPS,
    SparkDemoRunner,
    SparkRunConfig,
    SparkRunFailed,
    SparkRunResult,
)
from repolab.ingest.clone import TemporaryClone  # noqa: E402
from repolab.ingest.limits import DEFAULT_LIMITS  # noqa: E402
from repolab.ingest.url import validate_repo_url  # noqa: E402
from repolab.sandbox.docker import DockerEnvironment, docker_available  # noqa: E402

DEMO_REPO = "https://github.com/AlexIoannides/pyspark-example-project"

#: Pinned: the event-log field names were verified against this Spark version's
#: JsonProtocol source.
DEMO_IMAGE = "apache/spark:3.5.1-python3"


def human_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(value) < 1024 or unit == "GiB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GiB"


def print_summary(result: SparkRunResult) -> None:
    measurements = result.measurements
    distribution = measurements.distribution
    tasks = measurements.tasks

    print()
    print("run")
    print(f"  repository     {result.repo_url}")
    print(f"  commit         {result.commit_sha[:12]}")
    print(f"  environment    {result.environment.kind} / {result.environment.image}")
    print(f"  spark          {measurements.spark_version or 'unknown'}")
    print(f"  app id         {measurements.app_id or 'unknown'}")
    print()
    print("inputs")
    print(f"  dataset        {result.dataset.describe()}")
    print(f"  dataset hash   {result.dataset.sha256}")
    print(f"  partitions     spark.sql.shuffle.partitions={result.config.shuffle_partitions}")
    print(f"  master         {result.config.master}")
    print()
    print("measurements -- deterministic (should repeat across identical runs)")
    print(f"  stages                 {measurements.stage_count}")
    print(f"  tasks                  {measurements.task_count}")
    print(f"  failed tasks           {measurements.failed_task_count}")
    print(f"  shuffle read           {human_bytes(measurements.shuffle_bytes_read)}")
    print(f"  shuffle write          {human_bytes(measurements.shuffle_bytes_written)}")
    print(f"  shuffle records read   {measurements.shuffle_records_read:,}")
    print(f"  memory spilled         {human_bytes(measurements.memory_bytes_spilled)}")
    print(f"  disk spilled           {human_bytes(measurements.disk_bytes_spilled)}")
    print(f"  shuffle partitions     {distribution.partitions}")
    print(
        f"  empty partitions       {distribution.empty_partitions}"
        f" ({distribution.empty_fraction:.1%})"
    )
    print(f"  heaviest partition     {distribution.max_records:,} records")
    print(f"  median non-empty       {distribution.median_records:,.0f} records")
    print(f"  partition skew ratio   {distribution.record_skew_ratio}x")
    print(f"  top partitions         {distribution.top_records}")
    print()
    print("measurements -- timing (varies between runs)")
    total = measurements.total_runtime_ms
    print(f"  total runtime          {total:,} ms" if total else "  total runtime          unknown")
    print(f"  slowest task           {tasks.slowest_ms:,} ms (task {tasks.slowest_task_id})")
    print(f"  median task            {tasks.median_ms:,.1f} ms")
    print(f"  p95 task               {tasks.p95_ms:,.1f} ms")
    print(f"  task duration stddev   {tasks.stddev_ms:,.1f} ms")
    print(f"  task skew ratio        {tasks.skew_ratio}x")
    print(f"  wall clock (end to end){result.wall_clock_seconds:>8.1f} s")
    print()
    print("stages")
    for stage in measurements.stages:
        marker = "shuffle" if stage.shuffled else "       "
        duration = f"{stage.duration_ms:,} ms" if stage.duration_ms is not None else "?"
        print(
            f"  [{stage.stage_id}] {marker} {stage.num_tasks:>4} tasks  {duration:>10}  "
            f"read {human_bytes(stage.shuffle_bytes_read):>12}  "
            f"write {human_bytes(stage.shuffle_bytes_written):>12}  {stage.name[:34]}"
        )
    print()
    print("evidence")
    print(f"  measurements   {len(result.evidence.measurements)} (source: spark event log)")
    print(f"  code refs      {[ref.path for ref in result.evidence.code_references]}")
    print(f"  logs           {[ref.source for ref in result.evidence.logs]}")
    print(f"  simulated      {result.evidence.simulated}")
    print()
    print("raw measurement source")
    print(f"  event log      {result.event_log_path}")
    print(f"  size / sha256  {result.event_log_bytes:,} bytes / {result.event_log_sha256[:16]}")
    print(
        f"  lines parsed   {measurements.event_log_lines}"
        f" (skipped {measurements.event_log_skipped_lines})"
    )
    print()
    print("added by RepoLab, not present in the repository")
    for item in ADDED_STEPS:
        print(f"  - {item}")


async def run(args: argparse.Namespace) -> int:
    if not docker_available():
        print(
            "docker is not available; this demo runs Spark in a container so that "
            "repository code never executes on the host",
            file=sys.stderr,
        )
        return 2

    repo_url = validate_repo_url(args.url)
    config = SparkRunConfig(shuffle_partitions=args.partitions)

    print(f"image          {args.image}")
    print(f"cloning        {repo_url.slug}")

    with TemporaryClone(repo_url, limits=DEFAULT_LIMITS) as clone:
        environment = DockerEnvironment()
        runner = SparkDemoRunner(environment, runs_root=args.runs_dir)
        try:
            result = await runner.run(
                repo_url=repo_url.normalized,
                commit_sha=clone.commit_sha,
                repository_root=Path(clone.path),
                image=args.image,
                config=config,
                rows=args.rows,
                seed=args.seed,
            )
        except SparkRunFailed as exc:
            print(f"SPARK RUN FAILED  {exc}", file=sys.stderr)
            return 1

    print_summary(result)

    if args.json:
        args.json.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
        print(f"\nfull result written to {args.json}")

    if args.persist:
        from repolab.config import get_settings
        from repolab.db import build_engine, session_scope
        from repolab.db.runs import save_spark_run

        engine = build_engine(get_settings())
        try:
            with session_scope(engine) as session:
                record = save_spark_run(session, result)
                print(f"persisted spark_run id={record.id} run_id={result.run_id}")
        finally:
            engine.dispose()

    fingerprint = result.measurements.structural_fingerprint()
    print()
    print(f"structural fingerprint ({len(fingerprint)} deterministic fields)")
    print(f"  {json.dumps(fingerprint, sort_keys=True)}")
    print()
    print(
        f"MEASURED       {result.measurements.task_count} tasks from a real Spark run"
        f" (event log {result.event_log_sha256[:12]})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Spark demo and measure it.")
    parser.add_argument("--url", default=DEMO_REPO, help="repository to run")
    parser.add_argument("--image", default=DEMO_IMAGE, help="Spark container image")
    parser.add_argument("--rows", type=int, default=None, help="dataset rows")
    parser.add_argument("--seed", type=int, default=None, help="dataset seed")
    parser.add_argument("--partitions", type=int, default=200, help="spark.sql.shuffle.partitions")
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--persist", action="store_true", help="save to Postgres")
    parser.add_argument("--runs-dir", type=Path, default=None, help="where to preserve event logs")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
