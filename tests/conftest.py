"""Shared pytest fixtures.

All filesystem tests use ``tmp_path`` so the user's real directories are never
touched.  Qt-dependent tests set ``QT_QPA_PLATFORM=offscreen`` (done in
conftest at import time) so they run without a display.
"""
from __future__ import annotations

import os
import sys

# Make the package importable from a source checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Use the offscreen platform for any test that needs a QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402  (import intentionally after sys.path setup)


def make_tree(base, spec):
    """Create a directory tree from a compact spec.

    ``spec`` maps relative directory paths to a list of files to create inside
    them.  Returns the base path as a string.
    """
    base = os.fspath(base)
    for rel, files in spec.items():
        directory = os.path.join(base, rel) if rel else base
        os.makedirs(directory, exist_ok=True)
        for name in files:
            with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
                fh.write("x" * 10)
    return base


@pytest.fixture
def sample_tree(tmp_path):
    """A representative tree used by several scanner tests."""
    make_tree(tmp_path, {
        "node_modules": ["a.js"],
        "vendor": ["b.php"],
        "project-a": [],
        "project-a/node_modules": ["c.js"],
        "project-b": [],
        "project-b/vendor": ["d.php"],
        "regular-file.txt": [],
    })
    return tmp_path
