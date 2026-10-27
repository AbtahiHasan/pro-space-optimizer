"""Trash support.

Moving a folder to Trash follows the freedesktop.org Trash specification.  We
delegate to ``gio trash`` (part of GLib, present on every modern Ubuntu
desktop) rather than hand-rolling the ``files``/``info`` layout.  Delegating
means:

* cross-filesystem moves are handled correctly (the implementation chooses
  the trash directory on the correct mount, or falls back to a copy+delete),
* the ``.trashinfo`` metadata is written correctly so Files/Nautilus can
  restore the entry,
* we never construct a shell command line -- ``gio`` is invoked as a
  subprocess with an explicit argument list and ``shell=False``.

If ``gio`` is missing or reports failure, we surface the problem to the caller
so the GUI can explain it and offer permanent deletion with explicit
confirmation.  We deliberately do **not** implement trash as "rename into an
arbitrary folder".
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)


@dataclass
class TrashResult:
    path: str
    ok: bool
    error: Optional[str] = None
    available: bool = True


class TrashUnavailable(Exception):
    """Raised when no usable trash implementation can be found."""


def _find_gio() -> Optional[str]:
    return shutil.which("gio")


def trash_available() -> bool:
    """Return True if a supported trash mechanism is available."""
    return _find_gio() is not None


def _describe_failure(stderr: str, returncode: int) -> str:
    text = (stderr or "").strip()
    lowered = text.lower()
    if "no such file" in lowered:
        return "The folder no longer exists"
    if "permission denied" in lowered or "not permitted" in lowered:
        return "Permission denied"
    if "not supported" in lowered or "unsupported" in lowered:
        return "The filesystem does not support the Trash"
    if not text:
        return f"gio trash failed with exit code {returncode}"
    # Keep the first line only; gio can be verbose.
    first = text.splitlines()[0]
    return first


def move_to_trash(path: str) -> TrashResult:
    """Move a single *path* to the freedesktop Trash.

    Never raises for expected failures -- inspect ``TrashResult.ok``.  Raises
    :class:`TrashUnavailable` only when no trash backend exists at all.
    """
    gio = _find_gio()
    if gio is None:
        raise TrashUnavailable(
            "The 'gio' command (from GLib) was not found, so the Trash is "
            "not available on this system."
        )

    path = os.fspath(path)
    if not os.path.lexists(path):
        return TrashResult(path=path, ok=False, error="The folder no longer exists")

    try:
        proc = subprocess.run(
            [gio, "trash", "--", path],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return TrashResult(path=path, ok=False, error="Timed out while moving to Trash")
    except OSError as exc:
        return TrashResult(path=path, ok=False, error=str(exc))

    if proc.returncode == 0:
        log.info("Moved to Trash: %s", path)
        return TrashResult(path=path, ok=True)

    error = _describe_failure(proc.stderr, proc.returncode)
    log.warning("gio trash failed for %s: %s", path, error)
    return TrashResult(path=path, ok=False, error=error)


def move_many_to_trash(paths: Sequence[str]) -> List[TrashResult]:
    """Move several paths to Trash, returning one result per path.

    Each path is handled independently so one failure does not prevent the
    others from being attempted.
    """
    results: List[TrashResult] = []
    for path in paths:
        try:
            results.append(move_to_trash(path))
        except TrashUnavailable as exc:
            results.append(TrashResult(path=path, ok=False, error=str(exc),
                                       available=False))
    return results
