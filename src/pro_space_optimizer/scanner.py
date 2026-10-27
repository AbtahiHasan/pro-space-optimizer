"""Filesystem scanning.

The scanner recursively searches *all* subdirectories of each user-selected
parent folder and reports every directory whose name matches one of the
user-entered folder names.

Pruning semantics (important)
-----------------------------
When a directory matches, it is added to the results and its **contents are
never entered**.  This means a match nested inside an already-matched folder
is *not* reported as a separate result - the matched subtree is pruned at the
moment the match is found.  Traversal continues normally for all directories
outside the matched one.

Traversal is an explicit iterative depth-first walk (an explicit stack), not a
naive recursive function, so very deep trees cannot exhaust the Python call
stack, and pruning is immediate - matched directories are simply never pushed
onto the stack.

Other guarantees
----------------
* Only real directories are returned.  Symlinked directories are never
  followed, so a match can never redirect a later delete outside the selected
  parent.
* Missing or inaccessible directories are reported per parent rather than
  raising or being silently skipped.
* Directory identity (device + inode) is captured at scan time so deletion can
  detect targets that changed between scan and delete (TOCTOU defence).
* Cancellation is cooperative and checked at every level of the walk.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set

from . import ecosystem_registry as eco
from .models import ScanResult, SizeState
from .utils import is_valid_folder_name, normalize_path

log = logging.getLogger(__name__)


class MatchingPolicy(str, Enum):
    """How a candidate folder name is accepted as a result."""

    NAME_ONLY = "name"          # accept any directory whose name matches
    PROJECT_AWARE = "project"   # require project/ecosystem evidence too


@dataclass
class PolicyOptions:
    """Options controlling project-aware matching."""

    policy: MatchingPolicy = MatchingPolicy.NAME_ONLY
    # Minimum detection confidence required to accept a project.
    min_confidence: eco.Confidence = eco.Confidence.MEDIUM
    # Optional ecosystem allow-list.  Empty means "no ecosystem filter".
    ecosystem_filter: Set[str] = field(default_factory=set)
    # When True, a candidate that has a KNOWN dependency-folder name for a
    # detected ecosystem is accepted even if the project confidence is lower.
    use_ecosystem_folders: bool = True

    def ecosystem_allowed(self, eco_id: Optional[str]) -> bool:
        if not self.ecosystem_filter:
            return True
        if eco_id is None:
            return False
        return eco_id in self.ecosystem_filter



@dataclass
class ParentScanReport:
    """Outcome of scanning a single parent folder."""

    parent: str
    matches: List[ScanResult] = field(default_factory=list)
    error: Optional[str] = None
    skipped_symlinks: int = 0
    cancelled: bool = False
    directories_visited: int = 0


@dataclass
class ScanReport:
    """Aggregate outcome of a full scan."""

    parents: List[ParentScanReport] = field(default_factory=list)
    cancelled: bool = False

    @property
    def matches(self) -> List[ScanResult]:
        out: List[ScanResult] = []
        for p in self.parents:
            out.extend(p.matches)
        return out

    @property
    def errors(self) -> List[str]:
        return [f"{p.parent}: {p.error}" for p in self.parents if p.error]

    @property
    def skipped_symlinks(self) -> int:
        return sum(p.skipped_symlinks for p in self.parents)


class Scanner:
    """Recursive folder scanner with pruning of matched directories."""

    def __init__(self, follow_symlinks: bool = False,
                 options: Optional[PolicyOptions] = None):
        self.follow_symlinks = follow_symlinks
        self.options = options or PolicyOptions()
        # Cache of project detections keyed by directory path.  Detection is
        # pure (depends only on directory contents), so caching is safe within
        # a single scan and avoids re-reading manifests.
        self._detect_cache: Dict[str, eco.Detection] = {}

    # -- public API ---------------------------------------------------------
    def scan(
        self,
        parents: Sequence[str],
        names: Sequence[str],
        should_stop: Optional[Callable[[], bool]] = None,
        progress: Optional[Callable[[int, int, str], None]] = None,
        match_found: Optional[Callable[[ScanResult], None]] = None,
        options: Optional[PolicyOptions] = None,
    ) -> ScanReport:
        """Recursively scan *parents* for directories named in *names*.

        ``should_stop`` is polled cooperatively so a running scan can be
        cancelled.  ``progress(processed, total, current)`` reports parent
        processing (``processed``/``total`` count parent folders, ``current``
        is the directory currently being visited) and ``match_found(result)``
        streams each match as it is discovered.

        When ``options.policy`` is :attr:`MatchingPolicy.PROJECT_AWARE`, a
        candidate must additionally be justified by project/ecosystem evidence;
        rejected candidates are *not pruned* - the walk continues inside them
        so deeper valid matches are still found.
        """
        if options is not None:
            self.options = options
        valid_names = {n for n in names if is_valid_folder_name(n)[0]}
        report = ScanReport()
        total = len(parents)

        if should_stop is None:
            should_stop = lambda: False  # noqa: E731

        def emit(match: ScanResult) -> None:
            if match_found is not None:
                try:
                    match_found(match)
                except Exception:  # pragma: no cover - defensive
                    log.exception("match_found callback failed")

        def report_progress(processed: int, current: str) -> None:
            if progress is not None:
                try:
                    progress(processed, total, current)
                except Exception:  # pragma: no cover - defensive
                    log.exception("progress callback failed")

        for index, raw_parent in enumerate(parents):
            if should_stop():
                report.cancelled = True
                break

            parent = normalize_path(raw_parent)
            report_progress(index, parent)

            parent_report = ParentScanReport(parent=parent)
            self._scan_parent(
                parent, valid_names, should_stop, parent_report, emit,
                report_progress, index,
            )
            report.parents.append(parent_report)

            if parent_report.cancelled or should_stop():
                report.cancelled = True
                break

        report_progress(total, "")
        return report

    # -- internals ----------------------------------------------------------
    def _scan_parent(
        self,
        parent: str,
        names: Set[str],
        should_stop: Callable[[], bool],
        pr: ParentScanReport,
        emit: Callable[[ScanResult], None],
        report_progress: Callable[[int, str], None],
        parent_index: int,
    ) -> None:
        """Recursively walk *parent*, populating *pr*.

        Uses an explicit stack.  A directory that matches *names* is recorded
        and its subtree is pruned (never pushed), so nested matches inside a
        matched directory are not reported.
        """
        p = Path(parent)

        try:
            p.stat()
        except FileNotFoundError:
            pr.error = "Folder does not exist"
            return
        except PermissionError:
            pr.error = "Permission denied"
            return
        except OSError as exc:
            pr.error = f"Cannot access folder: {exc.strerror or exc}"
            return

        if not p.is_dir():
            pr.error = "Not a directory"
            return

        # The stack holds directories still to be descended into.  We start
        # with the parent itself; the parent is never itself a match.
        stack: List[str] = [parent]

        while stack:
            if should_stop():
                pr.cancelled = True
                return

            current = stack.pop()
            pr.directories_visited += 1
            report_progress(parent_index, current)

            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if should_stop():
                            pr.cancelled = True
                            return

                        name = entry.name

                        # Reject symlinks entirely: they are never followed,
                        # so a match can never escape the selected parent and
                        # a link loop can never trap the walk.
                        try:
                            if entry.is_symlink():
                                if name in names:
                                    pr.skipped_symlinks += 1
                                continue
                        except OSError:
                            continue

                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        except PermissionError:
                            if name in names:
                                # We cannot inspect it; report it with a note
                                # so the user is not silently left unaware.
                                match = self._make_match(
                                    name, parent, entry.path, None, None,
                                    error="Permission denied while inspecting "
                                          "folder",
                                )
                                pr.matches.append(match)
                                emit(match)
                            continue
                        except OSError:
                            continue

                        if not is_dir:
                            continue

                        if name in names:
                            # Candidate.  Under NAME_ONLY it is accepted
                            # immediately; under PROJECT_AWARE it must be
                            # justified by project/ecosystem evidence.
                            accepted, detection, reason = self._evaluate_candidate(
                                entry.path, name, current
                            )
                            if accepted:
                                # Accept: record it and PRUNE - do not descend.
                                try:
                                    lst = entry.stat(follow_symlinks=False)
                                    dev = getattr(lst, "st_dev", None)
                                    ino = getattr(lst, "st_ino", None)
                                except OSError:
                                    dev = ino = None
                                match = self._make_match(
                                    name, parent, entry.path, dev, ino,
                                    detection=detection, reason=reason,
                                )
                                pr.matches.append(match)
                                emit(match)
                                continue
                            # Rejected by policy: DO NOT prune; keep walking
                            # inside so deeper valid matches are still found.
                            stack.append(entry.path)
                            continue

                        # Not a match: descend into it later.
                        stack.append(entry.path)

            except FileNotFoundError:
                # The directory disappeared between listing and scanning.
                continue
            except PermissionError:
                # Cannot list this directory; record it once, keep going.
                if current == parent:
                    pr.error = "Permission denied while listing folder"
                else:
                    log.debug("Permission denied listing %s", current)
                continue
            except OSError as exc:
                if current == parent:
                    pr.error = f"Cannot list folder: {exc.strerror or exc}"
                else:
                    log.debug("Cannot list %s: %s", current, exc)
                continue

        return

    # -- project-aware matching --------------------------------------------
    def _detect_project(self, path: str) -> eco.Detection:
        """Detect the ecosystem of a directory, with per-scan caching."""
        cached = self._detect_cache.get(path)
        if cached is None:
            try:
                cached = eco.detect(path)
            except Exception:  # pragma: no cover - defensive
                log.exception("detection failed for %s", path)
                cached = eco.Detection(None, "Unknown", None)
            self._detect_cache[path] = cached
        return cached

    def _evaluate_candidate(self, candidate_path: str, name: str,
                            containing_dir: str):
        """Decide whether a name-matching candidate should be accepted.

        Returns ``(accepted, detection, reason)``.

        NAME_ONLY accepts every candidate.  PROJECT_AWARE accepts a candidate
        when the *containing directory* is a recognised project (confidence at
        or above ``min_confidence``) or, when enabled, when the candidate's
        folder name is a known dependency folder for the detected ecosystem.
        Rejected candidates are reported with a human-readable reason so the
        UI can explain ambiguous classifications.
        """
        opts = self.options
        if opts.policy == MatchingPolicy.NAME_ONLY:
            return True, None, "name match"

        detection = self._detect_project(containing_dir)

        if not detection.known:
            return (
                False,
                None,
                "no recognised project in the containing folder",
            )

        # Ecosystem allow-list (user filter).
        if not opts.ecosystem_allowed(detection.ecosystem_id):
            return (
                False,
                detection,
                f"ecosystem '{detection.label}' is filtered out",
            )

        rank = eco._CONFIDENCE_RANK
        meets = rank[detection.confidence] >= rank[opts.min_confidence]

        # The candidate's folder name must be a *known* dependency/generated
        # folder for the detected ecosystem (or a generic, ecosystem-agnostic
        # folder such as build/dist).  A recognised project alone does not
        # justify an arbitrary matched name, so `vendor` in a Node project is
        # rejected while `node_modules` is accepted.
        name_justified = False
        name_is_known = False
        for rule in eco.folder_rules():
            if rule.name != name:
                continue
            name_is_known = True
            if not rule.ecosystems:
                # generic folder (build/dist/...) - justified by the project
                name_justified = True
                break
            if detection.ecosystem_id in rule.ecosystems:
                name_justified = True
                break
            # A framework's dependency folder also justifies the language
            # container (e.g. node_modules for a Next.js project).
            matched_ids = set(detection.matched)
            if matched_ids & set(rule.ecosystems):
                name_justified = True
                break
            break

        if not opts.use_ecosystem_folders:
            # Ecosystem-folder checking disabled: fall back to the pure
            # project-confidence gate.
            if meets:
                reason = f"project: {detection.label}"
                if detection.confidence is not None:
                    reason += f" ({detection.confidence.value})"
                if detection.ambiguous:
                    reason += " [ambiguous]"
                return True, detection, reason
            return (
                False,
                detection,
                f"project '{detection.label}' below required confidence",
            )

        if not name_is_known:
            # Unknown folder name: not something the ecosystem registry can
            # justify, so project-aware mode declines it.
            return (
                False,
                detection,
                f"'{name}' is not a known dependency folder for "
                f"'{detection.label}'",
            )

        if name_justified and meets:
            reason = f"project: {detection.label}"
            if detection.confidence is not None:
                reason += f" ({detection.confidence.value})"
            if detection.ambiguous:
                reason += " [ambiguous]"
            return True, detection, reason

        if name_justified and not meets:
            return (
                False,
                detection,
                f"project '{detection.label}' below required confidence",
            )

        return (
            False,
            detection,
            f"'{name}' does not belong to '{detection.label}'",
        )

    @staticmethod
    def _make_match(name: str, parent: str, path: str,
                    st_dev: Optional[int], st_ino: Optional[int],
                    error: Optional[str] = None,
                    detection: Optional[eco.Detection] = None,
                    reason: str = "") -> ScanResult:
        result = ScanResult(
            name=name,
            parent=parent,
            path=os.fspath(path),
            size_state=SizeState.UNKNOWN if error else SizeState.PENDING,
            st_dev=st_dev,
            st_ino=st_ino,
            error=error,
            policy_reason=reason,
        )
        if detection is not None:
            result.ecosystem_id = detection.ecosystem_id
            result.ecosystem_label = detection.label
            result.ecosystem_ambiguous = detection.ambiguous
            result.ecosystem_detail = detection.detail
            if detection.confidence is not None:
                result.ecosystem_confidence = detection.confidence.value
        return result


def scan_parents(
    parents: Sequence[str],
    names: Sequence[str],
    options: Optional[PolicyOptions] = None,
    **kwargs,
) -> ScanReport:
    """Convenience wrapper around :class:`Scanner`."""
    return Scanner(options=options).scan(parents, names, **kwargs)
