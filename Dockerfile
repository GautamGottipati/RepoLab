# RepoLab backend: one image for the API, the RQ workers and migrations.
#
#   api      uvicorn api.main:app
#   worker   python -m worker.main
#   migrate  alembic upgrade head
#
# Repository code never runs in this image. The API and the workers drive the
# host's Docker daemon (through its socket) and run repository code in sibling
# containers, exactly as `make dev` does on a laptop. This image needs the
# `docker` CLI and `git`, nothing else from the host.

FROM docker:28-cli AS docker-cli

FROM python:3.11-slim

# git: repository ingestion clones with a hardened `git` subprocess whose PATH is
# fixed to /usr/bin:/bin:/usr/local/bin, so it must live in one of those.
# curl: the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY repolab ./repolab
COPY api ./api
COPY worker ./worker
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev

# Runs as root on purpose. The worker bind-mounts a fresh 0700 work directory
# into each repository container, whose processes run as root with every
# capability dropped; they can write there only if root owns it. The Docker
# socket this process holds is root-equivalent on the host regardless.
CMD ["uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"]
