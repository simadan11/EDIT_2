"""
LUMEN — Desktop: настольный компаньон в стиле классического EDIT.

    python -m lumen desktop

Внешность — как у обычного EDIT: тёмный «космос» со звёздами,
неоновое кольцо-лицо, статус-слова (STANDBY / TYPING / THINKING /
SPEAKING), волновая полоса, которая оживает, пока LUMEN думает или
говорит. Внутри — то же ядро LUMEN-1: конвейер «Луч», память,
инструменты, мысли и самообучение каждую минуту (lumen_data/).

Голос — TTS через pyttsx3 (опционально):  pip install pyttsx3
Никаких внешних AI-API: всё — LUMEN Core, локально и офлайн.
"""

from __future__ import annotations

import math
import queue
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import BRAND_NAME, MODEL_NAME, TAGLINE, VERSION
from .config import LumenConfig
from .kernel.engine import EngineResponse, create_engine
from .kernel.learning import AutoLearner

APP_TITLE = f"{BRAND_NAME} — Desktop · {MODEL_NAME} · v{VERSION}"

# ── палитра (как у классического EDIT) ──────────────────────────────────────
BG       = "#00060a"
PANEL    = "#010d14"
PANEL2   = "#010f18"
BORDER   = "#0d3347"
BORDER_B = "#1a5c7a"
PRI      = "#00d4ff"
PRI_DIM  = "#007a99"
PRI_GHO  = "#001f2e"
ACC2     = "#ffcc00"   # THINKING
GREEN    = "#00ff88"
RED      = "#ff3355"
TEXT     = "#8ffcff"
TEXT_DIM = "#3a8a9a"
TEXT_MED = "#5ab8cc"
WHITE    = "#d8f8ff"
BAR_BG   = "#011520"

RING_OUT   = "#00243a"   # кольца лица (внешнее → внутрь)
RING_MID   = "#004a6e"
RING_IN    = "#0099c2"
CORE_IN    = "#7fe9ff"
CORE_WHITE = "#eafdff"


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

        on_thought(we, text) — необязательный колбэк: получает текст
        мысли по мере их появления (для живой строки «думает»).
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
# GUI (tkinter) — «лицо» как у обычного EDIT
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
    """Окно LUMEN: лицо-кольцо (как у EDIT) + диалог + голос + статус."""

    STANDBY  = "STANDBY"
    TYPING   = "TYPING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"

    def __init__(self, tk: Any, scrolledtext: Any, session: DesktopSession) -> None:
        self.tk = tk
        self.session = session
        self.engine = session.engine
        self._ui_q: "queue.Queue[tuple]" = queue.Queue()
        self._last_reply = ""
        self._tts = _make_tts()
        self._busy = False
        self._state = self.STANDBY
        self._t0 = time.time()

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("920x660")
        self.root.minsize(720, 520)
        self.root.configure(bg=BG)

        # ── ЛИЦО (canvas: звёзды + статус-слово + волна + кольцо) ─────────
        self.face = tk.Canvas(self.root, bg=BG, height=236,
                              highlightthickness=0)
        self.face.pack(fill="x")
        self._stars: List[tuple] = []
        self._wav_rects: List[Any] = []
        self._word_id = None
        self._build_stars()
        self._state_word()
        self.face.bind("<Configure>",
                       lambda _e: (self._redraw_static(), self._state_word()))

        # ── окно диалога ──────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(2, 0))
        self.chat = scrolledtext.ScrolledText(
            body, wrap="word", state="disabled", font=("Consolas", 11),
            bg=PANEL, fg=WHITE, relief="flat", padx=12, pady=10,
            insertbackground=PRI, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=BORDER_B)
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_config("user", foreground=PRI, font=("Consolas", 11, "bold"))
        self.chat.tag_config("lumen", foreground=WHITE)
        self.chat.tag_config("stage", foreground=TEXT_DIM, font=("Consolas", 9))
        self.chat.tag_config("meta", foreground=TEXT_DIM, font=("Consolas", 9))

        # ── строка «LUMEN думает…» ────────────────────────────────────────
        self._thought_lbl = tk.Label(
            self.root, text="", anchor="w", bg=BG, fg=ACC2,
            font=("Consolas", 9), padx=18)
        self._thought_lbl.pack(fill="x", pady=(4, 0))

        # ── ввод ─────────────────────────────────────────────────────────
        inp = tk.Frame(self.root, bg=BG)
        inp.pack(fill="x", padx=14, pady=(4, 8))
        inner = tk.Frame(inp, bg=BORDER)
        inner.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.entry = tk.Entry(inner, font=("Consolas", 11), bg=BAR_BG,
                              fg=TEXT, insertbackground=PRI, relief="flat",
                              highlightthickness=0)
        self.entry.pack(fill="both", expand=True, padx=4, pady=4)
        self.entry.bind("<Return>", lambda _e: self._on_send())
        self.entry.bind("<KeyPress>", lambda _e: self._set_state(self.TYPING))
        self.entry.focus_set()

        self._voice_var = tk.BooleanVar(value=False)
        tk.Checkbutton(inp, text="🗣 говорить", variable=self._voice_var,
                       bg=BG, fg=TEXT_MED, activebackground=BG,
                       activeforeground=PRI, selectcolor=BAR_BG,
                       font=("Segoe UI", 9), bd=0, padx=6).pack(side="left")
        tk.Button(inp, text="🗣", width=3, command=lambda: self._speak(self._last_reply),
                  bg=BAR_BG, fg=TEXT_MED, activebackground=BORDER,
                  activeforeground=PRI, relief="flat", font=("Segoe UI", 10),
                  bd=1, highlightbackground=BORDER, highlightcolor=BORDER_B
                  ).pack(side="left", padx=(4, 8))
        tk.Button(inp, text="ОТПРАВИТЬ", command=self._on_send,
                  bg=PRI_GHO, fg=PRI, activebackground=PRI,
                  activeforeground="#001018",
                  relief="flat", font=("Segoe UI", 10, "bold"), bd=0,
                  padx=14, pady=6,
                  highlightbackground=BORDER, highlightcolor=BORDER_B
                  ).pack(side="left")

        # ── статус-бар ────────────────────────────────────────────────────
        self._bar = tk.Label(self.root, anchor="w", bg=PANEL, fg=TEXT_DIM,
                             font=("Segoe UI", 9), padx=14, pady=3)
        self._bar.pack(fill="x", side="bottom")
        self._tick_bar()

        self._append("lumen",
                     f"◈ LUMEN запущен (v{VERSION}). Ядро: "
                     f"{self.engine.backend.display}. Спросите меня о чём угодно — "
                     "я отвечаю, думаю на глазах и учусь сам.\n\n")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._animate)
        self.root.after(100, self._pump)

    # ── ЛИЦО ───────────────────────────────────────────────────────────────
    def _build_stars(self) -> None:
        rnd = random.Random(42)  # стабильное «небо»
        self._stars = []
        for _ in range(90):
            self._stars.append((rnd.random(), rnd.random(),
                                rnd.choice((1.0, 1.0, 1.6, 2.2)),
                                rnd.choice((PRI_GHO, "#0a3a52", "#12506e"))))

    def _redraw_static(self) -> None:
        """Звёзды + фоновая сетка рисуются при создании/ресайзе."""
        c = self.face
        c.delete("stars")
        w, h = c.winfo_width() or 900, c.winfo_height() or 236
        for fx, fy, r, col in self._stars:
            x, y = int(fx * w), int(fy * h)
            c.create_oval(x - r, y - r, x + r, y + r, fill=col,
                          outline="", tags="stars")

    def _state_word(self) -> None:
        """Статус-слово сверху (как у EDIT: буквы разнесены)."""
        c = self.face
        c.delete("word")
        word = " ".join(self._state)
        col = {self.STANDBY: TEXT_MED, self.TYPING: TEXT,
               self.THINKING: ACC2, self.SPEAKING: PRI}[self._state]
        w = c.winfo_width() or 900
        c.create_text(w / 2, 24, text=word, fill=col, tags="word",
                      font=("Courier New", 15, "bold"))

    def _set_state(self, st: str) -> None:
        if st != self._state:
            self._state = st
            self._state_word()

    def _animate(self) -> None:
        """Анимация: волновая полоса + пульс кольца."""
        try:
            c = self.face
            w, h = c.winfo_width() or 900, c.winfo_height() or 236
            t = time.time() - self._t0
            active = self._state in (self.THINKING, self.SPEAKING)
            # волна
            n = 64
            bw, gap = 4, 2
            total = n * (bw + gap)
            x0 = (w - total) / 2
            y0 = 48
            c.delete("anim")
            col = PRI if active else PRI_GHO
            for i in range(n):
                if active:
                    amp = (abs(math.sin(t * 2.1 + i * 0.52)) *
                           (0.45 + 0.55 * abs(math.cos(t * 0.71 + i * 0.29))))
                    bh = 3 + 20 * amp
                else:
                    bh = 2.5 + 1.5 * abs(math.sin(t * 0.9 + i * 0.35))
                x = x0 + i * (bw + gap)
                c.create_rectangle(x, y0 + 12 - bh / 2, x + bw, y0 + 12 + bh / 2,
                                   fill=col, outline="", tags="anim")
            # кольцо-лицо
            cx, cy = w / 2, 172
            if self._state == self.THINKING:
                scale = 1 + 0.05 * math.sin(t * 4.6)
                core = CORE_IN
            elif self._state == self.SPEAKING:
                scale = 1 + 0.09 * abs(math.sin(t * 7.0))
                core = CORE_WHITE
            else:
                scale = 1 + 0.02 * math.sin(t * 1.3)
                core = PRI
            R = 52 * scale
            for r, colr, wdt in ((R, RING_OUT, 1.4), (R - 9, RING_MID, 1.8),
                                 (R - 19, RING_IN, 2.4)):
                c.create_oval(cx - r, cy - r, cx + r, cy + r,
                              outline=colr, width=wdt, tags="anim")
            for r, colr in ((R - 30, RING_IN), (R - 38, RING_MID),
                            (R - 46, RING_OUT)):
                c.create_oval(cx - r, cy - r, cx + r, cy + r,
                              fill=colr, outline="", tags="anim")
            c.create_oval(cx - 16 * scale, cy - 16 * scale,
                          cx + 16 * scale, cy + 16 * scale,
                          fill=core, outline=WHITE, tags="anim")
            self.root.after(50, self._animate)
        except Exception:
            pass  # анимация не должна ронять приложение

    # ── диалог ───────────────────────────────────────────────────────────────
    def _append(self, tag: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _ui(self, fn: Callable, *args: Any) -> None:
        self._ui_q.put((fn, args))

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
        self._set_state(self.THINKING)

        def worker() -> None:
            def on_thought(phase: str, t: str) -> None:
                self._ui(self._thought_lbl.configure,
                         ("   💭 " + t[:160],))

            try:
                resp = self.session.send(text, on_thought=on_thought)
                self._last_reply = resp.reply
                meta = (f"   ⚙ {resp.intent} · {resp.latency_ms} мс"
                        + (f" · инструменты: "
                           + ", ".join(t["name"] for t in resp.tools_used)
                           if resp.tools_used else "") + "\n")
                self._ui(self._append, ("lumen", f"\nLUMEN › {resp.reply}\n"))
                self._ui(self._append, ("meta", meta))
                self._ui(self._thought_lbl.configure, ("",))
                if self._voice_var.get():
                    self._ui(self._speak, (resp.reply,))
                else:
                    self._ui(self._set_state, (self.STANDBY,))
            except Exception as e:  # noqa: BLE001
                self._ui(self._append, ("meta", f"\n   ✕ ошибка: {e}\n"))
                self._ui(self._thought_lbl.configure, ("",))
                self._ui(self._set_state, (self.STANDBY,))
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True).start()

    # ── голос ────────────────────────────────────────────────────────────────
    def _speak(self, text: str) -> None:
        if not text:
            self._set_state(self.STANDBY)
            return
        if self._tts is None:
            self._thought_lbl.configure(
                text="   🗣 Голос: установите pyttsx3 (pip install pyttsx3)")
            return
        self._set_state(self.SPEAKING)

        def worker() -> None:
            try:
                self._tts(text)
            except Exception:
                pass
            finally:
                self._ui(self._set_state, (self.STANDBY,))

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
                 f" · самообучение: "
                 + ("каждые " + str(int(s["interval_sec"])) + " с"
                    if s["enabled"] else "выключено")
                 + f" · циклов: {s['runs']}"
                 + f" · последний: {s['last_run_iso'] or '—'}")
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

def _make_tts() -> Optional[Callable[[str], None]]:
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
