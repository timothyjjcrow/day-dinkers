"""Web push delivery: mirrors in-app notifications to subscribed devices.

Dark unless VAPID keys are configured. Sends happen on a single daemon
worker thread so a burst of notifications (e.g. 200 court fans) never
blocks the request that created them.
"""
import json
import queue
import threading

_queue = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False


def is_configured(app):
    return bool(app.config.get('VAPID_PRIVATE_KEY') and app.config.get('VAPID_PUBLIC_KEY'))


def _ensure_worker(app):
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_drain, args=(app._get_current_object(),), daemon=True,
            name='webpush-sender',
        )
        thread.start()
        _worker_started = True


def _drain(app):
    from pywebpush import WebPushException, webpush
    while True:
        subscription_id, subscription_info, payload = _queue.get()
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


def send_to_user(user_id, title, body=''):
    """Queue a push to every device this user has subscribed. No-op when
    VAPID keys aren't configured. Call inside an app context."""
    from flask import current_app
    if not is_configured(current_app):
        return
    from backend.models import PushSubscription
    subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()
    if not subscriptions:
        return
    _ensure_worker(current_app)
    payload = json.dumps({'title': title, 'body': (body or '')[:180]})
    for subscription in subscriptions:
        _queue.put((subscription.id, subscription.subscription_info(), payload))
