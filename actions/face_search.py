"""
Find an enrolled person inside a video the user supplies.

Scope — deliberately limited:

  * Works on media the USER provides: a local file, or a URL they paste
    (their own upload, their own channel, a clip they were sent).
  * Matches ONLY against people already enrolled in the local face database,
    i.e. faces someone deliberately taught the system.
  * Answers "where in this video does <known person> appear?" and returns
    timestamps.

What this module intentionally does NOT do:

  * It does not crawl or scrape TikTok / YouTube / Instagram searching for a
    face. Identifying strangers by scanning social platforms is what got
    Clearview AI banned and fined under GDPR; it also breaks those platforms'
    terms of service, and in Ukraine/EU biometric data is a special category
    that requires the person's consent. Building a stalking tool is out of
    scope here.
  * It does not do internet-wide reverse face search.

Typical honest uses: locating yourself in your own footage, finding which
part of a long recording features a given (consenting) person, checking
where your own face appears in a video you already have.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_MAX_FRAMES = 900          # hard cap so a long video can't run forever
_DEFAULT_EVERY = 1.0       # sample one frame per second


def _hms(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _is_url(src: str) -> bool:
    return src.strip().lower().startswith(("http://", "https://"))


def _resolve_media(src: str) -> tuple[str | None, str | None, str]:
    """Return (local_path, tempdir_to_clean, note).

    A URL is downloaded with yt-dlp when available. yt-dlp is what makes a
    remote link playable locally; without it we ask for a local file.
    """
    src = (src or "").strip().strip('"').strip("'")
    if not src:
        return None, None, "No video given."

    if not _is_url(src):
        p = Path(os.path.expanduser(src))
        if not p.is_file():
            return None, None, f"File not found: {p}"
        return str(p), None, ""

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return None, None, (
            "To scan a link I need yt-dlp. Install it with "
            "`pip install yt-dlp`, or download the clip and give me the file path."
        )

    tmp = tempfile.mkdtemp(prefix="jarvis_scan_")
    out = os.path.join(tmp, "clip.%(ext)s")
    try:
        import yt_dlp
        opts = {
            "outtmpl": out,
            "format": "mp4[height<=720]/best[height<=720]/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "max_filesize": 300 * 1024 * 1024,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([src])
        files = [f for f in Path(tmp).iterdir() if f.is_file()]
        if not files:
            shutil.rmtree(tmp, ignore_errors=True)
            return None, None, "Could not download that link."
        return str(max(files, key=lambda f: f.stat().st_size)), tmp, ""
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, None, f"Download failed: {e}"


def scan_video(source: str, every_seconds: float = _DEFAULT_EVERY,
               max_seconds: float = 0.0) -> dict:
    """Scan `source` for faces already enrolled in the local database.

    Returns {"ok", "message", "hits": [{name, score, at, at_hms}], "checked"}.
    """
    try:
        import cv2
    except Exception as e:
        return {"ok": False, "message": f"OpenCV unavailable: {e}", "hits": []}

    from actions.face_db import get_db
    from actions.face_id import get_engine

    db = get_db()
    known = db.names()
    if not known:
        return {
            "ok": False,
            "hits": [],
            "message": ("My face database is empty, so there is nobody to look "
                        "for. Enrol someone first: point the camera at them and "
                        "say 'remember this face, this is <name>'."),
        }

    path, tmpdir, note = _resolve_media(source)
    if not path:
        return {"ok": False, "message": note, "hits": []}

    engine = get_engine()
    engine._ensure()
    hits: list[dict] = []
    checked = 0
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return {"ok": False, "message": "Could not open that video.",
                    "hits": []}
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = (total / fps) if fps > 0 else 0.0
        if max_seconds and duration:
            duration = min(duration, max_seconds)

        step = max(1, int(fps * max(0.2, every_seconds)))
        best_per_person: dict[str, dict] = {}
        idx = 0
        while checked < _MAX_FRAMES:
            ok = cap.grab()
            if not ok:
                break
            if idx % step:
                idx += 1
                continue
            ok, frame = cap.retrieve()
            idx += 1
            if not ok or frame is None:
                continue
            t_sec = idx / fps if fps else 0.0
            if max_seconds and t_sec > max_seconds:
                break
            checked += 1

            h, w = frame.shape[:2]
            if w > 640:
                sc = 640.0 / w
                frame = cv2.resize(frame, (640, max(1, int(h * sc))),
                                   interpolation=cv2.INTER_AREA)
            with engine._lock:
                boxes = engine._detect_boxes(frame)
            for (x, y, bw, bh) in sorted(
                    boxes, key=lambda b: b[2] * b[3], reverse=True)[:3]:
                vec = db.encode(frame, box=(x, y, bw, bh))
                if not vec:
                    continue
                name, sim = db.search(vec)
                if not name:
                    continue
                prev = best_per_person.get(name)
                if prev is None or sim > prev["score"]:
                    best_per_person[name] = {
                        "name": name,
                        "score": round(float(sim) * 100, 1),
                        "at": round(t_sec, 1),
                        "at_hms": _hms(t_sec),
                    }
                hits.append({
                    "name": name,
                    "score": round(float(sim) * 100, 1),
                    "at": round(t_sec, 1),
                    "at_hms": _hms(t_sec),
                })
        cap.release()

        if not best_per_person:
            return {
                "ok": True, "hits": [], "checked": checked,
                "message": (f"Scanned {checked} frame(s). None of the people I "
                            f"know ({', '.join(known)}) appear in this video."),
            }

        summary = "; ".join(
            f"{v['name']} first clear match at {v['at_hms']} ({v['score']:.0f}%)"
            for v in best_per_person.values()
        )
        return {
            "ok": True,
            "hits": sorted(hits, key=lambda h: h["at"]),
            "best": list(best_per_person.values()),
            "checked": checked,
            "message": f"Scanned {checked} frame(s). Found: {summary}.",
        }
    except Exception as e:
        return {"ok": False, "message": f"Scan failed: {e}", "hits": []}
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def scan_image(source: str) -> dict:
    """Same idea for a single photo the user supplies."""
    try:
        import cv2
    except Exception as e:
        return {"ok": False, "message": f"OpenCV unavailable: {e}", "hits": []}
    from actions.face_db import get_db
    from actions.face_id import get_engine

    db = get_db()
    if not db.names():
        return {"ok": False, "hits": [],
                "message": "My face database is empty — nobody to look for yet."}

    path, tmpdir, note = _resolve_media(source)
    if not path:
        return {"ok": False, "message": note, "hits": []}
    try:
        img = cv2.imread(path)
        if img is None:
            return {"ok": False, "message": "Could not read that image.",
                    "hits": []}
        engine = get_engine()
        engine._ensure()
        with engine._lock:
            boxes = engine._detect_boxes(img)
        hits = []
        for (x, y, w, h) in boxes[:5]:
            vec = db.encode(img, box=(x, y, w, h))
            if not vec:
                continue
            name, sim = db.search(vec)
            if name:
                hits.append({"name": name, "score": round(float(sim) * 100, 1)})
        if not hits:
            return {"ok": True, "hits": [],
                    "message": "No familiar face in that image."}
        return {"ok": True, "hits": hits,
                "message": "Found: " + ", ".join(
                    f"{h['name']} ({h['score']:.0f}%)" for h in hits)}
    except Exception as e:
        return {"ok": False, "message": f"Scan failed: {e}", "hits": []}
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
