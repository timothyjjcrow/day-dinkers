"""Friends, user search, public profiles, notifications, nearby players."""
import json
import math

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload

from backend.app import db
from backend.models import (
    BlockedUser,
    CheckIn,
    Court,
    FavoriteCourt,
    Friendship,
    Game,
    GameArrivalIntent,
    GamePlayer,
    Notification,
    PlayAvailabilityPulse,
    SKILL_LEVELS,
    User,
    blocked_pair_ids,
    can_direct_message,
    is_blocked_between,
    notify,
    player_badges,
    rating_history_for,
    iso,
    utcnow,
)
from datetime import timedelta
from backend.routes.auth import (
    active_checkin_for,
    checkin_expires_at,
    login_required,
    presence_absolute_cutoff,
    presence_stale_cutoff,
)
from backend.security import rate_limit

social_bp = Blueprint('social', __name__)

NEARBY_PLAYERS_DEFAULT_LIMIT = 50
NEARBY_PLAYERS_MAX_LIMIT = 50
NOTIFICATIONS_DEFAULT_LIMIT = 50
NOTIFICATIONS_MAX_LIMIT = 50


def _haversine_miles(lat1, lng1, lat2, lng2):
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _positive_page_arg(name, default, maximum=None):
    value = request.args.get(name, default=default, type=int)
    value = max(1, value or default)
    return min(value, maximum) if maximum is not None else value


def nearby_visibility_allows(user, viewer_id, viewer_friend_ids=None):
    """Whether ``viewer_id`` may discover ``user`` in passive nearby UI."""
    if not user or user.deleted_at is not None:
        return False
    if user.id == viewer_id:
        return True
    visibility = user.nearby_visibility \
        if user.nearby_visibility in {'everyone', 'friends', 'hidden'} \
        else 'everyone'
    if visibility == 'hidden':
        return False
    if visibility == 'everyone':
        return True
    return user.id in (viewer_friend_ids or set())


def _nearby_player_payload(user, *, viewer_is_friend=False):
    """The fields the nearby-player cards actually render.

    Public profiles remain available from ``/users/<id>``. Discovery should
    not repeat bios, full win/loss records, rating peaks, and other profile
    detail for every row in a potentially large result set.
    """
    return {
        'id': user.id,
        'display_name': user.display_name,
        'skill_level': user.skill_level,
        'skill_rating': user.skill_rating,
        # Explicit alias for newer clients; keep skill_rating for compatibility.
        'self_rating': user.skill_rating,
        'avatar_color': user.avatar_color,
        'avatar_url': user.avatar_url or '',
        'rating': user.rating,
        'current_streak': user.current_streak,
        'home_court_name': user.home_court.name if user.home_court else None,
        'availability': user.availability_list(),
        'active_now': bool(
            user.nearby_visibility == 'everyone' or viewer_is_friend
        ) and bool(
            user.last_active_at
            and (utcnow() - user.last_active_at).total_seconds() < 600
        ),
    }


def friend_ids(user_id):
    """IDs of all accepted friends of the given user."""
    rows = Friendship.query.filter(
        Friendship.status == 'accepted',
        or_(Friendship.requester_id == user_id, Friendship.addressee_id == user_id),
    ).all()
    ids = {
        f.addressee_id if f.requester_id == user_id else f.requester_id
        for f in rows
    }
    # Defense in depth for a legacy/cross-transaction block+friendship row:
    # blocked people never regain social visibility merely because stale data
    # survived in the relationship table.
    return ids - blocked_pair_ids(user_id)


def _lock_user_pair(user_a, user_b):
    """Serialize relationship decisions on one canonical pair in PostgreSQL.

    SQLite ignores FOR UPDATE but serializes writes; the unordered unique index
    remains the final invariant in either dialect.
    """
    ids = sorted({int(user_a), int(user_b)})
    return (
        User.query.filter(User.id.in_(ids))
        .order_by(User.id.asc())
        .with_for_update()
        .all()
    )


def _friendship_between(user_a, user_b):
    return Friendship.query.filter(
        or_(
            (Friendship.requester_id == user_a) & (Friendship.addressee_id == user_b),
            (Friendship.requester_id == user_b) & (Friendship.addressee_id == user_a),
        )
    ).order_by(
        db.case((Friendship.status == 'accepted', 0), else_=1),
        Friendship.id.asc(),
    ).first()


def _friend_entry(friendship, viewer_id):
    other = friendship.other_user(viewer_id)
    entry = other.to_public_dict()
    if friendship.status == 'accepted' and other.nearby_visibility != 'hidden':
        entry['active_now'] = bool(
            other.last_active_at
            and (utcnow() - other.last_active_at).total_seconds() < 600
        )
    entry['can_message'] = can_direct_message(viewer_id, other.id)
    entry['friendship_id'] = friendship.id
    entry['status'] = friendship.status
    entry['outgoing'] = friendship.requester_id == viewer_id
    # Hidden is a hard identity boundary across every passive presence surface,
    # including the accepted-friends list. The aggregate court count can still
    # reflect activity, but the friend row must not reveal where this user is.
    checkin = (
        active_checkin_for(other.id, fresh=True)
        if other.nearby_visibility != 'hidden'
        else None
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
    """Players near a location, by last check-in or saved home area/court."""
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({'error': 'location_required'}), 400
    radius = min(max(request.args.get('radius', default=25.0, type=float), 1.0), 250.0)
    text = str(request.args.get('q') or '').strip()
    skill = str(request.args.get('skill') or '').strip().lower()
    level = str(request.args.get('level') or '').strip()
    if level not in ('', '3.0', '3.5', '4.0'):
        return jsonify({'error': 'invalid_level'}), 400
    page = _positive_page_arg('page', 1)
    limit = _positive_page_arg(
        'limit', NEARBY_PLAYERS_DEFAULT_LIMIT, NEARBY_PLAYERS_MAX_LIMIT,
    )

    lat_delta = radius / 69.0
    lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
    lat_lo, lat_hi = lat - lat_delta, lat + lat_delta
    lng_lo, lng_hi = lng - lng_delta, lng + lng_delta

    home = aliased(Court)
    hidden = blocked_pair_ids(g.current_user.id)
    my_friends = friend_ids(g.current_user.id)
    query = (
        User.query.outerjoin(home, User.home_court_id == home.id)
        .filter(User.id != g.current_user.id, User.deleted_at.is_(None))
        .filter(or_(
            and_(User.last_lat.between(lat_lo, lat_hi), User.last_lng.between(lng_lo, lng_hi)),
            and_(User.home_lat.between(lat_lo, lat_hi), User.home_lng.between(lng_lo, lng_hi)),
            and_(home.latitude.between(lat_lo, lat_hi), home.longitude.between(lng_lo, lng_hi)),
        ))
    )
    if hidden:
        query = query.filter(User.id.notin_(hidden))
    if text:
        query = query.filter(User.display_name.ilike(f'%{text}%'))
    if skill in SKILL_LEVELS:
        query = query.filter(User.skill_level == skill)
    if level == '3.0':
        query = query.filter(User.skill_rating >= 3.0, User.skill_rating < 3.5)
    elif level == '3.5':
        query = query.filter(User.skill_rating >= 3.5, User.skill_rating < 4.0)
    elif level == '4.0':
        query = query.filter(User.skill_rating >= 4.0)

    # Distance and fresh-presence rank are deliberately evaluated before the
    # page slice, so every matching player remains reachable and page order is
    # deterministic. The previous hard cap silently lost everyone after 300.
    candidates = query.options(selectinload(User.home_court)).all()

    # One pass to know who's checked in right now.
    candidate_ids = [u.id for u in candidates]
    active = {}
    if candidate_ids:
        rows = (
            CheckIn.query.filter(
                CheckIn.user_id.in_(candidate_ids),
                CheckIn.checked_out_at.is_(None),
                CheckIn.checked_in_at >= presence_absolute_cutoff(),
                CheckIn.last_presence_ping_at >= presence_stale_cutoff(),
            )
            .order_by(CheckIn.id.desc())
            .all()
        )
        for ci in rows:
            active.setdefault(ci.user_id, ci)

    ranked_candidates = []
    recent_location_cutoff = utcnow() - timedelta(days=7)
    for user in candidates:
        if not nearby_visibility_allows(
            user, g.current_user.id, my_friends,
        ):
            continue
        ci = active.get(user.id)
        can_see_live_court = bool(
            ci and (user.id in my_friends or ci.looking_for_game)
        )
        if (
            can_see_live_court and ci.court
            and ci.court.latitude is not None and ci.court.longitude is not None
        ):
            ploc = (ci.court.latitude, ci.court.longitude)
            location_source = 'checkin'
        elif (
            user.last_lat is not None and user.last_lng is not None
            and user.last_location_at and user.last_location_at >= recent_location_cutoff
        ):
            ploc = (user.last_lat, user.last_lng)
            location_source = 'recent'
        elif user.home_lat is not None and user.home_lng is not None:
            ploc = (user.home_lat, user.home_lng)
            location_source = 'home_area'
        elif user.home_court:
            ploc = (user.home_court.latitude, user.home_court.longitude)
            location_source = 'home_court'
        else:
            ploc = (None, None)
            location_source = None
        if ploc[0] is None or ploc[1] is None:
            continue
        distance = _haversine_miles(lat, lng, ploc[0], ploc[1])
        if distance > radius:
            continue
        presence_rank = 0 if ci and ci.looking_for_game else 1 if ci else 2
        ranked_candidates.append((
            presence_rank, distance, user.id, user, ci, location_source,
            can_see_live_court,
        ))

    ranked_candidates.sort(key=lambda item: item[:3])
    total = len(ranked_candidates)
    start = (page - 1) * limit
    page_candidates = ranked_candidates[start:start + limit]

    page_user_ids = [item[2] for item in page_candidates]
    friendships = {}
    if page_user_ids:
        rows = Friendship.query.filter(or_(
            and_(
                Friendship.requester_id == g.current_user.id,
                Friendship.addressee_id.in_(page_user_ids),
            ),
            and_(
                Friendship.addressee_id == g.current_user.id,
                Friendship.requester_id.in_(page_user_ids),
            ),
        )).order_by(
            db.case((Friendship.status == 'accepted', 0), else_=1),
            Friendship.id.asc(),
        ).all()
        for friendship in rows:
            other_id = (
                friendship.addressee_id
                if friendship.requester_id == g.current_user.id
                else friendship.requester_id
            )
            friendships.setdefault(other_id, friendship)

    items = []
    for _rank, distance, user_id, user, ci, location_source, can_see_live_court in page_candidates:
        entry = _nearby_player_payload(
            user, viewer_is_friend=user_id in my_friends,
        )
        entry['can_message'] = can_direct_message(g.current_user.id, user.id)
        entry['distance_miles'] = round(distance, 1)
        entry['location_source'] = location_source
        friendship = friendships.get(user_id)
        entry['is_friend'] = user_id in my_friends
        entry['friendship_status'] = friendship.status if friendship else None
        entry['friendship_id'] = friendship.id if friendship else None
        entry['outgoing'] = bool(
            friendship and friendship.requester_id == g.current_user.id
        )
        # Nearby discovery may remain coarse for everyone, but an exact live
        # court is sensitive physical-presence data. Friends and players who
        # explicitly opted into "looking" discovery can reveal it; other
        # strangers stay discoverable without exposing where they are now.
        entry['checked_in_court'] = (
            {'id': ci.court.id, 'name': ci.court.name, 'looking_for_game': bool(ci.looking_for_game)}
            if can_see_live_court and ci.court else None
        )
        entry['last_seen_at'] = (
            user.last_location_at.isoformat() + 'Z'
            if location_source == 'recent' and user.last_location_at else None
        )
        items.append(entry)

    has_more = start + len(items) < total
    return jsonify({
        'items': items,
        'count': total,
        'page': page,
        'limit': limit,
        'has_more': has_more,
        'next_page': page + 1 if has_more else None,
    })


@social_bp.get('/players/looking')
@login_required
def players_looking():
    """Lightweight count (+ a few names) of players checked in and *looking
    for a game* near a location — powers the 'go play now' home prompt."""
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({
            'count': 0,
            'players': [],
            'rally_count': 0,
            'rallies': [],
            'pulse_count': 0,
            'pulses': [],
        })
    radius = min(max(request.args.get('radius', default=25.0, type=float), 1.0), 100.0)
    hidden = blocked_pair_ids(g.current_user.id)
    my_friends = friend_ids(g.current_user.id)
    now = utcnow()
    stale_cutoff = presence_stale_cutoff(now)
    absolute_cutoff = presence_absolute_cutoff(now)
    # Persist one-way assembly closure before discovery. This is a lazy sweep,
    # matching the existing feed cleanup model; completed multi-player rows
    # remain available to their participants for scoring.
    from backend.routes.games import (
        _arrival_capacity,
        _arrival_reservation_available,
        _active_live_rally_for_user,
        _close_instant_assembly_without_fresh_members,
        issue_play_pulse_accept_capability,
        issue_rally_arrival_capability,
    )
    lat_delta = radius / 69.0
    lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))

    rows = (
        db.session.query(CheckIn, User, Court)
        .join(User, CheckIn.user_id == User.id)
        .join(Court, CheckIn.court_id == Court.id)
        .filter(
            CheckIn.checked_out_at.is_(None),
            CheckIn.looking_for_game.is_(True),
            CheckIn.checked_in_at >= absolute_cutoff,
            CheckIn.last_presence_ping_at >= stale_cutoff,
            CheckIn.user_id != g.current_user.id,
            User.deleted_at.is_(None),
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )
        .order_by(CheckIn.id.desc())
        .limit(200)
        .all()
    )
    seen = set()
    players = []
    for ci, user, court in rows:
        if (user.id in seen or user.id in hidden
                or not nearby_visibility_allows(
                    user, g.current_user.id, my_friends,
                )):
            continue
        if _haversine_miles(lat, lng, court.latitude, court.longitude) > radius:
            continue
        seen.add(user.id)
        looking_expires_at = checkin_expires_at(ci)
        players.append({
            'id': user.id,
            'display_name': user.display_name,
            'avatar_color': user.avatar_color,
            'avatar_url': user.avatar_url,
            'court_id': court.id,
            'court_name': court.name,
            'distance_miles': round(
                _haversine_miles(lat, lng, court.latitude, court.longitude), 1,
            ),
            'looking_expires_at': (
                looking_expires_at.isoformat() + 'Z'
                if looking_expires_at else None
            ),
            'is_friend': user.id in my_friends,
        })

    # A member's LFG toggle clears as soon as they commit, but an underfilled
    # instant rally is still an assembly signal. A rally only surfaces while a
    # current member has fresh presence at that exact court.
    candidate_rallies = (
        Game.query.join(Court, Game.court_id == Court.id)
        .filter(
            Game.is_instant.is_(True),
            Game.status == 'upcoming',
            Game.assembly_closed_at.is_(None),
            Game.game_type == 'casual',
            Game.visibility == 'open',
            Game.recurrence == 'none',
            Game.scheduled_at >= now - timedelta(minutes=90),
            Game.scheduled_at <= now + timedelta(minutes=15),
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )
        .order_by(Game.scheduled_at.desc(), Game.id.desc())
        .limit(100)
        .execution_options(populate_existing=True)
        .all()
    )
    rally_ids_seen = set()
    rally_member_ids = set()
    rallies = []
    for game in candidate_rallies:
        if game.id in rally_ids_seen:
            continue
        rally_ids_seen.add(game.id)
        member_ids = {player.user_id for player in game.players}
        if member_ids & hidden:
            continue
        court = game.court
        if not court or court.latitude is None or court.longitude is None:
            continue
        distance = _haversine_miles(
            lat, lng, court.latitude, court.longitude,
        )
        if distance > radius:
            continue
        fresh_member_checkins = (
            CheckIn.query.filter(
                CheckIn.user_id.in_(member_ids),
                CheckIn.court_id == game.court_id,
                CheckIn.checked_out_at.is_(None),
                CheckIn.checked_in_at >= absolute_cutoff,
                CheckIn.last_presence_ping_at >= stale_cutoff,
            )
            .order_by(CheckIn.last_presence_ping_at.desc(), CheckIn.id.desc())
            .all()
        ) if member_ids else []
        if not fresh_member_checkins:
            # Discovery is the lifecycle sweep for physically abandoned
            # assemblies. Lock and re-check before making the one-way close so
            # a concurrent presence ping cannot be erased by this GET.
            locked_game = (
                Game.query.filter(Game.id == game.id)
                .with_for_update()
                .execution_options(populate_existing=True)
                .first()
            )
            if locked_game:
                db.session.expire(locked_game, ['players'])
                if _close_instant_assembly_without_fresh_members(
                    locked_game, now,
                ):
                    db.session.commit()
            continue
        capacity = _arrival_capacity(game, now)
        # A remote hold may consume the final effective spot, but the local
        # aggregate still helps nearby players understand that this physically
        # underfilled rally is converging. The UI simply omits its arrival CTA.
        if capacity['physical_spots_left'] <= 0:
            continue
        expires_at = max(
            checkin_expires_at(row) for row in fresh_member_checkins
        )
        expires_at = min(
            expires_at,
            game.scheduled_at + timedelta(minutes=90),
        )
        rally_member_ids.update(member_ids)
        arrival_available = _arrival_reservation_available(
            game, capacity, now,
        )
        rally = {
            'game_id': game.id,
            'court_id': court.id,
            'court_name': court.name,
            'court_city': court.city,
            'court_latitude': court.latitude,
            'court_longitude': court.longitude,
            'max_players': game.max_players,
            'ready_count': capacity['ready_count'],
            'roster_count': capacity['roster_count'],
            'on_the_way_count': capacity['on_the_way_count'],
            'committed_count': capacity['committed_count'],
            'physical_spots_left': capacity['physical_spots_left'],
            'spots_left': capacity['spots_left'],
            'distance_miles': round(distance, 1),
            'expires_at': expires_at.isoformat() + 'Z',
            'arrival_available': arrival_available,
            'assembly_state': (
                'finding'
                if capacity['ready_count'] < 2
                else ('ready' if capacity['spots_left'] > 0 else 'full')
            ),
            'is_joined': any(
                player.user_id == g.current_user.id for player in game.players
            ),
        }
        if arrival_available:
            rally['arrival_capability'] = issue_rally_arrival_capability(
                g.current_user.id, game.id, game.court_id, now,
            )
        rallies.append(rally)

    # A rostered member belongs to the durable rally signal, not the loose LFG
    # list as well. This also protects against legacy or racing rows whose LFG
    # flag was not cleared when membership committed.
    players = [
        player for player in players if player['id'] not in rally_member_ids
    ]
    rally_by_court = {}
    for rally in rallies:
        rally_by_court.setdefault(rally['court_id'], rally)
    for player in players:
        rally = rally_by_court.get(player['court_id'])
        player['game_id'] = rally['game_id'] if rally else None
        player['ready_count'] = rally['ready_count'] if rally else 0
        player['spots_left'] = rally['spots_left'] if rally else None

    pulse_rows = (
        db.session.query(PlayAvailabilityPulse, User, Court)
        .join(User, PlayAvailabilityPulse.user_id == User.id)
        .join(Court, PlayAvailabilityPulse.court_id == Court.id)
        .filter(
            PlayAvailabilityPulse.active.is_(True),
            PlayAvailabilityPulse.ended_at.is_(None),
            PlayAvailabilityPulse.expires_at > now,
            PlayAvailabilityPulse.user_id != g.current_user.id,
            User.deleted_at.is_(None),
            Court.closed.is_(False),
            Court.latitude.isnot(None),
            Court.longitude.isnot(None),
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )
        .order_by(
            PlayAvailabilityPulse.declared_at.desc(),
            PlayAvailabilityPulse.id.desc(),
        )
        .limit(200)
        .all()
    )
    pulse_windows = {
        pulse.user_id: (pulse.declared_at, pulse.expires_at)
        for pulse, _user, _court in pulse_rows
    }
    conflicting_game_users = set()
    if pulse_windows:
        earliest = min(window[0] for window in pulse_windows.values())
        latest = max(window[1] for window in pulse_windows.values())
        commitments = (
            db.session.query(GamePlayer.user_id, Game.scheduled_at)
            .join(Game, Game.id == GamePlayer.game_id)
            .filter(
                GamePlayer.user_id.in_(sorted(pulse_windows)),
                Game.status == 'upcoming',
                Game.is_instant.is_(False),
                Game.scheduled_at >= earliest,
                Game.scheduled_at <= latest,
            )
            .all()
        )
        conflicting_game_users = {
            user_id for user_id, scheduled_at in commitments
            if pulse_windows[user_id][0]
            <= scheduled_at
            <= pulse_windows[user_id][1]
        }
    pulses = []
    pulse_users_seen = set()
    for pulse, user, court in pulse_rows:
        if (
            user.id in hidden
            or user.id in pulse_users_seen
            or not nearby_visibility_allows(
                user, g.current_user.id, my_friends,
            )
        ):
            continue
        distance = _haversine_miles(
            lat, lng, court.latitude, court.longitude,
        )
        if distance > radius:
            continue
        # A pulse never masquerades as physical presence, and stale lifecycle
        # hooks cannot make a conflicting signal discoverable.
        if active_checkin_for(user.id, fresh=True, now=now):
            continue
        live_arrival = GameArrivalIntent.query.filter(
            GameArrivalIntent.user_id == user.id,
            GameArrivalIntent.active.is_(True),
            GameArrivalIntent.ended_at.is_(None),
            GameArrivalIntent.expires_at > now,
        ).first()
        if (
            live_arrival
            or user.id in conflicting_game_users
            or _active_live_rally_for_user(user.id, now)
        ):
            continue
        pulse_users_seen.add(user.id)
        pulses.append({
            'id': pulse.id,
            'user': user.to_public_dict(),
            'court': court.to_summary_dict(),
            'distance_miles': round(distance, 1),
            'expires_at': pulse.expires_at.isoformat() + 'Z',
            'accept_capability': issue_play_pulse_accept_capability(
                g.current_user.id, pulse.id, pulse.court_id, now,
            ),
        })
    return jsonify({
        'count': len(players),
        'players': players[:5],
        'rally_count': len(rallies),
        'rallies': rallies[:20],
        'pulse_count': len(pulses),
        'pulses': pulses[:20],
    })


@social_bp.get('/users/search')
@login_required
def search_users():
    text = str(request.args.get('q') or '').strip()
    if len(text) < 2:
        return jsonify({'items': []})
    like = f'%{text}%'
    identity_filters = [User.display_name.ilike(like)]
    # Email is a private account identifier. Allow an exact address so a
    # player can deliberately find someone they know, but never enumerate
    # accounts through email fragments or domains.
    if '@' in text:
        identity_filters.append(User.email == text.lower())
    hidden = blocked_pair_ids(g.current_user.id)
    query = User.query.filter(
        User.id != g.current_user.id,
        User.deleted_at.is_(None),
        or_(*identity_filters),
    )
    if hidden:
        query = query.filter(User.id.notin_(hidden))
    users = query.order_by(User.display_name.asc()).limit(20).all()
    items = []
    for user in users:
        entry = user.to_public_dict()
        entry['can_message'] = can_direct_message(g.current_user.id, user.id)
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
    """Minimal public card for invite links and an optional safe destination.

    Keep the legacy no-query response shape unchanged. New invite pages pass a
    ``game`` query parameter and receive a nullable, server-validated public
    destination in addition to the inviter fields.
    """
    user = db.session.get(User, user_id)
    if not user or user.deleted_at is not None:
        return jsonify({'error': 'user_not_found'}), 404
    payload = {
        'display_name': user.display_name,
        'avatar_color': user.avatar_color,
        'avatar_url': user.avatar_url or '',
    }
    if 'game' in request.args:
        from backend.services.player_invites import invite_destination_payload
        payload['destination'] = invite_destination_payload(
            user.id, request.args.get('game'),
        )
    return jsonify(payload)


@social_bp.get('/users/<int:user_id>')
@login_required
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if not user or user.deleted_at:
        return jsonify({'error': 'user_not_found'}), 404
    if user.id != g.current_user.id and is_blocked_between(
        g.current_user.id, user.id,
    ):
        # A block is a two-way privacy boundary. Use the same response as an
        # unavailable account so cached profile links cannot probe activity.
        return jsonify({'error': 'user_not_found'}), 404
    payload = user.to_public_dict()
    payload['can_message'] = can_direct_message(g.current_user.id, user.id)
    payload['invited_by_you'] = user.invited_by_user_id == g.current_user.id
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
    may_see_activity = (
        user.id == g.current_user.id
        or user.nearby_visibility == 'everyone'
        or user.nearby_visibility == 'friends'
        and friendship is not None and friendship.status == 'accepted'
    ) and user.nearby_visibility != 'hidden'
    payload['active_now'] = bool(
        may_see_activity
        and user.last_active_at
        and (utcnow() - user.last_active_at).total_seconds() < 600
    )

    # Mutual friends — "people you both know", for trust and connection.
    payload['mutual_friends'] = []
    if user.id != g.current_user.id:
        mutual_ids = friend_ids(g.current_user.id) & friend_ids(user.id)
        if mutual_ids:
            mutuals = User.query.filter(
                User.id.in_(mutual_ids), User.deleted_at.is_(None),
            ).order_by(User.display_name.asc()).limit(20).all()
            payload['mutual_friends'] = [
                {'id': u.id, 'display_name': u.display_name} for u in mutuals
            ]

    recent = (
        Game.query.join(GamePlayer)
        .filter(GamePlayer.user_id == user.id, Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(30)
        .all()
    )
    # Casual-only players need a meaningful public identity too. Keep these
    # broad totals separate from the visibility-filtered activity cards below:
    # they reveal only aggregate play, never a private game's details.
    completed_rows = (
        db.session.query(Game.completed_at, Game.court_id)
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
        .limit(500)
        .all()
    )
    completed_weeks = {row.completed_at.isocalendar()[:2] for row in completed_rows}
    week_streak = 0
    week_cursor = utcnow()
    if week_cursor.isocalendar()[:2] not in completed_weeks:
        week_cursor -= timedelta(days=7)
    while week_cursor.isocalendar()[:2] in completed_weeks:
        week_streak += 1
        week_cursor -= timedelta(days=7)
    payload['play_stats'] = {
        'games_total': len(completed_rows),
        'courts_played': len({row.court_id for row in completed_rows}),
        'week_streak': week_streak,
    }
    viewer_friends = friend_ids(g.current_user.id)
    viewer_hidden = blocked_pair_ids(g.current_user.id)
    from backend.routes.games import _game_has_blocked_participant
    visible_recent = []
    for game in recent:
        if (
            not game.visible_to(g.current_user.id, viewer_friends)
            or _game_has_blocked_participant(
                game, g.current_user.id, viewer_hidden,
            )
        ):
            continue
        item = game.to_dict(
            g.current_user.id,
            perspective_user_id=user.id,
        )
        # Owner-perspective win/rating fields power the public form card, but
        # an individual's MVP ballot is private even when the result is visible.
        item.pop('my_mvp_vote', None)
        item.pop('waitlist_position', None)
        item.pop('awaiting_your_confirmation', None)
        visible_recent.append(item)
        if len(visible_recent) >= 10:
            break
    payload['recent_games'] = visible_recent
    payload['badges'] = player_badges(user)
    from backend.models import league_titles, mvp_award_count, tournament_titles
    payload['tournament_titles'] = tournament_titles(user)
    payload['league_titles'] = league_titles(user)
    payload['mvp_awards'] = mvp_award_count(user)
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
    from backend.routes.games import (
        _discovery_game_payload,
        _instant_game_discovery_allowed,
    )
    payload['upcoming_games'] = [
        _discovery_game_payload(game, g.current_user, viewer_friends)
        for game in upcoming
        if game.visible_to(g.current_user.id, viewer_friends)
        and not _game_has_blocked_participant(
            game, g.current_user.id, viewer_hidden,
        )
        and _instant_game_discovery_allowed(
            game, g.current_user, viewer_friends,
        )
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
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason') or '').strip()[:500]
    details = str(payload.get('details') or '').strip()[:2000]
    if len(reason) < 3:
        return jsonify({'error': 'reason_required'}), 400
    recent = UserReport.query.filter(
        UserReport.reporter_id == g.current_user.id,
        UserReport.reported_id == target.id,
        UserReport.content_type == 'user',
        UserReport.created_at >= utcnow() - timedelta(hours=24),
    ).first()
    if not recent:
        db.session.add(UserReport(
            reporter_id=g.current_user.id, reported_id=target.id, reason=reason,
            details=details, content_type='user',
            content_snapshot=json.dumps({
                'display_name': target.display_name,
                'bio': target.bio or '',
            }, ensure_ascii=False, separators=(',', ':')),
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


def _reconcile_crews_for_block(blocker_id, blocked_id):
    """Remove a newly blocked pair from shared crews without deleting history.

    User-pair locks are acquired by the caller. Crew rows are then locked in a
    stable order so reciprocal/concurrent privacy actions choose the same
    deterministic survivor and bump each affected roster exactly once.
    """
    # Lazy imports keep the social blueprint independent of the crew routes.
    from backend.models import Crew, CrewChatRead, CrewInvite, CrewMember

    now = utcnow()

    # A pending invitation is a live relationship doorway too. Revoke both a
    # direct inviter/invitee relationship and an invitation that would place
    # either side into a Crew where the other is already accepted.
    pending = (
        CrewInvite.query
        .join(Crew, Crew.id == CrewInvite.crew_id)
        .filter(
            CrewInvite.status == 'pending',
            Crew.archived_at.is_(None),
            CrewInvite.invitee_id.in_((blocker_id, blocked_id)),
        )
        .order_by(CrewInvite.id.asc())
        .all()
    )
    for invite in pending:
        other_id = blocked_id if invite.invitee_id == blocker_id else blocker_id
        direct_pair = {
            invite.invited_by_id, invite.invitee_id,
        } == {blocker_id, blocked_id}
        if not direct_pair and not invite.crew.is_member(other_id):
            continue
        invite.status = 'revoked'
        invite.resolved_at = now
        Notification.query.filter_by(
            user_id=invite.invitee_id,
            related_crew_id=invite.crew_id,
            kind='crew_invite',
        ).delete(synchronize_session=False)

    blocker_crews = db.session.query(CrewMember.crew_id).filter(
        CrewMember.user_id == blocker_id,
    )
    blocked_crews = db.session.query(CrewMember.crew_id).filter(
        CrewMember.user_id == blocked_id,
    )
    shared = (
        Crew.query
        .filter(
            Crew.archived_at.is_(None),
            or_(Crew.owner_id == blocker_id, Crew.id.in_(blocker_crews)),
            or_(Crew.owner_id == blocked_id, Crew.id.in_(blocked_crews)),
        )
        .order_by(Crew.id.asc())
        .with_for_update()
        .all()
    )
    for crew in shared:
        # The blocker controls their own crew. In every other ownership shape,
        # blocking is the blocker's choice to leave the shared space.
        departing_id = blocked_id if crew.owner_id == blocker_id else blocker_id
        membership = CrewMember.query.filter_by(
            crew_id=crew.id, user_id=departing_id,
        ).first()
        if membership is None:
            continue
        # Remove through the loaded relationship so the response in this same
        # request cannot serialize a member that has already been evicted.
        crew.members.remove(membership)
        consent = CrewInvite.query.filter_by(
            crew_id=crew.id, invitee_id=departing_id, status='accepted',
        ).first()
        if consent:
            consent.status = 'revoked'
            consent.resolved_at = now
        CrewChatRead.query.filter_by(
            crew_id=crew.id, user_id=departing_id,
        ).delete(synchronize_session=False)
        from backend.services.conversations import (
            conversation_ref, delete_conversation_read,
        )
        delete_conversation_read(
            conversation_ref('crew', crew.id), departing_id,
        )
        crew.roster_version = int(crew.roster_version or 0) + 1


def _end_rally_arrivals_for_block(blocker_id, blocked_id):
    """A block immediately releases any remote spot across the new boundary."""
    from backend.models import GameArrivalIntent
    from backend.routes.games import _end_arrival_intent

    pair = {int(blocker_id), int(blocked_id)}
    participant_games = db.session.query(GamePlayer.game_id).filter(
        GamePlayer.user_id.in_(pair),
    )
    probes = GameArrivalIntent.query.filter(
        GameArrivalIntent.active.is_(True),
        or_(
            GameArrivalIntent.user_id.in_(pair),
            GameArrivalIntent.game_id.in_(participant_games),
        ),
    ).all()
    if not probes:
        return
    games = (
        Game.query.filter(Game.id.in_(sorted({row.game_id for row in probes})))
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    by_id = {game.id: game for game in games}
    intents = (
        GameArrivalIntent.query.filter(
            GameArrivalIntent.id.in_(sorted(row.id for row in probes)),
            GameArrivalIntent.active.is_(True),
        )
        .order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    now = utcnow()
    for intent in intents:
        game = by_id.get(intent.game_id)
        member_ids = {
            player.user_id for player in game.players
        } if game else set()
        if any(
            {intent.user_id, member_id} == pair
            for member_id in member_ids
        ):
            _end_arrival_intent(intent, 'blocked', now)


@social_bp.post('/users/<int:user_id>/block')
@rate_limit(30, 3600)
@login_required
def block_user(user_id):
    target = db.session.get(User, user_id)
    if not target or target.deleted_at:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_block_self'}), 400
    _lock_user_pair(g.current_user.id, target.id)
    existing = BlockedUser.query.filter_by(
        blocker_id=g.current_user.id, blocked_id=target.id,
    ).first()
    if not existing:
        db.session.add(BlockedUser(blocker_id=g.current_user.id, blocked_id=target.id))
    # Blocking ends every friendship/pending row between the pair. Run this on
    # idempotent repeats too, so a corrupt race cannot leave a ghost row.
    Friendship.query.filter(or_(
        (Friendship.requester_id == g.current_user.id)
        & (Friendship.addressee_id == target.id),
        (Friendship.requester_id == target.id)
        & (Friendship.addressee_id == g.current_user.id),
    )).delete(synchronize_session=False)
    _end_rally_arrivals_for_block(g.current_user.id, target.id)
    _reconcile_crews_for_block(g.current_user.id, target.id)
    # Stale social notifications are another profile doorway. Remove both
    # sides of the pair at the same privacy boundary.
    Notification.query.filter(or_(
        (Notification.user_id == g.current_user.id)
        & (Notification.related_user_id == target.id),
        (Notification.user_id == target.id)
        & (Notification.related_user_id == g.current_user.id),
    )).delete(synchronize_session=False)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if not BlockedUser.query.filter_by(
            blocker_id=g.current_user.id, blocked_id=target.id,
        ).first():
            raise
    return jsonify({'blocked': True})


@social_bp.post('/users/<int:user_id>/unblock')
@rate_limit(30, 3600)
@login_required
def unblock_user(user_id):
    target = db.session.get(User, user_id)
    if target and not target.deleted_at and target.id != g.current_user.id:
        _lock_user_pair(g.current_user.id, target.id)
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
    hidden_ids = blocked_pair_ids(g.current_user.id)
    for friendship in rows:
        other = friendship.other_user(g.current_user.id)
        if not other or other.deleted_at or other.id in hidden_ids:
            continue
        entry = _friend_entry(friendship, g.current_user.id)
        if friendship.status == 'accepted':
            friends.append(entry)
        elif friendship.requester_id == g.current_user.id:
            outgoing.append(entry)
        else:
            incoming.append(entry)
    friends.sort(key=lambda f: (f['checked_in_court'] is None, f['display_name'].lower()))
    return jsonify({'friends': friends, 'incoming': incoming, 'outgoing': outgoing})


@social_bp.get('/players/recent')
@login_required
def recent_coplayers():
    """Return recent eligible co-players for low-friction game planning.

    The result is viewer-relative, block-safe, and intentionally small. It may
    include both friends and non-friends because a completed shared game is
    enough context for a direct invitation; the client labels the relationship
    instead of flattening everyone into the friends list.
    """
    limit = _positive_page_arg('limit', 8, 20)
    hidden_ids = blocked_pair_ids(g.current_user.id)
    games = (
        Game.query
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .filter(
            GamePlayer.user_id == g.current_user.id,
            Game.status == 'completed',
        )
        .options(selectinload(Game.players))
        .order_by(Game.completed_at.desc(), Game.id.desc())
        .limit(60)
        .all()
    )
    stats = {}
    ordered_ids = []
    for game in games:
        for player in game.players:
            user_id = player.user_id
            credited = game.completion_kind == 'session' or player.team in (1, 2)
            if (
                not credited
                or user_id == g.current_user.id
                or user_id in hidden_ids
            ):
                continue
            if user_id not in stats:
                ordered_ids.append(user_id)
                stats[user_id] = {
                    'games_together': 0,
                    'last_played_at': game.completed_at,
                }
            stats[user_id]['games_together'] += 1
    ordered_ids = ordered_ids[:limit]
    users = {
        user.id: user for user in User.query.filter(
            User.id.in_(ordered_ids), User.deleted_at.is_(None),
        ).all()
    } if ordered_ids else {}
    friends = friend_ids(g.current_user.id)
    items = []
    for user_id in ordered_ids:
        user = users.get(user_id)
        if not user:
            continue
        item = user.to_public_dict()
        item.update({
            'games_together': stats[user_id]['games_together'],
            'last_played_at': iso(stats[user_id]['last_played_at']),
            'is_friend': user_id in friends,
        })
        items.append(item)
    return jsonify({'items': items})


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
    visibility = {}
    for gp, game in rows:
        if game.id not in visibility:
            visibility[game.id] = game.visible_to(g.current_user.id, fids)
        if not visibility[game.id]:
            continue
        game_ids.add(game.id)
        stats = stats_by_friend.setdefault(gp.user_id, {'games': 0, 'wins': 0, 'losses': 0})
        stats['games'] += 1
        if gp.team and game.score_team1 is not None and game.score_team2 is not None:
            won = (game.score_team1 > game.score_team2) == (gp.team == 1)
            stats['wins' if won else 'losses'] += 1
    checkins = (
        CheckIn.query.join(User, CheckIn.user_id == User.id)
        .filter(
            CheckIn.user_id.in_(fids),
            CheckIn.checked_in_at >= cutoff,
            User.nearby_visibility != 'hidden',
        )
        .count()
    )
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


@social_bp.post('/players/<int:user_id>/coming')
@rate_limit(30, 3600)
@login_required
def coming_to_play(user_id):
    """Tell a friend who's out looking for a game that you're on your way."""
    target = db.session.get(User, user_id)
    if not target or target.deleted_at:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_ping_self'}), 400
    if user_id not in friend_ids(g.current_user.id) or is_blocked_between(g.current_user.id, user_id):
        return jsonify({'error': 'not_friends'}), 403
    checkin = active_checkin_for(target.id, fresh=True)
    if not checkin or not checkin.looking_for_game or not checkin.court:
        # Friends may know one another, but a live court ping still requires a
        # current explicit invitation to assemble.
        return jsonify({'error': 'intent_unavailable'}), 409
    where = f' at {checkin.court.name}'
    notify(
        target.id,
        'player_coming',
        # No emoji in titles — the feed prepends the per-kind icon.
        f'{g.current_user.display_name} is coming to play{where}!',
        related_user_id=g.current_user.id,
        action_url=f'/#court/{checkin.court_id}',
    )
    db.session.commit()
    return jsonify({'sent': True})


@social_bp.get('/friends/suggestions')
@login_required
def friend_suggestions():
    """Players you've completed games with but haven't friended — ranked by
    games shared. The organic 'add the people you actually play with' nudge."""
    my_games = {
        game_id for (game_id,) in
        db.session.query(GamePlayer.game_id)
        .join(Game, Game.id == GamePlayer.game_id)
        .filter(
            GamePlayer.user_id == g.current_user.id,
            GamePlayer.team.in_((1, 2)),
            Game.status == 'completed',
        ).all()
    }
    if not my_games:
        return jsonify({'items': []})

    exclude = friend_ids(g.current_user.id) | blocked_pair_ids(g.current_user.id)
    exclude.add(g.current_user.id)
    # Pending requests (either direction) shouldn't be re-suggested.
    for f in Friendship.query.filter(
        or_(Friendship.requester_id == g.current_user.id,
            Friendship.addressee_id == g.current_user.id),
    ).all():
        exclude.add(f.requester_id)
        exclude.add(f.addressee_id)

    counts = {}
    for gp in GamePlayer.query.filter(
        GamePlayer.game_id.in_(my_games), GamePlayer.team.in_((1, 2)),
    ).all():
        if gp.user_id in exclude:
            continue
        counts[gp.user_id] = counts.get(gp.user_id, 0) + 1
    if not counts:
        return jsonify({'items': []})

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    users = {u.id: u for u in User.query.filter(
        User.id.in_([uid for uid, _ in ranked]),
        User.deleted_at.is_(None),
    ).all()}
    return jsonify({'items': [
        {**users[uid].to_public_dict(), 'games_together': n}
        for uid, n in ranked if uid in users
    ]})


@social_bp.post('/friends/request')
@rate_limit(40, 60)
@login_required
def send_friend_request():
    payload = request.get_json(silent=True) or {}
    try:
        target_id = int(payload.get('user_id') or 0)
    except (TypeError, ValueError):
        target_id = 0
    target = db.session.get(User, target_id)
    if not target or target.deleted_at:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_friend_self'}), 400
    _lock_user_pair(g.current_user.id, target.id)
    if is_blocked_between(g.current_user.id, target.id):
        return jsonify({'error': 'user_blocked'}), 403

    def existing_response(existing):
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

    existing = _friendship_between(g.current_user.id, target.id)
    if existing:
        return existing_response(existing)

    friendship = Friendship(
        requester_id=g.current_user.id,
        addressee_id=target.id,
        status='pending',
    )
    db.session.add(friendship)
    try:
        # Claim the unordered pair before creating a notification. A concurrent
        # contender can then resolve against the winner without duplicating
        # downstream social side effects.
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        _lock_user_pair(g.current_user.id, target.id)
        if is_blocked_between(g.current_user.id, target.id):
            return jsonify({'error': 'user_blocked'}), 403
        existing = _friendship_between(g.current_user.id, target.id)
        if not existing:
            raise
        return existing_response(existing)
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
    requester_id = friendship.requester_id
    _lock_user_pair(g.current_user.id, requester_id)
    db.session.expire_all()
    friendship = db.session.get(Friendship, friendship_id)
    if not friendship or friendship.addressee_id != g.current_user.id:
        return jsonify({'error': 'request_not_found'}), 404
    if is_blocked_between(g.current_user.id, requester_id):
        return jsonify({'error': 'user_blocked'}), 403
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
    other_id = (
        friendship.addressee_id
        if friendship.requester_id == g.current_user.id
        else friendship.requester_id
    )
    _lock_user_pair(g.current_user.id, other_id)
    db.session.expire_all()
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
    limit = _positive_page_arg(
        'limit', NOTIFICATIONS_DEFAULT_LIMIT, NOTIFICATIONS_MAX_LIMIT,
    )
    before_id = request.args.get('before_id', type=int)
    hidden_ids = blocked_pair_ids(g.current_user.id)
    visible = Notification.query.filter_by(user_id=g.current_user.id)
    if hidden_ids:
        visible = visible.filter(or_(
            Notification.related_user_id.is_(None),
            Notification.related_user_id.notin_(hidden_ids),
        ))

    unread = visible.filter(Notification.read.is_(False)).count()
    page_query = visible
    if before_id is not None:
        page_query = page_query.filter(Notification.id < before_id)
    page_rows = (
        page_query.order_by(Notification.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(page_rows) > limit
    rows = page_rows[:limit]
    return jsonify({
        'items': [n.to_dict() for n in rows],
        # Account-wide unread count, not merely unread rows on this page.
        'unread': unread,
        'limit': limit,
        'has_more': has_more,
        'next_cursor': rows[-1].id if has_more and rows else None,
    })


def _requested_notification_ids():
    """Return ``None`` for the legacy all-items mutation, or explicit ids.

    Both ``ids`` and ``notification_ids`` are accepted so web and native
    clients can batch the visible page. An empty list intentionally means
    "change nothing" rather than falling through to the legacy clear-all.
    """
    payload = request.get_json(silent=True)
    if payload is None:
        return None, None
    if not isinstance(payload, dict):
        return None, 'invalid_notification_ids'

    present = next(
        (key for key in ('ids', 'notification_ids', 'id') if key in payload),
        None,
    )
    if present is None:
        return None, None
    raw_ids = payload[present]
    if present == 'id':
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list) or len(raw_ids) > 100:
        return None, 'invalid_notification_ids'

    ids = []
    seen = set()
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            return None, 'invalid_notification_ids'
        try:
            notification_id = int(raw_id)
        except (TypeError, ValueError):
            return None, 'invalid_notification_ids'
        if notification_id <= 0:
            return None, 'invalid_notification_ids'
        if notification_id not in seen:
            seen.add(notification_id)
            ids.append(notification_id)
    return ids, None


@social_bp.post('/notifications/read')
@rate_limit(60, 60)
@login_required
def mark_notifications_read():
    ids, error = _requested_notification_ids()
    if error:
        return jsonify({'error': error}), 400
    query = Notification.query.filter_by(user_id=g.current_user.id, read=False)
    if ids is not None:
        if not ids:
            return jsonify({'ok': True, 'read': 0})
        query = query.filter(Notification.id.in_(ids))
    changed = query.update({
        'read': True,
        'unread_dedupe_key': None,
    }, synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True, 'read': changed})


@social_bp.post('/notifications/<int:notification_id>/read')
@rate_limit(60, 60)
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.filter_by(
        id=notification_id, user_id=g.current_user.id,
    ).first()
    if not notification:
        return jsonify({'error': 'notification_not_found'}), 404
    changed = 0
    if not notification.read:
        notification.read = True
        notification.unread_dedupe_key = None
        changed = 1
        db.session.commit()
    return jsonify({'ok': True, 'read': changed, 'id': notification.id})


@social_bp.delete('/notifications')
@rate_limit(20, 3600)
@login_required
def clear_notifications():
    ids, error = _requested_notification_ids()
    if error:
        return jsonify({'error': error}), 400
    query = Notification.query.filter_by(user_id=g.current_user.id)
    if ids is not None:
        if not ids:
            return jsonify({'cleared': 0})
        query = query.filter(Notification.id.in_(ids))
    removed = query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'cleared': removed})


@social_bp.delete('/notifications/<int:notification_id>')
@rate_limit(60, 60)
@login_required
def clear_notification(notification_id):
    notification = Notification.query.filter_by(
        id=notification_id, user_id=g.current_user.id,
    ).first()
    if not notification:
        return jsonify({'error': 'notification_not_found'}), 404
    db.session.delete(notification)
    db.session.commit()
    return jsonify({'cleared': 1, 'id': notification_id})
