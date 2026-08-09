"""
Face detection + recognition for the EDITH HUD.

Two independent jobs:

  * DETECTION  — find faces in a frame. Tries, in order of quality:
        1. YuNet DNN (`face_detection_yunet.onnx`) — accurate, handles
           profiles, tilted heads and close-ups.
        2. Haar cascade — bundled with most OpenCV builds.
        3. Skin-tone + contour heuristic — crude, but keeps the HUD useful
           on stripped-down builds with no model files at all.

  * RECOGNITION — say *who* it is. Uses OpenCV's LBPH recognizer, which
    trains locally in milliseconds from a handful of photos and needs no
    downloads. Enrolled people live in `config/faces/<Name>/*.jpg`, so the
    user just drops photos in a folder (or calls `enroll_from_frame`).

Everything degrades gracefully: no model → no crash, just fewer features.
Detections are returned in the same 0-1000 normalised box format the rest of
the HUD already speaks.
"""

from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "config"
_FACES_DIR = _CONFIG / "faces"
# Automatically captured faces are kept separately from enrolled identities.
# The leading underscore also makes the directory invisible to the recogniser.
_AUTO_FACES_DIR = _FACES_DIR / "_auto"
_YUNET_FILE = _CONFIG / "face_detection_yunet.onnx"
_MODEL_FILE = _CONFIG / "face_lbph.yml"

_YUNET_URLS = (
    "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx",
)

# LBPH distance below which we accept an identity (lower = more similar)
_MATCH_THRESHOLD = 72.0


def _download(url: str, dest: Path, timeout: int = 20) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310
            data = r.read()
        if len(data) > 1000:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True
    except Exception:
        pass
    return False


class FaceEngine:
    """Thread-safe face detector + optional identity recogniser."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ready = False
        self._cv2 = None
        self._np = None
        self._yunet = None
        self._haar = None
        self._recognizer = None
        self._labels: dict[int, str] = {}
        self._detector_kind = "none"
        self._last_size = (0, 0)
        self._auto_lock = threading.Lock()

    # ── setup ───────────────────────────────────────────────────────────
    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                import cv2
                import numpy as np
                self._cv2, self._np = cv2, np
            except Exception as e:
                print(f"[FaceID] OpenCV unavailable: {e}")
                self._ready = True
                return

            cv2 = self._cv2
            # 1) YuNet DNN detector
            try:
                if not _YUNET_FILE.exists():
                    for u in _YUNET_URLS:
                        if _download(u, _YUNET_FILE):
                            print("[FaceID] YuNet model downloaded")
                            break
                if _YUNET_FILE.exists() and hasattr(cv2, "FaceDetectorYN_create"):
                    self._yunet = cv2.FaceDetectorYN_create(
                        str(_YUNET_FILE), "", (320, 320), 0.6, 0.3, 5000
                    )
                    self._detector_kind = "yunet"
            except Exception as e:
                print(f"[FaceID] YuNet unavailable: {e}")
                self._yunet = None

            # 2) Haar cascade fallback
            if self._yunet is None:
                try:
                    base = getattr(cv2.data, "haarcascades", "")
                    p = Path(base) / "haarcascade_frontalface_default.xml"
                    if p.exists():
                        c = cv2.CascadeClassifier(str(p))
                        if not c.empty():
                            self._haar = c
                            self._detector_kind = "haar"
                except Exception:
                    self._haar = None

            if self._detector_kind == "none":
                self._detector_kind = "heuristic"

            self._load_recognizer()
            self._ready = True
            print(f"[FaceID] Detector: {self._detector_kind} | "
                  f"known faces: {len(self._labels) or 0}")

    # ── enrolment / training ────────────────────────────────────────────
    def _load_recognizer(self) -> None:
        """Train (or reload) the LBPH recogniser from config/faces/<Name>/."""
        cv2, np = self._cv2, self._np
        if cv2 is None or not hasattr(cv2, "face"):
            return
        try:
            samples, labels, names = [], [], {}
            if _FACES_DIR.is_dir():
                for idx, person in enumerate(
                        sorted(p for p in _FACES_DIR.iterdir()
                               if p.is_dir() and not p.name.startswith("_"))):
                    got = 0
                    for img_path in sorted(person.iterdir()):
                        if img_path.suffix.lower() not in (
                                ".jpg", ".jpeg", ".png", ".bmp"):
                            continue
                        img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
                        if img is None:
                            continue
                        face = self._largest_face_crop(img)
                        if face is None:
                            face = cv2.resize(img, (160, 160))
                        samples.append(face)
                        labels.append(idx)
                        got += 1
                    if got:
                        names[idx] = person.name
            if not samples:
                self._recognizer = None
                self._labels = {}
                return
            rec = cv2.face.LBPHFaceRecognizer_create()
            rec.train(samples, np.array(labels))
            self._recognizer = rec
            self._labels = names
            print(f"[FaceID] Trained on {len(samples)} photo(s): "
                  f"{', '.join(names.values())}")
        except Exception as e:
            print(f"[FaceID] Training failed: {e}")
            self._recognizer = None
            self._labels = {}

    def reload(self) -> None:
        """Re-read config/faces/ (call after adding new photos)."""
        self._ensure()
        with self._lock:
            self._load_recognizer()

    def enroll_from_frame(self, frame_bytes: bytes, name: str) -> bool:
        """Save every face found in this frame under config/faces/<name>/."""
        self._ensure()
        cv2, np = self._cv2, self._np
        if cv2 is None or not name.strip():
            return False
        try:
            bgr = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8),
                               cv2.IMREAD_COLOR)
            if bgr is None:
                return False
            boxes = self._detect_boxes(bgr)
            if not boxes:
                return False
            person_dir = _FACES_DIR / name.strip()
            person_dir.mkdir(parents=True, exist_ok=True)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            stamp = int(time.time() * 1000)
            saved = 0
            for i, (x, y, w, h) in enumerate(boxes[:3]):
                crop = gray[max(0, y):y + h, max(0, x):x + w]
                if crop.size == 0:
                    continue
                crop = cv2.resize(crop, (160, 160))
                cv2.imwrite(str(person_dir / f"{stamp}_{i}.jpg"), crop)
                saved += 1
            if saved:
                self.reload()
            return saved > 0
        except Exception as e:
            print(f"[FaceID] Enrol failed: {e}")
            return False

    # ── detection ───────────────────────────────────────────────────────
    def _detect_boxes(self, bgr) -> list[tuple[int, int, int, int]]:
        """Return face rectangles (x, y, w, h) in `bgr` pixel coordinates."""
        cv2, np = self._cv2, self._np
        h, w = bgr.shape[:2]

        if self._yunet is not None:
            try:
                if self._last_size != (w, h):
                    self._yunet.setInputSize((w, h))
                    self._last_size = (w, h)
                _, faces = self._yunet.detect(bgr)
                out = []
                if faces is not None:
                    for f in faces:
                        x, y, fw, fh = (int(v) for v in f[:4])
                        if fw > 8 and fh > 8:
                            out.append((x, y, fw, fh))
                return out
            except Exception:
                pass

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        if self._haar is not None:
            try:
                found = self._haar.detectMultiScale(
                    gray, scaleFactor=1.15, minNeighbors=6,
                    minSize=(max(24, w // 18), max(24, h // 18)),
                )
                return [(int(a), int(b), int(c), int(d)) for a, b, c, d in found]
            except Exception:
                pass

        return self._detect_skin(bgr)

    def _detect_skin(self, bgr) -> list[tuple[int, int, int, int]]:
        """Last-resort detector: skin-coloured blob with face-like proportions."""
        cv2, np = self._cv2, self._np
        try:
            h, w = bgr.shape[:2]
            ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
            mask = cv2.inRange(ycrcb, np.array([0, 133, 77], np.uint8),
                               np.array([255, 173, 127], np.uint8))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                    np.ones((5, 5), np.uint8))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                    np.ones((15, 15), np.uint8))
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            out = []
            for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:3]:
                area = cv2.contourArea(c)
                if area < (w * h) * 0.012:
                    continue
                x, y, cw, ch = cv2.boundingRect(c)
                ratio = ch / float(cw or 1)
                if 0.75 <= ratio <= 2.1:          # faces are roughly oval
                    out.append((x, y, cw, ch))
            return out
        except Exception:
            return []

    def _largest_face_crop(self, gray):
        """Grayscale image → 160×160 crop of the biggest face, or None."""
        cv2 = self._cv2
        try:
            bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            boxes = self._detect_boxes(bgr)
            if not boxes:
                return None
            x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
            crop = gray[max(0, y):y + h, max(0, x):x + w]
            if crop.size == 0:
                return None
            return cv2.resize(crop, (160, 160))
        except Exception:
            return None

    # ── public API ──────────────────────────────────────────────────────
    def detect(self, frame_bytes: bytes, max_faces: int = 5) -> list[dict]:
        """JPEG bytes → HUD detections of kind 'face' (0-1000 boxes)."""
        self._ensure()
        cv2, np = self._cv2, self._np
        if cv2 is None or not frame_bytes:
            return []
        try:
            buf = np.frombuffer(frame_bytes, np.uint8)
            if buf.size == 0:
                return []
            bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if bgr is None or bgr.size == 0:
                return []
            H, W = bgr.shape[:2]
            if W > 640:                       # keep detection cheap
                s = 640.0 / W
                bgr = cv2.resize(bgr, (640, max(1, int(H * s))),
                                 interpolation=cv2.INTER_AREA)
                H, W = bgr.shape[:2]

            with self._lock:
                boxes = self._detect_boxes(bgr)
            if not boxes:
                return []

            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            out = []
            for (x, y, w, h) in sorted(
                    boxes, key=lambda b: b[2] * b[3], reverse=True)[:max_faces]:
                name, score = self._identify(gray, x, y, w, h)
                label = f"FACE — {name}" if name else "FACE — UNKNOWN"
                det = {
                    "kind": "face",
                    "label": label,
                    "detail": (f"match {score:.0f}%" if name else "not enrolled"),
                    "box": [round(max(0, y) / H * 1000, 1),
                            round(max(0, x) / W * 1000, 1),
                            round(min(H, y + h) / H * 1000, 1),
                            round(min(W, x + w) / W * 1000, 1)],
                    "source": "local",
                    "known": bool(name),
                }
                out.append(det)
            return out
        except Exception as e:
            print(f"[FaceID] detect failed: {e}")
            return []

    def save_new_faces(self, frame_bytes: bytes, faces: list[dict]) -> int:
        """Save faces seen for the first time, without saving every video frame.

        Captures are deliberately not enrolled: automatic snapshots must not
        make an unknown person look like a named identity.  A perceptual hash
        of the normalised face crop is compared with previous captures, so a
        face moving slightly in the camera is still treated as the same face.
        """
        self._ensure()
        cv2, np = self._cv2, self._np
        if cv2 is None or not frame_bytes or not faces:
            return 0
        try:
            bgr = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                return 0
            H, W = bgr.shape[:2]
            captures = []
            for f in faces:
                # Named/enrolled faces are already stored; never duplicate them.
                if f.get("known"):
                    continue
                box = f.get("box")
                if not isinstance(box, (list, tuple)) or len(box) != 4:
                    continue
                y0, x0, y1, x1 = (float(v) for v in box)
                x, y = int(x0 * W / 1000), int(y0 * H / 1000)
                x2, y2 = int(x1 * W / 1000), int(y1 * H / 1000)
                crop = bgr[max(0, y):min(H, y2), max(0, x):min(W, x2)]
                if crop.size and crop.shape[0] >= 20 and crop.shape[1] >= 20:
                    captures.append(cv2.resize(crop, (32, 32), interpolation=cv2.INTER_AREA))
            if not captures:
                return 0
            def phash(img):
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                dct = cv2.dct(np.float32(gray))[:8, :8]
                return dct > np.median(dct[1:, 1:])
            with self._auto_lock:
                _AUTO_FACES_DIR.mkdir(parents=True, exist_ok=True)
                old = []
                for path in _AUTO_FACES_DIR.glob("*.jpg"):
                    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                    if img is not None:
                        old.append(phash(img))
                saved = 0
                stamp = int(time.time() * 1000)
                for i, crop in enumerate(captures):
                    signature = phash(crop)
                    # Hamming distance <= 12 means this is the same face/view.
                    if any(int(np.count_nonzero(signature != prev)) <= 12 for prev in old):
                        continue
                    path = _AUTO_FACES_DIR / f"{stamp}_{i}.jpg"
                    if cv2.imwrite(str(path), crop):
                        old.append(signature)
                        saved += 1
                return saved
        except Exception as e:
            print(f"[FaceID] automatic capture failed: {e}")
            return 0

    def _identify(self, gray, x, y, w, h):
        """Crop → (name, confidence%) or (None, 0) when unknown."""
        if self._recognizer is None:
            return None, 0.0
        cv2 = self._cv2
        try:
            crop = gray[max(0, y):y + h, max(0, x):x + w]
            if crop.size == 0:
                return None, 0.0
            crop = cv2.resize(crop, (160, 160))
            label, dist = self._recognizer.predict(crop)
            if dist <= _MATCH_THRESHOLD:
                name = self._labels.get(int(label))
                if name:
                    # map LBPH distance to a friendly 0-100% score
                    pct = max(0.0, min(100.0, 100.0 * (1 - dist / 100.0)))
                    return name.upper(), pct
            return None, 0.0
        except Exception:
            return None, 0.0

    @property
    def detector(self) -> str:
        self._ensure()
        return self._detector_kind

    @property
    def known_names(self) -> list[str]:
        self._ensure()
        return sorted(self._labels.values())


_engine: FaceEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> FaceEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = FaceEngine()
    return _engine


def identify_box(frame_bytes: bytes, faces: list[dict]) -> None:
    """Fill in identities for face detections that already have boxes/meshes.

    Used when the MediaPipe worker supplied the 478-point mesh: we only need
    to answer *who* it is, not where the face is. Mutates `faces` in place.
    """
    eng = get_engine()
    eng._ensure()
    cv2, np = eng._cv2, eng._np
    if cv2 is None or eng._recognizer is None or not faces:
        return
    try:
        bgr = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None or bgr.size == 0:
            return
        H, W = bgr.shape[:2]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        for f in faces:
            box = f.get("box")
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            y0, x0, y1, x1 = box
            x = int(x0 / 1000 * W)
            y = int(y0 / 1000 * H)
            w = max(1, int((x1 - x0) / 1000 * W))
            h = max(1, int((y1 - y0) / 1000 * H))
            name, score = eng._identify(gray, x, y, w, h)
            if name:
                f["label"] = f"FACE — {name}"
                f["detail"] = f"match {score:.0f}% · {len(f.get('mesh') or [])} nodes"
                f["known"] = True
            else:
                nodes = len(f.get("mesh") or [])
                f["label"] = "FACE — UNKNOWN"
                f["detail"] = (f"{nodes} nodes mapped · not enrolled"
                               if nodes else "not enrolled")
                f["known"] = False
    except Exception as e:
        print(f"[FaceID] identify_box failed: {e}")


def detect_faces(frame_bytes: bytes) -> list[dict]:
    """Convenience wrapper — face detections for one JPEG frame."""
    return get_engine().detect(frame_bytes)


def enroll(frame_bytes: bytes, name: str) -> bool:
    """Teach the system a new face from the current frame."""
    return get_engine().enroll_from_frame(frame_bytes, name)


def save_new_faces(frame_bytes: bytes, faces: list[dict]) -> int:
    """Save only face appearances not already captured on disk."""
    return get_engine().save_new_faces(frame_bytes, faces)
