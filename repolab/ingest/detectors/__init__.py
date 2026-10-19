"""Deterministic repository detectors.

Detection runs before any model is consulted, and every finding carries the file
references that justify it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors.base import DetectionContext, DetectorRun, Finding
from repolab.ingest.detectors.dependencies import detect_configuration, detect_dependencies
from repolab.ingest.detectors.entrypoints import detect_entrypoints, detect_tests
from repolab.ingest.detectors.services import detect_services
from repolab.ingest.detectors.structure import detect_structure
from repolab.ingest.detectors.surfaces import detect_surfaces
from repolab.manifest.models import Dependency, EntryPoint, Service

__all__ = [
    "DetectionContext",
    "DetectionOutcome",
    "DetectorRun",
    "Finding",
    "detect_all",
    "detect_configuration",
    "detect_dependencies",
    "detect_entrypoints",
    "detect_services",
    "detect_structure",
    "detect_surfaces",
    "detect_tests",
]


class DetectionOutcome(BaseModel):
    """Everything deterministic detection produced."""

    run: DetectorRun = Field(default_factory=DetectorRun)
    dependencies: list[Dependency] = Field(default_factory=list)
    entrypoints: list[EntryPoint] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)

    @property
    def unsupported_findings(self) -> list[Finding]:
        """Findings with no file reference. Should normally be empty."""
        return [f for f in self.run.findings if not f.is_supported]


def detect_all(context: DetectionContext, budget: ReadBudget) -> DetectionOutcome:
    """Run every detector and return their combined findings.

    Detection only gathers citable evidence. Deciding what that evidence means is
    :mod:`repolab.classify`'s job, so no surface is produced here.
    """
    outcome = DetectionOutcome()

    dependency_run, dependencies = detect_dependencies(context, budget)
    outcome.run.extend(dependency_run)
    outcome.dependencies = dependencies

    outcome.run.extend(detect_configuration(context, budget))

    entry_run, entrypoints = detect_entrypoints(context, budget)
    outcome.run.extend(entry_run)
    outcome.entrypoints = entrypoints

    outcome.run.extend(detect_tests(context))

    service_run, services = detect_services(context, budget)
    outcome.run.extend(service_run)
    outcome.services = services

    # Structural signals are path-only, so they cost nothing and still fire when
    # the read budget is exhausted on a very large repository.
    outcome.run.extend(detect_structure(context))

    outcome.run.extend(detect_surfaces(context, budget))
    return outcome
