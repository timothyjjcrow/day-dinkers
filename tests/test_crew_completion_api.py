"""Completion coverage for Crew badges, upcoming play, and member removal."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court,
    Crew,
    CrewChatRead,
    CrewInvite,
    CrewMember,
    Notification,
    utcnow,
)


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Crew Completion Court',
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


def create_crew(client, owner, invitees=()):
    response = client.post('/api/crews', json={
        'name': 'Crew Completion Group',
        'invite_user_ids': [player['user']['id'] for player in invitees],
    }, headers=headers(owner))
    assert response.status_code == 201, response.get_json()
    return response.get_json()['crew']


def accept_crew(client, crew_id, player):
    response = client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True},
        headers=headers(player),
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()['crew']


def invite_to_crew(client, crew_id, owner, player):
    response = client.post(
        f'/api/crews/{crew_id}/invites',
        json={'invite_user_ids': [player['user']['id']]},
        headers=headers(owner),
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['invited_count'] == 1
    return response.get_json()['crew']


def schedule_crew_game(client, owner, crew, court_id, visibility, suffix):
    response = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': visibility,
        'max_players': 12,
        'crew_id': crew['id'],
        'expected_crew_version': crew['roster_version'],
        'client_attempt_id': f'crew-completion-{suffix}',
    }, headers=headers(owner))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_me_crew_invites_are_durable_actionable_and_badgeable(client, app):
    owner = register(client, 'me-crew-owner', 'Owner')
    invitee = register(client, 'me-crew-invitee', 'Invitee')
    make_friends(client, owner, invitee)

    assert client.get('/api/me', headers=headers(invitee)).get_json()[
        'pending_crew_invites'
    ] == {'count': 0, 'items': []}

    crew = create_crew(client, owner, (invitee,))
    with app.app_context():
        invite = CrewInvite.query.filter_by(
            crew_id=crew['id'], invitee_id=invitee['user']['id'],
        ).one()
        durable_invite_id = invite.id

    pending = client.get('/api/me', headers=headers(invitee)).get_json()[
        'pending_crew_invites'
    ]
    assert pending['count'] == 1
    assert pending['items'] == [{
        'invite_id': durable_invite_id,
        'crew_id': crew['id'],
        'crew_name': 'Crew Completion Group',
        'owner_id': owner['user']['id'],
        'invited_by_id': owner['user']['id'],
        'invited_by_name': 'Owner',
        'source_game_id': None,
        'default_court_id': None,
        'default_court_name': None,
        'invited_at': pending['items'][0]['invited_at'],
    }]
    assert pending['items'][0]['invited_at'].endswith('Z')

    # Reading and clearing delivery hints must not clear durable consent.
    assert client.post(
        '/api/notifications/read', headers=headers(invitee),
    ).status_code == 200
    assert client.delete(
        '/api/notifications', headers=headers(invitee),
    ).status_code == 200
    after_clear = client.get('/api/me', headers=headers(invitee)).get_json()[
        'pending_crew_invites'
    ]
    assert after_clear['count'] == 1
    assert after_clear['items'][0]['invite_id'] == durable_invite_id

    declined = client.post(
        f"/api/crews/{crew['id']}/respond",
        json={'accept': False},
        headers=headers(invitee),
    )
    assert declined.status_code == 200, declined.get_json()
    assert client.get('/api/me', headers=headers(invitee)).get_json()[
        'pending_crew_invites'
    ] == {'count': 0, 'items': []}

    invite_to_crew(client, crew['id'], owner, invitee)
    reopened = client.get('/api/me', headers=headers(invitee)).get_json()[
        'pending_crew_invites'
    ]
    assert reopened['count'] == 1
    assert reopened['items'][0]['invite_id'] == durable_invite_id
    accept_crew(client, crew['id'], invitee)
    assert client.get('/api/me', headers=headers(invitee)).get_json()[
        'pending_crew_invites'
    ] == {'count': 0, 'items': []}


def test_crew_detail_serializes_only_visible_upcoming_play(client, app):
    owner = register(client, 'upcoming-owner', 'Owner')
    member = register(client, 'upcoming-member', 'Member')
    later_member = register(client, 'upcoming-later', 'Later Member')
    make_friends(client, owner, member)
    make_friends(client, owner, later_member)
    crew = create_crew(client, owner, (member,))
    crew = accept_crew(client, crew['id'], member)
    with app.app_context():
        court_id = Court.query.filter_by(name='Crew Completion Court').one().id

    private_game = schedule_crew_game(
        client, owner, crew, court_id, 'private', 'private',
    )
    friends_game = schedule_crew_game(
        client, owner, crew, court_id, 'friends', 'friends',
    )
    open_game = schedule_crew_game(
        client, owner, crew, court_id, 'open', 'open',
    )

    crew = invite_to_crew(client, crew['id'], owner, later_member)
    accept_crew(client, crew['id'], later_member)

    owner_detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(owner),
    ).get_json()
    assert {game['id'] for game in owner_detail['upcoming_games']} == {
        private_game['id'], friends_game['id'], open_game['id'],
    }

    original_member_detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(member),
    ).get_json()
    assert {game['id'] for game in original_member_detail['upcoming_games']} == {
        private_game['id'], friends_game['id'], open_game['id'],
    }

    later_detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(later_member),
    ).get_json()
    later_ids = {game['id'] for game in later_detail['upcoming_games']}
    # A friends/open audience is still honored, but accepting a Crew invitation
    # does not rewrite the immutable snapshot for an older private session.
    assert later_ids == {friends_game['id'], open_game['id']}
    assert all(
        game['crew_id'] == crew['id']
        for game in later_detail['upcoming_games']
    )
    assert all(
        game['crew_name'] == 'Crew Completion Group'
        for game in later_detail['upcoming_games']
    )


def test_only_owner_can_remove_nonowner_and_consent_cannot_be_reused(client, app):
    owner = register(client, 'remove-owner', 'Owner')
    target = register(client, 'remove-target', 'Target')
    remaining = register(client, 'remove-remaining', 'Remaining')
    outsider = register(client, 'remove-outsider', 'Outsider')
    for player in (target, remaining):
        make_friends(client, owner, player)
    crew = create_crew(client, owner, (target, remaining))
    accept_crew(client, crew['id'], target)
    crew = accept_crew(client, crew['id'], remaining)
    version_before = crew['roster_version']

    with app.app_context():
        db.session.add(CrewChatRead(
            crew_id=crew['id'], user_id=target['user']['id'],
            last_read_message_id=0,
        ))
        db.session.commit()

    endpoint = f"/api/crews/{crew['id']}/members/{target['user']['id']}"
    assert client.delete(endpoint).status_code == 401
    hidden = {'error': 'crew_not_found'}
    nonowner = client.delete(endpoint, headers=headers(remaining))
    assert nonowner.status_code == 404
    assert nonowner.get_json() == hidden
    unrelated = client.delete(endpoint, headers=headers(outsider))
    assert unrelated.status_code == 404
    assert unrelated.get_json() == hidden
    self_remove = client.delete(
        f"/api/crews/{crew['id']}/members/{owner['user']['id']}",
        headers=headers(owner),
    )
    assert self_remove.status_code == 404
    assert self_remove.get_json() == hidden

    removed = client.delete(endpoint, headers=headers(owner))
    assert removed.status_code == 200, removed.get_json()
    assert removed.get_json() == {'removed': True}

    owner_detail = client.get(
        f"/api/crews/{crew['id']}", headers=headers(owner),
    ).get_json()
    assert owner_detail['roster_version'] == version_before + 1
    assert {row['id'] for row in owner_detail['members']} == {
        owner['user']['id'], remaining['user']['id'],
    }
    assert client.get(
        f"/api/crews/{crew['id']}", headers=headers(target),
    ).status_code == 404
    assert client.post(
        f"/api/crews/{crew['id']}/respond",
        json={'accept': True},
        headers=headers(target),
    ).status_code == 404
    assert client.delete(endpoint, headers=headers(owner)).status_code == 404

    with app.app_context():
        stored = db.session.get(Crew, crew['id'])
        assert stored.owner_id == owner['user']['id']
        assert stored.roster_version == version_before + 1
        assert CrewMember.query.filter_by(
            crew_id=crew['id'], user_id=target['user']['id'],
        ).count() == 0
        assert CrewMember.query.filter_by(
            crew_id=crew['id'], user_id=remaining['user']['id'],
        ).count() == 1
        invite = CrewInvite.query.filter_by(
            crew_id=crew['id'], invitee_id=target['user']['id'],
        ).one()
        assert invite.status == 'revoked'
        assert invite.resolved_at is not None
        assert CrewChatRead.query.filter_by(
            crew_id=crew['id'], user_id=target['user']['id'],
        ).count() == 0
        notices = Notification.query.filter_by(
            user_id=target['user']['id'],
            related_crew_id=crew['id'],
            kind='crew_update',
        ).all()
        assert len(notices) == 1
        assert notices[0].related_user_id == owner['user']['id']
        assert notices[0].title == (
            'You were removed from the Crew Completion Group play group'
        )
        assert notices[0].action_url == ''
