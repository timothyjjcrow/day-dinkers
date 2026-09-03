"""Court discovery, detail, and check-in routes."""
import base64
from difflib import SequenceMatcher
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import UTC, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, current_app, g, jsonify, request
from sqlalchemy import and_, false, func, or_
from sqlalchemy.orm import joinedload

from backend.app import db
from backend.models import (
    COURT_CONDITIONS, BusinessOffering, BusinessProfile, BusinessScheduleItem,
    CheckIn, Court,
    CourtChatSubscription, CourtCondition, CourtEditSuggestion,
    CourtPhoto, CourtReview, FavoriteCourt, Friendship, Game, GamePlayer,
    Notification, User, blocked_pair_ids, can_direct_message, iso, notify, utcnow,
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
from backend.routes.social import friend_ids, nearby_visibility_allows
from backend.security import rate_limit
from backend.services.business_governance import business_access_role
from backend.services.business_visibility import public_business_query
from backend.services.presence_proof import (
    issue_instant_rally_presence_proof,
    validate_court_presence_location,
)
from backend.integrations.models import (
    BusinessProviderConnection,
    BusinessScheduleOccurrence,
)
from backend.integrations.services import publication_ready_connection_ids

courts_bp = Blueprint('courts', __name__)

MAX_COURT_RESULTS = 300

# --- Geocoding (OpenStreetMap Nominatim proxy) ---
_GEOCODE_CACHE = {}
_GEOCODE_CACHE_TTL = 60 * 60 * 24  # 24h — place coordinates don't move
_GEOCODE_MAX_CACHE = 500


def _court_page_cursor(offset):
    raw = json.dumps({'v': 1, 'o': int(offset)}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _court_page_args():
    try:
        limit = int(request.args.get('limit') or 100)
    except (TypeError, ValueError):
        return None, None, 'invalid_limit'
    if not 1 <= limit <= MAX_COURT_RESULTS:
        return None, None, 'invalid_limit'
    raw_cursor = str(request.args.get('cursor') or '').strip()
    if not raw_cursor:
        return limit, 0, None
    try:
        padded = raw_cursor + '=' * (-len(raw_cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
        if value.get('v') != 1:
            raise ValueError
        offset = int(value['o'])
        if offset < 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, None, 'invalid_cursor'
    return limit, offset, None


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


def _nominatim_fetch(query, viewbox=None):
    """Fetch geocoding results from Nominatim. Isolated so tests can mock it."""
    query_params = {
        'q': query,
        'format': 'jsonv2',
        'addressdetails': 1,
        'limit': 5,
        'countrycodes': 'us',
    }
    if viewbox:
        # Bias results toward the map the player is already exploring without
        # excluding a deliberate nationwide search.
        query_params.update({'viewbox': viewbox, 'bounded': 0})
    params = urllib.parse.urlencode(query_params)
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


def _geocode_viewbox(raw_value):
    """Validate Nominatim's left,top,right,bottom viewbox format."""
    value = str(raw_value or '').strip()
    if not value:
        return None, None
    try:
        west, north, east, south = [float(part) for part in value.split(',')]
    except (TypeError, ValueError):
        return None, 'invalid_viewbox'
    if not (
        -180 <= west < east <= 180
        and -90 <= south < north <= 90
    ):
        return None, 'invalid_viewbox'
    # Fixed precision keeps cache keys bounded while retaining a genuinely
    # local bias at normal city/court zoom levels.
    return ','.join(f'{value:.4f}' for value in (west, north, east, south)), None


@courts_bp.get('/geocode')
def geocode():
    """Search for a place by name and return coordinates to recenter the map."""
    query = str(request.args.get('q') or '').strip()
    if len(query) < 3:
        return jsonify({'items': []})

    viewbox, viewbox_error = _geocode_viewbox(request.args.get('viewbox'))
    if viewbox_error:
        return jsonify({'error': viewbox_error}), 400

    key = f'{query.casefold()}|{viewbox or "global"}'
    cached = _GEOCODE_CACHE.get(key)
    if cached and cached['expires_at'] > time.time():
        return jsonify({'items': cached['items']})

    try:
        raw_results = (
            _nominatim_fetch(query, viewbox=viewbox)
            if viewbox else _nominatim_fetch(query)
        )
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


def _normalize_court_search_text(value):
    """Normalize a player-entered court query for deterministic ranking."""
    return ' '.join(re.findall(r'[\w]+', str(value or '').casefold()))


def _court_search_relevance(court, raw_query, *, allow_fuzzy=False):
    """Return a sortable relevance tuple, or ``None`` when there is no match.

    Exact and prefix matches deliberately outrank substrings. A typo fallback
    is only enabled after the normal substring query is empty, and its high
    threshold avoids turning a vague query into unrelated court suggestions.
    """
    query = _normalize_court_search_text(raw_query)
    if not query:
        return None
    name = _normalize_court_search_text(court.name)
    city = _normalize_court_search_text(court.city)
    address = _normalize_court_search_text(court.address)
    if name == query:
        return 0, 0.0
    if name.startswith(query):
        return 1, 0.0
    if city.startswith(query):
        return 2, 0.0
    if address.startswith(query):
        return 3, 0.0
    if query in name:
        return 4, 0.0
    if query in city:
        return 5, 0.0
    if query in address:
        return 6, 0.0
    if not allow_fuzzy or len(query) < 4:
        return None

    comparison_values = [name, city]
    comparison_values.extend(name.split())
    comparison_values.extend(city.split())
    best_ratio = max(
        (SequenceMatcher(None, query, candidate).ratio()
         for candidate in comparison_values if candidate),
        default=0.0,
    )
    fuzzy_threshold = 0.9 if len(query) == 4 else 0.84
    if best_ratio < fuzzy_threshold:
        return None
    return 7, -best_ratio


def _court_discovery_summary(court):
    """Compact decision facts shared by map search and saved-court lists."""
    item = court.to_summary_dict()
    item.update({
        'surface_type': court.surface_type,
        'court_type': court.court_type,
        'open_play_schedule': court.open_play_schedule,
        'open_play_schedule_rows': court.open_play_schedule_rows_list(),
        'fees': court.fees,
        'hours': court.hours,
    })
    return item


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
    """Return fresh players plus upcoming and near-term visible open games.

    Court summaries used to count every future game, including full and
    invite-only games the viewer could not see. That made the map promise an
    "open game" that disappeared on tap. Keep this aggregate aligned with the
    same discovery/privacy rules as the games feed.
    """
    if not court_ids:
        return {}, {}, {}
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
    active_games = {}
    active_window_end = now + timedelta(hours=2)
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
        if game.scheduled_at <= active_window_end:
            active_games[game.court_id] = active_games.get(game.court_id, 0) + 1
    return players, games, active_games


@courts_bp.get('/courts')
def list_courts():
    """Court search: by map bounds (west,south,east,north) or lat/lng radius, plus text query."""
    current_user = optional_current_user()
    query = Court.query.filter(
        Court.latitude.isnot(None),
        Court.longitude.isnot(None),
        Court.closed.is_(False),
    )

    text = str(request.args.get('q') or '').strip()

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
    elif lat is not None and lng is not None and not text:
        # A typed court/city query is an explicit nationwide search. Keep the
        # caller's location for distance ranking below, but do not silently
        # discard an exact venue just because the player is more than the
        # default nearby radius away (important for owners managing locations
        # remotely and players planning travel).
        radius = min(max(request.args.get('radius', default=25.0, type=float), 1.0), 100.0)
        lat_delta = radius / 69.0
        lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
        query = query.filter(
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )

    if str(request.args.get('open_now') or '') in truthy:
        # Weekly hours may use each venue's own IANA timezone and may cross
        # midnight, which cannot be expressed portably across SQLite and
        # Postgres. Resolve the already area-bounded candidate set with the
        # canonical Court.hours_status implementation before pagination.
        open_ids = [
            court.id for court in query.order_by(None).all()
            if court.hours_status().get('is_open') is True
        ]
        query = query.filter(Court.id.in_(open_ids)) if open_ids \
            else query.filter(false())

    ranked_text_ids = None
    if text:
        # Escape SQL wildcard characters: `%` and `_` are ordinary search
        # input here, not a way to request the entire court directory.
        escaped_text = (
            text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        )
        like = f'%{escaped_text}%'
        text_matches = query.filter(or_(
            Court.name.ilike(like, escape='\\'),
            Court.city.ilike(like, escape='\\'),
            Court.address.ilike(like, escape='\\'),
        )).with_entities(
            Court.id, Court.name, Court.city, Court.address,
            Court.latitude, Court.longitude, Court.num_courts,
        ).order_by(None).all()
        allow_fuzzy = not text_matches
        candidate_rows = text_matches
        if allow_fuzzy:
            # The fuzzy pass is intentionally exceptional (only after zero
            # literal matches) and reads lightweight columns rather than full
            # model rows. This keeps a one-character typo useful without
            # weakening the normal precise search.
            candidate_rows = query.with_entities(
                Court.id, Court.name, Court.city, Court.address,
                Court.latitude, Court.longitude, Court.num_courts,
            ).order_by(None).all()

        ranked_rows = []
        for row in candidate_rows:
            relevance = _court_search_relevance(
                row, text, allow_fuzzy=allow_fuzzy,
            )
            if relevance is None:
                continue
            distance = (
                haversine_miles(lat, lng, row.latitude, row.longitude)
                if lat is not None and lng is not None else float('inf')
            )
            ranked_rows.append((
                (*relevance, distance, -int(row.num_courts or 0), row.id),
                row.id,
            ))
        ranked_rows.sort(key=lambda item: item[0])
        ranked_text_ids = [court_id for _, court_id in ranked_rows]

    limit, offset, page_error = _court_page_args()
    if page_error:
        return jsonify({'error': page_error}), 400
    sort = str(request.args.get('sort') or 'distance').strip().lower()
    total = (
        len(ranked_text_ids) if ranked_text_ids is not None
        else query.order_by(None).count()
    )
    courts = []
    if ranked_text_ids is not None:
        page_ids = ranked_text_ids[offset:offset + limit]
        if page_ids:
            selected = {
                court.id: court
                for court in Court.query.filter(Court.id.in_(page_ids)).all()
            }
            courts = [selected[court_id] for court_id in page_ids]
    elif sort == 'rating':
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
        courts = query.offset(offset).limit(limit).all()
    elif sort == 'distance' and lat is not None and lng is not None:
        # Never let an arbitrary popularity pre-order decide which courts are
        # allowed into distance sorting. Fetch only the lightweight location
        # columns, rank the complete filtered candidate set, then hydrate the
        # winning rows. This also works consistently in SQLite and Postgres
        # without relying on optional trigonometric SQL extensions.
        locations = query.with_entities(
            Court.id, Court.latitude, Court.longitude,
        ).order_by(None).all()
        locations.sort(key=lambda row: haversine_miles(
            lat, lng, row.latitude, row.longitude,
        ))
        selected_ids = [row.id for row in locations[offset:offset + limit]]
        if selected_ids:
            selected = {
                court.id: court
                for court in Court.query.filter(Court.id.in_(selected_ids)).all()
            }
            courts = [selected[court_id] for court_id in selected_ids]
    elif sort == 'active':
        # Activity must choose from the complete filtered pool. Taking a
        # popularity page first can otherwise hide the one genuinely busy
        # small venue the player is trying to find.
        locations = query.with_entities(
            Court.id, Court.latitude, Court.longitude, Court.num_courts,
        ).order_by(None).all()
        pool_players, _, pool_active_games = _active_counts_for(
            [row.id for row in locations], current_user,
        )
        locations.sort(key=lambda row: (
            -(pool_players.get(row.id, 0) + pool_active_games.get(row.id, 0)),
            haversine_miles(lat, lng, row.latitude, row.longitude)
            if lat is not None and lng is not None else float('inf'),
            -int(row.num_courts or 0),
            row.id,
        ))
        selected_ids = [row.id for row in locations[offset:offset + limit]]
        if selected_ids:
            selected = {
                court.id: court
                for court in Court.query.filter(Court.id.in_(selected_ids)).all()
            }
            courts = [selected[court_id] for court_id in selected_ids]
    else:
        query = query.order_by(Court.num_courts.desc(), Court.id.asc())
        # Activity is computed below in Python. Cursor pages stay disjoint and
        # deterministic even when this optional presentation sort is selected.
        courts = query.offset(offset).limit(limit).all()

    items = []
    for court in courts:
        item = _court_discovery_summary(court)
        if lat is not None and lng is not None:
            item['distance_miles'] = round(
                haversine_miles(lat, lng, court.latitude, court.longitude), 1,
            )
        items.append(item)
    items = items[:limit]

    _enrich_court_summaries(items, current_user)

    return jsonify({
        'items': items,
        'count': len(items),
        'total': total,
        'has_more': offset + len(items) < total,
        'next_cursor': (
            _court_page_cursor(offset + len(items))
            if offset + len(items) < total else None
        ),
        # Compatibility for older map clients; this now describes the entire
        # cursor sequence, not just an unexplained hard cut.
        'truncated': offset + len(items) < total,
    })


def _public_business_summaries(court_ids):
    """Batch compact venue signals for court discovery without claim metadata."""
    if not court_ids:
        return {}
    rows = public_business_query().filter(
        BusinessProfile.court_id.in_(sorted(set(court_ids))),
    ).all()
    profile_ids = [profile.id for profile in rows]
    now = utcnow()
    # IANA offsets span both sides of UTC midnight. Keep a one-day coarse DB
    # window, then apply the exact per-row timezone rule in Python.
    coarse_date = (now - timedelta(days=1)).date()
    offering_rows = db.session.query(
        BusinessOffering.business_id, BusinessOffering.booking_url,
    ).filter(
        BusinessOffering.business_id.in_(profile_ids),
        BusinessOffering.active.is_(True),
    ).all() if rows else []
    schedule_rows = BusinessScheduleItem.query.filter(
        BusinessScheduleItem.business_id.in_(profile_ids),
        BusinessScheduleItem.active.is_(True),
        BusinessScheduleItem.status.notin_(('cancelled', 'completed')),
        or_(
            BusinessScheduleItem.recurrence.notin_(('dated', 'date_range')),
            and_(
                BusinessScheduleItem.recurrence == 'dated',
                BusinessScheduleItem.event_date >= coarse_date,
            ),
            and_(
                BusinessScheduleItem.recurrence == 'date_range',
                BusinessScheduleItem.end_date >= coarse_date,
            ),
        ),
    ).all() if rows else []
    schedule_rows = [item for item in schedule_rows if item.is_current(now)]
    offering_ids = {business_id for business_id, _ in offering_rows}
    offering_booking_ids = {
        business_id for business_id, booking_url in offering_rows if booking_url
    }
    schedule_ids = {item.business_id for item in schedule_rows}
    schedule_booking_ids = {
        item.business_id for item in schedule_rows
        if item.booking_url
        and item.status == 'scheduled'
        and (item.spots_remaining is None or item.spots_remaining > 0)
    }
    integrated_rows = BusinessScheduleOccurrence.query.filter(
        BusinessScheduleOccurrence.business_id.in_(profile_ids),
        BusinessScheduleOccurrence.status.notin_(('cancelled', 'completed')),
        or_(
            BusinessScheduleOccurrence.event_date >= coarse_date,
            and_(
                BusinessScheduleOccurrence.starts_at.is_not(None),
                or_(
                    BusinessScheduleOccurrence.ends_at > now,
                    and_(
                        BusinessScheduleOccurrence.ends_at.is_(None),
                        BusinessScheduleOccurrence.starts_at >= now,
                    ),
                ),
            ),
            and_(
                BusinessScheduleOccurrence.recurrence != '',
                BusinessScheduleOccurrence.start_date.is_not(None),
                or_(
                    BusinessScheduleOccurrence.end_date.is_(None),
                    BusinessScheduleOccurrence.end_date >= coarse_date,
                ),
            ),
        ),
    ).all() if rows else []
    integrated_rows = [item for item in integrated_rows if item.is_current(now)]
    integrated_connection_ids = {
        item.connection_id for item in integrated_rows
    }
    integrated_connections = BusinessProviderConnection.query.options(
        joinedload(BusinessProviderConnection.business).joinedload(
            BusinessProfile.court,
        ),
    ).filter(
        BusinessProviderConnection.id.in_(integrated_connection_ids),
    ).all() if integrated_connection_ids else []
    ready_connection_ids = publication_ready_connection_ids(
        integrated_connections,
    )
    integrated_rows = [
        item for item in integrated_rows if item.connection_id in ready_connection_ids
    ]
    integrated_schedule_ids = {item.business_id for item in integrated_rows}
    integrated_booking_ids = {
        item.business_id for item in integrated_rows
        if item.booking_url
        and item.status == 'scheduled'
        and (item.spots_remaining is None or item.spots_remaining > 0)
    }
    return {
        profile.court_id: {
            'id': profile.id,
            'name': profile.name,
            'logo_url': profile.logo_url,
            'verified': True,
            'booking_available': bool(
                profile.booking_url
                or profile.id in offering_booking_ids
                or profile.id in schedule_booking_ids
                or profile.id in integrated_booking_ids
            ),
            'membership_available': bool(profile.membership_url),
            'schedule_available': bool(
                profile.id in schedule_ids
                or profile.id in integrated_schedule_ids
            ),
            'programs_available': bool(
                profile.id in offering_ids
                or profile.id in schedule_ids
                or profile.id in integrated_schedule_ids
            ),
        }
        for profile in rows
    }


def _public_business_detail(court_id, current_user=None):
    """Published operator content for a court sheet, with private workflow fields removed."""
    profile = public_business_query().filter(
        BusinessProfile.court_id == court_id,
    ).first()
    if profile is None:
        return None
    data = profile.to_public_dict()
    data['verified'] = True
    manager_role = business_access_role(profile, current_user.id) if current_user else None
    data['is_owner'] = manager_role == 'owner'
    data['is_manager'] = bool(manager_role)
    if manager_role:
        data['manager_role'] = manager_role
    return data


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
        address=str(payload.get('address') or '').strip()[:255],
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


def _anonymous_court_game_payload(game):
    """Public court availability without exposing a player or game owner."""
    return {
        'id': game.id,
        'scheduled_at': iso(game.scheduled_at),
        'game_type': game.game_type,
        'max_players': game.max_players,
        'spots_left': max(0, game.max_players - len(game.players)),
        'status': game.status,
        'players': [],
    }


def _anonymous_court_tournament_payload(tournament):
    """Public event facts; organizer, entrants, club, and champion stay private."""
    return {
        'id': tournament.id,
        'name': tournament.name,
        'description': tournament.description,
        'format': tournament.format,
        'event_type': tournament.event_type,
        'status': tournament.status,
        'ranked': bool(tournament.ranked),
        'starts_at': iso(tournament.starts_at),
        'max_entries': tournament.max_entries,
        'entry_count': len(tournament.entries),
    }


def _anonymous_court_review_payload(review):
    """Keep public venue feedback useful without publishing its author."""
    return {
        'id': review.id,
        'user_id': None,
        'user_name': 'Player',
        'avatar_color': '#2f9e44',
        'avatar_url': '',
        'rating': review.rating,
        'comment': review.comment,
        'created_at': iso(review.created_at),
        'updated_at': iso(review.updated_at),
    }


@courts_bp.get('/courts/<int:court_id>')
def court_detail(court_id):
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
    viewer_checkin = next(
        (
            checkin for checkin in active
            if current_user and checkin.user_id == current_user.id
        ),
        None,
    )
    now = utcnow()
    connection_states = {}
    if current_user:
        active_user_ids = sorted({
            checkin.user_id for checkin in active
            if checkin.user_id != current_user.id
        })
        if active_user_ids:
            relationships = Friendship.query.filter(or_(
                and_(
                    Friendship.requester_id == current_user.id,
                    Friendship.addressee_id.in_(active_user_ids),
                ),
                and_(
                    Friendship.addressee_id == current_user.id,
                    Friendship.requester_id.in_(active_user_ids),
                ),
            )).all()
            for relationship in relationships:
                other_id = (
                    relationship.addressee_id
                    if relationship.requester_id == current_user.id
                    else relationship.requester_id
                )
                if relationship.status == 'accepted':
                    state = 'friend'
                elif relationship.requester_id == current_user.id:
                    state = 'outgoing'
                else:
                    state = 'incoming'
                connection_states[other_id] = {
                    'state': state,
                    'friendship_id': relationship.id,
                }
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
        nearby_visibility = checkin.user.nearby_visibility \
            if checkin.user.nearby_visibility in {'everyone', 'friends', 'hidden'} \
            else 'everyone'
        can_discover_identity = (
            is_me
            or nearby_visibility != 'hidden' and is_friend
            or nearby_visibility == 'everyone' and checkin.looking_for_game
        )
        if not can_discover_identity:
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
        entry['can_message'] = (not is_me) and can_direct_message(
            current_user.id, checkin.user_id,
        )
        connection = connection_states.get(checkin.user_id, {})
        entry['friendship_state'] = 'self' if is_me else connection.get('state', 'none')
        entry['friendship_id'] = connection.get('friendship_id')
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
        Game.query.filter(
            Game.court_id == court.id,
            Game.status == 'completed',
            Game.score_team1.isnot(None),
            Game.score_team2.isnot(None),
        )
        .order_by(Game.completed_at.desc())
        .limit(3)
        .all()
    )

    payload = court.to_dict()
    payload['business'] = _public_business_detail(court.id, current_user)
    payload['photo_count'] = CourtPhoto.query.filter_by(court_id=court.id).count()
    payload['latest_condition'] = _latest_condition_for(
        court.id, include_identity=current_user is not None,
    )

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

    payload['regulars'] = []
    if current_user:
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
            for user, visits in regular_rows
            if user.id not in hidden_ids and nearby_visibility_allows(
                user, current_user.id, viewer_friends,
            )
        ]
    payload['busy_times'] = _busy_times(court)
    payload['court_leaders'] = (
        _court_leaders(court, hidden_ids) if current_user else []
    )
    # Court-chat preview and unread state share the exact ACL used by Inbox.
    payload['chat_unread'] = 0
    payload['chat_last_message'] = None
    payload['chat_subscription'] = {
        'joined': False, 'muted': False,
        'joined_at': None, 'muted_at': None,
    }
    if current_user:
        subscription = CourtChatSubscription.query.filter_by(
            user_id=current_user.id, court_id=court.id,
        ).first()
        from backend.routes.chat import court_room_summaries
        summary = court_room_summaries(
            current_user.id, {court.id},
            {court.id} if subscription and subscription.muted_at else set(),
        )[court.id]
        payload['chat_last_message'] = summary['last_message']
        if subscription:
            payload['chat_subscription'] = subscription.to_dict()
            payload['chat_unread'] = summary['unread']
    payload['players_here'] = players_here
    payload['players_here_count'] = visible_player_count
    # Aggregate freshness is safe for anonymous venue discovery and lets
    # clients qualify a live count (for example, "last confirmed 8m ago")
    # without exposing any additional player identity.
    last_confirmed_at = max(
        (
            checkin.last_presence_ping_at for checkin in active
            if checkin.user and not checkin.user.deleted_at
            and checkin.user_id not in hidden_ids
            and checkin.last_presence_ping_at
        ),
        default=None,
    )
    payload['players_here_last_confirmed_at'] = iso(last_confirmed_at)
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
    payload['tournaments'] = [
        t.to_dict(viewer_id) if current_user else _anonymous_court_tournament_payload(t)
        for t in court_tournaments
    ]
    # Hall of fame: recent tournament champions crowned at this court.
    payload['past_champions'] = []
    if current_user:
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
    game_payload = (
        (lambda game: _discovery_game_payload(game, current_user, viewer_friends))
        if current_user else _anonymous_court_game_payload
    )
    payload['games'] = [game_payload(game) for game in visible_upcoming]
    # "Now at this court" is an immediate assembly signal, not a count of
    # every plan on the calendar. Keep later games in `games` below while
    # giving the client a bounded, authoritative set for the Now card.
    now_window_end = now + timedelta(hours=2)
    payload['now_games'] = [
        game_payload(game)
        for game in visible_upcoming if game.scheduled_at <= now_window_end
    ]
    payload['recent_results'] = (
        [
            _discovery_game_payload(game, current_user, viewer_friends)
            for game in recent_completed if game_visible(game)
        ]
        if current_user else []
    )
    # These flags come from the same fresh, court-scoped presence row used to
    # render players_here. They therefore cannot drift from the authoritative
    # check-in state when a player toggles discovery without checking out.
    payload['is_checked_in'] = viewer_checkin is not None
    payload['is_looking_for_game'] = bool(
        viewer_checkin and viewer_checkin.looking_for_game
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
        (review.to_dict() if current_user else _anonymous_court_review_payload(review))
        for review in recent_reviews if review.user_id not in hidden_ids
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
    current_user = optional_current_user()
    hidden_ids = blocked_pair_ids(current_user.id) if current_user else set()
    limit = max(5, min(request.args.get('limit', 10, type=int) or 10, 25))
    before_id = request.args.get('before_id', type=int)
    query = CourtReview.query.filter_by(court_id=court.id)
    if hidden_ids:
        query = query.filter(~CourtReview.user_id.in_(hidden_ids))
    if before_id:
        query = query.filter(CourtReview.id < before_id)
    rows = query.order_by(CourtReview.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    reviews = rows[:limit]
    summary = _rating_summary_for([court.id]).get(court.id)
    return jsonify({
        'items': [
            (r.to_dict() if current_user else _anonymous_court_review_payload(r))
            for r in reviews
        ],
        'rating_avg': summary['rating_avg'] if summary else None,
        'rating_count': summary['rating_count'] if summary else 0,
        'has_more': has_more,
        'next_before_id': reviews[-1].id if has_more and reviews else None,
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


@courts_bp.delete('/courts/<int:court_id>/reviews/<int:review_id>')
@rate_limit(30, 3600)
@login_required
def delete_review(court_id, review_id):
    """Delete only the signed-in player's own review."""
    review = db.session.get(CourtReview, review_id)
    if not review or review.court_id != court_id:
        return jsonify({'error': 'review_not_found'}), 404
    if review.user_id != g.current_user.id:
        return jsonify({'error': 'review_not_owned'}), 403
    db.session.delete(review)
    db.session.commit()
    summary = _rating_summary_for([court_id]).get(court_id)
    return jsonify({
        'deleted': True,
        'rating_avg': summary['rating_avg'] if summary else None,
        'rating_count': summary['rating_count'] if summary else 0,
    })


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


_OPEN_PLAY_WEEKDAYS = {
    'mon': 'mon', 'monday': 'mon',
    'tue': 'tue', 'tues': 'tue', 'tuesday': 'tue',
    'wed': 'wed', 'wednesday': 'wed',
    'thu': 'thu', 'thur': 'thu', 'thurs': 'thu', 'thursday': 'thu',
    'fri': 'fri', 'friday': 'fri',
    'sat': 'sat', 'saturday': 'sat',
    'sun': 'sun', 'sunday': 'sun',
}
_OPEN_PLAY_TIME_RE = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')


def _norm_open_play_rows(value):
    """Return bounded, time-specific recurring windows for a court."""
    if not isinstance(value, list) or len(value) > 21:
        raise ValueError
    rows = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError
        weekday = _OPEN_PLAY_WEEKDAYS.get(
            str(raw.get('weekday') or '').strip().lower(),
        )
        start = str(raw.get('start') or '').strip()
        end = str(raw.get('end') or '').strip()
        if (
            not weekday
            or not _OPEN_PLAY_TIME_RE.fullmatch(start)
            or not _OPEN_PLAY_TIME_RE.fullmatch(end)
            or start == end
        ):
            raise ValueError
        row = {
            'weekday': weekday,
            'start': start,
            'end': end,
            'level': str(raw.get('level') or '').strip()[:40],
            'cost': str(raw.get('cost') or '').strip()[:60],
            'notes': str(raw.get('notes') or '').strip()[:160],
        }
        signature = tuple(row.items())
        if signature not in seen:
            seen.add(signature)
            rows.append(row)
    return rows


def _court_suggest_value(court, field):
    if field == 'open_play_schedule_rows':
        return court.open_play_schedule_rows_list()
    value = getattr(court, field)
    return value or '' if isinstance(value, str) else value


def _set_court_suggest_value(court, field, value):
    if field == 'open_play_schedule_rows':
        court.open_play_schedule_rows = json.dumps(value, separators=(',', ':'))
    else:
        setattr(court, field, value)


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
    'open_play_schedule': _norm_text(1000),
    'open_play_schedule_rows': _norm_open_play_rows,
    'closed': _norm_bool,
}
SUGGESTION_CONSENSUS = 2


def _court_suggestion_payload(suggestion):
    """Read a suggestion defensively so one malformed legacy row cannot block review."""
    try:
        payload = json.loads(suggestion.payload or '{}')
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _court_suggestion_signature(field, value):
    return field, json.dumps(value, sort_keys=True, separators=(',', ':'))


def _apply_court_suggestion_consensus(court):
    """Apply values independently once two distinct players confirm them."""
    pending = CourtEditSuggestion.query.filter_by(
        court_id=court.id, status='pending',
    ).all()
    votes = {}
    for suggestion in pending:
        for field, value in _court_suggestion_payload(suggestion).items():
            if field in SUGGESTABLE_FIELDS:
                votes.setdefault(_court_suggestion_signature(field, value), set()).add(
                    suggestion.user_id,
                )
    applied = {}
    for (field, packed), users in votes.items():
        if len(users) >= SUGGESTION_CONSENSUS:
            value = json.loads(packed)
            _set_court_suggest_value(court, field, value)
            applied[field] = value
    if applied:
        for suggestion in pending:
            remaining = {
                field: value
                for field, value in _court_suggestion_payload(suggestion).items()
                if field not in applied or value != applied[field]
            }
            if remaining:
                suggestion.payload = json.dumps(remaining, separators=(',', ':'))
            else:
                suggestion.status = 'applied'
    return applied


def _pending_court_suggestion_items(court_id, user_id):
    rows = CourtEditSuggestion.query.filter(
        CourtEditSuggestion.court_id == court_id,
        CourtEditSuggestion.status.in_(('pending', 'rejected')),
    ).all()
    grouped = {}
    rejected = {}
    for suggestion in rows:
        destination = grouped if suggestion.status == 'pending' else rejected
        for field, value in _court_suggestion_payload(suggestion).items():
            if field not in SUGGESTABLE_FIELDS:
                continue
            signature = _court_suggestion_signature(field, value)
            destination.setdefault(signature, set()).add(suggestion.user_id)
    items = []
    for (field, packed), confirmations in grouped.items():
        value = json.loads(packed)
        rejections = rejected.get((field, packed), set())
        items.append({
            'field': field,
            'value': value,
            'confirmations': len(confirmations),
            'rejections': len(rejections),
            'confirmed_by_me': user_id in confirmations,
            'rejected_by_me': user_id in rejections,
            'needed': max(0, SUGGESTION_CONSENSUS - len(confirmations)),
        })
    return sorted(items, key=lambda item: (item['field'], json.dumps(item['value'], sort_keys=True)))


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
        current = _court_suggest_value(court, field)
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

    applied = _apply_court_suggestion_consensus(court)
    db.session.commit()
    return jsonify({
        'submitted': True,
        'applied_fields': sorted(applied),
        'court': court.to_dict(),
    }), 201


@courts_bp.get('/courts/<int:court_id>/suggestions')
@login_required
def list_court_edit_suggestions(court_id):
    """Expose pending community corrections so the second vote is an informed one."""
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    return jsonify({
        'items': _pending_court_suggestion_items(court.id, g.current_user.id),
        'consensus_required': SUGGESTION_CONSENSUS,
    })


@courts_bp.post('/courts/<int:court_id>/suggestions/decision')
@rate_limit(40, 3600)
@login_required
def decide_court_edit_suggestion(court_id):
    """Confirm or reject one exact pending value without retyping the whole listing."""
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    body = request.get_json(silent=True) or {}
    field = str(body.get('field') or '').strip()
    decision = str(body.get('decision') or '').strip().lower()
    if field not in SUGGESTABLE_FIELDS or decision not in ('confirm', 'reject'):
        return jsonify({'error': 'invalid_decision'}), 400
    try:
        value = SUGGESTABLE_FIELDS[field](body.get('value'))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_field', 'field': field}), 400
    signature = _court_suggestion_signature(field, value)
    pending = CourtEditSuggestion.query.filter_by(
        court_id=court.id, status='pending',
    ).all()
    if not any(
        signature == _court_suggestion_signature(field, payload[field])
        for suggestion in pending
        for payload in (_court_suggestion_payload(suggestion),)
        if field in payload
    ):
        return jsonify({'error': 'suggestion_not_pending'}), 404

    own_pending = next(
        (row for row in pending if row.user_id == g.current_user.id), None,
    )
    own_changes = _court_suggestion_payload(own_pending) if own_pending else {}
    rejected_row = CourtEditSuggestion.query.filter_by(
        court_id=court.id, user_id=g.current_user.id, status='rejected',
    ).first()
    rejected_changes = _court_suggestion_payload(rejected_row) if rejected_row else {}

    if decision == 'confirm':
        own_changes[field] = value
        if not own_pending:
            own_pending = CourtEditSuggestion(
                court_id=court.id, user_id=g.current_user.id, status='pending',
            )
            db.session.add(own_pending)
        own_pending.payload = json.dumps(own_changes, separators=(',', ':'))
        if rejected_changes.get(field) == value:
            rejected_changes.pop(field, None)
    else:
        if own_changes.get(field) == value:
            own_changes.pop(field, None)
            if own_changes:
                own_pending.payload = json.dumps(own_changes, separators=(',', ':'))
            else:
                own_pending.status = 'withdrawn'
        rejected_changes[field] = value
        if not rejected_row:
            rejected_row = CourtEditSuggestion(
                court_id=court.id, user_id=g.current_user.id, status='rejected',
            )
            db.session.add(rejected_row)
    if rejected_row:
        if rejected_changes:
            rejected_row.payload = json.dumps(rejected_changes, separators=(',', ':'))
        else:
            rejected_row.status = 'withdrawn'

    db.session.flush()
    applied = _apply_court_suggestion_consensus(court)
    db.session.commit()
    return jsonify({
        'decision': decision,
        'applied_fields': sorted(applied),
        'court': court.to_dict(),
        'items': _pending_court_suggestion_items(court.id, g.current_user.id),
    })


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
    condition = _latest_condition_for(
        court.id, include_identity=optional_current_user() is not None,
    )
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
    players, games, active_games = _active_counts_for(ids, current_user)
    ratings = _rating_summary_for(ids)
    conditions = _conditions_for(ids)
    businesses = _public_business_summaries(ids)
    for item in items:
        court_id = item['id']
        item['players_here'] = players.get(court_id, 0)
        item['upcoming_games'] = games.get(court_id, 0)
        item['active_games'] = active_games.get(court_id, 0)
        item['condition'] = conditions.get(court_id)
        item['business'] = businesses.get(court_id)
        rating = ratings.get(court_id)
        item['rating_avg'] = rating['rating_avg'] if rating else None
        item['rating_count'] = rating['rating_count'] if rating else 0
    return items


def _latest_condition_for(court_id, include_identity=True):
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
        'user_name': (
            row.user.display_name if include_identity and row.user else 'Player'
        ),
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
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
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
    viewer = optional_current_user()
    if photo_ids:
        for like in CourtPhotoLike.query.filter(
                CourtPhotoLike.photo_id.in_(photo_ids)).all():
            likes[like.photo_id] = likes.get(like.photo_id, 0) + 1
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
        'user_name': p.user.display_name if viewer and p.user else 'Player',
        'caption': p.caption or '',
        'likes': likes.get(p.id, 0),
        'liked_by_me': p.id in mine,
        'can_delete': bool(viewer and p.user_id == viewer.id),
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


@courts_bp.delete('/courts/<int:court_id>/photos/<int:photo_id>')
@rate_limit(30, 3600)
@login_required
def delete_court_photo(court_id, photo_id):
    """Remove an uploader's own gallery photo and its reactions."""
    photo = db.session.get(CourtPhoto, photo_id)
    if not photo or photo.court_id != court_id:
        return jsonify({'error': 'photo_not_found'}), 404
    if photo.user_id != g.current_user.id:
        return jsonify({'error': 'photo_not_owned'}), 403
    from backend.models import CourtPhotoLike
    CourtPhotoLike.query.filter_by(photo_id=photo.id).delete(
        synchronize_session=False,
    )
    db.session.delete(photo)
    db.session.flush()
    court = db.session.get(Court, court_id)
    remaining = CourtPhoto.query.filter_by(court_id=court_id).count()
    if court and court.photo_url.startswith('/api/courts/') and not remaining \
            and not court.photo_data:
        court.photo_url = ''
    db.session.commit()
    return jsonify({
        'deleted': True,
        'photo_count': remaining,
        'photo_url': court.photo_url if court else '',
    })


@courts_bp.get('/courts/<int:court_id>/photos/<int:photo_id>')
def court_photo_item(court_id, photo_id):
    photo = db.session.get(CourtPhoto, photo_id)
    if not photo or photo.court_id != court_id:
        return jsonify({'error': 'photo_not_found'}), 404
    return _photo_response(photo.photo_data)


@courts_bp.post('/courts/<int:court_id>/favorite')
@courts_bp.put('/courts/<int:court_id>/favorite')
@rate_limit(60, 600)
@login_required
def toggle_favorite(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    existing = FavoriteCourt.query.filter_by(
        user_id=g.current_user.id, court_id=court.id,
    ).first()
    if request.method == 'PUT':
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('favorited'), bool):
            return jsonify({'error': 'invalid_payload'}), 400
        favorited = payload['favorited']
        if favorited and not existing:
            db.session.add(FavoriteCourt(user_id=g.current_user.id, court_id=court.id))
        elif not favorited and existing:
            db.session.delete(existing)
    elif existing:
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
    favorites = (
        FavoriteCourt.query.filter_by(user_id=user.id)
        .order_by(FavoriteCourt.id.desc())
        .all()
    )
    items = _enrich_court_summaries([
        _court_discovery_summary(favorite.court)
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
    """Top two-hour visit windows from the last 90 days of check-ins.

    Prefer the court's structured-hours timezone so the result remains correct
    across DST. Legacy courts without one retain the longitude approximation
    instead of presenting UTC as local time.
    """
    rows = CheckIn.query.filter(
        CheckIn.court_id == court.id,
        CheckIn.checked_in_at >= utcnow() - timedelta(days=90),
    ).all()
    timezone_name = str(court.structured_hours_dict().get('timezone') or '').strip()
    timezone = None
    if timezone_name:
        try:
            timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            timezone = None
    tz_offset = round(court.longitude / 15) if court.longitude is not None else 0
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    buckets = {}
    for checkin in rows:
        local = (
            checkin.checked_in_at.replace(tzinfo=UTC).astimezone(timezone)
            if timezone else checkin.checked_in_at + timedelta(hours=tz_offset)
        )
        hour = local.hour
        if not 5 <= hour < 23:
            continue
        # Anchor at 5 AM to produce player-friendly windows such as 9–11 AM.
        start_hour = 5 + ((hour - 5) // 2) * 2
        key = (local.weekday(), start_hour)
        buckets[key] = buckets.get(key, 0) + 1
    ranked = sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))

    def clock(hour):
        suffix = 'AM' if hour < 12 or hour == 24 else 'PM'
        value = hour % 24
        value = value % 12 or 12
        return f'{value} {suffix}'

    def window_label(weekday, start_hour):
        end_hour = start_hour + 2
        start = clock(start_hour)
        end = clock(end_hour)
        start_value, start_suffix = start.rsplit(' ', 1)
        _, end_suffix = end.rsplit(' ', 1)
        compact_start = start_value if start_suffix == end_suffix else start
        return f'{days[weekday]} {compact_start}–{end}'

    return [
        {'label': window_label(weekday, start_hour), 'count': count}
        for (weekday, start_hour), count in ranked[:3]
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
    if court.closed:
        return jsonify({'error': 'court_closed'}), 409

    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    looking = bool(payload.get('looking_for_game'))
    presence_intent = payload.get('presence_intent')
    verified_location = None
    if presence_intent is not None or 'presence_location' in payload:
        if presence_intent not in {
            'manual_checkin', 'instant_rally', 'arrival_join', 'auto_checkin',
        }:
            return jsonify({'error': 'invalid_presence_intent'}), 400
        verified_location, location_error = validate_court_presence_location(
            court, payload.get('presence_location'),
        )
        if location_error:
            status = 400 if location_error == 'invalid_presence_location' else 409
            return jsonify({'error': location_error}), status

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
    response = {'presence': presence_payload(g.current_user.id)}
    if verified_location:
        response['presence_verified'] = True
        response['presence_accuracy_meters'] = verified_location['accuracy_meters']
        # A short-lived, signed assertion is returned instead of retaining the
        # device coordinates. The rally route binds it to this user and court.
        response['instant_rally_presence_proof'] = (
            issue_instant_rally_presence_proof(g.current_user.id, court.id)
        )
    return jsonify(response)


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
            or not was_fresh
        ):
            # A heartbeat may extend only a still-fresh session. Reopening the
            # app after the freshness window must not silently resurrect an
            # exact court/LFG signal; an explicit check-in starts it again.
            _close_departed_instant_assemblies(
                instant_games, g.current_user.id, checkin.court_id, now,
            )
            checkin.checked_out_at = now
            checkin.looking_for_game = False
        else:
            checkin.last_presence_ping_at = now
        db.session.commit()
    return jsonify({'presence': presence_payload(g.current_user.id)})
