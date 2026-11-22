from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SETTING_KEY = "display/theme"
THEME_OPTIONS = (
    ("システム設定", THEME_SYSTEM),
    ("ライト", THEME_LIGHT),
    ("ダーク", THEME_DARK),
)


def normalize_theme(value: object) -> str:
    theme = str(value or THEME_SYSTEM).strip().lower()
    return theme if theme in {THEME_SYSTEM, THEME_LIGHT, THEME_DARK} else THEME_SYSTEM


def _explicit_palette(base: QPalette, *, dark: bool) -> QPalette:
    palette = QPalette(base)
    colors = (
        {
            QPalette.Window: "#2d2d2d",
            QPalette.WindowText: "#f0f0f0",
            QPalette.Base: "#1e1e1e",
            QPalette.AlternateBase: "#383838",
            QPalette.ToolTipBase: "#383838",
            QPalette.ToolTipText: "#f0f0f0",
            QPalette.Text: "#f0f0f0",
            QPalette.Button: "#353535",
            QPalette.ButtonText: "#f0f0f0",
            QPalette.BrightText: "#ffffff",
            QPalette.Link: "#66aaff",
            QPalette.Highlight: "#236aa8",
            QPalette.HighlightedText: "#ffffff",
            QPalette.PlaceholderText: "#b0b0b0",
            QPalette.Mid: "#686868",
        }
        if dark
        else {
            QPalette.Window: "#f5f5f5",
            QPalette.WindowText: "#1f1f1f",
            QPalette.Base: "#ffffff",
            QPalette.AlternateBase: "#eeeeee",
            QPalette.ToolTipBase: "#ffffff",
            QPalette.ToolTipText: "#1f1f1f",
            QPalette.Text: "#1f1f1f",
            QPalette.Button: "#f0f0f0",
            QPalette.ButtonText: "#1f1f1f",
            QPalette.BrightText: "#000000",
            QPalette.Link: "#0969da",
            QPalette.Highlight: "#0a64ad",
            QPalette.HighlightedText: "#ffffff",
            QPalette.PlaceholderText: "#666666",
            QPalette.Mid: "#a0a0a0",
        }
    )
    for role, value in colors.items():
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            palette.setColor(group, role, QColor(value))
    disabled_text = QColor("#858585" if dark else "#777777")
    palette.setColor(QPalette.Disabled, QPalette.Text, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.PlaceholderText, disabled_text)
    return palette


def apply_application_theme(app: QApplication, theme: object) -> str:
    normalized = normalize_theme(theme)
    if not hasattr(app, "_dxsync_system_palette"):
        app._dxsync_system_palette = QPalette(app.palette())  # type: ignore[attr-defined]
        object_name = app.style().objectName()
        app._dxsync_system_style = next(  # type: ignore[attr-defined]
            (
                name
                for name in QStyleFactory.keys()
                if name.casefold() == object_name.casefold()
            ),
            object_name,
        )
    system_palette = app._dxsync_system_palette  # type: ignore[attr-defined]
    if normalized == THEME_SYSTEM:
        system_style = app._dxsync_system_style  # type: ignore[attr-defined]
        if app.style().objectName().casefold() != system_style.casefold():
            app.setStyle(system_style)
        app.setPalette(QPalette(system_palette))
    else:
        if app.style().objectName().casefold() != "fusion":
            app.setStyle("Fusion")
        app.setPalette(
            _explicit_palette(system_palette, dark=normalized == THEME_DARK)
        )
    app.setProperty("dxsyncTheme", normalized)
    return normalized
