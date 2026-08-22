"""
LUMEN — Desktop: настольная программа (tkinter, чистая стандартная библиотека).

    python -m lumen desktop

Настольный компаньон в духе классических ассистентов:

  • окно с диалогом — то же ядро LUMEN-1, та же память и данные
    (lumen_data/), что и веб-платформа;
  • «LUMEN думает…» — живая строка мыслей: видно, что делает конвейер
    прямо во время обработки;
  • голос — LUMEN может говорить ответы (TTS через pyttsx3, если
    установлен:  pip install pyttsx3  — на Windows работает с SAPI5);
  • самообучение — фоновый AutoLearner (по умолчанию каждую минуту)
    работает и в десктопе.

Никаких внешних AI-API: всё — LUMEN Core, локально и офлайн.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Dict, Optional

from . import BRAND_NAME, MODEL_NAME, TAGLINE, VERSION
from .config import LumenConfig
from .kernel.engine import EngineResponse, create_engine
from .kernel.learning import AutoLearner

APP_TITLE = f"{BRAND_NAME} — Desktop · {MODEL_NAME} · v{VERSION}"


# ═══════════════════════════════════════════════════════════════════════════
# DesktopSession — логика сессии без GUI (используется и окном, и тестами)
# ═══════════════════════════════════════════════════════════════════════════

class DesktopSession:
    """Сессия десктопа: ядро + сессия памяти + фоновое самообучение."""

    def __init__(self, config: Optional[LumenConfig] = None) -> None:
        self.config = config or LumenConfig()
        self.engine = create_engine(self.config)
        self.session_id = self.engine.memory.new_session("Desktop")
        self.learner = AutoLearner(
            self.engine,
            interval_sec=float(self.config.get("learning.auto_interval_sec", 60)),
            enabled=bool(self.config.get("learning.auto_enabled", True)))
        self.learner.start()

    def send(self, text: str,
             on_thought: Optional[Any] = None) -> EngineResponse:
        """Обрабатывает реплику пользователя (синхронно).

        on_thought(we, text) — необязательный колбэк: получает мыслящую
        фазу и текст мысли по мере их появления (для живой строки «думает»).
        """
        def _ev(ev: Dict[str, Any]) -> None:
            if on_thought is not None and ev.get("stage") == "thought":
                try:
                    on_thought("thought", ev.get("text", ""))
                except Exception:
                    pass

        return self.engine.process(text, session_id=self.session_id,
                                   on_event=_ev)

    def stop(self) -> None:
        self.learner.stop()


# ═══════════════════════════════════════════════════════════════════════════
# GUI (tkinter)
# ═══════════════════════════════════════════════════════════════════════════

def run(config: Optional[LumenConfig] = None) -> int:
    """Запускает десктоп. Возвращает 0 (или код ошибки)."""
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except ImportError:
        print("  tkinter не найден. На Windows Python обычно включает его\n"
              "  из коробки; в Linux:  sudo apt install python3-tk")
        return 1

    session = DesktopSession(config)
    ui = _LumenDesktopTk(tk, scrolledtext, session)
    try:
        ui.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
    return 0


class _LumenDesktopTk:
    """Окно LUMEN: диалог, строка мыслей, голос, статус самообучения."""

    def __init__(self, tk: Any, scrolledtext: Any, session: DesktopSession) -> None:
        self.tk = tk
        self.session = session
        self.engine = session.engine
        self._ui_q: "queue.Queue[tuple]" = queue.Queue()
        self._last_reply = ""
        self._tts = _make_tts()

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("880x600")
        self.root.minsize(680, 460)

        # ── шапка ──────────────────────────────────────────────────────────
        head = tk.Frame(self.root, padx=14, pady=10)
        head.pack(fill="x")
        tk.Label(head, text="◈ LUMEN", font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(head, text=f"  {MODEL_NAME} · {TAGLINE}",
                 foreground="#888").pack(side="left", pady=(5, 0))
        backend, _reason = self.engine.backend.available()
        self._status = tk.Label(
            head, text=f"  ● {self.engine.backend.display}"
            + ("  ·  голос: готов" if self._tts else "  ·  голос: нужен pyttsx3"),
            foreground="#3fb950" if backend else "#f85149")
        self._status.pack(side="right")

        # ── окно диалога ────────────────────────────────────────────────────
        body = tk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=14)
        self.chat = scrolledtext.ScrolledText(
            body, wrap="word", state="disabled", font=("Consolas", 11),
            relief="flat", padx=10, pady=10)
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_config("user", foreground="#58a6ff",
                             font=("Consolas", 11, "bold"))
        self.chat.tag_config("lumen", foreground="#e6edf3")
        self.chat.tag_config("stage", foreground="#8b949e", font=("Consolas", 9))
        self.chat.tag_config("meta", foreground="#8b949e", font=("Consolas", 9))

        # ── строка «LUMEN думает…» ─────────────────────────────────────────
        self._thought_lbl = tk.Label(self.root, text="", anchor="w",
                                     foreground="#d2a8ff", padx=14)
        self._thought_lbl.pack(fill="x")

        # ── ввод ────────────────────────────────────────────────────────────
        inp = tk.Frame(self.root, padx=14, pady=4)
        inp.pack(fill="x")
        self.entry = tk.Entry(inp, font=("Consolas", 11))
        self.entry.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self._on_send())
        self.entry.focus_set()

        self._voice_var = tk.BooleanVar(value=False)
        tk.Checkbutton(inp, text="🗣 говорить", variable=self._voice_var).pack(side="left", padx=(0, 6))
        tk.Button(inp, text="🗣", width=3,
                  command=lambda: self._speak(self._last_reply)).pack(side="left", padx=(0, 6))
        tk.Button(inp, text="Отправить", command=self._on_send).pack(side="left")

        # ── статус-бар ──────────────────────────────────────────────────────
        self._bar = tk.Label(self.root, anchor="w", foreground="#8b949e",
                             font=("Segoe UI", 9), padx=14, pady=2)
        self._bar.pack(fill="x")
        self._tick_bar()

        self._append("lumen",
                     f"◈ LUMEN запущен (v{VERSION}). Ядро: "
                     f"{self.engine.backend.display}. Спросите меня о чём угодно — "
                     "я отвечаю, думаю на глазах и учусь сам.\n\n")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._pump)

    # ── диалог ───────────────────────────────────────────────────────────────
    def _append(self, tag: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _ui(self, fn: Any) -> None:
        """Вызывает fn в потоке GUI (через очередь)."""
        self._ui_q.put((fn, ()))

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._ui_q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _on_send(self) -> None:
        text = self.entry.get().strip()
        if not text or self._busy:
            return
        self._busy = True
        self.entry.delete(0, "end")
        self._append("user", f"\nВы  › {text}\n")
        self._append("stage", "   … LUMEN думает\n")
        self._thought_lbl.configure(text="   … LUMEN думает")

        def worker() -> None:
            def on_thought(phase: str, t: str) -> None:
                self._ui(self._thought_lbl.configure, ("   💭 " + t[:160],))

            try:
                resp = self.session.send(text, on_thought=on_thought)
                self._last_reply = resp.reply
                meta = f"   ⚙ {resp.intent} · {resp.latency_ms} мс" \
                       + (f" · инструменты: {', '.join(t['name'] for t in resp.tools_used)}"
                          if resp.tools_used else "") + "\n"
                self._ui(self._append, ("lumen", f"\nLUMEN › {resp.reply}\n"))
                self._ui(self._append, ("meta", meta))
                self._ui(self._thought_lbl.configure, ("",))
                if self._voice_var.get():
                    self._ui(self._speak, (resp.reply,))
            except Exception as e:  # noqa: BLE001
                self._ui(self._append, ("meta", f"\n   ✕ ошибка: {e}\n"))
                self._ui(self._thought_lbl.configure, ("",))
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True).start()

    # ── голос ────────────────────────────────────────────────────────────────
    def _speak(self, text: str) -> None:
        if not text:
            return
        if self._tts is None:
            self._thought_lbl.configure(
                text="   🗣 Голос: установите pyttsx3 (pip install pyttsx3)")
            return

        def worker() -> None:
            try:
                self._tts(text)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    # ── статус-бар ───────────────────────────────────────────────────────────
    def _tick_bar(self) -> None:
        s = self.session.learner.status()
        b = self.engine.backend
        ok, _r = b.available()
        self._bar.configure(
            text=f"{BRAND_NAME} v{VERSION} · {b.name} ({'●' if ok else '○'})"
                 f" · инструментов: {len(self.engine.registry.list())}"
                 f" · фактов в памяти: {self.engine.memory.count()}"
                 f" · самообучение: {'каждые ' + str(int(s['interval_sec'])) + ' с' if s['enabled'] else 'выключено'}"
                 f" · циклов: {s['runs']}"
                 f" · последний: {s['last_run_iso'] or '—'}")
        self.root.after(5000, self._tick_bar)

    # ── выход ────────────────────────────────────────────────────────────────
    def _on_close(self) -> None:
        self.session.stop()
        self.root.destroy()

    def mainloop(self) -> None:
        self.root.mainloop()


# ═══════════════════════════════════════════════════════════════════════════
# TTS (опциональный pyttsx3)
# ═══════════════════════════════════════════════════════════════════════════

def _make_tts() -> Optional[Any]:
    """Возвращает callable(text), говорящий голосом, или None."""
    try:
        import pyttsx3  # type: ignore
    except ImportError:
        return None

    def speak(text: str) -> None:
        engine = pyttsx3.init()
        try:
            voices = engine.getProperty("voices") or []
            for v in voices:  # ищем русский голос
                name = (getattr(v, "name", "") or "").lower()
                if "russian" in name or "русск" in name:
                    engine.setProperty("voice", v.id)
                    break
            engine.setProperty("rate", 180)
            engine.say(text[:4000])
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:
                pass

    return speak
