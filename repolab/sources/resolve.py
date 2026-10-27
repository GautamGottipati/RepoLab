"""Turning a claim's "where" into lines RepoLab has checked itself.

A model may say *which file* and *which symbol*. It is never trusted for *which
lines*: the resolver opens the investigated snapshot, finds the definition,
computes its range, re-reads that range and confirms the anchor is in it. A
model's line number is at most a hint, and is recorded when it was corrected.

    verify_reference(snapshot, path, symbol="DAGScheduler")
        -> CodeReference(start_line=120, end_line=184, verified=True, ...)

Anything that cannot be confirmed comes back with ``verified=False`` and a
reason, and the UI says "Source location could not be verified" instead of
showing a citation.
"""

from __future__ import annotations

import re
from typing import Protocol

from repolab.evidence.models import CodeReference, EvidenceStrength
from repolab.sources.symbols import Symbol, SymbolIndex, normalize, scan

#: How far from a hinted line an anchor may be and still count as that line.
NEARBY_LINES = 3

#: Longest excerpt kept on a reference (the viewer reads the file itself).
EXCERPT_LINES = 6


class Snapshot(Protocol):
    """One repository at one commit -- the only place lines are read from."""

    commit_sha: str

    def exists(self, path: str) -> bool: ...

    def text(self, path: str) -> str | None: ...


def strength_of(path: str) -> EvidenceStrength:
    """How directly a file shows a claim, from what kind of file it is."""
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if re.search(
        r"(^|/)(tests?|spec|__tests__|testing)/|(^|/)test_|_test\.\w+$|\.spec\.|test\.\w+$", lower
    ):
        return EvidenceStrength.TEST
    if name in {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "build.sbt",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "cargo.toml",
        "go.mod",
        "go.work",
        "pnpm-workspace.yaml",
        "lerna.json",
        "makefile",
    }:
        return EvidenceStrength.BUILD_METADATA
    if name.endswith((".md", ".rst", ".txt", ".adoc")) or "/docs/" in f"/{lower}":
        return EvidenceStrength.IN_REPO_DOCUMENTATION
    if name.endswith(
        (
            ".yml",
            ".yaml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".properties",
            ".json",
            ".xml",
            ".env.example",
        )
    ) or name.startswith("dockerfile"):
        return EvidenceStrength.CONFIGURATION
    return EvidenceStrength.DIRECT_SOURCE


def _excerpt(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : min(end, start + EXCERPT_LINES - 1)])


def _unverified(path: str, commit: str, reason: str, *, symbol: str | None = None) -> CodeReference:
    return CodeReference(
        path=path,
        commit_sha=commit,
        symbol=symbol,
        verified=False,
        verification=reason,
        strength=strength_of(path),
    )


def _contains(lines: list[str], start: int, end: int, needle: str) -> bool:
    window = "\n".join(lines[start - 1 : end])
    return needle in window


def _pick(symbols: list[Symbol], hint: int | None) -> Symbol | None:
    """One definition among same-named ones: the one at the hinted line, else
    the outermost type, else the only one."""
    if not symbols:
        return None
    if hint is not None:
        for symbol in symbols:
            if symbol.start_line <= hint <= symbol.end_line:
                return symbol
    if len(symbols) == 1:
        return symbols[0]
    outer = [s for s in symbols if s.container is None]
    if len(outer) == 1:
        return outer[0]
    # A class and its companion object (Scala) or a type and its impl (Rust):
    # the type is what a claim names.
    types = [s for s in outer if s.kind in _PRIMARY_KINDS]
    if len(types) == 1:
        return types[0]
    return None


_PRIMARY_KINDS = frozenset({"class", "interface", "trait", "struct", "enum", "record"})


def verify_reference(
    snapshot: Snapshot,
    path: str,
    *,
    symbol: str | None = None,
    line: int | None = None,
    end_line: int | None = None,
    expect: str | None = None,
    commit_sha: str | None = None,
    index: SymbolIndex | None = None,
) -> CodeReference:
    """A reference whose lines RepoLab computed and re-read, or why it could not be.

    Args:
        path: repository-relative file, as claimed.
        symbol: a definition the claim is about (class, function...). When
            given, its range *is* the reference, whatever line was hinted.
        line, end_line: a hint -- from a model or a detector -- never trusted
            on its own.
        expect: text the cited lines must contain when there is no symbol
            (an identifier, a route, a config key).
        commit_sha: the commit the claim was made against. A reference is only
            ever resolved against that same snapshot.
        index: a symbol index already built for this snapshot, to search the
            repository when the symbol is not in the claimed file.
    """
    path = normalize(path)
    commit = snapshot.commit_sha
    if commit_sha and commit_sha != commit:
        return _unverified(
            path,
            commit_sha,
            f"claimed at {commit_sha[:12]}, but the snapshot is {commit[:12]}",
            symbol=symbol,
        )

    if symbol:
        symbol = symbol.strip().strip("`").split("(")[0].split(".")[-1].strip()

    text = snapshot.text(path) if snapshot.exists(path) else None
    if text is None and symbol and index is not None:
        # The file was wrong; a symbol defined exactly once elsewhere is still a fact.
        candidates = [
            s for s in index.by_name.get(symbol, []) if s.container is None
        ] or index.by_name.get(symbol, [])
        if len(candidates) == 1:
            found = candidates[0]
            other = snapshot.text(found.path)
            if other is not None:
                ref = _from_symbol(other.splitlines(), found, commit)
                ref.verification = f"symbol found in {found.path}, not {path}"
                return ref
    if text is None:
        return _unverified(path, commit, "file not in the investigated snapshot", symbol=symbol)

    lines = text.splitlines()
    if symbol:
        defined = index.in_file(path, symbol) if index is not None and path in index.files else None
        if defined is None:
            scanned = scan(path, text)
            defined = [s for s in scanned.symbols if s.name == symbol] if scanned else []
        found = _pick(defined, line)
        if found is None and index is not None and not defined:
            elsewhere = [s for s in index.by_name.get(symbol, []) if s.container is None]
            if len(elsewhere) == 1 and (other := snapshot.text(elsewhere[0].path)) is not None:
                ref = _from_symbol(other.splitlines(), elsewhere[0], commit)
                ref.verification = f"symbol defined in {elsewhere[0].path}, not {path}"
                return ref
        if found is None:
            reason = (
                "symbol defined more than once here"
                if defined
                else f"no definition of {symbol} in this file"
            )
            return _unverified(path, commit, reason, symbol=symbol)
        ref = _from_symbol(lines, found, commit)
        if line is not None and not (found.start_line <= line <= found.end_line):
            ref.verification = f"corrected from line {line}"
        return ref

    # No symbol: the hinted line must exist and the anchor must be there.
    if line is None:
        if expect:
            for number, content in enumerate(lines, 1):
                if expect in content:
                    return _from_range(lines, path, number, number, commit, "anchor found")
            return _unverified(path, commit, f"{expect!r} does not appear in this file")
        return (
            _from_range(lines, path, 1, min(len(lines), 1), commit, "file exists")
            if lines
            else _unverified(path, commit, "file is empty")
        )
    if not 1 <= line <= len(lines):
        return _unverified(path, commit, f"line {line} is outside this file ({len(lines)} lines)")
    end = end_line if end_line and end_line >= line else line
    end = min(end, len(lines))
    if not expect:
        return _from_range(lines, path, line, end, commit, "line exists; no anchor to check")
    if _contains(lines, line, end, expect):
        return _from_range(lines, path, line, end, commit, "anchor found")
    low, high = max(1, line - NEARBY_LINES), min(len(lines), end + NEARBY_LINES)
    for number in range(low, high + 1):
        if expect in lines[number - 1]:
            ref = _from_range(lines, path, number, number, commit, "anchor found")
            ref.verification = f"corrected from line {line}"
            return ref
    return _unverified(path, commit, f"{expect!r} is not at line {line}")


def _from_symbol(lines: list[str], symbol: Symbol, commit: str) -> CodeReference:
    if not _contains(lines, symbol.start_line, symbol.start_line, symbol.name):
        return _unverified(
            symbol.path, commit, "definition line does not name the symbol", symbol=symbol.name
        )
    return CodeReference(
        path=symbol.path,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        commit_sha=commit,
        symbol=symbol.name,
        excerpt=_excerpt(lines, symbol.start_line, symbol.end_line),
        strength=strength_of(symbol.path),
        verified=True,
        verification=f"{symbol.kind} {symbol.name}",
    )


def _from_range(
    lines: list[str], path: str, start: int, end: int, commit: str, why: str
) -> CodeReference:
    return CodeReference(
        path=path,
        start_line=start,
        end_line=end,
        commit_sha=commit,
        excerpt=_excerpt(lines, start, end),
        strength=strength_of(path),
        verified=True,
        verification=why,
    )


def parse_citation(text: str) -> tuple[str, int | None, str | None]:
    """``path``, ``path:line``, ``path#Symbol`` or ``path:Symbol`` -> parts."""
    text = text.strip().strip("`")
    symbol = None
    if "#" in text:
        text, symbol = text.split("#", 1)
    path, _, tail = text.rpartition(":")
    if not path:
        return text, None, symbol
    if tail.isdigit():
        return path, int(tail), symbol
    if re.fullmatch(r"\d+-\d+", tail):
        return path, int(tail.split("-")[0]), symbol
    if re.fullmatch(r"[A-Za-z_][\w.]*", tail):
        return path, None, symbol or tail
    return text, None, symbol
