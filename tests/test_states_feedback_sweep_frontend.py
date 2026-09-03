import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_detail_destinations_open_loading_shells_and_fail_with_retry_and_fallback():
    assert "function openDetailLoadShell" in APP
    assert 'class="detail-load-progress" aria-hidden="true"' in APP
    assert "renderDetailLoadError(" in APP
    assert "detailLoadFallback(shell.route)" in APP
    assert 'data-goto="${fallback.goto}"' in APP
    for opener in (
        "openGameScreen", "openUserProfile", "openCrewScreen", "openClubScreen",
        "openTournamentScreen", "openLeagueScreen", "openActivity",
    ):
        body = section(f"async function {opener}", "\n  function " if opener == "openActivity" else "\n  async function ")
        assert "openDetailLoadShell({" in body
    court = section("async function openCourtDetail", "function openCourtPlayerActions")
    assert court.index("const modal = reuseModal || openModal(") < court.index("await api(`/courts/${normalizedCourtId}`)")
    assert 'class="detail-load-progress" aria-hidden="true"' in court
    assert "data-retry-court-detail" in court
    assert 'data-goto="courts-list"' in court
    assert "data-retry-court-refresh" in court
    assert "The previous details are still shown." in court


def test_toasts_are_queued_wrapping_actionable_and_long_errors_stay_longer():
    toast = section("function toastPresentation", "function clearToasts")
    assert "const toastQueue = []" in toast
    assert "activeToasts.size < 2" in toast
    assert "toast-copy" in toast and "toast-action" in toast and "toast-dismiss" in toast
    assert "presentation.text.length * 45" in toast
    assert "presentation.tone === 'error'" in toast
    assert "Math.max(10000, 5000 + presentation.text.length * 55)" in toast
    assert "Math.max(8000, requestedVisibleFor)" in toast
    assert "white-space: normal" in CSS
    assert ".toast-action" in CSS


def test_unknown_api_errors_have_safe_status_copy_and_never_show_snake_case():
    errors = section("function humanError", "const TOAST_GLYPH_ICONS")
    assert "normalized.endsWith('_not_found')" in errors
    assert "Number(status) === 403" in errors
    assert "Number(status) === 409" in errors
    assert "Number(status) === 400 || Number(status) === 422" in errors
    assert "Something went wrong. Try again" in errors
    assert "code.replace(/_/g" not in errors
    assert "Unmapped API error code" in errors


def test_offline_copy_matches_available_snapshot_and_transient_failures_recover():
    assert "saved details stay available" not in INDEX
    connectivity = section("function setupConnectivity", "function setupServiceWorkerRouteMessages")
    states = section("function setConnectionState", "function clearBootRetrySchedule")
    assert "setConnectionState('degraded')" in connectivity
    assert "await probeConnection()" in connectivity
    assert "showing details saved ${age}" in states
    assert "Actions wait until you reconnect" in states
    api_error = section("async function api", "function persistReplacementToken")
    assert "if (!navigator.onLine) setConnectionState('offline')" in api_error
    assert "else setConnectionState('degraded')" in api_error


def test_stale_snapshot_does_not_resurrect_an_expired_live_game_banner():
    helper = section("function activeGameFromSnapshot", "function invalidateMeRequests")
    boot = APP[APP.index("async function boot()") :]
    assert "snapshotAge > 10 * 60_000" in helper
    assert "now > scheduledAt + 4 * 3_600_000" in helper
    assert "active_game: activeGameFromSnapshot(snapshot.data.active_game, snapshotSavedAt)" in boot
    assert boot.index("state.snapshotSavedAt = snapshotSavedAt") < boot.index("active_game: activeGameFromSnapshot")


def test_feeds_retain_successful_content_and_profile_sections_fail_independently():
    shared = section("function beginViewRender", "function initials")
    assert "const hasUsableContent" in shared
    assert "if (!hasUsableContent)" in shared
    assert "retainViewAfterError" in shared
    profile = section("async function renderProfile", "function openEditProfile")
    assert "showProfileSectionUnavailable(" in profile
    assert profile.count("showProfileSectionUnavailable(") == 4
    assert "Showing your last update" in profile
    assert "reuseDashboard" in profile


def test_bare_list_failures_are_retryable_and_empty_destinations_have_actions():
    assert "renderError(el, e.message, () => renderNearbyPlayers(el))" in APP
    assert "data-profile-court-search-retry" in APP
    assert "data-eg-court-search-retry" in APP
    assert "data-lg-court-search-retry" in APP
    assert "data-ng-court-search-retry" in APP
    assert "function emptyStateHtml" in APP
    assert "Actionable empty states require a primary action" in APP
    assert "data-create-community" in APP
    assert 'data-goto="new-game"' in APP
    assert 'Plan a play session' in APP
    assert ".empty-state-actions" in CSS


def test_chat_failures_retry_fast_and_explain_reconnection_after_repeated_misses():
    poll = section("function startAdaptiveChatPoll", "function prepareChatRenderBatch")
    assert "let failed = false" in poll
    assert "consecutiveFailures >= 2" in poll
    assert "Reconnecting… New messages may be delayed. Your draft is safe." in poll
    assert "let fastUntil = startedAt + 2 * 60_000;" in poll
    assert "Date.now() < fastUntil" in poll
    assert "idleIndex = failed || changed || activityRevision !== revisionAtStart || inResponsiveWindow" in poll
    assert "? 0 : Math.min(idleIndex + 1" in poll


def test_courts_refresh_in_place_on_timer_pull_and_desktop_action():
    setup = section("function setupMap()", "async function maybeAutoLocateCourts")
    pull = section("function setupPullToRefresh", "function setupEmptyStateCtas")
    fetch = section("async function fetchCourtsInView", "function safePositiveId")
    assert "Refreshing live court activity…" in setup
    assert "60_000" in setup
    assert "fetchCourtsInView({ surfaceError: true })" in setup
    assert "['#tab-courts', '#court-list-items'" in pull
    assert "beginCourtContextRefresh('Refreshing courts…')" in pull
    assert fetch.count("renderCourtContextError(err") == 2
    assert "markViewReady('courts')" in fetch
    assert 'data-refresh-view="courts"' in INDEX


def test_map_refresh_diffs_layers_without_blanketing_existing_results():
    setup = section("function setupMap()", "async function maybeAutoLocateCourts")
    begin = section("function beginCourtContextRefresh", "function renderCourtContextError")
    markers = section("function drawMarkers", "function setCourtMarkerSelected")
    assert "state.courtMoveFetchTimer = setTimeout" in setup
    assert "}, 400);" in setup
    assert "const hasResults = state.courtsInView.length > 0" in begin
    assert "if (hasResults)" in begin
    assert "state.markers?.clearLayers?.()" in begin  # only the true first-load branch
    assert "const nextIds = new Set" in markers
    assert "state.markers.removeLayer(entry.marker)" in markers
    assert "existing.marker.setLatLng" in markers
    assert "existing.marker.setIcon" in markers
    assert "state.markers.clearLayers()" not in markers


def test_form_validation_and_destructive_actions_use_persistent_in_app_feedback():
    review = section("function renderReviewSection", "function gameToIcs")
    score = section("function openScoreModal", "const T_FORMAT_LABEL")
    feedback = section("async function renderProfile", "function openEditProfile")
    assert "showError('Pick a star rating first.')" in review
    assert "showError(err.message)" in review
    assert "formUX.showError('Each side needs at least one player.')" in score
    assert "formUX.showError(`Game ${index + 1} cannot end tied." in score
    assert "formUX.showError(err.message)" in score
    assert "formUX.showError('Share at least a few words" in feedback
    assert "window.confirm" not in APP
    assert "openActionConfirmation({" in APP
    activity = section("async function openActivity", "function renderPresenceBanner")
    assert "const clearAll = async (trigger)" in activity
    assert "title: 'Clear all activity?'" in activity
    assert "Undo" in activity


def test_game_poll_preserves_sheet_state_and_score_conflicts_remain_actionable():
    capture = section("function captureGameViewState", "function gameScreenHtml")
    game = section("async function openGameScreen", "function safeNotificationOverlayRoute")
    score = section("function openScoreModal", "const T_FORMAT_LABEL")
    assert "detailsOpen" in capture
    assert "box.scrollTop = snapshot.scrollTop" in capture
    assert "focusId" in capture
    assert "injectScoreConflictBanner" in capture
    assert "You had ${esc(entered)} entered" in capture
    assert 'data-score-conflict-action="confirm"' in capture
    assert "render(fresh, { preserve: true, announce: true })" in game
    assert "pendingScoreConflicts.set(Number(game.id)" in score
    assert "enteredScore1" in score and "enteredScore2" in score
    assert "already reported a score" not in score


def test_compact_status_regions_replace_whole_feed_announcements_and_notifications_open():
    assert 'id="play-content" class="tab-scroll" role="tabpanel"' in INDEX
    assert 'id="play-content" class="tab-scroll" aria-live' not in INDEX
    assert 'id="sr-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"' in INDEX
    apply_me = section("function applyMe", "function dismissedInvites")
    assert "action: { label: 'Open', onClick: () => openNotificationTarget(latest) }" in apply_me
    assert "duration: 6500" in apply_me


def test_reduced_motion_loaders_remain_meaningful_and_location_names_its_state():
    assert 'id="court-search-status" class="sr-only" role="status" aria-live="polite"' in INDEX
    locate = section("function locateMe", "function committedAreaLatLng")
    assert "setAttribute('aria-label', 'Locating…')" in locate
    reduced = CSS[CSS.rindex("@media (prefers-reduced-motion: reduce)") :]
    for selector in (".search-spin", ".map-load-spinner", ".play-now-loading .spinner", ".ptr-spinner.spin::before"):
        assert selector in reduced
    assert "animation: none !important" in reduced
    assert "border-style: dotted" in reduced


def test_every_primary_view_has_age_and_click_refresh_and_age_logic_runs():
    for tab in ("courts", "play", "chat", "profile"):
        assert f'data-refresh-view="{tab}"' in INDEX
    freshness = section("function viewAgeLabel", "function viewIsFresh")
    assert "setupViewFreshness" in freshness
    assert "markViewReady" in freshness
    function_source = section("function viewAgeLabel", "function viewSurface")
    script = function_source + "\nconsole.log(JSON.stringify([viewAgeLabel(Date.now()), viewAgeLabel(Date.now() - 125000)]));"
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert json.loads(completed.stdout) == ["Updated just now", "Updated 2 min ago"]


def test_refresh_copy_is_human_and_detail_progress_is_delayed_not_instant_noise():
    error = section("function renderCourtContextError", "function mapViewStorageKey")
    assert "Couldn’t load courts here" in error
    assert "Showing courts from your last search" in error
    assert "Check your connection, then try again" in error
    assert "No old-area results are being shown" not in error
    assert re.search(r"\.detail-load-progress\s*\{[^}]*animation:[^;]*\.3s", CSS, re.S)
