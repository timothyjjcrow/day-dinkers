from datetime import timedelta
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import and_, or_
from backend.app import db, socketio
from backend.models import CheckIn, Notification, Friendship, PlaySession, Court
from backend.auth_utils import login_required
from backend.time_utils import utcnow_naive

presence_bp = Blueprint('presence', __name__)


def _broadcast_presence_update(payload):
    socketio.emit('presence_update', payload)


def _presence_timeout_cutoff():
    timeout_minutes = current_app.config.get('PRESENCE_HEARTBEAT_TIMEOUT_MINUTES', 20)
    try:
        timeout_minutes = int(timeout_minutes)
    except (TypeError, ValueError):
        timeout_minutes = 20
    timeout_minutes = max(1, timeout_minutes)
    return utcnow_naive() - timedelta(minutes=timeout_minutes)


def _complete_active_now_sessions_for_user(user_id):
    now_sessions = PlaySession.query.filter_by(
        creator_id=user_id,
        status='active',
        session_type='now',
    ).all()
    for session in now_sessions:
        session.status = 'completed'


def _cleanup_stale_presence():
    cutoff = _presence_timeout_cutoff()
    stale_checkins = CheckIn.query.filter(
        CheckIn.checked_out_at.is_(None),
        or_(
            and_(
                CheckIn.last_presence_ping_at.isnot(None),
                CheckIn.last_presence_ping_at < cutoff,
            ),
            and_(
                CheckIn.last_presence_ping_at.is_(None),
                CheckIn.checked_in_at < cutoff,
            ),
        ),
    ).all()
    if not stale_checkins:
        return 0

    now = utcnow_naive()
    stale_user_ids = set()
    for checkin in stale_checkins:
        checkin.checked_out_at = now
        stale_user_ids.add(checkin.user_id)

    for user_id in stale_user_ids:
        _complete_active_now_sessions_for_user(user_id)

    db.session.commit()
    return len(stale_checkins)


def _touch_presence_ping(checkin):
    checkin.last_presence_ping_at = utcnow_naive()


@presence_bp.route('/checkin', methods=['POST'])
@login_required
def check_in():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None

    if not court_id:
        return jsonify({'error': 'Court ID is required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    # Check out from any existing check-in
    active = CheckIn.query.filter_by(
        user_id=request.current_user.id, checked_out_at=None
    ).all()
    for ci in active:
        ci.checked_out_at = utcnow_naive()

    # Also end any active "now" sessions at old courts
    _complete_active_now_sessions_for_user(request.current_user.id)

    checkin = CheckIn(
        user_id=request.current_user.id,
        court_id=court_id,
        last_presence_ping_at=utcnow_naive(),
    )
    db.session.add(checkin)

    # Notify friends
    friend_ids = _get_friend_ids(request.current_user.id)
    for fid in friend_ids:
        notif = Notification(
            user_id=fid, notif_type='checkin',
            content=f'{request.current_user.username} checked in at a court',
            reference_id=court_id,
        )
        db.session.add(notif)

    db.session.commit()

    _broadcast_presence_update({
        'user': request.current_user.to_public_dict(),
        'court_id': court_id, 'action': 'checkin'
    })

    return jsonify({'message': 'Checked in', 'checkin_id': checkin.id}), 201


@presence_bp.route('/checkout', methods=['POST'])
@login_required
def check_out():
    active = CheckIn.query.filter_by(
        user_id=request.current_user.id, checked_out_at=None
    ).first()
    if not active:
        return jsonify({'error': 'No active check-in'}), 400

    court_id = active.court_id
    active.checked_out_at = utcnow_naive()

    # Auto-end any active "now" sessions by this user
    _complete_active_now_sessions_for_user(request.current_user.id)

    db.session.commit()

    _broadcast_presence_update({
        'user': request.current_user.to_public_dict(),
        'court_id': court_id, 'action': 'checkout'
    })

    return jsonify({'message': 'Checked out', 'court_id': court_id})


@presence_bp.route('/ping', methods=['POST'])
@login_required
def presence_ping():
    active = CheckIn.query.filter_by(
        user_id=request.current_user.id, checked_out_at=None
    ).first()
    if not active:
        return jsonify({'checked_in': False})

    _touch_presence_ping(active)
    db.session.commit()
    return jsonify({
        'checked_in': True,
        'court_id': active.court_id,
        'checked_in_at': active.checked_in_at.isoformat() if active.checked_in_at else None,
        'last_presence_ping_at': active.last_presence_ping_at.isoformat() if active.last_presence_ping_at else None,
    })


@presence_bp.route('/lfg', methods=['POST'])
@login_required
def toggle_looking_for_game():
    """Toggle 'looking for game' status for the current check-in."""
    active = CheckIn.query.filter_by(
        user_id=request.current_user.id, checked_out_at=None
    ).first()
    if not active:
        return jsonify({'error': 'Not checked in anywhere'}), 400

    active.looking_for_game = not active.looking_for_game
    db.session.commit()

    _broadcast_presence_update({
        'user': request.current_user.to_public_dict(),
        'court_id': active.court_id,
        'action': 'lfg_toggle',
        'looking_for_game': active.looking_for_game,
    })

    status = 'looking for a game' if active.looking_for_game else 'not looking'
    return jsonify({
        'message': f'Now {status}',
        'looking_for_game': active.looking_for_game,
    })


@presence_bp.route('/status', methods=['GET'])
@login_required
def get_my_status():
    _cleanup_stale_presence()
    active = CheckIn.query.filter_by(
        user_id=request.current_user.id, checked_out_at=None
    ).first()
    if not active:
        return jsonify({'checked_in': False})
    return jsonify({
        'checked_in': True,
        'court_id': active.court_id,
        'looking_for_game': active.looking_for_game,
        'checked_in_at': active.checked_in_at.isoformat(),
        'last_presence_ping_at': active.last_presence_ping_at.isoformat() if active.last_presence_ping_at else None,
    })


@presence_bp.route('/active', methods=['GET'])
def get_active_checkins():
    _cleanup_stale_presence()
    active = CheckIn.query.filter_by(checked_out_at=None).all()
    result = {}
    for ci in active:
        cid = ci.court_id
        if cid not in result:
            result[cid] = {'court_id': cid, 'count': 0, 'users': []}
        result[cid]['count'] += 1
        result[cid]['users'].append(ci.user.to_public_dict())
    return jsonify({'active': list(result.values())})


@presence_bp.route('/friends', methods=['GET'])
@login_required
def get_friends_presence():
    _cleanup_stale_presence()
    friend_ids = _get_friend_ids(request.current_user.id)
    active = CheckIn.query.filter(
        CheckIn.user_id.in_(friend_ids),
        CheckIn.checked_out_at.is_(None)
    ).all()
    result = [{
        'user': ci.user.to_public_dict(),
        'court_id': ci.court_id,
        'checked_in_at': ci.checked_in_at.isoformat(),
    } for ci in active]
    return jsonify({'friends_presence': result})


def _get_friend_ids(user_id):
    friendships = Friendship.query.filter(
        ((Friendship.user_id == user_id) | (Friendship.friend_id == user_id))
        & (Friendship.status == 'accepted')
    ).all()
    return [
        f.friend_id if f.user_id == user_id else f.user_id
        for f in friendships
    ]
