"""
LUMEN — legacy-мост (LegacyToolBridge).

Старые модули actions/* из исходной кодовой базы не переписываются, а
подключаются к новому ядру как «наследственные инструменты» legacy.*.
Каждый адаптер:
  • лениво импортирует модуль (тяжёлые зависимости не грузятся заранее);
  • честно сообщает недоступность с причиной, если зависимости нет;
  • оборачивает вызов в ToolResult — любой сбой остаётся внутри моста.

Так платформа расширяется без переписывания: любой модуль со знакомым
интерфейсом можно объявить адаптером в один приём.
"""

from __future__ import annotations

import importlib
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..kernel.tools import ToolResult, ToolSpec, ToolParam


def _bind_args(func: Callable, args: Dict[str, Any]) -> Dict[str, Any]:
    """Привязывает только те аргументы, которые принимает функция."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {}
    bound = {}
    for name, p in sig.parameters.items():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
            if name in args and args[name] not in (None, ""):
                bound[name] = args[name]
    return bound


class LegacyToolBridge:
    """Адаптеры legacy-модулей actions/* в реестр LUMEN."""

    #: (legacy-модуль, имя функции, название инструмента, описание, параметры)
    ADAPTERS: List[Tuple[str, str, str, str, List[ToolParam]]] = [
        ("actions.system_monitor", "get_system_status", "legacy.system_monitor",
         "Детальный мониторинг системы (legacy-модуль): CPU, RAM, GPU, процессы.",
         []),
        ("actions.web_search", "web_search", "legacy.web_search",
         "Веб-поиск (legacy-модуль) через DuckDuckGo/новостные источники.",
         [ToolParam("query", "string", "Запрос поиска", True)]),
        ("actions.open_app", "open_app", "legacy.open_app",
         "Открыть приложение на этой машине (legacy-модуль).",
         [ToolParam("app", "string", "Название приложения", True)]),
        ("actions.file_controller", "file_controller", "legacy.file_controller",
         "Управление файлами: создать/переместить/удалить (legacy-модуль).",
         [ToolParam("command", "string", "Команда для file_controller", True)]),
    ]

    def __init__(self, base_dir) -> None:
        self.base_dir = base_dir

    def specs(self) -> List[ToolSpec]:
        out = []
        for module, func_name, tool_name, description, params in self.ADAPTERS:
            out.append(self._make_spec(module, func_name, tool_name, description, params))
        return out

    def _make_spec(self, module: str, func_name: str, tool_name: str,
                   description: str, params: List[ToolParam]) -> ToolSpec:
        def _availability():
            try:
                mod = importlib.import_module(module)
                func = getattr(mod, func_name, None)
                if func is None or not callable(func):
                    return False, f"в модуле {module} нет вызова {func_name}()"
                return True, ""
            except Exception as e:  # noqa: BLE001
                return False, f"недоступен (зависимости: {type(e).__name__})"

        def _handler(**args: Any) -> ToolResult:
            t0 = time.time()
            try:
                mod = importlib.import_module(module)
                func = getattr(mod, func_name)
                bound = _bind_args(func, args)
                value = func(**bound)
                ms = int((time.time() - t0) * 1000)
                if isinstance(value, (dict,)):
                    text = value.get("text") or value.get("message") or ""
                    return ToolResult(True, text=str(text)[:2000],
                                      data=value if len(str(value)) < 4000 else {}, ms=ms)
                text = str(value)
                return ToolResult(True, text=text[:2000] or "Выполнено (без текста).",
                                  ms=ms)
            except Exception as e:  # noqa: BLE001
                ms = int((time.time() - t0) * 1000)
                return ToolResult(False,
                                  error=f"legacy {tool_name}: {type(e).__name__}: {e}",
                                  ms=ms)

        return ToolSpec(
            name=tool_name,
            title=tool_name.replace("legacy.", "Legacy: "),
            description=description,
            handler=_handler,
            category="legacy",
            params=params,
            timeout=25.0,
            availability=_availability,
            icon="🧩",
            source="legacy",
        )
