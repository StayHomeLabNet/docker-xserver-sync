from __future__ import annotations

import secrets
import string
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from .models import Project, SyncMapping
from .laravel_setup import (
    LaravelSetupError,
    LaravelSetupRequest,
    detect_laravel_required_php,
    validate_laravel_setup_request,
)


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    return "".join(secrets.choice(alphabet) for _ in range(24))


class LaravelSetupWizard(QWizard):
    def __init__(
        self,
        project: Project,
        mapping: SyncMapping,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.mapping = mapping
        self.setWindowTitle("LaravelをXServerへ新規セットアップ")
        self.resize(960, 640)
        self.setMinimumSize(850, 580)
        self.setOption(QWizard.NoBackButtonOnStartPage)

        self._add_source_page()
        self._add_paths_page()
        self._add_database_page()
        self._add_laravel_settings_page()
        self._add_confirmation_page()
        self._configure_input_widths()
        self.currentIdChanged.connect(self._page_changed)

    def _configure_input_widths(self) -> None:
        for editor in self.findChildren(QLineEdit):
            editor.setMinimumWidth(560)

    def _add_source_page(self) -> None:
        page = QWizardPage()
        page.setTitle("1. ローカルLaravel環境")
        page.setSubTitle("Laravelプロジェクトルートとデータベース初期化方法を選びます。")
        form = QFormLayout(page)

        self.local_root_edit = QLineEdit(self.mapping.local_root)
        root_row = QHBoxLayout()
        root_row.addWidget(self.local_root_edit, 1)
        root_button = QPushButton("参照…")
        root_button.clicked.connect(self._browse_root)
        root_row.addWidget(root_button)
        form.addRow("Laravelルート", root_row)

        self.migration_mode_combo = QComboBox()
        self.migration_mode_combo.addItem("artisan migrate を実行（推奨）", "migrate")
        self.migration_mode_combo.addItem("SQLダンプをインポート", "sql_dump")
        self.migration_mode_combo.currentIndexChanged.connect(self._migration_mode_changed)
        form.addRow("DB初期化方法", self.migration_mode_combo)

        self.run_seed_check = QCheckBox("マイグレーション時に --seed（初期データ投入）を実行する")
        form.addRow("", self.run_seed_check)

        self.sql_edit = QLineEdit()
        self.sql_edit.setPlaceholderText("Docker DB等から出力した .sql ファイル")
        sql_row = QHBoxLayout()
        sql_row.addWidget(self.sql_edit, 1)
        self.sql_button = QPushButton("SQLを選択…")
        self.sql_button.clicked.connect(self._browse_sql)
        sql_row.addWidget(self.sql_button)
        self.sql_label = QLabel("SQLダンプ")
        form.addRow(self.sql_label, sql_row)

        self.vendor_note = QLabel(
            "注意: 依存関係（vendor/）はローカル環境から転送します。"
            "事前にDocker/ローカル環境で 'composer install' および 'npm run build' が完了していることを確認してください。"
        )
        self.vendor_note.setWordWrap(True)
        self.vendor_note.setStyleSheet("color: #57606a; margin-top: 10px;")
        form.addRow("", self.vendor_note)

        self._migration_mode_changed()
        self.addPage(page)

    def _migration_mode_changed(self) -> None:
        is_sql = self.migration_mode_combo.currentData() == "sql_dump"
        self.sql_edit.setEnabled(is_sql)
        self.sql_button.setEnabled(is_sql)
        self.sql_label.setEnabled(is_sql)
        self.run_seed_check.setEnabled(not is_sql)

    def _add_paths_page(self) -> None:
        page = QWizardPage()
        page.setTitle("2. XServer上の配置先")
        page.setSubTitle("セキュリティのため、Laravel本体（非公開）とpublic（公開）を分離して配置します。")
        form = QFormLayout(page)

        # Default paths
        # public: /home/{ssh_username}/{domain}/public_html
        # app: /home/{ssh_username}/{domain}_app
        default_public = (
            self.mapping.remote_root
            if self.mapping.remote_root
            else f"/home/{self.project.ssh_username}/{self.project.domain}/public_html"
        )
        default_app = f"/home/{self.project.ssh_username}/{self.project.domain}_app"

        self.remote_public_edit = QLineEdit(default_public)
        form.addRow("Web公開ディレクトリ (public)", self.remote_public_edit)

        self.remote_app_edit = QLineEdit(default_app)
        form.addRow("Laravel本体ディレクトリ", self.remote_app_edit)

        info_label = QLabel(
            "推奨構成: Laravel本体をWeb公開ディレクトリの外に配置し、"
            "public_html/index.php 内の読み込みパスを自動的に本体へ向けます。\n"
            "これにより、.envやソースコードがWeb上に誤って露出するリスクを完全に防ぎます。"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "background: #ddf4ff; color: #0969da; padding: 10px; border: 1px solid #54aeff;"
        )
        form.addRow("", info_label)
        self.addPage(page)

    def _add_database_page(self) -> None:
        page = QWizardPage()
        page.setTitle("3. XServerのMySQL")
        page.setSubTitle("APIで新規作成するか、サーバーパネルで作成済みの空DBを使用します。")
        form = QFormLayout(page)

        self.database_mode_combo = QComboBox()
        self.database_mode_combo.addItem("作成済みDBを使用（APIキー不要）", "existing")
        self.database_mode_combo.addItem("XServer APIで新規作成", "api")
        self.database_mode_combo.currentIndexChanged.connect(self._database_mode_changed)

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

        self.db_host_edit = QLineEdit("localhost")
        self.db_host_edit.setPlaceholderText("MariaDB 10.5 / 10.11: localhost")

        suffix = (
            self.project.domain.split(".", 1)[0].replace("-", "_")[:16]
            or "laravel"
        )
        self.db_suffix_edit = QLineEdit(suffix)
        self.user_suffix_edit = QLineEdit((suffix + "_user")[:32])

        self.existing_db_name_edit = QLineEdit()
        self.existing_db_name_edit.setPlaceholderText("例: xs123456_laravel")
        self.existing_db_user_edit = QLineEdit()
        self.existing_db_user_edit.setPlaceholderText("例: xs123456_lar_user")

        self.db_password_edit = QLineEdit(_password())
        self.db_password_edit.setEchoMode(QLineEdit.Password)
        reveal = QPushButton("表示/非表示")
        reveal.clicked.connect(self._toggle_password)
        password_row = QHBoxLayout()
        password_row.addWidget(self.db_password_edit, 1)
        password_row.addWidget(reveal)

        form.addRow("DBの準備方法", self.database_mode_combo)
        form.addRow("XServer APIキー", self.api_key_edit)
        form.addRow("API対象サーバー", self.servername_edit)
        form.addRow("MySQLホスト", self.db_host_edit)
        form.addRow("DB名サフィックス", self.db_suffix_edit)
        form.addRow("DBユーザーサフィックス", self.user_suffix_edit)
        form.addRow("作成済みDB名", self.existing_db_name_edit)
        form.addRow("作成済みDBユーザー", self.existing_db_user_edit)
        form.addRow("DBパスワード", password_row)

        self.database_instructions = QLabel()
        self.database_instructions.setWordWrap(True)
        self.database_instructions.setStyleSheet(
            "background: #fff8c5; color: #5d4a00; padding: 10px; border: 1px solid #d4a72c;"
        )
        form.addRow("", self.database_instructions)

        self.existing_db_prepared_check = QCheckBox(
            "DB・MySQLユーザーの作成とアクセス権付与が完了しています"
        )
        form.addRow("事前準備の確認", self.existing_db_prepared_check)

        self._database_mode_changed()
        self.addPage(page)

    def _database_mode_changed(self) -> None:
        uses_api = self.uses_xserver_api
        for editor in (
            self.api_key_edit,
            self.servername_edit,
            self.db_suffix_edit,
            self.user_suffix_edit,
        ):
            editor.setEnabled(uses_api)

        self.existing_db_name_edit.setEnabled(not uses_api)
        self.existing_db_user_edit.setEnabled(not uses_api)
        self.existing_db_prepared_check.setVisible(not uses_api)

        if uses_api:
            self.database_instructions.setText(
                "この方式では、アプリがXServer APIを使って新しいDBと"
                "MySQLユーザーを作成し、アクセス権を付与します。\n"
                "APIキーとDBパスワードは保存しません。"
            )
        else:
            self.database_instructions.setText(
                "重要: 先にXServerのサーバーパネルで、次の順に準備してください。\n"
                "1. 空のDBを作成\n"
                "2. MySQLユーザーを作成\n"
                "3. そのユーザーをDBのアクセス権所有ユーザーへ追加\n"
                "準備後、完全なDB名・ユーザー名・パスワードを入力してください。"
            )

    def _add_laravel_settings_page(self) -> None:
        page = QWizardPage()
        page.setTitle("4. Laravel本番設定")
        page.setSubTitle("本番環境の.env設定やCLI用PHPバージョンを指定します。")
        form = QFormLayout(page)

        default_name = self.project.name or "Laravel"
        self.app_name_edit = QLineEdit(default_name)
        form.addRow("APP_NAME", self.app_name_edit)

        self.app_url_edit = QLineEdit(f"https://{self.project.domain}")
        form.addRow("APP_URL（公開URL）", self.app_url_edit)

        root = Path(self.mapping.local_root).expanduser().resolve()
        detected_php = detect_laravel_required_php(root)
        self.php_cli_version_combo = QComboBox()
        for version in ("8.5", "8.4", "8.3", "8.2", "8.1"):
            label = f"PHP {version}"
            if detected_php and version == detected_php:
                label += "（検出: 推奨）"
            elif not detected_php and version == "8.3":
                label += "（推奨）"
            self.php_cli_version_combo.addItem(label, version)
        target_default = (
            detected_php
            if detected_php in ("8.5", "8.4", "8.3", "8.2", "8.1")
            else "8.3"
        )
        idx = self.php_cli_version_combo.findData(target_default)
        if idx >= 0:
            self.php_cli_version_combo.setCurrentIndex(idx)
        form.addRow("CLI用PHPバージョン", self.php_cli_version_combo)

        note = QLabel(
            "・APP_ENVは 'production'、APP_DEBUGは 'false' に自動設定されます。\n"
            "・APP_KEYは本番用にリモート側で自動生成されます。\n"
            "・CLI用PHPバージョンは、XServerの「PHP Ver.切替」で対象ドメインに"
            "設定したバージョンと合わせてください。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #57606a; margin-top: 10px;")
        form.addRow("", note)
        self.addPage(page)

    def _add_confirmation_page(self) -> None:
        page = QWizardPage()
        page.setTitle("5. 最終確認")
        page.setSubTitle("設定内容を確認し、対象ドメインを再入力してください。")
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
            "Laravel本体ファイルおよびpublic配下のファイルがXServerへ転送され、"
            "本番用.envの生成、キー生成、マイグレーション、キャッシュ最適化が実行されます。"
        )
        caution.setWordWrap(True)
        caution.setStyleSheet("color: #cf222e;")
        layout.addWidget(caution)
        self.addPage(page)

    def _browse_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Laravelルートを選択")
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
        if page_id != self.pageIds()[-1]:
            return
        server_id = self.servername_edit.text().strip().split(".", 1)[0]
        if self.uses_xserver_api:
            db_summary = (
                "DB準備: XServer APIで新規作成\n"
                f"作成DB: {server_id}_{self.db_suffix_edit.text().strip()}\n"
                f"作成DBユーザー: {server_id}_{self.user_suffix_edit.text().strip()}"
            )
        else:
            db_summary = (
                "DB準備: 作成済みDBを使用\n"
                f"DB名: {self.existing_db_name_edit.text().strip()}\n"
                f"DBユーザー: {self.existing_db_user_edit.text().strip()}"
            )

        mig_mode = (
            "artisan migrate --force"
            + (" (--seed)" if self.run_seed_check.isChecked() else "")
            if self.migration_mode_combo.currentData() == "migrate"
            else f"SQLダンプ: {self.sql_edit.text().strip()}"
        )

        summary_text = (
            f"対象ドメイン: {self.project.domain}\n"
            f"公開URL: {self.app_url_edit.text().strip()}\n"
            f"Laravel本体配置先: {self.remote_app_edit.text().strip()}\n"
            f"Web公開配置先: {self.remote_public_edit.text().strip()}\n"
            f"PHP CLI: PHP {self.php_cli_version_combo.currentData()}\n"
            f"DB初期化: {mig_mode}\n"
            f"{db_summary}"
        )
        self.summary_label.setText(summary_text)

    def validateCurrentPage(self) -> bool:
        current = self.currentId()
        page_ids = self.pageIds()

        # Page 1: Source
        if current == page_ids[0]:
            root = Path(self.local_root_edit.text().strip())
            if not root.is_dir():
                QMessageBox.warning(self, "エラー", f"Laravelルートが見つかりません:\n{root}")
                return False
            for req in ("artisan", "composer.json", "app", "public", "bootstrap"):
                if not (root / req).exists():
                    QMessageBox.warning(
                        self, "エラー", f"Laravelに必要な '{req}' がルートに見つかりません。"
                    )
                    return False
            vendor = root / "vendor"
            if not vendor.is_dir() or not (vendor / "autoload.php").is_file():
                QMessageBox.warning(
                    self,
                    "vendor/ が見つかりません",
                    "ローカルに vendor/autoload.php が見つかりません。\n"
                    "事前にDocker/ローカル環境で 'composer install' を実行してください。",
                )
                return False
            if self.migration_mode_combo.currentData() == "sql_dump":
                sql_path = Path(self.sql_edit.text().strip())
                if not sql_path.is_file():
                    QMessageBox.warning(self, "エラー", "有効なSQLダンプファイルを選択してください。")
                    return False

        # Page 2: Paths
        elif current == page_ids[1]:
            app_p = self.remote_app_edit.text().strip()
            pub_p = self.remote_public_edit.text().strip()
            if not app_p or not pub_p:
                QMessageBox.warning(self, "エラー", "本体と公開ディレクトリのパスを両方入力してください。")
                return False
            if app_p == pub_p:
                QMessageBox.warning(
                    self,
                    "エラー",
                    "本体ディレクトリとWeb公開ディレクトリは異なるディレクトリを指定してください。",
                )
                return False

        # Page 3: Database
        elif current == page_ids[2]:
            if self.uses_xserver_api:
                if not self.api_key_edit.text().strip():
                    QMessageBox.warning(self, "エラー", "XServer APIキーを入力してください。")
                    return False
                if not self.servername_edit.text().strip():
                    QMessageBox.warning(self, "エラー", "API対象サーバーを入力してください。")
                    return False
                if not self.db_suffix_edit.text().strip():
                    QMessageBox.warning(self, "エラー", "DB名サフィックスを入力してください。")
                    return False
                if not self.user_suffix_edit.text().strip():
                    QMessageBox.warning(self, "エラー", "DBユーザーサフィックスを入力してください。")
                    return False
            else:
                if not self.existing_db_prepared_check.isChecked():
                    QMessageBox.warning(
                        self,
                        "事前準備の確認が必要です",
                        "XServerのサーバーパネルでDB・ユーザー・アクセス権の作成が完了していることを確認し、チェックを入れてください。",
                    )
                    return False
                if not self.existing_db_name_edit.text().strip():
                    QMessageBox.warning(self, "エラー", "作成済みDB名を入力してください。")
                    return False
                if not self.existing_db_user_edit.text().strip():
                    QMessageBox.warning(self, "エラー", "作成済みDBユーザー名を入力してください。")
                    return False
            if not self.db_password_edit.text():
                QMessageBox.warning(self, "エラー", "DBパスワードを入力してください。")
                return False

        # Page 4: Settings
        elif current == page_ids[3]:
            if not self.app_url_edit.text().strip():
                QMessageBox.warning(self, "エラー", "公開URL（APP_URL）を入力してください。")
                return False
            root = Path(self.mapping.local_root).expanduser().resolve()
            detected = detect_laravel_required_php(root)
            if detected:
                selected = self.php_cli_version_combo.currentData()

                def _to_tuple(v: str) -> tuple[int, ...]:
                    try:
                        return tuple(int(x) for x in v.split("."))
                    except ValueError:
                        return (0,)

                if _to_tuple(selected) < _to_tuple(detected):
                    QMessageBox.warning(
                        self,
                        "PHPバージョン不一致",
                        f"ローカルのComposer環境（platform_check.php）は PHP {detected} 以上を要求しています。\n\n"
                        f"CLI用PHPバージョンには PHP {detected} 以上を選択し、XServerの「PHP Ver.切替」も合わせてください。",
                    )
                    return False

        # Page 5: Confirmation
        elif current == page_ids[4]:
            if self.confirm_domain_edit.text().strip() != self.project.domain:
                QMessageBox.warning(
                    self,
                    "ドメインが一致しません",
                    f"安全のため、対象ドメイン '{self.project.domain}' を正確に入力してください。",
                )
                return False

        return True

    @property
    def uses_xserver_api(self) -> bool:
        return self.database_mode_combo.currentData() == "api"

    @property
    def api_key(self) -> str:
        return self.api_key_edit.text().strip()

    @property
    def servername(self) -> str:
        return self.servername_edit.text().strip()

    def request_value(self) -> LaravelSetupRequest:
        sql_text = self.sql_edit.text().strip()
        sql_path = Path(sql_text) if sql_text else None

        return LaravelSetupRequest(
            local_root=Path(self.local_root_edit.text().strip()),
            remote_app_root=self.remote_app_edit.text().strip(),
            remote_public_root=self.remote_public_edit.text().strip(),
            domain=self.project.domain,
            app_url=self.app_url_edit.text().strip(),
            app_name=self.app_name_edit.text().strip() or "Laravel",
            db_host=self.db_host_edit.text().strip() or "localhost",
            database_mode=self.database_mode_combo.currentData(),
            db_name_suffix=self.db_suffix_edit.text().strip(),
            db_user_suffix=self.user_suffix_edit.text().strip(),
            db_password=self.db_password_edit.text(),
            existing_db_name=self.existing_db_name_edit.text().strip(),
            existing_db_user=self.existing_db_user_edit.text().strip(),
            php_cli_version=self.php_cli_version_combo.currentData(),
            migration_mode=self.migration_mode_combo.currentData(),
            sql_dump=sql_path,
            run_seed=self.run_seed_check.isChecked(),
        )
