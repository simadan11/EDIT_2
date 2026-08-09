"""
Headphones Mode (🎧 Режим наушников) for EDIT / MARK.

Turns a Bluetooth headset into a hands-free channel for the assistant:

  • Detects Bluetooth audio endpoints currently connected to the PC.
  • Routes EDIT's voice output → BT headphones (A2DP / Stereo endpoint).
  • Routes the microphone input ← BT headset mic (Hands-Free endpoint,
    falls back to the default PC mic when the headset has no mic).
  • Listens for the headphone's multifunction button (AVRCP play/pause is
    translated by Windows into the media key VK_MEDIA_PLAY_PAUSE) — a tap
    makes EDIT stop talking and start listening (push-to-listen).

Detection
---------
Windows: `Get-PnpDevice -Class AudioEndpoint -Status OK` filtered by the
InstanceId prefix ``BTHENUM\\`` / ``BTH\\`` (Bluetooth enumerator — NOT
localised, so it works on any Windows language).  The endpoint FriendlyName
is then matched against PortAudio/sounddevice device names to resolve the
device indices used for input/output streams.

Other OS: simple keyword scan over sounddevice device names (bluetooth,
headphone, наушник, гарнитура, hands-free, …).  Button capture is
Windows-only (the `keyboard` package).

Button capture
--------------
Requires `keyboard` (pip install keyboard; Windows-only hook).  If the
package is missing, the mode still works — only the headphone-button
trigger is unavailable (the UI toggle still reroutes the audio).
"""

from __future__ import annotations

import platform
import subprocess
import threading
import time

try:
    import sounddevice as sd
except ImportError:                      # pragma: no cover
    sd = None

# Keyword fallback (non-Windows — no PnP endpoint query available)
_BT_KEYWORDS = (
    "bluetooth", "bt-", " bts", "a2dp", "hfp",
    "hands-free", "handsfree", "headset", "headphone",
    "наушник", "гарнитур", "блютуз",
)

_DEBOUNCE_SECS = 0.4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _powershell(cmd: str, timeout: float = 4.0) -> str:
    """Run a PowerShell one-liner on Windows and return stdout (best-effort)."""
    if platform.system() != "Windows":
        return ""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            creationflags=flags,
        )
        return p.stdout or ""
    except Exception:
        return ""


def _detect_bt_endpoint_names() -> list[str]:
    """
    Windows: names of connected Bluetooth AudioEndpoint devices.

    Uses the InstanceId (BTHENUM\\ / BTH\\) — not the localised FriendlyName —
    so Russian Windows ("Наушники …") is detected the same as English.
    """
    if platform.system() != "Windows":
        return []
    ps = (
        "Get-PnpDevice -Class AudioEndpoint -Status OK | "
        "Where-Object { $_.InstanceId -match 'BTHENUM|^BTH\\\\' } | "
        "ForEach-Object { $_.FriendlyName }"
    )
    names: list[str] = []
    for line in _powershell(ps).splitlines():
        line = line.strip()
        if line and line not in names:
            names.append(line)
    return names


def _query_devices():
    """Return (all_devices, input_devices, output_devices) from sounddevice."""
    if sd is None:
        return [], [], []
    try:
        devs = sd.query_devices()
    except Exception:
        return [], [], []
    ins, outs = [], []
    for i, d in enumerate(devs):
        if int(d.get("max_input_channels") or 0) > 0:
            ins.append((i, str(d.get("name") or ""), d))
        if int(d.get("max_output_channels") or 0) > 0:
            outs.append((i, str(d.get("name") or ""), d))
    return devs, ins, outs


def _score_output(name_low: str, bt_names_low: list[str]) -> int:
    """Prefer a stereo A2DP 'Headphones' endpoint for output."""
    score = 0
    for fn in bt_names_low:
        if fn and fn in name_low:
            score += 4
            break
    if any(k in name_low for k in ("headphone", "наушник", "stereo", "a2dp")):
        score += 2
    if any(k in name_low for k in ("hands-free", "handsfree", "headset",
                                   "гарнитур", "микрофон", "hands free")):
        score -= 3
    return score


def _score_input(name_low: str, bt_names_low: list[str]) -> int:
    """Prefer the Hands-Free headset microphone for input."""
    score = 0
    for fn in bt_names_low:
        if fn and fn in name_low:
            score += 4
            break
    if any(k in name_low for k in ("headset", "hands-free", "handsfree",
                                   "hands free", "микрофон", "гарнитур",
                                   "микрофонная")):
        score += 3
    if any(k in name_low for k in ("speakers", "динамик")):
        score -= 2
    return score


def _match_devices(bt_names: list[str], ins, outs) -> dict:
    """Map BT endpoint FriendlyNames → sounddevice input/output indices."""
    bt_low = [n.lower().strip() for n in bt_names if n and n.strip()]
    out_pick, in_pick = None, None
    out_score, in_score = -10 ** 6, -10 ** 6

    for i, name, _d in outs:
        s = _score_output(name.lower(), bt_low)
        if s > out_score:
            out_score, out_pick = s, (i, name)
    for i, name, _d in ins:
        s = _score_input(name.lower(), bt_low)
        if s > in_score:
            in_score, in_pick = s, (i, name)

    return {
        "connected": bool(out_pick is not None),
        "output_idx": out_pick[0] if out_pick else None,
        "output":     out_pick[1] if out_pick else None,
        "input_idx":  in_pick[0] if in_pick else None,
        "input":      in_pick[1] if in_pick else None,
    }


def _match_by_keywords(ins, outs) -> dict:
    """Non-Windows / fallback: pick BT-looking devices by name keywords."""
    out_pick, in_pick = None, None
    out_kw = ("bluetooth", "a2dp", "наушник", "блютуз", "hands-free",
              "handsfree", "hands free", "headphone")
    in_kw = ("bluetooth", "headset", "hands-free", "handsfree", "hands free",
             "гарнитур", "наушник", "блютуз", "headphone")
    for i, name, _d in outs:
        low = name.lower()
        if any(k in low for k in out_kw):
            if out_pick is None:
                out_pick = (i, name)
            if "headphone" in low or "наушник" in low:
                out_pick = (i, name)
    for i, name, _d in ins:
        low = name.lower()
        if any(k in low for k in in_kw):
            if in_pick is None:
                in_pick = (i, name)
    return {
        "connected": bool(out_pick is not None or in_pick is not None),
        "output_idx": out_pick[0] if out_pick else None,
        "output":     out_pick[1] if out_pick else None,
        "input_idx":  in_pick[0] if in_pick else None,
        "input":      in_pick[1] if in_pick else None,
    }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class HeadphonesManager:
    """
    Tracks Bluetooth-headphone availability, reroutes audio devices and
    captures the headphone's multifunction button (AVRCP play/pause).

    Thread-safe: set_enabled/detect/status can be called from any thread.
    """

    def __init__(self, on_button=None, on_status_changed=None):
        self._lock = threading.Lock()
        self._enabled = False
        self._device: dict = {
            "connected": False, "output_idx": None, "output": None,
            "input_idx": None, "input": None,
        }
        self._old_defaults: tuple | None = None
        self._on_button = on_button          # callable() — headphone button
        self._on_status_changed = on_status_changed  # callable(status dict)
        self._hook_handle = None
        self._hook_name = None
        self._last_press = 0.0
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── state ──────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def status(self) -> dict:
        with self._lock:
            dev = dict(self._device)
        return {
            "enabled": self._enabled,
            "connected": bool(dev.get("connected")),
            "name": dev.get("output") or dev.get("input"),
            "output": dev.get("output"),
            "input": dev.get("input"),
            "output_idx": dev.get("output_idx"),
            "input_idx": dev.get("input_idx"),
            "button": self._hook_name is not None,
        }

    # ── detection ──────────────────────────────────────────────────────────

    def detect(self, timeout: float = 4.0) -> dict:
        """Find currently-connected Bluetooth audio endpoints."""
        with self._lock:
            return self._detect_locked(timeout)

    def _detect_locked(self, timeout: float = 4.0) -> dict:
        """(lock held) Find currently-connected Bluetooth audio endpoints."""
        bt_names = _detect_bt_endpoint_names()
        _all, ins, outs = _query_devices()
        if platform.system() == "Windows":
            # Windows: trust the PnP query.  Audio endpoints for a disconnected
            # headset stay registered, so keyword matching would falsely report
            # a connection — only map devices the OS actually lists as OK.
            if bt_names and outs:
                dev = _match_devices(bt_names, ins, outs)
            else:
                dev = {"connected": False, "output_idx": None, "output": None,
                       "input_idx": None, "input": None}
        else:
            dev = _match_by_keywords(ins, outs)
        # A headset with only a mic still counts as connected
        dev["connected"] = bool(
            dev.get("output_idx") is not None or dev.get("input_idx") is not None
        )
        dev["name"] = dev.get("output") or dev.get("input")
        self._device = dev
        return dev

    # ── routing ────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> dict:
        """
        Turn headphone mode on/off.  Blocking (runs a PowerShell query) —
        call from a worker thread.

        Returns the current status dict.
        """
        with self._lock:
            if enabled == self._enabled:
                self._apply_routing_locked()
                return self._make_status_locked()

            self._enabled = enabled
            if enabled:
                self._apply_routing_locked()
                self._start_button_listener_locked()
                self._start_monitor()
            else:
                self._stop_button_listener_locked()
                self._stop_monitor()
                self._restore_defaults_locked()
            return self._make_status_locked()

    def _apply_routing_locked(self) -> None:
        """(lock held) Detect BT devices and point sd.default at them."""
        if sd is None:
            return
        dev = self._detect_locked()
        if dev.get("connected"):
            self._old_defaults = sd.default.device
            try:
                sd.default.device = (dev.get("input_idx"), dev.get("output_idx"))
            except Exception as e:
                print(f"[Headphones] ⚠️  Could not reroute audio: {e}")
        else:
            print("[Headphones] No Bluetooth headphones detected — "
                  "audio stays on the default devices.")

    def _restore_defaults_locked(self) -> None:
        if sd is None or self._old_defaults is None:
            return
        try:
            sd.default.device = self._old_defaults
        except Exception as e:
            print(f"[Headphones] ⚠️  Could not restore audio defaults: {e}")
        self._old_defaults = None

    def _make_status_locked(self) -> dict:
        dev = dict(self._device)
        return {
            "enabled": self._enabled,
            "connected": bool(dev.get("connected")),
            "name": dev.get("output") or dev.get("input"),
            "output": dev.get("output"),
            "input": dev.get("input"),
            "output_idx": dev.get("output_idx"),
            "input_idx": dev.get("input_idx"),
            "button": self._hook_name is not None,
        }

    # ── periodic re-detection (headphones may connect later) ───────────────

    def _start_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True,
            name="Headphones-Monitor",
        )
        self._monitor_thread.start()

    def _stop_monitor(self) -> None:
        self._stop_event.set()

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(10):
            try:
                with self._lock:
                    enabled = self._enabled
                    prev = dict(self._device)
                if not enabled:
                    return
                dev = self._detect_locked()
                changed = (
                    dev.get("connected") != prev.get("connected")
                    or dev.get("output_idx") != prev.get("output_idx")
                )
                if not changed:
                    continue
                with self._lock:
                    if not self._enabled:
                        return
                    if dev.get("connected"):
                        self._apply_routing_locked()
                    else:
                        self._restore_defaults_locked()
                    status = self._make_status_locked()
                if self._on_status_changed:
                    try:
                        self._on_status_changed(status)
                    except Exception as e:
                        print(f"[Headphones] Status callback error: {e}")
            except Exception as e:
                print(f"[Headphones] Monitor error: {e}")

    # ── headphone button (AVRCP play/pause → Windows media key) ────────────

    def _start_button_listener_locked(self) -> None:
        if self._hook_handle is not None:
            return
        if platform.system() != "Windows":
            print("[Headphones] Button capture is Windows-only.")
            return
        try:
            import keyboard
        except ImportError:
            print("[Headphones] 'keyboard' not installed — headphone-button "
                  "trigger disabled. Run: pip install keyboard")
            return
        for name in ("play/pause", "media play/pause", "playpause"):
            try:
                handle = keyboard.on_press_key(
                    name, lambda _e: self._handle_button()
                )
                self._hook_handle = handle
                self._hook_name = name
                print(f"[Headphones] 🎧 Headphone button hook active "
                      f"(media key '{name}')")
                return
            except Exception:
                continue
        print("[Headphones] ⚠️  Could not install the media-key hook.")

    def _stop_button_listener_locked(self) -> None:
        if self._hook_handle is None:
            return
        try:
            import keyboard
            keyboard.unhook(self._hook_handle)
        except Exception:
            pass
        self._hook_handle = None
        self._hook_name = None

    def _handle_button(self) -> None:
        now = time.time()
        if now - self._last_press < _DEBOUNCE_SECS:
            return
        self._last_press = now
        if self._on_button:
            try:
                self._on_button()
            except Exception as e:
                print(f"[Headphones] Button handler error: {e}")
