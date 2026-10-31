from pathlib import Path

import pytest

from dxsync.models import Project, SiteType, SyncMapping
from dxsync.validation import normalize_remote_root, validate_project


def make_project(tmp_path: Path, mappings: tuple[SyncMapping, ...]) -> Project:
    return Project.create(
        name="Example",
        site_type=SiteType.LARAVEL,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="example",
        host_key_fingerprint="SHA256:test",
        private_key_reference="keyring://example",
        mappings=mappings,
    )


@pytest.mark.parametrize("value", ["/", "/home", "relative/path", "/safe/../escape"])
def test_dangerous_remote_roots_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_remote_root(value)


def test_overlapping_remote_mappings_are_rejected(tmp_path: Path) -> None:
    local_a = tmp_path / "a"
    local_b = tmp_path / "b"
    local_a.mkdir()
    local_b.mkdir()
    project = make_project(
        tmp_path,
        (
            SyncMapping.create("app", str(local_a), "/home/user/app"),
            SyncMapping.create("nested", str(local_b), "/home/user/app/public"),
        ),
    )
    issues = validate_project(project)
    assert any(issue.code == "overlapping_remote_roots" for issue in issues)


def test_missing_connection_fields_are_rejected(tmp_path: Path) -> None:
    local = tmp_path / "site"
    local.mkdir()
    project = make_project(
        tmp_path,
        (SyncMapping.create("public", str(local), "/home/user/public"),),
    )
    project = Project(
        **{
            **project.__dict__,
            "ssh_host": "",
            "ssh_username": " ",
        }
    )
    issues = validate_project(project)
    assert {issue.code for issue in issues} >= {"ssh_host", "ssh_username"}

