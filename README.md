# Docker → XServer Sync

Dockerへbind mountしたローカルのPHPサイトを、ドライランと承認を経てXServerへ安全に反映するデスクトップアプリです。

現在の最優先目標は、ローカルDocker環境のWordPressを空のXServerへ初回配置する専用セットアップ機能です。XServer APIによるMySQL作成、SQL移行、`wp-config.php`生成、安全なURL置換、HTTPS検証を一連のウィザードにまとめる方針です。詳細は[WordPress初回セットアップ仕様](docs/wordpress-first-setup.md)を参照してください。

初回セットアップウィザードの最小版を実装済みです。WordPressプロジェクトを選び、画面上部の「WordPress初回セットアップ」にある「セットアップを開始…」を押します。「プロジェクト」メニューの「WordPressをXServerへ新規セットアップ…」からも開けます。MySQLは、XServer APIでDBとユーザーを新規作成する方法と、サーバーパネルで作成済みの空DBをAPIキーなしで使用する方法を選べます。現在はDocker DBの自動ダンプ前のため、SQLファイルの事前出力が必要です。

初回セットアップ欄は、選択中の保存済みプロジェクトのサイト種類に応じて表示します。WordPressでは専用ウィザード、Laravelでは初回配置の手順と手動確認事項を表示し、独自PHPまたは未保存の手動設定では表示しません。Laravel専用の自動配置、`.env`生成、`artisan migrate`、キャッシュ生成はまだ実行しません。

`.gitignore`、`.github/`、`compose.yaml`、`docker-compose.yaml`、`Dockerfile`、プロジェクト直下の`README*`・`docs/`・`tests/`、IDE設定、`.env`、秘密鍵、`node_modules/`などは既定で転送対象から除外します。WordPress新規セットアップではコアを必須転送し、`wp-content/`配下はウィザードで選択できます。

読み取り専用ドライラン、承認前再検証、リモートバックアップ、承認付き同期、安全な復元エンジンを実装済みです。WordPress、Laravel、独自PHPのプリセット、複数プロジェクト・複数パスマッピング、ローカル・SFTP走査、ホスト鍵照合、15分間の差分計画、対象ファイルのステージング、操作履歴・バックアップ履歴を利用できます。独自PHPでは本番で書き込まれるディレクトリをマッピングごとに明示確認し、未確認のプロジェクトは同期できません。同期では全対象を一時名へ転送・SHA-256検証した後に公開名へ切り替え、途中失敗時は適用済みファイルをロールバックします。リモートのみのファイルは初期状態では削除されず、候補を個別選択・バックアップ・最終再承認した場合だけ削除されます。

初回配置などのため、バックアップの明示的なスキップと、差分・バックアップ・転送後ハッシュ照合を行わない「無条件上書き」も利用できます。確認画面でフォルダーまたはファイル単位の上書き対象を選択でき、「すべてチェック」「すべてチェック解除」も利用できます。無条件上書きでも、SSHホスト鍵、除外パターン、本番書き込み先の保護、明示承認は維持され、未選択ファイルとリモートのみのファイルは削除されません。

「安全同期を自動実行」では、読み取り専用ドライラン後に変更内容と事前設定を一度だけ統合確認し、承認後は再検証、バックアップ、承認付き同期、HTTPSヘルスチェックを自動継続します。自動実行設定はプロジェクト単位で「設定」メニューから保存でき、最終確認の対象ドメイン再入力だけを省略することもできます。統合確認と明示承認は省略しません。REMOTE_ONLYは自動削除せず、安全上限・再検証・バックアップのいずれかに失敗した場合は停止します。

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

GUIで実際に同期する場合は、「読み取り専用ドライラン」→「計画を再検証」→「バックアップを作成」→「承認付き同期」の順に進みます。バックアップは再検証成功後だけ作成でき、同期の最終確認では転送先・変更内容・バックアップの3項目と対象ドメインの再入力が必須です。「安全性確認」では異常終了後の残存一時ファイルを読み取り専用で検出できます。既存ファイルを含むバックアップは、履歴画面から外部変更チェックと再承認を経て復元できます。

GUIの一般プロジェクト設定はOS標準のアプリデータ領域にSQLiteで保存されます。接続先の確認コードはOS資格情報ストアへ分離して保存し、利用できない場合は同期を禁止します。旧バージョンのSQLite内にある確認コードは起動時に移行し、移行成功後に元の列を空にしてデータベースを再構成します。ただし、ストレージ媒体やOSバックアップ上の過去データまでの完全消去は保証しません。秘密鍵のファイルパスは保存しますが、秘密鍵の内容とパスフレーズは保存しません。

メニューバーの「設定」では、SSHホスト、ユーザー、リモートルート、確認コード、秘密鍵パスの起動時マスクを項目別に選べます。保存するのはマスク有無だけです。画面上の「接続情報のマスクを一時解除」で現在の表示を切り替えられ、「ヘルプ」→「バージョン情報」で実行中のバージョンを確認できます。

リモート側の読み取り専用マニフェストがある場合は、ローカルとの差分計画をJSONで出力できます。

```bash
uv run dxsync plan \
  --site wordpress \
  --root /path/to/site \
  --remote-manifest remote.json
```

製品全体の方針は[製品仕様](docs/product-spec.md)を参照してください。
初回の入力項目は[初回設定ガイド](docs/setup-guide.md)を参照してください。

## macOSアプリのビルド

Apple Silicon搭載Macでは次のコマンドで、テスト・コンパイル・起動確認・ZIP作成まで実行できます。

```bash
zsh scripts/build-app.sh
```

ビルド環境はOneDriveのプレースホルダー化を避けるため`/private/tmp`へ作成され、完成物だけが`dist/Docker XServer Sync-macOS-arm64.zip`へ保存されます。現在のビルドは自己署名・公証前の開発版です。

## Windowsアプリのビルド

Windows x64環境で次を実行すると、全テスト、Nuitkaコンパイル、起動スモークテスト、ZIP作成まで行います。Inno Setup 6が導入済みの場合は`-BuildInstaller`でインストーラーとインストール後の起動・アンインストール試験も実行できます。

```powershell
pwsh -File scripts/build-app.ps1
pwsh -File scripts/build-app.ps1 -BuildInstaller
```

完成物は`dist/Docker XServer Sync-Windows-x64.zip`と`dist/Docker-XServer-Sync-Setup-v0.1.8.exe`です。GitHubの「Build Windows app」ワークフローを手動実行することでも、Windowsランナーで同じビルドを実行できます。Windows版も現時点ではコード署名前の開発版です。

## Linuxアプリのビルド

Ubuntu x64環境では`patchelf`を導入したうえで、次のコマンドによりテスト、Nuitkaコンパイル、ヘッドレス起動確認、tar.gz作成まで実行できます。

```bash
bash scripts/build-app-linux.sh
```

完成物は`dist/Docker XServer Sync-Linux-x64.tar.gz`です。GitHubの「Build Linux app」ワークフローでも同じビルドを実行できます。Linux版はUbuntu x64向けで、現時点では署名前の開発版です。

