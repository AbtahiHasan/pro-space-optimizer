"""Small, dependency-free helpers shared across the application.

Nothing in this module touches the GUI or performs destructive operations.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Size formatting
# ---------------------------------------------------------------------------

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(num_bytes: Optional[int]) -> str:
    """Return a human readable size string such as ``1.2 GB``.

    ``None`` means the size could not be (fully) determined and is rendered
    as ``"Unknown"``.
    """
    if num_bytes is None:
        return "Unknown"
    if num_bytes < 0:
        return "Unknown"
    size = float(num_bytes)
    for unit in _UNITS:
        if size < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


# ---------------------------------------------------------------------------
# Folder-name input parsing
# ---------------------------------------------------------------------------

def parse_folder_names(text: str) -> List[str]:
    """Parse user input into a de-duplicated, ordered list of folder names.

    Names may be separated by commas, semicolons or newlines.  Whitespace is
    trimmed, empty entries are dropped and duplicates removed while preserving
    the order in which they first appeared.  Matching is *case-sensitive* on
    Linux, so the original case is preserved exactly.
    """
    if not text:
        return []
    parts = re.split(r"[,;\n\r]+", text)
    seen = set()
    result: List[str] = []
    for part in parts:
        name = part.strip()
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def is_valid_folder_name(name: str) -> Tuple[bool, str]:
    """Return ``(ok, reason)`` for a single folder name.

    We deliberately reject anything that looks like a path separator or that
    contains NUL so the value can never be interpreted as a path or injected
    into a command.  The name is used only for equality comparison with real
    directory entry names.
    """
    if not name:
        return False, "empty name"
    if "\x00" in name:
        return False, "name contains a NUL byte"
    if "/" in name or "\\" in name:
        return False, "name must not contain path separators"
    if name in (".", ".."):
        return False, "name must not be a relative path component"
    return True, ""


def validate_folder_names(names: Sequence[str]) -> List[str]:
    """Filter a sequence of names, keeping only valid ones."""
    return [n for n in names if is_valid_folder_name(n)[0]]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def normalize_path(path: os.PathLike | str) -> str:
    """Return an absolute, normalised (but *not* resolved) path string.

    Symlinks are intentionally **not** resolved here -- resolving could hide
    the fact that a user selected a symlinked parent folder and would make it
    harder to reason about identity checks later.
    """
    p = Path(path)
    try:
        p = p.expanduser()
    except (RuntimeError, KeyError):
        pass
    if not p.is_absolute():
        p = Path.cwd() / p
    return os.path.normpath(os.fspath(p))


def is_within(path: os.PathLike | str, parents: Iterable[os.PathLike | str]) -> bool:
    """True if *path* is a strict descendant of any of *parents*.

    Comparison is done on normalised absolute paths.  A path equal to a
    parent is **not** considered "within" -- callers use this to make sure a
    deletion target is at least one level below a saved parent.
    """
    target = normalize_path(path)
    target_parts = PurePosixPath(target).parts
    for parent in parents:
        parent_norm = normalize_path(parent)
        parent_parts = PurePosixPath(parent_norm).parts
        if len(target_parts) <= len(parent_parts):
            continue
        if target_parts[: len(parent_parts)] == parent_parts:
            return True
    return False


def is_direct_child(path: os.PathLike | str, parent: os.PathLike | str) -> bool:
    """True if *path* is exactly one level below *parent*."""
    try:
        p = Path(normalize_path(path))
        par = Path(normalize_path(parent))
        return p.parent == par and p != par
    except Exception:
        return False


def unique_preserving_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def shorten_path(path: str, max_len: int = 80) -> str:
    """Shorten the middle of a long path for compact display."""
    if max_len < 8 or len(path) <= max_len:
        return path
    head = max_len // 2 - 2
    tail = max_len - head - 3
    return f"{path[:head]}...{path[-tail:]}"


def sizeof_fmt(num_bytes: int) -> str:  # pragma: no cover - alias
    return human_size(num_bytes)
