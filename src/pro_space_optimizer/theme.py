"""Liquid Glass design system for Pro Space Optimizer.

This module centralises the visual language so the rest of the GUI only refers
to semantic tokens instead of hard-coded colours.  It provides:

* a small set of design tokens (colours, radii, spacing, typography),
* a helper to build a translucent "glass" background for custom-painted
  widgets,
* factory helpers that apply soft drop shadows, and
* fully generated Qt stylesheets for the dark (default) and light themes.

Honest note on "blur": Qt stylesheets cannot blur the content behind a widget.
What we implement here is *layered translucency*: semi-transparent surfaces
stacked over a softly-lit gradient background, with thin translucent borders
and soft shadows.  This reads as frosted glass and works reliably across
Ubuntu/X11/Wayland sessions regardless of compositor support.  Where a real
backdrop blur is desired, the caller would need a compositor effect; we do not
claim or depend on that.
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

# Spacing scale (px)
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

# Corner radii (px)
RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
RADIUS_XL = 20

# Typography
FONT_FAMILY = "Ubuntu, Cantarell, Inter, Segoe UI, sans-serif"
FONT_SIZE_BODY = 13
FONT_SIZE_SMALL = 12
FONT_SIZE_H2 = 17
FONT_SIZE_H1 = 21


@dataclass(frozen=True)
class Palette:
    """A complete colour set for one theme variant."""

    # window / background lighting
    window: str
    window_alt: str
    glow_a: str          # top-left ambient glow
    glow_b: str          # bottom-right ambient glow

    # glass surfaces
    glass: str           # rgba() string
    glass_strong: str
    glass_hover: str
    glass_active: str
    border: str          # thin translucent border
    border_strong: str

    # text
    text: str
    text_muted: str
    text_faint: str

    # accents
    accent: str
    accent_hover: str
    accent_soft: str
    accent_glow: str

    # semantic
    success: str
    warning: str
    error: str
    error_hover: str

    # misc
    shadow: str
    tooltip_bg: str


DARK = Palette(
    window="#0b1220",
    window_alt="#0e1526",
    glow_a="rgba(96, 132, 255, 0.22)",     # electric blue haze
    glow_b="rgba(158, 108, 255, 0.18)",    # soft violet haze
    glass="rgba(30, 41, 66, 0.55)",
    glass_strong="rgba(38, 51, 82, 0.72)",
    glass_hover="rgba(58, 76, 118, 0.55)",
    glass_active="rgba(72, 96, 150, 0.70)",
    border="rgba(255, 255, 255, 0.10)",
    border_strong="rgba(255, 255, 255, 0.20)",
    text="#eaf0ff",
    text_muted="#9fb0cf",
    text_faint="#6f7f9e",
    accent="#5b8cff",
    accent_hover="#7aa2ff",
    accent_soft="rgba(91, 140, 255, 0.18)",
    accent_glow="rgba(91, 140, 255, 0.45)",
    success="#69d69a",
    warning="#f0c060",
    error="#f07a72",
    error_hover="#f79289",
    shadow="rgba(0, 0, 0, 0.45)",
    tooltip_bg="#131c30",
)

LIGHT = Palette(
    window="#eef2fb",
    window_alt="#e7ecf8",
    glow_a="rgba(120, 160, 255, 0.35)",
    glow_b="rgba(180, 150, 255, 0.30)",
    glass="rgba(255, 255, 255, 0.62)",
    glass_strong="rgba(255, 255, 255, 0.80)",
    glass_hover="rgba(255, 255, 255, 0.85)",
    glass_active="rgba(232, 240, 255, 0.95)",
    border="rgba(20, 40, 90, 0.12)",
    border_strong="rgba(20, 40, 90, 0.22)",
    text="#16233f",
    text_muted="#5a6b8c",
    text_faint="#8493ad",
    accent="#3f6fe0",
    accent_hover="#5a85ea",
    accent_soft="rgba(63, 111, 224, 0.14)",
    accent_glow="rgba(63, 111, 224, 0.35)",
    success="#2f9e6b",
    warning="#c98a1e",
    error="#d9554c",
    error_hover="#e2706a",
    shadow="rgba(30, 50, 90, 0.22)",
    tooltip_bg="#ffffff",
)


def palette_for(theme: str) -> Palette:
    return LIGHT if theme == "light" else DARK


# ---------------------------------------------------------------------------
# Colour parsing
# ---------------------------------------------------------------------------
# Qt's QColor does NOT understand CSS "rgba(...)" strings - it silently
# returns black.  Several design tokens are rgba() strings (the glass
# surfaces), so we parse them ourselves.

_RGBA_RE = _re.compile(
    r"rgba?\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9]+)"
    r"(?:\s*,\s*([0-9]*\.?[0-9]+))?\s*\)"
)


def qc(value: str) -> QColor:
    """Return a QColor from a hex string or a CSS ``rgb()/rgba()`` string."""
    if not value:
        return QColor(0, 0, 0, 0)
    text = value.strip()
    m = _RGBA_RE.match(text)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return QColor(r, g, b, max(0, min(255, round(a * 255))))
    colour = QColor(text)
    return colour


def opaque(value: str, over: str) -> QColor:
    """Flatten a translucent colour *value* over an opaque *over* colour.

    Used for custom-painted surfaces so they render identically where
    compositing is unavailable.
    """
    fg = qc(value)
    bg = qc(over)
    a = fg.alphaF()
    return QColor(
        round(bg.red() * (1 - a) + fg.red() * a),
        round(bg.green() * (1 - a) + fg.green() * a),
        round(bg.blue() * (1 - a) + fg.blue() * a),
    )


# ---------------------------------------------------------------------------
# Effects helpers
# ---------------------------------------------------------------------------

def add_shadow(widget: QWidget, *, blur: int = 28, y: int = 6,
               alpha: int = 130, color: str = "#000000") -> QGraphicsDropShadowEffect:
    """Attach a soft layered shadow to *widget* and return the effect."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(y)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    widget.setGraphicsEffect(effect)
    return effect


def paint_glass_background(painter: QPainter, rect, palette: Palette,
                           radius: int = RADIUS_LG,
                           strong: bool = False) -> None:
    """Paint a translucent rounded glass surface into *rect*.

    Used by custom-painted panels.  This is layered translucency, not a real
    backdrop blur.
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    top = qc(palette.glass_strong if strong else palette.glass)
    bottom = qc(palette.glass)
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bottom)
    painter.fillPath(path, grad)

    pen = qc(palette.border_strong if strong else palette.border)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.restore()


# ---------------------------------------------------------------------------
# Stylesheet generation
# ---------------------------------------------------------------------------

def _rgba(hex_or_rgba: str, alpha: float) -> str:
    """Return an rgba() string from a hex colour, overriding its alpha."""
    c = qc(hex_or_rgba)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def build_stylesheet(p: Palette) -> str:
    """Generate the full application stylesheet for *p*."""
    return f"""
/* ---------- base ---------- */
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE_BODY}px;
    color: {p.text};
}}
QMainWindow, QDialog {{
    background: {p.window};
}}
#GlassDialog {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p.window}, stop:0.6 {p.window_alt}, stop:1 {p.window});
}}
QMessageBox {{
    background: {p.window_alt};
}}
QMessageBox QLabel {{
    color: {p.text};
}}
#RootSurface {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p.window}, stop:0.5 {p.window_alt}, stop:1 {p.window});
}}
QLabel {{ background: transparent; }}
QToolTip {{
    background: {p.tooltip_bg};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 6px 8px;
}}

/* ---------- header ---------- */
#Header {{
    background: {p.glass};
    border-bottom: 1px solid {p.border};
}}
#HeaderTitle {{
    font-size: {FONT_SIZE_H1}px;
    font-weight: 700;
    color: {p.text};
    letter-spacing: 0.2px;
}}
#HeaderSubtitle {{
    color: {p.text_muted};
    font-size: {FONT_SIZE_SMALL}px;
}}
#PageTitle {{ font-size: {FONT_SIZE_H2}px; font-weight: 600; }}
#StatusPill {{
    background: {p.glass_strong};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_MD}px;
    padding: 5px 14px;
    color: {p.text};
    font-weight: 600;
}}
#StatusPill[state="busy"] {{
    border: 1px solid {p.accent};
    color: {p.accent_hover};
}}
#StatusPill[state="ok"] {{
    border: 1px solid {p.success};
    color: {p.success};
}}
#StatusPill[state="error"] {{
    border: 1px solid {p.error};
    color: {p.error};
}}

/* ---------- sidebar ---------- */
#Sidebar {{
    background: {p.glass};
    border-right: 1px solid {p.border};
}}
#BrandTitle {{
    font-size: 15px;
    font-weight: 700;
    color: {p.text};
}}
#BrandSubtitle {{
    color: {p.text_faint};
    font-size: 11px;
}}
#Sidebar QListWidget {{
    background: {p.glass};
    border: none;
    outline: none;
    padding: 0;
}}
#Sidebar QListWidget::viewport {{
    background: {p.glass};
}}
#Sidebar QListWidget::item {{
    padding: 0 14px;
    border-radius: {RADIUS_MD}px;
    margin: 3px 6px;
    color: {p.text_muted};
    border-left: 3px solid transparent;
}}
#Sidebar QListWidget::item:hover {{
    background: {p.glass_hover};
    color: {p.text};
}}
#Sidebar QListWidget::item:selected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent_soft}, stop:1 {p.glass_active});
    color: {p.text};
    border-left: 3px solid {p.accent};
    font-weight: 600;
}}

/* ---------- glass panels (cards) ---------- */
#Card {{
    background: {p.glass};
    border: 1px solid {p.border};
    border-radius: {RADIUS_LG}px;
}}
#CardTitle {{
    font-size: 14px;
    font-weight: 700;
    color: {p.text};
}}
#SectionHint {{
    color: {p.text_muted};
    font-size: {FONT_SIZE_SMALL}px;
}}
#Divider {{
    background: {p.border};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ---------- buttons ---------- */
QPushButton {{
    padding: 7px 16px;
    border-radius: {RADIUS_MD}px;
    border: 1px solid {p.border_strong};
    background: {p.glass_strong};
    color: {p.text};
}}
QPushButton:hover {{
    background: {p.glass_hover};
    border: 1px solid {p.border_strong};
}}
QPushButton:pressed {{
    background: {p.glass_active};
}}
QPushButton:focus {{
    border: 1px solid {p.accent};
}}
QPushButton:disabled {{
    color: {p.text_faint};
    background: {p.glass};
    border: 1px solid {p.border};
}}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent}, stop:1 {p.accent_hover});
    color: #ffffff;
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent_hover}, stop:1 {_rgba(p.accent_hover, 0.85)});
    border: 1px solid {p.accent_glow};
}}
QPushButton#Primary:disabled {{
    background: {p.accent_soft};
    color: {p.text_faint};
    border: 1px solid {p.border};
}}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {p.border};
    color: {p.text_muted};
}}
QPushButton#Ghost:hover {{
    background: {p.glass_hover};
    color: {p.text};
}}
QPushButton#Danger {{
    background: {_rgba(p.error, 0.16)};
    color: {p.error};
    border: 1px solid {_rgba(p.error, 0.55)};
    font-weight: 600;
}}
QPushButton#Danger:hover {{
    background: {_rgba(p.error, 0.26)};
    border: 1px solid {p.error};
}}
QPushButton#Danger:disabled {{
    background: {p.glass};
    color: {p.text_faint};
    border: 1px solid {p.border};
}}
QPushButton#RowAction {{
    padding: 4px 12px;
    border-radius: {RADIUS_SM}px;
}}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QComboBox {{
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_MD}px;
    padding: 8px 12px;
    background: {p.glass};
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: #ffffff;
}}
QPlainTextEdit {{
    padding: 10px 12px;
}}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{
    border: 1px solid {p.accent_glow};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {p.accent};
    background: {p.glass_strong};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.window_alt};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    selection-background-color: {p.accent};
    selection-color: #ffffff;
    outline: none;
}}
QCheckBox {{ spacing: 8px; color: {p.text}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 5px;
    border: 1px solid {p.border_strong};
    background: {p.glass};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border: 1px solid {p.accent};
}}

/* ---------- lists ---------- */
QListWidget {{
    background: {p.glass};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
    outline: none;
}}
QListWidget::item {{
    padding: 9px 12px;
    border-radius: {RADIUS_SM}px;
    margin: 2px 0;
    color: {p.text};
    min-height: 22px;
}}
QListWidget::item:hover {{ background: {p.glass_hover}; }}
QListWidget::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
    border: 1px solid {_rgba(p.accent, 0.5)};
}}

/* ---------- table ---------- */
QTableView {{
    background: transparent;
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    gridline-color: transparent;
    outline: none;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    alternate-background-color: {p.glass};
}}
QTableView::item {{
    padding: 6px 8px;
    border: none;
}}
QTableView::item:hover {{
    background: {p.glass_hover};
}}
QTableView::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    padding: 8px;
    border: none;
    border-bottom: 1px solid {p.border_strong};
    background: {p.glass_strong};
    color: {p.text_muted};
    font-weight: 600;
}}
QHeaderView::section:first {{ border-top-left-radius: {RADIUS_MD}px; }}
QHeaderView::section:last {{ border-top-right-radius: {RADIUS_MD}px; }}
QTableCornerButton::section {{
    background: {p.glass_strong};
    border: none;
}}

/* ---------- progress ---------- */
QProgressBar {{
    border: 1px solid {p.border};
    border-radius: 8px;
    text-align: center;
    height: 16px;
    background: {p.glass};
    color: {p.text};
}}
QProgressBar::chunk {{
    border-radius: 7px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent}, stop:1 {p.accent_hover});
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.accent_glow}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{
    background: {p.border_strong}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.accent_glow}; }}

/* ---------- menus ---------- */
QMenu {{
    background: {p.window_alt};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: {RADIUS_SM}px;
    color: {p.text};
}}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

/* ---------- status bar ---------- */
QStatusBar {{
    background: {p.glass};
    color: {p.text_muted};
    border-top: 1px solid {p.border};
}}
QStatusBar::item {{ border: none; }}
"""
