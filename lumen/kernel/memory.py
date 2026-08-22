"""
LUMEN — модуль контекстной памяти (MemoryFabric).

Три слоя:
  • working   — реплики текущей сессии (в оперативной памяти + JSONL-лог);
  • episodic  — журналы завершённых сессий (lumen_data/sessions/);
  • semantic  — долговременные факты по категориям (lumen_data/memory.json).

Категории семантических фактов:
  identity, preferences, projects, relationships, wishes, notes
(совместимы с legacy-форматом — импорт работает без потерь).

Возможности:
  • relevance-выборка фактов для промпта (простой скоринг по токенам);
  • автоизвлечение фактов из реплик («меня зовут …», «мне нравится …»);
  • атомарная запись, потоковая безопасность.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")
MAX_FACT_VALUE = 500

# Шаблоны автоизвлечения (RU + EN)
_AUTO_PATTERNS: List[Tuple[str, re.Pattern, str, str]] = [
    ("name:ru", re.compile(r"(?:меня\s+зовут|мое\s+имя)\s+([A-ZА-ЯЁ][a-zа-яё]{1,20})", re.I), "identity", "user_name"),
    ("name:en", re.compile(r"(?:my\s+name\s+is|i\s+am|call\s+me)\s+([A-Z][a-z]{1,20})", re.I), "identity", "user_name"),
    ("city:ru", re.compile(r"(?:я\s+жив(у|у)?\s+в|живу\s+в)\s+([A-ZА-ЯЁ][A-Za-zА-Яа-яё\- ]{1,24})", re.I), "identity", "city"),
    ("city:en", re.compile(r"i\s+live\s+in\s+([A-Z][A-Za-z\- ]{1,24})", re.I), "identity", "city"),
    ("like:ru", re.compile(r"(?:я\s+люб(лю|ил)?|мне\s+нравится)\s+([A-Za-zА-Яа-яё0-9\-]{2,40})", re.I), "preferences", "likes"),
    ("like:en", re.compile(r"i\s+(?:love|like)\s+([A-Za-z0-9\- ]{2,40})", re.I), "preferences", "likes"),
    ("work:ru", re.compile(r"(?:я\s+работаю\s+([а-яёa-z]{3,30})|(?:на\s+работе|работа)\s+—\s+([а-яёa-z\- ]{3,40}))", re.I), "identity", "occupation"),
    ("lang:ru", re.compile(r"(?:предпочитаю\s+язык|говорю\s+на)\s+([а-яёa-z]{3,20})", re.I), "preferences", "language"),
]


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower()))


# Мини-тезаурус выборки: слово из вопроса → ключ факта.
# Примитивная, но прозрачная «семантика» на стороне памяти.
_SYNONYMS: Dict[str, str] = {
    "имя": "user_name", "имени": "user_name", "имена": "user_name",
    "name": "user_name", "зовут": "user_name",
    "город": "city", "города": "city", "city": "city", "живу": "city",
    "живёт": "city", "работа": "occupation", "работы": "occupation",
    "work": "occupation", "job": "occupation",
    "язык": "language", "языка": "language", "language": "language",
    "нравится": "likes", "like": "likes", "люб": "likes",
}


def _expand_query(query: str) -> str:
    low = (query or "").lower()
    extra = {v for k, v in _SYNONYMS.items() if k in low}
    if not extra:
        return query or ""
    return f"{query} {' '.join(sorted(extra))}"


def _empty_facts() -> Dict[str, Dict[str, Any]]:
    return {cat: {} for cat in CATEGORIES}


class MemoryFabric:
    """Совокупность слоёв памяти платформы."""

    def __init__(self, memory_path: Optional[Path] = None,
                 sessions_dir: Optional[Path] = None,
                 max_facts: int = 400) -> None:
        from ..config import DATA_DIR, MEMORY_PATH, SESSIONS_DIR
        self.path = Path(memory_path) if memory_path else MEMORY_PATH
        self.sessions_dir = Path(sessions_dir) if sessions_dir else SESSIONS_DIR
        self.max_facts = max_facts
        self._lock = Lock()
        self._facts: Dict[str, Dict[str, Any]] = _empty_facts()
        # working layer
        self._sessions: Dict[str, deque] = {}
        self._session_meta: Dict[str, Dict[str, Any]] = {}
        self._current: Optional[str] = None
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._load_facts()

    # ═══════════════════════════════ SEMANTIC LAYER ══════════════════════════

    def _load_facts(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                base = _empty_facts()
                if isinstance(data, dict):
                    for cat in CATEGORIES:
                        items = data.get(cat, {})
                        if isinstance(items, dict):
                            base[cat] = items
                self._facts = base
        except Exception as e:  # повреждённый файл не роняет ядро
            print(f"[LUMEN:memory] ⚠ не удалось загрузить память: {e}")

    def _save_facts(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._facts, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def remember(self, category: str, key: str, value: Any) -> Dict[str, Any]:
        category = category if category in CATEGORIES else "notes"
        key = str(key).strip()[:80] or "item"
        value = str(value).strip()[:MAX_FACT_VALUE]
        with self._lock:
            cat = self._facts.setdefault(category, {})
            # лимит по общему числу фактов: вытесняем самые старые
            total = sum(len(v) for v in self._facts.values())
            if total >= self.max_facts and key not in cat:
                oldest_cat, oldest_key = None, None
                oldest_ts = float("inf")
                for c, items in self._facts.items():
                    for k, v in items.items():
                        ts = v.get("updated", 0)
                        if ts < oldest_ts:
                            oldest_cat, oldest_key, oldest_ts = c, k, ts
                if oldest_cat:
                    del self._facts[oldest_cat][oldest_key]
            cat[key] = {"value": value, "updated": time.time(),
                        "updated_human": _now_iso(), "origin": "explicit"}
            self._save_facts()
            return cat[key]

    def forget(self, category: str, key: str) -> bool:
        with self._lock:
            if category in self._facts and key in self._facts[category]:
                del self._facts[category][key]
                self._save_facts()
                return True
        return False

    def facts(self, category: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            if category and category in self._facts:
                return {k: dict(v) for k, v in self._facts[category].items()}
            return {cat: {k: dict(v) for k, v in items.items()}
                    for cat, items in self._facts.items()}

    def count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._facts.values())

    def recall(self, query: str, top_k: int = 6,
               category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Relevance-выборка фактов: токен-оверлеп + тезаурусное расширение."""
        query = _expand_query(query or "")
        q_tokens = _tokens(query)
        scored: List[Tuple[float, str, str, Dict[str, Any]]] = []
        with self._lock:
            cats = [category] if category else list(CATEGORIES)
            for cat in cats:
                for key, fact in self._facts.get(cat, {}).items():
                    f_tokens = _tokens(key) | _tokens(str(fact.get("value", "")))
                    inter = q_tokens & f_tokens
                    score = len(inter) * 2.0 + len(q_tokens & _tokens(key)) * 1.5
                    if score <= 0:
                        continue
                    rec = {"category": cat, "key": key,
                           "value": fact.get("value", ""), "score": round(score, 2)}
                    scored.append((score, cat, key, rec))
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        out = []
        for _, cat, key, rec in scored[:top_k]:
            rec["updated"] = rec.get("updated_human", "")
            out.append(rec)
        return out

    def recall_lines(self, query: str, top_k: int = 6) -> List[str]:
        return [f"[{r['category']}] {r['key']}: {r['value']}"
                for r in self.recall(query, top_k=top_k)]

    # ── автоизвлечение фактов из реплик пользователя ────────────────────────
    def harvest(self, user_text: str) -> List[Dict[str, Any]]:
        harvested = []
        for _tag, rx, cat, key in _AUTO_PATTERNS:
            m = rx.search(user_text or "")
            if m:
                value = m.group(1).strip()
                if 1 < len(value) <= 40:
                    fact = self.remember(cat, key, value)
                    harvested.append({"category": cat, "key": key, "value": value})
        return harvested

    # ── импорт legacy-памяти (memory/long_term.json) ────────────────────────
    def import_legacy(self, legacy_path: Path, overwrite: bool = False) -> int:
        imported = 0
        try:
            data = json.loads(Path(legacy_path).read_text(encoding="utf-8"))
        except Exception:
            return 0
        if not isinstance(data, dict):
            return 0
        with self._lock:
            for cat in CATEGORIES:
                items = data.get(cat, {})
                if not isinstance(items, dict):
                    continue
                for key, value in items.items():
                    # legacy-формат: {"value": …, "updated": "…"} или скаляр
                    if isinstance(value, dict):
                        value = value.get("value")
                    if not isinstance(value, (str, int, float, bool)):
                        continue
                    if overwrite or key not in self._facts.get(cat, {}):
                        self._facts.setdefault(cat, {})[str(key)] = {
                            "value": str(value)[:MAX_FACT_VALUE],
                            "updated": time.time(),
                            "updated_human": _now_iso(),
                            "origin": "legacy-import",
                        }
                        imported += 1
            self._save_facts()
        return imported

    # ═══════════════════════════════ WORKING LAYER ═══════════════════════════

    def new_session(self, title: str = "", session_id: Optional[str] = None) -> str:
        sid = session_id or uuid.uuid4().hex[:10]
        with self._lock:
            self._sessions.setdefault(sid, deque(maxlen=200))
            self._session_meta.setdefault(sid, {
                "id": sid, "title": title or "Сессия",
                "created": _now_iso(), "created_ts": time.time(),
                "messages": 0,
            })
            self._current = sid
            self._append_log(sid, {"type": "session", "id": sid,
                                   "title": self._session_meta[sid]["title"],
                                   "created": self._session_meta[sid]["created"]})
        return sid

    def current_session(self) -> Optional[str]:
        return self._current

    def select_session(self, sid: str) -> bool:
        with self._lock:
            if sid in self._sessions:
                self._current = sid
                return True
            if (self.sessions_dir / f"{sid}.jsonl").exists():
                self._sessions[sid] = deque(maxlen=200)
                self._session_meta.setdefault(sid, {"id": sid, "title": "Сессия",
                                                    "created": _now_iso(),
                                                    "created_ts": time.time(),
                                                    "messages": 0})
                self._current = sid
                return True
        return False

    def add_turn(self, session_id: str, role: str, content: str, meta: Optional[Dict] = None) -> None:
        sid = session_id or self._current
        if not sid:
            sid = self.new_session()
        with self._lock:
            self._sessions.setdefault(sid, deque(maxlen=200))
            self._sessions[sid].append({"role": role, "content": content,
                                        "ts": _now_iso(), **(meta or {})})
            self._session_meta.setdefault(sid, {"id": sid, "title": "Сессия",
                                                "created": _now_iso(),
                                                "created_ts": time.time(),
                                                "messages": 0})
            self._session_meta[sid]["messages"] += 1
            self._append_log(sid, {"type": "turn", "role": role,
                                   "content": content, "ts": _now_iso(),
                                   **(meta or {})})

    def history(self, session_id: Optional[str] = None,
                max_turns: int = 12) -> List[Dict[str, str]]:
        sid = session_id or self._current
        if not sid:
            return []
        with self._lock:
            msgs = list(self._sessions.get(sid, ()))
        return [{"role": m["role"], "content": m["content"]}
                for m in msgs[-max_turns:]]

    def last_user_text(self, session_id: Optional[str] = None) -> str:
        for m in reversed(self.history(session_id=session_id, max_turns=200)):
            if m["role"] == "user":
                return m["content"]
        return ""

    def sessions_list(self) -> List[Dict[str, Any]]:
        with self._lock:
            meta = [dict(v) for v in self._session_meta.values()]
        # подхватываем сессии, восстановленные из диска, без кеша
        for f in self.sessions_dir.glob("*.jsonl"):
            sid = f.stem
            if sid not in {m["id"] for m in meta}:
                meta.append({"id": sid, "title": f"Сессия {sid[:6]}",
                             "created": "—", "messages": 0, "on_disk": True})
        meta.sort(key=lambda m: m.get("created_ts", 0), reverse=True)
        return meta

    def drop_session(self, sid: str) -> bool:
        with self._lock:
            self._sessions.pop(sid, None)
            self._session_meta.pop(sid, None)
            if self._current == sid:
                self._current = None
        f = self.sessions_dir / f"{sid}.jsonl"
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass
        return True

    def _append_log(self, sid: str, record: Dict[str, Any]) -> None:
        f = self.sessions_dir / f"{sid}.jsonl"
        try:
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── сводка сессии (эпизодический слой) ──────────────────────────────────
    def summarize_session(self, sid: str) -> Dict[str, Any]:
        hist = self.history(session_id=sid, max_turns=200)
        user_msgs = [m["content"] for m in hist if m["role"] == "user"]
        with self._lock:
            meta = self._session_meta.get(sid, {})
        return {
            "session_id": sid,
            "title": meta.get("title", "Сессия"),
            "created": meta.get("created", ""),
            "turns": len(hist),
            "first_request": (user_msgs[0][:120] if user_msgs else ""),
            "summary": (user_msgs[0][:80] + ("…" if len(user_msgs) > 1 else "")
                        + (f" (+{len(user_msgs)-1} реплик)" if len(user_msgs) > 1 else ""))
                      or "Сессия без реплик",
        }
