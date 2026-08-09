/* EDIT — Remote Dashboard Service Worker
   Caches the app shell (HTML + crypto + icons) for fast startup and offline
   use.  API/WebSocket traffic is never cached — auth stays in memory. */

const CACHE = 'edit-shell-v1';
const SHELL = [
  '/',
  '/static/crypto.js',
  '/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-192.png',
  '/static/icons/icon-maskable-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Never intercept API calls or the WebSocket upgrade
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')
      || url.pathname.startsWith('/uploads/')) {
    return;
  }
  // App shell: network-first, cache fallback (always fresh when online)
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res && res.ok && url.origin === self.location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
