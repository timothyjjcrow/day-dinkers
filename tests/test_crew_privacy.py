"""Privacy regressions at the boundary between Crews and existing surfaces."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, MessageHeart, utcnow


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Privacy Court',
            city='Costa Mesa',
            state='CA',
            county_slug='orange-county',
            latitude=33.66,
            longitude=-117.91,
            num_courts=6,
        ))
        db.session.commit()
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


def headers(player):
    return {'Authorization': f"Bearer {player['token']}"}


def court_id(client):
    return client.get('/api/courts?q=privacy').get_json()['items'][0]['id']


def completed_game(client, owner, players, *, visibility='open'):
    payload = {
        'court_id': court_id(client),
        'scheduled_at': (utcnow() + timedelta(hours=1)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': visibility,
        'max_players': len(players),
    }
    if visibility == 'private':
        payload['invite_user_ids'] = [
            player['user']['id'] for player in players[1:]
        ]
    created = client.post('/api/games', json=payload, headers=headers(owner))
    assert created.status_code == 201, created.get_json()
    game = created.get_json()
    for player in players[1:]:
        joined = client.post(
            f"/api/games/{game['id']}/join", headers=headers(player),
        )
        assert joined.status_code == 200, joined.get_json()
    split = max(1, len(players) // 2)
    completed = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [player['user']['id'] for player in players[:split]],
        'team2': [player['user']['id'] for player in players[split:]],
        'score_team1': 11,
        'score_team2': 7,
    }, headers=headers(owner))
    assert completed.status_code == 200, completed.get_json()
    assert completed.get_json()['status'] == 'completed'
    return completed.get_json()


def create_crew(client, game, owner):
    response = client.post(
        f"/api/games/{game['id']}/crew", headers=headers(owner),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()['crew']


def accept_crew(client, crew, player):
    response = client.post(
        f"/api/crews/{crew['id']}/respond",
        json={'accept': True},
        headers=headers(player),
    )
    assert response.status_code == 200, response.get_json()


def befriend(client, requester, addressee):
    response = client.post('/api/friends/request', json={
        'user_id': addressee['user']['id'],
    }, headers=headers(requester))
    assert response.status_code == 201, response.get_json()
    friendship_id = response.get_json()['friendship_id']
    accepted = client.post(
        f'/api/friends/{friendship_id}/respond',
        json={'accept': True},
        headers=headers(addressee),
    )
    assert accepted.status_code == 200, accepted.get_json()


def test_public_profile_uses_requester_for_crew_privacy_and_owner_for_result(client):
    owner = register(client, 'profile-owner@example.com', 'Owner')
    invitee = register(client, 'profile-invitee@example.com', 'Invitee')
    source = completed_game(
        client, owner, [owner, invitee], visibility='private',
    )
    crew = create_crew(client, source, owner)
    accept_crew(client, crew, invitee)
    detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(owner),
    ).get_json()
    created = client.post('/api/games', json={
        'court_id': court_id(client),
        'scheduled_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'crew_id': crew['id'],
        'expected_crew_version': detail['roster_version'],
    }, headers=headers(owner))
    assert created.status_code == 201, created.get_json()
    linked_game = created.get_json()
    assert client.post(
        f"/api/games/{linked_game['id']}/join", headers=headers(invitee),
    ).status_code == 200
    completed = client.post(f"/api/games/{linked_game['id']}/complete", json={
        'team1': [owner['user']['id']],
        'team2': [invitee['user']['id']],
        'score_team1': 11,
        'score_team2': 7,
    }, headers=headers(owner))
    assert completed.status_code == 200, completed.get_json()
    assert client.post(
        f"/api/crews/{crew['id']}/leave", headers=headers(invitee),
    ).status_code == 200

    # The former member can see the immutable private result, but no longer has
    # access to the Crew.
    # Result fields describe the profile owner; Crew fields authorize the actual
    # requester instead of borrowing that owner's identity.
    profile = client.get(
        f"/api/users/{owner['user']['id']}", headers=headers(invitee),
    ).get_json()
    result = next(
        item for item in profile['recent_games'] if item['id'] == linked_game['id']
    )
    assert result['you_won'] is True
    assert result['crew_id'] is None
    assert result['crew_name'] is None
    assert result['crew_roster_version'] is None

    owner_profile = client.get(
        f"/api/users/{owner['user']['id']}", headers=headers(owner),
    ).get_json()
    owner_result = next(
        item for item in owner_profile['recent_games']
        if item['id'] == linked_game['id']
    )
    assert owner_result['crew_id'] == crew['id']
    assert owner_result['you_won'] is True


def test_friends_digest_excludes_completed_games_hidden_from_viewer(client):
    viewer = register(client, 'digest-viewer@example.com', 'Viewer')
    friend = register(client, 'digest-friend@example.com', 'Friend')
    opponent = register(client, 'digest-opponent@example.com', 'Opponent')
    befriend(client, viewer, friend)

    completed_game(
        client, friend, [friend, opponent], visibility='private',
    )
    hidden_digest = client.get(
        '/api/friends/digest', headers=headers(viewer),
    ).get_json()
    assert hidden_digest == {
        'days': 7,
        'games': 0,
        'friends_played': 0,
        'checkins': 0,
        'top': [],
    }

    # A visible result still contributes normally; privacy filtering does not
    # turn the digest into an all-or-nothing feed.
    completed_game(client, friend, [friend, opponent], visibility='open')
    visible_digest = client.get(
        '/api/friends/digest', headers=headers(viewer),
    ).get_json()
    assert visible_digest['games'] == 1
    assert visible_digest['friends_played'] == 1
    assert visible_digest['top'] == [{
        'id': friend['user']['id'],
        'display_name': 'Friend',
        'games': 1,
        'wins': 1,
        'losses': 0,
    }]


def test_crew_reactions_only_serialize_current_authorized_players(client, app):
    owner = register(client, 'heart-owner@example.com', 'Owner')
    departed = register(client, 'heart-departed@example.com', 'Departed')
    blocked = register(client, 'heart-blocked@example.com', 'Blocked')
    deleted = register(client, 'heart-deleted@example.com', 'Deleted')
    game = completed_game(client, owner, [owner, departed, blocked, deleted])
    crew = create_crew(client, game, owner)
    for player in (departed, blocked, deleted):
        accept_crew(client, crew, player)

    sent = client.post(
        f"/api/crews/{crew['id']}/chat",
        json={'body': 'React to this'},
        headers=headers(owner),
    )
    assert sent.status_code == 201, sent.get_json()
    message_id = sent.get_json()['id']
    for reactor in (departed, blocked, deleted):
        reacted = client.post(
            f'/api/messages/{message_id}/heart', headers=headers(reactor),
        )
        assert reacted.status_code == 200, reacted.get_json()
    before = client.get(
        f"/api/crews/{crew['id']}/chat", headers=headers(owner),
    ).get_json()
    assert before['heart_counts'] == {str(message_id): 3}

    assert client.post(
        f"/api/crews/{crew['id']}/leave", headers=headers(departed),
    ).status_code == 200
    assert client.post(
        f"/api/users/{blocked['user']['id']}/block", headers=headers(owner),
    ).status_code == 200
    assert client.delete(
        '/api/me', json={'password': 'secret123'}, headers=headers(deleted),
    ).status_code == 200

    with app.app_context():
        assert MessageHeart.query.filter_by(
            user_id=deleted['user']['id'],
        ).count() == 0

    after = client.get(
        f"/api/crews/{crew['id']}/chat", headers=headers(owner),
    ).get_json()
    message = next(item for item in after['items'] if item['id'] == message_id)
    assert message['heart_user_ids'] == []
    assert message['heart_count'] == 0
    assert after['heart_counts'] == {str(message_id): 0}

    crew_list = client.get('/api/crews/mine', headers=headers(owner)).get_json()
    last_message = next(
        item['last_message'] for item in crew_list['items']
        if item['id'] == crew['id']
    )
    assert last_message['heart_user_ids'] == []
    assert last_message['heart_count'] == 0

    # The toggle response is a live count too; stale rows must not inflate it.
    owner_reaction = client.post(
        f'/api/messages/{message_id}/heart', headers=headers(owner),
    )
    assert owner_reaction.get_json() == {'hearted': True, 'heart_count': 1}
