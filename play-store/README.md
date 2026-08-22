# 🤖 LUMEN Remote — приложение для Google Play (TWA / Bubblewrap)

Панель Remote Control уже является установочным PWA. Чтобы получить настоящее
Android-приложение для Google Play, используется **Trusted Web Activity (TWA)** —
официальный способ Google завернуть PWA: приложение открывает наш дашборд
на весь экран, без адресной строки, со своей иконкой, через Google Play.

```
Google Play  →  app-release-signed.aab  →  Bubblewrap (Android-проект из PWA)
                                                       ↕ Digital Asset Links
                        https://remote.lumen.com  ←  дашборд LUMEN на вашем ПК
```

> **Пререквизит №1:** нужен постоянный публичный HTTPS-адрес дашборда.
> Это наш `remote.lumen.com` из `cloudflare/` (туннель). Быстрый
> `trycloudflare.com`-URL **не подходит** — он меняется при каждом запуске,
> а assetlinks жёстко привязаны к домену.

---

## Шаг 1. Инструменты (Windows)

```powershell
winget install OpenJS.NodeJS.LTS        # Node.js ≥ 18
winget install Microsoft.OpenJDK.17     # JDK 17
npm i -g @bubblewrap/cli
```

(Android SDK командной строки Bubblewrap предложит скачать сам на первом `init` —
соглашайтесь.)

## Шаг 2. Сгенерировать Android-проект

```powershell
mkdir LUMEN-TWA; cd LUMEN-TWA
bubblewrap init --manifest https://remote.lumen.com/manifest.webmanifest
```

Ответы мастера:
| Вопрос | Значение |
|---|---|
| Package name | `com.lumen.remote` |
| App name | `LUMEN Remote` |
| Launcher icon / splash | берутся из манифеста (icons 192/512 уже maskable-ready) |
| Signing key | создать новый (bubblewrap сделает `android.keystore`) — **сохраните файл и пароль навсегда**, без них нельзя обновлять приложение |

## Шаг 3. Сборка

```powershell
bubblewrap build
# → app-release-signed.aab   (это для Google Play)
# → app-release-signed.apk   (это можно сразу кинуть на телефон для теста)
```

## Шаг 4. Digital Asset Links (чтобы убрать адресную строку)

```powershell
keytool -list -v -keystore android.keystore          # скопируйте SHA256: AA:BB:...
bubblewrap fingerprint add <AA:BB:...>
bubblewrap fingerprint generateAssetLinks            # → assetlinks.json
```

Положите полученный файл в репозиторий:

```
dashboard/static/.well-known/assetlinks.json
```

Сервер уже отдаёт его по `https://remote.lumen.com/.well-known/assetlinks.json`
(роут добавлен в `dashboard/server.py`). Перезапустите LUMEN и проверьте в
браузере — должен отдаваться JSON со статусом 200.

> Если приложение открывается **с адресной строкой** браузера — assetlinks не
> совпали (отпечаток/пакет) или недоступны по URL. Проверка связки:
> `https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://remote.lumen.com&relation=delegate_permission/common.handle_all_urls`

## Шаг 5. Тест на телефоне

```powershell
bubblewrap install        # телефон по USB, включена отладка
# или просто перекиньте app-release-signed.apk и установите
```

Откройте: fullscreen-дашборд, введите PIN — работает голосовой канал,
наушники, LUMEN-камера (всё то же, что в PWA).

## Шаг 6. Google Play Console

1. **Аккаунт разработчика** — https://play.google.com/console ($25 один раз).
2. **Create app**: название `LUMEN Remote`, язык ru, категория «Инструменты».
3. **Production → New release →** загрузить `app-release-signed.aab`.
   - Включите **Play App Signing** (рекомендуется); тогда в assetlinks надо
     добавить **ещё один** fingerprint — SHA-256 из
     `Play Console → Setup → App integrity` (Play переподписывает пакет):
     `bubblewrap fingerprint add <плей-фингерпринт>` и обновить
     `dashboard/static/.well-known/assetlinks.json`.
4. Заполнить магазинную карточку:
   - Краткое описание: «Удалённое управление голосовым ассистентом LUMEN: микрофон, наушники, камера, файлы.»
   - Полное: использовать текст из секции «Capabilities» основного README.
   - Иконка 512×512 — `dashboard/static/icons/icon-512.png`.
   - Скриншоты: 2–8 шт. с телефона (портрет).
   - **Data safety**: микрофон/камера — да (аудио/видео передаются на ваш ПК, сторонам не передаются); геолокация — нет; файлы — по запросу.
   - **Privacy policy**: нужен публичный URL (подойдёт raw-страница в репозитории).
   - Контент-рейтинг: анкета IARC.
5. Внутреннее тестирование → затем Production.

## Обновления приложения

Изменили web-часть → обновлять приложение обычно **не нужно** (оно показывает
сайт). Новый билд требуется только при смене домена/иконки/имени:

```powershell
bubblewrap update          # подтянуть изменения из web-манифеста
bubblewrap build           # новый AAB (versionCode увеличится сам)
```

## Замечания

- TWA-приложение работает **только пока жив туннель/ПК** — оно отображает
  ваш дашборд, а не самостоятельный ассистент.
- Один и тот же `assetlinks.json` можно держать и для пути
  через Workers (`workers.dev`) — но origin в TWA должен быть один:
  выберите основной адрес (`remote.lumen.com`) и им пользуйтесь.
- Шаблон файла — `play-store/assetlinks.template.json` (обычно проще
  сгенерировать через `bubblewrap fingerprint generateAssetLinks`).

