"""Tests for trash manager and privileged helper validation."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from pro_space_optimizer import trash_manager
from pro_space_optimizer.trash_manager import TrashResult, move_to_trash, move_many_to_trash
from pro_space_optimizer import privileged_helper


class TestTrashManager:
    def test_reports_missing_path(self, tmp_path):
        result = move_to_trash(str(tmp_path / "nope"))
        assert result.ok is False
        assert "no longer exists" in (result.error or "")

    def test_uses_gio_without_shell(self, monkeypatch, tmp_path):
        target = tmp_path / "victim"
        target.mkdir()
        captured = {}

        class FakeProc:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()

        monkeypatch.setattr(trash_manager.shutil, "which", lambda name: "/usr/bin/gio")
        monkeypatch.setattr(trash_manager.subprocess, "run", fake_run)

        result = move_to_trash(str(target))
        assert result.ok
        assert captured["argv"][0] == "/usr/bin/gio"
        assert captured["argv"][1:3] == ["trash", "--"]
        assert captured["argv"][3] == str(target)
        assert captured["kwargs"].get("shell") is False

    def test_failure_is_reported(self, monkeypatch, tmp_path):
        target = tmp_path / "victim"
        target.mkdir()

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "gio: permission denied"

        monkeypatch.setattr(trash_manager.shutil, "which", lambda name: "/usr/bin/gio")
        monkeypatch.setattr(trash_manager.subprocess, "run", lambda *a, **k: FakeProc())
        result = move_to_trash(str(target))
        assert result.ok is False
        assert "Permission denied" in (result.error or "")

    def test_bulk_handles_each_path(self, monkeypatch, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()

        def fake_move(path):
            return TrashResult(path=path, ok=path.endswith("a"))

        monkeypatch.setattr(trash_manager, "move_to_trash", fake_move)
        results = move_many_to_trash([str(a), str(b)])
        assert results[0].ok
        assert not results[1].ok


class TestPrivilegedHelper:
    """Exercise the helper directly (no pkexec) to validate its guards."""

    def run_helper(self, *args):
        proc = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(privileged_helper.__file__),
                          "privileged_helper.py"), *args],
            capture_output=True, text=True, shell=False,
        )
        return proc

    def parse(self, proc):
        for line in reversed(proc.stdout.strip().splitlines()):
            if line.startswith("{"):
                return json.loads(line)
        return {"ok": False, "error": "no json"}

    def test_refuses_symlink(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported")
        proc = self.run_helper("--op", "delete", "--path", str(link),
                               "--recursive")
        payload = self.parse(proc)
        assert payload["ok"] is False
        assert "symbolic link" in payload["error"]
        assert real.exists()

    def test_refuses_relative_path(self, tmp_path):
        proc = self.run_helper("--op", "delete", "--path", "relative/dir")
        payload = self.parse(proc)
        assert payload["ok"] is False
        assert "absolute" in payload["error"]

    def test_refuses_protected_path(self):
        proc = self.run_helper("--op", "delete", "--path", "/etc")
        payload = self.parse(proc)
        assert payload["ok"] is False
        assert payload["ok"] is False

    def test_refuses_inode_mismatch(self, tmp_path):
        d = tmp_path / "target"
        d.mkdir()
        proc = self.run_helper("--op", "delete", "--path", str(d),
                               "--expect-ino", "999999999", "--recursive")
        payload = self.parse(proc)
        assert payload["ok"] is False
        assert d.exists()

    def test_deletes_matching_directory(self, tmp_path):
        d = tmp_path / "target"
        d.mkdir()
        (d / "f").write_text("x")
        st = os.lstat(d)
        proc = self.run_helper(
            "--op", "delete", "--path", str(d),
            "--expect-dev", str(st.st_dev),
            "--expect-ino", str(st.st_ino),
            "--recursive",
        )
        payload = self.parse(proc)
        assert payload["ok"] is True
        assert not d.exists()

    def test_missing_target(self, tmp_path):
        proc = self.run_helper("--op", "delete", "--path",
                               str(tmp_path / "nope"), "--recursive")
        payload = self.parse(proc)
        assert payload["ok"] is False
