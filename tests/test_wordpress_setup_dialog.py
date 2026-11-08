import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from dxsync.models import Project, SiteType, SyncMapping
from dxsync.wordpress_setup_dialog import WordPressSetupWizard


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_wordpress_setup_wizard_builds_request_without_persisting_secrets(
    tmp_path: Path,
) -> None:
    app()
    for directory in ("wp-admin", "wp-includes", "wp-content"):
        (tmp_path / directory).mkdir()
    (tmp_path / "wp-load.php").write_text("<?php", encoding="utf-8")
    (tmp_path / "index.php").write_text("<?php", encoding="utf-8")
    plugin = tmp_path / "wp-content" / "plugins" / "example"
    plugin.mkdir(parents=True)
    (plugin / "plugin.php").write_text("<?php", encoding="utf-8")
    (plugin / "README.md").write_text("plugin docs", encoding="utf-8")
    (tmp_path / "README.md").write_text("project docs", encoding="utf-8")
    (tmp_path / "compose.yaml").write_text("services: {}", encoding="utf-8")
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")
    mapping = SyncMapping.create(
        "WordPress",
        str(tmp_path),
        "/home/sample/example.com/public_html",
    )
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(mapping,),
    )
    wizard = WordPressSetupWizard(project, mapping)
    assert wizard.db_host_edit.text() == "localhost"
    assert "localhost" in wizard.db_host_edit.placeholderText()
    assert wizard.minimumWidth() == 900
    assert all(
        editor.minimumWidth() >= 620
        for editor in wizard.findChildren(QLineEdit)
    )
    wizard.sql_edit.setText(str(sql))
    wizard.db_host_edit.setText("mysql.example.xserver.jp")
    wizard.database_mode_combo.setCurrentIndex(
        wizard.database_mode_combo.findData("api")
    )
    wizard.api_key_edit.setText("xs_secret")
    wizard._refresh_file_selection()

    value = wizard.request_value()

    assert value.local_root == tmp_path
    assert value.sql_dump == sql
    assert value.remote_root == mapping.remote_root
    assert wizard.servername == "sample.xsrv.jp"
    assert wizard.api_key == "xs_secret"
    assert value.php_cli_version == "8.3"
    assert "PHP 8.3" in wizard.php_cli_version_combo.currentText()
    assert "index.php" in value.include_paths
    assert "wp-content/plugins/example/plugin.php" in value.include_paths
    assert "wp-content/plugins/example/README.md" in value.include_paths
    assert "README.md" not in value.include_paths
    assert "compose.yaml" not in value.include_paths

    wizard.file_tree.set_all_checked(False)
    selected = wizard.request_value().include_paths
    assert "index.php" in selected
    assert not any(path.startswith("wp-content/") for path in selected)
    wizard.close()


def test_wordpress_setup_wizard_supports_existing_database_without_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app()
    for directory in ("wp-admin", "wp-includes", "wp-content"):
        (tmp_path / directory).mkdir()
    (tmp_path / "wp-load.php").write_text("<?php", encoding="utf-8")
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")
    mapping = SyncMapping.create(
        "WordPress",
        str(tmp_path),
        "/home/sample/example.com/public_html",
    )
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(mapping,),
    )
    wizard = WordPressSetupWizard(project, mapping)
    wizard.sql_edit.setText(str(sql))
    wizard.db_host_edit.setText("mysql.example.xserver.jp")
    assert wizard.existing_db_name_edit.text() == ""
    assert wizard.existing_db_user_edit.text() == ""
    wizard.existing_db_name_edit.setText("sample_wordpress")
    wizard.existing_db_user_edit.setText("sample_wp_user")

    value = wizard.request_value()

    assert not wizard.uses_xserver_api
    assert wizard.api_key == ""
    assert value.database_mode == "existing"
    assert value.existing_db_name == "sample_wordpress"
    assert value.existing_db_user == "sample_wp_user"
    assert not wizard.api_key_edit.isEnabled()
    assert wizard.existing_db_name_edit.isEnabled()
    assert wizard.existing_db_prepared_check.isVisible() is False
    wizard.show()
    app().processEvents()
    wizard.setCurrentId(wizard.pageIds()[2])
    app().processEvents()
    assert wizard.existing_db_prepared_check.isVisible()
    assert "アプリはDBやMySQLユーザーを作成しません" in (
        wizard.database_instructions.text()
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        "dxsync.wordpress_setup_dialog.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    wizard.confirm_domain_edit.setText(project.domain)
    wizard.accept()
    assert warnings
    assert "アクセス権" in warnings[-1]
    assert wizard.result() == 0
    wizard.close()


def test_resume_defaults_to_remote_wp_config(tmp_path: Path) -> None:
    app()
    for directory in ("wp-admin", "wp-includes", "wp-content"):
        (tmp_path / directory).mkdir()
    (tmp_path / "wp-load.php").write_text("<?php", encoding="utf-8")
    mapping = SyncMapping.create(
        "WordPress",
        str(tmp_path),
        "/home/sample/example.com/public_html",
    )
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(mapping,),
    )

    wizard = WordPressSetupWizard(
        project, mapping, resume_existing=True
    )
    value = wizard.request_value()

    assert value.resume_existing
    assert value.use_remote_wp_config
    assert not wizard.existing_db_name_edit.isEnabled()
    assert not wizard.existing_db_user_edit.isEnabled()
    assert not wizard.db_password_edit.isEnabled()
    assert wizard.sql_edit.isEnabled()
    assert "DBが空" in wizard.sql_note.text()
    assert "DBパスワードをアプリへ読み出さず" in (
        wizard.database_instructions.text()
    )

    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")
    wizard.sql_edit.setText(str(sql))
    assert wizard.request_value().import_sql_if_database_empty
    wizard.close()
