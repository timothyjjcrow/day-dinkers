"""Community approval, moderation, discovery, and notification contracts."""

import pytest

from backend.app import create_app, db
from backend.models import ClubBan, ClubJoinRequest, ClubMember, Court


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add_all([
            Court(
                name='Near Park', city='Costa Mesa', state='CA',
                latitude=33.66, longitude=-117.91,
            ),
            Court(
                name='Far Park', city='Eureka', state='CA',
                latitude=40.80, longitude=-124.16,
            ),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email, 'password': 'secret123', 'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def create_club(client, owner, **overrides):
    payload = {'name': 'Sunrise Pickleball', **overrides}
    response = client.post('/api/clubs', json=payload, headers=auth(owner))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_request_join_approval_and_admin_decision(client, app):
    owner = register(client, 'owner@example.com', 'Olivia Owner')
    admin = register(client, 'admin@example.com', 'Andy Admin')
    candidate = register(client, 'candidate@example.com', 'Casey Candidate')
    club = create_club(client, owner, join_policy='request')

    requested = client.post(
        f"/api/clubs/{club['id']}/join", headers=auth(admin),
    )
    assert requested.status_code == 202
    assert requested.get_json()['join_request_status'] == 'pending'
    assert client.get(
        f"/api/clubs/{club['id']}", headers=auth(admin),
    ).get_json()['joined'] is False

    requests = client.get(
        f"/api/clubs/{club['id']}/join-requests", headers=auth(owner),
    ).get_json()['items']
    assert [row['player']['id'] for row in requests] == [admin['user']['id']]
    decision = client.post(
        f"/api/clubs/{club['id']}/join-requests/{requests[0]['id']}/decision",
        json={'decision': 'approve'}, headers=auth(owner),
    )
    assert decision.status_code == 200

    promoted = client.patch(
        f"/api/clubs/{club['id']}/members/{admin['user']['id']}",
        json={'role': 'admin'}, headers=auth(owner),
    )
    assert promoted.get_json()['role'] == 'admin'

    assert client.post(
        f"/api/clubs/{club['id']}/join", headers=auth(candidate),
    ).status_code == 202
    pending = client.get(
        f"/api/clubs/{club['id']}/join-requests", headers=auth(admin),
    ).get_json()['items']
    assert len(pending) == 1
    assert client.post(
        f"/api/clubs/{club['id']}/join-requests/{pending[0]['id']}/decision",
        json={'decision': 'approve'}, headers=auth(admin),
    ).status_code == 200

    with app.app_context():
        roles = {
            row.user_id: row.role
            for row in ClubMember.query.filter_by(club_id=club['id']).all()
        }
        assert roles == {
            owner['user']['id']: 'owner',
            admin['user']['id']: 'admin',
            candidate['user']['id']: 'member',
        }
        assert ClubJoinRequest.query.filter_by(
            club_id=club['id'], status='pending',
        ).count() == 0


def test_remove_and_block_prevents_rejoin_until_unbanned(client, app):
    owner = register(client, 'owner@example.com', 'Owner')
    player = register(client, 'player@example.com', 'Player')
    club = create_club(client, owner)
    assert client.post(
        f"/api/clubs/{club['id']}/join", headers=auth(player),
    ).status_code == 200

    removed = client.post(
        f"/api/clubs/{club['id']}/remove",
        json={'user_id': player['user']['id'], 'ban': True, 'reason': 'Repeated spam'},
        headers=auth(owner),
    )
    assert removed.get_json() == {'removed': True, 'banned': True}
    assert client.post(
        f"/api/clubs/{club['id']}/join", headers=auth(player),
    ).status_code == 403
    bans = client.get(
        f"/api/clubs/{club['id']}/bans", headers=auth(owner),
    ).get_json()['items']
    assert bans[0]['user']['id'] == player['user']['id']
    assert bans[0]['reason'] == 'Repeated spam'
    assert client.delete(
        f"/api/clubs/{club['id']}/bans/{player['user']['id']}",
        headers=auth(owner),
    ).status_code == 200
    assert client.post(
        f"/api/clubs/{club['id']}/join", headers=auth(player),
    ).status_code == 200
    with app.app_context():
        assert ClubBan.query.count() == 0


def test_per_community_mentions_and_quiet_mode(client):
    owner = register(client, 'owner@example.com', 'Olivia')
    player = register(client, 'player@example.com', 'Ben Player')
    club = create_club(client, owner)
    client.post(f"/api/clubs/{club['id']}/join", headers=auth(player))

    setting = client.patch(
        f"/api/clubs/{club['id']}/notification-settings",
        json={'level': 'mentions'}, headers=auth(player),
    )
    assert setting.get_json() == {'level': 'mentions'}
    client.post(
        f"/api/clubs/{club['id']}/chat", json={'body': 'See you Saturday'},
        headers=auth(owner),
    )
    notifications = client.get('/api/notifications', headers=auth(player)).get_json()
    assert not [row for row in notifications['items'] if row['kind'] == 'club_message']

    client.post(
        f"/api/clubs/{club['id']}/chat", json={'body': '@Ben can you bring balls?'},
        headers=auth(owner),
    )
    notifications = client.get('/api/notifications', headers=auth(player)).get_json()
    assert len([row for row in notifications['items'] if row['kind'] == 'club_message']) == 1

    client.patch(
        f"/api/clubs/{club['id']}/notification-settings",
        json={'level': 'off'}, headers=auth(player),
    )
    client.post(
        f"/api/clubs/{club['id']}/chat", json={'body': '@Ben another update'},
        headers=auth(owner),
    )
    notifications = client.get('/api/notifications', headers=auth(player)).get_json()
    assert len([row for row in notifications['items'] if row['kind'] == 'club_message']) == 1


def test_dated_announcement_nonfriend_invite_nearby_and_restore(client):
    owner = register(client, 'owner@example.com', 'Owner')
    player = register(client, 'player@example.com', 'Visible Player')
    far_owner = register(client, 'far@example.com', 'Far Owner')
    near = create_club(
        client, owner, description='Morning open play', home_court_id=1,
    )
    create_club(client, far_owner, name='Far North', home_court_id=2)

    # Friendship is not required and the recipient still chooses whether to join.
    invited = client.post(
        f"/api/clubs/{near['id']}/invite",
        json={'user_id': player['user']['id']}, headers=auth(owner),
    )
    assert invited.status_code == 200

    posted = client.post(
        f"/api/clubs/{near['id']}/announcement",
        json={'announcement': 'Saturday play begins at 9 AM.'},
        headers=auth(owner),
    ).get_json()
    assert posted['announcement_author_name'] == 'Owner'
    assert posted['announcement_posted_at']

    nearby = client.get(
        '/api/clubs?lat=33.65&lng=-117.90', headers=auth(player),
    ).get_json()['items']
    assert [row['name'] for row in nearby[:2]] == [
        'Sunrise Pickleball', 'Far North',
    ]
    assert nearby[0]['distance_miles'] < nearby[1]['distance_miles']

    closed = client.delete(
        f"/api/clubs/{near['id']}", headers=auth(owner),
    ).get_json()
    assert closed['recoverable'] is True
    assert client.get(
        f"/api/clubs/{near['id']}", headers=auth(player),
    ).status_code == 404
    restored = client.post(
        f"/api/clubs/{near['id']}/restore", headers=auth(owner),
    )
    assert restored.status_code == 200
    assert restored.get_json()['announcement'] == 'Saturday play begins at 9 AM.'
