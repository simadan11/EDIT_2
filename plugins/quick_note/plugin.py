"""Quick Notes plugin — tiny local note store."""

import json
import time
from pathlib import Path

NOTES_FILE = Path(__file__).resolve().parent / "notes.json"


def _load() -> list:
    try:
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(notes: list) -> None:
    NOTES_FILE.write_text(json.dumps(notes, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def run_tool(tool_name: str, args: dict, player=None) -> str:
    args = args or {}
    if tool_name == "note_save":
        text = str(args.get("text") or "").strip()
        if not text:
            return "Nothing to save — the note text is empty."
        notes = _load()
        notes.append({"text": text, "at": time.strftime("%Y-%m-%d %H:%M")})
        _save(notes)
        if player is not None and hasattr(player, "write_log"):
            player.write_log(f"SYS: Note saved — {text[:60]}")
        return f"Saved note #{len(notes)}: {text}"

    if tool_name == "note_list":
        notes = _load()
        if not notes:
            return "There are no saved notes."
        return "\n".join(f"{i + 1}. [{n.get('at', '')}] {n.get('text', '')}"
                         for i, n in enumerate(notes))

    if tool_name == "note_clear":
        count = len(_load())
        _save([])
        return f"Deleted {count} note(s)."

    return f"Unknown note action: {tool_name}"
