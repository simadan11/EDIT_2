"""Инструмент: files.search — поиск файлов в дереве проекта (безопасный).

Ищет по имени/подстроке, умеет фильтровать по расширению. Ходит только
в рамках корня проекта (или явно указанного подпути корня) — никуда
внешне не выходит, скрытые/тяжёлые каталоги пропускает.
"""

from __future__ import annotations

import fnmatch
import os
import time
from typing import Any, Dict, List

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             "dist", "build", ".cache", "lumen_data", "firmware"}
_MAX_RESULTS = 25


def _root() -> str:
    from ...config import BASE_DIR
    return str(BASE_DIR)


def handler(pattern: str = "", extension: str = "", max_results: int = 25) -> Dict[str, Any]:
    pattern = (pattern or "").strip().lstrip("./")
    extension = (extension or "").strip().lstrip(".").lower()
    root = _root()
    results: List[Dict[str, Any]] = []
    t0 = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if time.time() - t0 > 8:
            break
        for fn in filenames:
            if extension and not fn.lower().endswith("." + extension):
                continue
            if pattern and not (fnmatch.fnmatch(fn.lower(), pattern.lower())
                                or pattern.lower() in fn.lower()):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            try:
                size = os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                size = -1
            results.append({"path": rel, "size_kb": round(size / 1024, 1)})
            if len(results) >= min(int(max_results or 25), 100):
                break
        if len(results) >= min(int(max_results or 25), 100):
            break

    if not results:
        text = f"Файлы по запросу «{pattern or extension or '—'}» не найдены."
    else:
        lines = [f"• {r['path']} ({r['size_kb']} КБ)" for r in results]
        text = f"Найдено {len(results)} файлов:\n" + "\n".join(lines)
    return {"text": text, "count": len(results), "results": results,
            "ok": True}


def spec():
    from ...kernel.tools import ToolSpec, ToolParam
    return ToolSpec(
        name="files.search", title="Поиск файлов",
        description="Поиск файлов по имени в дереве проекта (подстрока или glob), "
                    "фильтр по расширению.",
        handler=handler, category="files", timeout=10.0, icon="📁",
        params=[
            ToolParam("pattern", "string", "Имя или подстроца, напр. *report*.md", False),
            ToolParam("extension", "string", "Расширение без точки, напр. py", False),
        ],
    )
