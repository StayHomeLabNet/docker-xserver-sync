import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from dxsync.gui import MainWindow
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore
from dxsync.theme import (
    THEME_DARK,
    THEME_LIGHT,
    THEME_SETTING_KEY,
    THEME_SYSTEM,
    apply_application_theme,
)


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_explicit_light_and_dark_palettes_have_readable_contrast() -> None:
    application = app()
    try:
        apply_application_theme(application, THEME_LIGHT)
        assert application.style().objectName().casefold() == "fusion"
        light = application.palette()
        assert light.color(QPalette.Window).lightness() > 180
        assert light.color(QPalette.WindowText).lightness() < 100

        apply_application_theme(application, THEME_DARK)
        assert application.style().objectName().casefold() == "fusion"
        dark = application.palette()
        assert dark.color(QPalette.Window).lightness() < 100
        assert dark.color(QPalette.WindowText).lightness() > 180
    finally:
        apply_application_theme(application, THEME_SYSTEM)


def test_main_window_theme_selector_persists_selection(tmp_path: Path) -> None:
    application = app()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue(THEME_SETTING_KEY, THEME_DARK)
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        settings,
    )
    try:
        assert window.theme_combo.currentData() == THEME_DARK
        light_index = window.theme_combo.findData(THEME_LIGHT)
        window.theme_combo.setCurrentIndex(light_index)
        assert settings.value(THEME_SETTING_KEY) == THEME_LIGHT
        assert application.property("dxsyncTheme") == THEME_LIGHT
    finally:
        window.close()
        apply_application_theme(application, THEME_SYSTEM)
