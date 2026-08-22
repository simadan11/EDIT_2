"""
LUMEN — обучающая петля (LearningLoop).

Механизмы «дальнейшего обучения» платформы:

  1. Фидбэк — пользователь ставит оценку (+/−) ответу; записи
     сохраняются в JSONL (lumen_data/learning/feedback.jsonl).
  2. Адаптация предпочтений — по накопленным оценкам выводятся
     «выученные предпочтения», которые встраиваются в системный
     промпт (context.py) и влияют на стиль генерации.
  3. Корпус для дообучения — export_corpus() выгружает пары
     «запрос → ответ → оценка» в JSONL: готовый датасет для
     future fine-tuning выбранной генеративной модели.
  4. Статистика — по намерениям, инструментам, качеству ответов.

Это не магия и не замена дообучению весов — это честная петля
«сигнал → адаптация → генерация», которая расширяется без переписывания ядра.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from ..config import LEARNING_DIR


class LearningLoop:
    def __init__(self, learning_dir: Optional[Path] = None) -> None:
        self.dir = Path(learning_dir) if learning_dir else LEARNING_DIR
        self.feedback_file = self.dir / "feedback.jsonl"
        self.prefs_file = self.dir / "preferences.json"
        self.stats_file = self.dir / "stats.json"
        self._lock = Lock()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._prefs = self._read_json(self.prefs_file, default={})
        self._stats = self._read_json(self.stats_file, default={})
        self._stats.setdefault("intents", {})
        self._stats.setdefault("tools", {})
        self._stats.setdefault("requests", 0)
        self._stats.setdefault("fallbacks", 0)

    # ── JSONL helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        out = []
        if not path.exists():
            return out
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            pass
        return out

    @staticmethod
    def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return default

    def _append_jsonl(self, path: Path, record: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── 1. фидбэк ────────────────────────────────────────────────────────────
    def record_feedback(self, message_id: str, rating: int,
                        note: str = "", intent: str = "") -> Dict[str, Any]:
        record = {"ts": time.time(), "message_id": message_id,
                  "rating": 1 if rating > 0 else (-1 if rating < 0 else 0),
                  "note": note[:300], "intent": intent}
        with self._lock:
            self._append_jsonl(self.feedback_file, record)
            self._adapt(record)
        return record

    def feedback_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._read_jsonl(self.feedback_file)[-limit:]

    # ── 2. адаптация предпочтений ────────────────────────────────────────────
    def _adapt(self, record: Dict[str, Any]) -> None:
        if record["rating"] == 0:
            return
        prefs = self._prefs
        # примитивная, но честная логика:
        #  • минус на длинном ответе → «короче»
        #  • плюс при коротком стиле → закрепить «краткость»
        #  • заметка пользователя → дословное предпочтение
        note = (record.get("note") or "").strip()
        if note:
            key = "note_" + re.sub(r"\W+", "_", note[:24]).strip("_")
            prefs[key] = {
                "text": f"Пользователь уточнил: {note}",
                "votes": prefs.get(key, {}).get("votes", 0) + record["rating"],
            }
        elif record["rating"] < 0:
            k = "answers_brief"
            prefs[k] = {"text": "Отвечать короче и без лишнего контекста",
                        "votes": prefs.get(k, {}).get("votes", 0) - 1}
        self._prefs_ts = time.time()
        self._save_json(self.prefs_file, prefs)

    def learned_preferences(self, top_k: int = 6) -> List[str]:
        """Выученные предпочтения для системного промпта."""
        prefs = self._prefs
        ranked = sorted(prefs.items(),
                        key=lambda kv: abs(kv[1].get("votes", 0)), reverse=True)
        out = []
        for key, val in ranked[:top_k]:
            votes = val.get("votes", 0)
            if votes == 0:
                continue
            out.append(f"{val.get('text', key)} (баланс оценок: {votes:+d})")
        return out

    # ── статистика запросов ──────────────────────────────────────────────────
    def record_request(self, intent: str, tools: List[str],
                       fallback: bool) -> None:
        with self._lock:
            self._stats["requests"] = self._stats.get("requests", 0) + 1
            self._stats["intents"][intent] = self._stats["intents"].get(intent, 0) + 1
            for t in tools:
                self._stats["tools"][t] = self._stats["tools"].get(t, 0) + 1
            if fallback:
                self._stats["fallbacks"] = self._stats.get("fallbacks", 0) + 1
            self._save_json(self.stats_file, self._stats)

    # ── 3. корпус для дообучения ─────────────────────────────────────────────
    def export_corpus(self, path: Path, sessions_dir: Path,
                      max_sessions: int = 200) -> int:
        """Собирает JSONL-корпус: {instruction, response, rating, intent}."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        feedback = {f["message_id"]: f for f in self._read_jsonl(self.feedback_file)}
        count = 0
        files = sorted(sessions_dir.glob("*.jsonl"))[-max_sessions:]
        with open(path, "w", encoding="utf-8") as out:
            for f in files:
                for rec in self._read_jsonl(f):
                    if rec.get("type") != "turn":
                        continue
                    if rec.get("role") != "lumen":
                        continue
                    mid = rec.get("message_id", "")
                    fb = feedback.get(mid)
                    out.write(json.dumps({
                        "instruction": rec.get("last_user", ""),
                        "response": rec.get("content", ""),
                        "intent": rec.get("intent", ""),
                        "rating": (fb["rating"] if fb else None),
                        "ts": rec.get("ts", ""),
                    }, ensure_ascii=False) + "\n")
                    count += 1
        return count

    # ── сводка для UI ────────────────────────────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        feedback = self._read_jsonl(self.feedback_file)
        pos = sum(1 for f in feedback if f.get("rating", 0) > 0)
        neg = sum(1 for f in feedback if f.get("rating", 0) < 0)
        with self._lock:
            stats = dict(self._stats)
        return {
            "requests": stats.get("requests", 0),
            "fallbacks": stats.get("fallbacks", 0),
            "intents": dict(sorted(stats.get("intents", {}).items(),
                                   key=lambda kv: -kv[1])),
            "tools": dict(sorted(stats.get("tools", {}).items(),
                                 key=lambda kv: -kv[1])),
            "feedback_total": len(feedback),
            "feedback_positive": pos,
            "feedback_negative": neg,
            "approval_rate": round(pos / max(1, pos + neg), 3),
            "learned_preferences": self.learned_preferences(),
        }

    def _save_json(self, path: Path, data: Dict[str, Any]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
