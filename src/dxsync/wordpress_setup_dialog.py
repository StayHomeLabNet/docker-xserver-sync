from __future__ import annotations

import secrets
import string
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from .file_selection import CheckableFileTree
from .force_overwrite import collect_overwrite_candidates
from .models import Project, SyncMapping
from .presets import COMMON_EXCLUDES
from .wordpress_setup import (
    WordPressSetupError,
    WordPressSetupRequest,
    validate_setup_request,
)


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    return "".join(secrets.choice(alphabet) for _ in range(24))


class WordPressSetupWizard(QWizard):
    def __init__(self, project: Project, mapping: SyncMapping, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.mapping = mapping
        self.setWindowTitle("WordPressをXServerへセットアップ")
        self.resize(820, 640)
        self.setMinimumSize(720, 560)
        self.setOption(QWizard.NoBackButtonOnStartPage)
        self._candidate_root = ""
        self._mandatory_paths: tuple[str, ...] = ()
        self._all_candidates: tuple[str, ...] = ()

        self._add_source_page()
        self._add_selection_page()
        self._add_database_page()
        self._add_url_page()
        self._add_confirmation_page()
        self.currentIdChanged.connect(self._page_changed)

    def _add_source_page(self) -> None:
        page = QWizardPage()
        page.setTitle("1. ローカルWordPress")
        page.setSubTitle("WordPressファイルとDocker DBから出力したSQLを選びます。")
        form = QFormLayout(page)
        self.local_root_edit = QLineEdit(self.mapping.local_root)
        root_row = QHBoxLayout()
        root_row.addWidget(self.local_root_edit, 1)
        root_button = QPushButton("参照…")
        root_button.clicked.connect(self._browse_root)
        root_row.addWidget(root_button)
        form.addRow("WordPressルート", root_row)

        self.sql_edit = QLineEdit()
        self.sql_edit.setPlaceholderText("mysqldumpで出力した.sqlファイル")
        sql_row = QHBoxLayout()
        sql_row.addWidget(self.sql_edit, 1)
        sql_button = QPushButton("SQLを選択…")
        sql_button.clicked.connect(self._browse_sql)
        sql_row.addWidget(sql_button)
        form.addRow("SQLダンプ", sql_row)
        note = QLabel(
            "現在の版ではSQLダンプを選択します。"
            "Docker DBの自動検出とダンプは次の実装単位で追加します。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #57606a;")
        form.addRow("", note)
        self.addPage(page)

    def _add_selection_page(self) -> None:
        page = QWizardPage()
        page.setTitle("2. アップロード対象")
        page.setSubTitle(
            "WordPressコアは必須で転送し、wp-content配下は選択できます。"
        )
        layout = QVBoxLayout(page)
        excluded = QLabel(
            "次の本番不要ファイルは既定で除外します: "
            ".git、.gitignore、compose.yaml、Dockerfile、README、docs、tests、"
            "IDE設定、.env、秘密鍵、node_modules"
        )
        excluded.setWordWrap(True)
        excluded.setStyleSheet("color: #57606a;")
        layout.addWidget(excluded)
        self.core_count_label = QLabel()
        self.core_count_label.setWordWrap(True)
        layout.addWidget(self.core_count_label)
        self.file_tree = CheckableFileTree()
        self.file_tree.setMinimumHeight(260)
        self.file_tree.selectionChanged.connect(self._update_selection_label)
        layout.addWidget(self.file_tree, 1)
        buttons = QHBoxLayout()
        select_all = QPushButton("すべて選択")
        clear_all = QPushButton("すべて解除")
        select_all.clicked.connect(lambda: self.file_tree.set_all_checked(True))
        clear_all.clicked.connect(lambda: self.file_tree.set_all_checked(False))
        buttons.addWidget(select_all)
        buttons.addWidget(clear_all)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.selection_label = QLabel()
        layout.addWidget(self.selection_label)
        self.selection_page_id = self.addPage(page)

    def _add_database_page(self) -> None:
        page = QWizardPage()
        page.setTitle("3. XServerのMySQL")
        page.setSubTitle("XServer APIで新しいDBとユーザーを作成します。")
        form = QFormLayout(page)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("xs_...（保存しません）")
        initial_domain = (
            self.project.ssh_host
            if self.project.ssh_host.startswith(self.project.ssh_username + ".")
            else f"{self.project.ssh_username}.xsrv.jp"
        )
        self.servername_edit = QLineEdit(initial_domain)
        self.servername_edit.setPlaceholderText("サーバーID.xsrv.jp（API用初期ドメイン）")
        self.db_host_edit = QLineEdit()
        self.db_host_edit.setPlaceholderText("例: mysql1234.xserver.jp")
        suffix = self.project.domain.split(".", 1)[0].replace("-", "_")[:16] or "wordpress"
        self.db_suffix_edit = QLineEdit(suffix)
        self.user_suffix_edit = QLineEdit((suffix + "_user")[:32])
        self.db_password_edit = QLineEdit(_password())
        self.db_password_edit.setEchoMode(QLineEdit.Password)
        reveal = QPushButton("表示/非表示")
        reveal.clicked.connect(self._toggle_password)
        password_row = QHBoxLayout()
        password_row.addWidget(self.db_password_edit, 1)
        password_row.addWidget(reveal)
        form.addRow("XServer APIキー", self.api_key_edit)
        form.addRow("対象サーバー", self.servername_edit)
        form.addRow("MySQLホスト", self.db_host_edit)
        form.addRow("DB名サフィックス", self.db_suffix_edit)
        form.addRow("DBユーザーサフィックス", self.user_suffix_edit)
        form.addRow("DBパスワード", password_row)
        hint = QLabel(
            "APIキーは対象サーバーを限定し、MySQLの読み書き権限だけを"
            "付与することを推奨します。APIキーとDBパスワードは今回の実行中だけ保持します。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #57606a;")
        form.addRow("", hint)
        self.addPage(page)

    def _add_url_page(self) -> None:
        page = QWizardPage()
        page.setTitle("4. URLとWordPress設定")
        page.setSubTitle("URLが変わる場合はWP-CLIで安全に置換します。")
        form = QFormLayout(page)
        self.old_url_edit = QLineEdit("http://localhost")
        self.new_url_edit = QLineEdit(f"https://{self.project.domain}")
        self.prefix_edit = QLineEdit("wp_")
        form.addRow("ローカルURL", self.old_url_edit)
        form.addRow("XServerのURL", self.new_url_edit)
        form.addRow("テーブル接頭辞", self.prefix_edit)
        warning = QLabel(
            "URLが異なる場合、XServerのSSH環境でWP-CLIが使えなければ"
            "セットアップを停止します。単純なSQL文字列置換は行いません。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #9a6700;")
        form.addRow("", warning)
        self.addPage(page)

    def _add_confirmation_page(self) -> None:
        page = QWizardPage()
        page.setTitle("5. 最終確認")
        page.setSubTitle("対象を確認し、ドメインを再入力してください。")
        layout = QVBoxLayout(page)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("background: #f6f8fa; padding: 12px;")
        layout.addWidget(self.summary_label)
        form = QFormLayout()
        self.confirm_domain_edit = QLineEdit()
        self.confirm_domain_edit.setPlaceholderText(self.project.domain)
        form.addRow("対象ドメインを再入力", self.confirm_domain_edit)
        layout.addLayout(form)
        caution = QLabel(
            "新しいDBとDBユーザーを作成し、SQLとWordPressファイルを配置します。"
            "途中失敗時に作成済みDBを自動削除はしません。"
        )
        caution.setWordWrap(True)
        caution.setStyleSheet("color: #cf222e;")
        layout.addWidget(caution)
        self.addPage(page)

    def _browse_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "WordPressルートを選択")
        if selected:
            self.local_root_edit.setText(selected)

    def _browse_sql(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "SQLダンプを選択", filter="SQL (*.sql);;すべてのファイル (*)"
        )
        if selected:
            self.sql_edit.setText(selected)

    def _toggle_password(self) -> None:
        mode = (
            QLineEdit.Normal
            if self.db_password_edit.echoMode() == QLineEdit.Password
            else QLineEdit.Password
        )
        self.db_password_edit.setEchoMode(mode)

    def _page_changed(self, page_id: int) -> None:
        if page_id == self.selection_page_id:
            self._refresh_file_selection()
        if page_id != self.pageIds()[-1]:
            return
        if not self._all_candidates:
            self._refresh_file_selection()
        server_id = self.servername_edit.text().strip().split(".", 1)[0]
        selected_content = self.file_tree.selected_file_paths()
        self.summary_label.setText(
            f"WordPress: {self.local_root_edit.text().strip()}\n"
            f"SQL: {self.sql_edit.text().strip()}\n"
            f"転送先: {self.mapping.remote_root}\n"
            f"公開URL: {self.new_url_edit.text().strip()}\n"
            f"転送ファイル: {len(self._mandatory_paths) + len(selected_content)} "
            f"(必須コア {len(self._mandatory_paths)} / wp-content {len(selected_content)})\n"
            f"作成DB: {server_id}_{self.db_suffix_edit.text().strip()}\n"
            f"作成DBユーザー: {server_id}_{self.user_suffix_edit.text().strip()}"
        )

    def _refresh_file_selection(self) -> None:
        root_text = self.local_root_edit.text().strip()
        if root_text == self._candidate_root and self._all_candidates:
            return
        try:
            candidates = collect_overwrite_candidates(
                Path(root_text),
                exclude_patterns=COMMON_EXCLUDES
                + ("wp-config.php", "*.sql", "*.sql.gz"),
                protect_patterns=(),
            )
        except Exception as exc:
            self._candidate_root = ""
            self._mandatory_paths = ()
            self._all_candidates = ()
            self.file_tree.set_file_paths(())
            QMessageBox.warning(self, "アップロード対象を確認できません", str(exc))
            return
        self._candidate_root = root_text
        self._all_candidates = candidates
        self._mandatory_paths = tuple(
            path for path in candidates if not path.startswith("wp-content/")
        )
        optional = tuple(
            path for path in candidates if path.startswith("wp-content/")
        )
        self.file_tree.set_file_paths(optional)
        self.core_count_label.setText(
            f"WordPressコアとルートの{len(self._mandatory_paths)}ファイルは、"
            "動作に必要なため必ず転送します。"
        )
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        selected = len(self.file_tree.selected_file_paths())
        total = sum(
            1 for path in self._all_candidates if path.startswith("wp-content/")
        )
        self.selection_label.setText(
            f"wp-content: {selected}/{total}ファイルを転送"
        )

    def request_value(self) -> WordPressSetupRequest:
        if not self._all_candidates and self.local_root_edit.text().strip():
            self._refresh_file_selection()
        return WordPressSetupRequest(
            local_root=Path(self.local_root_edit.text().strip()),
            sql_dump=Path(self.sql_edit.text().strip()),
            remote_root=self.mapping.remote_root,
            domain=self.project.domain,
            old_url=self.old_url_edit.text().strip(),
            new_url=self.new_url_edit.text().strip(),
            db_host=self.db_host_edit.text().strip(),
            db_name_suffix=self.db_suffix_edit.text().strip(),
            db_user_suffix=self.user_suffix_edit.text().strip(),
            db_password=self.db_password_edit.text(),
            table_prefix=self.prefix_edit.text().strip(),
            include_paths=tuple(
                sorted(
                    self._mandatory_paths
                    + self.file_tree.selected_file_paths()
                )
            ),
        )

    def accept(self) -> None:
        if self.confirm_domain_edit.text().strip() != self.project.domain:
            QMessageBox.warning(self, "ドメインを確認", "対象ドメインが一致しません。")
            return
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "APIキーを確認", "XServer APIキーを入力してください。")
            return
        try:
            validate_setup_request(self.request_value())
        except WordPressSetupError as exc:
            QMessageBox.warning(self, "入力を確認", str(exc))
            return
        super().accept()

    @property
    def api_key(self) -> str:
        return self.api_key_edit.text().strip()

    @property
    def servername(self) -> str:
        return self.servername_edit.text().strip()
