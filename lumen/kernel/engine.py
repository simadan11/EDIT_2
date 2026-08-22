"""
LUMEN — ядро LUMEN-1 (LumenEngine).

Конвейер «Луч» — шесть стадий обработки каждого запроса:

    1. ПРИЁМ      ThreatGuard.check_intake  — нормализация, лимиты, спам
    2. ЗАЩИТА     ThreatGuard.check_threats — инъекции, рискованные намерения
    3. АНАЛИЗ     IntentPlanner.plan        — намерения, слоты, инструменты
    4. КОНТЕКСТ   ContextWeaver.weave       — персона + факты + история + результаты
    5. ИНСТРУМЕНТЫ ToolRegistry.run         — исполнение с таймаутами и изоляцией
    6. ГЕНЕРАЦИЯ  Backend.generate          — LUMEN Core: единственный модуль,
                                              собственный ИИ (офлайн, без API)
    + ЗАПОМИНАНИЕ MemoryFabric              — сессия, авто-факты, эпизодический слой

На каждой стадии LUMEN ведёт «мысли» (thoughts) — честный трассирующий
след рассуждений: что принял, какое намерение увидел, какие инструменты
вызвал, какой контекст собрал, как сгенерировал. Мысли летят в события
конвейера (stage "thought"), попадают в EngineResponse и в журнал
lumen_data/thoughts.jsonl (UI показывает их в реальном времени).

Две API-поверхности:
    engine.process(message)   → EngineResponse (единым пакетом)
    engine.stream(message)    → генератор событий (для SSE/UI)
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..config import DATA_DIR
from ..persona import build_system_prompt
from .backends import get_backend, LumenCoreBackend
from .context import ContextWeaver
from .events import EventBus
from .learning import LearningLoop
from .memory import MemoryFabric
from .planner import IntentPlanner
from .safety import ThreatGuard
from .tools import ToolRegistry


@dataclass
class EngineResponse:
    message_id: str
    session_id: str
    reply: str
    ok: bool = True
    risk: str = "safe"
    intent: str = "chat"
    intents: List[str] = field(default_factory=list)
    confidence: float = 0.3
    tools_used: List[Dict[str, Any]] = field(default_factory=list)
    facts_used: List[str] = field(default_factory=list)
    backend: str = ""
    fallback: bool = False
    latency_ms: int = 0
    harvested: List[Dict[str, Any]] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    # «мысли» LUMEN: честный трассирующий след рассуждений по стадиям
    thoughts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "reply": self.reply,
            "ok": self.ok,
            "risk": self.risk,
            "intent": self.intent,
            "intents": self.intents,
            "confidence": self.confidence,
            "tools_used": self.tools_used,
            "facts_used": self.facts_used,
            "backend": self.backend,
            "fallback": self.fallback,
            "latency_ms": self.latency_ms,
            "harvested": self.harvested,
            "blocked_reasons": self.blocked_reasons,
            "thoughts": self.thoughts,
        }


class LumenEngine:
    """Оркестратор конвейера «Луч»."""

    def __init__(self, config, memory: Optional[MemoryFabric] = None,
                 registry: Optional[ToolRegistry] = None,
                 learning: Optional[LearningLoop] = None,
                 events: Optional[EventBus] = None) -> None:
        self.config = config
        self.events = events or EventBus()
        self.memory = memory or MemoryFabric(
            memory_path=config.get("memory.path"),
            max_facts=int(config.get("memory.max_facts", 400)))
        self.learning = learning or LearningLoop()
        self.guard = ThreatGuard(
            max_input_length=int(config.get("safety.max_input_length", 8000)),
            block_injection=bool(config.get("safety.block_injection", True)))
        self.planner = IntentPlanner()
        self.weaver = ContextWeaver(config, self.memory)
        self.registry = registry or ToolRegistry()
        self.backend = get_backend(config)
        # деградационный пол — всегда LUMEN Core (собственный модуль)
        self._lhc = self.backend if isinstance(self.backend, LumenCoreBackend) \
            else LumenCoreBackend(persona={
                "name": config.get("persona.name", "LUMEN"),
                "style": config.get("persona.style", "balanced")})
        # последние планы сессий — для анафоры («а в Киеве?»)
        self._last_plans: Dict[str, Any] = {}
        # журнал «мыслей» (lumen_data/thoughts.jsonl) — путь настраивается
        # в тестах; по умолчанию — общий data-каталог платформы
        self.thoughts_path = DATA_DIR / "thoughts.jsonl"

    # ── публичное API ────────────────────────────────────────────────────────
    def process(self, message: str, session_id: Optional[str] = None,
                on_event: Optional[Callable[[Dict[str, Any]], None]] = None) -> EngineResponse:
        t0 = time.time()
        response: Dict[str, Any] = {
            "message_id": uuid.uuid4().hex[:12],
            "session_id": session_id or self.memory.current_session() or "",
            "ok": True, "risk": "safe", "intent": "chat", "intents": [],
            "confidence": 0.3, "tools_used": [], "facts_used": [],
            "backend": self.backend.name, "fallback": False,
            "harvested": [], "blocked_reasons": [], "reply": "",
            "thoughts": [],
        }
        events: List[Dict[str, Any]] = []

        def emit(stage: str, **payload: Any) -> None:
            ev = {"stage": stage, "ts": round(time.time() - t0, 3), **payload}
            events.append(ev)
            self.events.publish("lumen.stage", **ev, message_id=response["message_id"])
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception:
                    pass

        def think(text: str) -> None:
            """Записать «мысль» в трассу рассуждений и выпустить её в события."""
            thought = {"stage": "thought", "text": text,
                       "ts": round(time.time() - t0, 3)}
            response["thoughts"].append(text)
            events.append(thought)
            self.events.publish("lumen.thought", **thought,
                                message_id=response["message_id"])
            if on_event is not None:
                try:
                    on_event(thought)
                except Exception:
                    pass

        # ── сессия ────────────────────────────────────────────────────────────
        sid = response["session_id"] or self.memory.new_session()
        response["session_id"] = sid

        # ── 1+2. приём и защита ───────────────────────────────────────────────
        intake = self.guard.process(message)
        response["risk"] = intake.risk
        response["blocked_reasons"] = intake.reasons
        emit("intake", risk=intake.risk, tags=intake.tags)
        think(f"Принял запрос ({len(intake.text)} зн.), уровень риска — {intake.risk}.")
        if not intake.ok:
            response["ok"] = False
            response["reply"] = self._blocked_reply(intake)
            think("Запрос не прошёл защитный слой — отвечаю стандартной отсылкой.")
            self._finalize(response, t0, emit, intake.text)
            return EngineResponse(**response)

        # ── 3. анализ (с анафорой: учитываем план предыдущего запроса) ───────
        plan = self.planner.plan(intake.text, last=self._last_plans.get(sid))
        self._last_plans[sid] = plan
        response["intent"] = plan.primary
        response["intents"] = plan.intents
        response["confidence"] = plan.confidence
        emit("plan", **plan.to_dict())
        slots = plan.to_dict().get("slots") or {}
        think("Анализ: намерение "
              + ", ".join(plan.intents)
              + f" (уверенность {plan.confidence:.0%})"
              + (f", слоты: {', '.join(slots)}" if slots else "") + ".")

        # ── 4a. контекст (первый проход: без инструментов) ────────────────────
        ctx = self.weaver.weave(session_id=sid,
                                learned_prefs=self.learning.learned_preferences(),
                                last_user_override=intake.text)

        # ── 5. инструменты ────────────────────────────────────────────────────
        tool_results: List[Dict[str, Any]] = []
        # capability-инструмент подмешиваем для identity/capability
        if plan.primary in ("identity", "capability") and plan.primary == "capability":
            pass  # LUMEN Core сам ответит; инструмент не обязателен
        for call in plan.tool_calls:
            if self.registry.get(call.name) is None:
                tool_results.append({"name": call.name, "ok": False,
                                     "error": "не зарегистрирован", "ms": 0, "text": ""})
                emit("tool", name=call.name, ok=False, error="не зарегистрирован")
                think(f"Инструмент {call.name} не зарегистрирован — обхожу без него.")
                continue
            think(f"Вызываю инструмент {call.name}.")
            res = self.registry.run(call.name, call.args)
            tr = {"name": call.name, "args": call.args, **res.to_dict()}
            tool_results.append(tr)
            response["tools_used"].append(
                {"name": call.name, "ok": res.ok, "ms": res.ms,
                 "error": res.error or None})
            emit("tool", name=call.name, ok=res.ok, ms=res.ms,
                 text=(res.text or res.error or "")[:300])
            think(f"Инструмент {call.name}: {'ок' if res.ok else 'ошибка'} за {res.ms} мс.")

        # ── 4b. контекст (финальный: с результатами инструментов) ────────────
        ctx = self.weaver.weave(session_id=sid, tool_results=tool_results,
                                learned_prefs=self.learning.learned_preferences(),
                                last_user_override=intake.text)
        response["facts_used"] = ctx["facts"]
        emit("context", facts=len(ctx["facts"]),
             history=len(ctx["messages"]), tokens=ctx["token_estimate"])
        think(f"Собрал контекст: {len(ctx['facts'])} факт(ов) из памяти, "
              f"{len(ctx['messages'])} реплик истории.")

        # ── 6. генерация ──────────────────────────────────────────────────────
        # Единственный модуль — LUMEN Core (собственный ИИ, офлайн).
        # Сеть и API-ключи не используются.
        messages = self.weaver.final_messages(ctx)
        fallback = False
        if plan.primary == "blocked" or intake.risk == "blocked":
            think("Запрос заблокирован защитным слоем — не генерирую, отвечаю вежливо.")
            reply = self._blocked_reply(intake)
        else:
            think("Генерирую ответ: LUMEN Core (офлайн, без внешних API).")
            try:
                # LUMEN Core всегда получает план, результаты инструментов
                # и факты; исходный текст — без префиксов контекста
                reply = self.backend.generate(
                    ctx["system"], messages,
                    temperature=float(self.config.get("backend.temperature", 0.7)),
                    text=intake.text,
                    plan=plan.to_dict(), tool_results=tool_results,
                    facts=ctx["facts"], risk=intake.risk)
                if not reply:
                    raise RuntimeError("модуль вернул пустой ответ")
            except Exception as e:  # noqa: BLE001 — страховочный повтор
                fallback = True
                response["fallback"] = True
                think(f"Основная генерация дала сбой ({e}) — деградирую на резервный проход.")
                reply = self._lhc.generate(
                    ctx["system"], messages,
                    temperature=float(self.config.get("backend.temperature", 0.7)),
                    text=intake.text,
                    plan=plan.to_dict(), tool_results=tool_results,
                    facts=ctx["facts"], risk=intake.risk)
                reply += f"\n\n_Была ошибка в обработке ({e}); ответ дал LUMEN Core._"

        # ── запоминание ───────────────────────────────────────────────────────
        harvested = self.memory.harvest(intake.text)
        response["harvested"] = harvested
        self.memory.add_turn(sid, "user", intake.text)
        self.memory.add_turn(sid, "lumen", reply,
                             meta={"message_id": response["message_id"],
                                   "intent": plan.primary,
                                   "last_user": intake.text[:300]})
        emit("remember", harvested=len(harvested), session=sid)
        if harvested:
            think(f"Запомнил новых фактов: {len(harvested)} — {', '.join(h['value'] for h in harvested[:3])}.")
        else:
            think("Сохраняю реплики в сессию.")

        # ── статистика обучения ───────────────────────────────────────────────
        self.learning.record_request(
            plan.primary,
            [t["name"] for t in response["tools_used"]],
            fallback=fallback)

        response["reply"] = reply
        self._finalize(response, t0, emit, intake.text)
        return EngineResponse(**response)

    def stream(self, message: str,
               session_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """Генератор событий конвейера для SSE/UI.

        Конвейер работает в отдельном потоке; события стадий улетают
        в UI по мере их появления (приём → защита → анализ → инструменты
        → контекст → генерация → запоминание → готово).
        """
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        def on_event(ev: Dict[str, Any]) -> None:
            q.put({"event": ev.get("stage", "stage"), "data": ev})

        def _runner() -> None:
            try:
                resp = self.process(message, session_id=session_id, on_event=on_event)
                q.put({"event": "result", "data": resp.to_dict()})
            except Exception as e:  # noqa: BLE001
                q.put({"event": "error",
                       "data": {"error": f"{type(e).__name__}: {e}"}})
            finally:
                q.put({"event": "end", "data": {}})

        yield {"event": "start",
               "data": {"message": message[:200],
                        "session_id": session_id or self.memory.current_session() or None}}
        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        while True:
            item = q.get()
            yield item
            if item["event"] == "end":
                break

    # ── служебное ────────────────────────────────────────────────────────────
    def _blocked_reply(self, intake) -> str:
        if intake.tags and any(t.startswith("spam") for t in intake.tags):
            return ("Запрос выглядит как спам-повтор — я его не обрабатываю. "
                    "Пришлите текст нормальным сообщением.")
        if intake.tags and any(t == "empty" for t in intake.tags):
            return "Похоже, сообщение пустое. Напишите что-нибудь — и я отработаю."
        if intake.risk == "blocked":
            return ("Запрос заблокирован защитным слоем: в нём есть признаки "
                    "prompt-injection (попытка переопределить правила ядра). "
                    "Сформулируйте вопрос напрямую — я рад помочь.")
        return "Не удалось разобрать запрос. Попробуйте сформулировать его иначе."

    def _finalize(self, response: Dict[str, Any], t0: float, emit,
                  message_text: str = "") -> None:
        response["latency_ms"] = int((time.time() - t0) * 1000)
        emit("done", latency_ms=response["latency_ms"],
             backend=self.backend.name, fallback=response["fallback"])
        self._journal_thoughts(response, message_text)

    def _journal_thoughts(self, response: Dict[str, Any], message_text: str) -> None:
        """Пишет след рассуждений в журнал мыслей (lumen_data/thoughts.jsonl)."""
        path = getattr(self, "thoughts_path", None)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": time.time(),
                "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                "message_id": response.get("message_id", ""),
                "session_id": response.get("session_id", ""),
                "message": (message_text or "")[:300],
                "intent": response.get("intent", ""),
                "risk": response.get("risk", ""),
                "thoughts": response.get("thoughts", []),
                "reply": (response.get("reply") or "")[:300],
                "latency_ms": response.get("latency_ms", 0),
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def thoughts(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Последние записи журнала мыслей (новые в конце)."""
        path = getattr(self, "thoughts_path", None)
        if not path or not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out


# ── фабрика «ядро + все части» ───────────────────────────────────────────────

def create_engine(config, import_legacy_memory: bool = True) -> LumenEngine:
    """Собирает готовое ядро: память (с legacy-импортом), инструменты, обучение."""
    from ..tools import build_default_tools
    memory = MemoryFabric(
        memory_path=config.get("memory.path"),
        max_facts=int(config.get("memory.max_facts", 400)))
    if import_legacy_memory:
        try:
            from ..config import LEGACY_MEMORY_PATH
            if LEGACY_MEMORY_PATH.exists() and memory.count() == 0:
                memory.import_legacy(LEGACY_MEMORY_PATH)
        except Exception:
            pass
    registry = build_default_tools(config, memory)
    return LumenEngine(config, memory=memory, registry=registry)
