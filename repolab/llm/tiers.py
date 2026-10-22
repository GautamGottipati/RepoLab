"""Logical model tiers and call purposes.

Callers choose a *tier*, never a model ID. Which concrete Nemotron model backs a
tier is configuration (``MODEL_NANO`` / ``MODEL_SUPER`` / ``MODEL_ULTRA``), so
model IDs never appear in application code.
"""

from __future__ import annotations

from enum import StrEnum


class ModelTier(StrEnum):
    """Routing tiers, cheapest first."""

    NANO = "nano"
    SUPER = "super"
    ULTRA = "ultra"

    @property
    def env_var(self) -> str:
        """The environment variable configuring this tier."""
        return f"MODEL_{self.value.upper()}"


class Purpose(StrEnum):
    """Why a call was made.

    Recorded on every call so cost, latency and failure can be attributed per
    activity. Members follow the per-tier routing guidance in CLAUDE.md, but
    callers may pass any string, so an unforeseen purpose is still recordable.
    """

    # Nano
    FILE_SUMMARY = "file_summary"
    LOG_PARSING = "log_parsing"
    LESSON_STEP_CHECK = "lesson_step_check"
    ERROR_EXPLANATION = "error_explanation"
    QUICK_ANSWER = "quick_answer"
    CLASSIFICATION = "classification"
    QUESTION_TRIAGE = "question_triage"

    # Super
    REPO_ARCHITECTURE = "repo_architecture"
    REPO_INVESTIGATION = "repo_investigation"
    CLASSIFICATION_TIE_BREAK = "classification_tie_break"
    LESSON_GENERATION = "lesson_generation"
    MINI_LAB_GENERATION = "mini_lab_generation"
    BUILD_DIAGNOSIS = "build_diagnosis"
    INTERNAL_BEHAVIOR_EXPLANATION = "internal_behavior_explanation"
    QUESTION_ANSWER = "question_answer"

    # Ultra
    EXPERIMENT_DESIGN = "experiment_design"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    RESULT_INTERPRETATION = "result_interpretation"

    # Meta
    SMOKE_TEST = "smoke_test"
