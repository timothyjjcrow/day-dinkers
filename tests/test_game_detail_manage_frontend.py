"""Focused source contracts for the game detail/manage audit surface."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_detail_header_roster_waitlist_and_dead_end_actions_are_visible():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    assert '<h3 class="game-detail-title"' in detail
    title = detail[detail.index('<h3 class="game-detail-title"'):detail.index('</h3>', detail.index('<h3 class="game-detail-title"'))]
    assert 'gameTypeTag' not in title
    assert '${detailMeta}' in detail
    assert 'class="game-player-row"' in detail
    assert 'class="game-player-overflow"' in detail
    assert 'Waitlist (${game.waitlist_count})' in detail
    assert 'id="gs-waitlist-auto"' in detail
    assert 'data-promote-waitlist' in detail
    assert '${waitlistHtml}' in detail
    assert 'id="gs-find-nearby"' in detail
    assert 'id="gs-plan-new"' in detail
    assert 'id="gs-message-group"' in detail


def test_invitation_score_and_host_leave_flows_are_explicit():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    screen = section('async function openGameScreen', 'function safeNotificationOverlayRoute')
    assert 'my_invite_status === \'pending\'' in detail
    assert 'Accept invitation' in detail
    assert 'id="gs-decline-invite"' in detail
    assert "api(`/games/${gameId}/invites/decline`" in screen
    assert 'id="gs-fix-score"' in detail
    assert 'scoreAutoConfirmCopy(game)' in detail
    assert "mode: 'counter'" in screen
    assert "mode: 'correction'" in screen
    assert 'confirmGameLeave(game, playNoun, btn)' in screen
    assert 'transfer_to_user_id: decision.transferToUserId' in screen
    assert "fresh.leave_outcome === 'host_transferred'" in screen
    assert "label: 'Rejoin'" in screen


def test_calendar_directions_rating_and_async_space_are_complete():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    screen = section('async function openGameScreen', 'function safeNotificationOverlayRoute')
    calendar = section('function openGameCalendarMenu', 'function downloadTournamentIcs')
    assert "game.status === 'upcoming' && courtDirectionsUrl(court)" in detail
    assert 'class="game-info-strip"' in detail
    assert 'Checking court conditions…' in detail
    assert 'Calculating rating stakes…' in detail
    assert "game.players.length >= 2" in screen
    assert 'If you win <strong>+${winPts}</strong>' in screen
    assert 'openThirdShotRatingExplainer({ parentModal: modal })' in screen
    assert 'Google Calendar' in calendar
    assert 'Apple, Outlook, or another app' in calendar
    assert 'Subscribe to all my games' in calendar
    assert 'openGameCalendarMenu(game)' in screen


def test_chat_has_preview_roster_privacy_and_back_target():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    chat = section('async function openGameChat', 'function mutualFriendsText')
    assert 'id="gs-chat-card"' in detail
    assert 'game.chat_preview' in detail
    assert 'game.chat_unread' in detail
    assert 'game-chat-roster' in chat
    assert 'id="gc-back-game"' in chat
    assert 'private to this roster' in chat
    assert "closeModal(modal)" in chat


def test_play_again_is_review_first_and_old_one_tap_rematch_is_gone():
    planner = section('async function openPostGamePlanner', 'function completedCrewConnectionsHtml')
    assert "crewRequest || api(`/games/${game.id}/crew`)" in planner
    assert "method: 'POST'" not in planner
    assert 'openNewGameModal(options)' in planner
    assert 'id="ng-save-group"' in APP
    assert "api(`/games/${sourceGameId}/crew`, {" in APP
    assert "body: JSON.stringify({ name: saveGroupName })" in APP
    assert 'openCompletedCrewPlanner' not in APP
    assert 'gs-rematch' not in APP


def test_game_detail_styles_reserve_space_and_meet_touch_targets():
    for selector in (
        '.game-detail-meta', '.game-player-row', '.game-player-overflow',
        '.game-waitlist', '.game-info-strip', '.game-chat-preview',
        '.score-deadline', '.game-what-now', '.host-transfer-option',
    ):
        assert selector in CSS
    assert '.game-player-row .player-profile-link { min-height: 48px;' in CSS
    assert '.game-info-slot { min-height: 35px;' in CSS
