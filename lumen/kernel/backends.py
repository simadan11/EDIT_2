"""
LUMEN — генеративный модуль платформы.

Единственный генеративный модуль — **LUMEN Core**: собственный
локальный ИИ (lumen/kernel/brain.py + lumen/knowledge). Он и есть
«мозг» платформы.

Принцип «полностью своё ИИ»:
  • внешних LLM-модулей (Gemini, OpenAI-совместимые) в платформе
    нет — ни как основного, ни как опционального overflow;
  • API-ключи и сеть для генерации не нужны вовсе;
  • генерация всегда локальная: база знаний, диалоговое состояние,
    вычисления, инструменты.

Сетевые запросы есть только у отдельных инструментов (погода —
wttr.in) — это данные, не ИИ.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Протокол бэкенда
# ═══════════════════════════════════════════════════════════════════════════

class Backend:
    name: str = "base"
    display: str = "Base"

    def available(self) -> (bool, str):
        return True, ""

    def generate(self, system: str, messages: List[Dict[str, str]],
                 temperature: float = 0.7, max_tokens: int = 1024,
                 **kwargs) -> str:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════
# LUMEN Core — собственный ИИ-модуль (ЕДИНСТВЕННЫЙ)
# ═══════════════════════════════════════════════════════════════════════════

class LumenCoreBackend(Backend):
    """Единственный генеративный модуль: собственный ИИ LUMEN Core.

    Вся логика рассуждения — в lumen.kernel.brain.LumenCore:
    база знаний, диалоговое состояние, календарная математика,
    текстовая статистика, честный фолбэк. Сеть и ключи не нужны.
    """

    name = "lumen_core"
    display = "LUMEN Core — собственный ИИ (офлайн, без API)"

    def __init__(self, persona: Optional[Dict[str, str]] = None) -> None:
        self.persona = persona or {"name": "LUMEN", "style": "balanced"}

    def available(self) -> (bool, str):
        return True, "собственный модуль, офлайн, без API"

    def generate(self, system: str, messages: List[Dict[str, str]],
                 temperature: float = 0.7, max_tokens: int = 1024,
                 plan: Optional[Dict[str, Any]] = None,
                 tool_results: Optional[List[Dict[str, Any]]] = None,
                 facts: Optional[List[str]] = None,
                 risk: str = "safe", **kwargs) -> str:
        from .brain import get_core  # локальный импорт: ядро подтягивает KB

        core = get_core(
            language=(self.persona or {}).get("language", "ru"),
            persona_name=(self.persona or {}).get("name", "LUMEN"))
        plan = plan or {}
        # текущий текст запроса: явно от конвейера (без префиксов контекста),
        # иначе — последняя user-реплика в messages
        text = str(kwargs.get("text") or "") or _last_user_text(messages)

        answer, _needs_ext = core.quick_answer(
            text, context=facts,
            plan=plan, tool_results=tool_results or [], risk=risk)
        # внешних модулей нет — сигнал overflow игнорируется:
        # ядро отвечает честно само
        return answer


# ── историческое имя (совместимость со старыми конфигами/импортами) ─────────
class HeuristicBackend(LumenCoreBackend):
    """LHC — историческое имя LUMEN Core. Полностью совместимо."""

    name = "lhc"
    display = "LUMEN Core (LHC, локальный режим)"


def _last_user_text(messages: List[Dict[str, str]]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user" and m.get("content"):
            return m["content"]
    if messages:
        return messages[-1].get("content", "") or ""
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# Фабрика
# ═══════════════════════════════════════════════════════════════════════════

BACKENDS = {
    "lumen_core": LumenCoreBackend,
    "core": LumenCoreBackend,
    "own": LumenCoreBackend,
    "heuristic": HeuristicBackend,   # устаревшее имя — тот же LUMEN Core
    "lhc": HeuristicBackend,
}

# provider-значения, которые старые конфиги могли задать для внешних
# модулей: все они теперь тоже дают LUMEN Core (внешних нет)
_LEGACY_NETWORK_NAMES = {"gemini", "openai", "ollama", "lmstudio"}


def get_backend(config) -> Backend:
    """Создаёт генеративный модуль. Всегда — LUMEN Core (свой ИИ).

    Любое значение `backend.provider` (включая старые gemini/openai)
    приводит к LUMEN Core: внешних модулей в платформе нет.
    """
    provider = str(config.get("backend.provider", "lumen_core")).lower()
    persona = {
        "name": config.get("persona.name", "LUMEN"),
        "style": config.get("persona.style", "balanced"),
        "language": config.get("language", "ru"),
    }
    if provider in ("heuristic", "lhc"):
        return HeuristicBackend(persona=persona)
    return LumenCoreBackend(persona=persona)
