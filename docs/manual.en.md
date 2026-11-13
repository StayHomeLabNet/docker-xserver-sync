# Docker → XServer Sync Comprehensive User Manual

A complete, exhaustive operational manual for safely, swiftly, and reliably synchronizing and deploying web applications developed with Docker (Custom PHP, WordPress, Laravel) to XServer staging and production environments.

---

## Table of Contents

1. [Architectural Philosophy & Safety Features](#1-architectural-philosophy--safety-features)
2. [Prerequisites & Environment Setup (XServer & Local)](#2-prerequisites--environment-setup-xserver--local)
3. [User Interface Overview](#3-user-interface-overview)
4. [Site-Specific Workflows & Configuration Guides](#4-site-specific-workflows--configuration-guides)
   - [4-1. Custom PHP Sites](#4-1-custom-php-sites)
   - [4-2. WordPress Sites](#4-2-wordpress-sites)
   - [4-3. Laravel Sites](#4-3-laravel-sites)
5. [Project Management (Create, Duplicate, Edit, Delete)](#5-project-management-create-duplicate-edit-delete)
6. [Synchronization Modes & Execution Pipeline](#6-synchronization-modes--execution-pipeline)
7. [Pause & Resumption Workflow (After App Restart)](#7-pause--resumption-workflow-after-app-restart)
8. [Safety Verification, Backups, and Rollbacks](#8-safety-verification-backups-and-rollbacks)
9. [Security & Credential Protection](#9-security--credential-protection)
10. [Troubleshooting & FAQ](#10-troubleshooting--faq)
11. [CLI (Command Line Interface) Mode](#11-cli-command-line-interface-mode)
12. [AI & MCP Integration (Antigravity, Claude, Cursor, etc.)](#12-ai--mcp-integration-antigravity-claude-cursor-etc)

---

## 1. Architectural Philosophy & Safety Features

Docker → XServer Sync was designed specifically to bridge the gap between local Docker container environments and remote XServer shared hosting infrastructure, preventing accidental production outages and data loss.

### Core Safety Mechanisms
- **Read-Only Dry Run**: Inspects all file differences (added, updated, deleted, remote-only, conflict, protected) without touching or modifying the remote server.
- **4-Stage Automated Sync Pipeline**: Orchestrates "Dry Run" → "Plan Revalidation" → "Pre-Sync Differential Backup" → "Explicit Approval Sync" with a single click.
- **Atomic File Replacement**: Files are uploaded to temporary `.new` files and atomically renamed only upon transfer completion, ensuring broken files are never left behind if an interruption occurs.
- **Automated Exclusion & Dynamic Content Preservation**: Sensitive and development-only files (`.git`, `.env`, `compose.yml`, `node_modules/`, etc.) are excluded automatically. Production runtime directories such as `wp-content/uploads/` and `storage/` are safeguarded from deletion or accidental overwrites.
- **OS Credential Store Integration**: SSH host key verification codes (SHA-256 fingerprints) are never stored in plain text. They are guarded inside Windows Credential Manager, macOS Keychain, or Linux Secret Service to prevent Man-in-the-Middle (MITM) attacks.

---

## 2. Prerequisites & Environment Setup (XServer & Local)

### Supported Platforms
- **Operating Systems**: Windows 10 / 11 (x64), macOS 12+ (Apple Silicon arm64 / Intel x86_64), Linux (x86_64)
- **Network**: Direct outbound SSH connectivity to XServer port `10022`.

### Remote (XServer) Setup
1. **Enable SSH in ServerPanel**:
   - Log into XServer ServerPanel, navigate to **SSH Settings**, and set SSH Status to **ON**.
2. **Generate SSH Key Pair**:
   - Under "Generate Public Key Authentication Keypair", specify a passphrase and generate keys. Download the private key (`.key` file) to your computer.
3. **Verify Connection Parameters**:
   - In "SSH Software Settings", check the following values:
     - **SSH Host**: `SERVER_ID.xsrv.jp`
     - **SSH User**: `SERVER_ID` (Your XServer account ID)
     - **SSH Port**: `10022`
     - **Verification Code**: The host key fingerprint starting with `SHA256:...`.

### Local (Docker / PC) Setup
- Specify the local folder on your computer's storage as the sync root.
- If your `compose.yml` mounts `./src:/var/www/html`, specify the absolute path to `./src` on your host PC (not `/var/www/html`).

---

## 3. User Interface Overview

```
+-------------------------------------------------------------------------------------------------------+
| Docker → XServer Sync                                                             [ - ] [ 口 ] [ X ]  |
| Project(P)   Settings(S)   Help(H)                                                                    |
+-------------------------------------------------------------------------------------------------------+
| Docker → XServer Sync                                                                                 |
| Safe Sync Mode — Performs revalidation, differential backups, and explicit approvals                  |
|                                                                                                       |
| Project: [ example.com                       v ] [New…] [Clone…] [Edit…] [Delete] [Verify] [History…]  |
|                                                                                                       |
| [WordPress/Laravel First-Time Setup Banner]     [Start Setup…] [Resume Setup…]                        |
|                                                                                                       |
| Site Type: [ Custom PHP / WordPress / Laravel v ] Target: [ site v ] [ C:/Repos/site... ] [Browse…]   |
|                                                                                                       |
| ┌─ XServer Connection & Safe Sync ──────────────────────────────────────────────────────────────────┐ |
| │ Host: [ server.xsrv.jp          ] Port: [ 10022 ] User: [ server_id                               ] │ |
| │ Remote Root: [ /home/server_id/example.com/public_html                                            ] │ |
| │ Verification Code: [ SHA256:*******************************************                           ] │ |
| │ Private Key: [ C:/Users/.../server_id.key              ] [Select Key…] [Passphrase not saved      ] │ |
| │                                                                                                     │ |
| │ [Force Overwrite] [Revalidate Plan] [Create Backup] [Approved Sync]         [Read-Only Dry Run]     │ |
| │ [ ] Temporarily Unmask Credentials                                          [★ Auto Safe Sync]      │ |
| └─────────────────────────────────────────────────────────────────────────────────────────────────────┘ |
|                                                                                                       |
| Status: Ready (or 4/4 Executing approved sync...)                                     [ Stop Process ]|
| Progress: [================================== 56/130 (43.1%) ========================================] |
|                                                                                                       |
| [ Status    ] [ Relative Path                        ] [ Size       ] [ SHA-256                     ] |
| ADD          api/admin_mail.php                         29.1 KiB      e1b6625e418dc4945f...           |
| UPDATE       api/admin_users.php                         5.7 KiB      e10f31a0096f2e9616...           |
| PROTECTED    wp-content/uploads/2026/11/sample.jpg     142.0 KiB      44b81c201a082f910a...           |
+-------------------------------------------------------------------------------------------------------+
```

### Action Controls
- **New…**: Creates a new synchronization project.
- **Clone…**: Clones the active project configuration into a new project with incremented naming.
- **Edit…**: Edits connection settings, directory mappings, and protected runtime paths. A **Clone…** button is also available inside the editor.
- **Delete**: Removes the project configuration from local SQLite (remote and local files remain intact).
- **Verify (安全性確認)**: Inspects remote XServer storage for orphaned `.new` temporary files and legacy `.bak` archives.
- **History…**: Opens the audit log and enables 1-click rollbacks from historical backups.
- **Auto Safe Sync (安全同期を自動実行)**: Recommended standard workflow. Executes Dry Run → Revalidation → Backup → Sync in sequence.
- **Read-Only Dry Run (読み取り専用ドライラン)**: Performs a remote differential scan without altering remote storage.
- **Stop Process (処理を停止)**: Safely cancels ongoing transfers at the next file boundary.

---

## 4. Site-Specific Workflows & Configuration Guides

### 4-1. Custom PHP Sites
For standard PHP projects outside the WordPress or Laravel frameworks.

1. **Configuration**:
   - Set Site Type to **Custom PHP (独自 PHP)**.
   - Click "Runtime Paths Configuration… (保持パス設定…)".
   - `uploads/`, `storage/`, `cache/`, `logs/`, and `tmp/` are automatically safeguarded.
   - Add site-specific runtime directories (e.g. `userfiles/`, `data/sessions/`) relative to the root.
   - Check the agreement box to confirm runtime directory preservation.
2. **Day-to-day Operations**:
   - Click "Auto Safe Sync". Code updates are deployed while production uploads and user data remain untouched.

---

### 4-2. WordPress Sites
Handles initial deployment and continuous synchronization for WordPress installations.

#### Initial Setup Wizard
Transfers a Docker-based WordPress environment to a clean XServer domain.
1. **Preparation**:
   - Export an SQL dump (`.sql`) from your Docker MySQL / MariaDB container.
   - Create an empty database and user in XServer ServerPanel (or use the XServer API option).
2. **Wizard Steps**:
   - Click **Start Setup… (セットアップを開始…)** in the setup panel.
   - **Select SQL Dump**: Choose the exported `.sql` file.
   - **Inspect Transfer Files**: Verify theme, plugin, uploads, and core files.
   - **Database Details**: Enter MySQL Host (usually `localhost`), DB name, DB user, password, and public URL.
   - **PHP CLI Version**: Align with your domain's PHP version in ServerPanel (Default: PHP 8.3).
   - **Execution**:
     - Pre-checks existing database tables (halts if non-empty to prevent overwrite).
     - Uploads and imports SQL dump.
     - Transfers WordPress files.
     - Generates and writes `wp-config.php`.
     - Executes WP-CLI search-replace to rewrite local URLs to the production domain.
     - Removes default XServer `index.html`.
     - Runs HTTPS health verification on the homepage and `/wp-login.php`.

#### Routine Synchronization
- After setup, run "Auto Safe Sync".
- `wp-config.php` and `wp-content/uploads/` are preserved as protected files.

---

### 4-3. Laravel Sites
Optimized for Laravel's split directory structure and automated deployment.

#### Recommended Architecture
For maximum security on shared hosting:
- **Application Root**: `/home/SERVER_ID/example.com_app` (stored safely above public root)
- **Public Root**: `/home/SERVER_ID/example.com/public_html` (document root)

#### Initial Setup Wizard
1. **Launch Wizard**:
   - Click **Start Setup…** in the Laravel setup panel.
2. **Parameters**:
   - Database credentials.
   - Production `APP_URL` (`https://example.com`).
   - PHP CLI version (e.g. `/usr/bin/php8.3`).
   - Migration preferences (`artisan migrate --force`).
3. **Automated Pipeline**:
   - Fast remote pre-scan (scans remote files in ~0.2 seconds).
   - Uploads app files and public files.
   - Generates production `.env`.
   - Generates application encryption key (`artisan key:generate --force`).
   - Configures `storage` permissions and creates storage symlink (`public/storage` → `storage/app/public`).
   - Executes database migrations.
   - Performs HTTPS health check.

#### Fast Skip Engine (v0.3.1+)
- When synchronizing Laravel applications, the engine scans the remote host over SSH. Identical files (matching size and modification timestamp) are skipped instantly. Thousands of vendor and framework files are verified in ~20 seconds.

---

## 5. Project Management (Create, Duplicate, Edit, Delete)

### New Project (`New…` / `新規…`)
- Opens the configuration dialog. Use "How to check inputs (入力項目の確認方法)" for guided assistance.

### Duplicate Project (`Clone…` / `複製…`)
Facilitates setting up multiple domains, staging environments, or similar configurations.
- **From Main Window**: Select a project and click **Clone… (複製…)**.
- **From Edit Dialog**: Click the **Clone…** button to the left of "How to check inputs".
- **Behavior**:
  - Inherits all connection parameters, mappings, and security settings.
  - Automatically names the duplicate as `<Name> のコピー` (or `<Name> のコピー 2`).
  - Generates unique project and mapping identifiers upon saving.
  - Automatically selects the newly created project in the main window.

### Edit Project (`Edit…` / `編集…`)
- Adjust host parameters, key paths, or mapping directories at any time.

### Delete Project (`Delete` / `削除`)
- Removes the configuration profile locally. Remote files and local project repositories are never touched.

---

## 6. Synchronization Modes & Execution Pipeline

### 1. Auto Safe Sync (安全同期を自動実行 - Recommended)
Executes a 4-phase safe deployment:
1. **Phase 1/4 (Dry Run)**: Discovers local and remote file differences and generates an immutable Sync Plan.
2. **Phase 2/4 (Revalidation)**: Validates that remote files have not drifted since the plan was staged.
3. **Phase 3/4 (Create Backup)**: Archives files slated for modification into remote `.bak` files.
4. **Phase 4/4 (Approved Sync)**: Uploads files via `.new` temporary buffers and atomically swaps them.

### 2. Read-Only Dry Run
- Audits remote files without modifying any data.

### 3. Approved Sync
- Allows manual inspection and deselecting specific files before initiating transfers.

### 4. Force Overwrite
- Bypasses dry run and pushes local files directly. Records completed paths to allow resuming if stopped.

---

## 7. Pause & Resumption Workflow (After App Restart)

### Pausing an Active Transfer (`Stop Process` / `処理を停止`)
- Clicking "Stop Process" displays a confirmation prompt.
- Confirmed cancellation stops transfers safely at the current file boundary.
- Active `.new` temporary files are cleared automatically.

### Resuming After Exit or Interruption
If the computer shuts down or network drops during a transfer:
1. **Custom PHP / General Sync**:
   - The app detects unfinished operations on relaunch and presents the **Resume Interrupted Process… (中断した処理を再開…)** button.
   - Staged Sync Plans stored in SQLite resume without recalculating differences from scratch.
2. **WordPress**:
   - Click **Resume Interrupted Setup… (中断したセットアップを再開…)** in the setup panel.
   - Skips finished database creation and SQL import steps, continuing directly from remaining file transfers.
3. **Laravel**:
   - Click **Resume Interrupted Setup…** in the setup panel.
   - Evaluates already uploaded files in 0.2s and resumes transfers and post-install commands seamlessly.

---

## 8. Safety Verification, Backups, and Rollbacks

### Safety Verification Scan (`Verify` / `安全性確認`)
- Scans remote XServer storage for residual `.new` temporary files or expired `.bak` archives.
- Provides 1-click cleanup to keep disk quotas tidy.

### Historical Rollback (`History…` / `履歴…`)
- View audit records of past synchronization runs.
- Select any operation and click **Restore from Backup (バックアップから復元)** to revert remote files to their pre-sync state.

---

## 9. Security & Credential Protection

### Host Key Fingerprint Verification
- Verifies XServer's SHA-256 host key fingerprint before authenticating.
- Aborts connections immediately if a fingerprint mismatch is detected, preventing MITM attacks.

### Credential Masking
- Masking prevents shoulder surfing by obscuring passwords, private key paths, and fingerprints.
- Use "Temporarily Unmask Credentials" or customize mask rules in `Settings → Connection Mask Settings…`.

---

## 10. Troubleshooting & FAQ

### Q. SSH connection error: "Host key does not match"
- **Solution**: Open XServer ServerPanel → "SSH Settings" → "SSH Software Settings", copy the exact `SHA256:...` string, and paste it into the Verification Code field.

### Q. Laravel error: `Composer detected issues in your platform...`
- **Cause**: PHP CLI version running on the server is lower than what `composer.json` mandates (e.g. PHP 8.3 vs requirement `>= 8.4.1`).
- **Solution**:
  1. Switch the domain's PHP version in ServerPanel ("PHP Ver. Switch") to PHP 8.4.
  2. In the setup wizard, select the matching CLI path (`/usr/bin/php8.4`).

---

## 11. CLI (Command Line Interface) Mode

The tool provides full headless execution for CI/CD pipelines, automation scripts, and AI agents. Adding `--json` to any command yields clean structured JSON output.

```bash
# List all configured projects (supports --json)
dxsync project list [--json]

# Show detailed configuration and mappings for a project
dxsync project show <project_name|domain|id> [--json]

# Execute a read-only dry run (no remote modifications)
dxsync run dry-run <project> [--mapping <mapping_name>] [--json]

# Execute a safe 4-stage automated synchronization pipeline
# Pass --yes to bypass interactive confirmation (ideal for CI and AI agents)
dxsync run auto-sync <project> [--mapping <mapping_name>] [--yes] [--json]

# Safety audit and orphaned temporary file cleanup
dxsync run verify <project> [--cleanup] [--json]

# List historical remote backups
dxsync backup list <project> [--json]

# Restore remote files from a specific backup
dxsync backup restore <project> --backup-id <id> [--yes] [--json]
```

---

## 12. AI & MCP Integration (Antigravity, Claude, Cursor, etc.)

Docker → XServer Sync implements the **Model Context Protocol (MCP)**, allowing AI assistants to safely query and operate sync tasks.

### 12-1. Safety Architecture
- **GUI-Managed Credentials**: High-risk configuration (SSH host, credentials, host key fingerprints) remains managed via the GUI and stored in the OS credential store (macOS Keychain, Windows Credential Manager).
- **AI Scope**: AI assistants can query projects, preview differences via dry runs, trigger automated safe syncs, verify leftover `.new` artifacts, and trigger rollbacks.
- **Atomic 4-Stage Sync**: When the AI invokes `dxsync_auto_sync`, the application performs the full pipeline: dry run -> plan revalidation -> remote backup -> atomic file deployment -> HTTPS health check.

### 12-2. Launching the MCP Server
A built-in stdio MCP server is available out of the box with zero additional dependencies:

```bash
# Launch stdio MCP server
dxsync mcp
```

#### Configuration Example (`claude_desktop_config.json` / Cursor)
```json
{
  "mcpServers": {
    "docker-xserver-sync": {
      "command": "dxsync",
      "args": ["mcp"]
    }
  }
}
```

#### Antigravity Skill (`/docker-xserver-sync`)
Placing `skills/docker-xserver-sync/` or `.gemini/skills/docker-xserver-sync/` in the workspace enables Antigravity CLI and IDE to invoke the skill seamlessly.

---

*Manual Published: November 2026 / Version v0.3.2 / StayHomeLabNet*
