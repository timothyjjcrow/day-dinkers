"""Frontend contracts for truthful, explicit public court chat."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin : APP.index(end, begin)]


def test_court_detail_names_chat_directly_and_shows_acl_safe_preview_and_unread():
    detail = section("async function openCourtDetail", "function openCourtPlayerActions")

    assert "court.chat_last_message" in detail
    assert "court.chat_unread" in detail
    assert "inboxMessagePreviewText" in detail
    assert "<b>Court chat</b>" in detail
    assert "data-court-chat-unread" in detail
    assert "unread message" in detail
    assert "Message players" not in detail
    assert "Message the court" not in detail
    assert "court.chat_unread = 0" in detail
    assert ".cd-chat-action" in STYLES


def test_first_open_explains_that_the_room_is_public_before_marking_seen():
    chat = section("async function openCourtChat", "// ---------- Crews ----------")
    before_seen_write = chat[: chat.index("localStorage.setItem(privacyKey, '1')")]

    assert "pp_court_chat_privacy:${state.me.id}:${court.id}" in chat
    assert "await openActionConfirmation" in before_seen_write
    assert "Court chat is public" in before_seen_write
    assert "Anyone signed in to Third Shot can read and post here" in before_seen_write
    assert "if (!continued) return null" in before_seen_write


def test_room_copy_and_composer_match_the_real_access_model():
    chat = section("async function openCourtChat", "// ---------- Crews ----------")

    assert "Public room — anyone on Third Shot can read and post." in chat
    assert 'aria-label="Message court chat"' in chat
    assert 'placeholder="Write a message…"' in chat
    assert "everyone at this court can read it" not in chat
    assert 'aria-label="Message the court"' not in chat


def test_join_leave_and_mute_are_explicit_durable_room_actions():
    chat = section("async function openCourtChat", "// ---------- Crews ----------")

    for action in ('data-cc-subscription="join"', 'data-cc-subscription="leave"',
                   'data-cc-subscription="mute"'):
        assert action in chat
    assert "`/courts/${court.id}/chat/subscription`" in chat
    assert "method: 'PUT', body: JSON.stringify(payload)" in chat
    assert "{ joined: false }" in chat
    assert "{ joined: true, muted:" in chat
    assert "Leave this room?" in chat
    assert ".court-chat-subscription" in STYLES
