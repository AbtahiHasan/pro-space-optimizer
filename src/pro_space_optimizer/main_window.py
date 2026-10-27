"""Main application window: header, sidebar navigation and pages."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_DISPLAY_NAME, APP_VERSION
from .app import app_icon, apply_theme, resolved_theme
from . import theme as liquid
from .deletion import DeletionManager, DeleteOutcome
from .dialogs import (
    AboutDialog,
    BulkReportDialog,
    confirm_bulk_delete,
    confirm_clear_parents,
    confirm_clear_results,
    confirm_privilege_retry,
    confirm_single_delete,
    show_error,
    show_info,
    show_warning,
)
from .models import (
    COL_ACTIONS,
    COL_ECOSYSTEM,
    COL_NAME,
    COL_PARENT,
    COL_PATH,
    COL_SELECT,
    COL_SIZE,
    COL_STATUS,
    ResultTableModel,
    RowStatus,
    ScanResult,
)
from . import ecosystem_registry as eco
from .permissions import pkexec_available
from .scanner import MatchingPolicy, PolicyOptions
from .settings import Settings
from .trash_manager import trash_available
from .utils import (
    human_size,
    is_valid_folder_name,
    normalize_path,
    parse_folder_names,
    shorten_path,
)
from .workers import DeleteWorker, ScanWorker, SizeWorker, ThreadController

log = logging.getLogger(__name__)

PAGE_SCANNER = 0
PAGE_LOCATIONS = 1
PAGE_SETTINGS = 2
PAGE_ABOUT = 3


class RootSurface(QWidget):
    """The window background: a soft gradient with ambient light blooms.

    This is what gives the glass panels something to sit on top of.  It is
    pure QPainter work (radial-ish gradients), so it needs no compositor
    support and renders identically on X11 and Wayland.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RootSurface")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._palette = liquid.DARK

    def set_palette(self, palette) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()

        # ambient glow, top-left
        g1 = QLinearGradient(r.topLeft(), r.center())
        g1.setColorAt(0.0, liquid.qc(p.glow_a))
        g1.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(r, g1)

        # ambient glow, bottom-right
        g2 = QLinearGradient(r.bottomRight(), r.center())
        g2.setColorAt(0.0, liquid.qc(p.glow_b))
        g2.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(r, g2)
        painter.end()


class Card(QFrame):
    """A titled glass panel with consistent padding and a soft shadow."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None,
                 accent: bool = False):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        if title:
            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            if accent:
                dot = QFrame()
                dot.setFixedSize(8, 8)
                dot.setStyleSheet(
                    f"background: {liquid.DARK.accent}; border-radius: 4px;"
                )
                self._accent_dot = dot
                title_row.addWidget(dot)
            header = QLabel(title)
            header.setObjectName("CardTitle")
            self._title_label = header
            title_row.addWidget(header)
            title_row.addStretch(1)
            layout.addLayout(title_row)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("SectionHint")
            sub.setWordWrap(True)
            self._sub_label = sub
            layout.addWidget(sub)
        self.body = layout
        liquid.add_shadow(self, blur=30, y=8, alpha=150)

    def set_accent_color(self, color: str) -> None:
        if hasattr(self, "_accent_dot"):
            self._accent_dot.setStyleSheet(
                f"background: {color}; border-radius: 4px;"
            )

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class GlassRowDelegate(QStyledItemDelegate):
    """Paints results rows with soft, alternating translucent surfaces."""

    delete_clicked = Signal(int)

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._palette = palette

    def set_palette(self, palette) -> None:
        self._palette = palette

    def paint(self, painter, option, index):
        from PySide6.QtWidgets import QStyle

        p = self._palette
        # Let the base class handle the text unless we are in the Actions
        # column, which gets a custom pill button.
        if index.column() != COL_ACTIONS:
            opt = option
            painter.save()
            # Paint our own row background first.
            row_bg = (liquid.opaque(p.glass, p.window) if (index.row() % 2 == 0)
                      else liquid.qc(p.window_alt))
            if option.state & QStyle.State_Selected:
                row_bg = liquid.qc(p.accent_soft)
            painter.fillRect(option.rect, row_bg)
            painter.restore()
            super().paint(painter, opt, index)
            return

        # Actions column: draw a restrained danger pill.
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = option.rect
        row_bg = (liquid.opaque(p.glass, p.window) if (index.row() % 2 == 0)
                      else liquid.qc(p.window_alt))
        if option.state & QStyle.State_Selected:
            row_bg = liquid.qc(p.accent_soft)
        painter.fillRect(r, row_bg)

        rect = self._button_rect(option)
        path = QPainterPath()
        path.addRoundedRect(rect, liquid.RADIUS_SM, liquid.RADIUS_SM)
        enabled = bool(option.state & QStyle.State_Enabled)
        if enabled:
            painter.fillPath(path, QColor(240, 122, 114, 46))
            painter.setPen(QColor(240, 122, 114, 190))
        else:
            painter.fillPath(path, liquid.opaque(p.glass, p.window))
            painter.setPen(liquid.qc(p.border))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(liquid.qc("#f79289") if enabled else liquid.qc(p.text_faint))
        painter.drawText(rect, Qt.AlignCenter, "Delete")
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 34))
        return s

    def _button_rect(self, option):
        from PySide6.QtCore import QRect
        r = option.rect
        margin = 7
        return QRect(r.x() + margin, r.y() + margin,
                     max(r.width() - 2 * margin, 40),
                     r.height() - 2 * margin)

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if index.column() != COL_ACTIONS:
            return False
        if event.type() == QEvent.MouseButtonRelease:
            if self._button_rect(option).contains(event.pos()):
                self.delete_clicked.emit(index.row())
                return True
        return False


class SidebarItemDelegate(QStyledItemDelegate):
    """Paints navigation items with a centered icon and vertically centered
    text, plus a rounded selection pill and accent bar that never shift the
    content (which a stylesheet ``border-left`` would)."""

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._palette = palette

    def set_palette(self, palette) -> None:
        self._palette = palette

    def paint(self, painter, option, index):
        from PySide6.QtCore import QRect, QSize
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QStyle

        p = self._palette
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        # Start from a clean, opaque row background so no artifact shows
        # through the icon's transparent pixels.  We blend the translucent
        # glass over the window colour to get an opaque equivalent, which
        # renders correctly even where compositing is unavailable.
        painter.fillRect(rect, liquid.opaque(p.glass, p.window))

        # Background pill, inset a little so the left accent bar fits.
        inset = 4
        pill = QRect(rect.x() + inset, rect.y() + 2,
                     rect.width() - 2 * inset, rect.height() - 4)
        path = QPainterPath()
        path.addRoundedRect(pill, liquid.RADIUS_MD, liquid.RADIUS_MD)
        if selected:
            grad = QLinearGradient(pill.topLeft(), pill.topRight())
            grad.setColorAt(0.0, liquid.qc(p.accent_soft))
            grad.setColorAt(1.0, liquid.opaque(p.glass_active, p.window))
            painter.fillPath(path, grad)
        elif hovered:
            painter.fillPath(path, liquid.opaque(p.glass_hover, p.window))

        # Accent bar on the left for the active item.
        if selected:
            bar = QRect(pill.x() + 3, pill.y() + 8, 3, pill.height() - 16)
            bar_path = QPainterPath()
            bar_path.addRoundedRect(bar, 1.5, 1.5)
            painter.fillPath(bar_path, liquid.qc(p.accent))

        colour = liquid.qc(p.text) if (selected or hovered) else liquid.qc(p.text_muted)

        # Icon, vertically centered.
        icon = index.data(Qt.DecorationRole)
        content_x = pill.x() + 14
        icon_size = QSize(18, 18)
        if icon is not None and not icon.isNull():
            pix = icon.pixmap(icon_size)
            iy = rect.y() + (rect.height() - pix.height()) // 2
            painter.drawPixmap(content_x, iy, pix)
            content_x += pix.width() + 10

        # Text, vertically centered.
        font = QFont(painter.font())
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(colour)
        text = index.data(Qt.DisplayRole) or ""
        text_rect = QRect(content_x, rect.y(),
                          pill.right() - content_x - 6, rect.height())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(0, 44)


class DeleteButtonDelegate(GlassRowDelegate):
    """Backwards-compatible alias for the glass row delegate."""


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or Settings()

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1100, 720)
        self.setMinimumSize(900, 600)

        self._parents: List[str] = self.settings.parent_folders()
        self._scan_results: List[ScanResult] = []
        self._scan_controller = ThreadController()
        self._size_controller = ThreadController()
        self._delete_controller = ThreadController()
        self._scan_active = False
        self._size_active = False
        self._delete_active = False
        self._palette = liquid.palette_for(resolved_theme(self.settings.theme()))

        self.model = ResultTableModel([])

        self._build_ui()
        self._wire_shortcuts()
        self._restore_geometry()
        self._refresh_parents_list()
        self._update_bulk_counts()
        self._update_scan_status_idle()
        self._update_policy_hint()
        self._on_nav_changed(self.nav.currentRow())

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        self.root_surface = RootSurface()
        self.root_surface.set_palette(self._palette)
        self.setCentralWidget(self.root_surface)
        root = QVBoxLayout(self.root_surface)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")
        self.pages.addWidget(self._build_scanner_page())
        self.pages.addWidget(self._build_locations_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_about_page())
        body.addWidget(self.pages, 1)

        root.addLayout(body, 1)
        self.statusBar().showMessage("Ready")

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setFixedHeight(74)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(14)

        icon_label = QLabel()
        icon_label.setPixmap(app_icon().pixmap(44, 44))
        layout.addWidget(icon_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self.page_title = QLabel(APP_DISPLAY_NAME)
        self.page_title.setObjectName("HeaderTitle")
        self.page_subtitle = QLabel(
            "Find and remove unnecessary development folders"
        )
        self.page_subtitle.setObjectName("HeaderSubtitle")
        text_box.addWidget(self.page_title)
        text_box.addWidget(self.page_subtitle)
        layout.addLayout(text_box)

        layout.addStretch(1)

        self.status_pill = QLabel("Idle")
        self.status_pill.setObjectName("StatusPill")
        self._set_pill_state("idle")
        layout.addWidget(self.status_pill)
        return header

    def _set_pill_state(self, state: str) -> None:
        """Set the status pill's semantic state (idle/busy/ok/error)."""
        self.status_pill.setProperty("state", state)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setAttribute(Qt.WA_StyledBackground, True)
        sidebar.setFixedWidth(224)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 16)
        layout.setSpacing(14)

        # -- branding block --
        brand = QHBoxLayout()
        brand.setSpacing(12)
        brand_icon = QLabel()
        brand_icon.setFixedSize(34, 34)
        brand_icon.setPixmap(app_icon().pixmap(30, 30))
        brand_icon.setScaledContents(True)
        brand_icon.setAlignment(Qt.AlignCenter)
        brand.addWidget(brand_icon, 0, Qt.AlignVCenter)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_title = QLabel("Folder Cleaner")
        brand_title.setObjectName("BrandTitle")
        brand_sub = QLabel("Pro Space Optimizer")
        brand_sub.setObjectName("BrandSubtitle")
        brand_text.addWidget(brand_title)
        brand_text.addWidget(brand_sub)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)

        divider = QFrame()
        divider.setObjectName("Divider")
        layout.addWidget(divider)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFocusPolicy(Qt.StrongFocus)
        self.nav.setUniformItemSizes(True)
        self.nav.setSpacing(2)
        self.nav.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._nav_delegate = SidebarItemDelegate(self._palette, self.nav)
        self.nav.setItemDelegate(self._nav_delegate)
        entries = [
            ("Scanner", QStyle.SP_FileDialogContentsView),
            ("Saved Locations", QStyle.SP_DirIcon),
            ("Settings", QStyle.SP_FileDialogDetailedView),
            ("About", QStyle.SP_MessageBoxInformation),
        ]
        for text, icon in entries:
            item = QListWidgetItem(self.style().standardIcon(icon), text)
            item.setSizeHint(QSize(0, 44))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(PAGE_SCANNER)
        layout.addWidget(self.nav)
        layout.addStretch(1)

        version = QLabel(f"Liquid Glass \u2022 v{APP_VERSION}")
        version.setObjectName("BrandSubtitle")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)
        return sidebar

    # -- Scanner page -------------------------------------------------------
    def _build_scanner_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(liquid.SPACE_LG)

        top = QHBoxLayout()
        top.setSpacing(liquid.SPACE_LG)
        top.addWidget(self._build_parents_card(), 1)
        top.addWidget(self._build_names_card(), 1)
        outer.addLayout(top)

        outer.addWidget(self._build_scan_controls_card())
        outer.addWidget(self._build_results_card(), 1)
        return page

    def _build_parents_card(self) -> QWidget:
        card = Card("Parent Folders",
                    "All subdirectories of these locations are scanned "
                    "recursively. Matched folders are pruned (not scanned "
                    "inside).", accent=True)
        self.parents_list = QListWidget()
        self.parents_list.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        self.parents_list.setMinimumHeight(132)
        self.parents_list.setIconSize(QSize(18, 18))
        card.add(self.parents_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(liquid.SPACE_SM)
        self.btn_add_folder = QPushButton("  Add Folder")
        self.btn_add_folder.setObjectName("Primary")
        self.btn_add_folder.setIcon(
            self.style().standardIcon(QStyle.SP_FileDialogNewFolder)
        )
        self.btn_add_folder.clicked.connect(self.add_parent_folder)
        self.btn_remove_folder = QPushButton("Remove")
        self.btn_remove_folder.setObjectName("Ghost")
        self.btn_remove_folder.clicked.connect(self.remove_selected_parents)
        self.btn_clear_folders = QPushButton("Clear All")
        self.btn_clear_folders.setObjectName("Ghost")
        self.btn_clear_folders.clicked.connect(self.clear_parents)
        btn_row.addWidget(self.btn_add_folder)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_remove_folder)
        btn_row.addWidget(self.btn_clear_folders)
        card.add_layout(btn_row)
        return card

    def _build_names_card(self) -> QWidget:
        card = Card("Folder Names to Find",
                    "Enter names separated by commas or new lines. Matching "
                    "is case-sensitive on Linux. A matched folder is not "
                    "searched inside, so nested duplicates are skipped.",
                    accent=True)
        self.names_edit = QPlainTextEdit()
        self.names_edit.setPlaceholderText(
            "node_modules, vendor, .venv, __pycache__, build, dist"
        )
        self.names_edit.setPlainText(self.settings.folder_names_text())
        self.names_edit.setMinimumHeight(132)
        self.names_edit.textChanged.connect(self._on_names_changed)
        card.add(self.names_edit)

        self.names_hint = QLabel("")
        self.names_hint.setObjectName("SectionHint")
        card.add(self.names_hint)
        self._on_names_changed()
        return card

    def _build_scan_controls_card(self) -> QWidget:
        card = Card("Scan")
        row = QHBoxLayout()
        row.setSpacing(liquid.SPACE_SM)
        self.btn_scan = QPushButton("  Start Scan")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setIcon(
            self.style().standardIcon(QStyle.SP_MediaPlay)
        )
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_stop = QPushButton("  Stop Scan")
        self.btn_stop.setObjectName("Ghost")
        self.btn_stop.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        row.addWidget(self.btn_scan)
        row.addWidget(self.btn_stop)
        row.addStretch(1)

        # Matching policy selector (name-only vs project-aware).
        policy_label = QLabel("Mode:")
        policy_label.setObjectName("SectionHint")
        row.addWidget(policy_label)
        self.policy_combo = QComboBox()
        self.policy_combo.addItem("Name only", "name")
        self.policy_combo.addItem("Project-aware", "project")
        pidx = self.policy_combo.findData(self.settings.match_policy())
        self.policy_combo.setCurrentIndex(pidx if pidx >= 0 else 0)
        self.policy_combo.setToolTip(
            "Name only: match any folder by name.\n"
            "Project-aware: only match folders that are justified by a "
            "recognised project/ecosystem (fewer false positives)."
        )
        self.policy_combo.currentIndexChanged.connect(self._on_policy_changed)
        row.addWidget(self.policy_combo)

        self.lbl_pct = QLabel("")
        self.lbl_pct.setObjectName("SectionHint")
        row.addWidget(self.lbl_pct)
        card.add_layout(row)

        self.lbl_policy_hint = QLabel("")
        self.lbl_policy_hint.setObjectName("SectionHint")
        self.lbl_policy_hint.setWordWrap(True)
        card.add(self.lbl_policy_hint)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        # Smoothly animate value changes for a polished feel.
        self._progress_anim = QPropertyAnimation(self.progress, b"value", self)
        self._progress_anim.setDuration(220)
        self._progress_anim.setEasingCurve(QEasingCurve.OutCubic)
        card.add(self.progress)

        info = QHBoxLayout()
        info.setSpacing(liquid.SPACE_SM)
        self.lbl_current = QLabel("Idle")
        self.lbl_current.setObjectName("SectionHint")
        self.lbl_counts = QLabel("")
        self.lbl_counts.setObjectName("SectionHint")
        info.addWidget(self.lbl_current, 1)
        info.addWidget(self.lbl_counts)
        card.add_layout(info)
        return card

    def _set_progress(self, value: int) -> None:
        """Animate the progress bar to *value* (falls back if animating)."""
        try:
            self._progress_anim.stop()
            self._progress_anim.setStartValue(self.progress.value())
            self._progress_anim.setEndValue(int(value))
            self._progress_anim.start()
        except Exception:
            self.progress.setValue(int(value))

    def _build_results_card(self) -> QWidget:
        card = Card("Results", "")

        # Separate, visually clear bulk-action bar.
        bulk = QFrame()
        bulk.setObjectName("BulkBar")
        self.bulk_bar = bulk
        bulk.setAttribute(Qt.WA_StyledBackground, True)
        bulk.setStyleSheet(
            f"#BulkBar {{ background: {self._palette.glass};"
            f" border: 1px solid {self._palette.border};"
            f" border-radius: {liquid.RADIUS_MD}px; }}"
        )
        bulk_layout = QHBoxLayout(bulk)
        bulk_layout.setContentsMargins(12, 8, 12, 8)
        bulk_layout.setSpacing(liquid.SPACE_SM)

        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setObjectName("Ghost")
        self.btn_select_all.clicked.connect(lambda: self._set_all_selected(True))
        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_deselect_all.setObjectName("Ghost")
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_selected(False))
        self.btn_delete_selected = QPushButton("Delete Selected")
        self.btn_delete_selected.setObjectName("Danger")
        self.btn_delete_selected.clicked.connect(self.delete_selected)
        self.btn_delete_all = QPushButton("Delete All Results")
        self.btn_delete_all.setObjectName("Danger")
        self.btn_delete_all.clicked.connect(self.delete_all_results)
        self.btn_clear_results = QPushButton("Clear Results")
        self.btn_clear_results.setObjectName("Ghost")
        self.btn_clear_results.clicked.connect(self.clear_results)

        bulk_layout.addWidget(self.btn_select_all)
        bulk_layout.addWidget(self.btn_deselect_all)
        bulk_layout.addStretch(1)
        self.lbl_selected = QLabel("0 selected")
        self.lbl_selected.setObjectName("SectionHint")
        bulk_layout.addWidget(self.lbl_selected)
        self.lbl_total_size = QLabel("Total: 0 B")
        self.lbl_total_size.setObjectName("SectionHint")
        bulk_layout.addWidget(self.lbl_total_size)
        bulk_layout.addSpacing(6)
        bulk_layout.addWidget(self.btn_clear_results)
        bulk_layout.addWidget(self.btn_delete_selected)
        bulk_layout.addWidget(self.btn_delete_all)
        card.add(bulk)

        self.table = QTableView()
        self.table.setObjectName("ResultsTable")
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)

        # Glass row delegate applied to every column so rows share one surface.
        self.delete_delegate = GlassRowDelegate(self._palette, self.table)
        self.delete_delegate.delete_clicked.connect(self.delete_row)
        for col in range(COL_ACTIONS + 1):
            self.table.setItemDelegateForColumn(col, self.delete_delegate)

        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(COL_SELECT, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_SELECT, 40)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_NAME, 150)
        header.setSectionResizeMode(COL_PARENT, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_PARENT, 200)
        header.setSectionResizeMode(COL_PATH, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_SIZE, 110)
        header.setSectionResizeMode(COL_ECOSYSTEM, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_ECOSYSTEM, 150)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_STATUS, 140)
        header.setSectionResizeMode(COL_ACTIONS, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_ACTIONS, 96)
        # Honour the user's preference for the ecosystem column.
        self.table.setColumnHidden(
            COL_ECOSYSTEM, not self.settings.show_ecosystem_column()
        )

        self.model.modelReset.connect(self._on_model_changed)
        self.model.rowsInserted.connect(self._on_model_changed)
        self.model.rowsRemoved.connect(self._on_model_changed)
        self.model.dataChanged.connect(self._on_data_changed)

        card.add(self.table)
        self._show_empty_state(True)
        return card

    # -- Locations page -----------------------------------------------------
    def _build_locations_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        card = Card("Saved Locations",
                    "These parent folders are stored between launches. Their "
                    "whole subtree is scanned recursively.")
        self.locations_list = QListWidget()
        self.locations_list.setMinimumHeight(300)
        card.add(self.locations_list)
        row = QHBoxLayout()
        add = QPushButton("Add Folder\u2026")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_parent_folder)
        rem = QPushButton("Remove Selected")
        rem.clicked.connect(self.remove_selected_parents)
        clr = QPushButton("Clear All")
        clr.clicked.connect(self.clear_parents)
        row.addWidget(add)
        row.addWidget(rem)
        row.addWidget(clr)
        row.addStretch(1)
        card.add_layout(row)
        outer.addWidget(card)
        outer.addStretch(1)
        return page

    # -- Settings page ------------------------------------------------------
    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        del_card = Card("Deletion")
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Deletion mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Move to Trash (recommended)", "trash")
        self.mode_combo.addItem("Permanently delete", "permanent")
        idx = self.mode_combo.findData(self.settings.delete_mode())
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        del_card.add_layout(mode_row)

        self.chk_calc_sizes = QCheckBox("Calculate folder sizes after scanning")
        self.chk_calc_sizes.setChecked(self.settings.calculate_sizes())
        self.chk_calc_sizes.toggled.connect(
            lambda v: self.settings.set_calculate_sizes(v)
        )
        del_card.add(self.chk_calc_sizes)

        self.lbl_trash_state = QLabel("")
        self.lbl_trash_state.setObjectName("HeaderSubtitle")
        del_card.add(self.lbl_trash_state)
        outer.addWidget(del_card)

        ui_card = Card("Appearance")
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("System default", "system")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        tidx = self.theme_combo.findData(self.settings.theme())
        self.theme_combo.setCurrentIndex(tidx if tidx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        ui_card.add_layout(theme_row)

        self.chk_ecosystem_col = QCheckBox("Show detected ecosystem column")
        self.chk_ecosystem_col.setChecked(self.settings.show_ecosystem_column())
        self.chk_ecosystem_col.toggled.connect(self._on_ecosystem_col_toggled)
        ui_card.add(self.chk_ecosystem_col)
        outer.addWidget(ui_card)

        outer.addWidget(self._build_detection_card())

        info_card = Card("Environment")
        self.lbl_env = QLabel("")
        self.lbl_env.setWordWrap(True)
        info_card.add(self.lbl_env)
        cfg = QLabel(f"Configuration: {self.settings.settings_path()}")
        cfg.setObjectName("HeaderSubtitle")
        cfg.setWordWrap(True)
        info_card.add(cfg)
        outer.addWidget(info_card)
        outer.addStretch(1)
        self._update_environment_info()
        return page

    def _build_detection_card(self) -> QWidget:
        """Expandable ecosystem-detection settings (kept off the scanner page)."""
        card = Card("Ecosystem Detection",
                    "Project-type detection is evidence-based. It never runs "
                    "project code or installs dependencies.")

        conf_row = QHBoxLayout()
        conf_row.addWidget(QLabel("Minimum confidence:"))
        self.conf_combo = QComboBox()
        self.conf_combo.addItem("Low (most permissive)", "low")
        self.conf_combo.addItem("Medium (recommended)", "medium")
        self.conf_combo.addItem("High (strict)", "high")
        cidx = self.conf_combo.findData(self.settings.min_confidence())
        self.conf_combo.setCurrentIndex(cidx if cidx >= 0 else 1)
        self.conf_combo.currentIndexChanged.connect(self._on_confidence_changed)
        conf_row.addWidget(self.conf_combo)
        conf_row.addStretch(1)
        card.add_layout(conf_row)

        hint = QLabel(
            "Applied only in Project-aware mode. Higher confidence reduces "
            "false positives from weak markers such as a lone Makefile."
        )
        hint.setObjectName("SectionHint")
        hint.setWordWrap(True)
        card.add(hint)

        self._eco_expander = QToolButton()
        self._eco_expander.setText("Ecosystem filter (advanced)")
        self._eco_expander.setCheckable(True)
        self._eco_expander.setChecked(False)
        self._eco_expander.setObjectName("Ghost")
        self._eco_expander.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._eco_expander.setArrowType(Qt.RightArrow)
        self._eco_expander.toggled.connect(self._on_eco_expander_toggled)
        card.add(self._eco_expander)

        self._eco_filter_box = QFrame()
        eco_layout = QVBoxLayout(self._eco_filter_box)
        eco_layout.setContentsMargins(2, 2, 2, 2)
        eco_help = QLabel(
            "Leave all unchecked to allow every ecosystem. If any is checked, "
            "only results belonging to the selected ecosystems are matched "
            "(in Project-aware mode)."
        )
        eco_help.setObjectName("SectionHint")
        eco_help.setWordWrap(True)
        eco_layout.addWidget(eco_help)

        self._eco_filter_list = QListWidget()
        self._eco_filter_list.setMinimumHeight(160)
        self._eco_filter_list.setSelectionMode(
            QAbstractItemView.NoSelection
        )
        self._populate_ecosystem_filter()
        eco_layout.addWidget(self._eco_filter_list)
        self._eco_filter_box.setVisible(False)
        card.add(self._eco_filter_box)

        self.lbl_eco_count = QLabel("")
        self.lbl_eco_count.setObjectName("SectionHint")
        card.add(self.lbl_eco_count)
        self._update_eco_count()
        return card

    def _populate_ecosystem_filter(self) -> None:
        selected = set(self.settings.ecosystem_filter())
        self._eco_filter_list.clear()
        # Sort by category then label for a tidy, grouped list.
        entries = sorted(eco.registry(), key=lambda e: (e.category, e.label))
        for e in entries:
            item = QListWidgetItem(f"{e.label}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if e.id in selected else Qt.Unchecked)
            item.setData(Qt.UserRole, e.id)
            item.setToolTip(f"id: {e.id}  •  {e.category}")
            self._eco_filter_list.addItem(item)
        self._eco_filter_list.itemChanged.connect(self._on_eco_filter_changed)

    def _on_eco_expander_toggled(self, checked: bool) -> None:
        self._eco_filter_box.setVisible(checked)
        self._eco_expander.setArrowType(
            Qt.DownArrow if checked else Qt.RightArrow
        )

    def _on_eco_filter_changed(self, _item) -> None:
        ids: List[str] = []
        for i in range(self._eco_filter_list.count()):
            it = self._eco_filter_list.item(i)
            if it.checkState() == Qt.Checked:
                ids.append(it.data(Qt.UserRole))
        self.settings.set_ecosystem_filter(ids)
        self._update_eco_count()
        self._update_policy_hint()

    def _update_eco_count(self) -> None:
        ids = self.settings.ecosystem_filter()
        if ids:
            self.lbl_eco_count.setText(f"{len(ids)} ecosystem(s) selected.")
        else:
            self.lbl_eco_count.setText("All ecosystems allowed.")

    def _on_confidence_changed(self, _index: int) -> None:
        self.settings.set_min_confidence(self.conf_combo.currentData())

    def _on_ecosystem_col_toggled(self, checked: bool) -> None:
        self.settings.set_show_ecosystem_column(bool(checked))
        try:
            self.table.setColumnHidden(COL_ECOSYSTEM, not checked)
        except Exception:
            pass

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 16)
        card = Card(f"About {APP_DISPLAY_NAME}")
        body = QLabel(
            f"<p><b>Version {APP_VERSION}</b></p>"
            "<p>Pro Space Optimizer recursively scans every subdirectory of "
            "the parent folders you choose, looking for development folders "
            "you name (for example <code>node_modules</code>, "
            "<code>vendor</code>, <code>.venv</code>, "
            "<code>__pycache__</code>).</p>"
            "<p><b>Safety:</b> when a folder matches it is not searched "
            "inside, so nested duplicates are skipped, and symlinked "
            "directories are never followed. Deletion defaults to the Trash. "
            "Clearing the results table never deletes files.</p>"
            "<p><b>Project-aware mode:</b> optionally require a recognised "
            "project/ecosystem (from marker files such as package.json, "
            "Cargo.toml, go.mod, composer.json) before a folder is matched. "
            "Detection never runs project code or installs dependencies.</p>"
            "<p><b>Privileges:</b> the application runs as your normal user. "
            "Administrator privileges are requested only for a single failed "
            "operation, through the system's authentication prompt.</p>"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        card.add(body)
        btn = QPushButton("Open About dialog")
        btn.clicked.connect(lambda: AboutDialog(self).exec())
        card.add(btn)
        outer.addWidget(card)
        outer.addStretch(1)
        return page

    # --------------------------------------------------------------- setup --
    def _wire_shortcuts(self) -> None:
        from PySide6.QtGui import QShortcut
        QShortcut(QKeySequence("Ctrl+O"), self, self.add_parent_folder)
        QShortcut(QKeySequence("F5"), self, self.start_scan)
        QShortcut(QKeySequence("Esc"), self, self.stop_scan)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.clear_results)

    def _restore_geometry(self) -> None:
        geo = self.settings.geometry()
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

    # ------------------------------------------------------------ sidebar --
    _PAGE_META = {
        PAGE_SCANNER: ("Scanner",
                       "Find and remove unnecessary development folders"),
        PAGE_LOCATIONS: ("Saved Locations",
                         "Manage the parent folders that persist between launches"),
        PAGE_SETTINGS: ("Settings",
                        "Deletion mode, appearance and environment"),
        PAGE_ABOUT: ("About", "About Pro Space Optimizer"),
    }

    def _on_nav_changed(self, row: int) -> None:
        if not hasattr(self, "pages"):
            return
        if 0 <= row < self.pages.count():
            self.pages.setCurrentIndex(row)
        title, subtitle = self._PAGE_META.get(
            row, (APP_DISPLAY_NAME, "")
        )
        if hasattr(self, "page_title"):
            self.page_title.setText(title)
            self.page_subtitle.setText(subtitle)

    # -------------------------------------------------------- parent list --
    def _folder_icon(self):
        return self.style().standardIcon(QStyle.SP_DirIcon)

    def _refresh_parents_list(self) -> None:
        self.parents_list.clear()
        self.locations_list.clear()
        icon = self._folder_icon()
        for path in self._parents:
            item = QListWidgetItem(icon, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(0, 38))
            self.parents_list.addItem(item)
            loc_item = QListWidgetItem(icon, path)
            loc_item.setToolTip(path)
            loc_item.setSizeHint(QSize(0, 38))
            self.locations_list.addItem(loc_item)

    def add_parent_folder(self) -> None:
        start = self._parents[0] if self._parents else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Select a parent folder", start,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not chosen:
            return
        self._add_parent(normalize_path(chosen))

    def _add_parent(self, folder: str) -> bool:
        if folder in self._parents:
            show_info(self, "Already added",
                      f"This folder is already in the list:\n{folder}")
            return False
        if not os.path.isdir(folder):
            show_warning(self, "Folder not found",
                         f"This path is not a directory:\n{folder}")
            return False
        self._parents.append(folder)
        self.settings.set_parent_folders(self._parents)
        self._refresh_parents_list()
        return True

    def remove_selected_parents(self) -> None:
        selected = self.parents_list.selectedItems()
        rows = sorted({self.parents_list.row(i) for i in selected}, reverse=True)
        if not rows:
            rows = sorted(
                {self.locations_list.row(i)
                 for i in self.locations_list.selectedItems()}, reverse=True
            )
        if not rows:
            show_info(self, "Nothing selected",
                      "Select one or more folders to remove.")
            return
        for row in rows:
            if 0 <= row < len(self._parents):
                del self._parents[row]
        self.settings.set_parent_folders(self._parents)
        self._refresh_parents_list()

    def clear_parents(self) -> None:
        if not self._parents:
            return
        if not confirm_clear_parents(self):
            return
        self._parents.clear()
        self.settings.set_parent_folders(self._parents)
        self._refresh_parents_list()

    # ----------------------------------------------------------- names ---
    def _on_names_changed(self) -> None:
        names = parse_folder_names(self.names_edit.toPlainText())
        invalid = [n for n in names if not is_valid_folder_name(n)[0]]
        text = f"{len(names)} name(s) will be searched."
        if invalid:
            text += f"  Ignored invalid: {', '.join(invalid)}"
        self.names_hint.setText(text)
        self.settings.set_folder_names_text(self.names_edit.toPlainText())

    def current_names(self) -> List[str]:
        names = parse_folder_names(self.names_edit.toPlainText())
        return [n for n in names if is_valid_folder_name(n)[0]]

    # ---------------------------------------------------------- scanning --
    def _set_scanning(self, active: bool) -> None:
        self._scan_active = active
        self.btn_scan.setEnabled(not active)
        self.btn_add_folder.setEnabled(not active)
        self.btn_remove_folder.setEnabled(not active)
        self.btn_clear_folders.setEnabled(not active)
        self.btn_stop.setEnabled(active)
        if active:
            self.progress.setRange(0, 0)
            self.status_pill.setText("Scanning\u2026")
            self._set_pill_state("busy")
        else:
            self.progress.setRange(0, 100)
            self._set_pill_state("idle")

    def _update_scan_status_idle(self) -> None:
        self.status_pill.setText("Idle")
        self._set_pill_state("idle")
        self.lbl_current.setText("Idle")
        self.lbl_counts.setText("")

    def start_scan(self) -> None:
        if self._scan_active:
            return
        if self._delete_active:
            show_warning(self, "Operation in progress",
                         "Wait for the current deletion to finish.")
            return
        parents = list(self._parents)
        if not parents:
            show_warning(self, "No parent folders",
                         "Add at least one parent folder before scanning.")
            return
        names = self.current_names()
        if not names:
            show_warning(self, "No folder names",
                         "Enter at least one folder name to search for.")
            return

        self.model.clear()
        self._scan_results = []
        self._on_model_changed()
        self._set_scanning(True)
        self.lbl_current.setText("Starting\u2026")
        self.lbl_counts.setText(f"0 / {len(parents)} folders processed")

        worker = ScanWorker(parents, names, options=self._build_policy_options())
        worker.progress.connect(self._on_scan_progress)
        worker.match_found.connect(self._on_match_found)
        worker.parent_error.connect(self._on_parent_error)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        try:
            self._scan_controller.start(worker, done_signals=("finished", "failed"))
        except RuntimeError as exc:
            self._set_scanning(False)
            show_error(self, "Scan failed", str(exc))

    def stop_scan(self) -> None:
        if not self._scan_active:
            return
        worker = self._scan_controller.worker
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        self.lbl_current.setText("Stopping\u2026")

    def _on_scan_progress(self, processed: int, total: int, current: str) -> None:
        if total:
            self.progress.setRange(0, total)
            self._set_progress(processed)
            self.lbl_pct.setText(f"{processed}/{total}")
        self.lbl_current.setText(
            f"Scanning: {shorten_path(current, 70)}" if current else "Scanning\u2026"
        )
        self.lbl_counts.setText(f"{processed} / {total} folders processed")

    def _on_match_found(self, result: ScanResult) -> None:
        self._scan_results.append(result)
        self.model.append_results([result])
        self._update_scan_counts()

    def _on_parent_error(self, parent: str, error: str) -> None:
        log.warning("Scan error for %s: %s", parent, error)
        self.statusBar().showMessage(f"{parent}: {error}", 8000)

    def _update_scan_counts(self) -> None:
        total_size = sum(r.size for r in self._scan_results if r.size)
        self.lbl_counts.setText(
            f"{len(self._scan_results)} match(es) \u2022 {human_size(total_size)}"
        )

    def _on_scan_finished(self, report) -> None:
        self._scan_controller.cleanup()
        self._set_scanning(False)
        cancelled = getattr(report, "cancelled", False)
        skipped = getattr(report, "skipped_symlinks", 0)
        errors = getattr(report, "errors", [])
        self._update_scan_counts()
        if cancelled:
            self.status_pill.setText("Cancelled")
            self._set_pill_state("idle")
            self.lbl_current.setText("Scan cancelled \u2014 results kept")
        else:
            self.status_pill.setText("Scan complete")
            self._set_pill_state("ok")
            self.lbl_current.setText("Scan complete")
        parts = [f"{len(self._scan_results)} match(es)"]
        if skipped:
            parts.append(f"{skipped} symlink(s) skipped")
        if errors:
            parts.append(f"{len(errors)} folder error(s)")
        self.lbl_counts.setText(" \u2022 ".join(parts))
        if errors:
            show_warning(
                self, "Scan completed with problems",
                "Some parent folders could not be scanned:",
                "\n".join(errors),
            )
        if self._scan_results and self.settings.calculate_sizes():
            self._start_size_calculation(list(self._scan_results))
        self._show_empty_state(not self._scan_results)
        self._update_bulk_counts()

    def _on_scan_failed(self, message: str) -> None:
        self._scan_controller.cleanup()
        self._set_scanning(False)
        self.status_pill.setText("Error")
        self._set_pill_state("error")
        show_error(self, "Scan failed", "The scan could not be completed.",
                   message)

    # ------------------------------------------------------- size calc ---
    def _start_size_calculation(self, results: List[ScanResult]) -> None:
        if self._size_active:
            return
        if not results:
            return
        self._size_active = True
        self.statusBar().showMessage("Calculating folder sizes\u2026")
        worker = SizeWorker(results)
        worker.size_ready.connect(self._on_size_ready)
        worker.finished.connect(self._on_size_finished)
        worker.failed.connect(self._on_size_failed)
        try:
            self._size_controller.start(worker, done_signals=("finished", "failed"))
        except RuntimeError:
            self._size_active = False

    def _on_size_ready(self, path: str, size, state) -> None:
        self.model.update_size(path, size, state)
        for r in self._scan_results:
            if r.path == path:
                r.size = size
                r.size_state = state
                break
        self._update_scan_counts()
        self._update_bulk_counts()

    def _on_size_finished(self) -> None:
        self._size_controller.cleanup()
        self._size_active = False
        self.statusBar().showMessage("Size calculation complete", 5000)

    def _on_size_failed(self, message: str) -> None:
        self._size_controller.cleanup()
        self._size_active = False
        log.warning("Size calculation failed: %s", message)

    # --------------------------------------------------------- results ---
    def _on_model_changed(self, *args) -> None:
        self._show_empty_state(self.model.rowCount() == 0)
        self._update_bulk_counts()

    def _on_data_changed(self, *args) -> None:
        self._update_bulk_counts()

    def _show_empty_state(self, empty: bool) -> None:
        if empty and not self._scan_active:
            self.statusBar().showMessage(
                "No results yet \u2014 add parent folders and start a scan.", 4000
            )

    def _set_all_selected(self, selected: bool) -> None:
        self.model.select_all(selected)

    def _update_bulk_counts(self) -> None:
        selected = self.model.selected_results()
        count = len(selected)
        self.lbl_selected.setText(f"{count} selected")
        active = count > 0 and not self._delete_active and not self._scan_active
        self.btn_delete_selected.setEnabled(active)
        self.btn_delete_all.setEnabled(
            self.model.rowCount() > 0 and not self._delete_active
            and not self._scan_active
        )
        self.btn_select_all.setEnabled(self.model.rowCount() > 0)
        self.btn_deselect_all.setEnabled(self.model.rowCount() > 0)
        total = self.model.total_size()
        self.lbl_total_size.setText(f"Total: {human_size(total)}")

    def clear_results(self) -> None:
        """Clear the table only.  Never touches the filesystem."""
        if self.model.rowCount() == 0:
            return
        if not confirm_clear_results(self):
            return
        self.model.clear()
        self._scan_results = []
        self._update_scan_counts()
        self._update_bulk_counts()
        self.statusBar().showMessage("Results table cleared (no files deleted)",
                                     4000)

    def _row_result(self, row: int) -> Optional[ScanResult]:
        return self.model.get_result(row)

    # -------------------------------------------------------- table menu --
    def _show_table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        result = self._row_result(row)
        if result is None:
            return
        menu = QMenu(self)
        act_copy = QAction("Copy Full Path", self)
        act_copy.triggered.connect(lambda: self._copy_path(result.path))
        act_open = QAction("Open Parent Directory", self)
        act_open.triggered.connect(lambda: self._open_parent(result.path))
        act_delete = QAction("Delete\u2026", self)
        act_delete.triggered.connect(lambda: self.delete_row(row))
        menu.addAction(act_copy)
        menu.addAction(act_open)
        menu.addSeparator()
        menu.addAction(act_delete)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_path(self, path: str) -> None:
        QApplication.clipboard().setText(path)
        self.statusBar().showMessage("Path copied to clipboard", 3000)

    def _open_parent(self, path: str) -> None:
        parent = os.path.dirname(path)
        url = QUrl.fromLocalFile(parent)
        if not QDesktopServices.openUrl(url):
            show_warning(self, "Could not open folder",
                         f"Could not open the file manager for:\n{parent}")

    # --------------------------------------------------------- deletion --
    def _deletion_manager(self) -> DeletionManager:
        return DeletionManager(self._parents, self.current_names())

    def _mode(self) -> str:
        return self.settings.delete_mode()

    def _confirm_targets(self, results: List[ScanResult], mode: str) -> bool:
        if mode == "trash":
            return confirm_bulk_delete(self, results, mode)
        return confirm_bulk_delete(self, results, mode)

    def delete_row(self, row: int) -> None:
        if self._delete_active or self._scan_active:
            return
        result = self._row_result(row)
        if result is None:
            return
        mode = self._mode()
        if not confirm_single_delete(self, result, mode):
            return
        self._delete_targets([result], row_hint=row)

    def delete_selected(self) -> None:
        if self._delete_active or self._scan_active:
            return
        results = self.model.selected_results()
        if not results:
            show_info(self, "Nothing selected",
                      "Select one or more result rows to delete.")
            return
        mode = self._mode()
        if not confirm_bulk_delete(self, results, mode):
            return
        self._delete_targets(results)

    def delete_all_results(self) -> None:
        if self._delete_active or self._scan_active:
            return
        results = list(self.model.results)
        if not results:
            return
        mode = self._mode()
        if not confirm_bulk_delete(self, results, mode):
            return
        self._delete_targets(results)

    def _delete_targets(self, results: List[ScanResult],
                        row_hint: Optional[int] = None) -> None:
        mode = self._mode()
        manager = self._deletion_manager()

        # Pre-mark rows as deleting.
        for r in results:
            self._set_status_for_path(r.path, RowStatus.DELETING)

        # Validate first so we can short-circuit obviously unsafe targets and
        # avoid spawning a worker for a no-op.
        valid: List[ScanResult] = []
        outcomes: List[DeleteOutcome] = []
        for r in results:
            v = manager.validate(r)
            if not v.ok:
                outcomes.append(DeleteOutcome(path=r.path, ok=False,
                                              error=v.reason, mode=mode,
                                              needs_privileges=v.needs_privileges))
                self._set_status_for_path(r.path, RowStatus.ERROR,
                                          v.reason or "Invalid target")
            else:
                valid.append(r)

        if not valid:
            self._report_outcomes(outcomes, mode)
            return

        self._delete_active = True
        self._update_bulk_counts()
        self.status_pill.setText("Deleting\u2026")
        self._set_pill_state("busy")
        self.progress.setRange(0, len(valid))
        self.progress.setValue(0)

        worker = DeleteWorker(manager, valid, mode)
        self._active_delete_mode = mode
        worker.progress.connect(self._on_delete_progress)
        worker.one_done.connect(self._on_one_deleted)
        worker.finished.connect(self._on_delete_finished)
        worker.failed.connect(self._on_delete_worker_failed)
        self._pending_outcomes = outcomes  # pre-validation failures
        try:
            self._delete_controller.start(worker, done_signals=("finished", "failed"))
        except RuntimeError as exc:
            self._delete_active = False
            show_error(self, "Deletion failed", str(exc))

    def _set_status_for_path(self, path: str, status: RowStatus,
                             detail: str = "") -> None:
        for row, r in enumerate(self.model.results):
            if r.path == path:
                r.status = status
                r.status_detail = detail
                self.model.set_row_status(row, status, detail)
                break

    def _on_delete_progress(self, done: int, total: int, path: str) -> None:
        self.progress.setRange(0, total)
        self._set_progress(done)

    def _on_one_deleted(self, outcome: DeleteOutcome) -> None:
        if outcome.ok:
            self._set_status_for_path(outcome.path, RowStatus.DELETED)
        else:
            self._set_status_for_path(outcome.path, RowStatus.ERROR,
                                      outcome.error or "Failed")

    def _on_delete_finished(self, outcomes: List[DeleteOutcome]) -> None:
        self._delete_controller.cleanup()
        self._delete_active = False
        mode = getattr(self, "_active_delete_mode", "trash")
        self.status_pill.setText("Ready")
        self._set_pill_state("idle")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._update_bulk_counts()

        all_outcomes = list(getattr(self, "_pending_outcomes", [])) + list(outcomes)
        self._pending_outcomes = []

        # Remove successful rows from the table.
        removed = [o.path for o in all_outcomes if o.ok]
        if removed:
            self.model.remove_paths(removed)
            self._scan_results = [r for r in self._scan_results
                                  if r.path not in set(removed)]

        # Offer privileged retry for permission failures, one at a time.
        privilege_candidates = [
            o for o in all_outcomes if not o.ok and o.needs_privileges
        ]
        for outcome in privilege_candidates:
            self._offer_privileged_retry(outcome, all_outcomes)

        self._report_outcomes(all_outcomes, mode)
        self._update_scan_counts()
        self._update_bulk_counts()

    def _on_delete_worker_failed(self, message: str) -> None:
        self._delete_controller.cleanup()
        self._delete_active = False
        self.status_pill.setText("Error")
        self._set_pill_state("error")
        show_error(self, "Deletion failed",
                   "The deletion operation could not be completed.", message)

    def _report_outcomes(self, outcomes: List[DeleteOutcome], mode: str) -> None:
        if not outcomes:
            return
        failed = [o for o in outcomes if not o.ok]
        succeeded = [o for o in outcomes if o.ok]
        if not failed:
            msg = f"Deleted {len(succeeded)} folder(s)."
            self.statusBar().showMessage(msg, 6000)
            if len(succeeded) > 1:
                show_info(self, "Deletion complete", msg)
            return
        # Show a detailed report when there are failures.
        BulkReportDialog(self, outcomes, mode).exec()

    def _offer_privileged_retry(self, outcome: DeleteOutcome,
                                all_outcomes: List[DeleteOutcome]) -> None:
        if not pkexec_available():
            show_warning(
                self, "Administrator privileges unavailable",
                "This folder could not be deleted because administrator "
                "privileges are required, but 'pkexec' is not available.",
                f"Path: {outcome.path}\nError: {outcome.error}",
            )
            return
        if not confirm_privilege_retry(self, "delete", outcome.path):
            return
        # Find the ScanResult to retry with identity checks.
        result = next((r for r in self.model.results
                       if r.path == outcome.path), None)
        if result is None:
            result = next((r for r in self._scan_results
                           if r.path == outcome.path), None)
        if result is None:
            show_warning(self, "Cannot retry",
                         "The original result is no longer available.",
                         outcome.path)
            return
        manager = self._deletion_manager()
        pr = manager.delete_privileged(result)
        if pr.ok:
            self.model.remove_paths([outcome.path])
            self._scan_results = [r for r in self._scan_results
                                  if r.path != outcome.path]
            outcome.ok = True
            outcome.error = None
            self.statusBar().showMessage(
                f"Deleted with privileges: {outcome.path}", 6000
            )
        elif pr.cancelled:
            show_info(self, "Authentication cancelled",
                      "The operation was left untouched.")
        else:
            show_warning(self, "Privileged deletion failed",
                         pr.error or "The operation failed.",
                         outcome.path)

    # ------------------------------------------------------------- misc --
    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode_combo.currentData()
        self.settings.set_delete_mode(mode)
        if mode == "permanent":
            show_warning(
                self, "Permanent deletion enabled",
                "New deletions will permanently remove folders from disk. "
                "You will still be asked to confirm each deletion.",
            )

    # --------------------------------------------------- project-aware --
    def _on_policy_changed(self, _index: int) -> None:
        policy = self.policy_combo.currentData()
        self.settings.set_match_policy(policy)
        self._update_policy_hint()

    def _update_policy_hint(self) -> None:
        if not hasattr(self, "lbl_policy_hint"):
            return
        policy = self.policy_combo.currentData()
        if policy == "project":
            filters = self.settings.ecosystem_filter()
            extra = ""
            if filters:
                labels = []
                for fid in filters:
                    e = eco.get_ecosystem(fid)
                    labels.append(e.label if e else fid)
                extra = "  Filter: " + ", ".join(sorted(labels)) + "."
            self.lbl_policy_hint.setText(
                "Project-aware: a folder is only matched when a recognised "
                "project/ecosystem justifies it. Non-matching folders are "
                "still traversed, so deeper matches are found." + extra
            )
        else:
            self.lbl_policy_hint.setText(
                "Name only: every folder whose name matches is listed. "
                "Project type is shown but does not affect matching."
            )

    def _build_policy_options(self) -> PolicyOptions:
        policy = self.policy_combo.currentData()
        mp = MatchingPolicy.PROJECT_AWARE if policy == "project" else MatchingPolicy.NAME_ONLY
        conf_name = self.settings.min_confidence()
        conf = {
            "low": eco.Confidence.LOW,
            "medium": eco.Confidence.MEDIUM,
            "high": eco.Confidence.HIGH,
        }.get(conf_name, eco.Confidence.MEDIUM)
        return PolicyOptions(
            policy=mp,
            min_confidence=conf,
            ecosystem_filter=set(self.settings.ecosystem_filter()),
        )

    def _on_theme_changed(self, _index: int) -> None:
        theme = self.theme_combo.currentData()
        self.settings.set_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)
        # Refresh custom-painted surfaces so they follow the new palette.
        self._palette = liquid.palette_for(resolved_theme(theme))
        if hasattr(self, "root_surface"):
            self.root_surface.set_palette(self._palette)
        if hasattr(self, "delete_delegate"):
            self.delete_delegate.set_palette(self._palette)
            self.table.viewport().update()
        if hasattr(self, "_nav_delegate"):
            self._nav_delegate.set_palette(self._palette)
            self.nav.viewport().update()
        if hasattr(self, "bulk_bar"):
            self.bulk_bar.setStyleSheet(
                f"#BulkBar {{ background: {self._palette.glass};"
                f" border: 1px solid {self._palette.border};"
                f" border-radius: {liquid.RADIUS_MD}px; }}"
            )
        for card in self.findChildren(Card):
            card.set_accent_color(
                self._palette.accent if hasattr(card, "_accent_dot")
                else self._palette.accent
            )

    def _update_environment_info(self) -> None:
        lines = []
        lines.append("Trash: " + ("available (gio)" if trash_available()
                                  else "NOT available"))
        lines.append("Administrator helper (pkexec): " +
                     ("available" if pkexec_available() else "NOT available"))
        self.lbl_trash_state.setText(lines[0])
        self.lbl_env.setText("<br>".join(lines))

    # ----------------------------------------------------------- shutdown --
    def closeEvent(self, event) -> None:
        if self._scan_active:
            reply = QMessageBox.question(
                self, "Scan in progress",
                "A scan is still running. Stop it and quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.stop_scan()
        if self._delete_active:
            reply = QMessageBox.question(
                self, "Deletion in progress",
                "A deletion is still running. Wait for it to finish before "
                "quitting?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

        self._scan_controller.stop_and_wait()
        self._size_controller.stop_and_wait()
        self._delete_controller.stop_and_wait()
        self._scan_controller.cleanup()
        self._size_controller.cleanup()
        self._delete_controller.cleanup()

        try:
            self.settings.save_geometry(self.saveGeometry())
            self.settings.save_window_state(self.saveState())
            self.settings.sync()
        except Exception:
            log.exception("Could not save settings on close")
        super().closeEvent(event)
