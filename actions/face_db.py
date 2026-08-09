"""
Vector face database — the "face print" store behind EDITH's identification.

This implements the classic face-recognition pipeline:

    detect  →  measure landmarks  →  encode into a vector  →  search the DB

The encoding step turns a face into a numeric embedding (a "face print"),
and identification is a nearest-neighbour search over every enrolled vector.
That is exactly what a vector database is built for, so identities live in
**Milvus Lite** — an embedded, file-backed Milvus that needs no server:

    client = MilvusClient("config/faces.db")

Embedding backends, best first:
  1. **SFace** (OpenCV `FaceRecognizerSF`, 128-D) — a real metric-learning
     model: same person ⇒ vectors close together, different people ⇒ far
     apart, even across lighting and pose. This is what makes recognition
     actually work.
  2. **Landmark geometry** (~150-D) — normalised inter-point distances from
     the 478-point face mesh. Weaker, but fully offline with no model file.

If Milvus is unavailable the same API falls back to a local JSON store with
brute-force cosine search, so identification never silently disappears.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "config"
_DB_FILE = _CONFIG / "faces.db"            # Milvus Lite database file
_JSON_FILE = _CONFIG / "faces_vectors.json"  # fallback store
_SFACE_FILE = _CONFIG / "face_recognition_sface.onnx"

_COLLECTION = "faces"

_SFACE_URLS = (
    "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx",
)

# Cosine similarity above which two face prints are considered the same person.
# SFace's own recommended threshold is 0.363; we keep a small safety margin.
_SFACE_THRESHOLD = 0.40
# The landmark-geometry descriptor is only weakly discriminative: different
# people still score ~0.997 against each other, so the bar has to sit very
# high. It is a stop-gap — install the SFace model for real recognition.
_GEOM_THRESHOLD = 0.9995
# Appearance descriptors are only weakly discriminative: two different faces
# still score ~0.82 against each other, so the bar must sit well above that.
# Install the SFace model for genuinely reliable matching.
# Measured: same face across rescaling + video compression ≈ 0.90, a
# different face ≈ 0.82. 0.87 sits between the two. This descriptor is a
# fallback — install the SFace model for a far wider safety margin.
_APPEAR_THRESHOLD = 0.87


def _model_ok(p) -> bool:
    """A model file must be big enough and not be an HTML error page."""
    try:
        if not p.exists() or p.stat().st_size < 20_000:
            return False
        head = p.open("rb").read(200).lstrip().lower()
        return not head.startswith((b"<!doctype", b"<html", b"{",
                                    b"version https://git-lfs"))
    except Exception:
        return False


def _download(url: str, dest: Path, timeout: int = 25) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310
            data = r.read()
        head = data[:200].lstrip().lower()
        if len(data) > 20_000 and not head.startswith(
                (b"<!doctype", b"<html", b"{", b"version https://git-lfs")):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True
        print(f"[{'FaceDB' if 'face_db' in __file__ else 'FaceID'}] "
              f"rejected invalid download")
    except Exception as e:
        print(f"[FaceDB] download failed: {e}")
    return False


def _norm(vec: list[float]) -> list[float]:
    s = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / s for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class FaceDB:
    """Face-print encoder + vector store (Milvus Lite, JSON fallback)."""

    def __init__(self):
        # Re-entrant: encode() may be called while other helpers already hold
        # the lock. A plain Lock would self-deadlock.
        self._lock = threading.RLock()
        # Dedicated lock for native model inference (not re-entrant safe in
        # OpenCV) so it never contends with plain store operations.
        self._infer_lock = threading.RLock()
        self._ready = False
        self._cv2 = None
        self._np = None
        self._sface = None
        self._dim = 0
        self._client = None          # MilvusClient
        self._json: list[dict] = []  # fallback rows
        self._backend = "none"
        self._encoder = "none"
        self._next_id = 1
        # Which encoder produced the most recent vector. Vectors from
        # different encoders live in the same collection but are NEVER
        # compared to each other — their cosine distance is meaningless.
        self._last_kind = "geom"

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
                print(f"[FaceDB] OpenCV missing: {e}")
                self._ready = True
                return

            # ── encoder: SFace produces a real 128-D face print ──────────
            try:
                if not _SFACE_FILE.exists():
                    for u in _SFACE_URLS:
                        if _download(u, _SFACE_FILE):
                            print("[FaceDB] SFace model downloaded")
                            break
                if _model_ok(_SFACE_FILE) and hasattr(self._cv2,
                                                    "FaceRecognizerSF_create"):
                    self._sface = self._cv2.FaceRecognizerSF_create(
                        str(_SFACE_FILE), ""
                    )
                    self._dim = 128
                    self._encoder = "sface"
            except Exception as e:
                print(f"[FaceDB] SFace unavailable: {e}")
                self._sface = None

            if self._sface is None:
                self._dim = 152           # landmark-geometry descriptor
                self._encoder = "geometry"

            self._open_store()
            self._ready = True
            print(f"[FaceDB] encoder={self._encoder} dim={self._dim} "
                  f"store={self._backend} people={len(self.names())}")

    def _open_store(self) -> None:
        """Open Milvus Lite, or fall back to a JSON file."""
        try:
            from pymilvus import MilvusClient
            _CONFIG.mkdir(parents=True, exist_ok=True)
            self._client = MilvusClient(str(_DB_FILE))
            if not self._client.has_collection(collection_name=_COLLECTION):
                # id + vector are implicit; `name` is scalar metadata we can
                # filter on later (e.g. delete everything for one person).
                self._client.create_collection(
                    collection_name=_COLLECTION,
                    dimension=self._dim,
                    metric_type="COSINE",
                    auto_id=False,
                )
            else:
                # dimension must match the active encoder
                try:
                    desc = self._client.describe_collection(
                        collection_name=_COLLECTION)
                    dim = None
                    for f in desc.get("fields", []):
                        p = f.get("params") or {}
                        if "dim" in p:
                            dim = int(p["dim"])
                    if dim and dim != self._dim:
                        print(f"[FaceDB] encoder changed ({dim}→{self._dim}) — "
                              f"rebuilding collection")
                        self._client.drop_collection(collection_name=_COLLECTION)
                        self._client.create_collection(
                            collection_name=_COLLECTION,
                            dimension=self._dim,
                            metric_type="COSINE",
                            auto_id=False,
                        )
                except Exception:
                    pass
            # A collection must be loaded into memory before search/query,
            # otherwise Milvus raises "state 'released'".
            try:
                self._client.load_collection(collection_name=_COLLECTION)
            except Exception:
                pass
            self._backend = "milvus"
            self._next_id = int(time.time() * 1000) % 1_000_000_000
            return
        except Exception as e:
            print(f"[FaceDB] Milvus Lite unavailable ({e}) — using JSON store")
            self._client = None

        self._backend = "json"
        try:
            if _JSON_FILE.exists():
                raw = json.loads(_JSON_FILE.read_text(encoding="utf-8"))
                self._json = [r for r in raw
                              if isinstance(r, dict) and r.get("vector")]
        except Exception:
            self._json = []

    def _save_json(self) -> None:
        try:
            _CONFIG.mkdir(parents=True, exist_ok=True)
            _JSON_FILE.write_text(json.dumps(self._json), encoding="utf-8")
        except Exception as e:
            print(f"[FaceDB] JSON save failed: {e}")

    # ── encoding ────────────────────────────────────────────────────────
    def encode(self, bgr, box=None, landmarks=None) -> list[float] | None:
        """Face image (+ optional box / 5-point landmarks) → unit face print."""
        self._ensure()
        cv2, np = self._cv2, self._np
        if cv2 is None:
            return None
        try:
            if self._sface is not None:
                # OpenCV DNN inference is not re-entrant: two threads calling
                # alignCrop()/feature() on the same net corrupts its internal
                # blobs and crashes the process (0xC0000005 on Windows).
                with self._infer_lock:
                    v = self._encode_sface(bgr, box, landmarks)
                self._last_kind = "sface"
                return v

            # geometry encoder — needs the dense mesh
            if landmarks is not None and len(landmarks) >= 68:
                self._last_kind = "geom"
                return self._encode_geometry(landmarks)
            # No mesh available (e.g. scanning a video frame where we only have
            # a bounding box): fall back to an appearance descriptor so the
            # face is still comparable instead of silently unencodable.
            if box is not None:
                self._last_kind = "appear"
                return self._encode_appearance(bgr, box)
            return None
        except Exception as e:
            print(f"[FaceDB] encode failed: {e}")
            return None

    def _encode_sface(self, bgr, box, landmarks) -> list[float] | None:
        """Run SFace. Caller MUST hold self._lock."""
        cv2, np = self._cv2, self._np
        try:
            if True:
                h, w = bgr.shape[:2]
                if landmarks is not None and len(landmarks) >= 5:
                    row = list(box or [0, 0, w, h]) + [
                        c for p in landmarks[:5] for c in p
                    ]
                    det = np.array([row], dtype=np.float32)
                else:
                    x, y, bw, bh = box or (0, 0, w, h)
                    # SFace wants 5 landmarks; approximate them from the box
                    det = np.array([[
                        x, y, bw, bh,
                        x + bw * 0.31, y + bh * 0.40,      # right eye
                        x + bw * 0.69, y + bh * 0.40,      # left eye
                        x + bw * 0.50, y + bh * 0.62,      # nose
                        x + bw * 0.35, y + bh * 0.80,      # right mouth
                        x + bw * 0.65, y + bh * 0.80,      # left mouth
                    ]], dtype=np.float32)
                aligned = self._sface.alignCrop(bgr, det[0])
                feat = self._sface.feature(aligned)
                return _norm([float(v) for v in np.asarray(feat).ravel()])
        except Exception as e:
            print(f"[FaceDB] sface encode failed: {e}")
            return None

    def _encode_appearance(self, bgr, box) -> list[float] | None:
        """Crop → compact appearance descriptor (grid of gradient histograms).

        Much weaker than SFace, but unlike raw pixels it tolerates lighting
        shifts, and it lets box-only pipelines (video scan) still match.
        """
        cv2, np = self._cv2, self._np
        try:
            x, y, w, h = (int(v) for v in box)
            H, W = bgr.shape[:2]
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(W, x + w), min(H, y + h)
            if x1 - x0 < 8 or y1 - y0 < 8:
                return None
            crop = bgr[y0:y1, x0:x1]
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (64, 64), interpolation=cv2.INTER_AREA)
            g = cv2.equalizeHist(g)                       # lighting invariance
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            mag = np.sqrt(gx * gx + gy * gy)
            ang = (np.arctan2(gy, gx) + np.pi) * (180.0 / np.pi)   # 0..360
            bins = 8
            vec = []
            cells = 4
            step = 64 // cells
            for cy in range(cells):
                for cx in range(cells):
                    m = mag[cy * step:(cy + 1) * step, cx * step:(cx + 1) * step]
                    a = ang[cy * step:(cy + 1) * step, cx * step:(cx + 1) * step]
                    hist, _ = np.histogram(a, bins=bins, range=(0, 360),
                                           weights=m)
                    vec.extend(float(v) for v in hist)
            if len(vec) < self._dim:
                vec.extend([0.0] * (self._dim - len(vec)))
            vec = vec[: self._dim]
            m = sum(vec) / len(vec)
            vec = [v - m for v in vec]
            sd = math.sqrt(sum(v * v for v in vec) / len(vec)) or 1.0
            return _norm([v / sd for v in vec])
        except Exception:
            return None

    def _encode_geometry(self, pts) -> list[float] | None:
        """Normalised inter-landmark distances → pose-invariant descriptor."""
        try:
            n = len(pts)
            idx = [int(i * (n - 1) / 39) for i in range(40)]
            sel = [pts[i] for i in idx]
            cy = sum(p[0] for p in sel) / len(sel)
            cx = sum(p[1] for p in sel) / len(sel)
            scale = math.sqrt(
                sum((p[0] - cy) ** 2 + (p[1] - cx) ** 2 for p in sel) / len(sel)
            ) or 1.0
            vec = []
            for i in range(len(sel)):
                for j in range(i + 1, len(sel)):
                    if len(vec) >= self._dim:
                        break
                    d = math.dist(sel[i], sel[j]) / scale
                    vec.append(d)
                if len(vec) >= self._dim:
                    break
            vec = (vec + [0.0] * self._dim)[: self._dim]
            # Raw distance vectors are dominated by a large common component,
            # which makes every face look ~0.999 similar. Removing the mean
            # (and dividing by the spread) keeps only what actually differs
            # between faces, restoring discrimination.
            m = sum(vec) / len(vec)
            vec = [v - m for v in vec]
            sd = math.sqrt(sum(v * v for v in vec) / len(vec)) or 1.0
            vec = [v / sd for v in vec]
            return _norm(vec)
        except Exception:
            return None

    # ── enrolment ───────────────────────────────────────────────────────
    def add(self, name: str, vector: list[float]) -> bool:
        """Store one face print under `name`."""
        self._ensure()
        name = (name or "").strip()
        if not name or not vector or len(vector) != self._dim:
            return False
        try:
            with self._lock:
                if self._backend == "milvus" and self._client is not None:
                    self._next_id += 1
                    self._client.insert(
                        collection_name=_COLLECTION,
                        data=[{"id": self._next_id,
                               "vector": vector,
                               "name": name,
                               "enc": self._last_kind}],
                    )
                    try:
                        self._client.load_collection(collection_name=_COLLECTION)
                    except Exception:
                        pass
                else:
                    self._json.append({"name": name, "vector": vector,
                                       "enc": self._last_kind})
                    self._save_json()
            return True
        except Exception as e:
            print(f"[FaceDB] add failed: {e}")
            return False

    def search(self, vector: list[float]) -> tuple[str | None, float]:
        """Nearest face print → (name, similarity 0-1) or (None, score)."""
        self._ensure()
        if not vector or len(vector) != self._dim:
            return None, 0.0
        thr = {"sface": _SFACE_THRESHOLD,
               "geom": _GEOM_THRESHOLD,
               "appear": _APPEAR_THRESHOLD}.get(self._last_kind, _GEOM_THRESHOLD)
        try:
            with self._lock:
                if self._backend == "milvus" and self._client is not None:
                    res = self._client.search(
                        collection_name=_COLLECTION,
                        data=[vector],
                        limit=3,
                        output_fields=["name", "enc"],
                        filter=f'enc == "{self._last_kind}"',
                    )
                    hits = res[0] if res else []
                    if not hits:
                        return None, 0.0
                    best = hits[0]
                    score = float(best.get("distance", 0.0))   # COSINE
                    name = (best.get("entity") or {}).get("name")
                    return (name, score) if score >= thr else (None, score)

                # JSON brute force
                best_name, best_score = None, -1.0
                for row in self._json:
                    v = row.get("vector")
                    if not v or len(v) != len(vector):
                        continue
                    if row.get("enc", "geom") != self._last_kind:
                        continue
                    s = _cosine(vector, v)
                    if s > best_score:
                        best_name, best_score = row.get("name"), s
                if best_score < 0:
                    return None, 0.0
                return ((best_name, best_score) if best_score >= thr
                        else (None, best_score))
        except Exception as e:
            print(f"[FaceDB] search failed: {e}")
            return None, 0.0

    def names(self) -> list[str]:
        """Every enrolled person."""
        try:
            if self._backend == "milvus" and self._client is not None:
                rows = self._client.query(
                    collection_name=_COLLECTION,
                    filter="id >= 0",
                    output_fields=["name"],
                    limit=1000,
                )
                return sorted({r.get("name") for r in rows if r.get("name")})
            return sorted({r.get("name") for r in self._json if r.get("name")})
        except Exception:
            return []

    def forget(self, name: str) -> int:
        """Delete every face print belonging to `name`."""
        self._ensure()
        name = (name or "").strip()
        if not name:
            return 0
        try:
            with self._lock:
                if self._backend == "milvus" and self._client is not None:
                    safe = name.replace('"', '\\"')
                    before = len(self._client.query(
                        collection_name=_COLLECTION,
                        filter=f'name == "{safe}"',
                        output_fields=["id"], limit=1000))
                    self._client.delete(
                        collection_name=_COLLECTION,
                        filter=f'name == "{safe}"',
                    )
                    return before
                n = len(self._json)
                self._json = [r for r in self._json if r.get("name") != name]
                self._save_json()
                return n - len(self._json)
        except Exception as e:
            print(f"[FaceDB] forget failed: {e}")
            return 0

    @property
    def encoder(self) -> str:
        self._ensure()
        return self._encoder

    @property
    def backend(self) -> str:
        self._ensure()
        return self._backend


_db: FaceDB | None = None
_db_lock = threading.Lock()


def get_db() -> FaceDB:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = FaceDB()
    return _db
