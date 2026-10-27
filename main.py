#!/usr/bin/env python3
"""Entry point for Pro Space Optimizer.

This file exists so the application can be launched with a bare
``python3 main.py`` from a source checkout without installing the package.
It simply prepends ``src/`` to ``sys.path`` and defers to the real entry point.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pro_space_optimizer.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
