from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from backend.app import db, socketio
from backend.models import CheckIn, Notification, Friendship, PlaySession, Court
from backend.auth_utils import login_required

presence_bp = Blueprint('presence', __name__)


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
        ci.checked_out_at = datetime.now(timezone.utc)

    # Also end any active "now" sessions at old courts
    old_sessions = PlaySession.query.filter_by(
        creator_id=request.current_user.id,
        status='active', session_type='now',
    ).all()
    for s in old_sessions:
        s.status = 'completed'

    checkin = CheckIn(user_id=request.current_user.id, court_id=court_id)
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

    socketio.emit('presence_update', {
        'user': request.current_user.to_dict(),
        'court_id': court_id, 'action': 'checkin'
    }, room=f'court_{court_id}')

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
    active.checked_out_at = datetime.now(timezone.utc)

    # Auto-end any active "now" sessions by this user
    now_sessions = PlaySession.query.filter_by(
        creator_id=request.current_user.id,
        status='active', session_type='now',
    ).all()
    for s in now_sessions:
        s.status = 'completed'

    db.session.commit()

    socketio.emit('presence_update', {
        'user': request.current_user.to_dict(),
        'court_id': court_id, 'action': 'checkout'
    }, room=f'court_{court_id}')

    return jsonify({'message': 'Checked out', 'court_id': court_id})


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

    socketio.emit('presence_update', {
        'user': request.current_user.to_dict(),
        'court_id': active.court_id,
        'action': 'lfg_toggle',
        'looking_for_game': active.looking_for_game,
    }, room=f'court_{active.court_id}')

    status = 'looking for a game' if active.looking_for_game else 'not looking'
    return jsonify({
        'message': f'Now {status}',
        'looking_for_game': active.looking_for_game,
    })


@presence_bp.route('/status', methods=['GET'])
@login_required
def get_my_status():
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
    })


@presence_bp.route('/active', methods=['GET'])
def get_active_checkins():
    active = CheckIn.query.filter_by(checked_out_at=None).all()
    result = {}
    for ci in active:
        cid = ci.court_id
        if cid not in result:
            result[cid] = {'court_id': cid, 'count': 0, 'users': []}
        result[cid]['count'] += 1
        result[cid]['users'].append(ci.user.to_dict())
    return jsonify({'active': list(result.values())})


@presence_bp.route('/friends', methods=['GET'])
@login_required
def get_friends_presence():
    friend_ids = _get_friend_ids(request.current_user.id)
    active = CheckIn.query.filter(
        CheckIn.user_id.in_(friend_ids),
        CheckIn.checked_out_at.is_(None)
    ).all()
    result = [{
        'user': ci.user.to_dict(),
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
