"""Court discovery, detail, and check-in routes."""
import base64
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import timedelta

from flask import Blueprint, Response, current_app, g, jsonify, request
from sqlalchemy import func, or_

from backend.app import db
from backend.models import (
    COURT_CONDITIONS, CheckIn, Court, CourtChatRead, CourtCondition,
    CourtEditSuggestion, CourtPhoto, CourtReview, FavoriteCourt, Game,
    GamePlayer, Message, Notification, User, blocked_pair_ids, iso, notify,
    utcnow,
)
from backend.routes.auth import (
    active_checkin_for,
    checkin_is_fresh,
    login_required,
    optional_current_user,
    presence_absolute_cutoff,
    presence_payload,
    presence_stale_cutoff,
)
from backend.routes.social import friend_ids
from backend.security import rate_limit

courts_bp = Blueprint('courts', __name__)

MAX_COURT_RESULTS = 300

# --- Geocoding (OpenStreetMap Nominatim proxy) ---
_GEOCODE_CACHE = {}
_GEOCODE_CACHE_TTL = 60 * 60 * 24  # 24h — place coordinates don't move
_GEOCODE_MAX_CACHE = 500


def _lock_open_instant_games_for_user(user_id):
    """Lock this member's live assembly rows in canonical id order."""
    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user_id,
            Game.is_instant.is_(True),
            Game.status == 'upcoming',
            Game.assembly_closed_at.is_(None),
        )
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for game in games:
        db.session.expire(game, ['players'])
    return games


def _close_departed_instant_assemblies(games, user_id, court_id, now):
    """Persist one-way closure when the last fresh member leaves a court."""
    from backend.routes.games import _end_game_arrivals

    stale_cutoff = presence_stale_cutoff(now)
    absolute_cutoff = presence_absolute_cutoff(now)
    for game in games:
        if game.court_id != court_id or game.assembly_closed_at is not None:
            continue
        other_member_ids = {
            player.user_id for player in game.players
            if player.user_id != user_id
        }
        other_fresh = None
        if other_member_ids:
            other_fresh = (
                CheckIn.query.filter(
                    CheckIn.user_id.in_(sorted(other_member_ids)),
                    CheckIn.court_id == court_id,
                    CheckIn.checked_out_at.is_(None),
                    CheckIn.checked_in_at >= absolute_cutoff,
                    CheckIn.last_presence_ping_at >= stale_cutoff,
                )
                .order_by(CheckIn.user_id.asc(), CheckIn.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
                .first()
            )
        if other_fresh is None:
            game.assembly_closed_at = now
            if len(game.players) <= 1:
                game.status = 'expired'
            _end_game_arrivals(game, 'presence_ended', now)


def _nominatim_fetch(query):
    """Fetch geocoding results from Nominatim. Isolated so tests can mock it."""
    params = urllib.parse.urlencode({
        'q': query,
        'format': 'jsonv2',
        'addressdetails': 1,
        'limit': 5,
        'countrycodes': 'us',
    })
    url = f'https://nominatim.openstreetmap.org/search?{params}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'ThirdShot/1.0 (pickleball court finder; contact: support@thirdshot.app)',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _format_place(raw):
    try:
        lat = float(raw['lat'])
        lng = float(raw['lon'])
    except (KeyError, TypeError, ValueError):
        return None
    addr = raw.get('address') or {}
    city = (addr.get('city') or addr.get('town') or addr.get('village')
            or addr.get('hamlet') or addr.get('county') or '')
    state = addr.get('state') or ''
    short = ', '.join(part for part in (city, state) if part)
    label = short or (raw.get('display_name') or '').split(',')[0]
    return {
        'lat': lat,
        'lng': lng,
        'label': label,
        'detail': raw.get('display_name', ''),
    }


@courts_bp.get('/geocode')
def geocode():
    """Search for a place by name and return coordinates to recenter the map."""
    query = str(request.args.get('q') or '').strip()
    if len(query) < 3:
        return jsonify({'items': []})

    key = query.lower()
    cached = _GEOCODE_CACHE.get(key)
    if cached and cached['expires_at'] > time.time():
        return jsonify({'items': cached['items']})

    try:
        raw_results = _nominatim_fetch(query)
    except Exception:
        current_app.logger.warning('Geocode lookup failed for %r', query, exc_info=True)
        return jsonify({'items': [], 'error': 'geocode_unavailable'})

    items = [p for p in (_format_place(r) for r in (raw_results or [])) if p][:5]

    if len(_GEOCODE_CACHE) > _GEOCODE_MAX_CACHE:
        _GEOCODE_CACHE.clear()
    _GEOCODE_CACHE[key] = {'items': items, 'expires_at': time.time() + _GEOCODE_CACHE_TTL}
    return jsonify({'items': items})


def _nominatim_reverse(lat, lng):
    """Reverse-geocode coordinates to a place. Isolated so tests can mock it."""
    params = urllib.parse.urlencode({
        'lat': lat, 'lon': lng, 'format': 'jsonv2', 'addressdetails': 1, 'zoom': 10,
    })
    url = f'https://nominatim.openstreetmap.org/reverse?{params}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'ThirdShot/1.0 (pickleball court finder; contact: support@thirdshot.app)',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read().decode('utf-8'))


@courts_bp.get('/geocode/reverse')
def geocode_reverse():
    """Turn coordinates into a human area label (for naming a home area)."""
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({'error': 'lat_lng_required'}), 400
    try:
        raw = _nominatim_reverse(lat, lng)
    except Exception:
        current_app.logger.warning('Reverse geocode failed for %s,%s', lat, lng, exc_info=True)
        return jsonify({'label': '', 'error': 'geocode_unavailable'})
    place = _format_place(raw) if raw else None
    return jsonify({'label': place['label'] if place else ''})


def cleanup_stale_presence():
    """Auto check-out anyone whose presence ping is older than the staleness window."""
    now = utcnow()
    cutoff = presence_stale_cutoff(now)
    absolute_cutoff = presence_absolute_cutoff(now)
    stale_user_ids = [
        row[0] for row in db.session.query(CheckIn.user_id).filter(
            CheckIn.checked_out_at.is_(None),
            or_(
                CheckIn.last_presence_ping_at < cutoff,
                CheckIn.checked_in_at < absolute_cutoff,
            ),
        ).order_by(CheckIn.user_id.asc()).all()
    ]
    for user_id in stale_user_ids:
        user = (
            User.query.filter(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        if not user:
            continue
        games = _lock_open_instant_games_for_user(user_id)
        checkin = active_checkin_for(user_id, for_update=True)
        if not checkin or not (
            checkin.last_presence_ping_at < cutoff
            or checkin.checked_in_at < absolute_cutoff
        ):
            db.session.commit()
            continue
        _close_departed_instant_assemblies(
            games, user_id, checkin.court_id, now,
        )
        checkin.checked_out_at = now
        checkin.looking_for_game = False
        db.session.commit()


def haversine_miles(lat1, lng1, lat2, lng2):
    radius_miles = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * radius_miles * math.asin(math.sqrt(a))


def _rating_summary_for(court_ids):
    """Batch {court_id: {avg, count}} for a list of courts."""
    if not court_ids:
        return {}
    rows = (
        db.session.query(
            CourtReview.court_id,
            func.avg(CourtReview.rating),
            func.count(CourtReview.id),
        )
        .filter(CourtReview.court_id.in_(court_ids))
        .group_by(CourtReview.court_id)
        .all()
    )
    return {
        cid: {'rating_avg': round(float(avg), 1), 'rating_count': int(count)}
        for cid, avg, count in rows
    }


def _active_counts_for(court_ids, current_user=None):
    """Return fresh players and viewer-visible games with an open roster spot.

    Court summaries used to count every future game, including full and
    invite-only games the viewer could not see. That made the map promise an
    "open game" that disappeared on tap. Keep this aggregate aligned with the
    same discovery/privacy rules as the games feed.
    """
    if not court_ids:
        return {}, {}
    rows = (
        db.session.query(CheckIn.court_id, func.count(CheckIn.id))
        .filter(
            CheckIn.court_id.in_(court_ids),
            CheckIn.checked_out_at.is_(None),
            CheckIn.checked_in_at >= presence_absolute_cutoff(),
            CheckIn.last_presence_ping_at >= presence_stale_cutoff(),
        )
        .group_by(CheckIn.court_id)
        .all()
    )
    players = {court_id: count for court_id, count in rows}
    now = utcnow()
    game_rows = (
        Game.query
        .filter(
            Game.court_id.in_(court_ids),
            Game.status == 'upcoming',
            # Match the discovery/detail window; live rallies scheduled a few
            # minutes ago remain joinable while their assembly is active.
            Game.scheduled_at >= now - timedelta(hours=2),
        )
        .all()
    )
    viewer_id = current_user.id if current_user else None
    viewer_friends = friend_ids(viewer_id) if viewer_id else set()
    hidden_ids = blocked_pair_ids(viewer_id) if viewer_id else set()
    # Local import avoids the games -> courts import cycle at module load.
    from backend.routes.games import (
        _game_has_blocked_participant,
        _instant_game_discovery_allowed,
        _instant_rally_is_actionable,
    )
    games = {}
    for game in game_rows:
        if len(game.players) >= game.max_players:
            continue
        if not game.visible_to(viewer_id, viewer_friends):
            continue
        if _game_has_blocked_participant(game, viewer_id, hidden_ids):
            continue
        if not _instant_game_discovery_allowed(game, current_user, viewer_friends):
            continue
        if game.is_instant and not _instant_rally_is_actionable(game, now):
            continue
        games[game.court_id] = games.get(game.court_id, 0) + 1
    return players, games


@courts_bp.get('/courts')
def list_courts():
    """Court search: by map bounds (west,south,east,north) or lat/lng radius, plus text query."""
    cleanup_stale_presence()
    current_user = optional_current_user()
    query = Court.query.filter(
        Court.latitude.isnot(None),
        Court.longitude.isnot(None),
        Court.closed.is_(False),
    )

    text = str(request.args.get('q') or '').strip()
    if text:
        like = f'%{text}%'
        query = query.filter(
            Court.name.ilike(like) | Court.city.ilike(like) | Court.address.ilike(like)
        )

    truthy = {'1', 'true'}
    if str(request.args.get('lighted') or '') in truthy:
        query = query.filter(Court.lighted.is_(True))
    if str(request.args.get('indoor') or '') in truthy:
        query = query.filter(Court.indoor.is_(True))
    if str(request.args.get('restrooms') or '') in truthy:
        query = query.filter(Court.has_restrooms.is_(True))
    if str(request.args.get('water') or '') in truthy:
        query = query.filter(Court.has_water.is_(True))
    if str(request.args.get('nets') or '') in truthy:
        query = query.filter(Court.nets_provided.is_(True))

    bbox = str(request.args.get('bbox') or '').strip()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)

    if bbox:
        try:
            west, south, east, north = [float(part) for part in bbox.split(',')]
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid_bbox'}), 400
        query = query.filter(
            Court.latitude >= south, Court.latitude <= north,
            Court.longitude >= west, Court.longitude <= east,
        )
    elif lat is not None and lng is not None:
        radius = min(max(request.args.get('radius', default=25.0, type=float), 1.0), 100.0)
        lat_delta = radius / 69.0
        lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
        query = query.filter(
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )

    limit = min(request.args.get('limit', default=MAX_COURT_RESULTS, type=int), MAX_COURT_RESULTS)
    sort = str(request.args.get('sort') or 'distance').strip().lower()
    if sort == 'rating':
        # Order by review average in SQL so the ranking survives the limit cut.
        rating_sq = (
            db.session.query(
                CourtReview.court_id.label('court_id'),
                func.avg(CourtReview.rating).label('rating_avg'),
                func.count(CourtReview.id).label('rating_count'),
            )
            .group_by(CourtReview.court_id)
            .subquery()
        )
        query = query.outerjoin(rating_sq, Court.id == rating_sq.c.court_id).order_by(
            rating_sq.c.rating_avg.desc().nullslast(),
            rating_sq.c.rating_count.desc().nullslast(),
            Court.num_courts.desc(),
            Court.id.asc(),
        )
    else:
        query = query.order_by(Court.num_courts.desc(), Court.id.asc())
    courts = query.limit(limit * 3).all()

    items = []
    for court in courts:
        item = court.to_summary_dict()
        if lat is not None and lng is not None:
            item['distance_miles'] = round(
                haversine_miles(lat, lng, court.latitude, court.longitude), 1,
            )
        items.append(item)
    if sort == 'distance' and lat is not None and lng is not None:
        items.sort(key=lambda c: c.get('distance_miles', 0))
    elif sort == 'active':
        # Rank by live activity — needs the counts computed on the full
        # candidate pool before the limit cut, then closest as a tiebreak.
        pool_players, pool_games = _active_counts_for(
            [c['id'] for c in items], current_user,
        )
        items.sort(key=lambda c: (
            -(pool_players.get(c['id'], 0) + pool_games.get(c['id'], 0)),
            c.get('distance_miles', 1e9),
        ))
    items = items[:limit]

    _enrich_court_summaries(items, current_user)

    return jsonify({'items': items, 'count': len(items)})


@courts_bp.post('/courts')
@rate_limit(5, 3600)
@login_required
def submit_court():
    """Community-submitted court — pinned wherever the player says it is."""
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()[:255]
    if len(name) < 3:
        return jsonify({'error': 'name_required'}), 400
    try:
        lat = float(payload.get('latitude'))
        lng = float(payload.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'error': 'location_required'}), 400
    if not (18.0 <= lat <= 72.0 and -180.0 <= lng <= -66.0):
        return jsonify({'error': 'location_out_of_range'}), 400
    try:
        num_courts = max(1, min(100, int(payload.get('num_courts') or 2)))
    except (TypeError, ValueError):
        num_courts = 2
    court = Court(
        name=name,
        latitude=lat,
        longitude=lng,
        num_courts=num_courts,
        indoor=bool(payload.get('indoor')),
        lighted=bool(payload.get('lighted')),
        city=str(payload.get('city') or '').strip()[:120],
        state=str(payload.get('state') or '').strip().upper()[:2],
        county_slug='community',
        verified=False,
    )
    db.session.add(court)
    db.session.flush()
    # Submitters care about their court — save it for them.
    db.session.add(FavoriteCourt(user_id=g.current_user.id, court_id=court.id))
    db.session.commit()
    return jsonify(court.to_dict()), 201


@courts_bp.get('/courts/<int:court_id>')
def court_detail(court_id):
    cleanup_stale_presence()
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    current_user = optional_current_user()
    viewer_friends = friend_ids(current_user.id) if current_user else set()
    hidden_ids = blocked_pair_ids(current_user.id) if current_user else set()
    active = (
        CheckIn.query.filter(
            CheckIn.court_id == court.id,
            CheckIn.checked_out_at.is_(None),
            CheckIn.checked_in_at >= presence_absolute_cutoff(),
            CheckIn.last_presence_ping_at >= presence_stale_cutoff(),
        )
        .order_by(CheckIn.checked_in_at.asc())
        .all()
    )
    now = utcnow()
    players_here = []
    visible_player_count = 0
    for checkin in active:
        if (
            not checkin.user or checkin.user.deleted_at
            or checkin.user_id in hidden_ids
        ):
            continue
        visible_player_count += 1
        # A public court page may show live aggregate activity, never the
        # identities of people physically present. Signed-in viewers see
        # themselves, friends, and people who explicitly opted into local
        # discovery by selecting "looking for a game."
        if not current_user:
            continue
        is_friend = checkin.user_id in viewer_friends
        is_me = checkin.user_id == current_user.id
        if not (is_me or is_friend or checkin.looking_for_game):
            continue
        entry = checkin.user.to_public_dict()
        entry['looking_for_game'] = bool(checkin.looking_for_game)
        entry['checked_in_at'] = checkin.checked_in_at.isoformat() + 'Z' if checkin.checked_in_at else None
        entry['minutes_here'] = (
            max(0, int((now - checkin.checked_in_at).total_seconds() // 60))
            if checkin.checked_in_at else 0
        )
        entry['is_friend'] = is_friend
        entry['is_me'] = is_me
        players_here.append(entry)
    # Friends first, then players looking for a game
    players_here.sort(key=lambda p: (not p['is_friend'], not p['looking_for_game']))

    upcoming = (
        Game.query.filter(
            Game.court_id == court.id,
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow() - timedelta(hours=2),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(20)
        .all()
    )

    recent_completed = (
        Game.query.filter(Game.court_id == court.id, Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(3)
        .all()
    )

    payload = court.to_dict()
    payload['photo_count'] = CourtPhoto.query.filter_by(court_id=court.id).count()
    payload['latest_condition'] = _latest_condition_for(court.id)

    # The viewer's personal win-loss record at this court.
    payload['my_record'] = None
    if current_user:
        my_games = (
            Game.query.join(GamePlayer)
            .filter(
                GamePlayer.user_id == current_user.id,
                Game.court_id == court.id,
                Game.status == 'completed',
                Game.score_team1.isnot(None),
            )
            .all()
        )
        wins = losses = 0
        for played in my_games:
            mine = next((p for p in played.players if p.user_id == current_user.id), None)
            if not mine or not mine.team:
                continue
            if (played.score_team1 > played.score_team2) == (mine.team == 1):
                wins += 1
            else:
                losses += 1
        if wins or losses:
            payload['my_record'] = {'wins': wins, 'losses': losses}

    # Court regulars: most frequent visitors over the last 60 days (2+ visits).
    from backend.models import User as UserModel
    regular_rows = (
        db.session.query(UserModel, func.count(CheckIn.id).label('visits'))
        .join(CheckIn, CheckIn.user_id == UserModel.id)
        .filter(
            CheckIn.court_id == court.id,
            CheckIn.checked_in_at >= utcnow() - timedelta(days=60),
            UserModel.deleted_at.is_(None),
        )
        .group_by(UserModel.id)
        .having(func.count(CheckIn.id) >= 2)
        .order_by(func.count(CheckIn.id).desc())
        .limit(5)
        .all()
    )
    payload['regulars'] = [
        {**user.to_public_dict(), 'visits': int(visits)}
        for user, visits in regular_rows if user.id not in hidden_ids
    ]
    payload['busy_times'] = _busy_times(court)
    payload['court_leaders'] = _court_leaders(court, hidden_ids)
    # Court-chat unread count — only once they've opened that chat before,
    # so untouched chat rooms don't nag.
    payload['chat_unread'] = 0
    if current_user:
        marker = CourtChatRead.query.filter_by(
            user_id=current_user.id, court_id=court.id,
        ).first()
        if marker:
            unread_query = Message.query.filter(
                Message.court_id == court.id,
                Message.id > marker.last_read_message_id,
            )
            if hidden_ids:
                unread_query = unread_query.filter(Message.sender_id.notin_(hidden_ids))
            payload['chat_unread'] = unread_query.count()
    payload['players_here'] = players_here
    payload['players_here_count'] = visible_player_count
    payload['friends_here'] = sum(1 for p in players_here if p['is_friend'])
    viewer_id = current_user.id if current_user else None
    # Tournaments hosted here — anything open for registration or under way.
    from backend.models import Tournament
    court_tournaments = (
        Tournament.query.filter(
            Tournament.court_id == court.id,
            Tournament.status.in_(['registration', 'active']),
            Tournament.starts_at >= utcnow() - timedelta(days=3),
        )
        .order_by(Tournament.starts_at.asc())
        .limit(3)
        .all()
    )
    payload['tournaments'] = [t.to_dict(viewer_id) for t in court_tournaments]
    # Hall of fame: recent tournament champions crowned at this court.
    past = (
        Tournament.query.filter(
            Tournament.court_id == court.id,
            Tournament.status == 'completed',
            Tournament.champion_entry_id.isnot(None),
        )
        .order_by(Tournament.completed_at.desc())
        .limit(3)
        .all()
    )
    payload['past_champions'] = [
        {
            'tournament_id': t.id,
            'tournament_name': t.name,
            'champion_name': t.champion_entry.display_name() if t.champion_entry else 'Champion',
            'completed_at': t.completed_at.isoformat() + 'Z' if t.completed_at else None,
        }
        for t in past
    ]
    # Runtime import avoids the games -> courts module cycle while sharing the
    # exact privacy contract used by the public game feed/detail routes.
    from backend.routes.games import (
        _discovery_game_payload,
        _instant_game_discovery_allowed,
    )

    def game_visible(game):
        player_ids = {player.user_id for player in game.players}
        blocked_game = bool(
            viewer_id and viewer_id not in player_ids and player_ids & hidden_ids
        )
        return (
            game.visible_to(viewer_id, viewer_friends)
            and not blocked_game
            and _instant_game_discovery_allowed(
                game, current_user, viewer_friends,
            )
        )

    visible_upcoming = [game for game in upcoming if game_visible(game)]
    payload['games'] = [
        _discovery_game_payload(game, current_user, viewer_friends)
        for game in visible_upcoming
    ]
    # "Now at this court" is an immediate assembly signal, not a count of
    # every plan on the calendar. Keep later games in `games` below while
    # giving the client a bounded, authoritative set for the Now card.
    now_window_end = now + timedelta(hours=2)
    payload['now_games'] = [
        _discovery_game_payload(game, current_user, viewer_friends)
        for game in visible_upcoming if game.scheduled_at <= now_window_end
    ]
    payload['recent_results'] = [
        _discovery_game_payload(game, current_user, viewer_friends)
        for game in recent_completed if game_visible(game)
    ]
    payload['is_checked_in'] = bool(
        current_user and any(c.user_id == current_user.id for c in active)
    )
    payload['is_favorite'] = bool(
        current_user and FavoriteCourt.query.filter_by(
            user_id=current_user.id, court_id=court.id,
        ).first()
    )

    summary = _rating_summary_for([court.id]).get(court.id)
    payload['rating_avg'] = summary['rating_avg'] if summary else None
    payload['rating_count'] = summary['rating_count'] if summary else 0
    recent_reviews = (
        CourtReview.query.filter_by(court_id=court.id)
        .order_by(CourtReview.updated_at.desc())
        .limit(10)
        .all()
    )
    payload['reviews'] = [
        review.to_dict() for review in recent_reviews
        if review.user_id not in hidden_ids
    ]
    my_review = (
        CourtReview.query.filter_by(court_id=court.id, user_id=current_user.id).first()
        if current_user else None
    )
    payload['my_review'] = my_review.to_dict() if my_review else None
    return jsonify(payload)


@courts_bp.get('/courts/<int:court_id>/reviews')
def court_reviews(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    reviews = (
        CourtReview.query.filter_by(court_id=court.id)
        .order_by(CourtReview.updated_at.desc())
        .limit(50)
        .all()
    )
    current_user = optional_current_user()
    hidden_ids = blocked_pair_ids(current_user.id) if current_user else set()
    summary = _rating_summary_for([court.id]).get(court.id)
    return jsonify({
        'items': [r.to_dict() for r in reviews if r.user_id not in hidden_ids],
        'rating_avg': summary['rating_avg'] if summary else None,
        'rating_count': summary['rating_count'] if summary else 0,
    })


@courts_bp.post('/courts/<int:court_id>/reviews')
@rate_limit(20, 60)
@login_required
def upsert_review(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    payload = request.get_json(silent=True) or {}
    try:
        rating = int(payload.get('rating'))
    except (TypeError, ValueError):
        return jsonify({'error': 'rating_required'}), 400
    if rating < 1 or rating > 5:
        return jsonify({'error': 'invalid_rating'}), 400
    comment = str(payload.get('comment') or '').strip()[:500]

    review = CourtReview.query.filter_by(court_id=court.id, user_id=g.current_user.id).first()
    if not review:
        review = CourtReview(court_id=court.id, user_id=g.current_user.id)
        db.session.add(review)
    review.rating = rating
    review.comment = comment
    db.session.commit()
    summary = _rating_summary_for([court.id]).get(court.id)
    return jsonify({
        'review': review.to_dict(),
        'rating_avg': summary['rating_avg'] if summary else None,
        'rating_count': summary['rating_count'] if summary else 0,
    }), 201


# field → normalizer, raising ValueError on bad input
def _norm_bool(v):
    if not isinstance(v, bool):
        raise ValueError
    return v


def _norm_courts(v):
    n = int(v)
    if not 1 <= n <= 100:
        raise ValueError
    return n


def _norm_text(limit):
    def norm(v):
        return str(v or '').strip()[:limit]
    return norm


SUGGESTABLE_FIELDS = {
    'num_courts': _norm_courts,
    'indoor': _norm_bool,
    'lighted': _norm_bool,
    'nets_provided': _norm_bool,
    'has_restrooms': _norm_bool,
    'has_water': _norm_bool,
    'surface_type': _norm_text(60),
    'fees': _norm_text(200),
    'hours': _norm_text(120),
    'closed': _norm_bool,
}
SUGGESTION_CONSENSUS = 2


@courts_bp.post('/courts/<int:court_id>/suggest')
@rate_limit(20, 3600)
@login_required
def suggest_court_edit(court_id):
    """Record proposed data fixes; apply a field once two users agree on it."""
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    body = request.get_json(silent=True) or {}
    changes = {}
    for field, normalize in SUGGESTABLE_FIELDS.items():
        if field not in body:
            continue
        try:
            value = normalize(body[field])
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid_field', 'field': field}), 400
        current = getattr(court, field)
        if isinstance(value, str):
            current = current or ''
        if value != current:
            changes[field] = value
    if not changes:
        return jsonify({'error': 'no_changes'}), 400

    # One live suggestion per user per court — resubmitting replaces it.
    suggestion = CourtEditSuggestion.query.filter_by(
        court_id=court.id, user_id=g.current_user.id, status='pending',
    ).first()
    if not suggestion:
        suggestion = CourtEditSuggestion(court_id=court.id, user_id=g.current_user.id)
        db.session.add(suggestion)
    suggestion.payload = json.dumps(changes)
    db.session.flush()

    # Consensus pass: apply any field value that N distinct users proposed.
    pending = CourtEditSuggestion.query.filter_by(court_id=court.id, status='pending').all()
    votes = {}
    for s in pending:
        for field, value in json.loads(s.payload).items():
            votes.setdefault((field, json.dumps(value)), set()).add(s.user_id)
    applied = {}
    for (field, packed), users in votes.items():
        if len(users) >= SUGGESTION_CONSENSUS and field in SUGGESTABLE_FIELDS:
            value = json.loads(packed)
            setattr(court, field, value)
            applied[field] = value
    if applied:
        for s in pending:
            remaining = {
                f: v for f, v in json.loads(s.payload).items()
                if f not in applied or v != applied[f]
            }
            if remaining:
                s.payload = json.dumps(remaining)
            else:
                s.status = 'applied'
    db.session.commit()
    return jsonify({
        'submitted': True,
        'applied_fields': sorted(applied),
        'court': court.to_dict(),
    }), 201


_PHOTO_DATA_RE = re.compile(r'^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)$')
MAX_PHOTO_BYTES = 500 * 1024


MAX_COURT_PHOTOS = 12
CONDITION_FRESH_HOURS = 3

# --- Court weather (US National Weather Service — free, keyless, and unlike
# Open-Meteo it serves datacenter IPs, which killed the first attempt on prod) ---
_WEATHER_CACHE = {}
_WEATHER_CACHE_TTL = 60 * 30  # forecasts don't move fast
_WEATHER_MAX_CACHE = 500
_NWS_HEADERS = {
    'User-Agent': 'ThirdShot/1.0 (pickleball court finder; contact: support@thirdshot.app)',
    'Accept': 'application/geo+json',
}


def _nws_fetch(lat, lng):
    """Hourly forecast summary via api.weather.gov. Isolated for test mocks."""
    req = urllib.request.Request(
        f'https://api.weather.gov/points/{lat:.3f},{lng:.3f}', headers=_NWS_HEADERS,
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        hourly_url = json.loads(resp.read())['properties']['forecastHourly']
    req = urllib.request.Request(hourly_url, headers=_NWS_HEADERS)
    with urllib.request.urlopen(req, timeout=8) as resp:
        periods = json.loads(resp.read())['properties']['periods'][:6]
    now = periods[0]
    rain_odds = max(
        (p.get('probabilityOfPrecipitation') or {}).get('value') or 0 for p in periods
    )
    return {
        'temp_f': round(float(now['temperature'])),
        'short': str(now.get('shortForecast') or '')[:60],
        'rain_soon': rain_odds >= 40,
    }


@courts_bp.get('/courts/<int:court_id>/weather')
def court_weather(court_id):
    court = db.session.get(Court, court_id)
    if not court or court.latitude is None:
        return jsonify({'error': 'court_not_found'}), 404
    # Fresh condition reports ride along so game screens get both in one call.
    # They're per-court and short-lived, so they stay OUT of the weather cache
    # (which is shared across courts at the same rounded lat/lng).
    condition = _latest_condition_for(court.id)
    key = (round(court.latitude, 2), round(court.longitude, 2))
    cached = _WEATHER_CACHE.get(key)
    if cached and cached['expires_at'] > time.time():
        return jsonify({**cached['data'], 'latest_condition': condition})
    try:
        data = _nws_fetch(court.latitude, court.longitude)
    except Exception:
        current_app.logger.warning('Weather lookup failed for court %s', court_id, exc_info=True)
        return jsonify({'error': 'weather_unavailable', 'latest_condition': condition})
    if len(_WEATHER_CACHE) > _WEATHER_MAX_CACHE:
        _WEATHER_CACHE.clear()
    _WEATHER_CACHE[key] = {'data': data, 'expires_at': time.time() + _WEATHER_CACHE_TTL}
    return jsonify({**data, 'latest_condition': condition})


@courts_bp.post('/courts/<int:court_id>/condition')
@rate_limit(30, 3600)
@login_required
def report_condition(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    condition = str((request.get_json(silent=True) or {}).get('condition') or '').strip()
    if condition not in COURT_CONDITIONS:
        return jsonify({'error': 'invalid_condition'}), 400
    db.session.add(CourtCondition(
        court_id=court.id, user_id=g.current_user.id, condition=condition,
    ))
    db.session.commit()
    return jsonify({'ok': True, 'condition': condition}), 201


def _conditions_for(court_ids):
    """Batch {court_id: freshest condition} for list views."""
    if not court_ids:
        return {}
    rows = (
        CourtCondition.query.filter(
            CourtCondition.court_id.in_(court_ids),
            CourtCondition.created_at >= utcnow() - timedelta(hours=CONDITION_FRESH_HOURS),
        )
        .order_by(CourtCondition.id.asc())
        .all()
    )
    return {r.court_id: r.condition for r in rows}  # later (newer) rows win


def _enrich_court_summaries(items, current_user=None):
    """Add the same live discovery signals to every court summary payload."""
    ids = [item['id'] for item in items]
    players, games = _active_counts_for(ids, current_user)
    ratings = _rating_summary_for(ids)
    conditions = _conditions_for(ids)
    for item in items:
        court_id = item['id']
        item['players_here'] = players.get(court_id, 0)
        item['upcoming_games'] = games.get(court_id, 0)
        item['condition'] = conditions.get(court_id)
        rating = ratings.get(court_id)
        item['rating_avg'] = rating['rating_avg'] if rating else None
        item['rating_count'] = rating['rating_count'] if rating else 0
    return items


def _latest_condition_for(court_id):
    row = (
        CourtCondition.query.filter(
            CourtCondition.court_id == court_id,
            CourtCondition.created_at >= utcnow() - timedelta(hours=CONDITION_FRESH_HOURS),
        )
        .order_by(CourtCondition.id.desc())
        .first()
    )
    if not row:
        return None
    return {
        'condition': row.condition,
        'reported_at': iso(row.created_at),
        'user_name': row.user.display_name if row.user else 'Player',
    }


def _photo_response(data_url):
    match = _PHOTO_DATA_RE.match(data_url or '')
    if not match:
        return jsonify({'error': 'photo_not_found'}), 404
    return Response(
        base64.b64decode(match.group(2)),
        mimetype=f'image/{match.group(1)}',
        headers={'Cache-Control': 'public, max-age=86400'},
    )


@courts_bp.post('/courts/<int:court_id>/photo')
@login_required
@rate_limit(10, 3600)
def upload_court_photo(court_id):
    """Add a community photo to the court's gallery. The newest one becomes
    the hero unless a curated/external photo exists."""
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    if CourtPhoto.query.filter_by(court_id=court.id).count() >= MAX_COURT_PHOTOS:
        return jsonify({'error': 'gallery_full'}), 409

    payload = request.get_json(silent=True) or {}
    match = _PHOTO_DATA_RE.match(str(payload.get('photo') or ''))
    if not match:
        return jsonify({'error': 'invalid_photo'}), 400
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except ValueError:  # binascii.Error subclasses ValueError
        return jsonify({'error': 'invalid_photo'}), 400
    if not (100 <= len(raw) <= MAX_PHOTO_BYTES):
        return jsonify({'error': 'photo_too_large' if len(raw) > MAX_PHOTO_BYTES else 'invalid_photo'}), 400

    photo = CourtPhoto(
        court_id=court.id,
        user_id=g.current_user.id,
        photo_data=f'data:image/{match.group(1)};base64,{match.group(2)}',
        caption=str(payload.get('caption') or '').strip()[:140],
    )
    db.session.add(photo)
    db.session.flush()
    if not court.photo_url or court.photo_url.startswith('/api/courts/'):
        court.photo_url = f'/api/courts/{court.id}/photo'
    db.session.commit()
    return jsonify({
        'photo_url': court.photo_url,
        'photo_id': photo.id,
        'photo_count': CourtPhoto.query.filter_by(court_id=court.id).count(),
    }), 201


@courts_bp.get('/courts/<int:court_id>/photo')
def court_photo(court_id):
    """The court's hero image: newest gallery photo, else the legacy single."""
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'photo_not_found'}), 404
    newest = (
        CourtPhoto.query.filter_by(court_id=court.id)
        .order_by(CourtPhoto.id.desc())
        .first()
    )
    return _photo_response(newest.photo_data if newest else court.photo_data)


@courts_bp.get('/courts/<int:court_id>/photos')
def court_photos(court_id):
    rows = (
        CourtPhoto.query.filter_by(court_id=court_id)
        .order_by(CourtPhoto.id.desc())
        .limit(MAX_COURT_PHOTOS)
        .all()
    )
    from backend.models import CourtPhotoLike
    photo_ids = [p.id for p in rows]
    likes = {}
    mine = set()
    if photo_ids:
        for like in CourtPhotoLike.query.filter(
                CourtPhotoLike.photo_id.in_(photo_ids)).all():
            likes[like.photo_id] = likes.get(like.photo_id, 0) + 1
        viewer = optional_current_user()
        if viewer:
            mine = {
                like.photo_id
                for like in CourtPhotoLike.query.filter(
                    CourtPhotoLike.photo_id.in_(photo_ids),
                    CourtPhotoLike.user_id == viewer.id,
                )
            }
    return jsonify({'items': [{
        'id': p.id,
        'url': f'/api/courts/{court_id}/photos/{p.id}',
        'user_name': p.user.display_name if p.user else 'Player',
        'caption': p.caption or '',
        'likes': likes.get(p.id, 0),
        'liked_by_me': p.id in mine,
        'created_at': iso(p.created_at),
    } for p in rows]})


@courts_bp.post('/courts/<int:court_id>/photos/<int:photo_id>/like')
@rate_limit(120, 3600)
@login_required
def toggle_photo_like(court_id, photo_id):
    photo = db.session.get(CourtPhoto, photo_id)
    if not photo or photo.court_id != court_id:
        return jsonify({'error': 'photo_not_found'}), 404
    from backend.models import CourtPhotoLike
    existing = CourtPhotoLike.query.filter_by(
        user_id=g.current_user.id, photo_id=photo.id,
    ).first()
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(CourtPhotoLike(user_id=g.current_user.id, photo_id=photo.id))
        liked = True
    db.session.commit()
    count = CourtPhotoLike.query.filter_by(photo_id=photo.id).count()
    return jsonify({'liked': liked, 'likes': count})


@courts_bp.get('/courts/<int:court_id>/photos/<int:photo_id>')
def court_photo_item(court_id, photo_id):
    photo = db.session.get(CourtPhoto, photo_id)
    if not photo or photo.court_id != court_id:
        return jsonify({'error': 'photo_not_found'}), 404
    return _photo_response(photo.photo_data)


@courts_bp.post('/courts/<int:court_id>/favorite')
@rate_limit(60, 600)
@login_required
def toggle_favorite(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    existing = FavoriteCourt.query.filter_by(
        user_id=g.current_user.id, court_id=court.id,
    ).first()
    if existing:
        db.session.delete(existing)
        favorited = False
    else:
        db.session.add(FavoriteCourt(user_id=g.current_user.id, court_id=court.id))
        favorited = True
    db.session.commit()
    return jsonify({'favorited': favorited})


@courts_bp.get('/courts/favorites')
@login_required
def list_favorites():
    return jsonify(favorite_courts_payload(g.current_user))


def favorite_courts_payload(user):
    """Endpoint-shaped saved courts, including the normal live enrichment."""
    cleanup_stale_presence()
    favorites = (
        FavoriteCourt.query.filter_by(user_id=user.id)
        .order_by(FavoriteCourt.id.desc())
        .all()
    )
    items = _enrich_court_summaries([
        favorite.court.to_summary_dict()
        for favorite in favorites
        if favorite.court
    ], user)
    return {'items': items}


def _court_leaders(court, hidden_ids=None):
    """Top winners at this court — most wins across completed scored games,
    ranked by wins then win rate. The local 'court champions' board."""
    from backend.models import User as UserModel
    games = (
        Game.query.filter(
            Game.court_id == court.id,
            Game.status == 'completed',
            Game.score_team1.isnot(None),
            Game.score_team2.isnot(None),
        )
        .order_by(Game.completed_at.desc())
        .limit(300)
        .all()
    )
    hidden_ids = set(hidden_ids or ())
    tally = {}
    for game in games:
        team1_won = game.score_team1 > game.score_team2
        for p in game.players:
            if not p.team or p.user_id in hidden_ids:
                continue
            rec = tally.setdefault(p.user_id, {'wins': 0, 'losses': 0})
            rec['wins' if (p.team == 1) == team1_won else 'losses'] += 1
    if not tally:
        return []
    users = {u.id: u for u in UserModel.query.filter(
        UserModel.id.in_(tally), UserModel.deleted_at.is_(None),
    ).all()}
    ranked = sorted(
        ((uid, r) for uid, r in tally.items() if uid in users and r['wins'] > 0),
        key=lambda kv: (-kv[1]['wins'], -(kv[1]['wins'] / (kv[1]['wins'] + kv[1]['losses']))),
    )[:5]
    return [
        {**users[uid].to_public_dict(), 'wins': r['wins'], 'losses': r['losses']}
        for uid, r in ranked
    ]


def _busy_times(court):
    """Popular visit blocks from the last 90 days of check-ins: top 3 blocks
    of (weekday, part-of-day) with 2+ visits. Local time is approximated from
    the court's longitude (±1h near DST/zone edges — fine at this granularity)."""
    if court.longitude is None:
        return []
    rows = CheckIn.query.filter(
        CheckIn.court_id == court.id,
        CheckIn.checked_in_at >= utcnow() - timedelta(days=90),
    ).all()
    tz_offset = round(court.longitude / 15)
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    buckets = {}
    for checkin in rows:
        local = checkin.checked_in_at + timedelta(hours=tz_offset)
        hour = local.hour
        if 5 <= hour < 12:
            part = 'mornings'
        elif 12 <= hour < 17:
            part = 'afternoons'
        elif 17 <= hour < 23:
            part = 'evenings'
        else:
            continue
        key = (local.weekday(), part)
        buckets[key] = buckets.get(key, 0) + 1
    ranked = sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {'label': f'{days[weekday]} {part}', 'count': count}
        for (weekday, part), count in ranked[:3]
        if count >= 2
    ]


def _notify_friends_looking(court):
    """Tell friends someone wants a game at a court — at most once per 3h per friend."""
    cutoff = utcnow() - timedelta(hours=3)
    for fid in friend_ids(g.current_user.id):
        already_pinged = Notification.query.filter(
            Notification.user_id == fid,
            Notification.kind == 'friend_checkin',
            Notification.related_user_id == g.current_user.id,
            Notification.created_at >= cutoff,
        ).first()
        if already_pinged:
            continue
        notify(
            fid,
            'friend_checkin',
            f'{g.current_user.display_name} is at {court.name} looking for a game',
            related_user_id=g.current_user.id,
        )


@courts_bp.post('/courts/<int:court_id>/checkin')
@rate_limit(40, 60)
@login_required
def check_in(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    looking = bool(payload.get('looking_for_game'))

    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    now = utcnow()
    instant_games = _lock_open_instant_games_for_user(g.current_user.id)
    existing = active_checkin_for(g.current_user.id, for_update=True)
    existing_was_fresh = checkin_is_fresh(existing, now)
    # Only a fresh "wants a game" (not a re-ping of an existing one) pings friends.
    started_looking = looking and not (
        existing_was_fresh
        and existing.court_id == court.id
        and existing.looking_for_game
    )
    if existing and existing.court_id == court.id:
        if not existing_was_fresh:
            _close_departed_instant_assemblies(
                instant_games, g.current_user.id, existing.court_id, now,
            )
            existing.checked_in_at = now
        existing.looking_for_game = looking
        existing.last_presence_ping_at = now
    else:
        if existing:
            _close_departed_instant_assemblies(
                instant_games, g.current_user.id, existing.court_id, now,
            )
            existing.checked_out_at = now
            # The partial unique index is immediate. Flush the retirement
            # before inserting the replacement presence row.
            db.session.flush()
        db.session.add(CheckIn(
            user_id=g.current_user.id,
            court_id=court.id,
            looking_for_game=looking,
            checked_in_at=now,
            last_presence_ping_at=now,
        ))
    # Physical presence supersedes the separate remote "available this hour"
    # signal. User -> Game -> CheckIn -> pulse is the shared lock order.
    from backend.routes.games import _end_active_play_pulse_for_user
    _end_active_play_pulse_for_user(g.current_user.id, 'checked_in', now)
    if started_looking:
        _notify_friends_looking(court)

    # Remember where the player is for "players near you" discovery.
    if court.latitude is not None and court.longitude is not None:
        g.current_user.last_lat = court.latitude
        g.current_user.last_lng = court.longitude
        g.current_user.last_location_at = now

    db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})


@courts_bp.post('/checkout')
@rate_limit(60, 60)
@login_required
def check_out():
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    instant_games = _lock_open_instant_games_for_user(g.current_user.id)
    checkin = active_checkin_for(g.current_user.id, for_update=True)
    if checkin:
        now = utcnow()
        _close_departed_instant_assemblies(
            instant_games, g.current_user.id, checkin.court_id, now,
        )
        checkin.checked_out_at = now
        checkin.looking_for_game = False
        db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})


@courts_bp.post('/presence/ping')
@rate_limit(60, 60)
@login_required
def presence_ping():
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    instant_games = _lock_open_instant_games_for_user(g.current_user.id)
    checkin = active_checkin_for(g.current_user.id, for_update=True)
    if checkin:
        now = utcnow()
        was_fresh = checkin_is_fresh(checkin, now)
        if (
            not checkin.checked_in_at
            or checkin.checked_in_at < presence_absolute_cutoff(now)
        ):
            # A heartbeat can maintain a short live session, never an
            # indefinite exact-location/LFG signal. Explicit check-in starts a
            # new confirmed session after this hard cap.
            _close_departed_instant_assemblies(
                instant_games, g.current_user.id, checkin.court_id, now,
            )
            checkin.checked_out_at = now
            checkin.looking_for_game = False
        else:
            if not was_fresh:
                _close_departed_instant_assemblies(
                    instant_games, g.current_user.id,
                    checkin.court_id, now,
                )
            checkin.last_presence_ping_at = now
        db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})
