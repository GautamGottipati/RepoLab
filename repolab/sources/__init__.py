"""Source code as evidence: symbols, verified references, commit-pinned copies.

symbols    definitions and the lines they span, found deterministically
resolve    claim -> lines RepoLab computed and re-read itself
store      copies of cited files, per repository and commit
"""

from repolab.sources.resolve import Snapshot, parse_citation, strength_of, verify_reference
from repolab.sources.store import CloneSnapshot, MemorySnapshot, StoredSnapshot, capture, read
from repolab.sources.symbols import Symbol, SymbolIndex, scan

__all__ = [
    "CloneSnapshot",
    "MemorySnapshot",
    "Snapshot",
    "StoredSnapshot",
    "Symbol",
    "SymbolIndex",
    "capture",
    "parse_citation",
    "read",
    "scan",
    "strength_of",
    "verify_reference",
]
