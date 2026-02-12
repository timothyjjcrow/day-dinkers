"""Tests for chat and presence routes."""
import json
from datetime import datetime, timedelta, timezone
from backend.app import db
from backend.models import CheckIn, PlaySession
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


def test_direct_message(client):
    token1 = _auth(client, 'dm1', 'dm1@test.com')
    token2 = _auth(client, 'dm2', 'dm2@test.com')
    reg2 = client.post('/api/auth/login', json={'email': 'dm2@test.com', 'password': 'password123'})
    uid2 = json.loads(reg2.data)['user']['id']

    res = client.post('/api/chat/send', json={
        'content': 'Hey!', 'recipient_id': uid2, 'msg_type': 'direct',
    }, headers={'Authorization': f'Bearer {token1}'})
    assert res.status_code == 201

    res = client.get(f'/api/chat/direct/{uid2}',
        headers={'Authorization': f'Bearer {token1}'})
    data = json.loads(res.data)
    assert len(data['messages']) == 1


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
