"""
LUMEN tools — сборка реестра инструментов платформы.

build_default_tools() создаёт ToolRegistry, наполняет встроенными
инструментами ядра и (если включено) legacy-мостом к actions/*.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..kernel.memory import MemoryFabric
from ..kernel.tools import ToolRegistry
from .builtin import knowledge, system, timecalc, weather, files
from .adapters import LegacyToolBridge


def build_default_tools(config: Any,
                        memory: MemoryFabric) -> ToolRegistry:
    """Собирает реестр инструментов для данного конфиг+память."""
    from .builtin import _ctx
    _ctx.memory_fabric = memory

    registry = ToolRegistry(
        default_timeout=float(config.get("tools.timeout_sec", 15))
    )

    # встроенные инструменты ядра
    registry.register(system.spec())
    registry.register(timecalc.time_spec())
    registry.register(timecalc.math_spec())
    registry.register(weather.spec())
    registry.register(files.spec())
    registry.register(knowledge.recall_spec())
    registry.register(knowledge.store_spec())
    registry.register(knowledge.capabilities_spec())

    _ctx.registry = registry

    # legacy-мост к старым actions/* (опционально)
    if config.get("tools.enable_legacy_bridge", True):
        from ..config import BASE_DIR
        bridge = LegacyToolBridge(BASE_DIR)
        for spec in bridge.specs():
            registry.register(spec)

    return registry


__all__ = ["build_default_tools", "LegacyToolBridge"]
