"""Small regression contracts for the mobile-first frontend shell."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "public" / "index.html").read_text()
APP = (ROOT / "public" / "app-v15.js").read_text()
SERVICE_WORKER = (ROOT / "public" / "sw.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()
MANIFEST = (ROOT / "public" / "manifest.webmanifest").read_text()


def test_map_assets_are_lazy_loaded():
    """The default Play route should not pay the Leaflet startup cost."""
    assert "leaflet@" not in INDEX
    assert "markercluster" not in INDEX.lower()
    assert "function ensureMapAssets()" in APP
    assert "function ensureMapReady()" in APP
    assert "existing?.remove();" in APP
    assert "link.dataset.loaded = '1'" in APP
    assert "link.remove();" in APP
    assert "script.remove();" in APP


def test_map_tiles_work_without_a_provider_api_key():
    assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in APP
    assert "basemaps.cartocdn.com" not in APP
    assert "OpenStreetMap</a> contributors" in APP


def test_primary_mobile_views_keep_accessible_navigation_contracts():
    assert 'id="play-segments" role="tablist"' in INDEX
    for segment, label in (
        ('games', 'Games'), ('scores', 'Rankings'), ('brackets', 'Events'),
    ):
        assert f'id="play-tab-{segment}"' in INDEX
        assert f'data-seg="{segment}"' in INDEX
        assert f'>{label}' in INDEX
    assert 'id="new-game-fab" class="fab"' in INDEX
    assert 'id="chat-segments" role="tablist"' in INDEX
    assert 'id="play-view-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"' in INDEX
    assert 'id="play-content" class="tab-scroll" role="tabpanel"' in INDEX
    assert 'id="play-content" class="tab-scroll" aria-live=' not in INDEX
    assert "#play-content { padding-bottom: 96px; }" in STYLES
    assert 'id="chat-content" class="tab-scroll" role="tabpanel"' in INDEX
    assert "setupTablistKeyboard($('#play-segments'))" in APP
    assert "setupTablistKeyboard($('#chat-segments'))" in APP
    assert "liveEl.setAttribute('aria-label', 'Play');" in APP
    assert "liveEl.setAttribute('aria-labelledby', `chat-tab-${seg}`);" in APP


def test_install_and_boot_copy_match_session_and_match_taxonomy():
    assert "Find courts, players & play" in INDEX
    assert "Getting your next play ready" in INDEX
    assert "Your play plan" in INDEX
    assert "Find courts, players & play" in MANIFEST
    assert "casual play sessions" in MANIFEST
    assert "ranked matches" in MANIFEST
    assert '"name": "Play"' in MANIFEST


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
    assert 'class="agb-join"' in APP
    assert 'aria-label="Decline game invite"' in APP

    auto = APP[APP.index("async function maybeAutoCheckIn()") : APP.index("function courtRowHtml")]
    assert "const callerSession = instantRallySession();" in auto
    assert "const callerLocation = [Number(state.userLoc[0]), Number(state.userLoc[1])];" in auto
    assert "const requestIsCurrent = () => instantRallySessionMatches(callerSession)" in auto
    assert "`/courts?lat=${callerLocation[0]}&lng=${callerLocation[1]}" in auto
    assert auto.index("if (!requestIsCurrent()) return;") < auto.index(
        "await api(`/courts/${nearest.id}/checkin`"
    )
    assert auto.count("if (!requestIsCurrent()) return;") >= 4


def test_checked_in_players_can_launch_and_fill_an_instant_rally():
    assert 'data-goto="instant-rally"' in APP
    assert 'data-goto="on-my-way"' in APP
    assert 'data-goto="play-pulse"' in APP
    assert 'data-goto="ranked-match"' in APP
    assert "function openGameFlow(options = {})" in APP
    assert "Start now" in APP
    assert "async function startInstantRally(button, options = {})" in APP
    assert "openGameFlow({" in APP
    assert "api('/games/rally'" in APP
    assert "id: `rally-${newGameAttemptId()}`" in APP
    assert "liveEl.innerHTML = rallyLauncherHtml() + skeletonHtml(4);" in APP
    assert "previousPresenceView !== nextPresenceView" in APP
    assert "renderPlay({ useCachedData: true });" in APP
    assert "result.outcome === 'joined'" in APP
    assert "openGameScreen(game.id);" in APP


def test_game_creation_flows_directly_into_multi_person_roster_fill():
    assert "const createdGame = await api('/games'" in APP
    assert "openGameScreen(createdGame.id, { replaceModal: modal });" in APP
    success = APP[APP.index("const createdGame = await api('/games'"):APP.index("} catch (err)", APP.index("const createdGame = await api('/games'"))]
    assert "closeModal(modal);" not in success
    assert "A live, underfilled rally still needs recruiting" in APP
    assert "JSON.stringify({ user_ids: requested })" in APP
    assert "Send ${selected.size} invite" in APP


def test_underfilled_games_use_one_live_roster_boost_sheet():
    assert 'id="gs-fill-roster"' in APP
    assert 'id="gs-invite"' not in APP
    assert 'id="gs-post-court"' not in APP
    for selector in (
        'id="rb-summary"', 'id="rb-friends"', 'data-rb-friend=',
        'id="rb-invite-send"', 'id="rb-post-court"',
        'id="rb-share"', 'id="rb-status"',
    ):
        assert selector in APP
    assert "1 spot left. The first person to join gets it." in APP
    assert "`${spotsLeft} spots left. The next ${spotsLeft} players to join get them.`" in APP
    assert "pendingGameOpenCallAttempt(accountId, game.id)" in APP
    assert "clearGameOpenCallAttempts(accountId);" in APP
    assert "body: JSON.stringify({ client_attempt_id: attempt.id })" in APP
    assert "return 'shared';" in APP
    assert "return 'copied';" in APP
    assert ".roster-boost-channel" in STYLES


def test_court_chat_renders_live_joinable_game_cards():
    assert "function courtOpenCallCardHtml(call)" in APP
    assert "function applyCourtOpenCallSnapshots(" in APP
    assert 'data-open-call-action="join"' in APP
    assert 'data-open-call-action="waitlist"' in APP
    assert 'data-open-call-action="withdraw"' in APP
    assert "applyCourtOpenCallSnapshots(msgsEl, fresh.open_calls)" in APP
    assert "message.open_call" in APP
    assert ".court-open-call.is-full" in STYLES
    assert ".court-open-call.is-closed" in STYLES


def test_offline_shell_and_signed_in_snapshot_contracts():
    assert "const CACHE = 'thirdshot-v15-r70';" in SERVICE_WORKER
    for asset in (
        "/release-assets/r68/styles-v15.min.css",
        "/release-assets/r68/crew-planner-v15.min.js",
        "/release-assets/r68/app-v15.min.js",
    ):
        assert asset in SERVICE_WORKER
    assert 'href="/release-assets/r68/styles-v15.min.css"' in INDEX
    assert 'src="/release-assets/r68/crew-planner-v15.min.js"' in INDEX
    assert 'src="/release-assets/r68/app-v15.min.js"' in INDEX
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
    assert "active_game: activeGameFromSnapshot(snapshot.data.active_game, snapshotSavedAt)" in boot
    assert boot.index("}, { persist: false, provisional: true });") < boot.index("api('/me', { timeoutMs:")
    assert boot.index("await showMain();") < boot.index("api('/me', { timeoutMs:")
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
    assert "const peekResultLimit = 3;" in APP
    assert "const visibleLimit = state.courtSheetSnap === 'peek' ? peekResultLimit : state.courtListLimit;" in APP
    assert "const firstNewIndex = 0;" in APP
    assert 'class="court-peek-strip"' in APP
    assert 'Browse all ${availableCourtCount} court' in APP
    assert "compactPortrait ? 'full' : 'half'" in APP
    assert 'id="court-show-more"' in APP
    assert "state.courtListLimit += 20;" in APP
    listing = APP[APP.index("function renderCourtList"):APP.index("function openSuggestEditSheet")]
    discovery = APP[APP.index("function courtDiscoveryReturnFocus"):APP.index("function selectCourtOnMap")]
    markers = APP[APP.index("function drawMarkers"):APP.index("function setCourtMarkerSelected")]
    assert "activateCourtFromDiscovery(byId.get(Number(row.dataset.court)), { preserveList: true })" in listing
    assert "selectCourtOnMap(court, { preserveList })" in discovery
    assert "return openCourtDetail(court.id" in discovery
    assert "returnFocusFallback: () => courtDiscoveryReturnFocus(court.id)" in discovery
    assert "on('click', () => activateCourtFromDiscovery(court))" in markers
    assert 'data-marker-label="${n} courts in this area. Activate to zoom in"' in APP
    assert "target.setAttribute('aria-label', visual?.dataset.markerLabel" in APP
    assert 'data-marker-label="${esc(markerLabel)}"' in APP
    assert "target.setAttribute('aria-label', label)" in APP
    assert "resultSignature !== state.courtListSignature" in APP
    assert "beginCourtContextRefresh('Finding courts near" in APP
    assert "setCourtMarkerSelected(previousCourtId, false);" in APP
    assert "firstNewIndex]?.focus({ preventScroll: true });" in APP


def test_rankings_and_fab_always_offer_the_contextual_next_action():
    assert APP.count('data-goto="ranked-match"') >= 2
    assert "function openRankedMatchFlow()" in APP
    assert "gameType: 'ranked', maxPlayers: 2, lockGameType: true" in APP
    assert "state.playSeg === 'brackets' ? 'Create a competition'" in APP
    assert "function openCompetitionCreateSheet()" in APP


def test_community_is_a_universal_attention_aware_inbox():
    assert 'id="chat-inbox-badge"' in INDEX
    assert 'id="chat-friends-badge"' in INDEX
    assert 'id="chat-groups-badge"' in INDEX
    assert "const inbox = await api('/inbox');" in APP
    for kind in ('game', 'tournament', 'league'):
        assert f"kind === '{kind}'" in APP
    assert "function universalInboxHtml(" in APP
    assert "['all', 'All'], ['direct', 'Direct'], ['games', 'Games']" in APP
    assert "['courts', 'Courts']" in APP
    assert 'id="chat-tab-groups"' in INDEX
    assert "Private groups" in APP
    assert "Your community groups" in APP
    assert "function renderPeopleLane" in APP
    assert "Everything else" in APP
    assert "state.communityRoomUnread" in APP
    assert "data.community_room_unread != null" in APP
    assert "syncCommunityUnreadLanes(rooms, clubs, competitions, crews);" in APP
    assert "if (row.disabled) return;" in APP
    rows = APP[APP.index("function bindCommunityConversationRows"):APP.index("function openCreateGroupChoiceSheet")]
    assert "roomModal._cleanupFns.push" in rows
    assert "opening group info" in rows
    assert "leaves unread messages alone" in rows
    assert "state.tab === 'chat' && ['chats', 'groups'].includes(state.chatSeg)" in rows
    assert "if (state.tab === 'chat') renderChat();" not in rows
    assert "avatarHtml(chat.user, '', 'span')" in APP
    assert "firstRevealed?.focus({ preventScroll: true });" in APP


def test_people_search_ignores_stale_community_renders():
    friends = APP[APP.index("async function renderFriends"):APP.index("async function openThread")]
    assert "if (!search) return;" in friends
    assert "let playerSearchSeq = 0;" in friends
    assert "const seq = ++playerSearchSeq;" in friends
    assert "if (!resultsEl) return;" in friends
    assert "if (seq !== playerSearchSeq || !search.isConnected || search.value.trim() !== q) return;" in friends
    assert "playerSearchSeq += 1;" in friends


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
    assert "const syncVisibleResult = () =>" in APP
    assert "score1.value = match.score1 ?? '';" in APP
    assert "score2.value = match.score2 ?? '';" in APP
    assert "score1.readOnly = !freshCanEditScores;" in APP
    assert "history.innerHTML = competitionResultHistoryHtml(match);" in APP
    assert "syncVisibleResult();" in APP
    assert "visible score, permissions, and history are now refreshed" in APP
    assert ".competition-result-status.is-pending" in STYLES
    assert ".competition-result-status.is-danger" in STYLES
    assert ".competition-result-status.is-success" in STYLES


def test_competition_live_refresh_preserves_mobile_context():
    assert "function captureCompetitionViewState(box)" in APP
    assert "bracketScrollLeft" in APP
    assert "function competitionOverlayCanRefresh(box)" in APP
    assert "currentOverlayEntry()?.el !== box" in APP
    assert "active.matches('input, textarea, select, .app-select-trigger" in APP
    assert "setDialogLabel(content, 'League')" in APP
    assert "setDialogLabel(content, 'Tournament')" in APP
    assert "const currentHead = modalBox.querySelector(':scope > .modal-head')" in APP
    assert "makePressable(card" in APP
    assert "competitionActionNeededHtml('league'" in APP
    assert "competitionActionNeededHtml('tournament'" in APP
    assert "blocksProgression: ['awaiting_confirmation', 'disputed'].includes(stateName)" in APP
    assert 'aria-describedby="lg-unresolved-note"' in APP


def test_log_game_court_search_keeps_the_branded_picker_in_sync():
    search_pick = APP[APP.index("chosenCourtId = Number(row.dataset.lgPick)"):]
    search_pick = search_pick[:search_pick.index("}, 300);")]
    assert "sel.value = String(chosenCourtId);" in search_pick
    assert "sel.dispatchEvent(new Event('change', { bubbles: true }));" in search_pick

    log_game = APP[APP.index("async function openLogGameSheet"):APP.index("async function openNewGameModal")]
    assert "const doublesAvailable = friends.length >= 3;" in log_game
    assert "Getting your friends and nearby courts ready…" in log_game
    assert "renderError(loadBox, `Couldn’t load your friends:" in log_game
    assert "transitionModal(modal, openLogGameSheet);" in log_game
    assert 'data-goto="chat-friends"' in log_game
    assert "modalBox.innerHTML = `" in log_game
    assert "enhanceAppSelects(modal);" in log_game
    assert 'aria-pressed="true">Singles' in log_game
    assert "partnerSel.value = String(friends[0].id);" in log_game
    assert "oppSel.value = String(friends[1].id);" in log_game
    assert "opp2Sel.value = String(friends[2].id);" in log_game
    assert "syncParticipantOptions" in log_game
    assert 'class="card row nav-row-button log-game-court-result"' in log_game
    assert 'role="option" aria-selected="false" tabindex="-1"' in log_game
    assert "const seq = ++courtSearchSeq;" in log_game
    assert "value.trim() !== q" in log_game
    assert "aria-live=\"polite\"" in log_game
    assert "courtSearchSeq += 1;" in log_game
    assert "let logAttemptId = `log-${newGameAttemptId()}`;" in log_game
    assert "logAttemptFingerprint = nextFingerprint;" in log_game
    assert "const sendLogRequest = (acceptNonstandard = false) => api('/games/log'" in log_game
    assert "...logAttemptPayload," in log_game
    assert "client_attempt_id: logAttemptId," in log_game
    assert "accept_nonstandard_score: true" in log_game
    assert "[408, 425, 429].includes(Number(failure.status))" in log_game
    assert "setLogInputsLocked(true);" in log_game
    assert "btn.textContent = 'Try same result again';" in log_game
    assert "if (failure.code === 'client_attempt_id_conflict')" in log_game
    assert "resetLogAttempt();" in log_game


def test_match_deep_links_and_notifications_open_the_exact_result():
    assert "(?:\\/match\\/(\\d+))?" in APP
    assert "function safeNotificationOverlayRoute(actionUrl)" in APP
    assert "url.origin !== location.origin" in APP
    assert "#tournament\\/(\\d+)\\/match\\/(\\d+)" in APP
    assert "#league\\/(\\d+)\\/match\\/(\\d+)" in APP
    assert "function notificationTarget(notification)" in APP
    assert "function openNotificationTarget(notification)" in APP
    assert "openTournamentScreen(Number(target.id), target.matchId)" in APP
    assert "openLeagueScreen(Number(target.id), target.matchId)" in APP
    assert "window.addEventListener('hashchange'" in APP
    assert "function navigateOverlayRoute(candidate)" in APP
    assert "rebuildReloadedMatchRouteIfNeeded" in APP
    assert "const route = { kind: 'league', id: leagueId };" in APP
    assert "const route = { kind: 'tournament', id: tournamentId };" in APP
    assert '${notification.body ? `<span class="row-sub notif-body">${esc(notification.body)}</span>` : \'\'}' in APP
    assert ".competition-match-highlight" in STYLES


def test_logout_is_a_hard_account_privacy_boundary():
    assert "function resetPrivateUiForLogout(accountId)" in APP
    assert "while (overlayStack.length)" in APP
    assert "purgeAccountChatDrafts(accountId);" in APP
    assert "profileRenderGeneration += 1;" in APP
    assert "panel.replaceChildren();" in APP
    assert "function areaViewKey()" in APP
    assert "`${state.me?.id || 'signed-out'}:play:${seg}:${areaViewKey()}:${discoveryKey}`" in APP
    assert "const peopleKey = seg === 'friends' ? `:${state.peopleMode}` : '';" in APP
    assert "`${state.me?.id || 'signed-out'}:chat:${seg}${peopleKey}:${areaViewKey()}`" in APP
    assert "const CHAT_DRAFT_TTL = 24 * 60 * 60 * 1000;" in APP
    assert "let requestToken = state.token;" in APP
    assert "const requestSessionEpoch = authSessionEpoch;" in APP
    assert "const refreshedToken = String(res.headers.get('X-Session-Token')" in APP
    assert "authSessionEpoch === requestSessionEpoch" in APP
    assert "function authenticatedTokenAccountId(token)" in APP
    assert "const requestTokenRevision = authTokenRevision;" in APP
    assert "authTokenRevision === requestTokenRevision" in APP
    assert "function requireSessionReauthentication(" in APP
    assert "_reauthAttempted: true" in APP
    assert "stale.isStaleSession = true;" in APP
    assert APP.count("assertCurrentSession();") >= 3
    load_favorites = APP[APP.index("async function loadFavIds()"):APP.index("const COURT_AMENITY_FILTERS")]
    assert "if (!err.isStaleSession)" in load_favorites
    assert "state.favIds = new Set();" in load_favorites
    assert "state.favoriteCourts = [];" in load_favorites
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


def test_rotated_credentials_are_a_hard_inflight_response_boundary():
    helper_start = APP.index("function persistReplacementToken")
    helper = APP[helper_start:APP.index("const ERROR_TEXT", helper_start)]
    assert "authSessionEpoch += 1;" in helper
    assert helper.index("authSessionEpoch += 1;") < helper.index("state.token = replacement;")
    assert "function instantRallySession()" in APP
    assert "{ token: authSessionEpoch, userId }" in APP


def test_online_event_verifies_the_api_before_announcing_recovery():
    start = APP.index("function setupConnectivity")
    connectivity = APP[start:APP.index("function setupServiceWorkerRouteMessages", start)]
    assert "setConnectionState('degraded');" in connectivity
    assert "const refreshed = await refreshMe();" in connectivity
    assert "if (!refreshed) return;" in connectivity
    assert connectivity.index("const refreshed = await refreshMe();") < connectivity.index("toast('Back online")


def test_chat_rendering_deduplicates_poll_and_post_races():
    assert "function prepareChatRenderBatch(msgsEl, rawItems, append)" in APP
    assert APP.count("const batch = prepareChatRenderBatch(msgsEl, items, append || prepend);") == 6
    assert "const batch = prepareChatRenderBatch(msgsEl, rawItems || [], append || prepend);" in APP
    assert APP.count("chatMessageActionHtml(m, mine)") == 6
    assert "chatMessageActionHtml(message, mine)" in APP
    assert "lastId = Math.max(lastId, batch.newestId)" in APP
