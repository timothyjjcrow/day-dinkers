"""Frontend contracts for consented doubles registration and partner recovery."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    offset = APP.index(start)
    return APP[offset:APP.index(end, offset)]


def test_partner_picker_searches_all_players_with_fit_context_not_friends_only():
    picker = section(
        'function tournamentPartnerPickerHtml',
        'async function openTournamentScreen',
    )
    assert 'Name or exact email' in picker
    assert 'api(`/users/search?q=${encodeURIComponent(query)}`)' in picker
    assert 'playerSkillIdentityHtml(player)' in picker
    assert 'skillLabel(player.skill_level)' not in picker
    assert 'sharedAvailabilityText(state.me?.availability, player.availability)' in picker
    assert 'data-partner-choice' in picker
    assert 'They’ll be asked to accept before your team is complete.' in picker
    assert "api('/friends')" not in picker


def test_registration_has_consent_partner_pool_and_link_invite_paths():
    screen = section('async function openTournamentScreen', '// Live sync while open')
    assert "tournamentPartnerPickerHtml('td-partner'" in screen
    assert 'id="td-need-partner"' in screen
    assert 'I need a partner' in screen
    assert 'data-share-player-invite' in screen
    assert 'Invite a friend to Third Shot' in screen
    assert "JSON.stringify({ needs_partner: true })" in screen
    assert 'data-partner-offer' in screen
    assert '/partner-offer`' in screen
    assert 'Offer to partner' in screen
    assert 'Needs a partner' in screen
    assert 'Approval pending' in screen
    assert 'Team ready' in screen


def test_partner_decision_is_actionable_in_detail_and_activity():
    screen = section('async function openTournamentScreen', '// Live sync while open')
    assert 't.my_partner_action' in screen
    assert 'id="td-partner-accept"' in screen
    assert 'id="td-partner-decline"' in screen
    assert 'You are not entered until you accept.' in screen
    assert "api(`/tournaments/${t.id}/partner/respond`" in screen
    assert "JSON.stringify({ accept: decision === 'accept' })" in screen

    activity = section('async function openActivity', '// ---------- Presence banner ----------')
    assert "action('tournament-accept', 'Accept', true)" in activity
    assert "action('tournament-decline', 'Decline')" in activity
    assert '/partner/respond`' in activity
    assert "JSON.stringify({ accept: true })" in activity
    assert "JSON.stringify({ accept: false })" in activity
    assert 'Nobody is added to a doubles team without accepting.' in activity


def test_incomplete_teams_cannot_look_startable():
    action_helper = section('function competitionActionNeeded', 'function competitionActionNeededHtml')
    assert 'parent.my_partner_action?.decision_for_me' in action_helper
    assert 'Number(parent.ready_entry_count) === Number(parent.entry_count)' in action_helper
    screen = section('async function openTournamentScreen', '// Live sync while open')
    assert 'Number(t.ready_entry_count) === Number(t.entry_count)' in screen
    assert 'partners still pending' in screen
    assert 'need 2 complete teams' in screen


def test_partner_consent_surfaces_have_mobile_safe_layout():
    for selector in (
        '.tournament-partner-picker',
        '.tournament-partner-result',
        '.tournament-partner-selection',
        '.competition-partner-action',
        '.competition-partner-action-buttons',
        '.competition-partner-entry',
    ):
        assert selector in STYLES
    assert 'min-height: 52px' in STYLES
    assert 'grid-template-columns: 1fr 1fr' in STYLES
