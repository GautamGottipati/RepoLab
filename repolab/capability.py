"""External capability probing.

RepoLab depends on capabilities that must be *observed* rather than assumed:
Nemotron inference (including where reasoning models put their output), reliable
structured JSON, Nebius Sandboxes, and Tavily research. This module probes each
one and reports what actually happened.

Design rules:

* Every probe is independently guarded. One failure never aborts the run, because
  a failed probe is a finding, not a crash.
* A missing credential is ``SKIP``, not ``FAIL`` -- nothing was tested, so nothing
  can be claimed either way.
* Optional capabilities (native tool calling) can only ``WARN``.
* Probe detail never contains secrets, and never contains model reasoning text --
  only field names and sizes, via :mod:`repolab.llm.response`.
"""

from __future__ import annotations

import difflib
import json
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from repolab.interfaces import LLMProvider, ResearchProvider
from repolab.llm.response import inspect_message
from repolab.sandbox.provider import SandboxProvider

SMOKE_IMAGE = "busybox:latest"
OFFICIAL_SPARK_DOMAIN = "spark.apache.org"


class ProbeStatus(StrEnum):
    """Outcome of a single capability probe."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


#: Worst-to-best ordering, used to aggregate several probes into one verdict.
_SEVERITY: dict[ProbeStatus, int] = {
    ProbeStatus.FAIL: 3,
    ProbeStatus.WARN: 2,
    ProbeStatus.SKIP: 1,
    ProbeStatus.PASS: 0,
}


def worst_status(statuses: list[ProbeStatus]) -> ProbeStatus:
    """Return the most severe status in *statuses* (``PASS`` when empty)."""
    if not statuses:
        return ProbeStatus.PASS
    return max(statuses, key=lambda status: _SEVERITY[status])


class ProbeResult(BaseModel):
    """What one probe observed."""

    capability: str
    status: ProbeStatus
    notes: str = ""
    latency_ms: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    """Optional capabilities cannot fail the overall run."""


class CapabilityReport(BaseModel):
    """A full capability run, renderable as a table."""

    results: list[ProbeResult] = Field(default_factory=list)

    def add(self, result: ProbeResult) -> None:
        self.results.append(result)

    def extend(self, results: list[ProbeResult]) -> None:
        self.results.extend(results)

    @property
    def overall(self) -> ProbeStatus:
        return worst_status([r.status for r in self.results])

    def blocking_failures(self) -> list[ProbeResult]:
        """Required probes that failed. These, and only these, fail the run."""
        return [r for r in self.results if r.status is ProbeStatus.FAIL and r.required]

    def exit_code(self) -> int:
        """Non-zero only when a *required* capability failed."""
        return 1 if self.blocking_failures() else 0

    def render_table(self) -> str:
        """Render the capability table."""
        header = ("Capability", "Status", "Notes")
        rows = [(r.capability, r.status.value, self._notes_for(r)) for r in self.results]

        cap_width = max(len(header[0]), *(len(r[0]) for r in rows)) if rows else len(header[0])
        status_width = max(len(header[1]), len("SKIP"))

        lines = [
            f"{header[0]:<{cap_width}}  {header[1]:<{status_width}}  {header[2]}",
            "-" * (cap_width + status_width + 4 + 40),
        ]
        for capability, status, notes in rows:
            lines.append(f"{capability:<{cap_width}}  {status:<{status_width}}  {notes}")
        return "\n".join(lines)

    @staticmethod
    def _notes_for(result: ProbeResult) -> str:
        notes = result.notes
        if result.latency_ms is not None:
            timing = f"{result.latency_ms:.0f}ms"
            notes = f"{notes} ({timing})" if notes else timing
        if not result.required and result.status is not ProbeStatus.PASS:
            notes = f"{notes} [optional]" if notes else "[optional]"
        return notes

    def render_markdown(self) -> str:
        """Render the same table as Markdown, for ``docs/PROGRESS.md``."""
        lines = ["| Capability | Status | Notes |", "|---|---|---|"]
        for result in self.results:
            notes = self._notes_for(result).replace("|", "\\|")
            lines.append(f"| {result.capability} | `{result.status.value}` | {notes} |")
        return "\n".join(lines)


def _describe_error(exc: BaseException) -> str:
    """One-line, secret-free description of an exception."""
    message = str(exc).strip().splitlines()
    detail = message[0] if message else ""
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


async def guarded(
    capability: str,
    operation: Callable[[], Awaitable[ProbeResult]],
    *,
    required: bool = True,
) -> ProbeResult:
    """Run *operation*, converting any exception into a ``FAIL`` row."""
    started = time.perf_counter()
    try:
        return await operation()
    except Exception as exc:  # noqa: BLE001 - a probe must never crash the run
        return ProbeResult(
            capability=capability,
            status=ProbeStatus.FAIL,
            notes=_describe_error(exc),
            latency_ms=(time.perf_counter() - started) * 1000,
            required=required,
        )


def skipped(capability: str, reason: str, *, required: bool = True) -> ProbeResult:
    """A probe that was not attempted, so claims nothing."""
    return ProbeResult(
        capability=capability,
        status=ProbeStatus.SKIP,
        notes=reason,
        required=required,
    )


def failed(capability: str, exc: BaseException, *, required: bool = True) -> ProbeResult:
    """A probe that was attempted and raised."""
    return ProbeResult(
        capability=capability,
        status=ProbeStatus.FAIL,
        notes=_describe_error(exc),
        required=required,
    )


# ---------------------------------------------------------------------------
# Local infrastructure
# ---------------------------------------------------------------------------


async def probe_postgres(database_url: str) -> ProbeResult:
    """Verify Postgres accepts connections and answers a trivial query."""
    import asyncio

    import psycopg

    def query() -> Any:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
            return row, conn.info.server_version

    started = time.perf_counter()
    row, server_version = await asyncio.to_thread(query)
    latency = (time.perf_counter() - started) * 1000

    ok = row is not None and row[0] == 1
    return ProbeResult(
        capability="Postgres connection",
        status=ProbeStatus.PASS if ok else ProbeStatus.FAIL,
        notes="" if ok else "SELECT 1 returned unexpected result",
        latency_ms=latency,
        detail={"server_version": server_version},
    )


async def probe_redis(redis_url: str) -> ProbeResult:
    """Verify Redis responds to PING."""
    import asyncio

    from redis import Redis

    def ping() -> Any:
        client = Redis.from_url(redis_url, socket_connect_timeout=5)
        try:
            return client.ping(), client.info("server").get("redis_version")
        finally:
            client.close()

    started = time.perf_counter()
    pong, version = await asyncio.to_thread(ping)
    latency = (time.perf_counter() - started) * 1000

    return ProbeResult(
        capability="Redis connection",
        status=ProbeStatus.PASS if pong else ProbeStatus.FAIL,
        notes="" if pong else "PING was not acknowledged",
        latency_ms=latency,
        detail={"redis_version": version},
    )


# ---------------------------------------------------------------------------
# Nemotron inference
# ---------------------------------------------------------------------------


async def probe_model_catalog(
    llm: LLMProvider,
    configured: dict[str, str | None],
) -> list[ProbeResult]:
    """List the live catalog, then check each configured model ID exists in it.

    CLAUDE.md forbids hardcoded model IDs, so the IDs in the environment are
    validated against what Token Factory actually serves rather than trusted.
    """
    started = time.perf_counter()
    catalog = await llm.list_models()
    latency = (time.perf_counter() - started) * 1000

    results = [
        ProbeResult(
            capability="Model catalog listing",
            status=ProbeStatus.PASS if catalog else ProbeStatus.FAIL,
            notes=f"{len(catalog)} models available" if catalog else "catalog returned no models",
            latency_ms=latency,
            detail={"model_count": len(catalog)},
        )
    ]

    for tier, model_id in configured.items():
        capability = f"{tier.capitalize()} model ID resolves"
        if not model_id:
            results.append(skipped(capability, f"MODEL_{tier.upper()} not set"))
            continue
        if model_id in catalog:
            results.append(
                ProbeResult(
                    capability=capability,
                    status=ProbeStatus.PASS,
                    notes=model_id,
                    detail={"model": model_id},
                )
            )
            continue

        suggestions = difflib.get_close_matches(model_id, catalog, n=3, cutoff=0.4)
        note = f"{model_id} not in catalog"
        if suggestions:
            note += f"; closest: {', '.join(suggestions)}"
        results.append(
            ProbeResult(
                capability=capability,
                status=ProbeStatus.FAIL,
                notes=note,
                detail={"configured": model_id, "suggestions": suggestions},
            )
        )

    return results


async def probe_inference(llm: LLMProvider, tier: str, model_id: str) -> ProbeResult:
    """Send one tiny request and record *where* the response text arrived.

    This is the probe that answers the open question for the model router: whether
    ``content`` is populated, and if not, which field carried the output. Only
    field names and sizes are recorded -- never reasoning text.
    """
    started = time.perf_counter()
    result = await llm.chat(
        model=model_id,
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        max_tokens=64,
    )
    latency = (time.perf_counter() - started) * 1000

    shape = inspect_message(result.raw_message)
    got_text = bool(result.text and result.text.strip())

    if not got_text:
        status = ProbeStatus.FAIL
        notes = f"no usable text; fields: {shape.summary}"
    elif not shape.content_populated:
        status = ProbeStatus.WARN
        notes = f"content empty; text arrived in '{shape.primary_text_field}'"
    else:
        status = ProbeStatus.PASS
        notes = f"content populated{'; +reasoning field' if shape.reasoning_fields else ''}"

    return ProbeResult(
        capability=f"{tier.capitalize()} inference",
        status=status,
        notes=notes,
        latency_ms=latency,
        detail={
            "model": model_id,
            "content_populated": shape.content_populated,
            "content_length": shape.content_length,
            "primary_text_field": shape.primary_text_field,
            "reasoning_fields": shape.reasoning_fields,
            "populated_fields": [f.model_dump() for f in shape.populated_fields],
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
        },
    )


class StatusPayload(BaseModel):
    """The tiny object each model is asked to produce."""

    status: str


STATUS_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "status_payload",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
    },
}


async def probe_structured_output(llm: LLMProvider, tier: str, model_id: str) -> ProbeResult:
    """Require a parseable, schema-valid ``{"status": "ok"}``.

    Nebius documentation notes that structured-output quality varies by model and
    recommends restating the schema in the prompt, which is done here.
    """
    started = time.perf_counter()
    result = await llm.chat(
        model=model_id,
        messages=[
            {
                "role": "system",
                "content": (
                    'Respond with JSON matching exactly: {"status": "ok"}. '
                    "No prose, no code fences."
                ),
            },
            {"role": "user", "content": "Report your status."},
        ],
        response_format=STATUS_JSON_SCHEMA,
        max_tokens=128,
    )
    latency = (time.perf_counter() - started) * 1000

    capability = f"{tier.capitalize()} structured JSON"
    text = (result.text or "").strip()
    if not text:
        return ProbeResult(
            capability=capability,
            status=ProbeStatus.FAIL,
            notes="empty response",
            latency_ms=latency,
            detail={"model": model_id},
        )

    try:
        payload = StatusPayload.model_validate(json.loads(text))
    except Exception as exc:  # noqa: BLE001 - malformed JSON is the finding
        return ProbeResult(
            capability=capability,
            status=ProbeStatus.FAIL,
            notes=f"unparseable: {_describe_error(exc)}",
            latency_ms=latency,
            detail={"model": model_id, "response_length": len(text)},
        )

    return ProbeResult(
        capability=capability,
        status=ProbeStatus.PASS,
        notes=f'parsed status="{payload.status}"',
        latency_ms=latency,
        detail={"model": model_id, "status": payload.status},
    )


TEMPERATURE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_current_temperature",
        "description": "Get the current temperature for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}


async def probe_tool_calling(llm: LLMProvider, tier: str, model_id: str) -> ProbeResult:
    """Check whether the model emits a well-formed tool request.

    The tool is never executed. RepoLab does not depend on native tool calling --
    agents request structured actions that application code validates -- so this
    probe is optional and can only WARN.
    """
    capability = f"{tier.capitalize()} native tool calling"
    started = time.perf_counter()
    result = await llm.chat(
        model=model_id,
        messages=[{"role": "user", "content": "What is the current temperature in Berlin?"}],
        tools=[TEMPERATURE_TOOL],
        max_tokens=256,
    )
    latency = (time.perf_counter() - started) * 1000

    if not result.tool_calls:
        return ProbeResult(
            capability=capability,
            status=ProbeStatus.WARN,
            notes="no tool request returned",
            latency_ms=latency,
            required=False,
            detail={"model": model_id},
        )

    call = result.tool_calls[0]
    function = getattr(call, "function", None)
    name = getattr(function, "name", None)
    raw_arguments = getattr(function, "arguments", None)

    well_formed = name == TEMPERATURE_TOOL["function"]["name"]
    parsed_arguments: Any = None
    if isinstance(raw_arguments, str):
        try:
            parsed_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            well_formed = False
    elif isinstance(raw_arguments, dict):
        parsed_arguments = raw_arguments

    if not isinstance(parsed_arguments, dict) or "city" not in parsed_arguments:
        well_formed = False

    return ProbeResult(
        capability=capability,
        status=ProbeStatus.PASS if well_formed else ProbeStatus.WARN,
        notes=f"requested {name}" if well_formed else f"malformed request (name={name})",
        latency_ms=latency,
        required=False,
        detail={
            "model": model_id,
            "tool_name": name,
            "argument_keys": sorted(parsed_arguments)
            if isinstance(parsed_arguments, dict)
            else None,
        },
    )


# ---------------------------------------------------------------------------
# Sandboxes
# ---------------------------------------------------------------------------


async def probe_sandbox(
    sandbox: SandboxProvider,
    image: str = SMOKE_IMAGE,
) -> list[ProbeResult]:
    """Start a sandbox from *image* and run the two smoke commands.

    ``python --version`` is expected to be absent from ``busybox``; that is
    recorded as an observation about the image, not a sandbox failure.
    """
    started = time.perf_counter()
    handle = await sandbox.create(image)
    create_latency = (time.perf_counter() - started) * 1000

    results = [
        ProbeResult(
            capability="Sandbox creation",
            status=ProbeStatus.PASS,
            notes=image,
            latency_ms=create_latency,
            detail={"image": image, "handle": handle},
        )
    ]

    started = time.perf_counter()
    handle, echo = await sandbox.execute(handle, "echo hello")
    echo_latency = (time.perf_counter() - started) * 1000

    echoed = echo.stdout.strip() == "hello" and echo.exit_code == 0
    results.append(
        ProbeResult(
            capability="Sandbox command",
            status=ProbeStatus.PASS if echoed else ProbeStatus.FAIL,
            notes="echo hello" if echoed else f"exit={echo.exit_code}, stdout={echo.stdout!r}",
            latency_ms=echo_latency,
            detail={
                "exit_code": echo.exit_code,
                "stdout": echo.stdout.strip()[:200],
                "stderr": echo.stderr.strip()[:200],
                # The runtime reports these itself; they are real measurements
                # and will matter once experiments need timing and budgeting.
                "elapsed_seconds": echo.elapsed_seconds,
                "cost": echo.cost,
            },
        )
    )

    # Checkpointing is how experiment variants later start from identical state,
    # so whether the provider can persist and name one matters.
    checkpoint_id: str | None = None
    try:
        checkpoint = await sandbox.checkpoint(handle)
        checkpoint_id = checkpoint.id
    except Exception as exc:  # noqa: BLE001 - recorded as an observation
        results.append(
            ProbeResult(
                capability="Sandbox checkpoint",
                status=ProbeStatus.WARN,
                notes=_describe_error(exc),
                required=False,
            )
        )
    else:
        results.append(
            ProbeResult(
                capability="Sandbox checkpoint",
                status=ProbeStatus.PASS,
                notes=f"checkpoint {checkpoint_id[:12]}",
                required=False,
                detail={"checkpoint_id": checkpoint_id},
            )
        )

    preserved = bool(checkpoint_id or echo.state_id)
    results.append(
        ProbeResult(
            capability="Sandbox state preserved",
            status=ProbeStatus.PASS if preserved else ProbeStatus.WARN,
            notes="reusable state id returned" if preserved else "no state id returned",
            required=False,
            detail={"state_id_present": preserved},
        )
    )

    handle, python = await sandbox.execute(handle, "python --version")
    available = python.exit_code == 0
    version = (python.stdout or python.stderr).strip()[:80]
    results.append(
        ProbeResult(
            capability="Sandbox python present",
            status=ProbeStatus.PASS if available else ProbeStatus.WARN,
            notes=version if available else f"not in {image} (exit={python.exit_code})",
            required=False,
            detail={"exit_code": python.exit_code, "output": version},
        )
    )

    return results


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


async def probe_research(
    research: ResearchProvider,
    *,
    domain: str = OFFICIAL_SPARK_DOMAIN,
) -> ProbeResult:
    """Search one official domain and require URLs *and* usable snippets."""
    started = time.perf_counter()
    hits = await research.search(
        "shuffle partitions configuration",
        include_domains=[domain],
        max_results=3,
    )
    latency = (time.perf_counter() - started) * 1000

    capability = "Tavily official search"
    if not hits:
        return ProbeResult(
            capability=capability,
            status=ProbeStatus.FAIL,
            notes=f"no results for {domain}",
            latency_ms=latency,
        )

    with_content = [h for h in hits if h.content and h.content.strip()]
    off_domain = [h for h in hits if domain not in h.url]

    if not with_content:
        status, notes = ProbeStatus.FAIL, "results carried no snippet content"
    elif off_domain:
        status = ProbeStatus.WARN
        notes = f"{len(hits)} results, {len(off_domain)} outside {domain}"
    else:
        status = ProbeStatus.PASS
        notes = f"{len(hits)} results with snippets"

    return ProbeResult(
        capability=capability,
        status=status,
        notes=notes,
        latency_ms=latency,
        detail={
            "result_count": len(hits),
            "with_content": len(with_content),
            "sample_url": hits[0].url,
        },
    )
