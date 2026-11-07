from __future__ import annotations

from dataclasses import dataclass

from .models import SiteType


COMMON_EXCLUDES = (
    ".git/",
    "/.gitignore",
    "/.gitattributes",
    "/.github/",
    "/.gitlab/",
    "/.dockerignore",
    "/.editorconfig",
    "/.idea/",
    "/.vscode/",
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose*.yml",
    "compose*.yaml",
    "Dockerfile*",
    "node_modules/",
    "/README*",
    "/readme*",
    "/CHANGELOG*",
    "/LICENSE*",
    "/license*",
    "/docs/",
    "/test/",
    "/tests/",
    "/AGENTS.md",
    "/CLAUDE.md",
    "/.phpunit.result.cache",
    "/phpunit.xml*",
    ".DS_Store",
    "Thumbs.db",
)


@dataclass(frozen=True)
class SitePreset:
    site_type: SiteType
    exclude_patterns: tuple[str, ...]
    protect_patterns: tuple[str, ...]
    required_paths: tuple[str, ...]


PRESETS = {
    SiteType.WORDPRESS: SitePreset(
        site_type=SiteType.WORDPRESS,
        exclude_patterns=COMMON_EXCLUDES,
        protect_patterns=(
            "wp-config.php",
            ".htaccess",
            "wp-content/uploads/",
            "wp-content/cache/",
            "wp-content/upgrade/",
            "wp-content/backup*/",
            "wp-content/*-backups/",
        ),
        required_paths=("wp-content",),
    ),
    SiteType.LARAVEL: SitePreset(
        site_type=SiteType.LARAVEL,
        exclude_patterns=COMMON_EXCLUDES,
        protect_patterns=(
            ".env",
            "storage/app/",
            "storage/logs/",
            "storage/framework/cache/",
            "storage/framework/sessions/",
            "storage/framework/views/",
            "bootstrap/cache/*.php",
            "public/storage",
        ),
        required_paths=("artisan", "composer.json", "app", "public"),
    ),
    SiteType.CUSTOM_PHP: SitePreset(
        site_type=SiteType.CUSTOM_PHP,
        exclude_patterns=COMMON_EXCLUDES,
        protect_patterns=(
            ".env",
            "config/local*.php",
            "uploads/",
            "storage/",
            "cache/",
            "logs/",
            "tmp/",
        ),
        required_paths=(),
    ),
}


def get_preset(site_type: SiteType) -> SitePreset:
    return PRESETS[site_type]

