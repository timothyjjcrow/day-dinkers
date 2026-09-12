"""Contracts for RSVP, changed-plan review and reminder reconfirmation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_joined_players_are_not_immediately_asked_to_rsvp_again():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    confirmation = section('function sessionConfirmationHtml', 'function sessionVisitFactsHtml')
    assert '${sessionConfirmationHtml(game)}' in detail
    assert '!game.attendance_confirmation_due' in confirmation
    assert 'Still coming?' in confirmation
    assert 'Yes, I’m coming' in confirmation
    assert 'Can’t make it' in confirmation
    assert "I'm coming — count me in" not in detail


def test_hosts_get_recruiting_as_the_primary_underfilled_action():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    assert 'const hostNeedsPlayers = canFillRoster && game.is_creator;' in detail
    assert 'if (hostNeedsPlayers) actions = fillRosterAction(true);' in detail
    assert 'if (canFillRoster && !hostNeedsPlayers)' in detail
    assert 'rosterBoostLauncherHtml(game, { primary })' in detail
    assert 'id="gs-fill-roster"' in APP
    assert 'data-roster-boost-channel="friends"' in APP
    assert 'data-roster-boost-channel="court"' in APP
    assert 'data-roster-boost-channel="share"' in APP
    assert 'aria-label="Players"' in detail
    assert 'const openSpots = Math.max(0, Number(game.spots_left) || 0);' in detail
    assert "sessionRsvpStatus(p) !== 'confirmed'" in detail
    assert 'Confirmed ${confirmed}' not in detail
    assert 'Unconfirmed ${unconfirmed}' not in detail


def test_reminder_decline_uses_the_same_guarded_leave_flow():
    screen = section('async function openGameScreen', 'function safeNotificationOverlayRoute')
    assert "querySelectorAll('#gs-leave, #gs-not-coming, #gs-leave-series, #gs-undo-join')" in screen
    assert "title: skipOnly ? 'Skip only this date?' : 'Leave this series?'" in screen
    assert "skipOnly ? `/games/${gameId}/skip-occurrence`" in screen
    assert 'decision = await confirmGameLeave(game, playNoun, btn);' in screen
    assert 'if (!decision.accepted) return;' in screen
