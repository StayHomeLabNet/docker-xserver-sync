import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
    wizard.sql_edit.setText(str(sql))
    wizard.db_host_edit.setText("mysql.example.xserver.jp")
    wizard.api_key_edit.setText("xs_secret")
    wizard._refresh_file_selection()

    value = wizard.request_value()

    assert value.local_root == tmp_path
    assert value.sql_dump == sql
    assert value.remote_root == mapping.remote_root
    assert wizard.servername == "sample.xsrv.jp"
    assert wizard.api_key == "xs_secret"
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
