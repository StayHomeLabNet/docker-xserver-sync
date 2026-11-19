# v1.1.0

## 日本語

Docker → XServer Sync **v1.1.0** では、自動実行時の `REMOTE_ONLY` 削除対応、SSH 秘密鍵パスフレーズのセッション保持・オプトイン保存、アプリ内アップデート確認、バックアップ保持ルールに基づく整理（GUI / CLI / MCP）、CLI / MCP のポリシー完全対応、UI・ワーカーのモジュール分割、および全プラットフォーム統合リリース自動化を実装しました。

### ✨ 新機能・UX 改善
- **自動実行での `REMOTE_ONLY` 削除候補選択 (`ask` モード)**:
  - **自動実行設定** の「リモートのみのファイル」に **「確認画面で削除候補を選択する（削除時は自動バックアップ）」(`ask`)** を追加しました。
  - 統合確認ダイアログ上で、ハッシュ付きビルド成果物（静的サイト等）の削除候補を個別または **「削除候補をすべて選択」** / **「削除候補をすべて解除」** で選択でき、「削除しないフォルダー」ルールも引き続き保護されます。削除対象を含む場合は事前バックアップが強制されます。
- **SSH 秘密鍵パスフレーズのセッション保持とオプトイン保存**:
  - 同じアプリ起動セッション中はパスフレーズをメモリキャッシュし、ドライラン・再検証・バックアップ・同期での繰り返し入力を削減しました。
  - パスフレーズ入力ダイアログに **「パスフレーズをOS資格情報ストアに保存」** を追加し、明示チェック時のみ Windows 資格情報マネージャー / macOS キーチェーン / Linux Secret Service へ暗号化保存します（チェックを外すと削除します）。
- **アプリ内アップデート確認**:
  - **「ヘルプ」→「アップデートを確認…」** から GitHub Releases の最新バージョンをバックグラウンドで確認し、新しいバージョンがある場合はリリースノートへの案内ダイアログを表示します。
  - また、「ヘルプ」→「オンラインマニュアル」を `https://stayhomelab.net/help/docker-xserver-sync/` に更新しました。

### 🗂️ バックアップ保持ルールと安全な整理（プルーン）
- **履歴ダイアログの保持整理 UI**:
  - 「操作履歴とバックアップ」ダイアログのバックアップタブに **「保持件数（最新）」**（既定 10件）と **「保持日数」**（既定 30日）を追加し、**「保持ルールで整理候補を選択」** で古いバックアップのみを安全に抽出して承認付きで削除できるようになりました。

### 🤖 CLI / MCP 完全パリティ
- **CLI (`dxsync`)**:
  - `dxsync run auto-sync` が `--backup-mode`、`--remote-only-policy`、`--delete-path`（複数指定可）、`--health-check-path`、`--no-health-check`、`--validity-minutes`、`--max-changed-files`、`--max-update-files`、`--max-upload-mib` に対応しました。
  - 古いバックアップの整理用コマンド `dxsync backup prune`（`--keep-latest` / `--max-age-days` / `--dry-run` / `--yes` / `--json`）を追加しました。
- **MCP (`dxsync mcp`)**:
  - `dxsync_dry_run` に `plan_validity_minutes` を、`dxsync_auto_sync` に自動実行ポリシー全項目（`backup_mode`, `remote_only_policy`, `delete_paths` 等）を追加しました。
  - `dxsync_prune_backups`（既定 `dry_run=true`、実削除時は `confirm_delete=true` 必須）を追加しました。

### 🧱 内部品質・CI / リリース自動化
- **GUI モジュール分割とテスト拡充**:
  - `gui.py` から純粋 Python の進捗追跡（`progress_tracker.py`）およびバックグラウンドワーカー（`workers.py`）を分離し、ユニットテストを **207件** まで拡充しました。
- **CI / リリース自動化**:
  - 全 OS（Ubuntu / macOS / Windows）での自動テストワークフロー（`.github/workflows/ci.yml`）および `v*` タグ駆動のマルチプラットフォーム一括リリースワークフロー（`.github/workflows/release.yml`）を整備しました。
  - 各ビルドスクリプトのバージョン参照を `pyproject.toml` に統一し、macOS のオプション署名・公証（`APPLE_SIGNING_IDENTITY` 等）および `SHA256SUMS.txt` 自動生成に対応しました。

---

## English

Docker → XServer Sync **v1.1.0** delivers interactive `REMOTE_ONLY` deletion in Auto Safe Sync, SSH private key passphrase session caching & opt-in OS credential storage, in-app update checking, retention-based backup pruning (GUI / CLI / MCP), full CLI & MCP policy parity, GUI modularization, and unified multi-platform CI & release automation.

### ✨ New Features & UX Improvements
- **Interactive `REMOTE_ONLY` Deletion in Auto Safe Sync (`ask` Mode)**:
  - Added **Select candidates in confirmation dialog (`ask`)** under **Remote-Only Policy** in **Auto-Sync Settings**.
  - Inside the unified confirmation dialog, you can mark or clear `REMOTE_ONLY` deletion candidates individually or via **Select All Deletions** / **Clear All Deletions** (ideal for hashed static-site build assets), while folders under "Do Not Delete Folders" remain protected and pre-sync backups are forced whenever deletions are scheduled.
- **SSH Passphrase Session Cache & Opt-In OS Credential Storage**:
  - Cached entered SSH private key passphrases in memory for the current app session to eliminate repeated prompts across Dry Run, Revalidation, Backup, and Sync.
  - Added an explicit **Save passphrase in OS credential store** option in the passphrase dialog (stored in Windows Credential Manager, macOS Keychain, or Linux Secret Service when checked; removed when unchecked).
- **In-App Update Checker**:
  - Added **Help → Check for Updates…** to check GitHub Releases asynchronously and link directly to new releases when available.
  - Updated **Help → Online Manual** to `https://stayhomelab.net/help/docker-xserver-sync/`.

### 🗂️ Backup Retention Rules & Safe Pruning
- **History Dialog Pruning Controls**:
  - Added **Keep Latest** (default: 10) and **Retention Days** (default: 30) controls with **Select Prune Candidates by Retention Rules** in the History dialog so aged backups outside both thresholds can be reviewed and removed safely.

### 🤖 Full CLI & MCP Policy Parity
- **CLI (`dxsync`)**:
  - Extended `dxsync run auto-sync` with `--backup-mode`, `--remote-only-policy`, `--delete-path` (repeatable), `--health-check-path`, `--no-health-check`, `--validity-minutes`, `--max-changed-files`, `--max-update-files`, and `--max-upload-mib`.
  - Added `dxsync backup prune` (`--keep-latest`, `--max-age-days`, `--dry-run`, `--yes`, `--json`).
- **MCP (`dxsync mcp`)**:
  - Extended `dxsync_dry_run` (`plan_validity_minutes`) and `dxsync_auto_sync` (`backup_mode`, `remote_only_policy`, `delete_paths`, limits, and health check options).
  - Added `dxsync_prune_backups` (`dry_run=true` default; `confirm_delete=true` required for deletion).

### 🧱 Architecture, CI & Release Engineering
- **GUI Modularization & Test Suite**:
  - Extracted pure-Python progress tracking (`progress_tracker.py`) and QObject worker classes (`workers.py`) out of `gui.py`, expanding the test suite to **207 unit tests**.
- **CI & Unified Release Automation**:
  - Added cross-platform CI matrix (`.github/workflows/ci.yml`) on Ubuntu, macOS, and Windows plus unified tag-triggered release workflow (`.github/workflows/release.yml`) generating `SHA256SUMS.txt`.
  - Unified dynamic version lookup from `pyproject.toml` across build scripts and added optional Apple Developer ID signing & notarization support.

---

## SHA-256

（リリースパイプライン完了後に確定値へ更新します / Populated upon release artifact build）
