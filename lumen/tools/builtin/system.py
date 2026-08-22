"""Инструмент: system.status — состояние машины, где живёт LUMEN."""

from __future__ import annotations

import platform
import shutil
import time
from typing import Dict, Any

from ...kernel.tools import ToolResult

_START = time.time()


def _linux_cpu_percent() -> float:
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = list(map(int, parts[1:8]))
        idle = vals[3]
        total = sum(vals)
        # вторая проба через 150 мс
        time.sleep(0.15)
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals2 = list(map(int, parts[1:8]))
        idle2 = vals2[3]
        total2 = sum(vals2)
        dt = total2 - total
        di = idle2 - idle
        return round(100.0 * (1 - di / dt), 1) if dt > 0 else 0.0
    except Exception:
        return -1.0


def _linux_mem() -> Dict[str, Any]:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = total - avail
        return {
            "total_gb": round(total / 1048576, 1),
            "used_gb": round(used / 1048576, 1),
            "percent": round(100.0 * used / total, 1) if total else 0.0,
        }
    except Exception:
        return {"total_gb": -1, "used_gb": -1, "percent": -1}


def handler() -> Dict[str, Any]:
    system = platform.system()
    out: Dict[str, Any] = {
        "os": system,
        "os_detail": platform.platform(),
        "cpu_count": platform.processor() or str(__import__("os").cpu_count()),
        "python": platform.python_version(),
        "uptime_sec": int(time.time() - _START),
        "hostname": __import__("socket").gethostname(),
    }
    if system == "Linux":
        out["cpu_percent"] = _linux_cpu_percent()
        out["mem"] = _linux_mem()
    else:
        out["cpu_percent"] = -1
        out["mem"] = {"total_gb": -1, "used_gb": -1, "percent": -1}
    try:
        du = shutil.disk_usage("/")
        out["disk"] = {
            "total_gb": round(du.total / 2**30, 1),
            "free_gb": round(du.free / 2**30, 1),
            "percent": round(100.0 * du.used / du.total, 1),
        }
    except Exception:
        out["disk"] = {"total_gb": -1, "free_gb": -1, "percent": -1}

    text = (
        f"Система: {out['os']} ({out['hostname']}), Python {out['python']}\n"
        f"CPU: {out['cpu_count']}, загрузка {out['cpu_percent']}%"
        if out["cpu_percent"] >= 0 else
        f"Система: {out['os']} ({out['hostname']}), Python {out['python']}"
    )
    m = out["mem"]
    if m["total_gb"] > 0:
        text += f"\nПамять: {m['used_gb']} / {m['total_gb']} ГБ ({m['percent']}%)"
    d = out["disk"]
    if d["total_gb"] > 0:
        text += f"\nДиск /: свободно {d['free_gb']} из {d['total_gb']} ГБ ({d['percent']}% занято)"
    text += f"\nLUMEN работает {out['uptime_sec']} с с момента запуска."
    out["text"] = text
    return out


def spec():
    from ...kernel.tools import ToolSpec
    return ToolSpec(
        name="system.status",
        title="Состояние системы",
        description="CPU, память, диск, хост и аптайм LUMEN — без внешних зависимостей.",
        handler=handler,
        category="system",
        timeout=6.0,
        icon="🖥",
    )
