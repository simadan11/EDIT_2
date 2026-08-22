"""
Internet Tunnel (🌐) — доступ к LUMEN из любой точки через мобильный интернет.

Когда телефона нет в одной сети с ПК (нет WiFi, только мобильные данные),
локальный адрес 192.168.x.x:8000 недоступен. Этот модуль поднимает
публичный HTTPS-туннель с ПК, и телефон открывает тот же Remote Dashboard
через интернет-URL вида https://xxxx.trycloudflare.com.

Движки (по порядку предпочтения; принудительно: "tunnel_engine" в конфиге):
  1. Cloudflare quick tunnel  — cloudflared tunnel --url http://localhost:PORT
     (бесплатно, без аккаунта, URL меняется при каждом запуске)
  2. ngrok                    — ngrok http PORT
     (бесплатно, без аккаунта, URL меняется при каждом запуске)
  3. playit.gg                — playit-agent (туннели TCP/UDP без port-forward)
     ФИКСИРОВАННЫЙ бесплатный адрес вида ххх.at.ply.gg:PORT — живёт, пока
     существует туннель в веб-дашборде playit. Первый запуск печатает
     claim-ссылку вида https://playit.gg/claim/XXXX — агент привязывается
     к аккаунту, туннель настраивается в веб-панели (TCP → 127.0.0.1:8001).
  4. portmap.io + OpenVPN     — стандартный клиент openvpn + их .ovpn-профиль
     (config/portmap.ovpn). Публичный адрес задаётся в кабинете portmap
     (yourname.portmap.io:PORT) и прописывается в конфиг LUMEN:
     "tunnel_engine": "portmap" + "tunnel_static_url".

Для постоянного адреса (Cloudflare-путь): бесплатный аккаунт Cloudflare +
named tunnel (см. README) — достаточно задать статический URL в конфиге.

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
# playit.gg: публичные адреса туннелей печатаются агентом в stdout
_PLAYIT_ADDR_RE = re.compile(
    r"([a-z0-9][a-z0-9\-\.]*\.(?:ply\.gg|joinmc\.link|auto\.playit\.gg))(?::(\d{2,5}))?", re.I)
# служебные хосты playit — не адреса туннелей
_PLAYIT_SKIP = {"playit.gg", "ping.playit.gg", "api.playit.gg", "new.playit.gg",
                "account.playit.gg", "docs.playit.gg"}
# привязка агента к аккаунту (первый запуск на новой машине)
_PLAYIT_CLAIM_RE = re.compile(r"https://playit\.gg/claim/[A-Za-z0-9\-_]+")

# openvpn (portmap.io): признак, что туннель поднялся
_OVPN_READY = "Initialization Sequence Completed"


def _find_portmap_ovpn() -> Path | None:
    """config/portmap.ovpn или первый *.ovpn в config/."""
    cfg_dir = TunnelManager.config_path().parent
    main = cfg_dir / "portmap.ovpn"
    if main.exists():
        return main
    try:
        for f in sorted(cfg_dir.glob("*.ovpn")):
            return f
    except Exception:
        pass
    return None


def _norm_public_url(u: str) -> str:
    """'name.portmap.host:12345' / 'tcp://…' → 'https://name.portmap.host:12345'."""
    u = (u or "").strip().strip("'\"").rstrip("/")
    if not u:
        return ""
    if u.lower().startswith(("tcp://", "udp://")):      # схема из панели portmap
        u = "https://" + u.split("://", 1)[1]
    if not re.match(r"https?://", u, re.I):
        u = "https://" + u
    return u


def portmap_hint() -> str:
    """Инструкция по настройке portmap.io + OpenVPN (показывается в LUMEN)."""
    return "\n".join([
        "portmap.io + OpenVPN — настройка (один раз):",
        "",
        "  1. Аккаунт: https://portmap.io  (бесплатно)",
        "  2. Создай Configuration → Tunnel → Protocol: TCP",
        "     → Local port: 8001   (HTTPS-алиас дашборда LUMEN)",
        "     portmap назначит адрес вида  ваше-имя.portmap.io:12345",
        "  3. Скачай их .ovpn-профиль → положи в  config\\portmap.ovpn",
        "  4. Установи OpenVPN:  https://openvpn.net/community-downloads/",
        "     (Windows: 'OpenVPN Windows Installer', нужны права админа — TAP-адаптер)",
        "  5. В config\\api_keys.json запиши адрес:",
        '       "tunnel_engine": "portmap",',
        '       "tunnel_static_url": "https://ваше-имя.portmap.io:12345"',
        "  6. Перезапусти LUMEN и нажми 🌐.",
        "",
        "  Телефон (4G): открыть адрес → 1 раз принять сертификат → PIN.",
    ])


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
            Path.home() / "Downloads" / "playit-windows-x86_64.exe",
            Path.home() / "Downloads" / "playit-windows-x64.exe",
            Path.home() / "Downloads" / "playit.exe",
            Path.home() / "playit.exe",
            Path(r"C:\Program Files\playit_gg\bin\playit.exe"),
            Path(r"C:\Program Files (x86)\playit_gg\bin\playit.exe"),
        ):
            if cand.exists():
                return str(cand)
    return None


def cloudflared_bin() -> str | None:
    return _find_bin(["cloudflared", "cloudflared.exe"])


def ngrok_bin() -> str | None:
    return _find_bin(["ngrok", "ngrok.exe"])


def playit_bin() -> str | None:
    return _find_bin(["playit", "playit.exe", "playit-cli", "playit-agent"])


def openvpn_bin() -> str | None:
    """Стандартный клиент OpenVPN — движок для portmap.io-туннелей."""
    p = _find_bin(["openvpn", "openvpn.exe"])
    if p:
        return p
    if platform.system() == "Windows":
        for cand in (
            Path(r"C:\Program Files\OpenVPN\bin\openvpn.exe"),
            Path(r"C:\Program Files (x86)\OpenVPN\bin\openvpn.exe"),
        ):
            if cand.exists():
                return str(cand)
    return None


_BINS = {"cloudflared": cloudflared_bin, "ngrok": ngrok_bin,
         "playit": playit_bin, "portmap": openvpn_bin}
# portmap сознательно НЕ входит в auto: OpenVPN стоит у многих «для себя»,
# его наличие не значит «хочу туннель через portmap» — только явный выбор.
_AUTO_ORDER = ("cloudflared", "ngrok", "playit")


def engine() -> str | None:
    """Which tunnel engine: 'cloudflared' | 'ngrok' | 'playit' | 'portmap' | None.

    Принудительный выбор: "tunnel_engine" в config/api_keys.json
    ("playit" / "cloudflared" / "ngrok" / "portmap" / "auto").
    При "auto" берётся первый установленный движок (кроме portmap — только явно).
    """
    pref = "auto"
    try:
        with open(TunnelManager.config_path(), encoding="utf-8") as f:
            pref = str(json.load(f).get("tunnel_engine", "auto") or "auto").strip().lower()
    except Exception:
        pass
    if pref in _BINS:
        return pref                                    # явный выбор пользователя
    for name in _AUTO_ORDER:
        if _BINS[name]():
            return name
    return None


def install_hint() -> str:
    """Human-readable instructions for installing a tunnel engine."""
    lines = [
        "Интернет-туннель не установлен. Установи один из движков:",
        "",
        "  playit.gg (рекомендуется для себя — ПОСТОЯННЫЙ бесплатный адрес):",
        "    1. Скачай агент:  https://playit.gg/download   (Windows: playit-windows-*.exe)",
        "    2. Нажми 🌐 в LUMEN ещё раз — агент напечатает claim-ссылку",
        "       https://playit.gg/claim/XXXX — открой её и привяжи агент к аккаунту",
        "    3. В панели playit.gg: Add Tunnel → Protocol TCP → Local 127.0.0.1 → Port 8001",
        "    4. Готово: твой постоянный адрес вида  xxx.at.ply.gg:12345",
        "       Телефон: https://xxx.at.ply.gg:12345 → принять сертификат (1 раз)",
        "       → PIN → «Add to Home screen» = личное приложение, работает на 4G.",
        "",
        "  Cloudflare (быстрый URL на один раз, без аккаунта):",
        "    Windows (PowerShell, от админа):",
        "      winget install cloudflare.cloudflared",
        "      (или скачай cloudflared.exe с https://github.com/cloudflare/cloudflared/releases)",
        "    macOS:  brew install cloudflared",
        "    Linux:  sudo apt install cloudflared   (или curl -L https://pkg.cloudflare.com/cloudflare-main.gpg ...)",
        "",
        "  ngrok:",
        "    https://ngrok.com/download  (или:  npm i -g ngrok / winget install ngrok)",
        "",
        "  portmap.io + OpenVPN (постоянный адрес, бесплатно):",
        "    см. полную инструкцию — в readme.md, раздел Internet Access, вариант D,",
        "    или: «tunnel_engine»: «portmap» в config/api_keys.json после настройки.",
        "",
        "После установки перезапусти LUMEN — кнопка 🌐 заработает.",
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
        self._claim_url: str = ""     # playit: ссылка привязки агента (1-й запуск)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()

    # ── state ────────────────────────────────────────────────────────────

    @property
    def url(self) -> str:
        with self._lock:
            return self._url or _norm_public_url(self._static_url)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def engine_name(self) -> str | None:
        with self._lock:
            return self._engine

    def status(self) -> dict:
        with self._lock:
            claim = self._claim_url
        st = {
            "active": self.active,
            "url": self.url,
            "engine": self.engine_name or engine(),
            "static_url": self._static_url,
        }
        if claim:
            st["claim_url"] = claim
        return st

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, timeout: float = 30.0) -> dict:
        """Start the tunnel (blocking up to `timeout` s waiting for the URL)."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.status()
            eng = engine()
            if self._static_url and eng != "portmap":
                # чисто-служебный статический URL (Cloudflare named tunnel и т.п.)
                self._url = _norm_public_url(self._static_url)
                return self.status()
            if not eng:
                return {"active": False, "error": "no_tunnel_binary",
                        "hint": install_hint()}
            exe = _BINS[eng]()
            if eng == "portmap":
                ovpn = _find_portmap_ovpn()
                if not exe or not ovpn or not self._static_url:
                    # openvpn не установлен / нет .ovpn / не задан адрес
                    return {"active": False, "error": "no_tunnel_binary",
                            "engine": "portmap", "hint": portmap_hint()}
            elif not exe:
                # движок выбран в конфиге ("tunnel_engine"), но не установлен
                return {"active": False, "error": "no_tunnel_binary", "engine": eng,
                        "hint": install_hint()}
            self._engine = eng
            self._stop.clear()
            self._ready.clear()
            self._claim_url = ""
            if eng == "cloudflared":
                cmd = [exe, "tunnel", "--url", f"http://localhost:{self._port}",
                       "--no-autoupdate"]
            elif eng == "playit":
                # playit-агент сам поднимает все туннели аккаунта;
                # публичный адрес назначен в веб-панели playit.gg
                cmd = [exe]
            elif eng == "portmap":
                # стандартный OpenVPN-клиент с профилем из кабинета portmap.io;
                # публичный адрес известен заранее (задан в tcp://... виде в панели)
                self._url = _norm_public_url(self._static_url)
                cmd = [exe, "--config", str(ovpn), "--auth-nocache"]
            else:  # ngrok
                cmd = [exe, "http", str(self._port), "--log", "stdout"]
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
            with self._lock:
                eng = self._engine
            if eng == "playit":
                # 1) claim-ссылка (первый запуск агента на новой машине)
                cm = _PLAYIT_CLAIM_RE.search(buf)
                if cm:
                    with self._lock:
                        self._claim_url = cm.group(0)
                    buf = buf[cm.end():]
                    continue
                # 2) публичный адрес туннеля, напр. xyz.at.ply.gg:25431
                for am in _PLAYIT_ADDR_RE.finditer(buf):
                    host, port = am.group(1), am.group(2)
                    if host.lower() in _PLAYIT_SKIP:
                        continue
                    with self._lock:
                        self._url = f"https://{host}:{port}" if port else f"https://{host}"
                    self._ready.set()
                    buf = buf[am.end():]
                    break
                if len(buf) > 4096:
                    buf = buf[-2048:]
                continue
            if eng == "portmap":
                # openvpn: туннель поднят, когда инициализация завершена
                if _OVPN_READY in buf:
                    self._ready.set()
                    buf = buf.split(_OVPN_READY, 1)[1]
                if len(buf) > 4096:
                    buf = buf[-2048:]
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

