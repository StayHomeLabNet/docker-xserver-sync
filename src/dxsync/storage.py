from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import ChangeType, FileChange, FileRecord, Project, SiteType, SyncMapping, SyncPlan


SCHEMA_VERSION = 3


@dataclass(frozen=True)
class StoredPlan:
    project_id: str
    plan: SyncPlan
    status: str
    staging_path: str | None


class ProjectStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

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
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def save(self, project: Project) -> None:
        self.initialize()
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
                    ssh_username, host_key_fingerprint, private_key_reference,
                    mappings_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name=excluded.name,
                    site_type=excluded.site_type,
                    domain=excluded.domain,
                    ssh_host=excluded.ssh_host,
                    ssh_port=excluded.ssh_port,
                    ssh_username=excluded.ssh_username,
                    host_key_fingerprint=excluded.host_key_fingerprint,
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
                    project.host_key_fingerprint,
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
        mappings = tuple(
            SyncMapping(
                mapping_id=item["mapping_id"],
                name=item["name"],
                local_root=item["local_root"],
                remote_root=item["remote_root"],
                exclude_patterns=tuple(item.get("exclude_patterns", ())),
                protect_patterns=tuple(item.get("protect_patterns", ())),
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
            host_key_fingerprint=row["host_key_fingerprint"],
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
            connection.execute("DELETE FROM plans WHERE project_id = ?", (project_id,))
            cursor = connection.execute(
                "DELETE FROM projects WHERE project_id = ?", (project_id,)
            )
        return cursor.rowcount > 0

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

