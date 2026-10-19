"""Experiment persistence.

An experiment is stored in three layers, each for a different question:

``experiments``          the plan, the statistics, the evidence, the explanation
                        and the reproducibility record -- what the UI reads
``experiment_variants``  one row per run, with a pointer and hash for the raw
                        event log it was measured from
``measurements``         one row per measured quantity, so a metric can be queried
                        across experiments without unpacking JSON

Saving is idempotent on the experiment id: re-saving replaces the variants and
measurements rather than appending, so a run that is retried leaves one record
rather than two sets of numbers that would both look authoritative.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import ExperimentRecord, ExperimentVariantRecord, MeasurementRecord
from repolab.experiments.experiment import Experiment
from repolab.experiments.models import ExperimentStatus, VariantRun


def save_experiment(session: Session, experiment: Experiment) -> ExperimentRecord:
    """Persist an experiment and everything it measured."""
    record = session.get(ExperimentRecord, experiment.id)
    if record is None:
        record = ExperimentRecord(id=experiment.id)
        session.add(record)

    record.job_id = experiment.job_id
    record.question = experiment.question
    record.repo_url = experiment.repo_url
    record.commit_sha = experiment.commit_sha
    record.status = experiment.status.value

    plan = experiment.plan
    if plan is not None:
        record.variable = plan.variable
        record.baseline_value = plan.baseline_value
        record.repeats = plan.repeats
        record.estimated_runs = plan.estimated_runs
        record.estimated_seconds = plan.estimated_seconds
        record.image = str(plan.fixed_settings.get("image", ""))

    reproducibility = experiment.reproducibility
    if reproducibility is not None:
        record.dataset_hash = reproducibility.dataset_hash
        record.image = reproducibility.image or record.image

    record.proposal = _dump(experiment.proposal)
    record.plan = _dump(plan)
    record.rejection = _dump(experiment.rejection)
    record.statistics = _dump(experiment.statistics)
    record.evidence = _dump(experiment.evidence)
    record.explanation = _dump(experiment.explanation)
    record.reproducibility = _dump(reproducibility)

    record.error = experiment.error
    record.explanation_error = experiment.explanation_error
    record.approved_by = experiment.approved_by
    record.created_at = experiment.created_at
    record.approved_at = experiment.approved_at
    record.started_at = experiment.started_at
    record.finished_at = experiment.finished_at
    session.flush()

    # Assigned through the relationships so the ORM keeps both sides in sync and
    # `delete-orphan` clears what a previous save wrote. Assigning by foreign key
    # alone would leave the collections empty in this session.
    #
    # Two flushes, in this order, because a measurement needs both foreign keys
    # and one of them is its variant's primary key. Putting the rows in both
    # collections instead looks tidier but autoflushes them while
    # ``experiment_id`` is still unset.
    # Cleared and flushed before the new rows are built, so the deletes happen
    # first: inserting a variant that re-uses a (variant_id, repeat) pair would
    # otherwise collide with the row a previous save left behind.
    record.measurements = []
    record.variants = []
    session.flush()

    record.variants = [_variant_record(run) for run in experiment.runs]
    session.flush()

    record.measurements = [
        row
        for variant, run in zip(record.variants, experiment.runs, strict=True)
        for row in _measurement_records(run, experiment.id, variant.id)
    ]
    session.flush()
    return record


def _measurement_records(
    run: VariantRun, experiment_id: str, variant_pk: int
) -> list[MeasurementRecord]:
    """One row per measured quantity, keyed to both the experiment and the run."""
    return [
        MeasurementRecord(
            experiment_id=experiment_id,
            variant_pk=variant_pk,
            variant_id=measurement.variant_id,
            name=measurement.name,
            value=measurement.value,
            unit=measurement.unit,
            source=measurement.source.value,
        )
        for measurement in run.to_measurements()
    ]


def _variant_record(run: VariantRun) -> ExperimentVariantRecord:
    return ExperimentVariantRecord(
        variant_id=run.variant_id,
        value=run.value,
        repeat=run.repeat,
        run_id=run.run_id,
        dataset_hash=run.dataset_hash,
        image=run.image,
        checkpoint_id=run.checkpoint_id,
        measurements=run.measurements.model_dump(mode="json"),
        event_log_path=run.event_log_path,
        event_log_sha256=run.event_log_sha256,
        task_count=run.measurements.task_count,
        total_runtime_ms=run.measurements.total_runtime_ms,
        wall_clock_seconds=run.wall_clock_seconds,
        timing_contended=run.timing_contended,
        ran_at=run.ran_at,
    )


def _dump(value: object | None) -> dict | None:
    if value is None:
        return None
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else None


def get_experiment(session: Session, experiment_id: str) -> ExperimentRecord | None:
    return session.get(ExperimentRecord, experiment_id)


def list_experiments(
    session: Session,
    *,
    repo_url: str | None = None,
    status: ExperimentStatus | None = None,
    limit: int = 50,
) -> list[ExperimentRecord]:
    """Experiments, newest first."""
    query = select(ExperimentRecord)
    if repo_url is not None:
        query = query.where(ExperimentRecord.repo_url == repo_url)
    if status is not None:
        query = query.where(ExperimentRecord.status == status.value)
    return list(
        session.scalars(query.order_by(ExperimentRecord.created_at.desc()).limit(limit)).all()
    )


def load_experiment(session: Session, experiment_id: str) -> Experiment | None:
    """Rebuild the domain object from storage.

    The API needs this to approve an experiment that was proposed by a different
    process: the plan has to come back exactly as it was presented, because that
    is what the user approved.
    """
    record = get_experiment(session, experiment_id)
    if record is None:
        return None

    return Experiment.model_validate(
        {
            "id": record.id,
            "job_id": record.job_id,
            "question": record.question,
            "repo_url": record.repo_url,
            "commit_sha": record.commit_sha,
            "status": record.status,
            "proposal": record.proposal,
            "plan": record.plan,
            "rejection": record.rejection,
            "runs": [
                _variant_payload(row) for row in record.variants if row.measurements is not None
            ],
            "statistics": record.statistics,
            "evidence": record.evidence,
            "explanation": record.explanation,
            "reproducibility": record.reproducibility,
            "error": record.error,
            "explanation_error": record.explanation_error,
            "approved_by": record.approved_by,
            "created_at": record.created_at,
            "approved_at": record.approved_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }
    )


def _variant_payload(row: ExperimentVariantRecord) -> dict:
    return {
        "variant_id": row.variant_id,
        "value": row.value,
        "repeat": row.repeat,
        "run_id": row.run_id,
        "dataset_hash": row.dataset_hash,
        "image": row.image,
        "checkpoint_id": row.checkpoint_id,
        "measurements": row.measurements,
        "event_log_path": row.event_log_path,
        "event_log_sha256": row.event_log_sha256,
        "wall_clock_seconds": row.wall_clock_seconds,
        "timing_contended": row.timing_contended,
        "ran_at": row.ran_at,
    }


def count_measurements(session: Session, experiment_id: str) -> int:
    rows = session.scalars(
        select(MeasurementRecord.id).where(MeasurementRecord.experiment_id == experiment_id)
    ).all()
    return len(rows)
