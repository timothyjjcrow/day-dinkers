"""Web push delivery: mirrors in-app notifications to subscribed devices.

Dark unless VAPID keys are configured. Sends happen on a single daemon
worker thread so a burst of notifications (e.g. 200 court fans) never
blocks the request that created them.
"""
import json
import queue
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

_queue = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False
_PENDING_PUSHES_KEY = 'thirdshot_pending_web_pushes_by_transaction'


def is_configured(app):
    return bool(app.config.get('VAPID_PRIVATE_KEY') and app.config.get('VAPID_PUBLIC_KEY'))


def _ensure_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_drain, daemon=True,
            name='webpush-sender',
        )
        thread.start()
        _worker_started = True


def _drain():
    from pywebpush import WebPushException, webpush
    while True:
        app, user_id, payload = _queue.get()
        try:
            # Resolve subscriptions in a fresh app/session scope after the
            # notification's transaction is durably committed.
            with app.app_context():
                from backend.models import PushSubscription
                subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()
                deliveries = [
                    (subscription.id, subscription.subscription_info())
                    for subscription in subscriptions
                ]
            for subscription_id, subscription_info in deliveries:
                try:
                    webpush(
                        subscription_info=subscription_info,
                        data=payload,
                        vapid_private_key=app.config['VAPID_PRIVATE_KEY'],
                        vapid_claims={'sub': app.config['VAPID_CLAIMS_EMAIL']},
                        ttl=3600,
                    )
                except WebPushException as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    if status in (404, 410):
                        # The browser dropped this subscription — forget it.
                        try:
                            with app.app_context():
                                from backend.app import db
                                from backend.models import PushSubscription
                                row = db.session.get(PushSubscription, subscription_id)
                                if row:
                                    db.session.delete(row)
                                    db.session.commit()
                        except Exception:
                            app.logger.exception('Pruning dead push subscription failed')
                except Exception:
                    app.logger.exception('Web push send failed')
        except Exception:
            app.logger.exception('Preparing web push delivery failed')
        finally:
            _queue.task_done()


def _safe_action_url(url):
    value = str(url or '').strip()
    safe = (
        value.startswith('/')
        and not value.startswith('//')
        and '\\' not in value
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )
    return value if safe else '/'


def _enqueue_user_push(app, user_id, title, body='', action_url=''):
    """Queue a committed user-level push intent without querying the caller's
    transaction-bound session."""
    if not is_configured(app):
        return
    _ensure_worker()
    payload = json.dumps({
        'title': title,
        'body': (body or '')[:180],
        'url': _safe_action_url(action_url),
    })
    _queue.put((app, user_id, payload))


def _pending_pushes_by_transaction(session, create=False):
    pending = session.info.get(_PENDING_PUSHES_KEY)
    if pending is None and create:
        pending = {}
        session.info[_PENDING_PUSHES_KEY] = pending
    return pending


def _take_transaction_pushes(session, transaction):
    pending = _pending_pushes_by_transaction(session)
    if not pending or transaction is None:
        return []
    intents = pending.pop(transaction, [])
    if not pending:
        session.info.pop(_PENDING_PUSHES_KEY, None)
    return intents


def _dispatch_committed_pushes(session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    intents = _take_transaction_pushes(session, transaction)
    if transaction is not None and transaction.nested:
        # RELEASE SAVEPOINT is not a durable commit. Promote its intents to the
        # parent transaction so an outer rollback can still discard them.
        if intents:
            pending = _pending_pushes_by_transaction(session, create=True)
            pending.setdefault(transaction.parent, []).extend(intents)
        return

    # Only the root transaction reaches dispatch.
    for intent in intents:
        app, user_id, title, body, *destination = intent
        try:
            if destination:
                _enqueue_user_push(
                    app, user_id, title, body, action_url=destination[0],
                )
            else:
                # Preserve the four-argument call shape for existing workers
                # and tests when a notification has no specific destination.
                _enqueue_user_push(app, user_id, title, body)
        except Exception:
            # The database has already committed; push remains best-effort and
            # must never turn a successful API mutation into a 500 response.
            app.logger.exception('Queueing committed web push failed')


def _discard_rolled_back_pushes(session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    _take_transaction_pushes(session, transaction)


def _discard_unfinished_transaction_pushes(session, transaction):
    # Covers Session.close()/transaction invalidation paths which do not reach
    # the explicit commit/rollback hooks. Normal endings already popped theirs.
    _take_transaction_pushes(session, transaction)


# Register once at module import. Flask-SQLAlchemy's session subclass inherits
# these base Session events, and each session carries its own pending intents.
if not event.contains(Session, 'after_commit', _dispatch_committed_pushes):
    event.listen(Session, 'after_commit', _dispatch_committed_pushes)
if not event.contains(Session, 'after_rollback', _discard_rolled_back_pushes):
    event.listen(Session, 'after_rollback', _discard_rolled_back_pushes)
if not event.contains(
        Session, 'after_transaction_end', _discard_unfinished_transaction_pushes):
    event.listen(
        Session, 'after_transaction_end', _discard_unfinished_transaction_pushes,
    )


def defer_to_user_after_commit(user_id, title, body='', action_url=''):
    """Attach a push intent to the current DB transaction.

    The intent is enqueued only by ``after_commit`` and is discarded by
    ``after_rollback``. No-op when VAPID keys are not configured.
    """
    from flask import current_app
    if not is_configured(current_app):
        return
    from backend.app import db
    session = db.session()
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        # ``notify`` adds its Notification first, which normally autobegins.
        # Keep this helper correct for direct callers too.
        transaction = session.begin()
    pending = _pending_pushes_by_transaction(session, create=True)
    intent = (
        current_app._get_current_object(), int(user_id), str(title), str(body or ''),
    )
    destination = _safe_action_url(action_url)
    if destination != '/':
        intent += (destination,)
    pending.setdefault(transaction, []).append(intent)


def send_to_user(user_id, title, body='', action_url=''):
    """Queue a push to every device this user has subscribed. No-op when
    VAPID keys aren't configured. Call inside an app context."""
    from flask import current_app
    args = (
        current_app._get_current_object(), int(user_id), str(title), str(body or ''),
    )
    destination = _safe_action_url(action_url)
    if destination != '/':
        _enqueue_user_push(*args, action_url=destination)
    else:
        _enqueue_user_push(*args)
