"""Tests for chat and presence routes."""
import json
from datetime import datetime, timedelta, timezone
from backend.app import db
from backend.models import CheckIn, Message, PlaySession
from backend.routes.chat import _authorize_socket_join
from backend.time_utils import utcnow_naive


def _auth(client, username='chatuser', email='chat@test.com'):
    res = client.post('/api/auth/register', json={
        'username': username, 'email': email, 'password': 'password123',
    })
    return json.loads(res.data)['token']


def _auth_with_user(client, username='chatuser', email='chat@test.com'):
    res = client.post('/api/auth/register', json={
        'username': username, 'email': email, 'password': 'password123',
    })
    payload = json.loads(res.data)
    return payload['token'], payload['user']


def _create_court(client, token):
    res = client.post('/api/courts', json={
        'name': 'Chat Court', 'latitude': 40.80, 'longitude': -124.16,
        'city': 'Eureka',
    }, headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['court']


def _create_game(client, token, court_id):
    res = client.post('/api/games', json={
        'title': 'Chat Game', 'court_id': court_id,
        'date_time': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }, headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['game']


def _make_friends(client, requester_token, requester_id, recipient_token, recipient_id):
    sent = client.post('/api/auth/friends/request', json={
        'friend_id': recipient_id,
    }, headers={'Authorization': f'Bearer {requester_token}'})
    assert sent.status_code == 201

    pending = client.get('/api/auth/friends/pending', headers={
        'Authorization': f'Bearer {recipient_token}',
    })
    assert pending.status_code == 200
    requests = json.loads(pending.data)['requests']
    request_row = next((r for r in requests if r['user']['id'] == requester_id), None)
    assert request_row is not None

    accepted = client.post('/api/auth/friends/respond', json={
        'friendship_id': request_row['id'],
        'action': 'accept',
    }, headers={'Authorization': f'Bearer {recipient_token}'})
    assert accepted.status_code == 200


# ── Chat Tests ─────────────────────────────────────
def test_game_chat(client):
    token = _auth(client)
    court = _create_court(client, token)
    game = _create_game(client, token, court['id'])

    # Send a message
    res = client.post('/api/chat/send', json={
        'content': 'Hello game chat!', 'game_id': game['id'], 'msg_type': 'game',
    }, headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 201

    # Get messages
    res = client.get(f'/api/chat/game/{game["id"]}',
        headers={'Authorization': f'Bearer {token}'})
    data = json.loads(res.data)
    assert len(data['messages']) == 1
    assert data['messages'][0]['content'] == 'Hello game chat!'


def test_court_chat(client):
    token = _auth(client)
    court = _create_court(client, token)

    res = client.post('/api/chat/send', json={
        'content': 'Anyone at this court?', 'court_id': court['id'], 'msg_type': 'court',
    }, headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 201

    res = client.get(f'/api/chat/court/{court["id"]}',
        headers={'Authorization': f'Bearer {token}'})
    data = json.loads(res.data)
    assert len(data['messages']) == 1


def test_court_chat_prunes_messages_older_than_retention_window(client, app):
    token, user = _auth_with_user(client, 'chatretention', 'chatretention@test.com')
    court = _create_court(client, token)
    headers = {'Authorization': f'Bearer {token}'}

    with app.app_context():
        stale = Message(
            sender_id=user['id'],
            court_id=court['id'],
            msg_type='court',
            content='stale-court-message',
            created_at=utcnow_naive() - timedelta(days=8),
        )
        fresh = Message(
            sender_id=user['id'],
            court_id=court['id'],
            msg_type='court',
            content='fresh-court-message',
            created_at=utcnow_naive() - timedelta(days=1),
        )
        db.session.add(stale)
        db.session.add(fresh)
        db.session.commit()

    res = client.get(f'/api/chat/court/{court["id"]}', headers=headers)
    assert res.status_code == 200
    messages = json.loads(res.data)['messages']
    contents = [m['content'] for m in messages]
    assert 'fresh-court-message' in contents
    assert 'stale-court-message' not in contents

    with app.app_context():
        stale_row = Message.query.filter_by(content='stale-court-message').first()
        assert stale_row is None


def test_court_chat_returns_latest_messages_in_ascending_order(client, app):
    token, user = _auth_with_user(client, 'chatlatest', 'chatlatest@test.com')
    court = _create_court(client, token)
    headers = {'Authorization': f'Bearer {token}'}

    with app.app_context():
        base_time = utcnow_naive() - timedelta(minutes=10)
        for i in range(105):
            db.session.add(Message(
                sender_id=user['id'],
                court_id=court['id'],
                msg_type='court',
                content=f'court-msg-{i}',
                created_at=base_time + timedelta(seconds=i),
            ))
        db.session.commit()

    res = client.get(f'/api/chat/court/{court["id"]}', headers=headers)
    assert res.status_code == 200
    messages = json.loads(res.data)['messages']
    assert len(messages) == 100
    assert messages[0]['content'] == 'court-msg-5'
    assert messages[-1]['content'] == 'court-msg-104'


def test_session_chat_is_scoped_and_separate_from_court_chat(client):
    token = _auth(client, 'sessionchat', 'sessionchat@test.com')
    court = _create_court(client, token)
    headers = {'Authorization': f'Bearer {token}'}

    start_one = utcnow_naive() + timedelta(hours=2)
    end_one = start_one + timedelta(hours=1)
    session_one_res = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'scheduled',
        'start_time': start_one.isoformat(),
        'end_time': end_one.isoformat(),
    }, headers=headers)
    assert session_one_res.status_code == 201
    session_one_id = json.loads(session_one_res.data)['session']['id']

    start_two = utcnow_naive() + timedelta(hours=5)
    end_two = start_two + timedelta(hours=1)
    session_two_res = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'scheduled',
        'start_time': start_two.isoformat(),
        'end_time': end_two.isoformat(),
    }, headers=headers)
    assert session_two_res.status_code == 201
    session_two_id = json.loads(session_two_res.data)['session']['id']

    session_msg_one = client.post('/api/chat/send', json={
        'content': 'Session one hello',
        'msg_type': 'session',
        'session_id': session_one_id,
    }, headers=headers)
    assert session_msg_one.status_code == 201
    assert json.loads(session_msg_one.data)['message']['session_id'] == session_one_id

    session_msg_two = client.post('/api/chat/send', json={
        'content': 'Session two hello',
        'msg_type': 'session',
        'session_id': session_two_id,
    }, headers=headers)
    assert session_msg_two.status_code == 201
    assert json.loads(session_msg_two.data)['message']['session_id'] == session_two_id

    court_msg = client.post('/api/chat/send', json={
        'content': 'General court hello',
        'msg_type': 'court',
        'court_id': court['id'],
    }, headers=headers)
    assert court_msg.status_code == 201
    assert json.loads(court_msg.data)['message']['msg_type'] == 'court'

    session_one_msgs = client.get(
        f'/api/chat/session/{session_one_id}',
        headers=headers,
    )
    assert session_one_msgs.status_code == 200
    session_one_data = json.loads(session_one_msgs.data)['messages']
    assert len(session_one_data) == 1
    assert session_one_data[0]['content'] == 'Session one hello'
    assert session_one_data[0]['msg_type'] == 'session'
    assert session_one_data[0]['session_id'] == session_one_id

    session_two_msgs = client.get(
        f'/api/chat/session/{session_two_id}',
        headers=headers,
    )
    assert session_two_msgs.status_code == 200
    session_two_data = json.loads(session_two_msgs.data)['messages']
    assert len(session_two_data) == 1
    assert session_two_data[0]['content'] == 'Session two hello'
    assert session_two_data[0]['msg_type'] == 'session'
    assert session_two_data[0]['session_id'] == session_two_id

    court_msgs = client.get(
        f'/api/chat/court/{court["id"]}',
        headers=headers,
    )
    assert court_msgs.status_code == 200
    court_data = json.loads(court_msgs.data)['messages']
    assert len(court_data) == 1
    assert court_data[0]['content'] == 'General court hello'
    assert court_data[0]['msg_type'] == 'court'
    assert court_data[0]['session_id'] is None


def test_session_chat_requires_session_id(client):
    token = _auth(client, 'sessionmissing', 'sessionmissing@test.com')
    res = client.post('/api/chat/send', json={
        'content': 'Missing session id',
        'msg_type': 'session',
    }, headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 400


def test_direct_message(client):
    token1, user1 = _auth_with_user(client, 'dm1', 'dm1@test.com')
    token2, user2 = _auth_with_user(client, 'dm2', 'dm2@test.com')
    uid2 = user2['id']
    _make_friends(client, token1, user1['id'], token2, uid2)

    res = client.post('/api/chat/send', json={
        'content': 'Hey!', 'recipient_id': uid2, 'msg_type': 'direct',
    }, headers={'Authorization': f'Bearer {token1}'})
    assert res.status_code == 201

    res = client.get(f'/api/chat/direct/{uid2}',
        headers={'Authorization': f'Bearer {token1}'})
    data = json.loads(res.data)
    assert len(data['messages']) == 1
    assert 'email' not in data['messages'][0]['sender']


def test_direct_message_requires_friendship_for_send_and_history(client):
    token1, _ = _auth_with_user(client, 'dm_block_1', 'dm_block_1@test.com')
    _, user2 = _auth_with_user(client, 'dm_block_2', 'dm_block_2@test.com')

    send = client.post('/api/chat/send', json={
        'content': 'Should not send',
        'recipient_id': user2['id'],
        'msg_type': 'direct',
    }, headers={'Authorization': f'Bearer {token1}'})
    assert send.status_code == 403

    fetch = client.get(
        f'/api/chat/direct/{user2["id"]}',
        headers={'Authorization': f'Bearer {token1}'},
    )
    assert fetch.status_code == 403


def test_socket_user_room_join_requires_matching_user_token(client, app):
    token1, user1 = _auth_with_user(client, 'socket_room_u1', 'socket_room_u1@test.com')
    _, user2 = _auth_with_user(client, 'socket_room_u2', 'socket_room_u2@test.com')

    with app.app_context():
        _, forbidden_error = _authorize_socket_join(f'user_{user2["id"]}', token1)
        assert forbidden_error == 'Forbidden room'

        _, allowed_error = _authorize_socket_join(f'user_{user1["id"]}', token1)
        assert allowed_error is None


# ── Presence Tests ─────────────────────────────────
def test_check_in_out(client):
    token = _auth(client, 'presuser', 'pres@test.com')
    court = _create_court(client, token)

    # Check in
    res = client.post('/api/presence/checkin',
        json={'court_id': court['id']},
        headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 201

    # Verify active
    res = client.get('/api/presence/active')
    data = json.loads(res.data)
    assert len(data['active']) == 1
    assert data['active'][0]['count'] == 1
    assert 'email' not in data['active'][0]['users'][0]

    # Check out
    res = client.post('/api/presence/checkout', json={},
        headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 200

    res = client.get('/api/presence/active')
    data = json.loads(res.data)
    assert len(data['active']) == 0


def test_checkin_auto_checkout_previous(client):
    token = _auth(client, 'autouser', 'auto@test.com')
    court1 = _create_court(client, token)
    res = client.post('/api/courts', json={
        'name': 'Court 2', 'latitude': 40.86, 'longitude': -124.08, 'city': 'Arcata',
    }, headers={'Authorization': f'Bearer {token}'})
    court2 = json.loads(res.data)['court']

    # Check in to court1
    client.post('/api/presence/checkin', json={'court_id': court1['id']},
        headers={'Authorization': f'Bearer {token}'})

    # Check in to court2 — should auto-checkout from court1
    client.post('/api/presence/checkin', json={'court_id': court2['id']},
        headers={'Authorization': f'Bearer {token}'})

    res = client.get('/api/presence/active')
    data = json.loads(res.data)
    assert len(data['active']) == 1
    assert data['active'][0]['court_id'] == court2['id']


def test_check_in_requires_existing_court(client):
    token = _auth(client, 'missingcourt', 'missingcourt@test.com')
    res = client.post(
        '/api/presence/checkin',
        json={'court_id': 999999},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 404


def test_presence_ping_updates_last_seen(client, app):
    token, user = _auth_with_user(client, 'pinguser', 'ping@test.com')
    court = _create_court(client, token)
    headers = {'Authorization': f'Bearer {token}'}

    checkin = client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=headers)
    assert checkin.status_code == 201

    with app.app_context():
        active = CheckIn.query.filter_by(user_id=user['id'], checked_out_at=None).first()
        active.last_presence_ping_at = utcnow_naive() - timedelta(minutes=30)
        db.session.commit()
        stale_time = active.last_presence_ping_at

    ping = client.post('/api/presence/ping', json={}, headers=headers)
    assert ping.status_code == 200
    ping_data = json.loads(ping.data)
    assert ping_data['checked_in'] is True
    assert ping_data['court_id'] == court['id']
    assert ping_data['last_presence_ping_at'] is not None

    with app.app_context():
        refreshed = CheckIn.query.filter_by(user_id=user['id'], checked_out_at=None).first()
        assert refreshed.last_presence_ping_at > stale_time


def test_stale_presence_auto_checkout_completes_now_session(client, app):
    token, user = _auth_with_user(client, 'staleuser', 'stale@test.com')
    court = _create_court(client, token)
    headers = {'Authorization': f'Bearer {token}'}

    checkin = client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=headers)
    assert checkin.status_code == 201

    create_session = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'now',
        'duration_minutes': 120,
    }, headers=headers)
    assert create_session.status_code == 201
    session_id = json.loads(create_session.data)['session']['id']

    with app.app_context():
        active = CheckIn.query.filter_by(user_id=user['id'], checked_out_at=None).first()
        active.last_presence_ping_at = utcnow_naive() - timedelta(minutes=25)
        db.session.commit()

    status = client.get('/api/presence/status', headers=headers)
    assert status.status_code == 200
    status_data = json.loads(status.data)
    assert status_data['checked_in'] is False

    with app.app_context():
        checkout_record = CheckIn.query.filter_by(user_id=user['id']).order_by(CheckIn.id.desc()).first()
        assert checkout_record.checked_out_at is not None
        session = db.session.get(PlaySession, session_id)
        assert session.status == 'completed'
