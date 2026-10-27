"""Persistent application settings backed by QSettings.

Configuration is stored per-user under
``~/.config/ProSpaceOptimizer/ProSpaceOptimizer.conf`` (the location Qt
derives from the organisation and application names).  No passwords, tokens
or other secrets are ever written here.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import QByteArray, QSettings

from . import ORG_NAME

log = logging.getLogger(__name__)

# Keys
K_PARENTS = "scanner/parent_folders"
K_NAMES = "scanner/folder_names"
K_DELETE_MODE = "deletion/mode"
K_GEOMETRY = "ui/geometry"
K_WINDOW_STATE = "ui/window_state"
K_THEME = "ui/theme"
K_CONFIRM_PERMANENT = "deletion/confirm_permanent"
K_FOLLOW_SIZES = "scanner/calculate_sizes"
K_MATCH_POLICY = "scanner/match_policy"
K_MIN_CONFIDENCE = "scanner/min_confidence"
K_ECOSYSTEM_FILTER = "scanner/ecosystem_filter"
K_SHOW_ECOSYSTEM = "ui/show_ecosystem_column"

VALID_THEMES = ("system", "light", "dark")
VALID_MODES = ("trash", "permanent")
VALID_POLICIES = ("name", "project")
VALID_CONFIDENCE = ("low", "medium", "high")

DEFAULT_NAMES = "node_modules, vendor, .venv, __pycache__, build, dist"


class Settings:
    """Thin, defensive wrapper around :class:`QSettings`."""

    def __init__(self, settings: Optional[QSettings] = None):
        if settings is not None:
            self._s = settings
        else:
            self._s = QSettings(ORG_NAME, ORG_NAME)

    # -- generic helpers ----------------------------------------------------
    def _get_list(self, key: str) -> List[str]:
        raw = self._s.value(key, [])
        if raw is None:
            return []
        if isinstance(raw, str):
            # QSettings may collapse a single-element list to a string.
            return [raw] if raw else []
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw if x is not None]
        return []

    def _set_list(self, key: str, values: List[str]) -> None:
        self._s.setValue(key, list(values))
        self._s.sync()

    def _get_bool(self, key: str, default: bool) -> bool:
        raw = self._s.value(key, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        try:
            return bool(raw)
        except Exception:
            return default

    # -- parent folders -----------------------------------------------------
    def parent_folders(self) -> List[str]:
        return self._get_list(K_PARENTS)

    def set_parent_folders(self, folders: List[str]) -> None:
        self._set_list(K_PARENTS, folders)

    def add_parent_folder(self, folder: str) -> bool:
        """Add a parent folder if not already present.  Returns True if added."""
        current = self.parent_folders()
        if folder in current:
            return False
        current.append(folder)
        self.set_parent_folders(current)
        return True

    def remove_parent_folder(self, folder: str) -> bool:
        current = self.parent_folders()
        if folder not in current:
            return False
        current.remove(folder)
        self.set_parent_folders(current)
        return True

    def clear_parent_folders(self) -> None:
        self.set_parent_folders([])

    # -- folder names -------------------------------------------------------
    def folder_names_text(self) -> str:
        raw = self._s.value(K_NAMES, DEFAULT_NAMES)
        if raw is None:
            return DEFAULT_NAMES
        return str(raw)

    def set_folder_names_text(self, text: str) -> None:
        self._s.setValue(K_NAMES, text)
        self._s.sync()

    # -- deletion mode ------------------------------------------------------
    def delete_mode(self) -> str:
        mode = str(self._s.value(K_DELETE_MODE, "trash")).lower()
        if mode not in VALID_MODES:
            log.warning("Invalid delete mode %r, falling back to 'trash'", mode)
            return "trash"
        return mode

    def set_delete_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            mode = "trash"
        self._s.setValue(K_DELETE_MODE, mode)
        self._s.sync()

    def confirm_permanent(self) -> bool:
        return self._get_bool(K_CONFIRM_PERMANENT, True)

    def set_confirm_permanent(self, value: bool) -> None:
        self._s.setValue(K_CONFIRM_PERMANENT, bool(value))
        self._s.sync()

    # -- scanner options ----------------------------------------------------
    def calculate_sizes(self) -> bool:
        return self._get_bool(K_FOLLOW_SIZES, True)

    def set_calculate_sizes(self, value: bool) -> None:
        self._s.setValue(K_FOLLOW_SIZES, bool(value))
        self._s.sync()

    # -- project-aware scanning --------------------------------------------
    def match_policy(self) -> str:
        policy = str(self._s.value(K_MATCH_POLICY, "name")).lower()
        if policy not in VALID_POLICIES:
            return "name"
        return policy

    def set_match_policy(self, policy: str) -> None:
        if policy not in VALID_POLICIES:
            policy = "name"
        self._s.setValue(K_MATCH_POLICY, policy)
        self._s.sync()

    def min_confidence(self) -> str:
        value = str(self._s.value(K_MIN_CONFIDENCE, "medium")).lower()
        if value not in VALID_CONFIDENCE:
            return "medium"
        return value

    def set_min_confidence(self, value: str) -> None:
        if value not in VALID_CONFIDENCE:
            value = "medium"
        self._s.setValue(K_MIN_CONFIDENCE, value)
        self._s.sync()

    def ecosystem_filter(self) -> List[str]:
        return self._get_list(K_ECOSYSTEM_FILTER)

    def set_ecosystem_filter(self, ecosystem_ids: List[str]) -> None:
        self._set_list(K_ECOSYSTEM_FILTER, ecosystem_ids)

    def show_ecosystem_column(self) -> bool:
        return self._get_bool(K_SHOW_ECOSYSTEM, True)

    def set_show_ecosystem_column(self, value: bool) -> None:
        self._s.setValue(K_SHOW_ECOSYSTEM, bool(value))
        self._s.sync()

    # -- ui -----------------------------------------------------------------
    def theme(self) -> str:
        theme = str(self._s.value(K_THEME, "system")).lower()
        if theme not in VALID_THEMES:
            return "system"
        return theme

    def set_theme(self, theme: str) -> None:
        if theme not in VALID_THEMES:
            theme = "system"
        self._s.setValue(K_THEME, theme)
        self._s.sync()

    def save_geometry(self, geometry: QByteArray) -> None:
        self._s.setValue(K_GEOMETRY, geometry)
        self._s.sync()

    def geometry(self) -> Optional[QByteArray]:
        raw = self._s.value(K_GEOMETRY, None)
        if isinstance(raw, QByteArray) and not raw.isEmpty():
            return raw
        return None

    def save_window_state(self, state: QByteArray) -> None:
        self._s.setValue(K_WINDOW_STATE, state)
        self._s.sync()

    def window_state(self) -> Optional[QByteArray]:
        raw = self._s.value(K_WINDOW_STATE, None)
        if isinstance(raw, QByteArray) and not raw.isEmpty():
            return raw
        return None

    def sync(self) -> None:
        self._s.sync()

    def settings_path(self) -> str:
        return self._s.fileName()
