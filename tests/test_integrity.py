from dxsync.integrity import project_config_sha256
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.presets import PRESETS, SitePreset


def test_project_config_hash_changes_when_preset_rules_change(monkeypatch) -> None:
    mapping = SyncMapping.create("public", "/tmp/site", "/home/user/public_html")
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="user.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    before = project_config_sha256(project, mapping)
    current = PRESETS[SiteType.WORDPRESS]
    changed = SitePreset(
        site_type=current.site_type,
        exclude_patterns=current.exclude_patterns + ("/new-default-exclude",),
        protect_patterns=current.protect_patterns,
        required_paths=current.required_paths,
    )
    monkeypatch.setitem(PRESETS, SiteType.WORDPRESS, changed)

    after = project_config_sha256(project, mapping)

    assert after != before
