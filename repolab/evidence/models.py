"""Evidence models.

Every substantive explanation RepoLab produces should be backed by an
:class:`EvidenceBundle`. Evidence is what separates RepoLab from a repository
chatbot: explanations are grounded in repository code, real execution, logs and
version-matched official documentation.

Deliberately thin. Fields are added when a milestone needs them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class MeasurementSource(StrEnum):
    """Where a measurement came from.

    Only real execution produces measurements (CLAUDE.md), so every member names
    an actual instrument rather than an inference.
    """

    SPARK_EVENT_LOG = "spark_event_log"
    PROCESS_TIMING = "process_timing"
    TEST_OUTPUT = "test_output"
    HTTP_METRICS = "http_metrics"
    CLI_OUTPUT = "cli_output"
    APPLICATION_LOG = "application_log"


class CodeReference(BaseModel):
    """A pointer into the repository under study."""

    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    commit_sha: str | None = None
    excerpt: str | None = None


class DocumentationReference(BaseModel):
    """A citation to external documentation, preferably official and version-matched."""

    url: str
    title: str | None = None
    excerpt: str | None = None
    version: str | None = None
    """The software version the cited page documents, when known."""


class Measurement(BaseModel):
    """One measured quantity.

    Values are produced by deterministic instrumentation and statistics are
    computed by Python, never by a model.
    """

    name: str
    value: float
    unit: str
    source: MeasurementSource
    variant_id: str | None = None
    """Which experiment variant produced this, when it came from an experiment."""


class LogReference(BaseModel):
    """An excerpt of a real log, retained so an explanation can cite it."""

    source: str
    """What produced the log, e.g. a command, service name, or file path."""

    excerpt: str
    line_range: tuple[int, int] | None = None


class EvidenceBundle(BaseModel):
    """The evidence supporting one explanation."""

    code_references: list[CodeReference] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
    logs: list[LogReference] = Field(default_factory=list)
    documentation: list[DocumentationReference] = Field(default_factory=list)
    simulated: bool = False
    """True only for Mini Lab conceptual output, which must be labelled as such."""

    @model_validator(mode="after")
    def _simulated_evidence_carries_no_measurements(self) -> EvidenceBundle:
        """Simulated evidence may never carry measurements.

        Mini Lab must never produce performance measurements and simulated
        behaviour must never be presented as experimental evidence (CLAUDE.md).
        Making that combination unrepresentable is cheaper than policing it at
        every call site.
        """
        if self.simulated and self.measurements:
            raise ValueError(
                "simulated evidence cannot contain measurements: "
                "Mini Lab must never produce measured results"
            )
        return self

    @property
    def is_empty(self) -> bool:
        """Whether this bundle grounds nothing."""
        return not (self.code_references or self.measurements or self.logs or self.documentation)
