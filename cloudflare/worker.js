// ═══════════════════════════════════════════════════════════════════════════
//  EDIT — Remote Control через Cloudflare Workers
//
//  Что это: тонкий HTTPS-прокси («красивая входная дверь») перед стабильным
//  Cloudflare Tunnel URL вашего ПК. Телефон открывает короткий адрес вида
//      https://edit-remote.<ваш-sub>.workers.dev/?k=СЕКРЕТ
//  а Worker прозрачно пробрасывает HTTP и WebSocket на домашний дашборд.
//
//  Чего Worker НЕ делает: он не запускает самого ассистента (EDIT — локальный
//  Python-процесс: микрофон, экран, файлы). Здесь только проксирование.
//
//  Настройка: 2 переменные в wrangler.toml — ORIGIN и SECRET.
//  ORIGIN = постоянный hostname вашего именного туннеля (см. cloudflare/README.md).
//  SECRET = ваша длинная случайная строка — второй рубеж поверх PIN приложения.
// ═══════════════════════════════════════════════════════════════════════════

export default {
  async fetch(request, env) {
    const url     = new URL(request.url);
    const origin  = (env.ORIGIN || "").replace(/\/+$/, "");
    const secret  = env.SECRET || "";
    if (!origin)  return new Response("ORIGIN not configured", { status: 500 });

    // ── 1. Свой слой авторизации поверх PIN-аутентификации приложения ─────
    // Браузер не умеет слать кастомные заголовки в WebSocket, поэтому секрет
    // принимаем и из query (?k=...), и из заголовка X-EDIT-Key.
    const k = url.searchParams.get("k") || request.headers.get("X-EDIT-Key");
    if (secret && k !== secret) {
      // Специально отвечаем 404, а не 403 — чужим не подсказываем, что тут живёт сервис.
      return new Response("404", { status: 404 });
    }

    // ── 2. Проброс на домашний дашборд (HTTP + WebSocket) ─────────────────
    // Upgrade: websocket Cloudflare Workers проксируют автоматически —
    // достаточно передать исходный Request в fetch().
    const target = new URL(url.pathname + url.search, origin);
    const upstream = new Request(target.toString(), request);
    upstream.headers.set("X-Forwarded-Host", url.host);
    upstream.headers.set("X-Forwarded-Proto", "https");

    const resp = await fetch(upstream);

    // Ответ с 101 (WebSocket) трогать нельзя — возвращаем как есть.
    if (resp.status === 101 || !resp.headers) return resp;

    // Для обычных HTTP-ответов запрещаем любое кеширование на погране.
    const h = new Headers(resp.headers);
    h.set("Cache-Control", "no-store");
    return new Response(resp.body, { status: resp.status, headers: h });
  },
};
