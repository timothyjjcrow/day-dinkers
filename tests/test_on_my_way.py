"""“On my way” for scheduled games: one tap, players only, near the start."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GameArrivalIntent, Notification, utcnow
from tests.schedule_test_support import post_with_schedule_review
from tests.session_plan_helpers import post_reviewed_game


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add_all([
            Court(
                name='Larson Park', city='Costa Mesa', state='CA',
                county_slug='orange-county', latitude=33.66,
                longitude=-117.91, num_courts=6,
            ),
            Court(
                name='Adorni Center', city='Eureka', state='CA',
                county_slug='humboldt-county', latitude=40.81,
                longitude=-124.16, num_courts=4,
            ),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _register(client, slug, name):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com', 'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def _court_id(name):
    return Court.query.filter_by(name=name).one().id


def _game(client, host, *players, starts_in=timedelta(minutes=30),
          visibility='open'):
    """A scheduled game created a day out, then moved to ``starts_in``."""
    created = post_with_schedule_review(client, '/api/games', json={
        'court_id': _court_id('Larson Park'),
        'scheduled_at': (utcnow() + timedelta(hours=24)).isoformat() + 'Z',
        'game_type': 'casual', 'max_players': 4, 'visibility': visibility,
    }, headers=_headers(host))
    assert created.status_code == 201, created.get_json()
    game_id = created.get_json()['id']
    for player in players:
        joined = post_reviewed_game(
            client, f'/api/games/{game_id}/join', headers=_headers(player),
        )
        assert joined.status_code == 200, joined.get_json()
    _move(game_id, starts_in)
    return game_id


def _move(game_id, starts_in):
    row = db.session.get(Game, game_id)
    row.scheduled_at = utcnow() + starts_in
    db.session.commit()


def _on_my_way(client, person, game_id, attempt='omw-1'):
    return client.put(f'/api/games/{game_id}/arrival', json={
        'eta_minutes': 10, 'client_attempt_id': attempt,
    }, headers=_headers(person))


def _detail(client, person, game_id):
    response = client.get(f'/api/games/{game_id}', headers=_headers(person))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _notices(kind, user=None):
    query = Notification.query.filter_by(kind=kind)
    if user is not None:
        query = query.filter_by(user_id=user['user']['id'])
    return query.all()


def test_player_shares_one_tap_eta_with_the_other_players(client):
    host = _register(client, 'omw-host', 'Hana')
    tim = _register(client, 'omw-tim', 'Tim')
    cy = _register(client, 'omw-cy', 'Cy')
    game_id = _game(client, host, tim, cy)

    shared = _on_my_way(client, tim, game_id)
    assert shared.status_code == 201, shared.get_json()
    body = shared.get_json()
    assert body['arrival']['active'] is True
    assert body['arrival']['eta_minutes'] == 10
    assert body['game']['my_arrival']['active'] is True
    assert [row['user_id'] for row in body['game']['arrivals']] == [tim['user']['id']]

    for person in (host, cy):
        [notice] = _notices('rally_arrival', person)
        assert notice.title == 'Tim is 10 min away'
        assert notice.body == 'Heading to Larson Park.'
        assert notice.related_game_id == game_id
        assert notice.action_url == f'/#game/{game_id}'
    assert _notices('rally_arrival', tim) == []

    seen_by_host = _detail(client, host, game_id)
    assert seen_by_host['my_arrival'] is None
    [row] = seen_by_host['arrivals']
    assert row['user_id'] == tim['user']['id']
    assert row['eta_minutes'] == 10 and row['active'] is True
    assert 'display_name' not in row  # the roster already names everyone

    # Scheduled games never use the live-rally capacity or ETA banner.
    me = client.get('/api/me', headers=_headers(tim)).get_json()
    assert me['active_arrival'] is None
    assert seen_by_host['spots_left'] == 1


def test_eta_is_offered_only_near_the_start_and_only_on_detail(client):
    host = _register(client, 'omw-window-host', 'Hana')
    tim = _register(client, 'omw-window-tim', 'Tim')
    game_id = _game(client, host, tim, starts_in=timedelta(minutes=61))

    early = _on_my_way(client, tim, game_id, 'omw-early')
    assert early.status_code == 409
    assert early.get_json() == {'error': 'arrival_window_closed'}
    detail = _detail(client, tim, game_id)
    assert 'arrivals' not in detail and 'my_arrival' not in detail

    _move(game_id, timedelta(minutes=59))
    detail = _detail(client, tim, game_id)
    assert detail['arrivals'] == [] and detail['my_arrival'] is None
    assert _on_my_way(client, tim, game_id, 'omw-open').status_code == 201

    # Feed rows stay slim: the ETA lives on the game page only.
    mine = client.get('/api/games?mine=1', headers=_headers(tim)).get_json()
    row = next(item for item in mine['items'] if item['id'] == game_id)
    assert 'arrivals' not in row and 'my_arrival' not in row

    client.delete(f'/api/games/{game_id}/arrival', headers=_headers(tim))
    _move(game_id, timedelta(minutes=-31))
    late = _on_my_way(client, tim, game_id, 'omw-late')
    assert late.status_code == 409
    assert late.get_json() == {'error': 'arrival_window_closed'}
    assert 'arrivals' not in _detail(client, tim, game_id)

    _move(game_id, timedelta(minutes=-29))
    assert _on_my_way(client, tim, game_id, 'omw-running-late').status_code == 201


def test_only_joined_players_can_share_or_see_etas(client):
    host = _register(client, 'omw-private-host', 'Hana')
    tim = _register(client, 'omw-private-tim', 'Tim')
    outsider = _register(client, 'omw-private-outsider', 'Olive')
    game_id = _game(client, host, tim)
    assert _on_my_way(client, tim, game_id).status_code == 201

    denied = _on_my_way(client, outsider, game_id, 'omw-outsider')
    assert denied.status_code == 404
    assert denied.get_json() == {'error': 'game_not_found'}
    seen = _detail(client, outsider, game_id)
    assert 'arrivals' not in seen and 'my_arrival' not in seen
    assert GameArrivalIntent.query.filter_by(
        user_id=outsider['user']['id'],
    ).count() == 0


def test_checked_in_players_are_already_there(client):
    host = _register(client, 'omw-here-host', 'Hana')
    tim = _register(client, 'omw-here-tim', 'Tim')
    cy = _register(client, 'omw-here-cy', 'Cy')
    game_id = _game(client, host, tim, cy)

    client.post(
        f"/api/courts/{_court_id('Larson Park')}/checkin", json={},
        headers=_headers(tim),
    )
    refused = _on_my_way(client, tim, game_id)
    assert refused.status_code == 409
    assert refused.get_json() == {'error': 'already_at_court'}

    # Presence at another court is no reason to refuse the ETA.
    client.post(
        f"/api/courts/{_court_id('Adorni Center')}/checkin", json={},
        headers=_headers(cy),
    )
    assert _on_my_way(client, cy, game_id, 'omw-elsewhere').status_code == 201


def test_checking_in_at_the_court_clears_the_eta_quietly(client):
    host = _register(client, 'omw-arrive-host', 'Hana')
    tim = _register(client, 'omw-arrive-tim', 'Tim')
    game_id = _game(client, host, tim)
    assert _on_my_way(client, tim, game_id).status_code == 201

    # Another court keeps it; the game's own court ends it.
    client.post(
        f"/api/courts/{_court_id('Adorni Center')}/checkin", json={},
        headers=_headers(tim),
    )
    assert _detail(client, tim, game_id)['my_arrival']['active'] is True
    checked_in = client.post(
        f"/api/courts/{_court_id('Larson Park')}/checkin",
        json={'looking_for_game': False}, headers=_headers(tim),
    )
    assert checked_in.status_code == 200, checked_in.get_json()
    assert checked_in.get_json()['presence']['checked_in'] is True

    intent = GameArrivalIntent.query.filter_by(user_id=tim['user']['id']).one()
    assert intent.active is False and intent.end_reason == 'arrived'
    detail = _detail(client, host, game_id)
    assert detail['arrivals'] == []
    assert _notices('rally_arrival_ended') == []


def test_undo_and_retap_do_not_push_twice(client):
    host = _register(client, 'omw-undo-host', 'Hana')
    tim = _register(client, 'omw-undo-tim', 'Tim')
    game_id = _game(client, host, tim)
    assert _on_my_way(client, tim, game_id, 'omw-first').status_code == 201

    again = _on_my_way(client, tim, game_id, 'omw-double-tap')
    assert again.status_code == 409
    assert again.get_json()['error'] == 'arrival_already_active'

    undone = client.delete(
        f'/api/games/{game_id}/arrival', headers=_headers(tim),
    )
    assert undone.status_code == 200
    assert undone.get_json()['arrival']['active'] is False
    assert _detail(client, tim, game_id)['my_arrival'] is None

    assert _on_my_way(client, tim, game_id, 'omw-second').status_code == 201
    assert len(_notices('rally_arrival', host)) == 1
    assert _notices('rally_arrival_ended') == []


def test_leaving_cancelling_moving_or_finishing_end_etas_without_pickup_pushes(client):
    host = _register(client, 'omw-end-host', 'Hana')
    tim = _register(client, 'omw-end-tim', 'Tim')
    cy = _register(client, 'omw-end-cy', 'Cy')
    game_id = _game(client, host, tim, cy)
    assert _on_my_way(client, tim, game_id, 'omw-end-tim').status_code == 201
    assert _on_my_way(client, cy, game_id, 'omw-end-cy').status_code == 201

    # Leaving ends only the leaver's ETA.
    left = client.post(
        f'/api/games/{game_id}/leave', json={}, headers=_headers(cy),
    )
    assert left.status_code == 200, left.get_json()
    ended = GameArrivalIntent.query.filter_by(user_id=cy['user']['id']).one()
    assert ended.active is False and ended.end_reason == 'left'
    assert [row['user_id'] for row in _detail(client, host, game_id)['arrivals']] == [
        tim['user']['id'],
    ]

    # Moving the time ends the rest; so does cancelling or finishing.
    moved = client.post(f'/api/games/{game_id}/reschedule', json={
        'scheduled_at': (utcnow() + timedelta(minutes=45)).isoformat() + 'Z',
    }, headers=_headers(host))
    assert moved.status_code == 200, moved.get_json()
    assert GameArrivalIntent.query.filter_by(active=True).count() == 0

    assert _on_my_way(client, tim, game_id, 'omw-end-again').status_code == 201
    cancelled = client.post(
        f'/api/games/{game_id}/cancel', json={}, headers=_headers(host),
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    assert GameArrivalIntent.query.filter_by(active=True).count() == 0
    assert _notices('rally_arrival_ended') == []

    other = _game(client, host, tim, starts_in=timedelta(minutes=-10))
    assert _on_my_way(client, tim, other, 'omw-finish').status_code == 201
    finished = client.post(
        f'/api/games/{other}/complete-session',
        json={'attendee_user_ids': [host['user']['id'], tim['user']['id']]},
        headers=_headers(host),
    )
    assert finished.status_code == 200, finished.get_json()
    intent = GameArrivalIntent.query.filter_by(
        client_attempt_id='omw-finish',
    ).one()
    assert intent.active is False and intent.end_reason == 'completed'
    assert _notices('rally_arrival_ended') == []


def test_editing_the_court_ends_etas_but_a_notes_edit_keeps_them(client):
    host = _register(client, 'omw-edit-host', 'Hana')
    tim = _register(client, 'omw-edit-tim', 'Tim')
    game_id = _game(client, host, tim)
    assert _on_my_way(client, tim, game_id).status_code == 201

    noted = client.patch(
        f'/api/games/{game_id}', json={'notes': 'Bring a ball'},
        headers=_headers(host),
    )
    assert noted.status_code == 200, noted.get_json()
    assert _detail(client, tim, game_id)['my_arrival']['active'] is True

    moved = client.patch(
        f'/api/games/{game_id}', json={'court_id': _court_id('Adorni Center')},
        headers=_headers(host),
    )
    assert moved.status_code == 200, moved.get_json()
    intent = GameArrivalIntent.query.filter_by(user_id=tim['user']['id']).one()
    assert intent.active is False and intent.end_reason == 'rescheduled'
    assert _notices('rally_arrival_ended') == []


def test_host_removal_ends_that_players_eta(client):
    host = _register(client, 'omw-remove-host', 'Hana')
    tim = _register(client, 'omw-remove-tim', 'Tim')
    game_id = _game(client, host, tim)
    assert _on_my_way(client, tim, game_id).status_code == 201
    removed = client.post(
        f"/api/games/{game_id}/remove/{tim['user']['id']}",
        headers=_headers(host),
    )
    assert removed.status_code == 200, removed.get_json()
    intent = GameArrivalIntent.query.filter_by(user_id=tim['user']['id']).one()
    assert intent.active is False and intent.end_reason == 'removed'


def test_live_rally_paths_keep_a_scheduled_eta_instead_of_closing_it(client):
    host = _register(client, 'omw-pulse-host', 'Hana')
    tim = _register(client, 'omw-pulse-tim', 'Tim')
    game_id = _game(client, host, tim)
    assert _on_my_way(client, tim, game_id).status_code == 201

    pulse = client.put('/api/play/pulse', json={
        'court_id': _court_id('Adorni Center'),
        'client_attempt_id': 'omw-pulse-attempt',
    }, headers=_headers(tim))
    assert pulse.status_code == 409
    assert pulse.get_json() == {'error': 'active_arrival'}

    intent = GameArrivalIntent.query.filter_by(user_id=tim['user']['id']).one()
    assert intent.active is True
    assert _notices('rally_arrival_ended') == []


def test_no_eta_while_friends_are_still_voting_on_the_time(client):
    host = _register(client, 'omw-vote-host', 'Hana')
    tim = _register(client, 'omw-vote-tim', 'Tim')
    game_id = _game(client, host, tim, starts_in=timedelta(minutes=20))
    row = db.session.get(Game, game_id)
    row.time_options = '[{"id": "a", "starts_at": "%sZ", "votes": []}]' % (
        row.scheduled_at.isoformat())
    db.session.commit()

    refused = _on_my_way(client, tim, game_id, 'omw-vote')
    assert refused.status_code == 409
    assert refused.get_json() == {'error': 'arrival_window_closed'}
    assert 'arrivals' not in _detail(client, tim, game_id)
