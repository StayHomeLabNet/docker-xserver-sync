import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFormLayout

from dxsync.models import SiteType
from dxsync.project_dialog import ProjectDialog, RuntimePathsDialog
from dxsync.storage import ProjectStore
from dxsync.secure_store import MemoryHostKeyStore


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_runtime_paths_dialog_requires_confirmation_and_normalizes_paths() -> None:
    app()
    dialog = RuntimePathsDialog()
    save_button = dialog.buttons.button(QDialogButtonBox.Ok)

    assert not save_button.isEnabled()
    dialog.paths_edit.setPlainText("userfiles\ndata/sessions/\nuserfiles/")
    dialog.confirm.setChecked(True)
    assert save_button.isEnabled()

    dialog._accept_paths()

    assert dialog.patterns == ("userfiles/", "data/sessions/")


def test_project_dialog_marks_new_custom_php_mapping_unconfirmed(tmp_path) -> None:
    app()
    dialog = ProjectDialog(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    )
    dialog.site_combo.setCurrentIndex(dialog.site_combo.findData(SiteType.CUSTOM_PHP.value))
    dialog._append_mapping("public", str(tmp_path), "/home/user/public_html")

    assert dialog.runtime_paths_button.isEnabled()
    assert dialog.mapping_table.item(0, 3).text() == "未確認（同期不可）"
    mapping = dialog._read_mappings()[0]
    assert not mapping.runtime_paths_confirmed


def test_project_dialog_site_order_and_mapping_button_order(tmp_path) -> None:
    app()
    dialog = ProjectDialog(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    )

    assert [dialog.site_combo.itemData(index) for index in range(3)] == [
        SiteType.CUSTOM_PHP.value,
        SiteType.WORDPRESS.value,
        SiteType.LARAVEL.value,
    ]
    assert dialog.mapping_header.indexOf(dialog.add_mapping_button) < (
        dialog.mapping_header.indexOf(dialog.runtime_paths_button)
    )


def test_project_dialog_hints_span_full_width_and_content_scrolls(tmp_path) -> None:
    app()
    dialog = ProjectDialog(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    )

    row, role = dialog.details_form.getWidgetPosition(dialog.site_help)

    assert row >= 0
    assert role == QFormLayout.SpanningRole
    assert dialog.scroll_area.widgetResizable()
    assert dialog.minimumWidth() >= 760
    dialog.close()
