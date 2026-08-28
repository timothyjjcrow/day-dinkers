"""Focused regressions for the stateless Vercel + Neon deployment path."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend import app as app_module
from backend import config as config_module
from backend.app import db
from backend.models import Message, RateLimitBucket


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / 'scripts'
    / 'migrate_sqlite_recovery.py'
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    'serverless_migrate_sqlite_recovery', SCRIPT_PATH,
)
assert SCRIPT_SPEC and SCRIPT_SPEC.loader
migration = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(migration)


@pytest.fixture()
def testing_app():
    app = app_module.create_app('testing')
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _registration_payload(index):
    return {
        'email': f'serverless-{index}@example.com',
        'password': 'secret123',
        'display_name': f'Player {index}',
    }


def test_neon_pooler_omits_startup_search_path_but_direct_url_keeps_it(
        monkeypatch):
    monkeypatch.setenv(
        'DATABASE_URL',
        'postgresql://app:secret@ep-blue-pooler.us-east-2.aws.neon.tech/appdb'
        '?sslmode=require',
    )
    pooled_options = config_module._engine_options()

    assert pooled_options['pool_pre_ping'] is True
    assert 'connect_args' not in pooled_options

    monkeypatch.setenv(
        'DATABASE_URL',
        'postgresql://app:secret@ep-blue.us-east-2.aws.neon.tech/appdb'
        '?sslmode=require',
    )
    direct_options = config_module._engine_options()

    assert direct_options['connect_args'] == {
        'options': '-csearch_path=picklepals',
    }


def test_schema_management_disabled_skips_every_startup_mutation(monkeypatch):
    class ServerlessTestConfig(config_module.TestingConfig):
        SCHEMA_MANAGEMENT_ENABLED = False
        AUTO_CREATE_DB = True
        AUTO_SEED_COURTS = True
        RESET_DB_ON_BOOT = False

    calls = []

    def record(name):
        def recorder(*_args, **_kwargs):
            calls.append(name)
        return recorder

    startup_helpers = (
        '_ensure_pg_schema',
        '_migrate_legacy_schema',
        '_clear_conflicting_legacy_indexes',
        '_upgrade_schema',
        '_ensure_game_attempt_index',
        '_ensure_message_attempt_index',
        '_ensure_message_send_attempt_schema',
        '_ensure_notification_unread_dedupe_index',
        '_maybe_auto_seed',
    )
    monkeypatch.setattr(
        app_module,
        'get_config',
        lambda _name=None: ServerlessTestConfig,
    )
    for helper_name in startup_helpers:
        monkeypatch.setattr(
            app_module,
            helper_name,
            record(helper_name),
        )
    monkeypatch.setattr(db, 'create_all', record('db.create_all'))
    monkeypatch.setattr(db, 'drop_all', record('db.drop_all'))

    app = app_module.create_app('serverless-test')

    assert app.config['SCHEMA_MANAGEMENT_ENABLED'] is False
    assert calls == []


def test_database_rate_limit_is_shared_across_clients_on_sqlite(testing_app):
    testing_app.config.update(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_BACKEND='database',
    )
    first_client = testing_app.test_client()
    second_client = testing_app.test_client()
    clients = (first_client, second_client)

    responses = [
        clients[index % 2].post(
            '/api/auth/register',
            json=_registration_payload(index),
            headers={'X-Forwarded-For': '203.0.113.44'},
        )
        for index in range(11)
    ]

    assert [response.status_code for response in responses[:10]] == [201] * 10
    assert responses[10].status_code == 429
    assert responses[10].get_json()['error'] == 'rate_limited'
    with testing_app.app_context():
        buckets = RateLimitBucket.query.all()
        assert len(buckets) == 1
        assert buckets[0].count == 11


def test_direct_message_delta_is_bounded_and_continuable(testing_app):
    client = testing_app.test_client()
    first = client.post('/api/auth/register', json=_registration_payload('one'))
    second = client.post('/api/auth/register', json=_registration_payload('two'))
    first_data = first.get_json()
    second_data = second.get_json()
    with testing_app.app_context():
        db.session.add_all([
            Message(
                sender_id=first_data['user']['id'],
                recipient_id=second_data['user']['id'],
                body=f'message {index}',
            )
            for index in range(205)
        ])
        db.session.commit()
        first_message_id = Message.query.order_by(Message.id).first().id

    headers = {'Authorization': f"Bearer {first_data['token']}"}
    first_page = client.get(
        f"/api/chat/{second_data['user']['id']}?since_id={first_message_id}",
        headers=headers,
    ).get_json()
    assert len(first_page['items']) == 200
    assert first_page['has_more'] is True

    second_page = client.get(
        f"/api/chat/{second_data['user']['id']}"
        f"?since_id={first_page['items'][-1]['id']}",
        headers=headers,
    ).get_json()
    assert len(second_page['items']) == 4
    assert second_page['has_more'] is False


def test_push_reports_disabled_when_delivery_flag_is_off_despite_keys(
        testing_app):
    client = testing_app.test_client()
    registration = client.post(
        '/api/auth/register',
        json=_registration_payload('push'),
    )
    assert registration.status_code == 201
    token = registration.get_json()['token']
    testing_app.config.update(
        VAPID_PRIVATE_KEY='test-private-key',
        VAPID_PUBLIC_KEY='test-public-key',
        PUSH_DELIVERY_ENABLED=False,
    )

    response = client.get(
        '/api/push/public-key',
        headers={'Authorization': f'Bearer {token}'},
    )

    assert response.status_code == 200
    assert response.get_json() == {'enabled': False}


@pytest.mark.parametrize(
    ('url', 'is_pooler'),
    (
        (
            'postgresql+psycopg://app:secret@'
            'ep-blue-pooler.us-east-2.aws.neon.tech/appdb',
            True,
        ),
        (
            'postgresql+psycopg://app:secret@'
            'ep-blue.us-east-2.aws.neon.tech/appdb',
            False,
        ),
        (
            'postgresql+psycopg://app:secret@db.example.com/appdb'
            '?application_name=ep-blue-pooler.neon.tech',
            False,
        ),
    ),
)
def test_recovery_migration_detects_only_neon_pooler_hosts(url, is_pooler):
    assert migration._is_neon_pooler_url(url) is is_pooler
