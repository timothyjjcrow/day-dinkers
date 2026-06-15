"""Authentication: register, login, current-user profile."""
import re
import time
from functools import wraps

import jwt
from flask import Blueprint, current_app, g, jsonify, request

from backend.app import db
from backend.models import (
    CheckIn,
    Court,
    Friendship,
    Game,
    GameInvite,
    GamePlayer,
    Message,
    Notification,
    SKILL_LEVELS,
    User,
    utcnow,
)

auth_bp = Blueprint('auth', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _issue_token(user):
    now = int(time.time())
    return jwt.encode(
        {
            'user_id': user.id,
            'iat': now,
            'exp': now + int(current_app.config.get('JWT_TTL_SECONDS', 2592000)),
        },
        current_app.config['SECRET_KEY'],
        algorithm=current_app.config.get('JWT_ALGORITHM', 'HS256'),
    )


def optional_current_user():
    auth_header = str(request.headers.get('Authorization') or '').strip()
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=[current_app.config.get('JWT_ALGORITHM', 'HS256')],
        )
    except Exception:
        return None
    user_id = payload.get('user_id')
    if not user_id:
        return None
    return db.session.get(User, user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = optional_current_user()
        if not user:
            return jsonify({'error': 'authentication_required'}), 401
        g.current_user = user
        return view(*args, **kwargs)
    return wrapped


def active_checkin_for(user_id):
    return (
        CheckIn.query.filter_by(user_id=user_id, checked_out_at=None)
        .order_by(CheckIn.checked_in_at.desc(), CheckIn.id.desc())
        .first()
    )


def presence_payload(user_id):
    checkin = active_checkin_for(user_id)
    if not checkin:
        return {'checked_in': False}
    court = checkin.court
    return {
        'checked_in': True,
        'court_id': checkin.court_id,
        'court_name': court.name if court else 'Court',
        'court_latitude': court.latitude if court else None,
        'court_longitude': court.longitude if court else None,
        'looking_for_game': bool(checkin.looking_for_game),
        'checked_in_at': checkin.checked_in_at.isoformat() + 'Z' if checkin.checked_in_at else None,
    }


def _games_to_confirm_count(user_id):
    """Games whose reported score is waiting on this user (opposing team) to confirm."""
    games = (
        Game.query.filter(Game.status == 'awaiting_confirmation')
        .join(GamePlayer)
        .filter(GamePlayer.user_id == user_id)
        .all()
    )
    count = 0
    for game in games:
        me = next((p for p in game.players if p.user_id == user_id), None)
        submitter = next(
            (p for p in game.players if p.user_id == game.score_submitted_by_id), None,
        )
        if me and submitter and me.team and submitter.team and me.team != submitter.team:
            count += 1
    return count


def _active_game_payload(user):
    """The single most relevant game for the banner.

    Priority: live game you're in > incoming challenge > score waiting on you
    > your score waiting on opponents > your next upcoming game."""
    now = utcnow()
    candidates = []

    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status.in_(['upcoming', 'awaiting_confirmation']),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(25)
        .all()
    )
    for game in games:
        data = game.to_dict(user.id)
        if game.status == 'upcoming' and game.scheduled_at <= now:
            rank, banner_state = 0, 'live'
        elif data['awaiting_your_confirmation']:
            rank, banner_state = 2, 'confirm'
        elif game.status == 'awaiting_confirmation':
            rank, banner_state = 4, 'waiting'
        else:
            rank, banner_state = 5, 'upcoming'
        data['banner_state'] = banner_state
        candidates.append((rank, data))

    # Private games you've been invited to (challenges + personal invites) and
    # haven't joined yet. The invite list is the source of truth, not notifications.
    invited_games = (
        Game.query.join(GameInvite)
        .filter(GameInvite.user_id == user.id, Game.status == 'upcoming')
        .order_by(Game.scheduled_at.asc())
        .limit(15)
        .all()
    )
    for game in invited_games:
        data = game.to_dict(user.id)
        if data['is_joined'] or data['spots_left'] <= 0:
            continue
        is_challenge = game.notes.startswith('⚔️')
        if is_challenge:
            data['banner_state'] = 'challenge'
            candidates.append((1, data))
        else:
            data['banner_state'] = 'invited'
            candidates.append((3, data))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]['scheduled_at'] or ''))
    return candidates[0][1]


def _me_payload(user):
    unread_messages = Message.query.filter_by(recipient_id=user.id, read_at=None).count()
    pending_requests = Friendship.query.filter_by(
        addressee_id=user.id, status='pending',
    ).count()
    unread_notifications = Notification.query.filter_by(
        user_id=user.id, read=False,
    ).count()
    latest = (
        Notification.query.filter_by(user_id=user.id)
        .order_by(Notification.id.desc())
        .first()
    )
    return {
        'user': user.to_dict(),
        'presence': presence_payload(user.id),
        'unread_messages': unread_messages,
        'pending_friend_requests': pending_requests,
        'unread_notifications': unread_notifications,
        'games_to_confirm': _games_to_confirm_count(user.id),
        'latest_notification': latest.to_dict() if latest else None,
        'active_game': _active_game_payload(user),
    }


@auth_bp.post('/auth/register')
def register():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email') or '').strip().lower()
    password = str(payload.get('password') or '')
    display_name = str(payload.get('display_name') or '').strip()

    if not _EMAIL_RE.match(email):
        return jsonify({'error': 'invalid_email'}), 400
    if len(password) < 6:
        return jsonify({'error': 'password_too_short'}), 400
    if not display_name:
        return jsonify({'error': 'display_name_required'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'email_taken'}), 409

    user = User(email=email, display_name=display_name[:120])
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({'token': _issue_token(user), **_me_payload(user)}), 201


@auth_bp.post('/auth/login')
def login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email') or '').strip().lower()
    password = str(payload.get('password') or '')

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'invalid_credentials'}), 401
    return jsonify({'token': _issue_token(user), **_me_payload(user)})


@auth_bp.get('/me')
@login_required
def me():
    return jsonify(_me_payload(g.current_user))


@auth_bp.patch('/me')
@login_required
def update_me():
    user = g.current_user
    payload = request.get_json(silent=True) or {}

    if 'display_name' in payload:
        name = str(payload.get('display_name') or '').strip()
        if not name:
            return jsonify({'error': 'display_name_required'}), 400
        user.display_name = name[:120]
    if 'bio' in payload:
        user.bio = str(payload.get('bio') or '').strip()[:500]
    if 'skill_level' in payload:
        level = str(payload.get('skill_level') or '').strip().lower()
        if level not in SKILL_LEVELS:
            return jsonify({'error': 'invalid_skill_level'}), 400
        user.skill_level = level
    if 'avatar_color' in payload:
        color = str(payload.get('avatar_color') or '').strip()
        if not re.match(r'^#[0-9a-fA-F]{6}$', color):
            return jsonify({'error': 'invalid_avatar_color'}), 400
        user.avatar_color = color
    if 'home_court_id' in payload:
        court_id = payload.get('home_court_id')
        if court_id in (None, '', 0):
            user.home_court_id = None
        else:
            court = db.session.get(Court, int(court_id))
            if not court:
                return jsonify({'error': 'court_not_found'}), 404
            user.home_court_id = court.id

    db.session.commit()
    return jsonify(_me_payload(user))
