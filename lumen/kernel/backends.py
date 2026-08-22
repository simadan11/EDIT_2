"""
LUMEN — генеративные модули (backends).

Основной генеративный модуль платформы — LUMEN Core: собственный
локальный ИИ (lumen/kernel/brain.py + lumen/knowledge) — офлайн,
без ключей, всегда доступен. Он и есть «мозг» платформы.

Внешние генеративные модули — опциональные надстройки:

  Gemini                      — Google Gemini через официальный REST API.
  OpenAI-совместимый          — любой сервер с /chat/completions
      (Ollama, LM Studio, Jan, llama.cpp, vLLM…).

Гибридный режим (overflow): если в настройках задан
`backend.overflow_provider`, LUMEN Core обрабатывает все структурированные
запросы (знания, вычисления, инструменты, диалог), а свободный
творческий текст — за которым ядро честно признаёт, что «без внешнего
модуля не закрыться» — отдаёт подключённому внешнему модулю.

ЛЮБОЙ сетевой сбой переключает генерацию на LUMEN Core
(см. engine.py) — деградация всегда вниз, к собственному модулю.
"""

from __future__ import annotations

import json
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
# LUMEN Core — собственный ИИ-модуль (ОСНОВНОЙ)
# ═══════════════════════════════════════════════════════════════════════════

class LumenCoreBackend(Backend):
    """Основной генеративный модуль: собственный ИИ LUMEN Core.

    Вся логика рассуждения — в lumen.kernel.brain.LumenCore:
    база знаний, диалоговое состояние, календарная математика,
    текстовая статистика, честный фолбэк. Сеть не нужна.
    """

    name = "lumen_core"
    display = "LUMEN Core — собственный ИИ (офлайн)"

    def __init__(self, persona: Optional[Dict[str, str]] = None,
                 overflow: Optional["Backend"] = None) -> None:
        self.persona = persona or {"name": "LUMEN", "style": "balanced"}
        # опциональный внешний модуль для свободного текста (overflow)
        self.overflow = overflow

    def available(self) -> (bool, str):
        return True, "собственный модуль, офлайн"

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

        answer, needs_ext = core.quick_answer(
            text, context=facts,
            plan=plan, tool_results=tool_results or [], risk=risk)

        # ── overflow: свободный текст → внешний модуль (если подключён) ────
        if needs_ext and self.overflow is not None:
            try:
                ok, reason = self.overflow.available()
                if ok:
                    ext = self.overflow.generate(
                        system, messages, temperature=temperature,
                        max_tokens=max_tokens)
                    if ext:
                        return (f"_{ext.strip()}_\n"
                                f"— свободный текст сгенерировал внешний "
                                f"модуль «{self.overflow.display}»; "
                                f"структурированные запросы — LUMEN Core.")
            except Exception:  # noqa: BLE001 — деградация к ядру
                answer += ("\n\n_Внешний модуль для свободного текста не "
                           "ответил — ответ дал LUMEN Core._")
        return answer


# ── устаревшее имя (совместимость с конфигами и импортами) ─────────────────
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
# Сетевые бэкенды — опциональные внешние модули (stdlib urllib)
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
    display = "Внешний модуль: OpenAI-совместимый"

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
    """Google Gemini (generativelanguage.googleapis.com) — внешний модуль."""

    name = "gemini"
    display = "Внешний модуль: Gemini (Google)"

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
    "lumen_core": LumenCoreBackend,
    "core": LumenCoreBackend,
    "own": LumenCoreBackend,
    "heuristic": HeuristicBackend,   # устаревшее имя — тот же LUMEN Core
    "lhc": HeuristicBackend,
    "openai": OpenAIBackend,
    "ollama": OpenAIBackend,
    "lmstudio": OpenAIBackend,
    "gemini": GeminiBackend,
}

_CORE_NAMES = {"lumen_core", "core", "own", "heuristic", "lhc"}


def _build_network_backend(config, ext: str) -> Optional[Backend]:
    """Собирает внешний модуль (для overflow или legacy-режима)."""
    if ext == "gemini":
        key = str(config.get("backend.api_key", "") or "")
        if not key:
            return None
        return GeminiBackend(api_key=key,
                             model=config.get("backend.model", "") or "gemini-2.0-flash")
    if ext in ("openai", "ollama", "lmstudio"):
        return OpenAIBackend(
            base_url=config.get("backend.base_url", "http://localhost:11434/v1"),
            api_key=str(config.get("backend.api_key", "") or ""),
            model=config.get("backend.model", "") or "llama3.2",
        )
    return None


def get_backend(config) -> Backend:
    """Создаёт активный генеративный модуль по конфигурации LUMEN.

    По умолчанию — LUMEN Core (собственный ИИ). Внешний модуль
    подключается либо как основной (legacy: provider=gemini/openai),
    либо как опциональный overflow для свободного текста.
    """
    provider = str(config.get("backend.provider", "lumen_core")).lower()

    # ── основной режим: LUMEN Core (собственный) ───────────────────────────
    if provider in _CORE_NAMES:
        persona = {
            "name": config.get("persona.name", "LUMEN"),
            "style": config.get("persona.style", "balanced"),
            "language": config.get("language", "ru"),
        }
        if provider in ("heuristic", "lhc"):
            backend: Backend = HeuristicBackend(persona=persona)
        else:
            backend = LumenCoreBackend(persona=persona)
        # опциональный внешний overflow
        ext = str(config.get("backend.overflow_provider", "") or "").lower()
        if ext and ext not in _CORE_NAMES:
            net = _build_network_backend(config, ext)
            if net is not None:
                backend.overflow = net
        return backend

    # ── legacy-режим: сетевой модуль как основной (деградация — к Core) ───
    cls = BACKENDS.get(provider)
    if cls is None:
        return LumenCoreBackend(persona={"name": "LUMEN", "style": "balanced"})
    if cls is OpenAIBackend:
        return cls(
            base_url=config.get("backend.base_url", "http://localhost:11434/v1"),
            api_key=str(config.get("backend.api_key", "") or ""),
            model=config.get("backend.model", "") or "llama3.2",
        )
    if cls is GeminiBackend:
        return cls(
            api_key=str(config.get("backend.api_key", "") or ""),
            model=config.get("backend.model", "") or "gemini-2.0-flash",
        )
    return cls()
