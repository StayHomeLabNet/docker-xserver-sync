---
name: docker-xserver-sync
description: >-
  Docker開発環境からXServer本番・ステージング環境への安全な同期・デプロイ・ドライラン・ロールバックを自律的・対話的に操作するスキル。CLI（dxsync）またはMCP（Model Context Protocol）ツール経由で安全パイプラインを実行します。
---

# Docker XServer Sync AI 操作スキル

このスキルは、ローカルの Docker 開発環境からエックスサーバー（XServer）への安全同期ツール **Docker XServer Sync** を AI エージェントから正確かつ安全に操作するためのガイドラインです。

---

## 1. 使用可能なインターフェース

AI エージェントは、環境に応じて **MCP (Model Context Protocol)** または **CLI (ターミナルコマンド)** のいずれかを利用できます。

### A. MCP ツールが利用可能な場合
以下のネイティブツールを直接呼び出します：
- `dxsync_list_projects`: 登録済みプロジェクト一覧の取得
- `dxsync_get_project`: プロジェクト詳細・マッピングの取得
- `dxsync_dry_run`: 読み取り専用ドライラン（差分確認のみ）
- `dxsync_auto_sync`: 一発安全同期パイプライン（バックアップ＆ヘルスチェック付き）
- `dxsync_list_backups`: 過去のバックアップ履歴取得
- `dxsync_restore_backup`: バックアップからのロールバック（復元）
- `dxsync_verify_safety`: 残存一時ファイル（`.new`）の検査・掃除

### B. CLI コマンド（ターミナル経由）を利用する場合
必ず `--json` を付与して実行し、結果を構造化データとして解釈します：
```bash
# プロジェクト一覧
uv run dxsync project list --json

# プロジェクト詳細
uv run dxsync project show <PROJECT_NAME_OR_DOMAIN> --json

# ドライラン
uv run dxsync run dry-run <PROJECT_NAME_OR_DOMAIN> --json

# 一発自動安全同期
uv run dxsync run auto-sync <PROJECT_NAME_OR_DOMAIN> --json

# バックアップ一覧
uv run dxsync backup list <PROJECT_NAME_OR_DOMAIN> --json

# ロールバック（復元）
uv run dxsync backup restore <PROJECT_NAME_OR_DOMAIN> --backup-id <BACKUP_ID> --json

# 安全性確認・掃除
uv run dxsync run verify <PROJECT_NAME_OR_DOMAIN> --cleanup --json
```

---

## 2. 実行プロトコルとユーザー対話の原則

### 原則 1: プロジェクトの自動判定
- ユーザーが「デプロイして」「同期して」と指示した際、プロジェクト名が明示されていない場合は `dxsync_list_projects` / `dxsync project list --json` を実行し、現在開いているワークスペースやドメイン名に一致するプロジェクトを自動特定します。
- 候補が複数あり確信が持てない場合のみ、ユーザーに選択肢を提示します。

### 原則 2: 一発自動同期（Auto Sync）の実行
- ユーザーから「同期して」「反映して」「デプロイして」と明確な実行意図がある場合、**`auto_sync` を直接実行して構いません**（バックグラウンドで自動的に「ドライラン → 再検証 → 自動バックアップ作成 → 安全同期 → HTTPSヘルスチェック」の全安全手順が完結します）。
- 実行後は、以下の3点を端的にユーザーへ報告してください：
  1. **転送ファイル数と転送量**（例: `3件 (24.5 KiB)`）
  2. **自動作成されたバックアップID**（例: `backup_20261113_...`）
  3. **HTTPS ヘルスチェック結果**（例: `200 OK`）

### 原則 3: 「確認してから」「ドライランして」と言われた場合
- ユーザーが差分確認を求めている場合は `dry_run` を実行し、結果を以下のように整理して伝えます：
  - 追加（ADD）: 件数と代表的なファイル
  - 更新（UPDATE）: 件数と代表的なファイル
  - 削除（DELETE）: 件数と代表的なファイル
  - 保護対象（PROTECTED）: スキップされた本番固有データ（`uploads/`、`storage/` 等）
  - ブロック理由やエラーの有無

### 原則 4: エラー時の自己回復とロールバック
- 同期後、万が一 HTTPS ヘルスチェックが失敗（404, 500 等）した場合や、ユーザーから「元に戻して」「動かなくなった」と言われた場合は：
  1. `list_backups` で直前のバックアップIDを確認。
  2. 直ちに `restore_backup` の実行を提案、または許可を得てロールバックを実行してください。
