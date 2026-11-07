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

