"""Rate-limit identity and proxy-boundary contracts."""

import time

import jwt
from flask import Flask, jsonify

import backend.security as security
from backend.security import client_ip, rate_limit


def _token(app, user_id):
    return jwt.encode({
        'user_id': user_id,
        'iat': int(time.time()),
        'exp': int(time.time()) + 300,
    }, app.config['SECRET_KEY'], algorithm='HS256')


def _app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY='rate-limit-test-secret-that-is-long-enough',
        JWT_ALGORITHM='HS256',
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_BACKEND='memory',
        TRUSTED_PROXY_HOPS=0,
        RATE_LIMIT_IP_CEILING_MULTIPLIER=10,
    )

    @app.post('/limited')
    @rate_limit(1, 60)
    def limited():
        return jsonify({'ok': True})

    @app.get('/ip')
    def ip():
        return jsonify({'ip': client_ip()})

    return app


def test_signed_in_people_on_one_network_get_independent_primary_buckets():
    app = _app()
    security._BUCKETS.clear()
    client = app.test_client()
    first = {'Authorization': f'Bearer {_token(app, 101)}'}
    second = {'Authorization': f'Bearer {_token(app, 202)}'}

    assert client.post('/limited', headers=first).status_code == 200
    assert client.post('/limited', headers=second).status_code == 200
    assert client.post('/limited', headers=first).status_code == 429


def test_forwarded_for_is_ignored_until_a_proxy_hop_is_explicitly_trusted():
    app = _app()
    client = app.test_client()
    header = {'X-Forwarded-For': '198.51.100.7, 203.0.113.8'}

    assert client.get('/ip', headers=header).get_json()['ip'] == '127.0.0.1'
    app.config['TRUSTED_PROXY_HOPS'] = 1
    assert client.get('/ip', headers=header).get_json()['ip'] == '203.0.113.8'
    app.config['TRUSTED_PROXY_HOPS'] = 2
    assert client.get('/ip', headers=header).get_json()['ip'] == '198.51.100.7'


def test_forged_bearer_token_cannot_choose_a_user_bucket():
    app = _app()
    security._BUCKETS.clear()
    client = app.test_client()
    forged = jwt.encode(
        {'user_id': 999, 'exp': int(time.time()) + 300},
        'wrong-secret-that-cannot-verify-the-token',
        algorithm='HS256',
    )

    assert client.post(
        '/limited', headers={'Authorization': f'Bearer {forged}'},
    ).status_code == 200
    assert client.post('/limited').status_code == 429
