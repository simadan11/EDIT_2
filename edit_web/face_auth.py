"""
face_auth.py — owner-face enrollment + verification for EDIT WEB login.

Reuses the SAME face engine as the main MARK L project (resolved by paths.py, so
it works whether EDIT WEB is nested inside the project or a sibling folder) so
the detection + perceptual-hash logic is identical and tested. No external
service, no network upload — matching happens entirely on this machine.

Security note: perceptual-hash face matching is a convenience "face unlock", not
cryptographic auth. A high-quality photo/video of the owner could fool it. For
real security pair it with a PIN (the app supports a setup PIN) or add liveness.
"""

from __future__ import annotations

import json
import sys
import time
import secrets
from pathlib import Path

import paths

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OWNER_INDEX = DATA_DIR / "owner.json"      # {hash(hex), enrolled_at, pin_hash?}
OWNER_IMAGE = DATA_DIR / "owner.jpg"

# Hamming distance (over 64-bit pHash) at/below which a captured face counts as
# the owner. Lower = stricter/safer but more false rejects; higher = looser.
LOGIN_THRESHOLD = 18

# ── import the shared face engine from the main MARK L project ────────────────
_engine = None


def _eng():
    global _engine
    if _engine is not None:
        return _engine
    main = paths.main_project_dir()
    if str(main) not in sys.path:
        sys.path.insert(0, str(main))
    try:
        from actions import face_vault as fv            # noqa
        if not (hasattr(fv, "detect_faces") and hasattr(fv, "phash_of_crop")):
            raise RuntimeError("face_vault too old (needs detect_faces/phash_of_crop)")
        _engine = fv
        return fv
    except Exception as e:
        print(f"[face_auth] shared engine unavailable (looked in {main}): {e}")
        _engine = False
        return None


def available() -> bool:
    fv = _eng()
    return bool(fv) and fv._get_cascade() is not None


# ── enrollment ────────────────────────────────────────────────────────────────

def enrolled() -> bool:
    return OWNER_INDEX.exists()


def owner_image_path() -> Path | None:
    return OWNER_IMAGE if OWNER_IMAGE.exists() else None


def enroll(image_bytes: bytes, pin: str | None = None) -> dict:
    """Register the owner's face from one frame. Must contain exactly one face."""
    fv = _eng()
    if not fv:
        return {"ok": False, "error": "Face engine unavailable (needs opencv-python in MARK L)"}
    faces = fv.detect_faces(image_bytes)
    if not faces:
        return {"ok": False, "error": "No face detected — center your face and try again"}
    if len(faces) > 1:
        return {"ok": False, "error": f"{len(faces)} faces found — enroll with ONLY your face in frame"}
    crop = faces[0]["crop"]
    h = fv.phash_of_crop(crop)
    if h is None:
        return {"ok": False, "error": "Could not read face — try better lighting"}
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return {"ok": False, "error": "Image encode failed"}
        OWNER_IMAGE.write_bytes(buf.tobytes())
    except Exception as e:
        return {"ok": False, "error": f"Image save failed: {e}"}
    data = {"hash": format(h, "x"), "enrolled_at": time.time()}
    if pin:
        data["pin_hash"] = _hash_pin(pin)
    OWNER_INDEX.write_text(json.dumps(data), encoding="utf-8")
    return {"ok": True, "enrolled_at": data["enrolled_at"]}


def reenroll(image_bytes: bytes, pin: str | None = None) -> dict:
    """Replace the owner face (re-enrollment)."""
    OWNER_INDEX.unlink(missing_ok=True)
    return enroll(image_bytes, pin)


# ── verification ──────────────────────────────────────────────────────────────

def verify(image_bytes: bytes, threshold: int = LOGIN_THRESHOLD) -> dict:
    """Match a login attempt against the enrolled owner. Returns match + distance."""
    if not enrolled():
        return {"ok": False, "match": False, "error": "No owner enrolled yet"}
    fv = _eng()
    if not fv:
        return {"ok": False, "match": False, "error": "Face engine unavailable"}
    try:
        owner_hash = int(json.loads(OWNER_INDEX.read_text())["hash"], 16)
    except Exception:
        return {"ok": False, "match": False, "error": "Owner record corrupt — re-enroll"}
    faces = fv.detect_faces(image_bytes)
    if not faces:
        return {"ok": True, "match": False, "distance": None, "reason": "no face"}
    best_dist = 10_000
    best_i = -1
    for i, f in enumerate(faces):
        h = fv.phash_of_crop(f["crop"])
        if h is None:
            continue
        d = fv._hamming(h, owner_hash)
        if d < best_dist:
            best_dist, best_i = d, i
    match = best_dist <= threshold
    return {
        "ok": True,
        "match": match,
        "distance": best_dist if best_i >= 0 else None,
        "threshold": threshold,
        "faces": len(faces),
    }


# ── optional PIN (fallback / second factor) ───────────────────────────────────

def _hash_pin(pin: str) -> str:
    import hashlib
    return hashlib.sha256(("EDITWEB-PIN|" + (pin or "")).encode()).hexdigest()


def has_pin() -> bool:
    try:
        return bool(json.loads(OWNER_INDEX.read_text()).get("pin_hash"))
    except Exception:
        return False


def check_pin(pin: str) -> bool:
    try:
        return json.loads(OWNER_INDEX.read_text()).get("pin_hash") == _hash_pin(pin)
    except Exception:
        return False


# ── session tokens (in-memory) ────────────────────────────────────────────────

_tokens: set[str] = set()


def new_session() -> str:
    t = secrets.token_urlsafe(32)
    _tokens.add(t)
    return t


def valid_session(tok: str | None) -> bool:
    return bool(tok) and tok in _tokens


def revoke_session(tok: str) -> None:
    _tokens.discard(tok)


def status() -> dict:
    return {
        "available": available(),
        "enrolled": enrolled(),
        "has_pin": has_pin(),
    }
