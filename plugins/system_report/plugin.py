"""System Report plugin — CPU / RAM / disks / battery / uptime."""

import platform
import time


def _uptime() -> str:
    try:
        import psutil
        secs = int(time.time() - psutil.boot_time())
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return f"{h} h {m} min"
    except Exception:
        return "unknown"


def run_tool(tool_name: str, args: dict, player=None) -> str:
    detail = str((args or {}).get("detail") or "short").lower()
    try:
        import psutil
    except Exception:
        return "psutil is not installed — cannot read system metrics."

    cpu = psutil.cpu_percent(interval=0.4)
    vm = psutil.virtual_memory()
    lines = [
        f"CPU: {cpu:.0f}% ({psutil.cpu_count(logical=True)} threads)",
        f"RAM: {vm.percent:.0f}% used — {vm.used / 1e9:.1f} / {vm.total / 1e9:.1f} GB",
        f"Uptime: {_uptime()}",
    ]

    if detail == "full":
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                lines.append(
                    f"Disk {part.device}: {usage.percent:.0f}% used — "
                    f"{usage.free / 1e9:.0f} GB free of {usage.total / 1e9:.0f} GB"
                )
            except Exception:
                continue
        battery = getattr(psutil, "sensors_battery", lambda: None)()
        if battery:
            state = "charging" if battery.power_plugged else "on battery"
            lines.append(f"Battery: {battery.percent:.0f}% ({state})")
        lines.append(f"OS: {platform.system()} {platform.release()}")
    else:
        try:
            usage = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
            lines.append(f"Disk: {usage.percent:.0f}% used, {usage.free / 1e9:.0f} GB free")
        except Exception:
            pass

    return "\n".join(lines)
