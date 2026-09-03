import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()
INDEX = (ROOT / 'public' / 'index.html').read_text()


def section(source, start, end):
    start_at = source.index(start)
    return source[start_at:source.index(end, start_at)]


def test_open_play_has_normalized_editor_today_fact_and_legacy_fallback():
    helpers = section(APP, 'const COURT_WEEKDAY_LABELS', 'function courtFeeFact')
    editor = section(APP, 'function openSuggestEditSheet', '// ---------- Modal helpers')
    detail = section(APP, 'async function openCourtDetail', 'function openCourtPlayerActions')

    assert 'function courtOpenPlayTodayFact' in helpers
    assert "'None scheduled today'" in helpers
    assert 'function courtOpenPlayScheduleHtml' in helpers
    assert 'Community-verified open play' in helpers
    for field in (
        'data-open-play-day', 'data-open-play-start', 'data-open-play-end',
        'data-open-play-level', 'data-open-play-cost', 'data-open-play-notes',
    ):
        assert field in editor
    assert 'open_play_schedule_rows: normalizedOpenPlayRows' in editor
    assert 'General note <span class="field-optional">Fallback</span>' in editor
    assert 'const structuredOpenPlay = courtOpenPlayTodayFact(court, 54);' in detail
    assert '${structuredOpenPlayHtml}' in detail
    assert '.court-open-play-row-head' in CSS
    assert '.cd-open-play-schedule' in CSS


def test_weekly_open_play_is_a_first_class_play_and_court_section():
    detail = section(APP, 'async function openCourtDetail', 'function openCourtPlayerActions')
    play = section(APP, 'async function renderPlay', 'function updatePlayHeader')

    assert "const regularPlaySessions = allCourtGames.filter" in detail
    assert "game.recurrence === 'weekly' && game.game_type !== 'ranked'" in detail
    assert 'Regular play sessions' in detail
    assert 'a live RSVP list, openings, and waitlist for each date' in detail
    assert 'const weeklySessions =' in play
    assert 'Regular group sessions' in play
    assert 'data-host-play-session' in play
    assert "recurrence: 'weekly'" in play
    assert '[...picked, ...restNearby].forEach' in play
    assert "plannerOptions.recurrence === 'weekly'" in APP
    assert 'id="ng-recurring" ${initiallyRecurring ? \'checked\' : \'\'}' in APP
    assert '.court-regular-sessions' in CSS
    assert '.play-regular-sessions' in CSS


def test_auto_checkin_requires_accurate_sustained_location():
    auto_checkin = section(APP, 'const AUTO_CHECKIN_MILES', 'function compactCourtFact')

    assert 'AUTO_CHECKIN_MAX_ACCURACY_METERS = 65' in auto_checkin
    assert 'AUTO_CHECKIN_SUSTAIN_MS = 30_000' in auto_checkin
    assert 'accuracyMeters > AUTO_CHECKIN_MAX_ACCURACY_METERS' in auto_checkin
    assert 'Number(nearest.distance_miles) + accuracyMeters / 1609.344' in auto_checkin
    assert 'now - state.autoCheckCandidateSince < AUTO_CHECKIN_SUSTAIN_MS' in auto_checkin
    assert 'const nearestPossibleDistance = distance - accuracyMeters / 1609.344;' in auto_checkin
    assert 'state.autoCheckoutDepartureFixes += 1;' in auto_checkin
    assert 'state.autoCheckoutDepartureFixes < 2' in auto_checkin
    assert 'resetAutoCheckoutDepartureConfirmation();' in auto_checkin
    assert auto_checkin.index('const presence = state.presence;') < auto_checkin.index(
        'if (now - (state.lastAutoCheckAt || 0) < 45000) return;'
    )


def test_map_search_delays_place_geocoding_and_shares_activation_semantics():
    search = section(APP, 'async function searchCourts', 'function courtMarkerIcon')
    activation = section(APP, 'function activateCourtFromDiscovery', 'function openCourtPlayMenu')

    assert 'includePlaces = false' in search
    assert '!(courtData.items || []).length' in search
    assert "q.length >= 3" in search
    assert 'courtSearchCache.set(cacheKey' in search
    assert 'Promise.all' not in search
    assert 'Number(state.selectedCourtId) === Number(court.id)' in activation
    assert 'selectCourtOnMap(court, { preserveList });' in activation
    assert "openCourtFromDiscovery(court" in activation


def test_court_detail_preserves_context_and_gives_recoverable_share_save_actions():
    context = section(APP, 'function captureCourtDetailContext', 'let pendingCourtDetailOpen')
    detail = section(APP, 'async function openCourtDetail', 'function openCourtPlayerActions')

    assert 'scrollTop: scroller?.scrollTop || 0' in context
    assert 'openDisclosures' in context
    assert 'restoreCourtDetailContext' in context
    assert "if (error?.name === 'AbortError') return;" in detail
    assert "shareButton.innerHTML = `${uiIcon('copy')}<span>Copy link</span>`" in detail
    assert "action: { label: 'Copy link'" in detail
    assert 'const applyFavoriteLocally = (favorited) =>' in detail
    assert "label: 'Undo'" in detail
    assert '[data-add-friend-inline]' not in detail


def test_court_context_strip_and_compact_metadata_are_consolidated():
    assert '<div class="court-map-hud"' not in INDEX
    assert '<div class="court-context-strip"' in INDEX
    sheet_head = INDEX[INDEX.index('class="court-sheet-head"'):INDEX.index('class="court-sheet-view-row"')]
    assert 'class="court-context-strip"' in sheet_head
    assert INDEX.index('id="presence-banner"') < INDEX.index('id="looking-banner"')
    assert INDEX.index('id="looking-banner"') < INDEX.index('id="use-map-area"')
    assert '#court-sheet-expand' not in APP
    assert '#court-sheet-expand' not in CSS
    assert '.court-card-reason' in CSS and 'font-size: 12px' in CSS
    assert '.court-card-metric span' in CSS and 'font-size: 12px' in CSS


def test_court_corrections_expose_the_pending_value_and_explicit_decisions():
    editor = section(APP, 'function openSuggestEditSheet', '// ---------- Modal helpers')

    assert 'Confirm a player’s correction' in editor
    assert 'Review the exact value before it can update the listing.' in editor
    assert "api(`/courts/${court.id}/suggestions`)" in editor
    assert 'data-suggestion-confirm' in editor
    assert 'data-suggestion-reject' in editor
    assert "button.hasAttribute('data-suggestion-confirm') ? 'confirm' : 'reject'" in editor
    assert "api(`/courts/${court.id}/suggestions/decision`" in editor
    assert '.court-pending-suggestion-actions' in CSS
    detail = section(APP, 'async function openCourtDetail', 'function openCourtPlayerActions')
    assert 'id="cd-report-closure"' in detail
    assert 'Report permanent closure' in detail
    assert 'A second player must independently confirm the report' in detail
    assert "JSON.stringify({ closed: true })" in detail


def test_structured_open_play_rows_launch_a_prefilled_weekly_session():
    helpers = section(APP, 'const COURT_WEEKDAY_LABELS', 'function courtFeeFact')
    detail = section(APP, 'async function openCourtDetail', 'function openCourtPlayerActions')

    assert 'function nextCourtOpenPlayStart' in helpers
    assert 'function courtOpenPlayDuration' in helpers
    assert 'data-plan-open-play' in helpers
    assert "modal.querySelectorAll('[data-plan-open-play]')" in detail
    assert "recurrence: 'weekly'" in detail
    assert 'recurrenceWeekdays: [row.weekday]' in detail
    assert 'scheduledAt: start.toISOString()' in detail
    assert 'durationMinutes: courtOpenPlayDuration(row)' in detail
    assert "title: 'Open play'" in detail


def test_map_uses_one_context_strip_and_court_controls_meet_touch_and_type_minimums():
    assert '.court-context-strip > :not(.hidden) ~ :not(.hidden) { display: none; }' in CSS
    assert '.court-peek-actions .btn {' in CSS
    assert '.court-open-play-row-head .icon-btn { width: var(--tap-min); min-height: var(--tap-min);' in CSS
    assert '.cd-presence-control .btn { min-height: var(--tap-min);' in CSS
    assert '.court-sheet-summary .row-sub { margin-top: 2px; font-size: var(--text-xs); }' in CSS
    assert '.court-sort-select-shell .app-select-trigger-value { color: var(--green-ink); font-size: var(--text-xs); }' in CSS


def test_court_player_actions_offer_casual_play_without_exposing_ranked_stats_in_roster():
    detail = section(APP, 'async function openCourtDetail', 'function openCourtPlayerActions')
    actions = section(APP, 'function openCourtPlayerActions', 'function openChallengeSheet')

    assert "playerSkillIdentityHtml(p, { includeDupr: false, includeMatchRating: false })" in detail
    assert 'p.ranked_wins' not in detail
    assert 'p.ranked_losses' not in detail
    assert 'Invite to a casual game' in actions
    assert 'invitees: [player]' in actions
    assert 'inviteUserIds: [player.id]' in actions
    assert "visibility: 'private'" in actions


def test_court_refresh_reuses_the_live_sheet_and_preserves_visible_context():
    refresh = section(APP, 'function refreshCourtDetailPreservingContext', 'let pendingCourtDetailOpen')
    detail = section(APP, 'async function openCourtDetail', 'function openCheckInSheet')

    assert '...captureCourtDetailContext(modal)' in refresh
    assert 'reuseModal: modal' in refresh
    assert 'restoreContext: context' in refresh
    assert 'focusFallbackSelector' in refresh
    assert 'const modal = reuseModal || openModal(`' in detail
    assert 'modalBox.classList.toggle(\'court-detail-refreshing\', !!reuseModal)' in detail
    assert 'The previous details are still shown.' in detail
    assert 'restoreCourtDetailContext(modal, restoreContext)' in detail
    assert 'transitionModal(modal, () => refreshCourtDetailPreservingContext' not in detail
    assert 'focusDataAttribute' in APP
    assert 'focusOrdinal' in APP
    assert 'focusRegion' in APP
    assert 'id="cd-home-status" tabindex="-1"' in detail
    assert "focusFallbackSelector: '#cd-home-status'" in detail

    actions = section(APP, 'function openCourtPlayerActions', 'function openChallengeSheet')
    assert 'transitionModal(courtModal, () => openCourtDetail(court.id))' not in actions
    assert 'refreshCourtDetailPreservingContext(courtModal, court.id' in actions
    assert 'focusFallbackSelector: `[data-court-player-actions="${player.id}"]`' in actions


def test_court_photo_failures_have_specific_recovery_copy():
    errors = section(APP, 'const ERROR_TEXT = {', 'function humanError')

    assert "gallery_full: 'This court already has 12 photos." in errors
    assert "photo_too_large: 'That photo is still too large." in errors
    assert "invalid_photo: 'Choose a valid JPEG, PNG, or WebP image" in errors


def test_core_court_planner_and_play_components_do_not_reintroduce_tiny_type():
    critical_sections = [
        section(CSS, '.planner-step {', '.postgame-next {'),
        section(CSS, '.court-view-switch {', '/* ---------- Cards & rows ---------- */'),
        section(CSS, '.cd-hero {', '/* Court reviews / star ratings */'),
        section(CSS, '/* Product-strategy home:', '.game-completion-choices {'),
    ]
    tiny_literal = re.compile(r'font-size:\s*(?:[0-9](?:\.\d+)?|1[01](?:\.\d+)?)px')
    for css in critical_sections:
        assert not tiny_literal.search(css), tiny_literal.search(css).group(0) if tiny_literal.search(css) else ''
