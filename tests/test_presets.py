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


def test_laravel_preset_protects_runtime_storage() -> None:
    preset = get_preset(SiteType.LARAVEL)
    protected = PathRules(preset.protect_patterns)
    assert protected.matches("storage/app/public/avatar.jpg")
    assert protected.matches("storage/logs/laravel.log")
    assert not protected.matches("app/Models/User.php")

