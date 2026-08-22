"""
LUMEN — самостоятельная ИИ-платформа.

LUMEN (Люмен) — «Свет, который понимает».

Платформа построена с нуля вокруг собственного ядра LUMEN-1:
шестистадийного конвейера обработки запроса «Луч», модуля контекстной
памяти, реестра инструментов и обучающей петли. Ядро работает на
чистой стандартной библиотеке Python — без обязательных внешних
зависимостей.

Состав:
    lumen.kernel   — ядро LUMEN-1 (конвейер «Луч», память, безопасность)
    lumen.tools    — реестр инструментов (встроенные + legacy-адаптеры)
    lumen.io       — текстовый и голосовой ввод/вывод
    lumen.api      — REST/SSE API (stdlib http.server)
    lumen.web      — веб-интерфейс (статика, нулевые зависимости)

Быстрый старт:
    python -m lumen serve --port 8090
    python -m lumen chat
"""

BRAND_NAME = "LUMEN"
BRAND_NAME_RU = "Люмен"
MODEL_NAME = "LUMEN-1"
TAGLINE = "Свет, который понимает"
VERSION = "1.0.0"
PIPELINE_NAME = "Луч"

__version__ = VERSION
__all__ = [
    "BRAND_NAME", "BRAND_NAME_RU", "MODEL_NAME", "TAGLINE",
    "VERSION", "PIPELINE_NAME", "__version__",
]
