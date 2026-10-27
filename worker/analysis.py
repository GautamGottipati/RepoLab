"""The investigation, as a sequence of artifacts published as soon as each exists.

    resolve commit (git ls-remote, no clone)
        └─ every eager artifact already ready for it?  → reuse, finish in seconds
    clone at that commit
    P0  repo.facts               tree, languages, build system, README opening
        repo.manifest            deterministic detectors + rule classification
        architecture.structure   static graph + baseline orientation
          → ingestion saved and linked to the job: every screen can read it now
        architecture.runtime     code model, verified
        architecture.source      cited files kept at this commit
    P1  repo.manifest (refined)  the investigator agent
        architecture.components  Super synthesis
        architecture.summary     Super orientation
    P2  architecture.runtime_naming   only when someone asks (worker/artifact_tasks.py)

Every artifact is generated → verified → persisted → announced on the job's
stream. A failure marks that artifact (and what depends on it) and the job goes
on; only a failure before the manifest exists fails the job.

Agents reason; deterministic code verifies; Postgres persists; SSE tells the
browser. Nothing here executes repository code.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session, sessionmaker

from repolab.agents import AgentStatus, RepositoryInvestigatorAgent
from repolab.analytics import Analytics, AnalyticsEventType
from repolab.architecture.build import build_static_architecture, runtime_architecture
from repolab.architecture.code_model import (
    flows_from_components,
    reading_path,
    runtime_from_components,
)
from repolab.architecture.models import ArchitectureGraph
from repolab.architecture.subsystems import rank
from repolab.architecture.synthesis import synthesise
from repolab.architecture.understanding import orient
from repolab.architecture.validate import validate
from repolab.architecture.verify import cited_paths, verify_graph
from repolab.artifacts import (
    ARCH_COMPONENTS,
    ARCH_RUNTIME,
    ARCH_SOURCE,
    ARCH_STRUCTURE,
    ARCH_SUMMARY,
    EAGER,
    REPO_FACTS,
    REPO_MANIFEST,
    Status,
)
from repolab.artifacts import store as artifacts
from repolab.artifacts.registry import dependants, spec
from repolab.classify import classify
from repolab.db import save_ingestion
from repolab.db.jobs import get_job, link_ingestion, set_commit
from repolab.db.store import find_ingestion, update_architecture, update_manifest
from repolab.events import ActivityReporter, DbEventEmitter, EventType, liveness
from repolab.events.liveness import (
    ActivitySink,
    Health,
    NoLiveness,
    job_key,
    read_liveness,
    task_health,
)
from repolab.experiences.facts import gather_facts
from repolab.ingest.clone import TemporaryClone, resolve_commit
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import detect_all
from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.enrich import EnrichmentResult
from repolab.ingest.languages import detect_languages
from repolab.ingest.limits import DEFAULT_LIMITS, IngestLimits, LimitReport
from repolab.ingest.pipeline import IngestionResult
from repolab.ingest.tree import FileTree, build_file_tree
from repolab.ingest.url import RepoUrl
from repolab.manifest.builder import build_evidence, build_manifest
from repolab.manifest.models import RepoManifest
from repolab.runtime.plan import plan_runtime
from repolab.sources import CloneSnapshot
from repolab.sources import capture as capture_sources

log = structlog.get_logger(__name__)

#: How long a job follows another job already analysing the same commit.
FOLLOW_SECONDS = 1800
FOLLOW_POLL_SECONDS = 5.0

#: Files whose presence names the build system.
BUILD_FILES = {
    "pom.xml": "Maven",
    "build.gradle": "Gradle",
    "build.gradle.kts": "Gradle",
    "build.sbt": "sbt",
    "package.json": "npm",
    "pyproject.toml": "Python (pyproject)",
    "setup.py": "Python (setuptools)",
    "cargo.toml": "Cargo",
    "go.mod": "Go modules",
    "makefile": "Make",
    "cmakelists.txt": "CMake",
    "build.xml": "Ant",
}


def _stage(artifact_type: str) -> str:
    """``architecture.runtime`` -> ``architecture``; ``repo.facts`` -> ``repository``."""
    prefix = artifact_type.split(".", 1)[0]
    return "repository" if prefix == "repo" else prefix


class Progress:
    """One job's view of its commit's artifacts: begin, finish, fail -- and announce."""

    def __init__(
        self,
        job_id: str,
        repo_url: str,
        commit_sha: str,
        factory: sessionmaker[Session],
        emitter: DbEventEmitter,
        analytics: Analytics,
        enqueue: Callable[[str, str], Any] | None = None,
        liveness: ActivitySink | None = None,
    ) -> None:
        self.job_id = job_id
        self.liveness = liveness or NoLiveness()
        self.repo_url = repo_url
        self.commit_sha = commit_sha
        self.factory = factory
        self.emitter = emitter
        self.analytics = analytics
        self.enqueue = enqueue
        self._refresh: set[str] = set()

    def begin(self, artifact_type: str) -> None:
        """Mark *artifact_type* generating -- or, if already usable, a refresh."""
        self.liveness.set_activity(_stage(artifact_type), artifact_type)
        with self.factory() as session:
            record = artifacts.ensure(session, self.repo_url, self.commit_sha, artifact_type)
            if Status(record.status).usable:
                self._refresh.add(artifact_type)
                session.commit()
                return
            record.job_id = self.job_id
            artifacts.transition(session, record, Status.GENERATING)
            session.commit()
            artifacts.announce(self.emitter, record, message=f"{artifact_type}: started")

    def verifying(self, artifact_type: str) -> None:
        self.liveness.set_activity(_stage(artifact_type), f"{artifact_type}:verifying")
        if artifact_type in self._refresh:
            return
        with self.factory() as session:
            record = artifacts.ensure(session, self.repo_url, self.commit_sha, artifact_type)
            artifacts.transition(session, record, Status.VERIFYING)
            session.commit()
            artifacts.announce(self.emitter, record)

    def done(
        self,
        artifact_type: str,
        *,
        payload: dict[str, Any] | None = None,
        model: str | None = None,
        partial: bool = False,
    ) -> None:
        refresh = artifact_type in self._refresh
        with self.factory() as session:
            record = artifacts.ensure(session, self.repo_url, self.commit_sha, artifact_type)
            record.job_id = self.job_id
            artifacts.transition(
                session,
                record,
                Status.PARTIAL if partial else Status.READY,
                payload=payload,
                model=model,
            )
            session.commit()
            artifacts.announce(self.emitter, record, updated=refresh)
            duration = record.duration_ms
        self._refresh.discard(artifact_type)
        self.analytics.record(
            AnalyticsEventType.ARTIFACT_READY,
            artifact_type=artifact_type,
            duration_ms=duration,
            cache_hit=False,
            commit=self.commit_sha[:12],
        )
        self.release(artifact_type)

    def updated(self, artifact_type: str, *, payload: dict[str, Any] | None = None) -> None:
        with self.factory() as session:
            record = artifacts.ensure(session, self.repo_url, self.commit_sha, artifact_type)
            if payload is not None:
                record.payload = payload
            artifacts.transition(session, record, Status(record.status))
            session.commit()
            artifacts.announce(self.emitter, record, updated=True)

    def failed(self, artifact_type: str, exc: BaseException, *, stage: str) -> None:
        from repolab.failures import explain

        with self.factory() as session:
            record = artifacts.ensure(session, self.repo_url, self.commit_sha, artifact_type)
            blocked = artifacts.fail(
                session,
                record,
                code=type(exc).__name__,
                stage=stage,
                message=explain(exc).summary,
                retryable=True,
            )
            session.commit()
            artifacts.announce(self.emitter, record)
            for name in blocked:
                blocked_record = artifacts.ensure(session, self.repo_url, self.commit_sha, name)
                artifacts.announce(self.emitter, blocked_record)
        self._refresh.discard(artifact_type)
        self.analytics.record(
            AnalyticsEventType.ARTIFACT_FAILED,
            artifact_type=artifact_type,
            error=type(exc).__name__,
        )
        log.warning(
            "artifact.failed", artifact_type=artifact_type, stage=stage, error=str(exc)[:300]
        )

    def release(self, artifact_type: str) -> None:
        """Start on-demand dependants someone asked for while this was pending."""
        if self.enqueue is None:
            return
        for dependant in dependants(artifact_type):
            if not spec(dependant).lazy:
                continue
            with self.factory() as session:
                record = artifacts.get(session, self.repo_url, self.commit_sha, dependant)
                ready = (
                    record is not None
                    and record.status == Status.QUEUED.value
                    and (
                        artifacts.dependencies_ready(
                            session, self.repo_url, self.commit_sha, dependant
                        )
                    )
                )
            if ready:
                self.enqueue(self.job_id, dependant)

    def usable(self, artifact_type: str) -> bool:
        with self.factory() as session:
            record = artifacts.get(session, self.repo_url, self.commit_sha, artifact_type)
            return record is not None and Status(record.status).usable


# -- facts ---------------------------------------------------------------------------


def repository_facts(
    root: Path, tree: FileTree, languages: list[Any], commit: str, branch: str | None
) -> dict[str, Any]:
    """What can be said about a repository from its tree alone, in milliseconds."""
    names = {entry.path.lower(): entry.path for entry in tree.files if "/" not in entry.path}
    build = sorted({label for name, label in BUILD_FILES.items() if name in names})
    readme_path = next((names[n] for n in sorted(names) if n.startswith("readme")), None)
    title, paragraph = None, None
    if readme_path:
        try:
            text = (root / readme_path).read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            text = ""
        title, paragraph = _readme_opening(text)
    return {
        "commit_sha": commit,
        "branch": branch,
        "file_count": tree.file_count,
        "total_size": tree.total_size,
        "languages": [s.language for s in languages],
        "build_systems": build,
        "readme": {"path": readme_path, "title": title, "first_paragraph": paragraph}
        if readme_path
        else None,
    }


def _readme_opening(text: str) -> tuple[str | None, str | None]:
    title = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip() or None
            break
    paragraph: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = re.sub(
            r"!\[[^\]]*\]\([^)]*\)|\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)|<[^>]+>", "", block
        ).strip()
        if not cleaned or cleaned.startswith(("#", "```", "|", "-", "*", ">", "[")):
            continue
        paragraph.append(re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)))
        break
    return title, (paragraph[0][:600] if paragraph else None)


# -- the pipeline ----------------------------------------------------------------------


async def analyse(
    job_id: str,
    repo_url: RepoUrl,
    *,
    settings: Any,
    emitter: DbEventEmitter,
    analytics: Analytics,
    factory: sessionmaker[Session],
    providers: Callable[[], tuple[Any, Any]],
    enqueue: Callable[[str, str], Any] | None = None,
    prepare: Callable[[RepoManifest, ActivityReporter], Any] | None = None,
    limits: IngestLimits = DEFAULT_LIMITS,
    allow_local_source: bool = False,
    liveness: ActivitySink | None = None,
) -> int:
    """Run the investigation progressively. Returns the ingestion id."""
    activity = ActivityReporter(emitter, sink=liveness)
    activity.enter("inspecting_repository")

    commit = await asyncio.to_thread(
        resolve_commit, repo_url, limits=limits, allow_local_source=allow_local_source
    )
    if commit is not None:
        _set_commit(factory, job_id, commit)
        reused = await _reuse(job_id, repo_url.normalized, commit, factory, emitter, analytics)
        if reused is not None:
            return reused

    emitter.emit(
        EventType.STEP_STARTED,
        f"cloning {repo_url.slug}",
        step="clone",
        data={"repo": repo_url.slug},
    )
    with TemporaryClone(
        repo_url, limits=limits, commit=commit, allow_local_source=allow_local_source
    ) as clone:
        commit = clone.commit_sha
        _set_commit(factory, job_id, commit)
        emitter.emit(
            EventType.STEP_DONE,
            f"cloned at {commit[:12]}",
            step="clone",
            data={"commit_sha": commit, "branch": clone.branch},
        )
        progress = Progress(
            job_id,
            repo_url.normalized,
            commit,
            factory,
            emitter,
            analytics,
            enqueue,
            liveness=liveness,
        )
        root = Path(clone.path)

        # -- P0: facts ---------------------------------------------------------------
        progress.begin(REPO_FACTS)
        report = LimitReport()
        budget = ReadBudget(limits, report)
        tree = build_file_tree(clone.path, limits=limits, report=report)
        languages = detect_languages(tree)
        progress.done(
            REPO_FACTS, payload=repository_facts(root, tree, languages, commit, clone.branch)
        )
        emitter.emit(
            EventType.PARTIAL_RESULT,
            f"{tree.file_count} files, "
            f"{', '.join(s.language for s in languages) or 'no languages detected'}",
            step="clone",
            data={"file_count": tree.file_count, "languages": [s.language for s in languages]},
        )
        context = DetectionContext(root=root, tree=tree, commit_sha=commit)

        # -- P0: the deterministic manifest and architecture ---------------------------
        progress.begin(REPO_MANIFEST)
        progress.begin(ARCH_STRUCTURE)
        emitter.emit(EventType.STEP_STARTED, "reading the repository's structure", step="manifest")
        outcome = detect_all(context, budget)
        classification = await classify(
            outcome.run, entrypoints=outcome.entrypoints, services=outcome.services, router=None
        )
        manifest = build_manifest(
            repo_url=repo_url.normalized,
            commit_sha=commit,
            outcome=outcome,
            languages=languages,
            classification=classification,
        )
        manifest.runtime = plan_runtime(context, manifest)
        manifest.facts = gather_facts(context)
        graph, index = build_static_architecture(context, outcome, manifest)
        graph = validate(await orient(graph, index, manifest, None), index)
        result = IngestionResult(
            repo_url=repo_url,
            commit_sha=commit,
            branch=clone.branch,
            manifest=manifest,
            architecture=graph,
            enrichment=EnrichmentResult(architecture_summary=graph.summary),
            evidence=build_evidence(outcome),
            classification=classification,
            languages=languages,
            file_count=tree.file_count,
            total_size=tree.total_size,
            tree_truncated=tree.truncated,
            limits_hit=list(report.hit),
            bytes_read=budget.bytes_read,
            files_read=budget.files_read,
        )
        with factory() as session:
            ingestion_id = save_ingestion(session, result).id
            link_ingestion(session, job_id, ingestion_id)
            session.commit()
        progress.done(
            REPO_MANIFEST, payload={"stage": "deterministic", "confidence": manifest.confidence}
        )
        progress.done(ARCH_STRUCTURE)
        analytics.record(AnalyticsEventType.FIRST_MEANINGFUL_CONTENT, ingestion_id=ingestion_id)
        analytics.record(
            AnalyticsEventType.REPO_MANIFEST_CREATED,
            confidence=manifest.confidence,
            surfaces=len(manifest.interaction_surfaces),
        )
        live = CloneSnapshot(root=root, commit_sha=commit, paths={e.path for e in tree.files})

        def persist(updated: ArchitectureGraph) -> None:
            with factory() as session:
                update_architecture(session, ingestion_id, updated)
                capture_sources(
                    session,
                    repo_url=repo_url.normalized,
                    commit_sha=commit,
                    snapshot=live,
                    paths=cited_paths(updated),
                )
                session.commit()

        # -- P0: the runtime architecture, then its sources --------------------------
        progress.begin(ARCH_RUNTIME)
        progress.begin(ARCH_SOURCE)
        try:
            graph = await runtime_architecture(graph, context, index, None)
            progress.verifying(ARCH_RUNTIME)
            persist(graph)
            progress.done(ARCH_RUNTIME)
            progress.done(ARCH_SOURCE)
        except Exception as exc:  # noqa: BLE001 - isolated: the rest still stands
            progress.failed(ARCH_RUNTIME, exc, stage="code model")

        # -- P1: the investigator agent refines the manifest ---------------------------
        router, research = providers()
        docs: list[Any] = []
        activity.enter("finding_entrypoints")
        try:
            refined, docs, run = await _refine_manifest(
                context,
                budget,
                router,
                research,
                emitter,
                activity,
                manifest,
                repo_url,
                commit,
                languages,
                analytics,
            )
            if run is not None:
                with factory() as session:
                    from repolab.db.jobs import save_agent_run

                    save_agent_run(session, job_id, run)
                    session.commit()
            if refined is not None and _materially_different(manifest, refined):
                manifest = refined
                with factory() as session:
                    update_manifest(session, ingestion_id, manifest)
                    session.commit()
                progress.updated(
                    REPO_MANIFEST, payload={"stage": "refined", "confidence": manifest.confidence}
                )
        except Exception as exc:  # noqa: BLE001 - the deterministic manifest stands
            log.warning("analysis.agent_refinement_failed", error=str(exc)[:300])
            emitter.emit(
                EventType.LOG_LINE,
                "the investigator agent could not refine the manifest; the rule-based one stands",
                step="manifest",
            )

        # -- P1: Super passes, each verified and persisted on its own ------------------
        activity.enter("understanding_data_flow")
        progress.begin(ARCH_COMPONENTS)
        try:
            graph = validate(await synthesise(graph, index, router), index)
            rank(graph, index, manifest)
            graph = _rename_function_runtime(graph)
            progress.verifying(ARCH_COMPONENTS)
            graph = verify_graph(graph, live)
            graph.reading_path = reading_path(graph)
            persist(graph)
            progress.done(ARCH_COMPONENTS, model=_model(router, "super"))
        except Exception as exc:  # noqa: BLE001 - isolated
            progress.failed(ARCH_COMPONENTS, exc, stage="component naming")

        if progress.usable(ARCH_COMPONENTS):
            progress.begin(ARCH_SUMMARY)
            try:
                graph = validate(await orient(graph, index, manifest, router, docs=docs), index)
                progress.verifying(ARCH_SUMMARY)
                graph = verify_graph(graph, live)
                graph.reading_path = reading_path(graph)
                persist(graph)
                progress.done(ARCH_SUMMARY, model=_model(router, "super"))
            except Exception as exc:  # noqa: BLE001 - isolated
                progress.failed(ARCH_SUMMARY, exc, stage="orientation")

        emitter.emit(
            EventType.STEP_DONE,
            f"architecture: {len(graph.subsystems)} major parts, "
            f"{len(graph.runtime.components)} runtime components, "
            f"{len(graph.nodes)} components, {graph.completeness.value}",
            step="architecture",
            data={"components": len(graph.nodes), "completeness": graph.completeness.value},
        )

    if prepare is not None:
        prepare(manifest, activity)
    return ingestion_id


# -- helpers ----------------------------------------------------------------------------


def _set_commit(factory: sessionmaker[Session], job_id: str, commit: str) -> None:
    with factory() as session:
        set_commit(session, job_id, commit)
        session.commit()


async def _reuse(
    job_id: str,
    repo_url: str,
    commit: str,
    factory: sessionmaker[Session],
    emitter: DbEventEmitter,
    analytics: Analytics,
) -> int | None:
    """Finish from cache when this commit was already fully analysed.

    If another job is analysing the same commit right now, follow it instead of
    starting a second analysis.
    """
    deadline = time.monotonic() + FOLLOW_SECONDS
    announced = False
    while True:
        with factory() as session:
            records = {t: artifacts.get(session, repo_url, commit, t) for t in EAGER}
            if all(r is not None and Status(r.status).usable for r in records.values()):
                ingestion = find_ingestion(session, repo_url, commit)
                if ingestion is None:
                    return None
                link_ingestion(session, job_id, ingestion.id)
                session.commit()
                artifacts.mark_cache_hits(session, repo_url, commit, records, emitter=None)
                session.commit()
                for record in records.values():
                    assert record is not None
                    artifacts.announce(
                        emitter,
                        record,
                        cache_hit=True,
                        message=f"{record.artifact_type} reused for this commit",
                    )
                emitter.emit(
                    EventType.STEP_DONE,
                    f"already analysed at {commit[:12]}: reusing the verified results",
                    step="manifest",
                    data={"ingestion_id": ingestion.id, "cache_hit": True},
                )
                analytics.record(
                    AnalyticsEventType.FIRST_MEANINGFUL_CONTENT,
                    ingestion_id=ingestion.id,
                    cache_hit=True,
                )
                return ingestion.id
            in_progress = [
                r
                for r in records.values()
                if r is not None
                and Status(r.status).active
                and r.job_id not in (None, job_id)
                and _job_alive(session, r.job_id)
            ]
        if not in_progress or time.monotonic() > deadline:
            return None
        if not announced:
            emitter.emit(
                EventType.LOG_LINE,
                f"another analysis of {commit[:12]} is in progress; "
                "following it instead of repeating it",
                step="manifest",
            )
            announced = True
        await asyncio.sleep(FOLLOW_POLL_SECONDS)


def _job_alive(session: Session, job_id: str | None) -> bool:
    """Running, and not interrupted: a job whose worker stopped beating is
    marked ``running`` until the abandoned-job sweep, but must not be followed."""
    if job_id is None:
        return False
    job = get_job(session, job_id)
    if job is None or job.status != "running":
        return False
    live = read_liveness(liveness.default_store(), job_key(job_id))
    return task_health(job.status, live, job.started_at or job.created_at) is not Health.INTERRUPTED


async def _refine_manifest(
    context: DetectionContext,
    budget: ReadBudget,
    router: Any,
    research: Any,
    emitter: DbEventEmitter,
    activity: ActivityReporter,
    manifest: RepoManifest,
    repo_url: RepoUrl,
    commit: str,
    languages: list[Any],
    analytics: Analytics,
) -> tuple[RepoManifest | None, list[Any], Any]:
    """The investigator agent's manifest, or ``None`` when there is no model to ask."""
    if router is None:
        return None, [], None
    from worker.tasks import retrieved_docs

    analytics.record(AnalyticsEventType.AGENT_STARTED, agent="repository_investigator")
    agent = RepositoryInvestigatorAgent(context, budget, research=research, router=router)
    investigation = await agent.investigate(emitter, activity=activity)
    for step in investigation.run.steps:
        if step.request is not None:
            analytics.record(AnalyticsEventType.TOOL_CALLED, tool=step.request.tool, ok=step.ok)
    if investigation.run.status is AgentStatus.FAILED:
        analytics.record(
            AnalyticsEventType.AGENT_FAILED, error=investigation.run.error or "unknown"
        )
        return None, [], investigation.run
    analytics.record(
        AnalyticsEventType.AGENT_COMPLETED,
        iterations=investigation.run.iterations,
        findings=investigation.findings,
    )
    outcome = investigation.outcome
    classification = await classify(
        outcome.run, entrypoints=outcome.entrypoints, services=outcome.services, router=router
    )
    refined = build_manifest(
        repo_url=repo_url.normalized,
        commit_sha=commit,
        outcome=outcome,
        languages=languages,
        classification=classification,
    )
    refined.runtime = manifest.runtime
    refined.facts = manifest.facts
    return refined, retrieved_docs(investigation.run), investigation.run


def _materially_different(before: RepoManifest, after: RepoManifest) -> bool:
    def key(m: RepoManifest) -> tuple:
        return (
            tuple(sorted((s.type.value, s.primary) for s in m.interaction_surfaces)),
            len(m.entrypoints),
            len(m.services),
            tuple(sorted(m.frameworks)),
        )

    return key(before) != key(after)


def _rename_function_runtime(graph: ArchitectureGraph) -> ArchitectureGraph:
    """A runtime model built from detected components follows their new names."""
    if graph.runtime.components and not any(c.members for c in graph.runtime.components):
        graph.runtime = runtime_from_components(graph)
        graph.flows = flows_from_components(graph, graph.runtime)
    return graph


def _model(router: Any, tier: str) -> str | None:
    if router is None:
        return None
    try:
        return str(router.models.get(tier)) if hasattr(router, "models") else None
    except Exception:  # noqa: BLE001 - informational only
        return None
