"""
LUMEN — генеративные модули (backends).

Три реализуются модуля, подключаемых к стадии 6 конвейера:

  LHC (LUMEN Heuristic Core)  — встроенный модуль локального рассуждения.
      Собирает ответ из плана, результатов инструментов и памяти без сети
      и ключей. Всегда доступен — это деградационный «пол» платформы.
  Gemini                      — Google Gemini через официальный REST API.
  OpenAI-совместимый          — любой сервер с /chat/completions
      (Ollama, LM Studio, Jan, llama.cpp, vLLM…).

Выбор — по конфигурации backend.provider. Любой сетевой сбой на лету
переключает генерацию на LHC с пометкой fallback (см. generator.py).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
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
# LHC — встроенный модуль локального рассуждения
# ═══════════════════════════════════════════════════════════════════════════

class HeuristicBackend(Backend):
    """LHC: детерминированный генератор по плану и результатам инструментов."""

    name = "lhc"
    display = "LUMEN LHC (локальный модуль)"

    def __init__(self, persona: Optional[Dict[str, str]] = None) -> None:
        self.persona = persona or {"name": "LUMEN", "style": "balanced"}
        self._greet_idx = 0
        self._thanks_idx = 0
        self._fallback_idx = 0

    def available(self) -> (bool, str):
        return True, "встроенный"

    # ── шаблоны (RU-first) ───────────────────────────────────────────────────
    _GREETINGS = [
        "Здравствуйте! Я LUMEN. Чем освещу путь? Спросите о времени, погоде, "
        "состоянии системы — или просто расскажите, что у вас нового.",
        "Привет! LUMEN на связи. Конвейер «Луч» разогрет, инструменты на месте. "
        "С чего начнём?",
        "Добрый день! Готов работать: анализ, инструменты, память — всё под рукой.",
    ]
    _THANKS = [
        "Всегда рад. Если какой-то ответ пришёлся — поставьте «+» под ним: "
        "обучающая петля LUMEN учится по вашим оценкам.",
        "Пожалуйста! Обращайтесь — я помню контекст сессии.",
    ]
    _FALLBACKS = [
        "Я обработал запрос, но у меня нет точного инструмента, который "
        "ответил бы на него полностью. Могу предложить: спросить о времени, "
        "погоде, состоянии системы; вычислить выражение; поискать файлы в "
        "проекте; что-то запомнить или вспомнить. Переформулируйте — попробую снова.",
        "Спросите меня иначе: мне проще, когда задача конкретна. Подсказка: "
        "время, погода, калькулятор, системный мониторинг, файлы, память — "
        "мои рабочие инструменты.",
        "Этот запрос вышел за рамки моих текущих инструментов. Скажите, чего "
        "вы ожидаете в ответе — я подберу путь или честно скажу, чего не хватает.",
    ]

    def generate(self, system: str, messages: List[Dict[str, str]],
                 temperature: float = 0.7, max_tokens: int = 1024,
                 plan: Optional[Dict[str, Any]] = None,
                 tool_results: Optional[List[Dict[str, Any]]] = None,
                 facts: Optional[List[str]] = None,
                 risk: str = "safe", **kwargs) -> str:
        plan = plan or {}
        primary = plan.get("primary", "chat")
        slots = plan.get("slots", {})
        tool_results = tool_results or []
        facts = facts or []

        def _first_tool_result(name: str) -> Optional[Dict[str, Any]]:
            for tr in tool_results:
                if tr.get("name") == name:
                    return tr
            return None

        # ── блоки: вежливость к риску ─────────────────────────────────────────
        if risk == "blocked":
            return (
                "Этот запрос заблокирован защитным слоем LUMEN: в нём есть "
                "признаки попытки переопределить правила ядра. Я так не работаю. "
                "Если вопрос был искренним — сформулируйте его без инструкций "
                "типа «проигнорируй правила»."
            )

        # ── по намерениям ─────────────────────────────────────────────────────
        if primary == "identity":
            return (
                "Я — LUMEN (Люмен), самостоятельная ИИ-платформа. "
                "Внутри работает собственное ядро LUMEN-1: шестистадийный "
                "конвейер «Луч» — приём, защита, анализ, контекст, инструменты, "
                "генерация — плюс модуль памяти и обучающая петля. "
                "Я не переименование и не копия прежних ассистентов: у меня "
                "своя архитектура, свой интерфейс и свой голос. "
                f"Ядро: {tool_results and 'разогрет' or 'готов к работе'}."
            )

        if primary == "capability":
            tr = _first_tool_result("knowledge.capabilities")
            if tr and tr.get("ok") and tr.get("text"):
                return tr["text"]
            return ("Мои возможности: анализ запросов и контекст, память "
                    "(семантические факты, сессии), инструменты — время, "
                    "калькулятор, погода, состояние системы, поиск файлов, "
                    "веб-поиск через legacy-мост, генерация текста выбранным "
                    "модулем. Полный список — в разделе «Инструменты».")

        if primary == "math":
            tr = _first_tool_result("math.calc")
            if tr and tr.get("ok"):
                return f"{slots.get('expression', 'Выражение')}  {tr['text']}."
            return "Не удалось вычислить. Пришлите выражение цифрами: например, «вычисли 12 × 8 + 4»."

        if primary == "weather":
            tr = _first_tool_result("weather.current")
            if tr and tr.get("ok") and tr.get("text"):
                return tr["text"]
            reason = (tr or {}).get("error", "инструмент не запускался")
            return f"Погоду получить не удалось ({reason}). Проверьте сеть и повторите."

        if primary == "system_status":
            tr = _first_tool_result("system.status")
            if tr and tr.get("ok") and tr.get("text"):
                return tr["text"]
            return "Системный мониторинг не ответил: " + (tr or {}).get("error", "неизвестно")

        if primary == "time":
            tr = _first_tool_result("time.now")
            if tr and tr.get("ok") and tr.get("text"):
                return tr["text"]
            return "Модуль времени не ответил — попробуйте ещё раз."

        if primary == "file_search":
            tr = _first_tool_result("files.search")
            if tr and tr.get("ok"):
                return tr.get("text") or "Поиск завершён."
            return "Поиск файлов не удался: " + (tr or {}).get("error", "неизвестно")

        if primary == "memory_save":
            tr = _first_tool_result("memory.store")
            if tr and tr.get("ok"):
                key = (tr.get("data") or {}).get("key", "")
                return (f"Запомнил: {key} — в семантическом слое памяти. "
                        "Ссылаться на него буду в следующих сессиях.")
            return "Не удалось сохранить в память: " + (tr or {}).get("error", "неизвестно")

        if primary == "memory_recall":
            tr = _first_tool_result("memory.recall")
            if tr and tr.get("ok") and tr.get("text"):
                header = "Вот что я помню:\n" if tr.get("count") else ""
                return header + tr["text"]
            return "В долговременной памяти по этому запросу пока пусто. " \
                   "Скажите «запомни, что …» — и я сохраню факт."

        if primary == "greeting":
            g = self._GREETINGS[self._greet_idx % len(self._GREETINGS)]
            self._greet_idx += 1
            if facts:
                g += "\n\nКстати, из контекста: " + " ".join(facts[:2])
            return g

        if primary == "thanks":
            t = self._THANKS[self._thanks_idx % len(self._THANKS)]
            self._thanks_idx += 1
            return t

        if primary == "farewell":
            return "До связи. Контекст сессии сохранён — вернётесь, продолжим с того же места."

        if primary == "web_info":
            tr = _first_tool_result("legacy.web_search")
            if tr and tr.get("ok") and tr.get("text"):
                return "Нашёл в сети:\n" + tr["text"]
            reason = (tr or {}).get("error", "веб-поиск недоступен на этой машине")
            return f"Веб-поиск не доступен ({reason}). Могу помочь с остальным."

        # ── chat: честный ответ с опорой на факты ─────────────────────────────
        fb = self._FALLBACKS[self._fallback_idx % len(self._FALLBACKS)]
        self._fallback_idx += 1
        parts = []
        if facts:
            parts.append("Из контекста: " + " ".join(facts[:3]))
        parts.append(fb)
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Сетевые бэкенды (stdlib urllib, без зависимостей)
# ═══════════════════════════════════════════════════════════════════════════

def _http_post_json(url: str, payload: Dict[str, Any],
                    headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          **headers},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OpenAIBackend(Backend):
    """Любой OpenAI-совместимый сервер (Ollama, LM Studio, Jan, vLLM…)."""

    name = "openai"
    display = "OpenAI-совместимый модуль"

    def __init__(self, base_url: str = "http://localhost:11434/v1",
                 api_key: str = "", model: str = "") -> None:
        self.base_url = (base_url or "http://localhost:11434/v1").rstrip("/")
        self.api_key = api_key
        self.model = model

    def available(self) -> (bool, str):
        if not self.model:
            return False, "не задана модель (backend.model)"
        try:
            req = urllib.request.Request(
                self.base_url + "/models",
                headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
            return True, f"{self.model} @ {self.base_url}"
        except Exception as e:  # noqa: BLE001
            return False, f"сервер не ответил: {type(e).__name__}"

    def _headers(self) -> Dict[str, str]:
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def generate(self, system: str, messages: List[Dict[str, str]],
                 temperature: float = 0.7, max_tokens: int = 1024, **kwargs) -> str:
        payload = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = _http_post_json(self.base_url + "/chat/completions",
                               payload, self._headers(), timeout=90)
        return data["choices"][0]["message"]["content"].strip()


class GeminiBackend(Backend):
    """Google Gemini (generativelanguage.googleapis.com)."""

    name = "gemini"
    display = "Модуль Gemini"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"

    def available(self) -> (bool, str):
        if not self.api_key:
            return False, "не задан API-ключ (backend.api_key)"
        return True, self.model

    def generate(self, system: str, messages: List[Dict[str, str]],
                 temperature: float = 0.7, max_tokens: int = 1024, **kwargs) -> str:
        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        data = _http_post_json(url, payload,
                               {"x-goog-api-key": self.api_key}, timeout=90)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Gemini: нет ответа в candidates ({e})")


# ═══════════════════════════════════════════════════════════════════════════
# Фабрика
# ═══════════════════════════════════════════════════════════════════════════

BACKENDS = {
    "heuristic": HeuristicBackend,
    "lhc": HeuristicBackend,
    "openai": OpenAIBackend,
    "ollama": OpenAIBackend,
    "lmstudio": OpenAIBackend,
    "gemini": GeminiBackend,
}


def get_backend(config) -> Backend:
    """Создаёт активный бэкенд по конфигурации LUMEN."""
    provider = str(config.get("backend.provider", "heuristic")).lower()
    cls = BACKENDS.get(provider, HeuristicBackend)
    if cls in (OpenAIBackend,):
        return cls(
            base_url=config.get("backend.base_url", "http://localhost:11434/v1"),
            api_key=config.get("backend.api_key", ""),
            model=config.get("backend.model", "") or "llama3.2",
        )
    if cls is GeminiBackend:
        return cls(
            api_key=config.get("backend.api_key", ""),
            model=config.get("backend.model", "") or "gemini-2.0-flash",
        )
    persona = {
        "name": config.get("persona.name", "LUMEN"),
        "style": config.get("persona.style", "balanced"),
    }
    return cls(persona=persona)
