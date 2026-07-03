"""Friends, user search, public profiles, notifications, nearby players."""
import math

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased

from backend.app import db
from backend.models import (
    BlockedUser,
    CheckIn,
    Court,
    FavoriteCourt,
    Friendship,
    Game,
    GamePlayer,
    Notification,
    SKILL_LEVELS,
    User,
    blocked_pair_ids,
    is_blocked_between,
    notify,
    player_badges,
    rating_history_for,
    utcnow,
)
from datetime import timedelta
from backend.routes.auth import login_required
from backend.security import rate_limit

social_bp = Blueprint('social', __name__)


def _haversine_miles(lat1, lng1, lat2, lng2):
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


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


@social_bp.get('/players/nearby')
@login_required
def players_nearby():
    """Players near a location, by last check-in (or home court as fallback)."""
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({'error': 'location_required'}), 400
    radius = min(max(request.args.get('radius', default=25.0, type=float), 1.0), 250.0)
    text = str(request.args.get('q') or '').strip()
    skill = str(request.args.get('skill') or '').strip().lower()

    lat_delta = radius / 69.0
    lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
    lat_lo, lat_hi = lat - lat_delta, lat + lat_delta
    lng_lo, lng_hi = lng - lng_delta, lng + lng_delta

    home = aliased(Court)
    hidden = blocked_pair_ids(g.current_user.id)
    query = (
        User.query.outerjoin(home, User.home_court_id == home.id)
        .filter(User.id != g.current_user.id, User.deleted_at.is_(None))
        .filter(or_(
            and_(User.last_lat.between(lat_lo, lat_hi), User.last_lng.between(lng_lo, lng_hi)),
            and_(User.last_lat.is_(None),
                 home.latitude.between(lat_lo, lat_hi),
                 home.longitude.between(lng_lo, lng_hi)),
        ))
    )
    if hidden:
        query = query.filter(User.id.notin_(hidden))
    if text:
        query = query.filter(User.display_name.ilike(f'%{text}%'))
    if skill in SKILL_LEVELS:
        query = query.filter(User.skill_level == skill)

    candidates = query.limit(300).all()

    my_friends = friend_ids(g.current_user.id)
    # One pass to know who's checked in right now.
    candidate_ids = [u.id for u in candidates]
    active = {}
    if candidate_ids:
        rows = (
            CheckIn.query.filter(
                CheckIn.user_id.in_(candidate_ids),
                CheckIn.checked_out_at.is_(None),
            )
            .order_by(CheckIn.id.desc())
            .all()
        )
        for ci in rows:
            active.setdefault(ci.user_id, ci)

    items = []
    for user in candidates:
        ploc = (user.last_lat, user.last_lng)
        if ploc[0] is None and user.home_court:
            ploc = (user.home_court.latitude, user.home_court.longitude)
        if ploc[0] is None or ploc[1] is None:
            continue
        distance = _haversine_miles(lat, lng, ploc[0], ploc[1])
        if distance > radius:
            continue
        entry = user.to_public_dict()
        entry['distance_miles'] = round(distance, 1)
        friendship = _friendship_between(g.current_user.id, user.id)
        entry['is_friend'] = user.id in my_friends
        entry['friendship_status'] = friendship.status if friendship else None
        entry['friendship_id'] = friendship.id if friendship else None
        entry['outgoing'] = bool(friendship and friendship.requester_id == g.current_user.id)
        ci = active.get(user.id)
        entry['checked_in_court'] = (
            {'id': ci.court.id, 'name': ci.court.name, 'looking_for_game': bool(ci.looking_for_game)}
            if ci and ci.court else None
        )
        entry['last_seen_at'] = (
            user.last_location_at.isoformat() + 'Z' if user.last_location_at else None
        )
        items.append(entry)

    # Active players first, then closest.
    items.sort(key=lambda i: (i['checked_in_court'] is None, i['distance_miles']))
    return jsonify({'items': items[:60], 'count': len(items)})


@social_bp.get('/users/search')
@login_required
def search_users():
    text = str(request.args.get('q') or '').strip()
    if len(text) < 2:
        return jsonify({'items': []})
    like = f'%{text}%'
    hidden = blocked_pair_ids(g.current_user.id)
    query = User.query.filter(
        User.id != g.current_user.id,
        User.deleted_at.is_(None),
        or_(User.display_name.ilike(like), User.email.ilike(like)),
    )
    if hidden:
        query = query.filter(User.id.notin_(hidden))
    users = query.order_by(User.display_name.asc()).limit(20).all()
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


@social_bp.get('/invite/<int:user_id>')
@rate_limit(30, 60)
def invite_card(user_id):
    """Minimal public card for invite links — just enough to say who's inviting.
    Name and avatar are already visible to any signed-in user."""
    user = db.session.get(User, user_id)
    if not user or user.deleted_at is not None:
        return jsonify({'error': 'user_not_found'}), 404
    return jsonify({
        'display_name': user.display_name,
        'avatar_color': user.avatar_color,
        'avatar_url': user.avatar_url or '',
    })


@social_bp.get('/users/<int:user_id>')
@login_required
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'user_not_found'}), 404
    payload = user.to_public_dict()
    payload['is_blocked'] = bool(BlockedUser.query.filter_by(
        blocker_id=g.current_user.id, blocked_id=user.id,
    ).first())

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
    payload['badges'] = player_badges(user)
    payload['rating_history'] = rating_history_for(user)
    # Last-5 form from the profile owner's perspective, newest first.
    payload['form'] = [
        'W' if g['you_won'] else 'L'
        for g in payload['recent_games'] if g['you_won'] is not None
    ][:5]

    # Head-to-head: completed scored games where viewer and target were on
    # opposite teams.
    payload['head_to_head'] = None
    payload['as_teammates'] = None
    if user.id != g.current_user.id:
        me_gp, them_gp = aliased(GamePlayer), aliased(GamePlayer)
        shared = (
            Game.query
            .join(me_gp, and_(me_gp.game_id == Game.id,
                              me_gp.user_id == g.current_user.id))
            .join(them_gp, and_(them_gp.game_id == Game.id,
                                them_gp.user_id == user.id))
            .filter(
                Game.status == 'completed',
                Game.score_team1.isnot(None),
                Game.score_team2.isnot(None),
                me_gp.team.isnot(None),
                them_gp.team.isnot(None),
                me_gp.team != them_gp.team,
            )
            .order_by(Game.completed_at.desc())
            .limit(100)
            .all()
        )
        if shared:
            wins = 0
            for game in shared:
                mine = next(p for p in game.players if p.user_id == g.current_user.id)
                if (game.score_team1 > game.score_team2) == (mine.team == 1):
                    wins += 1
            payload['head_to_head'] = {
                'wins': wins,
                'losses': len(shared) - wins,
                'last_game': shared[0].to_dict(g.current_user.id),
            }

        # And the flip side: how you fare on the SAME team.
        us_gp, partner_gp = aliased(GamePlayer), aliased(GamePlayer)
        together = (
            Game.query
            .join(us_gp, and_(us_gp.game_id == Game.id,
                              us_gp.user_id == g.current_user.id))
            .join(partner_gp, and_(partner_gp.game_id == Game.id,
                                   partner_gp.user_id == user.id))
            .filter(
                Game.status == 'completed',
                Game.score_team1.isnot(None),
                Game.score_team2.isnot(None),
                us_gp.team.isnot(None),
                us_gp.team == partner_gp.team,
            )
            .limit(100)
            .all()
        )
        if together:
            team_wins = 0
            for game in together:
                ours = next(p for p in game.players if p.user_id == g.current_user.id)
                if (game.score_team1 > game.score_team2) == (ours.team == 1):
                    team_wins += 1
            payload['as_teammates'] = {'wins': team_wins, 'losses': len(together) - team_wins}

    # Upcoming games this player is in — only those the viewer is allowed to see.
    viewer_friends = friend_ids(g.current_user.id)
    upcoming = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow() - timedelta(hours=2),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(20)
        .all()
    )
    payload['upcoming_games'] = [
        game.to_dict(g.current_user.id)
        for game in upcoming
        if game.visible_to(g.current_user.id, viewer_friends)
    ][:8]

    # Home + favorite courts.
    courts = []
    seen = set()
    if user.home_court:
        courts.append({**user.home_court.to_summary_dict(), 'is_home': True})
        seen.add(user.home_court.id)
    favs = (
        FavoriteCourt.query.filter_by(user_id=user.id)
        .order_by(FavoriteCourt.id.desc())
        .limit(10)
        .all()
    )
    for fav in favs:
        if fav.court and fav.court.id not in seen:
            courts.append({**fav.court.to_summary_dict(), 'is_home': False})
            seen.add(fav.court.id)
    payload['courts'] = courts[:8]
    return jsonify(payload)


@social_bp.post('/users/<int:user_id>/report')
@rate_limit(10, 3600)
@login_required
def report_user(user_id):
    """Flag a player for review. One live report per pair per day —
    repeat taps are acknowledged without stacking rows."""
    from backend.models import UserReport
    target = db.session.get(User, user_id)
    if not target or target.deleted_at:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_report_self'}), 400
    reason = str((request.get_json(silent=True) or {}).get('reason') or '').strip()[:500]
    recent = UserReport.query.filter(
        UserReport.reporter_id == g.current_user.id,
        UserReport.reported_id == target.id,
        UserReport.created_at >= utcnow() - timedelta(hours=24),
    ).first()
    if not recent:
        db.session.add(UserReport(
            reporter_id=g.current_user.id, reported_id=target.id, reason=reason,
        ))
        db.session.commit()
    return jsonify({'reported': True})


@social_bp.get('/users/blocked')
@login_required
def list_blocked():
    """Players you've blocked — the only surface they still appear on,
    so you can find them again to unblock."""
    rows = (
        BlockedUser.query.filter_by(blocker_id=g.current_user.id)
        .order_by(BlockedUser.id.desc())
        .limit(100)
        .all()
    )
    return jsonify({'items': [
        row.blocked.to_public_dict()
        for row in rows
        if row.blocked and row.blocked.deleted_at is None
    ]})


@social_bp.post('/users/<int:user_id>/block')
@rate_limit(30, 3600)
@login_required
def block_user(user_id):
    target = db.session.get(User, user_id)
    if not target:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_block_self'}), 400
    existing = BlockedUser.query.filter_by(
        blocker_id=g.current_user.id, blocked_id=target.id,
    ).first()
    if not existing:
        db.session.add(BlockedUser(blocker_id=g.current_user.id, blocked_id=target.id))
        # Blocking ends any friendship (or pending request) between the pair.
        friendship = _friendship_between(g.current_user.id, target.id)
        if friendship:
            db.session.delete(friendship)
        db.session.commit()
    return jsonify({'blocked': True})


@social_bp.post('/users/<int:user_id>/unblock')
@rate_limit(30, 3600)
@login_required
def unblock_user(user_id):
    BlockedUser.query.filter_by(
        blocker_id=g.current_user.id, blocked_id=user_id,
    ).delete()
    db.session.commit()
    return jsonify({'blocked': False})


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


@social_bp.get('/friends/digest')
@login_required
def friends_digest():
    """Last-7-days recap of friend activity: games played, records, check-ins."""
    fids = friend_ids(g.current_user.id)
    if not fids:
        return jsonify({'days': 7, 'games': 0, 'friends_played': 0, 'checkins': 0, 'top': []})
    cutoff = utcnow() - timedelta(days=7)
    rows = (
        db.session.query(GamePlayer, Game)
        .join(Game, GamePlayer.game_id == Game.id)
        .filter(
            GamePlayer.user_id.in_(fids),
            Game.status == 'completed',
            Game.completed_at >= cutoff,
        )
        .all()
    )
    stats_by_friend = {}
    game_ids = set()
    for gp, game in rows:
        game_ids.add(game.id)
        stats = stats_by_friend.setdefault(gp.user_id, {'games': 0, 'wins': 0, 'losses': 0})
        stats['games'] += 1
        if gp.team and game.score_team1 is not None and game.score_team2 is not None:
            won = (game.score_team1 > game.score_team2) == (gp.team == 1)
            stats['wins' if won else 'losses'] += 1
    checkins = CheckIn.query.filter(
        CheckIn.user_id.in_(fids),
        CheckIn.checked_in_at >= cutoff,
    ).count()
    users = {u.id: u for u in User.query.filter(User.id.in_(stats_by_friend)).all()}
    ranked = sorted(stats_by_friend.items(), key=lambda kv: (-kv[1]['games'], -kv[1]['wins']))
    return jsonify({
        'days': 7,
        'games': len(game_ids),
        'friends_played': len(stats_by_friend),
        'checkins': checkins,
        'top': [
            {'id': uid, 'display_name': users[uid].display_name, **stats}
            for uid, stats in ranked[:3] if uid in users
        ],
    })


@social_bp.post('/friends/request')
@rate_limit(40, 60)
@login_required
def send_friend_request():
    payload = request.get_json(silent=True) or {}
    target = db.session.get(User, int(payload.get('user_id') or 0))
    if not target:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_friend_self'}), 400
    if is_blocked_between(g.current_user.id, target.id):
        return jsonify({'error': 'user_blocked'}), 403

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
@rate_limit(40, 60)
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
@rate_limit(40, 60)
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
@rate_limit(60, 60)
@login_required
def mark_notifications_read():
    Notification.query.filter_by(user_id=g.current_user.id, read=False).update({'read': True})
    db.session.commit()
    return jsonify({'ok': True})
