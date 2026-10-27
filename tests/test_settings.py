"""Tests for persistent settings."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from pro_space_optimizer.settings import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    """A Settings backed by a temporary INI file."""
    path = os.path.join(str(tmp_path), "test.ini")
    qs = QSettings(path, QSettings.IniFormat)
    return Settings(qs)


def test_parent_folders_round_trip(settings):
    folders = ["/home/user/projects", "/home/user/work"]
    settings.set_parent_folders(folders)
    assert settings.parent_folders() == folders


def test_add_parent_prevents_duplicates(settings):
    assert settings.add_parent_folder("/a") is True
    assert settings.add_parent_folder("/a") is False
    assert settings.add_parent_folder("/b") is True
    assert settings.parent_folders() == ["/a", "/b"]


def test_remove_parent(settings):
    settings.set_parent_folders(["/a", "/b"])
    assert settings.remove_parent_folder("/a") is True
    assert settings.remove_parent_folder("/missing") is False
    assert settings.parent_folders() == ["/b"]


def test_clear_parents(settings):
    settings.set_parent_folders(["/a", "/b"])
    settings.clear_parent_folders()
    assert settings.parent_folders() == []


def test_folder_names_text(settings):
    settings.set_folder_names_text("node_modules, vendor")
    assert settings.folder_names_text() == "node_modules, vendor"


def test_default_folder_names(settings):
    # A fresh INI has no value stored -> default is returned.
    assert "node_modules" in settings.folder_names_text()


def test_delete_mode_default_and_invalid(settings):
    assert settings.delete_mode() == "trash"
    settings.set_delete_mode("permanent")
    assert settings.delete_mode() == "permanent"
    # Invalid values are coerced back to the safe default.
    settings.set_delete_mode("bogus")
    assert settings.delete_mode() == "trash"


def test_theme_round_trip(settings):
    for theme in ("light", "dark", "system"):
        settings.set_theme(theme)
        assert settings.theme() == theme
    settings.set_theme("nonsense")
    assert settings.theme() == "system"


def test_calculate_sizes_toggle(settings):
    assert settings.calculate_sizes() is True
    settings.set_calculate_sizes(False)
    assert settings.calculate_sizes() is False


def test_persistence_across_instances(tmp_path):
    path = os.path.join(str(tmp_path), "persist.ini")
    s1 = Settings(QSettings(path, QSettings.IniFormat))
    s1.set_parent_folders(["/x", "/y"])
    s1.set_delete_mode("permanent")
    s1.set_folder_names_text("vendor")

    s2 = Settings(QSettings(path, QSettings.IniFormat))
    assert s2.parent_folders() == ["/x", "/y"]
    assert s2.delete_mode() == "permanent"
    assert s2.folder_names_text() == "vendor"


def test_single_parent_not_collapsed(settings):
    settings.set_parent_folders(["/only"])
    assert settings.parent_folders() == ["/only"]
