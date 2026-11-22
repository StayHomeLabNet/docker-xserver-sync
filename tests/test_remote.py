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
        self.listdir_calls: list[str] = []
        self.open_calls: list[str] = []
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
        self.listdir_calls.append(path)
        return self.directories[path]

    def open(self, path: str, mode: str = "r"):
        self.open_calls.append(path)
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
    sftp = FakeSftp()
    result = SftpScanner(skip_patterns=("uploads/",)).scan(sftp, "/home/user/site")
    assert [item.relative_path for item in result.files] == ["index.php"]
    assert result.skipped_protected_paths == ("uploads",)
    assert sftp.listdir_calls == ["/home/user/site"]
    assert sftp.open_calls == ["/home/user/site/index.php"]


def test_sftp_scanner_does_not_open_an_exact_protected_file() -> None:
    sftp = FakeSftp()
    result = SftpScanner(skip_patterns=("index.php", "uploads/")).scan(
        sftp,
        "/home/user/site",
    )

    assert result.files == ()
    assert result.skipped_protected_paths == ("index.php", "uploads")
    assert sftp.listdir_calls == ["/home/user/site"]
    assert sftp.open_calls == []


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


def test_sftp_scanner_reports_progress_before_hashing_each_file() -> None:
    events: list[tuple[int, int | None, str]] = []
    SftpScanner().scan(
        FakeSftp(),
        "/home/user/site",
        progress_callback=lambda current, total, detail: events.append((current, total, detail)),
    )

    assert events[0] == (0, None, "")
    file_events = [event for event in events if event[1] is not None]
    assert file_events == [
        (0, 2, "index.php"),
        (1, 2, "index.php"),
        (1, 2, "uploads/photo.jpg"),
        (2, 2, "uploads/photo.jpg"),
    ]


def test_sftp_scanner_uses_ssh_batch_hashing_and_falls_back_on_missing() -> None:
    import hashlib

    sftp = FakeSftp()
    expected_index = hashlib.sha256(b"hello").hexdigest()
    exec_calls: list[bytes] = []

    def fake_ssh_exec(_cmd: str, stdin_bytes: bytes):
        exec_calls.append(stdin_bytes)
        # Return hash for index.php only; omit uploads/photo.jpg to verify SFTP fallback
        return 0, f"{expected_index}  /home/user/site/index.php\n", ""

    result = SftpScanner(ssh_exec=fake_ssh_exec).scan(sftp, "/home/user/site")

    assert len(exec_calls) == 1
    assert [f.relative_path for f in result.files] == ["index.php", "uploads/photo.jpg"]
    assert result.files[0].sha256 == expected_index
    assert result.files[1].sha256 == hashlib.sha256(b"img").hexdigest()
    assert sftp.open_calls == ["/home/user/site/uploads/photo.jpg"]

