from dxsync import ui_styles


def test_shared_ui_styles_follow_the_active_qt_palette() -> None:
    styles = {
        name: value
        for name, value in vars(ui_styles).items()
        if name.endswith("_STYLE")
    }

    assert styles
    assert all("palette(" in style for style in styles.values())
    assert all("#" not in style for style in styles.values())
