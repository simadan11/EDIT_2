@echo off
REM ── EDIT Remote Control: автонастройка Cloudflare (double-click) ──────────
REM Два щелчка мышью: этот файл. Два клика в браузере: cloudflared + wrangler.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-remote-control.ps1" %*
if errorlevel 1 pause
