"""Direct messaging between players."""
from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_

from backend.app import db
from backend.models import Message, User, utcnow

chat_bp = Blueprint('chat', __name__)

from backend.routes.auth import login_required  # noqa: E402


@chat_bp.get('/chat')
@login_required
def conversations():
    """Conversation list: latest message per partner with unread counts."""
    me = g.current_user.id
    messages = (
        Message.query.filter(or_(Message.sender_id == me, Message.recipient_id == me))
        .order_by(Message.id.desc())
        .limit(500)
        .all()
    )
    by_partner = {}
    unread = {}
    for message in messages:
        partner_id = message.recipient_id if message.sender_id == me else message.sender_id
        if partner_id not in by_partner:
            by_partner[partner_id] = message
        if message.recipient_id == me and message.read_at is None:
            unread[partner_id] = unread.get(partner_id, 0) + 1

    partners = {u.id: u for u in User.query.filter(User.id.in_(by_partner.keys())).all()}
    items = []
    for partner_id, last_message in by_partner.items():
        partner = partners.get(partner_id)
        if not partner:
            continue
        items.append({
            'user': partner.to_public_dict(),
            'last_message': last_message.to_dict(),
            'unread': unread.get(partner_id, 0),
        })
    items.sort(key=lambda i: i['last_message']['id'], reverse=True)
    return jsonify({'items': items})


@chat_bp.get('/chat/<int:user_id>')
@login_required
def thread(user_id):
    me = g.current_user.id
    partner = db.session.get(User, user_id)
    if not partner:
        return jsonify({'error': 'user_not_found'}), 404

    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(
        or_(
            (Message.sender_id == me) & (Message.recipient_id == user_id),
            (Message.sender_id == user_id) & (Message.recipient_id == me),
        )
    )
    if since_id:
        query = query.filter(Message.id > since_id)
        messages = query.order_by(Message.id.asc()).all()
    else:
        messages = list(reversed(query.order_by(Message.id.desc()).limit(100).all()))

    now = utcnow()
    changed = False
    for message in messages:
        if message.recipient_id == me and message.read_at is None:
            message.read_at = now
            changed = True
    if changed:
        db.session.commit()

    return jsonify({
        'user': partner.to_public_dict(),
        'items': [m.to_dict() for m in messages],
    })


@chat_bp.post('/chat/<int:user_id>')
@login_required
def send_message(user_id):
    partner = db.session.get(User, user_id)
    if not partner:
        return jsonify({'error': 'user_not_found'}), 404
    if partner.id == g.current_user.id:
        return jsonify({'error': 'cannot_message_self'}), 400

    payload = request.get_json(silent=True) or {}
    body = str(payload.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'message_body_required'}), 400

    message = Message(
        sender_id=g.current_user.id,
        recipient_id=partner.id,
        body=body[:2000],
    )
    db.session.add(message)
    db.session.commit()
    return jsonify(message.to_dict()), 201
