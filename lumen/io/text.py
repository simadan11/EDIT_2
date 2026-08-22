"""
LUMEN — текстовые утилиты ввода/вывода.

Очистка markdown для озвучки, компактные превью, язык запроса.
"""

from __future__ import annotations

import re
from typing import Optional

_MD_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*{1,2}([^*]+)\*{1,2}")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_LIST = re.compile(r"^\s*[-*•]\s+", re.M)
_MD_HEADING = re.compile(r"^\s*#{1,6}\s+", re.M)
_HTML = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_for_speech(text: str) -> str:
    """Текст без markdown/HTML — то, что произнесёт голосовой модуль."""
    t = _HTML.sub(" ", text or "")
    t = _MD_CODE_BLOCK.sub(" код ", t)
    t = _MD_INLINE_CODE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_LIST.sub("", t)
    t = _MD_HEADING.sub("", t)
    t = t.replace("\n", " ")
    t = t.replace("_", " ")
    return _WS.sub(" ", t).strip()


def preview(text: str, limit: int = 140) -> str:
    t = _WS.sub(" ", (text or "").strip())
    return t[:limit] + ("…" if len(t) > limit else "")


def detect_language(text: str) -> str:
    """Грубая детекция: ru | en | other (по доле кириллицы/латиницы)."""
    t = (text or "").lower()
    cyr = len(re.findall(r"[а-яё]", t))
    lat = len(re.findall(r"[a-z]", t))
    if cyr == 0 and lat == 0:
        return "other"
    if cyr / max(1, cyr + lat) >= 0.5:
        return "ru"
    if lat / max(1, cyr + lat) >= 0.5:
        return "en"
    return "other"


def fmt_bytes(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ПБ"
