"""Regression coverage for the audit's account-recovery and DM criticals."""
import re

import pytest

from backend.app import create_app, db


@pytest.fixture()
def app():
    app = create_app('testing')
    app.config.update(
        PUBLIC_APP_URL='https://third-shot.example',
        TRANSACTIONAL_EMAIL_FROM='Third Shot <hello@example.com>',
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name='Player'):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def token_from_last_email(app, kind):
    text = app.extensions['email_outbox'][-1]['text']
    match = re.search(rf'#{re.escape(kind)}=([A-Za-z0-9_-]+)', text)
    assert match, text
    return match.group(1)


def test_registration_sends_one_time_verification_and_updates_me(client, app):
    account = register(client, 'ana@example.com', 'Ana')
    assert account['verification_sent'] is True
    assert account['user']['email_verified'] is False
    token = token_from_last_email(app, 'verify-email')

    verified = client.post('/api/auth/verify-email', json={'token': token})
    assert verified.status_code == 200
    assert client.get('/api/me', headers=auth(account['token'])).get_json()['user']['email_verified'] is True

    replay = client.post('/api/auth/verify-email', json={'token': token})
    assert replay.status_code == 400
    assert replay.get_json()['error'] == 'verification_link_invalid_or_expired'


def test_forgot_password_does_not_enumerate_accounts_and_revokes_sessions(client, app):
    account = register(client, 'ana@example.com', 'Ana')
    app.extensions['email_outbox'].clear()

    known = client.post('/api/auth/forgot-password', json={'email': 'ana@example.com'})
    reset_token = token_from_last_email(app, 'reset-password')
    unknown = client.post('/api/auth/forgot-password', json={'email': 'nobody@example.com'})
    malformed = client.post('/api/auth/forgot-password', json={'email': 'not-an-email'})
    assert (known.status_code, known.get_json()) == (unknown.status_code, unknown.get_json())
    assert (known.status_code, known.get_json()) == (malformed.status_code, malformed.get_json())
    assert known.status_code == 202

    reset = client.post('/api/auth/reset-password', json={
        'token': reset_token,
        'new_password': 'new-secret-123',
    })
    assert reset.status_code == 200
    assert client.get('/api/me', headers=auth(account['token'])).status_code == 401
    assert client.post('/api/auth/login', json={
        'email': 'ana@example.com', 'password': 'secret123',
    }).status_code == 401
    assert client.post('/api/auth/login', json={
        'email': 'ana@example.com', 'password': 'new-secret-123',
    }).status_code == 200
    assert client.post('/api/auth/reset-password', json={
        'token': reset_token, 'new_password': 'another-secret',
    }).status_code == 400


def test_email_change_requires_password_confirmation_and_revokes_sessions(client, app):
    account = register(client, 'old@example.com', 'Ana')
    app.extensions['email_outbox'].clear()
    bad_password = client.post('/api/auth/change-email', json={
        'new_email': 'new@example.com', 'current_password': 'wrong',
    }, headers=auth(account['token']))
    assert bad_password.status_code == 403
    assert app.extensions['email_outbox'] == []

    requested = client.post('/api/auth/change-email', json={
        'new_email': 'NEW@example.com', 'current_password': 'secret123',
    }, headers=auth(account['token']))
    assert requested.status_code == 200
    change_token = token_from_last_email(app, 'confirm-email')
    assert app.extensions['email_outbox'][-1]['to'] == ['new@example.com']

    confirmed = client.post('/api/auth/confirm-email-change', json={'token': change_token})
    assert confirmed.status_code == 200
    assert client.get('/api/me', headers=auth(account['token'])).status_code == 401
    assert client.post('/api/auth/login', json={
        'email': 'old@example.com', 'password': 'secret123',
    }).status_code == 401
    new_login = client.post('/api/auth/login', json={
        'email': 'new@example.com', 'password': 'secret123',
    })
    assert new_login.status_code == 200
    assert new_login.get_json()['user']['email_verified'] is True


def test_direct_message_creates_actionable_mutable_notification(client):
    sender = register(client, 'sender@example.com', 'Sender')
    recipient = register(client, 'recipient@example.com', 'Recipient')
    assert 'direct_message' in recipient['muteable_notifications']

    request_row = client.post(
        '/api/friends/request',
        json={'user_id': recipient['user']['id']},
        headers=auth(sender['token']),
    ).get_json()
    accepted = client.post(
        f"/api/friends/{request_row['friendship_id']}/respond",
        json={'accept': True},
        headers=auth(recipient['token']),
    )
    assert accepted.status_code == 200

    sent = client.post(
        f"/api/chat/{recipient['user']['id']}",
        json={'body': 'Meet at Court 2?'},
        headers=auth(sender['token']),
    )
    assert sent.status_code == 201
    activity = client.get('/api/notifications', headers=auth(recipient['token'])).get_json()
    notice = activity['items'][0]
    assert notice['kind'] == 'direct_message'
    assert notice['title'] == 'New message from Sender'
    assert notice['body'] == 'Meet at Court 2?'
    assert notice['related_user_id'] == sender['user']['id']
    assert notice['action_url'] == f"/#chat/{sender['user']['id']}"
