"""
LUMEN — реестр инструментов (ToolRegistry).

Стадия 5 конвейера «Луч». Инструмент — это функция с декларативной
спецификацией (ToolSpec): имя, описание, параметры, таймаут.

Гарантии реестра:
  • декларативная схема — UI и LLM-бэкенды читают список как есть;
  • изоляция ошибок — сбой инструмента не роняет конвейер;
  • таймаут — исполнение в отдельном потоке с ограничением по времени;
  • честная доступность — инструмент, чьи зависимости не установлены,
    помечается unavailable с причиной (никогда не «тупо падает»).

Расширение:
    from lumen.kernel import ToolSpec
    registry.register(ToolSpec(...))
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ToolParam:
    name: str
    type: str = "string"          # string | number | integer | boolean | array
    description: str = ""
    required: bool = False
    default: Any = None


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    handler: Callable[..., "ToolResult"]
    category: str = "general"
    params: List[ToolParam] = field(default_factory=list)
    timeout: float = 15.0
    availability: Optional[Callable[[], Any]] = None   # () -> True | (ok, reason)
    icon: str = "⚙"
    source: str = "builtin"      # builtin | legacy | user

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "source": self.source,
            "params": [p.__dict__ for p in self.params],
            "timeout": self.timeout,
        }


@dataclass
class ToolResult:
    ok: bool
    text: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "text": self.text, "data": self.data,
            "error": self.error, "ms": self.ms,
        }


def _run_with_timeout(func: Callable[..., Any], timeout: float) -> Tuple[Any, Optional[str]]:
    """Потоковый таймаут (кроссплатформенный, без signal)."""
    result: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = func()
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, f"таймаут: инструмент не ответил за {timeout:g} c"
    if "error" in result:
        return None, result["error"]
    return result.get("value"), None


@dataclass
class _Running:
    name: str
    ok: bool
    ms: int


class ToolRegistry:
    def __init__(self, default_timeout: float = 15.0) -> None:
        self._tools: Dict[str, ToolSpec] = {}
        self._lock = threading.Lock()
        self.default_timeout = default_timeout
        self.recent_calls: List[_Running] = []   # для отладки/UI

    # ── регистрация ──────────────────────────────────────────────────────────
    def register(self, spec: ToolSpec) -> ToolSpec:
        with self._lock:
            self._tools[spec.name] = spec
        return spec

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Optional[ToolSpec]:
        with self._lock:
            return self._tools.get(name)

    # ── список с честной доступностью ────────────────────────────────────────
    def list(self, only_available: bool = False) -> List[Dict[str, Any]]:
        out = []
        with self._lock:
            specs = list(self._tools.values())
        for spec in sorted(specs, key=lambda s: (s.category, s.name)):
            ok, reason = self._probe(spec)
            if only_available and not ok:
                continue
            entry = spec.schema()
            entry["available"] = ok
            entry["availability_reason"] = reason
            out.append(entry)
        return out

    def available_names(self) -> List[str]:
        return [t["name"] for t in self.list() if t["available"]]

    def _probe(self, spec: ToolSpec) -> (bool, str):
        if spec.availability is None:
            return True, ""
        try:
            res = spec.availability()
            if res is True:
                return True, ""
            if res is False:
                return False, "недоступен"
            if isinstance(res, (list, tuple)) and len(res) == 2:
                ok, reason = res
                return bool(ok), str(reason)
            return bool(res), ""
        except Exception as e:  # noqa: BLE001
            return False, f"ошибка проверки: {e}"

    # ── исполнение ──────────────────────────────────────────────────────────
    def run(self, name: str, args: Optional[Dict[str, Any]] = None,
            timeout: Optional[float] = None) -> ToolResult:
        args = args or {}
        spec = self.get(name)
        if spec is None:
            return ToolResult(False, error=f"инструмент «{name}» не зарегистрирован")

        ok, reason = self._probe(spec)
        if not ok:
            return ToolResult(False, error=f"недоступен: {reason}")

        # нормализация аргументов по схеме
        bound: Dict[str, Any] = {}
        for p in spec.params:
            if p.name in args and args[p.name] not in (None, ""):
                bound[p.name] = self._coerce(p, args[p.name])
            elif p.required:
                return ToolResult(False,
                                  error=f"не задан обязательный параметр «{p.name}»")
            elif p.default is not None:
                bound[p.name] = p.default

        t0 = time.time()
        try:
            value, err = _run_with_timeout(
                lambda: spec.handler(**bound),
                timeout or spec.timeout or self.default_timeout,
            )
            ms = int((time.time() - t0) * 1000)
            if err is not None:
                self._log_call(name, False, ms)
                return ToolResult(False, error=err, ms=ms)
            if isinstance(value, ToolResult):
                value.ms = ms
                self._log_call(name, value.ok, ms)
                return value
            if isinstance(value, dict):
                ok = bool(value.pop("ok", True))
                text = str(value.pop("text", ""))
                self._log_call(name, ok, ms)
                return ToolResult(ok, text=text, data=value, ms=ms,
                                  error="" if ok else "инструмент вернул ошибку")
            self._log_call(name, True, ms)
            return ToolResult(True, text=str(value), data={"result": value}, ms=ms)
        except Exception as e:  # noqa: BLE001
            ms = int((time.time() - t0) * 1000)
            self._log_call(name, False, ms)
            return ToolResult(False, error=f"{type(e).__name__}: {e}", ms=ms)

    def _log_call(self, name: str, ok: bool, ms: int) -> None:
        self.recent_calls.append(_Running(name, ok, ms))
        if len(self.recent_calls) > 50:
            self.recent_calls.pop(0)

    @staticmethod
    def _coerce(p: ToolParam, value: Any) -> Any:
        try:
            if p.type in ("number", "integer"):
                return int(value) if p.type == "integer" else float(value)
            if p.type == "boolean":
                if isinstance(value, str):
                    return value.lower() in ("1", "true", "yes", "да")
                return bool(value)
            if p.type == "array":
                if isinstance(value, str):
                    return json.loads(value) if value.strip().startswith("[") else [value]
                return value
            return str(value)
        except Exception:
            return value
