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


def login_required(f):
    """Decorator to require authentication on a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
        try:
            payload = jwt.decode(
                token, current_app.config['SECRET_KEY'], algorithms=['HS256']
            )
            user = db.session.get(User, payload['user_id'])
            if not user:
                return jsonify({'error': 'User not found'}), 401
            request.current_user = user
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
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
