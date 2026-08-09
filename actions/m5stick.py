"""
M5Stick USB Controller — подключение и управление M5StickC/Plus по USB.

Поддерживаемые устройства:
  - M5StickC (ESP32-PICO, 80x160 TFT, MPU6886)
  - M5StickC Plus (ESP32-PICO, 135x240 TFT, MPU6886)
  - M5StickC Plus2 (ESP32-PICO-V3-02, 135x240 TFT, BMI270)

Функции:
  - Автоматическое обнаружение USB-порта
  - Чтение данных IMU (акселерометр, гироскоп)
  - Управление экраном (текст, иконки, очистка)
  - Обработка кнопок (A, B, Home)
  - RGB LED уведомления
  - Чтение температуры и батареи
  - Вибромотор (Plus2)
  - Голосовые команды через жесты

Протокол: Serial JSON на 115200 baud
  PC → M5:  {"cmd": "text", "data": "Hello"}
  M5 → PC:  {"event": "button", "btn": "A"}
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_SERIAL_AVAILABLE = False
try:
    import serial
    import serial.tools.list_ports
    _SERIAL_AVAILABLE = True
except ImportError:
    pass


# ── USB Identifiers for M5Stack devices ──────────────────────────────────────

_M5_USB_VID_PID = [
    (0x10C4, 0xEA60),   # CP2104 (M5StickC, M5StickC Plus)
    (0x1A86, 0x7523),   # CH340 (some M5StickC clones)
    (0x1A86, 0x55D4),   # CH9102 (M5StickC Plus2)
    (0x303A, 0x1001),   # ESP32-S3 USB (newer M5 devices)
    (0x0403, 0x6001),   # FTDI (some M5Stack products)
]

_BAUD_RATE = 115200
_READ_TIMEOUT = 0.1


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class IMUData:
    """IMU sensor readings (accelerometer + gyroscope)."""
    acc_x: float = 0.0
    acc_y: float = 0.0
    acc_z: float = 0.0
    gyro_x: float = 0.0
    gyro_y: float = 0.0
    gyro_z: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def tilt_x(self) -> float:
        """Tilt angle X in degrees (-180..180)."""
        import math
        return math.degrees(math.atan2(self.acc_y, self.acc_z))

    @property
    def tilt_y(self) -> float:
        """Tilt angle Y in degrees (-180..180)."""
        import math
        return math.degrees(math.atan2(self.acc_x, self.acc_z))

    @property
    def is_shaking(self) -> bool:
        """Detect shaking motion from gyroscope magnitude."""
        mag = (self.gyro_x**2 + self.gyro_y**2 + self.gyro_z**2) ** 0.5
        return mag > 500.0


@dataclass
class DeviceInfo:
    """M5Stick device information."""
    model: str = "M5StickC"
    firmware: str = ""
    battery: int = 0
    temperature: float = 0.0
    port: str = ""
    connected: bool = False


# ── M5StickController ────────────────────────────────────────────────────────

class M5StickController:
    """Thread-safe USB controller for M5Stick devices."""

    def __init__(self):
        self._lock = threading.Lock()
        self._serial = None
        self._read_thread: threading.Thread | None = None
        self._running = False
        self._port: str | None = None
        self._device_info = DeviceInfo()
        self._imu_data = IMUData()
        self._event_queue: queue.Queue = queue.Queue(maxsize=100)
        self._callbacks: dict[str, list[Callable]] = {
            "button": [],
            "imu": [],
            "gesture": [],
            "battery": [],
            "error": [],
            "connect": [],
            "disconnect": [],
        }
        self._last_gesture_time = 0.0
        self._gesture_buffer: list[IMUData] = []
        self._auto_reconnect = True
        self._reconnect_thread: threading.Thread | None = None

    # ── Connection ────────────────────────────────────────────────────────

    def find_port(self) -> str | None:
        """Auto-detect M5Stick USB port."""
        if not _SERIAL_AVAILABLE:
            return None

        ports = serial.tools.list_ports.comports()
        
        # Method 1: Match by VID/PID
        for port in ports:
            if port.vid and port.pid:
                for vid, pid in _M5_USB_VID_PID:
                    if port.vid == vid and port.pid == pid:
                        return port.device

        # Method 2: Match by description/name
        for port in ports:
            desc = (port.description or "").lower()
            name = (port.name or "").lower()
            if any(kw in desc for kw in ["m5stack", "m5stick", "cp2104", "ch340", "ch9102"]):
                return port.device
            if any(kw in name for kw in ["m5stack", "m5stick"]):
                return port.device

        # Method 3: Common port patterns
        if sys.platform == "win32":
            for port in ports:
                if port.device.startswith("COM"):
                    # Try to open and identify
                    if self._try_identify(port.device):
                        return port.device
        else:
            for port in ports:
                if "/dev/ttyUSB" in port.device or "/dev/ttyACM" in port.device:
                    return port.device

        return None

    def _try_identify(self, port: str) -> bool:
        """Try to identify M5Stick by sending a ping command."""
        try:
            with serial.Serial(port, _BAUD_RATE, timeout=1) as s:
                s.write(b'{"cmd":"ping"}\n')
                time.sleep(0.3)
                response = s.readline().decode("utf-8", errors="ignore").strip()
                if response:
                    data = json.loads(response)
                    return data.get("type") == "pong" or data.get("device", "").startswith("M5")
        except Exception:
            pass
        return False

    def connect(self, port: str | None = None) -> bool:
        """Connect to M5Stick via USB."""
        if not _SERIAL_AVAILABLE:
            print("[M5Stick] pyserial not installed. Run: pip install pyserial")
            return False

        with self._lock:
            if self._serial and self._serial.is_open:
                return True

            target_port = port or self.find_port()
            if not target_port:
                print("[M5Stick] No M5Stick device found on USB")
                return False

            try:
                self._serial = serial.Serial(
                    target_port,
                    _BAUD_RATE,
                    timeout=_READ_TIMEOUT,
                    write_timeout=1.0
                )
                self._port = target_port
                self._running = True

                # Start read thread
                self._read_thread = threading.Thread(
                    target=self._read_loop,
                    daemon=True,
                    name="M5Stick-Reader"
                )
                self._read_thread.start()

                # Request device info
                time.sleep(0.5)  # Wait for device to be ready
                self._send_command("info")

                self._device_info.port = target_port
                self._device_info.connected = True

                # Fire connect callback
                self._fire_event("connect", {"port": target_port})

                print(f"[M5Stick] Connected on {target_port}")
                return True

            except serial.SerialException as e:
                print(f"[M5Stick] Connection failed: {e}")
                self._serial = None
                return False

    def disconnect(self) -> None:
        """Disconnect from M5Stick."""
        with self._lock:
            self._running = False
            self._auto_reconnect = False
            
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            
            self._serial = None
            self._device_info.connected = False
            self._fire_event("disconnect", {})
            print("[M5Stick] Disconnected")

    @property
    def is_connected(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def imu(self) -> IMUData:
        return self._imu_data

    # ── Communication ─────────────────────────────────────────────────────

    def _send_command(self, cmd: str, **kwargs) -> bool:
        """Send a JSON command to M5Stick."""
        if not self._serial or not self._serial.is_open:
            return False
        
        try:
            msg = {"cmd": cmd, **kwargs}
            data = json.dumps(msg) + "\n"
            self._serial.write(data.encode("utf-8"))
            return True
        except Exception as e:
            print(f"[M5Stick] Send error: {e}")
            return False

    def _read_loop(self) -> None:
        """Background thread: read and parse JSON from M5Stick."""
        while self._running:
            try:
                if not self._serial or not self._serial.is_open:
                    break

                line = self._serial.readline()
                if not line:
                    continue

                text = line.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue

                try:
                    data = json.loads(text)
                    self._process_message(data)
                except json.JSONDecodeError:
                    # Not JSON, might be debug output
                    if text.startswith("[M5]"):
                        print(text)

            except serial.SerialException:
                if self._running:
                    print("[M5Stick] Connection lost")
                    self._device_info.connected = False
                    self._fire_event("disconnect", {})
                    
                    if self._auto_reconnect:
                        self._start_reconnect()
                break
            except Exception as e:
                if self._running:
                    print(f"[M5Stick] Read error: {e}")
                time.sleep(0.1)

    def _process_message(self, data: dict) -> None:
        """Process incoming JSON message from M5Stick."""
        msg_type = data.get("type", "")

        if msg_type == "imu":
            self._imu_data = IMUData(
                acc_x=data.get("ax", 0),
                acc_y=data.get("ay", 0),
                acc_z=data.get("az", 0),
                gyro_x=data.get("gx", 0),
                gyro_y=data.get("gy", 0),
                gyro_z=data.get("gz", 0),
                timestamp=time.time()
            )
            self._detect_gesture()
            self._fire_event("imu", self._imu_data)

        elif msg_type == "button":
            btn = data.get("btn", "")
            action = data.get("action", "press")  # press, release, long
            self._fire_event("button", {"btn": btn, "action": action})

        elif msg_type == "info":
            self._device_info.model = data.get("model", "M5StickC")
            self._device_info.firmware = data.get("fw", "")
            self._device_info.battery = data.get("bat", 0)
            self._device_info.temperature = data.get("temp", 0)
            print(f"[M5Stick] Device: {self._device_info.model} "
                  f"FW: {self._device_info.firmware} "
                  f"Battery: {self._device_info.battery}%")

        elif msg_type == "battery":
            self._device_info.battery = data.get("level", 0)
            self._fire_event("battery", self._device_info.battery)

        elif msg_type == "gesture":
            gesture = data.get("gesture", "")
            self._fire_event("gesture", gesture)

        elif msg_type == "pong":
            pass  # Response to ping

        # Queue raw event
        try:
            self._event_queue.put_nowait(data)
        except queue.Full:
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait(data)
            except Exception:
                pass

    def _detect_gesture(self) -> None:
        """Detect gestures from IMU data stream."""
        now = time.time()
        self._gesture_buffer.append(self._imu_data)
        
        # Keep only last 30 samples (~1 second at 30Hz)
        self._gesture_buffer = self._gesture_buffer[-30:]
        
        if len(self._gesture_buffer) < 10:
            return
        
        # Cooldown
        if now - self._last_gesture_time < 1.5:
            return

        # Detect shake
        if self._imu_data.is_shaking:
            shake_count = sum(1 for s in self._gesture_buffer[-10:] if s.is_shaking)
            if shake_count >= 6:
                self._last_gesture_time = now
                self._fire_event("gesture", "shake")
                return

        # Detect flip (Z acceleration changes sign)
        z_values = [s.acc_z for s in self._gesture_buffer[-10:]]
        if max(z_values) > 0.5 and min(z_values) < -0.5:
            self._last_gesture_time = now
            self._fire_event("gesture", "flip")
            return

        # Detect tilt left/right
        tilts = [s.tilt_x for s in self._gesture_buffer[-10:]]
        if max(tilts) > 45 and min(tilts) < 10:
            self._last_gesture_time = now
            self._fire_event("gesture", "tilt_right")
        elif min(tilts) < -45 and max(tilts) > -10:
            self._last_gesture_time = now
            self._fire_event("gesture", "tilt_left")

    def _start_reconnect(self) -> None:
        """Start background reconnection thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name="M5Stick-Reconnect"
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """Try to reconnect every 5 seconds."""
        while self._auto_reconnect and not self.is_connected:
            time.sleep(5)
            print("[M5Stick] Attempting reconnection...")
            port = self.find_port()
            if port:
                self.connect(port)

    # ── Event callbacks ───────────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """Register event callback.
        
        Events: button, imu, gesture, battery, error, connect, disconnect
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def off(self, event: str, callback: Callable = None) -> None:
        """Remove event callback."""
        if event in self._callbacks:
            if callback:
                self._callbacks[event] = [
                    cb for cb in self._callbacks[event] if cb != callback
                ]
            else:
                self._callbacks[event] = []

    def _fire_event(self, event: str, data) -> None:
        """Fire all callbacks for an event."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                print(f"[M5Stick] Callback error ({event}): {e}")

    def get_event(self, timeout: float = 0) -> dict | None:
        """Get next event from queue (blocking with timeout)."""
        try:
            return self._event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ── Screen commands ───────────────────────────────────────────────────

    def display_text(self, text: str, x: int = 0, y: int = 0,
                     size: int = 2, color: str = "#FFFFFF",
                     bg: str = "#000000") -> bool:
        """Display text on M5Stick screen."""
        return self._send_command(
            "text", text=text, x=x, y=y,
            size=size, color=color, bg=bg
        )

    def display_clear(self, color: str = "#000000") -> bool:
        """Clear the screen."""
        return self._send_command("clear", color=color)

    def display_title(self, title: str, subtitle: str = "") -> bool:
        """Display title bar with optional subtitle."""
        return self._send_command("title", title=title, subtitle=subtitle)

    def display_icon(self, icon: str, x: int = 0, y: int = 0,
                     size: int = 32) -> bool:
        """Display a built-in icon.
        
        Icons: mic, speaker, wifi, bluetooth, battery, check, error,
               warning, music, camera, search, settings, home, power
        """
        return self._send_command("icon", icon=icon, x=x, y=y, size=size)

    def display_progress(self, value: float, label: str = "") -> bool:
        """Display progress bar (0.0 to 1.0)."""
        return self._send_command(
            "progress",
            value=max(0.0, min(1.0, value)),
            label=label
        )

    def display_status(self, lines: list[str]) -> bool:
        """Display multiple status lines."""
        return self._send_command("status", lines=lines[:5])

    def display_face(self, expression: str = "happy") -> bool:
        """Display emoji face.
        
        Expressions: happy, sad, angry, surprised, sleepy, cool, love
        """
        return self._send_command("face", expression=expression)

    # ── LED commands ──────────────────────────────────────────────────────

    def led_color(self, color: str, brightness: int = 50) -> bool:
        """Set RGB LED color.
        
        Colors: red, green, blue, yellow, cyan, magenta, white, off
        Or hex: #FF0000
        """
        return self._send_command("led", color=color, brightness=brightness)

    def led_blink(self, color: str, times: int = 3,
                  interval: int = 200) -> bool:
        """Blink LED."""
        return self._send_command(
            "blink", color=color,
            times=times, interval=interval
        )

    def led_pulse(self, color: str, duration: int = 2000) -> bool:
        """Pulse LED (breathing effect)."""
        return self._send_command("pulse", color=color, duration=duration)

    # ── Vibration (Plus2 only) ────────────────────────────────────────────

    def vibrate(self, duration: int = 200, strength: int = 100) -> bool:
        """Vibrate motor (M5StickC Plus2 only).
        
        duration: milliseconds
        strength: 0-255
        """
        return self._send_command(
            "vibrate", duration=duration, strength=strength
        )

    def vibrate_pattern(self, pattern: list[int]) -> bool:
        """Vibrate with pattern [on_ms, off_ms, on_ms, ...]."""
        return self._send_command("vibrate_pattern", pattern=pattern[:10])

    # ── Audio (microphone) ────────────────────────────────────────────────

    def start_mic(self, sample_rate: int = 16000) -> bool:
        """Start microphone streaming."""
        return self._send_command("mic_start", rate=sample_rate)

    def stop_mic(self) -> bool:
        """Stop microphone streaming."""
        return self._send_command("mic_stop")

    # ── System commands ───────────────────────────────────────────────────

    def get_battery(self) -> bool:
        """Request battery level."""
        return self._send_command("battery")

    def get_temperature(self) -> bool:
        """Request temperature reading."""
        return self._send_command("temperature")

    def set_brightness(self, level: int) -> bool:
        """Set screen brightness (0-255)."""
        return self._send_command("brightness", level=max(0, min(255, level)))

    def sleep(self, seconds: int = 0) -> bool:
        """Put M5Stick to sleep (0 = deep sleep until button)."""
        return self._send_command("sleep", seconds=seconds)

    def beep(self, frequency: int = 1000, duration: int = 100) -> bool:
        """Play a beep (if buzzer connected to GPIO)."""
        return self._send_command(
            "beep", freq=frequency, duration=duration
        )

    def ping(self) -> bool:
        """Ping the device."""
        return self._send_command("ping")

    def reset(self) -> bool:
        """Software reset."""
        return self._send_command("reset")

    # ── Notification presets ──────────────────────────────────────────────

    def notify_success(self, message: str = "OK") -> None:
        """Green LED + check icon + success message."""
        self.led_color("green", 80)
        self.display_clear()
        self.display_icon("check", 40, 20)
        self.display_text(message, 10, 70, size=2, color="#00FF00")

    def notify_error(self, message: str = "Error") -> None:
        """Red LED + error icon + error message."""
        self.led_color("red", 80)
        self.display_clear()
        self.display_icon("error", 40, 20)
        self.display_text(message, 10, 70, size=2, color="#FF0000")
        self.led_blink("red", 3)

    def notify_warning(self, message: str = "Warning") -> None:
        """Yellow LED + warning icon."""
        self.led_color("yellow", 80)
        self.display_clear()
        self.display_icon("warning", 40, 20)
        self.display_text(message, 10, 70, size=2, color="#FFFF00")

    def notify_listening(self) -> None:
        """Show listening state with pulsing blue LED."""
        self.display_clear()
        self.display_icon("mic", 40, 20, size=48)
        self.display_text("Listening...", 10, 80, size=2, color="#00AAFF")
        self.led_pulse("blue", 5000)

    def notify_speaking(self) -> None:
        """Show speaking state."""
        self.display_clear()
        self.display_icon("speaker", 40, 20, size=48)
        self.display_text("Speaking...", 10, 80, size=2, color="#00FF00")
        self.led_color("green", 60)

    def notify_idle(self) -> None:
        """Return to idle state."""
        self.display_clear()
        self.display_face("happy")
        self.led_color("off")


# ── Singleton ─────────────────────────────────────────────────────────────────

_controller: M5StickController | None = None
_lock = threading.Lock()


def get_controller() -> M5StickController:
    """Get the global M5Stick controller instance."""
    global _controller
    if _controller is None:
        with _lock:
            if _controller is None:
                _controller = M5StickController()
    return _controller


def connect(port: str | None = None) -> bool:
    """Convenience: connect to M5Stick."""
    return get_controller().connect(port)


def disconnect() -> None:
    """Convenience: disconnect from M5Stick."""
    get_controller().disconnect()


def is_connected() -> bool:
    """Convenience: check connection status."""
    return get_controller().is_connected


# ── CLI test mode ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("M5Stick USB Controller — Test Mode")
    print("=" * 50)

    ctrl = get_controller()
    
    port = ctrl.find_port()
    if not port:
        print("No M5Stick found. Connect via USB and try again.")
        sys.exit(1)

    print(f"Found M5Stick on: {port}")
    
    if not ctrl.connect(port):
        print("Connection failed!")
        sys.exit(1)

    # Register event handlers
    def on_button(data):
        print(f"[Button] {data['btn']} ({data['action']})")

    def on_gesture(gesture):
        print(f"[Gesture] {gesture}")

    def on_imu(imu: IMUData):
        pass  # Too noisy for console

    ctrl.on("button", on_button)
    ctrl.on("gesture", on_gesture)

    # Show welcome screen
    time.sleep(1)
    ctrl.display_clear()
    ctrl.display_title("MARK L", "M5Stick Connected")
    ctrl.led_color("cyan", 50)

    print("\nListening for events (Ctrl+C to quit)...")
    print("Try pressing buttons or shaking the device!\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDisconnecting...")
        ctrl.display_text("Goodbye!", 20, 50)
        time.sleep(1)
        ctrl.disconnect()
