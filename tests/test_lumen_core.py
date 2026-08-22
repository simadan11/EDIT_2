"""LUMEN Core — тесты собственного ИИ-модуля (unittest, без внешних зависимостей).

Покрытие: база знаний, календарная математика, текстовая статистика,
шутки/загадки, анафора, составные запросы, бэкенд lumen_core как
основной, signal overflow, legacy-роутер.

Запуск:  python -m unittest tests.test_lumen_core -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# База знаний
# ═══════════════════════════════════════════════════════════════════════════

class TestKnowledgeBase(unittest.TestCase):
    def test_entries_count_and_shape(self):
        from lumen.knowledge import BASE, category_counts
        self.assertGreaterEqual(len(BASE), 100)
        for e in BASE:
            self.assertTrue(e["id"] and e["q"] and e["a"] and e["cat"])
            self.assertIsInstance(e["tags"], list)
        cc = category_counts()
        self.assertGreaterEqual(sum(cc.values()), 100)

    def test_search_hits(self):
        from lumen.knowledge import search
        self.assertEqual(search("Кто такой Гагарин?", top_k=1)[0]["id"], "gagarin")
        self.assertEqual(search("Сколько секунд в сутках?", top_k=1)[0]["id"],
                         "seconds_day")
        self.assertEqual(search("самая высокая гора", top_k=1)[0]["id"], "mount_max")

    def test_search_stem_tolerant(self):
        from lumen.knowledge import search
        # «секунд» → seconds_day, «программированию» → python
        self.assertIn(search("сколько секунд", top_k=1)[0]["id"],
                      ("seconds_day", "seconds_hour"))
        self.assertEqual(search("расскажи про программирование на питоне",
                                top_k=1)[0]["id"], "python")

    def test_search_no_match(self):
        from lumen.knowledge import search
        self.assertEqual(search("ззелёный квакш-фьюжн", top_k=1), [])

    def test_search_no_wrong_fact(self):
        """Неизвестная тема не даёт чужой факт (честный фолбэк дальше)."""
        from lumen.knowledge import search
        self.assertEqual(search("Кто такая Мона Лиза?", top_k=1), [])

    def test_search_topic_vs_mention(self):
        """Тема вопроса важнее упоминания в тегах другого факта."""
        from lumen.knowledge import search
        self.assertEqual(search("Что такое нейросеть?", top_k=1)[0]["id"],
                         "neural_net")
        self.assertEqual(search("Что такое искусственный интеллект?",
                                top_k=1)[0]["id"], "ai")


# ═══════════════════════════════════════════════════════════════════════════
# Календарная математика и текстовая статистика
# ═══════════════════════════════════════════════════════════════════════════

class TestDateMath(unittest.TestCase):
    def setUp(self):
        from lumen.kernel.brain import LumenCore
        self.core = LumenCore()

    def test_days_until_new_year(self):
        r = self.core._date_math("Сколько дней до 31 декабря?")
        self.assertIsNotNone(r)
        self.assertIn("дн.", r)
        self.assertIn("31 декабря", r)

    def test_past_date_next_year(self):
        # 8 марта — в прошлом относительно 22.08 → берём следующий год
        r = self.core._date_math("Сколько дней до 8 марта?")
        self.assertIsNotNone(r)
        self.assertIn("2027", r)

    def test_explicit_year(self):
        r = self.core._date_math("Сколько дней до 29 февраля 2028?")
        self.assertIsNotNone(r)
        self.assertIn("556", r)

    def test_weeks_unit(self):
        r = self.core._date_math("сколько недель до 31 декабря?")
        self.assertIsNotNone(r)
        self.assertIn("нед.", r)

    def test_future_offset(self):
        r = self.core._date_math("какое число будет через 30 дней?")
        self.assertIsNotNone(r)
        self.assertIn("сентября 2026", r)

    def test_new_year(self):
        r = self.core._date_math("Сколько дней до Нового года?")
        self.assertIsNotNone(r)
        self.assertIn("Нового года", r)
        self.assertIn("31 декабря", r)

    def test_unknown(self):
        self.assertIsNone(self.core._date_math("привет"))


class TestTextStats(unittest.TestCase):
    def test_stats(self):
        from lumen.kernel.brain import text_stats
        st = text_stats("привет мир")
        self.assertEqual(st["words"], 2)
        self.assertEqual(st["chars"], 9)
        self.assertEqual(st["chars_with_spaces"], 10)


# ═══════════════════════════════════════════════════════════════════════════
# Planner: новые намерения и анафора
# ═══════════════════════════════════════════════════════════════════════════

class TestPlannerCore(unittest.TestCase):
    def setUp(self):
        from lumen.kernel.planner import IntentPlanner
        self.p = IntentPlanner()

    def test_knowledge_intent(self):
        self.assertEqual(self.p.plan("Кто такой Тесла?").primary, "knowledge")
        self.assertEqual(self.p.plan("Сколько секунд в сутках?").primary,
                         "knowledge")
        self.assertEqual(self.p.plan("Как работает LUMEN?").primary,
                         "knowledge")

    def test_joke_riddle(self):
        self.assertEqual(self.p.plan("Расскажи шутку").primary, "joke")
        self.assertEqual(self.p.plan("Загадай загадку").primary, "riddle")

    def test_date_math_intent(self):
        self.assertEqual(self.p.plan("Сколько дней до 31 декабря?").primary,
                         "date_math")

    def test_text_ops_intent(self):
        self.assertEqual(self.p.plan("Сколько слов в «привет мир»?").primary,
                         "text_ops")

    def test_weather_guard_for_creative(self):
        # «напиши стихотворение о дожде» — не погодный запрос
        self.assertNotEqual(self.p.plan("Напиши стихотворение о дожде").primary,
                            "weather")

    def test_anaphora_weather_city(self):
        first = self.p.plan("Как погода в Киеве?")
        self.assertEqual(first.primary, "weather")
        follow = self.p.plan("а в Одессе?", last=first)
        self.assertEqual(follow.primary, "weather")
        self.assertEqual(follow.slots.get("city"), "Одессе")

    def test_anaphora_math_expr(self):
        first = self.p.plan("Вычисли 12 + 2")
        follow = self.p.plan("а 5*7?", last=first)
        self.assertEqual(follow.primary, "math")
        self.assertEqual(follow.slots.get("expression"), "5*7")

    def test_anaphora_plain_repeat(self):
        first = self.p.plan("Сколько времени?")
        follow = self.p.plan("а?", last=first)
        self.assertEqual(follow.primary, "time")

    def test_no_anaphora_for_long_or_clear(self):
        first = self.p.plan("Сколько времени?")
        # длинное сообщение — не анафора
        follow = self.p.plan("расскажи подробно про историю Древнего Рима", last=first)
        self.assertNotEqual(follow.primary, "time")
        # явное новое намерение не наследуется
        follow2 = self.p.plan("Какое время и какая погода?", last=first)
        self.assertIn("weather", follow2.intents)


# ═══════════════════════════════════════════════════════════════════════════
# Ядро LumenCore
# ═══════════════════════════════════════════════════════════════════════════

class TestLumenCoreBrain(unittest.TestCase):
    def setUp(self):
        from lumen.kernel.brain import LumenCore
        self.core = LumenCore()

    def test_knowledge_answer(self):
        ans, needs = self.core.quick_answer("Кто такой Гагарин?")
        self.assertIn("Гагарин", ans)
        self.assertFalse(needs)

    def test_joke(self):
        ans, needs = self.core.quick_answer("Расскажи шутку")
        self.assertIn("—", ans)
        self.assertFalse(needs)
        # следующая шутка — другая (ротация)
        ans2, _ = self.core.quick_answer("Расскажи ещё шутку")
        self.assertNotEqual(ans.split("\n")[0], ans2.split("\n")[0])

    def test_riddle(self):
        ans, needs = self.core.quick_answer("Загадай загадку")
        self.assertIn("Загадка", ans)
        self.assertFalse(needs)

    def test_math_offline(self):
        ans, needs = self.core.quick_answer("Вычисли 12 × 8 + 4")
        self.assertIn("100", ans)
        self.assertFalse(needs)

    def test_date_math_quick(self):
        ans, needs = self.core.quick_answer("Сколько дней до 31 декабря?")
        self.assertIn("31 декабря", ans)
        self.assertFalse(needs)

    def test_text_ops(self):
        ans, needs = self.core.quick_answer("Сколько слов в «привет мир как дела»?")
        self.assertIn("4 слова", ans)
        self.assertFalse(needs)
        ans1, _ = self.core.quick_answer("Сколько слов в «привет»?")
        self.assertIn("1 слово", ans1)

    def test_honest_fallback_and_overflow_signal(self):
        ans, needs = self.core.quick_answer("Напиши поэму о термодинамике и любви")
        self.assertTrue(needs)  # ядро признаёт: нужен внешний модуль
        self.assertTrue(ans)    # и всё равно отвечает честно

    def test_identity_mentions_own_core(self):
        ans, _ = self.core.quick_answer("Кто ты?")
        self.assertIn("LUMEN", ans)
        self.assertIn("собственн", ans.lower())  # «собственный»/«собственное»

    def test_blocked_risk(self):
        plan = {"primary": "chat", "intents": ["chat"]}
        ans = self.core.think(text="x", plan=plan, tool_results=[], risk="blocked")
        self.assertIn("заблокирован", ans.lower())


# ═══════════════════════════════════════════════════════════════════════════
# Бэкенд: lumen_core — основной
# ═══════════════════════════════════════════════════════════════════════════

class TestBackends(unittest.TestCase):
    def test_default_backend_is_core(self):
        from lumen.kernel.backends import get_backend, LumenCoreBackend
        cfg = type("C", (), {"get": staticmethod(lambda k, d=None: d)})()
        b = get_backend(cfg)
        self.assertIsInstance(b, LumenCoreBackend)
        self.assertEqual(b.name, "lumen_core")
        self.assertTrue(b.available()[0])

    def test_legacy_heuristic_alias(self):
        from lumen.kernel.backends import get_backend, HeuristicBackend
        cfg = type("C", (), {"get": staticmethod(
            lambda k, d=None: {"backend.provider": "heuristic"}.get(k, d))})()
        b = get_backend(cfg)
        self.assertIsInstance(b, HeuristicBackend)

    def test_gemini_backend_selected(self):
        from lumen.kernel.backends import get_backend, GeminiBackend
        cfg = type("C", (), {"get": staticmethod(
            lambda k, d=None: {"backend.provider": "gemini",
                               "backend.api_key": "k"}.get(k, d))})()
        self.assertIsInstance(get_backend(cfg), GeminiBackend)

    def test_core_generate_with_plan(self):
        from lumen.kernel.backends import get_backend, LumenCoreBackend
        cfg = type("C", (), {"get": staticmethod(lambda k, d=None: d)})()
        b: LumenCoreBackend = get_backend(cfg)
        plan = {"primary": "math", "intents": ["math"],
                "slots": {"expression": "6*7"},
                "tool_calls": [{"name": "math.calc", "args": {"expression": "6*7"}}],
                "confidence": 0.95}
        out = b.generate("система", [{"role": "user", "content": "Вычисли 6*7"}],
                         text="Вычисли 6*7", plan=plan, tool_results=[],
                         facts=[], risk="safe")
        self.assertIn("42", out)


# ═══════════════════════════════════════════════════════════════════════════
# Конвейер E2E на LUMEN Core
# ═══════════════════════════════════════════════════════════════════════════

class TestEngineWithCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp(prefix="lumen_core_test_"))
        from lumen.config import LumenConfig
        from lumen.kernel.engine import create_engine
        cfg = LumenConfig(path=tmp / "lumen.json")
        cfg.set("memory.path", str(tmp / "memory.json"))
        cfg.set("tools.enable_legacy_bridge", False)
        cls.eng = create_engine(cfg, import_legacy_memory=False)
        cls.eng.memory.sessions_dir = tmp / "sessions"
        cls.eng.memory.sessions_dir.mkdir(parents=True, exist_ok=True)

    def test_backend_is_lumen_core(self):
        self.assertEqual(self.eng.backend.name, "lumen_core")

    def test_knowledge_e2e(self):
        r = self.eng.process("Сколько секунд в сутках?")
        self.assertEqual(r.intent, "knowledge")
        self.assertIn("86 400", r.reply)
        self.assertEqual(r.backend, "lumen_core")
        self.assertFalse(r.fallback)

    def test_math_e2e(self):
        r = self.eng.process("Вычисли 6 * 7")
        self.assertIn("42", r.reply)
        self.assertEqual(r.intent, "math")

    def test_date_math_e2e(self):
        r = self.eng.process("Сколько дней до 31 декабря?")
        self.assertEqual(r.intent, "date_math")
        self.assertIn("31 декабря", r.reply)

    def test_joke_e2e(self):
        r = self.eng.process("Расскажи шутку")
        self.assertEqual(r.intent, "joke")
        self.assertIn("—", r.reply)

    def test_anaphora_e2e(self):
        sid = self.eng.memory.new_session("ан")
        self.eng.process("Сколько времени?", session_id=sid)
        r = self.eng.process("а 5*7?", session_id=sid)
        self.assertEqual(r.intent, "math")
        self.assertIn("35", r.reply)

    def test_compound_e2e(self):
        r = self.eng.process("Сколько времени и как там система?")
        names = [t["name"] for t in r.tools_used]
        self.assertIn("time.now", names)
        self.assertIn("system.status", names)


# ═══════════════════════════════════════════════════════════════════════════
# Legacy-роутер: LUMEN Core по умолчанию
# ═══════════════════════════════════════════════════════════════════════════

class TestModelRouter(unittest.TestCase):
    def test_current_model_name_is_core(self):
        from core import model_router
        self.assertIn("LUMEN Core", model_router.get_current_model_name())

    def test_generate_text_via_core(self):
        from core import model_router
        out = model_router.generate_text("Сколько секунд в сутках?")
        self.assertIn("86 400", out)

    def test_chat_completion_via_core(self):
        from core import model_router
        out = model_router.chat_completion(
            [{"role": "user", "content": "Вычисли 12 * 8 + 4"}])
        self.assertIn("100", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
