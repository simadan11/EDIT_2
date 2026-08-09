# 🌐 Remote Control через Cloudflare — пошаговая настройка

Доступ к EDIT с телефона из мобильного интернета (без домашнего WiFi).

> ⚡ **Автоматически ("под ключ"):** двойной клик по
> [`setup-remote-control.bat`](setup-remote-control.bat) — скрипт сам поставит
> cloudflared, создаст туннель `edit-remote`, DNS `remote.edit.com`, службу
> Windows, запишет `tunnel_static_url` в конфиг EDIT и задеплоит воркер.
> Ниже — то же самое вручную, шаг за шагом, и объяснение схемы.

> ⚠️ **Важно понимать:** сам ассистент — локальный Python-процесс (микрофон,
> экран, файлы), он не «переезжает» в Cloudflare. Workers/Tunnel — это только
> доставка панели Remote Control до вашего телефона:
> `Телефон (4G) → HTTPS → Cloudflare → ваш ПК (localhost:8000)`.

Есть три уровня — от простого к постоянному. Выбирайте один.

---

## Вариант 0 — встроенный быстрый туннель (уже работает, 0 настройки)

1. Установите `cloudflared`: `winget install cloudflare.cloudflared` (Windows) или `brew install cloudflared` (macOS).
2. В приложении: ⚙️ → **🌐 INTERNET ACCESS** (или скажите «включи интернет доступ»).
3. EDIT покажет случайный URL `https://xxxx.trycloudflare.com` — откройте его на телефоне.

**Минус:** URL случайный при каждом запуске.

---

## Путь A — постоянный адрес через **Cloudflare Tunnel** (рекомендуется, без кода)

Нужно: бесплатный аккаунт Cloudflare + **свой домен**, добавленный в Cloudflare DNS
(самый дешёвый домен стоит пару долларов в год; субдомен вида `edit.вашдомен.com` бесплатен).

### 1. Создайте именной туннель (PowerShell)

```powershell
cloudflared login                                   # откроется браузер → выберите домен
cloudflared tunnel create edit-remote               # запомните UUID из вывода
cloudflared tunnel route dns edit-remote edit.вашдомен.com
```

### 2. Конфиг `%USERPROFILE%\.cloudflared\config.yml`

```yaml
tunnel: ВАШ-UUID
credentials-file: C:\Users\ВЫ\.cloudflared\ВАШ-UUID.json

ingress:
  - hostname: edit.вашдомен.com
    service: http://localhost:8000        # дашборд EDIT (plain HTTP на 8000)
  - service: http_status:404
```

> HTTPS-режим дашборда включён (`config/certs`)? Добавьте к сервису:
> `originRequest: { noTLSVerify: true }` — самоподписанный сертификат пройдёт.

### 3. Запуск и автозапуск

```powershell
cloudflared tunnel run edit-remote        # проверка: https://edit.вашдомен.com открывается
cloudflared service install               # (опционально) служба Windows, стартует с ПК
```

### 4. Скажите EDIT про постоянный адрес — `config/api_keys.json`

```json
"internet_tunnel": true,
"tunnel_static_url": "https://edit.вашдомен.com"
```

Теперь QR/ссылка в панели **Remote Control** всегда показывают постоянный адрес.

---

## Путь B — фасад на **Cloudflare Workers** (красивый URL + свой секрет)

Поверх Пути A (нужен стабильный ORIGIN). Даёт адрес
`https://edit-remote.<sub>.workers.dev` и дополнительную «дверь» по секрету.

### 1. Установите Wrangler

```powershell
# нужен Node.js LTS → https://nodejs.org
npm i -g wrangler
wrangler login                                 # бесплатный аккаунт Cloudflare
```

### 2. Отредактируйте `wrangler.toml`

```toml
ORIGIN = "https://edit.вашдомен.com"     # ← ваш постоянный hostname из Пути A
SECRET = "придумайте-длинную-строку"     # ← ваша личная «вторая дверь»
```

### 3. Деплой

```powershell
cd cloudflare
wrangler deploy
# → https://edit-remote.<ваш-subdomain>.workers.dev
```

### 4. В приложении

```json
"tunnel_static_url": "https://edit-remote.<ваш-subdomain>.workers.dev"
```

На телефоне открываете адрес **с секретом**:
`https://edit-remote.<sub>.workers.dev/?k=ваш-SECRET` (добавьте в закладки /
домашний экран PWA — секрет «вшит» в ссылку).

**Замечания:**
- WebSocket (голосовой канал, EDITH-камера) Workers проксируют автоматически;
  при долгом простое соединение может обрываться — приложение переподключается само.
- Бесплатного тарифа Workers (100 000 запросов/день) для дашборда хватает с запасом.
- Свой домен вместо workers.dev — раскомментируйте `routes` в `wrangler.toml`.

---

## 🔒 Безопасность (что уже есть в приложении + что добавить)

| Рубеж | Где |
|---|---|
| PIN → device-token, привязка телефона | встроено в дашборд — первый вход лучше делать дома по WiFi, токен сохраняется |
| Отзыв устройств | ⚙️ → Remote Control → список устройств / revoke |
| `?k=SECRET` поверх PIN | Worker из Пути B (в `worker.js`) |
| TLS | терминируется на Cloudflare до любого из адресов |

Не публикуйте URL открыто: кто знает адрес **и** PIN — получает доступ к ассистенту.
