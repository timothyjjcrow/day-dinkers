"""Web push subscriptions: devices register here; delivery lives in
backend/services/push.py. All endpoints are safe no-ops until VAPID keys
are configured in the environment."""
from flask import Blueprint, current_app, g, jsonify, request

from backend.app import db
from backend.models import PushSubscription, utcnow
from backend.security import rate_limit
from backend.services.push import is_configured

push_bp = Blueprint('push', __name__)

from backend.routes.auth import login_required  # noqa: E402


@push_bp.get('/push/public-key')
@login_required
def public_key():
    """The VAPID public key the browser needs to subscribe — or enabled:false
    so the client quietly skips push setup."""
    if not is_configured(current_app):
        return jsonify({'enabled': False})
    return jsonify({'enabled': True, 'key': current_app.config['VAPID_PUBLIC_KEY']})


@push_bp.post('/push/subscribe')
@rate_limit(20, 3600)
@login_required
def subscribe():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_subscription'}), 400
    endpoint = str(payload.get('endpoint') or '').strip()
    keys = payload.get('keys') or {}
    if not isinstance(keys, dict):
        return jsonify({'error': 'invalid_subscription'}), 400
    p256dh = str(keys.get('p256dh') or '').strip()
    auth = str(keys.get('auth') or '').strip()
    if not endpoint.startswith('https://') or not p256dh or not auth:
        return jsonify({'error': 'invalid_subscription'}), 400

    # One row per endpoint: an atomic upsert makes browser-account switching
    # safe even when two serverless requests race on different instances.
    values = {
        'user_id': g.current_user.id,
        'endpoint': endpoint,
        'p256dh': p256dh[:255],
        'auth': auth[:255],
    }
    dialect = db.engine.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        return jsonify({'error': 'service_unavailable'}), 503
    table = PushSubscription.__table__
    statement = insert(table).values(**values).on_conflict_do_update(
        index_elements=[table.c.endpoint],
        set_={
            'user_id': values['user_id'],
            'p256dh': values['p256dh'],
            'auth': values['auth'],
            'updated_at': utcnow(),
        },
    )
    db.session.execute(statement)
    db.session.commit()
    return jsonify({'subscribed': True}), 201


@push_bp.post('/push/unsubscribe')
@rate_limit(20, 3600)
@login_required
def unsubscribe():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_subscription'}), 400
    endpoint = str(payload.get('endpoint') or '').strip()
    PushSubscription.query.filter_by(
        user_id=g.current_user.id, endpoint=endpoint,
    ).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'subscribed': False})
