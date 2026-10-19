"""The deterministic synthetic dataset.

Reproducibility rests on this: if the same seed did not produce byte-identical
output, two runs could not be compared and the dataset hash would mean nothing.
"""

from __future__ import annotations

import csv
import io

import pytest

from repolab.experiences.spark.dataset import (
    COLUMNS,
    DEFAULT_SEED,
    OTHER_KEYS,
    SKEWED_KEY,
    generate_dataset,
)


class TestDeterminism:
    def test_the_same_seed_produces_identical_bytes(self) -> None:
        first, _ = generate_dataset(rows=2000)
        second, _ = generate_dataset(rows=2000)
        assert first == second

    def test_the_same_seed_produces_the_same_hash(self) -> None:
        _, first = generate_dataset(rows=2000)
        _, second = generate_dataset(rows=2000)
        assert first.sha256 == second.sha256

    def test_a_different_seed_produces_a_different_dataset(self) -> None:
        _, default = generate_dataset(rows=2000)
        _, other = generate_dataset(rows=2000, seed=DEFAULT_SEED + 1)
        assert other.sha256 != default.sha256

    def test_a_different_row_count_produces_a_different_hash(self) -> None:
        _, small = generate_dataset(rows=100)
        _, large = generate_dataset(rows=200)
        assert small.sha256 != large.sha256

    def test_line_endings_are_fixed_so_the_hash_is_portable(self) -> None:
        """A platform-dependent newline would change the hash between machines."""
        data, _ = generate_dataset(rows=20)
        assert b"\r\n" not in data

    def test_the_hash_matches_the_bytes(self) -> None:
        import hashlib

        data, spec = generate_dataset(rows=500)
        assert spec.sha256 == hashlib.sha256(data).hexdigest()
        assert spec.size_bytes == len(data)


class TestShape:
    def test_the_header_is_the_declared_columns(self) -> None:
        data, spec = generate_dataset(rows=10)
        header = data.decode().splitlines()[0].split(",")

        assert header == list(COLUMNS)
        assert spec.columns == list(COLUMNS)

    def test_it_has_the_columns_the_repository_transform_needs(self) -> None:
        """transform_data selects id, first_name, second_name and floor."""
        _, spec = generate_dataset(rows=10)
        assert {"id", "first_name", "second_name", "floor"} <= set(spec.columns)

    def test_the_row_count_is_exact(self) -> None:
        data, spec = generate_dataset(rows=1234)
        rows = list(csv.DictReader(io.StringIO(data.decode())))

        assert len(rows) == 1234
        assert spec.rows == 1234

    def test_ids_are_sequential(self) -> None:
        data, _ = generate_dataset(rows=100)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        assert [int(row["id"]) for row in rows] == list(range(1, 101))

    def test_floors_are_within_range(self) -> None:
        data, _ = generate_dataset(rows=500)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        assert all(1 <= int(row["floor"]) <= 12 for row in rows)


class TestTheDeliberateSkew:
    def test_one_key_holds_most_of_the_rows(self) -> None:
        data, _ = generate_dataset(rows=10_000)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["dept"]] = counts.get(row["dept"], 0) + 1

        assert counts[SKEWED_KEY] > sum(count for key, count in counts.items() if key != SKEWED_KEY)

    def test_the_skew_matches_the_requested_fraction(self) -> None:
        data, spec = generate_dataset(rows=10_000)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        actual = sum(1 for row in rows if row["dept"] == SKEWED_KEY) / len(rows)

        assert actual == pytest.approx(spec.skew_fraction, abs=0.02)
        assert spec.skewed_rows == sum(1 for row in rows if row["dept"] == SKEWED_KEY)

    def test_the_skew_fraction_is_configurable(self) -> None:
        _, light = generate_dataset(rows=5000, skew_fraction=0.2)
        _, heavy = generate_dataset(rows=5000, skew_fraction=0.9)
        assert light.skewed_rows < heavy.skewed_rows

    @pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
    def test_an_impossible_skew_fraction_is_refused(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="skew_fraction"):
            generate_dataset(rows=10, skew_fraction=fraction)

    def test_the_other_keys_all_appear(self) -> None:
        """Enough distinct keys that 200 partitions is clearly too many."""
        data, _ = generate_dataset(rows=20_000)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        present = {row["dept"] for row in rows}

        assert SKEWED_KEY in present
        assert set(OTHER_KEYS) <= present

    def test_the_key_count_is_far_below_the_partition_count(self) -> None:
        """Why most of the 200 shuffle partitions end up empty."""
        _, spec = generate_dataset(rows=1000)
        assert spec.distinct_keys < 20


class TestDescription:
    def test_it_summarises_itself(self) -> None:
        _, spec = generate_dataset(rows=1000)
        described = spec.describe()

        assert "1,000 rows" in described
        assert SKEWED_KEY in described
        assert spec.short_hash in described
