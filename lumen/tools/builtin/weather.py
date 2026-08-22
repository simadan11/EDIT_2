"""Инструмент: weather.current — погода через wttr.in (JSON, stdlib urllib).

Работает без сторонних пакетов. Офлайн — честный ToolResult с ошибкой.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict

_DESC_RU = {
    "sunny": "ясно", "clear": "ясно", "partly cloudy": "местами облачно",
    "cloudy": "облачно", "overcast": "пасмурно", "mist": "туман",
    "fog": "туман", "patchy rain possible": "возможен дождь",
    "light drizzle": "морось", "patchy light drizzle": "местами морось",
    "light rain": "небольшой дождь", "moderate rain": "умеренный дождь",
    "heavy rain": "сильный дождь", "light sleet": "малоснежно с дождём",
    "moderate or heavy sleet": "сильный дождь со снегом",
    "blizzard": "метель", "snow": "снег", "light snow": "небольшой снег",
    "moderate snow": "умеренный снег", "heavy snow": "сильный снег",
    "thundery outbreaks possible": "возможна гроза",
    "patchy light rain with thunder": "дождь с грозой",
    "moderate or heavy rain with thunder": "сильный дождь с грозой",
    "patchy light snow with thunder": "снег с грозой",
    "moderate or heavy snow with thunder": "сильный снег с грозой",
}

_ICON = {
    "sunny": "☀️", "clear": "🌙", "partly cloudy": "⛅", "cloudy": "☁️",
    "overcast": "☁️", "light rain": "🌦", "moderate rain": "🌧",
    "heavy rain": "🌧", "light snow": "🌨", "moderate snow": "❄️",
    "heavy snow": "❄️", "blizzard": "🌨", "fog": "🌫", "mist": "🌫",
}


def handler(city: str = "") -> Dict[str, Any]:
    url = f"https://wttr.in/{urllib.parse.quote(city or 'Moscow')}?format=j1&lang=ru"
    req = urllib.request.Request(url, headers={"User-Agent": "LUMEN/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"нет ответа сервиса погоды: {e}",
                "text": "Погодный сервис (wttr.in) сейчас недоступен. "
                        "Проверьте сеть и повторите запрос."}

    try:
        cur = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        place = (area.get("areaName", [{}])[0].get("value", "") or
                 (area.get("country", [{}])[0].get("value", ""))) or city or "ваш город"
        desc_en = cur.get("weatherDesc", [{}])[0].get("value", "—").lower()
        desc = _DESC_RU.get(desc_en, cur.get("weatherDesc", [{}])[0].get("value", "—"))
        icon = _ICON.get(desc_en, "🌡")
        text = (
            f"{icon} {place}: {desc}, {cur.get('temp_C', '?')}°C "
            f"(ощущается {cur.get('FeelsLikeC', '?')}°C). "
            f"Влажность {cur.get('humidity', '?')}%, "
            f"ветер {cur.get('windspeedKmph', '?')} км/ч {cur.get('winddir16Point', '')}."
        )
        day = (data.get("weather") or [{}])[0]
        extra = ""
        if day:
            extra = (f" Сегодня: {day.get('mintempC', '?')}…{day.get('maxtempC', '?')}°C, "
                     f"день — {_DESC_RU.get((day.get('hourly') or [{}])[2].get('weatherDesc', [{}])[0].get('value', '').lower() if day.get('hourly') else '', day.get('maxtempC', ''))}.")
        return {"text": text + extra, "place": place, "desc": desc,
                "temp_c": cur.get("temp_C"), "feels_like_c": cur.get("FeelsLikeC"),
                "humidity": cur.get("humidity"), "icon": icon}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ошибка разбора ответа: {e}",
                "text": "Погодный сервис ответил, но я не смог разобрать прогноз."}


def spec():
    from ...kernel.tools import ToolSpec, ToolParam
    return ToolSpec(
        name="weather.current", title="Погода",
        description="Текущая погода по городу (wttr.in). Город: любой, на любом языке.",
        handler=handler, category="info", timeout=12.0, icon="🌦",
        params=[ToolParam("city", "string", "Город, например: Казань", False, "Москва")],
    )
