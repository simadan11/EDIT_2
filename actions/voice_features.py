# actions/voice_features.py — интеграция функций из jarvis_import/core/
"""Wakeword, silence detection, TTS optimization, offline fallback."""

WAKEWORDS = ["эдит", "edit", "edith", "едит", "эдита", "джарвис", "jarvis", "чарльз", "джервис"]


def wakeword_detect(text: str) -> bool:
    if not text:
        return False
    words = text.lower().split()
    return any(w in words for w in WAKEWORDS)


def optimize_for_tts(text: str) -> str:
    """Format answer for voice synthesis: short sentences, clean markup."""
    # Remove markdown, extra whitespace, keep natural flow
    import re
    text = re.sub(r"[\*\_\`\#\[\]]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Split long sentences at periods for natural pauses
    return text[:900]  # cap length for TTS


def silence_detect(last_voice_time: float, silence_timeout: float = 1.2) -> bool:
    import time
    return (time.time() - last_voice_time) > silence_timeout


def offline_fallback() -> str:
    return "Нет интернета — работаю в автономном режиме. Mогу искать в DDG или отвечать по памяти."
