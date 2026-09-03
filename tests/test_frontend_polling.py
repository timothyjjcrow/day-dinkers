"""Regression contracts for serverless-friendly frontend refresh cadence."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "public" / "app-v15.js"
if not APP_PATH.exists():
    APP_PATH = ROOT / "frontend" / "app.js"
APP = APP_PATH.read_text()


def test_account_refresh_is_event_driven_and_presence_has_a_bounded_heartbeat():
    assert "const PRESENCE_HEARTBEAT_INTERVAL_MS = 60_000;" in APP
    poll = APP[APP.index("state.presenceHeartbeatTimer = setInterval"):APP.index("function slotForNow")]
    assert "refreshMe();" not in poll.split("if (tick % 3", 1)[0]
    assert "api('/presence/ping', { method: 'POST' })" in poll
    assert "}, PRESENCE_HEARTBEAT_INTERVAL_MS);" in poll
    assert "document.addEventListener('visibilitychange'" in APP
    assert "window.addEventListener('focus', refreshForegroundState);" in APP
    assert "refreshForegroundState();" in APP


def test_newest_me_response_wins_over_slow_background_requests():
    assert "let meRequestGeneration = 0;" in APP
    assert "function invalidateMeRequests()" in APP
    refresh = APP[APP.index("async function refreshMe()"):APP.index("// ---------- Tabs ----------")]
    assert "const generation = invalidateMeRequests();" in refresh
    assert "const requestOwner = captureAuthenticatedSessionOwner();" in refresh
    assert "generation !== meRequestGeneration" in refresh
    assert "!authenticatedSessionOwnerIsCurrent(requestOwner)" in refresh
    assert "fromRefresh: true" in refresh
    apply = APP[APP.index("function applyMe(data"):APP.index("async function refreshMe()")]
    assert "if (!fromRefresh) invalidateMeRequests();" in apply
    checkin = APP[APP.index("async function confirmArrivalAtCourtAndJoin"):APP.index("function refreshPlayGamesAfterRallyMutation")]
    authoritative = APP[APP.index("function applyAuthoritativeCheckIn"):APP.index("function openCheckInSheet")]
    assert authoritative.index("invalidateMeRequests();") < authoritative.index("state.presence = response")
    assert checkin.index("applyAuthoritativeCheckIn(") < checkin.index("openReadyRally(")


def test_presence_heartbeat_applies_absolute_expiry_as_authoritative():
    poll = APP[APP.index("state.presenceHeartbeatTimer = setInterval"):APP.index("function slotForNow")]
    assert "const pingOwner = captureAuthenticatedSessionOwner();" in poll
    assert "!authenticatedSessionOwnerIsCurrent(pingOwner)" in poll
    assert "const pingPresenceIdentity = JSON.stringify" in poll
    assert "state.presence.checked_in_at || null" in poll
    assert "if (currentPresenceIdentity !== pingPresenceIdentity) return;" in poll
    assert poll.index("invalidateMeRequests();") < poll.index("state.presence = nextPresence;")
    assert "Your court check-in expired" in poll
    assert "refreshLookingBanner();" in poll


def test_all_chat_rooms_share_adaptive_visible_only_polling():
    assert "const CHAT_POLL_DELAYS_MS = [15_000, 30_000, 60_000];" in APP
    assert APP.count("startAdaptiveChatPoll(modal, msgsEl, async () => {") == 7
    assert APP.count("return fresh.items.length > 0;") == 5
    assert "return fresh.items.length > 0 || callChanged;" in APP
    assert "return items.length > 0;" in APP
    assert "currentOverlayEntry()?.el === modal" in APP
    assert "window.addEventListener('online', onOnline);" in APP
    assert "window.addEventListener('focus', onForeground);" in APP
    assert "modal.addEventListener('focusin', onModalFocus);" in APP
    assert "modal._cleanupFns?.push(stop);" in APP


def test_other_recurring_server_polls_have_bounded_cadences():
    assert "const LIVE_DETAIL_POLL_INTERVAL_MS = 15_000;" in APP
    assert "const COMPETITION_POLL_INTERVAL_MS = 20_000;" in APP
    assert APP.count("}, LIVE_DETAIL_POLL_INTERVAL_MS);") == 4
    assert APP.count("}, COMPETITION_POLL_INTERVAL_MS);") == 2
    assert APP.count("}, PRESENCE_HEARTBEAT_INTERVAL_MS);") == 1
    assert "ME_POLL_INTERVAL_MS" not in APP
