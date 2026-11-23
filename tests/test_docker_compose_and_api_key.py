from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from dxsync.docker_compose import (
    DockerComposeDbService,
    detect_db_services_from_compose,
    export_sql_dump_from_compose,
    find_compose_file,
)
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.secure_store import xserver_api_key_reference
from dxsync.setup_helpers import load_saved_xserver_api_key, persist_xserver_api_key
from dxsync.wordpress_setup_dialog import WordPressSetupWizard


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeApiKeyStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def store_api_key(self, project_id: str, api_key: str) -> str:
        ref = xserver_api_key_reference(project_id)
        self.values[ref] = api_key
        return ref

    def retrieve_api_key(self, reference: str) -> str | None:
        return self.values.get(reference)

    def delete_api_key(self, reference: str) -> None:
        self.values.pop(reference, None)


def test_find_and_detect_compose_db_service(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        """services:
  web:
    image: php:8.3-apache
  db:
    image: mariadb:10.11
    environment:
      MARIADB_DATABASE: wp_local
      MARIADB_USER: wp_user
      MARIADB_PASSWORD: secret
      MARIADB_ROOT_PASSWORD: rootsecret
""",
        encoding="utf-8",
    )
    nested = tmp_path / "public"
    nested.mkdir()

    found = find_compose_file(nested)
    assert found == compose

    services = detect_db_services_from_compose(compose)
    assert services == (
        DockerComposeDbService(
            service_name="db",
            image="mariadb:10.11",
            database="wp_local",
            user="wp_user",
            password="secret",
            root_password="rootsecret",
        ),
    )


def test_export_sql_dump_from_compose_writes_sql_file(tmp_path: Path, monkeypatch) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services:\n  db:\n    image: mysql:8.0\n", encoding="utf-8")
    service = DockerComposeDbService(
        service_name="db",
        image="mysql:8.0",
        database="app_db",
        root_password="pw",
    )
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout=b"-- SQL dump\nCREATE TABLE t (id INT);\n", stderr=b"")

    monkeypatch.setattr("dxsync.docker_compose.subprocess.run", fake_run)
    out = export_sql_dump_from_compose(compose, service, output_path=tmp_path / "out.sql")
    assert out == tmp_path / "out.sql"
    assert "CREATE TABLE t" in out.read_text(encoding="utf-8")
    assert "MYSQL_PWD=pw" in captured["command"]


def test_api_key_store_round_trip_and_wizard_prepopulation(tmp_path: Path) -> None:
    app()
    store = FakeApiKeyStore()
    persist_xserver_api_key(store, "proj-wp", "xs_saved_key", remember=True)
    assert load_saved_xserver_api_key(store, "proj-wp") == "xs_saved_key"

    mapping = SyncMapping(
        mapping_id="m1",
        name="main",
        local_root=str(tmp_path),
        remote_root="/home/user/example.com/public_html",
    )
    project = Project(
        project_id="proj-wp",
        name="WP",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="sv.xsrv.jp",
        ssh_port=10022,
        ssh_username="user",
        host_key_fingerprint="SHA256:abc",
        private_key_reference=str(tmp_path / "id_ed25519"),
        mappings=(mapping,),
    )
    wizard = WordPressSetupWizard(project, mapping, secure_store=store)
    assert wizard.api_key_edit.text() == "xs_saved_key"
    assert wizard.save_api_key_check.isChecked()

    persist_xserver_api_key(store, "proj-wp", "", remember=False)
    assert load_saved_xserver_api_key(store, "proj-wp") is None
