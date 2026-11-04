// Cache local de l'application : elle démarre sans réseau ni GitHub.
// Seuls les fichiers de l'app sont mis en cache ; les listes et les flux passent toujours par le réseau.
const VERSION = 'tvlibre-1.0.2';
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
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(FILES)));
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
  // Copie locale d'abord, réseau en secours
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || fetch(e.request)));
});

self.addEventListener('message', (e) => {
  if (e.data === 'skipWaiting') self.skipWaiting();
});
