"""Direct messaging between players, plus per-court chat rooms."""
from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_

from backend.app import db
from backend.models import (
    Court, CourtChatRead, Game, GameChatRead, GamePlayer, Message,
    Notification, User, blocked_pair_ids, is_blocked_between, notify, utcnow,
)
from backend.security import rate_limit

chat_bp = Blueprint('chat', __name__)

from backend.routes.auth import login_required  # noqa: E402


@chat_bp.get('/courts/<int:court_id>/chat')
@login_required
def court_chat(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.court_id == court_id)
    if since_id:
        messages = query.filter(Message.id > since_id).order_by(Message.id.asc()).all()
    else:
        messages = list(reversed(query.order_by(Message.id.desc()).limit(60).all()))

    # Reading the room marks it read — powers the unread badge on court detail.
    latest_id = db.session.query(db.func.max(Message.id)).filter(
        Message.court_id == court_id,
    ).scalar() or 0
    marker = CourtChatRead.query.filter_by(
        user_id=g.current_user.id, court_id=court.id,
    ).first()
    if not marker:
        db.session.add(CourtChatRead(
            user_id=g.current_user.id, court_id=court.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'court': {'id': court.id, 'name': court.name},
        'items': [m.to_dict() for m in messages],
    })


@chat_bp.post('/courts/<int:court_id>/chat')
@rate_limit(60, 60)
@login_required
def send_court_message(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    payload = request.get_json(silent=True) or {}
    body = str(payload.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'message_body_required'}), 400
    message = Message(sender_id=g.current_user.id, court_id=court.id, body=body[:2000])
    db.session.add(message)
    db.session.commit()
    return jsonify(message.to_dict()), 201


def _game_member_or_403(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return None, (jsonify({'error': 'game_not_found'}), 404)
    is_member = GamePlayer.query.filter_by(
        game_id=game.id, user_id=g.current_user.id,
    ).first() is not None
    if not is_member:
        return None, (jsonify({'error': 'players_only'}), 403)
    return game, None


@chat_bp.get('/games/<int:game_id>/chat')
@login_required
def game_chat(game_id):
    game, err = _game_member_or_403(game_id)
    if err:
        return err
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.game_id == game_id)
    if since_id:
        messages = query.filter(Message.id > since_id).order_by(Message.id.asc()).all()
    else:
        messages = list(reversed(query.order_by(Message.id.desc()).limit(60).all()))

    # Reading the thread marks it read — powers unread badges on game cards.
    latest_id = db.session.query(db.func.max(Message.id)).filter(
        Message.game_id == game_id,
    ).scalar() or 0
    marker = GameChatRead.query.filter_by(
        user_id=g.current_user.id, game_id=game.id,
    ).first()
    if not marker:
        db.session.add(GameChatRead(
            user_id=g.current_user.id, game_id=game.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'game': {'id': game.id, 'court_name': game.court.name if game.court else 'Court'},
        'items': [m.to_dict() for m in messages],
    })


@chat_bp.post('/games/<int:game_id>/chat')
@rate_limit(60, 60)
@login_required
def send_game_message(game_id):
    game, err = _game_member_or_403(game_id)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    body = str(payload.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'message_body_required'}), 400
    message = Message(sender_id=g.current_user.id, game_id=game.id, body=body[:2000])
    db.session.add(message)

    # Tell the other players — at most one unread ping per game per player, so
    # an active back-and-forth doesn't flood the activity feed.
    court_name = game.court.name if game.court else 'your game'
    for player in game.players:
        if player.user_id == g.current_user.id:
            continue
        already_pinged = Notification.query.filter_by(
            user_id=player.user_id,
            kind='game_message',
            related_game_id=game.id,
            read=False,
        ).first()
        if not already_pinged:
            notify(
                player.user_id,
                'game_message',
                f'{g.current_user.display_name} in game chat at {court_name}',
                body[:140],
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    db.session.commit()
    return jsonify(message.to_dict()), 201


@chat_bp.get('/chat')
@login_required
def conversations():
    """Conversation list: latest message per partner with unread counts."""
    me = g.current_user.id
    messages = (
        Message.query.filter(
            Message.court_id.is_(None),
            Message.game_id.is_(None),
            or_(Message.sender_id == me, Message.recipient_id == me),
        )
        .order_by(Message.id.desc())
        .limit(500)
        .all()
    )
    hidden = blocked_pair_ids(me)
    by_partner = {}
    unread = {}
    for message in messages:
        partner_id = message.recipient_id if message.sender_id == me else message.sender_id
        if partner_id in hidden:
            continue
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
        Message.court_id.is_(None),
        or_(
            (Message.sender_id == me) & (Message.recipient_id == user_id),
            (Message.sender_id == user_id) & (Message.recipient_id == me),
        ),
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
@rate_limit(60, 60)
@login_required
def send_message(user_id):
    partner = db.session.get(User, user_id)
    if not partner:
        return jsonify({'error': 'user_not_found'}), 404
    if partner.id == g.current_user.id:
        return jsonify({'error': 'cannot_message_self'}), 400
    if is_blocked_between(g.current_user.id, partner.id):
        return jsonify({'error': 'user_blocked'}), 403

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
