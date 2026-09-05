"""Owner withdrawal releases pending consent without changing membership."""
from backend.app import db
from backend.models import Crew, CrewInvite, CrewMember, Notification
from tests.test_crew_community_api import (
    app, client, register, headers, make_friends, create_direct_crew, accept_crew,
)


def detail(client, owner, crew_id):
    response = client.get(f'/api/crews/{crew_id}', headers=headers(owner))
    assert response.status_code == 200
    return response.get_json()


def withdraw(client, owner, crew_id, invitation):
    return client.delete(f"/api/crews/{crew_id}/invites/{invitation['id']}",
                         json={'expected_invited_at': invitation['invited_at']},
                         headers=headers(owner))


def test_withdrawal_frees_full_group_capacity_and_retires_invitation(client, app):
    owner = register(client, 'withdraw-owner', 'Owner')
    players = [register(client, f'withdraw-{i}', f'Player {i}') for i in range(12)]
    for player in players:
        make_friends(client, owner, player)
    crew = create_direct_crew(client, owner, players[:11])['crew']
    crew_id = crew['id']
    before = detail(client, owner, crew_id)
    invitation = next(i for i in before['pending_invites'] if i['user']['id'] == players[0]['user']['id'])
    full = client.post(f'/api/crews/{crew_id}/invites', headers=headers(owner),
                       json={'invite_user_ids': [players[11]['user']['id']]})
    assert full.get_json()['invited_count'] == 0
    response = withdraw(client, owner, crew_id, invitation)
    assert response.status_code == 200
    assert withdraw(client, owner, crew_id, invitation).status_code == 200
    after = detail(client, owner, crew_id)
    assert after['pending_count'] == 10
    assert after['roster_version'] == before['roster_version']
    with app.app_context():
        assert db.session.get(CrewInvite, invitation['id']).status == 'revoked'
        assert Notification.query.filter_by(user_id=players[0]['user']['id'], related_crew_id=crew_id, kind='crew_invite').count() == 0
    accept_old = client.post(f'/api/crews/{crew_id}/respond', json={'accept': True}, headers=headers(players[0]))
    assert accept_old.status_code == 404
    replacement = client.post(f'/api/crews/{crew_id}/invites', headers=headers(owner),
                              json={'invite_user_ids': [players[11]['user']['id']]})
    assert replacement.get_json()['invited_count'] == 1
    assert detail(client, owner, crew_id)['pending_count'] == 11


def test_members_and_strangers_cannot_withdraw_and_joined_players_stay_members(client, app):
    owner = register(client, 'owner', 'Owner')
    alice = register(client, 'alice', 'Alice')
    ben = register(client, 'ben', 'Ben')
    stranger = register(client, 'stranger', 'Stranger')
    for person in (alice, ben):
        make_friends(client, owner, person)
    crew_id = create_direct_crew(client, owner, (alice, ben))['crew']['id']
    invites = {i['user']['id']: i for i in detail(client, owner, crew_id)['pending_invites']}
    accept_crew(client, crew_id, alice)
    for actor in (alice, ben, stranger):
        assert withdraw(client, actor, crew_id, invites[ben['user']['id']]).status_code == 404
    response = withdraw(client, owner, crew_id, invites[alice['user']['id']])
    assert response.status_code == 409
    assert response.get_json()['error'] == 'crew_invitation_changed'
    with app.app_context():
        assert CrewMember.query.filter_by(crew_id=crew_id, user_id=alice['user']['id']).count() == 1
        assert db.session.get(CrewInvite, invites[ben['user']['id']]['id']).status == 'pending'


def test_stale_withdrawal_cannot_cancel_a_new_invitation_using_the_same_id(client):
    owner = register(client, 'owner', 'Owner')
    alice = register(client, 'alice', 'Alice')
    make_friends(client, owner, alice)
    crew_id = create_direct_crew(client, owner, (alice,))['crew']['id']
    old = detail(client, owner, crew_id)['pending_invites'][0]
    assert withdraw(client, owner, crew_id, old).status_code == 200
    resent = client.post(f'/api/crews/{crew_id}/invites', headers=headers(owner),
                         json={'invite_user_ids': [alice['user']['id']]})
    assert resent.get_json()['invited_count'] == 1
    fresh = detail(client, owner, crew_id)['pending_invites'][0]
    assert fresh['id'] == old['id']
    assert fresh['invited_at'] != old['invited_at']
    assert withdraw(client, owner, crew_id, old).status_code == 409
    assert accept_crew(client, crew_id, alice)['member_count'] == 2


def test_withdrawal_requires_auth_current_version_and_matching_group(client):
    owner = register(client, 'owner', 'Owner')
    alice = register(client, 'alice', 'Alice')
    make_friends(client, owner, alice)
    first_id = create_direct_crew(client, owner, (alice,))['crew']['id']
    second_id = create_direct_crew(client, owner, (alice,), name='Another group')['crew']['id']
    invitation = detail(client, owner, first_id)['pending_invites'][0]
    route = f"/api/crews/{first_id}/invites/{invitation['id']}"
    assert client.delete(route).status_code == 401
    assert client.delete(route, json={}, headers=headers(owner)).status_code == 400
    assert withdraw(client, owner, second_id, invitation).status_code == 404
    assert detail(client, owner, first_id)['pending_count'] == 1
