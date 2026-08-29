"""Regression contracts for serverless-friendly frontend refresh cadence."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "public" / "app-v15.js"
if not APP_PATH.exists():
    APP_PATH = ROOT / "frontend" / "app.js"
APP = APP_PATH.read_text()


def test_background_account_refresh_is_at_most_once_per_minute():
    assert "const ME_POLL_INTERVAL_MS = 60_000;" in APP
    assert "}, ME_POLL_INTERVAL_MS);" in APP
    assert "window.addEventListener('focus', refreshForegroundState);" in APP
    assert "refreshForegroundState();" in APP


def test_newest_me_response_wins_over_slow_background_requests():
    assert "let meRequestGeneration = 0;" in APP
    assert "function invalidateMeRequests()" in APP
    refresh = APP[APP.index("async function refreshMe()"):APP.index("// ---------- Tabs ----------")]
    assert "const generation = invalidateMeRequests();" in refresh
    assert "const requestToken = state.token;" in refresh
    assert "generation !== meRequestGeneration || state.token !== requestToken" in refresh
    assert "fromRefresh: true" in refresh
    apply = APP[APP.index("function applyMe(data"):APP.index("async function refreshMe()")]
    assert "if (!fromRefresh) invalidateMeRequests();" in apply
    checkin = APP[APP.index("async function checkInAndStartRally"):APP.index("async function openPlayNowCourtPicker")]
    assert checkin.index("invalidateMeRequests();") < checkin.index("state.presence = checkedIn")


def test_presence_heartbeat_applies_absolute_expiry_as_authoritative():
    poll = APP[APP.index("state.mePollTimer = setInterval"):APP.index("function slotForNow")]
    assert "const pingToken = state.token;" in poll
    assert "state.token !== pingToken" in poll
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
    assert APP.count("setInterval(") == 6
    assert APP.count("}, LIVE_DETAIL_POLL_INTERVAL_MS);") == 3
    assert APP.count("}, COMPETITION_POLL_INTERVAL_MS);") == 2
    assert APP.count("}, ME_POLL_INTERVAL_MS);") == 1
