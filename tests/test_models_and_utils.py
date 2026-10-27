"""Regression tests for the "clear results never deletes" guarantee."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pro_space_optimizer.models import (  # noqa: E402
    ResultTableModel,
    ScanResult,
)
from pro_space_optimizer.scanner import scan_parents  # noqa: E402
from pro_space_optimizer.utils import (  # noqa: E402
    human_size,
    is_within,
    is_direct_child,
    parse_folder_names,
    is_valid_folder_name,
    validate_folder_names,
)


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestClearResults:
    def test_clear_does_not_touch_disk(self, qapp, tmp_path):
        parent = tmp_path / "projects"
        match = parent / "node_modules"
        match.mkdir(parents=True)
        (match / "pkg.js").write_text("hello")

        report = scan_parents([str(parent)], ["node_modules"])
        assert len(report.matches) == 1

        model = ResultTableModel(report.matches)
        assert model.rowCount() == 1

        # Clearing the table must NOT delete anything.
        model.clear()
        assert model.rowCount() == 0
        assert match.exists()
        assert (match / "pkg.js").exists()

    def test_selection_toggle(self, qapp):
        results = [
            ScanResult(name="a", parent="/p", path="/p/a"),
            ScanResult(name="b", parent="/p", path="/p/b"),
        ]
        model = ResultTableModel(results)
        assert len(model.selected_results()) == 2
        model.select_all(False)
        assert len(model.selected_results()) == 0
        model.select_all(True)
        assert len(model.selected_results()) == 2

    def test_remove_paths_only_removes_rows(self, qapp, tmp_path):
        parent = tmp_path / "projects"
        match = parent / "node_modules"
        match.mkdir(parents=True)
        results = [ScanResult(name="node_modules", parent=str(parent),
                              path=str(match))]
        model = ResultTableModel(results)
        model.remove_paths([str(match)])
        assert model.rowCount() == 0
        assert match.exists()


class TestUtils:
    def test_human_size(self):
        assert human_size(0) == "0 B"
        assert human_size(512) == "512 B"
        assert human_size(1024) == "1.0 KB"
        assert human_size(1024 * 1024) == "1.0 MB"
        assert human_size(1024 ** 3) == "1.0 GB"
        assert human_size(1024 ** 4) == "1.0 TB"
        assert human_size(None) == "Unknown"

    def test_parse_folder_names(self):
        text = "node_modules, vendor\n.venv;__pycache__\n\n node_modules "
        assert parse_folder_names(text) == [
            "node_modules", "vendor", ".venv", "__pycache__"
        ]

    def test_parse_strips_and_dedupes(self):
        assert parse_folder_names("  a , a ,b,  ,b ") == ["a", "b"]

    def test_invalid_names_rejected(self):
        assert is_valid_folder_name("node_modules")[0]
        assert not is_valid_folder_name("a/b")[0]
        assert not is_valid_folder_name("..")[0]
        assert not is_valid_folder_name("")[0]
        assert not is_valid_folder_name("a\x00b")[0]

    def test_validate_folder_names_filters(self):
        assert validate_folder_names(["ok", "bad/path", "", "ok2"]) == ["ok", "ok2"]

    def test_is_direct_child(self):
        assert is_direct_child("/a/b", "/a")
        assert not is_direct_child("/a/b/c", "/a")
        assert not is_direct_child("/a", "/a")

    def test_is_within(self):
        assert is_within("/a/b/c", ["/a"])
        assert is_within("/a/b/c", ["/other", "/a"])
        assert not is_within("/a", ["/a"])
        assert not is_within("/x/y", ["/a"])
