# 🔒 EDIT WEB — phone face-login (synced with MARK L)

A **phone-first** web app that unlocks with **your face** (a single enrolled owner
face — Face ID style) and keeps its **settings & memory in sync** with the main
MARK L project. It reuses MARK L's face engine (`actions/face_vault.py`) so
detection + matching are identical and tested.

> Lives in this repo under `edit_web/`. The path logic is layout-agnostic, so you
> can also run it as a **sibling folder** if you prefer — it auto-finds MARK L.

## ✨ What it does

| Feature | Description |
|---|---|
| 🔒 Owner face setup | First visit registers **your** face (only this face unlocks). Stored locally only. |
| 🔓 Face login | Look at the camera → matched against the owner face → unlocked. |
| 🔢 PIN backup | Optional recovery PIN in case the camera can't be used. |
| 🔗 Settings sync | Reads/writes the **same** `config/api_keys.json` as MARK L → always consistent. |
| 🧠 Memory sync | Reads/writes the **same** `memory/long_term.json` → shared across both versions. |
| ⬇️⬆️ Pull / Push | Optional local snapshot copy to/from the main project. |

## 🚀 Run

```bash
cd edit_web
pip install -r requirements.txt
python app.py
```

It prints a LAN URL like `http://192.168.x.x:8050` — open it **on your phone**
(same Wi‑Fi). First visit → enroll your face. Then log in by face.

> EDIT WEB borrows the face engine from the parent MARK L project, so keep them
> together (or set `EDITWEB_MAIN=/path/to/mark-l`). Port override: `EDITWEB_PORT=8050`.

## 🗂️ Structure

```
edit_web/
├── app.py          # FastAPI app — setup / login / dashboard pages + API
├── face_auth.py    # owner-face enroll + verify (reuses MARK L face engine)
├── paths.py        # locate the MARK L project (nested or sibling)
├── sync.py         # settings & memory sync (format-stable live-link)
├── requirements.txt
└── data/           # owner.json + owner.jpg (your enrolled face — local only, gitignored)
```

## ⚠️ Security note

Perceptual-hash face matching is a **convenience lock**, not cryptographic auth — a
high-quality photo/video of you could fool it. Set a PIN as backup, and don't expose
the port to the public internet. For real security add liveness detection.

## 🔒 Privacy

Everything is local: your face image, the hash, the settings and memory never leave
your machine. Nothing is uploaded and no one is ever identified or looked up.
