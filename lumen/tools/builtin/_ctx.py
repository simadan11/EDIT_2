"""Внутренний контекст встроенных инструментов (DI-карман).

Встроенные инструменты не держат жёстких ссылок на ядро — фабрика
build_default_tools() подставляет сюда активные объекты памяти и
реестра. Это позволяет использовать инструменты и в CLI, и в API,
и в тестах независимо.
"""

from __future__ import annotations

from typing import Any, Optional

memory_fabric: Optional[Any] = None   # MemoryFabric
registry: Optional[Any] = None        # ToolRegistry
