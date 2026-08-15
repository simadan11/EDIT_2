<#
═══════════════════════════════════════════════════════════════════════════
 EDIT — Remote Control "под ключ" за одну команду (Windows 10/11)

 Что скрипт делает сам:
   1. Ставит cloudflared (если нет)                → winget
   2. Логин в Cloudflare (ОДИН клик в браузере: выбрать зону edit.com)
   3. Создаёт именной туннель  edit-remote
   4. Пишет config.yml (ingress → http://localhost:8000 — дашборд EDIT)
   5. Создаёт DNS-запись  remote.edit.com → туннель
   6. Регистрирует туннель как службу Windows (автозапуск с ПК)
   7. Прописывает в EDIT:  internet_tunnel + tunnel_static_url
   8. (-NoWorker отключает) Устанавливает Wrangler, деплоит Workers-фасад
      со случайным SECRET и подставляет workers.dev URL в конфиг EDIT

 Запуск (PowerShell из папки репозитория):
   powershell -ExecutionPolicy Bypass -File cloudflare\setup-remote-control.ps1

 Параметры:
   -Domain edit.com        # ваш домен на Cloudflare DNS
   -Sub remote             # поддомен панели (→ remote.<Domain>)
   -TunnelName edit-remote # имя туннеля
   -NoWorker               # только туннель, без Workers-фасада
   -SkipService            # не ставить службу Windows

 Ручные действия (неизбежны — логиниться в ваш аккаунт за вас нельзя):
   • 1 клик в браузере на шаге cloudflared login (выбрать домен)
   • 1 клик в браузере на шаге wrangler login (только Workers)
   • если edit.com ещё не привязан к Cloudflare: добавить сайт в панели
     Cloudflare и сменить NS у регистратора — скрипт об этом напомнит.
═══════════════════════════════════════════════════════════════════════════
#>
[CmdletBinding()]
param(
    [string]$Domain     = "edit.com",
    [string]$Sub        = "remote",
    [string]$TunnelName = "edit-remote",
    [switch]$NoWorker,
    [switch]$SkipService
)

$ErrorActionPreference = "Stop"
$Fqdn     = "$Sub.$Domain"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CfDir    = $PSScriptRoot
$CfHome   = Join-Path $env:USERPROFILE ".cloudflared"
$ApiKeys  = Join-Path $RepoRoot "config\api_keys.json"

function _say([string]$t)  { Write-Host "`n══ $t" -ForegroundColor Cyan }
function _ok([string]$t)   { Write-Host "  ✔ $t" -ForegroundColor Green }
function _warn([string]$t) { Write-Host "  ⚠ $t" -ForegroundColor Yellow }
function _err([string]$t)  { Write-Host "  ✖ $t" -ForegroundColor Red }
function _run([string]$exe, [string[]]$cmdArgs, [switch]$AllowFail) {
    Write-Host "  → $exe $($cmdArgs -join ' ')" -ForegroundColor DarkGray
    $out = & $exe @cmdArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        throw "команда завершилась с кодом $LASTEXITCODE`n$out"
    }
    return $out
}
function _refresh-path() {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}
function _admin() {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object System.Security.Principal.WindowsPrincipal($id)
        ).IsInRole([System.Security.Principal.WindowsBuiltinRole]::Administrator)
}

_say "EDIT Remote Control — автонастройка через Cloudflare"
Write-Host "  Адрес панели будет:  https://$Fqdn" -ForegroundColor White
Write-Host "  Дашборд на ПК:       http://localhost:8000`n"

# ── 0. Повышение прав (служба требует администратора) ──────────────────────
if (-not $SkipService -and -not (_admin)) {
    _warn "Нужны права администратора для службы Windows — перезапускаю от админа…"
    $psExe = (Get-Process -Id $PID).Path
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Domain `"$Domain`" -Sub `"$Sub`" -TunnelName `"$TunnelName`""
    if ($NoWorker) { $argList += " -NoWorker" }
    Start-Process $psExe -Verb RunAs -ArgumentList $argList -Wait
    _ok "Админ-запуск завершил работу — см. его окно."
    exit 0
}

# ── 1. cloudflared ─────────────────────────────────────────────────────────
_say "1/8  cloudflared"
$cl = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
if (-not $cl) {
    Write-Host "  Ставлю cloudflared через winget…"
    _run "winget" @("install", "--id", "Cloudflare.cloudflared", "-e",
                    "--accept-source-agreements", "--accept-package-agreements")
    _refresh-path
    $cand = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if (Test-Path $cand) { $cl = @{ Source = $cand } }
}
if (-not $cl) { $cl = Get-Command cloudflared.exe -ErrorAction SilentlyContinue }
if (-not $cl) { throw "cloudflared не найден даже после установки. Перезапустите PowerShell и повторите." }
$CFD = $cl.Source
_ok "cloudflared: $CFD"

# ── 2. Логин в Cloudflare (единственный клик) ──────────────────────────────
_say "2/8  Логин Cloudflare"
$certPem = Join-Path $CfHome "cert.pem"
if (Test-Path $certPem) {
    _ok "cert.pem уже есть — логин пропускаю"
} else {
    _warn "Сейчас откроется браузер: войдите в Cloudflare и ВЫБЕРИТЕ сайт $Domain"
    _run $CFD @("login")
    if (-not (Test-Path $certPem)) { throw "Логин не завершён — cert.pem не появился." }
    _ok "Логин выполнен"
}

# ── 3. Туннель ─────────────────────────────────────────────────────────────
_say "3/8  Туннель '$TunnelName'"
$existing = _run $CFD @("tunnel", "list", "--output", "json") -AllowFail
$TunnelId = $null
try {   # вариант 1: JSON-вывод
    $t = ($existing | ConvertFrom-Json) | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
    if ($t) { $TunnelId = $t.id }
} catch { }
if (-not $TunnelId) {   # вариант 2: текстовая таблица (предупреждения в stderr ломают JSON)
    foreach ($ln in ($existing -split "\r?\n")) {
        if ($ln -match [regex]::Escape($TunnelName) -and
            $ln -match "([a-f0-9]{8}\-[a-f0-9]{4}\-[a-f0-9]{4}\-[a-f0-9]{4}\-[a-f0-9]{12})") {
            $TunnelId = $Matches[1]; break
        }
    }
}
if ($TunnelId) {
    _ok "Туннель уже существует: $TunnelId"
} else {
    $out = _run $CFD @("tunnel", "create", $TunnelName)
    if ($out -match "with id ([a-f0-9\-]{36})") { $TunnelId = $Matches[1] }
    elseif ($out -match "([a-f0-9]{8}\-[a-f0-9]{4}\-[a-f0-9]{4}\-[a-f0-9]{4}\-[a-f0-9]{12})") { $TunnelId = $Matches[1] }
    else { throw "Не смог распарсить ID туннеля из вывода:`n$out" }
    _ok "Туннель создан: $TunnelId"
}

# ── 4. config.yml ──────────────────────────────────────────────────────────
_say "4/8  Конфиг ingress → http://localhost:8000"
$cfgYml = @"
tunnel: $TunnelId
credentials-file: $CfHome\$TunnelId.json

ingress:
  - hostname: $Fqdn
    service: http://localhost:8000
  - service: http_status:404
"@
New-Item -ItemType Directory -Force -Path $CfHome | Out-Null
Set-Content -Path (Join-Path $CfHome "config.yml") -Value $cfgYml -Encoding UTF8
_run $CFD @("tunnel", "ingress", "validate") -AllowFail | Out-Null
_ok "config.yml записан"

# ── 5. DNS ─────────────────────────────────────────────────────────────────
_say "5/8  DNS: $Fqdn → туннель"
$dnsOut = _run $CFD @("tunnel", "route", "dns", $TunnelName, $Fqdn) -AllowFail
if ($dnsOut -match "already|exists|Failed to add route" -and $LASTEXITCODE -ne 0) {
    if ($dnsOut -match "already") {
        _ok "DNS-запись уже существует — нормально"
    } else {
        _err "DNS-запись не создана. Похоже, $Domain ещё не привязан к вашему аккаунту Cloudflare."
        Write-Host @"

  Что сделать вручную (один раз, ~2 минуты):
    1. dash.cloudflare.com → Add site → введите $Domain → Free plan
    2. Cloudflare покажет 2 nameserver'а — впишите их у регистратора домена
    3. Дождитесь зелёного статуса (обычно 5–30 минут)
    4. Перезапустите этот скрипт — всё остальное уже сделано.
"@ -ForegroundColor Yellow
        throw "Сначала добавьте $Domain в Cloudflare и смените NS."
    }
} else {
    _ok "DNS route готов: https://$Fqdn"
}

# ── 6. Служба Windows ──────────────────────────────────────────────────────
if (-not $SkipService) {
    _say "6/8  Служба Windows (автозапуск туннеля)"
    try {
        $svcHome = "C:\Windows\System32\config\systemprofile\.cloudflared"
        New-Item -ItemType Directory -Force -Path $svcHome | Out-Null
        Copy-Item $certPem $svcHome -Force
        Copy-Item (Join-Path $CfHome "$TunnelId.json") $svcHome -Force

        $svcCfg = @"
tunnel: $TunnelId
credentials-file: $svcHome\$TunnelId.json
logfile: $svcHome\cloudflared.log

ingress:
  - hostname: $Fqdn
    service: http://localhost:8000
  - service: http_status:404
"@
        Set-Content -Path (Join-Path $svcHome "config.yml") -Value $svcCfg -Encoding UTF8

        if (-not (Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue)) {
            _run $CFD @("service", "install")
        }
        Set-Service -Name cloudflared -StartupType Automatic
        Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\cloudflared" `
            -Name ImagePath `
            -Value "`"$CFD`" --config=`"$svcHome\config.yml`" tunnel run"
        Start-Service cloudflared -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        if ((Get-Service cloudflared).Status -eq "Running") { _ok "Служба cloudflared запущена, автозапуск включён" }
        else { _warn "Служба создана, но статус не Running — проверьте лог $svcHome\cloudflared.log" }
    } catch {
        _warn "Службу поставить не вышло: $($_.Exception.Message)"
        _warn "План Б: туннель вручную при необходимости —  cloudflared tunnel run $TunnelName"
    }
} else {
    _say "6/8  Служба пропущена (-SkipService)"
    _warn "Туннель вручную:  cloudflared tunnel run $TunnelName"
}

# ── 7. Прописать адрес в EDIT ──────────────────────────────────────────────
_say "7/8  Конфиг EDIT (config\api_keys.json)"
$panelUrl = "https://$Fqdn"

# ── 8. Workers-фасад ───────────────────────────────────────────────────────
$workerUrl = ""
if (-not $NoWorker) {
    _say "8/8  Workers-фасад (workers.dev)"
    try {
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) {
            Write-Host "  Ставлю Node.js LTS через winget…"
            _run "winget" @("install", "--id", "OpenJS.NodeJS.LTS", "-e",
                            "--accept-source-agreements", "--accept-package-agreements")
            _refresh-path
            $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        }
        if (-not $npm) { throw "npm не найден — перезапустите PowerShell после установки Node.js" }

        $wr = Get-Command wrangler.cmd -ErrorAction SilentlyContinue
        if (-not $wr) {
            Write-Host "  Ставлю wrangler (npm i -g)…"
            _run $npm.Source @("i", "-g", "wrangler")
            _refresh-path
            $wr = Get-Command wrangler.cmd -ErrorAction SilentlyContinue
        }
        if (-not $wr) { throw "wrangler не установился" }
        $WR = $wr.Source

        # ORIGIN в wrangler.toml. SECRET храним как секрет воркера, а не в git-файле.
        $tomlPath = Join-Path $CfDir "wrangler.toml"
        $toml = Get-Content $tomlPath -Raw
        $toml = $toml -replace 'ORIGIN = "[^"]*"', "ORIGIN = `"https://$Fqdn`""
        Set-Content -Path $tomlPath -Value $toml -Encoding UTF8

        # wrangler login — один клик в браузере
        $wrConf = Join-Path $env:USERPROFILE ".wrangler\config\default.toml"
        if (-not (Test-Path $wrConf)) {
            _warn "Сейчас откроется браузер: разрешите доступ Wrangler к вашему аккаунту Cloudflare"
            _run $WR @("login")
        }

        $generated = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
        $secFile = Join-Path $env:TEMP "edit-worker-secret.txt"
        try {
            Set-Content -Path $secFile -Value $generated -Encoding ASCII -NoNewline
            Get-Content $secFile -Raw | & $WR secret put SECRET --config $tomlPath | Out-Host
        } finally {
            Remove-Item $secFile -Force -ErrorAction SilentlyContinue
        }

        $dep = _run $WR @("deploy", "--config", $tomlPath)
        if ($dep -match "(https://[a-zA-Z0-9\-\.]+\.workers\.dev)") {
            $workerUrl = $Matches[1]
            $sec = $generated
            _ok "Worker задеплоен: $workerUrl"
            Write-Host "  Открывать на телефоне:  $workerUrl/?k=$sec" -ForegroundColor Green
            $panelUrl = $workerUrl
        } else {
            _warn "Не смог прочитать workers.dev URL из вывода wrangler — посмотрите лог выше."
        }
    } catch {
        _warn "Workers-фасад пропущен: $($_.Exception.Message)"
        _warn "Без него панель всё равно работает: https://$Fqdn"
    }
}

# Записываем итоговый URL в конфиг EDIT (merge — остальные поля сохраняются)
$ht = @{}
if (Test-Path $ApiKeys) {
    try {
        (Get-Content $ApiKeys -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $ht[$_.Name] = $_.Value }
    } catch { }
}
$ht["internet_tunnel"]  = $true
$ht["tunnel_static_url"] = $panelUrl
$ht | ConvertTo-Json -Depth 10 | Set-Content -Path $ApiKeys -Encoding UTF8
_ok "tunnel_static_url → $panelUrl (internet_tunnel: true)"

# ── Итог ───────────────────────────────────────────────────────────────────
_say "ГОТОВО"
if ($workerUrl) {
    Write-Host "  📱 Откройте на телефоне:  $workerUrl/?k=<ваш-секрет>" -ForegroundColor White
    Write-Host "     (workers.dev — через Workers; SECRET задан через wrangler secret)"
} else {
    Write-Host "  📱 Откройте на телефоне:  https://$Fqdn" -ForegroundColor White
}
Write-Host @"

  Первый вход: введите PIN из панели Remote Control приложения (⚙️).
  Телефон запомнит device-token — дальше вход автоматический, хоть на 4G.
  Автозапуск: туннель — службой Windows; EDIT запускает дашборд сам.
"@
Read-Host "Нажмите Enter, чтобы закрыть окно"
