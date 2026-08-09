"""
Local, real-time person tracking for the EDITH HUD.

The Gemini vision pass is accurate but slow (~1-2 s round trip), so on a live
camera feed the silhouette/skeleton always lags behind the person. This module
runs entirely on the local machine and produces per-frame detections at video
rate, so the aura and bones stick to the body while it moves.

Detection chain (first one that works wins):
  1. MediaPipe PoseLandmarker — real skeleton (33 landmarks) + segmentation
     mask, which we trace into a true silhouette polygon.
  2. OpenCV HOG people detector — bounding boxes only; the HUD then draws its
     procedural rig inside the box.
  3. Nothing (module reports unavailable and the HUD falls back to Gemini).

Everything is lazily initialised and fully exception-guarded: if a dependency
or the model file is missing, `track()` simply returns [] and the rest of the
app keeps working exactly as before.
"""

from __future__ import annotations

import math
import os
import threading
import time
import subprocess  # noqa: F401  (used by _spawn_worker)
import sys
import urllib.request
from pathlib import Path

# Landmark indices of the MediaPipe Pose (BlazePose GHUM) topology
_MP_IDX = {
    "nose": 0,
    "l_eye": 2, "r_eye": 5,
    "l_ear": 7, "r_ear": 8,
    "l_shoulder": 11, "r_shoulder": 12,
    "l_elbow": 13, "r_elbow": 14,
    "l_wrist": 15, "r_wrist": 16,
    "l_hip": 23, "r_hip": 24,
    "l_knee": 25, "r_knee": 26,
    "l_ankle": 27, "r_ankle": 28,
}

# Joint names the HUD understands (must match ui._BONES)
_HUD_JOINTS = (
    "head", "neck", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_wrist", "r_wrist", "pelvis", "l_hip", "r_hip",
    "l_knee", "r_knee", "l_ankle", "r_ankle",
)

_MODEL_URLS = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "https://storage.googleapis.com/mediapipe-assets/pose_landmarker_lite.task",
    "https://huggingface.co/spaces/MediaPipe/pose-landmarker/resolve/main/"
    "pose_landmarker_lite.task",
)


def _model_path() -> Path:
    base = Path(__file__).resolve().parent.parent / "config"
    base.mkdir(parents=True, exist_ok=True)
    return base / "pose_landmarker_lite.task"


_FACE_MODEL_URLS = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task",
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task",
)


def _ensure_face_model() -> Path | None:
    """Fetch the 478-point face-mesh model (optional, enables the mask FX)."""
    p = _model_path().parent / "face_landmarker.task"
    if p.exists() and p.stat().st_size > 1000:
        return p
    for url in _FACE_MODEL_URLS:
        tmp = p.with_suffix(".part")
        try:
            print("[PoseTracker] Downloading face-mesh model (one time)…")
            with urllib.request.urlopen(url, timeout=20) as r:   # noqa: S310
                data = r.read()
            if len(data) > 1000:
                tmp.write_bytes(data)
                tmp.replace(p)
                print("[PoseTracker] Face-mesh model ready")
                return p
        except Exception as e:
            print(f"[PoseTracker] Face-mesh model unavailable ({e})")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return None


def _ensure_model() -> Path | None:
    """Return the local model file, downloading it once if needed.

    You can also drop `pose_landmarker_lite.task` into the config/ folder
    manually (e.g. on an offline machine) and it will be picked up here.
    """
    p = _model_path()
    if p.exists() and p.stat().st_size > 1000:
        return p
    for url in _MODEL_URLS:
        tmp = p.with_suffix(".part")
        try:
            print("[PoseTracker] Downloading pose model (one time, ~6 MB)…")
            # NB: a bare urlretrieve() has no timeout and can hang the app
            # forever on a dead network — always bound it.
            with urllib.request.urlopen(url, timeout=20) as r:   # noqa: S310
                data = r.read()
            if len(data) > 1000:
                tmp.write_bytes(data)
                tmp.replace(p)
                print(f"[PoseTracker] Model ready → {p.name}")
                return p
        except Exception as e:
            print(f"[PoseTracker] Model source unavailable ({e})")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    print("[PoseTracker] Could not fetch the pose model. Skeleton tracking is "
          "disabled until it is available.")
    print(f"[PoseTracker] Offline fix: download pose_landmarker_lite.task and "
          f"place it in {p.parent}")
    return None


class PoseTracker:
    """Thread-safe, lazily-initialised local person tracker."""

    def __init__(self, max_people: int = 4):
        self._max_people = max_people
        self._lock = threading.Lock()
        self._mp = None            # MediaPipe PoseLandmarker instance
        self._hog = None           # OpenCV HOG fallback
        self._mode = "init"        # init | mediapipe | hog | off
        self._np = None
        self._cv2 = None
        self._last_err = ""
        self._smooth: dict = {}    # exponential smoothing state
        self._fails = 0            # consecutive backend failures
        self._proc = None          # isolated MediaPipe worker process
        self._face_edges: list = []  # static face-mesh topology (sent once)
        self._restarts = 0         # worker crashes this session (never
                                   # cleared by a lucky frame — a flaky
                                   # backend must degrade, not oscillate)

    # ── availability ────────────────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self._mode

    def available(self) -> bool:
        self._ensure()
        return self._mode in ("mediapipe", "hog")

    def _ensure(self) -> None:
        if self._mode != "init":
            return
        with self._lock:
            if self._mode != "init":
                return
            try:
                self._init_backend()
            except BaseException as e:       # absolutely never propagate
                self._mode = "off"
                print(f"[PoseTracker] Init crashed, disabled — {e}")

    def _init_backend(self) -> None:
        if True:  # keeps the original indentation of the init block
            # numpy + cv2 are needed by both backends
            try:
                import numpy as np
                import cv2
                self._np, self._cv2 = np, cv2
            except Exception as e:
                self._mode, self._last_err = "off", f"numpy/cv2 missing: {e}"
                return

            # 1) MediaPipe pose + segmentation — in an isolated child process.
            #    Importing mediapipe here would load native code into THIS
            #    process, so we only check that it is installed and let the
            #    worker do the actual loading.
            try:
                import importlib.util
                if importlib.util.find_spec("mediapipe") is None:
                    raise RuntimeError("mediapipe not installed")
                if not self._spawn_worker():
                    raise RuntimeError(self._last_err or "worker failed")
                self._mode = "mediapipe"
                print("[PoseTracker] Backend: MediaPipe worker "
                      "(skeleton + silhouette, crash-isolated)")
                return
            except Exception as e:
                self._last_err = f"mediapipe: {e}"
                self._kill_worker()

            # 2) OpenCV HOG people detector
            try:
                hog = self._cv2.HOGDescriptor()
                hog.setSVMDetector(
                    self._cv2.HOGDescriptor_getDefaultPeopleDetector()
                )
                self._hog = hog
                self._mode = "hog"
                print("[PoseTracker] Backend: OpenCV HOG (boxes only)")
                return
            except Exception as e:
                self._last_err += f" | hog: {e}"

            self._mode = "off"
            print(f"[PoseTracker] Disabled — {self._last_err}")
            print("[PoseTracker] Tip: pip install mediapipe opencv-python "
                  "for real-time skeleton tracking.")

    # ── public API ──────────────────────────────────────────────────────
    def track(self, frame_bytes: bytes) -> list[dict]:
        """JPEG bytes → list of HUD detections (same schema as _edith_detect).

        Each person dict has: kind/label/box (+ outline & pose when the
        MediaPipe backend is active). Never raises.
        """
        self._ensure()
        if self._mode not in ("mediapipe", "hog"):
            return []
        try:
            if not frame_bytes:
                return []
            np, cv2 = self._np, self._cv2
            buf = np.frombuffer(frame_bytes, dtype=np.uint8)
            if buf.size == 0:
                return []
            bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if bgr is None or bgr.size == 0:
                return []
            # keep the analysis cheap — downscale wide frames
            h, w = bgr.shape[:2]
            if w > 640:
                sc = 640.0 / w
                bgr = cv2.resize(bgr, (640, max(1, int(h * sc))),
                                 interpolation=cv2.INTER_AREA)
            if self._mode == "mediapipe":
                out = self._track_mediapipe(bgr)
            else:
                out = self._track_hog(bgr)
            self._fails = 0
            return out
        except BaseException as e:      # never let a backend kill the app
            self._fails += 1
            print(f"[PoseTracker] track failed ({self._fails}): {e}")
            # A repeatedly dying worker is unrecoverable — drop to HOG fast so
            # the HUD keeps working instead of stuttering on every frame.
            if self._fails >= 3 or self._restarts >= 3:
                self._degrade()
            return []

    def _degrade(self) -> None:
        """A backend misbehaved repeatedly — drop to the next safest one."""
        with self._lock:
            if self._mode == "mediapipe":
                self._kill_worker()
                try:
                    hog = self._cv2.HOGDescriptor()
                    hog.setSVMDetector(
                        self._cv2.HOGDescriptor_getDefaultPeopleDetector()
                    )
                    self._hog = hog
                    self._mode = "hog"
                    print("[PoseTracker] MediaPipe unstable → switched to HOG")
                except Exception:
                    self._mode = "off"
                    print("[PoseTracker] Disabled after repeated failures")
            else:
                self._mode = "off"
                print("[PoseTracker] Disabled after repeated failures")
            self._fails = 0
            self._restarts = 0
            self._smooth.clear()

    def reset(self) -> None:
        """Clear tracking state and stop the worker (call when the stream ends)."""
        self._smooth.clear()
        self._restarts = 0
        with self._lock:
            self._kill_worker()

    def shutdown(self) -> None:
        """Terminate the worker process (call on application exit)."""
        with self._lock:
            self._kill_worker()

    # ── MediaPipe backend (runs in an isolated child process) ───────────
    #
    # MediaPipe is native C++ and calls abort() on contract violations, OOM or
    # bad tensors. SIGABRT CANNOT be caught by Python try/except — in-process
    # it would kill the whole JARVIS window. So it lives in its own process:
    # if it dies, we just restart it and the UI never notices.

    def _spawn_worker(self) -> bool:
        import subprocess
        model = _ensure_model()
        if model is None:
            return False
        _ensure_face_model()      # optional: enables the face-mesh overlay
        worker = Path(__file__).resolve().parent / "pose_worker.py"
        if not worker.exists():
            return False
        creation = {}
        if os.name == "nt":                     # never flash a console window
            creation["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(worker), str(model), str(self._max_people)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                bufsize=0,
                **creation,
            )
        except Exception as e:
            self._last_err = f"worker spawn: {e}"
            self._proc = None
            return False

        ready = self._recv(timeout=90.0)        # first run may download nothing
        if ready and ready.get("face_edges"):
            self._face_edges = ready["face_edges"]
        if not ready or not ready.get("ready"):
            self._kill_worker()
            self._last_err = f"worker init: {(ready or {}).get('fatal', 'no reply')}"
            return False
        return True

    def _kill_worker(self) -> None:
        p, self._proc = self._proc, None
        if p is None:
            return
        try:
            if p.stdin and not p.stdin.closed:
                p.stdin.close()
        except Exception:
            pass
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def _recv(self, timeout: float) -> dict | None:
        """Read one length-prefixed JSON reply from the worker."""
        p = self._proc
        if p is None or p.stdout is None:
            return None
        import json as _json
        import struct as _struct

        result: dict = {}

        def _reader():
            try:
                head = p.stdout.read(4)
                # A crashed worker closes the pipe → read() returns b"" at
                # once, so we fail fast instead of burning the full timeout.
                if not head or len(head) < 4:
                    return
                (n,) = _struct.unpack(">I", head)
                if n <= 0 or n > 64 * 1024 * 1024:
                    return
                buf = b""
                while len(buf) < n:
                    chunk = p.stdout.read(n - len(buf))
                    if not chunk:
                        return
                    buf += chunk
                result["v"] = _json.loads(buf.decode("utf-8"))
            except Exception:
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        # NOTE: the reader may sit in a blocking read() that never returns —
        # a crashed child does not always close its write end of the pipe. So
        # we watch the *process* as well and bail out as soon as it dies,
        # instead of stalling the video feed for the whole timeout. The
        # orphaned reader thread is a daemon and dies with the pipe.
        deadline = time.monotonic() + timeout
        dead_since = None
        while t.is_alive():
            t.join(0.02)
            if not t.is_alive():
                break
            if p.poll() is not None:
                if dead_since is None:
                    dead_since = time.monotonic()
                elif time.monotonic() - dead_since > 0.2:
                    return None                 # crashed → fail fast
            if time.monotonic() >= deadline:
                return None                     # hung → give up
        return result.get("v")

    def _track_mediapipe(self, frame_bytes: bytes) -> list[dict]:
        """Send the raw JPEG to the worker process and read back detections."""
        import struct as _struct

        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                if self._proc is not None:
                    print("[PoseTracker] Worker died — restarting")
                    self._kill_worker()
                    self._restarts += 1
                    self._smooth.clear()
                if self._restarts >= 3:
                    raise RuntimeError("worker keeps dying")
                if not self._spawn_worker():
                    raise RuntimeError(self._last_err or "worker unavailable")

            try:
                self._proc.stdin.write(_struct.pack(">I", len(frame_bytes)))
                self._proc.stdin.write(frame_bytes)
                self._proc.stdin.flush()
            except Exception as e:
                self._kill_worker()
                raise RuntimeError(f"worker write: {e}") from None

            # A worker that died on the previous frame may only be reaped now;
            # keep the per-frame budget small so the feed never visibly stalls.
            reply = self._recv(timeout=2.5)
            if reply is None:
                rc = self._proc.poll() if self._proc else None
                self._kill_worker()
                self._restarts += 1
                raise RuntimeError(
                    f"worker crashed (exit {rc})" if rc is not None
                    else "worker timeout"
                )

        dets = reply.get("dets") or []
        out = []
        for i, d in enumerate(dets):
            if not isinstance(d, dict):
                continue
            if d.get("kind") == "face":
                # re-attach the topology the worker sent us once at startup
                if self._face_edges:
                    d["mesh_edges"] = self._face_edges
                out.append(d)
                continue
            pose = d.get("pose") or {}
            outline = d.get("outline") or []
            box = d.get("box") or []
            if len(box) != 4:
                continue
            pose, outline, box = self._smooth_person(f"p{i}", pose, outline, box)
            d["pose"], d["outline"], d["box"] = pose, outline, box
            out.append(d)
        if not out:
            self._smooth.clear()
        return out

    # ── OpenCV HOG fallback ─────────────────────────────────────────────
    def _track_hog(self, bgr) -> list:
        """Coarse full-body detector.

        HOG is trained on upright, full-height pedestrians. On close-up or
        portrait framing it happily reports small bogus boxes, so we filter
        hard on confidence, size and aspect ratio. Crucially, results are
        marked `no_skeleton` — a HOG box carries no joint information, and
        inventing a rig inside it (as a generic fallback would) draws a
        skeleton that has nothing to do with the actual person.
        """
        cv2 = self._cv2
        h, w = bgr.shape[:2]
        scale_w = min(w, 480)
        small = cv2.resize(bgr, (scale_w, max(1, int(h * scale_w / w))))
        sh, sw = small.shape[:2]
        rects, weights = self._hog.detectMultiScale(
            small, winStride=(8, 8), padding=(8, 8), scale=1.06
        )
        out = []
        for (x, y, bw, bh), score in zip(rects, weights):
            if float(score) < 0.85:              # was 0.4 → far too permissive
                continue
            if bh < sh * 0.35:                   # ignore tiny spurious blobs
                continue
            ratio = bh / float(bw or 1)
            if not (1.4 <= ratio <= 4.5):        # people are tall, not square
                continue
            out.append({
                "kind":   "person",
                "label":  "PERSON",
                "detail": "detected",
                "box": [round(y / sh * 1000, 1), round(x / sw * 1000, 1),
                        round((y + bh) / sh * 1000, 1),
                        round((x + bw) / sw * 1000, 1)],
                "source": "local",
                "no_skeleton": True,
            })
            if len(out) >= self._max_people:
                break
        return out


# Process-wide singleton — the HUD and the relay share one tracker
_tracker: PoseTracker | None = None
_tracker_lock = threading.Lock()


def get_tracker() -> PoseTracker:
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = PoseTracker()
    return _tracker


def track_people(frame_bytes: bytes) -> list[dict]:
    """Convenience wrapper — local person detections for one JPEG frame."""
    return get_tracker().track(frame_bytes)
