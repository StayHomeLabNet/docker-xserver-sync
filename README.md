# Docker → XServer Sync

Dockerへbind mountしたローカルのPHPサイトを、ドライランと承認を経てXServerへ安全に反映するデスクトップアプリです。

現在は読み取り専用ドライラン、承認前再検証、バックアップ・復元エンジンまで実装済みです。WordPress、Laravel、独自PHPのプリセット、複数プロジェクト・複数パスマッピング、ローカル・SFTP走査、ホスト鍵照合、差分計画、5分間の計画保存、対象ファイルのステージング、操作履歴・バックアップ履歴を利用できます。公開ファイルへの同期処理はまだ実装していません。

## 開発環境

Python 3.12以上と[uv](https://docs.astral.sh/uv/)を使用します。

```bash
uv sync --extra dev
uv run pytest
uv run dxsync scan --site wordpress --root /path/to/site
uv sync --extra gui
uv run dxsync-gui
```

XServerとの読み取り専用ドライラン:

```bash
uv sync --extra ssh
uv run dxsync dry-run \
  --site wordpress \
  --root /path/to/site \
  --host SERVER_ID.xsrv.jp \
  --username SERVER_ID \
  --private-key /path/to/private.key \
  --fingerprint SHA256:... \
  --remote-root /home/SERVER_ID/example.com/public_html \
  --ask-passphrase
```

`dry-run`はSFTPの一覧取得とファイル読み取りだけを行います。リモートにディレクトリ、ロック、一時ファイルを作成しません。

GUIのプロジェクト設定はOS標準のアプリデータ領域にSQLiteで保存されます。秘密鍵のファイルパスは保存しますが、秘密鍵の内容とパスフレーズは保存しません。

リモート側の読み取り専用マニフェストがある場合は、ローカルとの差分計画をJSONで出力できます。

```bash
uv run dxsync plan \
  --site wordpress \
  --root /path/to/site \
  --remote-manifest remote.json
```

製品全体の方針は[製品仕様](docs/product-spec.md)を参照してください。
初回の入力項目は[初回設定ガイド](docs/setup-guide.md)を参照してください。

