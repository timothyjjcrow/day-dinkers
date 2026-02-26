import re

from flask import Blueprint, request, jsonify
from flask_socketio import emit, join_room, leave_room
from backend.app import db, socketio
from backend.models import Message, Game, PlaySession, PlaySessionPlayer, Friendship, User
from backend.auth_utils import login_required, get_user_from_token

chat_bp = Blueprint('chat', __name__)
_ROOM_PATTERN = re.compile(r'^(court|session|user|game)_(\d+)$')


def _get_friend_ids(user_id):
    friendships = Friendship.query.filter(
        ((Friendship.user_id == user_id) | (Friendship.friend_id == user_id))
        & (Friendship.status == 'accepted')
    ).all()
    friend_ids = set()
    for friendship in friendships:
        friend_ids.add(friendship.friend_id if friendship.user_id == user_id else friendship.user_id)
    return friend_ids


def _are_friends(user_a_id, user_b_id):
    if user_a_id == user_b_id:
        return True
    return Friendship.query.filter(
        (
            (Friendship.user_id == user_a_id)
            & (Friendship.friend_id == user_b_id)
        )
        | (
            (Friendship.user_id == user_b_id)
            & (Friendship.friend_id == user_a_id)
        ),
        Friendship.status == 'accepted',
    ).first() is not None


def _can_view_session(session, current_user_id):
    if session.visibility != 'friends':
        return True
    if session.creator_id == current_user_id:
        return True
    if PlaySessionPlayer.query.filter_by(session_id=session.id, user_id=current_user_id).first():
        return True
    return session.creator_id in _get_friend_ids(current_user_id)


def _authorize_socket_join(room, token):
    user = get_user_from_token(token)
    if not user:
        return None, 'Authentication required'

    room_match = _ROOM_PATTERN.match(room)
    if not room_match:
        return None, 'Invalid room'

    room_type = room_match.group(1)
    room_id = int(room_match.group(2))

    if room_type == 'user' and room_id != user.id:
        return None, 'Forbidden room'

    if room_type == 'session':
        session = db.session.get(PlaySession, room_id)
        if not session:
            return None, 'Session not found'
        if not _can_view_session(session, user.id):
            return None, 'Forbidden room'

    if room_type == 'game':
        game = db.session.get(Game, room_id)
        if not game:
            return None, 'Game not found'

    return user, None


@chat_bp.route('/court/<int:court_id>', methods=['GET'])
@login_required
def get_court_messages(court_id):
    messages = Message.query.filter_by(
        court_id=court_id, msg_type='court'
    ).order_by(Message.created_at.asc()).limit(100).all()
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/session/<int:session_id>', methods=['GET'])
@login_required
def get_session_messages(session_id):
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if not _can_view_session(session, request.current_user.id):
        return jsonify({'error': 'Session chat is private'}), 403
    messages = Message.query.filter_by(
        session_id=session_id, msg_type='session'
    ).order_by(Message.created_at.asc()).limit(100).all()
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/direct/<int:user_id>', methods=['GET'])
@login_required
def get_direct_messages(user_id):
    me = request.current_user.id
    other_user = db.session.get(User, user_id)
    if not other_user:
        return jsonify({'error': 'User not found'}), 404
    if not _are_friends(me, user_id):
        return jsonify({'error': 'Direct messages are limited to friends'}), 403
    messages = Message.query.filter(
        Message.msg_type == 'direct',
        ((Message.sender_id == me) & (Message.recipient_id == user_id))
        | ((Message.sender_id == user_id) & (Message.recipient_id == me))
    ).order_by(Message.created_at.asc()).limit(100).all()
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/game/<int:game_id>', methods=['GET'])
@login_required
def get_game_messages(game_id):
    """Legacy game chat compatibility endpoint."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404

    messages = Message.query.filter(
        Message.msg_type == 'game',
        Message.court_id == game.court_id,
        Message.created_at >= game.created_at,
    ).order_by(Message.created_at.asc()).limit(100).all()
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/send', methods=['POST'])
@login_required
def send_message():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Message content is required'}), 400

    msg_type = str(data.get('msg_type') or 'court').strip().lower()
    if msg_type not in {'court', 'session', 'direct', 'game'}:
        return jsonify({'error': 'Invalid message type'}), 400

    try:
        court_id = int(data.get('court_id')) if data.get('court_id') is not None else None
    except (TypeError, ValueError):
        court_id = None
    try:
        game_id = int(data.get('game_id')) if data.get('game_id') is not None else None
    except (TypeError, ValueError):
        game_id = None
    try:
        session_id = int(data.get('session_id')) if data.get('session_id') is not None else None
    except (TypeError, ValueError):
        session_id = None
    try:
        recipient_id = int(data.get('recipient_id')) if data.get('recipient_id') is not None else None
    except (TypeError, ValueError):
        recipient_id = None

    if msg_type == 'game':
        if not game_id:
            return jsonify({'error': 'Game ID is required for game chat'}), 400
        game = db.session.get(Game, game_id)
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        # Legacy game chat is scoped by the game's court and creation time.
        court_id = game.court_id
    elif msg_type == 'session':
        if not session_id:
            return jsonify({'error': 'Session ID is required for session chat'}), 400
        session = db.session.get(PlaySession, session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404
        if not _can_view_session(session, request.current_user.id):
            return jsonify({'error': 'Session chat is private'}), 403
        court_id = session.court_id
    elif msg_type == 'court':
        if not court_id:
            return jsonify({'error': 'Court ID is required for court chat'}), 400
    elif msg_type == 'direct':
        if not recipient_id:
            return jsonify({'error': 'Recipient ID is required for direct messages'}), 400
        if recipient_id == request.current_user.id:
            return jsonify({'error': 'Cannot send direct messages to yourself'}), 400
        recipient = db.session.get(User, recipient_id)
        if not recipient:
            return jsonify({'error': 'Recipient not found'}), 404
        if not _are_friends(request.current_user.id, recipient_id):
            return jsonify({'error': 'Direct messages are limited to friends'}), 403

    msg = Message(
        sender_id=request.current_user.id,
        content=content,
        msg_type=msg_type,
        court_id=court_id,
        session_id=session_id if msg_type == 'session' else None,
        recipient_id=recipient_id,
    )
    db.session.add(msg)
    db.session.commit()

    msg_dict = msg.to_dict()
    if msg.msg_type == 'session' and msg.session_id:
        socketio.emit('new_message', msg_dict, room=f'session_{msg.session_id}')
    elif msg.msg_type == 'game' and game_id:
        msg_dict['game_id'] = game_id
        socketio.emit('new_message', msg_dict, room=f'game_{game_id}')
    elif msg.court_id:
        socketio.emit('new_message', msg_dict, room=f'court_{msg.court_id}')
    elif msg.recipient_id:
        socketio.emit('new_message', msg_dict, room=f'user_{msg.recipient_id}')

    return jsonify({'message': msg_dict}), 201


# WebSocket event handlers
@socketio.on('join')
def on_join(data):
    payload = data if isinstance(data, dict) else {}
    room = str(payload.get('room') or '').strip()
    token = payload.get('token') or request.args.get('token') or ''
    _, error = _authorize_socket_join(room, token)
    if error:
        emit('status', {'error': error})
        return

    join_room(room)
    emit('status', {'message': f'Joined {room}'})


@socketio.on('leave')
def on_leave(data):
    room = data.get('room', '')
    if room:
        leave_room(room)
