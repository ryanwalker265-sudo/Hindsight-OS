"""Tiny in-process event bus used for Server-Sent Events."""
from __future__ import annotations

import queue
import threading


class EventBus:
    def __init__(self, backlog: int = 256):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._backlog = backlog

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._backlog)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def count(self) -> int:
        """How many live event streams are open (the shell keeps one)."""
        with self._lock:
            return len(self._subs)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A stuck client shouldn't block the system; drop it.
                self.unsubscribe(q)
