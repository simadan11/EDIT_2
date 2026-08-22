"""
LUMEN — сервер платформы (stdlib http.server, нулевые зависимости).

Поверхности:
  • REST API  /api/*   — чат, инструменты, память, сессии, настройки,
                         система, фидбэк, обучение;
  • SSE       /api/chat/stream — события конвейера «Луч» в реальном времени;
  • Статика   /*        — новый веб-интерфейс lumen/web.

Безопасность:
  • если задан api.auth_token — все запросы (включая статику) требуют
    заголовок Authorization: Bearer <token>;
  • JSON-тела с лимитом 256 КБ; ошибки — структурированными 4xx/5xx;
  • CORS не включён: интерфейс живёт на том же хосте (same-origin).
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import BRAND_NAME, MODEL_NAME, VERSION, TAGLINE
from ..config import LumenConfig, BASE_DIR, DATA_DIR
from ..kernel.backends import get_backend, HeuristicBackend
from ..kernel.engine import LumenEngine, create_engine
from ..io.voice import VoiceModule

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 256 * 1024


class LumenServer:
    """Обёртка: конфиг + ядро + HTTP-сервер."""

    def __init__(self, config: Optional[LumenConfig] = None) -> None:
        self.config = config or LumenConfig()
        self.engine: LumenEngine = create_engine(self.config)
        self.voice = VoiceModule(self.config)
        self.started = time.time()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._handler = self._make_handler()

    # ── запуск / остановка ───────────────────────────────────────────────────
    def serve(self, host: Optional[str] = None, port: Optional[int] = None,
              block: bool = True) -> ThreadingHTTPServer:
        host = host or self.config.get("api.host", "0.0.0.0")
        port = int(port or self.config.get("api.port", 8090))
        self._httpd = ThreadingHTTPServer((host, port), self._handler)
        self._httpd.daemon_threads = True
        if not block:
            return self._httpd
        print(f"◈ {BRAND_NAME} Platform v{VERSION} — {TAGLINE}")
        print(f"  интерфейс: http://{host}:{port}")
        print(f"  API:       http://{host}:{port}/api/health")
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._httpd.server_close()
        return self._httpd

    def shutdown(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    # ── маршрутизация API ────────────────────────────────────────────────────
    def handle_api(self, method: str, path: str,
                   body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        p = path.split("?")[0].rstrip("/")
        try:
            if method == "GET" and p == "/api/health":
                return 200, self._health()
            if method == "GET" and p == "/api/profile":
                return 200, self._profile()
            if method == "POST" and p == "/api/chat":
                return self._chat(body)
            if method == "GET" and p == "/api/tools":
                return 200, {"tools": self.engine.registry.list()}
            if method == "POST" and p == "/api/tools/run":
                return self._tool_run(body)
            if method == "GET" and p == "/api/memory":
                cat = body.get("category") if isinstance(body, dict) else None
                return 200, {"facts": self.engine.memory.facts(cat),
                             "count": self.engine.memory.count()}
            if method == "POST" and p == "/api/memory":
                if not (body.get("key") and body.get("value")):
                    return 400, {"error": "нужны поля key и value"}
                fact = self.engine.memory.remember(
                    body.get("category", "notes"), body["key"], body["value"])
                return 200, {"saved": fact}
            if method == "DELETE" and p == "/api/memory":
                if not (body.get("key")):
                    return 400, {"error": "нужно поле key (и category)"}
                ok = self.engine.memory.forget(body.get("category", "notes"), body["key"])
                return (200 if ok else 404), {"deleted": ok}
            if method == "GET" and p == "/api/memory/recall":
                q = (body.get("query") or "") if isinstance(body, dict) else ""
                return 200, {"facts": self.engine.memory.recall(q, top_k=10)}
            if method == "GET" and p == "/api/sessions":
                return 200, {"sessions": self.engine.memory.sessions_list()}
            if method == "POST" and p == "/api/sessions":
                sid = self.engine.memory.new_session(body.get("title", ""))
                return 200, {"session_id": sid}
            if method == "DELETE" and p.startswith("/api/sessions/"):
                sid = p.rsplit("/", 1)[-1]
                self.engine.memory.drop_session(sid)
                return 200, {"deleted": sid}
            if method == "GET" and p == "/api/settings":
                return 200, self.config.public()
            if method == "PUT" and p == "/api/settings":
                return self._settings_put(body)
            if method == "GET" and p == "/api/system":
                res = self.engine.registry.run("system.status", {})
                out = res.data if res.ok else {}
                return 200, {
                    "ok": res.ok,
                    "text": res.text if res.ok else res.error,
                    **out,
                    "platform": {
                        "brand": BRAND_NAME, "model": MODEL_NAME,
                        "version": VERSION,
                        "uptime_sec": int(time.time() - self.started),
                        "backend": self.engine.backend.name,
                        "backend_display": self.engine.backend.display,
                        "tools_total": len(self.engine.registry.list()),
                        "tools_available": len(self.engine.registry.available_names()),
                        "facts": self.engine.memory.count(),
                    },
                }
            if method == "POST" and p == "/api/feedback":
                rating = int(body.get("rating", 0))
                rec = self.engine.learning.record_feedback(
                    str(body.get("message_id", "")), rating,
                    note=str(body.get("note", "")),
                    intent=str(body.get("intent", "")))
                return 200, {"recorded": rec,
                             "learned": self.engine.learning.learned_preferences()}
            if method == "GET" and p == "/api/learning/stats":
                return 200, self.engine.learning.stats()
            if method == "POST" and p == "/api/learning/corpus":
                out = DATA_DIR / "export" / "corpus.jsonl"
                n = self.engine.learning.export_corpus(
                    out, self.engine.memory.sessions_dir)
                return 200, {"count": n, "path": str(out)}
            if method == "POST" and p == "/api/voice/speak":
                from ..io.text import strip_for_speech
                res = self.voice.speak(strip_for_speech(str(body.get("text", ""))))
                return (200 if res.get("ok") else 400), res
            return 404, {"error": f"маршрут не найден: {method} {path}"}
        except Exception as e:  # noqa: BLE001
            return 500, {"error": f"{type(e).__name__}: {e}"}

    def _health(self) -> Dict[str, Any]:
        b = self.engine.backend
        ok, reason = b.available()
        return {
            "status": "ok",
            "brand": BRAND_NAME,
            "model": MODEL_NAME,
            "version": VERSION,
            "pipeline": "Луч",
            "backend": {"name": b.name, "display": b.display,
                        "available": ok, "reason": reason},
            "tools": {"total": len(self.engine.registry.list()),
                      "available": len(self.engine.registry.available_names())},
            "facts": self.engine.memory.count(),
            "uptime_sec": int(time.time() - self.started),
        }

    def _profile(self) -> Dict[str, Any]:
        return {
            "brand": BRAND_NAME,
            "brand_ru": "Люмен",
            "model": MODEL_NAME,
            "version": VERSION,
            "tagline": TAGLINE,
            "concept": "Самостоятельная ИИ-платформа: собственное ядро LUMEN-1 "
                       "(конвейер «Луч»: приём → защита → анализ → контекст → "
                       "инструменты → генерация), модуль контекстной памяти, "
                       "реестр инструментов и обучающая петля.",
            "stages": ["Приём", "Защита", "Анализ", "Контекст", "Инструменты",
                       "Генерация"],
            "memory_layers": ["working (сессия)", "episodic (журналы)",
                              "semantic (факты)"],
            "backends": ["LHC — локальный модуль рассуждения",
                         "Gemini (REST)", "OpenAI-совместимый (Ollama, LM Studio…)"],
            "learning": ["фидбэк (+/−)", "адаптация предпочтений",
                         "экспорт корпуса для дообучения"],
        }

    def _chat(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        message = str(body.get("message", "")).strip()
        if not message:
            return 400, {"error": "пустое поле message"}
        sid = body.get("session_id") or None
        resp = self.engine.process(message, session_id=sid)
        return 200, resp.to_dict()

    def _tool_run(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        name = str(body.get("name", ""))
        if not name:
            return 400, {"error": "пустое поле name"}
        res = self.engine.registry.run(name, body.get("args") or {})
        status = 200 if res.ok else 400
        return status, res.to_dict()

    def _settings_put(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            return 400, {"error": "ожидается JSON-объект"}
        # защищаем служебные ключи
        body = {k: v for k, v in body.items() if k not in ("brand", "_migration")}
        self.config.update(body)
        # применяем изменения к ядру на лету
        try:
            self.engine.guard = type(self.engine.guard)(
                max_input_length=int(self.config.get("safety.max_input_length", 8000)),
                block_injection=bool(self.config.get("safety.block_injection", True)))
            self.engine.backend = get_backend(self.config)
            if isinstance(self.engine.backend, HeuristicBackend):
                self.engine._lhc = self.engine.backend
            self.engine.weaver = type(self.engine.weaver)(self.config, self.engine.memory)
        except Exception:
            pass
        return 200, self.config.public()

    # ── HTTP-обработчик ──────────────────────────────────────────────────────
    def _make_handler(self) -> type:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"LUMEN/{VERSION}"
            protocol_version = "HTTP/1.1"

            # ── утилиты ────────────────────────────────────────────────────
            def log_message(self, fmt: str, *args: Any) -> None:  # тихий лог
                pass

            def _send_json(self, code: int, payload: Dict[str, Any],
                           extra_headers: Optional[Dict[str, str]] = None) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra_headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _auth_ok(self) -> bool:
                token = server.config.get("api.auth_token", "")
                if not token:
                    return True
                got = self.headers.get("Authorization", "")
                return got == f"Bearer {token}"

            def _read_body(self) -> Dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    length = 0
                if length <= 0:
                    return {}
                if length > MAX_BODY:
                    raise ValueError("тело запроса слишком большое")
                raw = self.rfile.read(length)
                try:
                    data = json.loads(raw.decode("utf-8"))
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            def _query(self) -> Dict[str, str]:
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                return {k: v[0] for k, v in q.items()}

            # ── методы ─────────────────────────────────────────────────────
            def do_GET(self) -> None:  # noqa: N802
                self._route("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._route("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._route("PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                self._route("DELETE")

            def _route(self, method: str) -> None:
                from urllib.parse import urlparse
                path = urlparse(self.path).path
                try:
                    if not self._auth_ok():
                        self._send_json(401, {"error": "требуется Bearer-токен"})
                        return
                    if path == "/api/chat/stream" and method == "POST":
                        self._sse_chat()
                        return
                    body: Dict[str, Any] = {}
                    if method in ("POST", "PUT", "DELETE"):
                        body = self._read_body()
                    if method == "GET" and (path.startswith("/api/memory") or
                                            path.startswith("/api/sessions")):
                        body = self._query()
                    if method == "DELETE" and path.startswith("/api/sessions/"):
                        body = {}
                    code, payload = server.handle_api(method, self.path, body)
                    self._send_json(code, payload)
                except ValueError as e:
                    self._send_json(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

            # ── SSE: события конвейера ─────────────────────────────────────
            def _sse_chat(self) -> None:
                body = self._read_body()
                message = str(body.get("message", "")).strip()
                sid = body.get("session_id") or None
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()

                def _w(event: str, data: Dict[str, Any]) -> None:
                    try:
                        self.wfile.write(f"event: {event}\n".encode("utf-8"))
                        self.wfile.write(
                            ("data: " + json.dumps(data, ensure_ascii=False) + "\n\n")
                            .encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        raise StopIteration

                try:
                    for item in server.engine.stream(message, session_id=sid):
                        _w(item["event"], item["data"])
                except (StopIteration, BrokenPipeError, ConnectionResetError):
                    pass

            # ── статика (новый интерфейс) ──────────────────────────────────
            def do_HEAD(self) -> None:  # noqa: N802
                self._route("HEAD")

            def _static(self, path: str) -> None:
                if path in ("", "/"):
                    path = "/index.html"
                webroot = WEB_DIR.resolve()
                candidate = (webroot / path.lstrip("/")).resolve()
                try:
                    candidate.relative_to(webroot)
                except ValueError:
                    self._send_json(403, {"error": "запрещено"})
                    return
                if not candidate.is_file():
                    candidate = webroot / "index.html"   # SPA-fallback
                ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                f = candidate
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control",
                                 "no-cache" if f.suffix in (".html", ".js", ".css")
                                 else "max-age=300")
                self.end_headers()
                self.wfile.write(data)

            def _route_static(self) -> None:
                from urllib.parse import urlparse
                path = urlparse(self.path).path
                if not self._auth_ok():
                    self._send_json(401, {"error": "требуется Bearer-токен"})
                    return
                self._static(path)

        # статика обрабатывается отдельным ветвлением в _route
        _orig_route = Handler._route

        def _route_with_static(self, method: str) -> None:
            from urllib.parse import urlparse
            path = urlparse(self.path).path
            if path.startswith("/api/") or path == "/api":
                _orig_route(self, method)
                return
            if method == "HEAD":
                self._send_json(200, {})
                return
            self._route_static()

        Handler._route = _route_with_static
        return Handler


def run_server(host: str = "0.0.0.0", port: int = 8090, block: bool = True) -> LumenServer:
    server = LumenServer()
    server.serve(host=host, port=port, block=block)
    return server
