from __future__ import annotations

from threading import Event


class OperationCancelled(RuntimeError):
    """Raised at a safe checkpoint after the user requests cancellation."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled("ユーザーの指示により安全な区切りで停止しました")
