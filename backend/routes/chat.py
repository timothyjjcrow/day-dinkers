import re
from datetime import timedelta

from flask import Blueprint, current_app, request, jsonify
from flask_socketio import emit, join_room, leave_room
from backend.app import db, socketio
from backend.models import (
    Message, Game, PlaySession, PlaySessionPlayer, Friendship, User,
    MessageReadReceipt,
)
from backend.auth_utils import login_required, get_user_from_token
from backend.time_utils import utcnow_naive

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


def _chat_retention_days():
    raw_days = current_app.config.get('CHAT_RETENTION_DAYS', 7)
    try:
        parsed_days = int(raw_days)
    except (TypeError, ValueError):
        parsed_days = 7
    return max(1, parsed_days)


def _chat_fetch_limit():
    raw_limit = current_app.config.get('CHAT_FETCH_LIMIT', 100)
    try:
        parsed_limit = int(raw_limit)
    except (TypeError, ValueError):
        parsed_limit = 100
    return max(20, min(parsed_limit, 500))


def _prune_stale_chat_messages():
    cutoff = utcnow_naive() - timedelta(days=_chat_retention_days())
    deleted = Message.query.filter(Message.created_at < cutoff).delete(
        synchronize_session=False,
    )
    if deleted:
        db.session.commit()


def _recent_messages(query):
    limit = _chat_fetch_limit()
    rows = query.order_by(
        Message.created_at.desc(),
        Message.id.desc(),
    ).limit(limit).all()
    rows.reverse()
    return rows


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
    _prune_stale_chat_messages()
    messages = _recent_messages(
        Message.query.filter_by(court_id=court_id, msg_type='court')
    )
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/session/<int:session_id>', methods=['GET'])
@login_required
def get_session_messages(session_id):
    _prune_stale_chat_messages()
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if not _can_view_session(session, request.current_user.id):
        return jsonify({'error': 'Session chat is private'}), 403
    messages = _recent_messages(
        Message.query.filter_by(session_id=session_id, msg_type='session')
    )
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/direct/<int:user_id>', methods=['GET'])
@login_required
def get_direct_messages(user_id):
    _prune_stale_chat_messages()
    me = request.current_user.id
    other_user = db.session.get(User, user_id)
    if not other_user:
        return jsonify({'error': 'User not found'}), 404
    if not _are_friends(me, user_id):
        return jsonify({'error': 'Direct messages are limited to friends'}), 403
    messages = _recent_messages(
        Message.query.filter(
            Message.msg_type == 'direct',
            ((Message.sender_id == me) & (Message.recipient_id == user_id))
            | ((Message.sender_id == user_id) & (Message.recipient_id == me))
        )
    )
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/direct/threads', methods=['GET'])
@login_required
def get_direct_threads():
    _prune_stale_chat_messages()
    me = request.current_user.id
    limit = request.args.get('limit', type=int) or 12
    limit = max(1, min(limit, 50))

    rows = Message.query.filter(
        Message.msg_type == 'direct',
        ((Message.sender_id == me) | (Message.recipient_id == me)),
    ).order_by(
        Message.created_at.desc(),
        Message.id.desc(),
    ).limit(_chat_fetch_limit()).all()

    threads = []
    seen = set()
    for message in rows:
        other_user_id = message.recipient_id if message.sender_id == me else message.sender_id
        if not other_user_id or other_user_id in seen:
            continue
        if not _are_friends(me, other_user_id):
            continue
        other_user = db.session.get(User, other_user_id)
        if not other_user:
            continue
        seen.add(other_user_id)
        threads.append({
            'user': other_user.to_public_dict(),
            'last_message': message.to_dict(),
            'last_message_preview': message.content[:120],
            'last_message_at': message.created_at.isoformat() if message.created_at else None,
            'last_message_from_me': message.sender_id == me,
        })
        if len(threads) >= limit:
            break

    return jsonify({'threads': threads})


@chat_bp.route('/game/<int:game_id>', methods=['GET'])
@login_required
def get_game_messages(game_id):
    """Legacy game chat compatibility endpoint."""
    _prune_stale_chat_messages()
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404

    messages = _recent_messages(
        Message.query.filter(
            Message.msg_type == 'game',
            Message.court_id == game.court_id,
            Message.created_at >= game.created_at,
        )
    )
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/send', methods=['POST'])
@login_required
def send_message():
    _prune_stale_chat_messages()
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


# ── Inbox Endpoints ────────────────────────────────────────────────────

def _user_session_ids(user_id):
    creator = {row[0] for row in db.session.query(PlaySession.id).filter(
        PlaySession.creator_id == user_id).all()}
    player = {row[0] for row in db.session.query(PlaySessionPlayer.session_id).filter(
        PlaySessionPlayer.user_id == user_id).all()}
    return creator | player


def _read_receipt_map(user_id):
    rows = MessageReadReceipt.query.filter_by(user_id=user_id).all()
    return {(r.thread_type, r.thread_ref_id): r.last_read_message_id for r in rows}


def _build_dm_threads(user_id, receipt_map):
    rows = _recent_messages(Message.query.filter(
        Message.msg_type == 'direct',
        (Message.sender_id == user_id) | (Message.recipient_id == user_id),
    ))
    rows.reverse()  # newest first for dedup

    threads, seen = [], set()
    for msg in rows:
        other_id = msg.recipient_id if msg.sender_id == user_id else msg.sender_id
        if not other_id or other_id in seen:
            continue
        if not _are_friends(user_id, other_id):
            continue
        other = db.session.get(User, other_id)
        if not other:
            continue
        seen.add(other_id)
        last_read = receipt_map.get(('direct', other_id), 0)
        unread = Message.query.filter(
            Message.msg_type == 'direct', Message.sender_id == other_id,
            Message.recipient_id == user_id, Message.id > last_read,
        ).count()
        threads.append({
            'thread_type': 'direct', 'thread_ref_id': other_id,
            'name': other.name or other.username,
            'subtitle': f'@{other.username}',
            'last_message_preview': msg.content[:120],
            'last_message_at': msg.created_at.isoformat() if msg.created_at else None,
            'unread_count': unread,
            'user': other.to_public_dict(),
        })
        if len(threads) >= 20:
            break
    return threads


def _build_session_threads(user_id, receipt_map):
    session_ids = _user_session_ids(user_id)
    if not session_ids:
        return []
    threads = []
    for sid in session_ids:
        last_msg = Message.query.filter(
            Message.session_id == sid, Message.msg_type == 'session',
        ).order_by(Message.created_at.desc()).first()
        if not last_msg:
            continue
        session = db.session.get(PlaySession, sid)
        if not session:
            continue
        last_read = receipt_map.get(('session', sid), 0)
        unread = Message.query.filter(
            Message.session_id == sid, Message.msg_type == 'session',
            Message.sender_id != user_id, Message.id > last_read,
        ).count()
        court_name = session.court.name if session.court else 'Court'
        game_type = (session.game_type or 'open').replace('_', ' ').title()
        threads.append({
            'thread_type': 'session', 'thread_ref_id': sid,
            'name': f'{court_name} Session',
            'subtitle': game_type,
            'last_message_preview': last_msg.content[:120],
            'last_message_at': last_msg.created_at.isoformat() if last_msg.created_at else None,
            'unread_count': unread,
            'session_id': sid,
        })
    return threads


@chat_bp.route('/inbox', methods=['GET'])
@login_required
def get_inbox():
    me = request.current_user.id
    thread_filter = request.args.get('filter', 'all')
    _prune_stale_chat_messages()
    receipt_map = _read_receipt_map(me)
    threads = []
    if thread_filter in ('all', 'direct'):
        threads.extend(_build_dm_threads(me, receipt_map))
    if thread_filter in ('all', 'sessions'):
        threads.extend(_build_session_threads(me, receipt_map))
    threads.sort(key=lambda t: t.get('last_message_at') or '', reverse=True)
    return jsonify({
        'threads': threads[:30],
        'total_unread': sum(t.get('unread_count', 0) for t in threads),
    })


@chat_bp.route('/inbox/unread-count', methods=['GET'])
@login_required
def get_inbox_unread_count():
    me = request.current_user.id
    receipt_map = _read_receipt_map(me)
    total = 0
    dm_senders = db.session.query(Message.sender_id).filter(
        Message.msg_type == 'direct', Message.recipient_id == me,
    ).distinct().all()
    for (sender_id,) in dm_senders:
        last_read = receipt_map.get(('direct', sender_id), 0)
        total += Message.query.filter(
            Message.msg_type == 'direct', Message.sender_id == sender_id,
            Message.recipient_id == me, Message.id > last_read,
        ).count()
    for sid in _user_session_ids(me):
        last_read = receipt_map.get(('session', sid), 0)
        total += Message.query.filter(
            Message.msg_type == 'session', Message.session_id == sid,
            Message.sender_id != me, Message.id > last_read,
        ).count()
    return jsonify({'unread_count': total})


@chat_bp.route('/inbox/read', methods=['POST'])
@login_required
def mark_inbox_thread_read():
    me = request.current_user.id
    data = request.get_json(silent=True) or {}
    thread_type = str(data.get('thread_type', '')).strip()
    try:
        thread_ref_id = int(data.get('thread_ref_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'thread_ref_id is required'}), 400
    if thread_type not in ('direct', 'session'):
        return jsonify({'error': 'Invalid thread_type'}), 400
    if thread_type == 'direct':
        latest = Message.query.filter(
            Message.msg_type == 'direct',
            ((Message.sender_id == me) & (Message.recipient_id == thread_ref_id))
            | ((Message.sender_id == thread_ref_id) & (Message.recipient_id == me)),
        ).order_by(Message.id.desc()).first()
    else:
        latest = Message.query.filter(
            Message.msg_type == 'session', Message.session_id == thread_ref_id,
        ).order_by(Message.id.desc()).first()
    if not latest:
        return jsonify({'ok': True})
    receipt = MessageReadReceipt.query.filter_by(
        user_id=me, thread_type=thread_type, thread_ref_id=thread_ref_id,
    ).first()
    if receipt:
        receipt.last_read_message_id = latest.id
        receipt.updated_at = utcnow_naive()
    else:
        receipt = MessageReadReceipt(
            user_id=me, thread_type=thread_type,
            thread_ref_id=thread_ref_id, last_read_message_id=latest.id,
        )
        db.session.add(receipt)
    db.session.commit()
    return jsonify({'ok': True})


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
