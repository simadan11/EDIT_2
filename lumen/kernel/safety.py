"""
LUMEN — защитный слой (Intake + ThreatGuard).

Стадии 1-2 конвейера «Луч»:
  1. Приём  — нормализация и проверка корректности запроса (пустота, длина,
     управляющие символы, спам-повторы).
  2. Защита — обнаружение prompt-injection и некорректных попыток
     переопределить поведение ядра; присвоение уровня риска.

Результат: IntakeResult(risk ∈ safe | caution | blocked, ...)
Заблокированный запрос не доходит до генератора — ядро отвечает
стандартной вежливой отсылкой (см. engine.py).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Tuple

MAX_INPUT_LENGTH_DEFAULT = 8000

# Повторы: «аааааааа…» / «!!! ???»
_REPEAT_RUN = re.compile(r"(.)\1{9,}")
_PUNCT_RUN = re.compile(r"([!?.…~]){8,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Паттерны инъекций (RU/EN) — попытки переписать правила ядра
_INJECTION_PATTERNS: List[Tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|prompts)", "injection:en"),
    (r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|prompts)", "injection:en"),
    (r"you\s+are\s+now\s+(a|an|the)\s+", "injection:en"),
    (r"new\s+(system\s+)?prompt\s*:", "injection:en"),
    (r"reveal\s+(your\s+)?(system\s+)?prompt", "injection:reveal"),
    (r"show\s+me\s+your\s+(system\s+)?prompt", "injection:reveal"),
    (r"print\s+(the\s+)?(system\s+)?prompt", "injection:reveal"),
    (r"проигнорируй\s+(все\s+)?(предыдущие|прошлые|выше)(\s+)(инструкции|правила|промпты)", "injection:ru"),
    (r"забудь\s+(все\s+)?(свои\s+)?(правила|инструкции)", "injection:ru"),
    (r"ты\s+теперь\s+(—|—\s*)(искусственный|бот|злой|враг)", "injection:ru"),
    (r"(новый|новое)\s+системный\s+промпт\s*:", "injection:ru"),
    (r"покажи\s+(мне\s+)?(свой\s+)?(системный\s+)?промпт", "injection:reveal"),
    (r"раскрой\s+(свой\s+)?(системный\s+)?промпт", "injection:reveal"),
    (r"введи\s+режим\s+(без\s+)?(цензуры|developer|отладки)", "injection:mode"),
    (r"отключи\s+(защиту|безопасность|фильтры)", "injection:guard"),
]

_COMPILED_INJECTION = [(re.compile(p, re.IGNORECASE), tag) for p, tag in _INJECTION_PATTERNS]

# Запросы-ловушки: требуют действий с реальным миром, к которым у ядра нет
# доступа — помечаются как «caution», чтобы генератор ответил честно.
_CAUTION_PATTERNS: List[Tuple[str, str]] = [
    (r"(переведи|отправь)\s+(деньги|перевод|платёж)", "caution:money"),
    (r"(забронируй|купите)\s+билет", "caution:booking"),
    (r"взлом(ай|ать)?\s+", "caution:unauthorized"),
    (r"hack\s+(my|the)\s+(pc|computer|account)", "caution:unauthorized"),
    (r"(удали|format|форматир)\s+(диск|ссылку|систему|всё)", "caution:destructive"),
    (r"delete\s+(all|my\s+all)\s+(files|data)", "caution:destructive"),
]
_COMPILED_CAUTION = [(re.compile(p, re.IGNORECASE), tag) for p, tag in _CAUTION_PATTERNS]


@dataclass
class IntakeResult:
    ok: bool
    text: str
    risk: str = "safe"            # safe | caution | blocked
    tags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    original_length: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "risk": self.risk,
            "tags": self.tags, "reasons": self.reasons,
            "original_length": self.original_length,
        }


def normalize_text(raw: str) -> str:
    """NFKC, сжатие пробелов, удаление управляющих символов."""
    if raw is None:
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ThreatGuard:
    """Защитный слой: корректность + инъекции + рискованные намерения."""

    def __init__(self, max_input_length: int = MAX_INPUT_LENGTH_DEFAULT,
                 block_injection: bool = True) -> None:
        self.max_input_length = max(200, int(max_input_length))
        self.block_injection = block_injection

    # ── стадия 1: приём ──────────────────────────────────────────────────────
    def check_intake(self, raw: str) -> IntakeResult:
        original_length = len(raw or "")
        text = normalize_text(raw)

        if not text:
            return IntakeResult(False, "", "blocked", ["empty"],
                                ["запрос пуст"], original_length)
        if len(text) > self.max_input_length:
            text = text[: self.max_input_length]
            return IntakeResult(True, text, "caution", ["truncated"],
                                ["запрос обрезан до лимита"], original_length)
        if _REPEAT_RUN.search(text):
            return IntakeResult(False, text, "blocked", ["spam:repeat"],
                                ["повтор одного символа слишком длинный"],
                                original_length)
        if _PUNCT_RUN.search(text):
            return IntakeResult(False, text, "blocked", ["spam:punctuation"],
                                ["спам-повтор пунктуации"], original_length)
        return IntakeResult(True, text, "safe", [], [], original_length)

    # ── стадия 2: защита ─────────────────────────────────────────────────────
    def check_threats(self, result: IntakeResult) -> IntakeResult:
        if not result.ok:
            return result
        text = result.text
        low = text.lower()

        for rx, tag in _COMPILED_INJECTION:
            if rx.search(low):
                result.tags.append(tag)
                result.reasons.append(f"паттерн {tag}")
                if self.block_injection and "reveal" not in tag:
                    result.risk = "blocked"
                elif result.risk == "safe":
                    result.risk = "caution"
                break
        else:
            for rx, tag in _COMPILED_CAUTION:
                if rx.search(low):
                    result.tags.append(tag)
                    result.reasons.append(f"осторожное намерение: {tag}")
                    result.risk = "caution"
                    break
        return result

    # ── полный проход ────────────────────────────────────────────────────────
    def process(self, raw: str) -> IntakeResult:
        return self.check_threats(self.check_intake(raw))
