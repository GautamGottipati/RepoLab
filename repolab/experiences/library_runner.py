"""Running a learner's Python against a library installed from the commit.

A fresh, network-less container from the installed image, the snippet written to
a file, ``python`` run on it with a timeout. Output and exit code are the
interpreter's own; the only measurement is the process's wall-clock time, and it
is labelled as that.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

SNIPPET_PATH = "/work/snippet.py"
RUN_TIMEOUT = 30.0
MAX_OUTPUT = 20_000


class LibraryRunResult(BaseModel):
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False
    wall_clock_seconds: float
    image: str


async def run_snippet(
    environment, *, image: str, code: str, python: str = "python"
) -> LibraryRunResult:
    started = time.perf_counter()
    await environment.start(image)
    try:
        await environment.put_files({SNIPPET_PATH: code.encode()})
        result = await environment.run(f"{python} {SNIPPET_PATH}", timeout=RUN_TIMEOUT, cwd="/work")
        stdout, stderr = result.stdout or "", result.stderr or ""
        truncated = len(stdout) > MAX_OUTPUT or len(stderr) > MAX_OUTPUT
        return LibraryRunResult(
            ok=result.exit_code == 0,
            exit_code=result.exit_code,
            stdout=stdout[:MAX_OUTPUT],
            stderr=stderr[-MAX_OUTPUT:],
            truncated=truncated,
            wall_clock_seconds=round(time.perf_counter() - started, 3),
            image=image,
        )
    finally:
        await environment.stop()
