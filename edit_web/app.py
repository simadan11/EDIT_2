"""
app.py — EDIT WEB: a phone-first web app with owner-face login, synced with the
main MARK L project (settings + memory).

Run:
    pip install -r requirements.txt
    python app.py
Then open the printed LAN URL on your phone.

First visit → enroll your face (the single owner). Afterwards → log in by
showing your face. The dashboard reads/writes the SAME settings & memory as the
desktop app (../ai22), so the two versions stay in sync.
"""

from __future__ import annotations

import base64
import json
import socket
import time
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
import uvicorn

import face_auth
import sync

HERE = Path(__file__).resolve().parent
PORT = int(__import__("os").environ.get("EDITWEB_PORT", "8050"))


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5); s.connect((probe, 80))
            ip = s.getsockname()[0]; s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass
    return "127.0.0.1"


def _decode_frame(body: dict) -> bytes | None:
    b64 = str(body.get("frame") or "").strip()
    if not b64:
        return None
    if "," in b64 and b64[:32].lower().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        return base64.b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception:
        return None


def _session_token(request: Request) -> str | None:
    return request.cookies.get("ew_session")


def _require_auth(request: Request):
    tok = _session_token(request)
    if not face_auth.valid_session(tok):
        return RedirectResponse("/login", status_code=303)
    return None


# ── pages (mobile-first HTML) ─────────────────────────────────────────────────

_CSS = """
:root{--bg:#07090f;--surface:rgba(255,255,255,.05);--border:rgba(255,255,255,.09);
--accent:#6366f1;--text:#e7ecf4;--muted:#7b879b;--green:#22c55e;--red:#ef4444}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);font-family:-apple-system,Segoe UI,Roboto,sans-serif;
min-height:100dvh;display:flex;flex-direction:column;align-items:center;padding:24px 18px}
.wrap{width:100%;max-width:440px;display:flex;flex-direction:column;gap:18px}
h1{font-size:20px;letter-spacing:1px;font-weight:800;text-align:center}
.sub{font-size:13px;color:var(--muted);text-align:center;line-height:1.5}
video{width:100%;max-width:360px;aspect-ratio:3/4;object-fit:cover;border-radius:18px;
border:1px solid var(--border);background:#000;transform:scaleX(-1)}
.cam-wrap{display:flex;flex-direction:column;align-items:center;gap:12px}
.hint{font-size:12px;color:var(--muted);min-height:18px;text-align:center}
.hint.err{color:var(--red)}.hint.ok{color:var(--green)}
button{width:100%;padding:15px;border:none;border-radius:14px;font-size:14px;font-weight:800;
letter-spacing:1px;cursor:pointer;background:var(--accent);color:#fff;transition:transform .1s,opacity .15s}
button:active{transform:scale(.97)}button:disabled{opacity:.45}
button.secondary{background:var(--surface);border:1px solid var(--border);color:var(--text)}
.row{display:flex;gap:10px}.row button{flex:1}
input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--border);
background:var(--surface);color:var(--text);font-size:15px;outline:none}
input:focus{border-color:var(--accent)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px}
.card h3{font-size:12px;letter-spacing:1.5px;color:var(--accent);margin-bottom:12px;text-transform:uppercase}
.kv{display:flex;justify-content:space-between;font-size:13px;padding:6px 0;border-bottom:1px solid var(--border)}
.kv:last-child{border:none}.kv span:first-child{color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.on{background:var(--green)}.dot.off{background:var(--red)}.dot.mute{background:var(--muted)}
a{color:var(--accent);text-decoration:none}
.small{font-size:11px;color:var(--muted);text-align:center;line-height:1.5}
.brand{font-size:11px;letter-spacing:3px;color:var(--accent);text-align:center;font-weight:800}
"""

_CAM_JS = """
let v,keep;
async function cam(){v=document.getElementById('v');try{v.srcObject=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:720}},audio:false});await v.play();keep=true;}catch(e){setHint('Camera blocked — allow camera for this site','err');}}
function grab(Q=.82,MAX=720){if(!v||!v.videoWidth)return null;const sc=Math.min(1,MAX/Math.max(v.videoWidth,v.videoHeight));const c=document.createElement('canvas');c.width=Math.round(v.videoWidth*sc);c.height=Math.round(v.videoHeight*sc);c.getContext('2d').drawImage(v,0,0,c.width,c.height);return c.toDataURL('image/jpeg',Q).split(',')[1];}
function setHint(t,cls){const h=document.getElementById('hint');h.textContent=t||'';h.className='hint '+(cls||'');}
async function post(url,payload){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});return r.json();}
"""

_SETUP_HTML = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>EDIT WEB · Setup</title><style>{_CSS}</style></head><body>
<div class="wrap">
  <div class="brand">EDIT WEB</div>
  <h1>🔒 Owner Face Setup</h1>
  <div class="sub">Register <b>your</b> face. Only this face will unlock EDIT WEB. Stored on this machine only.</div>
  <div class="cam-wrap"><video id="v" autoplay playsinline muted></video>
    <div class="hint" id="hint">Center your face, good lighting, then tap ENROLL</div></div>
  <input id="pin" type="password" inputmode="numeric" placeholder="Optional recovery PIN (4-8 digits)">
  <button id="go">ENROLL MY FACE</button>
  <div class="small">Tip: face unlock is a convenience lock, not high security. Add a PIN as a backup.</div>
</div>
<script>{_CAM_JS}
cam();
document.getElementById('go').onclick=async()=>{{setHint('Enrolling…');const f=grab();if(!f){{setHint('No camera signal','err');return;}}const pin=document.getElementById('pin').value.trim();
const d=await post('/api/enroll',{{frame:f,pin:pin||null}});if(d.ok){{setHint('✓ Face registered — redirecting…','ok');setTimeout(()=>location='/login',700);}}else setHint(d.error||'Enroll failed','err');}};
</script></body></html>"""

_LOGIN_HTML = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>EDIT WEB · Login</title><style>{_CSS}</style></head><body>
<div class="wrap">
  <div class="brand">EDIT WEB</div>
  <h1>🔓 Face Login</h1>
  <div class="sub">Look at the camera to unlock.</div>
  <div class="cam-wrap"><video id="v" autoplay playsinline muted></video>
    <div class="hint" id="hint"></div></div>
  <button id="go">UNLOCK WITH FACE</button>
  <div id="pinrow" class="card" style="display:none">
    <h3>PIN backup</h3>
    <input id="pin" type="password" inputmode="numeric" placeholder="Recovery PIN">
    <div style="height:10px"></div>
    <button class="secondary" id="pinbtn">UNLOCK WITH PIN</button>
  </div>
  <a href="/setup" class="small">Re-enroll owner face</a>
</div>
<script>{_CAM_JS}
cam();
let busy=false;
document.getElementById('go').onclick=async()=>{{if(busy)return;busy=true;setHint('Scanning face…');const f=grab();if(!f){{setHint('No camera signal','err');busy=false;return;}}
const d=await post('/api/face-login',{{frame:f}});busy=false;
if(d.match){{setHint('✓ Welcome back','ok');setTimeout(()=>location='/app',500);}}
else{{setHint(d.reason==='no face'?'No face seen — try again':('Not recognized'+(d.distance!=null?' (distance '+d.distance+')':'')),'err');}}}};
// show PIN row only if a PIN was set during enroll
fetch('/api/status').then(r=>r.json()).then(s=>{{if(s.has_pin)document.getElementById('pinrow').style.display='block';}}).catch(()=>{{}});
document.getElementById('pinbtn').onclick=async()=>{{const pin=document.getElementById('pin').value.trim();const d=await post('/api/pin-login',{{pin}});if(d.ok)location='/app';else setHint('Wrong PIN','err');}};
</script></body></html>"""


def _app_html() -> str:
    s = sync.status()
    st = s["settings"]
    ms = s["memory_summary"]
    linked = s["linked"]
    link_dot = "on" if linked else "off"
    face_dot = "on" if face_auth.available() else "off"
    cfg_mtime = s["main"]["config_mtime"]
    mem_mtime = s["main"]["memory_mtime"]

    def fmt(t):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "—"

    mem_rows = "".join(
        f'<div class="kv"><span>{k}</span><span>{v}</span></div>' for k, v in ms.items()
    )
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>EDIT WEB</title><style>{_CSS}</style></head><body>
<div class="wrap">
  <div class="brand">EDIT WEB · UNLOCKED</div>
  <h1>👋 Welcome, {st.get('user_name') or 'owner'}</h1>
  <div class="card">
    <h3>Sync with MARK L</h3>
    <div class="kv"><span>Status</span><span><span class="dot {link_dot}"></span>{'live-linked' if linked else 'main project not found'}</span></div>
    <div class="kv"><span>Main folder</span><span style="font-size:11px">{s['main']['dir']}</span></div>
    <div class="kv"><span>Settings updated</span><span>{fmt(cfg_mtime)}</span></div>
    <div class="kv"><span>Memory updated</span><span>{fmt(mem_mtime)}</span></div>
    <div style="height:12px"></div>
    <div class="row">
      <button class="secondary" onclick="doSync('pull')">PULL</button>
      <button class="secondary" onclick="doSync('push')">PUSH</button>
      <button class="secondary" onclick="location.reload()">⟳</button>
    </div>
    <div class="small" style="margin-top:8px">Live-linked = both versions read/write the same files (always in sync). Pull/Push copy a local snapshot.</div>
  </div>
  <div class="card">
    <h3>Settings (shared)</h3>
    <div class="kv"><span>Assistant name</span><span>{st.get('assistant_name') or '—'}</span></div>
    <div class="kv"><span>Your name</span><span>{st.get('user_name') or '—'}</span></div>
    <div class="kv"><span>UI color</span><span>{st.get('ui_color') or '—'}</span></div>
    <div class="kv"><span>Morning brief</span><span>{'on' if st.get('morning_brief_enabled') else 'off'}</span></div>
    <div class="kv"><span>API key</span><span>{'set' if st.get('__has_key') else 'missing'}</span></div>
    <div style="height:12px"></div>
    <input id="uname" placeholder="Your name" value="{st.get('user_name') or ''}">
    <div style="height:10px"></div>
    <input id="aname" placeholder="Assistant name" value="{st.get('assistant_name') or ''}">
    <div style="height:10px"></div>
    <button class="secondary" onclick="saveSettings()">SAVE SETTINGS</button>
  </div>
  <div class="card">
    <h3>Memory (shared)</h3>
    {mem_rows or '<div class="small">empty</div>'}
  </div>
  <div class="row">
    <button class="secondary" onclick="location='/setup'">Re-enroll face</button>
    <button class="secondary" onclick="fetch('/api/logout',{{method:'POST'}}).then(()=>location='/login')">LOGOUT</button>
  </div>
  <div class="small">Face engine: <span class="dot {face_dot}"></span>{'ready' if face_auth.available() else 'unavailable'}</div>
</div>
<script>
async function doSync(dir){{const r=await fetch('/api/sync/'+dir,{{method:'POST'}});const d=await r.json();alert(dir.toUpperCase()+': '+(d.copied||0)+' file(s) copied');location.reload();}}
async function saveSettings(){{const u=document.getElementById('uname').value.trim();const a=document.getElementById('aname').value.trim();
const r=await fetch('/api/settings',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{user_name:u,assistant_name:a}})}});const d=await r.json();alert(d.ok?'Saved — synced to MARK L':('Error: '+(d.error||'')));}}
</script></body></html>"""


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="EDIT WEB", docs_url=None, redoc_url=None)


@app.get("/")
async def root(request: Request):
    if not face_auth.enrolled():
        return RedirectResponse("/setup", status_code=303)
    if face_auth.valid_session(_session_token(request)):
        return RedirectResponse("/app", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return HTMLResponse(_SETUP_HTML)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    if not face_auth.enrolled():
        return RedirectResponse("/setup", status_code=303)
    return HTMLResponse(_LOGIN_HTML)


@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    redir = _require_auth(request)
    if redir:
        return redir
    return HTMLResponse(_app_html())


# ── auth API ──────────────────────────────────────────────────────────────────

@app.post("/api/enroll")
async def api_enroll(request: Request):
    body = await request.json()
    frame = _decode_frame(body)
    if not frame:
        return JSONResponse({"ok": False, "error": "No frame received"}, status_code=400)
    pin = str(body.get("pin") or "")[:8] or None
    res = face_auth.enroll(frame, pin) if not face_auth.enrolled() else face_auth.reenroll(frame, pin)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/face-login")
async def api_face_login(request: Request):
    body = await request.json()
    frame = _decode_frame(body)
    if not frame:
        return JSONResponse({"ok": False, "match": False, "error": "No frame"}, status_code=400)
    res = face_auth.verify(frame)
    if res.get("match"):
        tok = face_auth.new_session()
        resp = JSONResponse({"ok": True, "match": True})
        resp.set_cookie("ew_session", tok, httponly=True, samesite="lax", max_age=7 * 86400)
        return resp
    return JSONResponse(res)


@app.post("/api/pin-login")
async def api_pin_login(request: Request):
    body = await request.json()
    if face_auth.has_pin() and face_auth.check_pin(str(body.get("pin") or "")):
        tok = face_auth.new_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie("ew_session", tok, httponly=True, samesite="lax", max_age=7 * 86400)
        return resp
    return JSONResponse({"ok": False}, status_code=401)


@app.post("/api/logout")
async def api_logout(request: Request):
    tok = _session_token(request)
    if tok:
        face_auth.revoke_session(tok)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("ew_session")
    return resp


@app.get("/api/status")
async def api_status():
    return JSONResponse(face_auth.status())


@app.get("/api/owner-image")
async def api_owner_image(request: Request):
    # only the authenticated owner may view their own enrolled face
    redir = _require_auth(request)
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    p = face_auth.owner_image_path()
    if not p:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(str(p), media_type="image/jpeg")


# ── sync API ──────────────────────────────────────────────────────────────────

def _authed(request: Request) -> bool:
    return face_auth.valid_session(_session_token(request))


@app.get("/api/sync")
async def api_sync_get(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(sync.status())


@app.post("/api/settings")
async def api_settings(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    return JSONResponse(sync.write_settings(body))


@app.post("/api/sync/pull")
async def api_sync_pull(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(sync.pull())


@app.post("/api/sync/push")
async def api_sync_push(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(sync.push())


if __name__ == "__main__":
    ip = _local_ip()
    print("=" * 56)
    print("  EDIT WEB  —  phone face-login (synced with MARK L)")
    print(f"  →  http://{ip}:{PORT}")
    print("  First visit enrolls your face. Then log in by face.")
    print("=" * 56)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
