"""External service detection from Dockerfiles and compose files.

Compose files are parsed as text line-by-line rather than with a YAML parser:
RepoLab has no YAML dependency, and the shape needed here -- service names,
images and ports -- is reliably extractable from the indentation.
"""

from __future__ import annotations

import re

from repolab.ingest.content import ReadBudget, read_text, reference
from repolab.ingest.detectors.base import DetectionContext, DetectorRun
from repolab.manifest.models import Service

_EXPOSE = re.compile(r"^\s*EXPOSE\s+(?P<ports>[\d\s/a-z]+)", re.I | re.M)
_FROM = re.compile(r"^\s*FROM\s+(?P<image>\S+)", re.I | re.M)
_SERVICE_NAME = re.compile(r"^  (?P<name>[A-Za-z0-9_.-]+):\s*$")
_IMAGE = re.compile(r"^\s+image:\s*['\"]?(?P<image>[^'\"\s]+)")
_PORT_MAPPING = re.compile(r"['\"]?(?P<host>\d{2,5}):(?P<container>\d{2,5})")

#: Images and service names that imply a known piece of infrastructure.
KNOWN_SERVICES: dict[str, tuple[str, int]] = {
    "cassandra": ("Cassandra", 9042),
    "postgres": ("PostgreSQL", 5432),
    "mysql": ("MySQL", 3306),
    "redis": ("Redis", 6379),
    "kafka": ("Kafka", 9092),
    "zookeeper": ("ZooKeeper", 2181),
    "elasticsearch": ("Elasticsearch", 9200),
    "mongo": ("MongoDB", 27017),
    "rabbitmq": ("RabbitMQ", 5672),
    "minio": ("MinIO", 9000),
    "spark": ("Spark", 7077),
    "airflow": ("Airflow", 8080),
}


def detect_services(
    context: DetectionContext, budget: ReadBudget
) -> tuple[DetectorRun, list[Service]]:
    """Find services the software expects to talk to."""
    run = DetectorRun()
    services: dict[str, Service] = {}

    for entry in context.tree.files:
        lowered = entry.name.lower()
        is_dockerfile = lowered.startswith("dockerfile") or entry.extension == ".dockerfile"
        is_compose = lowered.startswith(("docker-compose", "compose")) and entry.extension in {
            ".yml",
            ".yaml",
        }
        if not (is_dockerfile or is_compose):
            continue

        text = read_text(context.path_for(entry.path), budget=budget)
        if text is None:
            continue

        if is_dockerfile:
            _from_dockerfile(context, run, services, entry.path, text)
        else:
            _from_compose(context, run, services, entry.path, text)

    return run, list(services.values())


def _remember(services: dict[str, Service], name: str, image: str | None, ports: list[int]) -> None:
    existing = services.get(name)
    if existing is None:
        services[name] = Service(name=name, image=image, ports=sorted(set(ports)))
        return
    existing.image = existing.image or image
    existing.ports = sorted(set(existing.ports) | set(ports))


def _from_dockerfile(
    context: DetectionContext,
    run: DetectorRun,
    services: dict[str, Service],
    path: str,
    text: str,
) -> None:
    ports: list[int] = []
    for match in _EXPOSE.finditer(text):
        for token in match.group("ports").split():
            candidate = token.split("/")[0]
            if candidate.isdigit():
                ports.append(int(candidate))

    base_images = [m.group("image") for m in _FROM.finditer(text)]
    line = 1
    first_from = _FROM.search(text)
    if first_from:
        line = text[: first_from.start()].count("\n") + 1

    run.add(
        "dockerfile",
        path,
        detail=", ".join(base_images) or None,
        references=[reference(path, commit_sha=context.commit_sha, line=line)],
    )
    if ports:
        run.add(
            "exposed_ports",
            ",".join(str(p) for p in ports),
            detail=path,
            references=[reference(path, commit_sha=context.commit_sha)],
        )

    for image in base_images:
        known = _match_known(image)
        if known:
            name, default_port = known
            _remember(services, name, image, ports or [default_port])


def _from_compose(
    context: DetectionContext,
    run: DetectorRun,
    services: dict[str, Service],
    path: str,
    text: str,
) -> None:
    current: str | None = None
    current_line = 0
    image: str | None = None
    ports: list[int] = []

    def flush() -> None:
        if current is None:
            return
        known = _match_known(image or current)
        display = known[0] if known else current
        resolved = [known[1]] if not ports and known else ports
        _remember(services, display, image, resolved)
        run.add(
            "compose_service",
            display,
            detail=image or path,
            references=[reference(path, commit_sha=context.commit_sha, line=current_line)],
        )

    for number, raw in enumerate(text.splitlines(), start=1):
        name_match = _SERVICE_NAME.match(raw)
        if name_match:
            flush()
            current = name_match.group("name")
            current_line = number
            image, ports = None, []
            continue
        if current is None:
            continue
        image_match = _IMAGE.match(raw)
        if image_match:
            image = image_match.group("image")
            continue
        for port_match in _PORT_MAPPING.finditer(raw):
            ports.append(int(port_match.group("container")))

    flush()


def _match_known(token: str) -> tuple[str, int] | None:
    lowered = token.lower()
    for needle, value in KNOWN_SERVICES.items():
        if needle in lowered:
            return value
    return None
