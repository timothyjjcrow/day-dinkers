"""Focused coverage for paged discovery and targeted notification actions."""

import pytest

from backend.app import create_app, db
from backend.models import Club, ClubMember, Notification, User, utcnow


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def test_notifications_page_and_targeted_read_without_touching_other_rows(
    client, app,
):
    recipient = register(client, 'notifications@example.com', 'Recipient')
    stranger = register(client, 'stranger@example.com', 'Stranger')
    recipient_id = recipient['user']['id']
    stranger_id = stranger['user']['id']

    with app.app_context():
        rows = [
            Notification(
                user_id=recipient_id,
                kind='general',
                title=f'Notification {index}',
                unread_dedupe_key=f'topic:{index}',
                read=index == 0,
            )
            for index in range(7)
        ]
        other = Notification(
            user_id=stranger_id, kind='general', title='Private to stranger',
        )
        db.session.add_all([*rows, other])
        db.session.commit()
        expected_ids = [row.id for row in rows]
        other_id = other.id

    first = client.get(
        '/api/notifications?limit=3', headers=auth(recipient),
    ).get_json()
    assert [item['id'] for item in first['items']] == expected_ids[::-1][:3]
    assert first['unread'] == 6
    assert first['has_more'] is True
    assert first['next_cursor'] == first['items'][-1]['id']

    second = client.get(
        f"/api/notifications?limit=3&before_id={first['next_cursor']}",
        headers=auth(recipient),
    ).get_json()
    assert [item['id'] for item in second['items']] == expected_ids[::-1][3:6]
    assert not ({item['id'] for item in first['items']} & {
        item['id'] for item in second['items']
    })
    assert second['unread'] == 6
    assert second['has_more'] is True

    third = client.get(
        f"/api/notifications?limit=3&before_id={second['next_cursor']}",
        headers=auth(recipient),
    ).get_json()
    assert [item['id'] for item in third['items']] == expected_ids[:1]
    assert third['has_more'] is False
    assert third['next_cursor'] is None

    one_id = expected_ids[-1]
    marked = client.post(
        f'/api/notifications/{one_id}/read', headers=auth(recipient),
    )
    assert marked.status_code == 200
    assert marked.get_json()['read'] == 1
    # Repeating a targeted action is idempotent.
    assert client.post(
        f'/api/notifications/{one_id}/read', headers=auth(recipient),
    ).get_json()['read'] == 0

    batch_ids = expected_ids[2:4]
    batch = client.post(
        '/api/notifications/read', json={'ids': batch_ids},
        headers=auth(recipient),
    )
    assert batch.status_code == 200
    assert batch.get_json()['read'] == 2
    assert client.post(
        '/api/notifications/read', json={'ids': []}, headers=auth(recipient),
    ).get_json()['read'] == 0
    assert client.post(
        '/api/notifications/read', json={'ids': ['bad']},
        headers=auth(recipient),
    ).status_code == 400
    assert client.post(
        f'/api/notifications/{other_id}/read', headers=auth(recipient),
    ).status_code == 404

    with app.app_context():
        states = {
            row.id: (row.read, row.unread_dedupe_key)
            for row in Notification.query.filter_by(user_id=recipient_id).all()
        }
        assert states[one_id] == (True, None)
        assert all(states[item_id] == (True, None) for item_id in batch_ids)
        assert Notification.query.filter_by(user_id=stranger_id).count() == 1


def test_notifications_targeted_clear_keeps_legacy_clear_all_compatible(
    client, app,
):
    owner = register(client, 'clear-owner@example.com', 'Owner')
    other = register(client, 'clear-other@example.com', 'Other')
    with app.app_context():
        owner_rows = [
            Notification(user_id=owner['user']['id'], title=f'Owner {index}')
            for index in range(5)
        ]
        other_row = Notification(user_id=other['user']['id'], title='Other')
        db.session.add_all([*owner_rows, other_row])
        db.session.commit()
        owner_ids = [row.id for row in owner_rows]
        other_id = other_row.id

    assert client.delete(
        f'/api/notifications/{owner_ids[0]}', headers=auth(owner),
    ).get_json()['cleared'] == 1
    assert client.delete(
        '/api/notifications', json={'notification_ids': owner_ids[1:3]},
        headers=auth(owner),
    ).get_json()['cleared'] == 2
    assert client.delete(
        '/api/notifications', json={'ids': []}, headers=auth(owner),
    ).get_json()['cleared'] == 0
    assert client.delete(
        f'/api/notifications/{other_id}', headers=auth(owner),
    ).status_code == 404

    remaining = client.get('/api/notifications', headers=auth(owner)).get_json()
    assert {item['id'] for item in remaining['items']} == set(owner_ids[3:])
    # No body preserves the existing Clear all contract.
    assert client.delete(
        '/api/notifications', headers=auth(owner),
    ).get_json()['cleared'] == 2
    assert client.get(
        '/api/notifications', headers=auth(owner),
    ).get_json()['items'] == []
    assert client.get(
        '/api/notifications', headers=auth(other),
    ).get_json()['items'][0]['id'] == other_id


def test_nearby_players_use_stable_pages_and_slim_discovery_rows(client, app):
    viewer = register(client, 'viewer@example.com', 'Viewer')
    players = [
        register(client, f'nearby-{index}@example.com', f'Player {index}')
        for index in range(5)
    ]
    with app.app_context():
        for index, account in enumerate(players):
            user = db.session.get(User, account['user']['id'])
            user.last_lat = 33.66
            user.last_lng = -117.91 + (index * 0.001)
            user.last_location_at = utcnow()
            user.bio = f'Long profile biography {index}'
            user.ranked_wins = 20 + index
        db.session.commit()

    first = client.get(
        '/api/players/nearby?lat=33.66&lng=-117.91&limit=2&page=1',
        headers=auth(viewer),
    ).get_json()
    second = client.get(
        '/api/players/nearby?lat=33.66&lng=-117.91&limit=2&page=2',
        headers=auth(viewer),
    ).get_json()
    third = client.get(
        '/api/players/nearby?lat=33.66&lng=-117.91&limit=2&page=3',
        headers=auth(viewer),
    ).get_json()

    assert first['count'] == second['count'] == third['count'] == 5
    assert first['next_page'] == 2 and second['next_page'] == 3
    assert third['has_more'] is False and third['next_page'] is None
    all_ids = [
        item['id']
        for page in (first, second, third)
        for item in page['items']
    ]
    assert len(all_ids) == len(set(all_ids)) == 5
    assert [item['display_name'] for item in first['items']] == [
        'Player 0', 'Player 1',
    ]
    assert 'self_rating' in first['items'][0]
    assert 'bio' not in first['items'][0]
    assert 'ranked_wins' not in first['items'][0]
    assert client.get(
        '/api/players/nearby?lat=33.66&lng=-117.91&limit=500',
        headers=auth(viewer),
    ).get_json()['limit'] == 50


def test_club_directory_pages_after_exact_popularity_ranking(client, app):
    accounts = [
        register(client, f'club-{index}@example.com', f'Member {index}')
        for index in range(4)
    ]
    with app.app_context():
        creator_id = accounts[0]['user']['id']
        clubs = [
            Club(name=f'Club {index}', creator_id=creator_id)
            for index in range(4)
        ]
        clubs[2].description = 'Needle search phrase'
        db.session.add_all(clubs)
        db.session.flush()
        for club_index, club in enumerate(clubs):
            member_count = 4 - club_index
            for account in accounts[:member_count]:
                db.session.add(ClubMember(
                    club=club,
                    user_id=account['user']['id'],
                    role='owner' if account is accounts[0] else 'member',
                ))
        db.session.commit()
        club_ids = [club.id for club in clubs]

    first = client.get(
        '/api/clubs?limit=2&page=1', headers=auth(accounts[0]),
    ).get_json()
    second = client.get(
        '/api/clubs?limit=2&page=2', headers=auth(accounts[0]),
    ).get_json()
    assert first['count'] == second['count'] == 4
    assert [club['id'] for club in first['items']] == club_ids[:2]
    assert [club['member_count'] for club in first['items']] == [4, 3]
    assert [club['id'] for club in second['items']] == club_ids[2:]
    assert first['has_more'] is True and first['next_page'] == 2
    assert second['has_more'] is False and second['next_page'] is None

    searched = client.get(
        '/api/clubs?q=needle&limit=1', headers=auth(accounts[0]),
    ).get_json()
    assert searched['count'] == 1
    assert [club['id'] for club in searched['items']] == [club_ids[2]]
    assert client.get(
        '/api/clubs?limit=500', headers=auth(accounts[0]),
    ).get_json()['limit'] == 50
