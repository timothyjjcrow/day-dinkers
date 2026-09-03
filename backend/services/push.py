"""Durable web-push delivery.

Each push intent is inserted in the same database transaction as its in-app
Notification. Scheduled maintenance drains the outbox, so serverless process
shutdowns cannot discard committed alerts.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta


MAX_PUSH_ATTEMPTS = 8
PUSH_OUTBOX_RETENTION_DAYS = 7


def is_configured(app):
    return bool(
        app.config.get('PUSH_DELIVERY_ENABLED', True)
        and app.config.get('VAPID_PRIVATE_KEY')
        and app.config.get('VAPID_PUBLIC_KEY')
    )


def _safe_action_url(url):
    value = str(url or '').strip()
    safe = (
        value.startswith('/')
        and not value.startswith('//')
        and '\\' not in value
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )
    return value if safe else '/'


def _payload(title, body='', action_url=''):
    return json.dumps({
        'title': str(title)[:160],
        'body': str(body or '')[:180],
        'url': _safe_action_url(action_url),
    }, separators=(',', ':'))


def defer_to_user_after_commit(user_id, title, body='', action_url=''):
    """Write a durable intent into the caller's active transaction."""
    from flask import current_app

    if not is_configured(current_app):
        return None
    from backend.app import db
    from backend.models import PushOutbox

    row = PushOutbox(
        user_id=int(user_id),
        payload=_payload(title, body, action_url),
    )
    db.session.add(row)
    return row


def _retry_delay(attempts):
    return min(3600, 30 * (2 ** max(0, attempts - 1)))


def drain_push_outbox(*, limit=100, deadline=None):
    """Deliver due outbox rows and return bounded operational counters.

    PostgreSQL callers lock rows with SKIP LOCKED, so overlapping cron calls do
    not deliver the same row. Successful subscriptions are remembered on the
    row, preventing duplicates when another device needs a transient retry.
    """
    from flask import current_app
    from pywebpush import WebPushException, webpush

    from backend.app import db
    from backend.models import PushOutbox, PushSubscription, utcnow

    stats = {
        'selected': 0, 'sent': 0, 'retried': 0, 'failed': 0,
        'subscriptions_pruned': 0,
    }
    if not is_configured(current_app):
        stats['disabled'] = True
        return stats

    max_rows = max(1, min(int(limit), 500))
    processed = 0
    while processed < max_rows:
        if deadline is not None and time.monotonic() >= deadline:
            break
        # Claim exactly one row in this transaction. Committing a completed
        # delivery releases only that row's lock; it cannot accidentally
        # release locks for the rest of a prefetched batch and let a second
        # cron deliver them in parallel.
        query = PushOutbox.query.filter(
            PushOutbox.sent_at.is_(None),
            PushOutbox.failed_at.is_(None),
            PushOutbox.available_at <= utcnow(),
        ).order_by(PushOutbox.id.asc()).limit(1)
        if db.engine.dialect.name == 'postgresql':
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if row is None:
            break
        processed += 1
        stats['selected'] += 1
        delivered = row.delivered_ids()
        subscriptions = PushSubscription.query.filter_by(user_id=row.user_id).all()
        transient_errors = []
        for subscription in subscriptions:
            if subscription.id in delivered:
                continue
            try:
                webpush(
                    subscription_info=subscription.subscription_info(),
                    data=row.payload,
                    vapid_private_key=current_app.config['VAPID_PRIVATE_KEY'],
                    vapid_claims={'sub': current_app.config['VAPID_CLAIMS_EMAIL']},
                    ttl=3600,
                )
                delivered.add(subscription.id)
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (404, 410):
                    db.session.delete(subscription)
                    delivered.add(subscription.id)
                    stats['subscriptions_pruned'] += 1
                else:
                    transient_errors.append(f'HTTP {status or "unknown"}')
            except Exception as exc:  # provider/network failure; retry later
                transient_errors.append(type(exc).__name__)

        row.delivered_subscription_ids = json.dumps(sorted(delivered))
        if transient_errors:
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = ', '.join(transient_errors)[:500]
            if row.attempts >= MAX_PUSH_ATTEMPTS:
                row.failed_at = utcnow()
                stats['failed'] += 1
            else:
                row.available_at = utcnow() + timedelta(
                    seconds=_retry_delay(row.attempts),
                )
                stats['retried'] += 1
        else:
            # No subscriptions is a successful no-op: a future subscription
            # should not receive old alerts from before it existed.
            row.sent_at = utcnow()
            row.last_error = ''
            stats['sent'] += 1
        db.session.commit()

    # Completed rows contain notification copy and should not become a
    # permanent shadow inbox. Retain a short diagnostic window, then remove a
    # bounded batch on every dedicated drain.
    if deadline is None or time.monotonic() < deadline:
        cutoff = utcnow() - timedelta(days=PUSH_OUTBOX_RETENTION_DAYS)
        expired_ids = [row_id for (row_id,) in (
            db.session.query(PushOutbox.id)
            .filter(db.or_(
                PushOutbox.sent_at < cutoff,
                PushOutbox.failed_at < cutoff,
            ))
            .order_by(PushOutbox.id.asc())
            .limit(1000)
            .all()
        )]
        if expired_ids:
            PushOutbox.query.filter(PushOutbox.id.in_(expired_ids)).delete(
                synchronize_session=False,
            )
            db.session.commit()
        stats['purged'] = len(expired_ids)

    return stats


def send_to_user(user_id, title, body='', action_url=''):
    """Compatibility helper: enqueue durably in the caller's transaction."""
    return defer_to_user_after_commit(
        user_id, title, body, action_url=action_url,
    )
