from __future__ import annotations

import threading
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class Singleton(Generic[T]):
    """Lazy, thread-safe, create-once provider. Dependencies are other providers, resolved on first use."""

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._instance: T | None = None
        self._created = False

    def __call__(self) -> T:
        if not self._created:
            with self._lock:
                if not self._created:
                    self._instance = self._factory()
                    self._created = True
        return self._instance  # type: ignore[return-value]

    def reset(self) -> None:
        with self._lock:
            self._instance, self._created = None, False
