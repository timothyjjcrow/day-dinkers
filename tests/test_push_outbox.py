"""Durable push delivery survives request/process boundaries."""
import json
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import PushOutbox, PushSubscription, notify, utcnow
from backend.services.push import drain_push_outbox


@pytest.fixture()
def app():
    app = create_app('testing')
    app.config.update(
        VAPID_PRIVATE_KEY='test-private',
        VAPID_PUBLIC_KEY='test-public',
        VAPID_CLAIMS_EMAIL='mailto:test@example.com',
        PUSH_DELIVERY_ENABLED=True,
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def register(client):
    response = client.post('/api/auth/register', json={
        'email': 'push-outbox@example.com',
        'password': 'secret123',
        'display_name': 'Push Receiver',
    })
    assert response.status_code == 201
    return response.get_json()


def test_committed_push_is_delivered_and_marked_once(app, monkeypatch):
    client = app.test_client()
    player = register(client)
    user_id = player['user']['id']
    subscription = PushSubscription(
        user_id=user_id,
        endpoint='https://push.example/device',
        p256dh='public-key',
        auth='auth-key',
    )
    db.session.add(subscription)
    notify(
        user_id, 'direct_message', 'Dana sent you a message', 'See you at 6',
        action_url=f'/#chat/{user_id}',
    )
    db.session.commit()

    delivered = []
    monkeypatch.setattr(
        'pywebpush.webpush',
        lambda **kwargs: delivered.append(kwargs),
    )
    result = drain_push_outbox()

    assert result['sent'] == 1
    assert len(delivered) == 1
    assert json.loads(delivered[0]['data']) == {
        'title': 'Dana sent you a message',
        'body': 'See you at 6',
        'url': f'/#chat/{user_id}',
    }
    row = PushOutbox.query.one()
    assert row.sent_at is not None
    assert row.delivered_ids() == {subscription.id}

    assert drain_push_outbox()['selected'] == 0
    assert len(delivered) == 1


def test_transient_delivery_failure_is_retried_without_losing_row(app, monkeypatch):
    client = app.test_client()
    player = register(client)
    user_id = player['user']['id']
    db.session.add(PushSubscription(
        user_id=user_id,
        endpoint='https://push.example/retry',
        p256dh='public-key',
        auth='auth-key',
    ))
    notify(user_id, 'game_invite_direct', 'Game invite')
    db.session.commit()

    monkeypatch.setattr(
        'pywebpush.webpush',
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError('network down')),
    )
    result = drain_push_outbox()
    row = PushOutbox.query.one()
    assert result['retried'] == 1
    assert row.attempts == 1
    assert row.sent_at is None and row.failed_at is None
    assert row.available_at > utcnow()
    assert row.last_error == 'RuntimeError'

    row.available_at = utcnow()
    db.session.commit()
    delivered = []
    monkeypatch.setattr('pywebpush.webpush', lambda **kwargs: delivered.append(kwargs))
    assert drain_push_outbox()['sent'] == 1
    assert len(delivered) == 1
    assert PushOutbox.query.one().sent_at is not None


def test_push_outbox_rolls_back_with_notification(app):
    client = app.test_client()
    player = register(client)
    user_id = player['user']['id']

    notify(user_id, 'game_invite_direct', 'Rolled back invite')
    assert PushOutbox.query.count() == 1
    db.session.rollback()

    assert PushOutbox.query.count() == 0


def test_completed_push_payloads_are_purged_after_retention_window(app, monkeypatch):
    client = app.test_client()
    player = register(client)
    old = utcnow() - timedelta(days=8)
    db.session.add_all([
        PushOutbox(
            user_id=player['user']['id'], payload='{"title":"sent"}',
            sent_at=old,
        ),
        PushOutbox(
            user_id=player['user']['id'], payload='{"title":"failed"}',
            failed_at=old,
        ),
        PushOutbox(
            user_id=player['user']['id'], payload='{"title":"recent"}',
            sent_at=utcnow(),
        ),
    ])
    db.session.commit()
    monkeypatch.setattr('pywebpush.webpush', lambda **kwargs: None)

    result = drain_push_outbox()

    assert result['selected'] == 0
    assert result['purged'] == 2
    rows = PushOutbox.query.all()
    assert len(rows) == 1
    assert json.loads(rows[0].payload)['title'] == 'recent'
