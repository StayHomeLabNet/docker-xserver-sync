# Docker → XServer Sync Comprehensive User Manual

A complete, exhaustive operational manual for safely, swiftly, and reliably synchronizing and deploying web applications developed with Docker (Custom PHP, WordPress, Laravel, Static Site) to XServer staging and production environments.

---

## Table of Contents

1. [Architectural Philosophy & Safety Features](#1-architectural-philosophy--safety-features)
2. [Prerequisites & Environment Setup (XServer & Local)](#2-prerequisites--environment-setup-xserver--local)
3. [User Interface Overview](#3-user-interface-overview)
4. [Site-Specific Workflows & Configuration Guides](#4-site-specific-workflows--configuration-guides)
   - [4-1. Custom PHP Sites](#4-1-custom-php-sites)
   - [4-2. WordPress Sites](#4-2-wordpress-sites)
   - [4-3. Laravel Sites](#4-3-laravel-sites)
   - [4-4. Static Sites (HTML / Pre-built)](#4-4-static-sites-html--pre-built)
5. [Project Management (Create, Duplicate, Edit, Delete)](#5-project-management-create-duplicate-edit-delete)
6. [Synchronization Modes & Execution Pipeline](#6-synchronization-modes--execution-pipeline)
7. [Pause & Resumption Workflow (After App Restart)](#7-pause--resumption-workflow-after-app-restart)
8. [Safety Verification, Backups, Rollbacks, and Update Checks](#8-safety-verification-backups-rollbacks-and-update-checks)
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
| Docker → XServer Sync                                        Theme: [ System Setting v ]              |
| Safe Sync Mode — Performs revalidation, differential backups, and explicit approvals                  |
|                                                                                                       |
| Project: [ example.com                       v ] [New…] [Clone…] [Edit…] [Delete] [Verify] [History…]  |
|                                                                                                       |
| [WordPress/Laravel First-Time Setup Banner]     [Start Setup…] [Resume Setup…]                        |
|                                                                                                       |
| Site Type: [ Custom PHP / WordPress / Laravel / Static Site v ] Target: [ site v ] [ C:/Repos/site... ] [Browse…] |
|                                                                                                       |
| ┌─ XServer Connection & Safe Sync ──────────────────────────────────────────────────────────────────┐ |
| │ Host: [ server.xsrv.jp          ] Port: [ 10022 ] User: [ server_id                               ] │ |
| │ Remote Root: [ /home/server_id/example.com/public_html                                            ] │ |
| │ Verification Code: [ SHA256:*******************************************                           ] │ |
| │ Private Key: [ C:/Users/.../server_id.key              ] [Select Key…] [Passphrase not saved      ] │ |
| │                                                                                                     │ |
| │ [Force Overwrite] [Revalidate Plan] [Create Backup] [Approved Sync]         [Read-Only Dry Run]     │ |
| │ [ ] Temporarily Unmask Credentials  [ ] Save Passphrase in OS Store           [★ Auto Safe Sync]      │ |
| │                                    Auto-Sync Settings — Backup: Always…     [Auto-Sync Settings…]   │ |
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
- **Theme (表示テーマ)**: Follows the OS appearance with **System Setting**, or keeps the application in **Light** or **Dark** mode. The selection persists across launches.
- **New…**: Creates a new synchronization project.
- **Clone…**: Clones the active project configuration into a new project with incremented naming.
- **Edit…**: Edits connection settings, directory mappings, and protected runtime paths. A **Clone…** button is also available inside the editor.
- **Delete**: Removes the project configuration from local SQLite (remote and local files remain intact).
- **Verify (安全性確認)**: Inspects remote XServer storage for orphaned `.new` temporary files and legacy `.bak` archives.
- **History…**: Opens the audit log and enables 1-click rollbacks from historical backups.
- **Auto Safe Sync (安全同期を自動実行)**: Recommended standard workflow. Executes Dry Run → Revalidation → Backup → Sync in sequence.
- **Auto-Sync Settings… (自動実行設定…)**: Opens per-project Auto Safe Sync settings (backup mode, `REMOTE_ONLY` policy, HTTPS health check, safety limits). The active policy summary is displayed directly next to the button.
- **Read-Only Dry Run (読み取り専用ドライラン)**: Performs a remote differential scan without altering remote storage.
- **Stop Process (処理を停止)**: Safely cancels ongoing transfers at the next file boundary.

---

## 4. Site-Specific Workflows & Configuration Guides

### 4-1. Custom PHP Sites
For standard PHP projects outside the WordPress or Laravel frameworks.

1. **Configuration**:
   - Set Site Type to **Custom PHP (独自 PHP)**.
   - Click **Configure XServer Protection Targets… (XServer側の保護対象を設定…)**.
   - Click **Load List from XServer… (XServerから一覧を取得…)** to first display only files and directories directly under the synchronization destination root. Select protection targets with the checkbox shown to the left of each item; the checkbox remains visible across supported OS themes, including macOS dark mode.
   - To select individual nested items, click **Include Subfolders… (サブフォルダも含めて取得…)** in the list. Selecting a directory already protects its entire subtree, so loading subfolders is unnecessary in that case.
   - Protected targets are excluded from synchronization scans. Synchronization does not enter protected directories or read, hash, compare, back up, restore, upload, or delete protected files or directories. Only the parent directory is listed so the protected path can be identified and avoided.
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

### 4-4. Static Sites (HTML / Pre-built)
Tailored for static HTML/CSS/JS sites and pre-built outputs (e.g., Astro, Next.js `out/`, Hugo, Vite).

1. **Defaults & Behavior**:
   - **No Automatic Protected Paths (No Confirmation Required)**: Unlike PHP frameworks, directories/files such as `uploads/`, `wp-content/uploads/`, `wp-config.php`, or `.htaccess` are not automatically protected—build artifacts and static `.htaccess` files sync directly.
   - **Public Directory Friendly Excludes**: Only secrets, VCS metadata, dependencies, and dev files (`.git/`, `.env`, `node_modules/`, `docker-compose*.yml`, `.DS_Store`, etc.) are excluded. Public paths such as `/docs/`, `/test/`, `/README*`, and `/LICENSE*` are not excluded.
   - **Optional XServer Protection**: If you need to keep server-managed files or directories (e.g., `.htaccess`, `.well-known/`, `cgi-bin/`), click **Configure XServer Protection Targets… (XServer側の保護対象を設定…)** in the project editor. Use **Load List from XServer… (XServerから一覧を取得…)** to select them from the destination root, and load subfolders only when necessary.
2. **Bulk Selection for Deletion Candidates & Auto Safe Sync**:
   - When hashed build assets change between builds, use **Select All Deletions (削除候補をすべて選択)** / **Clear All Deletions (削除候補をすべて解除)** in manual or Auto Safe Sync confirmation dialogs (when **Remote-Only Policy** is set to **Select candidates in confirmation dialog (`ask`)**) to quickly mark or clear remote-only files for deletion (folders listed under "Do Not Delete Folders" remain safely disabled).

---

## 5. Project Management (Create, Duplicate, Edit, Delete)

### New Project (`New…` / `新規…`)
- Opens the configuration dialog. Use "How to check inputs (入力項目の確認方法)" for guided assistance.
- Click **Test Connection… (接続テスト…)** after entering connection details and paths to verify SSH/SFTP connectivity, host key fingerprint (SHA-256), and remote directory access before saving.

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
- Adjust host parameters, key paths, or mapping directories at any time. Key identity and path fields on saved projects are locked by default to prevent accidental destructive edits and can be unlocked intentionally when needed.

### Delete Project (`Delete` / `削除`)
- Removes the configuration profile locally. Remote files and local project repositories are never touched.

---

## 6. Synchronization Modes & Execution Pipeline

### 1. Auto Safe Sync (安全同期を自動実行 - Recommended)
Executes a 4-phase safe deployment (configurable per project via the **Auto-Sync Settings… (自動実行設定…)** button on the main window or **Settings → Auto-Sync Settings…** for backup mode (`always` / `add_only` / `skip`), `REMOTE_ONLY` handling (`keep` / `ask` / `stop`), HTTPS health-check path, and safety limits; the active policy summary is displayed directly next to the button):
1. **Phase 1/4 (Dry Run)**: Discovers local and remote file differences and generates an immutable Sync Plan.
2. **Unified Confirmation Dialog**: Review changes and safety thresholds. You can toggle **Skip Backup Creation** for the current run and optionally check **Always skip backups in this project's Auto-Sync settings going forward** to persist `backup_mode="skip"`. When `remote_only_policy` is `ask`, selecting any `REMOTE_ONLY` deletion candidates automatically enforces a pre-sync backup and keeps protected folders intact.
3. **Phase 2/4 (Revalidation)**: Validates that remote files have not drifted since the plan was staged.
4. **Phase 3/4 (Create Backup)**: Archives files slated for modification or deletion into remote `.bak` files (skipped when backup skip is active).
5. **Phase 4/4 (Approved Sync)**: Uploads files via `.new` temporary buffers, atomically swaps them, and runs the optional HTTPS health check.

### 2. Read-Only Dry Run
- Audits remote files without modifying any data.
- Generated sync plans remain valid for **15 minutes** by default and can be configured between **1 and 60 minutes** via **Settings → 計画の有効時間設定…**.

### 3. Approved Sync
- Allows manual inspection and selecting/deselecting files or deletion candidates (including bulk **Select All Deletions** / **Clear All Deletions**) before initiating transfers.

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
1. **Custom PHP / Static Site / General Sync**:
   - The app detects unfinished operations on relaunch and presents the **Resume Interrupted Process… (中断した処理を再開…)** button.
   - Staged Sync Plans stored in SQLite resume without recalculating differences from scratch.
2. **WordPress**:
   - Click **Resume Interrupted Setup… (中断したセットアップを再開…)** in the setup panel.
   - Skips finished database creation and SQL import steps, continuing directly from remaining file transfers.
3. **Laravel**:
   - Click **Resume Interrupted Setup…** in the setup panel.
   - Evaluates already uploaded files in 0.2s and resumes transfers and post-install commands seamlessly.

---

## 8. Safety Verification, Backups, Rollbacks, and Update Checks

### Safety Verification Scan (`Verify` / `安全性確認`)
- Scans remote XServer storage for residual `.new` temporary files or expired `.bak` archives.
- Provides 1-click cleanup to keep disk quotas tidy.

### Historical Rollback & Backup Retention Pruning (`History…` / `履歴…`)
- View audit records of past synchronization, overwrite, setup, and backup deletion runs.
- Select any operation and click **Restore from Backup (バックアップから復元)** to revert remote files to their pre-sync state.
- **Retention Pruning**: In the Backups tab, set **Keep Latest (保持件数（最新）)** (default: 10) and **Retention Days (保持日数)** (default: 30 days), then click **Select Prune Candidates by Retention Rules (保持ルールで整理候補を選択)** to mark only older backups outside both thresholds for reviewed deletion.
- **Prune Old History & Plans**: Click **Prune Old History & Plans… (古い操作履歴・計画を整理…)** at the bottom of the dialog to remove operation history older than 90 days (or beyond the latest 200 entries) and orphaned plans from the local database without affecting files or backups on XServer.

### In-App Update Check
- Click **Help → Check for Updates… (ヘルプ → アップデートを確認…)** to check GitHub Releases in the background and open release notes if a newer version is available.

---

## 9. Security & Credential Protection

### Host Key Fingerprint Verification
- Verifies XServer's SHA-256 host key fingerprint before authenticating.
- Aborts connections immediately if a fingerprint mismatch is detected, preventing MITM attacks.

### SSH Private Key Passphrase Caching & Opt-In Storage
- Passphrases entered during a session are cached in memory for the current app run so you are not prompted repeatedly across Dry Run, Revalidation, Backup, and Sync.
- Optionally check **Save passphrase in OS credential store (パスフレーズをOS資格情報ストアに保存)** in the passphrase dialog to persist it securely in Windows Credential Manager, macOS Keychain, or Linux Secret Service (unchecking removes any stored passphrase immediately).

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
dxsync run dry-run <project> [--mapping <mapping_name>] [--validity-minutes <1-60>] [--json]

# Execute a safe 4-stage automated synchronization pipeline
# Prompts for confirmation on TTY; --yes (-y) is required in non-interactive mode (CI / AI scripts)
dxsync run auto-sync <project> [--mapping <mapping_name>] \
  [--backup-mode always|add_only|skip] \
  [--remote-only-policy keep|stop|ask] [--delete-path <relative_path>] \
  [--health-check-path /] [--no-health-check] [--validity-minutes <1-60>] \
  [--max-changed-files <N>] [--max-update-files <N>] [--max-upload-mib <N>] \
  [--yes] [--json]

# Safety audit and orphaned temporary file cleanup
dxsync run verify <project> [--cleanup] [--json]

# List historical remote backups
dxsync backup list <project> [--json]

# Restore remote files from a specific backup
# Prompts for confirmation on TTY; --yes (-y) is required in non-interactive mode
dxsync backup restore <project> --backup-id <id> [--yes] [--json]

# Preview (--dry-run) or delete (--yes) old backups using retention thresholds
dxsync backup prune <project> [--keep-latest 10] [--max-age-days 30] [--dry-run | --yes] [--json]
```

---

## 12. AI & MCP Integration (Antigravity, Claude, Cursor, etc.)

Docker → XServer Sync implements the **Model Context Protocol (MCP)**, allowing AI assistants to safely query and operate sync tasks.

### 12-1. Safety Architecture
- **GUI-Managed Credentials**: High-risk configuration (SSH host, credentials, host key fingerprints) remains managed via the GUI and stored in the OS credential store (macOS Keychain, Windows Credential Manager).
- **AI Scope**: AI assistants can query projects, preview differences via dry runs, trigger automated safe syncs, verify leftover `.new` artifacts, trigger rollbacks, and prune aged backups via `dxsync_prune_backups` (`dry_run=true` by default; `confirm_delete=true` required for deletion).
- **Atomic 4-Stage Sync**: When the AI invokes `dxsync_auto_sync`, the application performs the full pipeline: dry run -> plan revalidation -> remote backup -> atomic file deployment -> HTTPS health check (supporting full policy parameters including `backup_mode`, `remote_only_policy`, `delete_paths`, `plan_validity_minutes`, and safety limits).

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

*Manual Published: November 2026 / Version v1.1.4 / StayHomeLabNet*
