"""
sync.py — keep EDIT WEB's settings & memory in sync with the main MARK L project.

Two versions, one source of truth:
  • Live-link (default): EDIT WEB reads/writes the SAME files the desktop app
    uses — <main>/config/api_keys.json and <main>/memory/long_term.json — so both
    versions are always consistent, no merge needed.
  • Pull / Push: copy to/from a local snapshot in edit_web/data/ for offline or
    standalone use, or to apply local edits back to the main project.

`<main>` is resolved by paths.py (nested edit_web/ OR sibling folder) and can be
overridden with the EDITWEB_MAIN env var.
"""

from __future__ import annotations

import json
import time
import shutil
from pathlib import Path

import paths

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MAIN_DIR = paths.main_project_dir()
MAIN_CONFIG = MAIN_DIR / "config" / "api_keys.json"
MAIN_MEMORY = MAIN_DIR / "memory" / "long_term.json"

LOCAL_CONFIG = DATA_DIR / "config.json"
LOCAL_MEMORY = DATA_DIR / "memory.json"


def main_dir() -> Path:
    return MAIN_DIR


def main_paths() -> dict:
    return {
        "dir": str(MAIN_DIR),
        "exists": MAIN_DIR.exists(),
        "config": str(MAIN_CONFIG),
        "config_exists": MAIN_CONFIG.exists(),
        "config_mtime": _mtime(MAIN_CONFIG),
        "memory": str(MAIN_MEMORY),
        "memory_exists": MAIN_MEMORY.exists(),
        "memory_mtime": _mtime(MAIN_MEMORY),
    }


def _mtime(p: Path):
    try:
        return p.stat().st_mtime
    except Exception:
        return None


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _serialize(path: Path, obj: dict) -> None:
    """Write JSON preserving the original file's newline style + indent (if it
    exists), so a live-link edit doesn't reformat the main project's files."""
    nl = "\n"
    indent = 2
    try:
        raw = path.read_bytes()
        if b"\r\n" in raw:
            nl = "\r\n"
        txt = raw.decode("utf-8", "replace")
        for line in txt.split("\n"):
            stripped = line.lstrip(" \t")
            if stripped.startswith('"'):
                indent = len(line) - len(stripped)
                break
    except Exception:
        pass
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    if nl == "\r\n":
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))   # exact bytes, no newline translation


# ── settings (config/api_keys.json) ───────────────────────────────────────────
# Only non-secret fields are exposed to the UI; the API key is never sent.

SAFE_FIELDS = ("assistant_name", "user_name", "ui_color", "os_system",
               "morning_brief_enabled")


def read_settings() -> dict:
    cfg = _read_json(MAIN_CONFIG) or {}
    out = {k: cfg.get(k) for k in SAFE_FIELDS}
    out["__linked"] = MAIN_CONFIG.exists()
    out["__has_key"] = bool(cfg.get("gemini_api_key"))
    return out


def write_settings(fields: dict) -> dict:
    """Merge safe fields into the main config (live-link write, format-stable)."""
    if not MAIN_CONFIG.exists():
        return {"ok": False, "error": "main config not found"}
    cfg = _read_json(MAIN_CONFIG) or {}
    for k in SAFE_FIELDS:
        if k in fields and fields[k] is not None:
            cfg[k] = fields[k]
    _serialize(MAIN_CONFIG, cfg)
    return {"ok": True, "saved": [k for k in SAFE_FIELDS if k in fields]}


# ── memory (long_term.json) ───────────────────────────────────────────────────

def read_memory() -> dict:
    return _read_json(MAIN_MEMORY) or {}


def memory_summary() -> dict:
    m = read_memory()
    summary = {}
    for k, v in m.items():
        if isinstance(v, list):
            summary[k] = len(v)
        elif isinstance(v, dict):
            summary[k] = len(v)
        else:
            summary[k] = 1 if v else 0
    return summary


def write_memory(memory: dict) -> dict:
    if not MAIN_MEMORY.parent.exists():
        return {"ok": False, "error": "main memory folder not found"}
    _serialize(MAIN_MEMORY, memory)
    return {"ok": True}


# ── explicit pull / push (local snapshot) ─────────────────────────────────────

def pull() -> dict:
    """Copy main config + memory → edit_web/data/ snapshot."""
    n = 0
    if MAIN_CONFIG.exists():
        shutil.copy2(MAIN_CONFIG, LOCAL_CONFIG); n += 1
    if MAIN_MEMORY.exists():
        shutil.copy2(MAIN_MEMORY, LOCAL_MEMORY); n += 1
    return {"ok": True, "copied": n, "at": time.time()}


def push() -> dict:
    """Apply the local snapshot back to the main project."""
    n = 0
    if LOCAL_CONFIG.exists() and MAIN_CONFIG.parent.exists():
        shutil.copy2(LOCAL_CONFIG, MAIN_CONFIG); n += 1
    if LOCAL_MEMORY.exists() and MAIN_MEMORY.parent.exists():
        shutil.copy2(LOCAL_MEMORY, MAIN_MEMORY); n += 1
    return {"ok": True, "copied": n, "at": time.time()}


def status() -> dict:
    return {
        "main": main_paths(),
        "linked": MAIN_CONFIG.exists() and MAIN_MEMORY.exists(),
        "local_config": LOCAL_CONFIG.exists(),
        "local_memory": LOCAL_MEMORY.exists(),
        "settings": read_settings(),
        "memory_summary": memory_summary(),
    }
