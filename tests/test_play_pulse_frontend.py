"""Frontend contracts for fixed one-hour, destination-only play pulses."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()
SW = (ROOT / "public" / "sw.js").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_me_recovers_only_a_live_server_expiring_pulse_and_refreshes_hero():
    normalize = section("function playPulseFromValue", "function rallySummaryFromValue")
    assert "value.expires_at ?? value.expiresAt" in normalize
    assert "function normalizeActivePlayPulse" in normalize
    assert "new Date(pulse.expiresAt).getTime() <= Date.now()" in normalize
    assert "value.active !== false" in normalize

    apply_me = section("function applyMe(data", "function dismissedInvites")
    assert "normalizeActivePlayPulse(data.active_play_pulse)" in apply_me
    assert "previousPlayPulseView" in apply_me
    assert "nextPlayPulseView" in apply_me
    assert "clearPlayPulseCreateAttempt(nextAccountId, state.activePlayPulse.courtId)" in apply_me
    assert "renderPlay({ useCachedData: true })" in apply_me


def test_remote_hero_exposes_play_now_intents_without_claiming_presence():
    hero = section("function rallyLauncherHtml", "async function renderPlay")
    assert 'data-goto="instant-rally"' in hero
    assert "<b>${here ? 'Play here' : 'At a court'}</b>" in hero
    assert 'data-goto="on-my-way"' in hero
    assert '<b>On my way</b>' in hero
    assert 'data-goto="play-pulse"' in hero
    assert '<b>Free this hour</b>' in hero
    assert 'data-goto="new-game"' in hero
    assert 'data-goto="ranked-match"' in hero
    assert 'data-goto="play-now"' not in hero
    assert "activePlayPulseBannerHtml(pulse)" in hero
    assert "Only the court is shared, not your location." in APP

    ctas = section("function setupEmptyStateCtas", "// ---------- Map / Courts ----------")
    assert "target === 'play-pulse'" in ctas
    assert "openPlayPulseCourtPicker();" in ctas


def test_court_picker_is_parameterized_and_explains_the_commitment():
    picker = section("async function openPlayNowCourtPicker", "function arrivalRequestIsAmbiguous")
    assert "intent = 'play-now'" in picker
    assert "const pulseIntent = intent === 'play-pulse'" in picker
    assert "Choose a court you could reach this hour." in picker
    assert "this does not check you in" in picker
    assert "Your current location stays private." in picker
    assert "server" not in picker.lower()
    assert "retries never" not in picker
    assert "await declarePlayPulse(selected, modal, confirmButton, errorEl)" in picker
    assert "openPlayNowCourtPicker({ ...options, intent: 'play-pulse' })" in picker


def test_publish_attempt_is_durable_account_and_court_scoped_and_idempotent():
    attempts = section("const playPulseCreateAttemptKey", "function sanitizePlannerInvitee")
    assert "`${PLAY_PULSE_CREATE_ATTEMPT_PREFIX}${accountId}:${expectedCourtId}`" in attempts
    assert "availableStorage('localStorage')" in attempts
    assert "persistRecoveryValue(key, JSON.stringify(fresh))" in attempts
    assert "if (existing) return existing;" in attempts
    assert "courtId: Number(courtId)" in attempts

    publish = section("async function declarePlayPulse", "async function cancelPlayPulse")
    assert "api('/play/pulse'" in publish
    assert "method: 'PUT'" in publish
    assert "court_id: selected.id" in publish
    assert "client_attempt_id: attempt.id" in publish
    assert "button.dataset.playPulseCreating === 'true'" in publish
    assert "playPulseRequestIsAmbiguous(error)" in publish
    assert "Couldn’t confirm Free this hour. Try again." in publish
    assert "Retry safely" not in publish
    assert "normalizeActivePlayPulse(error?.data?.pulse)" in publish
    assert "if (!ambiguous)" in publish
    assert "error.code !== 'client_attempt_id_conflict'" not in publish


def test_active_pulse_has_directions_details_and_cancellation():
    details = section("function openPlayPulseDetails", "function activePlayPulseBannerHtml")
    assert "playPulseDirectionsUrl(pulse)" in details
    assert "Only the court is shared, not your location." in details
    assert "The first player who joins starts a casual game for both of you." in details
    assert "Directions" in details
    assert "End Free this hour" in details
    assert "cancelPlayPulse(pulse" in details

    cancel = section("async function cancelPlayPulse", "function playPulseCommitmentCopy")
    assert "api(`/play/pulses/${pulse.id}`, { method: 'DELETE' })" in cancel
    assert cancel.index("invalidateMeRequests();") < cancel.index("state.activePlayPulse = null;")
    assert "response?.cancelled === false && response?.pulse?.end_reason === 'matched'" in cancel
    assert "your quick game was not cancelled" in cancel
    assert cancel.count("if (state.activePlayPulse?.id === pulse.id) state.activePlayPulse = null;") == 2
    assert "error.code === 'pulse_not_found'" in cancel

    banner = section("function activePlayPulseBannerHtml", "async function checkInAndStartRally")
    assert "Free this hour at" in banner
    assert "Only the court is shared, not your location." in banner
    assert "data-play-pulse-details" in banner
    assert "Directions" in banner
    assert "data-play-pulse-cancel" in banner


def test_play_pulse_user_copy_consistently_uses_free_this_hour():
    pulse_ui = section("async function declarePlayPulse", "async function checkInAndStartRally")
    for old_copy in (
        "Available to play at",
        "Available until",
        "Cancel availability",
        "One-hour availability",
        "one-hour availability",
    ):
        assert old_copy not in pulse_ui
    assert "You’re free this hour at" in pulse_ui
    assert "Free until" in pulse_ui
    assert "End Free this hour" in pulse_ui

    assert "court room" not in APP.lower()
    assert "Opening posted in Court chat" in APP
    assert "Sharing one opening in Court chat" in APP


def test_nearby_cards_never_imply_presence_and_require_explicit_confirmation():
    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert "normalizeLookingPulses(looking)" in nearby
    assert "wants to play at ${esc(pulse.courtName)} until" in nearby
    assert "intended destination" not in nearby
    assert "Join them for a casual game." in nearby
    assert 'data-play-pulse-accept="${pulse.id}"' in nearby
    assert ">I’m in</button>" in nearby
    assert "openPlayPulseAcceptConfirmation(pulse)" in nearby

    confirmation = section("function openPlayPulseAcceptConfirmation", "function openPlayPulseDetails")
    assert "playPulseCommitmentCopy(pulse)" in confirmation
    assert "I’m in" in confirmation
    assert "acceptPlayPulse(" in confirmation
    commitment = section("function playPulseCommitmentCopy", "async function acceptPlayPulse")
    assert "Only the court is shared, not your location." in commitment
    assert "we’ll set up a casual game" in commitment

    banner = section("async function refreshLookingBanner", "// ---------- Search suggestions")
    assert "const pulses = normalizeLookingPulses(data)" in banner
    assert "if (!rally && !count && !pulse)" in banner
    assert "can play at ${esc(pulse.courtName)} this hour" in banner
    assert "View details" in banner


def test_acceptance_attempt_is_durable_pulse_scoped_and_double_tap_safe():
    attempts = section("const playPulseAcceptAttemptKey", "function sanitizePlannerInvitee")
    assert "`${PLAY_PULSE_ACCEPT_ATTEMPT_PREFIX}${accountId}:${expectedPulseId}`" in attempts
    assert "acceptCapability" in attempts
    assert "capabilityRefreshedAt: Date.now()" in attempts
    assert "persistRecoveryValue(key, JSON.stringify(refreshed))" in attempts
    assert "persistRecoveryValue(key, JSON.stringify(fresh))" in attempts

    recovery = section("async function refreshPlayPulseAcceptAttempt", "async function acceptPlayPulse")
    assert "normalizeLookingPulses(looking).find" in recovery
    assert "api(`/play/pulses/${pulse.id}/accept`" in recovery
    assert "accept_capability: record.acceptCapability" in recovery
    assert "client_attempt_id: record.id" in recovery
    assert "if (error.code !== 'pulse_not_found') throw error" in recovery
    assert "retry once with the same id" in recovery

    accept = section("async function acceptPlayPulse", "function openPlayPulseAcceptConfirmation")
    assert "button.dataset.playPulseAccepting === 'true'" in accept
    assert "playPulseRequestIsAmbiguous(error)" in accept
    assert "Couldn’t confirm the game. Try again." in accept
    assert "Retry safely" not in accept
    assert "clearPlayPulseAcceptAttempt(callerSession.userId, pulse.id, attempt.id)" in accept
    assert "openGameScreen(gameId)" in accept
    assert "postPlayPulseAcceptance(pulse, attempt, callerSession)" in accept
    assert "if (!ambiguous)" in accept
    assert "error.code !== 'client_attempt_id_conflict'" not in accept

    reset = section("function resetPrivateUiForLogout", "function logout")
    assert "clearPlayPulseCreateAttempts(accountId);" in reset
    assert "clearPlayPulseAcceptAttempts(accountId);" in reset
    logout = section("function logout", "function tokenHint")
    assert logout.index("resetPrivateUiForLogout(accountId);") < logout.index("state.token = null;")


def test_errors_mobile_targets_and_shell_revision_are_explicit():
    for code in (
        "active_checkin_present",
        "active_arrival",
        "active_rally",
        "active_game",
        "pulse_already_active",
        "pulse_not_found",
        "pulse_start_window_closed",
        "invalid_accept_capability",
        "court_location_unavailable",
        "pulse_conflict",
    ):
        assert f"{code}:" in APP

    assert ".play-pulse-active-actions a, .play-pulse-active-actions button" in STYLES
    assert "min-height: 44px" in STYLES
    assert ".play-pulse-nearby-card" in STYLES
    assert "grid-template-columns: auto minmax(0, 1fr)" in STYLES
    assert ".play-pulse-nearby-card [data-play-pulse-accept]" in STYLES
    assert "min-width: 0" in STYLES
    assert "thirdshot-v15-r58" in SW
