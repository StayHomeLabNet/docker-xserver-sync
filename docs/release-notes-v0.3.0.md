# v0.3.0

## Laravel 初回セットアップウィザードの実装

- ローカルDockerで稼働するLaravelプロジェクトをXServerへ新規初期配置するウィザードを新設
- **2つのデータベース設定方式**:
  - XServer API（サーバーパネルAPI）を利用したDBおよびDBユーザーの自動作成
  - XServer側で事前作成済みのDB接続情報を入力して接続
- **Laravel向け推奨ディレクトリ分離配置**:
  - アプリ本体（`app/`, `vendor/`, `config/` 等）を公開領域外（`/home/{user}/{domain}_app` 等）に安全に配置
  - 公開アセット（`public/` 配下）をWeb公開ドキュメントルート（`/home/{user}/{domain}/public_html`）に配置
  - `public_html/index.php` 内の `vendor/autoload.php` および `bootstrap/app.php` の読み込みパスをリモート環境に合わせて自動書き換え
- **本番環境 `.env` の自動生成**:
  - `DB_CONNECTION`, `DB_HOST`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` を設定
  - `APP_ENV=production`, `APP_DEBUG=false`, `APP_URL` を指定のXServerドメインに合わせて設定
- **リモートArtisanコマンドの自動実行**:
  - `php artisan key:generate`（アプリケーションキー生成）
  - `php artisan migrate --force`（マイグレーション実行、SQLダンプインポートモードも選択可）
  - `php artisan storage:link`（ストレージシンボリックリンク作成）
  - `config:cache`, `route:cache`, `view:cache`（本番キャッシュ最適化）
- **完了検証**:
  - 公開URLへのHTTPSヘルスチェックによる正常起動確認
  - 完了ダイアログでのリンク付きURL表示・結果サマリー

## 進捗表示と再開機能の改善

- ドライランおよびセットアップ工程でファイル数、完了率（%）、推定残り時間、現在のファイル名をリアルタイム表示
- 処理中断後の再開ロジックの改善

## SHA-256

```text
c3c446a00d51ff68d3b4058145a2716ad4486db5a66044e76421b3758ed33d63  Docker XServer Sync-macOS-arm64.zip
```
