"""Tests for project-aware scanning policy and pruning.

These exercise the interaction between name matching and ecosystem detection:
* name-only accepts any matching folder;
* project-aware requires project evidence;
* a candidate rejected by policy is still traversed, so deeper valid matches
  are found;
* an accepted candidate is pruned (its descendants are not reported).
"""
from __future__ import annotations

import os

import pytest

from pro_space_optimizer.scanner import (
    MatchingPolicy,
    PolicyOptions,
    Scanner,
    scan_parents,
)


def mk(tmp_path, files):
    root = tmp_path / "projects"
    for rel, content in files.items():
        p = root / rel
        if content is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    return root


def rels(report, base):
    return sorted(os.path.relpath(m.path, base) for m in report.matches)


NAME_ONLY = PolicyOptions(policy=MatchingPolicy.NAME_ONLY)
PROJECT_AWARE = PolicyOptions(policy=MatchingPolicy.PROJECT_AWARE)


class TestNameOnly:
    def test_accepts_any_match(self, tmp_path):
        root = mk(tmp_path, {"random/node_modules/x": None})
        r = scan_parents([str(root)], ["node_modules"], options=NAME_ONLY)
        assert rels(r, root) == ["random/node_modules"]

    def test_prunes_after_match(self, tmp_path):
        root = mk(tmp_path, {"app/node_modules/node_modules/x": None})
        r = scan_parents([str(root)], ["node_modules"], options=NAME_ONLY)
        # Only the outer one; the nested one is pruned.
        assert rels(r, root) == ["app/node_modules"]

    def test_default_policy_is_name_only(self, tmp_path):
        root = mk(tmp_path, {"anything/vendor/x": None})
        r = scan_parents([str(root)], ["vendor"])
        assert rels(r, root) == ["anything/vendor"]


class TestProjectAware:
    def test_accepts_node_modules_in_node_project(self, tmp_path):
        root = mk(tmp_path, {
            "app/package.json": "{}",
            "app/node_modules/dep/index.js": "",
        })
        r = scan_parents([str(root)], ["node_modules"], options=PROJECT_AWARE)
        assert rels(r, root) == ["app/node_modules"]
        m = r.matches[0]
        assert m.ecosystem_id == "node"
        assert "project:" in m.policy_reason

    def test_rejects_node_modules_without_project(self, tmp_path):
        root = mk(tmp_path, {"loose/node_modules/x": None})
        r = scan_parents([str(root)], ["node_modules"], options=PROJECT_AWARE)
        assert r.matches == []

    def test_rejected_candidate_still_traversed(self, tmp_path):
        # The outer node_modules is NOT in a project, but it contains a real
        # project whose node_modules must still be found.
        root = mk(tmp_path, {
            "outer/node_modules/inner/package.json": "{}",
            "outer/node_modules/inner/node_modules/dep/x": None,
        })
        r = scan_parents([str(root)], ["node_modules"], options=PROJECT_AWARE)
        assert rels(r, root) == ["outer/node_modules/inner/node_modules"]

    def test_accepts_vendor_for_php_project(self, tmp_path):
        root = mk(tmp_path, {
            "site/composer.json": "{}",
            "site/vendor/lib/x": None,
        })
        r = scan_parents([str(root)], ["vendor"], options=PROJECT_AWARE)
        assert rels(r, root) == ["site/vendor"]
        assert r.matches[0].ecosystem_id == "composer"

    def test_accepts_target_for_rust_project(self, tmp_path):
        root = mk(tmp_path, {
            "crate/Cargo.toml": "[package]\nname='c'\n",
            "crate/target/debug/x": None,
        })
        r = scan_parents([str(root)], ["target"], options=PROJECT_AWARE)
        assert rels(r, root) == ["crate/target"]

    def test_rejects_vendor_for_node_project(self, tmp_path):
        # vendor is a PHP/Go/Ruby dependency folder, not a Node one.
        root = mk(tmp_path, {
            "app/package.json": "{}",
            "app/vendor/x": None,
        })
        r = scan_parents([str(root)], ["vendor"], options=PROJECT_AWARE)
        assert r.matches == []

    def test_generic_build_folder_in_known_project(self, tmp_path):
        root = mk(tmp_path, {
            "app/package.json": "{}",
            "app/build/out.js": "",
        })
        r = scan_parents([str(root)], ["build"], options=PROJECT_AWARE)
        assert rels(r, root) == ["app/build"]

    def test_min_confidence_high_rejects_low_marker(self, tmp_path):
        # A lone Makefile is LOW confidence; requiring HIGH must reject it.
        root = mk(tmp_path, {"cproj/Makefile": "all:\n\techo hi\n",
                             "cproj/build/x": None})
        opts = PolicyOptions(policy=MatchingPolicy.PROJECT_AWARE,
                             min_confidence=__import__(
                                 "pro_space_optimizer.ecosystem_registry",
                                 fromlist=["Confidence"]).Confidence.HIGH)
        r = scan_parents([str(root)], ["build"], options=opts)
        assert r.matches == []

    def test_ecosystem_filter_allows_only_selected(self, tmp_path):
        root = mk(tmp_path, {
            "nodeapp/package.json": "{}",
            "nodeapp/node_modules/x": None,
            "rustcrate/Cargo.toml": "[package]\nname='r'\n",
            "rustcrate/target/debug/x": None,
        })
        opts = PolicyOptions(policy=MatchingPolicy.PROJECT_AWARE,
                             ecosystem_filter={"rust"})
        r = scan_parents([str(root)], ["node_modules", "target"], options=opts)
        # Only the rust project's target is allowed; the node project's
        # node_modules is filtered out.
        assert rels(r, root) == ["rustcrate/target"]

    def test_ecosystem_filter_excludes_all(self, tmp_path):
        root = mk(tmp_path, {
            "app/package.json": "{}",
            "app/node_modules/x": None,
        })
        opts = PolicyOptions(policy=MatchingPolicy.PROJECT_AWARE,
                             ecosystem_filter={"rust"})
        r = scan_parents([str(root)], ["node_modules"], options=opts)
        assert r.matches == []


class TestNestedProjects:
    def test_nested_projects_each_matched(self, tmp_path):
        root = mk(tmp_path, {
            "mono/frontend/package.json": "{}",
            "mono/frontend/node_modules/x": None,
            "mono/backend/Cargo.toml": "[package]\nname='b'\n",
            "mono/backend/target/debug/y": None,
        })
        r = scan_parents([str(root)], ["node_modules", "target"],
                         options=PROJECT_AWARE)
        found = rels(r, root)
        assert "mono/frontend/node_modules" in found
        assert "mono/backend/target" in found

    def test_project_inside_matched_folder_is_pruned(self, tmp_path):
        root = mk(tmp_path, {
            "app/package.json": "{}",
            "app/node_modules/nested/package.json": "{}",
            "app/node_modules/nested/node_modules/z": None,
        })
        r = scan_parents([str(root)], ["node_modules"], options=PROJECT_AWARE)
        # app/node_modules is accepted and pruned; the nested project inside it
        # is never visited.
        assert rels(r, root) == ["app/node_modules"]


class TestPolicySafety:
    def test_symlinked_dirs_never_matched(self, tmp_path):
        root = mk(tmp_path, {"app/package.json": "{}"})
        real = tmp_path / "real_node_modules"
        real.mkdir()
        (real / "x").write_text("y")
        try:
            os.symlink(real, root / "app" / "node_modules")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported")
        r = scan_parents([str(root)], ["node_modules"], options=PROJECT_AWARE)
        assert r.matches == []
        assert r.skipped_symlinks == 1

    def test_cancellation_in_project_aware(self, tmp_path):
        files = {}
        for i in range(60):
            files[f"p{i}/package.json"] = "{}"
            files[f"p{i}/node_modules/x"] = None
        root = mk(tmp_path, files)
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] > 40

        scanner = Scanner(options=PROJECT_AWARE)
        report = scanner.scan([str(root)], ["node_modules"], should_stop=stop)
        assert report.cancelled

    def test_missing_parent_reported(self, tmp_path):
        missing = str(tmp_path / "nope")
        r = scan_parents([missing], ["node_modules"], options=PROJECT_AWARE)
        assert r.parents[0].error == "Folder does not exist"

    def test_carries_ecosystem_metadata(self, tmp_path):
        root = mk(tmp_path, {
            "app/package.json": '{"dependencies": {"next": "14"}}',
            "app/next.config.js": "",
            "app/node_modules/x": None,
        })
        r = scan_parents([str(root)], ["node_modules"], options=PROJECT_AWARE)
        m = r.matches[0]
        assert m.ecosystem_id == "nextjs"
        assert m.ecosystem_label == "Next.js"
        assert m.ecosystem_confidence == "high"
