from dxsync.rules import PathRules


def test_directory_and_negated_rules() -> None:
    rules = PathRules((".env*", "!.env.example", "node_modules/"))
    assert rules.matches(".env")
    assert rules.matches("config/.env.local")
    assert not rules.matches(".env.example")
    assert rules.matches("node_modules/package/index.js")
    assert not rules.matches("vendor/package/file.php")


def test_glob_matches_path_components() -> None:
    rules = PathRules(("*.key", "wp-content/backup*/"))
    assert rules.matches("secrets/deploy.key")
    assert rules.matches("wp-content/backup-2026/archive.zip")
    assert not rules.matches("wp-content/uploads/photo.jpg")

