from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import __version__
from .operations import (
    OperationError,
    execute_auto_sync,
    execute_dry_run,
    execute_prune_backups,
    execute_restore,
    execute_verify,
    find_project,
    get_project_detail,
    list_backups_summary,
    list_projects_summary,
)
from .planner import (
    DEFAULT_PLAN_VALIDITY_MINUTES,
    MAX_PLAN_VALIDITY_MINUTES,
    MIN_PLAN_VALIDITY_MINUTES,
)
from .storage import ProjectStore, get_default_store

TOOLS = [
    {
        "name": "dxsync_list_projects",
        "description": "List all configured projects in Docker XServer Sync.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "dxsync_get_project",
        "description": (
            "Get detailed configuration, path mappings, and security status for a project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                }
            },
            "required": ["project"],
        },
    },
    {
        "name": "dxsync_dry_run",
        "description": (
            "Run a read-only dry run against XServer to preview file changes "
            "(added, updated, deleted, protected) without modifying remote files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                },
                "mapping": {
                    "type": "string",
                    "description": "Optional mapping name or ID",
                },
                "plan_validity_minutes": {
                    "type": "integer",
                    "description": (
                        f"Plan validity window in minutes "
                        f"({MIN_PLAN_VALIDITY_MINUTES}-{MAX_PLAN_VALIDITY_MINUTES}, "
                        f"default: {DEFAULT_PLAN_VALIDITY_MINUTES})"
                    ),
                    "minimum": MIN_PLAN_VALIDITY_MINUTES,
                    "maximum": MAX_PLAN_VALIDITY_MINUTES,
                    "default": DEFAULT_PLAN_VALIDITY_MINUTES,
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "dxsync_auto_sync",
        "description": (
            "Safely synchronize local files to XServer. Runs a 4-stage pipeline: "
            "dry run -> plan revalidation -> automatic remote backup -> atomic file deployment -> HTTPS health check."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                },
                "mapping": {
                    "type": "string",
                    "description": "Optional mapping name or ID",
                },
                "backup_mode": {
                    "type": "string",
                    "enum": ["always", "add_only", "skip"],
                    "description": "Remote backup policy before deployment (default: always)",
                    "default": "always",
                },
                "remote_only_policy": {
                    "type": "string",
                    "enum": ["keep", "stop", "ask"],
                    "description": "How to handle REMOTE_ONLY files (default: keep; use 'ask' with delete_paths)",
                    "default": "keep",
                },
                "delete_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit relative paths of REMOTE_ONLY files to delete when remote_only_policy='ask'",
                    "default": [],
                },
                "health_check_enabled": {
                    "type": "boolean",
                    "description": "Whether to run HTTPS health check after deployment (default: true)",
                    "default": True,
                },
                "health_check_path": {
                    "type": "string",
                    "description": "Path on project domain to check over HTTPS (default: '/')",
                    "default": "/",
                },
                "plan_validity_minutes": {
                    "type": "integer",
                    "description": (
                        f"Plan validity window in minutes "
                        f"({MIN_PLAN_VALIDITY_MINUTES}-{MAX_PLAN_VALIDITY_MINUTES}, "
                        f"default: {DEFAULT_PLAN_VALIDITY_MINUTES})"
                    ),
                    "minimum": MIN_PLAN_VALIDITY_MINUTES,
                    "maximum": MAX_PLAN_VALIDITY_MINUTES,
                    "default": DEFAULT_PLAN_VALIDITY_MINUTES,
                },
                "max_changed_files": {
                    "type": "integer",
                    "description": "Abort if ADD+UPDATE exceeds this count (0 disables, default: 1000)",
                    "minimum": 0,
                    "default": 1000,
                },
                "max_update_files": {
                    "type": "integer",
                    "description": "Abort if UPDATE exceeds this count (0 disables, default: 500)",
                    "minimum": 0,
                    "default": 500,
                },
                "max_upload_mib": {
                    "type": "integer",
                    "description": "Abort if upload size exceeds this MiB (0 disables, default: 1024)",
                    "minimum": 0,
                    "default": 1024,
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "dxsync_verify_safety",
        "description": (
            "Inspect remote storage for residual .new temporary files or orphaned backups, "
            "with optional cleanup."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                },
                "mapping": {
                    "type": "string",
                    "description": "Optional mapping name or ID",
                },
                "cleanup": {
                    "type": "boolean",
                    "description": "Set true to automatically delete orphaned .new temporary files",
                    "default": False,
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "dxsync_list_backups",
        "description": "List historical backups available for rollback on XServer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                }
            },
            "required": ["project"],
        },
    },
    {
        "name": "dxsync_restore_backup",
        "description": "Restore remote files from a previous backup (roll back changes).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                },
                "backup_id": {
                    "type": "string",
                    "description": "ID of the backup to restore",
                },
                "mapping": {
                    "type": "string",
                    "description": "Optional mapping name or ID",
                },
            },
            "required": ["project", "backup_id"],
        },
    },
    {
        "name": "dxsync_prune_backups",
        "description": (
            "Select or delete old remote backups according to retention rules "
            "(keep_latest and max_age_days). Defaults to dry_run=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, domain, or project_id",
                },
                "keep_latest": {
                    "type": "integer",
                    "description": "Minimum number of newest backups to keep (default: 10)",
                    "minimum": 0,
                    "default": 10,
                },
                "max_age_days": {
                    "type": "integer",
                    "description": "Protect backups newer than this many days (default: 30)",
                    "minimum": 0,
                    "default": 30,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, list candidates without deleting remote backups (default: true)",
                    "default": True,
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Must be true when dry_run=false to delete selected backups",
                    "default": False,
                },
            },
            "required": ["project"],
        },
    },
]


class McpServer:
    def __init__(self, store: ProjectStore | None = None) -> None:
        self.store = store or get_default_store()

    def handle_request(self, message: dict[str, Any]) -> dict[str, Any] | None:
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "docker-xserver-sync",
                        "version": __version__,
                    },
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            }

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                result_data = self._dispatch_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    result_data, ensure_ascii=False, indent=2
                                ),
                            }
                        ],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error executing {tool_name}: {exc}",
                            }
                        ],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def _resolve_project(self, identifier: str):
        proj = find_project(self.store, identifier)
        if not proj:
            raise OperationError(f"Project '{identifier}' not found in configuration.")
        return proj

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == "dxsync_list_projects":
            return list_projects_summary(self.store)

        if name == "dxsync_get_project":
            project = self._resolve_project(args["project"])
            return get_project_detail(self.store, project)

        if name == "dxsync_dry_run":
            project = self._resolve_project(args["project"])
            return execute_dry_run(
                self.store,
                project,
                mapping_name=args.get("mapping"),
                plan_validity_minutes=int(
                    args.get("plan_validity_minutes", DEFAULT_PLAN_VALIDITY_MINUTES)
                ),
            )

        if name == "dxsync_auto_sync":
            project = self._resolve_project(args["project"])
            raw_delete_paths = args.get("delete_paths") or []
            if not isinstance(raw_delete_paths, list) or not all(
                isinstance(item, str) for item in raw_delete_paths
            ):
                raise OperationError("delete_paths must be an array of strings.")
            return execute_auto_sync(
                self.store,
                project,
                mapping_name=args.get("mapping"),
                backup_mode=str(args.get("backup_mode", "always")),
                remote_only_policy=str(args.get("remote_only_policy", "keep")),
                delete_paths=tuple(raw_delete_paths),
                health_check_enabled=bool(args.get("health_check_enabled", True)),
                health_check_path=str(args.get("health_check_path", "/")),
                plan_validity_minutes=int(
                    args.get("plan_validity_minutes", DEFAULT_PLAN_VALIDITY_MINUTES)
                ),
                max_changed_files=int(args.get("max_changed_files", 1000)),
                max_update_files=int(args.get("max_update_files", 500)),
                max_upload_mib=int(args.get("max_upload_mib", 1024)),
            )

        if name == "dxsync_verify_safety":
            project = self._resolve_project(args["project"])
            return execute_verify(
                self.store,
                project,
                mapping_name=args.get("mapping"),
                cleanup=args.get("cleanup", False),
            )

        if name == "dxsync_list_backups":
            project = self._resolve_project(args["project"])
            return list_backups_summary(self.store, project)

        if name == "dxsync_restore_backup":
            project = self._resolve_project(args["project"])
            return execute_restore(
                self.store,
                project,
                backup_id=args["backup_id"],
                mapping_name=args.get("mapping"),
            )

        if name == "dxsync_prune_backups":
            project = self._resolve_project(args["project"])
            dry_run = bool(args.get("dry_run", True))
            if not dry_run and not bool(args.get("confirm_delete", False)):
                raise OperationError(
                    "Setting dry_run=false requires confirm_delete=true to delete remote backups."
                )
            return execute_prune_backups(
                self.store,
                project,
                keep_latest=int(args.get("keep_latest", 10)),
                max_age_days=int(args.get("max_age_days", 30)),
                dry_run=dry_run,
            )

        raise OperationError(f"Unknown tool: {name}")


def run_stdio_server(store: ProjectStore | None = None) -> int:
    server = McpServer(store=store)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = server.handle_request(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0
