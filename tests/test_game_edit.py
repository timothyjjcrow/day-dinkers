"""Creator-only, in-place scheduled game editing contracts."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court, Game, GameOpenCall, GamePlayer, Message, Notification, utcnow,
)


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add_all([
            Court(
                name='Edit Court A', city='Irvine', state='CA',
                county_slug='orange-county', latitude=33.68,
                longitude=-117.82, num_courts=4,
            ),
            Court(
                name='Edit Court B', city='Tustin', state='CA',
                county_slug='orange-county', latitude=33.73,
                longitude=-117.81, num_courts=6,
            ),
        ])
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug, name):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def test_casual_open_play_capacity_scales_beyond_twelve(client):
    host = register(client, 'large-open-play-host', 'Large Session Host')
    court = Court.query.order_by(Court.id).first()
    created = client.post('/api/games', json={
        'court_id': court.id,
        'scheduled_at': (utcnow() + timedelta(hours=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 32,
    }, headers=headers(host))
    assert created.status_code == 201, created.get_json()
    assert created.get_json()['max_players'] == 32

    edited = client.patch(
        f"/api/games/{created.get_json()['id']}",
        json={'max_players': 48}, headers=headers(host),
    )
    assert edited.status_code == 200, edited.get_json()
    assert edited.get_json()['max_players'] == 48

    too_large = client.patch(
        f"/api/games/{created.get_json()['id']}",
        json={'max_players': 101}, headers=headers(host),
    )
    assert too_large.status_code == 400
    assert too_large.get_json()['error'] == 'invalid_max_players'


def test_host_edits_full_game_promotes_fifo_and_moves_typed_court_post(client):
    host = register(client, 'edit-host', 'Host')
    player = register(client, 'edit-player', 'Player')
    waiter = register(client, 'edit-waiter', 'Waiter')
    outsider = register(client, 'edit-outsider', 'Outsider')
    court_a, court_b = Court.query.order_by(Court.id).all()
    original_time = utcnow() + timedelta(hours=2)
    created = client.post('/api/games', json={
        'court_id': court_a.id,
        'scheduled_at': original_time.isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 2,
        'notes': 'Original note',
    }, headers=headers(host))
    assert created.status_code == 201, created.get_json()
    game = created.get_json()

    posted = client.post(
        f"/api/games/{game['id']}/open-call",
        json={'client_attempt_id': 'edit-open-call-attempt'},
        headers=headers(host),
    )
    assert posted.status_code == 201, posted.get_json()
    call_id = posted.get_json()['open_call']['id']
    message_id = posted.get_json()['open_call']['court_message_id']

    assert client.post(
        f"/api/games/{game['id']}/join", headers=headers(player),
    ).status_code == 200
    queued = client.post(
        f"/api/games/{game['id']}/waitlist", headers=headers(waiter),
    )
    assert queued.status_code == 200, queued.get_json()
    assert queued.get_json()['waitlist_position'] == 1

    denied = client.patch(
        f"/api/games/{game['id']}",
        json={'notes': 'Nope'}, headers=headers(outsider),
    )
    assert denied.status_code == 403

    new_time = original_time + timedelta(days=1, minutes=30)
    edited = client.patch(f"/api/games/{game['id']}", json={
        'court_id': court_b.id,
        'scheduled_at': new_time.isoformat() + 'Z',
        'max_players': 3,
        'preferred_level': 'advanced',
        'notes': 'Bring outdoor balls',
    }, headers=headers(host))
    assert edited.status_code == 200, edited.get_json()
    body = edited.get_json()
    assert set(body['updated_fields']) == {
        'court_id', 'scheduled_at', 'max_players', 'preferred_level', 'notes',
        'level_min', 'level_max',
    }
    assert body['court']['id'] == court_b.id
    assert body['max_players'] == 3
    assert body['preferred_level'] == 'advanced'
    assert body['notes'] == 'Bring outdoor balls'
    assert {item['user_id'] for item in body['players']} == {
        host['user']['id'], player['user']['id'], waiter['user']['id'],
    }
    assert body['waitlist_count'] == 0

    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    by_user = {item.user_id: item for item in row.players}
    assert by_user[host['user']['id']].attending_at is not None
    assert by_user[player['user']['id']].attending_at is None
    # Promotion is itself the waiter's fresh commitment to the edited slot.
    assert by_user[waiter['user']['id']].attending_at is not None
    call = db.session.get(GameOpenCall, call_id)
    message = db.session.get(Message, message_id)
    assert call.active is True
    assert message.court_id == court_b.id
    assert message.game_id is None and message.recipient_id is None
    assert Notification.query.filter_by(
        user_id=player['user']['id'], kind='game_updated',
        related_game_id=game['id'],
    ).count() == 1
    assert Notification.query.filter_by(
        user_id=waiter['user']['id'], kind='game_join',
        related_game_id=game['id'],
    ).count() == 1

    too_small = client.patch(
        f"/api/games/{game['id']}",
        json={'max_players': 2}, headers=headers(host),
    )
    assert too_small.status_code == 409
    assert too_small.get_json()['error'] == 'capacity_below_roster'

    replay = client.patch(
        f"/api/games/{game['id']}",
        json={'notes': 'Bring outdoor balls'}, headers=headers(host),
    )
    assert replay.status_code == 200
    assert replay.get_json()['updated_fields'] == []

    recurring = client.patch(
        f"/api/games/{game['id']}",
        json={'recurrence': 'weekly'}, headers=headers(host),
    )
    assert recurring.status_code == 200, recurring.get_json()
    db.session.expire_all()
    call = db.session.get(GameOpenCall, call_id)
    assert call.active is False
    assert call.end_reason == 'game_updated'


def test_edit_validation_prevents_data_loss_and_only_allows_visibility_widening(client):
    host = register(client, 'edit-private-host', 'Private Host')
    invitee = register(client, 'edit-private-invitee', 'Invitee')
    court = Court.query.order_by(Court.id).first()
    game = client.post('/api/games', json={
        'court_id': court.id,
        'scheduled_at': (utcnow() + timedelta(hours=3)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'private',
        'max_players': 2,
        'invite_user_ids': [invitee['user']['id']],
    }, headers=headers(host)).get_json()
    assert client.post(
        f"/api/games/{game['id']}/join", headers=headers(invitee),
    ).status_code == 200

    cases = (
        ({'mystery': True}, 400, 'invalid_game_edit_fields'),
        ({'max_players': True}, 400, 'invalid_max_players'),
        ({'max_players': 1}, 400, 'invalid_max_players'),
        ({'notes': 'x' * 501}, 400, 'invalid_notes'),
        ({'visibility': 'secret'}, 400, 'invalid_visibility'),
        ({'preferred_level': 'elite'}, 400, 'invalid_preferred_level'),
        ({'recurrence': 'daily'}, 400, 'invalid_recurrence'),
        ({'scheduled_at': 'not-a-date'}, 400, 'invalid_scheduled_at'),
    )
    for payload, status, error in cases:
        response = client.patch(
            f"/api/games/{game['id']}", json=payload, headers=headers(host),
        )
        assert response.status_code == status, response.get_json()
        assert response.get_json()['error'] == error

    widened = client.patch(
        f"/api/games/{game['id']}",
        json={'visibility': 'friends'}, headers=headers(host),
    )
    assert widened.status_code == 200, widened.get_json()
    assert widened.get_json()['visibility'] == 'friends'
    opened = client.patch(
        f"/api/games/{game['id']}",
        json={'visibility': 'open'}, headers=headers(host),
    )
    assert opened.status_code == 200, opened.get_json()
    narrowed = client.patch(
        f"/api/games/{game['id']}",
        json={'visibility': 'private'}, headers=headers(host),
    )
    assert narrowed.status_code == 409
    assert narrowed.get_json()['error'] == 'visibility_cannot_narrow'

    legacy = db.session.get(Game, game['id'])
    legacy.status = 'completed'
    legacy.completed_at = utcnow()
    db.session.commit()
    closed = client.patch(
        f"/api/games/{game['id']}",
        json={'notes': 'Too late'}, headers=headers(host),
    )
    assert closed.status_code == 400
    assert closed.get_json()['error'] == 'game_not_open'
