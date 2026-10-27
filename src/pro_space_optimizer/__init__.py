"""Pro Space Optimizer - find and remove unnecessary development folders.

A PySide6 desktop utility for Ubuntu that recursively scans the
subdirectories of user-selected parent folders for well-known development
folder names (``node_modules``, ``vendor``, ``.venv``, ``__pycache__``,
``build``, ``dist`` ...) and lets the user safely remove them.  Matched
folders are pruned, so nested duplicates are not reported.
"""

from __future__ import annotations

__all__ = [
    "APP_NAME",
    "APP_DISPLAY_NAME",
    "APP_VERSION",
    "ORG_NAME",
    "ORG_DOMAIN",
]

APP_NAME = "pro-space-optimizer"
APP_DISPLAY_NAME = "Pro Space Optimizer"
APP_VERSION = "1.0.0"

# QSettings organisation / application identifiers.
ORG_NAME = "ProSpaceOptimizer"
ORG_DOMAIN = "pro-space-optimizer.local"

__version__ = APP_VERSION
