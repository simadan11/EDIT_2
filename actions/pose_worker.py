"""
Isolated MediaPipe pose worker.

Runs as its OWN process and talks to the parent over stdin/stdout using a
simple length-prefixed protocol:

    parent → worker : [4-byte big-endian length][JPEG bytes]
    worker → parent : [4-byte big-endian length][UTF-8 JSON]

Why a separate process?  MediaPipe's VIDEO pipeline is native C++ and calls
abort() on contract violations, OOM or malformed tensors. A native abort
raises SIGABRT, which **cannot** be caught by Python try/except — it would
take the whole JARVIS window down with it. Here, a crash only kills this
worker; the parent notices, restarts it (or degrades gracefully) and the UI
never even flickers.

The module is intentionally dependency-light and prints nothing to stdout
(stdout is the data channel — diagnostics go to stderr).
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
from pathlib import Path

# stdout must stay a clean binary channel
_OUT = sys.stdout.buffer
_IN = sys.stdin.buffer


def _log(msg: str) -> None:
    print(f"[PoseWorker] {msg}", file=sys.stderr, flush=True)


def _read_exactly(n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = _IN.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _send(obj) -> None:
    data = json.dumps(obj).encode("utf-8")
    _OUT.write(struct.pack(">I", len(data)))
    _OUT.write(data)
    _OUT.flush()


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

_HUD_JOINTS = (
    "head", "neck", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_wrist", "r_wrist", "pelvis", "l_hip", "r_hip",
    "l_knee", "r_knee", "l_ankle", "r_ankle",
)


class _Engine:
    def __init__(self, model_path: str, max_people: int):
        import cv2
        import numpy as np
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        self.cv2, self.np = cv2, np
        self.max_people = max_people
        self._last_ts = 0

        opts = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=max_people,
            min_pose_detection_confidence=0.5,
            min_tracking_confidence=0.5,
            output_segmentation_masks=True,
        )
        self.landmarker = mp_vision.PoseLandmarker.create_from_options(opts)

        # ── Face mesh (478 landmarks) — optional, enabled when the model file
        #    is present next to the pose model.
        self.face = None
        self.face_edges = []
        try:
            face_model = str(Path(model_path).parent / "face_landmarker.task")
            if Path(face_model).exists():
                fopts = mp_vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(
                        model_asset_path=face_model
                    ),
                    running_mode=mp_vision.RunningMode.VIDEO,
                    num_faces=max_people,
                    min_face_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                self.face = mp_vision.FaceLandmarker.create_from_options(fopts)
                conns = mp_vision.FaceLandmarksConnections
                self.face_edges = [
                    [c.start, c.end]
                    for c in conns.FACE_LANDMARKS_TESSELATION
                ]
                _log(f"face mesh ready ({len(self.face_edges)} edges)")
        except Exception as e:
            _log(f"face mesh unavailable: {e}")
            self.face = None

    # ── main entry ──────────────────────────────────────────────────────
    def process(self, frame: bytes) -> list:
        import mediapipe as mp
        np, cv2 = self.np, self.cv2

        buf = np.frombuffer(frame, dtype=np.uint8)
        if buf.size == 0:
            return []
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if bgr is None or bgr.size == 0:
            return []
        h, w = bgr.shape[:2]
        if w > 640:
            sc = 640.0 / w
            bgr = cv2.resize(bgr, (640, max(1, int(h * sc))),
                             interpolation=cv2.INTER_AREA)
        h, w = bgr.shape[:2]

        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # single-threaded process → timestamps are trivially monotonic
        self._last_ts += 33
        res = self.landmarker.detect_for_video(image, self._last_ts)

        faces = self._face_mesh(image, self._last_ts)

        out = []
        for f in faces:
            out.append(f)
        lms = getattr(res, "pose_landmarks", None) or []
        masks = getattr(res, "segmentation_masks", None) or []
        for i, person in enumerate(lms[: self.max_people]):
            pts, xs, ys = {}, [], []
            for name, idx in _MP_IDX.items():
                if idx >= len(person):
                    continue
                lm = person[idx]
                vis = getattr(lm, "visibility", 1.0)
                if vis is not None and vis < 0.35:
                    continue
                x, y = float(lm.x), float(lm.y)
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                pts[name] = (y, x)
                xs.append(x)
                ys.append(y)
            if len(pts) < 4:
                continue
            pose = _to_hud_joints(pts)
            outline = self._mask_outline(masks[i], w, h) if i < len(masks) else []
            if not outline:
                outline = _pose_outline(pose)
            out.append({
                "kind": "person",
                "label": "PERSON — TRACKED",
                "detail": "live local tracking",
                "box": _box_from(xs, ys),
                "pose": pose,
                "outline": outline,
                "source": "local",
            })
        return out

    def _face_mesh(self, image, ts: int) -> list:
        """Detect face meshes → HUD detections carrying the full point cloud."""
        if self.face is None:
            return []
        try:
            fres = self.face.detect_for_video(image, ts)
        except Exception as e:
            _log(f"face mesh failed: {e}")
            return []
        out = []
        for lm in (getattr(fres, "face_landmarks", None) or []):
            pts, xs, ys = [], [], []
            for p in lm:
                x, y = float(p.x), float(p.y)
                if not (math.isfinite(x) and math.isfinite(y)):
                    x = y = 0.0
                x = max(0.0, min(1.0, x))
                y = max(0.0, min(1.0, y))
                pts.append([round(y * 1000, 1), round(x * 1000, 1)])
                xs.append(x)
                ys.append(y)
            if len(pts) < 100:
                continue
            out.append({
                "kind": "face",
                "label": "FACE — SCANNING",
                "detail": f"{len(pts)} nodes mapped",
                "box": _box_from(xs, ys),
                "mesh": pts,
                "source": "local",
                "known": False,
            })
        return out

    def _mask_outline(self, mask, w: int, h: int) -> list:
        try:
            np, cv2 = self.np, self.cv2
            arr = mask.numpy_view() if hasattr(mask, "numpy_view") else np.asarray(mask)
            if arr is None or arr.size == 0:
                return []
            binary = (arr > 0.5).astype(np.uint8) * 255
            if binary.ndim == 3:
                binary = binary[:, :, 0]
            mh, mw = binary.shape[:2]
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
                                      np.ones((5, 5), np.uint8))
            cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                return []
            c = max(cnts, key=cv2.contourArea)
            if cv2.contourArea(c) < (mw * mh) * 0.01:
                return []
            peri = cv2.arcLength(c, True)
            pts = cv2.approxPolyDP(c, 0.006 * peri, True).reshape(-1, 2)
            if len(pts) < 5:
                return []
            if len(pts) > 36:
                step = len(pts) / 36.0
                pts = [pts[int(i * step)] for i in range(36)]
            return [[round(float(p[1]) / mh * 1000, 1),
                     round(float(p[0]) / mw * 1000, 1)] for p in pts]
        except Exception:
            return []


def _to_hud_joints(pts: dict) -> dict:
    def mid(a, b):
        pa, pb = pts.get(a), pts.get(b)
        if pa and pb:
            return [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2]
        return None

    j = {}
    head = mid("l_ear", "r_ear") or mid("l_eye", "r_eye")
    if head is None and "nose" in pts:
        head = [pts["nose"][0], pts["nose"][1]]
    neck = mid("l_shoulder", "r_shoulder")
    pelvis = mid("l_hip", "r_hip")
    if head and neck:
        head = [head[0] - abs(neck[0] - head[0]) * 0.35, head[1]]
    if head:
        j["head"] = head
    if neck:
        j["neck"] = neck
    if pelvis:
        j["pelvis"] = pelvis
    for name in ("l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
                 "l_wrist", "r_wrist", "l_hip", "r_hip",
                 "l_knee", "r_knee", "l_ankle", "r_ankle"):
        if name in pts:
            j[name] = [pts[name][0], pts[name][1]]
    return {k: [round(v[0] * 1000, 1), round(v[1] * 1000, 1)]
            for k, v in j.items() if k in _HUD_JOINTS}


def _box_from(xs, ys) -> list:
    pad_x = (max(xs) - min(xs)) * 0.12 + 0.02
    pad_y = (max(ys) - min(ys)) * 0.10 + 0.03
    cl = lambda v: max(0.0, min(1000.0, v * 1000))   # noqa: E731
    return [round(cl(min(ys) - pad_y), 1), round(cl(min(xs) - pad_x), 1),
            round(cl(max(ys) + pad_y), 1), round(cl(max(xs) + pad_x), 1)]


def _pose_outline(pose: dict) -> list:
    need = ("head", "l_shoulder", "r_shoulder", "l_hip", "r_hip")
    if not all(k in pose for k in need):
        return []

    def g(k, dy=0.0, dx=0.0):
        p = pose.get(k)
        return None if p is None else [p[0] + dy, p[1] + dx]

    span = abs(pose["l_shoulder"][1] - pose["r_shoulder"][1]) or 60.0
    pad = span * 0.30
    chain = [
        g("head", -pad * 1.1),
        g("r_shoulder", -pad * 0.3, pad * 0.6),
        g("r_elbow", 0, pad * 0.6) or g("r_shoulder", 0, pad),
        g("r_wrist", 0, pad * 0.5) or g("r_elbow", 0, pad),
        g("r_hip", 0, pad * 0.5),
        g("r_knee", 0, pad * 0.45) or g("r_hip", pad, pad * 0.4),
        g("r_ankle", pad * 0.4, pad * 0.4) or g("r_knee", pad, 0),
        g("l_ankle", pad * 0.4, -pad * 0.4) or g("l_knee", pad, 0),
        g("l_knee", 0, -pad * 0.45) or g("l_hip", pad, -pad * 0.4),
        g("l_hip", 0, -pad * 0.5),
        g("l_wrist", 0, -pad * 0.5) or g("l_elbow", 0, -pad),
        g("l_elbow", 0, -pad * 0.6) or g("l_shoulder", 0, -pad),
        g("l_shoulder", -pad * 0.3, -pad * 0.6),
    ]
    pts = [[max(0.0, min(1000.0, p[0])), max(0.0, min(1000.0, p[1]))]
           for p in chain if p]
    return pts if len(pts) >= 6 else []


def main() -> int:
    if len(sys.argv) < 2:
        _log("missing model path")
        return 2
    model_path = sys.argv[1]
    max_people = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    try:
        engine = _Engine(model_path, max_people)
    except BaseException as e:
        _log(f"init failed: {e}")
        try:
            _send({"fatal": str(e)})
        except Exception:
            pass
        return 3

    # The tesselation is static (~2556 edges): send it once at startup instead
    # of re-serialising it on every single frame.
    _send({"ready": True, "face_edges": engine.face_edges})
    _log("ready")

    while True:
        header = _read_exactly(4)
        if not header:
            break                                    # parent closed the pipe
        (size,) = struct.unpack(">I", header)
        if size == 0:
            break                                    # shutdown sentinel
        if size > 32 * 1024 * 1024:
            _log("frame too large")
            break
        frame = _read_exactly(size)
        if frame is None:
            break
        try:
            dets = engine.process(frame)
        except BaseException as e:                   # keep serving next frames
            _log(f"process failed: {e}")
            dets = []
        try:
            _send({"dets": dets})
        except Exception:
            break
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
