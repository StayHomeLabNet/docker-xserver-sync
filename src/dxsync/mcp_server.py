from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import __version__
from .operations import (
    OperationError,
    execute_auto_sync,
    execute_dry_run,
    execute_restore,
    execute_verify,
    find_project,
    get_project_detail,
    list_backups_summary,
    list_projects_summary,
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
            )

        if name == "dxsync_auto_sync":
            project = self._resolve_project(args["project"])
            return execute_auto_sync(
                self.store,
                project,
                mapping_name=args.get("mapping"),
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
