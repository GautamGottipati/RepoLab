"""Framework adapters: they refine what the generic analyzers found, and only that.

Each sets a ``subtype`` or ``technology`` on existing nodes. None of them adds a
component the generic analyzers did not evidence, and removing all of them
leaves the graph's shape unchanged -- which is the test that the architecture
is generic.
"""

from __future__ import annotations

import re

from repolab.architecture.analyzers.base import AnalysisContext
from repolab.architecture.models import NodeCategory

_WEB_FRAMEWORKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FastAPI", re.compile(r"\bFastAPI\s*\(|from\s+fastapi\b|APIRouter\s*\(")),
    ("Flask", re.compile(r"\bFlask\s*\(|from\s+flask\b|Blueprint\s*\(")),
    ("Django", re.compile(r"from\s+django\b|urlpatterns\s*=")),
    ("Express", re.compile(r"require\(['\"]express['\"]\)|from\s+['\"]express['\"]")),
    ("Spring", re.compile(r"org\.springframework")),
    ("Go net/http", re.compile(r"\"net/http\"")),
)


class SparkAdapter:
    name = "spark"

    def analyze(self, context: AnalysisContext) -> None:
        for node in context.graph.nodes:
            if node.technology != "Spark":
                continue
            if node.category is NodeCategory.PIPELINE:
                node.subtype = node.subtype or "spark_job"
            elif node.category is NodeCategory.TRANSFORM:
                operations = node.properties.get("operations") or [node.properties.get("operation")]
                if isinstance(operations, list) and operations and operations[0]:
                    node.subtype = node.subtype or f"spark_{operations[0]}"


class WebFrameworkAdapter:
    name = "web_framework"

    def analyze(self, context: AnalysisContext) -> None:
        for node in context.graph.nodes:
            if node.category not in {NodeCategory.API, NodeCategory.SERVICE} or node.technology:
                continue
            for path in node.paths:
                text = context.index.text(path) or ""
                framework = next(
                    (name for name, pattern in _WEB_FRAMEWORKS if pattern.search(text)), None
                )
                if framework:
                    node.technology = framework
                    node.subtype = node.subtype or framework.lower().split()[0]
                    break


class WorkflowAdapter:
    name = "workflow_framework"

    def analyze(self, context: AnalysisContext) -> None:
        for node in context.graph.nodes:
            if (
                node.category is NodeCategory.PIPELINE
                and node.subtype == "workflow"
                and node.technology
            ):
                node.subtype = f"{node.technology.lower()}_workflow"
