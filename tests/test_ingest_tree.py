"""File tree walking, limits and language detection."""

from __future__ import annotations

from pathlib import Path

from repolab.ingest import IngestLimits, build_file_tree
from repolab.ingest.languages import detect_languages
from repolab.ingest.limits import LimitReport


def write(root: Path, paths: dict[str, str]) -> None:
    for relative, content in paths.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class TestSkippedDirectories:
    def test_dependency_and_build_directories_are_skipped(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            {
                "src/app.py": "x = 1\n",
                "node_modules/left-pad/index.js": "module.exports = 1\n",
                ".venv/lib/python3.11/site-packages/foo.py": "y = 2\n",
                "target/classes/Main.class": "binary-ish\n",
                "build/out.txt": "out\n",
                "__pycache__/app.cpython-311.pyc": "cache\n",
            },
        )
        tree = build_file_tree(tmp_path)
        paths = {entry.path for entry in tree.files}

        assert paths == {"src/app.py"}
        assert any("node_modules" in d for d in tree.skipped_directories)

    def test_the_git_directory_is_skipped(self, make_repo) -> None:
        root, _ = make_repo({"README.md": "# demo\n"})
        tree = build_file_tree(root)
        assert all(not entry.path.startswith(".git/") for entry in tree.files)

    def test_egg_info_directories_are_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, {"pkg.egg-info/PKG-INFO": "Name: pkg\n", "pkg/__init__.py": ""})
        tree = build_file_tree(tmp_path)
        assert {e.path for e in tree.files} == {"pkg/__init__.py"}


class TestLimits:
    def test_the_file_count_limit_truncates_and_is_reported(self, tmp_path: Path) -> None:
        write(tmp_path, {f"f{i}.py": "x = 1\n" for i in range(20)})
        report = LimitReport()
        tree = build_file_tree(tmp_path, limits=IngestLimits(max_files=5), report=report)

        assert tree.file_count == 5
        assert tree.truncated is True
        assert "max_files" in report.hit

    def test_the_total_size_limit_truncates(self, tmp_path: Path) -> None:
        write(tmp_path, {f"f{i}.py": "x" * 1000 for i in range(10)})
        report = LimitReport()
        tree = build_file_tree(tmp_path, limits=IngestLimits(max_tree_bytes=2500), report=report)

        assert tree.truncated is True
        assert "max_tree_bytes" in report.hit

    def test_the_depth_limit_stops_descending(self, tmp_path: Path) -> None:
        write(tmp_path, {"a/b/c/d/e/deep.py": "x = 1\n", "top.py": "y = 2\n"})
        report = LimitReport()
        tree = build_file_tree(tmp_path, limits=IngestLimits(max_depth=2), report=report)

        assert "top.py" in {e.path for e in tree.files}
        assert "a/b/c/d/e/deep.py" not in {e.path for e in tree.files}
        assert "max_depth" in report.hit

    def test_nothing_is_reported_when_no_limit_bites(self, tmp_path: Path) -> None:
        write(tmp_path, {"a.py": "x = 1\n"})
        report = LimitReport()
        build_file_tree(tmp_path, report=report)
        assert report.any_hit is False


class TestSymlinks:
    def test_symlinks_are_not_followed(self, tmp_path: Path) -> None:
        """A link could point outside the clone entirely."""
        write(tmp_path, {"real.py": "x = 1\n"})
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret\n")
        (tmp_path / "link.txt").symlink_to(outside)

        tree = build_file_tree(tmp_path)
        assert {e.path for e in tree.files} == {"real.py"}


class TestTreeQueries:
    def test_lookup_helpers(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            {
                "Dockerfile": "FROM x\n",
                "jobs/etl.py": "x = 1\n",
                "requirements.txt": "pyspark\n",
            },
        )
        tree = build_file_tree(tmp_path)

        assert [e.path for e in tree.by_name("dockerfile")] == ["Dockerfile"]
        assert [e.path for e in tree.by_extension("py")] == ["jobs/etl.py"]
        assert [e.path for e in tree.name_startswith("requirements")] == ["requirements.txt"]

    def test_entry_metadata(self, tmp_path: Path) -> None:
        write(tmp_path, {"a/b/c.py": "x = 1\n"})
        entry = build_file_tree(tmp_path).files[0]
        assert (entry.name, entry.extension, entry.depth) == ("c.py", ".py", 2)


class TestLanguageDetection:
    def test_languages_are_ranked_by_size(self, tmp_path: Path) -> None:
        write(tmp_path, {"big.py": "x" * 5000, "small.js": "y" * 100})
        languages = detect_languages(build_file_tree(tmp_path))
        assert [stat.language for stat in languages] == ["Python", "JavaScript"]

    def test_incidental_files_do_not_become_languages(self, tmp_path: Path) -> None:
        """One stray script in a huge Python repo is not 'a Ruby project'."""
        write(tmp_path, {"main.py": "x" * 100_000, "tiny.rb": "puts 1\n"})
        assert [s.language for s in detect_languages(build_file_tree(tmp_path))] == ["Python"]

    def test_documentation_is_not_a_language(self, tmp_path: Path) -> None:
        write(tmp_path, {"README.md": "# docs\n", "app.py": "x = 1\n"})
        assert [s.language for s in detect_languages(build_file_tree(tmp_path))] == ["Python"]

    def test_dockerfile_and_makefile_are_recognised(self, tmp_path: Path) -> None:
        write(tmp_path, {"Dockerfile": "FROM x\n" * 50, "Makefile": "all:\n" * 50})
        languages = {s.language for s in detect_languages(build_file_tree(tmp_path))}
        assert languages == {"Dockerfile", "Makefile"}

    def test_an_empty_repository_has_no_languages(self, tmp_path: Path) -> None:
        assert detect_languages(build_file_tree(tmp_path)) == []
