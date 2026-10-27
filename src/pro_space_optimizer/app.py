"""Application bootstrap: QApplication, logging, theming, entry point."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from . import APP_DISPLAY_NAME, APP_NAME, ORG_DOMAIN, ORG_NAME
from .settings import Settings
from . import theme as liquid

log = logging.getLogger(__name__)


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, APP_NAME)


def log_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, APP_NAME)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    )

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        os.makedirs(log_dir(), exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir(), "app.log"),
            maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        # Logging to a file is best-effort only.
        pass


def resource_path(*parts: str) -> str:
    """Locate a bundled resource (icons) both in-tree and when installed."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "resources", *parts),
        os.path.join(os.path.dirname(os.path.dirname(here)), "resources", *parts),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def app_icon() -> QIcon:
    path = resource_path("icons", "app.svg")
    if os.path.exists(path):
        return QIcon(path)
    return QIcon.fromTheme("folder")


# ---------------------------------------------------------------------------
# Theming - Liquid Glass design system
# ---------------------------------------------------------------------------

def _detect_system_theme(app: QApplication) -> str:
    """Best-effort detection of whether the desktop is in dark mode."""
    base = app.palette().color(QPalette.ColorRole.Base)
    return "dark" if base.lightness() < 128 else "light"


def resolved_theme(theme: str) -> str:
    """Map ``system`` to a concrete ``light``/``dark`` value."""
    if theme in ("light", "dark"):
        return theme
    app = QApplication.instance()
    if app is not None:
        return _detect_system_theme(app)
    return "dark"


def apply_theme(app: QApplication, theme: str) -> None:
    """Apply the Liquid Glass ``system``/``light``/``dark`` theme.

    We build a matching QPalette as well as the stylesheet so default-drawn
    widgets (menus, dialogs, tooltips) stay consistent, and we never depend on
    the compositor providing real backdrop blur - the glass look is produced
    with layered translucency and gradients that render everywhere.
    """
    concrete = resolved_theme(theme)
    palette = liquid.palette_for(concrete)

    # Build a QPalette that matches the theme so native-drawn pieces agree.
    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(palette.window))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.Base, QColor(palette.window_alt))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(palette.window))
    qp.setColor(QPalette.ColorRole.Text, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.Button, QColor(palette.window_alt))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(palette.accent))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette.tooltip_bg))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(palette.text_faint))
    app.setPalette(qp)
    app.setStyleSheet(liquid.build_stylesheet(palette))



def create_application(argv: Optional[list] = None) -> QApplication:
    argv = list(argv if argv is not None else sys.argv)
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(ORG_DOMAIN)
    app.setDesktopFileName(APP_NAME)
    app.setWindowIcon(app_icon())
    return app


def main(argv: Optional[list] = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    verbose = "--verbose" in argv or "-v" in argv
    setup_logging(verbose)

    app = create_application(argv)
    settings = Settings()
    apply_theme(app, settings.theme())

    # Imported lazily so that ``--verbose`` logging is configured first.
    from .main_window import MainWindow

    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
