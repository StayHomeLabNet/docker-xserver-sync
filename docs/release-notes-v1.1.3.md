# v1.1.3

## 日本語

Docker → XServer Sync **v1.1.3** は、XServer側で指定した保護対象へのアクセスを同期処理全体で遮断し、GUIのライト／ダークテーマ表示を改善する安全性・視認性向上リリースです。

### 🛡️ 保護対象の安全性強化

- **保護対象へ一切アクセスしない動作へ統一**:
  - 保護フォルダの配下へ入らず、保護ファイル／フォルダの内容を読み取りません。
  - ハッシュ計算、差分比較、バックアップ、復元、アップロード、削除のすべてから保護対象を除外します。
  - ドライラン、計画作成・再検証、通常同期、自動同期、復旧スキャン、バックアップ復元で同じ保護規則を適用します。
  - 保護対象を識別して避けるため、親フォルダの一覧だけは取得します。
- **保護対象一覧取得の改善**:
  - 再帰的な一覧取得では、すでに保護設定済みのフォルダへ入りません。
  - XServer側の一時ファイルを調査する復旧処理でも、保護対象と対応する一時ファイルを読み取りません。

### 🎨 GUIの視認性改善

- メイン画面、プロジェクト設定、自動実行設定、履歴、復旧、承認画面、WordPress／Laravelセットアップウィザードをライト／ダークテーマの両方で点検しました。
- ライトテーマ向けに固定されていた補足文、警告文、情報パネル、確認パネルの色を、OSのQtテーマへ追従する共通スタイルへ変更しました。
- ダークテーマで文字と背景が同化する問題を解消し、状態表示を色だけに依存しない表現へ変更しました。
- 全ウィザードページを最小ウィンドウサイズでも確認し、文字切れ、重なり、操作部品の欠落がないことを確認しました。

### 📚 ドキュメント

- README、日英マニュアル、セットアップガイドを更新し、保護対象に対して行わない処理と、親フォルダ一覧だけを取得する理由を明記しました。

### 🧪 テスト

- 保護フォルダへの非侵入、保護ファイルの非読み取り、復旧・復元・リモート一覧取得の保護動作、テーマ追従スタイルを自動テストへ追加しました。
- 全 **224件** のテスト合格と、ライト／ダークテーマで計32画面の描画確認を実施しました。

---

## English

Docker → XServer Sync **v1.1.3** strengthens protected-target isolation across the complete synchronization workflow and improves GUI readability in both light and dark themes.

### 🛡️ Stronger Protected-Target Isolation

- **No access to protected targets**:
  - Synchronization does not enter protected directories or read protected files or directories.
  - Protected targets are excluded from hashing, comparison, backup, restore, upload, and deletion.
  - The same rules now apply to dry runs, plan creation and revalidation, regular and automatic synchronization, recovery scans, and backup restoration.
  - Only the parent directory is listed so each protected entry can be identified and avoided.
- **Safer remote listing and recovery**:
  - Recursive protection-target listing does not descend into directories that are already protected.
  - Recovery analysis skips protected targets and temporary artifacts corresponding to protected files.

### 🎨 GUI Readability

- Audited the main window, project settings, automatic-sync settings, history, recovery and approval dialogs, plus every WordPress and Laravel setup-wizard page in light and dark themes.
- Replaced light-theme-specific text and panel colors with shared styles based on the active Qt palette.
- Fixed low-contrast text and panels in dark mode and made status presentation less dependent on color alone.
- Verified every wizard page at its minimum supported window size with no clipped text, overlapping controls, or missing inputs.

### 📚 Documentation

- Updated the README, Japanese and English manuals, and setup guide to document every operation excluded for protected targets and why listing their parent directory remains necessary.

### 🧪 Tests

- Added automated coverage for avoiding protected directories, never reading protected files, protected recovery and restore behavior, safe remote listing, and theme-aware GUI styles.
- Verified all **224 tests** and visually rendered 32 representative screens across light and dark themes.

---

Release assets include Windows x64 portable and installer packages, a macOS Apple Silicon application archive, a Linux x64 archive, and `SHA256SUMS.txt`.
