import hashlib
import re
import secrets
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from backend.app import db
from backend.models import (
    User, Friendship, Notification, PlaySession, PlaySessionPlayer, CheckIn,
    RateLimitBucket,
)
from backend.auth_utils import generate_token, login_required, csrf_token_for_bearer
from backend.time_utils import utcnow_naive

auth_bp = Blueprint('auth', __name__)
_RATE_LIMIT_PRUNE_INTERVAL_SECONDS = 300
_last_rate_limit_prune_ts = 0.0

_GOOGLE_TOKEN_INFO_URL = 'https://oauth2.googleapis.com/tokeninfo'
_ALLOWED_GOOGLE_ISSUERS = {'accounts.google.com', 'https://accounts.google.com'}


def _configured_admin_emails():
    raw_value = current_app.config.get('ADMIN_EMAILS', '')
    return {
        item.strip().lower()
        for item in str(raw_value).split(',')
        if item and item.strip()
    }


def _is_configured_admin_email(email):
    normalized = (email or '').strip().lower()
    return normalized in _configured_admin_emails()


def _maybe_grant_admin_from_config(user):
    if not user or user.is_admin:
        return False
    if not _is_configured_admin_email(user.email):
        return False
    user.is_admin = True
    return True


def _normalize_username_base(raw_value):
    cleaned = re.sub(r'[^a-zA-Z0-9_]+', '', str(raw_value or '').strip().lower())
    if not cleaned:
        cleaned = f'user{secrets.randbelow(100000):05d}'
    if cleaned[0].isdigit():
        cleaned = f'u_{cleaned}'
    return cleaned[:70]


def _build_unique_username(raw_value):
    base = _normalize_username_base(raw_value)
    candidate = base
    suffix = 1
    while User.query.filter_by(username=candidate).first():
        suffix += 1
        candidate_base = base[:max(1, 79 - len(str(suffix)))]
        candidate = f'{candidate_base}{suffix}'
    return candidate


def _verify_google_id_token(id_token):
    client_id = str(current_app.config.get('GOOGLE_CLIENT_ID') or '').strip()
    if not client_id:
        return None, ('Google login is not configured', 503)

    try:
        response = requests.get(
            _GOOGLE_TOKEN_INFO_URL,
            params={'id_token': id_token},
            timeout=5,
        )
    except requests.RequestException:
        return None, ('Unable to verify Google token', 502)

    if response.status_code != 200:
        return None, ('Invalid Google token', 401)

    try:
        token_info = response.json()
    except ValueError:
        return None, ('Invalid Google verification response', 502)

    aud = str(token_info.get('aud') or '').strip()
    if aud != client_id:
        return None, ('Invalid Google token audience', 401)

    iss = str(token_info.get('iss') or '').strip()
    if iss not in _ALLOWED_GOOGLE_ISSUERS:
        return None, ('Invalid Google token issuer', 401)

    try:
        exp_ts = int(token_info.get('exp'))
    except (TypeError, ValueError):
        return None, ('Invalid Google token expiration', 401)
    if exp_ts <= int(time.time()):
        return None, ('Google token expired', 401)

    email_verified = str(token_info.get('email_verified') or '').strip().lower()
    if email_verified not in {'true', '1'}:
        return None, ('Google account email is not verified', 401)

    if not token_info.get('sub') or not token_info.get('email'):
        return None, ('Google token missing required fields', 401)

    return token_info, None


def _client_ip_key():
    forwarded_for = str(request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    if forwarded_for:
        return forwarded_for[:80]
    return str(request.remote_addr or 'unknown')[:80]


def _window_started_at(now, window_seconds):
    window = max(1, int(window_seconds))
    now_epoch = int(now.timestamp())
    window_epoch = now_epoch - (now_epoch % window)
    return datetime.fromtimestamp(window_epoch, tz=timezone.utc).replace(tzinfo=None)


def _prune_expired_rate_limits(now):
    global _last_rate_limit_prune_ts
    now_ts = float(now.timestamp())
    if now_ts - _last_rate_limit_prune_ts < _RATE_LIMIT_PRUNE_INTERVAL_SECONDS:
        return
    _last_rate_limit_prune_ts = now_ts
    RateLimitBucket.query.filter(
        RateLimitBucket.expires_at < now,
    ).delete(synchronize_session=False)
    db.session.commit()


def _consume_rate_limit(scope, max_requests, window_seconds):
    if max_requests is None or int(max_requests) <= 0:
        return None
    if window_seconds is None or int(window_seconds) <= 0:
        return None

    max_requests_int = int(max_requests)
    window_seconds_int = int(window_seconds)
    now = utcnow_naive()
    actor_key = _client_ip_key()
    window_start = _window_started_at(now, window_seconds_int)
    window_end = window_start + timedelta(seconds=window_seconds_int)
    expires_at = window_end + timedelta(seconds=window_seconds_int)

    try:
        _prune_expired_rate_limits(now)
    except Exception:
        db.session.rollback()

    for _ in range(3):
        bucket = RateLimitBucket.query.filter_by(
            scope=str(scope),
            actor_key=actor_key,
            window_started_at=window_start,
        ).first()
        if not bucket:
            bucket = RateLimitBucket(
                scope=str(scope),
                actor_key=actor_key,
                window_started_at=window_start,
                window_seconds=window_seconds_int,
                request_count=1,
                expires_at=expires_at,
            )
            db.session.add(bucket)
            try:
                db.session.commit()
                return None
            except IntegrityError:
                db.session.rollback()
                continue

        current_count = int(bucket.request_count or 0)
        if current_count >= max_requests_int:
            retry_after = max(1, int((window_end - now).total_seconds()) + 1)
            return retry_after

        bucket.request_count = current_count + 1
        bucket.window_seconds = window_seconds_int
        if not bucket.expires_at or bucket.expires_at < expires_at:
            bucket.expires_at = expires_at
        try:
            db.session.commit()
            return None
        except IntegrityError:
            db.session.rollback()
            continue

    return 1


def _password_complexity_error(raw_password):
    password = str(raw_password or '')
    if len(password) < 8:
        return 'Password must be at least 8 characters long'
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return 'Password must include at least one letter and one number'
    return None


def _lockout_threshold():
    raw_value = current_app.config.get('AUTH_LOCKOUT_THRESHOLD', 5)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 5
    return max(1, parsed)


def _lockout_minutes():
    raw_value = current_app.config.get('AUTH_LOCKOUT_MINUTES', 15)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 15
    return max(1, parsed)


def _password_reset_ttl_minutes():
    raw_value = current_app.config.get('PASSWORD_RESET_TOKEN_TTL_MINUTES', 30)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 30
    return max(1, parsed)


def _hash_reset_token(raw_token):
    return hashlib.sha256(str(raw_token or '').encode('utf-8')).hexdigest()


def _clear_login_lock_state(user):
    changed = False
    if user.failed_login_attempts:
        user.failed_login_attempts = 0
        changed = True
    if user.locked_until is not None:
        user.locked_until = None
        changed = True
    return changed


def _active_lockout_retry_seconds(user):
    if not user or not user.locked_until:
        return 0
    now = utcnow_naive()
    if user.locked_until <= now:
        return 0
    remaining = (user.locked_until - now).total_seconds()
    return max(1, int(remaining))


def _record_failed_login_attempt(user):
    now = utcnow_naive()
    if user.locked_until and user.locked_until <= now:
        user.locked_until = None
        user.failed_login_attempts = 0

    attempts = int(user.failed_login_attempts or 0) + 1
    user.failed_login_attempts = attempts
    if attempts >= _lockout_threshold():
        user.locked_until = now + timedelta(minutes=_lockout_minutes())


def _find_or_create_google_user(token_info):
    google_sub = str(token_info.get('sub') or '').strip()
    email = str(token_info.get('email') or '').strip().lower()
    name = str(token_info.get('name') or '').strip()
    photo_url = str(token_info.get('picture') or '').strip()

    user = User.query.filter_by(google_sub=google_sub).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user and user.google_sub and user.google_sub != google_sub:
            return None, ('Email is already linked to another Google account', 409)
        if user:
            user.google_sub = google_sub
        else:
            username_seed = email.split('@', 1)[0] if '@' in email else name
            user = User(
                username=_build_unique_username(username_seed),
                email=email,
                password_hash=generate_password_hash(secrets.token_urlsafe(32)),
                google_sub=google_sub,
                is_admin=_is_configured_admin_email(email),
                name=name,
                photo_url=photo_url,
            )
            db.session.add(user)

    if name and not user.name:
        user.name = name
    if photo_url and not user.photo_url:
        user.photo_url = photo_url
    _maybe_grant_admin_from_config(user)

    db.session.commit()
    return user, None


@auth_bp.route('/google/config', methods=['GET'])
def google_config():
    client_id = str(current_app.config.get('GOOGLE_CLIENT_ID') or '').strip()
    return jsonify({
        'enabled': bool(client_id),
        'client_id': client_id if client_id else None,
    })


@auth_bp.route('/google', methods=['POST'])
def google_login():
    rate_window = current_app.config.get('AUTH_RATE_LIMIT_WINDOW_SECONDS', 60)
    rate_max = current_app.config.get('AUTH_GOOGLE_RATE_LIMIT_PER_WINDOW', 30)
    retry_after = _consume_rate_limit('google_login', rate_max, rate_window)
    if retry_after:
        return jsonify({
            'error': 'Too many Google login attempts. Please try again later.',
            'retry_after_seconds': retry_after,
        }), 429

    data = request.get_json() or {}
    id_token = str(data.get('id_token') or '').strip()
    if not id_token:
        return jsonify({'error': 'Google ID token is required'}), 400

    token_info, token_error = _verify_google_id_token(id_token)
    if token_error:
        message, status = token_error
        return jsonify({'error': message}), status

    user, user_error = _find_or_create_google_user(token_info)
    if user_error:
        message, status = user_error
        return jsonify({'error': message}), status

    token = generate_token(user.id)
    return jsonify({'token': token, 'user': user.to_dict()})


@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Username, email, and password are required'}), 400

    username = str(data['username']).strip()
    email = str(data['email']).strip().lower()
    password_error = _password_complexity_error(data.get('password'))
    if password_error:
        return jsonify({'error': password_error}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    user = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(data['password']),
        is_admin=_is_configured_admin_email(email),
        name=data.get('name', ''),
        skill_level=data.get('skill_level'),
        play_style=data.get('play_style', ''),
        preferred_times=data.get('preferred_times', ''),
    )
    db.session.add(user)
    db.session.commit()
    token = generate_token(user.id)
    return jsonify({'token': token, 'user': user.to_dict()}), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Email and password are required'}), 400

    email = str(data['email']).strip().lower()
    rate_window = current_app.config.get('AUTH_RATE_LIMIT_WINDOW_SECONDS', 60)
    rate_max = current_app.config.get('AUTH_LOGIN_RATE_LIMIT_PER_WINDOW', 30)
    retry_after = _consume_rate_limit(f'login:{email}', rate_max, rate_window)
    if retry_after:
        return jsonify({
            'error': 'Too many login attempts. Please try again later.',
            'retry_after_seconds': retry_after,
        }), 429

    user = User.query.filter_by(email=email).first()
    if user:
        locked_retry_after = _active_lockout_retry_seconds(user)
        if locked_retry_after:
            return jsonify({
                'error': 'Account is temporarily locked due to repeated failed logins.',
                'retry_after_seconds': locked_retry_after,
            }), 423

    if not user or not check_password_hash(user.password_hash, data['password']):
        if user:
            _record_failed_login_attempt(user)
            db.session.commit()
            locked_retry_after = _active_lockout_retry_seconds(user)
            if locked_retry_after:
                return jsonify({
                    'error': 'Account is temporarily locked due to repeated failed logins.',
                    'retry_after_seconds': locked_retry_after,
                }), 423
        return jsonify({'error': 'Invalid email or password'}), 401

    profile_changed = _clear_login_lock_state(user)
    if _maybe_grant_admin_from_config(user):
        profile_changed = True
    if profile_changed:
        db.session.commit()

    token = generate_token(user.id)
    return jsonify({'token': token, 'user': user.to_dict()})


@auth_bp.route('/password-reset/request', methods=['POST'])
def request_password_reset():
    rate_window = current_app.config.get('AUTH_RATE_LIMIT_WINDOW_SECONDS', 60)
    rate_max = current_app.config.get('PASSWORD_RESET_REQUEST_RATE_LIMIT_PER_WINDOW', 10)
    retry_after = _consume_rate_limit('password_reset_request', rate_max, rate_window)
    if retry_after:
        return jsonify({
            'error': 'Too many password reset requests. Please try again later.',
            'retry_after_seconds': retry_after,
        }), 429

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    email = str(data.get('email') or '').strip().lower()
    response_payload = {
        'message': 'If an account exists for that email, password reset instructions have been sent.',
    }
    if not email:
        return jsonify(response_payload)

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify(response_payload)

    reset_token = secrets.token_urlsafe(32)
    user.password_reset_token_hash = _hash_reset_token(reset_token)
    user.password_reset_token_expires_at = utcnow_naive() + timedelta(
        minutes=_password_reset_ttl_minutes(),
    )
    db.session.add(Notification(
        user_id=user.id,
        notif_type='password_reset_requested',
        content='A password reset was requested for your account.',
        reference_id=user.id,
    ))
    db.session.commit()

    if current_app.config.get('TESTING'):
        response_payload['reset_token'] = reset_token
    return jsonify(response_payload)


@auth_bp.route('/password-reset/confirm', methods=['POST'])
def confirm_password_reset():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    reset_token = str(data.get('token') or '').strip()
    new_password = data.get('new_password')
    if not reset_token or not str(new_password or '').strip():
        return jsonify({'error': 'Reset token and new password are required'}), 400

    password_error = _password_complexity_error(new_password)
    if password_error:
        return jsonify({'error': password_error}), 400

    token_hash = _hash_reset_token(reset_token)
    user = User.query.filter_by(password_reset_token_hash=token_hash).first()
    if not user:
        return jsonify({'error': 'Invalid or expired reset token'}), 400

    now = utcnow_naive()
    if not user.password_reset_token_expires_at or user.password_reset_token_expires_at <= now:
        user.password_reset_token_hash = None
        user.password_reset_token_expires_at = None
        db.session.commit()
        return jsonify({'error': 'Invalid or expired reset token'}), 400

    user.password_hash = generate_password_hash(str(new_password))
    user.password_reset_token_hash = None
    user.password_reset_token_expires_at = None
    _clear_login_lock_state(user)
    db.session.add(Notification(
        user_id=user.id,
        notif_type='password_reset_completed',
        content='Your password was reset successfully.',
        reference_id=user.id,
    ))
    db.session.commit()
    return jsonify({'message': 'Password reset successful'})


@auth_bp.route('/csrf', methods=['GET'])
@login_required
def get_csrf_token():
    auth_header = request.headers.get('Authorization', '')
    token = csrf_token_for_bearer(auth_header)
    if not token:
        return jsonify({'error': 'Unable to generate CSRF token'}), 400
    return jsonify({'csrf_token': token})


@auth_bp.route('/profile', methods=['GET'])
@login_required
def get_profile():
    user = request.current_user
    profile = user.to_dict()
    # Include stats
    profile['total_checkins'] = CheckIn.query.filter_by(user_id=user.id).count()
    profile['upcoming_games'] = db.session.query(PlaySessionPlayer.id).join(
        PlaySession,
        PlaySession.id == PlaySessionPlayer.session_id,
    ).filter(
        PlaySessionPlayer.user_id == user.id,
        PlaySessionPlayer.status == 'joined',
        PlaySession.status == 'active',
    ).count()
    friends_count = Friendship.query.filter(
        ((Friendship.user_id == user.id) | (Friendship.friend_id == user.id))
        & (Friendship.status == 'accepted')
    ).count()
    profile['friends_count'] = friends_count
    return jsonify({'user': profile})


@auth_bp.route('/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    user = request.current_user

    text_fields = {
        'name': 120,
        'bio': 2000,
        'photo_url': 500,
        'play_style': 50,
        'preferred_times': 200,
    }
    for field, max_len in text_fields.items():
        if field not in data:
            continue
        raw_value = data.get(field)
        if raw_value is None:
            setattr(user, field, '')
            continue
        cleaned = str(raw_value).strip()[:max_len]
        setattr(user, field, cleaned)

    if 'skill_level' in data:
        raw_skill = data.get('skill_level')
        if raw_skill in (None, ''):
            user.skill_level = None
        else:
            try:
                parsed = float(raw_skill)
            except (TypeError, ValueError):
                return jsonify({'error': 'Skill level must be a number'}), 400
            if parsed < 1.0 or parsed > 6.0:
                return jsonify({'error': 'Skill level must be between 1.0 and 6.0'}), 400
            user.skill_level = parsed

    db.session.commit()
    return jsonify({'user': user.to_dict()})


@auth_bp.route('/profile/<int:user_id>', methods=['GET'])
def get_user_profile(user_id):
    """View another user's public profile."""
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    profile = user.to_public_dict()
    profile['total_checkins'] = CheckIn.query.filter_by(user_id=user.id).count()
    return jsonify({'user': profile})


@auth_bp.route('/friends', methods=['GET'])
@login_required
def get_friends():
    user_id = request.current_user.id
    friendships = Friendship.query.filter(
        ((Friendship.user_id == user_id) | (Friendship.friend_id == user_id))
        & (Friendship.status == 'accepted')
    ).all()
    friends = []
    for f in friendships:
        friend = f.friend if f.user_id == user_id else f.user
        friends.append(friend.to_public_dict())
    return jsonify({'friends': friends})


@auth_bp.route('/friends/request', methods=['POST'])
@login_required
def send_friend_request():
    data = request.get_json()
    friend_id = data.get('friend_id')
    if not friend_id or friend_id == request.current_user.id:
        return jsonify({'error': 'Invalid friend ID'}), 400

    existing = Friendship.query.filter(
        ((Friendship.user_id == request.current_user.id) & (Friendship.friend_id == friend_id))
        | ((Friendship.user_id == friend_id) & (Friendship.friend_id == request.current_user.id))
    ).first()
    if existing:
        return jsonify({'error': 'Friend request already exists'}), 409

    friendship = Friendship(user_id=request.current_user.id, friend_id=friend_id)
    notif = Notification(
        user_id=friend_id, notif_type='friend_request',
        content=f'{request.current_user.username} sent you a friend request',
        reference_id=request.current_user.id,
    )
    db.session.add_all([friendship, notif])
    db.session.commit()
    return jsonify({'message': 'Friend request sent'}), 201


@auth_bp.route('/friends/respond', methods=['POST'])
@login_required
def respond_friend_request():
    data = request.get_json()
    friendship_id = data.get('friendship_id')
    action = data.get('action')

    friendship = db.session.get(Friendship, friendship_id)
    if not friendship or friendship.friend_id != request.current_user.id:
        return jsonify({'error': 'Friend request not found'}), 404

    friendship.status = 'accepted' if action == 'accept' else 'declined'
    db.session.commit()
    return jsonify({'message': f'Friend request {friendship.status}'})


@auth_bp.route('/friends/pending', methods=['GET'])
@login_required
def get_pending_requests():
    pending = Friendship.query.filter_by(
        friend_id=request.current_user.id, status='pending'
    ).all()
    requests_list = [{
        'id': f.id, 'user': f.user.to_public_dict(), 'created_at': f.created_at.isoformat()
    } for f in pending]
    return jsonify({'requests': requests_list})


@auth_bp.route('/users/search', methods=['GET'])
@login_required
def search_users():
    rate_window = current_app.config.get('AUTH_RATE_LIMIT_WINDOW_SECONDS', 60)
    rate_max = current_app.config.get('USER_SEARCH_RATE_LIMIT_PER_WINDOW', 60)
    retry_after = _consume_rate_limit(
        f'user_search:{request.current_user.id}',
        rate_max,
        rate_window,
    )
    if retry_after:
        return jsonify({
            'error': 'Too many search requests. Please try again later.',
            'retry_after_seconds': retry_after,
        }), 429

    q = request.args.get('q', '')
    if len(q) < 2:
        return jsonify({'users': []})
    users = User.query.filter(
        (User.username.ilike(f'%{q}%')) | (User.name.ilike(f'%{q}%'))
    ).limit(20).all()
    return jsonify({
        'users': [u.to_public_dict() for u in users if u.id != request.current_user.id]
    })


@auth_bp.route('/notifications', methods=['GET'])
@login_required
def get_notifications():
    notifs = Notification.query.filter_by(
        user_id=request.current_user.id
    ).order_by(Notification.created_at.desc()).limit(50).all()
    return jsonify({'notifications': [n.to_dict() for n in notifs]})


@auth_bp.route('/notifications/read', methods=['POST'])
@login_required
def mark_notifications_read():
    Notification.query.filter_by(
        user_id=request.current_user.id, read=False
    ).update({'read': True})
    db.session.commit()
    return jsonify({'message': 'Notifications marked as read'})
