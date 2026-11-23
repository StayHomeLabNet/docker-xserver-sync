from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QInputDialog, QMessageBox

from .docker_compose import (
    DockerComposeError,
    detect_db_services_from_compose,
    export_sql_dump_from_compose,
    find_compose_file,
)
from .secure_store import xserver_api_key_reference


def load_saved_xserver_api_key(secure_store: Any, project_id: str) -> str | None:
    if secure_store is None:
        return None
    retrieve = getattr(secure_store, "retrieve_api_key", None)
    if not callable(retrieve):
        return None
    try:
        return retrieve(xserver_api_key_reference(project_id))
    except Exception:
        return None


def persist_xserver_api_key(
    secure_store: Any,
    project_id: str,
    api_key: str,
    *,
    remember: bool,
) -> None:
    if secure_store is None:
        return
    if remember and api_key.strip():
        store = getattr(secure_store, "store_api_key", None)
        if callable(store):
            store(project_id, api_key.strip())
    else:
        delete = getattr(secure_store, "delete_api_key", None)
        if callable(delete):
            delete(xserver_api_key_reference(project_id))


def run_docker_compose_sql_dump_ui(parent: Any, local_root: str) -> str | None:
    root_path = Path(local_root).expanduser()
    compose_path = find_compose_file(root_path)
    if compose_path is None:
        QMessageBox.information(
            parent,
            "Docker Compose が見つかりません",
            f"'{root_path}' および親フォルダから compose.yaml / docker-compose.yml が見つかりませんでした。",
        )
        return None

    try:
        services = detect_db_services_from_compose(compose_path)
    except DockerComposeError as exc:
        QMessageBox.warning(parent, "Compose 解析エラー", str(exc))
        return None

    if not services:
        QMessageBox.information(
            parent,
            "DBサービスが見つかりません",
            f"{compose_path.name} から MySQL / MariaDB サービスを検出できませんでした。",
        )
        return None

    selected = services[0]
    if len(services) > 1:
        labels = [f"{svc.service_name} ({svc.database or 'DB未検出'})" for svc in services]
        choice, ok = QInputDialog.getItem(
            parent,
            "DBサービスを選択",
            "SQLダンプを出力する Docker サービスを選択してください:",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        selected = services[labels.index(choice)]

    try:
        dump_path = export_sql_dump_from_compose(compose_path, selected)
    except DockerComposeError as exc:
        QMessageBox.warning(parent, "SQLダンプ生成失敗", str(exc))
        return None

    QMessageBox.information(
        parent,
        "SQLダンプ生成完了",
        f"Docker サービス '{selected.service_name}' から SQL ダンプを生成しました:\n{dump_path}",
    )
    return str(dump_path)
