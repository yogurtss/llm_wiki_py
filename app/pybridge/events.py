from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class EventMessage:
    event: str
    payload: Any

    def to_sse(self) -> bytes:
        body = json.dumps({"event": self.event, "payload": self.payload}, ensure_ascii=False)
        return f"data: {body}\n\n".encode("utf-8")


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue[EventMessage]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[EventMessage]:
        subscriber: queue.Queue[EventMessage] = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[EventMessage]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def emit(self, event: str, payload: Any) -> None:
        message = EventMessage(event=event, payload=payload)
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(message)
