"""
LUMEN — событийная шина.

Лёгкая pub/sub-шина для событий конвейера «Луч». Через неё UI (SSE),
логи и будущие модули (голос, мониторинг) подписываются на жизнь ядра,
не привязываясь к конкретным классам.
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Any, Callable, Dict, Iterable, List


class EventBus:
    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    def subscribe(self, event: str, handler: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.setdefault(event, []).append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                subs = self._subs.get(event, [])
                if handler in subs:
                    subs.remove(handler)

        return _unsubscribe

    def publish(self, event: str, **payload: Any) -> None:
        payload = dict(payload)
        payload.setdefault("seq", next(self._seq))
        with self._lock:
            subs = list(self._subs.get(event, []))
            subs.extend(self._subs.get("*", []))
        for handler in subs:
            try:
                handler(payload)
            except Exception:
                # подписчик не должен ронять конвейер
                pass

    def as_json(self, event: str, **payload: Any) -> str:
        return json.dumps({"event": event, **payload}, ensure_ascii=False)
