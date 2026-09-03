"""Focused source and runtime contracts for the global application shell."""

from pathlib import Path
import json
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
INDEX = (ROOT / 'public' / 'index.html').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def app_section(start, end):
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_boot_has_unbundled_failure_watchdog_and_caught_boot_promise():
    assert 'id="boot-retry">Reload</button>' in INDEX
    assert 'window.__thirdShotShowBootFailure = showRecovery;' in INDEX
    assert "window.addEventListener('error', handleBundleError, true);" in INDEX
    assert "'bundle-error'" in INDEX
    assert "'launch-timeout'" in INDEX
    assert "retry.onclick = () => location.reload();" in INDEX
    assert '}, 12000);' in INDEX

    boot_tail = APP[APP.rindex('boot().catch'):]
    assert "console.error('Third Shot failed to boot', error);" in boot_tail
    assert "globalThis.__thirdShotShowBootFailure(message, 'boot-error');" in boot_tail
    assert "showBootRecovery(message, 'boot-error');" in boot_tail


def test_inline_watchdog_runs_without_the_application_bundle():
    inline_scripts = re.findall(r'<script>(.*?)</script>', INDEX, flags=re.DOTALL)
    watchdog = next(
        source for source in inline_scripts
        if '__thirdShotShowBootFailure' in source
    )
    harness = f"""
      const assert = require('assert');
      class ClassList {{
        constructor(values = []) {{ this.values = new Set(values); }}
        add(value) {{ this.values.add(value); }}
        remove(value) {{ this.values.delete(value); }}
        contains(value) {{ return this.values.has(value); }}
      }}
      const progress = {{ classList: new ClassList() }};
      const retry = {{ classList: new ClassList(['hidden']), textContent: '', onclick: null }};
      const message = {{ textContent: '' }};
      const boot = {{
        classList: new ClassList(), dataset: {{}},
        querySelector: (selector) => selector === '.boot-progress' ? progress : null,
      }};
      const auth = {{ classList: new ClassList(['hidden']) }};
      const main = {{ classList: new ClassList(['hidden']) }};
      const elements = {{
        'boot-screen': boot, 'boot-retry': retry, 'boot-message': message,
        'auth-screen': auth, 'main-screen': main,
      }};
      const listeners = {{}};
      const timers = [];
      globalThis.window = globalThis;
      globalThis.document = {{ getElementById: (id) => elements[id] || null }};
      globalThis.location = {{ reloads: 0, reload() {{ this.reloads += 1; }} }};
      globalThis.addEventListener = (type, fn) => {{ listeners[type] = fn; }};
      globalThis.removeEventListener = (type, fn) => {{
        if (listeners[type] === fn) delete listeners[type];
      }};
      globalThis.MutationObserver = class {{
        constructor(fn) {{ this.fn = fn; }}
        observe() {{}}
        disconnect() {{}}
      }};
      globalThis.setTimeout = (fn, ms) => {{ timers.push({{ fn, ms }}); return timers.length; }};
      globalThis.clearTimeout = () => {{}};
      eval({json.dumps(watchdog)});

      timers.find((timer) => timer.ms === 12000).fn();
      assert.equal(boot.dataset.recoveryState, 'launch-timeout');
      assert.equal(retry.textContent, 'Reload');
      assert.equal(retry.classList.contains('hidden'), false);
      assert.match(message.textContent, /did not finish opening/);
      retry.onclick();
      assert.equal(location.reloads, 1);

      listeners.error({{ target: {{ src: 'https://example.test/app-v15.js?v=r58' }} }});
      assert.equal(boot.dataset.recoveryState, 'bundle-error');
      assert.match(message.textContent, /could not load its app files/);
    """
    subprocess.run(
        ['node', '-e', harness], check=True, capture_output=True, text=True,
    )


def test_transient_failures_degrade_and_periodically_probe_for_recovery():
    api = app_section('async function api(', '// Password and MFA mutations')
    assert "if (aborted && !requestTimedOut)" in api
    assert "cancelled.isCancelled = true;" in api
    assert "else setConnectionState('degraded');" in api
    assert "state.networkFailureCount >= 2" not in api
    assert "if (res.status >= 500)" in api

    connectivity = app_section('function setupConnectivity', 'function navigateOverlayRoute')
    assert 'function scheduleConnectionProbe(' in connectivity
    assert 'async function probeConnection()' in connectivity
    assert "fetch('/health'" in connectivity
    assert 'CONNECTION_PROBE_MAX_DELAY_MS' in connectivity
    assert 'connectionProbeDelayMs * 2' in connectivity
    assert "else scheduleConnectionProbe();" in connectivity
    assert "scheduleConnectionProbe(0);" in connectivity
    assert "window.addEventListener('focus', refreshForegroundState);" in connectivity


def test_cached_launch_shows_age_retry_and_never_logs_in_on_transport_failure():
    connection = app_section('function snapshotAgeLabel', 'function navigateOverlayRoute')
    setup = app_section('function setupConnectivity', 'function setupServiceWorkerRouteMessages')
    assert "return 'just now';" in connection
    assert 'showing details saved ${age}' in connection
    assert "$('#connection-retry')?.addEventListener" in setup

    boot = APP[APP.index('async function boot()'):]
    assert 'if (snapshot && state.token)' in boot
    assert "scheduleBootRefreshRetries();" in boot
    assert "if (state.token)" in boot
    assert "scheduleColdBootRetries();" in boot
    assert 'Only api()\'s verified 401 path' in boot
    assert boot.index('if (state.token)') < boot.index("$('#auth-screen').classList.remove('hidden');")
    assert 'active_game: activeGameFromSnapshot(snapshot.data.active_game, snapshotSavedAt)' in boot


def test_tab_switches_and_refreshes_restore_each_primary_scroll_position():
    tabs = app_section('// ---------- Tabs ----------', '// One share sheet')
    assert "tabScrollPositions: { play: 0, chat: 0, profile: 0 }" in APP
    assert 'function rememberTabScroll(tab)' in tabs
    assert 'function restoreTabScroll(tab' in tabs
    assert 'function restoreTabAfterRender(' in tabs
    assert 'if (state.tab && state.tab !== tab) rememberTabScroll(state.tab);' in tabs
    assert 'const targetScrollTop = scrollToTop ? 0' in tabs
    assert 'restoreTabAfterRender(tab, renderResult, targetScrollTop);' in tabs

    commit = app_section('function commitViewRender', 'function retainViewAfterError')
    assert 'const priorScrollTop = sameView' in commit
    assert 'state.tabScrollPositions[tab] = priorScrollTop;' in commit
    assert 'el.scrollTop = priorScrollTop;' in commit

    refresh = app_section('function refreshActiveView', 'function navigateOverlayRoute')
    assert refresh.count('renderTabPreservingScroll(') == 3
    assert 'state.tabScrollPositions = { play: 0, chat: 0, profile: 0 };' in APP


def test_primary_feeds_are_not_whole_surface_live_regions():
    for element_id in ('play-content', 'chat-content', 'profile-content'):
        tag = re.search(rf'<div id="{element_id}"[^>]*>', INDEX).group(0)
        assert 'aria-live=' not in tag
        assert 'aria-atomic=' not in tag

    normalization = app_section(
        'function normalizePrimaryFeedLiveRegions',
        'function scrollTabToTop',
    )
    assert "removeAttribute('aria-live')" in normalization
    assert "removeAttribute('aria-atomic')" in normalization
    assert 'normalizePrimaryFeedLiveRegions();' in APP


def test_connection_banner_reserves_space_and_dead_legacy_bells_are_gone():
    assert "classList.toggle('is-degraded', degraded)" in APP
    assert '.is-degraded .tab-panel' in STYLES
    assert "$('#bell-badge')" not in APP
    assert "$('#bell-btn')" not in APP
