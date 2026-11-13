import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFormLayout

from dxsync.models import Project, SiteType, SyncMapping
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


def test_project_dialog_duplicate_button_position_and_visibility(tmp_path) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    key_file = tmp_path / "id_rsa"
    key_file.write_text("dummy key")

    # Creating a new project: duplicate button is hidden
    dialog_new = ProjectDialog(store)
    assert dialog_new.duplicate_button.isHidden()
    assert dialog_new.duplicate_button.text() == "複製…"
    assert dialog_new.intro_row.indexOf(dialog_new.duplicate_button) < (
        dialog_new.intro_row.indexOf(dialog_new.help_button)
    )
    dialog_new.close()

    # Editing an existing project: duplicate button is visible
    mapping = SyncMapping.create("site", str(tmp_path), "/home/user/public_html")
    project = Project.create(
        name="example.com",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="server.xsrv.jp",
        ssh_username="server",
        host_key_fingerprint="SHA256:testfingerprint",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store.save(project)

    dialog_edit = ProjectDialog(store, project)
    assert not dialog_edit.duplicate_button.isHidden()
    assert dialog_edit.intro_row.indexOf(dialog_edit.duplicate_button) < (
        dialog_edit.intro_row.indexOf(dialog_edit.help_button)
    )
    dialog_edit.close()


def test_project_dialog_duplicate_from_prefills_and_increments_name(tmp_path) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    key_file = tmp_path / "id_rsa"
    key_file.write_text("dummy key")

    mapping = SyncMapping.create("site", str(tmp_path), "/home/user/public_html")
    project = Project.create(
        name="mysite.com",
        site_type=SiteType.WORDPRESS,
        domain="mysite.com",
        ssh_host="server.xsrv.jp",
        ssh_username="server",
        host_key_fingerprint="SHA256:testfingerprint",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store.save(project)

    # First duplicate
    dialog1 = ProjectDialog(store, duplicate_from=project)
    assert dialog1.windowTitle() == "プロジェクトを複製"
    assert dialog1.name_edit.text() == "mysite.com のコピー"
    assert dialog1.domain_edit.text() == "mysite.com"
    assert dialog1.host_edit.text() == "server.xsrv.jp"
    dialog1._save()
    assert dialog1.saved_project is not None
    assert dialog1.saved_project.project_id != project.project_id
    assert dialog1.saved_project.mappings[0].mapping_id != project.mappings[0].mapping_id
    dialog1.close()

    # Second duplicate should increment name
    dialog2 = ProjectDialog(store, duplicate_from=project)
    assert dialog2.name_edit.text() == "mysite.com のコピー 2"
    dialog2.close()
