"""Where data lives and moves: databases, caches, queues, object storage, models.

One table, keyed by what the code *imports* or *declares*, replaces the old
hardcoded list of datastore names. A store is only claimed where a source file
uses its client (cited at the import) or a compose file declares it; and which
component reads from or writes to it is decided in assembly, from the verbs in
the file that uses it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from repolab.architecture.analyzers.base import AnalysisContext, Usage
from repolab.architecture.models import ArchitectureNode, EdgeType, NodeCategory
from repolab.architecture.source import find_all


@dataclass(frozen=True)
class StoreKind:
    key: str
    label: str
    category: NodeCategory
    imports: re.Pattern[str]
    images: tuple[str, ...] = ()
    mentions: re.Pattern[str] | None = None
    """How a function body that *uses* the client reads. Defaults to ``imports``."""

    def __post_init__(self) -> None:
        if self.mentions is None:
            object.__setattr__(self, "mentions", self.imports)


STORES: tuple[StoreKind, ...] = (
    StoreKind(
        "postgres",
        "PostgreSQL",
        NodeCategory.DATABASE,
        re.compile(
            r"^\s*(?:import|from)\s+(?:psycopg2?|asyncpg)\b|require\(['\"]pg['\"]\)|from\s+['\"]pg['\"]|org\.postgresql|postgres(?:ql)?(?:\+\w+)?://",
            re.M,
        ),
        ("postgres",),
    ),
    StoreKind(
        "mysql",
        "MySQL",
        NodeCategory.DATABASE,
        re.compile(
            r"^\s*(?:import|from)\s+(?:pymysql|MySQLdb|mysql\.connector)\b|['\"]mysql2?['\"]|mysql(?:\+\w+)?://",
            re.M,
        ),
        ("mysql", "mariadb"),
    ),
    StoreKind(
        "sqlite",
        "SQLite",
        NodeCategory.DATABASE,
        re.compile(r"^\s*import\s+sqlite3\b|sqlite:///", re.M),
    ),
    StoreKind(
        "mongodb",
        "MongoDB",
        NodeCategory.DATABASE,
        re.compile(
            r"^\s*(?:import|from)\s+(?:pymongo|motor)\b|['\"]mongoose['\"]|['\"]mongodb['\"]|mongodb://",
            re.M,
        ),
        ("mongo",),
    ),
    StoreKind(
        "cassandra",
        "Cassandra",
        NodeCategory.DATABASE,
        re.compile(r"^\s*from\s+cassandra\.cluster\b|com\.datastax\.", re.M),
        ("cassandra",),
    ),
    StoreKind(
        "elasticsearch",
        "Elasticsearch",
        NodeCategory.DATABASE,
        re.compile(r"^\s*(?:import|from)\s+elasticsearch\b|@elastic/elasticsearch", re.M),
        ("elasticsearch", "opensearch"),
    ),
    StoreKind(
        "sql",
        "SQL database",
        NodeCategory.DATABASE,
        re.compile(r"\bcreate_engine\s*\(|\bDriverManager\.getConnection\s*\(|\bsql\.Open\s*\("),
    ),
    StoreKind(
        "redis",
        "Redis",
        NodeCategory.CACHE,
        re.compile(
            r"^\s*(?:import\s+redis|from\s+redis\b|import\s+aioredis)|['\"]ioredis['\"]|redis://",
            re.M,
        ),
        ("redis", "valkey"),
    ),
    StoreKind(
        "kafka",
        "Kafka",
        NodeCategory.QUEUE,
        re.compile(
            r"^\s*from\s+(?:kafka|aiokafka|confluent_kafka)\b|['\"]kafkajs['\"]|org\.apache\.kafka",
            re.M,
        ),
        ("kafka", "redpanda"),
    ),
    StoreKind(
        "rabbitmq",
        "RabbitMQ",
        NodeCategory.QUEUE,
        re.compile(r"^\s*(?:import|from)\s+(?:pika|kombu)\b|['\"]amqplib['\"]|amqp://", re.M),
        ("rabbitmq",),
    ),
    StoreKind(
        "s3",
        "Object storage (S3)",
        NodeCategory.STORAGE,
        re.compile(r"boto3\.(?:client|resource)\(\s*['\"]s3['\"]|s3a?://"),
        ("minio",),
    ),
    StoreKind(
        "sqs",
        "SQS queue",
        NodeCategory.QUEUE,
        re.compile(r"boto3\.(?:client|resource)\(\s*['\"]sqs['\"]"),
    ),
    StoreKind(
        "gcs",
        "Object storage (GCS)",
        NodeCategory.STORAGE,
        re.compile(r"google\.cloud\s+import\s+storage|gs://"),
    ),
    StoreKind(
        "ml_model",
        "ML model",
        NodeCategory.MODEL,
        re.compile(
            r"^\s*(?:import|from)\s+(?:torch|tensorflow|sklearn|transformers|xgboost|lightgbm|keras)\b",
            re.M,
        ),
    ),
)

#: Call-site shapes, per store, for deciding which function in a file uses it.
_MENTIONS: dict[str, str] = {
    "postgres": r"psycopg2?\.|asyncpg\.|postgres(?:ql)?://",
    "mysql": r"pymysql\.|MySQLdb\.|mysql\.connector|mysql://",
    "sqlite": r"sqlite3\.|sqlite:///",
    "mongodb": r"MongoClient|pymongo\.|mongodb://|mongoose\.",
    "cassandra": r"\bCluster\(|\bsession\.execute\(",
    "elasticsearch": r"\bElasticsearch\(|\bes\.(?:index|search)\(",
    "sql": r"\bcreate_engine\(|\bengine\.|\bsession\.(?:execute|query|add)",
    "redis": r"\bredis\.|\bRedis\(",
    "kafka": r"KafkaProducer|KafkaConsumer|\bProducer\(|\bConsumer\(",
    "rabbitmq": r"\bpika\.|\bchannel\.basic_",
    "s3": r"s3a?://|\.s3\.|boto3",
    "sqs": r"sqs",
    "gcs": r"gs://|storage\.Client",
    "ml_model": r"\.fit\(|\.predict\w*\(|from_pretrained|\btorch\.",
}
STORES = tuple(
    StoreKind(
        kind.key,
        kind.label,
        kind.category,
        kind.imports,
        kind.images,
        re.compile(_MENTIONS[kind.key]) if kind.key in _MENTIONS else None,
    )
    for kind in STORES
)

_WRITE = re.compile(
    r"\bINSERT\b|\bUPDATE\b|\bDELETE\b|\.insert(?:_one|_many)?\s*\(|\.add\s*\(|\.commit\s*\(|"
    r"\.set\s*\(|\.put(?:_object)?\s*\(|\.upload|\.save\s*\(|\.produce\s*\(|\.send\s*\(|"
    r"\.publish\s*\(|basic_publish|\.fit\s*\(|\.save_pretrained\s*\(|\.write(?:Stream)?\s*\.|"
    r"\.to_(?:csv|parquet|sql|json)\s*\(",
)
_READ = re.compile(
    r"\bSELECT\b|\.query\s*\(|\.find(?:_one)?\s*\(|\.get(?:_object)?\s*\(|\.execute\s*\(|"
    r"\.fetch\w*\s*\(|\.download|\.poll\s*\(|\.subscribe\s*\(|\.consume\s*\(|basic_consume|"
    r"\.predict\w*\s*\(|from_pretrained\s*\(|\.read(?:Stream)?\s*\.|\bread_(?:csv|parquet|sql|json)\s*\(",
)


class StorageAnalyzer:
    name = "storage"

    def analyze(self, context: AnalysisContext) -> None:
        for path, text in context.index.source_files():
            for kind in STORES:
                hit = next(find_all(kind.imports, text), None)
                if hit is None:
                    continue
                line, _ = hit
                node = self._store(context, kind)
                node.source_references.append(context.index.ref(path, line))
                self._record_use(context, kind, node.id, path, text, line)

        for service in context.outcome.services:
            haystack = f"{service.name} {service.image or ''}".lower()
            kind = next((k for k in STORES if any(img in haystack for img in k.images)), None)
            references = [
                ref
                for finding in context.outcome.run.of_kind("compose_service")
                if finding.value == service.name
                for ref in finding.references
            ][:2]
            if not references:
                continue
            if kind is not None:
                node = self._store(context, kind)
                node.source_references.extend(references)
                node.properties.setdefault("container", service.image or service.name)
            # Other compose services -- the application's own containers, a proxy,
            # a mail catcher -- are deployment, not components the code shows a
            # relationship to; drawing them would add boxes with no arrows.

        # A generic "SQL database" is only worth showing when nothing names the engine.
        generic = context.graph.node("store:sql")
        specific = [
            n
            for n in context.graph.nodes
            if n.id.startswith("store:")
            and n.category is NodeCategory.DATABASE
            and n.id != "store:sql"
        ]
        if generic is not None and specific:
            target = specific[0]
            target.source_references.extend(
                r for r in generic.source_references if r not in target.source_references
            )
            context.usages[:] = [
                u._replace(target=target.id) if u.target == "store:sql" else u
                for u in context.usages
            ]
            context.graph.remove_node("store:sql")

    @staticmethod
    def _record_use(
        context: AnalysisContext, kind: StoreKind, store_id: str, path: str, text: str, line: int
    ) -> None:
        """Which component in *path* uses the store, and whether it reads or writes.

        Decided per component where the file has several -- a ``search`` command
        that only selects must not be drawn writing because its neighbour
        ``add`` inserts. A component's body must mention the store to use it;
        a file with no such component falls back to the file as a whole.
        """
        lines = text.splitlines()
        ranged = [
            n
            for n in context.graph.nodes
            if path in n.paths
            and isinstance(n.properties.get("line_start"), int)
            and isinstance(n.properties.get("line_end"), int)
        ]
        used = False
        for node in ranged:
            start, end = int(node.properties["line_start"]), int(node.properties["line_end"])  # type: ignore[arg-type]
            body = "\n".join(lines[start - 1 : end])
            mention = kind.mentions.search(body)
            if mention is None:
                continue
            used = True
            at = start + body.count("\n", 0, mention.start())
            context.usages.append(Usage(store_id, path, at, _edge_for(kind, body), node.id))
        if not used:
            context.usages.append(Usage(store_id, path, line, _edge_for(kind, text)))

    @staticmethod
    def _store(context: AnalysisContext, kind: StoreKind) -> ArchitectureNode:
        return context.graph.add_node(
            ArchitectureNode(
                id=f"store:{kind.key}",
                label=kind.label,
                category=kind.category,
                subtype=kind.key,
                technology=kind.label,
                confidence=0.8,
                detected_because=f"source code uses a {kind.label} client",
            )
        )


def _edge_for(kind: StoreKind, text: str) -> EdgeType:
    """How the file that uses a store relates to it, from what the file does."""
    writes, reads = bool(_WRITE.search(text)), bool(_READ.search(text))
    if kind.category is NodeCategory.QUEUE:
        return (
            EdgeType.PUBLISHES_TO
            if writes and not reads
            else EdgeType.CONSUMES_FROM
            if reads
            else EdgeType.DEPENDS_ON
        )
    if writes:
        return EdgeType.WRITES_TO
    if reads:
        return EdgeType.READS_FROM
    return EdgeType.DEPENDS_ON
