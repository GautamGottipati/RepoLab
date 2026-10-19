"""Assembly of a ``RepoManifest`` and architecture graph from detection findings.

Everything here is deterministic. The manifest's confidence reflects how much
corroborating evidence detection actually found, so a repository RepoLab
understands poorly says so rather than guessing confidently.
"""

from __future__ import annotations

from repolab.evidence.models import CodeReference, EvidenceBundle
from repolab.ingest.detectors import DetectionOutcome
from repolab.ingest.detectors.base import Finding
from repolab.ingest.languages import LanguageStat
from repolab.manifest.architecture import (
    ArchitectureEdge,
    ArchitectureGraph,
    ArchitectureNode,
    EdgeKind,
    NodeKind,
)
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Classification,
    Concept,
    Fidelity,
    RepoManifest,
    SmokeTest,
    SurfaceType,
)

#: Smoke tests proposed per surface type. A build is only ready once one passes.
SURFACE_SMOKE_TESTS: dict[SurfaceType, tuple[str, str]] = {
    SurfaceType.DATA_PLAYGROUND: ("spark job completes", "spark-submit --version"),
    SurfaceType.CLI: ("cli responds", "--help"),
    SurfaceType.WEB: ("health endpoint responds", "curl -fsS http://localhost:8000/"),
    SurfaceType.API: ("health endpoint responds", "curl -fsS http://localhost:8000/"),
    SurfaceType.TERMINAL: ("shell available", "echo ready"),
    SurfaceType.NOTEBOOK: ("kernel starts", "jupyter --version"),
}

#: Build tool -> the build kind it implies, most specific first.
_BUILD_TOOL_ORDER: tuple[tuple[str, str], ...] = (
    ("docker-compose", "compose"),
    ("docker", "docker"),
    ("maven", "maven"),
    ("gradle", "gradle"),
    ("sbt", "sbt"),
    ("setuptools", "python"),
    ("python-build", "python"),
    ("pipenv", "python"),
    ("make", "make"),
)


def build_manifest(
    *,
    repo_url: str,
    commit_sha: str,
    outcome: DetectionOutcome,
    languages: list[LanguageStat],
    classification: Classification,
) -> RepoManifest:
    """Assemble the manifest from deterministic findings and the classification."""
    frameworks = _unique(outcome.run.values_of("framework"))
    build_strategy = _infer_build_strategy(outcome)

    return RepoManifest(
        repo_url=repo_url,
        commit_sha=commit_sha,
        languages=[stat.language for stat in languages],
        frameworks=frameworks,
        entrypoints=outcome.entrypoints,
        services=outcome.services,
        dependencies=outcome.dependencies,
        interaction_surfaces=classification.surfaces,
        classification=classification,
        build_strategy=build_strategy,
        smoke_tests=_propose_smoke_tests(outcome, classification),
        concepts=_propose_concepts(outcome),
        confidence=_manifest_confidence(outcome, languages, classification),
    )


def _unique(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _infer_build_strategy(outcome: DetectionOutcome) -> BuildStrategy:
    """Choose a build approach from the build files that were found.

    Ingestion never builds anything; this records the *intended* approach for the
    build phase to act on.
    """
    tools = {f.value for f in outcome.run.of_kind("build_file")}
    steps: list[str] = []
    capabilities: list[str] = []
    limitations: list[str] = []

    chosen: str | None = None
    for tool, label in _BUILD_TOOL_ORDER:
        if tool in tools:
            chosen = label
            break

    if chosen == "compose":
        steps.append("docker compose up -d")
        capabilities.append("service orchestration declared by the repository")
    elif chosen == "docker":
        steps.append("docker build .")
        capabilities.append("containerised build declared by the repository")
    elif chosen in {"maven", "gradle", "sbt"}:
        steps.append(f"{chosen} package")
        limitations.append("JVM toolchain version must be resolved before building")
    elif chosen == "python":
        steps.append("install declared Python dependencies")
    elif chosen == "make":
        steps.append("make")

    if not steps:
        # Nothing declares how to build, so a source build is not yet justified.
        return BuildStrategy(
            kind=BuildKind.PARTIAL_BUILD,
            fidelity=Fidelity.PARTIAL,
            limitations=["no build tooling detected in the repository"],
        )

    return BuildStrategy(
        kind=BuildKind.SOURCE_BUILD,
        fidelity=Fidelity.EXACT,
        steps=steps,
        capabilities=capabilities,
        limitations=limitations,
    )


def _propose_smoke_tests(
    outcome: DetectionOutcome, classification: Classification
) -> list[SmokeTest]:
    """One smoke test per detected surface, plus the repository's own tests."""
    tests: list[SmokeTest] = []
    for surface in classification.surfaces:
        template = SURFACE_SMOKE_TESTS.get(surface.type)
        if template is None:
            continue
        name, command = template
        tests.append(SmokeTest(name=name, command=command))

    if outcome.run.of_kind("tests"):
        tests.append(SmokeTest(name="repository test suite", command="pytest"))
    return tests


def _propose_concepts(outcome: DetectionOutcome) -> list[Concept]:
    """Concepts grounded in what detection actually saw.

    Only findings that carry file references become concepts, so every concept
    can be traced back into the repository.
    """
    concepts: list[Concept] = []

    # "Spark configuration" may only be claimed when Spark was actually detected.
    # Generic settings matching words like "partition" are not Spark evidence --
    # Cassandra's partitioner would otherwise be mislabelled.
    spark_detected = bool(
        outcome.run.of_kind("spark_config")
        or outcome.run.of_kind("spark_session")
        or outcome.run.of_kind("spark_import")
        or outcome.run.of_kind("spark_submit")
    )
    settings = [f for f in outcome.run.of_kind("configuration_setting") if f.is_supported]
    spark_settings = [f for f in outcome.run.of_kind("spark_config") if f.is_supported]

    if spark_detected and (spark_settings or settings):
        concepts.append(
            Concept(
                name="Spark configuration",
                summary=(
                    "Configuration settings that govern how this Spark application "
                    "executes, including partitioning and resource settings."
                ),
                code_references=_merge_references(spark_settings + settings),
            )
        )
    elif settings:
        concepts.append(
            Concept(
                name="Configuration settings",
                summary=(
                    "Settings that govern how this application runs, found in its "
                    "configuration files."
                ),
                code_references=_merge_references(settings),
            )
        )

    session = [f for f in outcome.run.of_kind("spark_session") if f.is_supported]
    if session:
        concepts.append(
            Concept(
                name="SparkSession setup",
                summary="Where the Spark session is created and configured.",
                code_references=_merge_references(session),
            )
        )

    routes = [f for f in outcome.run.of_kind("route_decorator") if f.is_supported]
    if routes:
        concepts.append(
            Concept(
                name="HTTP routing",
                summary="How incoming requests are mapped to handlers.",
                code_references=_merge_references(routes),
            )
        )

    services = [f for f in outcome.run.of_kind("compose_service") if f.is_supported]
    if services:
        concepts.append(
            Concept(
                name="Service topology",
                summary="The external services this application runs alongside.",
                code_references=_merge_references(services),
            )
        )

    return concepts


def _merge_references(findings: list[Finding]) -> list[CodeReference]:
    merged: dict[tuple[str, int | None], CodeReference] = {}
    for finding in findings:
        for ref in finding.references:
            merged.setdefault((ref.path, ref.start_line), ref)
    return list(merged.values())[:5]


def _manifest_confidence(
    outcome: DetectionOutcome,
    languages: list[LanguageStat],
    classification: Classification,
) -> float:
    """Confidence from the breadth of corroborating evidence.

    Deliberately conservative: deterministic evidence alone should not claim
    should not claim certainty.
    """
    score = 0.2
    if languages:
        score += 0.15
    if outcome.dependencies:
        score += 0.15
    if outcome.entrypoints:
        score += 0.15
    if outcome.run.of_kind("build_file"):
        score += 0.1
    primary = classification.primary_type
    if primary is not None and primary is not SurfaceType.TERMINAL:
        score += 0.15
    if outcome.run.of_kind("tests"):
        score += 0.05
    return round(min(0.9, score), 2)


def build_architecture(outcome: DetectionOutcome) -> ArchitectureGraph:
    """Build the deterministic architecture graph.

    Every node carries the file references that justified it; edges are only
    added between nodes that exist.
    """
    graph = ArchitectureGraph()

    for entrypoint in outcome.entrypoints:
        references = [CodeReference(path=entrypoint.path)] if entrypoint.path else []
        graph.add_node(
            ArchitectureNode(
                id=f"entrypoint:{entrypoint.name}",
                kind=NodeKind.ENTRYPOINT,
                name=entrypoint.name,
                description=entrypoint.command,
                file_references=references,
            )
        )

    for finding in outcome.run.of_kind("configuration"):
        graph.add_node(
            ArchitectureNode(
                id=f"config:{finding.value}",
                kind=NodeKind.CONFIG,
                name=finding.value.rsplit("/", 1)[-1],
                description=finding.detail,
                file_references=list(finding.references),
            )
        )

    for service in outcome.services:
        graph.add_node(
            ArchitectureNode(
                id=f"service:{service.name}",
                kind=NodeKind.DATASTORE if _is_datastore(service.name) else NodeKind.SERVICE,
                name=service.name,
                description=service.image,
                file_references=_service_references(outcome, service.name),
            )
        )

    for framework in _unique(outcome.run.values_of("framework")):
        references = [
            ref
            for finding in outcome.run.of_kind("framework")
            if finding.value == framework
            for ref in finding.references
        ]
        graph.add_node(
            ArchitectureNode(
                id=f"external:{framework}",
                kind=NodeKind.EXTERNAL,
                name=framework,
                description="declared dependency",
                file_references=references,
            )
        )

    _add_deterministic_edges(graph, outcome)
    return graph


def _is_datastore(name: str) -> bool:
    lowered = name.lower()
    return any(
        hint in lowered
        for hint in ("cassandra", "postgres", "mysql", "redis", "mongo", "elastic", "minio")
    )


def _service_references(outcome: DetectionOutcome, name: str) -> list[CodeReference]:
    references: list[CodeReference] = []
    for kind in ("compose_service", "dockerfile"):
        for finding in outcome.run.of_kind(kind):
            if kind == "dockerfile" or finding.value == name:
                references.extend(finding.references)
    return references[:3]


def _add_deterministic_edges(graph: ArchitectureGraph, outcome: DetectionOutcome) -> None:
    """Relate entry points to the frameworks, configs and services they use."""
    entrypoint_ids = [n.id for n in graph.nodes if n.kind is NodeKind.ENTRYPOINT]
    framework_ids = [n.id for n in graph.nodes if n.kind is NodeKind.EXTERNAL]
    config_ids = [n.id for n in graph.nodes if n.kind is NodeKind.CONFIG]
    service_ids = [n.id for n in graph.nodes if n.kind in {NodeKind.SERVICE, NodeKind.DATASTORE}]

    for entry_id in entrypoint_ids:
        for framework_id in framework_ids:
            graph.add_edge(
                ArchitectureEdge(source=entry_id, target=framework_id, kind=EdgeKind.DEPENDS_ON)
            )
        for config_id in config_ids:
            graph.add_edge(
                ArchitectureEdge(source=config_id, target=entry_id, kind=EdgeKind.CONFIGURES)
            )
        for service_id in service_ids:
            graph.add_edge(
                ArchitectureEdge(source=entry_id, target=service_id, kind=EdgeKind.DEPENDS_ON)
            )


def build_evidence(outcome: DetectionOutcome) -> EvidenceBundle:
    """Collect the ingestion's evidence.

    Ingestion measures nothing, so the bundle carries code references only. No
    measurement is fabricated to make it look richer.
    """
    return EvidenceBundle(code_references=outcome.run.all_references[:200])
