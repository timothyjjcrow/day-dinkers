"""Friends, user search, public profiles, notifications."""
from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_

from backend.app import db
from backend.models import CheckIn, Friendship, Game, GamePlayer, Notification, User, notify
from backend.routes.auth import login_required

social_bp = Blueprint('social', __name__)


def friend_ids(user_id):
    """IDs of all accepted friends of the given user."""
    rows = Friendship.query.filter(
        Friendship.status == 'accepted',
        or_(Friendship.requester_id == user_id, Friendship.addressee_id == user_id),
    ).all()
    return {
        f.addressee_id if f.requester_id == user_id else f.requester_id
        for f in rows
    }


def _friendship_between(user_a, user_b):
    return Friendship.query.filter(
        or_(
            (Friendship.requester_id == user_a) & (Friendship.addressee_id == user_b),
            (Friendship.requester_id == user_b) & (Friendship.addressee_id == user_a),
        )
    ).first()


def _friend_entry(friendship, viewer_id):
    other = friendship.other_user(viewer_id)
    entry = other.to_public_dict()
    entry['friendship_id'] = friendship.id
    entry['status'] = friendship.status
    entry['outgoing'] = friendship.requester_id == viewer_id
    checkin = (
        CheckIn.query.filter_by(user_id=other.id, checked_out_at=None)
        .order_by(CheckIn.id.desc())
        .first()
    )
    if checkin and checkin.court:
        entry['checked_in_court'] = {
            'id': checkin.court.id,
            'name': checkin.court.name,
            'looking_for_game': bool(checkin.looking_for_game),
        }
    else:
        entry['checked_in_court'] = None
    return entry


@social_bp.get('/users/search')
@login_required
def search_users():
    text = str(request.args.get('q') or '').strip()
    if len(text) < 2:
        return jsonify({'items': []})
    like = f'%{text}%'
    users = (
        User.query.filter(
            User.id != g.current_user.id,
            or_(User.display_name.ilike(like), User.email.ilike(like)),
        )
        .order_by(User.display_name.asc())
        .limit(20)
        .all()
    )
    items = []
    for user in users:
        entry = user.to_public_dict()
        friendship = _friendship_between(g.current_user.id, user.id)
        if friendship:
            entry['friendship_status'] = friendship.status
            entry['friendship_id'] = friendship.id
            entry['outgoing'] = friendship.requester_id == g.current_user.id
        else:
            entry['friendship_status'] = None
        items.append(entry)
    return jsonify({'items': items})


@social_bp.get('/users/<int:user_id>')
@login_required
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'user_not_found'}), 404
    payload = user.to_public_dict()

    friendship = _friendship_between(g.current_user.id, user.id)
    if friendship:
        payload['friendship_status'] = friendship.status
        payload['friendship_id'] = friendship.id
        payload['outgoing'] = friendship.requester_id == g.current_user.id
    else:
        payload['friendship_status'] = None

    recent = (
        Game.query.join(GamePlayer)
        .filter(GamePlayer.user_id == user.id, Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(10)
        .all()
    )
    payload['recent_games'] = [game.to_dict(user.id) for game in recent]
    return jsonify(payload)


@social_bp.get('/friends')
@login_required
def list_friends():
    rows = Friendship.query.filter(
        or_(
            Friendship.requester_id == g.current_user.id,
            Friendship.addressee_id == g.current_user.id,
        )
    ).all()
    friends, incoming, outgoing = [], [], []
    for friendship in rows:
        entry = _friend_entry(friendship, g.current_user.id)
        if friendship.status == 'accepted':
            friends.append(entry)
        elif friendship.requester_id == g.current_user.id:
            outgoing.append(entry)
        else:
            incoming.append(entry)
    friends.sort(key=lambda f: (f['checked_in_court'] is None, f['display_name'].lower()))
    return jsonify({'friends': friends, 'incoming': incoming, 'outgoing': outgoing})


@social_bp.post('/friends/request')
@login_required
def send_friend_request():
    payload = request.get_json(silent=True) or {}
    target = db.session.get(User, int(payload.get('user_id') or 0))
    if not target:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_friend_self'}), 400

    existing = _friendship_between(g.current_user.id, target.id)
    if existing:
        if existing.status == 'accepted':
            return jsonify({'error': 'already_friends'}), 409
        if existing.requester_id == g.current_user.id:
            return jsonify({'error': 'request_already_sent'}), 409
        existing.status = 'accepted'
        notify(
            target.id,
            'friend_accept',
            f'You are now friends with {g.current_user.display_name}',
            related_user_id=g.current_user.id,
        )
        db.session.commit()
        return jsonify(_friend_entry(existing, g.current_user.id))

    friendship = Friendship(
        requester_id=g.current_user.id,
        addressee_id=target.id,
        status='pending',
    )
    db.session.add(friendship)
    notify(
        target.id,
        'friend_request',
        f'{g.current_user.display_name} sent you a friend request',
        related_user_id=g.current_user.id,
    )
    db.session.commit()
    return jsonify(_friend_entry(friendship, g.current_user.id)), 201


@social_bp.post('/friends/<int:friendship_id>/respond')
@login_required
def respond_friend_request(friendship_id):
    friendship = db.session.get(Friendship, friendship_id)
    if not friendship or friendship.addressee_id != g.current_user.id:
        return jsonify({'error': 'request_not_found'}), 404
    if friendship.status != 'pending':
        return jsonify({'error': 'not_pending'}), 400

    payload = request.get_json(silent=True) or {}
    if payload.get('accept'):
        friendship.status = 'accepted'
        notify(
            friendship.requester_id,
            'friend_accept',
            f'{g.current_user.display_name} accepted your friend request',
            related_user_id=g.current_user.id,
        )
        db.session.commit()
        return jsonify(_friend_entry(friendship, g.current_user.id))
    db.session.delete(friendship)
    db.session.commit()
    return jsonify({'deleted': True})


@social_bp.delete('/friends/<int:friendship_id>')
@login_required
def remove_friend(friendship_id):
    friendship = db.session.get(Friendship, friendship_id)
    if not friendship or g.current_user.id not in (
        friendship.requester_id, friendship.addressee_id,
    ):
        return jsonify({'error': 'friendship_not_found'}), 404
    db.session.delete(friendship)
    db.session.commit()
    return jsonify({'deleted': True})


@social_bp.get('/notifications')
@login_required
def list_notifications():
    rows = (
        Notification.query.filter_by(user_id=g.current_user.id)
        .order_by(Notification.id.desc())
        .limit(50)
        .all()
    )
    return jsonify({
        'items': [n.to_dict() for n in rows],
        'unread': sum(1 for n in rows if not n.read),
    })


@social_bp.post('/notifications/read')
@login_required
def mark_notifications_read():
    Notification.query.filter_by(user_id=g.current_user.id, read=False).update({'read': True})
    db.session.commit()
    return jsonify({'ok': True})
