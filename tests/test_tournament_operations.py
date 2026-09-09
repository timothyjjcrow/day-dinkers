"""Real API tests for tournament readiness, planning and court operations."""
import json
from datetime import timedelta

from test_competition_detail_completion import app, client, register, auth, create_tournament
from backend.app import db
from backend.models import Tournament, TournamentEntry, TournamentMatch, Notification, utcnow


def field(client, app, count=4, **settings):
    organizer = register(client, 'operations-host')
    players = [register(client, f'operations-player-{i}') for i in range(count)]
    tournament = create_tournament(client, app, organizer)
    url = f"/api/tournaments/{tournament['id']}"
    if settings:
        updated = client.patch(url, headers=auth(organizer), json=settings)
        assert updated.status_code == 200, updated.get_json()
    for player in players:
        assert client.post(url + '/register', headers=auth(player), json={}).status_code == 201
    return organizer, players, url


def start(client, organizer, url):
    preview = client.get(url + '/preview', headers=auth(organizer))
    assert preview.status_code == 200, preview.get_json()
    result = client.post(url + '/start', headers=auth(organizer), json={'preview_fingerprint': preview.get_json()['preview_fingerprint']})
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def test_preview_is_read_only_and_matches_real_bracket_with_bye(client, app):
    organizer, players, url = field(client, app, count=3)
    before_notifications = Notification.query.count()
    preview = client.get(url + '/preview', headers=auth(organizer)).get_json()
    assert TournamentMatch.query.count() == 0
    assert Notification.query.count() == before_notifications
    assert all(entry.seed is None for entry in TournamentEntry.query.all())
    assert len([match for match in preview['matches'] if match.get('result_state') == 'bye']) == 1
    assert preview['can_start']
    assert client.get(url + '/preview', headers=auth(players[0])).status_code == 403
    live = start(client, organizer, url)
    fields = ['round', 'position', 'entry1_id', 'entry2_id', 'scheduled_at', 'court_number']
    assert [[match.get(key) for key in fields] for match in live['matches']] == [[match.get(key) for key in fields] for match in preview['matches']]


def test_start_rejects_a_stale_preview_without_sending_start_notifications(client, app):
    organizer, players, url = field(client, app, count=2)
    preview = client.get(url + '/preview', headers=auth(organizer)).get_json()
    extra = register(client, 'operations-extra')
    client.post(url + '/register', headers=auth(extra), json={})
    response = client.post(url + '/start', headers=auth(organizer), json={'preview_fingerprint': preview['preview_fingerprint']})
    assert response.status_code == 409
    assert response.get_json()['error'] == 'preview_changed'
    assert TournamentMatch.query.count() == 0
    assert Notification.query.filter_by(kind='tournament_start').count() == 0


def test_doubles_arrival_is_individual_and_legacy_team_report_is_not_arrival(client, app):
    organizer = register(client, 'arrival-organizer')
    one, two = register(client, 'arrival-one'), register(client, 'arrival-two')
    tournament = create_tournament(client, app, organizer)
    url = f"/api/tournaments/{tournament['id']}"
    client.patch(url, headers=auth(organizer), json={'event_type': 'doubles', 'starts_at': (utcnow()+timedelta(hours=1)).isoformat()+'Z'})
    client.post(url+'/register', headers=auth(one), json={'partner_id': two['user']['id']})
    client.post(url+'/partner/respond', headers=auth(two), json={'accept': True})
    entry = TournamentEntry.query.one()
    entry.checked_in_at = utcnow()
    db.session.commit()
    legacy = client.get(url, headers=auth(one)).get_json()['entries'][0]
    assert legacy['legacy_team_checkin'] and not legacy['checked_in'] and legacy['arrived_count'] == 0
    forbidden = client.post(url+'/checkin', headers=auth(one), json={'user_id':two['user']['id']})
    assert forbidden.status_code == 403
    first = client.post(url+'/checkin', headers=auth(one), json={}).get_json()['entries'][0]
    assert first['arrived_count'] == 1 and first['arrival_expected_count'] == 2
    assert first['my_arrived'] and not first['checked_in']
    second = client.post(url+'/checkin', headers=auth(two), json={}).get_json()['entries'][0]
    assert second['arrived_count'] == 2 and second['checked_in']
    undone = client.post(url+'/checkin', headers=auth(two), json={'arrived':False}).get_json()['entries'][0]
    assert undone['arrived_count'] == 1 and not undone['checked_in']
    assert len(json.loads(db.session.get(TournamentEntry,entry.id).arrival_history)) == 3


def test_arrival_cannot_mean_here_tomorrow(client, app):
    organizer, players, url = field(client, app, count=2)
    assert client.post(url+'/checkin', headers=auth(players[0]), json={}).status_code == 409


def test_active_banner_reports_only_viewers_arrival_and_real_window(client, app):
    from backend.routes.auth import _active_tournament_payload
    from backend.models import User
    organizer = register(client, 'banner-organizer')
    one, two = register(client, 'banner-one'), register(client, 'banner-two')
    tournament = create_tournament(client, app, organizer)
    url = f"/api/tournaments/{tournament['id']}"
    client.patch(url, headers=auth(organizer), json={'event_type': 'doubles', 'starts_at': (utcnow()+timedelta(hours=3)).isoformat()+'Z'})
    client.post(url+'/register', headers=auth(one), json={'partner_id': two['user']['id']})
    client.post(url+'/partner/respond', headers=auth(two), json={'accept': True})
    entry = TournamentEntry.query.one()
    entry.checked_in_at = utcnow()  # A legacy team report is not either player's arrival.
    db.session.commit()
    first = db.session.get(User, one['user']['id'])
    second = db.session.get(User, two['user']['id'])
    assert not _active_tournament_payload(first)['arrival_open']
    assert not _active_tournament_payload(first)['my_checked_in']
    entry.tournament.starts_at = utcnow()+timedelta(hours=1)
    db.session.commit()
    assert client.post(url+'/checkin', headers=auth(one), json={}).status_code == 200
    assert _active_tournament_payload(first)['arrival_open']
    assert _active_tournament_payload(first)['my_checked_in']
    assert not _active_tournament_payload(second)['my_checked_in']


def test_schedule_blocks_court_overlap_and_feeder_order_without_partial_save(client, app):
    organizer, players, url = field(client, app)
    live = start(client, organizer, url)
    first, second = live['matches'][:2]
    response = client.patch(url+f"/matches/{second['id']}/schedule", headers=auth(organizer), json={'scheduled_at':first['scheduled_at'], 'court_number':first['court_number'], 'expected_schedule_version':0})
    assert response.status_code == 409
    assert 'court_overlap' in {item['kind'] for item in response.get_json()['conflicts']}
    assert db.session.get(TournamentMatch,second['id']).scheduled_at.isoformat() in second['scheduled_at']
    final = next(match for match in live['matches'] if match['round'] == 2 and match['position'] == 0)
    response = client.patch(url+f"/matches/{final['id']}/schedule", headers=auth(organizer), json={'scheduled_at':first['scheduled_at'], 'court_number':first['court_number']})
    assert response.status_code == 409
    assert 'feeder_order' in {item['kind'] for item in response.get_json()['conflicts']}
    assert db.session.get(Tournament,live['id']).schedule_version == 0


def test_round_robin_rejects_player_overlap_even_on_different_courts(client, app):
    organizer, players, url = field(client, app, format='round_robin', court_count=2)
    live = start(client, organizer, url)
    first = live['matches'][0]
    later = next(match for match in live['matches'] if match['round'] > 1 and set([match['entry1_id'],match['entry2_id']]) & set([first['entry1_id'],first['entry2_id']]))
    result = client.patch(url+f"/matches/{later['id']}/schedule", headers=auth(organizer), json={'scheduled_at':first['scheduled_at'], 'court_number':2 if first['court_number'] == 1 else 1})
    assert result.status_code == 409
    assert 'player_overlap' in {item['kind'] for item in result.get_json()['conflicts']}


def test_call_and_start_enforce_court_occupancy_and_require_decided_players(client, app):
    organizer, players, url = field(client, app)
    live = start(client, organizer, url)
    first, second = live['matches'][:2]
    final = next(match for match in live['matches'] if match['round'] == 2 and match['position'] == 0)
    def action(match, state, actor=organizer):
        return client.post(url+f"/matches/{match['id']}/play-state", headers=auth(actor), json={'play_state':state})
    assert action(first,'called',players[0]).status_code == 403
    assert action(final,'called').status_code == 409
    assert action(first,'playing').status_code == 409
    called = action(first,'called')
    assert called.status_code == 200, called.get_json()
    assert action(second,'called').get_json()['error'] == 'court_or_player_busy'
    playing = action(first,'playing')
    assert playing.status_code == 200
    result = next(match for match in playing.get_json()['matches'] if match['id'] == first['id'])
    assert result['play_state'] == 'playing' and result['started_at'] and result['called_at']
    assert action(first,'estimated').status_code == 409
    assert client.patch(url+f"/matches/{first['id']}/schedule", headers=auth(organizer), json={'court_number':1}).status_code == 409


def test_delay_preview_has_no_effect_apply_is_versioned_and_notifies_once(client, app):
    organizer, players, url = field(client, app)
    live = start(client, organizer, url)
    before = [match.scheduled_at for match in TournamentMatch.query.order_by(TournamentMatch.id)]
    payload = {'minutes':15,'expected_schedule_version':0}
    notification_count = Notification.query.count()
    preview = client.post(url+'/schedule/delay', headers=auth(organizer), json={**payload,'preview':True})
    assert preview.status_code == 200, preview.get_json()
    assert preview.get_json()['notification_count'] == 4
    assert [match.scheduled_at for match in TournamentMatch.query.order_by(TournamentMatch.id)] == before
    assert Notification.query.count() == notification_count
    applied = client.post(url+'/schedule/delay', headers=auth(organizer), json=payload)
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()['schedule_version'] == 1
    assert [match.scheduled_at for match in TournamentMatch.query.order_by(TournamentMatch.id)] == [when+timedelta(minutes=15) for when in before]
    assert Notification.query.count() == notification_count + 4
    assert client.post(url+'/schedule/delay', headers=auth(organizer), json=payload).status_code == 409
    assert Notification.query.count() == notification_count + 4


def test_entry_terms_are_explicit_and_cannot_change_after_signup(client, app):
    organizer, players, url = field(client, app, count=2, entry_fee_cents=1200, payment_method='Pay at check-in', withdrawal_policy='Refund until 24 hours before start', rest_minutes=10)
    result = client.get(url, headers=auth(players[0])).get_json()
    assert result['entry_fee_cents'] == 1200 and result['rest_minutes'] == 10
    assert result['payment_method'] == 'Pay at check-in'
    assert client.patch(url, headers=auth(organizer), json={'entry_fee_cents':2500}).status_code == 409
