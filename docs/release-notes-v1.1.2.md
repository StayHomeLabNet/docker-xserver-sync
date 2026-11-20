# v1.1.2

## 日本語

Docker → XServer Sync **v1.1.2** は、XServer側の同期先にあるファイル／フォルダを一覧から選択し、同期による上書き・削除から保護できるようにしたメンテナンスリリースです。

### ✨ 新機能・改善

- **XServer側の保護対象を一覧から選択**:
  - 「XServer側の保護対象を設定…」画面に **「XServerから一覧を取得…」** を追加しました。
  - 初回はXServer側の同期先ルート直下だけをSFTPで読み取り、ファイルとフォルダをチェック付きツリーで素早く選択できます。
  - 配下の項目を個別に選ぶ場合だけ **「サブフォルダも含めて取得…」** で再帰取得でき、取得後も既存のチェック状態を維持します。
  - フォルダを選択した場合は配下全体、ファイルを選択した場合はそのファイルだけを保護します。
  - 一覧取得はファイル内容を読まず、SSHホスト鍵フィンガープリントを照合した上でバックグラウンド実行します。
- **画面表現の明確化**:
  - 「XServer側のテストフォルダ」を「XServer側の同期先フォルダ」に改め、本番・検証のどちらにも使う実際の役割を明示しました。
  - 保護対象がXServer側のファイル／フォルダであることを、ボタン、一覧列、ダイアログ、警告メッセージで統一しました。
- **ダークモードの表示修正**:
  - 保護設定画面の説明欄で、背景色と文字色が同化して空白に見える問題を修正しました。
  - macOSのダークモードで、保護対象ツリーのチェックボックスが描画されず選択欄が見えない問題を修正しました。

### 🧪 テスト

- XServer側のルート直下／再帰ファイル列挙、シンボリックリンク非追跡、安全上限、ツリー選択、選択状態維持、ファイル保護パターンのテストを追加し、全 **219件** のテスト合格を確認しました。

---

## English

Docker → XServer Sync **v1.1.2** adds an XServer destination browser for selecting remote files and directories that must be protected from synchronization overwrites and deletion.

### ✨ New Features & Improvements

- **Select XServer Protection Targets from a Remote Listing**:
  - Added **Load List from XServer… (XServerから一覧を取得…)** to the protection settings dialog.
  - Initially lists only files and directories directly under the XServer synchronization destination root over SFTP for a fast first response.
  - **Include Subfolders… (サブフォルダも含めて取得…)** performs optional recursive loading while preserving existing selections.
  - Selecting a directory protects its complete subtree; selecting a file protects only that file.
  - Listing reads names and entry types only, verifies the configured SSH host-key fingerprint, and runs in the background to keep the UI responsive.
- **Clearer Destination and Protection Terminology**:
  - Renamed the misleading “XServer test folder” label to “XServer synchronization destination folder.”
  - Updated buttons, table columns, dialogs, and warnings to state explicitly that protection applies to files and directories on XServer.
- **Dark-Mode Display Fix**:
  - Fixed the protection-dialog information panel appearing blank because its light background and inherited dark-theme text color had insufficient contrast.
  - Fixed protection-tree checkboxes not being drawn in macOS dark mode, which made the selection controls appear to be missing.

### 🧪 Tests

- Added coverage for root-only and recursive remote listing, symlink avoidance, entry limits, tree selection preservation, and exact-file protection patterns (`219 passed`).

---

## SHA-256

```text
c3d63083ad841b75fbe176238d8882ee1b3c577c173a265483cea11982b728f5  Docker XServer Sync-Linux-x64.tar.gz
17d0dbf00cb89b9f302d0e53a05f0d8d5f21fe0562d96d9a397125c38622229f  Docker XServer Sync-Windows-x64.zip
6e5dc81a3a79cf13894c1b4df12ce3fb1b891b27cc868095c115e8b2cc97fe93  Docker XServer Sync-macOS-arm64.zip
2c478471223b4bbc27caa38de16890e6c4c4c710bb6bb4cec31e8dd15bd2bdb9  Docker-XServer-Sync-Setup-v1.1.2.exe
```
