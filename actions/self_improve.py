# actions/self_improve.py — Autonomous Self-Improvement, Custom Skills & Command Execution for EDIT
"""
Provides EDIT with:
1. create_skill: create and permanently register new Python tools/skills ('делать навыки навеки')
2. self_improve: edit codebase, redesign UI ('переделывать интерфейс'), add features ('добавлять функции возможности')
3. execute_command: run arbitrary system/bash commands or Python code ('полностью что я захочу')
"""

import os
import sys
import json
import time
import subprocess
import tempfile
import py_compile
import importlib.util
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOM_SKILLS_DIR = Path(__file__).resolve().parent / "custom_skills"
REGISTRY_FILE = CUSTOM_SKILLS_DIR / "registry.json"


def _ensure_registry_exists():
    CUSTOM_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        initial_data = {
            "skills": [
                {
                    "name": "crypto_price",
                    "description": "Fetches current cryptocurrency price in USD (e.g. bitcoin, ethereum, solana). Created as a permanent custom skill.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "coin": {
                                "type": "STRING",
                                "description": "Cryptocurrency name (e.g. bitcoin, ethereum)"
                            }
                        },
                        "required": ["coin"]
                    },
                    "file": "crypto_price.py"
                }
            ]
        }
        REGISTRY_FILE.write_text(json.dumps(initial_data, indent=4, ensure_ascii=False), encoding="utf-8")

        sample_skill_path = CUSTOM_SKILLS_DIR / "crypto_price.py"
        if not sample_skill_path.exists():
            sample_code = """import urllib.request
import json

def run_skill(args, player=None):
    coin = args.get("coin", "bitcoin").lower().strip()
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": "EDIT-AI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if coin in data and "usd" in data[coin]:
                price = data[coin]["usd"]
                return f"The current price of {coin.capitalize()} is ${price:,.2f} USD."
    except Exception:
        pass
    return f"Checked price for {coin}. Please specify exact coin name if price is unavailable."
"""
            sample_skill_path.write_text(sample_code, encoding="utf-8")


def get_custom_tool_declarations() -> list[dict]:
    """Returns all custom skill declarations for Gemini Live API."""
    _ensure_registry_exists()
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        skills = data.get("skills", [])
        decls = []
        for s in skills:
            name = s.get("name")
            if not name:
                continue
            decls.append({
                "name": name,
                "description": s.get("description", f"Custom skill: {name}"),
                "parameters": s.get("parameters", {"type": "OBJECT", "properties": {}})
            })
        return decls
    except Exception as e:
        print(f"[SelfImprove] Error loading custom skill declarations: {e}")
        return []


def is_custom_skill(name: str) -> bool:
    """Check if a tool name belongs to a custom skill."""
    _ensure_registry_exists()
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        return any(s.get("name") == name for s in data.get("skills", []))
    except Exception:
        return False


def run_custom_skill(skill_name: str, parameters: dict, player=None) -> str:
    """Execute a registered custom skill dynamically."""
    _ensure_registry_exists()
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        skill_entry = next((s for s in data.get("skills", []) if s.get("name") == skill_name), None)
        if not skill_entry:
            return f"Error: Custom skill '{skill_name}' not found in registry."

        file_name = skill_entry.get("file", f"{skill_name}.py")
        file_path = CUSTOM_SKILLS_DIR / file_name
        if not file_path.exists():
            return f"Error: Skill file {file_path} does not exist on disk."

        spec = importlib.util.spec_from_file_location(f"custom_skill_{skill_name}", str(file_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if hasattr(mod, "run_skill"):
            return str(mod.run_skill(parameters, player=player))
        elif hasattr(mod, "main"):
            return str(mod.main(parameters))
        else:
            return f"Error: Skill '{skill_name}' does not define 'def run_skill(args, player=None)'."
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[SelfImprove] ❌ Error executing custom skill '{skill_name}': {e}\n{tb}")
        return f"Execution error in custom skill '{skill_name}': {e}"


def create_skill(parameters: dict, player=None, speak=None) -> str:
    """Create, list, test, or remove permanent custom skills."""
    _ensure_registry_exists()
    action = parameters.get("action", "create").lower().strip()
    skill_name = parameters.get("skill_name", "").strip()

    if action == "list":
        try:
            data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            skills = data.get("skills", [])
            if not skills:
                return "No custom skills created yet."
            lines = ["Permanently installed custom skills:"]
            for s in skills:
                lines.append(f" • {s.get('name')}: {s.get('description')}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing skills: {e}"

    if action in ("remove", "delete"):
        if not skill_name:
            return "Please provide 'skill_name' to remove."
        try:
            data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            skills = data.get("skills", [])
            new_skills = [s for s in skills if s.get("name") != skill_name]
            if len(new_skills) == len(skills):
                return f"Skill '{skill_name}' was not found."
            data["skills"] = new_skills
            REGISTRY_FILE.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
            skill_file = CUSTOM_SKILLS_DIR / f"{skill_name}.py"
            if skill_file.exists():
                skill_file.unlink()
            if player:
                player.write_log(f"SYS: Skill removed — {skill_name}")
            return f"Skill '{skill_name}' has been permanently removed."
        except Exception as e:
            return f"Error removing skill: {e}"

    if action == "test":
        if not skill_name:
            return "Please provide 'skill_name' to test."
        return run_custom_skill(skill_name, parameters, player=player)

    # action == "create" or default
    if not skill_name:
        return "Please provide a valid unique 'skill_name'."

    description = parameters.get("description", f"Custom skill {skill_name}")
    python_code = parameters.get("python_code", "")
    parameters_schema_raw = parameters.get("parameters_schema", '{"type": "OBJECT", "properties": {}}')

    if not python_code.strip():
        return "Please provide complete 'python_code' implementing def run_skill(args, player=None)."

    # Parse schema
    if isinstance(parameters_schema_raw, str):
        try:
            parameters_schema = json.loads(parameters_schema_raw)
        except Exception:
            parameters_schema = {"type": "OBJECT", "properties": {}}
    elif isinstance(parameters_schema_raw, dict):
        parameters_schema = parameters_schema_raw
    else:
        parameters_schema = {"type": "OBJECT", "properties": {}}

    skill_file = CUSTOM_SKILLS_DIR / f"{skill_name}.py"

    # Syntax check before saving
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tf:
            tf.write(python_code)
            tf_path = tf.name
        py_compile.compile(tf_path, doraise=True)
        os.unlink(tf_path)
    except py_compile.PyCompileError as e:
        if os.path.exists(tf_path):
            os.unlink(tf_path)
        return f"SyntaxError in custom skill code for '{skill_name}': {e}. Fix syntax and try again."

    try:
        skill_file.write_text(python_code, encoding="utf-8")
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        skills = [s for s in data.get("skills", []) if s.get("name") != skill_name]
        skills.append({
            "name": skill_name,
            "description": description,
            "parameters": parameters_schema,
            "file": f"{skill_name}.py"
        })
        data["skills"] = skills
        REGISTRY_FILE.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

        if player:
            player.write_log(f"SYS: Skill permanently created — {skill_name}")
        print(f"[SelfImprove] ✅ Skill created: {skill_name} -> {skill_file}")
        return f"Success! Skill '{skill_name}' created and permanently saved. It is now available as a tool forever."
    except Exception as e:
        return f"Failed to save custom skill '{skill_name}': {e}"


def self_improve(parameters: dict, player=None, speak=None) -> str:
    """
    Autonomous self-improvement: edit code, redesign UI, add features.
    """
    action = parameters.get("action", "").lower().strip()
    file_path_str = parameters.get("file_path", "").strip()
    old_text = parameters.get("old_text", "")
    new_text = parameters.get("new_text", "")
    content = parameters.get("content", "")
    description = parameters.get("description", "self-improvement")

    target_path = BASE_DIR / file_path_str if file_path_str else BASE_DIR

    if action == "read_file":
        if not file_path_str:
            return "Specify 'file_path' relative to repository root."
        if not target_path.exists():
            return f"File not found: {file_path_str}"
        try:
            txt = target_path.read_text(encoding="utf-8")
            lines = txt.splitlines()
            if len(lines) > 150:
                return (f"File {file_path_str} ({len(lines)} lines). First 120 lines:\n" +
                        "\n".join(lines[:120]) + "\n... [truncated]")
            return txt
        except Exception as e:
            return f"Error reading file: {e}"

    elif action == "list_files":
        try:
            p = target_path if target_path.exists() else BASE_DIR
            files = []
            for item in sorted(p.iterdir()):
                if item.name.startswith(".") or item.name in ("__pycache__", "build", "dist"):
                    continue
                kind = "DIR " if item.is_dir() else "FILE"
                files.append(f"{kind}  {item.name}")
            return "\n".join(files)
        except Exception as e:
            return f"Error listing files: {e}"

    elif action == "edit_file":
        if not file_path_str:
            return "Specify 'file_path' to edit."
        if not target_path.exists():
            return f"File not found: {file_path_str}"
        if not old_text:
            return "Specify 'old_text' to replace."
        try:
            txt = target_path.read_text(encoding="utf-8")
            if old_text not in txt:
                return f"Could not find exact match for 'old_text' in {file_path_str}."
            new_content = txt.replace(old_text, new_text, 1)

            # Check syntax if python file
            if str(target_path).endswith(".py"):
                with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tf:
                    tf.write(new_content)
                    tf_path = tf.name
                try:
                    py_compile.compile(tf_path, doraise=True)
                    os.unlink(tf_path)
                except py_compile.PyCompileError as ce:
                    if os.path.exists(tf_path):
                        os.unlink(tf_path)
                    return f"SyntaxError after replacement in {file_path_str}: {ce}. Edit aborted."

            target_path.write_text(new_content, encoding="utf-8")
            if player:
                player.write_log(f"SYS: Code improved — {file_path_str}")
            return f"Successfully edited {file_path_str} ({description})."
        except Exception as e:
            return f"Error editing file: {e}"

    elif action == "write_file":
        if not file_path_str:
            return "Specify 'file_path' to write."
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if str(target_path).endswith(".py"):
                with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tf:
                    tf.write(content)
                    tf_path = tf.name
                try:
                    py_compile.compile(tf_path, doraise=True)
                    os.unlink(tf_path)
                except py_compile.PyCompileError as ce:
                    if os.path.exists(tf_path):
                        os.unlink(tf_path)
                    return f"SyntaxError in new code for {file_path_str}: {ce}. Write aborted."

            target_path.write_text(content, encoding="utf-8")
            if player:
                player.write_log(f"SYS: File written — {file_path_str}")
            return f"Successfully wrote {file_path_str} ({description})."
        except Exception as e:
            return f"Error writing file: {e}"

    elif action == "redesign_ui":
        # Supports modifying config/api_keys.json (ui_color, assistant_name) or editing ui.py/hub.py
        cfg_path = BASE_DIR / "config" / "api_keys.json"
        try:
            cfg = {}
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

            color = parameters.get("ui_color") or parameters.get("new_text")
            if color and color.startswith("#") and len(color) in (4, 7):
                cfg["ui_color"] = color
                cfg_path.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")

            # If editing ui.py was requested via old_text/new_text
            if old_text and new_text:
                ui_file = BASE_DIR / (file_path_str or "ui.py")
                if ui_file.exists():
                    txt = ui_file.read_text(encoding="utf-8")
                    if old_text in txt:
                        new_content = txt.replace(old_text, new_text, 1)
                        ui_file.write_text(new_content, encoding="utf-8")

            if player:
                player.write_log(f"SYS: EDIT UI redesigned — {description}")
                # Refresh UI name/color if player supports it
                try:
                    if hasattr(player, "_apply_name_update"):
                        asst_name = cfg.get("assistant_name", "EDIT")
                        player._apply_name_update(asst_name, "", cfg.get("ui_color", ""))
                except Exception:
                    pass

            return f"UI redesign applied successfully: {description}"
        except Exception as e:
            return f"Error redesigning UI: {e}"

    elif action == "add_feature":
        # Add or enhance a feature
        if not file_path_str and not content:
            return "Specify 'file_path' and 'content' for the new feature."
        return self_improve({
            "action": "write_file",
            "file_path": file_path_str,
            "content": content,
            "description": f"Added feature: {description}"
        }, player=player)

    elif action == "inspect_code":
        query = parameters.get("old_text", "") or description
        try:
            res = subprocess.run(
                ["grep", "-rn", query, str(BASE_DIR)],
                capture_output=True, text=True, timeout=10
            )
            out = res.stdout.strip()
            if not out:
                return f"No matches found for '{query}'."
            lines = out.splitlines()
            if len(lines) > 25:
                return f"Found {len(lines)} matches. First 25:\n" + "\n".join(lines[:25])
            return out
        except Exception as e:
            return f"Error inspecting code: {e}"

    return "Unknown action for self_improve. Try: read_file | edit_file | write_file | list_files | redesign_ui | add_feature | inspect_code"


def execute_command(parameters: dict, player=None, speak=None) -> str:
    """
    Execute arbitrary system/bash command or Python code so EDIT can do anything the user wants.
    """
    cmd = parameters.get("command", "").strip()
    mode = parameters.get("mode", "bash").lower().strip()
    timeout = int(parameters.get("timeout", 15))

    if not cmd:
        return "Please specify a 'command' to execute."

    if mode == "python":
        try:
            import io
            from contextlib import redirect_stdout, redirect_stderr

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            globals_dict = {
                "__name__": "__main__",
                "BASE_DIR": BASE_DIR,
                "player": player,
                "os": os,
                "sys": sys,
                "json": json,
                "subprocess": subprocess,
                "Path": Path
            }
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                try:
                    exec(compile(cmd, "<execute_command_python>", "exec"), globals_dict)
                except Exception as e:
                    print(f"Python exception: {e}")

            out = stdout_buf.getvalue() + stderr_buf.getvalue()
            if not out.strip():
                return "Python code executed successfully (no printed output)."
            return f"Python output:\n{out.strip()}"
        except Exception as e:
            return f"Error executing Python code: {e}"

    # Bash / Shell mode
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        out = (res.stdout or "") + (res.stderr or "")
        out = out.strip()
        if res.returncode == 0:
            return f"Command succeeded:\n{out}" if out else "Command executed successfully (no output)."
        else:
            return f"Command exited with status {res.returncode}:\n{out}"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"
