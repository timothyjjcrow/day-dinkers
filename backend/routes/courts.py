"""Court discovery, detail, and check-in routes."""
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func

from backend.app import db
from backend.models import CheckIn, Court, CourtReview, FavoriteCourt, Game, GamePlayer, utcnow
from backend.routes.auth import active_checkin_for, login_required, optional_current_user, presence_payload
from backend.routes.social import friend_ids
from backend.security import rate_limit

courts_bp = Blueprint('courts', __name__)

MAX_COURT_RESULTS = 300

# --- Geocoding (OpenStreetMap Nominatim proxy) ---
_GEOCODE_CACHE = {}
_GEOCODE_CACHE_TTL = 60 * 60 * 24  # 24h — place coordinates don't move
_GEOCODE_MAX_CACHE = 500


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
    cutoff = utcnow() - timedelta(
        seconds=int(current_app.config.get('PRESENCE_STALE_AFTER_SECONDS', 7200) or 7200),
    )
    stale = CheckIn.query.filter(
        CheckIn.checked_out_at.is_(None),
        CheckIn.last_presence_ping_at < cutoff,
    ).all()
    for checkin in stale:
        checkin.checked_out_at = cutoff
    if stale:
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


def _active_counts_for(court_ids):
    if not court_ids:
        return {}, {}
    rows = (
        db.session.query(CheckIn.court_id, func.count(CheckIn.id))
        .filter(CheckIn.court_id.in_(court_ids), CheckIn.checked_out_at.is_(None))
        .group_by(CheckIn.court_id)
        .all()
    )
    players = {court_id: count for court_id, count in rows}
    game_rows = (
        db.session.query(Game.court_id, func.count(Game.id))
        .filter(
            Game.court_id.in_(court_ids),
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow(),
        )
        .group_by(Game.court_id)
        .all()
    )
    games = {court_id: count for court_id, count in game_rows}
    return players, games


@courts_bp.get('/courts')
def list_courts():
    """Court search: by map bounds (west,south,east,north) or lat/lng radius, plus text query."""
    cleanup_stale_presence()
    query = Court.query.filter(Court.latitude.isnot(None), Court.longitude.isnot(None))

    text = str(request.args.get('q') or '').strip()
    if text:
        like = f'%{text}%'
        query = query.filter(
            Court.name.ilike(like) | Court.city.ilike(like) | Court.address.ilike(like)
        )

    if str(request.args.get('lighted') or '') in {'1', 'true'}:
        query = query.filter(Court.lighted.is_(True))
    if str(request.args.get('indoor') or '') in {'1', 'true'}:
        query = query.filter(Court.indoor.is_(True))

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
    items = items[:limit]

    ids = [c['id'] for c in items]
    players, games = _active_counts_for(ids)
    ratings = _rating_summary_for(ids)
    for item in items:
        item['players_here'] = players.get(item['id'], 0)
        item['upcoming_games'] = games.get(item['id'], 0)
        summary = ratings.get(item['id'])
        item['rating_avg'] = summary['rating_avg'] if summary else None
        item['rating_count'] = summary['rating_count'] if summary else 0

    return jsonify({'items': items, 'count': len(items)})


@courts_bp.get('/courts/<int:court_id>')
def court_detail(court_id):
    cleanup_stale_presence()
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    current_user = optional_current_user()
    viewer_friends = friend_ids(current_user.id) if current_user else set()
    active = (
        CheckIn.query.filter_by(court_id=court.id, checked_out_at=None)
        .order_by(CheckIn.checked_in_at.asc())
        .all()
    )
    now = utcnow()
    players_here = []
    for checkin in active:
        if not checkin.user:
            continue
        entry = checkin.user.to_public_dict()
        entry['looking_for_game'] = bool(checkin.looking_for_game)
        entry['checked_in_at'] = checkin.checked_in_at.isoformat() + 'Z' if checkin.checked_in_at else None
        entry['minutes_here'] = (
            max(0, int((now - checkin.checked_in_at).total_seconds() // 60))
            if checkin.checked_in_at else 0
        )
        entry['is_friend'] = checkin.user_id in viewer_friends
        entry['is_me'] = bool(current_user and checkin.user_id == current_user.id)
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
    payload['players_here'] = players_here
    payload['friends_here'] = sum(1 for p in players_here if p['is_friend'])
    viewer_id = current_user.id if current_user else None
    payload['games'] = [game.to_dict(viewer_id) for game in upcoming]
    payload['recent_results'] = [game.to_dict(viewer_id) for game in recent_completed]
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
    payload['reviews'] = [r.to_dict() for r in recent_reviews]
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
    summary = _rating_summary_for([court.id]).get(court.id)
    return jsonify({
        'items': [r.to_dict() for r in reviews],
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


@courts_bp.post('/courts/<int:court_id>/favorite')
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
    cleanup_stale_presence()
    favorites = (
        FavoriteCourt.query.filter_by(user_id=g.current_user.id)
        .order_by(FavoriteCourt.id.desc())
        .all()
    )
    items = [f.court.to_summary_dict() for f in favorites if f.court]
    players, games = _active_counts_for([c['id'] for c in items])
    for item in items:
        item['players_here'] = players.get(item['id'], 0)
        item['upcoming_games'] = games.get(item['id'], 0)
    return jsonify({'items': items})


@courts_bp.post('/courts/<int:court_id>/checkin')
@rate_limit(40, 60)
@login_required
def check_in(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    payload = request.get_json(silent=True) or {}
    looking = bool(payload.get('looking_for_game'))

    existing = active_checkin_for(g.current_user.id)
    if existing and existing.court_id == court.id:
        existing.looking_for_game = looking
        existing.last_presence_ping_at = utcnow()
    else:
        if existing:
            existing.checked_out_at = utcnow()
        db.session.add(CheckIn(
            user_id=g.current_user.id,
            court_id=court.id,
            looking_for_game=looking,
        ))

    # Remember where the player is for "players near you" discovery.
    if court.latitude is not None and court.longitude is not None:
        g.current_user.last_lat = court.latitude
        g.current_user.last_lng = court.longitude
        g.current_user.last_location_at = utcnow()

    db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})


@courts_bp.post('/checkout')
@login_required
def check_out():
    checkin = active_checkin_for(g.current_user.id)
    if checkin:
        checkin.checked_out_at = utcnow()
        db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})


@courts_bp.post('/presence/ping')
@login_required
def presence_ping():
    checkin = active_checkin_for(g.current_user.id)
    if checkin:
        checkin.last_presence_ping_at = utcnow()
        db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})
