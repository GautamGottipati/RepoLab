#!/usr/bin/env python
"""Answer a question about a repository by running an experiment.

    question -> investigate -> design -> validate -> ASK YOU -> run -> measure
             -> statistics -> evidence -> explanation

Usage:
    make experiment Q="Why does this job use 200 shuffle partitions?"
    uv run python scripts/run_experiment.py --question "..." [--yes] [--persist]
                 [--rows N] [--repeats N] [--values 8,50,200,400]
                 [--concurrency N] [--json out.json]

**It stops and waits.** The plan, the number of runs and the estimated time are
printed, and nothing executes until you answer. ``--yes`` pre-approves, for a
scripted run; there is no way to execute without one or the other.

Every number printed is computed by Python from Spark's own event logs. The
explanation is produced by a model that receives those computed statistics and
never calculates anything itself, and each of its claims is labelled *measured*,
*documented* or *inferred*. Claims the deterministic check could not corroborate
are printed as such rather than quietly dropped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repolab.config import get_settings  # noqa: E402
from repolab.events import CollectingEmitter, EventType  # noqa: E402
from repolab.experiences.spark.driver import SparkRunConfig  # noqa: E402
from repolab.experiences.spark.measurements import Determinism  # noqa: E402
from repolab.experiments import (  # noqa: E402
    ExperimentAgent,
    ExperimentBudget,
    ProposedPlan,
    validate_plan,
)
from repolab.experiments.experiment import Experiment  # noqa: E402
from repolab.experiments.explanation import ClaimKind  # noqa: E402
from repolab.ingest.clone import TemporaryClone  # noqa: E402
from repolab.ingest.limits import DEFAULT_LIMITS  # noqa: E402
from repolab.ingest.url import validate_repo_url  # noqa: E402
from repolab.sandbox.docker import docker_available  # noqa: E402

DEMO_REPO = "https://github.com/AlexIoannides/pyspark-example-project"

#: Pinned: the event-log field names were verified against this Spark version.
DEMO_IMAGE = "apache/spark:3.5.1-python3"

DEFAULT_QUESTION = "Why does this job use 200 shuffle partitions?"


class StreamingEmitter(CollectingEmitter):
    """Prints events as they happen, and keeps them for the summary."""

    #: Events worth a line on a terminal. Heartbeats and per-tool noise are kept
    #: but not printed, so the variant progress stays readable.
    SHOWN = frozenset(
        {
            EventType.AGENT_STARTED,
            EventType.AGENT_STATUS,
            EventType.STEP_STARTED,
            EventType.STEP_DONE,
            EventType.PARTIAL_RESULT,
            EventType.APPROVAL_REQUIRED,
            EventType.APPROVAL_RECEIVED,
            EventType.EXPERIMENT_VARIANT_STARTED,
            EventType.EXPERIMENT_VARIANT_COMPLETED,
            EventType.FAILED,
        }
    )

    MARKS: ClassVar[dict[EventType, str]] = {
        EventType.EXPERIMENT_VARIANT_STARTED: ">",
        EventType.EXPERIMENT_VARIANT_COMPLETED: "=",
        EventType.PARTIAL_RESULT: ".",
        EventType.FAILED: "!",
        EventType.APPROVAL_REQUIRED: "?",
        EventType.APPROVAL_RECEIVED: "+",
    }

    def emit(self, event_type, message="", *, step=None, data=None):  # type: ignore[override]
        event = super().emit(event_type, message, step=step, data=data)
        if event_type in self.SHOWN and message:
            mark = self.MARKS.get(event_type, "-")
            print(f"  {mark} {message}")
        return event


def human_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(value) < 1024 or unit == "GiB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GiB"


def print_plan(experiment: Experiment) -> None:
    plan = experiment.plan
    assert plan is not None

    print()
    print("=" * 72)
    print("RepoLab wants to run an experiment.")
    print("=" * 72)
    print()
    print(f"Question    {plan.question}")
    print(f"Hypothesis  {plan.hypothesis}")
    print()
    print(f"Variable    {plan.variable}")
    print(f"Values      {plan.values}   (baseline {plan.baseline_value})")
    print(f"Repeats     {plan.repeats}")
    print(f"Metrics     {', '.join(plan.metrics)}")
    print()
    print(f"Runs        {plan.estimated_runs} Spark jobs")
    print(f"Estimated   ~{plan.estimated_minutes} min", end="")
    print(
        " (sequential, so timings are comparable)"
        if plan.concurrency == 1
        else f" at concurrency {plan.concurrency} -- TIMINGS WILL BE CONTENDED"
    )
    print()
    print("Held fixed")
    for key, value in plan.fixed_settings.items():
        print(f"  {key:<34} {value}")
    if plan.dropped_metrics:
        print()
        print("Requested but not measurable by RepoLab (dropped)")
        for metric in plan.dropped_metrics:
            print(f"  - {metric}")
    if plan.adjustments:
        print()
        print("Adjusted by validation")
        for note in plan.adjustments:
            print(f"  - {note}")
    print()


def ask_for_approval() -> bool:
    """Block until the user answers. Anything but an explicit yes is a no."""
    try:
        answer = input("Approve this experiment? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


def print_statistics(experiment: Experiment) -> None:
    statistics = experiment.statistics
    plan = experiment.plan
    if statistics is None or plan is None:
        return

    print()
    print("measurements -- computed by Python from Spark's event logs")
    contention = (
        f"{statistics.contended_runs} contended" if statistics.contended_runs else "none contended"
    )
    print(
        f"  {statistics.total_runs} runs across {len(statistics.variants)} variants, {contention}"
    )

    unstable = statistics.unstable_structural_metrics()
    if unstable:
        print(f"  WARNING  these should have been identical across repeats: {unstable}")

    for comparison in statistics.comparisons:
        label = "timing" if comparison.determinism is Determinism.TIMING else "structural"
        print()
        print(f"  {comparison.metric}  ({comparison.unit}, {label})")
        for point in comparison.points:
            variant = next((v for v in statistics.variants if v.value == point.value), None)
            aggregate = variant.aggregates.get(comparison.metric) if variant else None
            relative = (
                "baseline"
                if point.is_baseline
                else (
                    f"{point.relative_difference_percent:+.1f}%"
                    if point.relative_difference_percent is not None
                    else "n/a"
                )
            )
            spread = f"spread {aggregate.spread:,.1f}" if aggregate else ""
            stddev = f"sd {aggregate.stddev:,.1f}" if aggregate else ""
            print(
                f"    {point.value:>6}  median {point.median:>14,.1f}  "
                f"{relative:>10}  {spread:>18}  {stddev}"
            )


def print_explanation(experiment: Experiment) -> None:
    verified = experiment.explanation
    if verified is None:
        print()
        print("no explanation: no model was available, so the measurements stand alone")
        return

    explanation = verified.explanation
    print()
    print("=" * 72)
    print("explanation -- grounded in the measurements above")
    print("=" * 72)
    print()
    print(explanation.answer)
    print()
    for kind in (ClaimKind.MEASURED, ClaimKind.DOCUMENTED, ClaimKind.INFERRED):
        claims = explanation.of_kind(kind)
        if not claims:
            continue
        print(f"{kind.value.upper()}")
        for claim in claims:
            print(f"  - {claim.statement}")
            print(f"      support: {claim.support}")
        print()
    if explanation.caveats:
        print("CAVEATS")
        for caveat in explanation.caveats:
            print(f"  - {caveat}")
        print()
    if verified.grounded:
        print(f"all {verified.checked_claims} checkable claim(s) traced back to the evidence")
    else:
        print(f"{len(verified.unsupported)} claim(s) could NOT be corroborated:")
        for item in verified.unsupported:
            print(f"  ! [{item.kind.value}] {item.statement}")
            print(f"      {item.problem}")


def print_reproducibility(experiment: Experiment) -> None:
    record = experiment.reproducibility
    if record is None:
        return
    print()
    print("reproducibility")
    print(f"  repository     {record.repo_url}")
    print(f"  commit         {record.commit_sha[:12]}")
    print(f"  image          {record.image}")
    print(f"  spark measured {record.application_version or 'unknown'}")
    print(f"  dataset        {record.dataset.describe()}")
    print(f"  dataset hash   {record.dataset_hash}")
    print(
        f"  resources      {record.resources.master}, driver {record.resources.driver_memory}"
        f", concurrency {record.resources.concurrency}"
    )
    print(f"  repeats        {record.repeats}")
    print(f"  reproducible   {record.reproducible}")
    print()
    print("  raw measurement sources (preserved, never discarded)")
    for run in experiment.runs:
        print(
            f"    {run.variant_id} r{run.repeat}  {run.event_log_sha256[:12]}  {run.event_log_path}"
        )
    print()
    print("  added by RepoLab, not present in the repository")
    for step in record.added_steps:
        print(f"    - {step}")


def _providers():
    settings = get_settings()
    router = None
    research = None
    if settings.nebius_api_key is not None and any(settings.configured_models.values()):
        from repolab.llm import build_router

        router = build_router(settings)
    if settings.tavily_api_key is not None:
        from repolab.research.tavily import TavilyResearchProvider

        research = TavilyResearchProvider(api_key=settings.tavily_api_key.get_secret_value())
    return router, research


def _forced_proposal(args: argparse.Namespace, question: str) -> ProposedPlan:
    """A proposal built from command-line values rather than from a model.

    For a reproducible demo and for the Docker test: the plan still goes through
    the same validation and the same approval gate.
    """
    values = [int(part) for part in args.values.split(",") if part.strip()]
    return ProposedPlan(
        question=question,
        hypothesis=(
            "Supplied on the command line, not a model hypothesis: the shuffle "
            "partition count determines how many partitions the data is spread "
            "across, most of which are empty when the data has few distinct keys."
        ),
        variable="spark.sql.shuffle.partitions",
        values=values,
        metrics=["runtime", "task count", "partitions", "empty partitions", "shuffle read"],
        repeats=args.repeats,
    )


async def run(args: argparse.Namespace) -> int:
    if not docker_available():
        print(
            "docker is not available; experiments run repository code in a container "
            "so it never executes on the host",
            file=sys.stderr,
        )
        return 2

    question = args.question
    repo_url = validate_repo_url(args.url)
    router, research = _providers()
    emitter = StreamingEmitter(job_id="cli")

    print(f"question       {question}")
    print(f"repository     {repo_url.slug}")
    print(f"image          {args.image}")
    print(f"model          {'available' if router else 'NOT configured -- deterministic fallback'}")
    print(f"research       {'available' if research else 'not configured'}")
    print()
    print("investigating the repository")

    with TemporaryClone(repo_url, limits=DEFAULT_LIMITS) as clone:
        agent = ExperimentAgent(
            repo_url=repo_url.normalized,
            commit_sha=clone.commit_sha,
            repository_root=Path(clone.path),
            image=args.image,
            router=router,
            research=research,
            emitter=emitter,
            budget=ExperimentBudget(max_runs=args.max_runs),
            base_config=SparkRunConfig(),
            concurrency=args.concurrency,
            rows=args.rows,
            runs_root=args.runs_dir,
            host_cpus=os.cpu_count(),
        )

        if args.values:
            # Skip the design call, but not validation and not the gate.
            experiment = Experiment(
                question=question,
                repo_url=repo_url.normalized,
                commit_sha=clone.commit_sha,
            )
            proposal = _forced_proposal(args, question)
            experiment.proposal = proposal
            result = validate_plan(
                proposal,
                budget=ExperimentBudget(max_runs=args.max_runs),
                config=SparkRunConfig(),
                image=args.image,
                concurrency=args.concurrency,
            )
            if result.plan is None:
                print(f"\nPLAN REFUSED  {result.rejection}", file=sys.stderr)
                return 1
            experiment.plan = result.plan
            experiment.reproducibility = agent.reproducibility_for(result.plan)
            experiment.await_approval()
        else:
            experiment = await agent.propose(question)

        if experiment.plan is None:
            print(f"\nPLAN REFUSED  {experiment.rejection}", file=sys.stderr)
            return 1

        print_plan(experiment)

        approved = True if args.yes else ask_for_approval()
        if not approved:
            experiment.reject(by="cli")
            print("\nnot approved; nothing was run")
            return 0

        experiment.approve(by="cli")
        print(f"\napproved -- running {experiment.plan.estimated_runs} Spark jobs")
        print()

        experiment = await agent.execute(experiment)

    print_statistics(experiment)
    print_explanation(experiment)
    print_reproducibility(experiment)

    if args.json:
        args.json.write_text(json.dumps(experiment.model_dump(mode="json"), indent=2))
        print(f"\nfull experiment written to {args.json}")

    if args.persist:
        from repolab.db import build_engine, session_scope
        from repolab.db.experiments import count_measurements, save_experiment

        engine = build_engine(get_settings())
        try:
            with session_scope(engine) as session:
                record = save_experiment(session, experiment)
                session.commit()
                stored = count_measurements(session, record.id)
            print(
                f"\npersisted experiment {record.id} with {len(experiment.runs)} variant run(s) "
                f"and {stored} measurement(s)"
            )
        finally:
            engine.dispose()

    print()
    print(
        f"MEASURED       {experiment.run_count} real Spark runs, "
        f"{len(experiment.evidence.measurements) if experiment.evidence else 0} measurements, "
        f"{len(experiment.evidence.logs) if experiment.evidence else 0} raw log excerpt(s)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--question", "-q", default=DEFAULT_QUESTION)
    parser.add_argument("--url", default=DEMO_REPO)
    parser.add_argument("--image", default=DEMO_IMAGE)
    parser.add_argument("--rows", type=int, default=None, help="dataset rows")
    parser.add_argument(
        "--values", default="", help="skip the design call and vary these values, e.g. 8,50,200,400"
    )
    parser.add_argument("--repeats", type=int, default=2, help="repeats per value, with --values")
    parser.add_argument("--concurrency", type=int, default=1, help="variants at once (>1 contends)")
    parser.add_argument("--max-runs", type=int, default=24, help="budget: total Spark runs")
    parser.add_argument("--yes", action="store_true", help="pre-approve, for a scripted run")
    parser.add_argument("--persist", action="store_true", help="save to Postgres")
    parser.add_argument("--json", type=Path, help="write the full experiment here")
    parser.add_argument("--runs-dir", type=Path, default=None, help="where to preserve event logs")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
