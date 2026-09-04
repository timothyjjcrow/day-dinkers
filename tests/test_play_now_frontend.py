"""Frontend-only contracts for the one-flow Play Now rally experience."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_global_play_now_enters_casual_community_launcher_not_mixed_planner():
    ctas = section("function setupEmptyStateCtas()", "// ---------- Map / Courts ----------")
    play_now_branch = ctas[ctas.index("target === 'play-now'"):ctas.index("target === 'new-ranked-game'")]
    assert "openPlaySoonFlow();" in play_now_branch
    assert "openGameFlow({ mode: 'find' });" not in play_now_branch
    assert "openNewGameModal" not in play_now_branch

    flow = section("function openGameFlow", "async function checkInAndStartRally")
    assert "uiIcon('map-pin')" in flow
    assert "'Current check-in'" in flow
    assert "'Saved'" in flow
    assert "'Home'" in flow
    for generic_location_glyph in ("📍", "⭐", "🏠"):
        assert generic_location_glyph not in flow
    assert "distance_miles" in flow
    assert 'id="game-flow-court-search"' in flow
    assert "Find or start a game" not in flow


def test_play_location_surfaces_share_product_icons_instead_of_platform_emoji():
    pulse = section("function openPlayPulseDetails", "function gameTypeLabel")
    picker = section("async function openPlayNowCourtPicker", "function openPlayPulseCourtPicker")
    assert pulse.count("uiIcon('map-pin')") >= 2
    assert "play-pulse-detail-card > span:first-child .ui-icon" in STYLES
    assert "play-pulse-active-icon .ui-icon" in STYLES
    assert "uiIcon('map-pin')" in picker
    assert "uiIcon(active ? 'check-circle' : 'map-pin')" in picker
    assert "play-now-selection > span:first-child .ui-icon" in STYLES
    assert ".play-now-court-pin .ui-icon" in STYLES
    for generic_location_glyph in ("📍", "⭐", "🏠"):
        assert generic_location_glyph not in picker


def test_ready_confirmation_checks_in_before_durable_rally_resolution():
    flow = section("async function checkInAndStartRally", "async function openPlayNowCourtPicker")
    checkin = "api(`/courts/${selected.id}/checkin`"
    rally = "await startInstantRally(null"
    assert flow.index(checkin) < flow.index(rally)
    assert "const presenceLocation = await freshCourtPresenceLocation(selected);" in flow
    assert "presence_intent: 'instant_rally'" in flow
    assert "presence_location: presenceLocation" in flow
    assert "applyAuthoritativeCheckIn(selected, checkedIn, true);" in flow
    assert "presenceConfirmed" not in flow
    assert "confirmCourtPresence" not in flow
    assert "expectedCourtId: selected.id" in flow
    assert "Your check-in is saved; try again." in flow
    assert "Retry safely" not in flow
    assert "const callerSession = instantRallySession();" in flow
    assert flow.index("if (!instantRallySessionMatches(callerSession)) return null;") < flow.index(
        "applyAuthoritativeCheckIn(selected, checkedIn, true);"
    )
    assert flow.count("if (!instantRallySessionMatches(callerSession)) return null;") >= 3


def test_checkin_sheet_commits_one_authoritative_visibility_choice():
    sheet = section("function openCheckInSheet", "// ---------- Games ----------")
    assert "I’m at ${esc(court.name)}" in sheet
    assert "Check in &amp; look for a game" in sheet
    assert "Check in quietly" in sheet
    assert 'type="radio" name="checkin-visibility"' in sheet
    assert 'id="ci-submit"' in sheet
    assert "form.addEventListener('submit'" in sheet
    assert "const lookingForGame = form.elements['checkin-visibility'].value === 'looking';" in sheet
    assert "const presenceLocation = await freshCourtPresenceLocation(court);" in sheet
    assert "presence_intent: presenceIntent" in sheet
    assert "presence_location: presenceLocation" in sheet
    assert sheet.count("api(`/courts/${court.id}/checkin`") == 1
    assert "applyAuthoritativeCheckIn(court, response, lookingForGame);" in sheet
    assert "openGameFlow" not in sheet
    assert "signed-in players nearby" in sheet
    assert "expires automatically" in sheet

    authoritative = section("function applyAuthoritativeCheckIn", "function openCheckInSheet")
    assert "invalidateMeRequests();" in authoritative
    assert "state.presence = response && response.presence" in authoritative
    assert "rememberInstantRallyPresenceProof(selected.id, response);" in authoritative
    assert "state.activePlayPulse = null;" in authoritative
    assert "state.playGamesCache = null;" in authoritative
    for refresh in (
        "renderPresenceBanner();", "renderActiveGameBanner();",
        "updatePlayHeader();", "refreshLookingBanner();", "fetchCourtsInView();",
    ):
        assert refresh in authoritative


def test_rally_attempt_is_shared_recoverable_and_history_safe():
    rally = section("let instantRallyInFlight = null", "function rallyLauncherHtml")
    assert "function instantRallySession()" in rally
    assert "function instantRallySessionMatches(session)" in rally
    assert "sharedRecord.token === callerSession.token" in rally
    assert "sharedRecord.userId === callerSession.userId" in rally
    assert "sharedRecord.courtId === expectedCourtId" in rally
    assert "resolution = await sharedRecord.promise;" in rally
    assert "const attempt = pendingInstantRallyAttempt(" in rally
    assert "requestedGameType, requestedMaxPlayers" in rally
    assert "Number(saved.courtId) === expectedCourtId" in APP
    assert "pp_instant_rally_v3:" in APP
    assert "pp_instant_rally_v2:" in APP
    assert "`${INSTANT_RALLY_ATTEMPT_PREFIX}${accountId}:${expectedCourtId}:${normalizedType}:${normalizedMax}`" in APP
    assert "sessionStorage.getItem(legacyKey)" in APP
    assert "court_id: attempt.courtId" in rally
    assert "attempt.gameType, attempt.maxPlayers" in rally
    assert "game_type: attempt.gameType" in rally
    assert "max_players: attempt.maxPlayers" in rally
    assert "confirm_court_presence" not in rally
    assert "courtId: expectedCourtId" in rally
    assert "gameType: requestedGameType" in rally
    assert "maxPlayers: requestedMaxPlayers" in rally
    assert "if (instantRallyInFlight === record) instantRallyInFlight = null;" in rally
    assert "resolution?.abandoned" in rally
    assert "function finishInstantRallyCall(resolution, options = {}, callerSession = null)" in rally
    assert "if (!instantRallySessionMatches(callerSession)) return;" in rally
    assert "openResolvedRallyGame(gameId, options.fromModal || null)" in rally
    assert "client_attempt_id: attempt.id" in rally
    assert "Number(error.status) === 429" in rally
    assert "if (!retrySafely) {" in rally
    assert "authoritativeRallyGame(error)" in rally
    assert "data.game_id" in rally and "data.existing_game_id" in rally
    assert "currentOverlayEntry()?.el !== fromModal" in rally
    assert "openGameScreen(gameId, { replaceModal: fromModal })" in rally
    assert "return { staleRally: true, error };" in rally
    assert "game.status !== 'upcoming' || game.assembly_active === false" in rally
    assert "return { staleRally: true, error: stale };" in rally
    assert "staleRallyRestarted: true" in rally
    assert "['rally_no_longer_active', 'rally_time_out_of_range'].includes(error.code)" in rally
    assert rally.index("return { staleRally: true, error: stale };") < rally.index(
        "const recoveredGame = authoritativeRallyGame(error)"
    )


def test_logout_detaches_inflight_rally_from_the_next_account():
    logout = section("function logout({", "function tokenHint()")
    assert logout.index("instantRallyInFlight = null;") < logout.index("state.token = null;")
    rally = section("let instantRallyInFlight = null", "function rallyLauncherHtml")
    assert "if (!instantRallySessionMatches(callerSession)) return { abandoned: true };" in rally
    assert "token: callerSession.token" in rally
    assert "userId: callerSession.userId" in rally
    assert "`${INSTANT_RALLY_ATTEMPT_PREFIX}${accountId}:`," in APP
    assert "`${LEGACY_INSTANT_RALLY_ATTEMPT_PREFIX}${accountId}:`," in APP
    assert "keys.forEach((key) => sessionStorage.removeItem(key));" in APP


def test_every_instant_join_uses_arrival_hold_until_at_court_confirmation():
    gate = section("async function openReadyRally", "function openCheckInSheet")
    assert "if (!isCheckedInAtCourt(courtId))" in gate
    assert "openRallyArrivalSheet(summary)" in gate
    assert "openArrivalDetails(ownArrival)" in gate
    assert "api(`/games/${gameId}/join`" in gate
    assert "['active_checkin_required', 'active_checkin_court_mismatch'].includes(error.code)" in gate
    assert "const staleCodes = ['game_full', 'game_not_open', 'game_not_found', 'rally_no_longer_active'];" in gate
    assert gate.index("if (staleCodes.includes(error.code))") < gate.index(
        "const recoveredGame = error.code === 'active_rally_elsewhere'"
    )
    assert "? authoritativeRallyGame(error) : null" in gate
    assert "const callerSession = instantRallySession();" in gate
    assert gate.count("if (!instantRallySessionMatches(callerSession)) return null;") >= 2
    assert "openResolvedRallyGame(gameId, sourceModal || null);" in gate
    assert "openResolvedRallyGame(recoveredGame.id, sourceModal || null);" in gate
    assert "refreshMe().finally(openConfirmation);" in gate
    assert "if (!document.body.contains(sourceModal)" in gate

    finish = section("function finishInstantRallyCall", "function rallyLauncherHtml")
    assert "'active_checkin_required', 'active_checkin_court_mismatch'," in finish
    assert "'presence_proof_required', 'presence_proof_expired', 'invalid_presence_proof'," in finish
    assert "refreshMe().finally(reopenConfirmation);" in finish
    assert "transitionModal(sourceModal, () => openGameFlow({" in finish
    assert "mode: 'start', gameType: options.gameType, maxPlayers: options.maxPlayers" in finish

    cards = section("function bindGameButtons", "// Share text")
    assert "b.dataset.instantRally === 'true'" in cards
    assert "await openReadyRally" in cards
    assert "data-rally-court" in APP
    assert "if (game.is_instant)" in section("const joinBtn = el.querySelector('#agb-join')", "if (dismissBtn)")


def test_instant_games_stay_assembly_first_until_explicit_finish():
    assembly = section("function instantRallyAssembly", "function renderActiveGameBanner")
    assert "!['finding', 'ready', 'full'].includes(serverAssemblyState)" in assembly
    assert "function instantRallyScorePending" in assembly
    assert "Number(game.ready_count)" in assembly
    assert "Math.max(" in assembly
    assert "Finding players" in assembly
    assert "Ready to play" in assembly
    assert "Game full — ready to play" in assembly

    active_banner = section("function renderActiveGameBanner", "function renderTournamentBanner")
    assert "openButton.onclick = () => openGameScreen(game.id)" in active_banner
    assert "openScoreModal(fresh" not in active_banner

    card = section("function gameCardHtml", "function bindGameButtons")
    assert "game.status === 'upcoming' && assembly" in card
    assert "status-banner rally-banner" in card
    instant_card = card[card.index("game.status === 'upcoming' && assembly"):card.index("} else if (game.status === 'upcoming')")]
    assert "data-game-waitlist" not in instant_card
    assert "rallyActionState(rally)" in instant_card
    assert "Someone’s on the way" in APP
    assert "instantRallyScorePending(game)" in card
    assert "Played? Finish with no score or add one." in card

    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "game.spots_left > 0 && (!game.is_instant || assembly)" in detail
    assert "game.is_instant && game.players.length >= 2" in detail
    assert "We finished — enter score" in detail


def test_visible_play_feed_revalidates_live_cards_and_joins_from_fresh_state():
    refresh = section("function startPlayLiveRefresh", "async function showMain")
    assert "state.tab !== 'play' || state.playSeg !== 'games'" in refresh
    assert "state.playGamesCache = null" in refresh
    assert "LIVE_DETAIL_POLL_INTERVAL_MS" in refresh

    cards = section("function bindGameButtons", "// Share text")
    assert "const fresh = await api(`/games/${gameId}`)" in cards
    assert "fresh.status !== 'upcoming' || Number(fresh.spots_left) <= 0" in cards
    assert "showInlineActionError(card, err.message)" in cards
    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "!game.is_instant && !startsAhead" in detail
    assert "Game full." in detail
    assert "Join at the court to see who’s playing." in detail
    assert "At the court (${readyCount}/${game.max_players})" in detail
    assert "Players (${readyCount}/${game.max_players})" in detail

    play = section("async function renderPlay", "function updatePlayHeader")
    assert "g.status === 'upcoming' && (g.can_enter_score || g.can_complete_session)" in play
    assert "? (instantRallyScorePending(g) || instantSessionWrapPending(g))" in play
    assert "!toScore.includes(g) && !toConfirm.includes(g) && !toReconfirm.includes(g) && !waiting.includes(g)" in play
    profile = section("// My upcoming games", "// Personal play stats")
    assert "const scorePending = (mine.items || []).filter((game) =>" in profile
    assert "? instantRallyScorePending(game)" in profile
    assert "const wrapPending = (mine.items || []).filter((game) =>" in profile
    assert "? instantSessionWrapPending(game)" in profile
    assert "Played — enter the score" in profile
    assert "!instantRallyClosed(game)" in profile

    closed = section("function instantRallyClosed", "function renderActiveGameBanner")
    assert "game.assembly_active === false" in closed
    assert "!['finding', 'ready', 'full'].includes(assemblyState)" in closed
    card = section("function gameCardHtml", "function bindGameButtons")
    assert "This pickup group ended without enough players." in card
    assert "instantRallyClosed(g)" in play
    assert "instantRallyClosed(game)" in detail
    assert "This pickup game didn’t fill up." in detail


def test_looking_summary_drives_exact_rally_and_nearby_actions_with_fallback():
    looking = section("function normalizeLookingRallies", "// \"N players near you want to play\"")
    assert "data.rallies" in looking
    assert "data.open_rallies" in looking
    banner = section("async function refreshLookingBanner", "// ---------- Search suggestions")
    assert "rallyGameId" in banner and "rallyCourtId" in banner
    assert "View nearby players" in banner
    assert "const generation = ++lookingBannerGeneration" in banner
    assert "const requestOwner = captureAuthenticatedSessionOwner()" in banner
    assert "authenticatedSessionOwnerIsCurrent(requestOwner)" in banner
    assert "lookingBannerContext(" in banner
    assert "captureAuthenticatedSessionOwner(), committedAreaLatLng()" in banner
    assert banner.count("if (!isCurrent()) return;") == 2
    assert "function clearLookingBanner({ invalidate = true } = {})" in APP
    assert "el.replaceChildren();" in APP
    logout = section("function resetPrivateUiForLogout", "function logout({")
    assert "clearLookingBanner();" in logout
    map_area = section("useMapAreaButton?.addEventListener", "// NB: don't pass the click event")
    assert "clearLookingBanner();" in map_area
    assert "refreshLookingBanner();" in map_area
    assert "await refreshCourtResults({ showLoading: false });" in map_area

    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert "Promise.all" in nearby
    assert "players/looking" in nearby
    assert "rank(b) - rank(a)" in nearby
    assert "data-rally-action" in nearby
    assert "openReadyRally" in nearby


def test_nearby_player_location_copy_labels_live_last_seen_and_profile_provenance():
    nearby = section("function nearbyPlayerLocationHtml", "async function renderFriends")
    assert "if (player.checked_in_court)" in nearby
    assert "wants to play!" in nearby
    assert "Last seen ${when}" in nearby
    assert "distanceText.replace('your area', 'this area then')" in nearby
    assert "Plays at ${esc(player.home_court_name)}" in nearby
    assert "Profile area" in nearby
    assert "Nearby from their profile" in nearby
    assert "let sub = nearbyPlayerLocationHtml(p);" in nearby
    assert "${p.distance_miles} mi away" not in nearby


def test_play_now_controls_have_mobile_and_accessibility_contracts():
    picker = section("async function openPlayNowCourtPicker", "async function openReadyRally")
    assert 'role="group" aria-label="Court choices" aria-busy="true"' in picker
    assert 'role="listbox"' not in picker
    assert 'role="option"' not in picker
    assert 'aria-pressed="${!!selected && selected.id === item.id}"' in picker
    assert "row.setAttribute('aria-pressed', String(active));" in picker
    assert 'role="alert" tabindex="-1"' in section(
        "async function openPlayNowCourtPicker", "async function openReadyRally"
    )
    assert ".play-now-court {" in STYLES
    assert "min-height: 58px" in STYLES
    assert ".play-now-court .row-title, .play-now-court .row-sub" in STYLES
    assert "text-overflow: ellipsis" in STYLES
    assert ".play-now-court .tag" in STYLES
    assert ".nearby-rally-card [data-rally-action]" in STYLES
    assert "min-height: 44px" in STYLES
    assert ".active-game-banner.state-rally" in STYLES
    assert 'class="nearby-level-filters" role="group"' in APP
    assert 'data-nearby-level="${value}"' in APP
    assert "How location sharing works" in APP
    assert 'data-msg="${p.id}"' in APP


def test_court_detail_uses_private_safe_presence_aggregate():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert "const visiblePlayersHere = Array.isArray(court.players_here)" in detail
    assert "Math.max(visiblePlayersHere.length, Number(court.players_here_count) || 0)" in detail
    assert "privatePlayersHere" in detail
    assert "Playing and forming now" in detail
    assert "People at this court (${nHere})" in detail
    assert "<b>${nHere}</b><span>at the court</span>" in detail
    assert "court.players_here.length" not in detail
