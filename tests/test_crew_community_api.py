"""Focused API coverage for direct Crews and community-hosted sessions."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court, Crew, CrewInvite, Friendship, Game, GameInvite, Notification, User,
    utcnow,
)


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add_all([
            Court(
                name='Community Court', city='Costa Mesa', state='CA',
                county_slug='orange-county', latitude=33.66,
                longitude=-117.91, num_courts=6,
            ),
            Court(
                name='Second Court', city='Irvine', state='CA',
                county_slug='orange-county', latitude=33.68,
                longitude=-117.82, num_courts=4,
            ),
        ])
        db.session.commit()
        yield app
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


def headers(player):
    return {'Authorization': f"Bearer {player['token']}"}


def court_ids(client):
    items = client.get('/api/courts?q=court').get_json()['items']
    return {item['name']: item['id'] for item in items}


def make_friends(client, requester, addressee):
    requested = client.post('/api/friends/request', json={
        'user_id': addressee['user']['id'],
    }, headers=headers(requester))
    assert requested.status_code in (200, 201), requested.get_json()
    friendship_id = requested.get_json()['friendship_id']
    accepted = client.post(
        f'/api/friends/{friendship_id}/respond',
        json={'accept': True},
        headers=headers(addressee),
    )
    assert accepted.status_code == 200, accepted.get_json()
    return friendship_id


def create_direct_crew(client, owner, invitees=(), **overrides):
    payload = {
        'name': 'Wednesday Regulars',
        'invite_user_ids': [player['user']['id'] for player in invitees],
        **overrides,
    }
    response = client.post('/api/crews', json=payload, headers=headers(owner))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def accept_crew(client, crew_id, invitee):
    response = client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True},
        headers=headers(invitee),
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()['crew']


def scheduled_payload(court_id, crew, visibility, *, max_players=4,
                      game_type='casual', suffix='one'):
    return {
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'game_type': game_type,
        'visibility': visibility,
        'max_players': max_players,
        'crew_id': crew['id'],
        'expected_crew_version': crew['roster_version'],
        'client_attempt_id': f'community-session-{suffix}',
    }


def test_direct_crew_uses_friend_consent_and_keeps_pending_identities_owner_only(
        client, app):
    owner = register(client, 'direct-owner', 'Owner')
    alice = register(client, 'direct-alice', 'Alice')
    ben = register(client, 'direct-ben', 'Ben')
    stranger = register(client, 'direct-stranger', 'Stranger')
    make_friends(client, owner, alice)
    make_friends(client, owner, ben)
    court_id = court_ids(client)['Community Court']

    body = create_direct_crew(
        client, owner, (alice, ben, stranger), default_court_id=court_id,
    )
    crew = body['crew']
    assert body['created'] is True
    assert body['invited_count'] == 2
    assert set(body['invited_user_ids']) == {
        alice['user']['id'], ben['user']['id'],
    }
    assert body['skipped'] == [{
        'user_id': stranger['user']['id'], 'reason': 'not_eligible',
    }]
    assert crew['source_game_id'] is None
    assert crew['default_court_id'] == court_id
    assert crew['member_count'] == 1
    assert crew['pending_count'] == 2
    assert {
        row['user']['id'] for row in crew['pending_invites']
    } == {alice['user']['id'], ben['user']['id']}

    with app.app_context():
        stored = db.session.get(Crew, crew['id'])
        assert stored.source_game_id is None
        assert CrewInvite.query.filter_by(
            crew_id=crew['id'], status='pending',
        ).count() == 2
        invite_notifications = Notification.query.filter_by(
            related_crew_id=crew['id'], kind='crew_invite',
        ).all()
        assert len(invite_notifications) == 2
        assert all(
            'invited you to the Wednesday Regulars play group' in item.title
            for item in invite_notifications
        )
        assert all(
            item.body == (
                'Join the play group to plan your next casual play session '
                'together.'
            )
            for item in invite_notifications
        )

    alice_mine = client.get('/api/crews/mine', headers=headers(alice)).get_json()
    assert alice_mine['items'] == []
    assert alice_mine['invitations'][0]['id'] == crew['id']
    assert 'pending_invites' not in alice_mine['invitations'][0]
    assert client.get(
        f"/api/crews/{crew['id']}", headers=headers(alice),
    ).status_code == 404

    accept_crew(client, crew['id'], alice)
    alice_detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(alice),
    ).get_json()
    assert 'pending_invites' not in alice_detail
    owner_detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(owner),
    ).get_json()
    assert [
        row['user']['id'] for row in owner_detail['pending_invites']
    ] == [ben['user']['id']]
    assert client.get(
        f"/api/crews/{crew['id']}", headers=headers(stranger),
    ).status_code == 404


def test_owner_adds_and_reinvites_friends_idempotently_and_can_change_home_court(
        client, app):
    owner = register(client, 'invite-owner', 'Owner')
    alice = register(client, 'invite-alice', 'Alice')
    ben = register(client, 'invite-ben', 'Ben')
    make_friends(client, owner, alice)
    make_friends(client, owner, ben)
    courts = court_ids(client)
    crew = create_direct_crew(client, owner)['crew']

    first = client.post(
        f"/api/crews/{crew['id']}/invites",
        json={'invite_user_ids': [alice['user']['id']]},
        headers=headers(owner),
    )
    assert first.status_code == 200, first.get_json()
    assert first.get_json()['invited_count'] == 1
    first_invite_id = first.get_json()['crew']['pending_invites'][0]['id']

    duplicate = client.post(
        f"/api/crews/{crew['id']}/invites",
        json={'invite_user_ids': [alice['user']['id']]},
        headers=headers(owner),
    )
    assert duplicate.status_code == 200, duplicate.get_json()
    assert duplicate.get_json()['invited_count'] == 0
    assert duplicate.get_json()['skipped'] == [{
        'user_id': alice['user']['id'], 'reason': 'already_pending',
    }]

    declined = client.post(
        f"/api/crews/{crew['id']}/respond",
        json={'accept': False}, headers=headers(alice),
    )
    assert declined.status_code == 200, declined.get_json()
    reopened = client.post(
        f"/api/crews/{crew['id']}/invites",
        json={'invite_user_ids': [alice['user']['id']]},
        headers=headers(owner),
    )
    assert reopened.status_code == 200, reopened.get_json()
    assert reopened.get_json()['invited_count'] == 1
    assert reopened.get_json()['crew']['pending_invites'][0]['id'] == first_invite_id
    with app.app_context():
        rows = CrewInvite.query.filter_by(
            crew_id=crew['id'], invitee_id=alice['user']['id'],
        ).all()
        assert len(rows) == 1
        assert rows[0].status == 'pending'
        assert rows[0].resolved_at is None
        assert Notification.query.filter_by(
            related_crew_id=crew['id'], user_id=alice['user']['id'],
            kind='crew_invite',
        ).count() == 2

    accept_crew(client, crew['id'], alice)
    forbidden = client.post(
        f"/api/crews/{crew['id']}/invites",
        json={'invite_user_ids': [ben['user']['id']]},
        headers=headers(alice),
    )
    assert forbidden.status_code == 404
    assert forbidden.get_json() == {'error': 'crew_not_found'}

    changed = client.patch(
        f"/api/crews/{crew['id']}",
        json={'default_court_id': courts['Second Court']},
        headers=headers(owner),
    )
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()['default_court_id'] == courts['Second Court']
    assert client.patch(
        f"/api/crews/{crew['id']}",
        json={'default_court_id': courts['Community Court']},
        headers=headers(alice),
    ).status_code == 404
    cleared = client.patch(
        f"/api/crews/{crew['id']}",
        json={'default_court_id': None}, headers=headers(owner),
    )
    assert cleared.status_code == 200
    assert cleared.get_json()['default_court_id'] is None


def test_direct_crew_filters_blocked_deleted_and_nonfriends_and_caps_pending_slots(
        client, app):
    owner = register(client, 'eligibility-owner', 'Owner')
    blocked = register(client, 'eligibility-blocked', 'Blocked')
    deleted = register(client, 'eligibility-deleted', 'Deleted')
    stranger = register(client, 'eligibility-stranger', 'Stranger')
    make_friends(client, owner, blocked)
    make_friends(client, owner, deleted)
    assert client.post(
        f"/api/users/{blocked['user']['id']}/block", headers=headers(owner),
    ).status_code == 200
    with app.app_context():
        db.session.get(User, deleted['user']['id']).deleted_at = utcnow()
        db.session.commit()

    filtered = create_direct_crew(client, owner, (blocked, deleted, stranger))
    assert filtered['invited_count'] == 0
    assert filtered['skipped'] == [
        {'user_id': blocked['user']['id'], 'reason': 'not_eligible'},
        {'user_id': deleted['user']['id'], 'reason': 'not_eligible'},
        {'user_id': stranger['user']['id'], 'reason': 'not_eligible'},
    ]

    invitees = [
        register(client, f'capacity-{index}', f'Player {index}')
        for index in range(12)
    ]
    with app.app_context():
        db.session.add_all([
            Friendship(
                requester_id=owner['user']['id'],
                addressee_id=player['user']['id'],
                status='accepted',
            )
            for player in invitees
        ])
        db.session.commit()
    full = create_direct_crew(client, owner, invitees[:11], name='Full Crew')
    assert full['invited_count'] == 11
    overflow = client.post(
        f"/api/crews/{full['crew']['id']}/invites",
        json={'invite_user_ids': [invitees[11]['user']['id']]},
        headers=headers(owner),
    )
    assert overflow.status_code == 200, overflow.get_json()
    assert overflow.get_json()['invited_count'] == 0
    assert overflow.get_json()['skipped'] == [{
        'user_id': invitees[11]['user']['id'], 'reason': 'crew_full',
    }]
    too_many = client.post('/api/crews', json={
        'name': 'Too Many',
        'invite_user_ids': [player['user']['id'] for player in invitees],
    }, headers=headers(owner))
    assert too_many.status_code == 400
    assert too_many.get_json() == {'error': 'too_many_invitees'}


def test_casual_crew_sessions_support_group_friends_and_open_without_identity_leaks(
        client, app):
    owner = register(client, 'session-owner', 'Owner')
    alice = register(client, 'session-alice', 'Alice')
    ben = register(client, 'session-ben', 'Ben')
    owner_friend = register(client, 'session-friend', 'Owner Friend')
    outsider = register(client, 'session-outsider', 'Outsider')
    make_friends(client, owner, alice)
    ben_friendship = make_friends(client, owner, ben)
    make_friends(client, owner, owner_friend)
    court_id = court_ids(client)['Community Court']
    crew = create_direct_crew(client, owner, (alice, ben))['crew']
    accept_crew(client, crew['id'], alice)
    crew = accept_crew(client, crew['id'], ben)

    private_payload = scheduled_payload(
        court_id, crew, 'private', max_players=12, suffix='private',
    )
    private = client.post(
        '/api/games', json=private_payload, headers=headers(owner),
    )
    assert private.status_code == 201, private.get_json()
    assert private.get_json()['visibility'] == 'private'
    assert private.get_json()['max_players'] == 3
    # Deploy-time compatibility: the old server hashed an explicitly open
    # Crew request as private. A delayed device retry must recover that same
    # immutable row instead of duplicating or changing its audience.
    legacy_retry_payload = {**private_payload, 'visibility': 'open'}
    legacy_retry = client.post(
        '/api/games', json=legacy_retry_payload, headers=headers(owner),
    )
    assert legacy_retry.status_code == 200, legacy_retry.get_json()
    assert legacy_retry.get_json()['id'] == private.get_json()['id']
    assert legacy_retry.get_json()['visibility'] == 'private'

    # Crew consent remains valid if a later social friendship is removed. The
    # direct GameInvite still grants that member access to a friends session.
    assert client.delete(
        f'/api/friends/{ben_friendship}', headers=headers(owner),
    ).status_code == 200
    friends_game = client.post('/api/games', json=scheduled_payload(
        court_id, crew, 'friends', max_players=8, suffix='friends',
    ), headers=headers(owner))
    assert friends_game.status_code == 201, friends_game.get_json()
    friends_body = friends_game.get_json()
    assert friends_body['visibility'] == 'friends'
    assert friends_body['max_players'] == 8
    assert client.get(
        f"/api/games/{friends_body['id']}", headers=headers(ben),
    ).status_code == 200
    friend_view = client.get(
        f"/api/games/{friends_body['id']}", headers=headers(owner_friend),
    )
    assert friend_view.status_code == 200
    assert friend_view.get_json()['crew_id'] is None
    assert friend_view.get_json()['crew_name'] is None
    assert client.get(
        f"/api/games/{friends_body['id']}", headers=headers(outsider),
    ).status_code == 404

    open_game = client.post('/api/games', json=scheduled_payload(
        court_id, crew, 'open', max_players=12, suffix='open',
    ), headers=headers(owner))
    assert open_game.status_code == 201, open_game.get_json()
    open_body = open_game.get_json()
    assert open_body['visibility'] == 'open'
    assert open_body['max_players'] == 12
    with app.app_context():
        for game_id in (
            private.get_json()['id'], friends_body['id'], open_body['id'],
        ):
            assert {
                row.user_id for row in GameInvite.query.filter_by(
                    game_id=game_id,
                ).all()
            } == {alice['user']['id'], ben['user']['id']}

    public_view = client.get(
        f"/api/games/{open_body['id']}", headers=headers(outsider),
    )
    assert public_view.status_code == 200
    assert public_view.get_json()['crew_id'] is None
    assert public_view.get_json()['crew_name'] is None
    assert public_view.get_json()['crew_roster_version'] is None

    call = client.post(
        f"/api/games/{open_body['id']}/open-call",
        json={'client_attempt_id': 'open-crew-community-call'},
        headers=headers(owner),
    )
    assert call.status_code == 201, call.get_json()
    room = client.get(
        f'/api/courts/{court_id}/chat', headers=headers(outsider),
    ).get_json()
    public_call = next(
        row['open_call'] for row in room['items'] if row.get('open_call')
    )
    assert public_call['game_id'] == open_body['id']
    assert not any(key.startswith('crew') for key in public_call)


def test_ranked_crew_sessions_are_forced_private_and_exact(client, app):
    owner = register(client, 'ranked-owner', 'Owner')
    opponent = register(client, 'ranked-opponent', 'Opponent')
    outsider = register(client, 'ranked-outsider', 'Outsider')
    make_friends(client, owner, opponent)
    court_id = court_ids(client)['Community Court']
    crew = create_direct_crew(client, owner, (opponent,))['crew']
    crew = accept_crew(client, crew['id'], opponent)

    ranked = client.post('/api/games', json=scheduled_payload(
        court_id, crew, 'open', max_players=12, game_type='ranked',
        suffix='ranked',
    ), headers=headers(owner))
    assert ranked.status_code == 201, ranked.get_json()
    body = ranked.get_json()
    assert body['game_type'] == 'ranked'
    assert body['visibility'] == 'private'
    assert body['max_players'] == 2
    assert client.get(
        f"/api/games/{body['id']}", headers=headers(outsider),
    ).status_code == 404
    unavailable = client.post(
        f"/api/games/{body['id']}/open-call",
        json={'client_attempt_id': 'ranked-crew-open-call'},
        headers=headers(owner),
    )
    assert unavailable.status_code == 409
    assert unavailable.get_json() == {'error': 'open_call_not_available'}
    with app.app_context():
        stored = db.session.get(Game, body['id'])
        assert stored.crew_id == crew['id']
        assert {row.user_id for row in stored.invites} == {
            opponent['user']['id'],
        }


def test_crew_invite_writer_locks_complete_user_set_before_crew(
        client, monkeypatch):
    owner = register(client, 'locks-owner', 'Owner')
    pending = register(client, 'locks-pending', 'Pending')
    target = register(client, 'locks-target', 'Target')
    make_friends(client, owner, pending)
    make_friends(client, owner, target)
    crew = create_direct_crew(client, owner, (pending,))['crew']

    from backend.routes import crews as crew_routes

    events = []
    original_lock_users = crew_routes._lock_users_for_update
    original_active_crew = crew_routes._active_crew

    def recording_lock_users(user_ids):
        events.append(('users', tuple(sorted(set(user_ids)))))
        return original_lock_users(user_ids)

    def recording_active_crew(crew_id, lock=False):
        if lock:
            events.append(('crew', crew_id))
        return original_active_crew(crew_id, lock=lock)

    monkeypatch.setattr(crew_routes, '_lock_users_for_update', recording_lock_users)
    monkeypatch.setattr(crew_routes, '_active_crew', recording_active_crew)
    response = client.post(
        f"/api/crews/{crew['id']}/invites",
        json={'invite_user_ids': [target['user']['id']]},
        headers=headers(owner),
    )
    assert response.status_code == 200, response.get_json()
    user_event = next(event for event in events if event[0] == 'users')
    crew_event = next(event for event in events if event[0] == 'crew')
    assert events.index(user_event) < events.index(crew_event)
    assert set(user_event[1]) == {
        owner['user']['id'], pending['user']['id'], target['user']['id'],
    }
