"""
dashboard/server.py — JARVIS Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import socket
import string
import time
from pathlib import Path

from actions.holo_lab import PART_CATALOG, normalize_part_ids

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
MAX_UPLOAD_MB = 500


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"JARVIS Dashboard Port {port}"
        prog_rule  = "JARVIS Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until JARVIS is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _device_name(agent: str) -> str:
    """Short human label for a User-Agent string."""
    a = (agent or "").lower()
    for needle, name in (
        ("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"),
        ("windows phone", "Windows Phone"), ("windows", "Windows PC"),
        ("macintosh", "Mac"), ("mac os", "Mac"), ("cros", "Chromebook"),
        ("linux", "Linux"),
    ):
        if needle in a:
            return name
    return "Device"


# ── Phone camera — EDITH-style HUD detection ──────────────────────────────────

_HUD_MODEL = "gemini-2.5-flash"
_MAX_FRAME_BYTES = 8 * 1024 * 1024   # decoded JPEG cap

_HUD_PROMPT = (
    "You are a tactical augmented-reality vision system (like EDITH from Spider-Man). "
    "Look at the photo and list every PERSON, every VEHICLE (plus any readable "
    "license plate), and every notable OBJECT, animal, readable text block or "
    "screen. Return ONLY a JSON array. Each element has exactly: "
    '"label": 2-6 word uppercase name (rules below); '
    '"kind": "person", "vehicle" or "object"; '
    '"detail": one short phrase (max 10 words) with visible appearance/context; '
    '"box": [ymin, xmin, ymax, xmax] as integers 0-1000 in normalized image '
    "coordinates, tight around the target. "
    "Label rules: person → 'PERSON — <clothing/color/pose>' (never a real name); "
    "vehicle → 'CAR / BIKE / TRUCK / BUS — <color> <make & model if recognizable>'; "
    "animal → 'DOG / CAT / BIRD — <color, likely type/breed>'; "
    "license/number plate → 'PLATE — <exact characters>' with kind 'vehicle', but "
    "ONLY if the characters are actually readable in the image; "
    "famous landmark, product or logo → its real well-known name; "
    "anything else → short common name like 'LAPTOP', 'CAR KEYS'. "
    "For people describe ONLY visible appearance/clothing/pose — never guess names "
    "or identities. Prefer precise small boxes over big loose ones. "
    "EXTRA FOR EVERY PERSON (kind == \"person\") add two more fields: "
    '"outline": an array of 10-24 [y, x] points (integers 0-1000) tracing the '
    "silhouette of that person (head, shoulders, arms, torso, legs) as a closed "
    "polygon in clockwise order; "
    '"pose": an object mapping joint names to [y, x] integer 0-1000 points, using '
    "ONLY these keys and only the joints you can actually see: head, neck, "
    "l_shoulder, r_shoulder, l_elbow, r_elbow, l_wrist, r_wrist, pelvis, l_hip, "
    "r_hip, l_knee, r_knee, l_ankle, r_ankle. "
    "Both are in the SAME normalized image coordinates as box. Omit outline/pose "
    "for non-person items. "
    "Max 12 items. If nothing notable is visible return []."
)


class _FrameError(Exception):
    def __init__(self, msg: str, status: int):
        super().__init__(msg)
        self.status = status


def _decode_frame(body: dict) -> bytes:
    """Decode a base64 JPEG frame from a JSON request body."""
    b64 = str(body.get("frame") or "").strip()
    if not b64:
        raise _FrameError("frame is required", 400)
    # tolerate data-URL payloads sent by some clients
    if "," in b64 and b64[:32].lower().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    if not raw or len(raw) > _MAX_FRAME_BYTES:
        raise _FrameError(
            f"frame too large (max {_MAX_FRAME_BYTES // (1024 * 1024)} MB)", 413
        )
    return raw


# Joint names accepted for the EDITH skeleton overlay
_POSE_JOINTS = (
    "head", "neck", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_wrist", "r_wrist", "pelvis", "l_hip", "r_hip",
    "l_knee", "r_knee", "l_ankle", "r_ankle",
)


def _norm_pt(p):
    """[y, x] (or {"y":..,"x":..}) → clamped (y, x) floats in 0-1000, else None."""
    if isinstance(p, dict):
        p = [p.get("y"), p.get("x")]
    if not (isinstance(p, (list, tuple)) and len(p) >= 2):
        return None
    try:
        y, x = float(p[0]), float(p[1])
    except (TypeError, ValueError):
        return None
    if y != y or x != x:                       # NaN guard
        return None
    return [max(0.0, min(1000.0, y)), max(0.0, min(1000.0, x))]


def _norm_outline(raw) -> list:
    """Silhouette polygon → list of [y, x] points (max 40)."""
    if not isinstance(raw, (list, tuple)):
        return []
    pts = [q for q in (_norm_pt(p) for p in raw[:40]) if q]
    return pts if len(pts) >= 4 else []


def _norm_pose(raw) -> dict:
    """Joint map → {joint: [y, x]} keeping only known, valid joints."""
    if not isinstance(raw, dict):
        return {}
    pose = {}
    for k in _POSE_JOINTS:
        q = _norm_pt(raw.get(k))
        if q:
            pose[k] = q
    return pose if len(pose) >= 3 else {}


def _edith_detect(image_bytes: bytes) -> list[dict]:
    """Blocking Gemini call → normalized list of HUD detections. Raises on failure."""
    from google import genai as _g
    from google.genai import types as _gt

    key = _get_gemini_key()
    if not key:
        raise RuntimeError("gemini_api_key not configured")
    client = _g.Client(api_key=key)
    resp = client.models.generate_content(
        model=_HUD_MODEL,
        contents=[
            _gt.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            _HUD_PROMPT,
        ],
        config=_gt.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    data = json.loads((resp.text or "").strip() or "[]")
    if isinstance(data, dict):           # model wrapped the array in an object
        data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        box = item.get("box") or item.get("box_2d")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        try:
            box = [float(x) for x in box]
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind", "")).lower()
        if kind not in ("person", "vehicle"):
            kind = "object"
        det = {
            "label":  str(item.get("label") or "TARGET")[:60],
            "kind":   kind,
            "detail": str(item.get("detail") or "")[:120],
            "box":    box,
        }
        if kind == "person":
            outline = _norm_outline(item.get("outline") or item.get("contour"))
            if outline:
                det["outline"] = outline
            pose = _norm_pose(item.get("pose") or item.get("keypoints"))
            if pose:
                det["pose"] = pose
        out.append(det)
        if len(out) >= 12:
            break
    return out


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._ip                          = _local_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._client_info: dict[str, dict] = {}   # dev_id → {ws, ip, name, since}
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._holo_callback               = None
        self._headphones_callback         = None   # async (enabled|None) -> status dict
        self._headphones_button_callback  = None   # async () -> None — phone headphone button
        self._phone_headphones_callback   = None   # async (bool) -> dict — phone Headphones Mode on/off
        self._audio_sink: str | None      = None   # dev_id of the phone running Headphones Mode
        self._loop                        = None   # asyncio loop (set in serve())
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._phone_vision_queue: asyncio.Queue   = asyncio.Queue(maxsize=10)
        self._phone_cam_queue: asyncio.Queue      = asyncio.Queue(maxsize=2)  # live stream → PC HUD
        self._cam_stream_active: bool             = False
        self._audio_out_queue: asyncio.Queue      = asyncio.Queue(maxsize=400)  # JARVIS voice → phones
        # Holo Lab projects are intentionally session-scoped: a design is a
        # visual prototype, not a claim that a physical hologram or wearable
        # has been manufactured.
        self._holo_projects: list[dict]            = []
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self.app                          = self._build_app()

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        certs = BASE_DIR / "config" / "certs"
        return (certs / "jarvis.key").exists() and (certs / "jarvis.crt").exists()

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._ssl_enabled():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_holo_callback(self, fn) -> None:
        """Called when a dashboard or voice command creates a Holo project."""
        self._holo_callback = fn

    def set_headphones_callback(self, fn) -> None:
        """fn is async: (enabled: bool | None) -> dict (headphone-mode status)."""
        self._headphones_callback = fn

    def set_headphones_button_callback(self, fn) -> None:
        """fn is async: () -> None — fired when the phone-side headphone
        button is pressed (EDIT should stop talking and listen)."""
        self._headphones_button_callback = fn

    def set_phone_headphones_callback(self, fn) -> None:
        """fn is async: (enabled: bool) -> dict — a phone turned its
        Headphones Mode on/off (PC speaker should mute while ON)."""
        self._phone_headphones_callback = fn

    def create_holo_project(
        self,
        model: str = "glasses",
        mode: str = "holo",
        name: str = "",
        clarity: int = 85,
        notes: str = "",
        components=None,
        subject: str = "",
        blueprint: str = "",
        geometry=None,
        parts=None,
    ) -> dict:
        """Create one sanitized, session-scoped visual prototype for any subject."""
        allowed_models = {"glasses", "glove", "suit", "custom"}
        allowed_modes = {"holo", "wireframe", "exploded", "clear"}
        model = str(model or "glasses").lower().strip()
        mode = str(mode or "holo").lower().strip()
        if model not in allowed_models:
            raise ValueError("Unknown prototype")
        if mode not in allowed_modes:
            raise ValueError("Unknown display mode")

        def _clean(value, limit: int, fallback: str) -> str:
            text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
            text = re.sub(r"\s+", " ", text).strip()
            return text[:limit] or fallback

        try:
            clarity = int(clarity)
        except (TypeError, ValueError):
            clarity = 85
        default_components = {
            "glasses": ["OPTICAL CAMERA", "HUD LENS", "EDGE SENSOR", "TEMPLE COMPUTE + BATTERY"],
            "glove": ["PALM DISPLAY", "FINGER SENSORS", "WRIST CAMERA", "REMOVABLE POWER MODULE"],
            "suit": ["CHEST SENSOR CORE", "HEAD OPTICS", "MOTION SENSORS", "SERVICE PORT"],
            "custom": ["AI GEOMETRY PRIMITIVES", "DISPLAY / PROJECTION CORE", "SENSOR ARRAY", "POWER + DATA MODULE"],
        }
        raw_components = components if isinstance(components, (list, tuple)) else []
        clean_components = [_clean(item, 72, "COMPONENT") for item in raw_components[:16]]
        if not clean_components:
            clean_components = default_components[model]

        def _number(value, fallback, low, high):
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = fallback
            return max(low, min(high, number))

        primitive_types = {"box", "cylinder", "sphere", "ring", "line", "point", "cone", "plane"}
        clean_geometry = []
        if isinstance(geometry, (list, tuple)):
            for raw in geometry[:32]:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("type") or "box").lower().strip()
                if kind not in primitive_types:
                    continue
                item = {
                    "type": kind,
                    "x": _number(raw.get("x"), 500, 0, 1000),
                    "y": _number(raw.get("y"), 500, 0, 1000),
                    "z": _number(raw.get("z"), 0, -500, 500),
                    "w": _number(raw.get("w"), 140, 4, 700),
                    "h": _number(raw.get("h"), 100, 4, 700),
                    "d": _number(raw.get("d"), 70, 0, 500),
                    "rotation": _number(raw.get("rotation"), 0, -180, 180),
                    "scale": _number(raw.get("scale"), 1, 0.05, 10),
                    "mm_w": _number(raw.get("mm_w"), 0, 0, 2000),
                    "mm_h": _number(raw.get("mm_h"), 0, 0, 2000),
                    "mm_d": _number(raw.get("mm_d"), 0, 0, 2000),
                    "part_id": normalize_part_ids([raw.get("part_id")])[0] if normalize_part_ids([raw.get("part_id")]) else "",
                    "label": _clean(raw.get("label"), 48, ""),
                }
                if kind == "line":
                    item.update({
                        "x2": _number(raw.get("x2"), item["x"] + item["w"], 0, 1000),
                        "y2": _number(raw.get("y2"), item["y"] + item["h"], 0, 1000),
                        "z2": _number(raw.get("z2"), item["z"], -500, 500),
                    })
                clean_geometry.append(item)
        if model == "custom" and not clean_geometry:
            clean_geometry = [
                {"type": "ring", "x": 500, "y": 500, "z": 0, "w": 270, "h": 270, "d": 0, "rotation": 0, "label": "CORE FIELD"},
                {"type": "box", "x": 500, "y": 500, "z": 20, "w": 170, "h": 120, "d": 90, "rotation": 0, "label": "AI CORE"},
                {"type": "sphere", "x": 500, "y": 340, "z": 60, "w": 95, "h": 95, "d": 95, "rotation": 0, "label": "SENSOR"},
            ]
        clean_subject = _clean(subject, 120, "custom hologram object" if model == "custom" else "")
        clean_blueprint = _clean(blueprint, 1200, "")
        project = {
            "id": "HOLO-" + secrets.token_hex(3).upper(),
            "name": _clean(name, 64, "Untitled hologram prototype"),
            "model": model,
            "subject": clean_subject,
            "mode": mode,
            "clarity": max(35, min(100, clarity)),
            "notes": _clean(notes, 600, ""),
            "blueprint": clean_blueprint,
            "components": clean_components,
            "geometry": clean_geometry,
            "parts": normalize_part_ids(parts),
            "created_at": int(time.time()),
        }
        self._holo_projects.append(project)
        self._holo_projects = self._holo_projects[-20:]
        return project

    # ── JARVIS voice → phones ─────────────────────────────────────────────

    def feed_audio(self, chunk: bytes) -> None:
        """main.py drops every JARVIS speech PCM slice here (24 kHz int16 mono).
        Non-blocking: a slow phone must never stall the assistant's voice.
        Thread-safe: called from the Jarvis Voice Module's worker thread too."""
        try:
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(
                    self._audio_out_queue.put_nowait, chunk
                )
            else:
                self._audio_out_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass  # phone lags more than ~8 s behind — drop rather than back up

    def clear_audio(self) -> None:
        """Drop buffered voice PCM (interrupt / headphone button). Thread-safe."""
        try:
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._drain_audio)
            else:
                self._drain_audio()
        except Exception:
            pass

    def _drain_audio(self) -> None:
        while True:
            try:
                self._audio_out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def send_tts(self, text: str) -> bool:
        """Deliver reply text to the phone that runs Headphones Mode
        (the single audio sink), which voices it with its own speech
        synthesis.  Returns False when there is no active sink — the caller
        then falls back to the PC TTS engine.
        """
        sink = self._audio_sink
        if sink and sink in self._client_info:
            try:
                await self._client_info[sink]["ws"].send_json(
                    {"type": "tts", "text": text}
                )
                return True
            except Exception:
                pass
        return False

    async def _audio_broadcast_loop(self) -> None:
        """Fan out JARVIS voice PCM to connected phones (binary WS frames).

        While a phone runs Headphones Mode, its WS connection is the audio
        sink and ONLY it receives the voice — otherwise every connected
        tab/device plays it and the user hears two voices in the earbuds.
        """
        while True:
            chunk = await self._audio_out_queue.get()
            if not self._clients:
                continue
            sink = self._audio_sink
            if sink and sink in self._client_info:
                ws = self._client_info[sink].get("ws")
                try:
                    await ws.send_bytes(chunk)
                    continue
                except Exception:
                    self._audio_sink = None   # sink tab closed → back to fan-out
            dead: set[WebSocket] = set()
            for ws in list(self._clients):
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    dead.add(ws)
            self._clients -= dead

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        # transient pings are not replayed to freshly reconnecting phones
        if msg.get("type") not in ("devices", "vision_status", "live_dets"):
            self._history.append(msg)
            if len(self._history) > 300:
                self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead
        if dead:
            dropped = {id(ws) for ws in dead}
            self._client_info = {
                k: v for k, v in self._client_info.items()
                if id(v.get("ws")) not in dropped
            }

    # ── device management ────────────────────────────────────────────────

    def devices_info(self) -> list[dict]:
        """Snapshot of remotes on the live /ws socket — for the PC hub & phone hub."""
        now = time.time()
        return [
            {
                "id":   did,
                "name": info["name"],
                "ip":   info["ip"],
                "secs": int(now - info["since"]),
            }
            for did, info in list(self._client_info.items())
        ]

    def revoke_all_paired(self) -> int:
        n = len(self._device_sessions)
        self._device_sessions.clear()
        return n

    async def disconnect_device(self, dev_id: str) -> bool:
        """Kick one connected remote off the /ws socket."""
        info = self._client_info.pop(dev_id, None)
        if not info:
            return False
        ws = info.get("ws")
        if ws is not None:
            self._clients.discard(ws)
            try:
                await ws.close(code=4000)
            except Exception:
                pass
        await self.broadcast({"type": "devices", "count": len(self._clients)})
        return True

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        # ── PWA — installable phone app ────────────────────────────────────
        @app.get("/manifest.webmanifest")
        async def pwa_manifest():
            try:
                data = (STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8")
            except Exception:
                return JSONResponse({}, media_type="application/manifest+json")
            return JSONResponse(
                json.loads(data), media_type="application/manifest+json"
            )

        @app.get("/sw.js")
        async def service_worker():
            return FileResponse(
                str(STATIC_DIR / "sw.js"), media_type="application/javascript"
            )

        @app.get("/static/icons/{name}")
        async def pwa_icon(name: str):
            safe = re.sub(r"[^a-zA-Z0-9._-]", "", name)
            path = STATIC_DIR / "icons" / safe
            if path.exists() and path.is_file():
                return FileResponse(str(path))
            return JSONResponse({"error": "Not found"}, status_code=404)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._aes_key(entered)                   # pre-derive & cache
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in JARVIS to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key}

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to JARVIS…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            if not dev_tok or dev_tok not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[dev_tok]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            return JSONResponse({"ok": True, "revoked": count})

        @app.get("/api/devices")
        async def list_devices(req: Request):
            """Who is live on the /ws socket right now (+ how many paired devices)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({
                "devices": self.devices_info(),
                "paired":  len(self._device_sessions),
            })

        @app.post("/api/disconnect")
        async def disconnect_ep(req: Request):
            """Kick one connected remote (hub action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                body = {}
            ok = await self.disconnect_device(str((body or {}).get("id") or ""))
            return JSONResponse({"ok": ok})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Headphones mode (🎧) ──────────────────────────────────────────────
        # The Remote Dashboard button toggles EDIT's headphone mode on the PC:
        # voice output → Bluetooth headphones, mic ← headset mic, and the
        # headphone's own button becomes "EDIT, listen now".

        @app.get("/api/headphones")
        async def headphones_status_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._headphones_callback:
                return JSONResponse({"ok": False, "error": "PC audio unavailable"})
            try:
                status = await self._headphones_callback(None)
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=502)
            return JSONResponse({"ok": True, "status": status})

        @app.post("/api/headphones")
        async def headphones_set_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._headphones_callback:
                return JSONResponse({"ok": False, "error": "PC audio unavailable"})
            try:
                body = await req.json()
            except Exception:
                body = {}
            enabled = bool(body.get("enabled"))
            try:
                status = await self._headphones_callback(enabled)
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=502)
            # the callback itself broadcasts the fresh status to every client
            return JSONResponse({"ok": True, "status": status})

        @app.post("/api/headphones/phone-mode")
        async def headphones_phone_mode_ep(req: Request):
            """Phone Headphones Mode on/off → mute/restore the PC speaker.

            While a phone runs Headphones Mode, EDIT's voice plays ONLY on the
            phone (into the Bluetooth headphones) — the PC must not speak too,
            otherwise the user hears two voices.
            """
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._phone_headphones_callback:
                return JSONResponse({"ok": False, "error": "PC audio unavailable"})
            try:
                body = await req.json()
            except Exception:
                body = {}
            enabled = bool(body.get("enabled"))

            # Single audio sink: while Headphones Mode is ON, only THIS client
            # (the phone with the earbuds) receives EDIT's voice — the others
            # stay silent, otherwise the user hears two voices.
            tok    = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            dev_id = str((body or {}).get("dev_id") or "").strip()
            if enabled:
                if dev_id and dev_id in self._client_info:
                    self._audio_sink = dev_id
                else:
                    # Fallback: newest connected client with the same token
                    for dev_id in reversed(list(self._client_info)):
                        if self._client_info[dev_id].get("token") == tok:
                            self._audio_sink = dev_id
                            break
            else:
                self._audio_sink = None

            try:
                status = await self._phone_headphones_callback(enabled)
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=502)
            # Tell every tab which one is the audio sink, so non-sink tabs
            # mute their own speaker (belt & suspenders against double voice).
            await self.broadcast({"type": "headphones", "sink": self._audio_sink})
            return JSONResponse({"ok": True, "status": status})

        @app.post("/api/headphones/button")
        async def headphones_button_ep(req: Request):
            """Phone-side headphone button (AVRCP play/pause) → EDIT listens.

            The phone catches the headphone's button press via the browser
            mediaSession API and forwards it here so EDIT stops talking and
            the phone-mic channel (headset mic) is heard on the PC.
            """
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._headphones_button_callback:
                return JSONResponse({"ok": False, "error": "PC audio unavailable"})
            try:
                await self._headphones_button_callback()
            except Exception as e:
                return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=502)
            return JSONResponse({"ok": True})

        # ── Phone camera — EDITH vision ───────────────────────────────────────

        @app.post("/api/vision-scan")
        async def vision_scan(req: Request):
            """Phone camera frame → queued for the main Gemini Live session.

            main.py::_relay_phone_vision injects the frame + question into the
            live session, JARVIS answers by voice on the PC, and the transcript
            is broadcast back to every phone feed automatically.
            """
            if not _auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad JSON"}, status_code=400)
            try:
                frame = _decode_frame(body)
            except _FrameError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status)
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad frame"}, status_code=400)

            question = str(body.get("question") or "").strip()[:500]
            try:
                self._phone_vision_queue.put_nowait((frame, "image/jpeg", question))
            except asyncio.QueueFull:
                # Latest view is always more relevant — evict the oldest frame
                try:
                    self._phone_vision_queue.get_nowait()
                    self._phone_vision_queue.put_nowait((frame, "image/jpeg", question))
                except Exception:
                    pass

            if self._wake_callback:
                self._wake_callback()
            asyncio.create_task(self.broadcast(
                {"type": "vision_status", "state": "received"}
            ))
            return JSONResponse({"ok": True})

        @app.post("/api/vision-hud")
        async def vision_hud(req: Request):
            """EDITH-style detection: frame → labeled boxes drawn on the phone HUD."""
            if not _auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad JSON"}, status_code=400)
            try:
                frame = _decode_frame(body)
            except _FrameError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status)
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad frame"}, status_code=400)
            try:
                detections = await asyncio.to_thread(_edith_detect, frame)
            except Exception as e:
                print(f"[Dashboard] HUD detection failed: {e}")
                msg = str(e)
                if any(k in msg for k in (
                    "UNAUTHENTICATED", "API_KEY_INVALID", "API key not valid",
                    "invalid authentication credentials", "ACCESS_TOKEN_TYPE_UNSUPPORTED",
                )):
                    msg = "GEMINI API KEY INVALID — update config/api_keys.json"
                else:
                    msg = msg[:200]
                return JSONResponse({"ok": False, "error": msg}, status_code=502)
            return JSONResponse({"ok": True, "detections": detections})

        # ── Holo Lab — safe wearable prototype records ───────────────────────

        @app.post("/api/holo-project")
        async def create_holo_project(req: Request):
            """Create a session-scoped visual prototype for the Holo Lab.

            The endpoint deliberately stores only a small, sanitized design
            record. It does not drive hardware, transmit camera data, or
            pretend to manufacture a physical hologram.
            """
            if not _auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad JSON"}, status_code=400)
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "JSON object required"}, status_code=400)

            try:
                project = self.create_holo_project(
                    model=body.get("model", "glasses"),
                    mode=body.get("mode", "holo"),
                    name=body.get("name", ""),
                    clarity=body.get("clarity", 85),
                    notes=body.get("notes", ""),
                    components=body.get("components"),
                    subject=body.get("subject", ""),
                    blueprint=body.get("blueprint", ""),
                    geometry=body.get("geometry"),
                    parts=body.get("parts"),
                )
            except ValueError as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            if self._holo_callback:
                try:
                    self._holo_callback(project)
                except Exception as exc:
                    print(f"[Dashboard] Holo PC callback failed: {exc}")
            await self.broadcast({"type": "holo_project", "project": project})
            return JSONResponse({"ok": True, "project": project})

        @app.get("/api/holo-projects")
        async def list_holo_projects(req: Request):
            """Return prototypes created during this dashboard session."""
            if not _auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            return JSONResponse({"ok": True, "projects": list(self._holo_projects)})

        @app.get("/api/holo-parts")
        async def list_holo_parts(req: Request):
            """Return the offline buy/make/test catalog for the remote lab."""
            if not _auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            return JSONResponse({"ok": True, "parts": list(PART_CATALOG)})

        # ── Phone camera live stream → PC HUD ─────────────────────────────────

        @app.websocket("/ws/phone-cam")
        async def phone_cam_ws(websocket: WebSocket, token: str = ""):
            """Continuous JPEG frames from the phone camera; the latest frame is
            what the PC window draws. ~3 fps keeps live preview cheap."""
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._cam_stream_active = True
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone camera streaming to PC."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_cam_queue.put_nowait(data)
                    except asyncio.QueueFull:
                        # always keep only the freshest frame
                        try:
                            self._phone_cam_queue.get_nowait()
                            self._phone_cam_queue.put_nowait(data)
                        except Exception:
                            pass
            except WebSocketDisconnect:
                pass
            finally:
                self._cam_stream_active = False
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone camera stream stopped."}
                ))

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            dev_id = secrets.token_hex(4)
            try:
                ip = websocket.client.host if websocket.client else "?"
            except Exception:
                ip = "?"
            self._client_info[dev_id] = {
                "ws":   websocket,
                "ip":   ip,
                "name": _device_name(websocket.headers.get("user-agent", "")),
                "since": time.time(),
                "token": tok,
            }
            # Tell this client its own dev_id so it can self-identify as the
            # audio sink when it enables Headphones Mode (distinguishes tabs
            # that share the same auth token).
            try:
                await websocket.send_json({"type": "dev_id", "id": dev_id})
            except Exception:
                pass
            asyncio.create_task(self.broadcast(
                {"type": "devices", "count": len(self._clients)}
            ))
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)
                self._client_info.pop(dev_id, None)
                if self._audio_sink == dev_id:
                    self._audio_sink = None   # the headphones-mode tab closed
                asyncio.create_task(self.broadcast(
                    {"type": "devices", "count": len(self._clients)}
                ))

        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        self._loop = asyncio.get_event_loop()   # for thread-safe feed_audio
        # Firewall setup runs in a thread — uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        use_ssl  = self._ssl_enabled()
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"

        if use_ssl:
            asyncio.create_task(self._serve_alias())

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        print("[Dashboard] Press 'Remote Control' in JARVIS UI to get the QR code.")
        asyncio.create_task(self._audio_broadcast_loop())
        await uvicorn.Server(cfg).serve()
