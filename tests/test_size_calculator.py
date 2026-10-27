"""Tests for directory size calculation."""
from __future__ import annotations

import os

import pytest

from pro_space_optimizer.size_calculator import calculate_size
from pro_space_optimizer.models import SizeState


def test_calculates_directory_size(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a.bin").write_bytes(b"a" * 1000)
    (d / "b.bin").write_bytes(b"b" * 2000)
    sub = d / "sub"
    sub.mkdir()
    (sub / "c.bin").write_bytes(b"c" * 500)

    result = calculate_size(str(d))
    assert result.size == 3500
    assert result.state == SizeState.DONE
    assert result.files == 3


def test_empty_directory(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    result = calculate_size(str(d))
    assert result.size == 0
    assert result.state == SizeState.DONE


def test_missing_directory(tmp_path):
    result = calculate_size(str(tmp_path / "nope"))
    assert result.size is None
    assert result.state == SizeState.UNKNOWN


def test_handles_disappearing_files(tmp_path, monkeypatch):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "keep.bin").write_bytes(b"k" * 100)
    (d / "vanish.bin").write_bytes(b"v" * 100)

    real_scandir = os.scandir
    removed = {"done": False}

    class VanishingEntry:
        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, item):
            return getattr(self._entry, item)

        def is_file(self, follow_symlinks=True):
            if self._entry.name == "vanish.bin" and not removed["done"]:
                removed["done"] = True
                os.remove(self._entry.path)
            return self._entry.is_file(follow_symlinks=follow_symlinks)

    class FakeScandir:
        def __init__(self, entries):
            self._entries = entries

        def __enter__(self):
            return iter(self._entries)

        def __exit__(self, *exc):
            return False

    def fake_scandir(path):
        return FakeScandir([VanishingEntry(e) for e in real_scandir(path)])

    monkeypatch.setattr(os, "scandir", fake_scandir)
    result = calculate_size(str(d))
    # The file that vanished is reported but the rest still counts.
    assert result.size == 100
    assert result.state == SizeState.PARTIAL
    assert result.errors


def test_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 100000)

    d = tmp_path / "dir"
    d.mkdir()
    (d / "small.bin").write_bytes(b"s" * 10)
    try:
        os.symlink(outside, d / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")

    result = calculate_size(str(d))
    assert result.size == 10


def test_cancellation_marks_partial(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a.bin").write_bytes(b"a" * 100)

    result = calculate_size(str(d), should_stop=lambda: True)
    assert result.state == SizeState.PARTIAL


def test_never_raises_on_permission_error(tmp_path, monkeypatch):
    d = tmp_path / "dir"
    d.mkdir()

    def fake_scandir(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "scandir", fake_scandir)
    result = calculate_size(str(d))
    assert result.state == SizeState.PARTIAL
    assert result.errors
