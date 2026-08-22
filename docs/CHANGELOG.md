# Changelog

## LUMEN 1.0 — «Свет, который понимает»

Полная переработка проекта: создана самостоятельная ИИ-платформа **LUMEN**
со собственным ядром, интерфейсом, памятью и инструментами.
Брендинг предшествующих версий из проекта полностью устранён.

### Добавлено (новая платформа)

> Бренд-примечание: в таблице ниже «предшественник» — название модели
> предшествующей версии проекта. Оно в проекте более не используется;
> строка приведена только как историческая карта миграции.

- **`lumen/` — ядро LUMEN-1**:
  - конвейер «Луч» из шести стадий (`kernel/engine.py`);
  - защитный слой `ThreatGuard` — приём, лимиты, anti-injection (RU/EN);
  - `IntentPlanner` — 14 намерений, слоты, план инструментов;
  - `ContextWeaver` — персона + факты + история с бюджетом токенов;
  - `MemoryFabric` — 3 слоя памяти + авто-извлечение + импорт legacy;
  - `ToolRegistry` — схемы, таймауты, изоляция ошибок, доступность;
  - `LearningLoop` — фидбэк, адаптация предпочтений, экспорт корпуса;
  - `EventBus` — события конвейера.
- **Генеративные модули**: LHC (локальный, без сети), Gemini,
  OpenAI-совместимый (Ollama/LM Studio/…) + деградация на LHC.
- **Веб-интерфейс** `lumen/web/` — дизайн-система «Luminance»,
  разделы: Диалог, Инструменты, Память, Система, Настройки;
  живое отображение стадий конвейера (SSE), markdown-ответы, фидбэк.
- **REST/SSE API** `lumen/api/server.py` — stdlib, нулевые зависимости:
  чат, стрим, инструменты, память, сессии, настройки, система,
  фидбэк, обучение, здоровье, профиль.
- **CLI** `python -m lumen` — serve | chat | tools | status | memory.
- **Конфигурация** `config/lumen.json` — новый формат; авто-миграция
  API-ключей из legacy-конфига.
- **Документация**: README, `docs/ARCHITECTURE.md`, `docs/API.md`,
  `docs/BRANDING.md`, `docs/ROADMAP.md`.
- **Тесты** `tests/` — 60 тестов (ядро, память, инструменты,
  безопасность, обучение, API, SSE).

### Удалено (заменено новой платформой)

| Что | Почему |
|---|---|
| старая папка мобильного веб-приложения | Мобильное веб-приложение предшествующей версии — заменено единым отзывчивым интерфейсом `lumen/web/` (см. MOBILE_WEB_README.md) |
| `dashboard/` | Старый HTTP-дашборд (FastAPI) — заменён `lumen/api/` (stdlib) и новым UI |
| `jarvis_import/` | Мёртвый импорт исходного проекта (нигде не ссылался) |
| `config/certs/` | Сертификаты локального TLS (использовались только удалённым дашбордом) |
| `readme.md` (корень) | Документация старой версии — архив в `docs/legacy/old_readme.md` |

### Переименовано

| Было | Стало | Примечание |
|---|---|---|
| Бренд/модель «предшественник» | **LUMEN** (ядро LUMEN-1) | везде: код, UI, доки, API |
| `assistant_name` по умолчанию | `LUMEN` | `memory/config_manager.py` |
| Инструмент `shutdown_jarvis` | `lumen_shutdown` | legacy-десктоп, `main.py` |
| Классы `JarvisUI` / `JarvisLive` | `LumenUI` / `LumenLive` | legacy-десктоп |
| Autostart: `JARVIS_AI` / `com.jarvis.assistant` / `jarvis.desktop` | `LUMEN_AI` / `com.lumen.assistant` / `lumen.desktop` | с compat-чтением старых ключей |
| Профили браузера `.jarvis_profiles` | `.lumen_profiles` | старая папка остаётся как есть |
| Задачи Windows `JARVISReminder_*`, `JARVIS_GameUpdater` | `LUMENReminder_*`, `LUMEN_GameUpdater` | новые имена задач |
| `create_*_launch_shortcut.py` | `create_lumen_launch_shortcut.py` | теперь запускает `python -m lumen serve` |
| Доменные имена в `cloudflare/`, `play-store/` (старый домен → `lumen.com` и т.п.) | `lumen-*` | порт туннеля 8000 → 8090 |
| Wake-words | `lumen, люмен, люмена, люмени` | `actions/voice_features.py` |
| `core/prompt.txt` | Перезаписан персоной LUMEN | правила работы с инструментами сохранены |

### Сохранено (совместимость)

- Конфиг-ключи `tts_jarvis_*` в `config/api_keys.json` продолжают
  читаться legacy-десктопом (имена ключей — часть пользовательского
  конфига; смена сломала бы существующие установки).
- `memory/long_term.json` — данные пользователя; импортируются
  в новую память при первом запуске (origin `legacy-import`).
- `actions/*` — модули действий; подключаются к ядру через
  legacy-мост `lumen/tools/adapters.py` (инструменты `legacy.*`).
- `main.py` / `ui.py` / `hub.py` — голосовой десктоп, переименован
  в LUMEN, позиционируется как режим «Desktop».
- `firmware/m5stick/`, `config/faces/` — без изменений.
