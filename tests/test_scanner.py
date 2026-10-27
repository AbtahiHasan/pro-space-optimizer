"""Tests for the recursive scanner with pruning of matched directories."""
from __future__ import annotations

import os

import pytest

from conftest import make_tree
from pro_space_optimizer.scanner import Scanner, scan_parents


def names_of(report):
    return sorted(os.path.basename(m.path) for m in report.matches)


def paths_of(report):
    return sorted(m.path for m in report.matches)


class TestScanning:
    def test_finds_direct_children(self, sample_tree):
        report = scan_parents([str(sample_tree)], ["node_modules", "vendor"])
        found = paths_of(report)
        # Direct children are found...
        assert str(sample_tree / "node_modules") in found
        assert str(sample_tree / "vendor") in found
        # ...and the nested ones under project-a / project-b are also found
        # now that scanning is recursive.
        assert str(sample_tree / "project-a" / "node_modules") in found
        assert str(sample_tree / "project-b" / "vendor") in found

    def test_prunes_nested_matches_inside_a_match(self, tmp_path):
        """The core requirement: never recurse into a matched directory."""
        make_tree(tmp_path, {
            "app1/node_modules/node_modules": [],
            "app2/src/node_modules": [],
            "node_modules/node_modules": [],
        })
        report = scan_parents([str(tmp_path)], ["node_modules"])
        found = paths_of(report)
        assert found == sorted([
            str(tmp_path / "app1" / "node_modules"),
            str(tmp_path / "app2" / "src" / "node_modules"),
            str(tmp_path / "node_modules"),
        ])
        # The inner node_modules inside a match must NOT appear.
        assert str(tmp_path / "app1" / "node_modules" / "node_modules") not in found
        assert str(tmp_path / "node_modules" / "node_modules") not in found

    def test_matches_at_multiple_depths(self, tmp_path):
        make_tree(tmp_path, {
            "a/b/c/vendor": [],
            "a/vendor": [],
            "vendor": [],
        })
        report = scan_parents([str(tmp_path)], ["vendor"])
        found = paths_of(report)
        # a/vendor is matched before a/b/c is reached? No: a is descended, its
        # child vendor matches (pruned), and a/b/c/vendor is under a/b, which is
        # outside the matched a/vendor subtree, so it is still found.
        assert str(tmp_path / "vendor") in found
        assert str(tmp_path / "a" / "vendor") in found
        assert str(tmp_path / "a" / "b" / "c" / "vendor") in found

    def test_vendor_does_not_prune_other_name(self, tmp_path):
        make_tree(tmp_path, {
            "app/node_modules": [],
            "app/node_modules/vendor": [],
        })
        report = scan_parents([str(tmp_path)], ["node_modules", "vendor"])
        found = paths_of(report)
        assert str(tmp_path / "app" / "node_modules") in found
        # vendor is inside the pruned node_modules subtree -> not reported.
        assert str(tmp_path / "app" / "node_modules" / "vendor") not in found

    def test_multiple_names(self, tmp_path):
        make_tree(tmp_path, {
            "node_modules": [],
            "vendor": [],
            ".venv": [],
            "__pycache__": [],
        })
        report = scan_parents(
            [str(tmp_path)],
            ["node_modules", "vendor", ".venv", "__pycache__"],
        )
        assert names_of(report) == [
            ".venv", "__pycache__", "node_modules", "vendor"
        ]

    def test_multiple_names_nested(self, tmp_path):
        make_tree(tmp_path, {
            "app/node_modules": [],
            "app/src/vendor": [],
            "app/src/.venv": [],
        })
        report = scan_parents(
            [str(tmp_path)], ["node_modules", "vendor", ".venv"]
        )
        found = paths_of(report)
        assert found == sorted([
            str(tmp_path / "app" / "node_modules"),
            str(tmp_path / "app" / "src" / "vendor"),
            str(tmp_path / "app" / "src" / ".venv"),
        ])

    def test_case_sensitive(self, tmp_path):
        make_tree(tmp_path, {"Node_Modules": [], "node_modules": []})
        report = scan_parents([str(tmp_path)], ["node_modules"])
        assert names_of(report) == ["node_modules"]

    def test_ignores_regular_files(self, tmp_path):
        make_tree(tmp_path, {"node_modules": ["a"]})
        # A *file* with a matching name must not be returned.
        with open(tmp_path / "vendor", "w", encoding="utf-8") as fh:
            fh.write("not a dir")
        report = scan_parents([str(tmp_path)], ["vendor"])
        assert report.matches == []

    def test_ignores_files_named_like_match_in_subdirs(self, tmp_path):
        make_tree(tmp_path, {"app/src": []})
        # A matching-named *file* deep in the tree must not be a result.
        with open(tmp_path / "app" / "src" / "build", "w",
                  encoding="utf-8") as fh:
            fh.write("file")
        report = scan_parents([str(tmp_path)], ["build"])
        assert report.matches == []

    def test_missing_parent_reports_error(self, tmp_path):
        missing = str(tmp_path / "does-not-exist")
        report = scan_parents([missing], ["node_modules"])
        assert report.matches == []
        assert report.parents[0].error == "Folder does not exist"

    def test_parent_is_file_reports_error(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        report = scan_parents([str(f)], ["node_modules"])
        assert report.parents[0].error == "Not a directory"

    def test_ignores_symlinked_directories(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        parent = tmp_path / "parent"
        parent.mkdir()
        link = parent / "node_modules"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported in this environment")

        report = scan_parents([str(parent)], ["node_modules"])
        assert report.matches == []
        assert report.skipped_symlinks == 1

    def test_does_not_follow_symlink_into_tree(self, tmp_path):
        outside = tmp_path / "outside"
        make_tree(outside, {"node_modules": []})
        parent = tmp_path / "parent"
        make_tree(parent, {"app": []})
        try:
            os.symlink(outside, parent / "link")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported")

        # The match lives outside, reached only through a symlink -> not found.
        report = scan_parents([str(parent)], ["node_modules"])
        assert report.matches == []

    def test_multiple_parents(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        make_tree(a, {"node_modules": [], "x/y/node_modules": []})
        make_tree(b, {"node_modules": []})
        report = scan_parents([str(a), str(b)], ["node_modules"])
        assert len(report.matches) == 3

    def test_deep_tree_no_stack_overflow(self, tmp_path):
        # Build a chain deeper than Python's default recursion limit (~1000)
        # but within the filesystem's path-length limit.  Single-character
        # names keep the path short; an iterative walk must not overflow.
        deep = tmp_path
        for i in range(700):
            deep = deep / "d"
        deep.mkdir(parents=True)
        (deep / "node_modules").mkdir()
        report = scan_parents([str(tmp_path)], ["node_modules"])
        assert len(report.matches) == 1

    def test_cancellation_stops_scan(self, sample_tree):
        calls = {"n": 0}

        def should_stop():
            calls["n"] += 1
            return calls["n"] > 3

        report = Scanner().scan(
            [str(sample_tree)], ["node_modules"], should_stop=should_stop
        )
        assert report.cancelled

    def test_cancellation_mid_walk_keeps_results(self, tmp_path):
        make_tree(tmp_path, {
            "a/node_modules": [],
            "b/node_modules": [],
            "c/node_modules": [],
        })
        state = {"n": 0}

        def should_stop():
            state["n"] += 1
            return state["n"] > 6

        report = Scanner().scan(
            [str(tmp_path)], ["node_modules"], should_stop=should_stop
        )
        assert report.cancelled

    def test_no_names_means_no_matches(self, sample_tree):
        report = scan_parents([str(sample_tree)], [])
        assert report.matches == []

    def test_match_result_carries_identity(self, tmp_path):
        make_tree(tmp_path, {"app/node_modules": []})
        report = scan_parents([str(tmp_path)], ["node_modules"])
        match = report.matches[0]
        assert match.name == "node_modules"
        assert match.parent == str(tmp_path)
        assert match.st_dev is not None
        assert match.st_ino is not None
