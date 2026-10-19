"""Saving and loading lessons and a learner's progress.

The lesson is written by whoever generated it -- the worker, after a model call
and a repository read -- and read by the API when a browser asks for it. One row
per repository commit, replaced on regeneration.

Progress is keyed by terminal session, so reloading the page finds the same
lesson at the same point rather than starting again.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from repolab.db.models import LessonProgressRecord, LessonRecord
from repolab.lessons.models import Lesson, LessonProgress, StepAttempt


def save_lesson(session: Session, lesson: Lesson) -> LessonRecord:
    """Store *lesson*, replacing any existing lesson for the same commit."""
    payload = lesson.model_dump(mode="json")
    existing = session.scalar(
        select(LessonRecord).where(
            LessonRecord.repo_url == lesson.repo_url,
            LessonRecord.commit_sha == lesson.commit_sha,
        )
    )

    if existing is None:
        record = LessonRecord(
            id=lesson.id,
            repo_url=lesson.repo_url,
            commit_sha=lesson.commit_sha,
            title=lesson.title[:512],
            generated_by=lesson.generated_by,
            fidelity=lesson.fidelity,
            simulated=lesson.simulated,
            step_count=lesson.step_count,
            lesson=payload,
        )
        session.add(record)
        session.flush()
        return record

    # The id stays the one already stored, so progress recorded against it is not
    # orphaned by a regeneration.
    existing.title = lesson.title[:512]
    existing.generated_by = lesson.generated_by
    existing.fidelity = lesson.fidelity
    existing.simulated = lesson.simulated
    existing.step_count = lesson.step_count
    existing.lesson = {**payload, "id": existing.id}
    existing.updated_at = datetime.now(UTC)
    session.flush()
    return existing


def load_lesson(session: Session, repo_url: str, commit_sha: str = "") -> Lesson | None:
    """The stored lesson for a commit, or the newest for the repository.

    Falling back to the newest matters when a caller knows the repository but not
    which commit was investigated; a lesson for the wrong commit is still better
    than none, and the lesson carries its own commit so the difference is visible.
    """
    if commit_sha:
        record = session.scalar(
            select(LessonRecord).where(
                LessonRecord.repo_url == repo_url,
                LessonRecord.commit_sha == commit_sha,
            )
        )
        if record is not None:
            return Lesson.model_validate(record.lesson)

    record = session.scalar(
        select(LessonRecord)
        .where(LessonRecord.repo_url == repo_url)
        .order_by(LessonRecord.updated_at.desc())
        .limit(1)
    )
    return None if record is None else Lesson.model_validate(record.lesson)


def load_lesson_by_id(session: Session, lesson_id: str) -> Lesson | None:
    record = session.get(LessonRecord, lesson_id)
    return None if record is None else Lesson.model_validate(record.lesson)


def save_progress(session: Session, progress: LessonProgress) -> LessonProgressRecord:
    """Store *progress*, replacing the row for this lesson and session."""
    existing = session.scalar(
        select(LessonProgressRecord).where(
            LessonProgressRecord.lesson_id == progress.lesson_id,
            LessonProgressRecord.session_id == (progress.session_id or ""),
        )
    )

    attempts = {"attempts": [attempt.model_dump(mode="json") for attempt in progress.attempts]}
    completed = {"completed": list(progress.completed)}

    if existing is None:
        record = LessonProgressRecord(
            lesson_id=progress.lesson_id,
            session_id=progress.session_id or "",
            attempts=attempts,
            completed=completed,
            completed_count=progress.completed_count,
            finished=progress.finished_at is not None,
            started_at=progress.started_at,
        )
        session.add(record)
        session.flush()
        return record

    existing.attempts = attempts
    existing.completed = completed
    existing.completed_count = progress.completed_count
    existing.finished = progress.finished_at is not None
    existing.updated_at = datetime.now(UTC)
    session.flush()
    return existing


def load_progress(session: Session, lesson_id: str, session_id: str) -> LessonProgress:
    """A learner's progress, or an empty record when they have not started."""
    record = session.scalar(
        select(LessonProgressRecord).where(
            LessonProgressRecord.lesson_id == lesson_id,
            LessonProgressRecord.session_id == session_id,
        )
    )
    if record is None:
        return LessonProgress(lesson_id=lesson_id, session_id=session_id)

    return LessonProgress(
        lesson_id=lesson_id,
        session_id=session_id,
        attempts=[
            StepAttempt.model_validate(item) for item in (record.attempts or {}).get("attempts", [])
        ],
        completed=list((record.completed or {}).get("completed", [])),
        started_at=record.started_at,
    )
