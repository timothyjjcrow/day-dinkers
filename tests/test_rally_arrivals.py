"""Arrival reservations for live rallies stay remote, private, and bounded."""

from datetime import timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from backend.app import create_app, db
from backend.models import (
    CheckIn,
    Court,
    Game,
    GameArrivalIntent,
    GamePlayer,
    Notification,
    utcnow,
)


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


def _register(client, slug, name=None):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name or slug.title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def _court(client, query):
    return client.get(f'/api/courts?q={query}').get_json()['items'][0]


def _launch(client, host, court, attempt):
    checked_in = client.post(
        f"/api/courts/{court['id']}/checkin",
        json={'looking_for_game': False}, headers=_headers(host),
    )
    assert checked_in.status_code == 200, checked_in.get_json()
    response = client.post('/api/games/rally', json={
        'court_id': court['id'],
        'scheduled_at': utcnow().isoformat() + 'Z',
        'client_attempt_id': attempt,
    }, headers=_headers(host))
    assert response.status_code == 201, response.get_json()
    return response.get_json()['game']


def _discover(client, person, court, game_id):
    response = client.get(
        '/api/players/looking',
        query_string={'lat': court['latitude'], 'lng': court['longitude']},
        headers=_headers(person),
    )
    assert response.status_code == 200, response.get_json()
    return next(
        rally for rally in response.get_json()['rallies']
        if rally['game_id'] == game_id
    )


def _arrive(client, person, game_id, capability, attempt, eta=10):
    return client.put(f'/api/games/{game_id}/arrival', json={
        'eta_minutes': eta,
        'client_attempt_id': attempt,
        'arrival_capability': capability,
    }, headers=_headers(person))


def _befriend(client, first, second):
    requested = client.post('/api/friends/request', json={
        'user_id': second['user']['id'],
    }, headers=_headers(first))
    assert requested.status_code == 201, requested.get_json()
    friendship_id = requested.get_json()['friendship_id']
    accepted = client.post(
        f'/api/friends/{friendship_id}/respond', json={'accept': True},
        headers=_headers(second),
    )
    assert accepted.status_code == 200, accepted.get_json()


def test_arrival_is_private_idempotent_bounded_and_announced_once(client):
    host = _register(client, 'arrival-host', 'Host')
    traveler = _register(client, 'arrival-traveler', 'Traveler')
    outsider = _register(client, 'arrival-outsider', 'Outsider')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-host-launch')

    summary = _discover(client, traveler, court, game['id'])
    assert summary['max_players'] == 4
    assert summary['court_city'] == court['city']
    assert summary['court_latitude'] == court['latitude']
    assert summary['court_longitude'] == court['longitude']
    assert summary['ready_count'] == 1
    assert summary['roster_count'] == 1
    assert summary['on_the_way_count'] == 0
    assert summary['arrival_available'] is True
    assert isinstance(summary['arrival_capability'], str)

    created = _arrive(
        client, traveler, game['id'], summary['arrival_capability'],
        'traveler-arrival-1', eta=10,
    )
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body['outcome'] == 'created'
    assert body['arrival']['active'] is True
    assert body['arrival']['eta_minutes'] == 10
    assert body['game']['ready_count'] == 1
    assert body['game']['roster_count'] == 1
    assert body['game']['on_the_way_count'] == 1
    assert body['game']['committed_count'] == 2
    assert body['game']['physical_spots_left'] == 3
    assert body['game']['spots_left'] == 2
    assert body['game']['my_arrival']['id'] == body['arrival']['id']
    assert body['game']['players'] == []
    assert 'creator_id' not in body['game']
    assert 'score_submitted_by' not in body['game']
    assert 'score_submitted_by_name' not in body['game']
    assert 'notes' not in body['game']
    assert 'club_id' not in body['game']
    assert 'crew_id' not in body['game']

    with client.application.app_context():
        row = db.session.get(GameArrivalIntent, body['arrival']['id'])
        assert row.arrives_at - row.declared_at == timedelta(minutes=10)
        assert row.expires_at - row.arrives_at == timedelta(minutes=5)
        assert row.expires_at <= row.declared_at + timedelta(minutes=20)
        assert GamePlayer.query.filter_by(
            game_id=game['id'], user_id=traveler['user']['id'],
        ).count() == 0
        assert CheckIn.query.filter_by(
            user_id=traveler['user']['id'], checked_out_at=None,
        ).count() == 0

    host_detail = client.get(
        f"/api/games/{game['id']}", headers=_headers(host),
    ).get_json()
    assert host_detail['arrivals'][0]['user_id'] == traveler['user']['id']
    assert host_detail['arrivals'][0]['eta_minutes'] == 10
    assert client.get(
        f"/api/games/{game['id']}", headers=_headers(outsider),
    ).status_code == 404
    outsider_summary = _discover(client, outsider, court, game['id'])
    assert outsider_summary['on_the_way_count'] == 1
    assert outsider_summary['spots_left'] == 2
    assert 'arrivals' not in outsider_summary
    assert 'user_id' not in outsider_summary

    mine = client.get('/api/me', headers=_headers(traveler)).get_json()
    assert mine['active_arrival']['game_id'] == game['id']
    assert mine['active_arrival']['max_players'] == 4
    assert mine['active_arrival']['court']['id'] == court['id']

    notices = client.get(
        '/api/notifications', headers=_headers(host),
    ).get_json()['items']
    arrival_notices = [item for item in notices if item['kind'] == 'rally_arrival']
    assert len(arrival_notices) == 1
    assert court['name'] not in arrival_notices[0]['title']
    assert court['name'] not in arrival_notices[0]['body']

    assert client.delete(
        '/api/notifications', headers=_headers(host),
    ).status_code == 200
    replay = _arrive(
        client, traveler, game['id'], 'expired-or-omitted-on-replay',
        'traveler-arrival-1', eta=10,
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['arrival']['expires_at'] == body['arrival']['expires_at']
    assert client.get(
        '/api/notifications', headers=_headers(host),
    ).get_json()['items'] == []
    changed = _arrive(
        client, traveler, game['id'], summary['arrival_capability'],
        'traveler-arrival-1', eta=15,
    )
    assert changed.status_code == 409
    assert changed.get_json() == {'error': 'client_attempt_id_conflict'}


def test_arrival_requires_safe_discovery_and_rejects_physical_presence(client):
    host = _register(client, 'arrival-auth-host', 'Host')
    stranger = _register(client, 'arrival-auth-stranger', 'Stranger')
    same_court = _register(client, 'arrival-same-court', 'Same Court')
    elsewhere = _register(client, 'arrival-elsewhere', 'Elsewhere')
    friend = _register(client, 'arrival-friend', 'Friend')
    invitee = _register(client, 'arrival-unsafe-invitee', 'Invitee')
    larson = _court(client, 'larson')
    adorni = _court(client, 'adorni')
    game = _launch(client, host, larson, 'arrival-auth-launch')

    denied = _arrive(
        client, stranger, game['id'], None, 'unauthorized-arrival', eta=5,
    )
    assert denied.status_code == 404
    assert denied.get_json() == {'error': 'game_not_found'}

    same_capability = _discover(
        client, same_court, larson, game['id'],
    )['arrival_capability']
    client.post(
        f"/api/courts/{larson['id']}/checkin", json={},
        headers=_headers(same_court),
    )
    present = _arrive(
        client, same_court, game['id'], same_capability,
        'same-court-arrival', eta=5,
    )
    assert present.status_code == 409
    assert present.get_json() == {'error': 'already_at_court'}

    elsewhere_capability = _discover(
        client, elsewhere, larson, game['id'],
    )['arrival_capability']
    client.post(
        f"/api/courts/{adorni['id']}/checkin", json={},
        headers=_headers(elsewhere),
    )
    wrong_presence = _arrive(
        client, elsewhere, game['id'], elsewhere_capability,
        'elsewhere-arrival', eta=10,
    )
    assert wrong_presence.status_code == 409
    assert wrong_presence.get_json() == {'error': 'active_checkin_elsewhere'}

    unsafe_invite = client.post(
        f"/api/games/{game['id']}/invite",
        json={'user_id': invitee['user']['id']}, headers=_headers(host),
    )
    assert unsafe_invite.status_code == 403
    assert unsafe_invite.get_json() == {'error': 'not_friends'}

    _befriend(client, host, friend)
    muted = client.patch('/api/me', json={
        'muted_notifications': ['rally_arrival'],
    }, headers=_headers(host))
    assert muted.status_code == 200, muted.get_json()
    assert 'rally_arrival' in muted.get_json()['user']['muted_notifications']
    accepted_friend = _arrive(
        client, friend, game['id'], None, 'friend-arrival', eta=15,
    )
    assert accepted_friend.status_code == 201, accepted_friend.get_json()
    assert not any(
        item['kind'] == 'rally_arrival'
        for item in client.get(
            '/api/notifications', headers=_headers(host),
        ).get_json()['items']
    )
    invited_friend = client.post(
        f"/api/games/{game['id']}/invite",
        json={'user_id': friend['user']['id']}, headers=_headers(host),
    )
    assert invited_friend.status_code == 200, invited_friend.get_json()
    declined = client.post(
        f"/api/games/{game['id']}/invites/decline",
        headers=_headers(friend),
    )
    assert declined.status_code == 200, declined.get_json()
    with client.application.app_context():
        intent = GameArrivalIntent.query.filter_by(
            client_attempt_id='friend-arrival',
        ).one()
        assert intent.active is False
        assert intent.end_reason == 'invite_declined'
        assert intent.last_announced_at is None


def test_partial_uniques_expiry_and_owned_retry_history(client):
    host = _register(client, 'arrival-slot-host', 'Host')
    other_host = _register(client, 'arrival-slot-other-host', 'Other Host')
    first = _register(client, 'arrival-slot-first', 'First')
    second = _register(client, 'arrival-slot-second', 'Second')
    court = _court(client, 'larson')
    other_court = _court(client, 'adorni')
    game = _launch(client, host, court, 'arrival-slot-launch')
    other_game = _launch(
        client, other_host, other_court, 'arrival-slot-other-launch',
    )
    first_capability = _discover(
        client, first, court, game['id'],
    )['arrival_capability']
    second_capability = _discover(
        client, second, court, game['id'],
    )['arrival_capability']

    reserved = _arrive(
        client, first, game['id'], first_capability,
        'arrival-slot-first-attempt', eta=5,
    )
    assert reserved.status_code == 201, reserved.get_json()
    other_capability = _discover(
        client, first, other_court, other_game['id'],
    )['arrival_capability']
    elsewhere = _arrive(
        client, first, other_game['id'], other_capability,
        'arrival-slot-first-elsewhere', eta=10,
    )
    assert elsewhere.status_code == 409
    assert elsewhere.get_json() == {
        'error': 'active_arrival_elsewhere',
        'game_id': game['id'],
    }
    occupied = _arrive(
        client, second, game['id'], second_capability,
        'arrival-slot-second-attempt', eta=10,
    )
    assert occupied.status_code == 409
    assert occupied.get_json() == {'error': 'arrival_slot_taken'}
    held_summary = _discover(client, second, court, game['id'])
    assert held_summary['arrival_available'] is False
    assert 'arrival_capability' not in held_summary

    with client.application.app_context():
        row = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-slot-first-attempt',
        ).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

    successor = _arrive(
        client, second, game['id'], second_capability,
        'arrival-slot-second-fresh', eta=10,
    )
    assert successor.status_code == 201, successor.get_json()
    replay = _arrive(
        client, first, game['id'], None,
        'arrival-slot-first-attempt', eta=5,
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['arrival']['active'] is False
    assert replay.get_json()['arrival']['end_reason'] == 'expired'
    assert replay.get_json()['game'] is None

    with client.application.app_context():
        reflected = {
            item['name']: item
            for item in inspect(db.engine).get_indexes('game_arrival_intent')
        }
        assert reflected['uq_game_arrival_active_user']['unique'] == 1
        assert reflected['uq_game_arrival_active_user']['column_names'] == ['user_id']
        assert reflected['uq_game_arrival_active_game']['unique'] == 1
        assert reflected['uq_game_arrival_active_game']['column_names'] == ['game_id']
        active = GameArrivalIntent.query.filter_by(active=True).one()
        db.session.add(GameArrivalIntent(
            game_id=active.game_id,
            user_id=first['user']['id'],
            eta_minutes=5,
            declared_at=utcnow(),
            arrives_at=utcnow() + timedelta(minutes=5),
            expires_at=utcnow() + timedelta(minutes=10),
            active=True,
            client_attempt_id='manual-duplicate-slot',
            client_attempt_fingerprint='0' * 64,
        ))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_effective_capacity_and_exact_court_join_convert_the_hold(client):
    host = _register(client, 'arrival-cap-host', 'Host')
    ready_two = _register(client, 'arrival-cap-two', 'Ready Two')
    ready_three = _register(client, 'arrival-cap-three', 'Ready Three')
    traveler = _register(client, 'arrival-cap-traveler', 'Traveler')
    walk_in = _register(client, 'arrival-cap-walkin', 'Walk In')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-cap-launch')

    for person in (ready_two, ready_three):
        client.post(
            f"/api/courts/{court['id']}/checkin", json={},
            headers=_headers(person),
        )
        joined = client.post(
            f"/api/games/{game['id']}/join", headers=_headers(person),
        )
        assert joined.status_code == 200, joined.get_json()

    capability = _discover(
        client, traveler, court, game['id'],
    )['arrival_capability']
    reserved = _arrive(
        client, traveler, game['id'], capability,
        'arrival-cap-traveler-attempt', eta=10,
    )
    assert reserved.status_code == 201, reserved.get_json()
    assert reserved.get_json()['game']['ready_count'] == 3
    assert reserved.get_json()['game']['committed_count'] == 4
    assert reserved.get_json()['game']['physical_spots_left'] == 1
    assert reserved.get_json()['game']['spots_left'] == 0

    # Discovery retains aggregate convergence even though its arrival CTA has
    # no effective slot left for this outsider.
    walk_in_summary = _discover(client, walk_in, court, game['id'])
    assert walk_in_summary['ready_count'] == 3
    assert walk_in_summary['on_the_way_count'] == 1
    assert walk_in_summary['spots_left'] == 0

    client.post(
        f"/api/courts/{court['id']}/checkin", json={},
        headers=_headers(walk_in),
    )
    full = client.post(
        f"/api/games/{game['id']}/join", headers=_headers(walk_in),
    )
    assert full.status_code == 400
    assert full.get_json() == {'error': 'game_full'}

    client.post(
        f"/api/courts/{court['id']}/checkin", json={},
        headers=_headers(traveler),
    )
    converted = client.post(
        f"/api/games/{game['id']}/join", headers=_headers(traveler),
    )
    assert converted.status_code == 200, converted.get_json()
    converted_body = converted.get_json()
    assert converted_body['ready_count'] == 4
    assert converted_body['on_the_way_count'] == 0
    assert converted_body['committed_count'] == 4
    assert converted_body['physical_spots_left'] == 0
    assert converted_body['spots_left'] == 0
    assert converted_body['my_arrival'] is None
    with client.application.app_context():
        intent = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-cap-traveler-attempt',
        ).one()
        assert intent.active is False
        assert intent.end_reason == 'arrived'
        assert GamePlayer.query.filter_by(
            game_id=game['id'], user_id=traveler['user']['id'],
        ).count() == 1


def test_partial_departure_changes_physical_ready_not_durable_occupancy(client):
    host = _register(client, 'arrival-ready-host', 'Host')
    departing = _register(client, 'arrival-ready-departing', 'Departing')
    traveler = _register(client, 'arrival-ready-traveler', 'Traveler')
    viewer = _register(client, 'arrival-ready-viewer', 'Viewer')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-ready-launch')
    client.post(
        f"/api/courts/{court['id']}/checkin", json={},
        headers=_headers(departing),
    )
    joined = client.post(
        f"/api/games/{game['id']}/join", headers=_headers(departing),
    )
    assert joined.status_code == 200, joined.get_json()
    capability = _discover(
        client, traveler, court, game['id'],
    )['arrival_capability']
    reserved = _arrive(
        client, traveler, game['id'], capability,
        'arrival-ready-hold', eta=10,
    )
    assert reserved.status_code == 201, reserved.get_json()
    before = reserved.get_json()['game']
    assert before['ready_count'] == 2
    assert before['roster_count'] == 2
    assert before['committed_count'] == 3
    assert before['physical_spots_left'] == 2
    assert before['spots_left'] == 1
    assert before['assembly_state'] == 'ready'

    checked_out = client.post('/api/checkout', headers=_headers(departing))
    assert checked_out.status_code == 200, checked_out.get_json()
    after = _discover(client, viewer, court, game['id'])
    assert after['ready_count'] == 1
    assert after['roster_count'] == 2
    assert after['on_the_way_count'] == 1
    assert after['committed_count'] == 3
    assert after['physical_spots_left'] == 3
    assert after['spots_left'] == 1
    assert after['assembly_state'] == 'finding'
    assert after['arrival_available'] is False
    host_detail = client.get(
        f"/api/games/{game['id']}", headers=_headers(host),
    )
    assert host_detail.status_code == 200, host_detail.get_json()
    assert host_detail.get_json()['assembly_state'] == 'finding'
    with client.application.app_context():
        rally = db.session.get(Game, game['id'])
        assert len(rally.players) == 2
        assert rally.assembly_closed_at is None


def test_rally_start_converts_hold_and_presence_loss_or_block_ends_it(client):
    host = _register(client, 'arrival-life-host', 'Host')
    traveler = _register(client, 'arrival-life-traveler', 'Traveler')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-life-launch')
    capability = _discover(
        client, traveler, court, game['id'],
    )['arrival_capability']
    reserved = _arrive(
        client, traveler, game['id'], capability,
        'arrival-life-convert', eta=5,
    )
    assert reserved.status_code == 201, reserved.get_json()
    client.post(
        f"/api/courts/{court['id']}/checkin", json={'looking_for_game': True},
        headers=_headers(traveler),
    )
    converted = client.post('/api/games/rally', json={
        'court_id': court['id'],
        'scheduled_at': utcnow().isoformat() + 'Z',
        'client_attempt_id': 'arrival-life-rally-tap',
    }, headers=_headers(traveler))
    assert converted.status_code == 200, converted.get_json()
    assert converted.get_json()['outcome'] == 'joined'
    assert converted.get_json()['game']['id'] == game['id']
    assert converted.get_json()['game']['on_the_way_count'] == 0
    assert Game.query.count() == 1

    remote = _register(client, 'arrival-life-remote', 'Remote')
    remote_capability = _discover(
        client, remote, court, game['id'],
    )['arrival_capability']
    remote_hold = _arrive(
        client, remote, game['id'], remote_capability,
        'arrival-life-remote-hold', eta=5,
    )
    assert remote_hold.status_code == 201, remote_hold.get_json()
    blocked = client.post(
        f"/api/users/{remote['user']['id']}/block", headers=_headers(host),
    )
    assert blocked.status_code == 200, blocked.get_json()
    assert client.get(
        f"/api/games/{game['id']}", headers=_headers(remote),
    ).status_code == 404
    with client.application.app_context():
        blocked_intent = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-life-remote-hold',
        ).one()
        assert blocked_intent.active is False
        assert blocked_intent.end_reason == 'blocked'
    blocked_replay = _arrive(
        client, remote, game['id'], None,
        'arrival-life-remote-hold', eta=5,
    )
    assert blocked_replay.status_code == 200, blocked_replay.get_json()
    assert blocked_replay.get_json()['game'] is None
    assert blocked_replay.get_json()['arrival']['end_reason'] == 'rally_closed'

    another = _register(client, 'arrival-life-another', 'Another')
    another_capability = _discover(
        client, another, court, game['id'],
    )['arrival_capability']
    another_hold = _arrive(
        client, another, game['id'], another_capability,
        'arrival-life-presence-hold', eta=10,
    )
    assert another_hold.status_code == 201, another_hold.get_json()
    with client.application.app_context():
        stale_at = utcnow() - timedelta(hours=3)
        CheckIn.query.filter_by(court_id=court['id'], checked_out_at=None).update({
            'checked_in_at': stale_at,
            'last_presence_ping_at': stale_at,
        }, synchronize_session=False)
        db.session.commit()

    # A remote arrival cannot keep a physically abandoned assembly alive.
    swept = client.get(
        '/api/players/looking',
        query_string={'lat': court['latitude'], 'lng': court['longitude']},
        headers=_headers(another),
    )
    assert swept.status_code == 200
    assert swept.get_json()['rally_count'] == 0
    with client.application.app_context():
        ended = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-life-presence-hold',
        ).one()
        assert ended.active is False
        assert ended.end_reason == 'rally_closed'
        closed = db.session.get(Game, game['id'])
        assert closed.assembly_closed_at is not None
    ended_notices = client.get(
        '/api/notifications', headers=_headers(another),
    ).get_json()['items']
    assert len([
        item for item in ended_notices
        if item['kind'] == 'rally_arrival_ended'
        and item['related_game_id'] == game['id']
    ]) == 1


def test_cancel_recreate_does_not_spam_roster(client):
    host = _register(client, 'arrival-spam-host', 'Host')
    traveler = _register(client, 'arrival-spam-traveler', 'Traveler')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-spam-launch')
    capability = _discover(
        client, traveler, court, game['id'],
    )['arrival_capability']
    first = _arrive(
        client, traveler, game['id'], capability,
        'arrival-spam-one', eta=5,
    )
    assert first.status_code == 201, first.get_json()
    assert client.delete(
        f"/api/games/{game['id']}/arrival", headers=_headers(traveler),
    ).status_code == 200
    assert not any(
        item['kind'] == 'rally_arrival_ended'
        for item in client.get(
            '/api/notifications', headers=_headers(traveler),
        ).get_json()['items']
    )
    client.delete('/api/notifications', headers=_headers(host))
    second = _arrive(
        client, traveler, game['id'], capability,
        'arrival-spam-two', eta=10,
    )
    assert second.status_code == 201, second.get_json()
    assert client.get(
        '/api/notifications', headers=_headers(host),
    ).get_json()['items'] == []
    with client.application.app_context():
        rows = GameArrivalIntent.query.filter_by(
            game_id=game['id'], user_id=traveler['user']['id'],
        ).order_by(GameArrivalIntent.id).all()
        assert len(rows) == 2
        assert rows[0].active is False
        assert rows[0].end_reason == 'cancelled'
        assert rows[1].active is True
        assert rows[1].last_announced_at == rows[0].last_announced_at
        assert Notification.query.filter_by(kind='rally_arrival').count() == 0


def test_cancel_score_and_account_deletion_release_arrivals(client):
    larson = _court(client, 'larson')
    adorni = _court(client, 'adorni')

    cancel_host = _register(client, 'arrival-clean-cancel-host', 'Cancel Host')
    cancel_traveler = _register(
        client, 'arrival-clean-cancel-traveler', 'Cancel Traveler',
    )
    cancel_game = _launch(
        client, cancel_host, larson, 'arrival-clean-cancel-launch',
    )
    cancel_capability = _discover(
        client, cancel_traveler, larson, cancel_game['id'],
    )['arrival_capability']
    assert _arrive(
        client, cancel_traveler, cancel_game['id'], cancel_capability,
        'arrival-clean-cancel-hold', eta=5,
    ).status_code == 201
    cancelled = client.post(
        f"/api/games/{cancel_game['id']}/cancel",
        headers=_headers(cancel_host),
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    with client.application.app_context():
        cancel_intent = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-clean-cancel-hold',
        ).one()
        assert cancel_intent.active is False
        assert cancel_intent.end_reason == 'rally_cancelled'
    cancel_notices = client.get(
        '/api/notifications', headers=_headers(cancel_traveler),
    ).get_json()['items']
    ended = [
        item for item in cancel_notices
        if item['kind'] == 'rally_arrival_ended'
    ]
    assert len(ended) == 1
    assert ended[0]['related_game_id'] == cancel_game['id']
    assert larson['name'] not in ended[0]['title']
    assert larson['name'] not in ended[0]['body']

    # A fresh rally can close its remote hold as soon as score entry starts.
    score_host = _register(client, 'arrival-clean-score-host', 'Score Host')
    opponent = _register(client, 'arrival-clean-opponent', 'Opponent')
    score_traveler = _register(
        client, 'arrival-clean-score-traveler', 'Score Traveler',
    )
    score_game = _launch(
        client, score_host, larson, 'arrival-clean-score-launch',
    )
    client.post(
        f"/api/courts/{larson['id']}/checkin", json={},
        headers=_headers(opponent),
    )
    joined = client.post(
        f"/api/games/{score_game['id']}/join", headers=_headers(opponent),
    )
    assert joined.status_code == 200, joined.get_json()
    score_capability = _discover(
        client, score_traveler, larson, score_game['id'],
    )['arrival_capability']
    assert _arrive(
        client, score_traveler, score_game['id'], score_capability,
        'arrival-clean-score-hold', eta=10,
    ).status_code == 201
    scored = client.post(
        f"/api/games/{score_game['id']}/complete", json={
            'team1': [score_host['user']['id']],
            'team2': [opponent['user']['id']],
            'score_team1': 11,
            'score_team2': 7,
        }, headers=_headers(score_host),
    )
    assert scored.status_code == 200, scored.get_json()
    with client.application.app_context():
        score_intent = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-clean-score-hold',
        ).one()
        assert score_intent.active is False
        assert score_intent.end_reason == 'completed'
    score_ended = [
        item for item in client.get(
            '/api/notifications', headers=_headers(score_traveler),
        ).get_json()['items']
        if item['kind'] == 'rally_arrival_ended'
    ]
    assert len(score_ended) == 1
    assert score_ended[0]['related_game_id'] == score_game['id']

    delete_host = _register(client, 'arrival-clean-delete-host', 'Delete Host')
    deleting = _register(client, 'arrival-clean-deleting', 'Deleting')
    delete_game = _launch(
        client, delete_host, larson, 'arrival-clean-delete-launch',
    )
    delete_capability = _discover(
        client, deleting, larson, delete_game['id'],
    )['arrival_capability']
    assert _arrive(
        client, deleting, delete_game['id'], delete_capability,
        'arrival-clean-delete-hold', eta=15,
    ).status_code == 201
    deleted = client.delete(
        '/api/me', json={'password': 'secret123'}, headers=_headers(deleting),
    )
    assert deleted.status_code == 200, deleted.get_json()
    with client.application.app_context():
        assert GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-clean-delete-hold',
        ).count() == 0

    deleting_host = _register(
        client, 'arrival-clean-deleting-host', 'Deleting Host',
    )
    stranded = _register(client, 'arrival-clean-stranded', 'Stranded')
    deleting_game = _launch(
        client, deleting_host, adorni, 'arrival-clean-deleting-host-launch',
    )
    stranded_capability = _discover(
        client, stranded, adorni, deleting_game['id'],
    )['arrival_capability']
    assert _arrive(
        client, stranded, deleting_game['id'], stranded_capability,
        'arrival-clean-stranded-hold', eta=10,
    ).status_code == 201
    removed_host = client.delete(
        '/api/me', json={'password': 'secret123'},
        headers=_headers(deleting_host),
    )
    assert removed_host.status_code == 200, removed_host.get_json()
    stranded_notices = client.get(
        '/api/notifications', headers=_headers(stranded),
    ).get_json()['items']
    assert len([
        item for item in stranded_notices
        if item['kind'] == 'rally_arrival_ended'
        and item['related_game_id'] == deleting_game['id']
    ]) == 1


def test_blocked_remote_holder_prevents_admission_and_invitation(client):
    host = _register(client, 'arrival-holder-host', 'Host')
    holder = _register(client, 'arrival-holder-reserved', 'Holder')
    blocked = _register(client, 'arrival-holder-blocked', 'Blocked')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-holder-launch')
    _befriend(client, host, blocked)
    boundary = client.post(
        f"/api/users/{blocked['user']['id']}/block", headers=_headers(holder),
    )
    assert boundary.status_code == 200, boundary.get_json()
    capability = _discover(client, holder, court, game['id'])[
        'arrival_capability'
    ]
    reserved = _arrive(
        client, holder, game['id'], capability,
        'arrival-holder-block-boundary', eta=10,
    )
    assert reserved.status_code == 201, reserved.get_json()

    client.post(
        f"/api/courts/{court['id']}/checkin", json={},
        headers=_headers(blocked),
    )
    denied_join = client.post(
        f"/api/games/{game['id']}/join", headers=_headers(blocked),
    )
    assert denied_join.status_code == 404
    assert denied_join.get_json() == {'error': 'game_not_found'}
    separate = client.post('/api/games/rally', json={
        'court_id': court['id'],
        'scheduled_at': utcnow().isoformat() + 'Z',
        'client_attempt_id': 'arrival-holder-blocked-rally',
    }, headers=_headers(blocked))
    assert separate.status_code == 201, separate.get_json()
    assert separate.get_json()['game']['id'] != game['id']
    denied_invite = client.post(
        f"/api/games/{game['id']}/invite",
        json={'user_id': blocked['user']['id']}, headers=_headers(host),
    )
    assert denied_invite.status_code == 403
    assert denied_invite.get_json() == {'error': 'user_blocked'}

    with client.application.app_context():
        intent = GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-holder-block-boundary',
        ).one()
        assert intent.active is True
        rally = db.session.get(Game, game['id'])
        assert {player.user_id for player in rally.players} == {
            host['user']['id'],
        }


def test_member_of_another_live_rally_cannot_reserve_remotely(client):
    first_host = _register(client, 'arrival-roster-first-host', 'First Host')
    second_host = _register(client, 'arrival-roster-second-host', 'Second Host')
    member = _register(client, 'arrival-roster-member', 'Member')
    larson = _court(client, 'larson')
    adorni = _court(client, 'adorni')
    first_game = _launch(
        client, first_host, larson, 'arrival-roster-first-launch',
    )
    client.post(
        f"/api/courts/{larson['id']}/checkin", json={},
        headers=_headers(member),
    )
    joined = client.post(
        f"/api/games/{first_game['id']}/join", headers=_headers(member),
    )
    assert joined.status_code == 200, joined.get_json()
    checked_out = client.post('/api/checkout', headers=_headers(member))
    assert checked_out.status_code == 200, checked_out.get_json()

    second_game = _launch(
        client, second_host, adorni, 'arrival-roster-second-launch',
    )
    capability = _discover(
        client, member, adorni, second_game['id'],
    )['arrival_capability']
    denied = _arrive(
        client, member, second_game['id'], capability,
        'arrival-roster-cross-rally', eta=10,
    )
    assert denied.status_code == 409, denied.get_json()
    assert denied.get_json()['error'] == 'active_rally_elsewhere'
    assert denied.get_json()['game_id'] == first_game['id']
    assert denied.get_json()['game']['id'] == first_game['id']
    with client.application.app_context():
        assert GameArrivalIntent.query.filter_by(
            client_attempt_id='arrival-roster-cross-rally',
        ).count() == 0


def test_discovery_hides_unusable_last_minutes_capability(client):
    host = _register(client, 'arrival-ceiling-host', 'Host')
    traveler = _register(client, 'arrival-ceiling-traveler', 'Traveler')
    court = _court(client, 'larson')
    game = _launch(client, host, court, 'arrival-ceiling-launch')
    capability = _discover(
        client, traveler, court, game['id'],
    )['arrival_capability']
    with client.application.app_context():
        row = db.session.get(Game, game['id'])
        row.scheduled_at = utcnow() - timedelta(minutes=86)
        db.session.commit()

    visible = _discover(client, traveler, court, game['id'])
    assert visible['arrival_available'] is False
    assert 'arrival_capability' not in visible
    rejected = _arrive(
        client, traveler, game['id'], capability,
        'arrival-ceiling-too-late', eta=5,
    )
    assert rejected.status_code == 409, rejected.get_json()
    assert rejected.get_json() == {'error': 'rally_no_longer_active'}


def test_auto_invited_nonmember_me_banner_is_aggregate_only(client):
    host = _register(client, 'arrival-banner-host', 'Host')
    invitee = _register(client, 'arrival-banner-invitee', 'Invitee')
    court = _court(client, 'larson')
    client.post(
        f"/api/courts/{court['id']}/checkin",
        json={'looking_for_game': True}, headers=_headers(invitee),
    )
    game = _launch(client, host, court, 'arrival-banner-launch')

    active_game = client.get(
        '/api/me', headers=_headers(invitee),
    ).get_json()['active_game']
    assert active_game['id'] == game['id']
    assert active_game['players'] == []
    assert active_game['ready_count'] == 1
    assert 'creator_id' not in active_game
    assert 'score_submitted_by' not in active_game
    assert 'notes' not in active_game
