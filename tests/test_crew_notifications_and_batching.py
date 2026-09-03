"""Crew alert preferences and bounded inbox-query coverage."""

from __future__ import annotations

import pytest
from sqlalchemy import event, inspect, text

from backend.app import _upgrade_schema, create_app, db
from backend.models import (
    Crew, CrewChatRead, CrewInvite, CrewMember, Message, MessageHeart,
    Notification,
)


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
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
    accepted = client.post(
        f"/api/friends/{requested.get_json()['friendship_id']}/respond",
        json={'accept': True}, headers=headers(addressee),
    )
    assert accepted.status_code == 200, accepted.get_json()


def create_joined_crew(client, owner, member):
    make_friends(client, owner, member)
    created = client.post('/api/crews', json={
        'name': 'Quiet Wednesday Group',
        'invite_user_ids': [member['user']['id']],
    }, headers=headers(owner))
    assert created.status_code == 201, created.get_json()
    crew_id = created.get_json()['crew']['id']
    joined = client.post(
        f'/api/crews/{crew_id}/respond', json={'accept': True},
        headers=headers(member),
    )
    assert joined.status_code == 200, joined.get_json()
    return crew_id


def test_crew_notification_levels_cover_owner_and_member_and_preserve_read_marker(
        client, app):
    owner = register(client, 'crew-alert-owner', 'Owner Person')
    member = register(client, 'crew-alert-member', 'Member Person')
    outsider = register(client, 'crew-alert-outsider', 'Outsider Person')
    crew_id = create_joined_crew(client, owner, member)

    for player in (owner, member):
        detail = client.get(
            f'/api/crews/{crew_id}', headers=headers(player),
        ).get_json()
        assert detail['my_notification_level'] == 'all'
        assert detail['notifications_muted'] is False

    mentions = client.patch(
        f'/api/crews/{crew_id}/notification-settings',
        json={'level': 'mentions'}, headers=headers(member),
    )
    assert mentions.status_code == 200, mentions.get_json()
    assert mentions.get_json() == {'level': 'mentions', 'muted': False}

    generic = client.post(
        f'/api/crews/{crew_id}/chat', json={'body': 'Court at six?'},
        headers=headers(owner),
    )
    assert generic.status_code == 201, generic.get_json()
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=member['user']['id'], kind='crew_message',
        ).count() == 0

    mentioned = client.post(
        f'/api/crews/{crew_id}/chat',
        json={'body': '@Member can you bring pickleballs?'},
        headers=headers(owner),
    )
    assert mentioned.status_code == 201, mentioned.get_json()
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=member['user']['id'], kind='crew_message',
        ).count() == 1

    read = client.get(
        f'/api/crews/{crew_id}/chat', headers=headers(member),
    )
    assert read.status_code == 200, read.get_json()
    newest_id = max(item['id'] for item in read.get_json()['items'])
    muted = client.patch(
        f'/api/crews/{crew_id}/notification-settings',
        json={'level': 'off'}, headers=headers(member),
    )
    assert muted.get_json() == {'level': 'off', 'muted': True}
    with app.app_context():
        preference = CrewChatRead.query.filter_by(
            crew_id=crew_id, user_id=member['user']['id'],
        ).one()
        assert preference.last_read_message_id == newest_id
        assert preference.notification_level == 'off'
        Notification.query.filter_by(kind='crew_message').delete()
        db.session.commit()

    still_quiet = client.post(
        f'/api/crews/{crew_id}/chat',
        json={'body': '@Member this is intentionally quiet'},
        headers=headers(owner),
    )
    assert still_quiet.status_code == 201, still_quiet.get_json()
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=member['user']['id'], kind='crew_message',
        ).count() == 0

    owner_muted = client.patch(
        f'/api/crews/{crew_id}/notification-settings',
        json={'level': 'off'}, headers=headers(owner),
    )
    assert owner_muted.status_code == 200, owner_muted.get_json()
    assert client.post(
        f'/api/crews/{crew_id}/chat', json={'body': '@Owner are you there?'},
        headers=headers(member),
    ).status_code == 201
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=owner['user']['id'], kind='crew_message',
        ).count() == 0

    mine = client.get('/api/crews/mine', headers=headers(member)).get_json()
    assert mine['items'][0]['my_notification_level'] == 'off'
    assert mine['items'][0]['notifications_muted'] is True
    invalid = client.patch(
        f'/api/crews/{crew_id}/notification-settings',
        json={'level': 'sometimes'}, headers=headers(member),
    )
    assert invalid.status_code == 400
    assert invalid.get_json() == {'error': 'invalid_notification_level'}
    assert client.patch(
        f'/api/crews/{crew_id}/notification-settings',
        json={'level': 'off'}, headers=headers(outsider),
    ).status_code == 404


def test_crews_mine_query_count_does_not_grow_per_crew(client, app):
    owner = register(client, 'batch-owner', 'Batch Owner')
    member = register(client, 'batch-member', 'Batch Member')
    invitee = register(client, 'batch-invitee', 'Batch Invitee')
    owner_id = owner['user']['id']
    member_id = member['user']['id']
    invitee_id = invitee['user']['id']

    def add_crews(count, offset=0):
        for index in range(offset, offset + count):
            crew = Crew(owner_id=owner_id, name=f'Batch Group {index}')
            db.session.add(crew)
            db.session.flush()
            db.session.add(CrewMember(crew_id=crew.id, user_id=member_id))
            db.session.add(CrewInvite(
                crew_id=crew.id, invitee_id=invitee_id,
                invited_by_id=owner_id, status='pending',
            ))
            message = Message(
                crew_id=crew.id, sender_id=member_id,
                body=f'Latest group update {index}',
            )
            db.session.add(message)
            db.session.flush()
            db.session.add(MessageHeart(message_id=message.id, user_id=owner_id))
        db.session.commit()
        db.session.remove()

    def measured_mine(player=owner):
        statements = []

        def record_statement(_conn, _cursor, statement, _parameters, _context,
                             _executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement)

        event.listen(db.engine, 'before_cursor_execute', record_statement)
        try:
            response = client.get('/api/crews/mine', headers=headers(player))
        finally:
            event.remove(db.engine, 'before_cursor_execute', record_statement)
        assert response.status_code == 200, response.get_json()
        return response.get_json(), len(statements)

    with app.app_context():
        add_crews(1)
        one, one_count = measured_mine()
        assert one['items'][0]['unread'] == 1
        assert one['items'][0]['last_message']['heart_count'] == 1
        assert len(one['items'][0]['pending_invites']) == 1
        one_invited, one_invited_count = measured_mine(invitee)
        assert len(one_invited['invitations']) == 1

        add_crews(7, offset=1)
        many, many_count = measured_mine()
        assert len(many['items']) == 8
        assert all(item['unread'] == 1 for item in many['items'])
        assert all(
            item['last_message']['heart_count'] == 1
            for item in many['items']
        )
        assert all(len(item['pending_invites']) == 1 for item in many['items'])
        many_invited, many_invited_count = measured_mine(invitee)
        assert len(many_invited['invitations']) == 8

    # Relationship loads, latest messages, reactions, unread counts, and
    # pending invites are set based. Adding seven rooms must not add queries
    # one room at a time.
    assert many_count <= one_count + 2, (one_count, many_count)
    assert many_invited_count <= one_invited_count + 2, (
        one_invited_count, many_invited_count,
    )


def test_additive_upgrade_restores_crew_notification_level(client, app):
    owner = register(client, 'migration-owner', 'Migration Owner')
    owner_id = owner['user']['id']
    with app.app_context():
        crew = Crew(owner_id=owner_id, name='Existing Crew')
        db.session.add(crew)
        db.session.flush()
        db.session.add(CrewChatRead(
            crew_id=crew.id, user_id=owner_id,
            last_read_message_id=17, notification_level='mentions',
        ))
        db.session.commit()
        db.session.remove()

        with db.engine.begin() as connection:
            connection.execute(text(
                'ALTER TABLE crew_chat_read DROP COLUMN notification_level'
            ))
        assert 'notification_level' not in {
            column['name']
            for column in inspect(db.engine).get_columns('crew_chat_read')
        }

        _upgrade_schema(app)

        reflected = {
            column['name']: column
            for column in inspect(db.engine).get_columns('crew_chat_read')
        }
        assert reflected['notification_level']['nullable'] is False
        row = db.session.execute(text(
            'SELECT last_read_message_id, notification_level '
            'FROM crew_chat_read'
        )).one()
        assert tuple(row) == (17, 'all')
