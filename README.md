<div align="center">

# 🐳 Docker XServer Sync

## ローカルDockerからXServerへの安全な一方向同期・セットアップツール

**Docker XServer Sync は、ローカルDocker環境で稼働する WordPress / Laravel / 独自PHP サイトを、  
読み取り専用ドライラン、承認、差分検証、事前バックアップを経てXServer本番環境へ安全に配置・同期するデスクトップアプリです。**

[English](README.en.md) | [日本語](README.md)

<br>

[機能](#機能) ・ [インストール](#インストール) ・ [クイックスタート](#クイックスタート) ・ [Laravel初回セットアップ](#laravel初回セットアップ) ・ [WordPress初回セットアップ](#wordpress初回セットアップ) ・ [CLIでの利用](#cliでの利用) ・ [開発・ビルド](#開発ビルド) ・ [ライセンス](#ライセンス)

<br>

![version](https://img.shields.io/badge/version-0.3.1-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-orange)
![app](https://img.shields.io/badge/app-PySide6%20%2F%20Qt6-purple)
![python](https://img.shields.io/badge/python-3.12-blue)
![docker](https://img.shields.io/badge/docker-supported-brightgreen)
![xserver](https://img.shields.io/badge/target-XServer-lightgrey)

<br>

<a href="https://ko-fi.com/stayhomelabnet">
  <img src="https://img.shields.io/badge/Ko--fi-Support%20StayHomeLabNet-ff5e5b?style=for-the-badge&logo=kofi&logoColor=white" alt="Support StayHomeLabNet on Ko-fi">
</a>
<a href="https://buymeacoffee.com/stayhomelabnet">
  <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support%20StayHomeLabNet-ffdd00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Support StayHomeLabNet on Buy Me a Coffee">
</a>

<br>

**Docker XServer Sync が役に立ったら、ぜひサポートをご検討ください。**

</div>

---

Dockerへbind mountしたローカル開発環境のファイルを、安全な検証とバックアップを経てXServerへ確実に反映するためのGUIデスクトップアプリケーションです。WordPressやLaravelの新規初回セットアップから日々の同期まで、安全性を最優先にした設計となっています。

## 機能

- **ローカルからXServerへの安全な一方向同期**:
  - SSHホスト鍵フィンガープリント（SHA-256）の厳密照合
  - リモートを変更しない読み取り専用ドライラン
  - 15分間有効な差分計画のステージング保存
  - 本番書き込み前の自動リモートバックアップ作成
  - 一時名転送・ハッシュ照合・アトミック切り替えによる安全同期（失敗時は自動ロールバック）
  - 本番で生成されるユーザーデータディレクトリ（`uploads`、`storage`等）の保護
  - 同期後の自動HTTPSヘルスチェック
- **Laravel 初回セットアップウィザード**:
  - アプリ本体（`app/`等）とWeb公開領域（`public_html`）のディレクトリ分離配置
  - `public/index.php` 内の相対パス（autoload / bootstrap）自動書き換え
  - 本番環境向け `.env` の自動生成
  - リモートArtisanコマンド（`key:generate`, `migrate`, `storage:link`, キャッシュ最適化）の自動実行
  - XServer APIによるDB自動作成、または作成済みDBへの接続に対応
  - ローカルComposer環境からのPHPバージョン自動検出（PHP 8.4等対応）と互換性バリデーション
  - 配置済みファイルの高速スキップ機能によるレジューム高速化
- **WordPress 初回セットアップウィザード**:
  - XServer APIによるDB/ユーザー新規作成、または事前作成済み空DBへの接続
  - WP-CLIを利用したシリアライズ対応の安全なドメイン/URL一括置換
  - XServer側PHPバージョンに応じたWP-CLI環境の自動照合
- **進捗追跡と中断・再開**:
  - ファイル数・割合（%）・推定残り時間（ETA）・現在のパスのリアルタイム表示
  - 安全な区切りでの中断と、次回起動後のチェックポイント再開
- **マルチプラットフォーム対応**:
  - macOS (Apple Silicon arm64)、Windows (x64 インストーラー & zip)、Linux (Ubuntu x64)

## インストール

[GitHub Releases](https://github.com/StayHomeLabNet/docker-xserver-sync/releases/latest) より最新版をダウンロードしてください。

### macOS
1. `Docker XServer Sync-macOS-arm64.zip` をダウンロードします。
2. 解凍して現れる `Docker XServer Sync.app` を `/Applications`（アプリケーションフォルダ）へ移動して起動します。

### Windows
- **インストーラー（推奨）**: `Docker-XServer-Sync-Setup-v0.3.1.exe` を実行してセットアップします。
- **ポータブル版**: `Docker XServer Sync-Windows-x64.zip` を展開し、フォルダ内の `Docker XServer Sync.exe` を起動します。

### Linux
`Docker XServer Sync-Linux-x64.tar.gz` を展開し、バイナリを実行します。

---

## クイックスタート

1. アプリを起動し、「新規プロジェクト」をクリックして設定を作成します。
2. サイト種別（WordPress / Laravel / カスタムPHP）を選択し、ローカルフォルダとXServerの接続情報を入力します。
3. **安全同期の手順**:
   - **読み取り専用ドライラン**: ローカルとXServerのファイルを照合し、変更計画を作成します。
   - **計画を再検証**: 反映直前に差分が変わっていないかを検証します。
   - **バックアップを作成**: 対象リモートファイルのバックアップを取得します。
   - **承認付き同期**: 変更内容とドメインを確認・承認してXServerへ反映します。
4. 設定画面から「安全同期を自動実行」を設定することで、ドライラン後の1クリック承認のみで一連の安全手順を自動完了させることも可能です。

---

## Laravel初回セットアップ

ローカルDockerのLaravelサイトをXServerへ新規配置する手順です：

1. プロジェクト種別を「Laravel」にして保存したプロジェクトを選択します。
2. 「Laravel初回セットアップ」欄の「セットアップを開始…」をクリックします。
3. **ウィザードの入力手順**:
   - **データベース設定**: XServer APIを利用して自動作成するか、サーバーパネルで作成済みのDB接続情報を入力します。
   - **リモートパス設定**:
     - 本体配置先: `/home/{user}/{domain}_app`（公開領域外）
     - 公開配置先: `/home/{user}/{domain}/public_html`（ドキュメントルート）
   - **PHPバージョン**: ローカルの `platform_check.php` から要求バージョン（例: PHP 8.4）が自動検出されます。
   - **アプリケーション設定**: アプリ名、本番URL、マイグレーション方式（Artisan migrate または SQLダンプ）を選択します。
4. 実行すると、ファイル転送、`index.php` 書き換え、`.env` 生成、Artisanコマンド実行、HTTPS疎通確認まで全自動で完了します。

---

## WordPress初回セットアップ

ローカルDockerのWordPressサイトをXServerへ新規配置する手順です：

1. プロジェクト種別を「WordPress」にして保存したプロジェクトを選択します。
2. 「WordPress初回セットアップ」欄の「セットアップを開始…」をクリックします。
3. DB作成方法（API自動作成 / 作成済みDB）を選択し、SQLダンプファイルと転送する `wp-content` コンテンツを指定します。
4. 使用するPHPバージョン（8.0〜8.5、既定: 8.3）を選択して実行します。
5. ファイル配置、DBインポート、WP-CLIによるURL置換、HTTPS確認が自動実行されます。

---

## CLIでの利用

CLIコマンド `dxsync` を使ってターミナルからスキャンやドライランを実行することも可能です。

```bash
# ローカル走査
dxsync scan --site wordpress --root /path/to/site

# XServerとの読み取り専用ドライラン
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

## 開発・ビルド

Python 3.12以上と [uv](https://docs.astral.sh/uv/) を使用します。

```bash
# 依存関係のインストール
uv sync --extra dev --extra gui --extra ssh --extra build

# ユニットテスト実行（全135件）
uv run pytest

# GUI起動
uv run dxsync-gui
```

### パッケージのビルド

- **macOS**: `zsh scripts/build-app.sh` → `dist/Docker XServer Sync-macOS-arm64.zip`
- **Windows**: `pwsh -File scripts/build-app.ps1 -BuildInstaller` → `dist/Docker-XServer-Sync-Setup-v0.3.1.exe`
- **Linux**: `bash scripts/build-app-linux.sh` → `dist/Docker XServer Sync-Linux-x64.tar.gz`

---

## ライセンス

本プロジェクトは [MIT License](LICENSE) のもとで公開されています。
