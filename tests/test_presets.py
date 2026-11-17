from dxsync.models import SiteType
from dxsync.presets import get_preset
from dxsync.rules import PathRules


def test_wordpress_preset_protects_uploads_but_not_vendor() -> None:
    preset = get_preset(SiteType.WORDPRESS)
    excludes = PathRules(preset.exclude_patterns)
    protected = PathRules(preset.protect_patterns)

    assert protected.matches("wp-content/uploads/2026/photo.jpg")
    assert not excludes.matches("vendor/autoload.php")
    assert excludes.matches(".env")
    assert not excludes.matches(".env.example")
    assert excludes.matches(".gitignore")
    assert excludes.matches("compose.yaml")
    assert excludes.matches("README.md")
    assert excludes.matches("docs/development.md")
    assert excludes.matches("tests/test_plugin.php")
    assert not excludes.matches("wp-content/plugins/example/README.md")


def test_laravel_preset_protects_runtime_storage() -> None:
    preset = get_preset(SiteType.LARAVEL)
    protected = PathRules(preset.protect_patterns)
    assert protected.matches("storage/app/public/avatar.jpg")
    assert protected.matches("storage/logs/laravel.log")
    assert not protected.matches("app/Models/User.php")


def test_static_site_preset_allows_uploads_htaccess_and_docs_while_excluding_secrets() -> None:
    preset = get_preset(SiteType.STATIC_SITE)
    excludes = PathRules(preset.exclude_patterns)
    protected = PathRules(preset.protect_patterns)

    assert preset.protect_patterns == ()
    assert preset.required_paths == ()
    assert not protected.matches("wp-content/uploads/2026/photo.jpg")
    assert not protected.matches(".htaccess")
    assert not excludes.matches("wp-content/uploads/2026/photo.jpg")
    assert not excludes.matches(".htaccess")
    assert not excludes.matches("docs/index.html")
    assert not excludes.matches("test/page.html")
    assert not excludes.matches("tests/page.html")
    assert not excludes.matches("license/index.html")
    assert excludes.matches(".git/config")
    assert excludes.matches(".env")
    assert excludes.matches(".env.production")
    assert excludes.matches("private.key")
    assert excludes.matches("node_modules/pkg/index.js")
    assert excludes.matches("docker-compose.yml")
    assert excludes.matches(".DS_Store")
