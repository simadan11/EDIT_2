"""
LUMEN — голосовой модуль (опциональный).

Голос — подключаемая надстройка над текстовым ядром: ядро LUMEN-1
полностью голосу не зависит. STT/TTS подключаются лениво:

  • TTS: edge-tts (если установлен) — иначе модуль честно сообщает
    «недоступен» и UI скрывает голосовые кнопки;
  • STT: локальная/облачная реализация подключается здесь же.

Публичное API:
    voice.available()          → (tts_ok, stt_ok, reasons)
    voice.speak(text)          → путь к wav/mp3 (или ошибка)
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


class VoiceModule:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.enabled = bool(config.get("voice.enabled", False))
        self._tts_engine = config.get("voice.tts_engine", "edge")
        self._stt_engine = config.get("voice.stt_engine", "auto")

    def available(self) -> Dict[str, Any]:
        tts_ok = False
        tts_reason = "модуль отключён в настройках"
        if self.enabled:
            if self._tts_engine == "edge" and shutil.which("edge-tts") is None:
                try:
                    import edge_tts  # noqa: F401
                    tts_ok = True
                except ImportError:
                    tts_reason = "не установлен edge-tts (pip install edge-tts)"
            else:
                tts_ok = True
        stt_ok = False
        stt_reason = "STT-движок не подключён к этому интерфейсу"
        return {"enabled": self.enabled, "tts": tts_ok,
                "stt": stt_ok, "tts_reason": tts_reason,
                "stt_reason": stt_reason, "engine": self._tts_engine}

    def speak(self, text: str, out_dir: Optional[Path] = None) -> Dict[str, Any]:
        info = self.available()
        if not info["tts"]:
            return {"ok": False, "error": info["tts_reason"]}
        out_dir = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"lumen_{abs(hash(text)) % 10**8}.mp3"
        try:
            cmd = ["edge-tts", "--text", text[:4000], "--write-media", str(out)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            return {"ok": True, "path": str(out)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
