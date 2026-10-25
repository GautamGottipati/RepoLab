"""Small repositories of different kinds, and a helper that builds their architecture.

Each fixture is the least code that a real repository of that kind would
contain. None of them is named after, or shaped like, a demo repository: the
point of the tests that use them is that one generic analysis handles all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repolab.architecture.build import build_static_architecture
from repolab.architecture.models import ArchitectureGraph
from repolab.architecture.source import SourceIndex
from repolab.architecture.understanding import orient
from repolab.classify import classify
from repolab.evidence.models import DocumentationReference
from repolab.ingest.content import ReadBudget
from repolab.ingest.detectors import DetectionContext, detect_all
from repolab.ingest.languages import detect_languages
from repolab.ingest.tree import build_file_tree
from repolab.manifest.builder import build_manifest
from repolab.manifest.models import RepoManifest

SHA = "f" * 40

SPARK_PIPELINE: dict[str, str] = {
    "requirements.txt": "pyspark==3.5.1\n",
    "configs/job_config.json": '{"region": "eu"}\n',
    "jobs/daily_sales.py": (
        "from pyspark.sql import SparkSession\n"
        "\n"
        "def read_orders(spark):\n"
        "    return spark.read.parquet('s3a://bucket/orders')\n"
        "\n"
        "def join_customers(orders, customers):\n"
        "    return orders.join(customers, 'customer_id')\n"
        "\n"
        "def aggregate_revenue(df):\n"
        "    return df.groupBy('region').agg({'amount': 'sum'})\n"
        "\n"
        "def write_report(df):\n"
        "    df.write.mode('overwrite').parquet('s3a://bucket/report')\n"
        "\n"
        "def main():\n"
        "    config = open('configs/job_config.json').read()\n"
        "    spark = SparkSession.builder.config(\n"
        "        'spark.sql.shuffle.partitions', '200'\n"
        "    ).getOrCreate()\n"
        "    orders = read_orders(spark)\n"
        "    joined = join_customers(orders, spark.table('customers'))\n"
        "    write_report(aggregate_revenue(joined))\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
}

WEB_API: dict[str, str] = {
    "pyproject.toml": (
        "[project]\nname = 'shop'\ndependencies = ['fastapi', 'uvicorn', 'psycopg2-binary']\n"
    ),
    "shop/main.py": (
        "from fastapi import FastAPI\n"
        "from shop.routes import orders\n"
        "\n"
        "app = FastAPI()\n"
        "app.include_router(orders.router)\n"
    ),
    "shop/routes/orders.py": (
        "from fastapi import APIRouter\n"
        "import psycopg2\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/orders/{order_id}')\n"
        "def get_order(order_id: int):\n"
        "    conn = psycopg2.connect('dbname=shop')\n"
        "    return conn.cursor().execute('SELECT * FROM orders WHERE id = %s', (order_id,))\n"
        "\n"
        "@router.post('/orders')\n"
        "def create_order(order: dict):\n"
        "    conn = psycopg2.connect('dbname=shop')\n"
        "    conn.cursor().execute('INSERT INTO orders VALUES (%s)', (order,))\n"
        "    conn.commit()\n"
    ),
    "web/package.json": json.dumps(
        {"name": "shop-web", "dependencies": {"react": "18.3.1"}}, indent=2
    ),
    "web/src/main.tsx": (
        "import React from 'react';\nexport const load = () => fetch('/orders/1');\n"
    ),
}

CLI_TOOL: dict[str, str] = {
    "pyproject.toml": (
        "[project]\nname = 'notes'\ndependencies = ['click']\n"
        "[project.scripts]\nnotes = 'notes.cli:main'\n"
    ),
    "notes/__init__.py": "",
    "notes/cli.py": (
        "import click\n"
        "import sqlite3\n"
        "\n"
        "@click.group()\n"
        "def main():\n"
        "    pass\n"
        "\n"
        "@main.command()\n"
        "def add():\n"
        "    sqlite3.connect('notes.db').execute('INSERT INTO notes VALUES (1)')\n"
        "\n"
        "@main.command()\n"
        "def search():\n"
        "    sqlite3.connect('notes.db').execute('SELECT * FROM notes')\n"
    ),
}

WORKFLOW: dict[str, str] = {
    "requirements.txt": "apache-airflow==2.9.0\n",
    "dags/etl.py": (
        "from airflow import DAG\n"
        "from airflow.operators.python import PythonOperator\n"
        "\n"
        "with DAG('nightly_etl', schedule='@daily') as dag:\n"
        "    extract = PythonOperator(task_id='extract', python_callable=print)\n"
        "    transform = PythonOperator(task_id='transform', python_callable=print)\n"
        "    load = PythonOperator(task_id='load', python_callable=print)\n"
        "    extract >> transform >> load\n"
    ),
}

LIBRARY: dict[str, str] = {
    "pyproject.toml": "[project]\nname = 'tinyparse'\n",
    "tinyparse/__init__.py": (
        "from .lexer import tokenize\n"
        "from .parser import parse\n"
        "\n"
        "__all__ = ['tokenize', 'parse']\n"
    ),
    "tinyparse/lexer.py": "def tokenize(text):\n    return text.split()\n",
    "tinyparse/parser.py": (
        "from .lexer import tokenize\n\ndef parse(text):\n    return tokenize(text)\n"
    ),
}

#: A web app and API plus a Spark job in one repository.
HYBRID: dict[str, str] = {
    **WEB_API,
    "analytics/daily_sales.py": SPARK_PIPELINE["jobs/daily_sales.py"],
    "requirements.txt": "pyspark==3.5.1\n",
}

UNKNOWN: dict[str, str] = {
    "README.md": "# notes\nAssorted helpers.\n",
    "helpers.py": "def add(a, b):\n    return a + b\n",
}

#: A large multi-module engine: modules declared in its build, its own README,
#: and machine-learning wrappers deep inside one module. A beginner should meet
#: the modules first and the wrappers only inside theirs.
_WRAPPER = (
    "from pyspark.sql import SparkSession\n"
    "from pyspark.ml.clustering import {model}\n\n"
    "def fit(spark, path):\n"
    "    frame = spark.read.parquet(path)\n"
    "    grouped = frame.groupBy('k').count()\n"
    "    grouped.write.parquet(path + '.out')\n"
)
MULTI_MODULE: dict[str, str] = {
    "README.md": (
        "# Example Engine\n\n"
        "[![build](https://ci.example/badge.svg)](https://ci.example)\n\n"
        "Example Engine is a distributed engine for large-scale data processing "
        "across a cluster of machines.\n\n"
        "It offers APIs in Scala and Python. See the mllib and sql modules.\n\n"
        "## Building\n\nRun the build.\n\n## Machine learning\n\nThe mllib module.\n"
    ),
    "pom.xml": (
        "<project>\n"
        "  <description>A distributed data processing engine.</description>\n"
        "  <modules>\n"
        "    <module>core</module>\n"
        "    <module>sql/catalyst</module>\n"
        "    <module>sql/core</module>\n"
        "    <module>mllib</module>\n"
        "    <module>streaming</module>\n"
        "    <module>connector/kafka</module>\n"
        "    <module>connector/avro</module>\n"
        "    <module>examples</module>\n"
        "  </modules>\n"
        "</project>\n"
    ),
    "core/src/main/scala/org/example/engine/scheduler/DAGScheduler.scala": (
        "package org.example.engine.scheduler\n\n"
        "class DAGScheduler {\n  def submitJob(): Unit = {}\n}\n"
    ),
    "core/src/main/scala/org/example/engine/scheduler/TaskScheduler.scala": (
        "package org.example.engine.scheduler\n\nclass TaskScheduler\n"
    ),
    "core/src/main/scala/org/example/engine/executor/Executor.scala": (
        "package org.example.engine.executor\n\nclass Executor\n"
    ),
    "core/src/main/scala/org/example/engine/EngineContext.scala": (
        "package org.example.engine\n\nclass EngineContext\n"
    ),
    "core/src/main/java/org/example/engine/deploy/MasterDaemon.java": (
        "package org.example.engine.deploy;\n\npublic class MasterDaemon {\n"
        "  public static void main(String[] args) {}\n}\n"
    ),
    "sql/catalyst/src/main/scala/org/example/sql/catalyst/Optimizer.scala": (
        "package org.example.sql.catalyst\n\nclass Optimizer\n"
    ),
    "sql/core/src/main/scala/org/example/sql/Dataset.scala": (
        "package org.example.sql\n\nclass Dataset\n"
    ),
    "mllib/src/main/python/example/ml/kmeans_wrapper.py": _WRAPPER.format(model="KMeans"),
    "mllib/src/main/python/example/ml/gaussian_mixture_wrapper.py": _WRAPPER.format(
        model="GaussianMixture"
    ),
    "streaming/src/main/scala/org/example/streaming/StreamingContext.scala": (
        "package org.example.streaming\n\nclass StreamingContext\n"
    ),
    "connector/kafka/src/main/scala/org/example/kafka/KafkaSource.scala": (
        "package org.example.kafka\n\nimport org.apache.kafka.clients.consumer.KafkaConsumer\n\n"
        "class KafkaSource\n"
    ),
    "connector/avro/src/main/scala/org/example/avro/AvroReader.scala": (
        "package org.example.avro\n\nclass AvroReader\n"
    ),
    "examples/src/main/python/pi.py": "print(3.14)\n",
    "docs/ml-guide.md": "# MLlib guide\n",
    "docs/sql-programming-guide.md": "# SQL guide\n",
    "docs/streaming-programming-guide.md": "# Streaming guide\n",
}


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


async def analyse(
    root: Path,
    *,
    repo_url: str = "https://github.com/example/repo",
    router: Any | None = None,
    docs: list[DocumentationReference] | None = None,
) -> tuple[ArchitectureGraph, RepoManifest, SourceIndex]:
    """Detect, classify, build the manifest, then the architecture (all levels).

    Without *router* nothing is asked of a model: the graph is the static one
    plus the deterministic orientation.
    """
    tree = build_file_tree(root)
    context = DetectionContext(root=root, tree=tree, commit_sha=SHA)
    outcome = detect_all(context, ReadBudget())
    classification = await classify(
        outcome.run, entrypoints=outcome.entrypoints, services=outcome.services
    )
    manifest = build_manifest(
        repo_url=repo_url,
        commit_sha=SHA,
        outcome=outcome,
        languages=detect_languages(tree),
        classification=classification,
    )
    graph, index = build_static_architecture(context, outcome, manifest)
    graph = await orient(graph, index, manifest, router, docs=docs)
    return graph, manifest, index
