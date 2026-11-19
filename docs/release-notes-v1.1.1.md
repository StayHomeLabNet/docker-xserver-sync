# v1.1.1

## 日本語

Docker → XServer Sync **v1.1.1** は、「安全同期を自動実行」におけるバックアップスキップの視認性・設定導線・確認ダイアログ操作性を改善したメンテナンスリリースです。

### ✨ 改善点
- **メイン画面からの直接設定と方針表示**:
  - 「安全同期を自動実行」ボタンの下に **「自動実行設定…」** ボタンを配置し、メニューを開かなくてもプロジェクト別の自動実行設定へすぐアクセスできるようにしました。
  - ボタン左側に現在の自動実行方針（例: `自動実行設定 — バックアップ: 必ず作成 / REMOTE_ONLY: 削除せず残す`）を常時表示し、「常にスキップ（復元不可）」設定時は注意色で明示します。
- **自動実行設定ダイアログの動的案内**:
  - **「バックアップ」**（「必ず作成する」「追加ファイルだけならスキップ」「常にスキップ（復元不可）」）および **「リモートのみのファイル」** の選択に応じて注意文を動的に更新し、「常にスキップ」時に `REMOTE_ONLY` 削除が無効になる理由を分かりやすく案内します。
- **統合確認ダイアログでのバックアップスキップ切替と設定保存**:
  - 「安全同期の自動実行を確認」ダイアログに **「バックアップ作成をスキップする（上書き前の状態には復元できません）」** チェックボックスを追加し、その回の実行単位でバックアップ作成有無を切り替えられるようにしました（既定「常にスキップ」時に今回だけバックアップを作成する切り替えも可能）。
  - スキップ選択時は **「次回以降もこのプロジェクトの自動実行設定でバックアップを常にスキップする」** を選択でき、承認と同時にプロジェクト設定（`backup_mode="skip"`）へ保存できます。
  - 削除安全制約（バックアップなしでのファイル削除禁止）を維持するため、`REMOTE_ONLY` 削除候補を選択した場合は自動的にバックアップ作成へ切り替わります。

### 🧪 テスト
- メイン画面の設定ボタン・方針表示、設定ダイアログの動的警告、統合確認ダイアログのトグル／設定保存、およびバックアップ段階スキップからデプロイ完了までの E2E テストを追加し、全 **212件** のテスト合格を確認しました。

---

## English

Docker → XServer Sync **v1.1.1** improves discoverability, configuration access, and per-run controls for skipping pre-deployment backups in Auto Safe Sync.

### ✨ Improvements
- **Direct Main-Window Shortcut & Policy Summary**:
  - Added an **Auto-Sync Settings… (自動実行設定…)** button directly beneath **Auto Safe Sync** so users can configure per-project settings without opening the menu bar.
  - Added a live policy summary label next to the button showing the active backup mode and `REMOTE_ONLY` policy (highlighted in amber when `backup_mode="skip"` is active).
- **Dynamic Policy Guidance in Settings Dialog**:
  - Updated `AutoSyncSettingsDialog` to dynamically explain the interaction between `backup_mode` (`always` / `add_only` / `skip`) and `remote_only_policy` (including why `REMOTE_ONLY` deletion is disabled when backups are always skipped).
- **Per-Run Backup Skip Toggle & Preference Persistence in Approval Dialog**:
  - Added a **Skip Backup Creation** checkbox inside `AutoSyncApprovalDialog` so users can toggle skipping or creating a backup for the current run (including one-off backup creation even when `backup_mode="skip"`).
  - Added an opt-in **Always skip backups in this project's Auto-Sync settings going forward** checkbox when skipping backups, saving `backup_mode="skip"` upon approval.
  - Preserved the safety invariant (`DeploymentAuthorization`): selecting any `REMOTE_ONLY` deletion candidates automatically enforces backup creation and disables skipping.

### 🧪 Tests
- Added unit and end-to-end `MainWindow` tests covering the settings shortcut, policy summary label, dynamic warnings, approval-dialog skip toggles/persistence, and backup-skipped deployment (`212 passed`).
