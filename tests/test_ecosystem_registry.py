"""Tests for ecosystem detection.

All tests use temporary directories and never touch real user files.
Detection is pure: it only stats files and reads small config files.
"""
from __future__ import annotations

import os

import pytest

from pro_space_optimizer import ecosystem_registry as eco


def make(tmp_path, files=None, dirs=None):
    """Create a project directory from ``{relpath: content}`` and dir names."""
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    for rel, content in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for d in (dirs or ()):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


class TestRegistry:
    def test_registry_loads(self):
        assert len(eco.registry()) > 100
        ids = eco.ecosystem_ids()
        assert len(ids) == len(set(ids)), "ecosystem ids must be unique"

    def test_known_ids_present(self):
        ids = set(eco.ecosystem_ids())
        for expected in ("node", "nextjs", "django", "flutter", "unity",
                         "terraform", "go", "rust", "dotnet", "maven"):
            assert expected in ids

    def test_folder_rules_include_common_names(self):
        names = set(eco.ecosystem_folder_names())
        for expected in ("node_modules", "vendor", ".venv", "__pycache__",
                         "target", "build", "dist", ".next", "Pods",
                         ".dart_tool", ".gradle", ".terraform"):
            assert expected in names


class TestStrongMarkers:
    def test_node(self, tmp_path):
        d = make(tmp_path, {"package.json": "{}"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "node"
        assert r.confidence == eco.Confidence.HIGH

    def test_rust(self, tmp_path):
        d = make(tmp_path, {"Cargo.toml": "[package]\nname='x'\n"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "rust"

    def test_go(self, tmp_path):
        d = make(tmp_path, {"go.mod": "module example.com/x\n"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "go"

    def test_maven(self, tmp_path):
        d = make(tmp_path, {"pom.xml": "<project></project>"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "maven"

    def test_dotnet(self, tmp_path):
        d = make(tmp_path, {"App.csproj": "<Project></Project>"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "dotnet"

    def test_php_composer(self, tmp_path):
        d = make(tmp_path, {"composer.json": "{}"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "composer"


class TestFrameworkSpecificity:
    def test_nextjs_outranks_node(self, tmp_path):
        d = make(tmp_path, {
            "package.json": '{"dependencies": {"next": "14"}}',
            "next.config.js": "",
        })
        r = eco.detect(str(d))
        assert r.ecosystem_id == "nextjs"
        assert "node" in r.matched

    def test_rails_outranks_ruby(self, tmp_path):
        d = make(tmp_path, {"Gemfile": 'gem "rails"', "bin/rails": ""})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "rails"
        assert not r.ambiguous

    def test_django_outranks_python(self, tmp_path):
        d = make(tmp_path, {"manage.py": "", "requirements.txt": "Django\n"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "django"
        assert not r.ambiguous

    def test_flutter_outranks_dart(self, tmp_path):
        d = make(tmp_path, {"pubspec.yaml": "flutter:\n",
                            "lib/main.dart": ""},
                 dirs=("android",))
        r = eco.detect(str(d))
        assert r.ecosystem_id == "flutter"

    def test_phoenix_outranks_elixir(self, tmp_path):
        d = make(tmp_path, {"mix.exs": "defp deps, do: [{:phoenix, '~> 1.7'}]"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "phoenix"


class TestWeakMarkers:
    def test_generic_makefile_is_low_confidence_only(self, tmp_path):
        d = make(tmp_path, {"Makefile": "all:\n\techo hi\n",
                            "README.md": "# hi\n"})
        r = eco.detect(str(d))
        # A lone Makefile must not classify as a specific language project.
        assert r.ecosystem_id in (None, "make")
        if r.ecosystem_id == "make":
            assert r.confidence == eco.Confidence.LOW

    def test_readme_alone_is_unknown(self, tmp_path):
        d = make(tmp_path, {"README.md": "# just docs\n"})
        r = eco.detect(str(d))
        assert not r.known

    def test_git_alone_is_unknown(self, tmp_path):
        d = make(tmp_path, {}, dirs=(".git",))
        r = eco.detect(str(d))
        assert not r.known

    def test_dockerfile_alone_is_unknown(self, tmp_path):
        d = make(tmp_path, {"Dockerfile": "FROM scratch\n"})
        r = eco.detect(str(d))
        assert not r.known


class TestMalformedConfigs:
    def test_broken_package_json_does_not_crash(self, tmp_path):
        d = make(tmp_path, {"package.json": "{not valid json"})
        r = eco.detect(str(d))
        # Still recognised as node by the primary marker, but no framework.
        assert r.ecosystem_id == "node"

    def test_broken_toml_does_not_crash(self, tmp_path):
        d = make(tmp_path, {"Cargo.toml": "this = = invalid"})
        r = eco.detect(str(d))
        assert r.ecosystem_id == "rust"

    def test_broken_pyproject_does_not_crash(self, tmp_path):
        d = make(tmp_path, {"pyproject.toml": "[[[ not toml",
                            "requirements.txt": "flask\n"})
        r = eco.detect(str(d))
        assert r.known

    def test_binary_garbage_does_not_crash(self, tmp_path):
        d = tmp_path / "proj"
        d.mkdir()
        (d / "package.json").write_bytes(b"\xff\xfe\x00\x00garbage")
        (d / "pyproject.toml").write_bytes(b"\x00\x01\x02")
        r = eco.detect(str(d))
        assert r is not None


class TestMultipleEcosystems:
    def test_repo_with_several_ecosystems(self, tmp_path):
        # A monorepo containing a Node app and a Rust crate in subdirs.
        d = tmp_path / "mono"
        (d / "frontend").mkdir(parents=True)
        (d / "frontend" / "package.json").write_text(
            '{"dependencies": {"next": "14"}}')
        (d / "frontend" / "next.config.js").write_text("")
        (d / "backend").mkdir(parents=True)
        (d / "backend" / "Cargo.toml").write_text("[package]\nname='b'\n")

        assert eco.detect(str(d / "frontend")).ecosystem_id == "nextjs"
        assert eco.detect(str(d / "backend")).ecosystem_id == "rust"

    def test_ambiguous_top_tier(self, tmp_path):
        # Two unrelated HIGH markers of equal specificity -> ambiguous.
        d = make(tmp_path, {"Cargo.toml": "[package]\nname='x'\n",
                            "go.mod": "module x\n"})
        r = eco.detect(str(d))
        assert r.ambiguous
        assert r.ecosystem_id in ("rust", "go")


class TestSymlinkHandling:
    def test_symlinked_marker_not_followed_for_dirs(self, tmp_path):
        # A symlinked directory named like a marker must not be treated as one.
        real = tmp_path / "realassets"
        real.mkdir()
        d = tmp_path / "proj"
        d.mkdir()
        try:
            os.symlink(real, d / "Assets")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported")
        # Unity needs Assets/ + ProjectSettings/... ; the symlinked Assets
        # must not satisfy the directory check.
        r = eco.detect(str(d))
        assert r.ecosystem_id != "unity"


class TestDeterminism:
    def test_same_input_same_output(self, tmp_path):
        d = make(tmp_path, {
            "package.json": '{"dependencies": {"react": "18"}}',
        })
        first = eco.detect(str(d))
        second = eco.detect(str(d))
        assert first.ecosystem_id == second.ecosystem_id
        assert first.matched == second.matched
