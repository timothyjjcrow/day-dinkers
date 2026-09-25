"""Durable web-push delivery.

Each push intent is inserted in the same database transaction as its in-app
Notification. The request that committed it then delivers it before
responding, so an invite or message reaches phones right away. Rows that
request could not finish (a slow push service, a crash) stay in the outbox
for the scheduler tick and the daily drain, so serverless process shutdowns
cannot discard committed alerts.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta


MAX_PUSH_ATTEMPTS = 8
PUSH_OUTBOX_RETENTION_DAYS = 7
# A phone alert is only useful while it is current: "your game starts in an
# hour" must never arrive the next morning. Older undelivered rows expire.
PUSH_MAX_AGE_HOURS = 6
# One push service call may not hold a user's request (or a tick) for long;
# a timed-out send is retried by the next drain.
PUSH_SEND_TIMEOUT_SECONDS = 4
# The request that queued alerts delivers a bounded batch in parallel before
# it responds. Larger fan-outs (a tournament start) finish on the next tick.
INLINE_PUSH_ROW_LIMIT = 25
INLINE_PUSH_BUDGET_SECONDS = 5
PUSH_SEND_WORKERS = 8


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
    _mark_request_pending()
    return row


def _mark_request_pending():
    """Ask the current request to deliver its queued alerts before replying."""
    from flask import g, has_request_context

    if has_request_context():
        g.push_outbox_pending = True


def _send_notification(subscription_info, payload, private_key, claims_email, timeout):
    """Send one alert to one device. Returns ('sent'|'gone'|'retry', error)."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={'sub': claims_email},
            ttl=3600,
            timeout=timeout,
        )
        return 'sent', ''
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            return 'gone', ''
        return 'retry', f'HTTP {status or "unknown"}'
    except Exception as exc:  # provider/network failure; retry later
        return 'retry', type(exc).__name__


def _record_row_outcome(row, delivered, transient_errors, stats):
    """Mark one outbox row sent, retrying, or failed after its sends."""
    from backend.models import utcnow

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


def _retry_delay(attempts):
    return min(3600, 30 * (2 ** max(0, attempts - 1)))


def drain_push_outbox(*, limit=100, deadline=None):
    """Deliver due outbox rows and return bounded operational counters.

    PostgreSQL callers lock rows with SKIP LOCKED, so overlapping cron calls do
    not deliver the same row. Successful subscriptions are remembered on the
    row, preventing duplicates when another device needs a transient retry.
    """
    from flask import current_app

    from backend.app import db
    from backend.models import PushOutbox, PushSubscription, utcnow

    stats = {
        'selected': 0, 'sent': 0, 'retried': 0, 'failed': 0,
        'subscriptions_pruned': 0,
    }
    if not is_configured(current_app):
        stats['disabled'] = True
        return stats

    stale_before = utcnow() - timedelta(hours=PUSH_MAX_AGE_HOURS)
    stats['expired'] = PushOutbox.query.filter(
        PushOutbox.sent_at.is_(None),
        PushOutbox.failed_at.is_(None),
        PushOutbox.created_at < stale_before,
    ).update(
        {'failed_at': utcnow(), 'last_error': 'expired'},
        synchronize_session=False,
    )
    db.session.commit()

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
            PushOutbox.created_at >= stale_before,
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
            outcome, error = _send_notification(
                subscription.subscription_info(), row.payload,
                current_app.config['VAPID_PRIVATE_KEY'],
                current_app.config['VAPID_CLAIMS_EMAIL'],
                PUSH_SEND_TIMEOUT_SECONDS,
            )
            if outcome == 'sent':
                delivered.add(subscription.id)
            elif outcome == 'gone':
                db.session.delete(subscription)
                delivered.add(subscription.id)
                stats['subscriptions_pruned'] += 1
            else:
                transient_errors.append(error)

        _record_row_outcome(row, delivered, transient_errors, stats)
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


def deliver_due_push_now(*, limit=INLINE_PUSH_ROW_LIMIT, deadline=None):
    """Deliver a bounded batch of due outbox rows, sending in parallel.

    Used by the request that queued alerts and by the scheduler tick. Rows
    are locked with SKIP LOCKED on PostgreSQL, so a concurrent drain or tick
    never sends the same row twice. Every send has its own timeout and the
    pool is joined before rows are updated, so a timed-out send is retried
    rather than racing a later delivery.
    """
    from flask import current_app

    from backend.app import db
    from backend.models import PushOutbox, PushSubscription, utcnow

    stats = {
        'selected': 0, 'sent': 0, 'retried': 0, 'failed': 0,
        'subscriptions_pruned': 0,
    }
    if not is_configured(current_app):
        stats['disabled'] = True
        return stats
    if deadline is not None and time.monotonic() >= deadline:
        return stats

    now = utcnow()
    query = PushOutbox.query.filter(
        PushOutbox.sent_at.is_(None),
        PushOutbox.failed_at.is_(None),
        PushOutbox.available_at <= now,
        # The daily drain marks older rows expired; never send them here.
        PushOutbox.created_at >= now - timedelta(hours=PUSH_MAX_AGE_HOURS),
    ).order_by(PushOutbox.id.asc()).limit(max(1, min(int(limit), 100)))
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    if not rows:
        db.session.commit()
        return stats
    stats['selected'] = len(rows)

    subscriptions = defaultdict(list)
    for subscription in PushSubscription.query.filter(
        PushSubscription.user_id.in_({row.user_id for row in rows}),
    ).order_by(PushSubscription.id.asc()).all():
        subscriptions[subscription.user_id].append(subscription)

    delivered = {row.id: row.delivered_ids() for row in rows}
    jobs = [
        (row, subscription)
        for row in rows
        for subscription in subscriptions[row.user_id]
        if subscription.id not in delivered[row.id]
    ]
    outcomes = defaultdict(list)
    if jobs:
        timeout = PUSH_SEND_TIMEOUT_SECONDS
        if deadline is not None:
            timeout = max(1, min(timeout, deadline - time.monotonic()))
        private_key = current_app.config['VAPID_PRIVATE_KEY']
        claims_email = current_app.config['VAPID_CLAIMS_EMAIL']
        # Threads only talk to push services; every database read and write
        # stays on this thread's session.
        with ThreadPoolExecutor(max_workers=min(PUSH_SEND_WORKERS, len(jobs))) as pool:
            futures = [
                (row, subscription, pool.submit(
                    _send_notification, subscription.subscription_info(),
                    row.payload, private_key, claims_email, timeout,
                ))
                for row, subscription in jobs
            ]
        for row, subscription, future in futures:
            outcomes[row.id].append((subscription, *future.result()))

    pruned = set()
    for row in rows:
        transient_errors = []
        for subscription, outcome, error in outcomes[row.id]:
            if outcome == 'sent':
                delivered[row.id].add(subscription.id)
            elif outcome == 'gone':
                delivered[row.id].add(subscription.id)
                if subscription.id not in pruned:
                    pruned.add(subscription.id)
                    db.session.delete(subscription)
                    stats['subscriptions_pruned'] += 1
            else:
                transient_errors.append(error)
        _record_row_outcome(row, delivered[row.id], transient_errors, stats)
    db.session.commit()
    return stats


def register_inline_delivery(app):
    """Deliver alerts queued by a request before that request responds.

    Serverless functions cannot run work after the response, and a daily
    drain would deliver an invite hours late. Uncommitted work is discarded
    here exactly as the session teardown would discard it, so this can only
    deliver rows other code already committed.
    """
    from flask import g

    @app.after_request
    def _deliver_queued_push(response):
        if not g.pop('push_outbox_pending', False):
            return response
        if not app.config.get('PUSH_INLINE_DELIVERY', True) or not is_configured(app):
            return response
        from backend.app import db

        try:
            db.session.rollback()
            deliver_due_push_now(
                limit=INLINE_PUSH_ROW_LIMIT,
                deadline=time.monotonic() + INLINE_PUSH_BUDGET_SECONDS,
            )
        except Exception:
            db.session.rollback()
            app.logger.exception('Inline push delivery failed; the scheduler will retry')
        return response


def send_to_user(user_id, title, body='', action_url=''):
    """Compatibility helper: enqueue durably in the caller's transaction."""
    return defer_to_user_after_commit(
        user_id, title, body, action_url=action_url,
    )
