from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .laravel_setup import LaravelSetupProgress
from .service import DryRunProgress
from .wordpress_setup import WordPressSetupProgress


@dataclass(frozen=True)
class OperationProgress:
    stage: int
    total_stages: int
    message: str
    current: int | None = None
    total: int | None = None
    unit: str = ""
    detail: str = ""
    phase_key: tuple[object, ...] | None = None
    progress_label: str = ""
    summary_prefix: str = ""
    remote_wait: bool = True


class _ThrottledProgressEmitter:
    def __init__(
        self,
        emit: Callable[[object], None],
        *,
        min_interval: float = 0.05,
    ) -> None:
        self._emit = emit
        self._min_interval = min_interval
        self._last_emitted_at: float | None = None
        self._last_phase_key: tuple[object, ...] | None = None

    @staticmethod
    def _classify(event: object) -> tuple[tuple[object, ...] | None, bool]:
        if isinstance(event, DryRunProgress):
            key = (event.stage, event.message, event.unit, event.total)
            is_boundary = event.current is not None and (
                event.current == 0
                or (event.total is not None and event.current >= event.total)
            )
            return key, is_boundary
        if isinstance(event, OperationProgress):
            key = (
                event.phase_key
                if event.phase_key is not None
                else (event.stage, event.message, event.unit, event.total)
            )
            is_boundary = event.current is not None and (
                event.current == 0
                or (event.total is not None and event.current >= event.total)
            )
            return key, is_boundary
        if isinstance(event, (WordPressSetupProgress, LaravelSetupProgress)):
            key = (event.step, event.message, event.unit, event.total)
            is_boundary = event.current is not None and (
                event.current == 0
                or (event.total is not None and event.current >= event.total)
            )
            return key, is_boundary
        return None, True

    def __call__(self, event: object) -> None:
        now = time.monotonic()
        phase_key, is_boundary = self._classify(event)
        if (
            self._last_emitted_at is None
            or phase_key != self._last_phase_key
            or is_boundary
            or (now - self._last_emitted_at) >= self._min_interval
        ):
            self._last_emitted_at = now
            self._last_phase_key = phase_key
            self._emit(event)


def format_worker_error(exc: Exception) -> str:
    msg = str(exc).strip()
    if not msg or msg.lower() == "failure":
        return (
            f"SFTP/SSHファイル転送エラー ({type(exc).__name__}: SFTP Failure - "
            "サーバーまたはファイルアクセスでエラーが発生しました)"
        )
    return msg


def format_size(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def format_progress_duration(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    if whole_seconds < 60:
        return f"{whole_seconds}秒"
    minutes, remaining_seconds = divmod(whole_seconds, 60)
    if minutes < 60:
        return (
            f"{minutes}分{remaining_seconds}秒"
            if remaining_seconds
            else f"{minutes}分"
        )
    hours, remaining_minutes = divmod(minutes, 60)
    return (
        f"{hours}時間{remaining_minutes}分"
        if remaining_minutes
        else f"{hours}時間"
    )


def format_progress_amount(
    event: WordPressSetupProgress | LaravelSetupProgress | OperationProgress,
) -> str:
    assert event.current is not None and event.total is not None
    if event.unit == "bytes":
        mib = 1024 * 1024
        return f"{event.current / mib:.1f}/{event.total / mib:.1f} MiB"
    return f"{event.current:,}/{event.total:,}"
