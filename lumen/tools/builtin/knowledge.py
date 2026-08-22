"""Инструменты знания: memory.recall / memory.store / knowledge.capabilities."""

from __future__ import annotations

from typing import Any, Dict, Optional


# Мини-тезаурус: вопрос → ключи фактов (примитивная «семантика» выборки)
_SYNONYMS = {
    "имя": "user_name", "имени": "user_name", "имена": "user_name",
    "name": "user_name", "зовут": "user_name",
    "город": "city", "города": "city", "city": "city", "жив": "city",
    "работа": "occupation", "работы": "occupation", "work": "occupation",
    "job": "occupation", "язык": "language", "языка": "language",
    "language": "language", "нравится": "likes", "like": "likes", "люб": "likes",
}


def _expand_query(query: str) -> str:
    low = (query or "").lower()
    extra = []
    for word, key in _SYNONYMS.items():
        if word in low:
            extra.append(key)
    return (query or "") + (" " + " ".join(extra) if extra else "")


def recall_handler(query: str = "") -> Dict[str, Any]:
    # фабрика: подставляет активную MemoryFabric при вызове
    from ._ctx import memory_fabric
    if memory_fabric is None:
        return {"ok": False, "text": "Модуль памяти не инициализирован.", "error": "no memory"}
    facts = memory_fabric.recall(_expand_query(query), top_k=8)
    if facts:
        lines = [f"• [{f['category']}] {f['key']}: {f['value']}" for f in facts]
        return {"text": "Из долговременной памяти:\n" + "\n".join(lines),
                "count": len(facts), "facts": facts}
    # нет совпадений — показываем последние сохранённые факты
    all_facts = []
    for cat, items in memory_fabric.facts().items():
        for k, v in items.items():
            all_facts.append((v.get("updated", 0), cat, k, v.get("value", "")))
    all_facts.sort(reverse=True)
    if not all_facts:
        return {"text": f"По запросу «{query}» в долговременной памяти пока пусто.",
                "count": 0, "facts": []}
    lines = [f"• [{c}] {k}: {v}" for _, c, k, v in all_facts[:4]]
    return {"text": ("Точного совпадения не нашлось — вот последние сохранённые факты:\n"
                     + "\n".join(lines)), "count": 0, "facts": []}


def store_handler(category: str, key: str, value: str) -> Dict[str, Any]:
    from ._ctx import memory_fabric
    if memory_fabric is None:
        return {"ok": False, "text": "Модуль памяти не инициализирован.", "error": "no memory"}
    from ...kernel.memory import CATEGORIES
    cat = category if category in CATEGORIES else "notes"
    memory_fabric.remember(cat, key, value)
    return {"text": f"Записал в память: [{cat}] {key} = {value}",
            "category": cat, "key": key}


def capabilities_handler() -> Dict[str, Any]:
    from ... import BRAND_NAME, MODEL_NAME, VERSION, PIPELINE_NAME
    from ._ctx import registry
    lines = []
    if registry is not None:
        for t in registry.list():
            mark = "●" if t["available"] else "○"
            lines.append(f"{mark} {t['icon']} {t['name']} — {t['description'][:90]}")
    text = (
        f"{BRAND_NAME} ({MODEL_NAME}, v{VERSION}) — самостоятельная ИИ-платформа. "
        f"Конвейер «{PIPELINE_NAME}»: приём → защита → анализ → контекст → инструменты → генерация.\n"
        f"Доступные инструменты ({sum(1 for l in lines if l.startswith('●'))}):"
        + ("\n" + "\n".join(lines) if lines else "")
    )
    return {"text": text}


def recall_spec():
    from ...kernel.tools import ToolSpec, ToolParam
    return ToolSpec(
        name="memory.recall", title="Вспомнить",
        description="Поиск фактов в долговременной памяти LUMEN.",
        handler=recall_handler, category="memory", timeout=4.0, icon="🧠",
        params=[ToolParam("query", "string", "Что вспомнить", True)],
    )


def store_spec():
    from ...kernel.tools import ToolSpec, ToolParam
    return ToolSpec(
        name="memory.store", title="Запомнить",
        description="Сохранить факт в долговременную память (категория: identity, "
                    "preferences, projects, relationships, wishes, notes).",
        handler=store_handler, category="memory", timeout=4.0, icon="💾",
        params=[
            ToolParam("category", "string", "Категория", False, "notes"),
            ToolParam("key", "string", "Ключ факта", True),
            ToolParam("value", "string", "Значение", True),
        ],
    )


def capabilities_spec():
    from ...kernel.tools import ToolSpec
    return ToolSpec(
        name="knowledge.capabilities", title="Возможности LUMEN",
        description="Карточка платформы: версия, конвейер, список инструментов и их доступность.",
        handler=capabilities_handler, category="knowledge", timeout=4.0, icon="✨",
    )
