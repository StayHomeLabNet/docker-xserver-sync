import io
import stat
from dataclasses import dataclass

from dxsync.remote import FingerprintPolicy, HostKeyMismatchError, SftpScanner, host_key_sha256


@dataclass
class Attr:
    filename: str
    st_mode: int
    st_size: int = 0
    st_mtime: int = 1


class FakeSftp:
    def __init__(self) -> None:
        self.directories = {
            "/home/user/site": [
                Attr("index.php", stat.S_IFREG | 0o644, 5),
                Attr("uploads", stat.S_IFDIR | 0o755),
            ],
            "/home/user/site/uploads": [Attr("photo.jpg", stat.S_IFREG | 0o644, 3)],
        }
        self.files = {
            "/home/user/site/index.php": b"hello",
            "/home/user/site/uploads/photo.jpg": b"img",
        }

    def listdir_attr(self, path: str):
        return self.directories[path]

    def open(self, path: str, mode: str = "r"):
        return io.BytesIO(self.files[path])


class FakeKey:
    def asbytes(self) -> bytes:
        return b"server-key"

    def get_name(self) -> str:
        return "ssh-ed25519"


class FakeHostKeys:
    def add(self, *args) -> None:
        self.added = args


class FakeClient:
    def __init__(self) -> None:
        self.keys = FakeHostKeys()

    def get_host_keys(self):
        return self.keys


def test_sftp_scanner_is_read_only_and_hashes_files() -> None:
    result = SftpScanner(("uploads/",)).scan(FakeSftp(), "/home/user/site")
    assert [item.relative_path for item in result.files] == ["index.php"]
    assert result.files[0].sha256
    assert result.excluded_count == 1


def test_sftp_scanner_skips_protected_directories_without_reading_contents() -> None:
    result = SftpScanner(skip_patterns=("uploads/",)).scan(FakeSftp(), "/home/user/site")
    assert [item.relative_path for item in result.files] == ["index.php"]
    assert result.skipped_protected_paths == ("uploads",)


def test_fingerprint_policy_accepts_only_exact_sha256() -> None:
    key = FakeKey()
    expected = host_key_sha256(key.asbytes())
    client = FakeClient()
    FingerprintPolicy(expected).missing_host_key(client, "example", key)
    assert client.keys.added[0] == "example"

    try:
        FingerprintPolicy("SHA256:wrong").missing_host_key(client, "example", key)
    except HostKeyMismatchError:
        pass
    else:
        raise AssertionError("A mismatched host key was accepted")
