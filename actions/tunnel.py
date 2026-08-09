"""
Internet Tunnel (🌐) — доступ к EDIT из любой точки через мобильный интернет.

Когда телефона нет в одной сети с ПК (нет WiFi, только мобильные данные),
локальный адрес 192.168.x.x:8000 недоступен. Этот модуль поднимает
публичный HTTPS-туннель с ПК, и телефон открывает тот же Remote Dashboard
через интернет-URL вида https://xxxx.trycloudflare.com.

Движки (по порядку предпочтения):
  1. Cloudflare quick tunnel  — cloudflared tunnel --url http://localhost:PORT
     (бесплатно, без аккаунта, URL меняется при каждом запуске)
  2. ngrok                    — ngrok http PORT
     (бесплатно, без аккаунта, URL меняется при каждом запуске)

Для постоянного адреса: бесплатный аккаунт Cloudflare + named tunnel
(см. README) — достаточно задать статический URL в конфиге.

Порядок запуска: start_tunnel() — фоновый процесс, URL парсится из логов.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

_PORT = 8000
_URL_RE = re.compile(r"https://[a-zA-Z0-9\-\.]+\.(?:trycloudflare\.com|ngrok\.(?:io|app))[^\s'\"]*", re.I)


def _find_bin(names: list[str]) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    # Windows: common install locations
    if platform.system() == "Windows":
        for cand in (
            Path.home() / "cloudflared.exe",
            Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
            Path.home() / "ngrok.exe",
        ):
            if cand.exists():
                return str(cand)
    return None


def cloudflared_bin() -> str | None:
    return _find_bin(["cloudflared", "cloudflared.exe"])


def ngrok_bin() -> str | None:
    return _find_bin(["ngrok", "ngrok.exe"])


def engine() -> str | None:
    """Which tunnel engine is available: 'cloudflared' | 'ngrok' | None."""
    if cloudflared_bin():
        return "cloudflared"
    if ngrok_bin():
        return "ngrok"
    return None


def install_hint() -> str:
    """Human-readable instructions for installing a tunnel engine."""
    lines = [
        "Интернет-туннель не установлен. Установи один из движков:",
        "",
        "  Cloudflare (рекомендуется):",
        "    Windows (PowerShell, от админа):",
        "      winget install cloudflare.cloudflared",
        "      (или скачай cloudflared.exe с https://github.com/cloudflare/cloudflared/releases)",
        "    macOS:  brew install cloudflared",
        "    Linux:  sudo apt install cloudflared   (или curl -L https://pkg.cloudflare.com/cloudflare-main.gpg ...)",
        "",
        "  ngrok:",
        "    https://ngrok.com/download  (или:  npm i -g ngrok / winget install ngrok)",
        "",
        "После установки перезапусти EDIT — кнопка 🌐 заработает.",
    ]
    return "\n".join(lines)


class TunnelManager:
    """Manages the public HTTPS tunnel process (background)."""

    def __init__(self, port: int = _PORT, static_url: str = ""):
        self._port = port
        self._static_url = static_url.strip()
        self._proc: subprocess.Popen | None = None
        self._url: str = ""
        self._engine: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()

    # ── state ────────────────────────────────────────────────────────────

    @property
    def url(self) -> str:
        with self._lock:
            return self._url or self._static_url

    @property
    def active(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def engine_name(self) -> str | None:
        with self._lock:
            return self._engine

    def status(self) -> dict:
        return {
            "active": self.active,
            "url": self.url,
            "engine": self.engine_name or engine(),
            "static_url": self._static_url,
        }

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, timeout: float = 30.0) -> dict:
        """Start the tunnel (blocking up to `timeout` s waiting for the URL)."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.status()
            if self._static_url:
                self._url = self._static_url
                return self.status()
            eng = engine()
            if not eng:
                return {"active": False, "error": "no_tunnel_binary",
                        "hint": install_hint()}
            self._engine = eng
            self._stop.clear()
            self._ready.clear()
            if eng == "cloudflared":
                cmd = [cloudflared_bin(), "tunnel", "--url", f"http://localhost:{self._port}",
                       "--no-autoupdate"]
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:  # ngrok
                cmd = [ngrok_bin(), "http", str(self._port), "--log", "stdout"]
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

        # Parse the URL from the process output in a thread
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        return self.status()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc = self._proc
            self._proc = None
            self._url = ""
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=4)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _watch(self) -> None:
        proc = None
        with self._lock:
            proc = self._proc
        if proc is None:
            self._ready.set()
            return
        buf = ""
        while not self._stop.is_set():
            if proc.poll() is not None and not buf:
                break
            try:
                chunk = proc.stdout.readline() if proc.stdout else ""
            except Exception:
                chunk = ""
            if not chunk:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
                continue
            buf += chunk
            m = _URL_RE.search(buf)
            if m:
                with self._lock:
                    self._url = m.group(0).rstrip("/")
                self._ready.set()
                buf = buf[m.end():]
                continue
            if len(buf) > 4096:
                buf = buf[-2048:]
        self._ready.set()

    # ── config helpers ───────────────────────────────────────────────────

    @staticmethod
    def config_path() -> Path:
        base = Path(__file__).resolve().parent.parent
        return base / "config" / "api_keys.json"

    @staticmethod
    def enabled() -> bool:
        try:
            with open(TunnelManager.config_path(), encoding="utf-8") as f:
                return bool(json.load(f).get("internet_tunnel", False))
        except Exception:
            return False

    @staticmethod
    def static_url() -> str:
        try:
            with open(TunnelManager.config_path(), encoding="utf-8") as f:
                return str(json.load(f).get("tunnel_static_url", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def set_enabled(enabled: bool) -> None:
        path = TunnelManager.config_path()
        try:
            with open(path, "r+", encoding="utf-8") as f:
                cfg = json.load(f)
                cfg["internet_tunnel"] = bool(enabled)
                f.seek(0)
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.truncate()
        except Exception:
            pass
