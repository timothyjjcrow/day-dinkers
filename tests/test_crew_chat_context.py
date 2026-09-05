"""Chat reads keep the group's current membership context without exposing it to outsiders."""
from tests.test_crew_community_api import (
    app, client, register, headers, make_friends, create_direct_crew, accept_crew,
    court_ids, scheduled_payload,
)
from datetime import timedelta

from backend.app import db
from backend.models import Game, utcnow


def test_chat_context_tracks_pending_and_joined_players(client):
    owner = register(client, 'chat-context-owner', 'Owner')
    player = register(client, 'chat-context-player', 'Player')
    make_friends(client, owner, player)
    crew_id = create_direct_crew(client, owner, [player])['crew']['id']
    route = f'/api/crews/{crew_id}/chat'
    before = client.get(route, headers=headers(owner)).get_json()
    assert before['crew']['member_count'] == 1
    assert before['crew']['pending_count'] == 1
    assert before['crew']['is_owner'] is True
    assert before['items'] == []
    assert client.get(route, headers=headers(player)).status_code == 404
    accept_crew(client, crew_id, player)
    after = client.get(route + '?since_id=0', headers=headers(owner)).get_json()
    assert after['crew']['member_count'] == 2
    assert after['crew']['pending_count'] == 0
    joined = client.get(route, headers=headers(player)).get_json()
    assert joined['crew']['joined'] is True
    assert joined['crew']['is_owner'] is False


def test_chat_next_session_tracks_rsvp_and_moves_past_cancelled_or_old_games(client, app):
    owner = register(client, 'next-owner', 'Owner')
    member = register(client, 'next-member', 'Member')
    make_friends(client, owner, member)
    crew = create_direct_crew(client, owner, [member])['crew']
    crew = accept_crew(client, crew['id'], member)
    route = f"/api/crews/{crew['id']}/chat"
    assert client.get(route, headers=headers(owner)).get_json()['next_game'] is None
    ids = []
    for days in (1, 2):
        payload = scheduled_payload(court_ids(client)['Community Court'], crew, 'private', suffix=f'next-{days}')
        payload['scheduled_at'] = (utcnow() + timedelta(days=days)).isoformat() + 'Z'
        response = client.post('/api/games', json=payload, headers=headers(owner))
        assert response.status_code == 201, response.get_json()
        ids.append(response.get_json()['id'])
    read = lambda player: client.get(route + '?since_id=0', headers=headers(player)).get_json()['next_game']
    assert read(owner)['id'] == ids[0]
    assert read(owner)['is_joined'] is True
    assert read(member)['is_invited'] is True
    assert read(member)['is_joined'] is False
    assert read(member)['court']['name'] == 'Community Court'
    joined = client.post(f'/api/games/{ids[0]}/join', headers=headers(member))
    assert joined.status_code == 200, joined.get_json()
    assert read(member)['is_joined'] is True
    with app.app_context():
        db.session.get(Game, ids[0]).status = 'cancelled'
        db.session.commit()
    assert read(owner)['id'] == ids[1]
    with app.app_context():
        db.session.get(Game, ids[1]).scheduled_at = utcnow() - timedelta(hours=1)
        db.session.commit()
    assert read(owner) is None


def test_chat_next_session_skips_private_snapshots_and_revokes_access_after_host_block(client):
    owner = register(client, 'next-private-owner', 'Owner')
    member = register(client, 'next-private-member', 'Member')
    late = register(client, 'next-private-late', 'Later member')
    for player in (member, late):
        make_friends(client, owner, player)
    crew = create_direct_crew(client, owner, [member])['crew']
    crew = accept_crew(client, crew['id'], member)
    games = []
    for days, visibility in enumerate(('private', 'friends', 'open'), start=1):
        payload = scheduled_payload(court_ids(client)['Community Court'], crew, visibility, suffix=f'next-{visibility}')
        payload['scheduled_at'] = (utcnow() + timedelta(days=days)).isoformat() + 'Z'
        response = client.post('/api/games', json=payload, headers=headers(owner))
        assert response.status_code == 201, response.get_json()
        games.append(response.get_json()['id'])
    invited = client.post(f"/api/crews/{crew['id']}/invites", json={
        'invite_user_ids': [late['user']['id']],
    }, headers=headers(owner))
    assert invited.status_code == 200, invited.get_json()
    route = f"/api/crews/{crew['id']}/chat"
    assert client.get(route, headers=headers(late)).status_code == 404
    accept_crew(client, crew['id'], late)
    assert client.get(route, headers=headers(member)).get_json()['next_game']['id'] == games[0]
    assert client.get(route, headers=headers(late)).get_json()['next_game']['id'] == games[1]
    blocked = client.post(f"/api/users/{owner['user']['id']}/block", headers=headers(late))
    assert blocked.status_code == 200, blocked.get_json()
    assert client.get(route, headers=headers(late)).status_code == 404


def test_chat_hides_a_session_hosted_by_a_blocked_former_group_member(client):
    owner = register(client, 'next-block-owner', 'Owner')
    host = register(client, 'next-block-host', 'Host')
    make_friends(client, owner, host)
    crew = create_direct_crew(client, owner, [host])['crew']
    crew = accept_crew(client, crew['id'], host)
    response = client.post('/api/games', json=scheduled_payload(
        court_ids(client)['Community Court'], crew, 'private', suffix='blocked-host',
    ), headers=headers(host))
    assert response.status_code == 201, response.get_json()
    route = f"/api/crews/{crew['id']}/chat"
    assert client.get(route, headers=headers(owner)).get_json()['next_game']['id'] == response.get_json()['id']
    left = client.post(f"/api/crews/{crew['id']}/leave", headers=headers(host))
    assert left.status_code == 200, left.get_json()
    blocked = client.post(f"/api/users/{host['user']['id']}/block", headers=headers(owner))
    assert blocked.status_code == 200, blocked.get_json()
    result = client.get(route, headers=headers(owner))
    assert result.status_code == 200
    assert result.get_json()['next_game'] is None
