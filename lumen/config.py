"""
LUMEN — конфигурация платформы.

Новый формат конфигурации:  config/lumen.json
При первом запуске автоматически:
  1) создаёт config/lumen.json со значениями по умолчанию;
  2) переносит API-ключи из legacy config/api_keys.json (gemini_api_key);
  3) импортирует семантическую память из legacy memory/long_term.json
     (как seed-факты) — данные пользователя не теряются.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ── Пути платформы ────────────────────────────────────────────────────────────

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
DATA_DIR = BASE_DIR / "lumen_data"
CONFIG_PATH = BASE_DIR / "config" / "lumen.json"
LEGACY_KEYS_PATH = BASE_DIR / "config" / "api_keys.json"
LEGACY_MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"

MEMORY_PATH = DATA_DIR / "memory.json"
SESSIONS_DIR = DATA_DIR / "sessions"
LEARNING_DIR = DATA_DIR / "learning"
LOG_DIR = DATA_DIR / "logs"


def default_config() -> Dict[str, Any]:
    """Значения по умолчанию новой конфигурации LUMEN."""
    return {
        "brand": {"name": "LUMEN", "model": "LUMEN-1", "version": "1.0.0"},
        "language": "ru",
        "backend": {
            # heuristic — встроенный модуль локального рассуждения LUMEN LHC
            # gemini    — Google Gemini (нужен API-ключ)
            # openai    — любой OpenAI-совместимый сервер (Ollama, LM Studio…)
            "provider": "heuristic",
            "model": "",
            "api_key": "",
            "base_url": "http://localhost:11434/v1",
            "temperature": 0.7,
            "max_tokens": 1024,
        },
        "persona": {
            "name": "LUMEN",
            "style": "balanced",  # brief | balanced | creative
        },
        "context": {
            "max_turns": 12,       # сколько последних реплик нести в контекст
            "max_facts": 6,        # сколько семантических фактов в промпт
            "token_budget": 6000,  # ориентировочный бюджет токенов контекста
        },
        "memory": {
            "path": str(MEMORY_PATH),
            "max_facts": 400,
        },
        "safety": {
            "max_input_length": 8000,
            "block_injection": True,
        },
        "tools": {
            "timeout_sec": 20,
            "enable_legacy_bridge": True,
        },
        "voice": {
            "enabled": False,
            "tts_engine": "edge",
            "stt_engine": "auto",
        },
        "ui": {"theme": "dark"},
        "api": {
            "auth_token": "",      # если задан — все /api/* требуют Bearer
            "host": "0.0.0.0",
            "port": 8090,
        },
    }


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate_legacy_keys(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Перенос gemini-ключа из legacy-конфига (один раз)."""
    legacy = _read_json(LEGACY_KEYS_PATH)
    key = legacy.get("gemini_api_key", "")
    if key and not cfg["backend"].get("api_key"):
        cfg["backend"]["api_key"] = key
        cfg.setdefault("_migration", {})["legacy_keys_imported"] = True
    return cfg


class LumenConfig:
    """Живая конфигурация: чтение/запись JSON, атомарная запись, события."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self._dirty = False
        self.load()

    # ── загрузка / сохранение ────────────────────────────────────────────────
    def load(self) -> "LumenConfig":
        data = _read_json(self.path)
        cfg = _deep_merge(default_config(), data)
        cfg = _migrate_legacy_keys(cfg)
        self._data = cfg
        if not self.path.exists():
            self.save()
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        self._dirty = False

    # ── доступ ───────────────────────────────────────────────────────────────
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, save: bool = True) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        self._dirty = True
        if save:
            self.save()

    def update(self, mapping: Dict[str, Any]) -> None:
        """Рекурсивно накладывается поверх текущей конфигурации."""
        self._data = _deep_merge(self._data, mapping)
        self.save()

    def public(self) -> Dict[str, Any]:
        """Версия для API: без сырых ключей."""
        out = copy.deepcopy(self._data)
        key = out.get("backend", {}).get("api_key", "")
        if key:
            out["backend"]["api_key"] = key[:4] + "••••••••" + key[-4:]
        return out

    def reload(self) -> None:
        self.load()

    # ── служебное ────────────────────────────────────────────────────────────
    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    @property
    def start_time(self) -> float:
        return getattr(self, "_start", time.time())
