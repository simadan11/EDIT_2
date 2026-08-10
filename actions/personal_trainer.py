"""
Personal Trainer (📚) — личный тренер EDIT.

Возможности:
  • План тренировок и питания   — профиль цели + AI-план, хранится локально
  • Отслеживание прогресса      — тренировки, вес, стрик, недельная динамика
  • Мотивационные напоминания   — ежедневный толчок в заданное время
    (через proactive-цикл: заметка подаётся голосом, когда время наступило)
  • Анализ сна                  — длительность, стабильность, долг сна, оценка

Данные: ~/.jarvis/personal_trainer.json  (вне репозитория — это личное).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_path() -> Path:
    d = Path.home() / ".jarvis"
    d.mkdir(parents=True, exist_ok=True)
    return d / "personal_trainer.json"


def _default() -> dict:
    return {
        "profile":  {},                     # goal/level/days/equipment/height/weight
        "plan":     {"text": "", "updated": ""},
        "workouts": [],                     # [{date, type, duration_min, intensity, notes}]
        "weights":  [],                     # [{date, kg}]
        "sleep":    [],                     # [{date, bed, wake, hours, quality}]
        "motivation": {"enabled": False, "time": "07:30", "last_fired": ""},
    }


def _load() -> dict:
    try:
        d = json.loads(_store_path().read_text(encoding="utf-8"))
        base = _default()
        base.update({k: v for k, v in d.items() if k in base})
        return base
    except Exception:
        return _default()


def _save(d: dict) -> None:
    try:
        _store_path().write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception:
        pass


def _log(msg: str, player) -> None:
    try:
        if player and hasattr(player, "write_log"):
            player.write_log(msg)
        elif player and hasattr(player, "append_log"):
            player.append_log(msg)
    except Exception:
        pass


def _today() -> str:
    return date.today().isoformat()


_WD_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_WD_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

def _workout_streak(workouts: list) -> int:
    """Серия подряд идущих дней с тренировками (сегодня считается, если записан)."""
    days = {w.get("date") for w in workouts}
    streak = 0
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)                     # сегодняшний день ещё может случиться
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def _count_since(workouts: list, days: int) -> int:
    cut = (date.today() - timedelta(days=days - 1)).isoformat()
    return sum(1 for w in workouts if (w.get("date") or "") >= cut)


def _sleep_hours(bed: str, wake: str) -> float | None:
    """bed 23:15 + wake 07:00 → 7.75 ч (переход через полночь — ок)."""
    try:
        bh, bm = [int(x) for x in bed.strip().split(":")[:2]]
        wh, wm = [int(x) for x in wake.strip().split(":")[:2]]
        start = bh * 60 + bm
        end   = wh * 60 + wm
        if end <= start:
            end += 24 * 60
        return round((end - start) / 60.0, 2)
    except Exception:
        return None


def _sleep_stats(rows: list, days: int = 7) -> dict:
    cut = (datetime.now() - timedelta(days=days - 1)).date().isoformat()
    recent = [r for r in rows if (r.get("date") or "") >= cut and r.get("hours")]
    if not recent:
        return {}
    hrs = [float(r["hours"]) for r in recent]
    bedtimes = [r.get("bed", "") for r in recent]
    # стабильность отбоя — разброс в минутах вокруг среднего
    mins = []
    for b in bedtimes:
        try:
            h, m = [int(x) for x in b.split(":")[:2]]
            mins.append((h * 60 + m + 720) % 1440)      # вокруг 00:00
        except Exception:
            pass
    spread = (max(mins) - min(mins) - 1440 * (max(mins) - min(mins) > 720)) if len(mins) > 1 else 0
    avg = sum(hrs) / len(hrs)
    debt = max(0.0, 8.0 - avg) * len(hrs)               # условный долг к 8 ч
    return {
        "n": len(hrs), "avg": round(avg, 2),
        "best": round(max(hrs), 2), "worst": round(min(hrs), 2),
        "spread_min": int(spread), "debt": round(debt, 1),
    }


def _sleep_score(stats: dict) -> int:
    """0–100: 8 ч = сотка; минус за короткие ночи и нестабильный отбой."""
    if not stats:
        return 0
    avg = stats["avg"]
    score = 100 - min(60, abs(8.0 - avg) * 22) - min(25, stats["spread_min"] / 12)
    return max(0, min(100, int(score)))


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------

_WEEKDAY_TAIL = ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")
_WEEKDAY_RU_SHORT = ("понед","вторн","сред","четверг","пятниц","суббот","воскрес")


def _plan_for_today(plan_text: str) -> str:
    """Выдёргивает из markdown-плана секцию сегодняшнего дня (грубо, по заголовку)."""
    if not plan_text.strip():
        return ""
    wd = datetime.now().weekday()
    key_en = _WEEKDAY_TAIL[wd]
    key_ru = _WEEKDAY_RU_SHORT[wd]
    lines = plan_text.splitlines()
    grab, out = False, []
    for ln in lines:
        low = ln.strip().lower()
        head = ln.strip().startswith(("#", "**", "-", "*", "•"))
        hit = (key_en in low) or (key_ru in low)
        if hit and head:
            if grab:
                break
            grab = True
            out.append(ln)
            continue
        if grab:
            if head and ("day" in low or "день" in low or
                         any(k in low for k in _WEEKDAY_TAIL + _WEEKDAY_RU_SHORT)):
                break
            out.append(ln)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Motivation (ежедневный толчок — читает proactive-цикл main.py)
# ---------------------------------------------------------------------------

def motivation_due() -> dict | None:
    """Готовый мотивационный контекст, если время наступило и сегодня ещё не было.
    Вызывается из proactive-цикла; помечает день как использованный."""
    d = _load()
    m = d["motivation"]
    if not m.get("enabled"):
        return None
    t = (m.get("time") or "07:30")
    try:
        hh, mm = [int(x) for x in t.split(":")[:2]]
    except Exception:
        hh, mm = 7, 30
    now = datetime.now()
    if (now.hour, now.minute) < (hh, mm):
        return None
    if m.get("last_fired") == _today():
        return None
    m["last_fired"] = _today()
    _save(d)
    stats = _sleep_stats(d["sleep"])
    today_plan = _plan_for_today(d["plan"].get("text", ""))
    return {
        "goal":        d["profile"].get("goal", ""),
        "streak":      _workout_streak(d["workouts"]),
        "today_plan":  today_plan,
        "sleep_avg":   stats.get("avg"),
        "planned":     m.get("time"),
    }


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

def trainer_action(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    p  = parameters or {}
    act = str(p.get("action") or "").strip().lower()
    d  = _load()

    # ── профиль / цель ────────────────────────────────────────────────────
    if act in ("setup", "profile", "set_profile"):
        prof = d["profile"]
        for key, alt in (("goal", None), ("level", None), ("equipment", None)):
            v = p.get(key)
            if v:
                prof[key] = str(v)[:80]
        for key in ("days_per_week",):
            if p.get(key):
                try:
                    prof[key] = max(1, min(7, int(p[key])))
                except Exception:
                    pass
        for key in ("weight_kg", "height_cm"):
            if p.get(key):
                try:
                    prof[key] = float(p[key])
                except Exception:
                    pass
        prof["updated"] = _today()
        _save(d)
        msg = (f"Профиль тренера обновлён: цель — {prof.get('goal', '—')}, "
               f"уровень — {prof.get('level', '—')}, дней в неделю — "
               f"{prof.get('days_per_week', '—')}. Теперь скажи «составь мне план», "
               f"и я запишу программу тренировок и питания.")
        _log(f"[Trainer] profile: {prof}", player)
        return msg

    # ── сохранить AI-сгенерированный план ─────────────────────────────────
    if act in ("save_plan", "set_plan"):
        text = (p.get("plan_text") or p.get("text") or "").strip()
        if not text:
            return "Пустой план не сохраняю — пришли текст программы."
        d["plan"] = {"text": text[:12000], "updated": _today()}
        _save(d)
        _log("[Trainer] plan saved", player)
        return "План записал. Спроси «что сегодня по плану» — скажу, какой день."

    # ── план на сегодня ───────────────────────────────────────────────────
    if act in ("today", "get_plan", "plan"):
        plan = d["plan"].get("text", "")
        if not plan:
            return ("Плана ещё нет. Сначала скажи цель и уровень («настрой профиль тренера»), "
                    "потом попроси составить план — я его запомню.")
        today_slice = _plan_for_today(plan)
        wd = _WD_RU[datetime.now().weekday()]
        if today_slice:
            return f"Сегодня — {wd}. По плану:\n{today_slice}"
        return (f"Сегодня — {wd}. Отдельной секции на сегодня в плане не нашёл, "
                f"программа целиком в «прогрессе» по команде. План от {d['plan'].get('updated', '—')}.")

    # ── лог тренировки ────────────────────────────────────────────────────
    if act in ("log_workout", "workout_done"):
        w = {
            "date": _today(),
            "type": str(p.get("type") or "workout")[:40],
            "duration_min": int(p.get("duration_min") or 45),
            "intensity":  str(p.get("intensity") or "normal")[:16],
            "notes":      str(p.get("notes") or "")[:120],
        }
        d["workouts"].append(w)
        d["workouts"] = d["workouts"][-400:]
        _save(d)
        streak = _workout_streak(d["workouts"])
        week   = _count_since(d["workouts"], 7)
        return (f"Записал: {w['type']}, {w['duration_min']} мин ({w['intensity']}). "
                f"За неделю уже {week} тренировк(а/и), серия — {streak} дн. "
                f"Так держать!")

    # ── вес ────────────────────────────────────────────────────────────────
    if act in ("log_weight", "weight"):
        try:
            kg = float(p.get("kg") or p.get("weight_kg"))
        except Exception:
            return "Не понял вес — скажи числом, например «вес 78.5»."
        d["weights"].append({"date": _today(), "kg": kg})
        d["weights"] = d["weights"][-400:]
        _save(d)
        trend = ""
        if len(d["weights"]) >= 2:
            delta = d["weights"][-1]["kg"] - d["weights"][0]["kg"]
            sign  = "минус" if delta < 0 else "плюс"
            trend = f" С первой записи: {sign} {abs(delta):.1f} кг."
        return f"Вес записан: {kg} кг.{trend}"

    # ── лог сна ────────────────────────────────────────────────────────────
    if act in ("log_sleep", "sleep"):
        bed  = str(p.get("bed")  or "").strip()
        wake = str(p.get("wake") or "").strip()
        hrs  = p.get("hours")
        if hrs is None and bed and wake:
            hrs = _sleep_hours(bed, wake)
        try:
            hrs = float(hrs)
        except Exception:
            return ("Как записать сон: «спал с 23:30 до 7:00» или «спал 7.5 часов». "
                    "Повтори, пожалуйста.")
        if not (0 < hrs <= 20):
            return "Что-то с часами не то — спим от 0 до 20 часов. Повтори?"
        q = p.get("quality")
        row = {"date": _today(), "bed": bed, "wake": wake, "hours": hrs}
        if q is not None:
            try:
                row["quality"] = max(1, min(5, int(q)))
            except Exception:
                pass
        d["sleep"].append(row)
        d["sleep"] = d["sleep"][-400:]
        _save(d)
        mark = "отлично" if hrs >= 8 else ("норм" if hrs >= 7 else "маловато")
        return f"Сон записан: {hrs:.2f} ч — {mark}. Норма 7–9 часов."

    # ── прогресс ───────────────────────────────────────────────────────────
    if act in ("progress", "stats", "summary"):
        w7, w30 = _count_since(d["workouts"], 7), _count_since(d["workouts"], 30)
        streak  = _workout_streak(d["workouts"])
        lines = [
            f"📊 Прогресс:",
            f"• Тренировки: за 7 дней — {w7}, за 30 — {w30}, серия — {streak} дн.",
        ]
        if len(d["weights"]) >= 1:
            w_now, w_first = d["weights"][-1]["kg"], d["weights"][0]["kg"]
            delta = w_now - w_first
            lines.append(f"• Вес: {w_now} кг (с первой записи {'−' if delta < 0 else '+'}"
                         f"{abs(delta):.1f} кг, записей {len(d['weights'])})")
        st = _sleep_stats(d["sleep"])
        if st:
            lines.append(
                f"• Сон: в среднем {st['avg']:.1f} ч за {st['n']} н., "
                f"лучший {st['best']:.1f}, худший {st['worst']:.1f}, "
                f"оценка {_sleep_score(st)}/100")
        goal = d["profile"].get("goal")
        if goal:
            lines.append(f"• Цель: {goal}")
        msg = "\n".join(lines)
        if player and hasattr(player, "show_content"):
            try:
                player.show_content("📊 FITNESS PROGRESS", msg)
            except Exception:
                pass
        return msg.replace("📊 ", "")

    # ── анализ сна ─────────────────────────────────────────────────────────
    if act in ("sleep_analysis", "analyse_sleep", "analyze_sleep"):
        st = _sleep_stats(d["sleep"], 14)
        if not st:
            return ("Записей сна пока нет. Скажи, например: «запиши сон: лёг 23:40, "
                    "встал 7:10» — и я начну следить.")
        score = _sleep_score(st)
        verdict = ("отличный режим" if score >= 85 else
                   "неплохо, есть что чинить" if score >= 65 else
                   "режим проседает — нужна стабилизация")
        msg = (f"Анализ сна за ~2 недели ({st['n']} ночей): средняя длина "
               f"{st['avg']:.1f} ч, лучший {st['best']:.1f}, худший {st['worst']:.1f}, "
               f"разброс времени отбоя ~{st['spread_min']} мин, "
               f"условный долг сна ≈ {st['debt']:.1f} ч. Оценка: {score}/100 — {verdict}.")
        if player and hasattr(player, "show_content"):
            try:
                player.show_content("🌙 SLEEP ANALYSIS", msg)
            except Exception:
                pass
        return msg

    # ── мотивационные напоминания ──────────────────────────────────────────
    if act in ("motivation", "motivate", "reminder"):
        m = d["motivation"]
        flag = p.get("enabled")
        t    = str(p.get("time") or "").strip()
        if t and ":" in t:
            m["time"] = t[:5]
        if flag is None:
            flag = not m.get("enabled")          # подзыв без аргумента = toggle
        m["enabled"] = bool(flag)
        _save(d)
        if m["enabled"]:
            return (f"Мотивация включена: каждый день в {m['time']} подгоню "
                    f"короткой репликой (пока EDIT запущен). Выключить: «выключи мотивацию».")
        return "Мотивационные напоминания выключены."

    # ── справка ────────────────────────────────────────────────────────────
    return (
        "Личный тренер умеет: «настрой профиль тренера» (цель/уровень/дни), "
        "«сохрани план тренировок» (+питание), «что сегодня по плану», "
        "«запиши тренировку: 45 минут силовая», «запиши вес 78.5», "
        "«запиши сон: лёг 23:30 встал 7:00», «мой прогресс», «анализ сна», "
        "«мотивация в 7:30»."
    )


# совместимость с соглашением имён actions: <module>_action
personal_trainer_action = trainer_action
