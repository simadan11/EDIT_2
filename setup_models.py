#!/usr/bin/env python3
"""
Download the vision models EDITH needs, with verification.

Run once:

    python setup_models.py

Without these files the HUD still works, but degrades:
  * no `pose_landmarker.task`     → no skeleton (falls back to coarse boxes)
  * no `face_landmarker.task`     → no 478-point face mesh
  * no `face_recognition_sface`   → weak identification (geometry only)

Every file is validated after download (size + magic bytes), because a
truncated or HTML error page saved as a ".onnx" is exactly what makes the
native OpenCV/MediaPipe loaders crash with 0xC0000005 later on.
"""

from __future__ import annotations

import shutil
import sys
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config"

MODELS = {
    "pose_landmarker_lite.task": {
        "why": "body skeleton tracking",
        "min_size": 1_000_000,
        "urls": [
            "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
            "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
            "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
            "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
        ],
    },
    "face_landmarker.task": {
        "why": "478-point face mesh (the wireframe mask)",
        "min_size": 1_000_000,
        "urls": [
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task",
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task",
        ],
    },
    "face_recognition_sface.onnx": {
        "why": "face prints for reliable name recognition",
        "min_size": 30_000,
        "urls": [
            "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
            "face_recognition_sface/face_recognition_sface_2021dec.onnx",
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
            "models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        ],
    },
    "face_detection_yunet.onnx": {
        "why": "accurate face detection (profiles, close-ups)",
        "min_size": 50_000,
        "urls": [
            "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
            "face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
            "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        ],
    },
}


def _looks_valid(data: bytes, min_size: int) -> tuple[bool, str]:
    """Reject HTML error pages, LFS pointers and truncated files."""
    if len(data) < min_size:
        return False, f"too small ({len(data)} bytes)"
    head = data[:200].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"{", b"version https://git-lfs")):
        return False, "server returned a web page, not a model"
    return True, ""


def fetch(name: str, spec: dict) -> bool:
    dest = CONFIG / name
    if dest.exists() and dest.stat().st_size >= spec["min_size"]:
        print(f"  ✔ {name} — already present "
              f"({dest.stat().st_size / 1e6:.1f} MB)")
        return True

    for url in spec["urls"]:
        try:
            print(f"  ↓ {name} … ", end="", flush=True)
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (JARVIS setup)"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310
                data = r.read()
            ok, why = _looks_valid(data, spec["min_size"])
            if not ok:
                print(f"rejected: {why}")
                continue
            CONFIG.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            print(f"OK ({len(data) / 1e6:.1f} MB)")
            return True
        except Exception as e:
            print(f"failed ({type(e).__name__})")
    return False


def main() -> int:
    print("EDITH vision models →", CONFIG)
    print()
    missing = []
    for name, spec in MODELS.items():
        print(f"{spec['why']}:")
        if not fetch(name, spec):
            missing.append((name, spec))
        print()

    if not missing:
        print("All models ready. Restart JARVIS to enable the full HUD.")
        return 0

    print("=" * 66)
    print("Could not download:")
    for name, spec in missing:
        print(f"  • {name}  ({spec['why']})")
        print(f"    {spec['urls'][0]}")
    print()
    print(f"Download them manually in a browser and drop the files into:")
    print(f"  {CONFIG}")
    print()
    print("The app still runs without them — it just falls back to the")
    print("simpler detectors instead of the full mesh/skeleton.")
    print("=" * 66)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
