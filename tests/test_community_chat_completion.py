"""End-to-end contracts for the consolidated Community conversation model."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    CheckIn, Court, Game, GamePlayer, Message, Notification,
    can_direct_message, utcnow,
)


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
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


def share_game(app, first, second, suffix):
    with app.app_context():
        court = Court(
            name=f'Shared Courts {suffix}', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82, num_courts=4,
        )
        db.session.add(court)
        db.session.flush()
        game = Game(
            court_id=court.id, creator_id=first['user']['id'],
            scheduled_at=utcnow() + timedelta(hours=2), status='completed',
        )
        db.session.add(game)
        db.session.flush()
        db.session.add_all([
            GamePlayer(game_id=game.id, user_id=first['user']['id']),
            GamePlayer(game_id=game.id, user_id=second['user']['id']),
        ])
        db.session.commit()


def test_single_inbox_keeps_direct_message_requests_and_partial_sources(
        client, app, monkeypatch):
    sender = register(client, 'sender@example.com', 'Sender')
    recipient = register(client, 'recipient@example.com', 'Recipient')
    share_game(app, sender, recipient, 'Inbox')
    sent = client.post(
        f"/api/chat/{recipient['user']['id']}",
        json={'body': 'Would you like to play tomorrow?'},
        headers=auth(sender),
    )
    assert sent.status_code == 201, sent.get_json()

    inbox = client.get('/api/inbox', headers=auth(recipient))
    assert inbox.status_code == 200, inbox.get_json()
    payload = inbox.get_json()
    assert set(payload) == {
        'direct', 'courts', 'clubs', 'competitions', 'crews', 'errors',
    }
    assert payload['errors'] == {}
    row = payload['direct']['items'][0]
    assert row['user']['id'] == sender['user']['id']
    assert row['unread'] == 1
    assert row['message_request'] is True

    import backend.routes.chat as chat_routes

    def failed_court_source():
        raise RuntimeError('simulated court source outage')

    monkeypatch.setattr(chat_routes, 'my_court_rooms', failed_court_source)
    partial = client.get('/api/inbox', headers=auth(recipient))
    assert partial.status_code == 200, partial.get_json()
    partial_payload = partial.get_json()
    assert partial_payload['errors'] == {'courts': 'unavailable'}
    assert partial_payload['courts'] == {'items': []}
    assert partial_payload['direct']['items'][0]['user']['id'] == sender['user']['id']


def test_direct_mute_suppresses_alerts_and_badges_until_unmuted(client, app):
    sender = register(client, 'sender@example.com', 'Sender')
    recipient = register(client, 'recipient@example.com', 'Recipient')
    share_game(app, sender, recipient, 'Mute')
    sender_id = sender['user']['id']

    muted = client.put(
        f'/api/chat/{sender_id}/settings', json={'muted': True},
        headers=auth(recipient),
    )
    assert muted.status_code == 200 and muted.get_json()['muted'] is True
    assert client.put(
        f'/api/chat/{sender_id}/settings', json={'muted': 'yes'},
        headers=auth(recipient),
    ).status_code == 400

    first = client.post(
        f"/api/chat/{recipient['user']['id']}", json={'body': 'Muted hello'},
        headers=auth(sender),
    )
    assert first.status_code == 201
    inbox = client.get('/api/inbox', headers=auth(recipient)).get_json()
    assert inbox['direct']['items'][0]['muted'] is True
    assert inbox['direct']['items'][0]['unread'] == 0
    assert client.get('/api/me', headers=auth(recipient)).get_json()['unread_messages'] == 0
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=recipient['user']['id'], kind='direct_message',
        ).count() == 0

    thread = client.get(f'/api/chat/{sender_id}', headers=auth(recipient)).get_json()
    assert thread['message_request'] is True
    assert thread['muted'] is True
    unmuted = client.put(
        f'/api/chat/{sender_id}/settings', json={'muted': False},
        headers=auth(recipient),
    )
    assert unmuted.status_code == 200 and unmuted.get_json()['muted'] is False

    second = client.post(
        f"/api/chat/{recipient['user']['id']}", json={'body': 'Visible hello'},
        headers=auth(sender),
    )
    assert second.status_code == 201
    assert client.get('/api/me', headers=auth(recipient)).get_json()['unread_messages'] == 1
    notices = client.get('/api/notifications', headers=auth(recipient)).get_json()['items']
    request_notice = next(item for item in notices if item['kind'] == 'direct_message')
    assert request_notice['title'] == 'Message request from Sender'
    assert request_notice['action_url'] == f'/#chat/{sender_id}'

    marked = client.post('/api/chat/read-all', json={}, headers=auth(recipient))
    assert marked.status_code == 200, marked.get_json()
    assert marked.get_json()['direct_messages_marked'] == 1
    assert client.get('/api/me', headers=auth(recipient)).get_json()['unread_messages'] == 0
    assert client.get('/api/inbox', headers=auth(recipient)).get_json()['direct']['items'][0]['unread'] == 0


def test_unsolicited_dm_is_rejected_without_a_shared_context(client):
    sender = register(client, 'sender@example.com', 'Sender')
    stranger = register(client, 'stranger@example.com', 'Stranger')
    stranger_id = stranger['user']['id']

    opened = client.get(f'/api/chat/{stranger_id}', headers=auth(sender))
    assert opened.status_code == 403
    assert opened.get_json()['error'] == 'message_not_allowed'
    sent = client.post(
        f'/api/chat/{stranger_id}', json={'body': 'Unsolicited hello'},
        headers=auth(sender),
    )
    assert sent.status_code == 403
    assert sent.get_json()['error'] == 'message_not_allowed'
    settings = client.put(
        f'/api/chat/{stranger_id}/settings', json={'muted': True},
        headers=auth(sender),
    )
    assert settings.status_code == 403
    assert settings.get_json()['error'] == 'message_not_allowed'


def test_only_time_overlapping_court_visits_create_message_context(client, app):
    first = register(client, 'first@example.com', 'First')
    overlap = register(client, 'overlap@example.com', 'Overlap')
    later = register(client, 'later@example.com', 'Later')
    now = utcnow()
    with app.app_context():
        court = Court(
            name='Overlap Park', city='Irvine', state='CA',
            latitude=33.67, longitude=-117.81, num_courts=4,
        )
        db.session.add(court)
        db.session.flush()
        db.session.add_all([
            CheckIn(
                user_id=first['user']['id'], court_id=court.id,
                checked_in_at=now - timedelta(hours=3),
                checked_out_at=now - timedelta(hours=2),
                last_presence_ping_at=now - timedelta(hours=2),
            ),
            CheckIn(
                user_id=overlap['user']['id'], court_id=court.id,
                checked_in_at=now - timedelta(hours=2, minutes=30),
                checked_out_at=now - timedelta(hours=1, minutes=30),
                last_presence_ping_at=now - timedelta(hours=1, minutes=30),
            ),
            CheckIn(
                user_id=later['user']['id'], court_id=court.id,
                checked_in_at=now - timedelta(hours=1),
                checked_out_at=now - timedelta(minutes=30),
                last_presence_ping_at=now - timedelta(minutes=30),
            ),
        ])
        db.session.commit()
        assert can_direct_message(first['user']['id'], overlap['user']['id']) is True
        assert can_direct_message(first['user']['id'], later['user']['id']) is False

    allowed = client.post(
        f"/api/chat/{overlap['user']['id']}", json={'body': 'Good game.'},
        headers=auth(first),
    )
    assert allowed.status_code == 201
    denied = client.post(
        f"/api/chat/{later['user']['id']}", json={'body': 'We were never there together.'},
        headers=auth(first),
    )
    assert denied.status_code == 403


def test_room_authors_can_react_to_their_own_message_but_dm_senders_cannot(
        client, app):
    author = register(client, 'author@example.com', 'Author')
    recipient = register(client, 'reader@example.com', 'Reader')
    with app.app_context():
        court = Court(
            name='Reaction Courts', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82, num_courts=4,
        )
        db.session.add(court)
        db.session.flush()
        game = Game(
            court_id=court.id, creator_id=author['user']['id'],
            scheduled_at=utcnow() + timedelta(hours=2),
        )
        db.session.add(game)
        db.session.flush()
        db.session.add(GamePlayer(game_id=game.id, user_id=author['user']['id']))
        room_message = Message(
            sender_id=author['user']['id'], game_id=game.id,
            body='I am excited to play.',
        )
        direct_message = Message(
            sender_id=author['user']['id'], recipient_id=recipient['user']['id'],
            body='This heart belongs to the recipient.',
        )
        db.session.add_all([room_message, direct_message])
        db.session.commit()
        room_message_id = room_message.id
        direct_message_id = direct_message.id

    reaction = client.post(
        f'/api/messages/{room_message_id}/heart', headers=auth(author),
    )
    assert reaction.status_code == 200
    assert reaction.get_json() == {'hearted': True, 'heart_count': 1}
    assert client.post(
        f'/api/messages/{direct_message_id}/heart', headers=auth(author),
    ).status_code == 403
    recipient_reaction = client.post(
        f'/api/messages/{direct_message_id}/heart', headers=auth(recipient),
    )
    assert recipient_reaction.status_code == 200
    assert recipient_reaction.get_json()['hearted'] is True
