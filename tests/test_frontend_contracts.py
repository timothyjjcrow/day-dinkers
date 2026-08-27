"""Small regression contracts for the mobile-first frontend shell."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend" / "index.html").read_text()
APP = (ROOT / "frontend" / "app.js").read_text()
SERVICE_WORKER = (ROOT / "frontend" / "sw.js").read_text()
STYLES = (ROOT / "frontend" / "styles.css").read_text()


def test_map_assets_are_lazy_loaded():
    """The default Play route should not pay the Leaflet startup cost."""
    assert "leaflet@" not in INDEX
    assert "markercluster" not in INDEX.lower()
    assert "function ensureMapAssets()" in APP
    assert "function ensureMapReady()" in APP


def test_primary_mobile_views_keep_accessible_tab_contracts():
    assert 'id="play-segments" role="tablist"' in INDEX
    assert 'id="chat-segments" role="tablist"' in INDEX
    assert 'id="play-content" class="tab-scroll" role="tabpanel"' in INDEX
    assert 'id="chat-content" class="tab-scroll" role="tabpanel"' in INDEX
    assert "setupTablistKeyboard($('#play-segments'))" in APP
    assert "setupTablistKeyboard($('#chat-segments'))" in APP


def test_profile_uses_one_guarded_dashboard_round_trip():
    assert "let profileRenderGeneration = 0;" in APP
    assert "api('/me/dashboard')" in APP
    assert "function profileDashboardRequest(userId, { reuse = false } = {})" in APP
    assert "renderProfile({ reuseDashboard: true })" in APP
    assert "profileDashboardCache.promise" in APP
    assert "el.dataset.profileRender" in APP
    assert 'id="pf-play-stats" aria-busy="true"' in APP


def test_long_mobile_forms_have_recoverable_non_sensitive_drafts():
    assert "pp_form_draft_v1:" in APP
    assert "['password', 'file', 'hidden', 'submit', 'button']" in APP
    for key in (
        "add-court",
        "create-league",
        "create-tournament",
        "create-club",
        "edit-profile",
    ):
        assert f"draftKey: '{key}'" in APP
    assert "formUX.clearDraft({ disable: true });" in APP


def test_location_and_invite_controls_preserve_explicit_user_choice():
    assert "`pp_auto_checkin:${userId}`" in APP
    assert "localStorage.removeItem('pp_auto_checkin')" in APP
    assert "setAutoCheckInEnabled(true);" in APP
    assert "setAutoCheckInEnabled(false);" in APP
    assert 'id="use-map-area"' in INDEX
    assert 'id="active-game-banner" class="active-game-banner hidden"></div>' in INDEX
    assert 'class="agb-dismiss"' in APP
    assert 'aria-label="Decline game invite"' in APP


def test_offline_shell_and_signed_in_snapshot_contracts():
    assert "const CORE_SHELL = ['/', '/styles-v13.css', '/app-v13.js'];" in SERVICE_WORKER
    assert 'href="/styles-v13.css"' in INDEX
    assert 'src="/app-v13.js"' in INDEX
    assert "const NAVIGATION_TIMEOUT_MS = 1200;" in SERVICE_WORKER
    assert "url.pathname.startsWith('/api')" in SERVICE_WORKER
    assert "caches.match('/')" in SERVICE_WORKER
    assert "function saveMeSnapshot(data)" in APP
    assert "function readMeSnapshot()" in APP
    assert 'id="connection-retry"' in INDEX
    assert ".is-offline .tab-panel" in STYLES
    assert "data.url || '/'" in SERVICE_WORKER
    assert "requested.origin === self.location.origin" in SERVICE_WORKER
    assert "win.postMessage({ type: 'open-overlay-route', url: destination })" in SERVICE_WORKER
    assert "function setupServiceWorkerRouteMessages()" in APP
    assert "clients.openWindow(destination)" in SERVICE_WORKER


def test_returning_players_get_snapshot_first_launch_instead_of_a_blank_screen():
    assert 'id="boot-screen" class="screen boot-screen"' in INDEX
    assert '.boot-screen {' in STYLES
    assert "const observer = new MutationObserver(syncBoot);" in INDEX
    assert "!screen.classList.contains('hidden')" in INDEX
    boot = APP[APP.index('async function boot()'):]
    assert "const snapshot = readMeSnapshot();" in boot
    assert boot.index("applyMe(snapshot.data, { persist: false, provisional: true });") < boot.index("api('/me', { timeoutMs:")
    assert "timeoutMs: snapshot ? 5000 : 8000" in boot
    assert "applyMe(freshMe, { reconcileSnapshot: !!snapshot });" in boot
    assert "snapshotAreaProvisional: false" in APP
    assert "reconcileSnapshot: state.snapshotAreaProvisional" in APP
    assert "if (snapshot && state.token)" in boot
    assert "401 already called logout()" in boot
    assert "state.tab = initialTabFromLocation();" in boot
    assert "function initialTabFromLocation()" in APP
    assert "if (/^#court\\//.test(location.hash)) return 'courts';" in APP


def test_court_results_render_progressively_without_collapsing_list_context():
    assert "const visibleLimit = state.courtSheetSnap === 'peek' ? 8 : state.courtListLimit;" in APP
    assert 'id="court-show-more"' in APP
    assert "state.courtListLimit += 20;" in APP
    assert "{ preserveList: state.courtSheetSnap !== 'peek' }" in APP
    assert "if (!preserveList || state.courtSheetSnap === 'peek') setCourtSheetSnap('half');" in APP
    assert 'aria-label="${n} courts in this area. Activate to zoom in"' in APP
    assert 'aria-label="${esc(markerLabel)}"' in APP
    assert "resultSignature !== state.courtListSignature" in APP
    assert "beginCourtContextRefresh('Finding courts near" in APP
    assert "setCourtMarkerSelected(previousCourtId, false);" in APP
    assert "firstNewIndex]?.focus({ preventScroll: true });" in APP


def test_rankings_and_fab_always_offer_the_contextual_next_action():
    assert APP.count('data-goto="new-ranked-game"') >= 2
    assert "openNewGameModal(null, 'ranked');" in APP
    assert "state.playSeg === 'brackets' ? 'Create a competition'" in APP
    assert "function openCompetitionCreateSheet()" in APP


def test_community_is_a_universal_attention_aware_inbox():
    assert 'id="chat-inbox-badge"' in INDEX
    assert 'id="chat-friends-badge"' in INDEX
    assert "api('/chat/competitions')" in APP
    for kind in ('game', 'tournament', 'league'):
        assert f"kind === '{kind}'" in APP
    assert "Recent conversations" in APP
    assert "Ready to coordinate" in APP
    assert "state.communityRoomUnread" in APP
    assert "data.community_room_unread != null" in APP
    assert "if (row.disabled) return;" in APP
    assert "The room GET is the authoritative read action" in APP
    assert "api('/chat/competitions')," in APP
    assert "avatarHtml(chat.user, '', 'span')" in APP
    assert "firstRevealed?.focus({ preventScroll: true });" in APP


def test_every_chat_channel_uses_shared_mobile_continuity():
    assert "function bindChatContinuity(" in APP
    assert "pp_chat_draft_v${CHAT_DRAFT_VERSION}:${accountId}:" in APP
    for channel in (
        "`dm:${userId}`",
        "`court:${court.id}`",
        "`club:${club.id}`",
        "`game:${game.id}`",
        "`tournament:${t.id}`",
        "`league:${lg.id}`",
    ):
        assert channel in APP
    assert "chat-new-messages hidden" in APP
    assert "inputEl.maxLength : 2000" in APP
    assert APP.count('autocomplete="off" maxlength="2000"') >= 6
    assert ".chat-new-messages" in STYLES
    assert "min-width: 132px; min-height: 44px" in STYLES


def test_competitions_share_one_versioned_result_sheet():
    assert "function normalizeCompetitionResult(match = {})" in APP
    assert "pending: 'awaiting_confirmation'" in APP
    assert "final: 'confirmed'" in APP
    assert "function openCompetitionResultSheet(" in APP
    assert 'id="competition-result-form"' in APP
    assert 'for="competition-score-1"' in APP
    assert "const formUX = primaryAction ? bindModalFormUX(modal, primaryAction) : null;" in APP
    assert "const actionUX = new Map()" not in APP
    assert "result_version: Number(match.result_version || 0)" in APP
    assert "if (!raw1)" in APP and "if (!raw2)" in APP
    assert "openLeagueScoreSheet" not in APP
    assert "openTournamentScoreModal" not in APP


def test_competition_result_actions_cover_review_resolution_and_audit():
    for action in ("score", "confirm", "dispute", "resolve", "void"):
        assert f'data-result-action="{action}"' in APP
    assert "competitionResultProvenanceHtml" in APP
    assert "competitionResultHistoryHtml" in APP
    assert "Standings and bracket progression wait until the score is confirmed or resolved." in APP
    assert "err.code === 'stale_result'" in APP
    assert "hooks.adoptFresh?.(fresh, { render: false })" in APP
    assert ".competition-result-status.is-pending" in STYLES
    assert ".competition-result-status.is-danger" in STYLES
    assert ".competition-result-status.is-success" in STYLES


def test_competition_live_refresh_preserves_mobile_context():
    assert "function captureCompetitionViewState(box)" in APP
    assert "bracketScrollLeft" in APP
    assert "function competitionOverlayCanRefresh(box)" in APP
    assert "currentOverlayEntry()?.el !== box" in APP
    assert "active.matches('input, textarea, select" in APP
    assert "setDialogLabel(content, 'League')" in APP
    assert "setDialogLabel(content, 'Tournament')" in APP
    assert "const currentHead = modalBox.querySelector(':scope > .modal-head')" in APP
    assert "makePressable(card" in APP
    assert "competitionActionNeededHtml('league'" in APP
    assert "competitionActionNeededHtml('tournament'" in APP
    assert "blocksProgression: ['awaiting_confirmation', 'disputed'].includes(stateName)" in APP
    assert 'aria-describedby="lg-unresolved-note"' in APP


def test_match_deep_links_and_notifications_open_the_exact_result():
    assert "(?:\\/match\\/(\\d+))?" in APP
    assert "function safeNotificationOverlayRoute(actionUrl)" in APP
    assert "url.origin !== location.origin" in APP
    assert "#tournament\\/(\\d+)\\/match\\/(\\d+)" in APP
    assert "#league\\/(\\d+)\\/match\\/(\\d+)" in APP
    assert "openTournamentScreen(Number(row.dataset.notifId), matchId)" in APP
    assert "openLeagueScreen(Number(row.dataset.notifId), matchId)" in APP
    assert "window.addEventListener('hashchange'" in APP
    assert "function navigateOverlayRoute(candidate)" in APP
    assert "rebuildReloadedMatchRouteIfNeeded" in APP
    assert "const route = { kind: 'league', id: leagueId };" in APP
    assert "const route = { kind: 'tournament', id: tournamentId };" in APP
    assert '${n.body ? `<div class="row-sub notif-body">${esc(n.body)}</div>` : \'\'}' in APP
    assert ".competition-match-highlight" in STYLES


def test_logout_is_a_hard_account_privacy_boundary():
    assert "function resetPrivateUiForLogout(accountId)" in APP
    assert "while (overlayStack.length)" in APP
    assert "purgeAccountChatDrafts(accountId);" in APP
    assert "profileRenderGeneration += 1;" in APP
    assert "panel.replaceChildren();" in APP
    assert "`${state.me?.id || 'signed-out'}:play:${seg}`" in APP
    assert "`${state.me?.id || 'signed-out'}:chat:${seg}`" in APP
    assert "const CHAT_DRAFT_TTL = 24 * 60 * 60 * 1000;" in APP
    assert "const requestToken = state.token;" in APP
    assert "state.token === requestToken" in APP
    assert "stale.isStaleSession = true;" in APP
    assert APP.count("assertCurrentSession();") >= 3
    assert "if (!err.isStaleSession) state.favIds = new Set();" in APP
    assert "`pp_mapview:${userId}`" in APP
    assert "localStorage.removeItem('pp_mapview')" in APP
    assert "if (state.map) restoreAccountMapView();" in APP
    assert "state.lastAutoCheckAt = 0;" in APP
    assert "revokePushSubscription(state.token);" in APP
    assert "sub.unsubscribe()" in APP
    assert "reg.getNotifications()" in APP
    assert "pageNotifications.forEach" in APP
    assert "notification.close()" in APP
    assert "'/api/push/unsubscribe'" in APP
    assert "keepalive: true" in APP
    assert "await pushResetPromise;" in APP
    assert "type: 'push-auth-state', enabled: false" in APP
    assert "if (!pushAuthorizedForCurrentSession) return;" in SERVICE_WORKER


def test_chat_rendering_deduplicates_poll_and_post_races():
    assert "function prepareChatRenderBatch(msgsEl, rawItems, append)" in APP
    assert APP.count('data-message-id="${m.id}"') == 6
    assert APP.count("const batch = prepareChatRenderBatch(msgsEl, items, append);") == 6
    assert "lastId = Math.max(lastId, batch.newestId)" in APP
