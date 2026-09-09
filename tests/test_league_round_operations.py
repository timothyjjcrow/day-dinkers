"""Round standings, reviewed progression and participation use real API state."""
import json
from datetime import timedelta

from test_competition_detail_completion import app, client, register, auth, create_league
from backend.app import db
from backend.models import League, LeagueMatch, Notification, User, utcnow
from backend.routes.leagues import _finalize_match_score, advance_due_league_rounds, maintain_league_results


def field(client, app, count=6, total_rounds=3):
    people = [register(client, f'round-player-{i}') for i in range(count)]
    created = create_league(client, app, people[0])
    url = f"/api/leagues/{created['id']}"
    assert client.patch(url, headers=auth(people[0]), json={'total_rounds': total_rounds}).status_code == 200
    for person in people[1:]:
        assert client.post(url+'/join', headers=auth(person), json={}).status_code == 200
    assert client.post(url+'/start', headers=auth(people[0]), json={}).status_code == 200
    return people, db.session.get(League, created['id']), url


def decisive_round(league):
    for match in league.matches:
        if match.round == league.current_round:
            _finalize_match_score(match, 11, 4)
            match.result_state = 'confirmed'
    db.session.commit()


def preview(client, people, url, finish=False):
    result = client.get(url+'/round/preview'+('?finish=1' if finish else ''), headers=auth(people[0]))
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def close(client, people, url, plan, finish=False):
    return client.post(url+'/round/close', headers=auth(people[0]), json={'preview_fingerprint': plan['preview_fingerprint'], 'finish': finish})


def availability(client, person, url, action, *, apply=True):
    plan = client.post(url+'/availability', headers=auth(person), json={'action': action, 'preview': True})
    assert plan.status_code == 200, plan.get_json()
    if not apply:
        return plan.get_json()
    result = client.post(url+'/availability', headers=auth(person), json={'action': action, 'preview_fingerprint': plan.get_json()['preview_fingerprint']})
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def test_movement_uses_round_results_not_season_totals_and_freezes_round(client, app):
    people, league, url = field(client, app)
    decisive_round(league)
    upper = sorted([m for m in league.members if m.box == 1], key=lambda m: m.user_id)
    lower = sorted([m for m in league.members if m.box == 2], key=lambda m: m.user_id)
    upper[-1].points += 100  # Older season history must not win this round.
    lower[-1].points += 100
    db.session.commit()
    plan = preview(client, people, url)
    assert {move['user_id'] for move in plan['movements']} == {upper[-1].user_id, lower[0].user_id}
    assert plan['round_standings'][0]['players'][-1]['points'] == 2
    result = close(client, people, url, plan)
    assert result.status_code == 200, result.get_json()
    data = result.get_json()
    assert data['current_round'] == 2
    assert data['round_standings'][0]['players'][0]['points'] == 0
    assert data['round_history'][0]['round_standings'] == plan['round_standings']
    assert league.member_for(upper[-1].user_id).points == 102
    assert league.member_for(upper[-1].user_id).box == 2
    assert close(client, people, url, plan).status_code == 409


def test_ties_share_places_and_no_played_loss_is_invented(client, app):
    people, league, url = field(client, app)
    plan = preview(client, people, url)
    assert plan['movements'] == [] and plan['movement_notes']
    assert all(row['place'] == 1 and row['tied'] for table in plan['round_standings'] for row in table['players'])
    assert close(client, people, url, plan).status_code == 200
    assert all(m.wins == m.losses == m.points == 0 for m in league.members)
    assert all(m.effective_result_state() == 'void' and m.resolution_kind == 'round_closed_unplayed' for m in league.matches if m.round == 1)


def test_score_change_invalidates_preview_and_unresolved_score_blocks_close(client, app):
    people, league, url = field(client, app)
    plan = preview(client, people, url)
    match = league.matches[0]
    match.result_state = 'awaiting_confirmation'
    match.score1, match.score2 = 11, 5
    match.result_version = 1
    db.session.commit()
    assert close(client, people, url, plan).status_code == 409
    latest = preview(client, people, url)
    assert latest['unresolved_count'] == 1
    assert close(client, people, url, latest).get_json()['error'] == 'unresolved_results'
    assert league.current_round == 1 and not json.loads(league.round_history)


def test_last_round_finishes_without_extra_matches_and_legacy_total_is_unknown(client, app):
    people, league, url = field(client, app, total_rounds=1)
    decisive_round(league)
    plan = preview(client, people, url)
    assert plan['ends_season'] and plan['next_deadline_at'] is None
    before = LeagueMatch.query.count()
    assert close(client, people, url, plan).status_code == 200
    assert league.status == 'completed' and LeagueMatch.query.count() == before
    assert league.champion_user_id == plan['champion_user_id']
    league.total_rounds = None
    db.session.commit()
    detail = client.get(url, headers=auth(people[0])).get_json()
    assert detail['total_rounds'] is None and detail['season_end_estimate_at'] is None


def test_absence_previews_and_cancels_appointments_without_rebooking_on_return(client, app):
    people, league, url = field(client, app, count=3)
    match = league.matches[0]
    person = next(p for p in people if p['user']['id'] == match.player1_id)
    match.scheduled_at = utcnow()+timedelta(days=1)
    match.scheduled_court_id = league.court_id
    match.schedule_proposals = json.dumps([{'id': 'alternative', 'starts_at': '2099-01-01T12:00:00Z'}])
    db.session.commit()
    before = preview(client, people, url)
    plan = availability(client, person, url, 'unavailable', apply=False)
    assert any(m['scheduled_at'] and m['opponent'] for m in plan['affected_matches'])
    assert match.scheduled_at is not None  # preview is read-only
    data = availability(client, person, url, 'unavailable')
    assert data['my_unavailable_round'] == 1
    assert match.scheduled_at is None and match.effective_result_state() == 'void'
    event = json.loads(league.member_for(person['user']['id']).availability_history)[0]
    assert event['matches'][0]['scheduled_at'] and event['matches'][0]['proposals'][0]['id'] == 'alternative'
    assert close(client, people, url, before).status_code == 409
    assert Notification.query.filter_by(user_id=match.player2_id, kind='league_match').count() >= 1
    availability(client, person, url, 'available')
    assert match.effective_result_state() == 'unreported'
    assert match.scheduled_at is None and match.schedule_proposals == '[]'
    assert all(m.points == m.losses == 0 for m in league.members)


def test_other_players_absence_prevents_premature_reopening(client, app):
    people, league, url = field(client, app, count=3)
    match = league.matches[0]
    one = next(p for p in people if p['user']['id'] == match.player1_id)
    two = next(p for p in people if p['user']['id'] == match.player2_id)
    availability(client, one, url, 'unavailable')
    availability(client, two, url, 'unavailable')
    availability(client, one, url, 'available')
    assert match.effective_result_state() == 'void'
    availability(client, two, url, 'available')
    assert match.effective_result_state() == 'unreported'


def test_withdrawal_keeps_current_round_and_history_but_not_future_assignments(client, app):
    people, league, url = field(client, app)
    leaving = people[-1]
    user_id = leaving['user']['id']
    old_ids = {m.id for m in league.matches if user_id in (m.player1_id, m.player2_id)}
    availability(client, leaving, url, 'withdraw')
    assert old_ids and all(m.effective_result_state() == 'unreported' for m in league.matches if m.id in old_ids)
    plan = preview(client, people, url)
    assert plan['withdrawals'][0]['user_id'] == user_id
    assert close(client, people, url, plan).status_code == 200
    assert league.member_for(user_id).withdrawn_at
    assert old_ids.issubset({m.id for m in league.matches})
    assert not any(user_id in (m.player1_id, m.player2_id) for m in league.matches if m.round == 2)
    assert client.post(url+'/availability', headers=auth(leaving), json={'action': 'stay', 'preview': True}).status_code == 403


def test_deadline_requests_review_instead_of_silently_moving_players(client, app):
    people, league, url = field(client, app)
    league.round_started_at = utcnow()-timedelta(days=8)
    db.session.commit()
    advance_due_league_rounds()
    advance_due_league_rounds()
    assert league.current_round == 1
    assert Notification.query.filter_by(user_id=league.organizer_id, unread_dedupe_key=f'league-close-preview:{league.id}:1').count() == 1


def test_withdrawals_do_not_strand_a_player_in_a_division_without_opponents(client, app):
    people, league, url = field(client, app)
    availability(client, people[1], url, 'withdraw')
    availability(client, people[2], url, 'withdraw')
    plan = preview(client, people, url)
    assert any('everyone has an opponent' in note for note in plan['movement_notes'])
    assert any(move.get('reason') == 'division_combined' for move in plan['movements'])
    assert close(client, people, url, plan).status_code == 200
    assert sum(m.round == 2 for m in league.matches) == 6  # Four remaining players.


def test_availability_and_close_require_current_review_and_permissions(client, app):
    people, league, url = field(client, app)
    assert client.get(url+'/round/preview', headers=auth(people[1])).status_code == 403
    assert client.post(url+'/round/close', headers=auth(people[0]), json={}).status_code == 409
    assert client.post(url+'/availability', headers=auth(people[1]), json={'action': 'unavailable'}).status_code == 409
    assert league.member_for(people[1]['user']['id']).unavailable_round is None


def test_absence_can_be_challenged_with_a_played_result_without_automatic_points(client, app):
    people, league, url = field(client, app, count=3)
    match = league.matches[0]
    one = next(p for p in people if p['user']['id'] == match.player1_id)
    two = next(p for p in people if p['user']['id'] == match.player2_id)
    availability(client, one, url, 'unavailable')
    detail = client.get(url, headers=auth(two)).get_json()
    item = next(m for m in detail['matches'] if m['id'] == match.id)
    assert item['can_report_played_after_absence'] and item['can_report_result']
    submitted = client.post(url+f'/matches/{match.id}/score', headers=auth(two), json={'score1':11,'score2':5,'result_version':item['result_version']})
    assert submitted.status_code == 200, submitted.get_json()
    assert submitted.get_json()['requires_explicit_confirmation']
    assert preview(client, people, url)['unresolved_count'] == 1
    match.reported_at = utcnow()-timedelta(days=2)
    db.session.commit()
    maintain_league_results()
    assert match.effective_result_state() == 'awaiting_confirmation'
    assert all(member.points == 0 for member in league.members)
    confirmed = client.post(url+f'/matches/{match.id}/confirm', headers=auth(one), json={'result_version':match.result_version})
    assert confirmed.status_code == 200, confirmed.get_json()
    assert match.effective_result_state() == 'confirmed'
    assert sum(m.points for m in league.members) == 4


def test_absence_objection_can_be_disputed_and_resolved_by_organizer(client, app):
    people, league, url = field(client, app, count=3)
    match = next(m for m in league.matches if people[0]['user']['id'] not in (m.player1_id,m.player2_id))
    one, two = people[1], people[2]
    availability(client, one, url, 'unavailable')
    client.post(url+f'/matches/{match.id}/score', headers=auth(two), json={'score1':11,'score2':4,'result_version':match.result_version})
    challenged = client.post(url+f'/matches/{match.id}/dispute', headers=auth(one), json={'reason':'We did not play','result_version':match.result_version})
    assert challenged.status_code == 200
    assert match.resolution_kind == 'absence_result_claim'
    resolved = client.post(url+f'/matches/{match.id}/resolve', headers=auth(people[0]), json={'void':True,'reason':'Both players confirmed the match was cancelled','result_version':match.result_version})
    assert resolved.status_code == 200
    assert match.effective_result_state() == 'void' and all(m.points == 0 for m in league.members)


def test_withdrawn_player_has_no_active_league_banner_or_calendar_deadline(client, app):
    from backend.routes.auth import _active_league_payload
    people, league, url = field(client, app)
    person = people[-1]
    availability(client, person, url, 'withdraw')
    close(client, people, url, preview(client, people, url))
    user = db.session.get(User, person['user']['id'])
    assert _active_league_payload(user) is None
    token = client.get('/api/calendar/token', headers=auth(person)).get_json()['token']
    assert f'league-{league.id}-round-' not in client.get(f'/api/calendar/{token}.ics').get_data(as_text=True)


def test_legacy_progression_routes_cannot_bypass_the_reviewed_close(client, app):
    people, league, url = field(client, app)
    for path in ('advance','complete'):
        assert client.post(url+'/'+path, headers=auth(people[0])).status_code == 409
        assert league.current_round == 1 and league.status == 'active'
    plan = preview(client, people, url, finish=True)
    result = client.post(url+'/complete', headers=auth(people[0]), json={'preview_fingerprint':plan['preview_fingerprint']})
    assert result.status_code == 200 and league.status == 'completed'


def test_reviewed_extension_keeps_appointments_and_changes_calendar_deadline(client, app):
    people, league, url = field(client, app)
    match = league.matches[0]
    original_slot = utcnow()+timedelta(days=1)
    match.scheduled_at = original_slot
    match.scheduled_court_id = league.court_id
    db.session.commit()
    payload = {'deadline_at': (league.round_started_at+timedelta(days=10)).isoformat()+'Z','reason':'Rain this week'}
    assert client.post(url+'/round/extend',headers=auth(people[1]),json={**payload,'preview':True}).status_code == 403
    plan = client.post(url+'/round/extend',headers=auth(people[0]),json={**payload,'preview':True}).get_json()
    assert plan['retained_appointments'] == 1 and plan['notification_count'] == 5
    assert league.round_deadline_override_at is None
    applied = client.post(url+'/round/extend',headers=auth(people[0]),json={**payload,'preview_fingerprint':plan['preview_fingerprint']})
    assert applied.status_code == 200, applied.get_json()
    assert match.scheduled_at == original_slot and league.round_version == 1
    assert applied.get_json()['round_deadline_at'] == payload['deadline_at']
    assert json.loads(league.round_history)[0]['reason'] == 'Rain this week'
    assert client.post(url+'/round/extend',headers=auth(people[0]),json={**payload,'preview_fingerprint':plan['preview_fingerprint']}).status_code in (400,409)
    token = client.get('/api/calendar/token',headers=auth(people[0])).get_json()['token']
    calendar = client.get(f'/api/calendar/{token}.ics').get_data(as_text=True)
    assert league.round_deadline_override_at.strftime('%Y%m%dT%H%M%SZ') in calendar
    assert 'SEQUENCE:1' in calendar
    # Closing resets the explicit extension for the next round.
    assert close(client, people, url, preview(client, people, url)).status_code == 200
    assert league.round_deadline_override_at is None
