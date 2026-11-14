# 🧠 Локальный ИИ (Ollama) и 🧩 менеджер плагинов

В интерфейсе появились две кнопки — в шапке (маленькие значки **🧩** и **🧠**) и в
панели **SETTINGS & CONTROLS** (⚙ слева сверху):

| Кнопка | Что делает |
|---|---|
| **🧩 PLUGIN MANAGER** | включает/выключает плагины, ставит новые `.py` / `.zip` / папки, показывает, сколько инструментов ИИ активно |
| **🧠 LOCAL MODEL / OLLAMA** | находит папку с моделями Ollama на диске, показывает список моделей, запускает `ollama serve`, активирует выбранную модель, проверяет её ответ |

---

## 1. Модели на диске `D:\бодикам`

EDIT сам ищет хранилище Ollama в таком порядке:

1. `ollama_models_dir` из `config/api_keys.json` (по умолчанию `D:\бодикам`);
2. переменная окружения `OLLAMA_MODELS`;
3. `D:\бодикам`, `D:\бодикам\models`, `D:\бодикам\ollama`, `C:\бодикам`, …
4. `%USERPROFILE%\.ollama\models`, `%LOCALAPPDATA%\Ollama\models`.

Поддерживаются оба формата индекса Ollama:

```
D:\бодикам\manifests-v2\ollama.com\huihui_ai\qwen3-abliterated\abliterated-v3
D:\бодикам\manifests\registry.ollama.ai\library\llama3.2\latest
```

Имя модели собирается автоматически: `huihui_ai/qwen3-abliterated:abliterated-v3`.
Модели из `huihui_ai` выбираются по умолчанию (авто-режим).

### Если Ollama «не видит» папку

Скажите Ollama, где лежат модели — один раз:

* в окне **🧠 LOCAL MODEL / OLLAMA** нажмите **📌 MAKE PERMANENT** (это
  выполняет `setx OLLAMA_MODELS "D:\бодикам"`), затем полностью закройте Ollama
  (иконка в трее → Quit) и запустите снова;
* либо задайте переменную вручную: *Параметры → Система → Дополнительные
  параметры системы → Переменные среды → OLLAMA_MODELS = D:\бодикам*.

После перезапуска нажмите **SCAN** — у моделей появится метка `[server]`.
Кнопка **▶ START OLLAMA** запускает `ollama serve` из EDIT с правильным
`OLLAMA_MODELS`.

### Как это работает в EDIT

* `config/api_keys.json`:
  ```json
  "llm_provider": "ollama",
  "llm_url": "http://localhost:11434",
  "llm_model": "",              // пусто = авто-выбор (huihui_ai в приоритете)
  "llm_model_auto": true,
  "ollama_models_dir": "D:\\бодикам",
  "use_local_claude": true,
  "local_claude_base_url": "http://localhost:11434/v1",
  "local_claude_model": "",
  "local_llm_chat": true
  ```
* все действия (code_helper, computer_control, social_osint и т. д.) через
  `core/model_router.py` идут в Ollama;
* набранные вручную команды обрабатывает локальная модель с полным списком
  инструментов (`core/local_agent.py`): она может вызывать действия, плагины,
  скиллы, а ответ озвучивается TTS-модулем;
* переключатель **💬 LOCAL CHAT** в панели настроек: ON — печатать в Ollama,
  OFF — отправлять текст в Gemini Live (как раньше);
* при старте EDIT экспортирует `OLLAMA_MODELS`, поднимает сервер и делает
  пробный запрос — в логе видно `Local model ready — <модель> (<ms>)`.

> ⚠ Голосовой «живой» диалог (микрофон в реальном времени) по-прежнему идёт
> через Gemini Live — Ollama не умеет native-audio стриминг. Всё, что вы
> **печатаете**, и все действия/инструменты работают полностью локально.

---

## 2. Плагины

Плагин — папка в `plugins/`:

```
plugins/my_plugin/
  plugin.json
  plugin.py
```

`plugin.json`:

```json
{
  "name": "my_plugin",
  "title": "My Plugin",
  "version": "1.0.0",
  "description": "Что делает плагин",
  "entry": "plugin.py",
  "tools": [
    {
      "name": "my_tool",
      "description": "Что делает инструмент",
      "parameters": {
        "type": "object",
        "properties": { "query": { "type": "string" } },
        "required": ["query"]
      }
    }
  ]
}
```

`plugin.py`:

```python
def run_tool(tool_name: str, args: dict, player=None) -> str:
    return "результат"

# или для плагина с одним инструментом:
def run(args: dict, player=None) -> str:
    return "результат"
```

* включённые плагины автоматически попадают в список инструментов ИИ
  (локальная модель видит их сразу, Gemini — после переподключения сессии);
* состояние хранится в `config/plugins.json` (`disabled: [...]`);
* **INSTALL PLUGIN** принимает `.py`, `.zip` или папку;
* готовые примеры: `plugins/system_report` (CPU/RAM/диски/батарея),
  `plugins/quick_note` (заметки), `plugins/weather_now` (погода без API-ключа).

---

## 3. Быстрая проверка

```bash
python -c "from core import ollama_local; import json; print(json.dumps(ollama_local.status(), ensure_ascii=False, indent=2))"
python -c "from core import plugin_manager; print(plugin_manager.summary())"
```

Эти команды покажут найденную папку моделей, список моделей, активную модель,
состояние сервера и количество активных плагинов.
