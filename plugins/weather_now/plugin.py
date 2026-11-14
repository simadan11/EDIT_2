"""Weather Now plugin — wttr.in, no API key required."""

import json
import urllib.parse
import urllib.request


def run_tool(tool_name: str, args: dict, player=None) -> str:
    city = str((args or {}).get("city") or "").strip() or "Kharkiv"
    url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&lang=ru"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return f"Weather service unavailable ({type(e).__name__})."

    try:
        cur = data["current_condition"][0]
        desc = (cur.get("lang_ru") or [{}])[0].get("value") or \
               (cur.get("weatherDesc") or [{}])[0].get("value") or ""
        return (
            f"{city}: {cur.get('temp_C')}°C (feels like {cur.get('FeelsLikeC')}°C), "
            f"{desc}, humidity {cur.get('humidity')}%, wind {cur.get('windspeedKmph')} km/h."
        )
    except Exception:
        return "Weather data received but could not be parsed."
