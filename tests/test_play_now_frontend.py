"""Frontend-only contracts for the one-flow Play Now rally experience."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_global_play_now_enters_unified_find_flow_not_planner_directly():
    ctas = section("function setupEmptyStateCtas()", "// ---------- Map / Courts ----------")
    play_now_branch = ctas[ctas.index("target === 'play-now'"):ctas.index("target === 'new-ranked-game'")]
    assert "openGameFlow({ mode: 'find' });" in play_now_branch
    assert "openNewGameModal" not in play_now_branch

    flow = section("function openGameFlow", "async function checkInAndStartRally")
    assert "📍 Current check-in" in flow
    assert "⭐ Saved" in flow
    assert "🏠 Home" in flow
    assert "distance_miles" in flow
    assert 'id="game-flow-court-search"' in flow
    assert "Finding never creates a game" in flow


def test_ready_confirmation_checks_in_before_durable_rally_resolution():
    flow = section("async function checkInAndStartRally", "async function openPlayNowCourtPicker")
    checkin = "api(`/courts/${selected.id}/checkin`"
    rally = "await startInstantRally(null"
    assert flow.index(checkin) < flow.index(rally)
    assert "JSON.stringify({ looking_for_game: true })" in flow
    assert "state.presence = checkedIn && checkedIn.presence" in flow
    assert "? checkedIn.presence : fallbackPresence;" in flow
    assert "presenceConfirmed: true" in flow
    assert "expectedCourtId: selected.id" in flow
    assert "Your check-in is saved; try again." in flow
    assert "Retry safely" not in flow
    assert "const callerSession = instantRallySession();" in flow
    assert flow.index("if (!instantRallySessionMatches(callerSession)) return null;") < flow.index(
        "invalidateMeRequests();"
    )
    assert flow.count("if (!instantRallySessionMatches(callerSession)) return null;") >= 3


def test_checkin_sheet_keeps_plain_group_checkin_and_ready_privacy_copy():
    sheet = section("function openCheckInSheet", "// ---------- Games ----------")
    assert "I’m at ${esc(court.name)}" in sheet
    assert "Find a game now" in sheet
    assert "Just check in" in sheet
    assert "JSON.stringify({ looking_for_game: false })" in sheet
    assert "signed-in players nearby" in sheet
    assert "expires automatically" in sheet


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
    assert "confirm_court_presence: confirmCourtPresence" in rally
    assert "courtId: expectedCourtId" in rally
    assert "gameType: requestedGameType" in rally
    assert "maxPlayers: requestedMaxPlayers" in rally
    assert "confirmCourtPresence," in rally
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
    logout = section("function logout()", "function tokenHint()")
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
    assert "['active_checkin_required', 'active_checkin_court_mismatch'].includes" in finish
    assert "refreshMe().finally(reopenConfirmation);" in finish
    assert "transitionModal(sourceModal, () => openGameFlow({" in finish
    assert "mode: 'start', gameType: options.gameType, maxPlayers: options.maxPlayers" in finish

    cards = section("function bindGameButtons", "// Share text")
    assert "b.dataset.instantRally === 'true'" in cards
    assert "await openReadyRally" in cards
    assert "data-rally-court" in APP
    assert "if (game.is_instant)" in section("const joinBtn = el.querySelector('#agb-join')", "const dismissBtn")


def test_instant_games_stay_assembly_first_until_explicit_finish():
    assembly = section("function instantRallyAssembly", "function renderActiveGameBanner")
    assert "!['finding', 'ready', 'full'].includes(serverAssemblyState)" in assembly
    assert "function instantRallyScorePending" in assembly
    assert "Number(game.ready_count)" in assembly
    assert "Math.max(" in assembly
    assert "Finding players" in assembly
    assert "Ready to play" in assembly
    assert "Rally full — ready to play" in assembly

    active_banner = section("function renderActiveGameBanner", "function renderTournamentBanner")
    assert "if (!assembly && game.banner_state === 'live'" in active_banner

    card = section("function gameCardHtml", "function bindGameButtons")
    assert "game.status === 'upcoming' && assembly" in card
    assert "status-banner rally-banner" in card
    instant_card = card[card.index("game.status === 'upcoming' && assembly"):card.index("} else if (game.status === 'upcoming')")]
    assert "data-game-waitlist" not in instant_card
    assert "rallyActionState(rally)" in instant_card
    assert "Travel spot held" in APP
    assert "instantRallyScorePending(game)" in card
    assert "Played? Tap to enter the score." in card

    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "game.spots_left > 0 && (!game.is_instant || assembly)" in detail
    assert "game.is_instant && game.players.length >= 2" in detail
    assert "We finished — enter score" in detail
    assert "!game.is_instant && !startsAhead" in detail
    assert "No additional spot is promised." in detail
    assert "Player identities stay private until you join this rally at the court." in detail
    assert "At the court (${readyCount}/${game.max_players})" in detail
    assert "Players (${readyCount}/${game.max_players})" in detail

    play = section("async function renderPlay", "function updatePlayHeader")
    assert "g.status === 'upcoming' && g.can_enter_score" in play
    assert "? instantRallyScorePending(g)" in play
    assert "!toScore.includes(g) && !toConfirm.includes(g) && !waiting.includes(g)" in play
    profile = section("// My upcoming games", "// Personal play stats")
    assert "const scorePending = (mine.items || []).filter((game) => instantRallyScorePending(game))" in profile
    assert "Played — enter the score" in profile
    assert "!instantRallyClosed(game)" in profile

    closed = section("function instantRallyClosed", "function renderActiveGameBanner")
    assert "game.assembly_active === false" in closed
    assert "!['finding', 'ready', 'full'].includes(assemblyState)" in closed
    assert "This rally ended without enough players." in card
    assert "instantRallyClosed(g)" in play
    assert "instantRallyClosed(game)" in detail
    assert "This rally is no longer accepting players." in detail


def test_looking_summary_drives_exact_rally_and_nearby_actions_with_fallback():
    looking = section("function normalizeLookingRallies", "// \"N players near you want to play\"")
    assert "data.rallies" in looking
    assert "data.open_rallies" in looking
    banner = section("async function refreshLookingBanner", "// ---------- Search suggestions")
    assert "rallyGameId" in banner and "rallyCourtId" in banner
    assert "View nearby players" in banner
    assert "const generation = ++lookingBannerGeneration" in banner
    assert "lookingBannerContext(state.token, areaLatLng()) === context" in banner
    assert banner.count("if (!isCurrent()) return;") == 2
    assert "function clearLookingBanner({ invalidate = true } = {})" in APP
    assert "el.replaceChildren();" in APP
    logout = section("function resetPrivateUiForLogout", "function logout()")
    assert "clearLookingBanner();" in logout
    map_area = section("$('#use-map-area')?.addEventListener", "// NB: don't pass the click event")
    assert "clearLookingBanner();" in map_area
    assert "refreshLookingBanner();" in map_area

    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert "Promise.all" in nearby
    assert "players/looking" in nearby
    assert "rank(b) - rank(a)" in nearby
    assert "data-rally-action" in nearby
    assert "openReadyRally" in nearby


def test_play_now_controls_have_mobile_and_accessibility_contracts():
    assert 'role="listbox" aria-label="Court choices" aria-busy="true"' in APP
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
    assert 'id="nearby-skill"' in APP
    assert '<details class="nearby-filter"' in APP
    assert "How location sharing works" in APP
    assert 'action = `<button class="btn btn-secondary btn-sm" data-msg="${p.id}">Message</button>`' in APP


def test_court_detail_uses_private_safe_presence_aggregate():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert "const visiblePlayersHere = Array.isArray(court.players_here)" in detail
    assert "Math.max(visiblePlayersHere.length, Number(court.players_here_count) || 0)" in detail
    assert "privatePlayersHere" in detail
    assert "Playing now (${nHere})" in detail
    assert "court.players_here.length" not in detail
