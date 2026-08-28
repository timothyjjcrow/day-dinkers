"""Regression contracts for serverless-friendly frontend refresh cadence."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "public" / "app-v13.js"
if not APP_PATH.exists():
    APP_PATH = ROOT / "frontend" / "app.js"
APP = APP_PATH.read_text()


def test_background_account_refresh_is_at_most_once_per_minute():
    assert "const ME_POLL_INTERVAL_MS = 60_000;" in APP
    assert "}, ME_POLL_INTERVAL_MS);" in APP
    assert "window.addEventListener('focus', refreshForegroundState);" in APP
    assert "refreshForegroundState();" in APP


def test_all_chat_rooms_share_adaptive_visible_only_polling():
    assert "const CHAT_POLL_DELAYS_MS = [15_000, 30_000, 60_000];" in APP
    assert APP.count("startAdaptiveChatPoll(modal, msgsEl, async () => {") == 6
    assert APP.count("return fresh.items.length > 0;") == 6
    assert "currentOverlayEntry()?.el === modal" in APP
    assert "window.addEventListener('online', onOnline);" in APP
    assert "window.addEventListener('focus', onForeground);" in APP
    assert "modal.addEventListener('focusin', onModalFocus);" in APP
    assert "modal._cleanupFns?.push(stop);" in APP


def test_other_recurring_server_polls_have_bounded_cadences():
    assert "const LIVE_DETAIL_POLL_INTERVAL_MS = 15_000;" in APP
    assert "const COMPETITION_POLL_INTERVAL_MS = 20_000;" in APP
    assert APP.count("setInterval(") == 5
    assert APP.count("}, LIVE_DETAIL_POLL_INTERVAL_MS);") == 2
    assert APP.count("}, COMPETITION_POLL_INTERVAL_MS);") == 2
    assert APP.count("}, ME_POLL_INTERVAL_MS);") == 1
