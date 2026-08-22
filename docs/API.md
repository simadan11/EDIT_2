# REST/SSE API LUMEN

Базовый адрес: `http://<host>:<port>` (по умолчанию `0.0.0.0:8090`).
Все ответы — JSON (`charset=utf-8`). Если задан `api.auth_token` —
каждый запрос (включая статику) требует заголовок
`Authorization: Bearer <token>`.

## Чат

### `POST /api/chat`

```json
{ "message": "Сколько времени?", "session_id": "1d85beae02" }
```

Ответ:

```json
{
  "message_id": "c7fd105a7883",
  "session_id": "1d85beae02",
  "reply": "Сейчас 07:47:46 — 22 August 2026, суббота.",
  "ok": true,
  "risk": "safe",
  "intent": "time",
  "intents": ["time"],
  "confidence": 0.88,
  "tools_used": [{ "name": "time.now", "ok": true, "ms": 0, "error": null }],
  "facts_used": [],
  "backend": "lumen_core",
  "fallback": false,
  "latency_ms": 2,
  "harvested": [],
  "blocked_reasons": []
}
```

Ошибки: `400` — пустое поле `message`.

### `POST /api/chat/stream` (SSE)

Тот же вход; ответ — поток событий:

```
event: start
data: {"message": "…", "session_id": null}

event: intake
data: {"stage": "intake", "ts": 0.0, "risk": "safe", "tags": []}

event: plan
data: {"stage": "plan", "primary": "time", "intents": ["time"], "confidence": 0.88, "slots": {}, "tool_calls": []}

event: tool
data: {"stage": "tool", "name": "time.now", "ok": true, "ms": 0, "text": "Сейчас …"}

event: context
data: {"stage": "context", "facts": 0, "history": 2, "tokens": 334}

event: remember
data: {"stage": "remember", "harvested": 0, "session": "1d85beae02"}

event: done
data: {"stage": "done", "latency_ms": 2, "backend": "lumen_core", "fallback": false}

event: result
data: { …полный EngineResponse… }

event: end
data: {}
```

Возможны также `event: error` с `data.error`.

## Инструменты

### `GET /api/tools`
Список всех инструментов: схема параметров, доступность, причина.

### `POST /api/tools/run`
```json
{ "name": "math.calc", "args": { "expression": "2+2" } }
```
Ответ: `{ "ok": true, "text": "≈ 4", "data": {…}, "error": "", "ms": 0 }`
Ошибки: `400` — неизвестный инструмент, недоступность, обязательный
параметр, ошибка выполнения (с `error`).

## Память

### `GET /api/memory` / `GET /api/memory?category=identity`
Все факты (по категориям) и счётчик.

### `POST /api/memory`
```json
{ "category": "identity", "key": "user_name", "value": "Алексей" }
```

### `DELETE /api/memory`
```json
{ "category": "identity", "key": "user_name" }
```
`404`, если факта не было.

### `GET /api/memory/recall?query=…`
Relevance-поиск по семантическому слою (с тезаурусным расширением).

## Сессии

- `GET /api/sessions` — список (id, title, created, messages).
- `POST /api/sessions` `{ "title": "…" }` — новая сессия.
- `DELETE /api/sessions/<id>` — удалить (и журнал).

## Настройки

### `GET /api/settings`
Публичная конфигурация (API-ключ маскируется: `sk-s••••••••2345`).

### `PUT /api/settings`
Частичное обновление, накладывается рекурсивно:

```json
{ "backend": { "provider": "lumen_core" },
  "persona": { "style": "brief" } }
```

`backend.provider`: единственный реальный вариант — `lumen_core`
(собственный ИИ, офлайн). Любое другое значение (включая старые
`gemini`/`openai`/`lhc`/`heuristic`) приводит к LUMEN Core — внешних
модулей в платформе нет, поле читается для совместимости.

Применяется к ядру сразу: guard, backend, context-сборщик пересоздаются
без перезапуска сервера.

## Система и обучение

### `GET /api/system`
CPU/RAM/диск/хост/аптайм + блок `platform` (бренд, модуль, инструменты,
факты).

### `POST /api/feedback`
```json
{ "message_id": "c7fd105a7883", "rating": 1, "note": "короче, пожалуйста", "intent": "time" }
```
`rating`: `1` (👍) / `-1` (👎). Запускает адаптацию предпочтений.

### `GET /api/learning/stats`
Запросы, top намерений, top инструментов, фидбэк, уровень одобрения,
выученные предпочтения.

### `POST /api/learning/corpus`
Экспорт JSONL-корпуса для дообучения (все сессии).
Ответ: `{ "count": 142, "path": "…/lumen_data/export/corpus.jsonl" }`.

## Голос и прочее

- `GET /api/health` — статус, модуль, счётчики, аптайм.
- `GET /api/profile` — карточка платформы (концепция, стадии, слои, backends).
- `POST /api/voice/speak` `{ "text": "…" }` — TTS (если доступен).
- `404` — неизвестный маршрут; `500` — ошибка ядра (структурированная).

## Примеры (curl)

```bash
curl -s http://localhost:8090/api/health
curl -s -X POST http://localhost:8090/api/chat \
     -H 'Content-Type: application/json' \
     -d '{"message":"Состояние системы"}'
curl -s -N -X POST http://localhost:8090/api/chat/stream \
     -H 'Content-Type: application/json' \
     -d '{"message":"Какая погода в Казани?"}'
```
