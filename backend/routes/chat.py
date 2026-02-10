from flask import Blueprint, request, jsonify
from flask_socketio import emit, join_room, leave_room
from backend.app import db, socketio
from backend.models import Message, Game
from backend.auth_utils import login_required

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/court/<int:court_id>', methods=['GET'])
@login_required
def get_court_messages(court_id):
    messages = Message.query.filter_by(
        court_id=court_id, msg_type='court'
    ).order_by(Message.created_at.asc()).limit(100).all()
    return jsonify({'messages': [m.to_dict() for m in messages]})


@chat_bp.route('/direct/<int:user_id>', methods=['GET'])
@login_required
def get_direct_messages(user_id):
    me = request.current_user.id
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
    data = request.get_json()
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Message content is required'}), 400

    msg_type = data.get('msg_type', 'court')
    court_id = data.get('court_id')
    game_id = data.get('game_id')

    if msg_type == 'game':
        if not game_id:
            return jsonify({'error': 'Game ID is required for game chat'}), 400
        game = db.session.get(Game, game_id)
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        # Legacy game chat is scoped by the game's court and creation time.
        court_id = game.court_id

    msg = Message(
        sender_id=request.current_user.id,
        content=content,
        msg_type=msg_type,
        court_id=court_id,
        recipient_id=data.get('recipient_id'),
    )
    db.session.add(msg)
    db.session.commit()

    msg_dict = msg.to_dict()
    if msg.msg_type == 'game' and game_id:
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
    room = data.get('room', '')
    if room:
        join_room(room)
        emit('status', {'message': f'Joined {room}'}, room=room)


@socketio.on('leave')
def on_leave(data):
    room = data.get('room', '')
    if room:
        leave_room(room)
