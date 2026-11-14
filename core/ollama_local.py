"""Local Ollama model discovery & activation for EDIT / MARK XL.

The user keeps their GGUF / Ollama models on a second disk, e.g.

    D:\\бодикам\\manifests-v2\\ollama.com\\huihui_ai\\<model>\\<tag>
    D:\\бодикам\\blobs\\sha256-...

This module:

  * finds the Ollama model store automatically (config → env → common
    folders → every fixed drive on Windows, including ``D:\\бодикам``),
  * reads the model names straight from the manifest tree
    (``manifests-v2/ollama.com/<ns>/<model>/<tag>`` and the legacy
    ``manifests/registry.ollama.ai/<ns>/<model>/<tag>``), so the list works
    even when the Ollama server is not running yet,
  * starts ``ollama serve`` with ``OLLAMA_MODELS`` pointing at that store,
  * writes the chosen model into ``config/api_keys.json`` so every part of
    EDIT (model_router, llm_client, actions…) uses it,
  * probes the model with a tiny chat request.

No third-party dependency is required beyond ``requests``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

import requests

# ── paths & config ──────────────────────────────────────────────────────────


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODELS_DIR_HINTS = (
    r"D:\бодикам",
    r"D:\бодикам\models",
    r"D:\бодикам\ollama",
    r"D:\models",
    r"D:\ollama",
    r"C:\бодикам",
    r"C:\models",
)

# Folder names Ollama uses for its index
_MANIFEST_DIR_NAMES = ("manifests-v2", "manifests")
_KNOWN_HOSTS = ("ollama.com", "registry.ollama.ai", "registry.ollama.com")


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(updates: dict) -> dict:
    """Merge ``updates`` into api_keys.json (creating it when missing)."""
    cfg = load_config()
    cfg.update(updates or {})
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:  # pragma: no cover - disk/permission problems
        print(f"[OLLAMA] Could not save config: {e}")
    return cfg


def get_base_url() -> str:
    cfg = load_config()
    url = (cfg.get("llm_url") or DEFAULT_URL).strip().rstrip("/")
    return url or DEFAULT_URL


# ── model store discovery ───────────────────────────────────────────────────


def _norm(p: str | os.PathLike) -> Path | None:
    try:
        return Path(os.path.expandvars(os.path.expanduser(str(p))))
    except Exception:
        return None


def _drive_roots() -> list[Path]:
    """Windows: every fixed drive is worth a shallow look for ``бодикам``."""
    roots: list[Path] = []
    if sys.platform == "win32":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            d = Path(f"{letter}:\\")
            if d.exists():
                roots.append(d)
    return roots


def _models_dir_from(path: Path) -> Path | None:
    """Return the Ollama *models* root if ``path`` lies inside one."""
    if not path:
        return None
    try:
        p = path.expanduser()
        if p.is_file():
            p = p.parent
        if not p.exists():
            return None
        # the path itself is a models root
        for name in _MANIFEST_DIR_NAMES:
            if (p / name).is_dir():
                return p
        if p.name in _MANIFEST_DIR_NAMES:       # .../models/manifests-v2
            return p.parent
        # path points at manifests-v2\ollama.com or deeper
        for parent in [p, *p.parents]:
            if parent.name in _MANIFEST_DIR_NAMES:
                return parent.parent
            if parent.name == "blobs" and (parent.parent / "manifests-v2").is_dir():
                return parent.parent
        # a few known nesting levels (D:\бодикам\models, D:\бодикам\ollama\models)
        for name in _MANIFEST_DIR_NAMES:
            for sub in p.glob(f"*/{name}"):
                return sub.parent
            for sub in p.glob(f"*/*/{name}"):
                return sub.parent
    except Exception:
        return None
    return None


def _candidate_paths() -> list[Path]:
    cfg = load_config()
    extra = cfg.get("ollama_models_dir_extra") or []
    if isinstance(extra, str):
        extra = [extra]
    out: list[Path] = []
    for raw in (
        cfg.get("ollama_models_dir"),
        os.environ.get("OLLAMA_MODELS"),
        *extra,
    ):
        if raw:
            p = _norm(raw)
            if p:
                out.append(p)
    for hint in DEFAULT_MODELS_DIR_HINTS:
        p = _norm(hint)
        if p:
            out.append(p)
    home = Path.home()
    out += [
        home / ".ollama" / "models",
        home / "AppData" / "Local" / "Ollama" / "models",
        Path("/usr/share/ollama/.ollama/models"),
        Path("/usr/local/share/ollama/.ollama/models"),
        Path("/var/lib/ollama/models"),
    ]
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        out.append(Path(localappdata) / "Ollama" / "models")
    return out


_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 10.0


def _cached(key: str, ttl: float, producer):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


def find_models_dirs() -> list[Path]:
    return list(_cached("dirs", _CACHE_TTL, _find_models_dirs))


def _find_models_dirs() -> list[Path]:
    """Every plausible Ollama model store found on this machine."""
    found: list[Path] = []
    for p in _candidate_paths():
        md = _models_dir_from(p)
        if md and all(str(md).lower() != str(x).lower() for x in found):
            found.append(md)

    # Shallow hunt for a "бодикам" folder next to the drive roots.
    for drive in _drive_roots():
        for name in ("бодикам", "bodicam", "bodikam"):
            p = drive / name
            md = _models_dir_from(p)
            if md and all(str(md).lower() != str(x).lower() for x in found):
                found.append(md)

    # Sort: configured first, then writable/with models
    cfg_dir = str(load_config().get("ollama_models_dir") or "").lower()
    found.sort(key=lambda x: (str(x).lower() != cfg_dir, -_count_manifests(x)))
    return found


def _manifest_roots(models_dir: Path) -> list[Path]:
    return [models_dir / n for n in _MANIFEST_DIR_NAMES if (models_dir / n).is_dir()]


def _walk_manifest_files(root: Path, limit: int = 4000) -> Iterable[Path]:
    """Manifest files live at <root>/<host>/<ns>/<model>/<tag>."""
    seen = 0
    try:
        for host in sorted(root.iterdir()):
            if not host.is_dir():
                continue
            for ns in sorted(host.iterdir()):
                if not ns.is_dir():
                    continue
                for model in sorted(ns.iterdir()):
                    if not model.is_dir():
                        continue
                    for tag in sorted(model.iterdir()):
                        if not tag.is_file():
                            continue
                        yield tag
                        seen += 1
                        if seen >= limit:
                            return
    except Exception:
        return


def _manifest_size(path: Path) -> int:
    """Total size from the manifest JSON, 0 when unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        total = 0
        for layer in data.get("layers") or []:
            total += int(layer.get("size") or 0)
        total += int((data.get("config") or {}).get("size") or 0)
        return total
    except Exception:
        return 0


def _model_name_from_manifest(root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root).parts
    except Exception:
        return None
    if len(rel) < 4:
        return None
    host, ns, model, tag = rel[0], rel[-3], rel[-2], rel[-1]
    if host not in _KNOWN_HOSTS:
        # unusual registry → keep it so Ollama can resolve it
        prefix = f"{host}/"
    else:
        prefix = ""
    name = f"{ns}/{model}" if ns and ns != "library" else model
    return f"{prefix}{name}:{tag}"


def list_filesystem_models(models_dir: Path | None = None) -> list[dict]:
    """Models discovered from the manifest tree (works offline)."""
    if models_dir is None:
        return list(_cached("models", _CACHE_TTL, lambda: _list_filesystem_models(None)))
    return _list_filesystem_models(models_dir)


def _list_filesystem_models(models_dir: Path | None) -> list[dict]:
    dirs = [models_dir] if models_dir else find_models_dirs()
    models: list[dict] = []
    seen: set[str] = set()
    for md in dirs:
        for root in _manifest_roots(md):
            for path in _walk_manifest_files(root):
                name = _model_name_from_manifest(root, path)
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                models.append(
                    {
                        "name": name,
                        "namespace": name.rsplit("/", 1)[0] if "/" in name else "",
                        "size": _manifest_size(path),
                        "manifest": str(path),
                        "models_dir": str(md),
                        "source": "filesystem",
                    }
                )
    models.sort(key=lambda m: m["name"].lower())
    return models


def server_models(url: str | None = None, timeout: float = 3.0) -> list[dict]:
    """Models known to a running Ollama server."""
    url = (url or get_base_url()).rstrip("/")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        out = []
        for m in resp.json().get("models", []):
            name = m.get("name") or m.get("model") or ""
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "namespace": name.rsplit("/", 1)[0] if "/" in name else "",
                    "size": int(m.get("size") or 0),
                    "manifest": "",
                    "models_dir": "",
                    "source": "server",
                }
            )
        return out
    except Exception:
        return []


def list_models(url: str | None = None) -> list[dict]:
    """Union of filesystem manifests and the running server's model list."""
    merged: dict[str, dict] = {}
    for m in list_filesystem_models():
        merged[m["name"].lower()] = m
    for m in server_models(url):
        key = m["name"].lower()
        if key in merged:
            merged[key].update({"source": "filesystem+server"})
            if m.get("size"):
                merged[key]["size"] = m["size"]
        else:
            merged[key] = m
    models = list(merged.values())
    models.sort(key=lambda m: m["name"].lower())
    return models


def _count_manifests(models_dir: Path) -> int:
    try:
        roots = _manifest_roots(models_dir)
        if not roots:
            return 0
        return sum(1 for _ in _walk_manifest_files(roots[0], limit=2000))
    except Exception:
        return 0


def is_server_up(url: str | None = None, timeout: float = 2.0) -> bool:
    try:
        return requests.get(f"{(url or get_base_url()).rstrip('/')}/api/tags", timeout=timeout).status_code == 200
    except Exception:
        return False


# ── ollama process ──────────────────────────────────────────────────────────


def find_ollama_exe() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama app.exe")
    candidates += [
        Path(r"C:\Program Files\Ollama\ollama.exe"),
        Path(r"C:\Program Files\Ollama\ollama app.exe"),
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
        Path("/opt/homebrew/bin/ollama"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def ollama_env(models_dir: Path | None = None) -> dict:
    env = os.environ.copy()
    md = models_dir or (find_models_dirs()[0] if find_models_dirs() else None)
    if md:
        env["OLLAMA_MODELS"] = str(md)
        os.environ["OLLAMA_MODELS"] = str(md)
    env.setdefault("OLLAMA_KEEP_ALIVE", "-1")
    return env


def apply_env(models_dir: Path | None = None) -> Path | None:
    """Export OLLAMA_MODELS in this process so child servers inherit it."""
    md = models_dir
    if md is None:
        found = find_models_dirs()
        md = found[0] if found else None
    if md:
        os.environ["OLLAMA_MODELS"] = str(md)
        cfg_dir = load_config().get("ollama_models_dir")
        if not cfg_dir:
            save_config({"ollama_models_dir": str(md)})
        clear_cache()
    return md


def persist_models_dir(models_dir: str | Path) -> tuple[bool, str]:
    """Make OLLAMA_MODELS permanent so the Ollama desktop app finds the models.

    Windows: ``setx OLLAMA_MODELS <path>`` (takes effect after Ollama restarts).
    Linux/macOS: prints the export line to add to the shell profile.
    """
    md = _models_dir_from(Path(str(models_dir)).expanduser()) or Path(str(models_dir))
    if sys.platform == "win32":
        try:
            subprocess.run(["setx", "OLLAMA_MODELS", str(md)],
                           capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            save_config({"ollama_models_dir": str(md)})
            return True, (f"OLLAMA_MODELS = {md}\n"
                          "Close Ollama completely (tray icon → Quit), start it again "
                          "and press SCAN.")
        except Exception as e:
            return False, f"setx failed: {e}"
    return True, (f"Add this to your shell profile, then restart Ollama:\n"
                  f"  export OLLAMA_MODELS=\"{md}\"")


def start_ollama(models_dir: Path | None = None, wait: float = 20.0) -> tuple[bool, str]:
    """Launch ``ollama serve`` with the right model store. Returns (ok, message)."""
    url = get_base_url()
    if is_server_up(url):
        return True, "Ollama already running."

    exe = find_ollama_exe()
    if not exe:
        return False, ("Ollama is not installed (ollama.exe not found). "
                       "Install it from https://ollama.com/download")

    md = models_dir or apply_env()
    env = ollama_env(md)
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # The desktop build ships "ollama app.exe" which starts the server itself
    # (passing an argument to it does nothing useful).
    launch = [exe] if "app.exe" in exe.lower() else [exe, "serve"]
    try:
        subprocess.Popen(launch, **kwargs)
    except Exception as e:
        return False, f"Could not launch Ollama: {e}"

    deadline = time.time() + max(2.0, wait)
    while time.time() < deadline:
        if is_server_up(url):
            return True, f"Ollama started (models: {md or 'default store'})."
        time.sleep(0.75)
    return False, "Ollama did not answer in time. Open the Ollama app and try again."


# ── model selection ─────────────────────────────────────────────────────────


def _score(name: str) -> tuple:
    """Preference order for the automatic model choice.

    1. the user's own  huihui_ai  models first,
    2. uncensored / abliterated variants,
    3. models that are reliable at tool-calling (Qwen, Llama 3.3, Mistral…),
    4. avoid huge models that would take minutes to load,
    5. shortest name as the final tie-breaker.
    """
    low = name.lower()
    return (
        0 if "huihui_ai" in low else 1,
        0 if any(k in low for k in ("abliterated", "uncensored", "dolphin")) else 1,
        0 if any(k in low for k in ("qwen3", "qwen2.5", "qwen2", "llama3.3",
                                    "mistral", "devstral", "gpt-oss")) else 1,
        1 if any(k in low for k in ("70b", "72b", "235b", "405b")) else 0,
        len(low),
    )


def pick_best_model(models: list[dict] | None = None) -> str:
    models = models if models is not None else list_models()
    if not models:
        return ""
    ranked = sorted(models, key=lambda m: _score(m["name"]))
    return ranked[0]["name"]


def active_model() -> str:
    """Configured model, falling back to auto-detection."""
    cfg = load_config()
    model = (cfg.get("llm_model") or cfg.get("local_claude_model") or "").strip()
    available = {m["name"].lower() for m in list_models()}
    if model and (not available or model.lower() in available):
        return model
    if model and not available:
        return model
    picked = pick_best_model()
    return picked or model


def resolve_model(model: str | None = None) -> str:
    if model:
        return model
    cfg = load_config()
    auto = cfg.get("llm_model_auto", True)
    stored = (cfg.get("llm_model") or cfg.get("local_claude_model") or "").strip()
    if stored and not auto:
        return stored
    available = list_models()
    names = {m["name"].lower(): m["name"] for m in available}
    if stored and stored.lower() in names:
        return names[stored.lower()]
    picked = pick_best_model(available)
    return picked or stored


def activate(model: str | None = None, models_dir: str | Path | None = None,
             auto: bool | None = None) -> dict:
    """Make Ollama + ``model`` the active brain of EDIT."""
    md = Path(models_dir) if models_dir else None
    if md:
        md = _models_dir_from(md) or md
    else:
        found = find_models_dirs()
        md = found[0] if found else None

    # The new store must be visible *before* auto-detecting the model.
    if md:
        os.environ["OLLAMA_MODELS"] = str(md)
        save_config({"ollama_models_dir": str(md)})
        clear_cache()

    model = (model or "").strip() or resolve_model()
    auto_val = bool(not model) if auto is None else bool(auto)
    updates = {
        "llm_provider": "ollama",
        "llm_url": get_base_url() or DEFAULT_URL,
        "llm_model": model,
        "llm_model_auto": auto_val,
        "use_local_claude": True,
        "local_claude_base_url": f"{get_base_url() or DEFAULT_URL}/v1",
        "local_claude_model": model,
        "local_claude_api_key": "ollama",
        "local_llm_chat": load_config().get("local_llm_chat", True),
    }
    if md:
        updates["ollama_models_dir"] = str(md)
        os.environ["OLLAMA_MODELS"] = str(md)
    save_config(updates)
    clear_cache()
    return {
        "model": model,
        "models_dir": str(md) if md else "",
        "server_up": is_server_up(),
    }


def test_model(model: str | None = None, timeout: int = 120,
               models_dir: str | Path | None = None) -> dict:
    """Send a one-word request to prove the model really answers."""
    model = resolve_model(model)
    if not model:
        return {"ok": False, "error": "No local model found. Check the models folder.",
                "model": ""}
    url = get_base_url()
    if not is_server_up(url):
        ok, msg = start_ollama(Path(models_dir) if models_dir else None)
        if not ok:
            return {"ok": False, "error": msg, "model": model}
    started = time.time()
    try:
        resp = requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Answer with one word only."},
                    {"role": "user", "content": "Say OK."},
                ],
                "stream": False,
                "keep_alive": -1,
                "options": {"num_predict": 12, "num_gpu": 99},
            },
            timeout=timeout,
        )
        if resp.status_code == 404:
            return {"ok": False, "model": model,
                    "error": (f"Model '{model}' is not in the running Ollama. "
                              "Restart Ollama with the right model folder "
                              "(📌 MAKE PERMANENT) or pick another model."),
                    "ms": int((time.time() - started) * 1000)}
        resp.raise_for_status()
        reply = ((resp.json().get("message") or {}).get("content") or "").strip()
        return {
            "ok": True,
            "model": model,
            "reply": reply or "(model loaded, empty reply)",
            "ms": int((time.time() - started) * 1000),
        }
    except Exception as e:
        return {"ok": False, "model": model,
                "error": f"{type(e).__name__}: {e}", "ms": int((time.time() - started) * 1000)}


def status() -> dict:
    """Everything the UI needs on one screen."""
    cfg = load_config()
    dirs = find_models_dirs()
    fs_models = list_filesystem_models(dirs[0] if dirs else None)
    url = get_base_url()
    up = is_server_up(url)
    server = server_models(url) if up else []
    merged: dict[str, dict] = {m["name"].lower(): m for m in fs_models}
    for m in server:
        merged.setdefault(m["name"].lower(), m)
    models = sorted(merged.values(), key=lambda m: m["name"].lower())
    return {
        "url": url,
        "server_up": up,
        "ollama_exe": find_ollama_exe() or "",
        "models_dirs": [str(d) for d in dirs],
        "models_dir": str(dirs[0]) if dirs else (cfg.get("ollama_models_dir") or ""),
        "models": models,
        "active_model": resolve_model(),
        "provider": (cfg.get("llm_provider") or "ollama"),
        "local_mode": bool(cfg.get("use_local_claude")),
        "auto": bool(cfg.get("llm_model_auto", True)),
        "config_path": str(CONFIG_PATH),
    }


def human_size(size: int) -> str:
    try:
        size = float(size)
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return "—"


if __name__ == "__main__":  # tiny CLI: python -m core.ollama_local
    st = status()
    print(json.dumps(st, indent=2, ensure_ascii=False))
