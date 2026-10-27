"""Tests for safe deletion.

These tests never touch real user data: everything happens under ``tmp_path``.
Privilege escalation is exercised through the helper's own validation, not by
actually invoking pkexec.
"""
from __future__ import annotations

import os

import pytest

from pro_space_optimizer import deletion as deletion_mod
from pro_space_optimizer.deletion import DeletionManager
from pro_space_optimizer.models import ScanResult


def make_result(path, name=None, parent=None, dev=None, ino=None):
    path = os.fspath(path)
    st = os.lstat(path) if os.path.lexists(path) else None
    return ScanResult(
        name=name or os.path.basename(path),
        parent=parent or os.path.dirname(path),
        path=path,
        st_dev=dev if dev is not None else (getattr(st, "st_dev", None)),
        st_ino=ino if ino is not None else (getattr(st, "st_ino", None)),
    )


@pytest.fixture
def tree(tmp_path):
    parent = tmp_path / "projects"
    match = parent / "node_modules"
    match.mkdir(parents=True)
    (match / "pkg.js").write_text("x")
    return parent, match


def test_delete_single_permanent(tree):
    parent, match = tree
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(match)
    outcome = manager.delete(result, mode="permanent")
    assert outcome.ok
    assert not match.exists()
    assert parent.exists()  # parent must survive


def test_delete_single_trash(tree, monkeypatch):
    parent, match = tree
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(match)

    called = {}

    def fake_move(path):
        called["path"] = path
        from pro_space_optimizer.trash_manager import TrashResult
        # simulate a successful move by removing the directory
        import shutil
        shutil.rmtree(path)
        return TrashResult(path=path, ok=True)

    monkeypatch.setattr(deletion_mod, "move_to_trash", fake_move)
    outcome = manager.delete(result, mode="trash")
    assert outcome.ok
    assert called["path"] == str(match)
    assert not match.exists()


def test_refuses_when_target_vanished(tree):
    parent, match = tree
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(match)
    import shutil
    shutil.rmtree(match)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert "no longer exists" in (outcome.error or "")


def test_refuses_when_identity_changed(tree):
    """TOCTOU: a different directory swapped in must be rejected."""
    parent, match = tree
    import shutil
    shutil.rmtree(match)
    match.mkdir()  # same name, new inode
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(match, ino=999999999)  # stale inode from scan time
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert "changed since scanning" in (outcome.error or "")
    assert match.exists()


def test_refuses_target_outside_saved_parents(tree, tmp_path):
    parent, match = tree
    other = tmp_path / "elsewhere"
    other.mkdir()
    target = other / "node_modules"
    target.mkdir()
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(target)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert "not inside a saved parent" in (outcome.error or "")
    assert target.exists()


def test_deletes_nested_match(tmp_path):
    """Recursive scans produce deep matches; those must be deletable."""
    parent = tmp_path / "projects"
    nested = parent / "app2" / "src" / "node_modules"
    nested.mkdir(parents=True)
    (nested / "pkg.js").write_text("x")
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(nested)
    outcome = manager.delete(result, mode="permanent")
    assert outcome.ok
    assert not nested.exists()
    # The intermediate directories and the parent must survive.
    assert (parent / "app2" / "src").exists()
    assert parent.exists()


def test_nested_match_outside_parents_refused(tmp_path):
    """A deep match is only allowed within a saved parent's subtree."""
    parent = tmp_path / "projects"
    parent.mkdir()
    elsewhere = tmp_path / "elsewhere" / "deep" / "node_modules"
    elsewhere.mkdir(parents=True)
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(elsewhere)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert elsewhere.exists()


def test_refuses_name_not_in_search_list(tree):
    parent, match = tree
    manager = DeletionManager([str(parent)], ["something_else"])
    result = make_result(match)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert "does not" in (outcome.error or "") or "match" in (outcome.error or "")
    assert match.exists()


def test_refuses_symlink_target(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    real = tmp_path / "real_target"
    real.mkdir()
    link = parent / "node_modules"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported")
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(link)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert real.exists()


def test_refuses_parent_itself(tree):
    parent, match = tree
    # A saved parent is never "within" itself (is_within is a strict
    # descendant test), so deleting a parent must be refused.
    manager = DeletionManager([str(parent)], ["projects"])
    result = make_result(parent)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert parent.exists()


def test_bulk_mixed_success_and_failure(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    good = parent / "node_modules"
    good.mkdir()
    (good / "a").write_text("x")
    bad = parent / "vendor"
    bad.mkdir()
    (bad / "b").write_text("y")

    manager = DeletionManager([str(parent)], ["node_modules", "vendor"])
    good_result = make_result(good)
    bad_result = make_result(bad, ino=123456789)  # stale -> should fail

    outcomes = manager.delete_many([good_result, bad_result], mode="permanent")
    assert len(outcomes) == 2
    assert outcomes[0].ok
    assert not outcomes[1].ok
    assert not good.exists()
    assert bad.exists()  # failure did not stop the other


def test_permanent_delete_refuses_symlink_inside(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    match = parent / "node_modules"
    match.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, match / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported")
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(match)
    outcome = manager.delete(result, mode="permanent")
    assert not outcome.ok
    assert "symbolic link was found inside" in (outcome.error or "")
    assert match.exists()


def test_validates_before_delete(tree):
    parent, match = tree
    manager = DeletionManager([str(parent)], ["node_modules"])
    result = make_result(match)
    v = manager.validate(result)
    assert v.ok
    assert v.st_dev is not None
    assert v.st_ino is not None


def test_no_shell_true_used():
    """Guard against a regression that reintroduces shell execution."""
    import inspect
    src = inspect.getsource(deletion_mod)
    assert "shell=True" not in src
