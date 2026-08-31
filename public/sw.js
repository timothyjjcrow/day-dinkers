/* Third Shot service worker: fast app-shell caching with an offline fallback. */
// Bump the cache revision whenever an in-place v15 shell changes. The asset
// path release guards major transitions; this revision makes existing v15
// installs refresh the simplified Play, Courts, Community, and Settings shell
// instead of retaining old executable bytes.
const CACHE = 'thirdshot-v15-r12';
const CORE_SHELL = ['/', '/styles-v15.css?v=r12', '/crew-planner-v15.js?v=r12', '/app-v15.js?v=r12'];
const OPTIONAL_SHELL = ['/manifest.webmanifest', '/icon-512.png', '/icon-maskable.png', '/logo.jpg'];
const NAVIGATION_TIMEOUT_MS = 1200;

async function cacheShellAsset(cache, url) {
  const response = await fetch(new Request(url, { cache: 'reload' }));
  if (!response.ok) throw new Error(`Could not cache ${url}: ${response.status}`);
  await cache.put(url, response);
}

function cacheSuccessful(request, response) {
  if (!response.ok) return Promise.resolve();
  return caches.open(CACHE).then((cache) => cache.put(request, response.clone()));
}

function fetchWithTimeout(request, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(request, { signal: controller.signal })
    .finally(() => clearTimeout(timer));
}

function isStaticAsset(request, url) {
  return ['style', 'script', 'image', 'font', 'manifest', 'worker'].includes(request.destination)
    || /\.(?:css|js|mjs|png|jpe?g|svg|webp|gif|ico|woff2?|webmanifest)$/i.test(url.pathname);
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then(async (cache) => {
      // Keep the previous worker active if the executable shell is incomplete,
      // while letting a missing icon or logo remain an optional install detail.
      await Promise.all(CORE_SHELL.map((url) => cacheShellAsset(cache, url)));
      await Promise.allSettled(OPTIONAL_SHELL.map((url) => cacheShellAsset(cache, url)));
    }).then(() => self.skipWaiting()),
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

  if (event.request.mode === 'navigate') {
    const networkResponse = fetchWithTimeout(event.request, NAVIGATION_TIMEOUT_MS);
    const cacheUpdate = networkResponse.then((response) => cacheSuccessful(event.request, response));
    event.waitUntil(cacheUpdate.catch(() => {}));
    event.respondWith((async () => {
      try {
        const response = await networkResponse;
        if (response.ok || response.status < 500) return response;
      } catch { /* timeout or offline: use the app shell */ }
      return (await caches.match('/')) || new Response('Third Shot is offline.', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' },
      });
    })());
    return;
  }

  if (!isStaticAsset(event.request, url)) return;
  const cachedResponse = caches.open(CACHE).then((cache) => cache.match(event.request));
  const networkResponse = fetch(event.request);
  const cacheUpdate = networkResponse.then((response) => cacheSuccessful(event.request, response));
  event.waitUntil(cacheUpdate.catch(() => {}));
  event.respondWith((async () => {
    const cached = await cachedResponse;
    if (cached) return cached;
    return networkResponse;
  })());
});


// --- Web push ---
let pushAuthorizedForCurrentSession = true;
self.addEventListener('message', (event) => {
  if (event.data?.type === 'push-auth-state') {
    pushAuthorizedForCurrentSession = event.data.enabled === true;
  }
});

self.addEventListener('push', (event) => {
  if (!pushAuthorizedForCurrentSession) return;
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* opaque payload */ }
  const title = data.title || 'Third Shot';
  let destination = '/';
  try {
    const requested = new URL(data.url || '/', self.registration.scope);
    if (requested.origin === self.location.origin) {
      destination = `${requested.pathname}${requested.search}${requested.hash}`;
    }
  } catch { /* malformed destinations fall back to the app home */ }
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/icon-512.png',
    badge: '/icon-512.png',
    data: { url: destination },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  let destination = new URL('/', self.registration.scope).href;
  try {
    const requested = new URL(event.notification.data?.url || '/', self.registration.scope);
    if (requested.origin === self.location.origin) destination = requested.href;
  } catch { /* malformed destinations fall back to the app home */ }
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const win of wins) {
        if (!win.url.startsWith(self.registration.scope)) continue;
        // A focused SPA should route in place. Fragment navigation itself adds
        // a browser-history entry before the page can react, producing ghost
        // Back stops (and identical fragments may emit no hashchange at all).
        win.postMessage({ type: 'open-overlay-route', url: destination });
        return win.focus();
      }
      return clients.openWindow(destination);
    }),
  );
});
