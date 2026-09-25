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


def register_as(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email, 'password': 'secret123', 'display_name': name,
    })
    assert response.status_code == 201
    return response.get_json()


def subscribe(user_id, endpoint):
    subscription = PushSubscription(
        user_id=user_id, endpoint=endpoint, p256dh='public-key', auth='auth-key',
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


def test_request_delivers_its_alert_before_responding(app, monkeypatch):
    app.config['PUSH_INLINE_DELIVERY'] = True
    client = app.test_client()
    sender = register_as(client, 'inline-sender@example.com', 'Dana')
    receiver = register_as(client, 'inline-receiver@example.com', 'Marcus')
    subscribe(receiver['user']['id'], 'https://push.example/marcus')
    delivered = []
    monkeypatch.setattr('pywebpush.webpush', lambda **kwargs: delivered.append(kwargs))

    response = client.post(
        '/api/friends/request', json={'user_id': receiver['user']['id']},
        headers={'Authorization': f"Bearer {sender['token']}"},
    )

    assert response.status_code == 201
    assert [json.loads(call['data'])['title'] for call in delivered] == [
        'Dana sent you a friend request',
    ]
    assert delivered[0]['timeout'] > 0
    row = PushOutbox.query.one()
    assert row.sent_at is not None and row.attempts == 0


def test_inline_delivery_can_be_turned_off(app, monkeypatch):
    app.config['PUSH_INLINE_DELIVERY'] = False
    client = app.test_client()
    sender = register_as(client, 'off-sender@example.com', 'Dana')
    receiver = register_as(client, 'off-receiver@example.com', 'Marcus')
    subscribe(receiver['user']['id'], 'https://push.example/off')
    delivered = []
    monkeypatch.setattr('pywebpush.webpush', lambda **kwargs: delivered.append(kwargs))

    client.post(
        '/api/friends/request', json={'user_id': receiver['user']['id']},
        headers={'Authorization': f"Bearer {sender['token']}"},
    )

    assert delivered == []
    assert PushOutbox.query.one().sent_at is None


def test_failed_inline_send_stays_queued_for_the_scheduler(app, monkeypatch):
    app.config['PUSH_INLINE_DELIVERY'] = True
    client = app.test_client()
    sender = register_as(client, 'retry-sender@example.com', 'Dana')
    receiver = register_as(client, 'retry-receiver@example.com', 'Marcus')
    subscribe(receiver['user']['id'], 'https://push.example/flaky')

    def flaky(**kwargs):
        raise TimeoutError('push service slow')

    monkeypatch.setattr('pywebpush.webpush', flaky)
    response = client.post(
        '/api/friends/request', json={'user_id': receiver['user']['id']},
        headers={'Authorization': f"Bearer {sender['token']}"},
    )

    assert response.status_code == 201
    row = PushOutbox.query.one()
    assert row.sent_at is None and row.failed_at is None
    assert row.attempts == 1 and row.last_error == 'TimeoutError'


def test_inline_delivery_never_commits_work_the_request_left_uncommitted(app, monkeypatch):
    from backend.services.push import deliver_due_push_now

    client = app.test_client()
    player = register_as(client, 'uncommitted@example.com', 'Dana')
    user_id = player['user']['id']
    subscribe(user_id, 'https://push.example/dana')
    notify(user_id, 'game_invite_direct', 'Committed invite')
    db.session.commit()
    notify(user_id, 'game_invite_direct', 'Never committed')
    db.session.rollback()
    delivered = []
    monkeypatch.setattr('pywebpush.webpush', lambda **kwargs: delivered.append(kwargs))

    stats = deliver_due_push_now()

    assert stats['sent'] == 1
    assert [json.loads(call['data'])['title'] for call in delivered] == ['Committed invite']


def test_batch_delivery_sends_every_device_and_prunes_dead_ones_once(app, monkeypatch):
    from pywebpush import WebPushException

    from backend.services.push import deliver_due_push_now

    client = app.test_client()
    dana = register_as(client, 'batch-dana@example.com', 'Dana')['user']['id']
    marcus = register_as(client, 'batch-marcus@example.com', 'Marcus')['user']['id']
    phone = subscribe(dana, 'https://push.example/dana-phone')
    subscribe(dana, 'https://push.example/dana-gone')
    subscribe(marcus, 'https://push.example/marcus-phone')
    for title in ('First', 'Second'):
        notify(dana, 'game_invite_direct', title)
    notify(marcus, 'game_invite_direct', 'Third')
    db.session.commit()

    class Gone:
        status_code = 410

    sent = []

    def fake_webpush(**kwargs):
        if kwargs['subscription_info']['endpoint'].endswith('gone'):
            raise WebPushException('gone', response=Gone())
        sent.append((kwargs['subscription_info']['endpoint'], json.loads(kwargs['data'])['title']))

    monkeypatch.setattr('pywebpush.webpush', fake_webpush)
    stats = deliver_due_push_now()

    assert stats['selected'] == 3 and stats['sent'] == 3
    assert stats['subscriptions_pruned'] == 1
    assert sorted(sent) == [
        ('https://push.example/dana-phone', 'First'),
        ('https://push.example/dana-phone', 'Second'),
        ('https://push.example/marcus-phone', 'Third'),
    ]
    assert {row.id for row in PushSubscription.query.filter_by(user_id=dana)} == {phone.id}
    assert PushOutbox.query.filter(PushOutbox.sent_at.is_(None)).count() == 0
    assert deliver_due_push_now()['selected'] == 0


def test_stale_alerts_expire_instead_of_arriving_late(app, monkeypatch):
    from backend.services.push import deliver_due_push_now

    client = app.test_client()
    player = register_as(client, 'stale@example.com', 'Dana')
    user_id = player['user']['id']
    subscribe(user_id, 'https://push.example/stale')
    notify(user_id, 'game_reminder', 'Game at Larson in about an hour')
    notify(user_id, 'direct_message', 'Fresh message')
    db.session.commit()
    stale, fresh = PushOutbox.query.order_by(PushOutbox.id).all()
    stale.created_at = utcnow() - timedelta(hours=7)
    db.session.commit()
    delivered = []
    monkeypatch.setattr('pywebpush.webpush', lambda **kwargs: delivered.append(kwargs))

    assert deliver_due_push_now()['sent'] == 1
    result = drain_push_outbox()

    assert result['expired'] == 1
    assert [json.loads(call['data'])['title'] for call in delivered] == ['Fresh message']
    db.session.refresh(stale)
    assert stale.failed_at is not None and stale.last_error == 'expired'
    assert stale.sent_at is None
