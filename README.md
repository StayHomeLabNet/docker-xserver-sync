# 🐳 Docker XServer Sync

## Safe one-way deployment and setup tool from local Docker to XServer

**Docker XServer Sync safely deploys and synchronizes local Docker-based WordPress, Laravel, and custom PHP sites to XServer production environments with dry-run verification, approval gates, and automatic backups.**

[English](README.md) | [日本語](README.ja.md)

[Features](#features) ・ [Install](#install) ・ [Quick Start](#quick-start) ・ [Laravel Setup](#laravel-setup) ・ [WordPress Setup](#wordpress-setup) ・ [CLI Usage](#cli-usage) ・ [Development](#development) ・ [License](#license)

![version](https://img.shields.io/badge/version-0.3.0-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-orange)
![app](https://img.shields.io/badge/app-PySide6%20%2F%20Qt6-purple)
![python](https://img.shields.io/badge/python-3.12-blue)
![docker](https://img.shields.io/badge/docker-supported-brightgreen)
![xserver](https://img.shields.io/badge/target-XServer-lightgrey)

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20StayHomeLabNet-ff5e5b?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/stayhomelabnet)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support%20StayHomeLabNet-ffdd00?style=for-the-badge&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/stayhomelabnet)

**If Docker XServer Sync is useful to you, please consider supporting the project.**

---

Docker XServer Sync is a cross-platform desktop application designed to safely deploy and synchronize local PHP projects running in Docker (WordPress, Laravel, and custom PHP applications) to XServer shared hosting. Built with safety-first principles, it protects against accidental overwrites, destructive deletions, and unexpected downtime.

## Features

- **Safe One-Way Synchronization**:
  - Strict SSH host key fingerprint verification (SHA-256)
  - Read-only dry-runs that never modify the remote server
  - 15-minute diff staging plan
  - Automatic pre-deployment remote backups
  - Atomic two-stage deployment: upload to temporary filenames, verify SHA-256 digests, and switch into place (with automatic rollback on failure)
  - Protection rules for production-generated directories (e.g. `uploads`, `storage`)
  - Post-deployment automated HTTPS health checks
- **Laravel Initial Setup Wizard**:
  - Architecture-recommended separation: Application files (`app/`, `vendor/`) are placed outside public document root (`/home/{user}/{domain}_app`), while public assets (`public/`) are placed in `public_html`
  - Automatic rewrite of `public_html/index.php` paths (`vendor/autoload.php` and `bootstrap/app.php`)
  - Automatic `.env` generation with production settings and database credentials
  - Remote Artisan commands executed via SSH (`key:generate`, `migrate`, `storage:link`, `config:cache`, `route:cache`, `view:cache`)
  - Database provisioning via XServer API or connecting to a pre-existing database
- **WordPress Initial Setup Wizard**:
  - Automated MySQL database and user creation via XServer API, or connection to pre-existing empty databases
  - Safe, serialized-data-aware domain and URL replacement via remote WP-CLI
  - PHP version compatibility check (PHP 8.0 - 8.5)
- **Progress Tracking & Resumption**:
  - Real-time display of processed file counts, percentage, ETA, and current file paths
  - Safe interruption at file boundaries with checkpoint resumption after app restart
- **Cross-Platform Support**:
  - macOS (Apple Silicon arm64), Windows (x64 installer & zip), Linux (Ubuntu x64)

## Install

Download the latest release from [GitHub Releases](https://github.com/StayHomeLabNet/docker-xserver-sync/releases/latest).

### macOS
1. Download `Docker XServer Sync-macOS-arm64.zip`.
2. Extract and move `Docker XServer Sync.app` to your `/Applications` folder.

### Windows
- **Installer (Recommended)**: Run `Docker-XServer-Sync-Setup-v0.3.0.exe`.
- **Portable**: Extract `Docker XServer Sync-Windows-x64.zip` and run `Docker XServer Sync.exe`.

### Linux
Extract `Docker XServer Sync-Linux-x64.tar.gz` and run the executable.

---

## Quick Start

1. Launch the application and click **New Project**.
2. Select your site type (WordPress, Laravel, or Custom PHP) and configure local roots and XServer SSH connection details.
3. Follow the safe deployment pipeline:
   - **Read-Only Dry Run**: Compares local files against XServer and generates a diff plan.
   - **Revalidate Plan**: Verifies that remote and local files have not drifted before deployment.
   - **Create Backup**: Creates a remote backup archive of target files on XServer.
   - **Approved Sync**: Review changes and target domain before executing atomic deployment.
4. Optionally enable **Auto Sync** in settings to execute the full pipeline with a single confirmation click after dry-run.

---

## Laravel Setup

To deploy a local Docker Laravel project to a fresh XServer domain:

1. Select a project with site type set to **Laravel**.
2. In the setup panel, click **Start Setup…**.
3. **Wizard Configuration**:
   - **Database**: Select whether to create a new database via XServer API or connect to an existing database.
   - **Remote Paths**:
     - Application Root: `/home/{user}/{domain}_app` (outside public web space)
     - Document Root: `/home/{user}/{domain}/public_html` (web root)
   - **App Configuration**: Enter App Name, App URL, and choose migration mode (`php artisan migrate` or SQL dump).
4. Run the wizard: files are deployed, `index.php` paths are adjusted, `.env` is generated, Artisan commands are executed, and HTTPS health checks verify the live site.

---

## WordPress Setup

To deploy a local Docker WordPress project to a fresh XServer domain:

1. Select a project with site type set to **WordPress**.
2. In the setup panel, click **Start Setup…**.
3. Choose database provisioning mode (XServer API or pre-existing database), and specify your local SQL dump and `wp-content` options.
4. Select the remote PHP version (PHP 8.0 - 8.5; default: 8.3).
5. The wizard automatically transfers files, imports database tables, performs serialized URL replacement via WP-CLI, and validates HTTPS access.

---

## CLI Usage

You can also use the `dxsync` command-line tool for local scanning and remote dry-runs:

```bash
# Scan local directory
dxsync scan --site wordpress --root /path/to/site

# Read-only dry run against XServer
dxsync dry-run \
  --site wordpress \
  --root /path/to/site \
  --host SERVER_ID.xsrv.jp \
  --username SERVER_ID \
  --private-key /path/to/private.key \
  --fingerprint SHA256:... \
  --remote-root /home/SERVER_ID/example.com/public_html \
  --ask-passphrase
```

---

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync --extra dev --extra gui --extra ssh --extra build

# Run unit tests (130 passing tests)
uv run pytest

# Launch GUI
uv run dxsync-gui
```

### Building Application Packages

- **macOS**: `zsh scripts/build-app.sh` → `dist/Docker XServer Sync-macOS-arm64.zip`
- **Windows**: `pwsh -File scripts/build-app.ps1 -BuildInstaller` → `dist/Docker-XServer-Sync-Setup-v0.3.0.exe`
- **Linux**: `bash scripts/build-app-linux.sh` → `dist/Docker XServer Sync-Linux-x64.tar.gz`

---

## License

This project is licensed under the [MIT License](LICENSE).
