/* Third Shot service worker: fast app-shell caching with an offline fallback. */
// Bump the cache revision whenever an in-place v15 shell changes. The asset
// path release guards major transitions; this revision makes existing v15
// installs refresh the simplified Play, Courts, Community, and Settings shell
// instead of retaining old executable bytes.
const SHELL_CACHE_PREFIX = 'thirdshot-v15-r';
const CACHE = 'thirdshot-v15-r70';
const CORE_SHELL = [
  '/',
  '/release-assets/r68/styles-v15.min.css',
  '/release-assets/r68/crew-planner-v15.min.js',
  '/release-assets/r68/app-v15.min.js',
  '/vendor/leaflet/leaflet.css?v=1.9.4',
  '/vendor/leaflet/leaflet.js?v=1.9.4',
  '/vendor/leaflet-markercluster/MarkerCluster.css?v=1.5.3',
  '/vendor/leaflet-markercluster/leaflet.markercluster.js?v=1.5.3',
  '/vendor/leaflet/images/layers.png',
  '/vendor/leaflet/images/layers-2x.png',
  '/vendor/leaflet/images/marker-icon.png',
  '/vendor/leaflet/images/marker-icon-2x.png',
  '/vendor/leaflet/images/marker-shadow.png',
];
const OPTIONAL_SHELL = ['/manifest.webmanifest', '/icon-512.png', '/icon-maskable.png', '/logo.jpg'];
const LEAFLET_SHELL = [
  '/vendor/leaflet/leaflet.css?v=1.9.4',
  '/vendor/leaflet/leaflet.js?v=1.9.4',
  '/vendor/leaflet-markercluster/MarkerCluster.css?v=1.5.3',
  '/vendor/leaflet-markercluster/leaflet.markercluster.js?v=1.5.3',
];
const MAP_TILE_CACHE = 'thirdshot-map-tiles-v1';
const MAP_TILE_ORIGIN = 'https://tile.openstreetmap.org';
const MAX_MAP_TILES = 160;
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

function isLeafletShellAsset(url) {
  return LEAFLET_SHELL.includes(`${url.pathname}${url.search}`);
}

function isMapTile(url) {
  return url.origin === MAP_TILE_ORIGIN && /\/\d+\/\d+\/\d+\.png$/.test(url.pathname);
}

async function trimCache(cache, maximumEntries) {
  const requests = await cache.keys();
  const overflow = requests.length - maximumEntries;
  if (overflow > 0) await Promise.all(requests.slice(0, overflow).map((request) => cache.delete(request)));
}

async function cacheFirstLeafletAsset(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

function offlineMapTile() {
  return new Response(
    '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect width="256" height="256" fill="#e8ece9"/><path d="M0 0 256 256M256 0 0 256" stroke="#d4dbd6" stroke-width="1"/><text x="128" y="132" text-anchor="middle" fill="#637069" font-family="system-ui,sans-serif" font-size="13">Map unavailable offline</text></svg>',
    { headers: { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'no-store' } },
  );
}

async function cacheMapTile(request, response) {
  if (!(response.ok || response.type === 'opaque')) return;
  const copy = response.clone();
  const cache = await caches.open(MAP_TILE_CACHE);
  await cache.put(request, copy);
  await trimCache(cache, MAX_MAP_TILES);
}

function staleWhileRevalidateMapTile(event) {
  const cached = caches.match(event.request);
  const network = fetch(event.request);
  event.waitUntil(network
    .then((response) => cacheMapTile(event.request, response))
    .catch(() => {}));
  return cached.then(async (response) => {
    if (response) return response;
    try {
      return await network;
    } catch {
      return offlineMapTile();
    }
  });
}

function shellCacheRevision(name) {
  if (!name.startsWith(SHELL_CACHE_PREFIX)) return -1;
  const revision = Number(name.slice(SHELL_CACHE_PREFIX.length));
  return Number.isSafeInteger(revision) ? revision : -1;
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
    }),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      // Retain one prior executable shell for a still-open page whose asset
      // URLs predate this worker. Map tiles use a separate bounded cache.
      const priorShell = keys
        .filter((key) => key !== CACHE && shellCacheRevision(key) >= 0)
        .sort((a, b) => shellCacheRevision(b) - shellCacheRevision(a))[0];
      const keep = new Set([CACHE, MAP_TILE_CACHE, priorShell].filter(Boolean));
      return Promise.all(keys
        .filter((key) => shellCacheRevision(key) >= 0 && !keep.has(key))
        .map((key) => caches.delete(key)));
    }).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (isLeafletShellAsset(url)) {
    event.respondWith(cacheFirstLeafletAsset(event.request));
    return;
  }
  if (isMapTile(url)) {
    event.respondWith(staleWhileRevalidateMapTile(event));
    return;
  }
  if (url.origin !== location.origin) return;
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
  // Search retained shells too, so an old page remains usable during the
  // explicit update prompt even when its versioned URL is no longer current.
  const cachedResponse = caches.match(event.request);
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
  if (event.data?.type === 'skip-waiting') {
    self.skipWaiting();
    return;
  }
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
