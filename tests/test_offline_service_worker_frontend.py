from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
SW = (ROOT / "public" / "sw.js").read_text()


def test_update_waits_for_an_explicit_accessible_refresh_action():
    boot = APP[APP.index("async function boot()") :]

    assert "registration.addEventListener('updatefound'" in boot
    assert "navigator.serviceWorker.addEventListener('controllerchange'" in boot
    assert "if (registration.waiting) showServiceWorkerUpdatePrompt();" in boot
    assert "toast('A new version is ready.'" in boot
    assert "persistent: true" in boot
    assert "action: { label: 'Refresh', onClick: applyServiceWorkerUpdate }" in boot
    assert "const isAlert = presentation.tone === 'error' || presentation.tone === 'warning';" in APP
    assert "el.setAttribute('role', isAlert ? 'alert' : 'status');" in APP
    assert 'class="toast-action"' in APP
    assert 'aria-label="Dismiss message"' in APP
    assert "waiting.postMessage({ type: 'skip-waiting' });" in boot
    assert "setTimeout(reloadForServiceWorkerUpdate, 1800);" in boot
    assert "if (serviceWorkerReloadStarted) return;" in boot
    assert ".then(() => self.skipWaiting())" not in SW
    assert "event.data?.type === 'skip-waiting'" in SW


def test_versioned_shell_and_pinned_leaflet_are_available_offline():
    assert "const CACHE = 'thirdshot-v15-r61';" in SW
    for asset in (
        "/release-assets/r59/styles-v15.min.css",
        "/release-assets/r59/crew-planner-v15.min.js",
        "/release-assets/r59/app-v15.min.js",
        "/vendor/leaflet/leaflet.css?v=1.9.4",
        "/vendor/leaflet-markercluster/MarkerCluster.css?v=1.5.3",
        "/vendor/leaflet/leaflet.js?v=1.9.4",
        "/vendor/leaflet-markercluster/leaflet.markercluster.js?v=1.5.3",
        "/vendor/leaflet/images/marker-icon.png",
    ):
        assert asset in SW
    assert "https://unpkg.com" not in SW
    assert "https://unpkg.com" not in APP
    for path in (
        ROOT / "public/vendor/leaflet/leaflet.js",
        ROOT / "public/vendor/leaflet/leaflet.css",
        ROOT / "public/vendor/leaflet-markercluster/leaflet.markercluster.js",
        ROOT / "public/vendor/leaflet-markercluster/MarkerCluster.css",
    ):
        assert path.is_file() and path.stat().st_size > 500
    assert "await Promise.all(CORE_SHELL.map" in SW
    assert "if (isLeafletShellAsset(url))" in SW
    assert "cacheFirstLeafletAsset(event.request)" in SW
    assert "const cachedResponse = caches.match(event.request);" in SW
    assert "const priorShell = keys" in SW


def test_viewed_map_tiles_use_a_bounded_runtime_cache_and_offline_fallback():
    assert "const MAP_TILE_ORIGIN = 'https://tile.openstreetmap.org';" in SW
    assert "const MAX_MAP_TILES = 160;" in SW
    assert "if (isMapTile(url))" in SW
    assert "staleWhileRevalidateMapTile(event)" in SW
    assert "await trimCache(cache, MAX_MAP_TILES);" in SW
    assert "Map unavailable offline" in SW
    assert "'Cache-Control': 'no-store'" in SW


def test_offline_copy_does_not_promise_uncached_fresh_data():
    assert "You're offline. Fresh results and actions need a connection." in INDEX
    assert "saved details stay available" not in INDEX.lower()
