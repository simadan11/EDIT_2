"""
LUMEN — анализ намерений (IntentPlanner).

Стадия 3 конвейера «Луч». Детерминированный классификатор намерений
(RU/EN) + извлечение слотов + выбор инструментов. Детерминированность
сделана осознанно: план должен быть быстрым, объяснимым и
воспроизводимым; LLM участвует только на стадии генерации.

Намерения (primary + secondary):
  identity, capability, greeting, farewell, thanks,
  time, math, weather, system_status, file_search,
  memory_save, memory_recall, web_info,
  date_math (календарная математика), text_ops (статистика текста),
  joke, riddle, knowledge (встроенная база знаний LUMEN Core),
  chat
Плюс анафора: короткое продолжение («а в Киеве?») наследует
последнее намерение сессии (plan(text, last=...)).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── паттерны намерений: (намерение, [регулярки], вес) ────────────────────────
_INTENTS: List[Dict[str, Any]] = [
    {"name": "math", "weight": 0.95, "patterns": [
        r"(вычисли|посчитай|сколько будет|сколько будет|разность|произведение|сумма)",
        r"how\s+much\s+is", r"calculate", r"what\s+is\s+\d",
        r"^\s*[-+]?\d[\d\s+\-*/%().,^×÷]{1,60}=?\s*$",
    ]},
    {"name": "weather", "weight": 0.9, "patterns": [
        r"погод", r"temperatur", r"температур", r"дожд", r"снегопад", r"ветер\s+(сейчас|сила)",
        r"weather", r"rain(?:ing)?\b", r"snow(?:ing)?\b",
    ]},
    {"name": "system_status", "weight": 0.9, "patterns": [
        r"состояние\s+(системы|компьютера|машины)", r"ресурсы\s+системы",
        r"как\s+(там\s+|дела\s+у\s+)?(система|компьютер|машина)\b", r"как\s+у\s+(системы|компьютера|машины)",
        r"(cpu|ram|gpu|оперативн|нагрузк)\s*(загрузк|использован|\b)",
        r"как\s+(компьютер|система|машина)\b", r"system\s+status", r"cpu\s+usage",
        r"сколько\s+(ram|памяти)\s+свободно",
    ]},
    {"name": "time", "weight": 0.88, "patterns": [
        r"сколько\s+(сейчас\s+)?врем", r"какое\s+время", r"какое\s+(сегодня\s+)?(число|дата)",
        r"какая\s+дата", r"какой\s+сегодня\s+день", r"какой\s+день\s+сегодня",
        r"current\s+time", r"what\s+time", r"what'\s*s\s+the\s+date",
        r"what\s+is\s+the\s+date",
    ]},
    {"name": "file_search", "weight": 0.85, "patterns": [
        r"найди\s+файл", r"поищи\s+файл", r"где\s+файл", r"файл(ы)?\s+(с|назван)",
        r"найди\s+в\s+проекте", r"search\s+(for\s+)?files?", r"find\s+(the\s+)?file",
    ]},
    {"name": "memory_save", "weight": 0.9, "patterns": [
        r"запомни\b", r"запиши\s+в\s+память", r"пометь\s+это", r"remember\s+(that\s+|this|to)",
        r"запомни\s+что",
    ]},
    {"name": "memory_recall", "weight": 0.9, "patterns": [
        r"что\s+ты\s+помниш", r"вспомни\b", r"ты\s+помниш", r"назови\s+(моё|мои|мои\s+)",
        r"recall", r"what\s+do\s+you\s+remember", r"что\s+я\s+(говорил|рассказывал|рассказывала)",
    ]},
    {"name": "web_info", "weight": 0.7, "patterns": [
        r"поищи\s+в\s+интернете", r"найди\s+(информацию|новости)\s+в\s+сети",
        r"веб-поиск", r"вебпоиск", r"search\s+the\s+web", r"news\s+today",
    ]},
    {"name": "date_math", "weight": 0.9, "patterns": [
        r"сколько\s+(дн\w+|нед\w+|месяц\w+|лет\w*)\s+до\s",
        r"(дн\w+|месяц\w+)\s+осталось\s+до",
        r"какое\s+число\s+будет\s+через",
        r"что\s+будет\s+через\s+\d+\s*(дн|нед|мес|год)",
        r"days\s+until", r"how\s+many\s+days",
    ]},
    {"name": "text_ops", "weight": 0.82, "patterns": [
        r"сколько\s+(в\s+)?(этом\s+)?(тексте|предложении|словах)",
        r"сколько\s+(слов|знаков|букв|символов)\b",
        r"подсчитай\s+(слова|знаки|буквы|символы)",
        r"посчитай\s+(слова|знаки|буквы|символы)",
        r"word\s+count", r"count\s+(the\s+)?words",
    ]},
    {"name": "joke", "weight": 0.85, "patterns": [
        r"расскажи\s+(мне\s+)?(шутк|анекдот)", r"\bшутк\w*",
        r"что-нибудь\s+смешн", r"(смешн\w+|анекдот)\s+(расскажи|придумай)",
        r"придумай\s+шутк",
        r"\bjoke\b", r"make\s+me\s+laugh", r"tell\s+me\s+a\s+joke",
    ]},
    {"name": "riddle", "weight": 0.85, "patterns": [
        r"загадк\w*", r"загадай\s+(мне\s+)?", r"\briddle\b",
    ]},
    {"name": "knowledge", "weight": 0.62, "patterns": [
        r"кто\s+(такой|такая|был|была)\s+[a-zа-яё]{2,}",
        r"что\s+такое\s+\S", r"расскажи\s+(мне\s+)?(о|про)\s+\S",
        r"как\s+(ты\s+|устроен\w*\s+)?(работает|работаешь)\s",
        r"как\s+устроен\w*\s+\w+",
        r"сколько\s+(секунд|минут|часов|дней|месяцев|лет|раундов)\s+в\s+",
        r"какая\s+(самая\s+)?(высокая|большая|длинная|быстрая|древняя|столица|страна|река|гора)",
        r"где\s+(находится|расположен\w*)\s",
        r"почему\s+(небо|снег|солнце|луна|закат|восход|радуга|дождь)",
        r"explain\s+", r"who\s+(was|is)\s+the",
        r"what\s+is\s+the\s+(capital|area|population)", r"how\s+(far|old|many)\s",
    ]},
    {"name": "identity", "weight": 0.92, "patterns": [
        r"кто\s+ты\b", r"как\s+тебя\s+зовут", r"кто\s+тебя\s+создал", r"кто\s+твой\s+создатель",
        r"расскажи\s+о\s+себе", r"что\s+ты\s+такое", r"что\s+такое\s+lumen",
        r"что\s+ты\s+представляешь",
        r"what\s+are\s+you", r"who\s+are\s+you", r"who\s+created\s+you", r"tell\s+me\s+about\s+yourself",
    ]},
    {"name": "capability", "weight": 0.85, "patterns": [
        r"что\s+ты\s+умеешь", r"твои\s+возможности", r"что\s+ты\s+можешь\s+(делать|сделать)",
        r"список\s+(инструментов|функций)", r"what\s+can\s+you\s+do", r"your\s+capabilities",
        r"какие\s+у\s+тебя\s+инструменты",
    ]},
    {"name": "thanks", "weight": 0.8, "patterns": [
        r"спасибо", r"благодарю", r"thanks", r"thank\s+you", r"миле?\s*фю", r"thanks\s+a\s+lot",
    ]},
    {"name": "farewell", "weight": 0.75, "patterns": [
        r"^\s*пока\b", r"до\s+свидания", r"до\s+встречи", r"всего\s+добра",
        r"bye\b", r"goodbye", r"see\s+you",
    ]},
    {"name": "greeting", "weight": 0.7, "patterns": [
        r"^\s*(привет|здравствуй|здравствуйте|салют|хай|хэй|добрый\s+(день|вечер|утро)|good\s+(morning|afternoon|evening)|hi|hello)\b",
    ]},
]

_COMPILED: List[Dict[str, Any]] = [
    {**item, "rx": [re.compile(p, re.IGNORECASE) for p in item["patterns"]]}
    for item in _INTENTS
]

_PRIORITY = ["identity", "capability", "math", "weather", "system_status", "time",
            "file_search", "memory_save", "memory_recall", "web_info",
            "date_math", "text_ops", "joke", "riddle", "knowledge",
            "thanks", "farewell", "greeting", "chat"]


@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    primary: str = "chat"
    intents: List[str] = field(default_factory=list)
    slots: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[ToolCall] = field(default_factory=list)
    confidence: float = 0.3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": self.primary, "intents": self.intents,
            "slots": self.slots,
            "tool_calls": [{"name": t.name, "args": t.args} for t in self.tool_calls],
            "confidence": round(self.confidence, 3),
        }


# ── извлечение слотов ────────────────────────────────────────────────────────
_MATH_EXPR = re.compile(
    r"[-+]?\d[\d\s+\-*/%().,^×÷]{1,80}(?=[\s.!?]|$)", re.IGNORECASE
)
_QUOTED = re.compile(r"[«\"']([^»\"']{1,60})[»\"']")
_CITY_RU = re.compile(
    r"(?:в\s+городе\s+|в\s+)[A-ZА-ЯЁ][A-Za-zА-Яа-яё\-]{2,30}\b", re.I)


def _extract_math(text: str) -> Optional[str]:
    m = _MATH_EXPR.search(text)
    if not m:
        return None
    expr = m.group(0).strip().rstrip("?!. ")
    # отбрасываем «числа-даты» и одинокие числа
    if not re.search(r"[+\-*/%^×÷]", expr) and not re.fullmatch(r"[-+]?\d+", expr):
        return None
    if re.search(r"\d{1,2}\s*(январ|феврал|марта|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр)", expr, re.I):
        return None
    return expr


def _extract_file_query(text: str) -> Dict[str, str]:
    quoted = _QUOTED.search(text)
    if quoted:
        return {"pattern": quoted.group(1).strip()}
    m = re.search(r"(?:файл(ы)?|file|files)\s*(?:с\s+названием|назван)?\s*[:«\"]?\s*([A-Za-z0-9_*\-. ]{2,60})\b",
                  text, re.I)
    if m:
        return {"pattern": m.group(1).strip()}
    m2 = re.search(r"([A-Za-z0-9_*\-.]+\.(?:py|md|txt|json|html|js|css|pdf|docx?|xlsx?))",
                   text, re.I)
    if m2:
        return {"pattern": m2.group(1).strip()}
    return {}


def _extract_city(text: str) -> str:
    quoted = _QUOTED.search(text)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(r"в\s+([A-ZА-ЯЁ][A-Za-zА-Яа-яё\-]{2,30})\b", text)
    if m:
        return m.group(1).strip()
    return ""


def _extract_memory_save(text: str) -> Dict[str, str]:
    m = re.search(r"запомни,?\s+что\s+(.+)$", text, re.I)
    payload = (m.group(1).strip(" .!?") if m else text)
    name = re.search(r"(?:меня\s+зовут|мое\s+имя)\s+([A-ZА-ЯЁ][a-zа-яё]{1,20})", payload, re.I)
    if name:
        return {"category": "identity", "key": "user_name",
                "value": name.group(1).strip()}
    city = re.search(r"живу\s+в\s+([A-ZА-ЯЁ][A-Za-zА-Яа-яё\- ]{1,24})", payload, re.I)
    if city:
        return {"category": "identity", "key": "city",
                "value": city.group(1).strip()}
    return {"category": "notes", "key": "auto_note",
            "value": payload[:120] or "новая заметка"}


class IntentPlanner:
    """Классификация намерений + слоты + выбор инструментов."""

    _ANAPHORA_START = re.compile(
        r"^(а|ну|тогда|просто|короче|а что|а как|и что|и как)\b", re.IGNORECASE)
    _BARE_MATH = re.compile(r"[-+]?[0-9][0-9+\-*/^×÷.()\s]{0,39}")
    _NOT_CITY = {"воздухе", "воде", "доме", "офисе", "городе", "стране",
                 "мире", "интернете", "заливе", "лесу", "парке", "центре"}

    def plan(self, text: str, last: Optional["Plan"] = None) -> Plan:
        """Стадия 3. `last` — план предыдущего запроса сессии (для анафоры:
        «а в Киеве?», «а 5×7?» наследуют предыдущее намерение)."""
        low = text.lower().strip()
        scored: List[str] = []
        for item in _COMPILED:
            for rx in item["rx"]:
                if rx.search(low) or rx.search(text):
                    scored.append(item["name"])
                    break

        # гарантируем порядок по приоритету
        intents = [i for i in _PRIORITY if i in scored]

        # «напиши/сочини стихотворение о дожде» — творческий текст, не погода
        if "weather" in intents and re.search(r"стихотворени|сочини|напиши", low):
            intents = [i for i in intents if i != "weather"]

        primary = intents[0] if intents else "chat"
        confidence = 0.3
        if intents:
            w = {it["name"]: it["weight"] for it in _COMPILED}
            confidence = max(w.get(i, 0.4) for i in intents)

        slots: Dict[str, Any] = {}
        tool_calls: List[ToolCall] = []

        if primary == "math" or (intents and "math" in intents):
            expr = _extract_math(text)
            if expr:
                slots["expression"] = expr
                tool_calls.append(ToolCall("math.calc", {"expression": expr}))

        if "weather" in intents:
            city = _extract_city(text)
            tool_calls.append(ToolCall("weather.current", {"city": city} if city else {}))

        if "system_status" in intents:
            tool_calls.append(ToolCall("system.status", {}))

        if "time" in intents:
            tool_calls.append(ToolCall("time.now", {}))

        if "file_search" in intents:
            fq = _extract_file_query(text)
            tool_calls.append(ToolCall("files.search", fq))

        if "memory_save" in intents:
            ms = _extract_memory_save(text)
            tool_calls.append(ToolCall("memory.store", ms))

        if "memory_recall" in intents:
            tool_calls.append(ToolCall("memory.recall", {"query": text[:120]}))

        if "web_info" in intents:
            tool_calls.append(ToolCall("legacy.web_search", {"query": text[:160]}))

        # capability/identity/time без инструментов — генератор сам справится
        plan = Plan(primary=primary, intents=intents, slots=slots,
                    tool_calls=tool_calls, confidence=confidence)

        # ── анафора: «а в Киеве?», «а 5*7?» — наследуем последнее намерение ─
        plan = self._resolve_anaphora(text, plan, last)
        return plan

    def _resolve_anaphora(self, text: str, plan: Plan,
                          last: Optional["Plan"]) -> Plan:
        """Короткое продолжение диалога без собственного явного намерения
        наследует намерение/слоты предыдущего запроса (с обновлёнными
        слотами, если они есть: город, выражение)."""
        if last is None or plan.primary != "chat":
            return plan
        inheritable = ("weather", "math", "time", "system_status", "date_math",
                       "text_ops", "file_search")
        if last.primary not in inheritable:
            return plan
        t = text.strip()
        if len(t) > 60:
            return plan
        is_followup = bool(self._ANAPHORA_START.match(t)) or t.endswith("?")
        if not (is_followup or self._BARE_MATH.fullmatch(t)):
            return plan

        city = _extract_city(text)
        if not city:
            # город в предложном падеже в конце: «а в киеве?»
            m = re.search(r"\bв\s+([а-яё]{3,30})\??\s*$", t.lower())
            if m and m.group(1) not in self._NOT_CITY:
                city = m.group(1).capitalize()
        expr = _extract_math(text)

        if last.primary == "weather":
            new_city = city or str((last.slots or {}).get("city") or "")
            plan.primary, plan.intents = "weather", ["weather"]
            plan.slots = {"city": new_city}
            plan.tool_calls = [
                ToolCall("weather.current", {"city": new_city} if new_city else {})]
        elif expr:
            # новое выражение — считаем, даже если раньше был другой вопрос
            plan.primary, plan.intents = "math", ["math"]
            plan.slots = {"expression": expr}
            plan.tool_calls = [ToolCall("math.calc", {"expression": expr})]
        else:
            # повторение последнего намерения с теми же слотами/инструментами
            plan.primary, plan.intents = last.primary, list(last.intents)
            plan.slots = dict(last.slots or {})
            plan.tool_calls = [ToolCall(c.name, dict(c.args)) for c in last.tool_calls]
        plan.confidence = max(plan.confidence, 0.6)
        return plan
