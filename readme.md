# ⚙️ MARK L (50)
### The Ultimate Cross-Platform Personal AI Assistant — By FatihMakes

> 📺 **[Watch the full setup video on YouTube](https://www.youtube.com/@FatihMakes)**

A real-time voice AI that can hear, see, understand, and control your computer — on any OS. Supports Windows, macOS, and Linux. Built on the Gemini Live API for native audio streaming, delivering zero subscriptions and total digital autonomy.

---

## ✨ Overview

MARK L is where the assistant stops being a tool and starts being a presence. It remembers yesterday's conversation, watches the topics you care about, and speaks first when it has something worth saying. The goal of this build was continuity — JARVIS should feel like it never fully left, even after you close it.

It's not just an assistant — it's an extension of your digital life.

---

## 🚀 Capabilities

### Core Features
| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Ultra-low latency conversation in any language via Gemini Live API |
| 🖥️ System Control | Launch apps, adjust volume/brightness, WiFi, shortcuts, power — all by voice |
| 🧩 Autonomous Tasks | High-level planning for complex multi-step goals via agent mode |
| 👁️ Visual Awareness | Real-time screen capture and webcam vision piped into your main Gemini session |
| 🧠 Persistent Memory | Deeply remembers projects, preferences, and personal context across sessions |
| ⌨️ Hybrid Input | Seamlessly switch between keyboard typing and voice commands |
| 🌅 Morning Briefing | On first boot: greets you, reads the time, recaps yesterday, and fetches live news |
| 🔔 Proactive 2.0 | Time-aware, context-aware check-ins — knows the time of day, your projects, and what you've been discussing |
| 🗓️ Session Memory | Summarises each conversation and mentions it naturally next morning — consumed after use, never repeats |
| 👁️‍🗨️ Background Monitoring | User-configured topic watching — checks for new headlines once a day and alerts naturally |
| 📊 Hardware Monitoring | Continuous CPU, RAM, GPU and temperature telemetry with localized voice alerts |
| 🌤️ Weather Report | Live weather data for your city, personalized from memory |
| 🗺️ Dynamic Content Panel | Scrollable display layer beneath the HUD that renders web results, news, and search data |
| 🔍 Multi-Mode Web Search | `news` / `research` / `price` / `compare` / `search` — Gemini Grounded first, DDG fallback |
| ⏰ Smart Reminders | OS-native scheduled notifications (Windows Task Scheduler / macOS LaunchAgent / Linux systemd) |
| ✈️ Flight Finder | Live flight price and availability lookup |
| 🎮 Game Updater | Checks and triggers game updates on Steam and Epic Games on demand |
| 📂 File Processor | Read, summarize, and answer questions about local files |
| 💻 Code Helper | Inline code review, debugging, and generation |
| 🌐 Browser Control | Open URLs, navigate tabs, and interact with the browser by voice |
| 📨 Send Message | Compose and send messages through WhatsApp, Telegram, and more |
| 🎬 YouTube Control | Search, play, and control YouTube playback by voice |
| 🖱️ Desktop Control | Taskbar, window management, and desktop-level operations |
| 🧑‍💻 Silent Language Memory | Detects spoken language on first use — all future sessions adapt automatically |
| 📱 Remote Dashboard | Control the assistant from your phone via QR code pairing |
| 🎧 Headphones Mode | Bluetooth headphones paired to the phone or the PC become a hands-free channel: EDIT speaks through them, hears you via the headset mic, and the headphone's own button is push-to-listen |
| 📷 Phone Camera Vision | EDITH-style scan from the phone camera — labels people, cars & plates on a live HUD, JARVIS answers by voice on PC **and** phone |
| ◈ Holo Lab | Create any hologram/blueprint, assemble a buy/make BOM, run diagnostics and print a build report — smart glasses, robot, vehicle, building, planet or custom geometry |
| ⚡ Auto-Start on Boot | Registers with the OS startup system (registry / LaunchAgent / .desktop) |
| 📋 Clipboard Intelligence | Copy any text → floating panel with Translate / Summarise / Explain / Fix |
| 🎨 Assistant Customization | Change the assistant name and your name from the UI — takes effect immediately |

---

## 🌐 Internet Access — use EDIT from anywhere over mobile data (no WiFi)

When you leave home there is no WiFi, so the local address `192.168.x.x:8000` is unreachable. EDIT can open a **public HTTPS tunnel** (Cloudflare quick tunnel or ngrok) that gives you an internet URL — open it on your phone over **mobile data** and the same Remote Dashboard works: headphones mode 🎧, voice channel, EDITH camera.

### How to use
1. **Install a tunnel engine on the PC** (one time):
   - Cloudflare (recommended, free, no account):
     - Windows: `winget install cloudflare.cloudflared` (or download `cloudflared.exe` from the releases page),
     - macOS: `brew install cloudflared`,
     - Linux: `sudo apt install cloudflared`.
   - or ngrok: https://ngrok.com/download
2. In EDIT press **🌐 INTERNET ACCESS** in the settings drawer (⚙️), or say *«включи интернет доступ»* / *«internet access on»*.
3. EDIT shows the internet URL (e.g. `https://xxxx.trycloudflare.com`) on the content panel and in the log.
4. Open that URL on the phone — even on mobile data, away from home. A phone that already paired at home (device token) enters automatically; a new phone needs the PIN from Remote Control.

To turn it off: press **🌐** again or say *«выключи интернет доступ»*. The mode can auto-start with EDIT (`internet_tunnel: true` in `config/api_keys.json`).

> 🔒 The tunnel URL is random on every start (quick tunnels). For a **permanent** address, create a free Cloudflare account, set up a named tunnel, and put your stable URL in `config/api_keys.json` → `tunnel_static_url`. Auth still applies (PIN / paired device token), and API/WS traffic is never cached by the service worker.
> 📶 Voice over mobile data works but latency is higher than on LAN — fine for commands, slightly delayed for back-and-forth conversation.

---

## 📲 Install EDIT on your phone as an app (PWA)

The Remote Dashboard is an installable web app (PWA) — you get an icon on the home screen, a full-screen window and a faster start, so the assistant feels like a real app on the phone.

**Android (Chrome):**
1. Open the Remote Dashboard on the phone (pair via **Remote Control** QR first — the pairing is remembered automatically after the first scan).
2. Tap the **⤓** button in the header (it appears when the app is installable), or open the browser menu **⋮ → «Добавить на главный экран» / «Установить приложение»**.
3. Confirm — an **EDIT** icon appears on the home screen. Tap it to launch the dashboard in its own full-screen window. First launch may ask to re-pair if the app data was cleared; normally it reconnects automatically via the remembered device token.

**iPhone / iPad (Safari):**
1. Open the dashboard, then tap **Share (⤴) → «На экран „Домой"»**.
2. Add — an EDIT icon appears on the home screen. (iOS runs the dashboard in a standalone Safari window.)

Notes: the installed app keeps working as the remote: headphones mode (🎧), voice channel, EDITH camera, Holo Lab. It also holds the screen wake lock during headphones mode, so the display stays on. The app needs the PC to be running — it is a remote control, not a standalone server.

---

## 📷 Phone Camera Vision — EDITH Mode

The Remote Dashboard now turns your phone into JARVIS's eyes, Spider-Man style:

1. On your PC, press **Remote Control** and pair your phone with the QR code.
2. Tap **📷** in the dashboard footer — a full-screen tactical HUD opens with your live camera feed (corner brackets, scanline, back/front camera toggle).
3. Tap **SCAN** (optionally type a question like *"who is in the room?"* or *"what is on my desk?"*):
   - **HUD detection** — the server scans the frame and your phone draws labeled boxes over the live video: **people in orange** (`PERSON — RED JACKET, GLASSES`), **vehicles in yellow** (`CAR — WHITE BMW X5`, `PLATE — A123BC` when the plate text is legible), **objects in cyan** (`LAPTOP`, `CAR KEYS`, famous landmarks & products by their real names).
   - **PC overlay** — the same labeled snapshot pops onto the PC window's HUD area for ~20 s, so you see on the big screen exactly what the phone saw.
   - **LIVE stream** — the **LIVE** toggle (~3 fps) streams the phone camera onto the PC main window in real time; a background detection pass repaints **person / vehicle / animal / object** boxes every ~1.5 s on *both* the PC window and the phone HUD. Tap **⏹** to stop.
   - **Tap for info** — tap any object/vehicle/landmark tag on the phone and JARVIS looks it up and explains what it is (people and plates are intentionally not searchable).
   - **Voice answer** — the same frame is injected into the main Gemini Live session, so JARVIS speaks a concise tactical report out loud — both on the PC **and on the phone's speaker** (🔊 toggle in the dashboard header), with the transcript in the feed and on the HUD.

### 🛰 Devices Hub — every remote under control
- **On the PC** — open **Remote Control**; the overlay now lists every connected phone (device type, IP, session time) with a **KICK** button per device and **REVOKE PAIRED DEVICES**.
- **On the phone** — the 🛰 chip in the header shows the live remote count and opens the same hub: see who is connected, kick a device, or revoke all saved pairings.

> 🔊 **Voice notes:** JARVIS speaks on PC and phone simultaneously; if both are in the same room and you hear an echo, tap 🔇 on the phone or use headphones. While JARVIS's voice plays on the phone speaker, the phone mic is briefly suppressed so JARVIS never hears himself (that echo is what used to interrupt answers).

> ⚠️ **Privacy by design:** people are described by appearance/clothing/pose only — the AI never identifies real people and never looks anyone up. License plates are transcribed as visible text only; there is intentionally **no owner lookup**. Camera access on `http://` origins needs the same one-time Chrome flag as the microphone (the app shows setup instructions automatically on first tap).

## ◈ Holo Lab — wearable / smart-glasses prototype

Open **◈ HOLO** in the Remote Dashboard — or open **◈ HOLO LAB / PC MONITOR** in the desktop settings drawer — to get a software holographic workbench inspired by sci-fi HUDs:

1. Choose **Smart Optics** (glasses + camera), **AR Glove**, **Field Suit**, or **Custom / any object or scene**.
2. For a custom design, type a subject such as *robot*, *car*, *house*, *spaceship*, *room*, *planet* or anything else. Press **ASK JARVIS TO DESIGN ANYTHING** and the AI generates the component schedule plus safe geometry primitives automatically.
3. Press **CREATE HOLOGRAM** for an immediate visual prototype. The server returns a session project ID such as `HOLO-A1B2C3` and mirrors the blueprint to the PC monitor.
4. Inspect the animated concept in **HOLO**, **WIREFRAME**, **EXPLODED**, or **CLEAR VIEW** mode. The PC renderer draws boxes, cylinders, spheres, rings, lines and other blueprint primitives into the hologram.
5. On the PC, the overlay draws the AI blueprint, subject, component schedule, geometry and animated hologram directly in the desktop window.
6. Start the camera feed and press **SCAN SPACE** to reuse the EDITH detector on the test feed. **ASK JARVIS ABOUT VIEW** sends one selected frame to the existing vision session for a spoken explanation.
7. In the PC Holo Lab, open **PARTS CATALOG / BUILD** to select a BOM from a broad offline catalog of parts that can be bought or made: controllers, cameras, displays, optics, batteries, chargers, regulators, sensors, 3-D printed enclosures, PCB parts, test instruments and safety equipment.
8. Open **ASSEMBLY EDITOR / PLACE PARTS** to add BOM parts into the scene, drag them in the viewport like a simple Blender assembly view, edit X/Y/Z, rotation and scale, snap to a grid, remove parts, and save/load the project as JSON.
9. Press **RUN DIAGNOSTICS / HELP** when something fails. It checks missing controller/power/protection/display/camera/thermal/test parts and gives a problem, fix and next bench test. You can also say *"камера не работает"*, *"экран чёрный"*, *"батарея греется"* or *"почему перезагружается"*.
10. Press **PRINT BLUEPRINT** to open the system printer dialog and print the blueprint, AI component schedule, buy/make BOM and diagnostics. You can also say *"распечатай схему голограммы"*.
11. You can also say or type *"создай полностью любую голограмму автомобиля и покажи чертёж по частям"*. The `holo_project` tool generates the subject, component list, geometry and suggested parts, opens the PC Holo Lab automatically and mirrors the project to connected dashboards.

This is an honest software prototype: it renders a hologram-style visualization on a phone/PC screen and can use a camera, but it cannot create a physical free-space hologram or switch on hardware by itself. Before building any real wearable, validate optics, heat, battery safety, fit, privacy and local regulations.

---

## 🎧 Headphones Mode — talk to EDIT through Bluetooth headphones

The headphones can be paired either to the **phone** (Remote Dashboard) or to the **PC** — EDIT supports both.

### 🗣️ Wake Bracket Protocol — «EDIT … команда … EDIT» (default ON)

By default EDIT answers **only** voice commands framed between two standalone **«EDIT»** words:

1. Say **«EDIT»** (эдит / едит / edith) — EDIT starts paying attention but stays silent.
2. Say your command: «EDIT, включи музыку, EDIT».
3. Say **«EDIT»** again — EDIT answers exactly what was said between the two words.

EDIT hears everything, but outside the frame it stays completely silent (no reaction to a single name, noise, or unframed questions). The wake word must be a standalone word — «отредактируй», «редактировать», «editable» etc. do **not** trigger it. While EDIT is speaking you can interrupt by saying «EDIT» — it stops and listens.

The protocol is saved in `config/api_keys.json` as `wake_bracket` (default `true`) and can be switched off by voice: *«выключи режим EDIT в начале и в конце»* (then EDIT responds to everything) or back on with *«включи wake protocol»*.

### 🗣️ Voice: PC speaks as always, phone headphones use the Jarvis Voice Module

- **On the PC — the voice is as always:** the AI's own audio plays through the PC speakers (default). No TTS module involved.
- **Phone headphones mode — improved Jarvis Voice Module:** the AI's audio is discarded and the reply text is voiced by a dedicated module with a **Jarvis-quality Russian voice** — deep male neural voice **ru-RU-DmitryNeural** (EdgeTTS), synthesised on the PC and streamed as PCM to the phone's single sink tab (= your headphones). Exactly one voice, never two; no robotic phone TTS. The replies are generated in Russian anyway, so the Russian voice matches.
  - Fallback: if `edge-tts`/`miniaudio` are not installed, the phone's own `speechSynthesis` is used instead.
  - The headphone button / saying «EDIT» stops the Jarvis voice instantly.

Configuration (`config/api_keys.json`): `tts_jarvis_voice` (default `ru-RU-DmitryNeural` — pick any EdgeTTS voice, e.g. `ru-RU-SvetlanaNeural` female, `en-GB-RyanNeural` for English Jarvis). The optional PC TTS mode (`tts_voice_mode`, default `false`) is toggled by voice: *«включи TTS модуль»* / *«пусть ИИ говорит как всегда»*.

### 📱 Headphones connected to the phone (main scenario)

1. Open the **Remote Dashboard** on your phone (pair via **Remote Control** QR) and tap the **🎧** chip in the header.
2. EDIT asks for microphone access once (that is the headset mic). The mode is now ON — the chip lights up.
3. Press the **button on your Bluetooth headphones** — EDIT instantly stops talking and listens to you through the headset:
   - the press is caught by the phone (browser media session → `/api/headphones/button`);
   - the phone streams its microphone (the headset mic) to the PC, so EDIT hears you;
   - EDIT's reply is voiced by the **Jarvis Voice Module** (deep Russian male neural voice, ru-RU-DmitryNeural) straight into your headphones.

Notes:

- **No double voice, guaranteed:** while the phone's 🎧 mode is ON the AI's audio is not used at all (Gemini returns text only), the PC speaker is muted automatically, and the phone tab that runs the mode is the single audio sink — no other tab/device voices the reply. Even with the dashboard open in two tabs you hear exactly one voice. The mode is restored after a page reload.
- **Screen stays on:** while the mode is ON (or the phone mic is streaming) the phone requests a **screen wake lock**, so the display never goes black and the browser never suspends the sound/mic. When the app returns to the foreground, the lock and audio contexts are resumed automatically. (Requires HTTPS — the dashboard already runs over HTTPS.)
- Works best in **Chrome on Android** (`navigator.mediaSession`). If the phone is also playing music in another app, the button controls that app instead — pause it first.
- **Works with Gelius and any other Bluetooth earbuds/headset** — they all send the standard AVRCP play/pause command on the multifunction button (on Gelius TWS earbuds it's a **single tap** on the earbud). EDIT listens to all of the play/pause/next/prev actions, so any of them triggers push-to-listen.
- While the mode is ON the tab keeps a silent media session so the button reaches EDIT, and the headset-mic channel is open (tap 🎤 to stop it manually, 🎧 again to turn the mode off).

### 🖥️ Headphones connected to the PC

- Press **🎧 HEADPHONES MODE** in the settings drawer (⚙️), or say *"включи режим наушников"* / *"headphones mode on"*.

| What | What happens |
|---|---|
| 🎧 Output | EDIT's voice plays through the headphones (A2DP/stereo endpoint) instead of the PC speakers |
| 🎤 Input | EDIT hears you through the headset microphone (Hands-Free endpoint; falls back to the default PC mic if the headset has none) |
| 🔘 Headphone button | The multifunction button on the headphones (AVRCP play/pause) becomes **push-to-listen** — tap it while EDIT is talking and EDIT instantly stops and listens to you through the headset |

PC-side details:

- **Auto-switch** — the mode re-checks Bluetooth every ~10 s: connect your headset later and EDIT switches over automatically; unplug it and the audio falls back to the default devices.
- **Remembered** — the mode is saved to `config/api_keys.json` (`headphones_mode`) and restored on the next start.
- **Voice control** — the `headphones_mode` tool answers *"наушники"*, *"режим наушников"*, *"bluetooth headphones"*, *"говори через наушники"*, etc.
- **Button capture** — Windows-only, requires `pip install keyboard` (already in `requirements.txt`). Without it the mode still reroutes the audio; only the headphone-button trigger is unavailable.

---

## 🆕 What's New in Mark L

### 🗓️ Session Memory — JARVIS Remembers Yesterday
At the end of every session, JARVIS generates a 1-2 sentence summary of what was discussed and saves it to memory. The next morning, it's mentioned naturally in the briefing:
> *"Good morning, sir — it's 09:15. Yesterday you were working on the Mark L background monitoring feature. Fetching today's headlines now."*

The summary is consumed immediately after use — it never repeats in future briefings and adds zero long-term bloat to memory.

### 👁️‍🗨️ Background Monitoring — JARVIS Watches While You're Away
Tell JARVIS to monitor any topic and it checks for new developments once a day using DuckDuckGo news. When a headline changes, it reports back naturally in your language:
> *"Efendim, takip ettiğiniz yapay zeka haberlerinde bir gelişme var: Google yeni bir model duyurdu."*

Fully opt-in — JARVIS monitors nothing without being explicitly asked. Crypto, financial, and trading topics are blocked at the code level regardless of what is requested. Same headline never triggers twice.

### 🔔 Proactive System 2.0 — Context-Aware, Time-Aware, Non-Repetitive
The proactive engine was rebuilt from the ground up. Instead of a generic check-in after 15 minutes of silence, JARVIS now:
- Knows the **time of day** — morning tone differs from evening tone
- Knows your **active projects** from memory and can ask how something is going
- Knows your **monitored topics** and can bring one up naturally
- Knows **what you were just talking about** (last 8 conversation turns)
- **Rotates** between three focus areas so it never opens with the same line twice
- Has a 20-minute cooldown (up from 10) — less intrusive, more meaningful

### 👁️ Instant Vision Acknowledgment — No More Silent Waiting
When you ask JARVIS to look at your screen or camera, it no longer goes silent while processing. It immediately says something natural ("Looking at your screen now, sir" / "Ekrana bakıyorum efendim") while the capture runs. The actual analysis follows as the next response.

### 📰 Parallel News Search — First Result Wins
News queries now run Gemini Grounded Search and DuckDuckGo news simultaneously in two threads. Whichever delivers a valid result first is used; the other is silently discarded. A Gemini 503 error no longer delays results — the DDG fallback is already running in parallel.

---

## 🗺️ Mark Roadmap

| Mark | Focus |
|---|---|
| **XLVIII** | Instant interrupt · parallel news · two-phase briefing · exponential backoff · vision cooldown |
| **XLIX** | Auto-start · clipboard intelligence · assistant customization |
| **L** | Session memory · background monitoring · proactive 2.0 · instant vision · parallel news search |
| **LI+** | Plugin system · email · quiz mode · calorie counter · calendar |

---

## ⚡ Quick Start

```bash
git clone https://github.com/FatihMakes/Mark-L.git
cd Mark-L
pip install -r requirements.txt
python main.py
```

> ⚠️ **Installation Note:** Some OS-specific dependencies are not bundled in `requirements.txt` to keep the repo lightweight. If you hit a `ModuleNotFoundError`, install the missing package with `pip install <module_name>`.

---

## 📋 Requirements

| Requirement | Details |
| --- | --- |
| **OS** | Windows 10/11, macOS, or Linux |
| **Python** | 3.11 or 3.12 |
| **Microphone** | Required for voice interaction |
| **API Key** | Free Gemini API key (`config/api_keys.json`) |

---

## 🗂️ Project Structure

```
Mark L/
├── main.py                   # Core loop — Gemini Live session, audio I/O, tool dispatch
├── ui.py                     # PyQt6 HUD — waveform, log panel, interrupt button, camera feed
├── setup.py                  # First-run configuration wizard
├── actions/
│   ├── web_search.py         # Gemini + DDG parallel search (news, research, price, compare)
│   ├── screen_processor.py   # Screen capture & webcam vision via Gemini Live
│   ├── background_monitor.py # User-configured topic watching — daily DDG check, no crypto
│   ├── proactive.py          # Proactive 2.0 — time/context/rotation-aware check-ins
│   ├── reminder.py           # OS-native scheduled notifications
│   ├── system_monitor.py     # CPU / RAM / GPU / temperature telemetry
│   ├── computer_settings.py  # Volume, brightness, WiFi, power
│   ├── computer_control.py   # Keyboard shortcuts, mouse, window management
│   ├── open_app.py           # Application launcher
│   ├── browser_control.py    # Web browser control
│   ├── file_controller.py    # File system operations
│   ├── file_processor.py     # Document reading and summarization
│   ├── send_message.py       # Messaging integration
│   ├── weather_report.py     # Live weather data
│   ├── flight_finder.py      # Flight search
│   ├── youtube_video.py      # YouTube playback control
│   ├── game_updater.py       # Game update management (Steam / Epic)
│   ├── code_helper.py        # Code review and generation
│   ├── dev_agent.py          # Developer task agent
│   └── desktop.py            # Desktop and taskbar control
├── memory/
│   ├── memory_manager.py     # Load/save long_term.json — sessions, monitors, identity
│   └── long_term.json        # Persistent store: identity, preferences, projects, sessions, monitors
├── core/
│   └── prompt.txt            # Assistant personality and tool-routing rules
└── config/
    └── api_keys.json         # API key, OS setting, assistant name, user name
```

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

---

## 👤 Connect with the Creator

Engineered by a developer building a real-world JARVIS-style assistant.
⭐ **Star the repository to support the journey to Mark 100.**

| Platform | Link |
| --- | --- |
| YouTube | [@FatihMakes](https://www.youtube.com/@FatihMakes) |
| Instagram | [@fatihmakes](https://www.instagram.com/fatihmakes) |
