"""Data pipelines: where data is read, how it is transformed, where it is written.

Framework-neutral by construction. A pipeline is any non-test file that both
reads or writes data and transforms it -- through Spark, pandas, Polars, Dask,
Beam or plain dataframes alike -- and its steps are found two ways:

* **Stage functions.** Most pipelines are written as named steps
  (``extract_data``, ``transform_data``, ``load_data``). When a file defines two
  or more, each becomes a component, ordered by the order the file *calls* them,
  and classified by what its body does: a step that reads is an input, one that
  writes is an output, anything else is a transform. The operations inside each
  (join, aggregate, filter...) are kept as its static properties.
* **Operations.** Otherwise the reads, transforms and writes themselves are the
  steps, in source order.

Configuration the file sets (``spark.sql.shuffle.partitions=200``) is recorded as
a static property of the pipeline, so a question about it can name the real value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise

from repolab.architecture.analyzers.base import AnalysisContext, slug, stem, title
from repolab.architecture.models import ArchitectureEdge, ArchitectureNode, EdgeType, NodeCategory
from repolab.architecture.source import find_all, line_of
from repolab.evidence.models import CodeReference

_READ = re.compile(
    r"\.read(?:Stream)?\s*\.\s*(?P<fmt>csv|json|parquet|orc|text|jdbc|table|format|load|avro|delta)\b|"
    r"\bread_(?P<fmt2>csv|parquet|json|sql|excel|table|feather)\s*\(|"
    r"\bspark\.table\s*\(|\btextFile\s*\(|\bReadFromText\b|\bscan_(?P<fmt3>csv|parquet)\s*\("
)
_WRITE = re.compile(
    r"\.write(?:Stream)?\s*\.\s*(?P<fmt>csv|json|parquet|orc|text|jdbc|format|save|mode|saveAsTable|insertInto|option|avro|delta)?|"
    r"\.to_(?P<fmt2>csv|parquet|json|sql|excel|feather)\s*\(|\.saveAsTable\s*\(|\.saveAsTextFile\s*\(|"
    r"\bWriteToText\b|\.sink_(?P<fmt3>parquet|csv)\s*\("
)

#: Operation kind -> its pattern. The labels are what a reader calls them.
OPERATIONS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("join", "Join", re.compile(r"\.join\s*\(|\bpd\.merge\s*\(|\.merge\s*\(")),
    (
        "aggregate",
        "Aggregate",
        re.compile(
            r"\.groupBy\s*\(|\.groupby\s*\(|\.group_by\s*\(|\.agg\s*\(|\.reduceByKey\s*\(|\.aggregate\s*\(|\.pivot\s*\("
        ),
    ),
    ("filter", "Filter", re.compile(r"\.filter\s*\(|\.where\s*\(|\.query\s*\(")),
    (
        "derive",
        "Derive columns",
        re.compile(
            r"\.withColumn\s*\(|\.with_columns?\s*\(|\.assign\s*\(|\.flatMap\s*\(|\.mapPartitions\s*\("
        ),
    ),
    ("select", "Select columns", re.compile(r"\.select\s*\(|\.selectExpr\s*\(")),
    (
        "deduplicate",
        "Deduplicate",
        re.compile(r"\.dropDuplicates\s*\(|\.drop_duplicates\s*\(|\.distinct\s*\("),
    ),
    ("sort", "Sort", re.compile(r"\.orderBy\s*\(|\.sort_values\s*\(|\.sortBy\s*\(")),
    ("window", "Window", re.compile(r"\bWindow\.\w+\s*\(|\.over\s*\(")),
    ("repartition", "Repartition", re.compile(r"\.repartition\s*\(|\.coalesce\s*\(")),
)

_TECHNOLOGY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*(?:from|import)\s+pyspark|org\.apache\.spark", re.M), "Spark"),
    (re.compile(r"^\s*import\s+polars|from\s+polars", re.M), "Polars"),
    (re.compile(r"^\s*import\s+dask|from\s+dask", re.M), "Dask"),
    (re.compile(r"^\s*import\s+apache_beam|from\s+apache_beam", re.M), "Apache Beam"),
    (re.compile(r"^\s*import\s+pandas|from\s+pandas", re.M), "pandas"),
)

_SETTING = re.compile(
    r"['\"](?P<key>(?:spark|hive|hadoop)\.[A-Za-z0-9_.]+)['\"]\s*[,:=]\s*['\"]?(?P<value>[\w.\-]+)"
)
_DEF = re.compile(r"^(?P<indent>[ \t]*)def\s+(?P<name>\w+)\s*\(", re.M)
_STAGE_NAME = re.compile(
    r"^(extract|load|transform|read|write|save|ingest|parse|clean|enrich|aggregate|join|compute|"
    r"process|prepare|filter|normali[sz]e|validate|score|train|predict|export|publish|store|fetch|"
    r"collect|build|merge|split|map|reduce)",
    re.I,
)

MAX_STEPS = 8


@dataclass
class _Function:
    name: str
    start: int
    end: int
    body: str
    reads: list[tuple[int, str]] = field(default_factory=list)
    writes: list[tuple[int, str]] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    call_line: int | None = None
    """Where the pipeline calls this step, which is what orders the flow."""


class DataPipelineAnalyzer:
    name = "data"

    def analyze(self, context: AnalysisContext) -> None:
        for path, text in context.index.source_files():
            reads = _formats(_READ, text)
            writes = _formats(_WRITE, text)
            ops = [
                (line, kind)
                for kind, _, pattern in OPERATIONS
                for line, _ in find_all(pattern, text)
            ]
            if not _is_pipeline(text, reads, writes, ops):
                continue
            self._pipeline(context, path, text, reads, writes, sorted(ops))

    def _pipeline(
        self,
        context: AnalysisContext,
        path: str,
        text: str,
        reads: list[tuple[int, str]],
        writes: list[tuple[int, str]],
        ops: list[tuple[int, str]],
    ) -> None:
        technology = next((name for pattern, name in _TECHNOLOGY if pattern.search(text)), None)
        settings = [f"{m.group('key')}={m.group('value')}" for _, m in find_all(_SETTING, text)]
        first_line = min(line for line, _ in [*reads, *writes, *ops])

        pipeline = context.graph.add_node(
            ArchitectureNode(
                id=f"pipeline:{slug(path)}",
                label=f"{title(stem(path))} pipeline",
                category=NodeCategory.PIPELINE,
                technology=technology,
                source_references=[context.index.ref(path, first_line)],
                properties={
                    "operations": sorted({kind for _, kind in ops}),
                    **({"settings": settings[:10]} if settings else {}),
                },
                confidence=0.85,
                detected_because=(
                    f"{path} reads or writes data and transforms it ({len(ops)} operation(s))"
                ),
            )
        )

        functions = _stage_functions(text)
        steps = (
            self._function_steps(context, path, text, functions, technology)
            if len(functions) >= 2
            else self._operation_steps(context, path, reads, writes, ops, technology)
        )
        if not steps:
            return

        context.graph.add_edge(
            ArchitectureEdge(
                source=pipeline.id,
                target=steps[0][0].id,
                type=EdgeType.CONTROL_FLOW,
                evidence=[steps[0][1]],
                description="runs first",
            )
        )
        for (previous, _), (current, evidence) in pairwise(steps):
            context.graph.add_edge(
                ArchitectureEdge(
                    source=previous.id,
                    target=current.id,
                    type=EdgeType.DATA_FLOW,
                    evidence=[evidence],
                    description="passes its result on",
                    confidence=0.8,
                )
            )

    def _function_steps(
        self,
        context: AnalysisContext,
        path: str,
        text: str,
        functions: list[_Function],
        technology: str | None,
    ) -> list[tuple[ArchitectureNode, CodeReference]]:
        order = _call_order(text, functions)
        any_input = any(fn.reads for fn in functions)
        steps = []
        for fn in order[:MAX_STEPS]:
            category = _function_category(fn, any_input)
            node = context.graph.add_node(
                ArchitectureNode(
                    id=f"step:{slug(path)}:{slug(fn.name)}",
                    label=title(fn.name),
                    category=category,
                    technology=technology,
                    source_references=[context.index.ref(path, fn.start, end_line=fn.end)],
                    properties={
                        "function": fn.name,
                        "line_start": fn.start,
                        "line_end": fn.end,
                        **({"operations": fn.operations} if fn.operations else {}),
                        **({"reads": [f for _, f in fn.reads]} if fn.reads else {}),
                        **({"writes": [f for _, f in fn.writes]} if fn.writes else {}),
                    },
                    confidence=0.8,
                    detected_because=_why_function(fn, category),
                )
            )
            steps.append((node, context.index.ref(path, fn.call_line or fn.start)))
        return steps

    def _operation_steps(
        self,
        context: AnalysisContext,
        path: str,
        reads: list[tuple[int, str]],
        writes: list[tuple[int, str]],
        ops: list[tuple[int, str]],
        technology: str | None,
    ) -> list[tuple[ArchitectureNode, CodeReference]]:
        events: list[tuple[int, NodeCategory, str, str]] = []
        for line, fmt in reads[:2]:
            events.append((line, NodeCategory.INPUT, f"read-{fmt}", f"Read {fmt}"))
        seen: set[str] = set()
        for line, kind in ops:
            if kind in seen:
                continue
            seen.add(kind)
            label = next(label for k, label, _ in OPERATIONS if k == kind)
            events.append((line, NodeCategory.TRANSFORM, kind, label))
        for line, fmt in writes[:2]:
            events.append((line, NodeCategory.OUTPUT, f"write-{fmt}", f"Write {fmt}"))
        events.sort()

        steps = []
        for line, category, key, label in events[:MAX_STEPS]:
            node = context.graph.add_node(
                ArchitectureNode(
                    id=f"step:{slug(path)}:{slug(key)}",
                    label=label,
                    category=category,
                    technology=technology,
                    source_references=[context.index.ref(path, line)],
                    properties={"operation": key},
                    confidence=0.75,
                    detected_because=f"{path}:{line} performs this operation",
                )
            )
            steps.append((node, context.index.ref(path, line)))
        return steps


def _is_pipeline(
    text: str,
    reads: list[tuple[int, str]],
    writes: list[tuple[int, str]],
    ops: list[tuple[int, str]],
) -> bool:
    """Whether a file moves data through transformations.

    With a dataframe library imported, reading or writing plus one operation is
    enough. Without one, ``.filter(`` and ``.write(`` are too common -- a Java
    serializer has both -- so the file must read *and* write and do at least two
    kinds of operation.
    """
    if not ops or not (reads or writes):
        return False
    if any(pattern.search(text) for pattern, _ in _TECHNOLOGY):
        return True
    return bool(reads and writes and len({kind for _, kind in ops}) >= 2)


def _formats(pattern: re.Pattern[str], text: str) -> list[tuple[int, str]]:
    found = []
    for line, match in find_all(pattern, text):
        groups = match.groupdict()
        fmt = groups.get("fmt") or groups.get("fmt2") or groups.get("fmt3") or "data"
        if fmt in {"format", "load", "option", "mode", "save"}:
            fmt = "data"
        found.append((line, fmt))
    return found


def _stage_functions(text: str) -> list[_Function]:
    """Top-level functions whose names say they are pipeline steps."""
    matches = [m for m in _DEF.finditer(text) if not m.group("indent")]
    functions: list[_Function] = []
    for index, match in enumerate(matches):
        name = match.group("name")
        if not _STAGE_NAME.match(name):
            continue
        start = line_of(text, match.start())
        end_offset = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start() : end_offset]
        end = line_of(text, end_offset) - (1 if index + 1 < len(matches) else 0)
        fn = _Function(name=name, start=start, end=max(start, end), body=body)
        fn.reads = [(start + line - 1, fmt) for line, fmt in _formats(_READ, body)]
        fn.writes = [(start + line - 1, fmt) for line, fmt in _formats(_WRITE, body)]
        fn.operations = [kind for kind, _, pattern in OPERATIONS if pattern.search(body)]
        functions.append(fn)
    return functions


def _call_order(text: str, functions: list[_Function]) -> list[_Function]:
    """The steps in the order the file calls them; definition order otherwise."""
    positions = []
    for fn in functions:
        call = None
        for match in re.finditer(rf"\b{re.escape(fn.name)}\s*\(", text):
            line = line_of(text, match.start())
            if not any(other.start <= line <= other.end for other in functions):
                call = line
                break
        positions.append((call, fn))
    called = [(line, fn) for line, fn in positions if line is not None]
    if len(called) >= 2:
        for line, fn in called:
            fn.call_line = line
        return [fn for _, fn in sorted(called, key=lambda pair: pair[0])]
    return functions


def _function_category(fn: _Function, any_input: bool) -> NodeCategory:
    if fn.writes:
        return NodeCategory.OUTPUT
    if fn.reads:
        return NodeCategory.INPUT
    lowered = fn.name.lower()
    if lowered.startswith(("write", "save", "export", "publish", "store")):
        return NodeCategory.OUTPUT
    if lowered.startswith(("extract", "read", "ingest", "fetch", "collect")):
        return NodeCategory.INPUT
    if lowered.startswith("load"):
        return NodeCategory.OUTPUT if any_input else NodeCategory.INPUT
    return NodeCategory.TRANSFORM


def _why_function(fn: _Function, category: NodeCategory) -> str:
    if fn.reads:
        return f"`{fn.name}` reads {', '.join(sorted({f for _, f in fn.reads}))} data"
    if fn.writes:
        return f"`{fn.name}` writes {', '.join(sorted({f for _, f in fn.writes}))} data"
    if fn.operations:
        return f"`{fn.name}` performs {', '.join(fn.operations)}"
    return f"`{fn.name}` is a named {category.value} step of the pipeline"
