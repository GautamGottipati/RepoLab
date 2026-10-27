"""Definitions in source files, with the lines they span.

Deterministic and dependency-free: one regular expression per language finds
where a class, function or similar begins, and the end is found by matching
braces (strings and comments skipped) or, for Python and brace-less bodies, by
indentation. This is how RepoLab turns "DAGScheduler in DAGScheduler.scala"
into "lines 120-184" without ever trusting a model for a number.

It is approximate by design and errs towards *not* answering: a definition
whose block cannot be matched gets its first line only, and the verifier
(:mod:`repolab.sources.resolve`) re-reads every range before it is shown.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

#: Language by file extension. Anything else has no symbols.
LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "js",
    ".tsx": "js",
    ".go": "go",
    ".rs": "rust",
}

_JAVA_MODS = (
    r"(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|strictfp"
    r"|synchronized|native|default)\s+)"
)
_KOTLIN_MODS = (
    r"(?:(?:public|private|protected|internal|open|abstract|sealed|data|enum|annotation"
    r"|inner|override|suspend|inline|value|final|operator|infix|tailrec|external|companion)\s+)"
)
_SCALA_MODS = (
    r"(?:(?:private|protected)(?:\[[\w.]+\])?\s+"
    r"|(?:final|sealed|abstract|implicit|case|override|lazy|inline|transparent|opaque|open)\s+)"
)
_SP = r"[ \t]"
_ID = r"(?P<name>[A-Za-z_]\w*)"
_JS_ID = r"(?P<name>[A-Za-z_$][\w$]*)"
_EXPORT = rf"^{_SP}*(?:export{_SP}+)?(?:default{_SP}+)?"


def _p(*parts: str) -> re.Pattern[str]:
    return re.compile("".join(parts), re.M)


#: (pattern, fixed kind or None to use the ``kind`` group). Anchored at line starts.
_PATTERNS: dict[str, list[tuple[re.Pattern[str], str | None]]] = {
    "python": [(_p(rf"^{_SP}*(?:async{_SP}+)?(?P<kind>class|def){_SP}+", _ID), None)],
    "java": [
        (
            _p(rf"^{_SP}*{_JAVA_MODS}*(?P<kind>class|interface|enum|record|@interface){_SP}+", _ID),
            None,
        ),
        # A method needs at least one modifier, so statements are not mistaken for it.
        (
            _p(
                rf"^{_SP}*{_JAVA_MODS}+(?:<[^>\n]*>{_SP}*)?[\w<>\[\],.? ]+?{_SP}+",
                r"(?P<name>[a-z_]\w*)",
                rf"{_SP}*\(",
            ),
            "method",
        ),
    ],
    "kotlin": [
        (
            _p(
                rf"^{_SP}*{_KOTLIN_MODS}*(?P<kind>class|interface|object|fun){_SP}+",
                rf"(?:<[^>\n]*>{_SP}*)?(?:[\w.]+\.)?",
                _ID,
            ),
            None,
        )
    ],
    "scala": [
        (
            _p(
                rf"^{_SP}*{_SCALA_MODS}*(?P<kind>class|object|trait|enum|def){_SP}+",
                r"(?P<name>[A-Za-z_]\w*|`[^`]+`)",
            ),
            None,
        )
    ],
    "js": [
        (_p(_EXPORT, rf"(?:abstract{_SP}+)?(?P<kind>class|interface){_SP}+", _JS_ID), None),
        (_p(_EXPORT, rf"(?:async{_SP}+)?(?P<kind>function)\*?{_SP}+", _JS_ID), None),
        (
            _p(
                rf"^{_SP}*(?:export{_SP}+)?(?:const|let|var){_SP}+",
                _JS_ID,
                rf"{_SP}*(?::[^=\n]+)?={_SP}*(?:async{_SP}+)?",
                rf"(?:\([^)\n]*\)|[A-Za-z_$][\w$]*){_SP}*(?::[^=\n]+)?=>",
            ),
            "function",
        ),
    ],
    "go": [
        (_p(rf"^func{_SP}+(?:\([^)\n]*\){_SP}*)?", _ID), "func"),
        (_p(rf"^type{_SP}+", _ID, rf"{_SP}+(?P<kind>struct|interface)\b"), None),
    ],
    "rust": [
        (
            _p(
                rf"^{_SP}*(?:pub(?:\([^)\n]*\))?{_SP}+)?",
                rf"(?:(?:async|unsafe|const|extern(?:{_SP}+\"\w+\")?){_SP}+)*",
                rf"(?P<kind>fn|struct|trait|enum|impl|mod)(?:{_SP}*<[^>\n]*>)?{_SP}+",
                _ID,
            ),
            None,
        )
    ],
}

#: Strings and comments to skip, and braces to pair, per family.
_C_TOKENS = re.compile(
    r'"""[\s\S]*?"""'  # Scala/Kotlin raw strings
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])'"  # char literals only; lone ' (Rust lifetimes) is left alone
    r"|//[^\n]*"
    r"|/\*[\s\S]*?\*/"
    r"|[{}]"
)
_JS_TOKENS = re.compile(
    r'"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'"
    r"|`(?:\\.|[^`\\])*`"
    r"|//[^\n]*"
    r"|/\*[\s\S]*?\*/"
    r"|[{}]"
)

#: Guard rails for pathological files.
MAX_FILE_BYTES = 2_000_000
MAX_BLOCK_LINES = 20_000


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    container: str | None = None
    """The enclosing definition's name, when nested."""


@dataclass
class FileSymbols:
    path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)


def normalize(path: str) -> str:
    """A repository-relative path: leading ``./`` removed, nothing else touched."""
    path = path.strip().strip("`")
    while path.startswith("./"):
        path = path[2:]
    return path


def language_of(path: str) -> str | None:
    dot = path.rfind(".")
    return LANGUAGE.get(path[dot:].lower()) if dot >= 0 else None


class _Lines:
    """Offset -> 1-based line number, for one text."""

    def __init__(self, text: str) -> None:
        self.starts = [0]
        for match in re.finditer("\n", text):
            self.starts.append(match.end())
        self.count = len(self.starts) if not text.endswith("\n") else len(self.starts) - 1

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


def _brace_pairs(text: str, language: str) -> dict[int, int]:
    """Every ``{`` offset mapped to its matching ``}``, strings and comments skipped."""
    tokens = _JS_TOKENS if language == "js" else _C_TOKENS
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for match in tokens.finditer(text):
        token = match.group()
        if token == "{":
            stack.append(match.start())
        elif token == "}" and stack:
            pairs[stack.pop()] = match.start()
    return pairs


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _indented_end(lines: list[str], start: int) -> int:
    """The last line of a block defined by indentation, from 1-based *start*."""
    base = _indent(lines[start - 1])
    end = start
    for number in range(start + 1, min(len(lines), start + MAX_BLOCK_LINES) + 1):
        line = lines[number - 1]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) <= base:
            break
        end = number
    return end


_CONTINUES_AFTER = (",", "(", "=", ":", "->", "=>", "extends", "with", "implements", "&", "|")
_CONTINUES_BEFORE = (
    "{",
    "extends",
    "with",
    "implements",
    ":",
    ".",
    "=",
    "where",
    "throws",
    ")",
    "->",
    "=>",
    "<",
    "&",
    "|",
)


def _header_continues(text: str, newline: int) -> bool:
    """Whether a definition's header runs on past the line ending at *newline*."""
    line_start = text.rfind("\n", 0, newline) + 1
    current = text[line_start:newline].split("//", 1)[0].rstrip()
    if current.endswith(_CONTINUES_AFTER):
        return True
    rest = text[newline + 1 : newline + 400]
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.startswith(_CONTINUES_BEFORE)
    return False


def _brace_end(text: str, offset: int, pairs: dict[int, int], positions: _Lines) -> int | None:
    """The line of the ``}`` closing the body that starts after *offset*, if any.

    The body opens at the first ``{`` outside parentheses before a statement
    ends. ``;`` or ``=`` followed by a non-brace (an expression body) ends the
    search -- the caller then falls back to indentation.
    """
    depth = 0
    limit = min(len(text), offset + 4000)
    i = offset
    while i < limit:
        char = text[i]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            if char == "{":
                close = pairs.get(i)
                return positions.line(close) if close is not None else None
            if char == ";":
                return None
            if char == "\n" and not _header_continues(text, i):
                return None
            if char == "=" and text[i + 1 : i + 2] not in ("=", ">"):
                rest = text[i + 1 : i + 200].lstrip()
                if not rest.startswith("{"):
                    return None
        i += 1
    return None


def scan(path: str, text: str) -> FileSymbols | None:
    """The definitions in one file, with their line ranges."""
    language = language_of(path)
    if language is None or len(text) > MAX_FILE_BYTES:
        return None
    lines = text.splitlines()
    positions = _Lines(text)
    pairs = _brace_pairs(text, language) if language != "python" else {}
    found: list[Symbol] = []
    for pattern, fixed_kind in _PATTERNS[language]:
        for match in pattern.finditer(text):
            name = match.group("name").strip("`")
            kind = fixed_kind or match.group("kind")
            start = positions.line(match.start("name"))
            if language == "python":
                end = _indented_end(lines, start)
            else:
                end = _brace_end(text, match.end(), pairs, positions)
                if end is None:
                    end = _indented_end(lines, start)
            found.append(
                Symbol(name=name, kind=kind, path=path, start_line=start, end_line=max(start, end))
            )
    # De-duplicate (two patterns can match one definition) and attach containers.
    unique: dict[tuple[str, int], Symbol] = {}
    for symbol in sorted(found, key=lambda s: (s.start_line, -s.end_line)):
        unique.setdefault((symbol.name, symbol.start_line), symbol)
    ordered = list(unique.values())
    nested: list[Symbol] = []
    stack: list[Symbol] = []
    for symbol in ordered:
        while stack and stack[-1].end_line < symbol.start_line:
            stack.pop()
        container = stack[-1].name if stack else None
        nested.append(
            Symbol(symbol.name, symbol.kind, path, symbol.start_line, symbol.end_line, container)
        )
        if symbol.end_line > symbol.start_line:
            stack.append(symbol)
    return FileSymbols(path=path, language=language, symbols=nested)


#: Definitions that name a type: what an architecture is made of.
TYPE_KINDS = frozenset(
    {"class", "interface", "enum", "record", "object", "trait", "struct", "@interface"}
)


@dataclass
class SymbolIndex:
    """Every definition found, by file and by name."""

    files: dict[str, FileSymbols] = field(default_factory=dict)
    by_name: dict[str, list[Symbol]] = field(default_factory=dict)
    scanned_bytes: int = 0
    truncated: bool = False

    def add(self, path: str, text: str) -> None:
        result = scan(path, text)
        if result is None:
            return
        self.files[path] = result
        self.scanned_bytes += len(text)
        for symbol in result.symbols:
            self.by_name.setdefault(symbol.name, []).append(symbol)

    def in_file(self, path: str, name: str) -> list[Symbol]:
        found = self.files.get(path)
        return [s for s in found.symbols if s.name == name] if found else []

    def types(self) -> Iterator[Symbol]:
        for result in self.files.values():
            for symbol in result.symbols:
                if symbol.kind in TYPE_KINDS:
                    yield symbol

    @classmethod
    def build(
        cls,
        sources: Iterable[tuple[str, str]],
        *,
        max_files: int = 8000,
        max_bytes: int = 60_000_000,
    ) -> SymbolIndex:
        index = cls()
        for count, (path, text) in enumerate(sources):
            if count >= max_files or index.scanned_bytes + len(text) > max_bytes:
                index.truncated = True
                break
            index.add(path, text)
        return index
