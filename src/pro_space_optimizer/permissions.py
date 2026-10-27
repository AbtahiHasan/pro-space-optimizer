"""Privilege escalation support.

The GUI never runs as root and never calls ``sudo`` for the whole
application.  When -- and only when -- a filesystem operation fails with a
permission error, the user is offered the chance to retry that *single*
operation through ``pkexec`` invoking our dedicated helper.

This module owns the (testable) logic for:

* deciding whether an error is a "needs privileges" error,
* locating the helper script and the ``pkexec`` binary,
* invoking the helper with **structured arguments** (never a shell string),
* parsing the helper's structured JSON result.

Nothing here stores or collects passwords; authentication is performed by the
operating system's PolicyKit agent.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

HELPER_TIMEOUT = 180

# Structured operations the helper understands.  Keep this list minimal: the
# helper is intentionally not a general-purpose root file tool.
OP_DELETE = "delete"


@dataclass
class PrivilegeResult:
    ok: bool
    error: Optional[str] = None
    cancelled: bool = False
    unavailable: bool = False
    detail: Optional[str] = None


def is_permission_error(exc: BaseException) -> bool:
    """Return True if *exc* is an errno-based permission failure."""
    if isinstance(exc, PermissionError):
        return True
    errno = getattr(exc, "errno", None)
    if errno in (13,):  # EACCES / EPERM
        return True
    # Some operations raise OSError with a permission message.
    msg = str(exc).lower()
    return "permission denied" in msg or "operation not permitted" in msg


def pkexec_available() -> bool:
    return shutil.which("pkexec") is not None


def helper_path() -> str:
    """Absolute path to the privileged helper script."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "privileged_helper.py")


def _python_for_helper() -> str:
    """Interpreter used to run the helper under pkexec.

    We prefer the current interpreter so the helper runs with a known-good
    Python, but the helper itself only uses the standard library.
    """
    return sys.executable or shutil.which("python3") or "/usr/bin/python3"


def run_helper(operation: str, target: str, expected_dev: Optional[int],
               expected_ino: Optional[int],
               recursive: bool = True) -> PrivilegeResult:
    """Run the privileged helper for a single, structured operation.

    Returns a :class:`PrivilegeResult`.  Never raises for ordinary failures.
    """
    if not pkexec_available():
        return PrivilegeResult(
            ok=False,
            unavailable=True,
            error="pkexec was not found, so administrator privileges cannot "
                  "be requested on this system.",
        )

    helper = helper_path()
    if not os.path.isfile(helper):
        return PrivilegeResult(
            ok=False,
            unavailable=True,
            error=f"Privileged helper not found at {helper}.",
        )

    argv = [
        "pkexec",
        _python_for_helper(),
        helper,
        "--op", operation,
        "--path", os.fspath(target),
    ]
    if expected_dev is not None:
        argv += ["--expect-dev", str(int(expected_dev))]
    if expected_ino is not None:
        argv += ["--expect-ino", str(int(expected_ino))]
    if recursive:
        argv.append("--recursive")

    log.info("Requesting elevated operation %s for %s", operation, target)
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=HELPER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return PrivilegeResult(ok=False, error="Elevated operation timed out.")
    except FileNotFoundError:
        return PrivilegeResult(ok=False, unavailable=True,
                               error="pkexec could not be executed.")
    except OSError as exc:
        return PrivilegeResult(ok=False, error=str(exc))

    # pkexec exit codes: 126 = not authorized / dismissed, 127 = auth error.
    if proc.returncode == 126:
        return PrivilegeResult(ok=False, cancelled=True,
                               error="Authentication was dismissed or not granted.")
    if proc.returncode == 127:
        return PrivilegeResult(
            ok=False,
            unavailable=True,
            error="PolicyKit authentication is not configured for this "
                  "helper. See the README for the optional policy file.",
        )

    payload = _parse_json(proc.stdout)
    if payload is None:
        if proc.returncode == 0:
            # The helper succeeded but produced no JSON -- treat as success
            # only if the target is truly gone.
            gone = not os.path.lexists(target)
            return PrivilegeResult(ok=gone,
                                   error=None if gone else "Unexpected helper output.")
        return PrivilegeResult(ok=False,
                               error=(proc.stderr or "").strip() or
                                     f"Helper exited with code {proc.returncode}.")

    if payload.get("ok"):
        return PrivilegeResult(ok=True, detail=payload.get("detail"))
    return PrivilegeResult(ok=False, error=payload.get("error", "Unknown error"),
                           detail=payload.get("detail"))


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    # The helper prints exactly one JSON object; be tolerant of extra lines.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
