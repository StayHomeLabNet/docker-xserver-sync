from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .models import ChangeType, FileChange, FileRecord, Project, SiteType, SyncMapping, SyncPlan
from .secure_store import (
    HostKeySecureStore,
    KeyringHostKeyStore,
    SecureStoreError,
)


SCHEMA_VERSION = 5


def get_default_state_directory() -> Path:
    override = os.environ.get("DXSYNC_STATE_DIR")
    if override:
        return Path(override)
    try:
        from PySide6.QtCore import QStandardPaths

        location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if location:
            return Path(location)
    except Exception:
        pass
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        return base / "Docker XServer Sync"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Docker XServer Sync"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
        return base / "Docker XServer Sync"


def get_default_store(secure_store: HostKeySecureStore | None = None) -> ProjectStore:
    directory = get_default_state_directory()
    return ProjectStore(directory / "state.db", secure_store=secure_store)


@dataclass(frozen=True)
class ProjectSecurityStatus:
    project_id: str
    state: str
    backend: str
    message: str


@dataclass(frozen=True)
class StoredPlan:
    project_id: str
    plan: SyncPlan
    status: str
    staging_path: str | None


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    project_id: str
    mapping_id: str
    kind: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    details: dict
    error: str | None


@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    project_id: str
    mapping_id: str
    plan_id: str
    backup_directory: str
    manifest_path: str
    created_at: datetime
    file_count: int
    total_bytes: int
    status: str = "available"


class ProjectStore:
    def __init__(
        self,
        database_path: Path,
        secure_store: HostKeySecureStore | None = None,
    ) -> None:
        self.database_path = database_path
        self.secure_store = secure_store or KeyringHostKeyStore()
        self._initialized = False

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    site_type TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    ssh_host TEXT NOT NULL,
                    ssh_port INTEGER NOT NULL,
                    ssh_username TEXT NOT NULL,
                    host_key_fingerprint TEXT NOT NULL,
                    host_key_reference TEXT NOT NULL DEFAULT '',
                    private_key_reference TEXT NOT NULL,
                    mappings_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    mapping_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    staging_path TEXT,
                    local_snapshot_sha256 TEXT NOT NULL,
                    remote_snapshot_sha256 TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL DEFAULT '',
                    changes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_plans_project_status
                    ON plans(project_id, status);
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    mapping_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_operations_project_started
                    ON operations(project_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS backups (
                    backup_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    mapping_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    backup_directory TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_backups_project_created
                    ON backups(project_id, created_at DESC);
                """
            )
            plan_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(plans)").fetchall()
            }
            if "config_sha256" not in plan_columns:
                connection.execute(
                    "ALTER TABLE plans ADD COLUMN config_sha256 TEXT NOT NULL DEFAULT ''"
                )
            project_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "host_key_reference" not in project_columns:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN "
                    "host_key_reference TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        self._initialized = True
        self._migrate_legacy_host_keys()

    def _migrate_legacy_host_keys(self) -> None:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT project_id, host_key_fingerprint FROM projects "
                "WHERE host_key_reference = '' AND host_key_fingerprint != ''"
            ).fetchall()
        migrated = False
        for row in rows:
            project_id = row["project_id"]
            fingerprint = row["host_key_fingerprint"]
            try:
                reference = self.secure_store.store(project_id, fingerprint)
                if self.secure_store.retrieve(reference) != fingerprint:
                    raise SecureStoreError("Credential verification returned a different value")
            except SecureStoreError as exc:
                with self._connection() as connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                        (f"host_key_migration_error:{project_id}", str(exc)),
                    )
                continue
            with self._connection() as connection:
                connection.execute(
                    "UPDATE projects SET host_key_fingerprint = '', "
                    "host_key_reference = ? WHERE project_id = ? "
                    "AND host_key_fingerprint = ?",
                    (reference, project_id, fingerprint),
                )
                connection.execute(
                    "DELETE FROM metadata WHERE key = ?",
                    (f"host_key_migration_error:{project_id}",),
                )
            migrated = True
        if migrated:
            connection = sqlite3.connect(self.database_path)
            try:
                connection.execute("VACUUM")
            finally:
                connection.close()

    def save(self, project: Project) -> None:
        self.initialize()
        reference = self.secure_store.store(
            project.project_id, project.host_key_fingerprint
        )
        mappings_json = json.dumps(
            [asdict(mapping) for mapping in project.mappings],
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    project_id, name, site_type, domain, ssh_host, ssh_port,
                    ssh_username, host_key_fingerprint, host_key_reference,
                    private_key_reference,
                    mappings_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name=excluded.name,
                    site_type=excluded.site_type,
                    domain=excluded.domain,
                    ssh_host=excluded.ssh_host,
                    ssh_port=excluded.ssh_port,
                    ssh_username=excluded.ssh_username,
                    host_key_fingerprint=excluded.host_key_fingerprint,
                    host_key_reference=excluded.host_key_reference,
                    private_key_reference=excluded.private_key_reference,
                    mappings_json=excluded.mappings_json,
                    updated_at=excluded.updated_at
                """,
                (
                    project.project_id,
                    project.name,
                    project.site_type.value,
                    project.domain,
                    project.ssh_host,
                    project.ssh_port,
                    project.ssh_username,
                    "",
                    reference,
                    project.private_key_reference,
                    mappings_json,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )

    def get(self, project_id: str) -> Project | None:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None
        fingerprint = row["host_key_fingerprint"]
        reference = row["host_key_reference"]
        if reference:
            try:
                fingerprint = self.secure_store.retrieve(reference) or ""
            except SecureStoreError:
                fingerprint = ""
        mappings = tuple(
            SyncMapping(
                mapping_id=item["mapping_id"],
                name=item["name"],
                local_root=item["local_root"],
                remote_root=item["remote_root"],
                exclude_patterns=tuple(item.get("exclude_patterns", ())),
                protect_patterns=tuple(item.get("protect_patterns", ())),
                runtime_paths_confirmed=item.get("runtime_paths_confirmed", False),
                file_mode=item.get("file_mode", 0o644),
                directory_mode=item.get("directory_mode", 0o755),
            )
            for item in json.loads(row["mappings_json"])
        )
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            site_type=SiteType(row["site_type"]),
            domain=row["domain"],
            ssh_host=row["ssh_host"],
            ssh_port=row["ssh_port"],
            ssh_username=row["ssh_username"],
            host_key_fingerprint=fingerprint,
            private_key_reference=row["private_key_reference"],
            mappings=mappings,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_projects(self) -> tuple[Project, ...]:
        self.initialize()
        with self._connection() as connection:
            ids = [
                row["project_id"]
                for row in connection.execute(
                    "SELECT project_id FROM projects ORDER BY name COLLATE NOCASE"
                ).fetchall()
            ]
        return tuple(project for project_id in ids if (project := self.get(project_id)))

    def delete(self, project_id: str) -> bool:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT host_key_reference FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is not None and row["host_key_reference"]:
            self.secure_store.delete(row["host_key_reference"])
        with self._connection() as connection:
            connection.execute("DELETE FROM plans WHERE project_id = ?", (project_id,))
            cursor = connection.execute(
                "DELETE FROM projects WHERE project_id = ?", (project_id,)
            )
        return cursor.rowcount > 0

    def security_status(self, project_id: str) -> ProjectSecurityStatus:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT host_key_fingerprint, host_key_reference FROM projects "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        backend = self.secure_store.backend_name
        if row is None:
            return ProjectSecurityStatus(project_id, "missing", backend, "Project not found")
        reference = row["host_key_reference"]
        if not reference:
            return ProjectSecurityStatus(
                project_id,
                "legacy",
                backend,
                "Host-key fingerprint is still stored in the local database",
            )
        try:
            fingerprint = self.secure_store.retrieve(reference)
        except SecureStoreError as exc:
            return ProjectSecurityStatus(project_id, "unavailable", backend, str(exc))
        if not fingerprint:
            return ProjectSecurityStatus(
                project_id,
                "missing",
                backend,
                "Host-key fingerprint is missing from the OS credential store",
            )
        return ProjectSecurityStatus(
            project_id,
            "secure",
            backend,
            "Host-key fingerprint is stored in the OS credential store",
        )

    def update_host_key(self, project_id: str, fingerprint: str) -> bool:
        self.initialize()
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if exists is None:
            return False
        reference = self.secure_store.store(project_id, fingerprint)
        with self._connection() as connection:
            connection.execute(
                "UPDATE projects SET host_key_fingerprint = '', "
                "host_key_reference = ?, updated_at = ? WHERE project_id = ?",
                (reference, datetime.now(timezone.utc).isoformat(), project_id),
            )
            connection.execute(
                "DELETE FROM metadata WHERE key = ?",
                (f"host_key_migration_error:{project_id}",),
            )
        return True

    def save_plan(
        self,
        project_id: str,
        plan: SyncPlan,
        staging_path: str | None = None,
    ) -> None:
        self.initialize()
        changes = [
            {
                "relative_path": change.relative_path,
                "change_type": change.change_type.value,
                "local": change.local.to_dict() if change.local else None,
                "remote": change.remote.to_dict() if change.remote else None,
                "reason": change.reason,
            }
            for change in plan.changes
        ]
        with self._connection() as connection:
            connection.execute(
                "UPDATE plans SET status = 'superseded' "
                "WHERE project_id = ? AND mapping_id = ? AND status = 'ready'",
                (project_id, plan.mapping_id),
            )
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, project_id, mapping_id, created_at, expires_at,
                    status, staging_path, local_snapshot_sha256,
                    remote_snapshot_sha256, config_sha256, changes_json
                ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    project_id,
                    plan.mapping_id,
                    plan.created_at.isoformat(),
                    plan.expires_at.isoformat(),
                    staging_path,
                    plan.local_snapshot_sha256,
                    plan.remote_snapshot_sha256,
                    plan.config_sha256,
                    json.dumps(changes, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get_plan(self, plan_id: str) -> StoredPlan | None:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            return None
        changes = tuple(
            FileChange(
                relative_path=item["relative_path"],
                change_type=ChangeType(item["change_type"]),
                local=FileRecord.from_dict(item["local"]) if item["local"] else None,
                remote=FileRecord.from_dict(item["remote"]) if item["remote"] else None,
                reason=item.get("reason"),
            )
            for item in json.loads(row["changes_json"])
        )
        plan = SyncPlan(
            plan_id=row["plan_id"],
            mapping_id=row["mapping_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            changes=changes,
            local_snapshot_sha256=row["local_snapshot_sha256"],
            remote_snapshot_sha256=row["remote_snapshot_sha256"],
            config_sha256=row["config_sha256"],
        )
        return StoredPlan(
            project_id=row["project_id"],
            plan=plan,
            status=row["status"],
            staging_path=row["staging_path"],
        )

    def expire_plans(self, now: datetime | None = None) -> int:
        self.initialize()
        current = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT plan_id, expires_at FROM plans WHERE status = 'ready'"
            ).fetchall()
            expired_ids = [
                row["plan_id"]
                for row in rows
                if datetime.fromisoformat(row["expires_at"]) <= current
            ]
            if expired_ids:
                connection.executemany(
                    "UPDATE plans SET status = 'expired' WHERE plan_id = ?",
                    ((plan_id,) for plan_id in expired_ids),
                )
        return len(expired_ids)

    def set_plan_status(self, plan_id: str, status: str) -> bool:
        allowed = {"ready", "expired", "superseded", "invalid", "used"}
        if status not in allowed:
            raise ValueError(f"Unsupported plan status: {status}")
        self.initialize()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE plans SET status = ? WHERE plan_id = ?", (status, plan_id)
            )
        return cursor.rowcount > 0

    def list_plans(self, statuses: set[str] | None = None) -> tuple[StoredPlan, ...]:
        self.initialize()
        with self._connection() as connection:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    f"SELECT plan_id FROM plans WHERE status IN ({placeholders}) ORDER BY created_at",
                    tuple(sorted(statuses)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT plan_id FROM plans ORDER BY created_at"
                ).fetchall()
        return tuple(
            stored
            for row in rows
            if (stored := self.get_plan(row["plan_id"])) is not None
        )

    def start_operation(
        self,
        *,
        project_id: str,
        mapping_id: str,
        kind: str,
        details: dict | None = None,
    ) -> OperationRecord:
        self.initialize()
        operation = OperationRecord(
            operation_id=str(uuid4()),
            project_id=project_id,
            mapping_id=mapping_id,
            kind=kind,
            status="running",
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            details=details or {},
            error=None,
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, project_id, mapping_id, kind, status,
                    started_at, completed_at, details_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    operation.operation_id,
                    operation.project_id,
                    operation.mapping_id,
                    operation.kind,
                    operation.status,
                    operation.started_at.isoformat(),
                    json.dumps(operation.details, ensure_ascii=False, sort_keys=True),
                ),
            )
        return operation

    def finish_operation(
        self,
        operation_id: str,
        *,
        status: str,
        details: dict | None = None,
        error: str | None = None,
    ) -> bool:
        allowed = {"succeeded", "failed", "cancelled", "blocked", "interrupted"}
        if status not in allowed:
            raise ValueError(f"Unsupported operation status: {status}")
        self.initialize()
        completed_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE operations
                SET status = ?, completed_at = ?, details_json = ?, error = ?
                WHERE operation_id = ? AND status = 'running'
                """,
                (
                    status,
                    completed_at,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    error,
                    operation_id,
                ),
            )
        return cursor.rowcount > 0

    def update_operation_details(self, operation_id: str, details: dict) -> bool:
        """Persist a resumable checkpoint without changing operation status."""
        self.initialize()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE operations SET details_json = ? WHERE operation_id = ?",
                (
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    operation_id,
                ),
            )
        return cursor.rowcount > 0

    def get_operation(self, operation_id: str) -> OperationRecord | None:
        return next(
            (
                operation
                for operation in self.list_operations(limit=10_000)
                if operation.operation_id == operation_id
            ),
            None,
        )

    def recover_interrupted_operations(self) -> tuple[OperationRecord, ...]:
        """Mark operations left running by a previous process as interrupted."""
        running = tuple(
            operation for operation in self.list_operations(limit=10_000)
            if operation.status == "running"
        )
        for operation in running:
            details = dict(operation.details)
            details["detected_on_startup"] = True
            self.finish_operation(
                operation.operation_id,
                status="interrupted",
                details=details,
                error="Application exited before this operation completed",
            )
        return running

    def list_operations(
        self,
        project_id: str | None = None,
        *,
        limit: int = 200,
    ) -> tuple[OperationRecord, ...]:
        self.initialize()
        with self._connection() as connection:
            if project_id:
                rows = connection.execute(
                    "SELECT * FROM operations WHERE project_id = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM operations ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return tuple(
            OperationRecord(
                operation_id=row["operation_id"],
                project_id=row["project_id"],
                mapping_id=row["mapping_id"],
                kind=row["kind"],
                status=row["status"],
                started_at=datetime.fromisoformat(row["started_at"]),
                completed_at=(
                    datetime.fromisoformat(row["completed_at"])
                    if row["completed_at"]
                    else None
                ),
                details=json.loads(row["details_json"]),
                error=row["error"],
            )
            for row in rows
        )

    def save_backup(self, backup: BackupRecord) -> None:
        self.initialize()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO backups (
                    backup_id, project_id, mapping_id, plan_id,
                    backup_directory, manifest_path, created_at,
                    file_count, total_bytes, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(backup_id) DO UPDATE SET
                    backup_directory=excluded.backup_directory,
                    manifest_path=excluded.manifest_path,
                    file_count=excluded.file_count,
                    total_bytes=excluded.total_bytes,
                    status=excluded.status
                """,
                (
                    backup.backup_id,
                    backup.project_id,
                    backup.mapping_id,
                    backup.plan_id,
                    backup.backup_directory,
                    backup.manifest_path,
                    backup.created_at.isoformat(),
                    backup.file_count,
                    backup.total_bytes,
                    backup.status,
                ),
            )

    def get_backup(self, backup_id: str) -> BackupRecord | None:
        return next(
            (backup for backup in self.list_backups() if backup.backup_id == backup_id),
            None,
        )

    def set_backup_status(self, backup_id: str, status: str) -> bool:
        allowed = {"available", "restored", "invalid", "deleted"}
        if status not in allowed:
            raise ValueError(f"Unsupported backup status: {status}")
        self.initialize()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE backups SET status = ? WHERE backup_id = ?",
                (status, backup_id),
            )
        return cursor.rowcount > 0

    def list_backups(self, project_id: str | None = None) -> tuple[BackupRecord, ...]:
        self.initialize()
        with self._connection() as connection:
            if project_id:
                rows = connection.execute(
                    "SELECT * FROM backups WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM backups ORDER BY created_at DESC"
                ).fetchall()
        return tuple(
            BackupRecord(
                backup_id=row["backup_id"],
                project_id=row["project_id"],
                mapping_id=row["mapping_id"],
                plan_id=row["plan_id"],
                backup_directory=row["backup_directory"],
                manifest_path=row["manifest_path"],
                created_at=datetime.fromisoformat(row["created_at"]),
                file_count=row["file_count"],
                total_bytes=row["total_bytes"],
                status=row["status"],
            )
            for row in rows
        )

