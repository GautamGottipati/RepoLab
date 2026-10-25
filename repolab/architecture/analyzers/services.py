"""Long-running processes: servers, daemons, background workers, consumers."""

from __future__ import annotations

import re

from repolab.architecture.analyzers.base import AnalysisContext, slug, stem, title
from repolab.architecture.models import ArchitectureNode, NodeCategory
from repolab.architecture.source import find_all, line_of

_DAEMON_CLASS = re.compile(r"\bclass\s+(?P<name>\w+Daemon)\b")
_LISTENS = re.compile(
    r"\bServerSocket(?:Channel)?\s*[.(]|\.listen\s*\(|\bserve_forever\s*\(|\bListenAndServe\s*\("
)
_MAIN = re.compile(r"public\s+static\s+void\s+main\s*\(|if\s+__name__\s*==|^func\s+main\s*\(", re.M)
#: Background-job frameworks. Airflow's ``@task`` is a workflow task, not a
#: worker, so only decorators namespaced by a job app (``@app.task``,
#: ``@shared_task``) count here.
_WORKER = re.compile(
    r"@\w+\.task\b|@shared_task\b|\bCelery\s*\(|\bdramatiq\.actor\b|\brq\.Worker\b"
)
_CONSUMER = re.compile(
    r"\bKafkaConsumer\s*\(|\bConsumer\s*\(\s*\{|@KafkaListener|\.basic_consume\s*\(|\.subscribe\s*\(\s*\["
)

MAX_SERVERS = 3


class ServiceAnalyzer:
    name = "services"

    def analyze(self, context: AnalysisContext) -> None:
        servers = 0
        for path, text in context.index.source_files():
            if servers >= MAX_SERVERS:
                break
            named = _DAEMON_CLASS.search(text)
            listens = _LISTENS.search(text)
            # A *Daemon class is a server by name; anything else must both start
            # (a main) and accept connections, or it is just a client.
            if named is None and not (listens and _MAIN.search(text)):
                continue
            match = named or listens
            assert match is not None
            label = title(named.group("name")) if named else f"{title(stem(path))} server"
            context.graph.add_node(
                ArchitectureNode(
                    id=f"service:{slug(path)}",
                    label=label,
                    category=NodeCategory.SERVICE,
                    subtype="daemon",
                    source_references=[context.index.ref(path, line_of(text, match.start()))],
                    confidence=0.8,
                    detected_because=f"{path} runs a long-lived server process",
                )
            )
            servers += 1

        for path, text in context.index.source_files():
            worker = next(find_all(_WORKER, text), None)
            consumer = next(find_all(_CONSUMER, text), None)
            if worker is None and consumer is None:
                continue
            hit = consumer or worker
            assert hit is not None
            line = hit[0]
            label = "Message consumer" if consumer else "Background worker"
            context.graph.add_node(
                ArchitectureNode(
                    id=f"worker:{slug(path)}",
                    label=f"{title(stem(path))} {label.lower()}"
                    if stem(path) not in {"worker", "tasks", "consumer"}
                    else label,
                    category=NodeCategory.WORKER,
                    source_references=[context.index.ref(path, line)],
                    confidence=0.75,
                    detected_because=f"{path}:{line} defines background work",
                )
            )
            # The queue it consumes from is linked in assembly, from the usage
            # storage recorded for this same file.
