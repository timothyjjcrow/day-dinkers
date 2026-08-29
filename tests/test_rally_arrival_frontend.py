"""Frontend contracts for reserving one remote rally spot while traveling."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_discovery_capability_and_capacity_survive_normalization_and_dom():
    normalize = section("function rallySummaryFromValue", "function playerRallySummary")
    for field in (
        "on_the_way_count",
        "roster_count",
        "committed_count",
        "physical_spots_left",
        "spots_left",
        "my_arrival",
        "arrival_available",
    ):
        assert field in normalize
    assert "value.arrival_capability ?? value.arrivalCapability ?? value.discovery_token" in normalize
    assert "const arrivalAvailable = !!arrivalCapability" in normalize
    assert "rawArrivalAvailable == null" in normalize

    datasets = section("function rallyDatasetAttributes", "function rallyActionState")
    assert "data-rally-arrival-capability" in datasets
    assert "dataset.rallyArrivalCapability" in datasets
    assert "data-rally-arrival-available" in datasets
    assert "dataset.rallyArrivalAvailable" in datasets
    assert "data-rally-on-way-count" in datasets
    assert "data-rally-roster-count" in datasets
    assert "dataset.rallyRosterCount" in datasets
    assert "data-rally-physical-spots-left" in datasets

    player = section("function playerRallySummary", "function normalizeLookingRallies")
    assert "arrival_available: player.arrival_available ?? player.arrivalAvailable" in player
    assert "roster_count: player.roster_count ?? court.roster_count" in player

    reserve = section("async function reserveRallyArrival", "function openRallyArrivalSheet")
    assert "if (rally.arrivalCapability) body.arrival_capability = rally.arrivalCapability;" in reserve
    assert "discovery_token" not in reserve


def test_remote_rally_action_is_arrival_while_court_presence_still_joins():
    action = section("function rallyActionState", "function playerRallySummary")
    assert "isCheckedInAtCourt(rally.courtId)" in action
    assert "label: 'Join rally'" in action
    assert "label: 'I’m on my way'" in action
    assert "label: 'View held spot'" in action
    assert "label: 'Remote spot held'" in action
    assert "label: 'Rally committed'" in action
    assert "label: 'Rally wrapping up'" in action
    assert action.index("isCheckedInAtCourt(rally.courtId)") < action.index(
        "if (!rally.arrivalAvailable)"
    )
    assert action.index("if (rally.onWayCount > 0)") < action.index(
        "if (!rally.arrivalAvailable)"
    )
    assert action.index("if (rally.spotsLeft <= 0)") < action.index(
        "if (!rally.arrivalAvailable)"
    )

    gate = section("async function openReadyRally", "function openCheckInSheet")
    assert "openRallyArrivalSheet(summary)" in gate
    assert "api(`/games/${gameId}/join`" in gate
    assert gate.index("if (!isCheckedInAtCourt(courtId))") < gate.index(
        "api(`/games/${gameId}/join`"
    )
    assert "expectedCourtId: courtId" in gate
    assert "finding the next rally at this court" in gate


def test_eta_sheet_is_accessible_and_never_claims_physical_readiness():
    sheet = section("function openRallyArrivalSheet", "async function cancelRallyArrival")
    assert "${[5, 10, 15].map" in sheet
    assert '<fieldset class="arrival-eta-fieldset">' in sheet
    assert "<legend>When can you arrive?</legend>" in sheet
    assert 'type="radio" name="arrival-eta"' in sheet
    assert 'role="alert" tabindex="-1"' in sheet
    assert "The server will confirm the exact expiration time" in sheet
    assert "the hold ends sooner if the rally closes" in sheet
    assert "You’re physically ready" not in sheet
    assert "data-arrival-directions" in sheet
    assert "rally.spotsLeft <= 0 || rally.onWayCount > 0" in sheet
    assert "!rally.arrivalAvailable || !rally.arrivalCapability" in sheet
    assert "This rally is wrapping up, so remote spot holds are closed." in sheet
    directions = section(
        "function hydrateArrivalDirections", "async function reserveRallyArrival"
    )
    assert "courtDirectionsUrl" in directions
    assert "api(`/courts/${rally.courtId}`)" in directions
    directions_url = section("function courtDirectionsUrl", "function selectCourtOnMap")
    assert "const destination = address || (hasCoordinates" in directions_url
    assert "if (!destination) return '';" in directions_url


def test_attempt_is_account_and_game_scoped_and_ambiguous_retries_do_not_extend():
    attempts = section("const rallyArrivalAttemptKey", "function sanitizePlannerInvitee")
    assert "`${RALLY_ARRIVAL_ATTEMPT_PREFIX}${accountId}:${expectedGameId}`" in attempts
    assert "if (existing) return existing;" in attempts
    assert "etaMinutes: eta" in attempts
    assert "sessionStorage.setItem(key, JSON.stringify(fresh))" in attempts
    assert "clearRallyArrivalAttempt" in attempts

    reserve = section("async function reserveRallyArrival", "function openRallyArrivalSheet")
    assert "client_attempt_id: attempt.id" in reserve
    assert "eta_minutes: attempt.etaMinutes" in reserve
    assert "arrivalRequestIsAmbiguous(error)" in reserve
    assert "!arrivalRequestIsAmbiguous(error)" in reserve
    assert "clearRallyArrivalAttempt(callerSession.userId, gameId, attempt.id)" in reserve
    assert "refreshPlayGamesAfterRallyMutation();" in reserve

    sheet = section("function openRallyArrivalSheet", "async function cancelRallyArrival")
    assert "won’t create or extend another hold" in sheet
    assert "Retry safely: the same request will not create or extend another hold." in sheet
    assert "input.disabled = Number(input.value) !== saved.etaMinutes" in sheet

    logout = section("function logout()", "function tokenHint()")
    assert "rallyArrivalInFlight = null;" in logout
    assert "clearRallyArrivalAttempt(accountId);" in logout
    assert logout.index("clearRallyArrivalAttempt(accountId);") < logout.index("state.token = null;")


def test_active_trip_banner_details_cancel_and_explicit_arrival_confirmation():
    banner = section("function renderActiveGameBanner", "function renderTournamentBanner")
    assert banner.index("const trip = normalizeActiveArrival") < banner.index("const game = state.activeGame")
    assert "Heading to ${esc(trip.courtName)}" in banner
    assert 'id="agb-arrived"' in banner
    assert "openArrivalCheckInConfirmation(trip)" in banner

    copy = section("function arrivalReservationCopy", "function rallyCourtForDirections")
    assert "A spot is held until ${fmtTimeShort(arrival.expiresAt)}, as long as the rally stays active. Check in when you arrive." in copy

    detail = section("function openArrivalDetails", "function openArrivalCheckInConfirmation")
    assert 'id="arrival-im-here"' in detail
    assert 'id="arrival-cancel"' in detail
    assert "Cancel trip" in detail
    assert "data-arrival-directions" in detail

    checkin = section(
        "function openArrivalCheckInConfirmation",
        "function clearArrivalAfterConfirmedMembership",
    )
    assert "Only continue once you’re physically at this court." in checkin
    assert "Check in &amp; join" in checkin
    assert "api(`/courts/${rally.courtId}/checkin`" in checkin
    assert "JSON.stringify({ looking_for_game: false })" in checkin
    assert "looking_for_game: false" in checkin
    assert "clearRallyArrivalAttempt" not in checkin
    assert "state.activeArrival = null" not in checkin
    assert checkin.index("await api(`/courts/${rally.courtId}/checkin`") < checkin.index(
        "await openReadyRally(rally, button)"
    )
    assert checkin.index("renderPresenceBanner();") < checkin.index(
        "await openReadyRally(rally, button)"
    )

    membership = section(
        "function clearArrivalAfterConfirmedMembership",
        "function openCheckInSheet",
    )
    assert "clearRallyArrivalAttempt(callerSession.userId, gameId)" in membership
    assert "state.activeArrival = null" in membership
    assert membership.index("invalidateMeRequests();") < membership.index(
        "state.activeArrival = null"
    )
    assert "refreshPlayGamesAfterRallyMutation();" in membership
    surface_refresh = section(
        "function refreshPlayGamesAfterRallyMutation",
        "function clearArrivalAfterConfirmedMembership",
    )
    assert "state.playGamesCache = null;" in surface_refresh
    assert "state.tab === 'play' && state.playSeg === 'games'" in surface_refresh
    assert "renderPlay();" in surface_refresh
    recovery = section(
        "async function recoverRallyAfterConfirmedArrival", "async function openReadyRally"
    )
    assert "safePositiveId(result && result.game && result.game.id)" in recovery
    assert recovery.index("safePositiveId(result && result.game && result.game.id)") < recovery.index(
        "clearArrivalAfterConfirmedMembership(callerSession, arrivalGameId)"
    )
    direct_join = section("async function openReadyRally", "function openCheckInSheet")
    assert direct_join.index("await api(`/games/${gameId}/join`") < direct_join.index(
        "clearArrivalAfterConfirmedMembership(callerSession, gameId)"
    )
    assert "if (error.code === 'already_joined')" in direct_join
    already_joined = direct_join[direct_join.index("if (error.code === 'already_joined')"):]
    assert already_joined.index("clearArrivalAfterConfirmedMembership") < already_joined.index(
        "openResolvedRallyGame"
    )

    cancel = section("async function cancelRallyArrival", "function openArrivalDetails")
    assert "api(`/games/${arrival.gameId}/arrival`, { method: 'DELETE' })" in cancel
    assert "state.activeArrival = null" in cancel
    assert "refreshPlayGamesAfterRallyMutation();" in cancel


def test_stale_reservation_presence_recovery_is_private_and_actionable_offline():
    assert "already_at_court:" in APP
    assert "active_checkin_elsewhere:" in APP

    sheet = section("function openRallyArrivalSheet", "async function cancelRallyArrival")
    stale = sheet[sheet.index("if (['already_at_court', 'active_checkin_elsewhere']") :]
    assert "await refreshMe().catch(() => false);" in stale
    assert "error.code === 'already_at_court'" in stale
    assert "looking_for_game: false" in stale
    assert "openAtCourtRallyJoinSheet(rally)" in stale
    assert "openArrivalCheckInConfirmation(rally)" in stale
    assert stale.index("await refreshMe().catch(() => false);") < stale.index(
        "isCheckedInAtCourt(rally.courtId)"
    )

    at_court = section(
        "function openAtCourtRallyJoinSheet", "function openArrivalCheckInConfirmation"
    )
    assert "Your court check-in is private." in at_court
    assert "await openReadyRally(rally, button)" in at_court
    assert 'role="alert" tabindex="-1"' in at_court


def test_wrapping_rally_disables_only_remote_hold_and_detail_cta_requires_capability():
    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "rally.arrivalAvailable && rally.arrivalCapability" in detail
    assert "This rally is wrapping up, so remote spot holds are closed." in detail
    assert detail.index("isCheckedInAtCourt(rally.courtId) && (rally.spotsLeft > 0 || myArrival)") < detail.index(
        "rally.arrivalAvailable && rally.arrivalCapability"
    )

    banner = section("async function refreshLookingBanner", "// ---------- Search suggestions")
    assert "el.dataset.rallyArrivalAvailable = String(rally.arrivalAvailable)" in banner
    assert "Remote holds are closed because this rally is wrapping up." in banner


def test_server_capacity_ceiling_and_modal_titles_are_not_rewritten_or_double_escaped():
    normalize = section("function rallySummaryFromValue", "function playerRallySummary")
    assert "const maxPlayers = Math.max(1, Number(" in normalize
    assert "const maxPlayers = Math.max(readyCount" not in normalize
    assembly = section("function instantRallyAssembly", "function instantRallyScorePending")
    assert "const maxPlayers = Math.max(1, Number(game.max_players) || 4);" in assembly
    assert "const maxPlayers = Math.max(committedCount" not in assembly
    counts = section("function rallyCountsText", "function arrivalEtaLabel")
    assert "const max = Math.max(1, Number(rally?.maxPlayers) || 4);" in counts
    assert "const max = Math.max(ready" not in counts

    sheet = section("function openRallyArrivalSheet", "async function cancelRallyArrival")
    assert "modalHead(`🚗 Head to ${rally.courtName}`)" in sheet
    assert "modalHead(`🚗 Head to ${esc(rally.courtName)}`)" not in sheet
    checkin = section("function openAtCourtRallyJoinSheet", "function clearArrivalAfterConfirmedMembership")
    assert "modalHead(`📍 You’re at ${rally.courtName}`)" in checkin
    assert "modalHead(`📍 Are you at ${rally.courtName}?`)" in checkin
    assert "modalHead(`📍 You’re at ${esc(rally.courtName)}`)" not in checkin


def test_ready_and_traveling_counts_and_roster_eta_are_separate_everywhere():
    counts = section("function rallyCountsText", "function arrivalEtaLabel")
    assert "rally?.rosterCount" in counts
    assert "roster > ready" in counts
    assert "physically ready" in counts
    assert "joined" in counts
    assert "on the way" in counts

    banner = section("async function refreshLookingBanner", "// ---------- Search suggestions")
    assert "rallyCountsText(rally)" in banner
    assert "rallyOnWayCount" in banner
    assert "rallyRosterCount" in banner

    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert "rallyCountsText(rally)" in nearby
    assert "I’m on my way" not in nearby  # centralized in rallyActionState
    assert "data-rally-action" in nearby

    cards = section("function gameCardHtml", "function bindGameButtons")
    assert "rallyCountsText(assembly)" in cards
    assert "rallyActionState(rally)" in cards

    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "Joined roster (${rosterCount}/${game.max_players})" in detail
    assert "On the way (${rally.onWayCount})" in detail
    assert "game.arrivals" in detail
    assert "ETA ${esc(fmtTimeShort(arrival.arrivesAt))}" in detail
    assert "Their identity is visible only to the current court roster." in detail
    assert "Your spot is held" in detail
    invite = section("function openRosterBoostSheet", "function crewSummaryFrom")
    assert "rallyCountsText(rallySummaryFromValue(game))" in invite
    assert "traveling player’s held spot" in invite

    assembly = section("function instantRallyAssembly", "function instantRallyScorePending")
    assert "const readyCount = Number.isFinite(aggregateReadyCount)" in assembly
    assert "const rosterCount = Number.isFinite(aggregateRosterCount)" in assembly
    assert "visibleRosterCount" in assembly
    assert "visibleReadyCount" not in assembly


def test_confirmed_arrival_mutations_invalidate_older_me_reads_before_state_changes():
    reserve = section("async function reserveRallyArrival", "function openRallyArrivalSheet")
    put_mutation = reserve[reserve.index("const arrival = normalizeActiveArrival") :]
    assert put_mutation.index("invalidateMeRequests();") < put_mutation.index(
        "state.activeArrival = arrival;"
    )
    assert put_mutation.index("state.activeArrival = arrival;") < put_mutation.index(
        "refreshMe().catch(() => {});"
    )

    cancel = section("async function cancelRallyArrival", "function openArrivalDetails")
    assert cancel.index("invalidateMeRequests();") < cancel.index("state.activeArrival = null;")
    assert cancel.index("state.activeArrival = null;") < cancel.index(
        "refreshMe().catch(() => {});"
    )

    fingerprint = section("function gameFingerprint", "function gameScreenHtml")
    assert "game.roster_count" in fingerprint
    assert "game.arrival_available" in fingerprint
    assert "!!(game.arrival_capability || game.discovery_token)" in fingerprint


def test_arrival_errors_notifications_and_mobile_targets_are_explicit():
    for code in (
        "rally_full",
        "arrival_slot_taken",
        "active_arrival_elsewhere",
        "arrival_already_active",
        "already_at_court",
        "active_checkin_elsewhere",
        "invalid_eta_minutes",
        "invalid_client_attempt_id",
        "client_attempt_id_conflict",
    ):
        assert f"{code}:" in APP
    assert "rally_arrival: '🚗'" in APP
    assert "rally_arrival_ended: '⚠️'" in APP
    assert "['active_arrival_elsewhere', 'arrival_already_active'].includes(error.code)" in APP

    assert ".active-game-banner.state-arrival" in STYLES
    assert ".arrival-eta-options" in STYLES
    assert ".arrival-summary.has-roster" in STYLES
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in STYLES
    assert ".arrival-eta-option > span" in STYLES
    assert "min-height: 54px" in STYLES
    assert "#arrival-im-here, #arrival-cancel, #arrival-checkin-confirm, #at-court-join" in STYLES
    assert "min-height: 48px" in STYLES
    assert ".nearby-rally-card [data-rally-action]" in STYLES
    assert ".modal-close.btn-block" in STYLES

    dialog_labels = section("function setDialogLabel", "function openModal")
    assert "const visibleText = btn.textContent.trim();" in dialog_labels
    assert "else if (!visibleText || /^[✕×]$/.test(visibleText))" in dialog_labels
