"""Data model for a matching folder discovered by a scan.

The GUI uses :class:`ResultTableModel` (a ``QAbstractTableModel``) so the
results view stays responsive even with many rows and can be sorted.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from .utils import human_size


class SizeState(str, Enum):
    PENDING = "Calculating..."
    DONE = "Ready"
    PARTIAL = "Partial"
    UNKNOWN = "Unknown"


class RowStatus(str, Enum):
    OK = "Ready"
    DELETING = "Deleting..."
    DELETED = "Deleted"
    ERROR = "Error"
    PROTECTED = "Permission denied"


@dataclass
class ScanResult:
    """A single matching directory found during a scan."""

    name: str
    parent: str           # the parent folder the user selected
    path: str             # full path to the matching directory
    size: Optional[int] = None
    size_state: SizeState = SizeState.PENDING
    status: RowStatus = RowStatus.OK
    status_detail: str = ""
    # identity captured at scan time for TOCTOU checks
    st_dev: Optional[int] = None
    st_ino: Optional[int] = None
    selected: bool = True
    error: Optional[str] = None
    # ecosystem detection (optional; populated by project-aware scanning)
    ecosystem_id: Optional[str] = None
    ecosystem_label: str = ""
    ecosystem_confidence: str = ""
    ecosystem_ambiguous: bool = False
    ecosystem_detail: str = ""
    # why a candidate was rejected/accepted under project-aware policy
    policy_reason: str = ""

    @property
    def size_text(self) -> str:
        if self.size_state == SizeState.PENDING:
            return SizeState.PENDING.value
        return human_size(self.size)

    @property
    def ecosystem_text(self) -> str:
        if not self.ecosystem_id:
            return "—"
        if self.ecosystem_ambiguous:
            return f"{self.ecosystem_label} (ambiguous)"
        return self.ecosystem_label

    @property
    def status_text(self) -> str:
        if self.status == RowStatus.ERROR and self.status_detail:
            return f"{self.status.value}: {self.status_detail}"
        return self.status.value

    def to_public_dict(self) -> dict:
        return {
            "name": self.name,
            "parent": self.parent,
            "path": self.path,
            "size": self.size,
            "status": self.status.value,
            "ecosystem": self.ecosystem_id,
        }


# Column indexes
COL_SELECT = 0
COL_NAME = 1
COL_PARENT = 2
COL_PATH = 3
COL_SIZE = 4
COL_ECOSYSTEM = 5
COL_STATUS = 6
COL_ACTIONS = 7

HEADERS = [
    "",
    "Folder Name",
    "Parent Folder",
    "Full Path",
    "Folder Size",
    "Ecosystem",
    "Status",
    "Actions",
]


class ResultTableModel(QAbstractTableModel):
    """Table model backed by a list of :class:`ScanResult`."""

    def __init__(self, results: Optional[List[ScanResult]] = None, parent=None):
        super().__init__(parent)
        self._results: List[ScanResult] = list(results or [])

    # -- Qt API -------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._results)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if 0 <= section < len(HEADERS):
                return HEADERS[section]
            return None
        return section + 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._results):
            return None
        r = self._results[row]

        if role == Qt.CheckStateRole and col == COL_SELECT:
            return Qt.Checked if r.selected else Qt.Unchecked

        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            if col == COL_NAME:
                return r.name
            if col == COL_PARENT:
                return r.parent
            if col == COL_PATH:
                return r.path
            if col == COL_SIZE:
                return r.size_text
            if col == COL_ECOSYSTEM:
                return r.ecosystem_text
            if col == COL_STATUS:
                return r.status_text
            if col == COL_ACTIONS:
                return "Delete"
            return None

        if role == Qt.UserRole:
            return r

        if role == Qt.TextAlignmentRole and col == COL_SIZE:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_SELECT:
            return base | Qt.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.EditRole):  # noqa: N802
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == COL_SELECT:
            self._results[index.row()].selected = (
                Qt.CheckState(value) == Qt.Checked
            )
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    # -- convenience --------------------------------------------------------
    @property
    def results(self) -> List[ScanResult]:
        return self._results

    def set_results(self, results: List[ScanResult]) -> None:
        self.beginResetModel()
        self._results = list(results)
        self.endResetModel()

    def clear(self) -> None:
        """Clear the model only.  Never touches the filesystem."""
        self.beginResetModel()
        self._results = []
        self.endResetModel()

    def append_results(self, results: List[ScanResult]) -> None:
        if not results:
            return
        start = len(self._results)
        self.beginInsertRows(QModelIndex(), start, start + len(results) - 1)
        self._results.extend(results)
        self.endInsertRows()

    def get_result(self, row: int) -> Optional[ScanResult]:
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    def update_size(self, path: str, size: Optional[int], state: SizeState) -> None:
        for row, r in enumerate(self._results):
            if r.path == path:
                r.size = size
                r.size_state = state
                left = self.index(row, COL_SIZE)
                right = self.index(row, COL_SIZE)
                self.dataChanged.emit(left, right)
                return

    def set_row_status(self, row: int, status: RowStatus, detail: str = "") -> None:
        if 0 <= row < len(self._results):
            r = self._results[row]
            r.status = status
            r.status_detail = detail
            idx = self.index(row, COL_STATUS)
            self.dataChanged.emit(idx, idx)

    def remove_row(self, row: int) -> None:
        if 0 <= row < len(self._results):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._results[row]
            self.endRemoveRows()

    def remove_paths(self, paths: List[str]) -> None:
        pathset = set(paths)
        self.beginResetModel()
        self._results = [r for r in self._results if r.path not in pathset]
        self.endResetModel()

    def select_all(self, selected: bool) -> None:
        if not self._results:
            return
        for r in self._results:
            r.selected = selected
        top = self.index(0, COL_SELECT)
        bottom = self.index(len(self._results) - 1, COL_SELECT)
        self.dataChanged.emit(top, bottom, [Qt.CheckStateRole])

    def selected_results(self) -> List[ScanResult]:
        return [r for r in self._results if r.selected]

    def total_size(self) -> int:
        return sum(r.size for r in self._results if r.size)
