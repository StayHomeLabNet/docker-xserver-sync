import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from dxsync import ui_styles
from dxsync.theme import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, apply_application_theme


def test_shared_ui_styles_follow_the_active_qt_palette() -> None:
    styles = {
        name: value
        for name, value in vars(ui_styles).items()
        if name.endswith("_STYLE")
    }

    assert styles
    assert all("palette(" in style for style in styles.values())
    assert all("#" not in style for style in styles.values())


def test_theme_aware_warning_and_danger_styles_distinguish_light_and_dark() -> None:
    app = QApplication.instance() or QApplication([])
    try:
        apply_application_theme(app, THEME_LIGHT)
        light_warn = ui_styles.warning_text_style(app)
        light_danger = ui_styles.danger_text_style(app)

        apply_application_theme(app, THEME_DARK)
        dark_warn = ui_styles.warning_text_style(app)
        dark_danger = ui_styles.danger_text_style(app)

        assert light_warn != dark_warn
        assert light_danger != dark_danger
        assert "color:" in light_warn and "color:" in dark_warn
    finally:
        apply_application_theme(app, THEME_SYSTEM)
