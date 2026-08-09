"""
M5Stick Voice Actions — голосовое управление M5Stick через MARK L.

Интеграция M5Stick с основным AI ассистентом.
Позволяет управлять устройством голосовыми командами.

Примеры команд:
  - "покажи время на M5Stick"
  - "отправь сообщение на M5Stick"
  - "включи зеленый светодиод"
  - "покажи смайлик на M5Stick"
  - "отключи M5Stick"
  - "статус M5Stick"
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Import M5Stick controller
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from actions.m5stick import get_controller, is_connected
except ImportError:
    def get_controller():
        return None
    def is_connected():
        return False


def m5stick_action(query: str) -> str:
    """Process M5Stick-related voice commands.
    
    Args:
        query: User's voice command
        
    Returns:
        Response message
    """
    query_lower = query.lower()
    ctrl = get_controller()
    
    # Check connection
    if not ctrl or not is_connected():
        # Try to connect
        if ctrl and ctrl.connect():
            return "M5Stick подключен!"
        return "M5Stick не подключен. Подключите устройство по USB."
    
    # ── Status commands ───────────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["статус", "status", "состояние"]):
        info = ctrl.device_info
        imu = ctrl.imu
        
        response = (
            f"M5Stick {info.model} подключен на {info.port}. "
            f"Батарея: {info.battery}%. "
            f"Температура: {info.temperature:.1f}°C. "
            f"Наклон: {imu.tilt_x:.0f}° по X, {imu.tilt_y:.0f}° по Y."
        )
        return response
    
    if any(kw in query_lower for kw in ["батарея", "battery", "заряд"]):
        ctrl.get_battery()
        return f"Уровень заряда: {ctrl.device_info.battery}%"
    
    if any(kw in query_lower for kw in ["температура", "temperature", "темп"]):
        ctrl.get_temperature()
        return f"Температура: {ctrl.device_info.temperature:.1f} градусов"
    
    # ── Display commands ──────────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["покажи", "отобрази", "show", "display"]):
        # Extract text to display
        text = query
        for prefix in ["покажи на m5stick", "покажи на m5 stick", "отобрази на m5stick",
                       "show on m5stick", "display on m5stick", "покажи", "отобрази"]:
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                break
        
        if text:
            ctrl.display_clear()
            ctrl.display_text(text, 10, 40, size=2)
            return f"Отображаю: {text}"
        else:
            return "Что показать на M5Stick?"
    
    if any(kw in query_lower for kw in ["очисти", "clear", "очистить экран"]):
        ctrl.display_clear()
        return "Экран очищен"
    
    if any(kw in query_lower for kw in ["смайлик", "face", "лицо", "эмодзи"]):
        # Determine expression
        if any(w in query_lower for w in ["счастлив", "happy", "радост"]):
            ctrl.display_face("happy")
            return "Показываю счастливое лицо"
        elif any(w in query_lower for w in ["грустн", "sad", "печальн"]):
            ctrl.display_face("sad")
            return "Показываю грустное лицо"
        elif any(w in query_lower for w in ["злой", "angry", "сердит"]):
            ctrl.display_face("angry")
            return "Показываю злое лицо"
        elif any(w in query_lower for w in ["удивлен", "surprised"]):
            ctrl.display_face("surprised")
            return "Показываю удивленное лицо"
        elif any(w in query_lower for w in ["сонн", "sleepy", "спящ"]):
            ctrl.display_face("sleepy")
            return "Показываю сонное лицо"
        elif any(w in query_lower for w in ["крут", "cool"]):
            ctrl.display_face("cool")
            return "Показываю крутое лицо"
        elif any(w in query_lower for w in ["любов", "love", "сердечк"]):
            ctrl.display_face("love")
            return "Показываю лицо с любовью"
        else:
            ctrl.display_face("happy")
            return "Показываю счастливое лицо"
    
    if any(kw in query_lower for kw in ["время", "time", "час"]):
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        ctrl.display_clear()
        ctrl.display_text(now, 60, 50, size=4, color="#00D4FF")
        return f"Время: {now}"
    
    if any(kw in query_lower for kw in ["прогресс", "progress"]):
        # Extract percentage
        import re
        match = re.search(r'(\d+)', query)
        if match:
            pct = int(match.group(1)) / 100.0
            ctrl.display_progress(pct, "Progress")
            return f"Прогресс: {int(pct * 100)}%"
        else:
            ctrl.display_progress(0.5, "50%")
            return "Показываю прогресс 50%"
    
    # ── LED commands ──────────────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["светодиод", "led", "лампочк", "подсветк"]):
        colors = {
            "красн": "red", "red": "red",
            "зелен": "green", "green": "green",
            "синий": "blue", "blue": "blue",
            "желт": "yellow", "yellow": "yellow",
            "голуб": "cyan", "cyan": "cyan",
            "розов": "magenta", "magenta": "magenta",
            "бел": "white", "white": "white",
        }
        
        color = "white"
        for ru, en in colors.items():
            if ru in query_lower:
                color = en
                break
        
        if any(kw in query_lower for kw in ["выключи", "off", "потуши"]):
            ctrl.led_color("off")
            return "Светодиод выключен"
        elif any(kw in query_lower for kw in ["мигай", "blink", "мигн"]):
            ctrl.led_blink(color, times=5)
            return f"Мигаю {color} светодиодом"
        elif any(kw in query_lower for kw in ["пульс", "pulse", "дыши"]):
            ctrl.led_pulse(color, duration=3000)
            return f"Пульсирую {color} светодиодом"
        else:
            ctrl.led_color(color, brightness=80)
            return f"Включаю {color} светодиод"
    
    # ── Vibration commands ────────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["вибр", "vibrat", "тряс"]):
        ctrl.vibrate(duration=300, strength=150)
        return "Вибрирую"
    
    # ── Notification commands ─────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["успех", "success", "готово", "ok"]):
        ctrl.notify_success("OK!")
        return "Уведомление об успехе отправлено"
    
    if any(kw in query_lower for kw in ["ошибка", "error", "проблема"]):
        ctrl.notify_error("Error")
        return "Уведомление об ошибке отправлено"
    
    if any(kw in query_lower for kw in ["предупреждение", "warning", "внимание"]):
        ctrl.notify_warning("Warning")
        return "Предупреждение отправлено"
    
    if any(kw in query_lower for kw in ["слушаю", "listening", "микрофон"]):
        ctrl.notify_listening()
        return "M5Stick в режиме прослушивания"
    
    if any(kw in query_lower for kw in ["говорю", "speaking", "воспроизвед"]):
        ctrl.notify_speaking()
        return "M5Stick в режиме воспроизведения"
    
    if any(kw in query_lower for kw in ["ожидание", "idle", "покой"]):
        ctrl.notify_idle()
        return "M5Stick в режиме ожидания"
    
    # ── System commands ───────────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["яркость", "brightness"]):
        import re
        match = re.search(r'(\d+)', query)
        if match:
            level = int(match.group(1))
            level = int(level * 255 / 100)  # Convert percentage to 0-255
            ctrl.set_brightness(level)
            return f"Яркость установлена на {int(level * 100 / 255)}%"
        else:
            ctrl.set_brightness(128)
            return "Яркость установлена на 50%"
    
    if any(kw in query_lower for kw in ["сон", "sleep", "спать"]):
        ctrl.sleep(0)
        return "M5Stick переходит в спящий режим"
    
    if any(kw in query_lower for kw in ["перезагруз", "reset", "рестарт"]):
        ctrl.reset()
        return "M5Stick перезагружается"
    
    if any(kw in query_lower for kw in ["отключи", "disconnect", "выключи m5"]):
        ctrl.disconnect()
        return "M5Stick отключен"
    
    if any(kw in query_lower for kw in ["подключи", "connect", "соедини"]):
        if ctrl.connect():
            return "M5Stick подключен!"
        else:
            return "Не удалось подключить M5Stick"
    
    # ── Gesture feedback ──────────────────────────────────────────────
    
    if any(kw in query_lower for kw in ["жест", "gesture", "движение"]):
        imu = ctrl.imu
        if imu.is_shaking:
            return "Обнаружена тряска"
        elif abs(imu.tilt_x) > 45:
            return f"Наклон {imu.tilt_x:.0f} градусов"
        else:
            return "Движений не обнаружено"
    
    # ── Default: show help ────────────────────────────────────────────
    
    return (
        "M5Stick команды: статус, покажи текст, смайлик счастливый, "
        "светодиод зеленый, вибрируй, яркость 50, отключи"
    )


# ── Event handlers for M5Stick integration ───────────────────────────────────

def setup_m5stick_handlers(ai_callback=None):
    """Setup event handlers for M5Stick buttons and gestures.
    
    Args:
        ai_callback: Function to call with voice commands from M5Stick
    """
    ctrl = get_controller()
    if not ctrl:
        return
    
    def on_button(data):
        """Handle button presses."""
        btn = data.get("btn", "")
        action = data.get("action", "")
        
        if action == "press":
            if btn == "A":
                # Button A: trigger listening mode
                if ai_callback:
                    ai_callback("listen")
                ctrl.notify_listening()
            elif btn == "B":
                # Button B: show status
                ctrl.display_clear()
                info = ctrl.device_info
                ctrl.display_status([
                    f"Battery: {info.battery}%",
                    f"Temp: {info.temperature:.1f}C",
                    f"Model: {info.model}",
                ])
        
        elif action == "long":
            if btn == "A":
                # Long press A: idle mode
                ctrl.notify_idle()
            elif btn == "B":
                # Long press B: disconnect
                ctrl.disconnect()
    
    def on_gesture(gesture):
        """Handle gestures."""
        if gesture == "shake":
            # Shake: trigger action
            if ai_callback:
                ai_callback("shake")
            ctrl.led_blink("cyan", 3)
        elif gesture == "flip":
            # Flip: toggle display
            ctrl.display_face("surprised")
        elif gesture == "tilt_left":
            ctrl.display_text("<", 10, 50, size=4)
        elif gesture == "tilt_right":
            ctrl.display_text(">", 200, 50, size=4)
    
    def on_connect(data):
        """Handle connection."""
        ctrl.display_clear()
        ctrl.display_title("MARK L", "Connected!")
        ctrl.led_color("cyan", 50)
    
    def on_disconnect(data):
        """Handle disconnection."""
        pass  # Can't display on disconnected device
    
    # Register handlers
    ctrl.on("button", on_button)
    ctrl.on("gesture", on_gesture)
    ctrl.on("connect", on_connect)
    ctrl.on("disconnect", on_disconnect)
    
    print("[M5Stick] Event handlers registered")


# ── Tool definition for Gemini ───────────────────────────────────────────────

M5STICK_TOOL = {
    "name": "m5stick_control",
    "description": (
        "Controls the M5StickC device connected via USB. "
        "Can display text, show emoji faces, control LED colors, "
        "vibrate, show status information, and more. "
        "Use this when the user wants to interact with their M5Stick device."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "The command to execute. Examples: "
                    "'show time', 'display hello', 'led green', "
                    "'face happy', 'vibrate', 'status', 'disconnect'"
                )
            }
        },
        "required": ["command"]
    }
}


def handle_m5stick_tool(args: dict) -> str:
    """Handle M5Stick tool calls from Gemini."""
    command = args.get("command", "")
    return m5stick_action(command)
