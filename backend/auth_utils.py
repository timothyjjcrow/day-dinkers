import hashlib
import hmac
from functools import wraps
from flask import request, jsonify, current_app
import jwt
from backend.app import db
from backend.models import User


def generate_token(user_id):
    """Generate a JWT token for a user."""
    from datetime import datetime, timedelta, timezone
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(
            hours=current_app.config.get('JWT_EXPIRATION_HOURS', 24)
        ),
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')


def _normalize_bearer_token(raw_token):
    token = str(raw_token or '').strip()
    if token.startswith('Bearer '):
        token = token.split(' ', 1)[1].strip()
    return token


def _decode_user_from_token(token):
    normalized = _normalize_bearer_token(token)
    if not normalized:
        return None, 'Authentication required'
    try:
        payload = jwt.decode(
            normalized, current_app.config['SECRET_KEY'], algorithms=['HS256']
        )
        user = db.session.get(User, payload['user_id'])
        if not user:
            return None, 'User not found'
        return user, None
    except jwt.ExpiredSignatureError:
        return None, 'Token expired'
    except jwt.InvalidTokenError:
        return None, 'Invalid token'


def get_user_from_token(token):
    """Resolve a user from a raw JWT/bearer token value."""
    user, _ = _decode_user_from_token(token)
    return user


def csrf_token_for_bearer(token):
    """Build deterministic CSRF token tied to bearer token."""
    normalized = _normalize_bearer_token(token)
    if not normalized:
        return ''
    secret = str(current_app.config.get('SECRET_KEY') or '')
    if not secret:
        return ''
    return hmac.new(
        secret.encode('utf-8'),
        normalized.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def csrf_token_matches(token, candidate):
    expected = csrf_token_for_bearer(token)
    provided = str(candidate or '').strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def login_required(f):
    """Decorator to require authentication on a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        token = _normalize_bearer_token(auth_header)
        user, error = _decode_user_from_token(token)
        if error:
            return jsonify({'error': error}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator to require an authenticated admin user on a route."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(request.current_user, 'is_admin', False):
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated
