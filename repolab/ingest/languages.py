"""Language detection from file extensions and well-known filenames.

Deterministic and cheap: languages are ranked by the number of bytes attributed
to them, so a repository with one stray script is not reported as that language.
"""

from __future__ import annotations

from pydantic import BaseModel

from repolab.ingest.tree import FileTree

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".java": "Java",
    ".scala": "Scala",
    ".sc": "Scala",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".sql": "SQL",
    ".r": "R",
    ".ipynb": "Jupyter Notebook",
    ".hs": "Haskell",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".swift": "Swift",
}

FILENAME_LANGUAGES: dict[str, str] = {
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
}

#: Extensions that are configuration or documentation rather than a language.
NON_LANGUAGE_EXTENSIONS = frozenset(
    {".md", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml"}
)


class LanguageStat(BaseModel):
    """How much of the repository one language accounts for."""

    language: str
    bytes: int
    files: int


def detect_languages(tree: FileTree, *, min_share: float = 0.01) -> list[LanguageStat]:
    """Rank languages in *tree* by attributed bytes, largest first.

    Languages below *min_share* of the classified total are dropped unless they
    are the only ones found, so incidental files do not become "languages".
    """
    totals: dict[str, LanguageStat] = {}

    for entry in tree.files:
        language = EXTENSION_LANGUAGES.get(entry.extension)
        if language is None:
            language = FILENAME_LANGUAGES.get(entry.name.lower())
        if language is None and entry.name.lower().startswith("dockerfile"):
            language = "Dockerfile"
        if language is None:
            continue

        stat = totals.get(language)
        if stat is None:
            totals[language] = LanguageStat(language=language, bytes=entry.size, files=1)
        else:
            stat.bytes += entry.size
            stat.files += 1

    ranked = sorted(totals.values(), key=lambda s: (s.bytes, s.files), reverse=True)
    if not ranked:
        return []

    classified = sum(s.bytes for s in ranked) or 1
    significant = [s for s in ranked if s.bytes / classified >= min_share]
    return significant or ranked[:1]
