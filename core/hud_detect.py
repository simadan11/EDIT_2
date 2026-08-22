"""
core/hud_detect.py — тактическая vision-детекция для камеры телефона.

Функция перенесена из удалённого legacy-дашборда в ядро-сопровождение:
запрос к Gemini-модели с JSON-контракт на объекты (люди/транспорт/объекты,
bounding box, силуэт и позы людей). Используется голосовым десктопом
(main.py) при работе с [PHONE CAMERA].

Без ключа gemini_api_key — честная RuntimeError.
"""

from __future__ import annotations

import json

HUD_MODEL = "gemini-2.5-flash"
MAX_FRAME_BYTES = 8 * 1024 * 1024   # decoded JPEG cap

HUD_PROMPT = (
    "You are a tactical augmented-reality vision system. "
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

_POSE_JOINTS = (
    "head", "neck", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_wrist", "r_wrist", "pelvis", "l_hip", "r_hip",
    "l_knee", "r_knee", "l_ankle", "r_ankle",
)


def _get_gemini_key():
    try:
        from core.model_router import _get_gemini_key as _k
        return _k()
    except Exception:
        return None


def _norm_pt(p):
    """[y, x] (или {"y":..,"x":..}) → (y, x) float 0-1000, иначе None."""
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


def lumen_hud_detect(image_bytes: bytes) -> list[dict]:
    """Blocking Gemini call → normalized list of HUD detections. Raises on failure."""
    from google import genai as _g
    from google.genai import types as _gt

    key = _get_gemini_key()
    if not key:
        raise RuntimeError("gemini_api_key not configured")
    client = _g.Client(api_key=key)
    resp = client.models.generate_content(
        model=HUD_MODEL,
        contents=[
            _gt.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            HUD_PROMPT,
        ],
        config=_gt.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    data = json.loads((resp.text or "").strip() or "[]")
    if isinstance(data, dict):           # модель обернула массив в объект
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
