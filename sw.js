// Fichiers de l'app : réseau d'abord (toujours la dernière version publiée),
// copie locale si le réseau est indisponible (démarrage hors ligne ou sans GitHub).
// Les listes et les flux ne passent jamais par ce cache.
const VERSION = 'tvlibre-1.0.11';
const FILES = [
  './',
  'index.html',
  'app.css',
  'manifest.webmanifest',
  'js/main.js',
  'js/catalog.js',
  'js/builtin.js',
  'js/m3u.js',
  'js/health.js',
  'js/guide.js',
  'js/location.js',
  'js/net.js',
  'js/player.js',
  'js/store.js',
  'js/vendor/hls.light.min.js',
  'icons/icon-180.png',
  'icons/icon-192.png',
  'icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  self.skipWaiting(); // la nouvelle version prend la main tout de suite
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(FILES.map((f) => new Request(f, { cache: 'reload' })))));
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== VERSION) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return;
  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 4000);
      const res = await fetch(e.request, { cache: 'no-cache', signal: ctrl.signal });
      clearTimeout(t);
      if (res.ok) cache.put(e.request, res.clone());
      return res;
    } catch {
      const hit = await cache.match(e.request, { ignoreSearch: true });
      return hit || Response.error();
    }
  })());
});
