"""Directory size calculation.

Sizes are computed by walking *inside* a single already-discovered matching
directory.  This walk never looks for additional matching directory names and
never follows symlinks, so it cannot expand the result set.

Correctness notes
-----------------
* Hard links are de-duplicated using ``(st_dev, st_ino)`` so the same inode is
  never counted twice.
* Symlinks are not followed, so a link to a huge directory does not inflate
  the total and a symlink loop cannot hang the walk.
* Permission errors and files that disappear mid-walk are collected and the
  size is reported as *partial* rather than failing the whole result.
* On many filesystems the value differs from on-disk usage because of block
  allocation, sparse files, hard links and permission restrictions.  This is
  documented in the README.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set, Tuple

from .models import SizeState

log = logging.getLogger(__name__)


@dataclass
class SizeResult:
    path: str
    size: Optional[int]
    state: SizeState
    files: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return self.state == SizeState.PARTIAL


def calculate_size(
    path: str,
    should_stop: Optional[Callable[[], bool]] = None,
) -> SizeResult:
    """Return the total size in bytes of the directory tree at *path*.

    The function never raises for ordinary filesystem errors; those are
    collected in :attr:`SizeResult.errors`.  A cancellation request stops the
    walk early and marks the result *partial*.
    """
    if should_stop is None:
        should_stop = lambda: False  # noqa: E731

    total = 0
    files = 0
    errors: List[str] = []
    seen: Set[Tuple[int, int]] = set()

    def _walk(directory: str) -> None:
        nonlocal total, files
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if should_stop():
                        return
                    try:
                        if entry.is_symlink():
                            # Do not follow symlinks; count the link itself
                            # has no meaningful size on most filesystems.
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            _walk(entry.path)
                            continue
                        if entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            key = (st.st_dev, st.st_ino)
                            if key in seen:
                                continue
                            seen.add(key)
                            total += st.st_size
                            files += 1
                    except FileNotFoundError:
                        errors.append(f"Disappeared: {entry.path}")
                    except PermissionError:
                        errors.append(f"Permission denied: {entry.path}")
                    except OSError as exc:
                        errors.append(f"{exc.strerror or exc}: {entry.path}")
        except FileNotFoundError:
            errors.append(f"Directory disappeared: {directory}")
        except PermissionError:
            # Can't list this subdirectory; mark partial but continue.
            errors.append(f"Permission denied: {directory}")
        except OSError as exc:
            errors.append(f"{exc.strerror or exc}: {directory}")

    try:
        os.lstat(path)
    except FileNotFoundError:
        return SizeResult(path=path, size=None, state=SizeState.UNKNOWN,
                          errors=["Folder disappeared"])
    except PermissionError:
        return SizeResult(path=path, size=None, state=SizeState.UNKNOWN,
                          errors=["Permission denied"])
    except OSError as exc:
        return SizeResult(path=path, size=None, state=SizeState.UNKNOWN,
                          errors=[str(exc)])

    _walk(path)

    if should_stop() or errors:
        state = SizeState.PARTIAL if not should_stop() else SizeState.PARTIAL
    else:
        state = SizeState.DONE
    return SizeResult(path=path, size=total, state=state, files=files,
                      errors=errors)
