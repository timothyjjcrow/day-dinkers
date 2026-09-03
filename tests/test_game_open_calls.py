"""Durable court-room recruiting cards for underfilled local games."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court,
    Crew,
    Game,
    GameOpenCall,
    Message,
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


def register(client, slug, name=None):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name or slug.title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def court_id(client, query='larson'):
    return client.get(f'/api/courts?q={query}').get_json()['items'][0]['id']


def create_game(client, owner, court, **overrides):
    payload = {
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(hours=3)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'recurrence': 'none',
        'max_players': 4,
    }
    payload.update(overrides)
    response = client.post(
        '/api/games', json=payload, headers=headers(owner),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def post_call(client, person, game_id, attempt):
    return client.post(
        f'/api/games/{game_id}/open-call',
        json={'client_attempt_id': attempt},
        headers=headers(person),
    )


def test_host_posts_one_typed_retry_safe_open_call(client):
    host = register(client, 'call-host', 'Host')
    viewer = register(client, 'call-viewer', 'Viewer')
    court = court_id(client)
    game = create_game(client, host, court)

    assert client.post(
        f"/api/games/{game['id']}/open-call",
        json={'client_attempt_id': 'missing-auth'},
    ).status_code == 401
    assert post_call(client, viewer, game['id'], 'outsider-call').status_code == 404

    with client.application.app_context():
        before_notifications = Notification.query.count()

    created = post_call(client, host, game['id'], 'open-call-1')
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body['outcome'] == 'created'
    assert body['open_call']['state'] == 'open'
    assert body['open_call']['spots_left'] == 3
    assert body['game']['open_call']['id'] == body['open_call']['id']

    with client.application.app_context():
        assert GameOpenCall.query.count() == 1
        assert Message.query.count() == 1
        message = Message.query.one()
        assert message.court_id == court
        assert message.game_id is None
        assert message.body == 'Open play session — see the live roster and join details.'
        assert Notification.query.count() == before_notifications

    room = client.get(
        f'/api/courts/{court}/chat', headers=headers(viewer),
    ).get_json()
    assert room['items'][0]['open_call']['id'] == body['open_call']['id']
    assert room['open_calls'][0]['game_id'] == game['id']
    assert client.get(
        f"/api/games/{game['id']}", headers=headers(host),
    ).get_json()['open_call']['court_message_id'] == room['items'][0]['id']

    exact = post_call(client, host, game['id'], 'open-call-1')
    assert exact.status_code == 200
    assert exact.get_json()['open_call']['id'] == body['open_call']['id']
    different_key = post_call(client, host, game['id'], 'open-call-2')
    assert different_key.status_code == 200
    assert different_key.get_json()['open_call']['id'] == body['open_call']['id']

    other = create_game(client, host, court)
    reused = post_call(client, host, other['id'], 'open-call-1')
    assert reused.status_code == 409
    assert reused.get_json() == {'error': 'client_attempt_id_conflict'}

    withdrawn = client.delete(
        f"/api/games/{game['id']}/open-call", headers=headers(host),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.get_json()['outcome'] == 'withdrawn'
    assert withdrawn.get_json()['open_call']['state'] == 'withdrawn'
    repeated = client.delete(
        f"/api/games/{game['id']}/open-call", headers=headers(host),
    )
    assert repeated.status_code == 200
    assert repeated.get_json()['outcome'] == 'already_withdrawn'
    no_repost = post_call(client, host, game['id'], 'open-call-3')
    assert no_repost.status_code == 200
    assert no_repost.get_json()['open_call']['state'] == 'withdrawn'

    with client.application.app_context():
        assert GameOpenCall.query.count() == 1
        assert Message.query.count() == 1


def test_open_call_rejects_ineligible_or_non_host_games(client):
    host = register(client, 'eligibility-host', 'Host')
    invitee = register(client, 'eligibility-invitee', 'Invitee')
    outsider = register(client, 'eligibility-outsider', 'Outsider')
    court = court_id(client)

    ordinary = create_game(client, host, court)
    assert post_call(client, outsider, ordinary['id'], 'not-host').status_code == 404

    private = create_game(
        client, host, court, visibility='private',
        invite_user_ids=[invitee['user']['id']],
    )
    friendship = client.post(
        '/api/friends/request', json={'user_id': invitee['user']['id']},
        headers=headers(host),
    )
    assert friendship.status_code == 201, friendship.get_json()
    accepted = client.post(
        f"/api/friends/{friendship.get_json()['friendship_id']}/respond",
        json={'accept': True}, headers=headers(invitee),
    )
    assert accepted.status_code == 200, accepted.get_json()
    friends = create_game(client, host, court, visibility='friends')
    weekly = create_game(client, host, court, recurrence='weekly')
    full = create_game(client, host, court, max_players=2)
    assert client.post(
        f"/api/games/{full['id']}/join", headers=headers(invitee),
    ).status_code == 200
    instant = create_game(client, host, court)
    crew_game = create_game(client, host, court_id(client, 'adorni'))
    started = create_game(client, host, court)
    closed_court_game = create_game(client, host, court)

    with client.application.app_context():
        db.session.get(Game, instant['id']).is_instant = True
        crew = Crew(
            owner_id=host['user']['id'], name='Private Crew',
            default_court_id=court,
        )
        db.session.add(crew)
        db.session.flush()
        db.session.get(Game, crew_game['id']).crew_id = crew.id
        db.session.get(Game, started['id']).scheduled_at = (
            utcnow() - timedelta(minutes=16)
        )
        db.session.get(Court, court).closed = True
        db.session.commit()

    candidates = [
        private['id'], friends['id'], weekly['id'], full['id'],
        instant['id'], started['id'],
        closed_court_game['id'],
    ]
    for index, game_id in enumerate(candidates):
        rejected = post_call(client, host, game_id, f'ineligible-{index}')
        assert rejected.status_code == 409, (game_id, rejected.get_json())

    # A public casual group session may ask its court chat for players.
    crew_call = post_call(client, host, crew_game['id'], 'eligible-group-session')
    assert crew_call.status_code == 201, crew_call.get_json()

    cancelled = create_game(client, host, court_id(client, 'adorni'))
    assert client.post(
        f"/api/games/{cancelled['id']}/cancel", headers=headers(host),
    ).status_code == 200
    assert post_call(client, host, cancelled['id'], 'cancelled').status_code == 409

    with client.application.app_context():
        assert GameOpenCall.query.count() == 1
        assert Message.query.count() == 1


def test_live_card_tracks_roster_waitlist_and_reopens_without_reposting(client):
    host = register(client, 'live-host', 'Host')
    player2 = register(client, 'live-two', 'Two')
    player3 = register(client, 'live-three', 'Three')
    player4 = register(client, 'live-four', 'Four')
    waiter = register(client, 'live-waiter', 'Waiter')
    court = court_id(client)
    game = create_game(client, host, court)
    call = post_call(client, host, game['id'], 'live-card').get_json()['open_call']

    for person in (player2, player3, player4):
        joined = client.post(
            f"/api/games/{game['id']}/join", headers=headers(person),
        )
        assert joined.status_code == 200, joined.get_json()

    full_room = client.get(
        f'/api/courts/{court}/chat', headers=headers(waiter),
    ).get_json()
    full_call = full_room['items'][0]['open_call']
    assert full_call['id'] == call['id']
    assert full_call['state'] == 'full'
    assert full_call['spots_left'] == 0
    assert full_call['can_waitlist'] is True

    queued = client.post(
        f"/api/games/{game['id']}/waitlist", headers=headers(waiter),
    )
    assert queued.status_code == 200, queued.get_json()
    queued_call = client.get(
        f'/api/courts/{court}/chat', headers=headers(waiter),
    ).get_json()['items'][0]['open_call']
    assert queued_call['state'] == 'full'
    assert queued_call['can_waitlist'] is False

    assert client.post(
        f"/api/games/{game['id']}/leave", headers=headers(player2),
    ).status_code == 200
    promoted = client.get(
        f'/api/courts/{court}/chat', headers=headers(waiter),
    ).get_json()['items'][0]['open_call']
    assert promoted['state'] == 'full'
    assert promoted['is_joined'] is True

    assert client.post(
        f"/api/games/{game['id']}/leave", headers=headers(player3),
    ).status_code == 200
    reopened = client.get(
        f'/api/courts/{court}/chat', headers=headers(waiter),
    ).get_json()['items'][0]['open_call']
    assert reopened['state'] == 'open'
    assert reopened['spots_left'] == 1
    assert reopened['can_join'] is False

    with client.application.app_context():
        assert GameOpenCall.query.count() == 1
        assert Message.query.filter_by(court_id=court).count() == 1


def test_host_transfer_and_message_delete_preserve_retry_ledgers(client):
    old_host = register(client, 'transfer-old', 'Old Host')
    new_host = register(client, 'transfer-new', 'New Host')
    court = court_id(client)
    game = create_game(client, old_host, court)
    assert client.post(
        f"/api/games/{game['id']}/join", headers=headers(new_host),
    ).status_code == 200
    old_call = post_call(
        client, old_host, game['id'], 'old-host-call',
    ).get_json()['open_call']

    left = client.post(
        f"/api/games/{game['id']}/leave", headers=headers(old_host),
    )
    assert left.status_code == 200
    assert left.get_json()['creator_id'] == new_host['user']['id']

    # An exact lost-response retry resolves for the former host even though
    # they can no longer mutate the game.
    replay = post_call(client, old_host, game['id'], 'old-host-call')
    assert replay.status_code == 200
    assert replay.get_json()['open_call']['id'] == old_call['id']
    assert replay.get_json()['open_call']['end_reason'] == 'host_changed'
    assert post_call(client, old_host, game['id'], 'old-host-new-key').status_code == 404

    successor = post_call(client, new_host, game['id'], 'new-host-call')
    assert successor.status_code == 201, successor.get_json()
    new_call = successor.get_json()['open_call']
    assert new_call['id'] != old_call['id']

    # The former host can still converge an exact lost-response retry, but a
    # later block by somebody on the current roster prevents that receipt from
    # leaking the new time, roster, or waitlist state.
    assert client.post(
        f"/api/users/{old_host['user']['id']}/block",
        headers=headers(new_host),
    ).status_code == 200
    private_replay = post_call(
        client, old_host, game['id'], 'old-host-call',
    )
    assert private_replay.status_code == 200
    private_payload = private_replay.get_json()
    assert private_payload['game'] is None
    assert private_payload['open_call'] == {
        'id': old_call['id'],
        'game_id': game['id'],
        'created_by_id': old_host['user']['id'],
        'state': 'closed',
        'active': False,
        'end_reason': 'host_changed',
        'can_join': False,
        'can_waitlist': False,
        'can_withdraw': False,
    }

    deleted = client.delete(
        f"/api/messages/{new_call['court_message_id']}",
        headers=headers(new_host),
    )
    assert deleted.status_code == 200
    retry = post_call(client, new_host, game['id'], 'new-host-call')
    assert retry.status_code == 200
    assert retry.get_json()['open_call']['state'] == 'withdrawn'
    assert retry.get_json()['open_call']['court_message_id'] is None
    assert post_call(client, new_host, game['id'], 'new-host-second').status_code == 200

    with client.application.app_context():
        calls = GameOpenCall.query.order_by(GameOpenCall.id).all()
        assert len(calls) == 2
        assert calls[0].end_reason == 'host_changed'
        assert calls[1].end_reason == 'message_deleted'
        assert calls[1].court_message_id is None
        assert Message.query.count() == 1


def test_whole_roster_block_hides_linked_court_card_everywhere(client):
    host = register(client, 'privacy-host', 'Host')
    teammate = register(client, 'privacy-teammate', 'Teammate')
    viewer = register(client, 'privacy-viewer', 'Viewer')
    court = court_id(client)
    game = create_game(client, host, court)
    assert client.post(
        f"/api/games/{game['id']}/join", headers=headers(teammate),
    ).status_code == 200
    generic = client.post(
        f'/api/courts/{court}/chat', json={'body': 'Courts are dry.'},
        headers=headers(host),
    ).get_json()
    call = post_call(
        client, host, game['id'], 'privacy-call',
    ).get_json()['open_call']

    visible = client.get(
        f'/api/courts/{court}/chat', headers=headers(viewer),
    ).get_json()
    assert any(item.get('open_call') for item in visible['items'])
    assert client.put(
        f'/api/courts/{court}/chat/subscription',
        json={'joined': True}, headers=headers(viewer),
    ).status_code == 200
    detail = client.get(
        f'/api/courts/{court}', headers=headers(viewer),
    ).get_json()
    assert detail['chat_last_message']['open_call']['id'] == call['id']
    listed = client.get('/api/chat/courts', headers=headers(viewer)).get_json()
    assert listed['items'][0]['last_message']['open_call']['id'] == call['id']

    blocked = client.post(
        f"/api/users/{viewer['user']['id']}/block",
        headers=headers(teammate),
    )
    assert blocked.status_code == 200

    hidden = client.get(
        f'/api/courts/{court}/chat', headers=headers(viewer),
    ).get_json()
    assert [item['id'] for item in hidden['items']] == [generic['id']]
    assert hidden['open_calls'] == []
    assert str(call['court_message_id']) not in hidden['heart_counts']

    room_list = client.get(
        '/api/chat/courts', headers=headers(viewer),
    ).get_json()['items']
    assert room_list[0]['last_message']['id'] == generic['id']
    assert 'open_call' not in room_list[0]['last_message']
    detail = client.get(
        f'/api/courts/{court}', headers=headers(viewer),
    ).get_json()
    assert detail['chat_last_message']['id'] == generic['id']
    assert 'open_call' not in detail['chat_last_message']
    heart = client.post(
        f"/api/messages/{call['court_message_id']}/heart",
        headers=headers(viewer),
    )
    assert heart.status_code == 403


def test_reschedule_updates_one_live_card_and_cancel_closes_it(client):
    host = register(client, 'lifecycle-call-host', 'Host')
    court = court_id(client)
    game = create_game(client, host, court)
    call = post_call(
        client, host, game['id'], 'lifecycle-call',
    ).get_json()['open_call']

    new_when = (utcnow() + timedelta(hours=8)).replace(microsecond=0)
    rescheduled = client.post(
        f"/api/games/{game['id']}/reschedule",
        json={'scheduled_at': new_when.isoformat() + 'Z'},
        headers=headers(host),
    )
    assert rescheduled.status_code == 200, rescheduled.get_json()
    assert rescheduled.get_json()['open_call']['id'] == call['id']
    assert rescheduled.get_json()['open_call']['scheduled_at'] == (
        new_when.isoformat() + 'Z'
    )

    room = client.get(
        f'/api/courts/{court}/chat', headers=headers(host),
    ).get_json()
    live_card = next(
        item['open_call'] for item in room['items']
        if item.get('open_call')
    )
    assert live_card['id'] == call['id']
    assert live_card['scheduled_at'] == new_when.isoformat() + 'Z'
    assert live_card['state'] == 'open'

    cancelled = client.post(
        f"/api/games/{game['id']}/cancel", headers=headers(host),
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    ended_room = client.get(
        f'/api/courts/{court}/chat', headers=headers(host),
    ).get_json()
    ended_card = next(
        item['open_call'] for item in ended_room['items']
        if item.get('open_call')
    )
    assert ended_card['id'] == call['id']
    assert ended_card['state'] == 'closed'
    assert ended_card['active'] is False
    assert ended_card['end_reason'] == 'cancelled'

    with client.application.app_context():
        assert GameOpenCall.query.count() == 1
        assert Message.query.filter_by(court_id=court).count() == 1


def test_account_deletion_closes_and_detaches_open_call(client):
    host = register(client, 'delete-call-host', 'Host')
    court = court_id(client)
    game = create_game(client, host, court)
    call = post_call(
        client, host, game['id'], 'delete-account-call',
    ).get_json()['open_call']

    deleted = client.delete(
        '/api/me', json={'password': 'secret123'}, headers=headers(host),
    )
    assert deleted.status_code == 200, deleted.get_json()

    with client.application.app_context():
        stored = db.session.get(GameOpenCall, call['id'])
        assert stored.active is False
        assert stored.end_reason == 'creator_deleted'
        assert stored.court_message_id is None
        assert db.session.get(Message, call['court_message_id']) is None
