#!/usr/bin/env python3
"""Privileged deletion helper for Pro Space Optimizer.

This script is intended to be launched via ``pkexec`` by the main application
to remove a *single* directory that the user could not delete with their own
privileges.  It is deliberately minimal:

* It accepts **structured arguments** (``--op``, ``--path``, ``--expect-dev``,
  ``--expect-ino``, ``--recursive``); it never executes a shell string.
* It independently re-validates every input instead of trusting the caller.
* It refuses to follow symlinks: if the target is a symlink it is refused.
* It optionally verifies the device/inode identity captured at scan time so a
  swapped-in target is rejected.
* It performs exactly one operation and prints a single JSON object to stdout.
* It does not expose a general-purpose root file API.

It only uses the Python standard library so it runs with a system Python.

Usage (internal):
    pkexec python3 privileged_helper.py --op delete --path /x/y \\
        [--expect-dev N] [--expect-ino N] [--recursive]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys


def _fail(error: str, detail: str = "") -> int:
    print(json.dumps({"ok": False, "error": error, "detail": detail}))
    return 1


def _succeed(detail: str = "") -> int:
    print(json.dumps({"ok": True, "detail": detail}))
    return 0


def _validate_path(raw: str) -> str:
    if not raw:
        raise ValueError("path is required")
    if "\x00" in raw:
        raise ValueError("path contains a NUL byte")
    path = os.path.normpath(raw)
    if not os.path.isabs(path):
        raise ValueError("path must be absolute")
    if path in ("/", "/home", "/usr", "/etc", "/bin", "/sbin", "/var", "/boot",
                "/dev", "/proc", "/sys", "/root"):
        raise ValueError("refusing to operate on a protected system path")
    # Refuse paths that contain newlines or other control characters.
    if any(ord(ch) < 32 for ch in path):
        raise ValueError("path contains control characters")
    return path


def _delete(path: str, recursive: bool, expect_dev, expect_ino) -> int:
    # Use lstat so we inspect the entry itself, not a symlink target.
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return _fail("The folder no longer exists")
    except PermissionError:
        return _fail("Permission denied while inspecting the folder")
    except OSError as exc:
        return _fail(exc.strerror or str(exc))

    if stat.S_ISLNK(st.st_mode):
        return _fail("Refusing to operate on a symbolic link")

    if not stat.S_ISDIR(st.st_mode):
        return _fail("Target is not a directory")

    if expect_dev is not None and int(st.st_dev) != int(expect_dev):
        return _fail("Target changed since scanning (device mismatch)")
    if expect_ino is not None and int(st.st_ino) != int(expect_ino):
        return _fail("Target changed since scanning (inode mismatch)")

    try:
        if recursive:
            shutil.rmtree(path, ignore_errors=False)
        else:
            os.rmdir(path)
    except FileNotFoundError:
        return _fail("The folder no longer exists")
    except PermissionError:
        return _fail("Permission denied while deleting the folder")
    except OSError as exc:
        return _fail(exc.strerror or str(exc))

    return _succeed(f"Deleted {path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--op", required=True, choices=["delete"])
    parser.add_argument("--path", required=True)
    parser.add_argument("--expect-dev", type=int, default=None)
    parser.add_argument("--expect-ino", type=int, default=None)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args(argv)

    try:
        path = _validate_path(args.path)
    except ValueError as exc:
        return _fail(str(exc))

    if args.op == "delete":
        return _delete(path, args.recursive, args.expect_dev, args.expect_ino)

    return _fail(f"Unsupported operation: {args.op}")


if __name__ == "__main__":
    sys.exit(main())
