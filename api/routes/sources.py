"""Source files at the exact commit a claim was made against.

    GET /jobs/{id}/source?path=        the investigated commit's copy
    GET /sources?repo_url=&commit_sha=&path=
                                       any captured commit (an answer made at
                                       another commit cites that commit)

Served only from RepoLab's own copies (:mod:`repolab.sources.store`). A file
that was not captured is reported as such, with a link to the host at the same
commit -- never replaced by whatever the repository holds today.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_session
from repolab.db.jobs import get_job
from repolab.db.models import Ingestion
from repolab.sources import read

router = APIRouter(tags=["sources"])

#: Longest file served whole; longer ones are served around the requested lines.
MAX_LINES = 5000


class SourceFile(BaseModel):
    repo_url: str
    commit_sha: str
    path: str
    captured: bool
    """False when RepoLab kept no copy of this file at this commit."""
    sha256: str | None = None
    first_line: int = 1
    """The line number of ``lines[0]`` (files longer than MAX_LINES are windowed)."""
    lines: list[str] = []
    line_count: int = 0
    host_url: str | None = None
    """The same file at the same commit on its host, when the host is known."""


def host_url(repo_url: str, commit_sha: str, path: str) -> str | None:
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if not match or not commit_sha:
        return None
    return f"https://github.com/{match[1]}/{match[2]}/blob/{commit_sha}/{path}"


def _serve(
    session: Session, repo_url: str, commit_sha: str, path: str, around: int | None
) -> SourceFile:
    if not path or ".." in path.split("/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid path")
    record = read(session, repo_url, commit_sha, path)
    link = host_url(repo_url, commit_sha, path)
    if record is None:
        return SourceFile(
            repo_url=repo_url, commit_sha=commit_sha, path=path, captured=False, host_url=link
        )
    lines = record.content.splitlines()
    first = 1
    if len(lines) > MAX_LINES:
        centre = around or 1
        first = max(1, centre - MAX_LINES // 2)
        lines = lines[first - 1 : first - 1 + MAX_LINES]
    return SourceFile(
        repo_url=repo_url,
        commit_sha=commit_sha,
        path=record.path,
        captured=True,
        sha256=record.sha256,
        first_line=first,
        lines=lines,
        line_count=len(record.content.splitlines()),
        host_url=link,
    )


@router.get("/jobs/{job_id}/source", response_model=SourceFile)
def job_source(
    job_id: str,
    session: Annotated[Session, Depends(get_session)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    around: Annotated[int | None, Query(ge=1)] = None,
) -> SourceFile:
    """A file as it was at the commit this job investigated."""
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no job {job_id}")
    ingestion = session.get(Ingestion, job.ingestion_id) if job.ingestion_id else None
    if ingestion is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"job {job_id} has not been investigated yet"
        )
    return _serve(session, job.repo_url, ingestion.commit_sha, path, around)


@router.get("/sources", response_model=SourceFile)
def source_at_commit(
    session: Annotated[Session, Depends(get_session)],
    repo_url: Annotated[str, Query(min_length=1, max_length=1024)],
    commit_sha: Annotated[str, Query(pattern=r"^[0-9a-f]{7,64}$")],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    around: Annotated[int | None, Query(ge=1)] = None,
) -> SourceFile:
    """A file at an explicit commit -- for a citation made somewhere other than
    the investigation (an answer, an experience)."""
    return _serve(session, repo_url, commit_sha, path, around)
