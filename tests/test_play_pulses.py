"""Available-this-hour pulses are remote, bounded, private, and retry-safe."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court,
    Game,
    GamePlayer,
    Notification,
    PlayAvailabilityPulse,
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
                name='Closed Court', city='Costa Mesa', state='CA',
                county_slug='orange-county', latitude=33.67,
                longitude=-117.90, closed=True,
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


def _court(client, query='larson'):
    return client.get(f'/api/courts?q={query}').get_json()['items'][0]


def _publish(client, person, court_id, attempt):
    return client.put('/api/play/pulse', json={
        'court_id': court_id,
        'client_attempt_id': attempt,
    }, headers=_headers(person))


def _discover(client, person, court):
    return client.get('/api/players/looking', query_string={
        'lat': court['latitude'],
        'lng': court['longitude'],
    }, headers=_headers(person))


def test_publish_is_fixed_idempotent_and_visible_in_me(client):
    owner = _register(client, 'pulse-owner', 'Owner')
    court = _court(client)

    created = _publish(client, owner, court['id'], 'publish-1')
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body['outcome'] == 'created'
    assert body['pulse']['active'] is True

    with client.application.app_context():
        row = db.session.get(PlayAvailabilityPulse, body['pulse']['id'])
        assert row.expires_at - row.declared_at == timedelta(minutes=60)

    replay = _publish(client, owner, court['id'], 'publish-1')
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['pulse']['expires_at'] == body['pulse']['expires_at']

    conflict = _publish(client, owner, court['id'], 'publish-2')
    assert conflict.status_code == 409
    assert conflict.get_json()['error'] == 'pulse_already_active'

    changed = _publish(client, owner, 999999, 'publish-1')
    assert changed.status_code == 409
    assert changed.get_json() == {'error': 'client_attempt_id_conflict'}

    me = client.get('/api/me', headers=_headers(owner)).get_json()
    assert me['active_play_pulse']['id'] == body['pulse']['id']
    assert me['active_play_pulse']['court']['id'] == court['id']

    cancelled = client.delete(
        f"/api/play/pulses/{body['pulse']['id']}",
        headers=_headers(owner),
    )
    assert cancelled.status_code == 200
    assert cancelled.get_json()['cancelled'] is True
    assert cancelled.get_json()['pulse']['active'] is False
    assert client.get(
        '/api/me', headers=_headers(owner),
    ).get_json()['active_play_pulse'] is None


def test_discovery_capability_accept_and_exact_retry_create_one_normal_game(client):
    owner = _register(client, 'pulse-source', 'Source')
    actor = _register(client, 'pulse-actor', 'Actor')
    competitor = _register(client, 'pulse-competitor', 'Competitor')
    court = _court(client)
    source = _publish(client, owner, court['id'], 'source-publish').get_json()['pulse']
    actor_pulse = _publish(
        client, actor, court['id'], 'actor-publish',
    ).get_json()['pulse']

    discovered = _discover(client, actor, court)
    assert discovered.status_code == 200, discovered.get_json()
    assert discovered.get_json()['pulse_count'] == 1
    summary = discovered.get_json()['pulses'][0]
    assert summary['id'] == source['id']
    assert summary['user']['id'] == owner['user']['id']
    assert summary['court']['id'] == court['id']
    assert isinstance(summary['accept_capability'], str)

    competitor_summary = next(
        item for item in _discover(
            client, competitor, court,
        ).get_json()['pulses']
        if item['id'] == source['id']
    )
    accepted = client.post(
        f"/api/play/pulses/{source['id']}/accept",
        json={
            'accept_capability': summary['accept_capability'],
            'client_attempt_id': 'accept-1',
        },
        headers=_headers(actor),
    )
    assert accepted.status_code == 201, accepted.get_json()
    accepted_body = accepted.get_json()
    assert accepted_body['outcome'] == 'created'
    assert accepted_body['pulse']['active'] is False
    assert accepted_body['pulse']['end_reason'] == 'matched'
    game = accepted_body['game']
    assert game['game_type'] == 'casual'
    assert game['visibility'] == 'open'
    assert game['recurrence'] == 'none'
    assert game['max_players'] == 4
    assert game['is_instant'] is False
    assert game['status'] == 'upcoming'
    assert {item['user_id'] for item in game['players']} == {
        owner['user']['id'], actor['user']['id'],
    }
    assert all(item['attending'] for item in game['players'])

    late_cancel = client.delete(
        f"/api/play/pulses/{source['id']}", headers=_headers(owner),
    )
    assert late_cancel.status_code == 200
    assert late_cancel.get_json()['cancelled'] is False
    assert late_cancel.get_json()['outcome'] == 'already_ended'
    assert late_cancel.get_json()['pulse']['end_reason'] == 'matched'

    replay = client.post(
        f"/api/play/pulses/{source['id']}/accept",
        json={
            'accept_capability': 'no-longer-needed-for-exact-retry',
            'client_attempt_id': 'accept-1',
        },
        headers=_headers(actor),
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['game']['id'] == game['id']

    lost = client.post(
        f"/api/play/pulses/{source['id']}/accept",
        json={
            'accept_capability': competitor_summary['accept_capability'],
            'client_attempt_id': 'competitor-accept',
        },
        headers=_headers(competitor),
    )
    assert lost.status_code == 404
    assert lost.get_json() == {'error': 'pulse_not_found'}
    assert 'game' not in lost.get_json()

    with client.application.app_context():
        assert Game.query.count() == 1
        persisted = db.session.get(Game, game['id'])
        assert persisted.scheduled_at - persisted.created_at <= timedelta(minutes=16)
        assert GamePlayer.query.filter_by(game_id=game['id']).count() == 2
        assert db.session.get(
            PlayAvailabilityPulse, actor_pulse['id'],
        ).end_reason == 'matched'


def test_pulse_match_reaches_saved_court_fans_once(client):
    owner = _register(client, 'pulse-fan-source', 'Source')
    actor = _register(client, 'pulse-fan-actor', 'Actor')
    fan = _register(client, 'pulse-fan-listener', 'Listener')
    court = _court(client)
    assert client.post(
        f"/api/courts/{court['id']}/favorite", headers=_headers(fan),
    ).status_code == 200
    source = _publish(
        client, owner, court['id'], 'fan-source-publish',
    ).get_json()['pulse']
    summary = next(
        item for item in _discover(client, actor, court).get_json()['pulses']
        if item['id'] == source['id']
    )

    accepted = client.post(
        f"/api/play/pulses/{source['id']}/accept",
        json={
            'accept_capability': summary['accept_capability'],
            'client_attempt_id': 'fan-accept',
        },
        headers=_headers(actor),
    )
    assert accepted.status_code == 201, accepted.get_json()
    game_id = accepted.get_json()['game']['id']

    def fan_alerts():
        return [
            item for item in client.get(
                '/api/notifications', headers=_headers(fan),
            ).get_json()['items']
            if item['kind'] == 'court_game'
        ]

    assert len(fan_alerts()) == 1
    assert fan_alerts()[0]['related_game_id'] == game_id
    replay = client.post(
        f"/api/play/pulses/{source['id']}/accept",
        json={
            'accept_capability': 'not-needed-on-exact-retry',
            'client_attempt_id': 'fan-accept',
        },
        headers=_headers(actor),
    )
    assert replay.status_code == 200
    assert len(fan_alerts()) == 1
    with client.application.app_context():
        assert Notification.query.filter_by(
            user_id=fan['user']['id'], kind='court_game',
            related_game_id=game_id,
        ).count() == 1


def test_pulse_rejects_conflicting_state_and_checkin_ends_it(client):
    player = _register(client, 'pulse-presence', 'Presence')
    court = _court(client)
    created = _publish(
        client, player, court['id'], 'presence-publish',
    ).get_json()['pulse']
    checked_in = client.post(
        f"/api/courts/{court['id']}/checkin",
        json={'looking_for_game': False},
        headers=_headers(player),
    )
    assert checked_in.status_code == 200
    with client.application.app_context():
        row = db.session.get(PlayAvailabilityPulse, created['id'])
        assert row.active is False
        assert row.end_reason == 'checked_in'
    rejected = _publish(client, player, court['id'], 'presence-publish-2')
    assert rejected.status_code == 409
    assert rejected.get_json() == {'error': 'active_checkin_present'}


def test_invalid_capability_block_and_account_delete_do_not_leak(client):
    owner = _register(client, 'pulse-delete-owner', 'Owner')
    viewer = _register(client, 'pulse-delete-viewer', 'Viewer')
    court = _court(client)
    pulse = _publish(
        client, owner, court['id'], 'delete-publish',
    ).get_json()['pulse']

    invalid = client.post(
        f"/api/play/pulses/{pulse['id']}/accept",
        json={
            'accept_capability': 'invalid',
            'client_attempt_id': 'invalid-accept',
        }, headers=_headers(viewer),
    )
    assert invalid.status_code == 404
    assert invalid.get_json() == {'error': 'pulse_not_found'}

    blocked = client.post(
        f"/api/users/{owner['user']['id']}/block",
        headers=_headers(viewer),
    )
    assert blocked.status_code in (200, 201), blocked.get_json()
    discovery = _discover(client, viewer, court).get_json()
    assert discovery['pulses'] == []

    deleted = client.delete('/api/me', json={
        'password': 'secret123',
    }, headers=_headers(owner))
    assert deleted.status_code == 200, deleted.get_json()
    with client.application.app_context():
        assert PlayAvailabilityPulse.query.filter_by(id=pulse['id']).first() is None


def test_pulse_remains_accept_capable_until_expiry(client):
    owner = _register(client, 'pulse-late-owner', 'Late Owner')
    actor = _register(client, 'pulse-late-actor', 'Late Actor')
    court = _court(client)
    pulse = _publish(
        client, owner, court['id'], 'late-publish',
    ).get_json()['pulse']
    with client.application.app_context():
        row = db.session.get(PlayAvailabilityPulse, pulse['id'])
        row.expires_at = utcnow() + timedelta(minutes=5)
        db.session.commit()

    summary = next(
        item for item in _discover(client, actor, court).get_json()['pulses']
        if item['id'] == pulse['id']
    )
    accepted = client.post(
        f"/api/play/pulses/{pulse['id']}/accept",
        json={
            'accept_capability': summary['accept_capability'],
            'client_attempt_id': 'late-accept',
        }, headers=_headers(actor),
    )
    assert accepted.status_code == 201, accepted.get_json()
    body = accepted.get_json()
    with client.application.app_context():
        row = db.session.get(PlayAvailabilityPulse, pulse['id'])
        game = db.session.get(Game, body['game']['id'])
        assert game.scheduled_at > row.expires_at
        assert game.scheduled_at - utcnow() > timedelta(minutes=14)


def test_overlapping_ordinary_game_consumes_pulse_but_future_game_does_not(client):
    creator = _register(client, 'pulse-game-creator', 'Creator')
    joiner = _register(client, 'pulse-game-joiner', 'Joiner')
    future_creator = _register(client, 'pulse-future-creator', 'Future')
    court = _court(client)
    creator_pulse = _publish(
        client, creator, court['id'], 'creator-pulse',
    ).get_json()['pulse']
    joiner_pulse = _publish(
        client, joiner, court['id'], 'joiner-pulse',
    ).get_json()['pulse']
    future_pulse = _publish(
        client, future_creator, court['id'], 'future-pulse',
    ).get_json()['pulse']

    near = client.post('/api/games', json={
        'court_id': court['id'],
        'scheduled_at': (utcnow() + timedelta(minutes=30)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 4,
        'client_attempt_id': 'near-game',
    }, headers=_headers(creator))
    assert near.status_code == 201, near.get_json()
    joined = client.post(
        f"/api/games/{near.get_json()['id']}/join",
        headers=_headers(joiner),
    )
    assert joined.status_code == 200, joined.get_json()

    future = client.post('/api/games', json={
        'court_id': court['id'],
        'scheduled_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 4,
        'client_attempt_id': 'future-game',
    }, headers=_headers(future_creator))
    assert future.status_code == 201, future.get_json()

    with client.application.app_context():
        assert db.session.get(
            PlayAvailabilityPulse, creator_pulse['id'],
        ).end_reason == 'game_created'
        assert db.session.get(
            PlayAvailabilityPulse, joiner_pulse['id'],
        ).end_reason == 'game_joined'
        future_row = db.session.get(
            PlayAvailabilityPulse, future_pulse['id'],
        )
        assert future_row.active is True

    immediate_conflict = _publish(
        client, creator, court['id'], 'creator-pulse-2',
    )
    assert immediate_conflict.status_code == 409
    assert immediate_conflict.get_json() == {'error': 'active_game'}


def test_waitlist_promotion_and_reschedule_consume_overlapping_pulses(client):
    host = _register(client, 'pulse-lifecycle-host', 'Host')
    occupant = _register(client, 'pulse-lifecycle-occupant', 'Occupant')
    waiter = _register(client, 'pulse-lifecycle-waiter', 'Waiter')
    rescheduler = _register(
        client, 'pulse-lifecycle-rescheduler', 'Rescheduler',
    )
    partner = _register(client, 'pulse-lifecycle-partner', 'Partner')
    court = _court(client)

    full_game = client.post('/api/games', json={
        'court_id': court['id'],
        'scheduled_at': (utcnow() + timedelta(minutes=30)).isoformat() + 'Z',
        'visibility': 'open',
        'max_players': 2,
        'client_attempt_id': 'promotion-game',
    }, headers=_headers(host)).get_json()
    assert client.post(
        f"/api/games/{full_game['id']}/join", headers=_headers(occupant),
    ).status_code == 200
    waiter_pulse = _publish(
        client, waiter, court['id'], 'waiter-pulse',
    ).get_json()['pulse']
    assert client.post(
        f"/api/games/{full_game['id']}/waitlist", headers=_headers(waiter),
    ).status_code == 200
    left = client.post(
        f"/api/games/{full_game['id']}/leave", headers=_headers(occupant),
    )
    assert left.status_code == 200, left.get_json()

    future_game = client.post('/api/games', json={
        'court_id': court['id'],
        'scheduled_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'visibility': 'open',
        'max_players': 4,
        'client_attempt_id': 'reschedule-game',
    }, headers=_headers(rescheduler)).get_json()
    assert client.post(
        f"/api/games/{future_game['id']}/join", headers=_headers(partner),
    ).status_code == 200
    host_pulse = _publish(
        client, rescheduler, court['id'], 'reschedule-host-pulse',
    ).get_json()['pulse']
    partner_pulse = _publish(
        client, partner, court['id'], 'reschedule-partner-pulse',
    ).get_json()['pulse']
    moved = client.post(
        f"/api/games/{future_game['id']}/reschedule",
        json={
            'scheduled_at': (
                utcnow() + timedelta(minutes=30)
            ).isoformat() + 'Z',
        },
        headers=_headers(rescheduler),
    )
    assert moved.status_code == 200, moved.get_json()

    with client.application.app_context():
        waiter_row = db.session.get(
            PlayAvailabilityPulse, waiter_pulse['id'],
        )
        assert waiter_row.end_reason == 'waitlist_promoted'
        assert GamePlayer.query.filter_by(
            game_id=full_game['id'], user_id=waiter['user']['id'],
        ).count() == 1
        assert db.session.get(
            PlayAvailabilityPulse, host_pulse['id'],
        ).end_reason == 'game_rescheduled'
        assert db.session.get(
            PlayAvailabilityPulse, partner_pulse['id'],
        ).end_reason == 'game_rescheduled'


def test_me_and_discovery_hide_stale_pulse_with_overlapping_game(client):
    owner = _register(client, 'pulse-stale-owner', 'Owner')
    viewer = _register(client, 'pulse-stale-viewer', 'Viewer')
    court = _court(client)
    pulse = _publish(
        client, owner, court['id'], 'stale-publish',
    ).get_json()['pulse']

    # Simulate an old writer that committed membership without the lifecycle
    # hook. Read surfaces must still refuse to advertise conflicting intent.
    with client.application.app_context():
        game = Game(
            court_id=court['id'],
            creator_id=owner['user']['id'],
            scheduled_at=utcnow() + timedelta(minutes=30),
            game_type='casual',
            visibility='open',
            recurrence='none',
            max_players=4,
            preferred_level='any',
            is_instant=False,
        )
        db.session.add(game)
        db.session.flush()
        db.session.add(GamePlayer(
            game_id=game.id, user_id=owner['user']['id'],
        ))
        db.session.commit()

    me = client.get('/api/me', headers=_headers(owner)).get_json()
    assert me['active_play_pulse'] is None
    discovery = _discover(client, viewer, court).get_json()
    assert all(item['id'] != pulse['id'] for item in discovery['pulses'])
