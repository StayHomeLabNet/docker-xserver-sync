# v1.1.2

## 日本語

Docker → XServer Sync **v1.1.2** は、XServer側の同期先にあるファイル／フォルダを一覧から選択し、同期による上書き・削除から保護できるようにしたメンテナンスリリースです。

### ✨ 新機能・改善

- **XServer側の保護対象を一覧から選択**:
  - 「XServer側の保護対象を設定…」画面に **「XServerから一覧を取得…」** を追加しました。
  - XServer側の同期先をSFTPで読み取り、ファイルとフォルダをチェック付きツリーで選択できます。
  - フォルダを選択した場合は配下全体、ファイルを選択した場合はそのファイルだけを保護します。
  - 一覧取得はファイル内容を読まず、SSHホスト鍵フィンガープリントを照合した上でバックグラウンド実行します。
- **画面表現の明確化**:
  - 「XServer側のテストフォルダ」を「XServer側の同期先フォルダ」に改め、本番・検証のどちらにも使う実際の役割を明示しました。
  - 保護対象がXServer側のファイル／フォルダであることを、ボタン、一覧列、ダイアログ、警告メッセージで統一しました。
- **ダークモードの表示修正**:
  - 保護設定画面の説明欄で、背景色と文字色が同化して空白に見える問題を修正しました。

### 🧪 テスト

- XServer側のファイル／フォルダ列挙、シンボリックリンク非追跡、安全上限、ツリー選択、ファイル保護パターンのテストを追加し、全 **216件** のテスト合格を確認しました。

---

## English

Docker → XServer Sync **v1.1.2** adds an XServer destination browser for selecting remote files and directories that must be protected from synchronization overwrites and deletion.

### ✨ New Features & Improvements

- **Select XServer Protection Targets from a Remote Listing**:
  - Added **Load List from XServer… (XServerから一覧を取得…)** to the protection settings dialog.
  - Lists files and directories under the XServer synchronization destination over SFTP in a checkable tree.
  - Selecting a directory protects its complete subtree; selecting a file protects only that file.
  - Listing reads names and entry types only, verifies the configured SSH host-key fingerprint, and runs in the background to keep the UI responsive.
- **Clearer Destination and Protection Terminology**:
  - Renamed the misleading “XServer test folder” label to “XServer synchronization destination folder.”
  - Updated buttons, table columns, dialogs, and warnings to state explicitly that protection applies to files and directories on XServer.
- **Dark-Mode Display Fix**:
  - Fixed the protection-dialog information panel appearing blank because its light background and inherited dark-theme text color had insufficient contrast.

### 🧪 Tests

- Added coverage for remote file/directory listing, symlink avoidance, entry limits, tree selection, and exact-file protection patterns (`216 passed`).

---

## SHA-256

```text
0d9036c3bdbc3147623fbe9553135876dd05be079e7d28bb7bda93ca08751c53  Docker XServer Sync-Linux-x64.tar.gz
ff002c3465d3479cf2469c5ef74df8b8ab1fef1847628702c2ced77e6f070d83  Docker XServer Sync-Windows-x64.zip
b3a5ad3a3d1770975bccb6a98480b5bbf1218fccc26cfaaf1da94200e4ad5a72  Docker XServer Sync-macOS-arm64.zip
76eb75d9aeb0f2620319a1c07ffdff20f97b5d29580b3eca42bfb9066165cb05  Docker-XServer-Sync-Setup-v1.1.2.exe
```
