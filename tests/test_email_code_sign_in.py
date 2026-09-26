"""Passwordless sign-in: an emailed six-digit code creates or opens an account."""
import re
import time

import pytest

from backend.app import create_app, db
from backend.models import User
from backend.services import email_code
from backend.services.mfa import _totp_at


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def request_code(client, app, email):
    response = client.post('/api/auth/email-code', json={'email': email})
    assert response.status_code == 202, response.get_json()
    message = app.extensions['email_outbox'][-1]
    assert message['to'] == [email.lower()]
    code = re.match(r'^(\d{6}) is your Third Shot code$', message['subject']).group(1)
    assert code in message['text']
    return response.get_json()['challenge'], code


def verify(client, email, challenge, code, **extra):
    return client.post('/api/auth/email-code/verify', json={
        'email': email, 'challenge': challenge, 'code': code, **extra,
    })


def test_options_offer_email_codes(client):
    assert client.get('/api/auth/options').get_json() == {'email_code': True}


def test_new_player_signs_up_with_a_code_and_a_name(client, app):
    challenge, code = request_code(client, app, 'New.Player@Example.com')

    missing_name = verify(client, 'new.player@example.com', challenge, code)
    assert missing_name.status_code == 400
    assert missing_name.get_json() == {'error': 'display_name_required', 'new_account': True}

    created = verify(client, 'new.player@example.com', challenge, code, display_name='Jess')
    assert created.status_code == 201
    body = created.get_json()
    assert body['created'] is True
    me = client.get('/api/me', headers={'Authorization': f"Bearer {body['token']}"})
    assert me.status_code == 200
    user = User.query.filter_by(email='new.player@example.com').one()
    assert user.display_name == 'Jess'
    assert user.email_verified_at is not None


def test_existing_player_signs_in_and_keeps_their_password(client, app):
    registered = client.post('/api/auth/register', json={
        'email': 'dana@example.com', 'password': 'secret123', 'display_name': 'Dana',
    })
    assert registered.status_code == 201
    challenge, code = request_code(client, app, 'dana@example.com')

    signed_in = verify(client, 'dana@example.com', challenge, code)

    assert signed_in.status_code == 200
    assert signed_in.get_json()['created'] is False
    assert signed_in.get_json()['user']['display_name'] == 'Dana'
    assert User.query.filter_by(email='dana@example.com').one().email_verified_at is not None
    assert client.post('/api/auth/login', json={
        'email': 'dana@example.com', 'password': 'secret123',
    }).status_code == 200


def test_a_code_works_once(client, app):
    challenge, code = request_code(client, app, 'once@example.com')
    assert verify(client, 'once@example.com', challenge, code, display_name='Once').status_code == 201

    replay = verify(client, 'once@example.com', challenge, code)

    assert replay.status_code == 400
    assert replay.get_json()['error'] == 'email_code_used'


def test_wrong_code_other_address_or_tampering_is_rejected(client, app):
    challenge, code = request_code(client, app, 'guard@example.com')
    wrong = f'{(int(code) + 1) % 1_000_000:06d}'

    assert verify(client, 'guard@example.com', challenge, wrong).get_json()['error'] == 'invalid_email_code'
    assert verify(client, 'other@example.com', challenge, code).get_json()['error'] == 'invalid_email_code'
    body, mac = challenge.split('.')
    forged = f'{body[:-2]}AA.{mac}'
    assert verify(client, 'guard@example.com', forged, code).get_json()['error'] == 'invalid_email_code'
    assert verify(client, 'guard@example.com', challenge, 'abc123').get_json()['error'] == 'invalid_email_code'
    assert User.query.count() == 0


def test_expired_code_is_rejected(client, app, monkeypatch):
    challenge, code = request_code(client, app, 'late@example.com')
    later = time.time() + email_code.CODE_LIFETIME_SECONDS + 1
    monkeypatch.setattr(email_code.time, 'time', lambda: later)

    response = verify(client, 'late@example.com', challenge, code, display_name='Late')

    assert response.status_code == 400
    assert response.get_json()['error'] == 'email_code_expired'


def test_mfa_accounts_still_need_their_second_factor(client, app):
    registered = client.post('/api/auth/register', json={
        'email': 'mfa@example.com', 'password': 'secret123', 'display_name': 'Secure',
    }).get_json()
    headers = {'Authorization': f"Bearer {registered['token']}"}
    secret = client.post('/api/auth/mfa/setup', json={
        'current_password': 'secret123',
    }, headers=headers).get_json()['secret']
    assert client.post('/api/auth/mfa/enable', json={
        'code': _totp_at(secret, time.time()),
    }, headers=headers).status_code == 200
    challenge, code = request_code(client, app, 'mfa@example.com')

    needs_mfa = verify(client, 'mfa@example.com', challenge, code)
    assert needs_mfa.status_code == 401
    assert needs_mfa.get_json()['error'] == 'mfa_required'

    signed_in = verify(
        client, 'mfa@example.com', challenge, code,
        mfa_code=_totp_at(secret, time.time()),
    )
    assert signed_in.status_code == 200


def test_invited_sign_up_keeps_the_inviter(client, app):
    inviter = client.post('/api/auth/register', json={
        'email': 'host@example.com', 'password': 'secret123', 'display_name': 'Host',
    }).get_json()['user']
    challenge, code = request_code(client, app, 'guest@example.com')

    created = verify(
        client, 'guest@example.com', challenge, code,
        display_name='Guest', invited_by_user_id=inviter['id'],
    )

    assert created.status_code == 201
    assert User.query.filter_by(email='guest@example.com').one().invited_by_user_id == inviter['id']


def test_codes_are_refused_when_email_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(email_code, 'is_available', lambda app=None: False)

    assert client.get('/api/auth/options').get_json() == {'email_code': False}
    response = client.post('/api/auth/email-code', json={'email': 'x@example.com'})
    assert response.status_code == 503
    assert response.get_json()['error'] == 'email_code_unavailable'


def test_invalid_email_is_rejected(client):
    response = client.post('/api/auth/email-code', json={'email': 'not-an-email'})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'invalid_email'


def test_availability_follows_email_configuration(app):
    app.config['TESTING'] = False
    app.config['RESEND_API_KEY'] = ''
    assert email_code.is_available(app) is False
    app.config['RESEND_API_KEY'] = 're_test'
    app.config['TRANSACTIONAL_EMAIL_FROM'] = 'Third Shot <hello@example.com>'
    assert email_code.is_available(app) is True
    app.config['TESTING'] = True
