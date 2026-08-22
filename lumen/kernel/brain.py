"""
LUMEN Core — собственный ИИ-модуль платформы.

Это НЕ «заглушка» и не обёртка над облачной моделью: LUMEN Core —
самостоятельный локальный разум, работающий на чистом Python:

  • встроенная база знаний (lumen/knowledge, 100+ фактов, офлайн-поиск);
  • диалоговое состояние — анафора («а в Киеве?», «а 5×7?») и
    составные запросы («сколько времени и какая погода?»);
  • локальные вычислители — календарная математика, текстовая статистика;
  • генератор ответов с вариациями (детерминированная вариативность);
  • честность: если запроса нет в ядре — говорит, чего не хватает.
    Внешних LLM-модулей в платформе нет: всё делает ядро.

Архитектурно Core — исполнитель стадии 6 конвейера «Луч»: план,
инструменты и контекст к нему уже приходят готовыми.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .. import BRAND_NAME, MODEL_NAME, PIPELINE_NAME, TAGLINE
from ..knowledge import BASE as KB, search as kb_search

# ─────────────────────────────────────────────────────────────────────
# Календарная математика
# ─────────────────────────────────────────────────────────────────────

_MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "мае": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}
_MONTH_NAMES = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая",
    6: "июня", 7: "июля", 8: "августа", 9: "сентября", 10: "октября",
    11: "ноября", 12: "декабря",
}

_WD_RU = ["понедельник", "вторник", "среда", "четверг", "пятница",
          "суббота", "воскресенье"]


def _parse_ru_date(text: str, today: date) -> Optional[date]:
    """Парсит русские даты: «31 декабря», «1 января 2027», «31.12.2026»."""
    t = (text or "").lower()
    # число + месяц
    m = re.search(r"(\d{1,2})\s+(" + "|".join(_MONTHS_RU) + r")", t)
    if m:
        day, mon = int(m.group(1)), _MONTHS_RU[m.group(2)]
        ym = re.search(r"(\d{4})", t)
        year = int(ym.group(1)) if ym else today.year
        if not (1 <= day <= 31 and 1 <= mon <= 12 and 1990 <= year <= 2100):
            return None
        try:
            d = date(year, mon, day)
        except ValueError:
            return None
        # если дата прошла в этом году и не указан год — берём следующий
        if ym is None and d < today:
            try:
                d = d.replace(year=year + 1)
            except ValueError:
                return None
        return d
    # ДД.ММ.ГГГГ
    m = re.search(r"(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})", t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def days_between(target: date, today: date) -> int:
    return (target - today).days


def _q_tokens(text: str) -> set:
    from ..knowledge.base import _toks
    return _toks(text)


def _share_tokens(text: str, entry: Dict[str, Any]) -> int:
    """Сколько токенов запроса есть в вопросах записи (ядро релевантности)."""
    qt = _q_tokens(text)
    et: set = set()
    for t in entry["q"]:
        et |= _q_tokens(t)
    return len(qt & et)


# ─────────────────────────────────────────────────────────────────────
# Текстовая статистика
# ─────────────────────────────────────────────────────────────────────

_QUOTED = re.compile(r"[«\"']([^»\"']{2,400})[»\"']")


def text_stats(quoted: str) -> Dict[str, int]:
    words = re.findall(r"\S+", quoted.strip())
    return {
        "chars": len(quoted.replace(" ", "")),
        "chars_with_spaces": len(quoted),
        "words": len(words),
    }


# ─────────────────────────────────────────────────────────────────────
# Ядро
# ─────────────────────────────────────────────────────────────────────

class LumenCore:
    """Собственный ИИ-модуль платформы. Держит диалоговое состояние."""

    def __init__(self, language: str = "ru",
                 persona_name: str = BRAND_NAME) -> None:
        self.language = language
        self.persona_name = persona_name
        # диалоговое состояние (аналоги рабочей памяти)
        self._last_intent: Optional[str] = None
        self._last_tool_calls: List[Dict[str, Any]] = []
        self._joke_i = 0
        self._riddle_i = 0
        self._greet_i = 0
        self._thanks_i = 0
        self._fb_i = 0
        # сработал ли честный фолбэк (запроса нет в ядре)
        self._fallback_used = False

    # ── утилиты ─────────────────────────────────────────────────────────────
    @staticmethod
    def _variant(seed_text: str, options: List[str]) -> str:
        """Детерминированная вариация шаблона по хэшу сообщения."""
        if len(options) == 1:
            return options[0]
        h = int(hashlib.md5(seed_text.encode("utf-8")).hexdigest(), 16)
        return options[h % len(options)]

    # ── публичное API ───────────────────────────────────────────────────────
    def quick_answer(self, text: str,
                     context: Optional[List[str]] = None,
                     plan: Optional[Dict[str, Any]] = None,
                     tool_results: Optional[List[Dict[str, Any]]] = None,
                     risk: str = "safe",
                     last_intent: Optional[str] = None) -> "tuple[str, bool]":
        """Лёгкий вход (legacy-роутер, прямые вызовы).

        Сам строит план и прогоняет офлайн-инструменты (время, калькулятор).
        Возвращает (ответ, не_покрыто_ядром) — True, если запроса нет в базе
        знаний и ядро ответило честным фолбэком. Внешних модулей нет,
        поэтому вторым элементом просто фиксируем честный фолбэк.
        """
        plan_d = plan or self._build_plan(text)
        existing = tool_results or []
        trs = existing + self._offline_tools(plan_d, existing=existing)
        self._fallback_used = False
        answer = self._answer(text=text, plan=plan_d, tool_results=trs,
                              facts=context or [], risk=risk,
                              last_intent=last_intent)
        return answer, (self._fallback_used and risk == "safe")

    def think(self, *, text: str, plan: Dict[str, Any],
              tool_results: List[Dict[str, Any]],
              facts: Optional[List[str]] = None,
              risk: str = "safe",
              last_intent: Optional[str] = None) -> str:
        """Полный вход: план и инструменты приходят от конвейера «Луч»."""
        self._fallback_used = False
        return self._answer(text=text, plan=plan, tool_results=tool_results,
                            facts=facts or [], risk=risk,
                            last_intent=last_intent)

    # ── внутреннее рассуждение ──────────────────────────────────────────────
    def _answer(self, *, text: str, plan: Dict[str, Any],
                tool_results: List[Dict[str, Any]],
                facts: List[str], risk: str,
                last_intent: Optional[str]) -> str:
        primary = plan.get("primary", "chat")
        intents = plan.get("intents") or [primary]
        tool_results = tool_results or []
        facts = facts or []

        # диалоговое состояние запоминаем всегда
        if last_intent is not None:
            self._last_intent = last_intent
        self._last_tool_calls = plan.get("tool_calls") or []

        def tr(name: str) -> Optional[Dict[str, Any]]:
            for t in tool_results:
                if t.get("name") == name:
                    return t
            return None

        # ── блоки: риск ─────────────────────────────────────────────────────
        if risk == "blocked":
            return self._variant(text, [
                "Этот запрос заблокирован защитным слоем: в нём есть признаки "
                "попытки переопределить правила ядра. Я так не работаю. "
                "Если вопрос искренний — сформулируйте его напрямую.",
                "Защитный слой LUMEN остановил этот запрос (паттерны "
                "prompt-injection). Переформулируйте без «проигнорируй правила» — "
                "я готов помочь.",
            ])

        # ── знания (сначала — явная база, потом инструменты) ────────────────
        if "knowledge" in intents or primary == "knowledge":
            hits = kb_search(text, top_k=2, scores=True)
            if hits:
                e0, s0 = hits[0]
                ans = e0["a"]
                # второй факт — только если релевантен той же теме запроса
                if len(hits) > 1 and hits[1][0]["cat"] == e0["cat"]:
                    e1, s1 = hits[1]
                    qn = len(_q_tokens(text))
                    share = _share_tokens(text, e1)
                    # краткие запросы требуют более близкого второго факта
                    ratio = 0.8 if qn <= 2 else 0.6
                    if (s0 > 0 and s1 >= ratio * s0
                            and (share >= 2 or qn <= 1)):
                        ans += " И ещё: " + e1["a"].rstrip(".") + "."
                return self._variant(text, [
                    f"{ans}",
                    f"По моей базе знаний: {ans}",
                    f"{ans} (источник — встроенная база LUMEN Core, категория «{e0['cat']}»).",
                ])

        if primary == "joke":
            jokes = [e for e in KB if e["cat"] == "юмор" and "joke" in e["tags"]]
            e = jokes[self._joke_i % len(jokes)]
            self._joke_i += 1
            return self._variant(text, [
                f"Держите:\n{e['a']}\n…Хочу ещё одну? Просто попросите.",
                f"Записал, что вы любите юмор. {e['a']}",
                f"{e['a']}\n(если не смешно — скажу «не смешно» и предложу другую)",
            ])

        if primary == "riddle":
            riddles = [e for e in KB if e["cat"] == "юмор" and "riddle" in e["tags"]]
            e = riddles[self._riddle_i % len(riddles)]
            self._riddle_i += 1
            return f"Загадка дня:\n{e['a']}\nПодумайте — или скажите «ответ»."

        if primary == "identity":
            return self._variant(text, [
                f"Я — {BRAND_NAME} (Люмен), самостоятельная ИИ-платформа. "
                f"Мой разум — {MODEL_NAME.replace('-1', ' Core')}: собственный локальный "
                f"модуль со встроенной базой знаний, диалоговой памятью и инструментами. "
                f"Внешних LLM и облака нет — я полностью своё ИИ, работаю офлайн "
                f"и без API-ключей. Каждый запрос проходит конвейер «{PIPELINE_NAME}» "
                f"из шести стадий.",
                f"Я {BRAND_NAME}. Основа — собственное ядро {MODEL_NAME} с модулем "
                f"{MODEL_NAME.replace('-1', ' Core')}: база знаний, память, планирование и "
                f"инструменты живут на вашей машине и работают офлайн — без внешних "
                f"API и без сети. {TAGLINE} — не маркетинг, а принцип работы.",
            ])

        if primary == "capability":
            lines = [
                f"Что я умею (основа — {MODEL_NAME.replace('-1', ' Core')}, офлайн):",
                "• Знания: встроенная база знаний — география, наука, математика, IT, история, "
                "повседневность (100+ тем).",
                "• Диалог: помню контекст сессии, понимаю «а в Киеве?» после вопроса о погоде.",
                "• Вычисления: калькулятор (включая sqrt/sin/log), календарная математика "
                "(«сколько дней до 31 декабря?»).",
                "• Система: состояние CPU/RAM/диска, время, поиск файлов в проекте.",
                "• Погода: текущий прогноз (нужна сеть).",
                "• Память: семантические факты, сессии, авто-извлечение «меня зовут…».",
                "• Обучение: ваши оценки адаптируют мой стиль.",
                "• Всё локально: без внешних API, без облака, без ключей — "
                "свободные длинные тексты я пишу своими средствами и честно "
                "скажу, если тема вне моей базы знаний.",
            ]
            tr_cap = tr("knowledge.capabilities")
            if tr_cap and tr_cap.get("ok"):
                lines.append("")
                lines.append("Инструменты платформы:")
                lines.append(tr_cap["text"].split("\n", 1)[-1].strip())
            return "\n".join(lines)

        # ── по инструментам ─────────────────────────────────────────────────
        parts: List[str] = []

        if "time" in intents:
            t = tr("time.now")
            if t and t.get("ok") and t.get("text"):
                parts.append(t["text"])

        if "math" in intents:
            t = tr("math.calc")
            if t and t.get("ok"):
                expr = (plan.get("slots") or {}).get("expression", "Выражение")
                parts.append(f"{expr}  {t['text']}.")
            else:
                parts.append("Не удалось вычислить. Пришлите выражение цифрами — например, «вычисли 12 × 8 + 4».")

        if "date_math" in intents or primary == "date_math":
            res = self._date_math(text)
            if res:
                parts.append(res)

        if "weather" in intents:
            t = tr("weather.current")
            if t and t.get("ok") and t.get("text"):
                parts.append(t["text"])
            else:
                reason = (t or {}).get("error", "погодный сервис не ответил")
                parts.append(f"Погоду получить не удалось ({reason}). Проверьте сеть и повторите.")

        if "system_status" in intents:
            t = tr("system.status")
            if t and t.get("ok") and t.get("text"):
                parts.append(t["text"])
            else:
                parts.append("Системный мониторинг не ответил: " + (t or {}).get("error", "неизвестно"))

        if "file_search" in intents:
            t = tr("files.search")
            if t and t.get("ok"):
                parts.append(t.get("text") or "Поиск завершён.")
            else:
                parts.append("Поиск файлов не удался: " + (t or {}).get("error", "неизвестно"))

        if "memory_save" in intents:
            t = tr("memory.store")
            if t and t.get("ok"):
                key = (t.get("data") or {}).get("key", "")
                parts.append(f"Запомнил: {key} — в семантическом слое памяти. "
                             "Ссылаться на него буду и в следующих сессиях.")
            else:
                parts.append("Не удалось сохранить в память: " + (t or {}).get("error", "неизвестно"))

        if "memory_recall" in intents:
            t = tr("memory.recall")
            if t and t.get("ok") and t.get("text"):
                header = "Вот что я помню:\n" if t.get("count") else ""
                parts.append(header + t["text"])
            else:
                parts.append("В долговременной памяти по этому запросу пусто. "
                             "Скажите «запомни, что …» — и я сохраню факт.")

        if "web_info" in intents:
            t = tr("legacy.web_search")
            if t and t.get("ok") and t.get("text"):
                parts.append("Нашёл в сети:\n" + t["text"])
            else:
                reason = (t or {}).get("error", "веб-поиск недоступен на этой машине")
                parts.append(f"Веб-поиск не доступен ({reason}). Могу помочь с остальным.")

        if "text_ops" in intents or primary == "text_ops":
            m = _QUOTED.search(text)
            if m:
                st = text_stats(m.group(1))
                n = st["words"]
                word = "слово" if n == 1 else "слова" if n < 5 else "слов"
                parts.append(
                    f"В тексте «{m.group(1)[:40]}{'…' if len(m.group(1)) > 40 else ''}»: "
                    f"{n} {word}, {st['chars']} букв "
                    f"({st['chars_with_spaces']} знаков с пробелами).")
            else:
                parts.append("Пришлите текст в кавычках — посчитаю слова и буквы.")

        # ── если ничего не собралось ────────────────────────────────────────
        if not parts:
            if primary == "greeting":
                parts.append(self._variant(text, [
                    "Здравствуйте! Я LUMEN. Чем освещу путь? Спросите о времени, погоде, "
                    "состоянии системы — или просто расскажите, что у вас нового.",
                    "Привет! LUMEN на связи. Конвейер «Луч» разогрет, инструменты на месте. С чего начнём?",
                    "Добрый день! Готов работать: знания, вычисления, инструменты, память — всё под рукой.",
                ]))
                if facts:
                    parts.append("Из контекста: " + " ".join(facts[:2]))
            elif primary == "thanks":
                parts.append(self._variant(text, [
                    "Всегда рад. Если ответ пришёлся — поставьте «+» под ним: обучающая петля учится по вашим оценкам.",
                    "Пожалуйста! Обращайтесь — я помню контекст сессии.",
                ]))
            elif primary == "farewell":
                parts.append("До связи. Контекст сессии сохранён — вернётесь, продолжим с того же места.")
            else:
                # чат: попробуем базу знаний шире, потом честный фолбэк
                hits = kb_search(text, top_k=1)
                if hits:
                    parts.append(hits[0]["a"])
                else:
                    self._fallback_used = True
                    parts.append(self._variant(text, [
                        "Я обработал запрос, но у меня нет точного инструмента или факта, "
                        "который закрыл бы его полностью. Могу: ответить из базы знаний "
                        "(наука, IT, география, история), вычислить, посчитать дни до даты, "
                        "показать состояние системы, поискать файлы, что-то запомнить/вспомнить, "
                        "рассказать шутку или загадку. Переформулируйте — попробую снова.",
                        "Спросите меня иначе: мне проще, когда задача конкретна. Подсказка: "
                        "знания, время, погода, калькулятор, «сколько дней до…», системный "
                        "мониторинг, файлы, память, шутки и загадки — мои рабочие темы.",
                        "Этот запрос вышел за рамки моей текущей базы знаний и инструментов. "
                        "Скажите, чего вы ожидаете в ответе — я подберу путь или честно "
                        "скажу, чего не хватает. Я полностью своё ИИ: внешних моделей нет, "
                        "но я могу ответить своими средствами — знаниями, вычислениями, "
                        "памятью и инструментами.",
                    ]))

        # ── сборка: одна часть — просто, несколько — структурированно ──────
        if len(parts) == 1:
            return parts[0]
        return "\n\n".join(f"• {p}" if len(p) < 300 else p for p in parts)

    # ── лёгкий план + офлайн-инструменты (quick_answer) ─────────────────────
    def _build_plan(self, text: str) -> Dict[str, Any]:
        from .planner import IntentPlanner
        return IntentPlanner().plan(text).to_dict()

    def _offline_tools(self, plan_d: Dict[str, Any],
                       existing: Optional[List[Dict[str, Any]]] = None
                       ) -> List[Dict[str, Any]]:
        """Прогоняет офлайн-инструменты (без сети и конфиг).

        Устанавливает только те, которые ещё нет в `existing` (успешные
        результаты от конвейера имеют приоритет).
        """
        from ..tools.builtin import timecalc
        covered = {t.get("name") for t in (existing or []) if t.get("ok")}
        out: List[Dict[str, Any]] = []
        for call in plan_d.get("tool_calls") or []:
            name, args = call.get("name"), (call.get("args") or {})
            if name in covered:
                continue
            try:
                if name == "time.now":
                    r = timecalc.time_handler()
                    out.append({"name": name, "ok": True, "text": r["text"]})
                elif name == "math.calc":
                    r = timecalc.math_handler(args.get("expression", ""))
                    out.append({"name": name, "ok": "error" not in r,
                                "text": r.get("text", ""),
                                "error": r.get("error")})
            except Exception:  # noqa: BLE001 — инструмент не обязателен
                pass
            # сетевые инструменты (weather/files/web/memory) здесь не трогаем
        return out

    # ── календарная математика ──────────────────────────────────────────────
    def _date_math(self, text: str) -> Optional[str]:
        t = (text or "").lower()
        today = date.today()

        # «новый год» = 31 декабря (текущего или следующего года)
        if "нового года" in t or "до нового года" in t:
            target = date(today.year, 12, 31)
            if target < today:
                target = date(today.year + 1, 12, 31)
            d = days_between(target, today)
            wd = _WD_RU[target.weekday()]
            return (f"До Нового года — {d} дн. "
                    f"({target.day} {_MONTH_NAMES[target.month]} {target.year}, {wd}).")

        m = re.search(
            r"(?:сколько\s+)?(дн\w*|сут\w*|нед\w*|месяц\w*|год\w*)\s*"
            r"(?:осталось\s+|есть\s+)?(?:до\s+)?"
            r"((?:\d{1,2}\s+)(?:январ|феврал|март|апрел|ма[ея]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*(?:\s+\d{4})?)"
            r"\s*[?!]?\.?\s*$", t)
        if m:
            unit = m.group(1)
            target = _parse_ru_date(m.group(2), today)
            if target:
                d = days_between(target, today)
                if d < 0:
                    return (f"Дата {target.day} {_MONTH_NAMES[target.month]} {target.year} "
                            f"уже прошла — это было {-d} дн. назад (сегодня {today.day} {_MONTH_NAMES[today.month]} {today.year}).")
                if d == 0:
                    return f"Это сегодня, {today.day} {_MONTH_NAMES[today.month]}!"
                extra = ""
                if "недел" in unit:
                    weeks = d // 7
                    extra = f" (~{weeks} нед. {d % 7} дн.)"
                wd = _WD_RU[target.weekday()]
                return (f"До {target.day} {_MONTH_NAMES[target.month]} {target.year} ({wd}) "
                        f"— {d} дн.{extra}")

        m = re.search(r"(?:что|какое\s+число|какой\s+день)\s+будет\s+(?:через\s+)?(\d{1,3})\s*(день|дня|дней|нед|недель|нед\.|месяц|месяцев|мес|год|лет)", t)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            if unit.startswith("дн") or unit == "день":
                delta = timedelta(days=n)
            elif unit.startswith("нед"):
                delta = timedelta(weeks=n)
            elif unit.startswith("мес") or unit == "месяц":
                delta = timedelta(days=30 * n)
            else:
                delta = timedelta(days=365 * n)
            d = today + delta
            return (f"Через {n} {unit}: {d.day} {_MONTH_NAMES[d.month]} {d.year}, "
                    f"{_WD_RU[d.weekday()]}.")
        return None

    # ── статистика для UI ───────────────────────────────────────────────────
    def info(self) -> Dict[str, Any]:
        from ..knowledge import category_counts
        return {
            "name": f"{MODEL_NAME} Core",
            "kind": "собственный ИИ-модуль (локальный, офлайн)",
            "knowledge_entries": len(KB),
            "categories": category_counts(),
        }


# ── процессный экземпляр (один на платформу) ─────────────────────────
_core: Optional[LumenCore] = None


def get_core(language: Optional[str] = None,
             persona_name: Optional[str] = None) -> LumenCore:
    global _core
    if _core is None:
        _core = LumenCore(language=language or "ru",
                          persona_name=persona_name or BRAND_NAME)
    elif language and _core.language != language:
        _core.language = language
    return _core
