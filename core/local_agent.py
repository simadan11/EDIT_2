"""Local chat agent — runs EDIT's brain on Ollama instead of the cloud.

Used by ``main.py`` when *local AI mode* is active: typed commands are sent to
the local Ollama model (the user's own ``huihui_ai`` model from
``D:\\бодикам``) together with the full tool list.  The model may call tools;
they are executed on the running application (``tool_executor``) and the final
answer is spoken by the TTS module / written to the log.

Gemini-style declarations (``{"type": "OBJECT"}``) are converted to the JSON
Schema that Ollama expects, so the same tool list works for both backends.
"""

from __future__ import annotations

import json
import time
from typing import Callable

import requests

try:                                     # package import (normal)
    from core import ollama_local
except Exception:                        # script / frozen import
    try:
        import ollama_local              # type: ignore
    except Exception:
        ollama_local = None              # type: ignore

MAX_ROUNDS = 5


# ── schema conversion ───────────────────────────────────────────────────────


def _lower_schema(schema: dict | None) -> dict:
    """Gemini uppercase types → JSON-Schema lowercase (recursive)."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out: dict = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out[key] = value.lower()
        elif isinstance(value, dict):
            out[key] = _lower_schema(value)
        elif isinstance(value, list):
            out[key] = [_lower_schema(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    if out.get("type") == "object":
        out.setdefault("properties", {})
    return out


def to_ollama_tools(declarations: list[dict] | None) -> list[dict]:
    tools: list[dict] = []
    for decl in declarations or []:
        name = decl.get("name")
        if not name:
            continue
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": decl.get("description", ""),
                "parameters": _lower_schema(decl.get("parameters")),
            },
        })
    return tools


# ── agent ───────────────────────────────────────────────────────────────────


class LocalAgent:
    """Small tool-calling loop against Ollama's native /api/chat."""

    def __init__(self, model: str | None = None, url: str | None = None,
                 timeout: int = 300,
                 on_status: Callable[[str], None] | None = None):
        if ollama_local is not None:
            self.url = (url or ollama_local.get_base_url()).rstrip("/")
            self.model = model or ollama_local.resolve_model()
        else:
            self.url = (url or "http://localhost:11434").rstrip("/")
            self.model = model or ""
        self.timeout = timeout
        self.on_status = on_status or (lambda _msg: None)

    def _log(self, msg: str) -> None:
        try:
            self.on_status(msg)
        except Exception:
            pass

    def _ensure_server(self) -> bool:
        if ollama_local is None:
            return True
        if ollama_local.is_server_up(self.url):
            return True
        self._log("Ollama offline — starting the local server…")
        ok, msg = ollama_local.start_ollama()
        self._log(msg)
        return ok

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,
            "options": {"num_predict": 900, "num_gpu": 99, "temperature": 0.6},
        }
        if tools:
            payload["tools"] = tools
        resp = requests.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
        if resp.status_code == 404:
            raise RuntimeError(
                f"Model '{self.model}' is not available in Ollama. "
                "Open 🧠 LOCAL MODEL / OLLAMA and pick a model from your folder."
            )
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
        return {
            "content": (msg.get("content") or "").strip(),
            "tool_calls": msg.get("tool_calls") or [],
        }

    def run(
        self,
        user_text: str,
        system_prompt: str = "",
        declarations: list[dict] | None = None,
        tool_executor: Callable[[str, dict], str] | None = None,
        history: list[dict] | None = None,
        max_rounds: int = MAX_ROUNDS,
    ) -> str:
        """Ask the local model; execute any tools it calls; return the answer."""
        if not self.model:
            return ("No local Ollama model is configured. Open 🧠 LOCAL MODEL / "
                    "OLLAMA and choose a model from D:\\бодикам.")
        if not self._ensure_server():
            return "Ollama is not running and could not be started."

        tools = to_ollama_tools(declarations)
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_text})

        started = time.time()
        last_content = ""
        for round_no in range(max_rounds):
            try:
                self._log(f"{self.model} is thinking…" if round_no == 0
                          else f"{self.model} — step {round_no + 1}…")
                reply = self.chat(messages, tools)
            except requests.exceptions.ConnectionError:
                if self._ensure_server():
                    reply = self.chat(messages, tools)
                else:
                    return "Lost connection to Ollama."
            except Exception as e:
                return f"Local model error: {e}"

            calls = reply.get("tool_calls") or []
            last_content = reply.get("content") or last_content

            if not calls:
                elapsed = time.time() - started
                print(f"[LOCAL-AI] {self.model} answered in {elapsed:.1f}s")
                return last_content or "(The model returned an empty answer.)"

            # echo the assistant turn with its tool calls (Ollama protocol)
            messages.append({
                "role": "assistant",
                "content": reply.get("content") or "",
                "tool_calls": calls,
            })

            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args or "{}")
                    except Exception:
                        raw_args = {}
                args = raw_args if isinstance(raw_args, dict) else {}
                self._log(f"⚙ {name}…")
                if tool_executor is None:
                    result = f"No executor available for tool '{name}'."
                else:
                    try:
                        result = tool_executor(name, args)
                    except Exception as e:
                        result = f"Tool '{name}' failed: {e}"
                messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": str(result)[:4000],
                })

        return last_content or "The local model used too many tool steps; try a simpler request."
