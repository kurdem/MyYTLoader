// Service Worker: cached nur die App-Shell. API, Medien und Feeds gehen immer ans Netz.
const CACHE = 'ytl-shell-v4';
const SHELL = [
  './',
  'index.html',
  'css/app.css',
  'js/app.js',
  'js/api.js',
  'js/util.js',
  'js/views/start.js',
  'js/views/jobs.js',
  'js/views/library.js',
  'js/views/media.js',
  'js/views/more.js',
  'js/views/users.js',
  'js/views/feeds.js',
  'js/views/feed.js',
  'manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/feed/')) return;
  // Network first, Cache als Offline-Fallback -> Updates kommen sofort an
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('index.html'))),
  );
});
