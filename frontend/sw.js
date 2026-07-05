/* Third Shot service worker: offline fallback for the app shell.
   Network-first everywhere so deploys are never stale. */
const CACHE = 'thirdshot-v5';
const SHELL = ['/', '/styles.css', '/app.js', '/manifest.webmanifest', '/icon-512.png', '/icon-maskable.png', '/logo.jpg'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api')) return; // API is always live
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return res;
      })
      .catch(() => caches.match(event.request, { ignoreSearch: true })),
  );
});


// --- Web push ---
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* opaque payload */ }
  const title = data.title || 'Third Shot';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/icon-512.png',
    badge: '/icon-512.png',
    data: { url: '/' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const win of wins) {
        if (win.url.startsWith(self.registration.scope)) return win.focus();
      }
      return clients.openWindow('/');
    }),
  );
});
