"""
actions/face_vault.py — Face Vault (private, manual-label face library)

Privacy by design (consistent with MARK L's existing stance):
  • Faces are detected and stored locally on YOUR machine only.
  • Every face is saved WITHOUT a name. Names are typed by the owner manually.
  • There is NO automatic identification, NO account/owner lookup, NO network
    upload of any face. Nothing here ever tries to "find out who this is".
  • Same face seen again is recognised by a local perceptual hash so it is NOT
    re-saved as a duplicate — it just bumps a "seen N times" counter.

Deduplication uses a dependency-free perceptual hash (DCT pHash) computed with
numpy + opencv-python (both already project deps). It is good enough to stop the
same person being saved over and over; it is intentionally NOT a forensic
face-matcher.
"""

from __future__ import annotations

import json
import threading
import time
import secrets
from pathlib import Path

# ── optional deps (already in requirements.txt) ──────────────────────────────
try:
    import numpy as np
    _NP = True
except Exception:                       # pragma: no cover
    _NP = False

try:
    import cv2
    _CV2 = True
except Exception:                       # pragma: no cover
    _CV2 = False


BASE_DIR   = Path(__file__).resolve().parent.parent
VAULT_DIR  = BASE_DIR / "memory" / "face_vault"
FACES_DIR  = VAULT_DIR / "faces"
INDEX_PATH = VAULT_DIR / "index.json"

# Hamming distance (over the 64-bit pHash) below which two crops are the SAME
# person. Lower = stricter (more separate entries); higher = looser.
DEFAULT_THRESHOLD = 16

# Hard caps so the local folder can never grow without limit.
MAX_ENTRIES  = 1500
MAX_INGEST_INTERVAL = 3.0          # seconds between ingest passes (throttle)

# Haar cascade ships inside the opencv-python wheel; fall back to a one-time
# download only if the bundled file is somehow missing.
_HAAR_NAME = "haarcascade_frontalface_default.xml"
_HAAR_URL  = ("https://raw.githubusercontent.com/opencv/opencv/4.x/"
              "data/haarcascades/" + _HAAR_NAME)

_lock_singleton = threading.Lock()
_vault: "FaceVault | None" = None


# ── Haar cascade loader (lazy, cached) ────────────────────────────────────────

_cascade_lock = threading.Lock()
_cascade = None
_cascade_tried = False


def _get_cascade():
    """Return a cv2.CascadeClassifier for frontal faces, or None if unavailable."""
    global _cascade, _cascade_tried
    if not _CV2 or not _NP:
        return None
    if _cascade_tried:
        return _cascade
    with _cascade_lock:
        if _cascade_tried:
            return _cascade
        _cascade_tried = True
        # 1) bundled inside this repo (next to face_vault.py) - always
        #    available, works even if the opencv wheel lacks the cascade or
        #    there is no network connection
        this_dir = Path(__file__).resolve().parent
        candidates = [this_dir / _HAAR_NAME]
        try:
            candidates.append(Path(cv2.data.haarcascades) / _HAAR_NAME)  # opencv wheel
        except Exception:
            pass
        candidates.append(VAULT_DIR / _HAAR_NAME)      # last-resort downloaded copy
        for path in candidates:
            try:
                if path.exists():
                    cl = cv2.CascadeClassifier(str(path))
                    if not cl.empty():
                        _cascade = cl
                        return _cascade
            except Exception:
                continue
        # 2) one-time download fallback (offline-safe: just disables the feature)
        try:
            import urllib.request
            VAULT_DIR.mkdir(parents=True, exist_ok=True)
            dest = VAULT_DIR / _HAAR_NAME
            urllib.request.urlretrieve(_HAAR_URL, str(dest))
            cl = cv2.CascadeClassifier(str(dest))
            if not cl.empty():
                _cascade = cl
                return _cascade
        except Exception:
            pass
        print("[FaceVault] Haar cascade unavailable — face capture disabled "
              "(install/repair opencv-python to enable).")
        return None


# ── perceptual hashing ────────────────────────────────────────────────────────

def _phash(gray_square: "np.ndarray", size: int = 32, hash_size: int = 8) -> int:
    """DCT-based 64-bit perceptual hash of an equalized grayscale face crop."""
    g = cv2.resize(gray_square, (size, size), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(g))
    low = dct[:hash_size, :hash_size].flatten()
    med = float(np.median(low[1:])) if low.size > 1 else 0.0   # ignore DC term
    bits = low > med
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")

# ── reusable face primitives (Face Vault + EDIT WEB login share these) ───────

def phash_of_crop(crop_bgr) -> int | None:
    """64-bit perceptual hash of a BGR/GRAY ndarray crop, or None on failure."""
    if not _CV2 or not _NP or crop_bgr is None:
        return None
    try:
        g = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr
        g = cv2.equalizeHist(g)
        return _phash(g)
    except Exception:
        return None


def detect_faces(image_bytes: bytes) -> list[dict]:
    """Decode an image and return [{box, crop}] for each detected face.
    ``box`` = (x, y, w, h) in original-image pixels; ``crop`` = BGR ndarray.
    Empty list if OpenCV/cascade unavailable or no faces found."""
    if not _CV2 or not _NP or _get_cascade() is None:
        return []
    try:
        bgr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            return []
    except Exception:
        return []
    cascade = _get_cascade()
    h_img, w_img = bgr.shape[:2]
    scale = 1.0
    max_dim = 720
    if max(h_img, w_img) > max_dim:
        scale = max_dim / float(max(h_img, w_img))
        small = cv2.resize(bgr, (int(w_img * scale), int(h_img * scale)),
                           interpolation=cv2.INTER_AREA)
    else:
        small = bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    try:
        raw = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5,
                                       minSize=(40, 40))
    except Exception:
        return []
    out: list[dict] = []
    for (sx, sy, sw, sh) in raw:
        if scale != 1.0:
            x, y, w, h = sx / scale, sy / scale, sw / scale, sh / scale
        else:
            x, y, w, h = float(sx), float(sy), float(sw), float(sh)
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w < 28 or h < 28:
            continue
        pad = int(min(w, h) * 0.2)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w_img, x + w + pad), min(h_img, y + h + pad)
        if x1 - x0 < 28 or y1 - y0 < 28:
            continue
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        out.append({"box": (x0, y0, x1 - x0, y1 - y0), "crop": crop})
    return out


# ── the vault ─────────────────────────────────────────────────────────────────

class FaceVault:
    """Thread-safe local store of detected faces with manual labeling."""

    def __init__(self):
        self._lock = threading.RLock()
        FACES_DIR.mkdir(parents=True, exist_ok=True)
        self._enabled: bool = True
        self._threshold: int = DEFAULT_THRESHOLD
        self._entries: list[dict] = []
        self._last_ingest = 0.0
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            raw = INDEX_PATH.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            self._enabled  = bool(data.get("enabled", True))
            self._threshold = int(data.get("threshold", DEFAULT_THRESHOLD))
            entries = data.get("entries") or []
            if isinstance(entries, list):
                self._entries = [e for e in entries if isinstance(e, dict)]
            # rebuild hash ints (stored as hex strings)
            for e in self._entries:
                try:
                    e["hash"] = int(str(e.get("hash", "0")), 16)
                except Exception:
                    e["hash"] = 0
                e.setdefault("name", "")
                e.setdefault("count", 1)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[FaceVault] index load failed ({e}) — starting fresh")

    def _save(self) -> None:
        with self._lock:
            payload = {
                "enabled": self._enabled,
                "threshold": self._threshold,
                "entries": [
                    {
                        "id":        e["id"],
                        "name":      e.get("name", ""),
                        "hash":      format(int(e.get("hash", 0)), "x"),
                        "count":     int(e.get("count", 1)),
                        "first_seen": float(e.get("first_seen", time.time())),
                        "last_seen":  float(e.get("last_seen", time.time())),
                        "image":     e.get("image", ""),
                    }
                    for e in self._entries
                ],
            }
            tmp = INDEX_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(INDEX_PATH)

    # ── public config ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return _CV2 and _NP and _get_cascade() is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> bool:
        with self._lock:
            self._enabled = bool(value)
            self._save()
        return self._enabled

    def threshold(self) -> int:
        return self._threshold

    def set_threshold(self, value: int) -> int:
        with self._lock:
            self._threshold = max(0, min(40, int(value)))
            self._save()
        return self._threshold

    # ── listing / mutations ──────────────────────────────────────────────────

    def list_entries(self) -> list[dict]:
        with self._lock:
            out = []
            for e in self._entries:
                out.append({
                    "id":         e["id"],
                    "name":       e.get("name", ""),
                    "count":      int(e.get("count", 1)),
                    "first_seen": float(e.get("first_seen", 0.0)),
                    "last_seen":  float(e.get("last_seen", 0.0)),
                    "has_image":  bool(e.get("image")),
                })
            # newest first
            out.sort(key=lambda d: d["last_seen"], reverse=True)
            return out

    def rename(self, face_id: str, name: str) -> bool:
        with self._lock:
            for e in self._entries:
                if e["id"] == face_id:
                    e["name"] = (name or "").strip()[:80]
                    self._save()
                    return True
            return False

    def delete(self, face_id: str) -> bool:
        with self._lock:
            for i, e in enumerate(self._entries):
                if e["id"] == face_id:
                    img = e.get("image")
                    del self._entries[i]
                    self._save()
                    if img:
                        try:
                            (FACES_DIR / Path(img).name).unlink(missing_ok=True)
                        except Exception:
                            pass
                    return True
            return False

    def clear(self) -> int:
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            self._save()
        try:
            for p in FACES_DIR.glob("*.jpg"):
                p.unlink(missing_ok=True)
        except Exception:
            pass
        return n

    def image_path(self, face_id: str) -> Path | None:
        with self._lock:
            for e in self._entries:
                if e["id"] == face_id and e.get("image"):
                    p = FACES_DIR / Path(e["image"]).name
                    return p if p.exists() else None
        return None

    # ── core: ingest a decoded frame (JPEG/PNG bytes) ────────────────────────

    def ingest_frame(self, image_bytes: bytes) -> list[dict]:
        """Detect every face in the frame; save new ones, dedup known ones.

        Returns a list of {id, is_new, name} for each face found this pass.
        Thread-safe and self-throttled.
        """
        if not self._enabled or not self.available:
            return []
        now = time.monotonic()
        if now - self._last_ingest < MAX_INGEST_INTERVAL:
            return []
        self._last_ingest = now

        try:
            arr = np.frombuffer(image_bytes, np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None:
                return []
        except Exception:
            return []

        boxes = self._detect_face_boxes(bgr)
        if not boxes:
            return []

        results: list[dict] = []
        for (x, y, w, h) in boxes:
            res = self._add_crop(bgr, x, y, w, h)
            if res:
                results.append(res)
        return results

    # ── helpers ──────────────────────────────────────────────────────────────

    def _detect_face_boxes(self, bgr: "np.ndarray") -> list[tuple[int, int, int, int]]:
        cascade = _get_cascade()
        if cascade is None:
            return []
        # downscale very large frames for speed; remember the scale to map boxes back
        h_img, w_img = bgr.shape[:2]
        scale = 1.0
        max_dim = 720
        if max(h_img, w_img) > max_dim:
            scale = max_dim / float(max(h_img, w_img))
            small = cv2.resize(bgr, (int(w_img * scale), int(h_img * scale)),
                               interpolation=cv2.INTER_AREA)
        else:
            small = bgr
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        try:
            raw = cascade.detectMultiScale(
                gray, scaleFactor=1.2, minNeighbors=5,
                minSize=(40, 40),
            )
        except Exception:
            return []
        boxes = []
        for (sx, sy, sw, sh) in raw:
            if scale != 1.0:
                sx, sy, sw, sh = (sx / scale, sy / scale, sw / scale, sh / scale)
            boxes.append((int(sx), int(sy), int(sw), int(sh)))
        return boxes

    def _add_crop(self, bgr, x, y, w, h) -> dict | None:
        h_img, w_img = bgr.shape[:2]
        # expand the box ~20% to include a little hair/chin context
        pad = int(min(w, h) * 0.2)
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w_img, x + w + pad)
        y1 = min(h_img, y + h + pad)
        if x1 - x0 < 28 or y1 - y0 < 28:
            return None
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return None

        # hash on a normalized, equalized grayscale square
        try:
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            g = cv2.equalizeHist(g)
            h_val = _phash(g)
        except Exception:
            return None

        now_ts = time.time()
        with self._lock:
            best_id = None
            best_d = 10_000
            for e in self._entries:
                d = _hamming(h_val, int(e.get("hash", 0)))
                if d < best_d:
                    best_d = d
                    best_id = e["id"]
            if best_id is not None and best_d <= self._threshold:
                e = next(en for en in self._entries if en["id"] == best_id)
                e["count"] = int(e.get("count", 1)) + 1
                e["last_seen"] = now_ts
                # keep a clearer representative crop while the face is still unnamed
                if not e.get("name"):
                    self._maybe_replace_image(e, crop)
                self._save()
                return {"id": best_id, "is_new": False, "name": e.get("name", "")}

            if len(self._entries) >= MAX_ENTRIES:
                return None  # vault full — still allowed to dedup existing

            face_id = secrets.token_hex(6)
            fname = f"{face_id}.jpg"
            rel = f"faces/{fname}"
            if not self._write_jpeg(FACES_DIR / fname, crop):
                return None
            entry = {
                "id": face_id,
                "name": "",                 # always unnamed until the owner labels it
                "hash": h_val,
                "count": 1,
                "first_seen": now_ts,
                "last_seen": now_ts,
                "image": rel,
            }
            self._entries.append(entry)
            self._save()
            return {"id": face_id, "is_new": True, "name": ""}

    def _maybe_replace_image(self, entry: dict, new_crop: "np.ndarray") -> None:
        """Swap the stored thumbnail for a larger/clearer crop (only if unnamed)."""
        try:
            old_h, old_w = new_crop.shape[:2]
            if old_w * old_h < 90 * 90:
                return  # new crop too small to bother
            fname = Path(entry.get("image", "")).name
            if not fname:
                return
            self._write_jpeg(FACES_DIR / fname, new_crop)
        except Exception:
            pass

    @staticmethod
    def _write_jpeg(path: Path, crop_bgr: "np.ndarray") -> bool:
        try:
            thumb = crop_bgr
            th = thumb.shape[0]
            if thumb.shape[0] > 256 or thumb.shape[1] > 256:
                fx = 256.0 / max(thumb.shape[:2])
                thumb = cv2.resize(
                    thumb, (int(thumb.shape[1] * fx), int(thumb.shape[0] * fx)),
                    interpolation=cv2.INTER_AREA,
                )
            ok, buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                return False
            path.write_bytes(buf.tobytes())
            return True
        except Exception:
            return False


def get_vault() -> FaceVault:
    """Process-wide singleton."""
    global _vault
    if _vault is None:
        with _lock_singleton:
            if _vault is None:
                _vault = FaceVault()
    return _vault
