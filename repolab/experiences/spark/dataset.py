"""The deterministic synthetic dataset.

Generated from a fixed seed with the standard library only -- no pandas, no
pyarrow -- so the same seed always produces byte-identical output. The sha256 of
those bytes *is* the dataset hash, computed before anything executes, which is
what lets a later experiment claim two runs saw the same input.

The columns are the ones the repository's own ``transform_data`` needs (``id``,
``first_name``, ``second_name``, ``floor``) plus a ``dept`` column carrying one
**deliberately skewed** key, so a shuffle on ``dept`` produces visibly uneven
partitions rather than a tidy distribution that hides the problem.
"""

from __future__ import annotations

import csv
import hashlib
import io
import random

from pydantic import BaseModel

#: Fixed so the dataset is reproducible. Changing it changes the dataset hash.
DEFAULT_SEED = 20260930

DEFAULT_ROWS = 200_000

#: The key given a disproportionate share of rows.
SKEWED_KEY = "engineering"

#: How much of the dataset lands on the skewed key.
DEFAULT_SKEW_FRACTION = 0.72

#: The other keys, splitting what is left.
OTHER_KEYS: tuple[str, ...] = (
    "finance",
    "marketing",
    "operations",
    "research",
    "support",
    "legal",
)

FIRST_NAMES: tuple[str, ...] = (
    "ada",
    "grace",
    "alan",
    "edsger",
    "barbara",
    "ken",
    "linus",
    "margaret",
    "donald",
    "john",
    "tony",
    "leslie",
)
SECOND_NAMES: tuple[str, ...] = (
    "lovelace",
    "hopper",
    "turing",
    "dijkstra",
    "liskov",
    "thompson",
    "torvalds",
    "hamilton",
    "knuth",
    "mccarthy",
    "hoare",
    "lamport",
)

COLUMNS: tuple[str, ...] = ("id", "first_name", "second_name", "floor", "dept")


class DatasetSpec(BaseModel):
    """What was generated, and how to verify it."""

    rows: int
    seed: int
    columns: list[str]

    skewed_key: str
    skew_fraction: float
    skewed_rows: int

    sha256: str
    """Hash of the exact bytes written. This is the dataset hash."""

    size_bytes: int
    filename: str = "dataset.csv"

    @property
    def short_hash(self) -> str:
        return self.sha256[:16]

    @property
    def distinct_keys(self) -> int:
        return 1 + len(OTHER_KEYS)

    def describe(self) -> str:
        return (
            f"{self.rows:,} rows, {self.distinct_keys} keys, "
            f"{self.skew_fraction:.0%} on '{self.skewed_key}' "
            f"({self.skewed_rows:,} rows), sha256 {self.short_hash}"
        )


def generate_dataset(
    *,
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    skew_fraction: float = DEFAULT_SKEW_FRACTION,
) -> tuple[bytes, DatasetSpec]:
    """Build the dataset and return its bytes alongside its specification.

    Deterministic in every respect: a seeded ``random.Random``, a fixed column
    order, and ``\\n`` line endings regardless of platform.
    """
    if not 0.0 < skew_fraction < 1.0:
        raise ValueError(f"skew_fraction must be between 0 and 1, got {skew_fraction}")

    rng = random.Random(seed)
    buffer = io.StringIO(newline="")
    # Explicit \n so the bytes -- and therefore the hash -- do not depend on the
    # platform's line ending.
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)

    skewed_rows = 0
    for row_id in range(1, rows + 1):
        if rng.random() < skew_fraction:
            dept = SKEWED_KEY
            skewed_rows += 1
        else:
            dept = OTHER_KEYS[rng.randrange(len(OTHER_KEYS))]

        writer.writerow(
            [
                row_id,
                FIRST_NAMES[rng.randrange(len(FIRST_NAMES))],
                SECOND_NAMES[rng.randrange(len(SECOND_NAMES))],
                rng.randint(1, 12),
                dept,
            ]
        )

    data = buffer.getvalue().encode("utf-8")
    return data, DatasetSpec(
        rows=rows,
        seed=seed,
        columns=list(COLUMNS),
        skewed_key=SKEWED_KEY,
        skew_fraction=skew_fraction,
        skewed_rows=skewed_rows,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
