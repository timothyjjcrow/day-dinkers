"""Time discovery is applied before pagination and never hides personal plans."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Friendship, Game, GamePlayer, User, utcnow


@pytest.fixture()
def setup():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        client = app.test_client()
        account = client.post('/api/auth/register', json={
            'email': 'discovery@example.com', 'password': 'secret123',
            'display_name': 'Discovery',
        }).get_json()
        user = db.session.get(User, account['user']['id'])
        court = Court(name='Discovery Court', latitude=45.52, longitude=-122.68,
                      city='Portland', state='OR')
        db.session.add(court)
        db.session.flush()
        yield app, client, {'Authorization': f"Bearer {account['token']}"}, user, court
        db.session.remove()
        db.drop_all()


def add_game(user, court, starts, *, duration=90, visibility='open', status='upcoming'):
    game = Game(court_id=court.id, creator_id=user.id, scheduled_at=starts,
                duration_minutes=duration, game_type='casual', max_players=4,
                visibility=visibility, status=status)
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=user.id, team=1,
                              attending_at=utcnow()))
    db.session.commit()
    return game


def bounds(now, minutes=60):
    return {'lat': 45.52, 'lng': -122.68, 'ends_after': now.isoformat()+'Z',
            'starts_before': (now+timedelta(minutes=minutes)).isoformat()+'Z'}


def test_window_filters_before_pagination_and_includes_ongoing_games(setup):
    _, client, auth, user, court = setup
    now = utcnow()
    add_game(user, court, now-timedelta(minutes=100), duration=30)
    ongoing = add_game(user, court, now-timedelta(minutes=80), duration=120)
    soon = add_game(user, court, now+timedelta(minutes=30))
    add_game(user, court, now+timedelta(minutes=60))  # exclusive upper bound
    query = dict(bounds(now), limit=1)
    first = client.get('/api/games', query_string=query, headers=auth).get_json()
    assert [g['id'] for g in first['items']] == [ongoing.id]
    assert first['has_more'] is True
    second = client.get('/api/games', query_string=dict(query, cursor=first['next_cursor']), headers=auth).get_json()
    assert [g['id'] for g in second['items']] == [soon.id]
    assert second['has_more'] is False


def test_play_home_preserves_personal_plans_outside_discovery_window(setup):
    _, client, auth, user, court = setup
    now = utcnow()
    future = add_game(user, court, now+timedelta(days=5))
    payload = client.get('/api/play/home', query_string=bounds(now), headers=auth).get_json()
    assert payload['nearby']['items'] == []
    assert future.id in [g['id'] for g in payload['mine']['items']]


def test_completed_history_is_available_for_repeat_planning(setup):
    _, client, auth, user, court = setup
    game = add_game(user, court, utcnow()-timedelta(days=1), status='completed')
    teammate = User(email='teammate@example.com', display_name='Teammate', password_hash='unused-test-account')
    db.session.add(teammate)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=teammate.id, team=2))
    game.completed_at = utcnow()-timedelta(hours=22)
    db.session.commit()
    payload = client.get('/api/play/home', headers=auth).get_json()
    assert payload['recent']['items'][0]['id'] == game.id
    assert payload['recent']['items'][0]['is_joined'] is True


@pytest.mark.parametrize('query', [
    {'ends_after': 'not-a-time'}, {'starts_before': '2026-09-04T12:00:00'},
    {'ends_after': '2026-09-04T12:00:00Z', 'starts_before': '2026-09-04T11:00:00Z'},
])
def test_invalid_windows_rejected_consistently(setup, query):
    _, client, auth, _, _ = setup
    for route in ['/api/games', '/api/play/home']:
        response = client.get(route, query_string=query, headers=auth)
        assert response.status_code == 400
        assert response.get_json()['error'] == 'invalid_discovery_window'


def test_equivalent_offsets_produce_identical_results(setup):
    _, client, auth, user, court = setup
    now = utcnow()
    add_game(user, court, now+timedelta(minutes=20))
    utc_query = bounds(now)
    offset_query = dict(utc_query, ends_after=(now-timedelta(hours=7)).isoformat()+'-07:00')
    a = client.get('/api/games', query_string=utc_query, headers=auth).get_json()
    b = client.get('/api/games', query_string=offset_query, headers=auth).get_json()
    assert [g['id'] for g in a['items']] == [g['id'] for g in b['items']]


def test_time_window_does_not_expose_private_games_to_other_players(setup):
    _, client, auth, user, court = setup
    now = utcnow()
    hidden = add_game(user, court, now+timedelta(minutes=20), visibility='private')
    other = client.post('/api/auth/register', json={
        'email':'other@example.com','password':'secret123','display_name':'Other',
    }).get_json()
    response = client.get('/api/games', query_string=bounds(now),
        headers={'Authorization': f"Bearer {other['token']}"}).get_json()
    assert response['items'] == []
    own = client.get('/api/games', query_string=bounds(now), headers=auth).get_json()
    assert [g['id'] for g in own['items']] == [hidden.id]


def test_friend_discovery_respects_radius_before_pagination_and_preserves_plans(setup):
    _, client, auth, user, court = setup
    friend = User(email='friend@example.com', display_name='Friend', password_hash='unused-test-account')
    far = Court(name='Far court', latitude=45.90, longitude=-122.68, city='Far', state='OR')
    db.session.add_all([friend, far])
    db.session.flush()
    db.session.add(Friendship(requester_id=user.id, addressee_id=friend.id, status='accepted'))
    db.session.commit()
    now = utcnow()
    distant = add_game(friend, far, now+timedelta(minutes=10))
    near = add_game(friend, court, now+timedelta(minutes=20))
    own = add_game(user, far, now+timedelta(minutes=30))
    query = dict(bounds(now), radius=10)
    page = client.get('/api/games', query_string=dict(query, friends=1, limit=1), headers=auth).get_json()
    assert [g['id'] for g in page['items']] == [near.id]
    assert page['has_more'] is False
    home = client.get('/api/play/home', query_string=query, headers=auth).get_json()
    assert [g['id'] for g in home['friends']['items']] == [near.id]
    assert own.id in [g['id'] for g in home['mine']['items']]
    wider = client.get('/api/play/home', query_string=dict(query, radius=50), headers=auth).get_json()
    assert {g['id'] for g in wider['friends']['items']} == {near.id, distant.id}
    # Without an area, the legacy friends view still works.
    anywhere = client.get('/api/games?friends=1', headers=auth).get_json()
    assert {g['id'] for g in anywhere['items']} == {near.id, distant.id}
