"""Statistics over CLI experiment runs, and parsing cqlsh's own output.

Python only. Row counts and error classes are read from what cqlsh printed --
its ``(N rows)`` footer and the server's ``<Class>: Error from server: code=...``
line -- and durations are the wall clock around each invocation. No model is
involved in any of it.
"""

from __future__ import annotations

import re
import statistics as stdlib_statistics

from repolab.experiments.cli.models import (
    CliStatistics,
    CliVariantRun,
    CliVariantStatistics,
    Spread,
    StatementStatistics,
    metric_prefix,
)

_ROWS = re.compile(r"\((\d+) rows?\)")

#: ``<stdin>:1:InvalidRequest: Error from server: code=2200 [Invalid query] message="..."``
_SERVER_ERROR = re.compile(
    r"(?P<cls>[A-Z][A-Za-z]+(?:Exception|Error|Request|Timeout|Unavailable)?)"
    r":\s*Error from server:\s*code=(?P<code>\d+)"
)
_CLIENT_ERROR = re.compile(
    r"(?P<cls>SyntaxException|InvalidRequest|ConfigurationException|Unavailable|"
    r"ReadTimeout|WriteTimeout|NoHostAvailable|OperationTimedOut)"
)


def parse_rows(stdout: str) -> int | None:
    """The row count cqlsh reported, if it reported one."""
    matches = _ROWS.findall(stdout)
    return int(matches[-1]) if matches else None


def parse_error(stdout: str, stderr: str) -> tuple[str | None, int | None, str | None]:
    """The error class, code and first error line cqlsh printed, if any."""
    for text in (stderr, stdout):
        match = _SERVER_ERROR.search(text)
        if match:
            line = next((line for line in text.splitlines() if "Error from server" in line), "")
            return match.group("cls"), int(match.group("code")), line.strip()[:500] or None
    for text in (stderr, stdout):
        match = _CLIENT_ERROR.search(text)
        if match:
            line = next((line for line in text.splitlines() if match.group("cls") in line), "")
            return match.group("cls"), None, line.strip()[:500] or None
    return None, None, None


def spread(samples: list[float]) -> Spread:
    values = [float(value) for value in samples] or [0.0]
    return Spread(
        samples=values,
        median=round(stdlib_statistics.median(values), 3),
        minimum=round(min(values), 3),
        maximum=round(max(values), 3),
        spread=round(max(values) - min(values), 3),
        stddev=round(stdlib_statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
        mean=round(stdlib_statistics.fmean(values), 3),
    )


def compute_cli_statistics(runs: list[CliVariantRun]) -> CliStatistics:
    """Aggregate each variant's statements across their repeats."""
    by_variant: dict[int, list[CliVariantRun]] = {}
    for run in runs:
        by_variant.setdefault(run.variant_index, []).append(run)

    variants: list[CliVariantStatistics] = []
    metrics: list[str] = []
    setup_failures = 0

    for index in sorted(by_variant):
        group = sorted(by_variant[index], key=lambda run: run.repeat)
        first = group[0]
        setup_failures += sum(1 for run in group for outcome in run.setup if not outcome.ok)

        statements: list[StatementStatistics] = []
        for position, outcome in enumerate(first.statements):
            samples = [run.statements[position] for run in group if position < len(run.statements)]
            rows = [sample.rows for sample in samples]
            known = [value for value in rows if value is not None]
            errors = sorted({sample.error_class for sample in samples if sample.error_class})
            message = next(
                (
                    (sample.stderr or sample.stdout).strip().splitlines()[0][:500]
                    for sample in samples
                    if not sample.ok and (sample.stderr or sample.stdout).strip()
                ),
                None,
            )
            statements.append(
                StatementStatistics(
                    index=outcome.index,
                    statement=outcome.statement,
                    runs=len(samples),
                    succeeded_runs=sum(1 for sample in samples if sample.ok),
                    duration_ms=spread([sample.duration_ms for sample in samples]),
                    rows=rows,
                    rows_median=stdlib_statistics.median(known) if known else None,
                    rows_stable=len(set(rows)) <= 1,
                    error_classes=errors,
                    error_message=message,
                )
            )
            prefix = metric_prefix(first.label, outcome.index)
            metrics.extend([f"{prefix} duration_ms", f"{prefix} succeeded"])
            if known:
                metrics.append(f"{prefix} rows")

        variants.append(
            CliVariantStatistics(
                variant_id=first.variant_id,
                label=first.label,
                runs=len(group),
                failed_statements=sum(
                    1 for run in group for outcome in run.statements if not outcome.ok
                ),
                total_duration_ms=spread(
                    [sum(outcome.duration_ms for outcome in run.statements) for run in group]
                ),
                statements=statements,
            )
        )
        metrics.append(f"{first.label} total_duration_ms")

    return CliStatistics(variants=variants, metrics=metrics, setup_failures=setup_failures)


def allowed_numbers(statistics: CliStatistics) -> set[float]:
    """Every number the statistics contain, for the explanation's numeric guard."""
    numbers: set[float] = set()
    for variant in statistics.variants:
        numbers.update(_spread_numbers(variant.total_duration_ms))
        numbers.update({float(variant.runs), float(variant.failed_statements)})
        for statement in variant.statements:
            numbers.update(_spread_numbers(statement.duration_ms))
            numbers.update({float(statement.runs), float(statement.succeeded_runs)})
            numbers.update(float(value) for value in statement.rows if value is not None)
            if statement.rows_median is not None:
                numbers.add(float(statement.rows_median))
            numbers.update(float(n) for n in re.findall(r"\d+", statement.statement))
            if statement.error_message:
                numbers.update(float(n) for n in re.findall(r"\d+", statement.error_message))
    return numbers


def _spread_numbers(value: Spread) -> set[float]:
    return {
        *value.samples,
        value.median,
        value.minimum,
        value.maximum,
        value.spread,
        value.stddev,
        value.mean,
    }
