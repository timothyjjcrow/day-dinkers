"""Tests for authentication routes."""
import json

from backend.models import User


class _FakeGoogleResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_register(client):
    res = client.post('/api/auth/register', json={
        'username': 'testuser', 'email': 'test@test.com',
        'password': 'password123', 'name': 'Test User',
        'skill_level': 3.5, 'play_style': 'doubles',
    })
    assert res.status_code == 201
    data = json.loads(res.data)
    assert 'token' in data
    assert data['user']['username'] == 'testuser'
    assert data['user']['skill_level'] == 3.5
    assert data['user']['play_style'] == 'doubles'


def test_register_missing_fields(client):
    res = client.post('/api/auth/register', json={'username': 'x'})
    assert res.status_code == 400


def test_register_duplicate_username(client):
    client.post('/api/auth/register', json={
        'username': 'dup', 'email': 'dup@test.com', 'password': 'password123',
    })
    res = client.post('/api/auth/register', json={
        'username': 'dup', 'email': 'dup2@test.com', 'password': 'password123',
    })
    assert res.status_code == 409


def test_login(client):
    client.post('/api/auth/register', json={
        'username': 'loginuser', 'email': 'login@test.com', 'password': 'password123',
    })
    res = client.post('/api/auth/login', json={
        'email': 'login@test.com', 'password': 'password123',
    })
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'token' in data


def test_login_bad_password(client):
    client.post('/api/auth/register', json={
        'username': 'badpw', 'email': 'bad@test.com', 'password': 'password123',
    })
    res = client.post('/api/auth/login', json={
        'email': 'bad@test.com', 'password': 'wrong',
    })
    assert res.status_code == 401


def test_get_profile(client):
    reg = client.post('/api/auth/register', json={
        'username': 'profuser', 'email': 'prof@test.com', 'password': 'password123',
        'name': 'Profile User',
    })
    token = json.loads(reg.data)['token']
    res = client.get('/api/auth/profile', headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['user']['name'] == 'Profile User'
    assert 'friends_count' in data['user']
    assert 'total_checkins' in data['user']


def test_update_profile(client):
    reg = client.post('/api/auth/register', json={
        'username': 'upuser', 'email': 'up@test.com', 'password': 'password123',
    })
    token = json.loads(reg.data)['token']
    res = client.put('/api/auth/profile',
        json={'name': 'Updated Name', 'bio': 'I love pickleball', 'play_style': 'singles'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['user']['name'] == 'Updated Name'
    assert data['user']['bio'] == 'I love pickleball'
    assert data['user']['play_style'] == 'singles'


def test_update_profile_rejects_invalid_payload(client):
    reg = client.post('/api/auth/register', json={
        'username': 'invalidpayload',
        'email': 'invalidpayload@test.com',
        'password': 'password123',
    })
    token = json.loads(reg.data)['token']
    res = client.put(
        '/api/auth/profile',
        data='null',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
    )
    assert res.status_code == 400


def test_profile_upcoming_games_excludes_completed_sessions(client):
    host = client.post('/api/auth/register', json={
        'username': 'profilehost',
        'email': 'profilehost@test.com',
        'password': 'password123',
    })
    guest = client.post('/api/auth/register', json={
        'username': 'profileguest',
        'email': 'profileguest@test.com',
        'password': 'password123',
    })
    host_token = json.loads(host.data)['token']
    guest_token = json.loads(guest.data)['token']

    court = client.post('/api/courts', json={
        'name': 'Profile Sessions Court',
        'latitude': 40.8,
        'longitude': -124.16,
        'city': 'Eureka',
    }, headers={'Authorization': f'Bearer {host_token}'})
    court_id = json.loads(court.data)['court']['id']

    checkin = client.post(
        '/api/presence/checkin',
        json={'court_id': court_id},
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert checkin.status_code == 201

    session = client.post('/api/sessions', json={
        'court_id': court_id,
        'session_type': 'now',
    }, headers={'Authorization': f'Bearer {host_token}'})
    session_id = json.loads(session.data)['session']['id']

    join = client.post(
        f'/api/sessions/{session_id}/join',
        json={},
        headers={'Authorization': f'Bearer {guest_token}'},
    )
    assert join.status_code == 200

    end = client.post(
        f'/api/sessions/{session_id}/end',
        json={},
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert end.status_code == 200

    profile = client.get('/api/auth/profile', headers={'Authorization': f'Bearer {guest_token}'})
    assert profile.status_code == 200
    assert json.loads(profile.data)['user']['upcoming_games'] == 0


def test_view_other_profile(client):
    reg = client.post('/api/auth/register', json={
        'username': 'viewuser', 'email': 'view@test.com', 'password': 'password123',
        'name': 'Viewable User',
    })
    user_id = json.loads(reg.data)['user']['id']
    res = client.get(f'/api/auth/profile/{user_id}')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['user']['username'] == 'viewuser'
    assert 'email' not in data['user']  # Email should be hidden


def test_friend_request_flow(client):
    reg1 = client.post('/api/auth/register', json={
        'username': 'user1', 'email': 'u1@test.com', 'password': 'password123',
    })
    reg2 = client.post('/api/auth/register', json={
        'username': 'user2', 'email': 'u2@test.com', 'password': 'password123',
    })
    token1 = json.loads(reg1.data)['token']
    token2 = json.loads(reg2.data)['token']
    uid2 = json.loads(reg2.data)['user']['id']

    # Send request
    res = client.post('/api/auth/friends/request',
        json={'friend_id': uid2},
        headers={'Authorization': f'Bearer {token1}'},
    )
    assert res.status_code == 201

    # Check pending
    res = client.get('/api/auth/friends/pending',
        headers={'Authorization': f'Bearer {token2}'},
    )
    assert res.status_code == 200
    pending = json.loads(res.data)['requests']
    assert len(pending) == 1

    # Accept
    res = client.post('/api/auth/friends/respond',
        json={'friendship_id': pending[0]['id'], 'action': 'accept'},
        headers={'Authorization': f'Bearer {token2}'},
    )
    assert res.status_code == 200

    # Check friends list
    res = client.get('/api/auth/friends',
        headers={'Authorization': f'Bearer {token1}'},
    )
    assert res.status_code == 200
    friends = json.loads(res.data)['friends']
    assert len(friends) == 1


def test_search_users(client):
    reg = client.post('/api/auth/register', json={
        'username': 'searchable', 'email': 'search@test.com',
        'password': 'password123', 'name': 'Find Me',
    })
    token = json.loads(reg.data)['token']
    res = client.get('/api/auth/users/search?q=search',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 200


def test_notifications(client):
    reg = client.post('/api/auth/register', json={
        'username': 'notifuser', 'email': 'notif@test.com', 'password': 'password123',
    })
    token = json.loads(reg.data)['token']
    res = client.get('/api/auth/notifications',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 200
    assert 'notifications' in json.loads(res.data)

    res = client.post('/api/auth/notifications/read', json={},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 200


def test_google_config_endpoint(client):
    client.application.config['GOOGLE_CLIENT_ID'] = ''
    disabled = client.get('/api/auth/google/config')
    assert disabled.status_code == 200
    assert json.loads(disabled.data)['enabled'] is False

    client.application.config['GOOGLE_CLIENT_ID'] = 'google-client-id.apps.googleusercontent.com'
    enabled = client.get('/api/auth/google/config')
    payload = json.loads(enabled.data)
    assert enabled.status_code == 200
    assert payload['enabled'] is True
    assert payload['client_id'] == 'google-client-id.apps.googleusercontent.com'


def test_google_login_creates_user(client, monkeypatch):
    client.application.config['GOOGLE_CLIENT_ID'] = 'google-client-id.apps.googleusercontent.com'
    google_payload = {
        'aud': 'google-client-id.apps.googleusercontent.com',
        'iss': 'https://accounts.google.com',
        'exp': '9999999999',
        'email_verified': 'true',
        'sub': 'google-sub-123',
        'email': 'googleuser@test.com',
        'name': 'Google User',
        'picture': 'https://example.com/avatar.png',
    }

    def fake_google_verify(url, params=None, timeout=0):
        assert params['id_token'] == 'valid-google-token'
        return _FakeGoogleResponse(200, google_payload)

    monkeypatch.setattr('backend.routes.auth.requests.get', fake_google_verify)

    res = client.post('/api/auth/google', json={'id_token': 'valid-google-token'})
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'token' in data
    assert data['user']['email'] == 'googleuser@test.com'
    assert data['user']['name'] == 'Google User'

    with client.application.app_context():
        user = User.query.filter_by(email='googleuser@test.com').first()
        assert user is not None
        assert user.google_sub == 'google-sub-123'


def test_google_login_links_existing_email_account(client, monkeypatch):
    client.application.config['GOOGLE_CLIENT_ID'] = 'google-client-id.apps.googleusercontent.com'
    client.post('/api/auth/register', json={
        'username': 'existinggoogle',
        'email': 'existinggoogle@test.com',
        'password': 'password123',
    })
    google_payload = {
        'aud': 'google-client-id.apps.googleusercontent.com',
        'iss': 'https://accounts.google.com',
        'exp': '9999999999',
        'email_verified': 'true',
        'sub': 'google-sub-xyz',
        'email': 'existinggoogle@test.com',
        'name': 'Existing Google',
    }

    def fake_google_verify(url, params=None, timeout=0):
        return _FakeGoogleResponse(200, google_payload)

    monkeypatch.setattr('backend.routes.auth.requests.get', fake_google_verify)

    res = client.post('/api/auth/google', json={'id_token': 'valid-google-token'})
    assert res.status_code == 200

    with client.application.app_context():
        user = User.query.filter_by(email='existinggoogle@test.com').first()
        assert user is not None
        assert user.google_sub == 'google-sub-xyz'


def test_google_login_rejects_invalid_token(client, monkeypatch):
    client.application.config['GOOGLE_CLIENT_ID'] = 'google-client-id.apps.googleusercontent.com'

    def fake_google_verify(url, params=None, timeout=0):
        return _FakeGoogleResponse(401, {'error': 'invalid_token'})

    monkeypatch.setattr('backend.routes.auth.requests.get', fake_google_verify)

    res = client.post('/api/auth/google', json={'id_token': 'bad-token'})
    assert res.status_code == 401


def test_admin_emails_config_sets_admin_on_register_and_login(client):
    client.application.config['ADMIN_EMAILS'] = 'admin@test.com'

    register = client.post('/api/auth/register', json={
        'username': 'adminuser',
        'email': 'admin@test.com',
        'password': 'password123',
    })
    assert register.status_code == 201

    with client.application.app_context():
        admin_user = User.query.filter_by(email='admin@test.com').first()
        assert admin_user is not None
        assert admin_user.is_admin is True

    user_register = client.post('/api/auth/register', json={
        'username': 'normaluser',
        'email': 'normal@test.com',
        'password': 'password123',
    })
    assert user_register.status_code == 201

    with client.application.app_context():
        normal_user = User.query.filter_by(email='normal@test.com').first()
        assert normal_user is not None
        assert normal_user.is_admin is False

    client.application.config['ADMIN_EMAILS'] = 'normal@test.com'
    login = client.post('/api/auth/login', json={
        'email': 'normal@test.com',
        'password': 'password123',
    })
    assert login.status_code == 200
    with client.application.app_context():
        promoted_user = User.query.filter_by(email='normal@test.com').first()
        assert promoted_user is not None
        assert promoted_user.is_admin is True
