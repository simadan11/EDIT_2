from __future__ import annotations

import html
import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

# Prevent SetProcessDpiAwarenessContext errors on Windows
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
if platform.system() == "Windows" and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "windows:dpiawareness=0"

import psutil

from actions.holo_lab import (
    PART_CATALOG, PARTS_BY_ID, diagnose_project, format_diagnostics,
    normalize_part_ids, parts_for_ids, suggested_part_ids,
)

if platform.system() == "Windows":

    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut, QTextDocument,
)
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


# Ana renge (accent) bağlı anahtarlar — durum renkleri (ACC, GREEN, RED…) sabit kalır
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Seçilen accent rengine göre tüm turkuaz-ailesi paleti yeniden türetir
    (hue kaydırma — parlaklık/doygunluk oranları korunur, tasarım bozulmaz).
    Boyanan öğeler (HUD, dalga formu, metrikler) bir sonraki karede yeni
    rengi alır; stylesheet tabanlı paneller yeniden kurulduklarında alır.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # griye yakın accent → tüm tema desaturize edilir

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """C sınıfındaki accent'e bağlı renklerin anlık kopyası."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    CANLI tam tema değişimi. Uygulamadaki HER widget'ın stylesheet'inde eski
    palet renklerini yenileriyle değiştirir ve yeniden çizdirir. Böylece renk
    değişimi yalnızca boyanan öğelerde değil, panel/buton/kenarlık dahil tüm
    arayüzde ANINDA uygulanır — yeniden başlatma gerekmez.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free, works on all platforms if installed
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes
        try:
            import ctypes
            _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            nv = ctypes.CDLL(_lib)
            nv.nvmlInit_v2()
            dev = ctypes.c_void_p()
            nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess)
        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                pass

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots
        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31

        # ── solid (never transparent) core disc behind the orb ────────────
        r_solid = r_face * 1.12 * self._scale
        disc = QRadialGradient(cx, cy - r_solid * 0.35, r_solid * 1.7)
        disc.setColorAt(0.0, qcol(C.PANEL2, 255))
        disc.setColorAt(0.6, qcol(C.DARK, 255))
        disc.setColorAt(1.0, qcol(C.BG, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(disc))
        p.drawEllipse(QRectF(cx - r_solid, cy - r_solid, r_solid * 2, r_solid * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, 220), 1.6))
        p.drawEllipse(QRectF(cx - r_solid, cy - r_solid, r_solid * 2, r_solid * 2))

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # face
        if self._face_px:
            fsz    = int(fw * 0.62 * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
        else:
            orb_r = int(fw * 0.27 * self._scale)
            oc    = (200, 0, 50) if self.muted else (0, 60, 110)
            for i in range(8, 0, -1):
                r2  = int(orb_r * i / 8)
                frc = i / 8
                p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), 255)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
            p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 80, cy - 14, 160, 28),
                       Qt.AlignmentFlag.AlignCenter, self._assistant_name)

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # status text
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

class _GearButton(QWidget):
    """Floating animated settings button — the only chrome besides the orb."""

    clicked = pyqtSignal()
    SIZE = 46

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Settings & Controls")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._angle   = 0.0
        self._hover   = 0.0        # 0..1 eased hover amount
        self._active  = False
        self._press   = 0.0
        self._pulse   = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def set_active(self, on: bool):
        self._active = on
        self.update()

    def _step(self):
        target = 1.0 if (self.underMouse() or self._active) else 0.0
        self._hover += (target - self._hover) * 0.18
        self._press *= 0.86
        self._pulse = (self._pulse + 0.03) % (2 * math.pi)
        spin = 0.4 + 2.6 * self._hover
        self._angle = (self._angle + spin) % 360
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = 1.0

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width(); cx = cy = W / 2
        hov = max(0.0, min(1.0, self._hover))
        breathe = 0.5 + 0.5 * math.sin(self._pulse)

        # soft outer glow
        for i in range(6, 0, -1):
            r = (W / 2 - 2) * (0.72 + i * 0.045) * (1 + 0.02 * self._press)
            a = int((10 + 26 * hov + 8 * breathe) * (i / 6))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(cx, cy), r, r)

        # solid disc (never transparent)
        r_disc = W / 2 - 5
        grad = QRadialGradient(cx, cy - r_disc * 0.3, r_disc * 1.6)
        grad.setColorAt(0.0, qcol(C.PANEL2, 255))
        grad.setColorAt(1.0, qcol(C.BG, 255))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(qcol(C.PRI, int(140 + 100 * hov)), 1.4))
        p.drawEllipse(QPointF(cx, cy), r_disc, r_disc)

        # rotating dashed ring
        p.setPen(QPen(qcol(C.PRI, int(90 + 120 * hov)), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        rr = r_disc - 3
        rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
        base = self._angle
        for k in range(4):
            p.drawArc(rect, int((base + k * 90) * 16), int(52 * 16))

        # gear glyph
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle * 0.6)
        col = qcol(C.PRI, int(200 + 55 * hov))
        p.setPen(QPen(col, 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        r_out = r_disc * 0.60
        r_in  = r_disc * 0.34
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(math.cos(a) * r_in,  math.sin(a) * r_in),
                       QPointF(math.cos(a) * r_out, math.sin(a) * r_out))
        p.drawEllipse(QPointF(0, 0), r_in, r_in)
        p.setBrush(QBrush(qcol(C.PRI, int(120 + 100 * hov))))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), r_in * 0.34, r_in * 0.34)
        p.restore()


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for EDIT", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class HoloBlueprintCanvas(QWidget):
    """Animated PC-side hologram and blueprint renderer.

    It is deliberately vector based: the assistant can redraw a safe concept
    immediately from a structured project instead of pretending that a real
    free-space hologram or hardware device exists.
    """

    selection_changed = pyqtSignal(int)
    scene_changed = pyqtSignal(object)

    _ALIASES = {
        "smart glasses": "glasses", "camera glasses": "glasses", "glass": "glasses",
        "ар очки": "glasses", "очки": "glasses", "перчатка": "glove", "костюм": "suit",
        "any": "custom", "any object": "custom", "произвольный": "custom", "любой объект": "custom",
    }
    _DEFAULT_PARTS = {
        "glasses": ["OPTICAL CAMERA", "HUD LENS", "EDGE SENSOR", "TEMPLE COMPUTE + BATTERY"],
        "glove": ["PALM DISPLAY", "FINGER SENSORS", "WRIST CAMERA", "REMOVABLE POWER MODULE"],
        "suit": ["CHEST SENSOR CORE", "HEAD OPTICS", "MOTION SENSORS", "SERVICE PORT"],
        "custom": ["AI GEOMETRY PRIMITIVES", "DISPLAY / PROJECTION CORE", "SENSOR ARRAY", "POWER + DATA MODULE"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(430, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._project: dict = {}
        self._selected_index = -1
        self._dragging = False
        self._view_zoom = 1.0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.set_project({})

    def _tick(self):
        self._phase = (self._phase + 0.035) % (math.tau)
        self.update()

    @classmethod
    def _model_key(cls, value: str) -> str:
        raw = str(value or "glasses").lower().strip()
        raw = cls._ALIASES.get(raw, raw)
        return raw if raw in ("glasses", "glove", "suit", "custom") else "custom"

    def set_project(self, project: dict | None):
        project = dict(project or {})
        model = self._model_key(project.get("model"))
        mode = str(project.get("mode") or "holo").lower().strip()
        if mode not in ("holo", "wireframe", "exploded", "clear"):
            mode = "holo"
        components = project.get("components")
        if not isinstance(components, (list, tuple)) or not components:
            components = self._DEFAULT_PARTS[model]
        components = [str(x).strip()[:38] for x in components[:16] if str(x).strip()]
        try:
            clarity = int(project.get("clarity") or 85)
        except (TypeError, ValueError):
            clarity = 85
        geometry = project.get("geometry")
        if not isinstance(geometry, (list, tuple)):
            geometry = []
        safe_geometry = [g for g in geometry[:32] if isinstance(g, dict)]
        safe_parts = normalize_part_ids(project.get("parts"))
        if self._selected_index >= len(safe_geometry):
            self._selected_index = -1
        self._project = {
            "id": str(project.get("id") or "PC-HOLO"),
            "name": str(project.get("name") or "Untitled hologram prototype")[:64],
            "subject": str(project.get("subject") or "")[:120],
            "model": model,
            "mode": mode,
            "clarity": max(35, min(100, clarity)),
            "notes": str(project.get("notes") or "")[:600],
            "blueprint": str(project.get("blueprint") or "")[:1200],
            "components": components or list(self._DEFAULT_PARTS[model]),
            "geometry": safe_geometry,
            "parts": safe_parts,
        }
        self.update()

    def project(self) -> dict:
        return dict(self._project)

    def _scene_to_screen(self, item: dict, index: int, count: int,
                         cx: float, cy: float, scale: float, suffix: str = "") -> QPointF:
        def number(key, fallback=0.0):
            try:
                return float(item.get(key, fallback))
            except (TypeError, ValueError, AttributeError):
                return fallback
        x = number("x" + suffix, 500)
        y = number("y" + suffix, 500)
        z = number("z" + suffix, 0)
        exploded = self._project.get("mode") == "exploded" and not suffix
        ex = ((index - (count - 1) / 2) * 14) if exploded else 0
        ey = -(abs(index - count / 2) * 6) if exploded else 0
        return QPointF(
            cx + (x - 500) * scale * 0.52 + z * scale * 0.13 + ex,
            cy + (y - 500) * scale * 0.38 - z * scale * 0.10 + ey,
        )

    def _screen_to_scene(self, pos: QPointF, index: int) -> tuple[float, float]:
        w, h = self.width(), self.height()
        cx, cy = w * 0.48, h * 0.43
        scale = max(0.45, min(1.05, min(w / 650.0, h / 430.0))) * self._view_zoom
        geometry = self._project.get("geometry") or []
        count = max(1, len(geometry))
        exploded = self._project.get("mode") == "exploded"
        ex = ((index - (count - 1) / 2) * 14) if exploded else 0
        ey = -(abs(index - count / 2) * 6) if exploded else 0
        x = 500 + (pos.x() - cx - ex) / max(0.01, scale * 0.52)
        y = 500 + (pos.y() - cy - ey) / max(0.01, scale * 0.38)
        return max(0.0, min(1000.0, x)), max(0.0, min(1000.0, y))

    def set_selected_index(self, index: int):
        geometry = self._project.get("geometry") or []
        index = int(index) if 0 <= int(index) < len(geometry) else -1
        if index != self._selected_index:
            self._selected_index = index
            self.selection_changed.emit(index)
            self.update()

    def selected_index(self) -> int:
        return self._selected_index

    def wheelEvent(self, event):
        direction = 1 if event.angleDelta().y() > 0 else -1
        self._view_zoom = max(0.55, min(1.8, self._view_zoom + direction * 0.08))
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        geometry = self._project.get("geometry") or []
        if not geometry:
            self.set_selected_index(-1)
            return
        w, h = self.width(), self.height()
        cx, cy = w * 0.48, h * 0.43
        scale = max(0.45, min(1.05, min(w / 650.0, h / 430.0))) * self._view_zoom
        nearest, distance = -1, float("inf")
        for index, item in enumerate(geometry):
            if not isinstance(item, dict):
                continue
            point = self._scene_to_screen(item, index, len(geometry), cx, cy, scale)
            d = math.hypot(point.x() - event.position().x(), point.y() - event.position().y())
            if d < distance:
                nearest, distance = index, d
        if nearest >= 0:
            candidate = geometry[nearest] if nearest < len(geometry) else {}
            try:
                hit_radius = max(36.0, max(float(candidate.get("w", 100)) * scale * .28, float(candidate.get("h", 100)) * scale * .22))
            except (TypeError, ValueError, AttributeError):
                hit_radius = max(30.0, 42.0 * scale)
        else:
            hit_radius = 0
        if nearest >= 0 and distance <= hit_radius:
            self.set_selected_index(nearest)
            self._dragging = True
        else:
            self.set_selected_index(-1)

    def mouseMoveEvent(self, event):
        if not self._dragging or not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)
        geometry = self._project.get("geometry") or []
        index = self._selected_index
        if 0 <= index < len(geometry) and isinstance(geometry[index], dict):
            x, y = self._screen_to_scene(event.position(), index)
            geometry[index]["x"], geometry[index]["y"] = round(x, 1), round(y, 1)
            self.scene_changed.emit(self.project())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        return super().mouseReleaseEvent(event)

    def _palette(self):
        mode = self._project.get("mode", "holo")
        if mode == "clear":
            return QColor(C.GREEN), qcol(C.GREEN, 40), QColor(C.ACC2)
        return QColor(C.PRI), qcol(C.PRI, 34), QColor(C.ACC)

    def _grid(self, p: QPainter, w: int, h: int):
        p.fillRect(0, 0, w, h, qcol("#00080f"))
        p.setPen(QPen(qcol(C.PRI, 24), 1))
        step = 28
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)
        # perspective floor / coordinate axes
        p.setPen(QPen(qcol(C.PRI, 48), 1))
        horizon = int(h * 0.68)
        for i in range(-8, 9):
            p.drawLine(w // 2, horizon, w // 2 + i * 80, h)
        p.drawLine(0, horizon, w, horizon)
        p.setPen(QPen(qcol(C.PRI, 100), 1))
        p.drawLine(w // 2, 20, w // 2, h - 20)
        p.drawLine(20, int(h * 0.46), w - 20, int(h * 0.46))

    def _text(self, p: QPainter, text: str, x: float, y: float,
              size: int = 8, color: str = C.TEXT_DIM, bold: bool = False):
        p.setFont(QFont("Courier New", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        p.setPen(QPen(QColor(color)))
        p.drawText(QPointF(x, y), str(text))

    def _ring(self, p: QPainter, cx: float, cy: float, r: float,
              color: QColor, dashed: bool = False):
        pen = QPen(color, 1.0)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r * 0.34)

    def _leader(self, p: QPainter, x1: float, y1: float, x2: float, y2: float,
                label: str, color: QColor):
        p.setPen(QPen(color, 1))
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.drawEllipse(QPointF(x1, y1), 2.2, 2.2)
        self._text(p, label, x2 + 5, y2 + 3, 7, color.name(), True)

    def _draw_glasses(self, p: QPainter, cx: float, cy: float, s: float,
                      accent: QColor, fill: QColor):
        exploded = self._project.get("mode") == "exploded"
        wire = self._project.get("mode") == "wireframe"
        dx = 46 * s if exploded else 0
        dy = -28 * s if exploded else 0
        left = QRectF(cx - 176 * s - dx, cy - 42 * s, 132 * s, 82 * s)
        right = QRectF(cx + 44 * s + dx, cy - 42 * s, 132 * s, 82 * s)
        pen = QPen(accent, 2.2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush if wire else QBrush(fill))
        p.drawRoundedRect(left, 20 * s, 20 * s)
        p.drawRoundedRect(right, 20 * s, 20 * s)
        p.drawLine(QPointF(left.right(), cy - 2 * s), QPointF(right.left(), cy - 2 * s))
        p.drawLine(QPointF(left.left(), cy - 8 * s), QPointF(cx - 238 * s, cy - 30 * s))
        p.drawLine(QPointF(right.right(), cy - 8 * s), QPointF(cx + 238 * s, cy - 30 * s))
        # HUD projection strips inside both lenses
        p.setPen(QPen(qcol(C.WHITE, 210), 1.2))
        p.drawLine(QPointF(left.left() + 16 * s, cy + 17 * s),
                   QPointF(left.right() - 20 * s, cy + 17 * s))
        p.drawLine(QPointF(right.left() + 20 * s, cy + 17 * s),
                   QPointF(right.right() - 16 * s, cy + 17 * s))
        p.setPen(QPen(QColor(C.ACC2), 2))
        camera_x = cx + 208 * s + (72 * s if exploded else 0)
        camera_y = cy - 49 * s + (dy if exploded else 0)
        p.setBrush(QBrush(QColor(C.ACC)))
        p.drawEllipse(QPointF(camera_x, camera_y), 11 * s, 11 * s)
        p.setBrush(QBrush(qcol("#050c12")))
        p.drawEllipse(QPointF(camera_x, camera_y), 4 * s, 4 * s)
        p.setBrush(QBrush(QColor(C.ACC2)))
        p.drawEllipse(QPointF(cx - 198 * s, cy - 31 * s), 4 * s, 4 * s)
        # moving optical beam
        p.setPen(QPen(qcol(C.WHITE, 190), 1.3, Qt.PenStyle.DashLine))
        beam_x = camera_x + 9 * s
        beam_y = camera_y + 8 * s
        p.drawLine(QPointF(beam_x, beam_y), QPointF(beam_x + 72 * s, beam_y + 70 * s))
        pulse = (math.sin(self._phase) + 1.0) * 0.5
        self._ring(p, cx, cy + 36 * s, (155 + pulse * 18) * s, qcol(accent.name(), 90))
        self._ring(p, cx, cy + 36 * s, 118 * s, qcol(accent.name(), 55), True)
        self._leader(p, camera_x, camera_y, min(self.width() - 150, camera_x + 70 * s), camera_y - 34 * s, "CAMERA / FOV", QColor(C.ACC))
        self._leader(p, left.left() + 10 * s, cy + 38 * s, max(16, left.left() - 86 * s), cy + 78 * s, "HUD LENS", accent)
        self._text(p, "SMART OPTICS", cx - 52 * s, cy + 126 * s, 10, accent.name(), True)
        self._text(p, "CAMERA + AR DISPLAY / BLUEPRINT", cx - 120 * s, cy + 144 * s, 7, C.TEXT_DIM)

    def _draw_glove(self, p: QPainter, cx: float, cy: float, s: float,
                    accent: QColor, fill: QColor):
        exploded = self._project.get("mode") == "exploded"
        wire = self._project.get("mode") == "wireframe"
        palm = QRectF(cx - 55 * s, cy - 12 * s + (24 * s if exploded else 0), 112 * s, 130 * s)
        p.setPen(QPen(accent, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush if wire else QBrush(fill))
        p.drawRoundedRect(palm, 22 * s, 22 * s)
        for i, x in enumerate((-43, -19, 6, 30)):
            top = cy - (122 - (i % 2) * 10) * s - (28 * s if exploded else 0)
            p.drawRoundedRect(QRectF(cx + x * s, top, 22 * s, 105 * s), 10 * s, 10 * s)
            p.setBrush(QBrush(QColor(C.ACC2)))
            p.drawEllipse(QPointF(cx + (x + 11) * s, top + 10 * s), 4 * s, 4 * s)
            p.setBrush(Qt.BrushStyle.NoBrush if wire else QBrush(fill))
        p.setPen(QPen(QColor(C.WHITE), 1.2))
        p.drawRoundedRect(QRectF(cx - 33 * s, cy + 20 * s, 68 * s, 48 * s), 8 * s, 8 * s)
        p.drawLine(QPointF(cx - 20 * s, cy + 38 * s), QPointF(cx + 18 * s, cy + 38 * s))
        p.drawLine(QPointF(cx - 20 * s, cy + 50 * s), QPointF(cx + 8 * s, cy + 50 * s))
        p.setBrush(QBrush(QColor(C.ACC)))
        p.drawEllipse(QPointF(cx + 67 * s, cy + 82 * s), 10 * s, 10 * s)
        p.setBrush(QBrush(qcol("#050c12")))
        p.drawEllipse(QPointF(cx + 67 * s, cy + 82 * s), 4 * s, 4 * s)
        self._ring(p, cx, cy + 36 * s, 146 * s, qcol(accent.name(), 76))
        self._leader(p, cx + 67 * s, cy + 82 * s, cx + 122 * s, cy + 109 * s, "WRIST CAMERA", QColor(C.ACC))
        self._leader(p, cx - 35 * s, cy - 95 * s, cx - 164 * s, cy - 132 * s, "FINGER SENSORS", QColor(C.ACC2))
        self._text(p, "AR GLOVE", cx - 42 * s, cy + 160 * s, 10, accent.name(), True)
        self._text(p, "GESTURE INTERFACE / BLUEPRINT", cx - 111 * s, cy + 178 * s, 7, C.TEXT_DIM)

    def _draw_suit(self, p: QPainter, cx: float, cy: float, s: float,
                   accent: QColor, fill: QColor):
        exploded = self._project.get("mode") == "exploded"
        wire = self._project.get("mode") == "wireframe"
        head_y = cy - 130 * s - (28 * s if exploded else 0)
        torso = QPainterPath()
        torso.moveTo(QPointF(cx - 74 * s, cy - 84 * s))
        torso.lineTo(QPointF(cx - 138 * s, cy - 18 * s))
        torso.lineTo(QPointF(cx - 102 * s, cy + 13 * s))
        torso.lineTo(QPointF(cx - 76 * s, cy - 10 * s))
        torso.lineTo(QPointF(cx - 85 * s, cy + 150 * s))
        torso.lineTo(QPointF(cx + 85 * s, cy + 150 * s))
        torso.lineTo(QPointF(cx + 76 * s, cy - 10 * s))
        torso.lineTo(QPointF(cx + 102 * s, cy + 13 * s))
        torso.lineTo(QPointF(cx + 138 * s, cy - 18 * s))
        torso.lineTo(QPointF(cx + 74 * s, cy - 84 * s))
        torso.closeSubpath()
        p.setPen(QPen(accent, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush if wire else QBrush(fill))
        p.drawPath(torso)
        p.setBrush(Qt.BrushStyle.NoBrush if wire else QBrush(qcol(C.PRI, 24)))
        p.drawEllipse(QPointF(cx, cy - 8 * s), 34 * s, 34 * s)
        p.setPen(QPen(QColor(C.WHITE), 1.5))
        p.drawEllipse(QPointF(cx, cy - 8 * s), 17 * s, 17 * s)
        p.setPen(QPen(QColor(C.ACC2), 1.2))
        for x, y in ((-67, -4), (67, -4), (-67, 72), (67, 72)):
            p.setBrush(QBrush(QColor(C.ACC2)))
            p.drawEllipse(QPointF(cx + x * s, cy + y * s), 6 * s, 6 * s)
        p.setPen(QPen(QColor(C.ACC), 2))
        p.setBrush(QBrush(QColor(C.ACC)))
        p.drawRoundedRect(QRectF(cx - 12 * s, head_y - 12 * s, 24 * s, 14 * s), 5 * s, 5 * s)
        p.setBrush(QBrush(qcol("#050c12")))
        p.drawEllipse(QPointF(cx, head_y - 5 * s), 4 * s, 4 * s)
        self._ring(p, cx, cy - 8 * s, 142 * s, qcol(accent.name(), 75))
        self._leader(p, cx, cy - 8 * s, cx - 174 * s, cy - 69 * s, "CHEST SENSOR CORE", accent)
        self._leader(p, cx, head_y - 5 * s, cx + 96 * s, head_y - 37 * s, "HEAD OPTICS", QColor(C.ACC))
        self._text(p, "FIELD SUIT", cx - 42 * s, cy + 185 * s, 10, accent.name(), True)
        self._text(p, "SENSOR CORE / BLUEPRINT", cx - 86 * s, cy + 203 * s, 7, C.TEXT_DIM)

    def _draw_custom(self, p: QPainter, cx: float, cy: float, s: float,
                     accent: QColor, fill: QColor):
        """Render AI-supplied geometry primitives for arbitrary holograms."""
        geometry = list(self._project.get("geometry") or [])
        if not geometry:
            geometry = [
                {"type": "ring", "x": 500, "y": 500, "z": 0, "w": 270, "h": 270, "label": "CORE FIELD"},
                {"type": "box", "x": 500, "y": 500, "z": 20, "w": 170, "h": 120, "d": 90, "label": "AI CORE"},
                {"type": "sphere", "x": 500, "y": 340, "z": 60, "w": 95, "h": 95, "d": 95, "label": "SENSOR"},
            ]
        wire = self._project.get("mode") == "wireframe"
        exploded = self._project.get("mode") == "exploded"
        subject = self._project.get("subject") or self._project.get("name") or "CUSTOM OBJECT"

        def number(item, key, fallback=0.0):
            try:
                return float(item.get(key, fallback))
            except (TypeError, ValueError, AttributeError):
                return fallback

        def point(item, suffix=""):
            x = number(item, "x" + suffix, 500)
            y = number(item, "y" + suffix, 500)
            z = number(item, "z" + suffix, 0)
            ex = ((index - (len(geometry) - 1) / 2) * 14) if exploded and not suffix else 0
            return QPointF(
                cx + (x - 500) * s * 0.52 + z * s * 0.13 + ex,
                cy + (y - 500) * s * 0.38 - z * s * 0.10 - (abs(index - len(geometry) / 2) * 6 if exploded and not suffix else 0),
            )

        for index, item in enumerate(geometry[:32]):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "box").lower()
            center = point(item)
            px, py = center.x(), center.y()
            visual_scale = max(0.05, min(10.0, number(item, "scale", 1)))
            ww = max(4.0, number(item, "w", 140) * s * 0.52 * visual_scale)
            hh = max(4.0, number(item, "h", 100) * s * 0.38 * visual_scale)
            dd = max(0.0, number(item, "d", 70) * s * 0.16 * visual_scale)
            rotation = number(item, "rotation", 0)
            p.save()
            p.translate(px, py)
            if rotation:
                p.rotate(rotation)
            p.setPen(QPen(accent, 1.8 if kind not in ("point", "line") else 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush if wire else QBrush(fill))
            if kind in ("box", "plane"):
                rect = QRectF(-ww / 2, -hh / 2, ww, hh)
                p.drawRect(rect)
                if dd:
                    p.drawRect(QRectF(-ww / 2 + dd, -hh / 2 - dd, ww, hh))
                    p.drawLine(rect.topLeft(), QPointF(-ww / 2 + dd, -hh / 2 - dd))
                    p.drawLine(rect.topRight(), QPointF(ww / 2 + dd, -hh / 2 - dd))
                    p.drawLine(rect.bottomLeft(), QPointF(-ww / 2 + dd, hh / 2 - dd))
                    p.drawLine(rect.bottomRight(), QPointF(ww / 2 + dd, hh / 2 - dd))
            elif kind == "cylinder":
                p.drawEllipse(QPointF(0, -hh / 2), ww / 2, hh * 0.18)
                p.drawEllipse(QPointF(0, hh / 2), ww / 2, hh * 0.18)
                p.drawLine(QPointF(-ww / 2, -hh / 2), QPointF(-ww / 2, hh / 2))
                p.drawLine(QPointF(ww / 2, -hh / 2), QPointF(ww / 2, hh / 2))
            elif kind == "sphere":
                radius = min(ww, hh) / 2
                p.drawEllipse(QPointF(0, 0), radius, radius)
                p.drawEllipse(QPointF(0, 0), radius * .55, radius)
                p.drawEllipse(QPointF(0, 0), radius, radius * .55)
            elif kind == "ring":
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(0, 0), ww / 2, hh / 2)
                p.drawEllipse(QPointF(0, 0), ww * .38, hh * .38)
            elif kind == "cone":
                path = QPainterPath()
                path.moveTo(QPointF(0, -hh / 2))
                path.lineTo(QPointF(-ww / 2, hh / 2))
                path.lineTo(QPointF(ww / 2, hh / 2))
                path.closeSubpath()
                p.drawPath(path)
                p.drawEllipse(QPointF(0, hh / 2), ww / 2, hh * .16)
            elif kind == "line":
                p.setBrush(Qt.BrushStyle.NoBrush)
                endpoint = point(item, "2")
                p.drawLine(QPointF(0, 0), QPointF(endpoint.x() - center.x(), endpoint.y() - center.y()))
            else:  # point and unknown safe fallback
                p.setBrush(QBrush(QColor(C.ACC2)))
                p.drawEllipse(QPointF(0, 0), 5 * s, 5 * s)
            p.restore()
            if index == self._selected_index:
                p.setPen(QPen(QColor(C.WHITE), 1.2, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(px, py), max(12, ww * .62), max(12, hh * .62))
                self._text(p, "SELECTED / DRAG TO PLACE", px + 10, py + hh * .72, 7, C.WHITE, True)
            label = str(item.get("label") or "").strip()[:38]
            if label:
                self._leader(p, px, py, min(self.width() - 150, px + 56 * s), py - 24 * s, label, accent)

        pulse = (math.sin(self._phase) + 1.0) * 0.5
        self._ring(p, cx, cy + 24 * s, (170 + pulse * 24) * s, qcol(accent.name(), 84))
        self._ring(p, cx, cy + 24 * s, 115 * s, qcol(accent.name(), 50), True)
        self._text(p, "CUSTOM HOLOGRAM", cx - 72 * s, cy + 150 * s, 10, accent.name(), True)
        self._text(p, str(subject).upper()[:42], cx - 120 * s, cy + 168 * s, 7, C.TEXT_DIM)

    def paintEvent(self, _event):
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._grid(p, w, h)
            accent, fill, hot = self._palette()
            self._text(p, "PC HOLO OUTPUT  //  AI-GENERATED BLUEPRINT", 16, 22, 8, accent.name(), True)
            self._text(p, f"ID {self._project.get('id', 'PC-HOLO')}", max(16, w - 132), 22, 7, C.TEXT_DIM)
            self._text(p, f"MODE {str(self._project.get('mode', 'holo')).upper()}", 16, 40, 7, C.TEXT_DIM)
            subject = str(self._project.get("subject") or self._project.get("name") or "")
            if subject:
                self._text(p, "SUBJECT " + subject.upper()[:52], 16, 56, 7, C.TEXT_MED)
            self._text(p, f"CLARITY {self._project.get('clarity', 85)}%", max(16, w - 106), 40, 7, C.TEXT_DIM)
            cx, cy = w * 0.48, h * 0.43
            s = max(0.45, min(1.05, min(w / 650.0, h / 430.0))) * self._view_zoom
            model = self._project.get("model", "glasses")
            if model == "glove":
                self._draw_glove(p, cx, cy, s, accent, fill)
            elif model == "suit":
                self._draw_suit(p, cx, cy, s, accent, fill)
            elif model == "custom":
                self._draw_custom(p, cx, cy, s, accent, fill)
            else:
                self._draw_glasses(p, cx, cy, s, accent, fill)
            # AI's component list is the lower blueprint strip.
            y = h - min(112, max(88, len(self._project.get("components", [])) * 15 + 34))
            p.setPen(QPen(qcol(accent.name(), 110), 1))
            p.drawLine(16, y - 10, w - 16, y - 10)
            self._text(p, "AI COMPONENT SCHEDULE", 16, y + 4, 7, hot.name(), True)
            for i, component in enumerate(self._project.get("components", [])[:6]):
                self._text(p, f"{i + 1:02d}  {component}", 18, y + 20 + i * 14, 7, C.TEXT_MED)
            if self._project.get("notes"):
                note = " ".join(str(self._project["notes"]).split())[:74]
                self._text(p, "BRIEF  " + note, max(18, w * 0.48), y + 20, 7, C.TEXT_DIM)
            sweep_y = 56 + ((math.sin(self._phase) + 1) * 0.5) * max(80, h * 0.54)
            p.setPen(QPen(qcol(C.WHITE, 80), 1))
            p.drawLine(12, sweep_y, w - 12, sweep_y)
        finally:
            p.end()


class HoloLabOverlay(QWidget):
    """PC monitor surface for AI-generated wearable blueprints."""

    closed = pyqtSignal()
    _OW, _OH = 900, 610

    _MODELS = (
        ("SMART OPTICS / CAMERA GLASSES", "glasses"),
        ("AR GLOVE / GESTURE INTERFACE", "glove"),
        ("FIELD SUIT / SENSOR CORE", "suit"),
        ("CUSTOM / ANY HOLOGRAM", "custom"),
    )
    _MODES = (("HOLO", "holo"), ("WIREFRAME", "wireframe"),
              ("EXPLODED / BY PARTS", "exploded"), ("CLEAR VIEW", "clear"))
    _PARTS = HoloBlueprintCanvas._DEFAULT_PARTS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            HoloLabOverlay {{
                background: rgba(0, 8, 15, 248);
                border: 1px solid {C.BORDER_B}; border-radius: 7px;
            }}
            QComboBox, QLineEdit, QTextEdit {{
                background: #000d14; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 5px;
            }}
            QComboBox:focus, QLineEdit:focus, QTextEdit:focus {{ border-color: {C.PRI}; }}
            QComboBox QAbstractItemView {{ background: #00121c; color: {C.TEXT}; selection-background-color: {C.PRI_GHO}; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("◈  HOLO LAB  //  AI BLUEPRINT + HOLOGRAM")
        title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        header.addWidget(title)
        header.addStretch()
        self._id_lbl = QLabel("PC-HOLO")
        self._id_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._id_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        header.addWidget(self._id_lbl)
        close = QPushButton("✕")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: 1px solid {C.BORDER}; border-radius: 4px;")
        close.clicked.connect(self._close)
        header.addWidget(close)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(8)
        self._canvas = HoloBlueprintCanvas()
        self._canvas.selection_changed.connect(self._on_scene_selection)
        self._canvas.scene_changed.connect(self._on_scene_changed)
        body.addWidget(self._canvas, stretch=1)

        panel = QWidget()
        panel.setFixedWidth(245)
        panel.setStyleSheet(f"background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 5px;")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(9, 9, 9, 9)
        pl.setSpacing(6)

        def _lbl(text, color=C.TEXT_DIM, size=7, bold=False):
            label = QLabel(text)
            label.setFont(QFont("Courier New", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            label.setStyleSheet(f"color: {color}; background: transparent;")
            return label

        pl.addWidget(_lbl("AI-GENERATED DESIGN", C.PRI_DIM, 8, True))
        self._name = QLineEdit("PC wearable prototype")
        self._name.setFont(QFont("Courier New", 9))
        self._name.setFixedHeight(29)
        pl.addWidget(self._name)
        pl.addWidget(_lbl("PROTOTYPE", C.TEXT_DIM))
        self._model = QComboBox()
        for text, key in self._MODELS:
            self._model.addItem(text, key)
        self._model.setFixedHeight(29)
        pl.addWidget(self._model)
        pl.addWidget(_lbl("ANY SUBJECT / OBJECT", C.TEXT_DIM))
        self._subject = QLineEdit()
        self._subject.setPlaceholderText("robot, car, house, planet, anything…")
        self._subject.setFixedHeight(29)
        pl.addWidget(self._subject)
        pl.addWidget(_lbl("DISPLAY / PRESENTATION", C.TEXT_DIM))
        self._mode = QComboBox()
        for text, key in self._MODES:
            self._mode.addItem(text, key)
        self._mode.setFixedHeight(29)
        pl.addWidget(self._mode)
        pl.addWidget(_lbl("DESIGN BRIEF", C.TEXT_DIM))
        self._notes = QTextEdit()
        self._notes.setPlaceholderText("What should the device see, measure or show?")
        self._notes.setFont(QFont("Courier New", 8))
        self._notes.setMaximumHeight(85)
        pl.addWidget(self._notes)
        self._parts_lbl = _lbl("", C.TEXT_MED, 7)
        self._parts_lbl.setWordWrap(True)
        self._parts_lbl.setMinimumHeight(72)
        self._parts_lbl.setMaximumHeight(92)
        pl.addWidget(self._parts_lbl)

        parts_btn = QPushButton("▣  PARTS CATALOG / BUILD")
        parts_btn.setFixedHeight(27)
        parts_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        parts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        parts_btn.setStyleSheet(f"background: {C.PANEL2}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        parts_btn.clicked.connect(self._open_parts_catalog)
        pl.addWidget(parts_btn)
        assembly_btn = QPushButton("▦  ASSEMBLY EDITOR / PLACE PARTS")
        assembly_btn.setFixedHeight(27)
        assembly_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        assembly_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        assembly_btn.setStyleSheet(f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        assembly_btn.clicked.connect(self._open_assembly_editor)
        pl.addWidget(assembly_btn)
        diag_btn = QPushButton("⚠  RUN DIAGNOSTICS / HELP")
        diag_btn.setFixedHeight(27)
        diag_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        diag_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        diag_btn.setStyleSheet(f"background: {C.PANEL2}; color: {C.ACC2}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        diag_btn.clicked.connect(self._run_diagnostics)
        pl.addWidget(diag_btn)
        print_btn = QPushButton("⎙  PRINT BLUEPRINT")
        print_btn.setFixedHeight(27)
        print_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        print_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        print_btn.setStyleSheet(f"background: {C.PANEL2}; color: {C.GREEN}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        print_btn.clicked.connect(self._print_blueprint)
        pl.addWidget(print_btn)
        file_row = QHBoxLayout()
        save_btn = QPushButton("SAVE")
        load_btn = QPushButton("LOAD")
        for file_btn in (save_btn, load_btn):
            file_btn.setFixedHeight(25)
            file_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            file_btn.setStyleSheet(f"background: {C.PANEL2}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        save_btn.clicked.connect(self._save_project)
        load_btn.clicked.connect(self._load_project)
        file_row.addWidget(save_btn); file_row.addWidget(load_btn)
        pl.addLayout(file_row)
        pl.addStretch()

        generate = QPushButton("▸  GENERATE BLUEPRINT")
        generate.setFixedHeight(32)
        generate.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        generate.setCursor(Qt.CursorShape.PointingHandCursor)
        generate.setStyleSheet(f"""
            QPushButton {{ background: {C.PRI}; color: #001018; border: none; border-radius: 4px; }}
            QPushButton:hover {{ background: {C.WHITE}; }}
        """)
        generate.clicked.connect(self._generate_local)
        pl.addWidget(generate)
        self._status = _lbl("READY / waiting for AI or manual design", C.GREEN, 7, True)
        self._status.setWordWrap(True)
        pl.addWidget(self._status)
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFixedWidth(265)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setStyleSheet(f"QScrollArea {{ background: {C.PANEL}; border: none; }} QScrollBar:vertical {{ width: 6px; background: {C.BG}; }} QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 3px; }}")
        panel_scroll.setWidget(panel)
        body.addWidget(panel_scroll)
        root.addLayout(body, stretch=1)

        foot = QLabel("SAFE PC PREVIEW  ·  Vector blueprint only  ·  No hardware or weapon systems are activated")
        foot.setFont(QFont("Courier New", 7))
        foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        root.addWidget(foot)
        self._selected_parts: list[str] = []
        self.set_project({})

    def _close(self):
        self.hide()
        self.closed.emit()

    def _generate_local(self):
        model = self._model.currentData() or "glasses"
        mode = self._mode.currentData() or "holo"
        subject = self._subject.text().strip() or ("custom hologram object" if model == "custom" else "")
        self.set_project({
            "id": "PC-" + time.strftime("%H%M%S"),
            "name": self._name.text().strip() or (subject[:48] if model == "custom" else "PC wearable prototype"),
            "model": model,
            "subject": subject,
            "mode": mode,
            "clarity": 90,
            "notes": self._notes.toPlainText().strip(),
            "components": list(self._PARTS[model]),
            "parts": list(self._selected_parts or suggested_part_ids({"model": model, "subject": subject, "notes": self._notes.toPlainText()})),
        })
        self._status.setText("LOCAL BLUEPRINT GENERATED / READY")

    def set_project(self, project: dict | None):
        p = dict(project or {})
        model = HoloBlueprintCanvas._model_key(p.get("model"))
        p["model"] = model
        self._selected_parts = normalize_part_ids(p.get("parts")) if "parts" in p else suggested_part_ids(p)
        p["parts"] = list(self._selected_parts)
        mode = str(p.get("mode") or "holo").lower().strip()
        model_idx = self._model.findData(model)
        mode_idx = self._mode.findData(mode)
        if model_idx >= 0:
            self._model.setCurrentIndex(model_idx)
        if mode_idx >= 0:
            self._mode.setCurrentIndex(mode_idx)
        self._name.setText(str(p.get("name") or "PC wearable prototype")[:64])
        self._subject.setText(str(p.get("subject") or "")[:120])
        self._notes.setPlainText(str(p.get("notes") or "")[:600])
        self._canvas.set_project(p)
        current = self._canvas.project()
        self._id_lbl.setText(current["id"])
        items = current.get("components") or []
        part_names = [PARTS_BY_ID[part_id]["name"] for part_id in self._selected_parts if part_id in PARTS_BY_ID]
        part_preview = "\n".join(f"• {x}" for x in part_names[:4]) or "• no parts selected"
        self._parts_lbl.setText(
            "COMPONENTS\n" + "\n".join(f"{i + 1:02d}  {x}" for i, x in enumerate(items[:5])) +
            f"\n\nBUILD BOM: {len(self._selected_parts)}\n" + part_preview
        )
        self._status.setText("AI BLUEPRINT RECEIVED / DRAWING ON PC")

    def _on_scene_selection(self, index: int):
        if index >= 0:
            geometry = self._canvas.project().get("geometry") or []
            label = geometry[index].get("label", "PART") if index < len(geometry) else "PART"
            self._status.setText(f"SELECTED {index + 1:02d} / {label}  ·  DRAG IT IN THE VIEWPORT")

    def _on_scene_changed(self, _project=None):
        self._status.setText("SCENE CHANGED / SAVE PROJECT OR RUN DIAGNOSTICS")

    @staticmethod
    def _part_geometry(part: dict, index: int) -> dict:
        category = str(part.get("category") or "MECHANICAL")
        if category in ("VISION", "SENSORS", "AUDIO"):
            kind, w, h, d = "sphere", 80, 80, 70
        elif category in ("DISPLAY", "OPTICS", "LIGHTING"):
            kind, w, h, d = "plane", 150, 90, 12
        elif category in ("POWER", "COMPUTE", "CONTROL", "TEST"):
            kind, w, h, d = "box", 135, 90, 70
        else:
            kind, w, h, d = "box", 120, 70, 50
        col = index % 4
        row = index // 4
        return {
            "type": kind,
            "part_id": str(part.get("id") or ""),
            "label": str(part.get("name") or "PART")[:38],
            "x": 300 + col * 135,
            "y": 360 + row * 115,
            "z": row * 30,
            "w": w,
            "h": h,
            "d": d,
            "rotation": 0,
            "scale": 1,
            "mm_w": part.get("size_mm", [w, h, d])[0] if isinstance(part.get("size_mm"), list) else w,
            "mm_h": part.get("size_mm", [w, h, d])[1] if isinstance(part.get("size_mm"), list) else h,
            "mm_d": part.get("size_mm", [w, h, d])[2] if isinstance(part.get("size_mm"), list) else d,
        }

    def _add_parts_to_scene(self, part_ids=None):
        ids = normalize_part_ids(part_ids if part_ids is not None else self._selected_parts)
        project = self._current_project()
        geometry = list(project.get("geometry") or [])
        existing = {str(item.get("part_id")) for item in geometry if isinstance(item, dict)}
        added = 0
        for part_id in ids:
            if part_id in existing or part_id not in PARTS_BY_ID:
                continue
            geometry.append(self._part_geometry(PARTS_BY_ID[part_id], len(geometry)))
            existing.add(part_id)
            added += 1
        project["geometry"] = geometry
        self._canvas.set_project(project)
        if added:
            self._canvas.set_selected_index(len(geometry) - 1)
        self._status.setText(f"ASSEMBLY UPDATED / {added} PART(S) PLACED  ·  DRAG PARTS IN VIEWPORT")

    def _open_assembly_editor(self):
        """Blender-like placement panel: select, drag, snap and edit XYZ."""
        dlg = QDialog(self)
        dlg.setWindowTitle("HOLO LAB — ASSEMBLY EDITOR / PLACE PARTS")
        dlg.setMinimumSize(720, 520)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {C.DARK}; color: {C.TEXT}; }}
            QListWidget, QLineEdit {{ background: #00080f; color: {C.TEXT}; border: 1px solid {C.BORDER}; padding: 5px; }}
            QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 5px 9px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        root = QVBoxLayout(dlg)
        root.addWidget(QLabel("Drag parts directly in the PC viewport. Use XYZ and rotation for precise placement; SNAP rounds to the assembly grid."))
        body = QHBoxLayout()
        scene_list = QListWidget()
        body.addWidget(scene_list, stretch=1)
        controls = QVBoxLayout()
        fields = {}
        for key in ("x", "y", "z", "rotation", "scale"):
            row = QHBoxLayout()
            label = QLabel(key.upper())
            label.setFixedWidth(64)
            field = QLineEdit()
            field.setPlaceholderText(key)
            row.addWidget(label); row.addWidget(field)
            controls.addLayout(row)
            fields[key] = field
        apply_btn = QPushButton("APPLY POSITION")
        snap_btn = QPushButton("SNAP TO GRID")
        delete_btn = QPushButton("REMOVE FROM SCENE")
        add_btn = QPushButton("ADD SELECTED BOM PARTS")
        controls.addWidget(apply_btn); controls.addWidget(snap_btn); controls.addWidget(delete_btn); controls.addWidget(add_btn)
        controls.addStretch()
        body.addLayout(controls)
        root.addLayout(body, stretch=1)
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(dlg.reject)
        root.addWidget(close_box)

        def geometry():
            return self._canvas.project().get("geometry") or []

        def refresh():
            current = self._canvas.selected_index()
            scene_list.blockSignals(True)
            scene_list.clear()
            for index, item in enumerate(geometry()):
                label = str(item.get("label") or item.get("part_id") or f"PRIMITIVE {index + 1}")
                scene_list.addItem(f"{index + 1:02d}  {label}")
            scene_list.setCurrentRow(current if 0 <= current < scene_list.count() else (0 if scene_list.count() else -1))
            scene_list.blockSignals(False)
            sync(scene_list.currentRow())

        def sync(index: int):
            self._canvas.set_selected_index(index)
            item = geometry()[index] if 0 <= index < len(geometry()) else {}
            for key, field in fields.items():
                if not item:
                    field.setText("")
                    continue
                try:
                    value = round(float(item.get(key, 0)), 2)
                except (TypeError, ValueError):
                    value = 0
                field.setText(str(value))

        def apply_position():
            index = scene_list.currentRow()
            items = geometry()
            if not (0 <= index < len(items)):
                return
            item = items[index]
            for key, field in fields.items():
                try:
                    item[key] = round(float(field.text().strip()), 2)
                except ValueError:
                    pass
            self._canvas.set_selected_index(index)
            self._canvas.scene_changed.emit(self._canvas.project())
            self._canvas.update()
            self._status.setText("POSITION APPLIED / RUN DIAGNOSTICS TO CHECK CLEARANCE")

        def snap_to_grid():
            index = scene_list.currentRow()
            items = geometry()
            if not (0 <= index < len(items)):
                return
            for key in ("x", "y", "z"):
                try:
                    items[index][key] = round(float(items[index].get(key, 0)) / 10) * 10
                except (TypeError, ValueError):
                    pass
            refresh()
            self._canvas.set_selected_index(index)
            self._canvas.scene_changed.emit(self._canvas.project())
            self._canvas.update()

        def remove_part():
            index = scene_list.currentRow()
            items = geometry()
            if 0 <= index < len(items):
                items.pop(index)
                self._canvas.set_project({**self._current_project(), "geometry": items})
                self._canvas.set_selected_index(min(index, len(items) - 1))
                refresh()
                self._status.setText("PART REMOVED / SAVE PROJECT WHEN READY")

        scene_list.currentRowChanged.connect(sync)
        apply_btn.clicked.connect(apply_position)
        snap_btn.clicked.connect(snap_to_grid)
        delete_btn.clicked.connect(remove_part)
        add_btn.clicked.connect(lambda: (self._add_parts_to_scene(), refresh()))
        refresh()
        dlg.exec()

    def _current_project(self) -> dict:
        project = self._canvas.project()
        project["parts"] = list(self._selected_parts)
        return project

    def _open_parts_catalog(self):
        """Let the user assemble a buy/make/test BOM from the offline catalog."""
        dlg = QDialog(self)
        dlg.setWindowTitle("HOLO LAB — PARTS CATALOG / BUILD")
        dlg.setMinimumSize(720, 560)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {C.DARK}; color: {C.TEXT}; }}
            QLineEdit {{ background: #000d14; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 6px; }}
            QListWidget {{ background: #00080f; color: {C.TEXT}; border: 1px solid {C.BORDER}; }}
            QListWidget::item {{ padding: 5px; border-bottom: 1px solid {C.BORDER}; }}
            QListWidget::item:selected {{ background: {C.PRI_GHO}; }}
            QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 5px 10px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        lay = QVBoxLayout(dlg)
        title = QLabel("PARTS YOU CAN BUY OR MAKE  ·  select a starter BOM, then test one subsystem at a time")
        title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI};")
        lay.addWidget(title)
        search = QLineEdit()
        search.setPlaceholderText("Search camera, display, battery, sensor, 3-D print, test…")
        lay.addWidget(search)
        list_w = QListWidget()
        lay.addWidget(list_w, stretch=1)
        count_lbl = QLabel()
        count_lbl.setFont(QFont("Courier New", 8))
        count_lbl.setStyleSheet(f"color: {C.GREEN};")
        lay.addWidget(count_lbl)

        for part in PART_CATALOG:
            item = QListWidgetItem(
                f"{part['name']}   [{part['category']}]   {part['source']}   ~${part['price']}"
            )
            item.setData(Qt.ItemDataRole.UserRole, part["id"])
            item.setToolTip(f"{part['spec']}\nSupplier: {part['supplier']}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if part["id"] in self._selected_parts else Qt.CheckState.Unchecked
            )
            list_w.addItem(item)

        def refresh_count():
            selected = sum(
                list_w.item(i).checkState() == Qt.CheckState.Checked
                for i in range(list_w.count()) if not list_w.item(i).isHidden()
            )
            count_lbl.setText(f"VISIBLE SELECTED: {selected}  ·  catalog: {len(PART_CATALOG)} parts  ·  BUY and MAKE entries are marked")

        def filter_items(text: str):
            query = text.strip().lower()
            for i in range(list_w.count()):
                item = list_w.item(i)
                part = PARTS_BY_ID.get(item.data(Qt.ItemDataRole.UserRole), {})
                haystack = " ".join(str(part.get(k, "")) for k in ("id", "name", "category", "source", "spec", "supplier")).lower()
                item.setHidden(bool(query) and query not in haystack)
            refresh_count()

        search.textChanged.connect(filter_items)
        list_w.itemChanged.connect(lambda _item: refresh_count())
        refresh_count()
        add_to_scene = [False]
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        scene_button = QPushButton("ADD SELECTED TO SCENE")
        scene_button.clicked.connect(lambda: (add_to_scene.__setitem__(0, True), dlg.accept()))
        button_row = QHBoxLayout()
        button_row.addWidget(scene_button)
        button_row.addWidget(buttons)
        lay.addLayout(button_row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._selected_parts = normalize_part_ids(
            list_w.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(list_w.count())
            if list_w.item(i).checkState() == Qt.CheckState.Checked
        )
        self.set_project({**self._current_project(), "parts": self._selected_parts})
        if add_to_scene[0]:
            self._add_parts_to_scene(self._selected_parts)
        self._status.setText(f"BOM UPDATED / {len(self._selected_parts)} PARTS SELECTED")

    def _run_diagnostics(self, symptom: str = ""):
        """Explain likely faults and safe next tests instead of blindly retrying hardware."""
        dlg = QDialog(self)
        dlg.setWindowTitle("HOLO LAB — DIAGNOSTICS / HELP")
        dlg.setMinimumSize(700, 500)
        dlg.setStyleSheet(f"QDialog {{ background: {C.DARK}; }} QLineEdit, QTextEdit {{ background: #00080f; color: {C.TEXT}; border: 1px solid {C.BORDER}; padding: 6px; }} QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; padding: 5px 10px; }}")
        lay = QVBoxLayout(dlg)
        head = QLabel("DIAGNOSTICS  ·  describe the symptom; the assistant gives a problem, fix and bench test")
        head.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        head.setStyleSheet(f"color: {C.ACC2};")
        lay.addWidget(head)
        symptom_edit = QLineEdit(symptom)
        symptom_edit.setPlaceholderText("e.g. black screen, camera not working, resets, battery gets hot…")
        lay.addWidget(symptom_edit)
        report = QTextEdit()
        report.setReadOnly(True)
        report.setFont(QFont("Courier New", 8))
        lay.addWidget(report, stretch=1)
        run_btn = QPushButton("⚠  CHECK PROJECT")
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(dlg.reject)
        row = QHBoxLayout(); row.addWidget(run_btn); row.addWidget(close_box)
        lay.addLayout(row)

        def run_check():
            issues = diagnose_project(self._current_project(), self._selected_parts, symptom_edit.text())
            report.setPlainText(format_diagnostics(issues))
            errors = sum(x.get("severity") == "ERROR" for x in issues)
            self._status.setText(f"DIAGNOSTICS COMPLETE / {errors} ERROR(S) / TEST BEFORE POWER")

        run_btn.clicked.connect(run_check)
        run_check()
        dlg.exec()

    def _print_blueprint(self):
        """Send a printable BOM, blueprint brief and diagnostics to a real printer."""
        project = self._current_project()
        issues = diagnose_project(project, self._selected_parts)
        subject = html.escape(str(project.get("subject") or project.get("name") or "custom hologram"))
        name = html.escape(str(project.get("name") or "Holo project"))
        components = "".join(f"<li>{html.escape(str(x))}</li>" for x in project.get("components", []))
        parts_html = "".join(
            f"<tr><td>{html.escape(part['name'])}</td><td>{html.escape(part['category'])}</td>"
            f"<td>{html.escape(part['source'])}</td><td>${part['price']}</td>"
            f"<td>{html.escape(part['supplier'])}</td></tr>"
            for part in parts_for_ids(self._selected_parts)
        )
        issues_html = "".join(
            f"<li><b>{html.escape(str(issue.get('severity')))}</b> {html.escape(str(issue.get('problem')))}<br>"
            f"<b>FIX:</b> {html.escape(str(issue.get('fix')))}<br>"
            f"<b>TEST:</b> {html.escape(str(issue.get('test')))}</li>"
            for issue in issues
        )
        def _geom_number(item, key):
            try:
                return f"{float(item.get(key, 0)):.1f}"
            except (TypeError, ValueError):
                return "0.0"
        geometry_html = "".join(
            f"<tr><td>{html.escape(str(item.get('label') or item.get('part_id') or i + 1))}</td>"
            f"<td>{html.escape(str(item.get('type', 'box')))}</td><td>{_geom_number(item, 'x')}</td>"
            f"<td>{_geom_number(item, 'y')}</td><td>{_geom_number(item, 'z')}</td>"
            f"<td>{_geom_number(item, 'w')} × {_geom_number(item, 'h')} × {_geom_number(item, 'd')}</td></tr>"
            for i, item in enumerate(project.get("geometry", [])) if isinstance(item, dict)
        )
        document_html = f"""
        <html><head><style>
        body {{ font-family: sans-serif; color: #111; }} h1 {{ color: #064e63; }}
        h2 {{ color: #0e7490; border-bottom: 1px solid #9ca3af; }}
        table {{ border-collapse: collapse; width: 100%; }} th,td {{ border: 1px solid #9ca3af; padding: 5px; font-size: 9pt; }}
        th {{ background: #cffafe; }} li {{ margin: 6px 0; }} .small {{ color: #4b5563; }}
        </style></head><body>
        <h1>HOLO LAB — BLUEPRINT + BUILD REPORT</h1>
        <p><b>Project:</b> {name}<br><b>Subject:</b> {subject}<br>
        <b>Mode:</b> {html.escape(str(project.get('mode', 'holo')))}<br>
        <b>Project ID:</b> {html.escape(str(project.get('id', 'PC-HOLO')))}</p>
        <h2>AI COMPONENT SCHEDULE</h2><ul>{components or '<li>No component schedule supplied</li>'}</ul>
        <h2>SCENE PLACEMENT / ASSEMBLY COORDINATES</h2>
        <table><tr><th>Object</th><th>Primitive</th><th>X</th><th>Y</th><th>Z</th><th>Size / footprint</th></tr>
        {geometry_html or '<tr><td colspan="6">No placed scene geometry yet — use ASSEMBLY EDITOR.</td></tr>'}</table>
        <h2>PARTS / BUY OR MAKE BOM</h2>
        <table><tr><th>Part</th><th>Category</th><th>Source</th><th>Est.</th><th>Supplier / route</th></tr>
        {parts_html or '<tr><td colspan="5">No catalog parts selected</td></tr>'}</table>
        <h2>DIAGNOSTICS AND NEXT TESTS</h2><ul>{issues_html}</ul>
        <p class="small">Software concept only. Verify voltage, current, heat, fit, optics, battery protection and local regulations before building. Never look into an untested bright source or laser.</p>
        </body></html>
        """
        try:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            dialog = QPrintDialog(printer, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._status.setText("PRINT CANCELLED")
                return
            document = QTextDocument()
            document.setHtml(document_html)
            if hasattr(document, "print_"):
                document.print_(printer)
            else:
                document.print(printer)
            self._status.setText("BLUEPRINT SENT TO PRINTER")
        except Exception as exc:
            self._status.setText("PRINTER ERROR / SAVE REPORT OR CHECK DRIVER")
            self._run_diagnostics(f"printer error: {exc}")

    def _save_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Holo Lab project", "holo_project.json", "Holo project (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._current_project(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._status.setText("PROJECT SAVED / READY FOR REAL-WORLD BUILD REVIEW")
        except Exception as exc:
            self._status.setText(f"SAVE ERROR / {str(exc)[:60]}")

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Holo Lab project", "", "Holo project (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            project = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(project, dict):
                raise ValueError("project must be an object")
            self.set_project(project)
            self._status.setText("PROJECT LOADED / RUN DIAGNOSTICS BEFORE POWER")
        except Exception as exc:
            self._status.setText(f"LOAD ERROR / {str(exc)[:60]}")

    def print_blueprint(self):
        self._print_blueprint()

    def show_diagnostics(self, symptom: str = ""):
        self._run_diagnostics(symptom)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._close()
        else:
            super().keyPressEvent(event)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Dairesel renk seçici. Kullanıcı tutamacı (küçük beyaz daire) çarkın
    çevresinde sürükleyerek TÜM renk tonları arasından seçim yapar.
    Merkezdeki dolu daire seçilen rengin canlı önizlemesidir.
    """

    hue_picked    = pyqtSignal(str)   # sürükleme sırasında (canlı)
    hue_committed = pyqtSignal(str)   # tutamaç bırakıldığında

    _RING = 16   # halka kalınlığı (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometri yardımcıları ────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # ekran y'si aşağı — matematiksel eksene çevir
        ang = math.atan2(dy, dx)      # [-π, π], saat yönünün tersi
        return (ang / (2 * math.pi)) % 1.0

    # ── çizim ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # merkez önizleme dairesi
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # sürüklenen tutamaç
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name and UI colour."""

    saved = pyqtSignal(str, str, str)   # assistant_name, user_name, ui_color
    _OW, _OH = 400, 500

    def __init__(self, assistant_name="EDIT", user_name="",
                 ui_color=DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── UI colour — renk çarkı ───────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — canlı önizleme; MainWindow bağlar

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── renk akışı ───────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Seçili rengi günceller; hex kutusu + çark senkron kalır, tema canlı önizlenir."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # Sürükleme sırasında: hex kutusunu güncelle, temayı henüz uygulama
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Tutamaç bırakıldı → tüm arayüzü canlı önizle
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # Önizleme uygulandıysa açılıştaki renge geri dön
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "EDIT"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR)
        self.hide()


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback
    + management panel for every remote device currently connected."""

    closed = pyqtSignal()

    _OW, _OH = 400, 640

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        # ── Connected devices (hub) ───────────────────────────────────────────
        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep3)

        self._dev_provider = None
        self._dev_kicker   = None
        self._dev_refresh_ctr = 0

        self._dev_title = _lbl("◈  CONNECTED DEVICES — 0", 8, True,
                               align=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._dev_title)

        self._dev_holder = QWidget()
        self._dev_holder.setStyleSheet("background: transparent;")
        self._dev_box = QVBoxLayout(self._dev_holder)
        self._dev_box.setContentsMargins(0, 0, 0, 0)
        self._dev_box.setSpacing(3)
        lay.addWidget(self._dev_holder)

        revoke_btn = QPushButton("REVOKE PAIRED DEVICES")
        revoke_btn.setFixedHeight(26)
        revoke_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        revoke_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        revoke_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: #f87171; border: 1px solid #7f2d2d; }}
        """)
        revoke_btn.clicked.connect(self._revoke_paired)
        lay.addWidget(revoke_btn)
        self._revoke_btn = revoke_btn

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    # ── Device hub ────────────────────────────────────────────────────────────

    def set_devices_callbacks(self, provider, kicker) -> None:
        """provider: () -> [{id,name,ip,secs}], kicker: (dev_id | 'revoke') -> None"""
        self._dev_provider = provider
        self._dev_kicker   = kicker
        self.refresh_devices()

    def refresh_devices(self) -> None:
        # drop old rows
        while self._dev_box.count():
            item = self._dev_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        devs = []
        if self._dev_provider:
            try:
                devs = self._dev_provider() or []
            except Exception:
                devs = []

        self._dev_title.setText(f"◈  CONNECTED DEVICES — {len(devs)}")
        if not devs:
            msg = "scan the QR code with a phone…" if self._dev_provider else "dashboard offline"
            lbl = QLabel(msg)
            lbl.setFont(QFont("Courier New", 8))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._dev_box.addWidget(lbl)
            return

        for d in devs[:6]:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            secs = int(d.get("secs", 0))
            mm, ss = divmod(secs, 60)
            txt = f"{d.get('name','Device')} · {d.get('ip','?')} · {mm:02d}:{ss:02d}"
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 8))
            lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            kick = QPushButton("KICK")
            kick.setFixedSize(46, 20)
            kick.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            kick.setCursor(Qt.CursorShape.PointingHandCursor)
            kick.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 4px;
                }}
                QPushButton:hover {{ color: #f87171; border: 1px solid #7f2d2d; }}
            """)
            kick.clicked.connect(lambda _c, did=str(d.get("id", "")): self._kick_device(did))
            hl.addWidget(lbl, stretch=1)
            hl.addWidget(kick)
            self._dev_box.addWidget(row)

    def _kick_device(self, dev_id: str) -> None:
        if self._dev_kicker and dev_id:
            self._dev_kicker(dev_id)
        QTimer.singleShot(300, self.refresh_devices)

    def _revoke_paired(self) -> None:
        if self._dev_kicker:
            self._dev_kicker("revoke")
        self._revoke_btn.setText("PAIRED DEVICES REVOKED")
        QTimer.singleShot(2000,
                          lambda: self._revoke_btn.setText("REVOKE PAIRED DEVICES"))

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()
            return
        # refresh the devices list every 5 s while the overlay is open
        self._dev_refresh_ctr += 1
        if self._dev_refresh_ctr % 5 == 0:
            self.refresh_devices()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — EDIT ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _holo_sig       = pyqtSignal(object)     # AI-generated blueprint → PC Holo Lab
    _holo_diag_sig  = pyqtSignal(str)        # diagnostics request from AI
    _holo_print_sig = pyqtSignal()           # printer request from AI
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _scan_sig       = pyqtSignal(bytes, object)  # phone scan frame + detections → HUD area
    _pcam_sig       = pyqtSignal(bool)           # phone live stream: True=start, False=stop
    _pcam_frame_sig = pyqtSignal(bytes)          # phone live frame → HUD area
    _pcam_dets_sig  = pyqtSignal(object)         # live detections for the current frame
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _headphones_sig = pyqtSignal(object)     # headphones-mode status dict (thread-safe)
    _internet_sig   = pyqtSignal(object)     # internet-tunnel status dict (thread-safe)

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "EDIT").strip()
        _display = self._assistant_name.upper()

        # Kayıtlı UI rengini panel/stylesheet'ler kurulmadan ÖNCE uygula
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — MARK XLIX")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_headphones_toggle = None  # callable: () -> None — toggle 🎧 mode
        self.on_internet_toggle   = None  # callable: () -> None — toggle 🌐 tunnel
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._holo_overlay: HoloLabOverlay | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header/footer/side panels are still constructed (their widgets are
        # used by the rest of the app) but they now live INSIDE the settings
        # panel instead of cluttering the main view.
        self._header      = self._build_header()
        self._left_panel  = self._build_left_panel()
        self._right_panel = self._build_right_panel()
        self._header.hide()

        # Center: HUD (the orb) + optional content panel
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self._close_feed_view)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_title    = _cam_title
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD (orb), 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        root.addWidget(self._center_split, stretch=1)

        # Floating settings (gear) button — the ONLY chrome on screen
        self._gear_btn = _GearButton(central)
        self._gear_btn.move(16, 16)
        self._gear_btn.clicked.connect(self._toggle_drawer)
        self._gear_btn.show()
        self._gear_btn.raise_()

        # Sliding settings panel (Remote control, mic, monitor, log, input…)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._holo_sig.connect(self._show_holo_project)
        self._holo_diag_sig.connect(self._show_holo_diagnostics)
        self._holo_print_sig.connect(self._print_holo_project)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._scan_sig.connect(self._on_phone_scan)
        self._pcam_sig.connect(self._on_pcam_stream)
        self._pcam_frame_sig.connect(self._on_pcam_frame)
        self._pcam_dets_sig.connect(self._on_pcam_dets)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._headphones_sig.connect(self._on_headphones_status)
        self._internet_sig.connect(self._on_internet_status)
        self._cam_stop = threading.Event()
        self._feed_mode = "none"             # none | camera | scan | phonecam — who owns the HUD area
        self._pcam_px   = None               # latest phone live frame (QPixmap)
        self._pcam_dets: list = []           # latest live detections for that frame
        self._scan_px   = None               # frozen EDITH scan frame (QPixmap)
        self._scan_dets: list = []           # detections for that frozen frame
        self.devices_provider = None         # set by main.py: () -> list[dict]
        self.devices_kicker   = None         # set by main.py: (dev_id | "revoke") -> None

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - pw - 16,
            cw.height() - ph - 16,
            pw, ph,
        )

    # --- Live camera stream / phone scan in HUD area -----------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._feed_mode = "camera"
            self._cam_title.setText("◈  CAMERA FEED")
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            # camera loop ended — only reclaim the area if no other feed owns it
            if self._feed_mode in ("camera", "none"):
                self._feed_mode = "none"
                self._cam_title.setText("◈  CAMERA FEED")
                self._hud_cam_stack.setCurrentIndex(0)
                self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        if self._feed_mode != "camera":
            return  # a phone scan snapshot owns the area right now
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()
        if self._feed_mode == "camera":
            self._feed_mode = "none"
        # the camera thread's finally-block emits _cam_stream_sig(False) → HUD restored

    def _close_feed_view(self) -> None:
        """✕ button in the feed header — closes webcam stream / scan / phone live."""
        self._cam_stop.set()
        self._feed_mode = "none"
        self._pcam_px = None
        self._pcam_dets = []
        self._cam_stream_sig.emit(False)

    # --- Shared EDITH box painter -------------------------------------------
    _DET_COLORS = None   # lazily built QColor map

    # Bone chain for the EDITH skeleton overlay
    _BONES = (
        ("head", "neck"),
        ("neck", "l_shoulder"), ("neck", "r_shoulder"),
        ("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"),
        ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist"),
        ("neck", "pelvis"),
        ("l_shoulder", "l_hip"), ("r_shoulder", "r_hip"),
        ("pelvis", "l_hip"), ("pelvis", "r_hip"), ("l_hip", "r_hip"),
        ("l_hip", "l_knee"), ("l_knee", "l_ankle"),
        ("r_hip", "r_knee"), ("r_knee", "r_ankle"),
    )
    _SKEL_COL   = QColor("#ff2d3d")     # bones — red, like the reference
    _AURA_COL   = QColor("#ffa500")     # silhouette aura — orange

    def _person_phase(self) -> float:
        """0..1 animation phase shared by every person overlay (pulsing aura)."""
        return (time.time() * 0.9) % 1.0

    def _draw_person_fx(self, p: QPainter, d: dict, sw: int, sh: int,
                        x: int, y: int, bw: int, bh: int) -> None:
        """EDITH-style person effect: glowing silhouette aura + red bone rig."""
        ph    = self._person_phase()
        pulse = 0.5 + 0.5 * math.sin(ph * 2 * math.pi)

        def _pt(q):
            y, x = float(q[0]), float(q[1])
            if y != y or x != x:                     # NaN
                raise ValueError("nan point")
            return QPointF(x / 1000.0 * sw, y / 1000.0 * sh)

        # ── 1) silhouette outline (falls back to the bounding box) ─────────
        # Rebuilding the QPainterPath every repaint is pure overhead: at 60 Hz
        # the FX timer redraws the same geometry many times between frames.
        # Cache it against the raw coordinates + canvas size.
        outline = d.get("outline") or []
        cache_key = (id(d), sw, sh, x, y, bw, bh, len(outline))
        cached = getattr(self, "_fx_path_cache", None)
        if cached is not None and cached[0] == cache_key:
            path = cached[1]
        else:
            path = QPainterPath()
            if isinstance(outline, (list, tuple)) and len(outline) >= 4:
                pts = []
                for q in outline[:40]:
                    if isinstance(q, (list, tuple)) and len(q) >= 2:
                        try:
                            pts.append(_pt(q))
                        except (TypeError, ValueError):
                            pass
                if len(pts) >= 4:
                    path.moveTo(pts[0])
                    for q in pts[1:]:
                        path.lineTo(q)
                    path.closeSubpath()
            if path.isEmpty():
                path.addRoundedRect(QRectF(x, y, bw, bh), 10, 10)
            self._fx_path_cache = (cache_key, path)

        # Glowing aura. Antialiasing wide strokes is by far the most expensive
        # operation in the whole HUD (~9 ms vs ~1 ms), and it is invisible on
        # the soft glow layers — so only the final crisp outline gets it.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for w, a in ((10, 34), (5, 70)):        # 2 glow layers read the same
            col = QColor(self._AURA_COL)
            col.setAlpha(int(a * (0.65 + 0.35 * pulse)))
            pen = QPen(col, w)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPath(path)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(self._AURA_COL)
        col.setAlpha(int(210 * (0.65 + 0.35 * pulse)))
        pen = QPen(col, 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(path)

        # soft inner fill so the target reads as "locked"
        fill = QColor(self._AURA_COL)
        fill.setAlpha(int(26 + 16 * pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(fill))
        p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 2) skeleton / bone rig ─────────────────────────────────────────
        pose = d.get("pose")
        joints: dict[str, QPointF] = {}
        if isinstance(pose, dict):
            for k, q in pose.items():
                if isinstance(q, (list, tuple)) and len(q) >= 2:
                    try:
                        joints[str(k)] = _pt(q)
                    except (TypeError, ValueError):
                        pass
        if len(joints) < 3:
            # A detector that only knows the bounding box (e.g. HOG) carries no
            # joint data. Drawing a generic rig there paints a skeleton that
            # does not match the real body, so we show only the outline.
            if d.get("no_skeleton"):
                joints = {}
            else:
                joints = self._fallback_skeleton(x, y, bw, bh)

        # bone glow pass (no AA — it is a blur anyway), then crisp bone pass
        bones = [(joints[a], joints[b]) for a, b in self._BONES
                 if a in joints and b in joints] if joints else []
        for w, a, aa in ((5, 70, False), (2, 255, True)):
            if not bones:
                break
            p.setRenderHint(QPainter.RenderHint.Antialiasing, aa)
            col = QColor(self._SKEL_COL)
            col.setAlpha(int(a * (0.7 + 0.3 * pulse)) if a < 255 else 255)
            pen = QPen(col, w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for q1, q2 in bones:
                p.drawLine(q1, q2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # joint nodes
        p.setPen(Qt.PenStyle.NoPen)
        for name, q in joints.items():
            r = 4.0 if name in ("head", "pelvis", "neck") else 2.6
            halo = QColor(self._SKEL_COL); halo.setAlpha(90)
            p.setBrush(QBrush(halo))
            p.drawEllipse(q, r * 2.1, r * 2.1)
            p.setBrush(QBrush(QColor("#fff0f0")))
            p.drawEllipse(q, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 3) animated corner brackets around the target ──────────────────
        col = QColor(self._AURA_COL)
        col.setAlpha(int(150 + 105 * pulse))
        p.setPen(QPen(col, 2))
        cl = max(8, int(min(bw, bh) * 0.22))
        pad = int(4 + 3 * pulse)
        x0, y0 = x - pad, y - pad
        x1, y1 = x + bw + pad, y + bh + pad
        for bx, by, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                               (x0, y1, 1, -1), (x1, y1, -1, -1)):
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * cl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * cl))

        # ── 4) scan line sweeping down the target ──────────────────────────
        sy = y + bh * ph
        gl = QLinearGradient(x, sy, x + bw, sy)
        c0 = QColor(self._AURA_COL); c0.setAlpha(0)
        c1 = QColor("#fff2cc");      c1.setAlpha(190)
        gl.setColorAt(0.0, c0); gl.setColorAt(0.5, c1); gl.setColorAt(1.0, c0)
        p.setPen(QPen(QBrush(gl), 2))
        p.drawLine(QPointF(x, sy), QPointF(x + bw, sy))

    _FACE_KNOWN   = QColor("#00ffa8")     # recognised → green lock
    _FACE_UNKNOWN = QColor("#00d4ff")     # detected but not enrolled → cyan

    def _draw_face_mesh(self, p: QPainter, d: dict, sw: int, sh: int,
                        col: QColor, pulse: float) -> bool:
        """Wireframe face mask: node cloud + tesselation, like a biometric scan.

        Returns True when a mesh was actually drawn.
        """
        mesh = d.get("mesh")
        if not isinstance(mesh, (list, tuple)) or len(mesh) < 100:
            return False

        # normalised [y, x] → screen points (cached: the point cloud only
        # changes when a new frame arrives, but we repaint at 60 Hz)
        key = (id(d), sw, sh, len(mesh))
        cached = getattr(self, "_mesh_cache", None)
        if cached is not None and cached[0] == key:
            pts = cached[1]
        else:
            pts = []
            for q in mesh:
                try:
                    yy, xx = float(q[0]), float(q[1])
                except (TypeError, ValueError, IndexError):
                    return False
                if yy != yy or xx != xx:
                    return False
                pts.append(QPointF(xx / 1000.0 * sw, yy / 1000.0 * sh))
            self._mesh_cache = (key, pts)

        edges = d.get("mesh_edges") or []
        n = len(pts)

        # ── 1) wire tesselation ────────────────────────────────────────────
        if edges:
            wire = QColor(col)
            wire.setAlpha(int(70 + 40 * pulse))
            p.setPen(QPen(wire, 0.7))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for e in edges:
                try:
                    a, b = int(e[0]), int(e[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if 0 <= a < n and 0 <= b < n:
                    p.drawLine(pts[a], pts[b])
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # ── 2) node cloud — the "measured points" of the face print ────────
        node = QColor("#ffffff")
        node.setAlpha(int(170 + 85 * pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(node))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for q in pts:
            p.drawEllipse(q, 1.1, 1.1)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 3) scan line sweeping across the mesh (biometric read-out) ─────
        ys = [q.y() for q in pts]
        xs = [q.x() for q in pts]
        top, bot = min(ys), max(ys)
        lft, rgt = min(xs), max(xs)
        sy = top + (bot - top) * (self._person_phase() % 1.0)
        grad = QLinearGradient(lft, sy, rgt, sy)
        c0 = QColor(col); c0.setAlpha(0)
        c1 = QColor("#ffffff"); c1.setAlpha(200)
        grad.setColorAt(0.0, c0); grad.setColorAt(0.5, c1); grad.setColorAt(1.0, c0)
        p.setPen(QPen(QBrush(grad), 1.6))
        p.drawLine(QPointF(lft, sy), QPointF(rgt, sy))
        return True

    def _draw_face_fx(self, p: QPainter, d: dict,
                      x: int, y: int, bw: int, bh: int,
                      sw: int = 0, sh: int = 0) -> None:
        """Targeting reticle + biometric wireframe mask over a detected face."""
        known = bool(d.get("known"))
        col   = self._FACE_KNOWN if known else self._FACE_UNKNOWN
        pulse = 0.5 + 0.5 * math.sin(self._person_phase() * 2 * math.pi)

        if sw and sh:
            self._draw_face_mesh(p, d, sw, sh, col, pulse)

        p.setBrush(Qt.BrushStyle.NoBrush)
        # soft halo
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        halo = QColor(col); halo.setAlpha(int(40 + 30 * pulse))
        p.setPen(QPen(halo, 6))
        p.drawRect(x, y, bw, bh)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # crisp frame
        main = QColor(col); main.setAlpha(230)
        p.setPen(QPen(main, 1.6))
        p.drawRect(x, y, bw, bh)

        # corner ticks
        cl = max(6, int(min(bw, bh) * 0.26))
        p.setPen(QPen(main, 2.4))
        for bx, by, dx, dy in ((x, y, 1, 1), (x + bw, y, -1, 1),
                               (x, y + bh, 1, -1), (x + bw, y + bh, -1, -1)):
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * cl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * cl))

        # cross-hair on the face centre
        cx, cy = x + bw / 2, y + bh / 2
        tick = max(4, int(min(bw, bh) * 0.10))
        faint = QColor(col); faint.setAlpha(150)
        p.setPen(QPen(faint, 1))
        p.drawLine(QPointF(cx - tick, cy), QPointF(cx + tick, cy))
        p.drawLine(QPointF(cx, cy - tick), QPointF(cx, cy + tick))

        # a recognised identity gets a pulsing lock ring
        if known:
            r = max(bw, bh) * (0.50 + 0.03 * pulse)
            ring = QColor(col); ring.setAlpha(int(90 + 80 * pulse))
            p.setPen(QPen(ring, 1.6))
            p.drawEllipse(QPointF(cx, cy), r, r)

    @staticmethod
    def _fallback_skeleton(x: int, y: int, bw: int, bh: int) -> dict:
        """Anatomically-plausible rig derived from the bounding box alone,
        used when the model didn't return pose keypoints."""
        def P(fx, fy):
            return QPointF(x + bw * fx, y + bh * fy)
        return {
            "head":       P(0.50, 0.07),
            "neck":       P(0.50, 0.17),
            "l_shoulder": P(0.31, 0.21), "r_shoulder": P(0.69, 0.21),
            "l_elbow":    P(0.22, 0.39), "r_elbow":    P(0.78, 0.39),
            "l_wrist":    P(0.19, 0.56), "r_wrist":    P(0.81, 0.56),
            "pelvis":     P(0.50, 0.53),
            "l_hip":      P(0.38, 0.55), "r_hip":      P(0.62, 0.55),
            "l_knee":     P(0.37, 0.75), "r_knee":     P(0.63, 0.75),
            "l_ankle":    P(0.36, 0.96), "r_ankle":    P(0.64, 0.96),
        }

    def _draw_dets_on(self, px2: QPixmap, detections) -> None:
        """Paint labeled detection boxes (0-1000 normalized) onto a pixmap.
        People additionally get the EDITH silhouette-aura + skeleton effect."""
        if self._DET_COLORS is None:
            type(self)._DET_COLORS = {
                "person":  QColor("#f97316"),
                "vehicle": QColor("#facc15"),
                "object":  QColor("#00d4ff"),
                "face":    QColor("#00ffa8"),
            }
        sw, sh = px2.width(), px2.height()
        if sw <= 0 or sh <= 0:
            return
        p = QPainter(px2)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            for d in (detections or [])[:12]:
                if not isinstance(d, dict):
                    continue
                box = d.get("box") or []
                if not isinstance(box, (list, tuple)) or len(box) != 4:
                    continue
                try:
                    ymin, xmin, ymax, xmax = (
                        max(0.0, min(1000.0, float(v))) for v in box
                    )
                except (TypeError, ValueError):
                    continue
                if not all(v == v for v in (ymin, xmin, ymax, xmax)):
                    continue                      # NaN guard
                if xmax - xmin < 5 or ymax - ymin < 5:
                    continue
                kind = d.get("kind")
                col  = self._DET_COLORS.get(kind, self._DET_COLORS["object"])
                x  = int(xmin / 1000 * sw)
                y  = int(ymin / 1000 * sh)
                bw = int((xmax - xmin) / 1000 * sw)
                bh = int((ymax - ymin) / 1000 * sh)

                if kind == "face":
                    try:
                        self._draw_face_fx(p, d, x, y, bw, bh, sw, sh)
                    except Exception:
                        p.setPen(QPen(col, 2))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(x, y, bw, bh)
                elif kind == "person":
                    # full EDITH treatment: aura outline + bones + brackets
                    try:
                        self._draw_person_fx(p, d, sw, sh, x, y, bw, bh)
                    except Exception:
                        p.setPen(QPen(col, 2))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(x, y, bw, bh)
                else:
                    pen = QPen(col)
                    pen.setWidth(2)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(x, y, bw, bh)

                label = str(d.get("label") or "TARGET").upper()[:42]
                fm    = p.fontMetrics()
                tw    = min(fm.horizontalAdvance(label) + 10, sw)
                th    = fm.height() + 4
                ly    = y - th if y - th > 0 else y
                p.setPen(Qt.PenStyle.NoPen)
                p.fillRect(x, ly, tw, th, col)
                p.setPen(QPen(QColor("#04070c")))
                p.drawText(QRectF(x + 5, ly, tw - 8, th),
                           Qt.AlignmentFlag.AlignVCenter, label)
        except Exception as e:
            print(f"[HUD] overlay paint failed: {e}")
        finally:
            p.end()

    # --- Phone live camera stream on PC HUD ---------------------------------
    def _on_pcam_stream(self, start: bool) -> None:
        if start:
            self._feed_mode = "phonecam"
            self._cam_title.setText("◈  PHONE CAM — LIVE")
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            if self._feed_mode == "phonecam":
                self._feed_mode = "none"
                self._hud_cam_stack.setCurrentIndex(0)
            self._pcam_px = None
            self._pcam_dets = []
            if self._feed_mode == "none":
                self._cam_live_lbl.clear()

    def _on_pcam_frame(self, data: bytes) -> None:
        if self._feed_mode != "phonecam":
            return
        px = QPixmap()
        px.loadFromData(data)
        if px.isNull():
            return
        self._pcam_px = px
        self._repaint_pcam()

    def _on_pcam_dets(self, dets) -> None:
        if self._feed_mode != "phonecam":
            return
        # keep only well-formed entries — the HUD assumes dicts downstream
        self._pcam_dets = [d for d in (dets or []) if isinstance(d, dict)]
        try:
            self._repaint_pcam()
        except Exception as e:
            print(f"[HUD] pcam repaint failed: {e}")

    def _scaled_frame(self, src: QPixmap, key: str) -> QPixmap:
        """Scale `src` to the label, reusing the previous result when possible.

        The FX timer repaints at 60 Hz while new frames arrive at ~30 Hz, so
        without this cache every second repaint would pay for a full smooth
        rescale (~4 ms) that produces an identical bitmap.
        """
        w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
        if w <= 1 or h <= 1:
            return QPixmap(src)
        sig = (key, src.cacheKey(), w, h)
        if getattr(self, "_scale_sig", None) == sig:
            return QPixmap(self._scale_cache)      # copy-on-write: free
        out = src.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._scale_sig, self._scale_cache = sig, out
        return QPixmap(out)

    def _repaint_pcam(self) -> None:
        if self._pcam_px is None:
            return
        px2 = self._scaled_frame(self._pcam_px, "pcam")
        self._draw_dets_on(px2, self._pcam_dets)
        self._cam_live_lbl.setPixmap(px2)
        self._sync_fx_timer()

    # --- Phone scan overlay (EDITH snapshot with detection boxes) ----------
    def _on_phone_scan(self, img_bytes: bytes, detections) -> None:
        """Slot (main thread): draw the phone's scanned frame + labeled boxes."""
        px = QPixmap()
        px.loadFromData(img_bytes)
        if px.isNull():
            return
        self._feed_mode = "scan"
        self._cam_title.setText("◈  PHONE SCAN — EDITH")
        self._hud_cam_stack.setCurrentIndex(1)

        self._scan_px   = px
        self._scan_dets = [d for d in (detections or [])
                           if isinstance(d, dict)]
        self._repaint_scan()
        self._sync_fx_timer()
        QTimer.singleShot(20000, self._leave_scan)

    def _repaint_scan(self) -> None:
        if getattr(self, "_scan_px", None) is None:
            return
        px2 = self._scaled_frame(self._scan_px, "scan")
        self._draw_dets_on(px2, self._scan_dets)
        self._cam_live_lbl.setPixmap(px2)

    # --- EDITH person-effect animation --------------------------------------
    def _has_person(self) -> bool:
        dets = (self._scan_dets if self._feed_mode == "scan" else self._pcam_dets)
        return any(isinstance(d, dict) and d.get("kind") in ("person", "face")
                   for d in (dets or []))

    def _sync_fx_timer(self) -> None:
        """Run a ~20 fps repaint loop while a human target is highlighted so the
        aura pulses / scan line sweeps even on a frozen snapshot."""
        if not hasattr(self, "_fx_tmr"):
            self._fx_tmr = QTimer(self)
            self._fx_tmr.setInterval(16)        # ~60 FPS
            self._fx_tmr.timeout.connect(self._fx_tick)
        if self._feed_mode in ("scan", "phonecam") and self._has_person():
            if not self._fx_tmr.isActive():
                self._fx_tmr.start()
        elif self._fx_tmr.isActive():
            self._fx_tmr.stop()

    def _fx_tick(self) -> None:
        if self._feed_mode == "scan":
            self._repaint_scan()
        elif self._feed_mode == "phonecam":
            self._repaint_pcam()
        else:
            self._fx_tmr.stop()

    def _leave_scan(self) -> None:
        if self._feed_mode == "scan":
            self._feed_mode = "none"
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()
        self._scan_px   = None
        self._scan_dets = []
        self._sync_fx_timer()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_gear_btn"):
            self._gear_btn.move(16, 16)
            self._gear_btn.raise_()
        if hasattr(self, "_quick_drawer"):
            self._position_quick_drawer()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._holo_overlay and self._holo_overlay.isVisible():
            margin = 24
            ow = min(HoloLabOverlay._OW, max(620, cw.width() - margin * 2))
            oh = min(HoloLabOverlay._OH, max(430, cw.height() - margin * 2))
            self._holo_overlay.setGeometry(
                (cw.width() - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - pw - 16,
            cw.height() - ph - 16,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Settings drawer + floating gear button
        if hasattr(self, '_quick_drawer'):
            self._position_quick_drawer()
        if hasattr(self, '_gear_btn'):
            self._gear_btn.move(16, 16)
            self._gear_btn.raise_()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge("MARK XLIX", C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Courier New", 17, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(self._title_lbl)
        _sub_text = ("Just A Rather Very Intelligent System"
                     if _disp in ("JARVIS", "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))
        if hasattr(self, "_clock_lbl2"):
            self._clock_lbl2.setText(time.strftime("%H:%M:%S"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("PROTOCOL\nXLIX",   C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #140008; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: #200010; border: 1px solid #ff6688;
            }}
            QPushButton:pressed {{
                background: #300018;
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Sliding settings panel — the ONLY chrome besides the orb.

        Contains every control that used to be spread across the header,
        the side panels and the footer: remote control, mic, interrupt,
        system monitor, activity log, file upload and the command input.
        """
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 4px;
                text-align: left; padding: 0 10px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            QPushButton:pressed {{ background: {C.PRI_GHO}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: {C.PANEL2}; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px;
                text-align: left; padding: 0 10px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border-left: 1px solid {C.BORDER_B};
            }}
        """)
        w.setAutoFillBackground(True)
        w.hide()

        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── panel header ──────────────────────────────────────────────────
        top = QWidget()
        top.setFixedHeight(42)
        top.setStyleSheet(f"background: {C.PANEL}; border-bottom: 1px solid {C.BORDER};")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(12, 0, 8, 0)
        ttl = QLabel("\u25c8  SETTINGS & CONTROLS")
        ttl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        top_l.addWidget(ttl)
        top_l.addStretch()
        self._clock_lbl2 = QLabel("")
        self._clock_lbl2.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._clock_lbl2.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        top_l.addWidget(self._clock_lbl2)
        close_b = QPushButton("\u2715")
        close_b.setFixedSize(24, 24)
        close_b.setCursor(Qt.CursorShape.PointingHandCursor)
        close_b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                           border: none; font-size: 13px; }}
            QPushButton:hover {{ color: {C.MUTED_C}; }}
        """)
        close_b.clicked.connect(lambda: self._toggle_drawer(False))
        top_l.addSpacing(6)
        top_l.addWidget(close_b)
        outer.addWidget(top)

        # ── scrollable body ───────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {C.DARK}; border: none; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 7px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B};
                                           border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none; }}
        """)
        body = QWidget()
        body.setStyleSheet(f"background: {C.DARK};")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 10, 10, 12)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"\u25b8 {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                            f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 3px;")
            return l

        lay.addWidget(_sec("CONTROLS"))

        remote_btn = QPushButton("\u25c9  REMOTE CONTROL")
        remote_btn.setFixedHeight(32)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        # ── Internet tunnel toggle ───────────────────────────────────────────
        self._internet_btn = QPushButton("🌐  INTERNET ACCESS: OFF")
        self._internet_btn.setFixedHeight(28)
        self._internet_btn.setFont(QFont("Courier New", 7))
        self._internet_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._internet_btn.clicked.connect(self._toggle_internet)
        lay.addWidget(self._internet_btn)

        # ── Headphones mode toggle ───────────────────────────────────────────
        self._headphones_btn = QPushButton("🎧  HEADPHONES MODE: OFF")
        self._headphones_btn.setFixedHeight(28)
        self._headphones_btn.setFont(QFont("Courier New", 7))
        self._headphones_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._headphones_btn.clicked.connect(self._toggle_headphones)
        lay.addWidget(self._headphones_btn)

        holo_pc_btn = QPushButton("◈  HOLO LAB / PC MONITOR")
        holo_pc_btn.setFixedHeight(32)
        holo_pc_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        holo_pc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        holo_pc_btn.setStyleSheet(_BTN_STYLE_PRI)
        holo_pc_btn.clicked.connect(self._open_holo_pc)
        lay.addWidget(holo_pc_btn)

        fs_btn = QPushButton("\u26f6  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(28)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("\u229e  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(28)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("\u25c9  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(28)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("\u2699  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(28)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(28)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # ── Local Claude toggle ─────────────────────────────────────────────
        self._local_claude_btn = QPushButton()
        self._local_claude_btn.setFixedHeight(28)
        self._local_claude_btn.setFont(QFont("Courier New", 7))
        self._local_claude_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._local_claude_btn.clicked.connect(self._toggle_local_claude)
        lay.addWidget(self._local_claude_btn)
        self._update_local_claude_btn(_read_full_config().get("use_local_claude", False))

        # ── OSINT Mode toggle ───────────────────────────────────────────────
        self._osint_btn = QPushButton()
        self._osint_btn.setFixedHeight(28)
        self._osint_btn.setFont(QFont("Courier New", 7))
        self._osint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._osint_btn.clicked.connect(self._toggle_osint)
        lay.addWidget(self._osint_btn)
        self._update_osint_btn(_read_full_config().get("osint_mode", False))

        # ── Europe Satellite + AI Enhance toggle ────────────────────────────
        self._europe_ai_btn = QPushButton()
        self._europe_ai_btn.setFixedHeight(28)
        self._europe_ai_btn.setFont(QFont("Courier New", 7))
        self._europe_ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._europe_ai_btn.clicked.connect(self._toggle_europe_ai)
        lay.addWidget(self._europe_ai_btn)
        self._update_europe_ai_btn(_read_full_config().get("europe_satellite_ai", False))

        # ── Открыть GEOINT / OSINT Hub ───────────────────────────────────────
        hub_btn = QPushButton("🛰️  GEOINT / Карта военных баз")
        hub_btn.setToolTip("Интерактивная карта: активные и заброшенные базы, техника, РЛС, бункеры (Google Maps, OSM, Esri)")
        hub_btn.setFixedHeight(28)
        hub_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        hub_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hub_btn.setStyleSheet(f"""
            QPushButton {{
                background: #002436; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: #003b58; color: #fff; }}
        """)
        hub_btn.clicked.connect(self._open_osint_hub)
        lay.addWidget(hub_btn)

        # ── Открыть Google Maps / Спутник в браузере ─────────────────────────
        browser_map_btn = QPushButton("🌐  Google Maps / Спутник (Браузер)")
        browser_map_btn.setToolTip("Открыть интерактивную карту со всеми отмеченными базами и спутником в браузере")
        browser_map_btn.setFixedHeight(26)
        browser_map_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        browser_map_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browser_map_btn.setStyleSheet(f"""
            QPushButton {{
                background: #001e14; color: #00ff88;
                border: 1px solid #00ff88; border-radius: 4px;
            }}
            QPushButton:hover {{ background: #003624; color: #fff; }}
        """)
        browser_map_btn.clicked.connect(self._open_geoint_browser)
        lay.addWidget(browser_map_btn)

        # ── system monitor (was the left panel) ───────────────────────────
        lay.addSpacing(4)
        self._left_panel.setMinimumWidth(0)
        self._left_panel.setMaximumWidth(16777215)
        self._left_panel.setStyleSheet(f"background: {C.DARK}; border: none;")
        lay.addWidget(self._left_panel)

        # ── log / upload / input / mic (was the right panel) ──────────────
        self._right_panel.setMinimumWidth(0)
        self._right_panel.setMaximumWidth(16777215)
        self._right_panel.setStyleSheet(f"background: {C.DARK}; border: none;")
        self._right_panel.setMinimumHeight(420)
        lay.addWidget(self._right_panel, stretch=1)

        hint = QLabel("[F4] Mute  \u00b7  [F11] Fullscreen  \u00b7  [ESC] Interrupt")
        hint.setFont(QFont("Courier New", 7))
        hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        self._drawer_open = False
        self._drawer_anim = None
        return w

    _DRAWER_W = 360

    def _toggle_drawer(self, checked: bool | None = None):
        """Slide the settings panel in/out with an animation."""
        from PyQt6.QtCore import QPropertyAnimation, QRect
        want_open = (not self._drawer_open) if checked is None else bool(checked)
        if want_open == self._drawer_open and self._quick_drawer.isVisible():
            return
        cw = self.centralWidget()
        dw = min(self._DRAWER_W, max(260, cw.width() - 60))
        h  = cw.height()
        shown  = QRect(cw.width() - dw, 0, dw, h)
        hidden = QRect(cw.width(),      0, dw, h)

        if self._drawer_anim is not None:
            self._drawer_anim.stop()

        if want_open:
            self._quick_drawer.setGeometry(hidden)
            self._quick_drawer.show()
            self._quick_drawer.raise_()
            start, end = hidden, shown
        else:
            start, end = self._quick_drawer.geometry(), hidden

        anim = QPropertyAnimation(self._quick_drawer, b"geometry", self)
        anim.setDuration(280)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic
                            if want_open else QEasingCurve.Type.InCubic)
        if not want_open:
            anim.finished.connect(self._quick_drawer.hide)
        anim.start()
        self._drawer_anim = anim
        self._drawer_open = want_open
        if hasattr(self, "_gear_btn"):
            self._gear_btn.set_active(want_open)
            self._gear_btn.raise_()

    def _position_quick_drawer(self):
        if not hasattr(self, "_quick_drawer"):
            return
        cw = self.centralWidget()
        dw = min(self._DRAWER_W, max(260, cw.width() - 60))
        if self._drawer_open:
            self._quick_drawer.setGeometry(cw.width() - dw, 0, dw, cw.height())
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.setGeometry(cw.width(), 0, dw, cw.height())

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("By FatihMakes", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_holo_pc(self):
        """Open a PC-only Holo Lab with a starter concept."""
        self._show_holo_project({
            "id": "PC-HOLO",
            "name": "Smart Optics / PC prototype",
            "model": "glasses",
            "subject": "smart glasses with camera",
            "mode": "holo",
            "clarity": 90,
            "notes": "Camera input, transparent HUD lens, motion sensor and removable compute/power module.",
            "components": list(HoloBlueprintCanvas._DEFAULT_PARTS["glasses"]),
        })

    def _show_holo_project(self, project=None):
        """Render an AI-generated blueprint and hologram on the PC monitor."""
        if hasattr(self, "_quick_drawer") and self._quick_drawer.isVisible():
            self._toggle_drawer(False)
        cw = self.centralWidget()
        if self._holo_overlay is None:
            ov = HoloLabOverlay(parent=cw)
            ov.closed.connect(lambda: setattr(self, '_holo_overlay', None))
            self._holo_overlay = ov
        self._holo_overlay.set_project(project or {})
        margin = 24
        ow = min(HoloLabOverlay._OW, max(620, cw.width() - margin * 2))
        oh = min(HoloLabOverlay._OH, max(430, cw.height() - margin * 2))
        self._holo_overlay.setGeometry(
            (cw.width() - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        self._holo_overlay.show()
        self._holo_overlay.raise_()
        self._log.append_log(
            f"SYS: Holo blueprint rendered on PC — {self._holo_overlay._canvas.project().get('id', 'PC-HOLO')}"
        )

    def _show_holo_diagnostics(self, symptom: str = ""):
        if self._holo_overlay is None or not self._holo_overlay.isVisible():
            self._open_holo_pc()
        if self._holo_overlay:
            self._holo_overlay.show_diagnostics(symptom)

    def _print_holo_project(self):
        if self._holo_overlay is None or not self._holo_overlay.isVisible():
            self._open_holo_pc()
        if self._holo_overlay:
            self._holo_overlay.print_blueprint()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.set_devices_callbacks(
            getattr(self, "devices_provider", None),
            getattr(self, "devices_kicker", None),
        )
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 4px;
                    text-align: left; padding: 0 10px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 4px;
                    text-align: left; padding: 0 10px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Local Claude (Ollama / LM Studio) toggle ─────────────────────────────
    def _toggle_local_claude(self):
        cfg = _read_full_config()
        new_val = not cfg.get("use_local_claude", False)
        cfg["use_local_claude"] = new_val
        try:
            API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception:
            pass
        self._update_local_claude_btn(new_val)
        status = "ON (локальный Claude)" if new_val else "OFF (Gemini)"
        self._log.append_log(f"SYS: Локальный Claude — {status}")

    def _update_local_claude_btn(self, enabled: bool):
        if not hasattr(self, '_local_claude_btn'):
            return
        if enabled:
            self._local_claude_btn.setText("🧠  LOCAL CLAUDE: ON")
            self._local_claude_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._local_claude_btn.setText("🧠  LOCAL CLAUDE: OFF")
            self._local_claude_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── OSINT Mode ─────────────────────────────────────────────────────────
    def _toggle_osint(self):
        cfg = _read_full_config()
        new_val = not cfg.get("osint_mode", False)
        cfg["osint_mode"] = new_val
        try:
            API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception:
            pass
        self._update_osint_btn(new_val)
        status = "ON (OSINT — без цензуры)" if new_val else "OFF"
        self._log.append_log(f"SYS: OSINT Mode — {status}")

    def _update_osint_btn(self, enabled: bool):
        if not hasattr(self, '_osint_btn'):
            return
        if enabled:
            self._osint_btn.setText("🕵️  OSINT MODE: ON")
            self._osint_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #2a1a00; color: {C.ACC};
                    border: 1px solid {C.ACC}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #3a2500; }}
            """)
        else:
            self._osint_btn.setText("🕵️  OSINT MODE: OFF")
            self._osint_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Internet tunnel (🌐) ─────────────────────────────────────────────
    def _toggle_internet(self):
        """Ask the assistant to toggle the internet tunnel."""
        if self.on_internet_toggle:
            self.on_internet_toggle()

    def _on_internet_status(self, status: dict):
        """Slot — update the 🌐 button from any thread (via signal)."""
        self._update_internet_btn(status or {})

    def _update_internet_btn(self, status: dict):
        if not hasattr(self, '_internet_btn'):
            return
        active = bool(status.get("active", False))
        url    = str(status.get("url") or "").strip()
        if active and url:
            self._internet_btn.setText(f"🌐  INTERNET ACCESS: ON — {url}")
            self._internet_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        elif active:
            self._internet_btn.setText("🌐  INTERNET ACCESS: STARTING…")
            self._internet_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #2a1a00; color: {C.ACC};
                    border: 1px solid {C.ACC}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #3a2500; }}
            """)
        else:
            self._internet_btn.setText("🌐  INTERNET ACCESS: OFF")
            self._internet_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Headphones mode (🎧) ─────────────────────────────────────────────
    def _toggle_headphones(self):
        """Ask the assistant to toggle headphone mode (work runs off-thread)."""
        if self.on_headphones_toggle:
            self.on_headphones_toggle()

    def _on_headphones_status(self, status: dict):
        """Slot — update the 🎧 button from any thread (via signal)."""
        self._update_headphones_btn(status or {})

    def _update_headphones_btn(self, status: dict):
        if not hasattr(self, '_headphones_btn'):
            return
        enabled   = bool(status.get("enabled", False))
        connected = bool(status.get("connected", False))
        name      = str(status.get("name") or "").strip()
        if enabled and connected:
            label = f"🎧  HEADPHONES MODE: ON — {name}" if name else "🎧  HEADPHONES MODE: ON"
            self._headphones_btn.setText(label)
            self._headphones_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140a2a; color: #c4b5fd;
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #1e1040; }}
            """)
        elif enabled:
            self._headphones_btn.setText("🎧  HEADPHONES MODE: ON — no BT headphones")
            self._headphones_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #2a1a00; color: {C.ACC};
                    border: 1px solid {C.ACC}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #3a2500; }}
            """)
        else:
            self._headphones_btn.setText("🎧  HEADPHONES MODE: OFF")
            self._headphones_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Europe Satellite + AI Enhance ─────────────────────────────────────
    def _toggle_europe_ai(self):
        cfg = _read_full_config()
        new_val = not cfg.get("europe_satellite_ai", False)
        cfg["europe_satellite_ai"] = new_val
        try:
            API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception:
            pass
        self._update_europe_ai_btn(new_val)
        status = "ON (Европа + AI Enhance)" if new_val else "OFF"
        self._log.append_log(f"SYS: Europe Satellite + AI — {status}")

    def _update_europe_ai_btn(self, enabled: bool):
        if not hasattr(self, '_europe_ai_btn'):
            return
        if enabled:
            self._europe_ai_btn.setText("🛰️  EUROPE SAT + AI: ON")
            self._europe_ai_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a2e; color: {C.PRI};
                    border: 1px solid {C.PRI}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002a4a; }}
            """)
        else:
            self._europe_ai_btn.setText("🛰️  EUROPE SAT + AI: OFF")
            self._europe_ai_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _open_osint_hub(self):
        """Открывает отдельное окно GEOINT / OSINT Hub с картой баз и спутником"""
        try:
            import subprocess
            import sys
            hub_path = str(Path(__file__).parent / "hub.py")
            subprocess.Popen([sys.executable, hub_path])
            self._log.append_log("SYS: GEOINT / OSINT Hub открыт (карта 40+ военных баз)")
        except Exception as e:
            self._log.append_log(f"ERR: Не удалось открыть Hub: {e}, открываю в браузере...")
            self._open_geoint_browser()

    def _open_geoint_browser(self):
        """Открывает интерактивную GEOINT карту в браузере"""
        try:
            from actions.geoint_engine import open_map_in_browser
            msg = open_map_in_browser()
            self._log.append_log(f"SYS: {msg}")
        except Exception as e:
            self._log.append_log(f"ERR: Ошибка запуска карты в браузере: {e}")

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "EDIT") or "EDIT",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Canlı önizleme — tüm arayüzü yeni renge boyar (config'e YAZMAZ)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "EDIT"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — MARK XLIX")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        elif display in ("EDIT", "EDITH", "E.D.I.T.H.", "E.D.I.T.H", "ЭДИТ"):
            self._sub_lbl.setText("Even Dead I'm The Hero — Autonomous Evolving AI")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Tüm arayüzü (paneller, butonlar, kenarlıklar, HUD) canlı boya
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "EDIT") or "EDIT"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_headphones_toggle(self):
        return self._win.on_headphones_toggle

    @on_headphones_toggle.setter
    def on_headphones_toggle(self, cb):
        self._win.on_headphones_toggle = cb

    def update_headphones_btn(self, status: dict):
        """Thread-safe: refresh the 🎧 mode button from any thread."""
        self._win._headphones_sig.emit(status or {})

    def update_internet_btn(self, status: dict):
        """Thread-safe: refresh the 🌐 internet-tunnel button."""
        self._win._internet_sig.emit(status or {})

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_holo_project(self, project: dict | None = None):
        """Thread-safe: show an AI-generated wearable blueprint on the PC."""
        self._win._holo_sig.emit(project or {})

    def run_holo_diagnostics(self, symptom: str = ""):
        """Thread-safe: open the Holo Lab diagnostic/help panel."""
        self._win._holo_diag_sig.emit(str(symptom or ""))

    def print_holo_blueprint(self):
        """Thread-safe: open the OS printer dialog for the current blueprint."""
        self._win._holo_print_sig.emit()

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    def show_phone_scan(self, img_bytes: bytes, detections) -> None:
        """Thread-safe: paint a phone-scanned frame + EDITH boxes onto the HUD area."""
        self._win._scan_sig.emit(img_bytes, detections)

    def start_phone_cam(self) -> None:
        """Thread-safe: switch the HUD area to the phone's live camera stream."""
        self._win._pcam_sig.emit(True)

    def stop_phone_cam(self) -> None:
        """Thread-safe: hide the phone's live camera stream."""
        self._win._pcam_sig.emit(False)

    def show_phone_cam_frame(self, frame_bytes: bytes) -> None:
        """Thread-safe: newest live frame from the phone camera."""
        self._win._pcam_frame_sig.emit(frame_bytes)

    def show_phone_cam_dets(self, dets) -> None:
        """Thread-safe: newest live detection boxes for the phone stream."""
        self._win._pcam_dets_sig.emit(dets)

    def set_device_callbacks(self, provider, kicker) -> None:
        """Wire the Remote overlay's device hub to the dashboard (called by main.py)."""
        self._win.devices_provider = provider
        self._win.devices_kicker   = kicker

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")