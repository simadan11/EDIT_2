// ═══════════════════════════════════════════════════════════════════════════
//  EDIT — Remote Control через Cloudflare Workers
//
//  Тонкий HTTPS-прокси перед постоянным Cloudflare Tunnel URL вашего ПК.
//  Настройка: ORIGIN в wrangler.toml; SECRET желательно задавать через
//      wrangler secret put SECRET
// ═══════════════════════════════════════════════════════════════════════════

const FORWARDED_HEADERS = [
  "accept",
  "accept-encoding",
  "accept-language",
  "authorization",
  "cache-control",
  "content-length",
  "content-type",
  "cookie",
  "origin",
  "pragma",
  "range",
  "sec-websocket-extensions",
  "sec-websocket-key",
  "sec-websocket-protocol",
  "sec-websocket-version",
  "user-agent",
  "x-edit-key",
  "x-requested-with",
];

function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const ab = enc.encode(String(a));
  const bb = enc.encode(String(b));
  if (ab.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ab.length; i++) diff |= ab[i] ^ bb[i];
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const url    = new URL(request.url);
    const origin = (env.ORIGIN || "").replace(/\/+$/, "");
    const secret = env.SECRET || "";
    if (!origin) return new Response("ORIGIN not configured", { status: 500 });

    const k = url.searchParams.get("k") || request.headers.get("X-EDIT-Key");
    if (secret && !timingSafeEqual(k || "", secret)) {
      return new Response("404", { status: 404 });
    }
    url.searchParams.delete("k");

    const isWs = request.headers.get("upgrade")?.toLowerCase() === "websocket";
    const target = new URL(url.pathname + url.search, origin);
    const fwd = new Headers();
    for (const name of FORWARDED_HEADERS) {
      const value = request.headers.get(name);
      if (value) fwd.set(name, value);
    }
    // Do not set Host manually: fetch() derives the correct upstream host
    // from target; a copied Workers Host can break the Tunnel origin request.
    fwd.set("X-Forwarded-Host", url.host);
    fwd.set("X-Forwarded-Proto", "https");
    const cfIp = request.headers.get("cf-connecting-ip");
    if (cfIp) fwd.set("X-Forwarded-For", cfIp);

    const init = { method: request.method, headers: fwd, redirect: "manual" };
    if (!["GET", "HEAD"].includes(request.method)) {
      init.body = request.body;
      init.duplex = "half";
    }

    let resp;
    try {
      resp = await fetch(new Request(target.toString(), init));
    } catch (err) {
      return new Response("Origin unavailable", { status: 502 });
    }

    if (resp.status === 101 || !resp.headers) return resp;

    const h = new Headers(resp.headers);
    h.set("Cache-Control", "no-store");
    h.set("X-Content-Type-Options", "nosniff");
    h.set("Referrer-Policy", "no-referrer");
    if (!isWs) h.set("X-Frame-Options", "DENY");
    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: h,
    });
  },
};
