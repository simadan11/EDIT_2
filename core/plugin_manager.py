"""Plugin manager for EDIT / MARK XL.

A plugin is a folder inside ``plugins/`` with a ``plugin.json`` manifest and a
Python entry file::

    plugins/
      my_plugin/
        plugin.json
        plugin.py

``plugin.json``::

    {
        "name": "my_plugin",              # unique, snake_case
        "title": "My Plugin",             # shown in the UI
        "version": "1.0.0",
        "author": "you",
        "description": "What the plugin does",
        "entry": "plugin.py",
        "enabled": true,
        "tools": [
            {
                "name": "my_tool",
                "description": "What the tool does",
                "parameters": {                # plain JSON-Schema
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]
                }
            }
        ]
    }

The entry module exposes one of:

    def run_tool(tool_name: str, args: dict, player=None) -> str
    def run(args: dict, player=None) -> str          # single-tool plugins

Enabled plugins are injected into the assistant's tool list, so the AI can
call them exactly like built-in actions. Everything is toggled from the
"PLUGIN MANAGER" button in the UI; state is stored in
``config/plugins.json`` (list of disabled plugin names).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import shutil
import sys
import time
import traceback
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = BASE_DIR / "plugins"
STATE_FILE = BASE_DIR / "config" / "plugins.json"
MANIFEST_NAMES = ("plugin.json", "manifest.json")

_state_cache: dict = {}
_module_cache: dict[str, tuple[float, object]] = {}


# ── helpers ─────────────────────────────────────────────────────────────────


def ensure_plugins_dir() -> Path:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    return PLUGINS_DIR


def _load_state() -> dict:
    global _state_cache
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.setdefault("disabled", [])
    if not isinstance(data["disabled"], list):
        data["disabled"] = []
    _state_cache = data
    return data


def _save_state(state: dict) -> None:
    global _state_cache
    _state_cache = state
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=4, ensure_ascii=False),
                              encoding="utf-8")
    except Exception as e:
        print(f"[PLUGINS] Could not save state: {e}")


def is_enabled(name: str) -> bool:
    state = _state_cache or _load_state()
    return str(name).lower() not in {str(x).lower() for x in state.get("disabled", [])}


def _slug(text: str) -> str:
    keep = []
    for ch in str(text).strip().lower().replace("-", "_").replace(" ", "_"):
        if ch.isalnum() or ch in "_.":
            keep.append(ch)
    return "".join(keep) or "plugin"


def _normalize_type(value: str) -> str:
    v = str(value or "string").strip().lower()
    return {
        "object": "object", "string": "string", "integer": "integer",
        "number": "number", "boolean": "boolean", "array": "array",
        "object ": "object", "str": "string", "int": "integer", "bool": "boolean",
        "float": "number", "list": "array", "dict": "object",
    }.get(v, "string")


def normalize_schema(schema: dict | None) -> dict:
    """Accept Gemini-style (OBJECT/STRING) and plain JSON-Schema."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}, "required": []}
    out: dict = {"type": _normalize_type(schema.get("type", "object")),
                 "properties": {}, "required": list(schema.get("required") or [])}
    props = schema.get("properties") or {}
    if isinstance(props, dict):
        for key, spec in props.items():
            spec = spec if isinstance(spec, dict) else {}
            item = {
                "type": _normalize_type(spec.get("type", "string")),
                "description": str(spec.get("description") or ""),
            }
            if item["type"] == "array":
                item["items"] = {"type": _normalize_type(
                    (spec.get("items") or {}).get("type", "string"))}
            if spec.get("enum"):
                item["enum"] = spec["enum"]
            out["properties"][key] = item
    if not out["required"]:
        out["required"] = list(out["properties"].keys())
    return out


def _read_manifest(folder: Path) -> dict | None:
    for name in MANIFEST_NAMES:
        path = folder / name
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data["_manifest_path"] = str(path)
                    return data
            except Exception as e:
                print(f"[PLUGINS] Bad manifest {path}: {e}")
                return None
    return None


def _plugin_specs() -> list[dict]:
    """Raw specs for everything inside plugins/ (no imports)."""
    ensure_plugins_dir()
    specs: list[dict] = []
    try:
        children = sorted(PLUGINS_DIR.iterdir())
    except Exception:
        return specs

    for child in children:
        if child.name.startswith((".", "_")):
            continue
        if child.is_dir():
            manifest = _read_manifest(child)
            if manifest:
                entry = child / (manifest.get("entry") or "plugin.py")
                if not entry.is_file():
                    py = sorted(child.glob("*.py"))
                    entry = py[0] if py else None
                if not entry:
                    continue
                name = _slug(manifest.get("name") or child.name)
                specs.append({
                    "name": name,
                    "title": manifest.get("title") or name.replace("_", " ").title(),
                    "version": str(manifest.get("version") or "0.0.0"),
                    "author": manifest.get("author") or "",
                    "description": manifest.get("description") or "",
                    "entry": entry,
                    "folder": child,
                    "tools": manifest.get("tools") or [],
                    "error": "",
                })
            else:
                # folder without a manifest — single .py plugin
                py = [p for p in sorted(child.glob("*.py")) if not p.name.startswith("_")]
                if len(py) == 1:
                    specs.append(_spec_from_py(py[0]))
        elif child.suffix.lower() == ".py" and not child.name.startswith("_"):
            specs.append(_spec_from_py(child))
    return specs


def _spec_from_py(path: Path) -> dict:
    stem = path.stem
    return {
        "name": _slug(stem),
        "title": stem.replace("_", " ").title(),
        "version": "0.0.0",
        "author": "",
        "description": f"Python plugin {path.name}",
        "entry": path,
        "folder": path.parent,
        "tools": [],
        "error": "",
    }


def _load_module(spec: dict):
    entry: Path = spec["entry"]
    key = str(entry)
    try:
        mtime = entry.stat().st_mtime
    except Exception:
        mtime = 0.0
    cached = _module_cache.get(key)
    if cached and cached[0] >= mtime:
        return cached[1]

    mod_name = f"edit_plugin_{spec['name']}_{abs(hash(key)) & 0xFFFFFF}"
    try:
        module_spec = importlib.util.spec_from_file_location(mod_name, entry)
        if not module_spec or not module_spec.loader:
            raise ImportError(f"cannot load {entry}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[mod_name] = module
        module_spec.loader.exec_module(module)
        _module_cache[key] = (mtime, module)
        return module
    except Exception as e:
        print(f"[PLUGINS] Failed to load {entry}: {e}")
        traceback.print_exc()
        return None


def _module_tools(spec: dict) -> list[dict]:
    """Tool declarations: manifest first, module TOOLS as a fallback."""
    tools = []
    for raw in spec.get("tools") or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        tools.append({
            "name": _slug(raw["name"]),
            "description": str(raw.get("description") or ""),
            "parameters": normalize_schema(raw.get("parameters")),
        })
    if tools:
        return tools
    module = _load_module(spec)
    if module is None:
        return []
    for raw in getattr(module, "TOOLS", []) or []:
        if isinstance(raw, dict) and raw.get("name"):
            tools.append({
                "name": _slug(raw["name"]),
                "description": str(raw.get("description") or ""),
                "parameters": normalize_schema(raw.get("parameters")),
            })
    if not tools and (hasattr(module, "run") or hasattr(module, "run_tool")):
        tools.append({
            "name": spec["name"],
            "description": spec["description"] or spec["title"],
            "parameters": normalize_schema(getattr(module, "PARAMETERS", None)),
        })
    return tools


def list_plugins(include_disabled: bool = True) -> list[dict]:
    _load_state()
    result = []
    for spec in _plugin_specs():
        enabled = is_enabled(spec["name"])
        if not include_disabled and not enabled:
            continue
        tools = _module_tools(spec) if enabled else (spec.get("tools") or [])
        tool_names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
        result.append({
            "name": spec["name"],
            "title": spec["title"],
            "version": spec["version"],
            "author": spec["author"],
            "description": spec["description"],
            "enabled": enabled,
            "path": str(spec["entry"]),
            "folder": str(spec["folder"]),
            "tools": tool_names,
            "tool_count": len(tool_names),
        })
    return result


def set_enabled(name: str, enabled: bool) -> bool:
    state = _load_state()
    disabled = {str(x).lower() for x in state.get("disabled", [])}
    lowered = str(name).lower()
    if enabled:
        disabled.discard(lowered)
    else:
        disabled.add(lowered)
    state["disabled"] = sorted(disabled)
    _save_state(state)
    return enabled


def toggle(name: str) -> bool:
    return set_enabled(name, not is_enabled(name))


def plugin_names() -> list[str]:
    return [spec["name"] for spec in _plugin_specs()]


# ── AI tool bridge ──────────────────────────────────────────────────────────


def get_tool_declarations() -> list[dict]:
    """Gemini-style declarations for every tool of every enabled plugin."""
    decls: list[dict] = []
    for spec in _plugin_specs():
        if not is_enabled(spec["name"]):
            continue
        for tool in _module_tools(spec):
            decls.append({
                "name": tool["name"],
                "description": f"[plugin: {spec['title']}] {tool['description']}".strip(),
                "parameters": {
                    "type": tool["parameters"]["type"].upper(),
                    "properties": {
                        k: {**v, "type": str(v.get("type", "string")).upper()}
                        for k, v in tool["parameters"].get("properties", {}).items()
                    },
                    "required": tool["parameters"].get("required", []),
                },
            })
    return decls


_TOOL_OWNER: dict[str, dict] = {}


def _refresh_tool_owners() -> None:
    _TOOL_OWNER.clear()
    for spec in _plugin_specs():
        if not is_enabled(spec["name"]):
            continue
        for tool in _module_tools(spec):
            _TOOL_OWNER[tool["name"]] = {**spec, "_tools": [tool["name"]]}


def is_plugin_tool(name: str) -> bool:
    _refresh_tool_owners()
    return str(name) in _TOOL_OWNER


def run_plugin_tool(name: str, args: dict | None = None,
                    player=None) -> str:
    """Execute a plugin tool by name. Never raises — returns a message."""
    if not is_plugin_tool(name):
        return f"Plugin tool '{name}' is not available (plugin disabled?)."
    spec = _TOOL_OWNER[str(name)]
    module = _load_module(spec)
    if module is None:
        return f"Plugin '{spec['name']}' failed to load."

    args = dict(args or {})
    started = time.time()
    try:
        if hasattr(module, "run_tool"):
            result = module.run_tool(str(name), args, player)
        elif hasattr(module, "handle"):
            result = module.handle(str(name), args, player)
        elif hasattr(module, "run"):
            run = module.run
            try:
                sig = inspect.signature(run)
                params = list(sig.parameters)
            except Exception:
                params = []
            if len(params) >= 2 and params[0] in ("tool_name", "name", "tool"):
                result = run(str(name), args, player)
            else:
                result = run(args, player)
        else:
            return f"Plugin '{spec['name']}' has no run()/run_tool() function."
        text = str(result) if result is not None else "Done."
    except TypeError:
        # Entry points vary — retry with the plain (args, player) signature.
        try:
            result = module.run(args, player)  # type: ignore[union-attr]
            text = str(result) if result is not None else "Done."
        except Exception as e:
            traceback.print_exc()
            return f"Plugin '{spec['name']}' error: {e}"
    except Exception as e:
        traceback.print_exc()
        return f"Plugin '{name}' failed: {e}"
    ms = int((time.time() - started) * 1000)
    print(f"[PLUGINS] {name} → {text[:100]} ({ms} ms)")
    return text


# ── install / remove ────────────────────────────────────────────────────────


def install_plugin(source: str | os.PathLike) -> tuple[bool, str]:
    """Install a plugin from a .py file, a folder or a .zip archive."""
    src = Path(str(source)).expanduser()
    ensure_plugins_dir()
    if not src.exists():
        return False, f"Not found: {src}"

    try:
        if src.is_dir():
            name = _slug((_read_manifest(src) or {}).get("name") or src.name)
            dest = PLUGINS_DIR / name
            if dest.exists():
                return False, f"Plugin '{name}' is already installed."
            shutil.copytree(src, dest)
            clear_cache()
            return True, f"Installed plugin '{name}'."
        if src.suffix.lower() == ".zip":
            name = _slug(src.stem)
            dest = PLUGINS_DIR / name
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(src) as zf:
                for member in zf.namelist():
                    member_path = (dest / member).resolve()
                    if not str(member_path).startswith(str(dest.resolve())):
                        return False, "Archive contains unsafe paths."
                zf.extractall(dest)
            # zip may contain a single top-level folder
            entries = [p for p in dest.iterdir()]
            if len(entries) == 1 and entries[0].is_dir():
                inner = entries[0]
                for item in inner.iterdir():
                    shutil.move(str(item), str(dest / item.name))
                inner.rmdir()
            clear_cache()
            return True, f"Installed plugin '{name}' from archive."
        if src.suffix.lower() == ".py":
            name = _slug(src.stem)
            dest_dir = PLUGINS_DIR / name
            if dest_dir.exists():
                return False, f"Plugin '{name}' is already installed."
            dest_dir.mkdir(parents=True)
            shutil.copy2(src, dest_dir / "plugin.py")
            manifest = {
                "name": name,
                "title": name.replace("_", " ").title(),
                "version": "0.0.0",
                "description": f"Installed from {src.name}",
                "entry": "plugin.py",
            }
            (dest_dir / "plugin.json").write_text(
                json.dumps(manifest, indent=4, ensure_ascii=False), encoding="utf-8")
            clear_cache()
            return True, f"Installed plugin '{name}'."
        return False, "Unsupported file type (use .py, .zip or a folder)."
    except Exception as e:
        return False, f"Install failed: {e}"


def uninstall_plugin(name: str) -> tuple[bool, str]:
    for spec in _plugin_specs():
        if spec["name"].lower() != str(name).lower():
            continue
        folder = Path(spec["folder"])
        try:
            if folder.is_dir() and folder != PLUGINS_DIR:
                shutil.rmtree(folder)
            elif folder.is_file() and folder.parent == PLUGINS_DIR:
                folder.unlink()
        except Exception as e:
            return False, f"Could not remove plugin: {e}"
        state = _load_state()
        state["disabled"] = [x for x in state.get("disabled", [])
                             if str(x).lower() != str(name).lower()]
        _save_state(state)
        clear_cache()
        return True, f"Plugin '{name}' removed."
    return False, f"Plugin '{name}' not found."


def reload_plugins() -> None:
    clear_cache()


def clear_cache() -> None:
    _module_cache.clear()
    _TOOL_OWNER.clear()


def open_plugins_folder() -> str:
    folder = ensure_plugins_dir()
    try:
        if sys.platform == "win32":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(folder)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        print(f"[PLUGINS] Could not open folder: {e}")
    return str(folder)


def summary() -> dict:
    plugins = list_plugins()
    enabled = [p for p in plugins if p["enabled"]]
    return {
        "total": len(plugins),
        "enabled": len(enabled),
        "tools": sum(p["tool_count"] for p in enabled),
        "folder": str(PLUGINS_DIR),
    }
