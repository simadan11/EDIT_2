import os as _os
import platform as _platform
import subprocess as _subprocess
import sys as _sys

# ── Prevent Qt DPI awareness warnings on Windows BEFORE any Qt library is loaded ──
_os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
_os.environ["QT_QPA_PLATFORM"] = "windows:dpiawareness=0"
_os.environ["QT_SCALE_FACTOR"] = "1"
_os.environ["QT_FONT_DPI"] = "96"

# ── Force UTF-8 and replace errors on Windows to prevent cp1251 UnicodeDecodeError ──
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(_sys.stderr, "reconfigure"):
    try:
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
_os.environ["PYTHONIOENCODING"] = "utf-8"

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen
    _OrigRun = _subprocess.run

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            if kw.get("text", False) or kw.get("universal_newlines", False):
                kw.setdefault("encoding", "utf-8")
                kw.setdefault("errors", "replace")
            super().__init__(args, **kw)

    def _safe_run(*args, **kw):
        if kw.get("text", False) or kw.get("universal_newlines", False):
            kw.setdefault("encoding", "utf-8")
            kw.setdefault("errors", "replace")
        return _OrigRun(*args, **kw)

    _subprocess.Popen = _Popen
    _subprocess.run = _safe_run
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import queue
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from openai import OpenAI as _OpenAIClient   # для локального Claude / Ollama
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.social_osint      import social_osint
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.headphones import HeadphonesManager
from actions.tunnel import TunnelManager
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import get_brief_enabled
from core.model_router         import print_model_status, is_local_mode


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

LIVE_MODEL_CANDIDATES = [
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
    "models/gemini-2.0-flash-realtime-exp",
    "gemini-2.0-flash-realtime-exp",
    "models/gemini-2.0-flash-live-001",
    "gemini-2.0-flash-live-001",
    "models/gemini-2.0-flash",
    "gemini-2.0-flash",
    "models/gemini-2.5-flash-live",
    "gemini-2.5-flash-live",
    "models/gemini-2.5-flash",
    "gemini-2.5-flash",
    "models/gemini-3-flash-preview",
    "gemini-3-flash-preview",
    "models/gemini-2.0-flash-exp",
]

def get_current_live_model(idx: int = 0) -> str:
    try:
        _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
        custom_model = (_cfg.get("live_model") or "").strip()
        if custom_model and idx == 0:
            return custom_model
    except Exception:
        pass
    return LIVE_MODEL_CANDIDATES[idx % len(LIVE_MODEL_CANDIDATES)]

def save_connected_live_model(model_name: str) -> None:
    try:
        if API_CONFIG_PATH.exists():
            _cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
            if _cfg.get("live_model") != model_name:
                _cfg["live_model"] = model_name
                API_CONFIG_PATH.write_text(json.dumps(_cfg, indent=4, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        return cfg.get("gemini_api_key", "")

def _get_local_claude_config() -> dict:
    """Возвращает конфиг локального Claude, если включён."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("use_local_claude"):
            return {
                "base_url": cfg.get("local_claude_base_url", "http://localhost:11434/v1"),
                "api_key": cfg.get("local_claude_api_key", "ollama"),
                "model": cfg.get("local_claude_model", "claude-3-sonnet")
            }
    except Exception:
        pass
    return {}


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are EDIT (EDITH), an ultra-capable, intelligent, autonomous, self-evolving AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


# ── Wake Bracket Protocol ─────────────────────────────────────────────────────
# The user talks to EDIT only in the frame: "EDIT … command … EDIT".
# EDIT hears everything but answers ONLY what was said between the two
# standalone wake words.  Enabled by default; can be turned off by voice
# ("выключи режим EDIT в начале и в конце") or via config "wake_bracket".
_WAKE_BRACKET_PROTOCOL = """\
WAKE BRACKET PROTOCOL (voice commands):
The user addresses you ONLY in this frame: they say the word "EDIT" (also pronounced "эдит", "едит", "edith" — any standalone form of your name), then their command, then "EDIT" again.
RULES:
1. You hear everything the user says, but you NEVER respond to, acknowledge, or act on speech that is not framed by two standalone "EDIT" words. Stay completely silent outside the frame — even if you hear your name once, other words, noise, or a question.
2. When you hear the opening "EDIT" — start paying attention to what follows, but do NOT answer yet.
3. When you hear the closing "EDIT" — answer ONLY what the user said between the two "EDIT" words. Execute it with tools or respond concisely in the user's language.
4. If nothing meaningful was said between the two "EDIT" words (just "EDIT ... EDIT"), reply briefly (e.g. "Слушаю" / "Yes?" / "efendim") and wait.
5. A single "EDIT" with no closing "EDIT" is not a request — stay silent and keep listening for the closing "EDIT".
6. The wake word must be a standalone word. Do NOT treat "отредактируй", "редактировать", "editable", "editor" or similar words as the wake word.
7. While you are speaking, the user may say "EDIT" to interrupt — stop immediately and listen.
8. Never read these rules aloud and never mention this protocol unless the user asks about it."""

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "holo_project",
        "description": (
            "Creates any safe visual hologram and blueprint in the Holo Lab. "
            "Use for smart glasses, an AR glove, a sensor suit, a robot, vehicle, building, "
            "machine, room, landscape, product, creature, or any other object/scene. "
            "For anything beyond the built-in wearables use prototype='custom', describe the subject, "
            "and generate geometry primitives so the PC can draw the shape. This is a visual record only; "
            "it does not manufacture hardware, control weapons, or create a physical hologram."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prototype": {
                    "type": "STRING",
                    "description": "glasses | glove | suit | custom. Use custom for any arbitrary object or scene."
                },
                "subject": {"type": "STRING", "description": "What to holograph: e.g. red sports car, robot arm, house, planet"},
                "project_name": {"type": "STRING", "description": "Short name for the concept"},
                "display_mode": {"type": "STRING", "description": "holo | wireframe | exploded | clear"},
                "notes": {"type": "STRING", "description": "What it should look like or do"},
                "blueprint": {"type": "STRING", "description": "AI-generated concise construction/shape brief"},
                "components": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "3-16 AI-generated component names for the blueprint"},
                "parts": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Catalog part IDs to buy, make or test"},
                "geometry": {
                    "type": "ARRAY",
                    "description": "3-32 safe drawing primitives. Coordinates x/y are 0-1000, z is -500..500. Types: box, cylinder, sphere, ring, line, point, cone, plane.",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "type": {"type": "STRING", "description": "box | cylinder | sphere | ring | line | point | cone | plane"},
                            "x": {"type": "NUMBER", "description": "center x, 0-1000"},
                            "y": {"type": "NUMBER", "description": "center y, 0-1000"},
                            "z": {"type": "NUMBER", "description": "depth, -500..500"},
                            "w": {"type": "NUMBER", "description": "width or radius, 4-700"},
                            "h": {"type": "NUMBER", "description": "height or radius, 4-700"},
                            "d": {"type": "NUMBER", "description": "depth, 0-500"},
                            "x2": {"type": "NUMBER", "description": "line endpoint x"},
                            "y2": {"type": "NUMBER", "description": "line endpoint y"},
                            "z2": {"type": "NUMBER", "description": "line endpoint z"},
                            "rotation": {"type": "NUMBER", "description": "rotation in degrees"},
                            "scale": {"type": "NUMBER", "description": "visual scale 0.05-10"},
                            "mm_w": {"type": "NUMBER", "description": "approximate real width in millimeters; verify datasheet"},
                            "mm_h": {"type": "NUMBER", "description": "approximate real height in millimeters; verify datasheet"},
                            "mm_d": {"type": "NUMBER", "description": "approximate real depth in millimeters; verify datasheet"},
                            "part_id": {"type": "STRING", "description": "catalog part ID if this primitive represents a selected part"},
                            "label": {"type": "STRING", "description": "short part label"},
                        },
                        "required": ["type", "x", "y"]
                    }
                },
                "clarity": {"type": "INTEGER", "description": "Visual contrast from 35 to 100"},
            },
            "required": ["prototype"]
        }
    },
    {
        "name": "holo_diagnose",
        "description": (
            "Diagnoses a Holo Lab build or prototype when the user says it does not work. "
            "Give the symptom and return likely problems, fixes and safe bench tests; never tell the user to bypass battery protection."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symptom": {"type": "STRING", "description": "What is wrong: black screen, camera, heat, reset, battery, Wi-Fi, printer, etc."},
                "parts": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Optional catalog part IDs in the build"},
            },
            "required": ["symptom"]
        }
    },
    {
        "name": "print_holo_blueprint",
        "description": (
            "Prints the current Holo Lab blueprint, AI component schedule, selected buy/make BOM and diagnostics using the PC printer dialog. "
            "Use when the user explicitly asks to print the hologram scheme or blueprint."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "remember_face",
        "description": (
            "Learns the face currently visible on the live phone camera and "
            "links it to a name, so the HUD labels that person from then on. "
            "Call when the user says: remember this face, remember me, this is "
            "<name>, save my face, запомни это лицо, запомни меня, это <имя>."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING",
                         "description": "Name to attach to the visible face"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "EDIT checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "headphones_mode",
        "description": (
            "Toggles Headphones Mode (режим наушников): EDIT's voice is routed "
            "through Bluetooth headphones connected to the PC, the microphone is "
            "routed through the headset mic, and pressing the button on the "
            "headphones makes EDIT stop talking and listen (push-to-listen). "
            "Call when the user says: headphones mode on/off, включи/выключи "
            "режим наушников, наушники, bluetooth headphones, listen through "
            "headphones, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True to enable, false to disable. Omit to just check status."
                },
                "status_only": {
                    "type": "BOOLEAN",
                    "description": "True to only report whether Bluetooth headphones are connected and whether the mode is on (no change)."
                }
            },
            "required": []
        }
    },
    {
        "name": "wake_protocol",
        "description": (
            "Toggles the WAKE BRACKET PROTOCOL: EDIT only answers voice commands "
            "that are framed between two standalone 'EDIT' words — the user says "
            "'EDIT', then the command, then 'EDIT' again, and EDIT responds to what "
            "was said between them. Call when the user says: режим EDIT в начале и "
            "в конце, надо сказать EDIT в начале и в конце, отвечай только когда "
            "скажу EDIT, только между EDIT, включи/выключи wake protocol, "
            "включи/выключи протокол вызова, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True to enable the bracket protocol, false to disable it and respond normally."
                }
            },
            "required": []
        }
    },
    {
        "name": "tts_voice",
        "description": (
            "Toggles the TTS VOICE MODULE. When ON (default), EDIT's replies "
            "are voiced by the dedicated TTS module (EdgeTTS/Kokoro on the PC, "
            "or the phone's own speech synthesis in phone headphones mode) — "
            "the AI's audio is not used, so there is never a doubled voice. "
            "When OFF, Gemini speaks directly. Call when the user says: "
            "включи/выключи озвучку, TTS модуль, голос TTS, чтобы не ИИ "
            "говорил, модуль озвучки, tts voice, speak via tts, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True to voice replies through the TTS module, false for the AI's direct voice."
                }
            },
            "required": []
        }
    },
    {
        "name": "internet_access",
        "description": (
            "Toggles a public HTTPS tunnel (Cloudflare/ngrok) so EDIT's Remote "
            "Dashboard is reachable from the phone over MOBILE DATA when there "
            "is no WiFi (e.g. away from home). Provides an internet URL like "
            "https://xxx.trycloudflare.com with the same dashboard: headphones "
            "mode 🎧, voice, EDITH camera. Call when the user says: интернет "
            "доступ, доступ через интернет, мобильный интернет, туннель, "
            "чтобы работало не дома, internet access, tunnel, remote from "
            "anywhere, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True to start the tunnel, false to stop it. Omit to toggle."
                },
                "status_only": {
                    "type": "BOOLEAN",
                    "description": "True to only report the current status (no change)."
                }
            },
            "required": []
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "self_improve",
        "description": (
            "Autonomous self-improvement and self-modification tool for EDIT. "
            "Use this to improve your own codebase, redesign or modify your UI interface ('переделывать интерфейс', ui.py, hub.py, styles, colors), "
            "add new functions and capabilities ('добавлять функции возможности'), or read/write/edit any file in the project."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "read_file | edit_file | write_file | list_files | redesign_ui | add_feature | inspect_code"
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Relative file path in repository (e.g. 'ui.py', 'main.py', 'actions/weather_report.py')"
                },
                "old_text": {
                    "type": "STRING",
                    "description": "Exact or fuzzy text to search and replace (for edit_file)"
                },
                "new_text": {
                    "type": "STRING",
                    "description": "New text to replace old_text with (for edit_file)"
                },
                "content": {
                    "type": "STRING",
                    "description": "Full file content (for write_file)"
                },
                "description": {
                    "type": "STRING",
                    "description": "Human-readable description of the improvement or UI redesign"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "create_skill",
        "description": (
            "Creates and permanently registers a new custom skill/tool for EDIT ('делать навыки навеки'). "
            "Saves the skill as a Python file in actions/custom_skills/ and registers it in the dynamic tool registry, "
            "making it permanently available to you across all future sessions."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "create | list | remove | test (default: create)"
                },
                "skill_name": {
                    "type": "STRING",
                    "description": "Unique snake_case skill name (e.g. 'crypto_price', 'spotify_control')"
                },
                "description": {
                    "type": "STRING",
                    "description": "Description of what the skill does (used as the tool description)"
                },
                "parameters_schema": {
                    "type": "STRING",
                    "description": "JSON string describing tool parameters schema (type, properties, required)"
                },
                "python_code": {
                    "type": "STRING",
                    "description": "Complete Python code defining 'def run_skill(args, player=None): ...'"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "execute_command",
        "description": (
            "Executes arbitrary system/bash/terminal commands or Python scripts so EDIT can do absolutely anything the user wants ('полностью что я захочу'). "
            "Can run terminal utilities, scripts, file commands, network inspections, package installations, or dynamic Python evaluation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": "Terminal command string or Python code to execute"
                },
                "mode": {
                    "type": "STRING",
                    "description": "bash | python (default: bash)"
                },
                "timeout": {
                    "type": "INTEGER",
                    "description": "Timeout in seconds (default: 15)"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "geoint_lookup",
        "description": (
            "Maximum GEOINT (Geospatial Intelligence) tool for EDIT. "
            "Searches, analyzes, and displays active, abandoned, and historical military bases, airfields, radar sites (e.g. Duga), "
            "bunkers, naval ports, and equipment locations on interactive maps (Google Maps, Google Satellite, OSM). "
            "Always use this when the user asks about Google Maps, military sites, abandoned bases, satellite imagery, or GEOINT."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Name of military base, country, keyword, or coordinates (e.g. 'Ramstein', 'Duga', 'abandoned bunkers')"
                },
                "category": {
                    "type": "STRING",
                    "description": "all | active | abandoned | radar | airbase | bunker | historic (default: all)"
                },
                "country": {
                    "type": "STRING",
                    "description": "all | ukraine | russia (default: all)"
                },
                "open_map": {
                    "type": "BOOLEAN",
                    "description": "Set True to automatically launch the interactive GEOINT map or browser"
                },
                "calc_distance_to": {
                    "type": "STRING",
                    "description": "Optional second base/location name to compute geodesic distance and bearing"
                },
                "ai_assess": {
                    "type": "BOOLEAN",
                    "description": "Set True to generate an AI GEOINT strategic assessment report"
                }
            },
            "required": ["query"]
        }
    },
]

# --- Dynamic Skills & Self-Improvement System ---
def get_all_tool_declarations() -> list[dict]:
    try:
        from actions.self_improve import get_custom_tool_declarations
        custom_decls = get_custom_tool_declarations()
    except Exception as e:
        print(f"[EDIT] Warning loading custom tool declarations: {e}")
        custom_decls = []
    return TOOL_DECLARATIONS + custom_decls


class _TTSBridge:
    """
    Dedicated TTS voice module (EdgeTTS / Kokoro / ElevenLabs).

    Two modes:
      • stream_cb=None  — voices text through the engine's own player on a
        strict FIFO worker thread (PC speakers).
      • stream_cb=fn    — JARVIS VOICE MODULE: synthesises with a deep
        Russian male EdgeTTS voice ("ru-RU-DmitryNeural") and streams the PCM
        to the phone's single audio-sink tab (Bluetooth headphones), so the
        phone gets a proper Jarvis-quality neural voice.

    Either way the AI (Gemini Live) does NOT speak — exactly one voice.
    """

    def __init__(self, stream_cb=None, voice: str = "ru-RU-DmitryNeural"):
        self._q = queue.Queue()
        self._player = None
        self._lock = threading.Lock()
        self._stream_cb = stream_cb          # callable(bytes) → dashboard sink
        self._voice = voice or "ru-RU-DmitryNeural"
        self._streaming = False              # True while streaming to the phone
        self._cancelled = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="TTS-Voice"
        )
        self._thread.start()

    def speak(self, text: str) -> None:
        """Enqueue text (non-blocking, strict FIFO)."""
        text = (text or "").strip()
        if text:
            try:
                self._q.put_nowait(text)
            except queue.Full:
                pass

    @property
    def playing(self) -> bool:
        """True while the TTS engine is actually speaking (mic echo guard)."""
        with self._lock:
            if self._stream_cb is not None:
                return self._streaming
            try:
                return bool(self._player and self._player.is_playing)
            except Exception:
                return False

    def clear(self) -> None:
        """Stop current playback/streaming and drop queued text (interrupt)."""
        with self._lock:
            self._cancelled = True
            if self._player:
                try:
                    self._player.stop()
                except Exception:
                    pass
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _run(self) -> None:
        while True:
            text = self._q.get()
            if text is None:
                break
            self._cancelled = False          # fresh utterance
            try:
                if self._stream_cb is not None:
                    self._speak_stream(text)
                    continue
                if self._player is None:
                    import json as _json
                    from core.tts import create_tts_player
                    cfg = {}
                    try:
                        cfg = _json.loads(
                            open(API_CONFIG_PATH, encoding="utf-8").read()
                        )
                    except Exception:
                        pass
                    self._player = create_tts_player(cfg)
                with self._lock:
                    if self._player:
                        self._player.speak(text)
            except Exception as e:
                print(f"[TTS] Voice module error: {e}")

    # ── Jarvis voice streaming (phone headphones mode) ────────────────────

    def _speak_stream(self, text: str) -> None:
        """Synthesise on the PC with the Jarvis voice and stream PCM (24 kHz
        int16 mono) to the phone sink via stream_cb — never played on the PC."""
        with self._lock:
            self._streaming = True
        try:
            import asyncio as _aio
            import edge_tts
            import miniaudio
            import numpy as np

            loop = _aio.new_event_loop()
            try:
                audio = loop.run_until_complete(self._synth_async(text, edge_tts))
            finally:
                loop.close()
            if not audio or self._cancelled:
                return

            decoded = miniaudio.decode(
                audio,
                output_format=miniaudio.SampleFormat.FLOAT32,
                nchannels=1,
            )
            samples = np.asarray(decoded.samples, dtype=np.float32)
            sr = int(decoded.sample_rate) or 24000
            if sr != 24000 and len(samples):
                n = int(len(samples) * 24000 / sr)
                xo = np.linspace(0, 1, len(samples), endpoint=False)
                xn = np.linspace(0, 1, n, endpoint=False)
                samples = np.interp(xn, xo, samples).astype(np.float32)
            pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
            # ~50 ms slices — matches the phone player's PCM framing
            for i in range(0, len(pcm), 2400):
                if self._cancelled:
                    break
                if self._stream_cb is not None:
                    try:
                        self._stream_cb(pcm[i : i + 2400])
                    except Exception:
                        break
        except Exception as e:
            print(f"[Jarvis] Voice stream error: {e}")
        finally:
            with self._lock:
                self._streaming = False

    async def _synth_async(self, text: str, edge_tts) -> bytes:
        comm = edge_tts.Communicate(text, self._voice)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "EDIT"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._phone_headphones_active = False  # True when a phone runs Headphones Mode → PC speaker muted
        self._last_frame: bytes | None = None   # newest live camera frame
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None

        # ── Headphones mode (🎧) ──────────────────────────────────────────
        # Audio devices used by the live mic/playback streams: (input_idx, output_idx).
        # None = OS default.  Bumped every time the routing changes so the
        # _listen_audio / _play_audio loops reopen their streams.
        self._headphones    = HeadphonesManager(
            on_button=self._on_headphone_button,
            on_status_changed=self._on_headphone_status_changed,
        )
        self._audio_devices = (None, None)
        self._audio_gen     = 0
        self.ui.on_headphones_toggle = self._ui_toggle_headphones

        # ── TTS voice module (🎙) ───────────────────────────────────────────
        # PC: the AI speaks as always (its own audio plays through the PC).
        # Phone headphones mode: the AI's audio is discarded and the reply is
        # voiced by the Jarvis Voice Module (EdgeTTS, deep British male)
        # streamed to the phone's headphones — exactly one voice.
        self._pc_tts       = None          # local PC TTS bridge (optional mode)
        self._jarvis_tts   = None          # Jarvis voice streaming bridge (phone)

        # ── Internet tunnel (🌐) — access EDIT over mobile data ────────────
        self._tunnel      = TunnelManager(
            port=8000, static_url=TunnelManager.static_url()
        )
        self.ui.on_internet_toggle = self._ui_toggle_internet
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        self._cancel_tts()          # TTS voice module stops instantly
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    # ── Headphones mode (🎧) ────────────────────────────────────────────────
    # Flow: the UI toggle or the Remote Dashboard button calls
    #   _ui_toggle_headphones / _dashboard_headphones_cb → _headphones_toggle_task.
    # Routing is applied by HeadphonesManager (sd.default + device indices),
    # the live streams reopen via self._audio_gen, and the headphone's own
    # multifunction button triggers _on_headphone_button → interrupt + listen.

    def _ui_toggle_headphones(self) -> None:
        """Called from the Qt thread when the 🎧 button is pressed."""
        if not self._loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._headphones_toggle_task(), self._loop
            )
        except Exception as e:
            print(f"[Headphones] UI toggle error: {e}")

    async def _dashboard_headphones_cb(self, enabled: bool | None = None) -> dict:
        """Async callback for the Remote Dashboard /api/headphones endpoint."""
        if enabled is None:
            return self._headphones.status()
        return await self._headphones_toggle_task(bool(enabled))

    async def _headphones_toggle_task(self, enabled: bool | None = None) -> dict:
        """Turn headphone mode on/off and push the new status everywhere."""
        try:
            if enabled is None:
                enabled = not self._headphones.enabled
            status = await asyncio.to_thread(
                self._headphones.set_enabled, bool(enabled)
            )
            self._apply_headphone_audio(status)
            self.ui.update_headphones_btn(status)

            if enabled:
                if status.get("connected"):
                    name = status.get("name") or "Bluetooth headphones"
                    self.ui.write_log(f"🎧 Headphones mode: ON — {name}")
                else:
                    self.ui.write_log(
                        "🎧 Headphones mode: ON — Bluetooth headphones not "
                        "detected, audio stays on the default devices"
                    )
            else:
                self.ui.write_log(
                    "🎧 Headphones mode: OFF — audio back to default devices"
                )

            # Persist the preference so it survives restarts
            self._save_headphones_pref(bool(enabled))

            if self._dashboard:
                try:
                    await self._dashboard.broadcast(
                        {"type": "headphones", "status": status}
                    )
                except Exception:
                    pass
            return status
        except Exception as e:
            print(f"[Headphones] Toggle error: {e}")
            traceback.print_exc()
            return self._headphones.status()

    def _apply_headphone_audio(self, status: dict | None = None) -> None:
        """Point the live mic/playback streams at the headphone devices."""
        if status is None:
            status = self._headphones.status()
        if status.get("enabled") and status.get("connected"):
            self._audio_devices = (
                status.get("input_idx"),
                status.get("output_idx"),
            )
        else:
            self._audio_devices = (None, None)
        self._audio_gen += 1   # _listen_audio / _play_audio reopen their streams

    def _save_headphones_pref(self, enabled: bool) -> None:
        try:
            with open(API_CONFIG_PATH, "r+", encoding="utf-8") as f:
                cfg = json.load(f)
                cfg["headphones_mode"] = bool(enabled)
                f.seek(0)
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.truncate()
        except Exception:
            pass

    def _load_headphones_pref(self) -> bool:
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                return bool(json.load(f).get("headphones_mode", False))
        except Exception:
            return False

    def _wake_bracket_enabled(self) -> bool:
        """Wake Bracket Protocol — 'EDIT … command … EDIT' framing."""
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                return bool(json.load(f).get("wake_bracket", True))
        except Exception:
            return True

    def _tts_voice_mode(self) -> bool:
        """TTS voice module on the PC: replies voiced by the PC TTS engine.
        Default OFF — the PC speaks with the AI's own voice as always."""
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                return bool(json.load(f).get("tts_voice_mode", False))
        except Exception:
            return False

    def _jarvis_voice(self) -> str:
        """EdgeTTS voice used by the Jarvis Voice Module (phone headphones)."""
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                return str(
                    json.load(f).get("tts_jarvis_voice", "ru-RU-DmitryNeural")
                ).strip() or "ru-RU-DmitryNeural"
        except Exception:
            return "ru-RU-DmitryNeural"

    def _save_tts_voice_mode(self, enabled: bool) -> None:
        try:
            with open(API_CONFIG_PATH, "r+", encoding="utf-8") as f:
                cfg = json.load(f)
                cfg["tts_voice_mode"] = bool(enabled)
                f.seek(0)
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.truncate()
        except Exception:
            pass

    def _save_wake_bracket(self, enabled: bool) -> None:
        try:
            with open(API_CONFIG_PATH, "r+", encoding="utf-8") as f:
                cfg = json.load(f)
                cfg["wake_bracket"] = bool(enabled)
                f.seek(0)
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.truncate()
        except Exception:
            pass

    def _on_headphone_button(self) -> None:
        """Headphone multifunction button pressed (AVRCP play/pause).

        EDIT stops talking and opens the mic — push-to-listen through the
        headset.  Runs in the keyboard-hook thread; marshal into the loop.
        """
        if not self._headphones.enabled or not self._loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._headphone_button_task(), self._loop
            )
        except Exception as e:
            print(f"[Headphones] Button relay error: {e}")

    async def _headphone_button_task(self) -> None:
        with self._speaking_lock:
            was_speaking = self._is_speaking
        turn_already_done = bool(
            self._turn_done_event and self._turn_done_event.is_set()
        )
        audio_in_flight = bool(
            self.audio_in_queue is not None and not self.audio_in_queue.empty()
        )
        self.interrupt()

        # interrupt() sets self._interrupted so the in-flight response audio is
        # discarded until the interrupted turn completes.  But if EDIT was
        # silent (or that turn already completed) no new turn_complete will
        # arrive, and a stuck flag would mute the user's NEXT reply — so clear
        # it right away in that case.  For a genuine mid-turn interrupt we keep
        # discarding (fast path: turn_complete clears the flag) with a 6 s
        # safety net in case Gemini never finishes the turn.
        if not was_speaking and not audio_in_flight:
            self._interrupted = False
        elif turn_already_done:
            self._interrupted = False
        else:
            async def _safety_clear():
                await asyncio.sleep(6.0)
                self._interrupted = False
            asyncio.create_task(_safety_clear())

        self.ui.write_log("🎧 Headphone button — EDIT listening…")
        if self._dashboard:
            try:
                await self._dashboard.broadcast(
                    {"type": "headphones", "action": "listen"}
                )
            except Exception:
                pass

    async def _on_phone_headphone_button(self) -> None:
        """Phone-side headphone button pressed (headphones on the phone).

        The Remote Dashboard catches the AVRCP play/pause press via the
        browser mediaSession API and calls /api/headphones/button → here:
        EDIT stops talking so the phone-mic stream (= headset mic) is heard.
        """
        self.interrupt()
        self.ui.write_log("🎧 Headphone button (phone) — EDIT listening…")
        if self._dashboard:
            try:
                await self._dashboard.broadcast(
                    {"type": "headphones", "action": "listen"}
                )
            except Exception:
                pass

    async def _set_phone_headphones_mode(self, active: bool) -> dict:
        """Phone reported its Headphones Mode turned on/off.

        While ON, EDIT's voice plays ONLY through the phone (= the Bluetooth
        headphones connected to it) via the phone's own TTS module, and the
        PC speaker is muted — otherwise the user hears two voices.
        """
        self._phone_headphones_active = bool(active)
        self._audio_gen += 1   # _play_audio reopens (or skips) the PC stream
        if active:
            self.ui.write_log(
                "🎧 Phone headphones mode ON — EDIT speaks only through the "
                "phone (PC speakers muted)"
            )
        else:
            self.ui.write_log(
                "🎧 Phone headphones mode OFF — PC audio restored"
            )
        return {"enabled": self._phone_headphones_active}

    def _on_headphone_status_changed(self, status: dict) -> None:
        """Called by HeadphonesManager's monitor when BT devices (dis)connect."""
        try:
            self._apply_headphone_audio(status)
            self.ui.update_headphones_btn(status)
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_headphone_status(status), self._loop
                )
        except Exception as e:
            print(f"[Headphones] Status change error: {e}")

    async def _broadcast_headphone_status(self, status: dict) -> None:
        if self._dashboard:
            try:
                await self._dashboard.broadcast(
                    {"type": "headphones", "status": status}
                )
            except Exception:
                pass

    # ── Internet tunnel (🌐) — EDIT from anywhere over mobile data ─────────

    def _ui_toggle_internet(self) -> None:
        """Called from the Qt thread when the 🌐 button is pressed."""
        if not self._loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._internet_toggle_task(), self._loop
            )
        except Exception as e:
            print(f"[Tunnel] UI toggle error: {e}")

    async def _internet_toggle_task(self, enabled: bool | None = None) -> dict:
        """Start/stop the public tunnel and push the status everywhere."""
        try:
            if enabled is None:
                enabled = not self._tunnel.active
            if enabled:
                st = await asyncio.to_thread(self._tunnel.start, 35.0)
                if st.get("error") == "no_tunnel_binary":
                    self.ui.write_log("🌐 Tunnel engine missing — see log for install steps")
                    self.ui.show_content(
                        "🌐 INTERNET ACCESS — install tunnel",
                        st.get("hint", ""),
                    )
                    return st
                TunnelManager.set_enabled(True)
                url = st.get("url") or ""
                if url:
                    self.ui.write_log(f"🌐 INTERNET ACCESS: {url}")
                    self.ui.show_content(
                        "🌐 INTERNET ACCESS — EDIT (мобильный интернет)",
                        f"{url}\n\nОткрой этот адрес на телефоне — тот же Remote "
                        "Dashboard, режим наушников 🎧 и всё остальное работают "
                        "через мобильный интернет. Первый вход: PIN из "
                        "Remote Control (телефон с запомненным токеном входит сам).",
                    )
                else:
                    self.ui.write_log("🌐 Tunnel started but URL not ready yet")
            else:
                await asyncio.to_thread(self._tunnel.stop)
                TunnelManager.set_enabled(False)
                self.ui.write_log("🌐 INTERNET ACCESS: OFF")
            self.ui.update_internet_btn(self._tunnel.status())
            return self._tunnel.status()
        except Exception as e:
            print(f"[Tunnel] Error: {e}")
            traceback.print_exc()
            return self._tunnel.status()

    def _maybe_start_internet_tunnel(self) -> None:
        """Auto-start the public tunnel at boot if enabled in the config."""
        if TunnelManager.enabled() and not self._tunnel.active:
            asyncio.create_task(self._internet_toggle_task(True))

    # ── TTS voice module — the AI does not speak, this module does ─────────

    def _tts_mode_on(self) -> bool:
        """True when replies must be voiced by the TTS module (never the AI)."""
        return self._tts_voice_mode() or self._phone_headphones_active

    @staticmethod
    def _jarvis_available() -> bool:
        """Dependencies of the Jarvis Voice Module (EdgeTTS + miniaudio)."""
        try:
            import edge_tts      # noqa: F401
            import miniaudio     # noqa: F401
            return True
        except Exception:
            return False

    async def _dispatch_tts(self, text: str) -> None:
        """Voice `text` through the dedicated TTS module.

        Phone Headphones Mode → the Jarvis Voice Module (EdgeTTS deep British
        male on the PC) streamed to the phone's single sink tab; falls back to
        the phone's own speechSynthesis if the module's deps are missing.
        Otherwise → the PC TTS engine (EdgeTTS/Kokoro) on the PC speakers.
        """
        if self._phone_headphones_active and self._dashboard:
            if self._jarvis_available():
                if self._jarvis_tts is None:
                    self._jarvis_tts = _TTSBridge(
                        stream_cb=self._dashboard.feed_audio,
                        voice=self._jarvis_voice(),
                    )
                self._jarvis_tts.speak(text)
            else:
                try:
                    await self._dashboard.send_tts(text)   # phone speechSynthesis
                except Exception:
                    pass
            self.set_speaking(True)
            return
        if self._pc_tts is None:
            self._pc_tts = _TTSBridge()
        self._pc_tts.speak(text)
        self.set_speaking(True)

    def _cancel_tts(self) -> None:
        """Stop the TTS voice instantly (interrupt / headphone button)."""
        if self._pc_tts is not None:
            self._pc_tts.clear()
        if self._jarvis_tts is not None:
            self._jarvis_tts.clear()
        if self._dashboard:
            try:
                self._dashboard.clear_audio()   # drop buffered phone PCM
            except Exception:
                pass
            if self._loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.broadcast(
                            {"type": "tts", "text": "", "cancel": True}
                        ),
                        self._loop,
                    )
                except Exception:
                    pass

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "EDIT").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "EDIT"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else "ADDRESS: When speaking Russian → address politely and naturally in Russian. "
                      "When speaking Turkish → always say \"efendim\". "
                      "When speaking English → say \"sir\". Never mix languages.")
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name} (also known as EDITH / EDIT). "
            f"Always refer to yourself as {self._asst_name}. Never call yourself JARVIS.\n"
            f"You have autonomous self-improvement tools (self_improve, create_skill, execute_command).\n"
            f"{_addr}\n\n"
        )

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        # Wake Bracket Protocol — voice commands are framed as
        # "EDIT … command … EDIT"; EDIT answers only what is between them.
        if self._wake_bracket_enabled():
            parts.append(_WAKE_BRACKET_PROTOCOL)

        # The live models (audio-native) only support AUDIO modality — TEXT-only
        # sessions are rejected (error 1007).  So the session always runs with
        # AUDIO, but when the TTS voice module is enabled the incoming AI audio
        # is DISCARDED and the output transcription is voiced by the dedicated
        # TTS module (phone speechSynthesis / PC EdgeTTS).  Exactly one voice.
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": get_all_tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "holo_project":
                # Voice/text command → create the same safe visual prototype
                # that the dashboard's HOLO button creates.
                if not self._dashboard:
                    _subject = str(args.get("subject") or args.get("prototype") or "custom hologram object").strip()
                    self.ui.show_holo_project({
                        "id": "PC-LOCAL-HOLO",
                        "name": str(args.get("project_name") or _subject[:48]),
                        "model": "custom",
                        "subject": _subject,
                        "mode": str(args.get("display_mode") or "holo"),
                        "notes": args.get("notes") or "",
                        "components": args.get("components") or [],
                        "geometry": args.get("geometry") or [],
                        "parts": args.get("parts") or [],
                    })
                    result = "Rendered the custom hologram and blueprint locally on the PC. The dashboard is offline, so remote mirroring is unavailable."
                else:
                    _model_raw = str(args.get("prototype") or "custom").lower().strip()
                    _model_aliases = {
                        "smart glasses": "glasses", "glass": "glasses", "camera glasses": "glasses",
                        "ар очки": "glasses", "очки": "glasses", "перчатка": "glove", "костюм": "suit",
                        "любой объект": "custom", "произвольный": "custom", "any": "custom",
                    }
                    _known_models = {"glasses", "glove", "suit", "custom"}
                    _model = _model_aliases.get(_model_raw, _model_raw)
                    _subject = str(args.get("subject") or "").strip()
                    if _model not in _known_models:
                        _subject = _subject or _model_raw
                        _model = "custom"
                    if _model == "custom" and not _subject:
                        _subject = "custom hologram object"
                    _mode = str(args.get("display_mode") or "holo").lower().strip()
                    _mode_aliases = {"clear view": "clear", "по частям": "exploded", "частями": "exploded", "каркас": "wireframe"}
                    _mode = _mode_aliases.get(_mode, _mode)
                    try:
                        project = self._dashboard.create_holo_project(
                            model=_model,
                            mode=_mode,
                            name=args.get("project_name") or (_subject[:48] if _model == "custom" else ""),
                            clarity=args.get("clarity", 85),
                            notes=args.get("notes") or "",
                            components=args.get("components"),
                            subject=_subject,
                            blueprint=args.get("blueprint") or "",
                            geometry=args.get("geometry"),
                            parts=args.get("parts"),
                        )
                        self.ui.show_holo_project(project)
                        await self._dashboard.broadcast({"type": "holo_project", "project": project})
                        self.ui.show_content(
                            "HOLO LAB — " + project["id"],
                            f"{project['name']}\n\n"
                            f"Subject: {project.get('subject') or project['model']} · Display: {project['mode']}\n"
                            f"AI geometry primitives: {len(project.get('geometry') or [])}\n"
                            "Open the Remote Dashboard or use the PC Holo Lab to inspect the animated concept, "
                            "component schedule, geometry and exploded view."
                        )
                        result = (
                            f"Created hologram {project['id']} for {project.get('subject') or project['model']}. "
                            "The AI blueprint and geometry are now rendered on the PC monitor and mirrored to connected dashboards. "
                            "This is a software visualization, not a physical hologram."
                        )
                    except ValueError as exc:
                        result = f"I could not create that hologram: {exc}. Use a supported wearable or describe any custom subject."

            elif name == "holo_diagnose":
                from actions.holo_lab import diagnose_project as _diagnose_holo, format_diagnostics as _format_holo
                symptom = str(args.get("symptom") or "unknown symptom")[:300]
                issues = _diagnose_holo(
                    {"model": "custom", "subject": "current Holo Lab build", "notes": symptom},
                    args.get("parts"),
                    symptom,
                )
                self.ui.run_holo_diagnostics(symptom)
                result = _format_holo(issues)

            elif name == "print_holo_blueprint":
                self.ui.print_holo_blueprint()
                result = "I opened the PC printer dialog for the current Holo Lab blueprint, BOM and diagnostics. Choose a printer and confirm."

            elif name == "remember_face":

                who = (args.get("name") or args.get("person") or "").strip()
                frame = self._last_frame
                if not who:
                    result = "Tell me the person's name so I can label the face."
                elif not frame:
                    result = ("No live camera frame available. Start the phone "
                              "camera first, then ask me again.")
                else:
                    from actions.face_id import enroll as _enroll
                    ok = await loop.run_in_executor(
                        None, lambda: _enroll(frame, who)
                    )
                    if ok:
                        self.ui.write_log(f"SYS: Face enrolled — {who}")
                        result = (f"Saved. I will recognise {who} from now on. "
                                  f"Show the face from a few angles to improve it.")
                    else:
                        result = ("I could not find a clear face in the frame. "
                                  "Move closer to the camera and try again.")

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "geoint_lookup":
                from actions.geoint_engine import geoint_lookup as _geoint_lookup
                r = await loop.run_in_executor(None, lambda: _geoint_lookup(parameters=args, player=self.ui, speak=self.speak))
                result = r or "GEOINT lookup complete."

            elif name == "social_osint":
                r = await loop.run_in_executor(None, lambda: social_osint(parameters=args, player=self.ui))
                result = r or "OSINT search complete."

            elif name == "self_improve":
                from actions.self_improve import self_improve as _self_improve
                r = await loop.run_in_executor(None, lambda: _self_improve(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "create_skill":
                from actions.self_improve import create_skill as _create_skill
                r = await loop.run_in_executor(None, lambda: _create_skill(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "execute_command":
                from actions.self_improve import execute_command as _execute_cmd
                r = await loop.run_in_executor(None, lambda: _execute_cmd(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif name == "headphones_mode":
                if args.get("status_only"):
                    status = self._headphones.status()
                else:
                    status = await self._headphones_toggle_task(
                        bool(args.get("enabled", not self._headphones.enabled))
                    )
                if status.get("enabled"):
                    if status.get("connected"):
                        result = (
                            "Headphones mode is ON. EDIT speaks through "
                            f"{status.get('name') or 'Bluetooth headphones'} "
                            "and hears you through the headset mic. "
                            "Press the button on the headphones to make me listen."
                        )
                    else:
                        result = (
                            "Headphones mode is ON, but no Bluetooth headphones "
                            "are currently connected — audio stays on the default "
                            "devices. Connect them and I will switch automatically."
                        )
                else:
                    result = "Headphones mode is OFF — audio uses the default devices."
                self.ui.update_headphones_btn(status)

            elif name == "wake_protocol":
                enabled = bool(args.get("enabled", True))
                self._save_wake_bracket(enabled)
                self.ui.write_log(
                    "SYS: Wake Bracket Protocol — "
                    + ("ON ('EDIT … команда … EDIT')" if enabled else "OFF")
                )
                if enabled:
                    result = (
                        "Wake bracket protocol is ON. From the next connection, "
                        "I answer only what you say between two standalone 'EDIT' "
                        "words: say EDIT, then your command, then EDIT again."
                    )
                else:
                    result = (
                        "Wake bracket protocol is OFF. From the next connection "
                        "I respond normally to everything you say."
                    )

            elif name == "tts_voice":
                enabled = bool(args.get("enabled", True))
                if not enabled and self._phone_headphones_active:
                    result = (
                        "Phone Headphones Mode requires the TTS voice module — "
                        "it stays ON so your headphones keep working."
                    )
                else:
                    self._save_tts_voice_mode(enabled)
                    self.ui.write_log(
                        "SYS: TTS voice module — "
                        + ("ON (AI does not speak)" if enabled else "OFF (AI speaks)")
                    )
                    self._cancel_tts()
                    if enabled:
                        result = (
                            "TTS voice module is ON — my replies are voiced by "
                            "the TTS module from now on."
                        )
                    else:
                        result = (
                            "TTS voice module is OFF — the AI speaks directly "
                            "from now on."
                        )

            elif name == "internet_access":
                if args.get("status_only"):
                    st = self._tunnel.status()
                else:
                    st = await self._internet_toggle_task(
                        bool(args.get("enabled", not self._tunnel.active))
                    )
                if st.get("active") or st.get("url"):
                    result = (
                        "Internet access is ON. Open this address on your phone "
                        "to use EDIT from anywhere, even on mobile data without "
                        f"WiFi: {st.get('url') or ''}"
                    )
                elif st.get("error") == "no_tunnel_binary":
                    result = (
                        "Internet tunnel needs a tunnel program installed on the "
                        "PC: Cloudflare (cloudflared) or ngrok. See the on-screen "
                        "instructions."
                    )
                else:
                    result = "Internet access is OFF — EDIT is reachable only on the local network."

            else:
                from actions.self_improve import is_custom_skill, run_custom_skill
                if is_custom_skill(name):
                    r = await loop.run_in_executor(None, lambda: run_custom_skill(name, parameters=args, player=self.ui))
                    result = r or f"Executed skill '{name}'."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if (
                not jarvis_speaking
                and not self.ui.muted
                and not self._phone_active
                and not self._phone_headphones_active
                and not (self._pc_tts is not None and self._pc_tts.playing)
            ):
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        # Device-aware loop: restarts the stream with a new device whenever
        # headphone mode reroutes the audio (self._audio_gen changes).
        while True:
            gen = self._audio_gen
            dev = self._audio_devices[0] if self._audio_devices else None
            try:
                with sd.InputStream(
                    device=dev,
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    callback=callback,
                ):
                    print(f"[JARVIS] 🎤 Mic stream open (device={dev})")
                    while self._audio_gen == gen:
                        await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[JARVIS] ❌ Mic: {e}")
                if self._audio_gen == gen:
                    if dev is not None:
                        # Stale device (headset unplugged) → drop to default
                        print("[JARVIS] Mic device unavailable — using default")
                        self._audio_devices = (None, self._audio_devices[1])
                        self._audio_gen += 1
                        await asyncio.sleep(1.0)
                        continue
                    raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted or self._tts_mode_on():
                            # Interrupted OR TTS voice module: the AI's own
                            # audio is discarded — it is never played on the PC
                            # and never streamed to the phone (the reply text is
                            # voiced by the TTS module instead).
                            pass
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                _slice = _audio_data[_i : _i + _SLICE]
                                self.audio_in_queue.put_nowait(_slice)
                                if self._dashboard:
                                    # same slice → JARVIS voice plays on the phone too
                                    self._dashboard.feed_audio(_slice)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if getattr(sc, "interrupted", False):
                            # Gemini was interrupted — stop the TTS voice now
                            self._cancel_tts()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                                # TTS voice module: voice the complete reply
                                # through the dedicated TTS (phone or PC).
                                if self._tts_mode_on() and not self._interrupted:
                                    await self._dispatch_tts(full_out)
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until JARVIS finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        # Device-aware loop: restarts the stream whenever headphone mode
        # reroutes the audio (self._audio_gen changes).
        while True:
            gen = self._audio_gen
            dev = self._audio_devices[1] if self._audio_devices else None
            stream = None
            # Phone Headphones Mode / TTS voice module: the AI's audio is
            # discarded, so the PC output stream stays closed — no sound from
            # the PC speaker at all.
            if not self._phone_headphones_active and not self._tts_voice_mode():
                try:
                    stream = sd.RawOutputStream(
                        device=dev,
                        samplerate=RECEIVE_SAMPLE_RATE,
                        channels=CHANNELS,
                        dtype="int16",
                        blocksize=CHUNK_SIZE,
                    )
                    stream.start()
                except Exception as e:
                    print(f"[JARVIS] ❌ Play: {e}")
                    if self._audio_gen == gen and dev is not None:
                        # Stale device (headset unplugged) → drop to default
                        print("[JARVIS] Play device unavailable — using default")
                        self._audio_devices = (self._audio_devices[0], None)
                        self._audio_gen += 1
                        await asyncio.sleep(1.0)
                        continue
                    raise

            try:
                while self._audio_gen == gen:
                    try:
                        chunk = await asyncio.wait_for(
                            self.audio_in_queue.get(),
                            timeout=0.1
                        )
                    except asyncio.TimeoutError:
                        if (
                            self._turn_done_event
                            and self._turn_done_event.is_set()
                            and self.audio_in_queue.empty()
                        ):
                            self.set_speaking(False)
                            self._turn_done_event.clear()
                        continue

                    self.set_speaking(True)

                    # Batch all immediately-available chunks into one write to reduce
                    # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                    # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                    batch = bytearray(chunk)
                    while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                        try:
                            batch.extend(self.audio_in_queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break

                    if stream is None:
                        # Phone Headphones Mode — the phone plays this audio;
                        # just keep consuming so the queue never fills.
                        continue
                    try:
                        await asyncio.to_thread(stream.write, bytes(batch))
                    except (RuntimeError, asyncio.CancelledError):
                        if self._audio_gen == gen:
                            raise   # executor shutting down — exit cleanly
                        break      # device swap in progress — reopen stream
            finally:
                self.set_speaking(False)
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=4.0)
                except Exception:
                    news_text = ""

                if not self.session:
                    return

                if news_text and len(news_text) > 60:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from google import genai as _genai
            client = _genai.Client(api_key=_get_api_key())
            resp   = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
            )
            summary = (resp.text or "").strip()
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            self.ui.write_log(f"SYS: Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── phone camera vision relay ───────────────────────────────────────────

    async def _relay_phone_vision(self) -> None:
        """Forward phone-camera frames from the dashboard into the Gemini Live session.

        Flow: phone SCAN → /api/vision-scan queues (frame, question) → inject the
        image here → JARVIS answers by voice on the PC, and _receive_audio already
        broadcasts the transcript back to the phone feed (EDITH-style).
        """
        import base64 as _b64
        q = self._dashboard._phone_vision_queue
        _DEFAULT_Q = (
            "Identify everything visible — every person (appearance only, no "
            "identities), every vehicle (color, make/model — quote the license "
            "plate characters if legible), animals and notable objects."
        )
        while True:
            try:
                frame, mime_t, question = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[Dashboard] Vision queue error: {e}")
                await asyncio.sleep(0.5)
                continue
            try:
                # Phone may scan while JARVIS sleeps — wait up to 10 s for a session
                for _ in range(100):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if not self.session:
                    print("[Dashboard] Dropped phone frame — no active session")
                    await self._dashboard.broadcast({
                        "type": "vision_status", "state": "error",
                        "text": "JARVIS is offline on the PC — start it, then scan again.",
                    })
                    continue
                # Don't collide with a PC-side screen/camera vision cycle
                for _ in range(150):  # up to 15 s
                    if not self._vision_busy:
                        break
                    await asyncio.sleep(0.1)

                await self._dashboard.broadcast(
                    {"type": "vision_status", "state": "analyzing"}
                )
                q_text = (
                    "[PHONE CAMERA] The user pointed their phone camera at the real "
                    "world and pressed SCAN. The attached image is what their phone "
                    "sees right now. Answer in the user's language, concisely, like "
                    "a tactical heads-up display report. Identify visible people by "
                    "appearance/clothing/pose only — never guess a real name or "
                    "identity, and never dig up personal data about anyone. "
                    "For vehicles: color and make/model when recognizable; if a "
                    "license plate is legible, quote its characters only. "
                    f"User's question: {question or _DEFAULT_Q}"
                )
                b64 = _b64.b64encode(frame).decode("ascii")
                await self.session.send_client_content(
                    turns={"parts": [
                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                        {"text": q_text},
                    ]},
                    turn_complete=True,
                )
                print(f"[Dashboard] 📷 Phone frame {len(frame):,} bytes → live session")
                self.ui.write_log(f"[PhoneCam]: {question or 'auto-scan'}")
                if question:
                    await self._dashboard.broadcast({
                        "type": "log", "speaker": "user",
                        "text": f"📷 {question}",
                        "ts": datetime.now().isoformat(),
                    })
                # EDITH snapshot with labeled boxes on the PC screen too
                asyncio.create_task(self._pc_scan_overlay(frame))
            except Exception as e:
                print(f"[Dashboard] Vision relay error: {e}")
                await asyncio.sleep(0.5)

    async def _pc_scan_overlay(self, frame: bytes) -> None:
        """Detect people/vehicles/objects in the phone's frame and paint the
        labeled snapshot onto the PC window's HUD area."""
        try:
            from dashboard.server import _edith_detect
            dets = await asyncio.to_thread(_edith_detect, frame)
            if dets and hasattr(self.ui, "show_phone_scan"):
                self.ui.show_phone_scan(frame, dets)
                print(f"[Dashboard] 🖥️  Scan overlay: {len(dets)} target(s) on PC HUD")
        except Exception as e:
            print(f"[Dashboard] PC scan overlay failed: {e}")

    # ── phone camera live stream relay ───────────────────────────────────

    async def _relay_phone_cam(self) -> None:
        """Live phone-camera frames → PC HUD area. While streaming, a background
        detection pass (~every 1.4 s) refreshes EDITH boxes on PC and phone."""
        q = self._dashboard._phone_cam_queue
        live = False
        last_det = 0.0
        det_task = None
        trk_task = None
        last_trk = 0.0
        self._live_labels: list = []      # newest Gemini labels (slow path)
        while True:
            try:
                frame = await asyncio.wait_for(q.get(), timeout=0.8)
            except asyncio.TimeoutError:
                if live:   # stream went quiet — hide the PC overlay
                    live = False
                    last_det = 0.0
                    self._live_labels = []
                    try:
                        from actions.pose_tracker import get_tracker
                        get_tracker().reset()
                    except Exception:
                        pass
                    try:
                        self.ui.stop_phone_cam()
                    except Exception:
                        pass
                continue
            except Exception as e:
                print(f"[Dashboard] Cam queue error: {e}")
                await asyncio.sleep(0.5)
                continue
            if not live:
                live = True
                try:
                    self.ui.start_phone_cam()
                    self.ui.write_log("SYS: Phone camera live on PC HUD.")
                except Exception:
                    pass
            self._last_frame = frame          # newest frame, for face enrolment
            try:
                self.ui.show_phone_cam_frame(frame)
            except Exception:
                pass

            # ── FAST PATH: local person tracking (skeleton + aura).
            #    Capped at ~30 Hz: the HUD interpolates/animates at 60 FPS on
            #    top of these results, and tracking every single frame would
            #    steal CPU from the render loop and drop the display below 60.
            if (now_t := time.monotonic()) - last_trk >= 0.033 and (
                    trk_task is None or trk_task.done()):
                last_trk = now_t
                trk_task = asyncio.create_task(self._live_track_task(frame))

            # ── SLOW PATH: Gemini labels/objects, refreshed occasionally.
            now = time.monotonic()
            if now - last_det >= 2.5 and (det_task is None or det_task.done()):
                last_det = now
                det_task = asyncio.create_task(self._live_detect_task(frame))

    async def _live_track_task(self, frame: bytes) -> None:
        """Local, real-time person tracking for the live HUD overlay.

        Fully isolated: any failure here must never interrupt the video feed
        or take the application down.
        """
        try:
            from actions.pose_tracker import track_people
            people = await asyncio.wait_for(
                asyncio.to_thread(track_people, frame), timeout=5.0
            )
        except asyncio.TimeoutError:
            print("[Dashboard] Local tracking timed out — skipping frame")
            return
        except BaseException as e:
            print(f"[Dashboard] Local tracking failed: {e}")
            return

        # Face detection + identity, also fully local and per-frame.
        # The pose worker already returns 478-point face meshes when its model
        # is available; those are richer (they carry the wireframe), so they
        # win. face_id then only has to answer "who is this?".
        mesh_faces = [d for d in people if d.get("kind") == "face"]
        people = [d for d in people if d.get("kind") != "face"]
        faces = []
        try:
            from actions.face_id import detect_faces, identify_box, save_new_faces
            if mesh_faces:
                faces = mesh_faces
                # attach identities to the meshes we already have
                await asyncio.to_thread(identify_box, frame, faces)
            else:
                faces = await asyncio.wait_for(
                    asyncio.to_thread(detect_faces, frame), timeout=5.0
                )
        except asyncio.TimeoutError:
            pass
        except BaseException as e:
            print(f"[Dashboard] Face detection failed: {e}")

        # Keep a single snapshot for each new face appearance.  This runs
        # after detection (including mesh detections) and is deduplicated by
        # actions.face_id against the captures already on disk.
        if faces:
            try:
                await asyncio.to_thread(save_new_faces, frame, faces)
            except BaseException as e:
                print(f"[Dashboard] Automatic face capture failed: {e}")
        if not self._dashboard._cam_stream_active:
            return
        # Reuse the newest Gemini label for a person, if we have one, so the
        # box still reads e.g. "PERSON — BLUE HEADPHONES" instead of "TRACKED".
        labels = [d for d in (getattr(self, "_live_labels", None) or [])
                  if d.get("kind") == "person"]
        for i, p in enumerate(people):
            if i < len(labels):
                p["label"]  = labels[i].get("label")  or p["label"]
                p["detail"] = labels[i].get("detail") or p["detail"]
        # Non-person Gemini findings (objects/vehicles) stay on screen too.
        extras = [d for d in (getattr(self, "_live_labels", None) or [])
                  if d.get("kind") != "person"]
        # A recognised face is the strongest identity signal we have — promote
        # the name onto the person box that contains it.
        for f in faces:
            if not f.get("known"):
                continue
            fy0, fx0, fy1, fx1 = f["box"]
            fcy, fcx = (fy0 + fy1) / 2, (fx0 + fx1) / 2
            for p in people:
                py0, px0, py1, px1 = p["box"]
                if py0 <= fcy <= py1 and px0 <= fcx <= px1:
                    p["label"] = f["label"].replace("FACE — ", "PERSON — ")
                    p["detail"] = f["detail"]
                    break
        dets = people + faces + extras
        try:
            self.ui.show_phone_cam_dets(dets)
        except Exception:
            pass
        try:
            await self._dashboard.broadcast(
                {"type": "live_dets", "detections": dets}
            )
        except Exception:
            pass

    async def _live_detect_task(self, frame: bytes) -> None:
        """One background detection pass over the freshest live frame."""
        try:
            from dashboard.server import _edith_detect
            dets = await asyncio.to_thread(_edith_detect, frame)
        except Exception as e:
            print(f"[Dashboard] Live detection failed: {e}")
            return
        if not dets or not self._dashboard._cam_stream_active:
            return  # stream stopped while the model was thinking
        self._live_labels = dets
        # If local tracking is unavailable, the cloud result drives the HUD
        # directly (previous behaviour).
        try:
            from actions.pose_tracker import get_tracker
            if get_tracker().available():
                return
        except Exception:
            pass
        try:
            self.ui.show_phone_cam_dets(dets)
        except Exception:
            pass
        try:
            await self._dashboard.broadcast(
                {"type": "live_dets", "detections": dets}
            )
        except Exception:
            pass

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_holo_callback(self.ui.show_holo_project)
            self._dashboard.set_headphones_callback(self._dashboard_headphones_cb)
            self._dashboard.set_headphones_button_callback(self._on_phone_headphone_button)
            self._dashboard.set_phone_headphones_callback(self._set_phone_headphones_mode)
            asyncio.create_task(self._dashboard.serve())
            # Wire the Remote overlay's device hub (list + kick + revoke)
            def _kick_device(did: str) -> None:
                if not self._dashboard:
                    return
                if did == "revoke":
                    n = self._dashboard.revoke_all_paired()
                    self.ui.write_log(f"SYS: {n} paired device(s) revoked.")
                    return
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.disconnect_device(did), self._loop
                    )
            self.ui.set_device_callbacks(self._dashboard.devices_info, _kick_device)
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
            asyncio.create_task(self._relay_phone_vision())
            asyncio.create_task(self._relay_phone_cam())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # Headphones mode — re-apply persisted preference at startup so the
        # routing is active before the first session connects
        if self._load_headphones_pref():
            asyncio.create_task(self._headphones_toggle_task(True))

        # Internet tunnel — auto-start if enabled (access over mobile data)
        self._maybe_start_internet_tunnel()

        while True:
            try:
                current_model = get_current_live_model(getattr(self, "_live_model_idx", 0))
                print(f"[EDIT] Connecting to Live API using model: {current_model}...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=current_model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print(f"[EDIT] ✅ Connected to Live API ({current_model}).")
                    save_connected_live_model(current_model)
                    self.ui.set_state("LISTENING")
                    self.ui.write_log(f"SYS: EDIT online ({current_model}).")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled)
                    if not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                err_str = str(e)
                print(f"[EDIT] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Model compatibility error — switch to next available candidate in LIVE_MODEL_CANDIDATES
                _model_err = any(k in err_str for k in (
                    "1007", "1008", "not supported for bidiGenerateContent",
                    "CONTENT_TYPE_AUDIO", "not found for API version", "model is not supported",
                    "not found", "INVALID_ARGUMENT", "404"
                ))
                if _model_err:
                    old_model = get_current_live_model(getattr(self, "_live_model_idx", 0))
                    self._live_model_idx = getattr(self, "_live_model_idx", 0) + 1
                    new_model = get_current_live_model(self._live_model_idx)
                    self.ui.write_log(f"SYS: Модель {old_model} отклонила Live-канал → переключение на {new_model}")
                    print(f"[EDIT] Model '{old_model}' not supported for bidiGenerateContent. Switching to '{new_model}'...")
                    _conn_backoff = 1
                    await asyncio.sleep(1)
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                _auth_err = any(k in err_str for k in (
                    "API key not valid", "API_KEY_INVALID",
                    "invalid authentication credentials",
                    "ACCESS_TOKEN_TYPE_UNSUPPORTED",
                    "UNAUTHENTICATED",
                ))
                if _auth_err:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[EDIT] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3

        self.set_speaking(False)
        self.ui.set_state("SLEEPING")

        if self._dashboard:
            await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

        delay = getattr(self, "_conn_backoff", 3)
        print(f"[JARVIS] Reconnecting in {delay}s...")
        await asyncio.sleep(delay)

def _install_crash_guard() -> None:
    """Keep the window alive if a stray exception escapes a Qt slot or thread.

    Without this, any unhandled error inside a Qt callback tears the whole
    application down with no message — the app just vanishes.
    """
    import traceback

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        print("=" * 60)
        print("[JARVIS] Unhandled exception (app kept alive):")
        traceback.print_exception(exc_type, exc, tb)
        print("=" * 60)

    sys.excepthook = _hook
    try:
        threading.excepthook = lambda a: _hook(
            a.exc_type, a.exc_value, a.exc_traceback
        )
    except Exception:
        pass


def main():
    _install_crash_guard()
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()

        # Показываем текущую модель
        print_model_status()
        if is_local_mode():
            ui.write_log("🧠 ЛОКАЛЬНАЯ МОДЕЛЬ БЕЗ ЦЕНЗУРЫ АКТИВНА")
        from core.model_router import is_osint_mode
        if is_osint_mode():
            ui.write_log("🕵️ OSINT MODE АКТИВЕН — максимальная свобода")

        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("europe_satellite_ai"):
                ui.write_log("🛰️ EUROPE SATELLITE + AI ENHANCE АКТИВЕН")
        except Exception:
            pass

        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    try:
        ui.root.mainloop()
    finally:
        # make sure the isolated pose worker never outlives the UI
        try:
            from actions.pose_tracker import get_tracker
            get_tracker().shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    main()