"""Theme-aware styles shared by the Qt GUI."""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

MUTED_TEXT_STYLE = "color: palette(placeholder-text);"
MUTED_NOTE_STYLE = "color: palette(placeholder-text); padding: 1px 0 6px 0;"

PANEL_STYLE = (
    "background-color: palette(alternate-base); color: palette(text); "
    "border: 1px solid palette(mid); padding: 10px;"
)

WARNING_TEXT_STYLE = "color: palette(text);"
WARNING_PANEL_STYLE = (
    "background-color: palette(alternate-base); color: palette(text); "
    "border: 1px solid palette(mid); padding: 10px;"
)
DANGER_TEXT_STYLE = "color: palette(text); font-weight: bold;"


def is_dark_palette(app: QApplication | None = None) -> bool:
    instance = app if app is not None else QApplication.instance()
    if not isinstance(instance, QApplication):
        return False
    return instance.palette().color(QPalette.Window).lightness() < 128


def warning_text_style(app: QApplication | None = None) -> str:
    color = "#f0b44c" if is_dark_palette(app) else "#8a4b08"
    return f"color: {color}; font-weight: 600;"


def danger_text_style(app: QApplication | None = None) -> str:
    color = "#ff7b72" if is_dark_palette(app) else "#b42318"
    return f"color: {color}; font-weight: bold;"
