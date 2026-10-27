"""Confirmation, error and About dialogs.

Keeping these in one module makes the wording of destructive confirmations
consistent and easy to audit.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from . import APP_DISPLAY_NAME, APP_VERSION
from .models import ScanResult
from .utils import human_size, shorten_path


def _mode_label(mode: str) -> str:
    return "Move to Trash" if mode == "trash" else "Permanently delete"


def _style_button(button, role: str) -> None:
    """Tag a dialog button so the app stylesheet renders it semantically.

    ``role`` is one of ``primary``, ``danger`` or ``ghost``.
    """
    if role == "primary":
        button.setObjectName("Primary")
    elif role == "danger":
        button.setObjectName("Danger")
    else:
        button.setObjectName("Ghost")
    # Re-polish so the stylesheet is applied after the object name changes.
    try:
        button.style().unpolish(button)
        button.style().polish(button)
    except Exception:
        pass


def confirm_single_delete(parent: QWidget, result: ScanResult, mode: str) -> bool:
    """Confirmation for deleting one folder.  Trash is the safe default."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Confirm deletion")
    box.setText(f"{_mode_label(mode)} this folder?")
    box.setInformativeText(
        f"<b>Name:</b> {result.name}<br>"
        f"<b>Path:</b> {result.path}<br>"
        f"<b>Size:</b> {result.size_text}"
    )
    if mode == "permanent":
        box.setInformativeText(
            box.informativeText()
            + "<br><br><b>This cannot be undone.</b> The folder will be "
              "permanently removed from disk."
        )
        delete_btn = box.addButton("Permanently Delete", QMessageBox.DestructiveRole)
        _style_button(delete_btn, "danger")
    else:
        delete_btn = box.addButton("Move to Trash", QMessageBox.AcceptRole)
        _style_button(delete_btn, "primary")
    cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
    _style_button(cancel_btn, "ghost")
    box.setDefaultButton(cancel_btn)
    box.exec()
    return box.clickedButton() is delete_btn


def confirm_bulk_delete(parent: QWidget, results: Sequence[ScanResult],
                        mode: str) -> bool:
    """Confirmation for deleting multiple folders."""
    count = len(results)
    total = sum(r.size for r in results if r.size)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Confirm bulk deletion")
    box.setText(
        f"{_mode_label(mode)} {count} folder{'s' if count != 1 else ''}?"
    )
    preview = "<br>".join(
        f"&bull; {shorten_path(r.path, 90)}" for r in list(results)[:15]
    )
    more = ""
    if count > 15:
        more = f"<br>&hellip; and {count - 15} more"
    extra = ""
    if mode == "permanent":
        extra = "<br><br><b>This cannot be undone.</b>"
    box.setInformativeText(
        f"Total size: <b>{human_size(total) if total else 'Unknown'}</b><br><br>"
        f"{preview}{more}{extra}"
    )
    if mode == "permanent":
        delete_btn = box.addButton("Permanently Delete All",
                                   QMessageBox.DestructiveRole)
        _style_button(delete_btn, "danger")
    else:
        delete_btn = box.addButton("Move All to Trash", QMessageBox.AcceptRole)
        _style_button(delete_btn, "primary")
    cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
    _style_button(cancel_btn, "ghost")
    box.setDefaultButton(cancel_btn)
    box.exec()
    return box.clickedButton() is delete_btn


def confirm_clear_results(parent: QWidget) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Clear results")
    box.setText("Clear the results table?")
    box.setInformativeText(
        "This only removes the rows from the table. <b>No files or folders "
        "are deleted from disk.</b>"
    )
    yes = box.addButton("Clear Table", QMessageBox.AcceptRole)
    _style_button(yes, "primary")
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    _style_button(cancel, "ghost")
    box.exec()
    return box.clickedButton() is yes


def confirm_clear_parents(parent: QWidget) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Clear saved locations")
    box.setText("Remove all saved parent folders from the list?")
    box.setInformativeText(
        "This only clears the saved list. It does not delete anything from "
        "disk and does not affect the current results."
    )
    yes = box.addButton("Clear All", QMessageBox.DestructiveRole)
    _style_button(yes, "danger")
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    _style_button(cancel, "ghost")
    box.exec()
    return box.clickedButton() is yes


def confirm_privilege_retry(parent: QWidget, operation: str, path: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Administrator privileges required")
    box.setText(
        f"The <b>{operation}</b> operation failed because administrator "
        "privileges are required."
    )
    box.setInformativeText(
        f"<b>Path:</b> {path}<br><br>"
        "Retry this single operation with administrator privileges? A system "
        "authentication prompt will appear. Your password is handled by the "
        "operating system, not by this application."
    )
    yes = box.addButton("Retry with privileges", QMessageBox.AcceptRole)
    _style_button(yes, "primary")
    skip = box.addButton("Skip", QMessageBox.RejectRole)
    _style_button(skip, "ghost")
    box.exec()
    return box.clickedButton() is yes


def show_error(parent: QWidget, title: str, message: str,
               detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    box.exec()


def show_info(parent: QWidget, title: str, message: str,
              detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    box.exec()


def show_warning(parent: QWidget, title: str, message: str,
                 detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    box.exec()


class BulkReportDialog(QDialog):
    """Show per-item success/failure for a bulk deletion."""

    def __init__(self, parent: QWidget, outcomes: List, mode: str):
        super().__init__(parent)
        self.setWindowTitle("Deletion report")
        self.setMinimumWidth(580)
        self.setObjectName("GlassDialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        succeeded = [o for o in outcomes if o.ok]
        failed = [o for o in outcomes if not o.ok]

        heading = QLabel(
            f"<h3 style='margin:0'>Deletion report</h3>"
            f"<p style='margin:4px 0 0 0'>"
            f"<b>{len(succeeded)}</b> succeeded, <b>{len(failed)}</b> failed "
            f"({_mode_label(mode)}).</p>"
        )
        heading.setTextFormat(Qt.RichText)
        layout.addWidget(heading)

        if failed:
            lines = []
            for o in failed[:50]:
                lines.append(f"&bull; {shorten_path(o.path, 70)}<br>"
                             f"&nbsp;&nbsp;<i>{o.error}</i>")
            more = ""
            if len(failed) > 50:
                more = f"<br>&hellip; and {len(failed) - 50} more"
            detail = QLabel("<br>".join(lines) + more)
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.RichText)
            detail.setMaximumHeight(320)
            layout.addWidget(detail)
        else:
            layout.addWidget(QLabel("All operations completed successfully."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_DISPLAY_NAME}")
        self.setMinimumWidth(500)
        self.setObjectName("GlassDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title = QLabel(f"<h2 style='margin:0'>{APP_DISPLAY_NAME}</h2>"
                       f"<p style='margin:2px 0 0 0; color:#9fb0cf'>"
                       f"Liquid Glass edition &bull; v{APP_VERSION}</p>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        body = QLabel(
            "<p>Find and remove unnecessary development folders such as "
            "<code>node_modules</code>, <code>vendor</code>, "
            "<code>.venv</code>, <code>__pycache__</code>, "
            "<code>build</code> and <code>dist</code>.</p>"
            "<p>Recursively scans every subdirectory of the parent folders "
            "you select. When a folder matches it is not searched inside, so "
            "nested duplicates are skipped, and symlinked directories are "
            "never followed.</p>"
            "<p>Deletion defaults to moving folders to the Ubuntu Trash. "
            "Permanent deletion requires explicit confirmation.</p>"
            "<p style='color:#9fb0cf'>Built with Python and PySide6. The glass "
            "surfaces use layered translucency and gradients, not a compositor "
            "backdrop blur, so they render consistently on X11 and Wayland.</p>"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        layout.addWidget(body)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            _style_button(close_btn, "primary")
        layout.addWidget(buttons)
