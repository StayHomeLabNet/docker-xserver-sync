from __future__ import annotations

from dataclasses import dataclass


DEFAULT_SFTP_MAX_WORKERS = 4
MIN_SFTP_MAX_WORKERS = 1
MAX_SFTP_MAX_WORKERS = 16


def normalize_sftp_max_workers(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_SFTP_MAX_WORKERS
    return max(MIN_SFTP_MAX_WORKERS, min(MAX_SFTP_MAX_WORKERS, parsed))


@dataclass(frozen=True)
class AutoSyncPreferences:
    backup_mode: str = "always"
    remote_only_policy: str = "keep"
    health_check_enabled: bool = True
    health_check_path: str = "/"
    max_changed_files: int = 1000
    max_update_files: int = 500
    max_upload_mib: int = 1024
    require_domain_confirmation: bool = True
    skip_approval_dialog: bool = False
    pre_sync_command: str = ""
    post_sync_remote_command: str = ""
    post_sync_webhook_url: str = ""
    auto_delete_allowed_folders: tuple[str, ...] = ()
    sftp_max_workers: int = DEFAULT_SFTP_MAX_WORKERS
    auto_maintenance_mode: bool = False

    def resolved_backup_skip(self, update_count: int, delete_count: int = 0) -> bool:
        if self.backup_mode == "skip":
            return True
        return self.backup_mode == "add_only" and update_count == 0 and delete_count == 0


def auto_sync_policy_issues(
    preferences: AutoSyncPreferences,
    *,
    add_count: int,
    update_count: int,
    remote_only_count: int,
    upload_bytes: int,
) -> tuple[str, ...]:
    issues: list[str] = []
    changed_count = add_count + update_count
    if preferences.remote_only_policy == "stop" and remote_only_count:
        issues.append(
            f"REMOTE_ONLYが{remote_only_count}件あるため停止設定に該当しました。"
        )
    if preferences.max_changed_files and changed_count > preferences.max_changed_files:
        issues.append(
            f"ADD＋UPDATE {changed_count}件が上限"
            f"{preferences.max_changed_files}件を超えています。"
        )
    if preferences.max_update_files and update_count > preferences.max_update_files:
        issues.append(
            f"UPDATE {update_count}件が上限"
            f"{preferences.max_update_files}件を超えています。"
        )
    max_upload_bytes = preferences.max_upload_mib * 1024 * 1024
    if max_upload_bytes and upload_bytes > max_upload_bytes:
        issues.append(
            f"転送量{upload_bytes:,} bytesが上限"
            f"{preferences.max_upload_mib} MiBを超えています。"
        )
    return tuple(issues)
