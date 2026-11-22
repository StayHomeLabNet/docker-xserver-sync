from __future__ import annotations

from dxsync.progress_tracker import (
    OperationProgress,
    _ThrottledProgressEmitter,
    format_progress_amount,
    format_progress_duration,
    format_size,
    format_worker_error,
)
from dxsync.service import DryRunProgress


def test_progress_formatting_helpers() -> None:
    assert format_size(None) == "—"
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KiB"
    assert format_size(5 * 1024 * 1024) == "5.0 MiB"

    assert format_progress_duration(-3) == "0秒"
    assert format_progress_duration(45) == "45秒"
    assert format_progress_duration(120) == "2分"
    assert format_progress_duration(125) == "2分5秒"
    assert format_progress_duration(3600) == "1時間"
    assert format_progress_duration(3660) == "1時間1分"

    bytes_event = OperationProgress(
        stage=1,
        total_stages=1,
        message="upload",
        current=2 * 1024 * 1024,
        total=4 * 1024 * 1024,
        unit="bytes",
    )
    assert format_progress_amount(bytes_event) == "2.0/4.0 MiB"

    file_event = OperationProgress(
        stage=1,
        total_stages=1,
        message="scan",
        current=1234,
        total=2000,
        unit="files",
    )
    assert format_progress_amount(file_event) == "1,234/2,000"


def test_format_worker_error_and_throttled_emitter() -> None:
    assert "SFTP Failure" in format_worker_error(RuntimeError("Failure"))
    assert "SFTP Failure" in format_worker_error(RuntimeError(""))
    assert format_worker_error(RuntimeError("custom boom")) == "custom boom"

    emitted: list[object] = []
    emitter = _ThrottledProgressEmitter(emitted.append, min_interval=60.0)
    emitter(DryRunProgress(2, 4, "scan", current=0, total=10, unit="files"))
    emitter(DryRunProgress(2, 4, "scan", current=5, total=10, unit="files"))
    emitter(DryRunProgress(2, 4, "scan", current=10, total=10, unit="files"))
    # Boundary events (0 and total) always emit immediately; middle event is throttled.
    assert [item.current for item in emitted] == [0, 10]
