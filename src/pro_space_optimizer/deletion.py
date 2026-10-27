"""Safe deletion orchestration.

All destructive operations flow through :class:`DeletionManager`.  Before
touching the filesystem it validates every target against the expected result
captured at scan time.  Two modes are supported:

* ``trash`` (default) -- move to the freedesktop Trash via
  :mod:`pro_space_optimizer.trash_manager`.
* ``permanent`` -- remove the directory tree; this requires explicit
  confirmation from the GUI layer (enforced there), and here we simply refuse
  to recurse into symlinks.

Safety rules enforced here
--------------------------
1. The target must be a non-symlink directory.
2. The target must be a *strict descendant* of one of the saved parent folders
   (scanning is recursive, so a match may be several levels deep, but it must
   stay inside a user-approved parent subtree and never equal a parent).
3. The target's name must be one of the searched names.
4. The target's device/inode must match the values captured at scan time
   (defence against time-of-check/time-of-use races and symlink substitution).
5. Nothing is ever executed through a shell.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from . import permissions
from .models import ScanResult
from .trash_manager import TrashUnavailable, move_to_trash
from .utils import is_valid_folder_name, is_within, normalize_path

log = logging.getLogger(__name__)


@dataclass
class DeleteOutcome:
    path: str
    ok: bool
    error: Optional[str] = None
    needs_privileges: bool = False
    mode: str = "trash"


@dataclass
class ValidationResult:
    ok: bool
    reason: Optional[str] = None
    needs_privileges: bool = False
    st_dev: Optional[int] = None
    st_ino: Optional[int] = None


class DeletionManager:
    def __init__(self, parent_folders: Sequence[str], folder_names: Sequence[str]):
        self.parent_folders = [normalize_path(p) for p in parent_folders]
        self.folder_names = set(folder_names)

    # -- validation ---------------------------------------------------------
    def validate(self, result: ScanResult) -> ValidationResult:
        """Check that *result* is still a legitimate deletion target."""
        path = normalize_path(result.path)

        # 1. name must be in the search set and a valid folder name
        if path not in (result.path,) and path != normalize_path(result.path):
            pass
        name = os.path.basename(path)
        if name not in self.folder_names and result.name not in self.folder_names:
            return ValidationResult(False, "Name no longer matches the search list")
        if not is_valid_folder_name(name)[0]:
            return ValidationResult(False, "Invalid folder name")

        # 2. target must live inside a saved parent's subtree.  Scanning is
        #    recursive, so a match may be several levels below the parent; the
        #    safety property we need is that it is a *descendant* of a
        #    user-approved parent, never equal to one and never outside them.
        if not is_within(path, self.parent_folders):
            return ValidationResult(
                False, "Target is not inside a saved parent folder"
            )

        # 3. must exist
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return ValidationResult(False, "The folder no longer exists")
        except PermissionError:
            return ValidationResult(False, "Permission denied", needs_privileges=True)
        except OSError as exc:
            return ValidationResult(False, exc.strerror or str(exc))

        # 4. must not be a symlink
        if stat.S_ISLNK(st.st_mode):
            return ValidationResult(False, "Target is a symbolic link; refusing")

        # 5. must still be a directory
        if not stat.S_ISDIR(st.st_mode):
            return ValidationResult(False, "Target is no longer a directory")

        # 6. identity must match scan time (when we captured it)
        if result.st_ino is not None and int(st.st_ino) != int(result.st_ino):
            return ValidationResult(False, "Target changed since scanning (inode)")
        if result.st_dev is not None and int(st.st_dev) != int(result.st_dev):
            return ValidationResult(False, "Target changed since scanning (device)")

        return ValidationResult(True, st_dev=st.st_dev, st_ino=st.st_ino)

    # -- public deletion ----------------------------------------------------
    def delete(
        self,
        result: ScanResult,
        mode: str = "trash",
        confirm_permanent: Optional[Callable[[ScanResult], bool]] = None,
    ) -> DeleteOutcome:
        """Delete a single result.  Never raises for expected failures."""
        mode = mode if mode in ("trash", "permanent") else "trash"
        path = os.fspath(result.path)

        validation = self.validate(result)
        if not validation.ok:
            return DeleteOutcome(path=path, ok=False, error=validation.reason,
                                 needs_privileges=validation.needs_privileges,
                                 mode=mode)

        if mode == "permanent" and not self._recurse_is_safe(path):
            return DeleteOutcome(
                path=path, ok=False, mode=mode,
                error="Refusing to delete: a symbolic link was found inside "
                      "the target directory.",
            )

        if mode == "permanent" and confirm_permanent is not None:
            if not confirm_permanent(result):
                return DeleteOutcome(path=path, ok=False, mode=mode,
                                     error="Cancelled by user")

        if mode == "trash":
            try:
                trash_result = move_to_trash(path)
            except TrashUnavailable as exc:
                return DeleteOutcome(path=path, ok=False, mode=mode,
                                     error=str(exc))
            if trash_result.ok:
                return DeleteOutcome(path=path, ok=True, mode=mode)
            return DeleteOutcome(
                path=path, ok=False, mode=mode, error=trash_result.error,
                needs_privileges=permissions.is_permission_error(
                    PermissionError(trash_result.error or "")),
            )

        # permanent
        try:
            shutil.rmtree(path, ignore_errors=False)
        except PermissionError:
            return DeleteOutcome(path=path, ok=False, mode=mode,
                                 error="Permission denied",
                                 needs_privileges=True)
        except FileNotFoundError:
            return DeleteOutcome(path=path, ok=False, mode=mode,
                                 error="The folder no longer exists")
        except OSError as exc:
            return DeleteOutcome(path=path, ok=False, mode=mode,
                                 error=exc.strerror or str(exc))
        return DeleteOutcome(path=path, ok=True, mode=mode)

    def delete_many(
        self,
        results: Sequence[ScanResult],
        mode: str = "trash",
        progress: Optional[Callable[[int, int, ScanResult], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        confirm_permanent: Optional[Callable[[ScanResult], bool]] = None,
    ) -> List[DeleteOutcome]:
        """Delete several results, attempting each independently."""
        outcomes: List[DeleteOutcome] = []
        total = len(results)
        for index, result in enumerate(results):
            if should_stop is not None and should_stop():
                break
            if progress is not None:
                progress(index, total, result)
            outcomes.append(self.delete(result, mode=mode,
                                        confirm_permanent=confirm_permanent))
        if progress is not None:
            try:
                progress(total, total, results[-1] if results else None)  # type: ignore
            except Exception:
                pass
        return outcomes

    # -- privileged retry ---------------------------------------------------
    def delete_privileged(self, result: ScanResult, recursive: bool = True):
        """Retry a single deletion through the pkexec helper."""
        validation = self.validate(result)
        # Even if validation failed only due to permissions, we still need a
        # sane path; recompute identity from lstat where possible.
        dev = result.st_dev
        ino = result.st_ino
        if validation.ok:
            dev = validation.st_dev
            ino = validation.st_ino
        return permissions.run_helper(
            permissions.OP_DELETE,
            os.fspath(result.path),
            dev,
            ino,
            recursive=recursive,
        )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _recurse_is_safe(path: str) -> bool:
        """Return False if any symlink is found inside *path*.

        Only callers using permanent recursive deletion use this.  It is a
        conservative guard: rather than risk a race we simply refuse to
        recursively delete trees that contain symlinks through this code path.
        """
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                full = os.path.join(root, name)
                try:
                    if os.path.islink(full):
                        return False
                except OSError:
                    return False
        return True
