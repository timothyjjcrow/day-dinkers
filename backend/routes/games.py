"""Game scheduling, joining, and ranked match results."""
import hashlib
import json
import math
import re
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from flask import Blueprint, Response, current_app, g, jsonify, request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    CheckIn,
    Court,
    Crew,
    CrewInvite,
    FavoriteCourt,
    GAME_RECURRENCES,
    GAME_TYPES,
    GAME_VISIBILITIES,
    SKILL_LEVELS,
    Game,
    GameArrivalIntent,
    GameInvite,
    GameMvpVote,
    GameOpenCall,
    GamePlayer,
    GameWaitlist,
    Message,
    Notification,
    PlayAvailabilityPulse,
    Friendship,
    User,
    blocked_pair_ids,
    is_blocked_between,
    notify,
    utcnow,
)
from backend.routes.auth import (
    active_checkin_for,
    login_required,
    optional_current_user,
    presence_absolute_cutoff,
    presence_stale_cutoff,
)
from backend.routes.courts import haversine_miles
from backend.routes.social import friend_ids
from backend.security import rate_limit

games_bp = Blueprint('games', __name__)

CLIENT_ATTEMPT_ID_MAX_LENGTH = 64
CLIENT_ATTEMPT_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')


def _client_attempt_id(payload):
    """Return (value, valid) for an optional, UUID-friendly retry key."""
    if 'client_attempt_id' not in payload or payload.get('client_attempt_id') is None:
        return None, True
    raw = payload.get('client_attempt_id')
    if not isinstance(raw, str):
        return None, False
    if not raw or len(raw) > CLIENT_ATTEMPT_ID_MAX_LENGTH:
        return None, False
    if not CLIENT_ATTEMPT_ID_RE.fullmatch(raw):
        return None, False
    return raw, True


def _ics_escape(s):
    return str(s or '').replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')


def _ics_stamp(dt):
    return dt.strftime('%Y%m%dT%H%M%SZ')


@games_bp.get('/calendar/token')
@login_required
def calendar_token():
    """The user's personal calendar-feed URL path (token generated on first ask)."""
    if not g.current_user.calendar_token:
        g.current_user.calendar_token = secrets.token_urlsafe(24)
        db.session.commit()
    return jsonify({'token': g.current_user.calendar_token})


@games_bp.post('/calendar/token/reset')
@rate_limit(10, 3600)
@login_required
def reset_calendar_token():
    """Rotate the feed token — old subscription URLs stop working."""
    g.current_user.calendar_token = secrets.token_urlsafe(24)
    db.session.commit()
    return jsonify({'token': g.current_user.calendar_token})


@games_bp.get('/calendar/<token>.ics')
def calendar_feed(token):
    """Public ICS feed of a user's upcoming games — the token IS the auth
    (calendar apps can't send headers). Only upcoming games this user is in."""
    user = User.query.filter_by(calendar_token=token).first() if token else None
    if not user or user.deleted_at:
        return Response('not found', status=404)
    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status.in_(['upcoming', 'awaiting_confirmation']),
            Game.scheduled_at >= utcnow() - timedelta(hours=3),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(200)
        .all()
    )
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Third Shot//EN',
             'X-WR-CALNAME:Third Shot games', 'CALSCALE:GREGORIAN']
    now_stamp = _ics_stamp(utcnow())
    for game in games:
        start = game.scheduled_at
        end = start + timedelta(minutes=90)
        court = game.court
        ranked = ' (ranked)' if game.game_type == 'ranked' else ''
        lines += [
            'BEGIN:VEVENT',
            f'UID:thirdshot-game-{game.id}@thirdshot.app',
            f'DTSTAMP:{now_stamp}',
            f'DTSTART:{_ics_stamp(start)}',
            f'DTEND:{_ics_stamp(end)}',
            f'SUMMARY:{_ics_escape("Pickleball" + ranked + " at " + (court.name if court else "the court"))}',
            f'LOCATION:{_ics_escape(", ".join(filter(None, [court.name, court.city])) if court else "")}',
            f'DESCRIPTION:{_ics_escape(f"{len(game.players)}/{game.max_players} players")}',
            'END:VEVENT',
        ]

    # Tournaments the user is entered in (or organizing) join the feed too.
    from backend.models import Tournament, TournamentEntry
    entered = db.session.query(TournamentEntry.tournament_id).filter(
        db.or_(
            TournamentEntry.player1_id == user.id,
            TournamentEntry.player2_id == user.id,
        )
    )
    tournaments = (
        Tournament.query.filter(
            db.or_(Tournament.id.in_(entered), Tournament.organizer_id == user.id),
            Tournament.status.in_(['registration', 'active']),
            Tournament.starts_at >= utcnow() - timedelta(hours=6),
        )
        .order_by(Tournament.starts_at.asc())
        .limit(50)
        .all()
    )
    for tournament in tournaments:
        court = tournament.court
        fmt_label = 'round robin' if tournament.format == 'round_robin' else 'bracket'
        lines += [
            'BEGIN:VEVENT',
            f'UID:thirdshot-tournament-{tournament.id}@thirdshot.app',
            f'DTSTAMP:{now_stamp}',
            f'DTSTART:{_ics_stamp(tournament.starts_at)}',
            f'DTEND:{_ics_stamp(tournament.starts_at + timedelta(hours=4))}',
            f'SUMMARY:{_ics_escape("🏆 " + tournament.name)}',
            f'LOCATION:{_ics_escape(", ".join(filter(None, [court.name, court.city])) if court else "")}',
            f'DESCRIPTION:{_ics_escape(f"Pickleball tournament ({fmt_label}, {tournament.event_type}) — {len(tournament.entries)} entries")}',
            'END:VEVENT',
        ]
    # Active box-league round deadlines: get your matches in before this.
    from backend.models import League, LeagueMember
    leagues = (
        League.query.join(LeagueMember, LeagueMember.league_id == League.id)
        .filter(
            LeagueMember.user_id == user.id,
            League.status == 'active',
            League.round_started_at.isnot(None),
        )
        .limit(20)
        .all()
    )
    for league in leagues:
        deadline = league.round_started_at + timedelta(days=league.round_days)
        if deadline < utcnow() - timedelta(hours=6):
            continue  # the lazy sweep will roll this round over shortly
        court = league.court
        lines += [
            'BEGIN:VEVENT',
            f'UID:thirdshot-league-{league.id}-round-{league.current_round}@thirdshot.app',
            f'DTSTAMP:{now_stamp}',
            f'DTSTART:{_ics_stamp(deadline - timedelta(hours=1))}',
            f'DTEND:{_ics_stamp(deadline)}',
            f'SUMMARY:{_ics_escape(f"📦 {league.name} — round {league.current_round} deadline")}',
            f'LOCATION:{_ics_escape(", ".join(filter(None, [court.name, court.city])) if court else "")}',
            f'DESCRIPTION:{_ics_escape("Play your box matches before the round closes")}',
            'END:VEVENT',
        ]

    lines.append('END:VCALENDAR')
    return Response('\r\n'.join(lines), mimetype='text/calendar',
                    headers={'Content-Disposition': 'inline; filename="thirdshot.ics"'})


ELO_K = 32
SCORE_AUTO_CONFIRM_HOURS = 24
REMINDER_LEAD_MINUTES = 65
UNSCORED_EXPIRY_DAYS = 7
INSTANT_RALLY_ASSEMBLY_MINUTES = 90
RALLY_ARRIVAL_ETA_MINUTES = (5, 10, 15)
RALLY_ARRIVAL_GRACE_MINUTES = 5
RALLY_ARRIVAL_HARD_MAX_MINUTES = 20
RALLY_ARRIVAL_CAPABILITY_SECONDS = 5 * 60
RALLY_ARRIVAL_ANNOUNCEMENT_COOLDOWN_MINUTES = 20
PLAY_PULSE_MINUTES = 60
PLAY_PULSE_START_LEAD_MINUTES = 15
PLAY_PULSE_CAPABILITY_SECONDS = 5 * 60
GAME_OPEN_CALL_CREATE_GRACE_MINUTES = 15
RALLY_ARRIVAL_EARLY_END_REASONS = frozenset({
    'completed', 'creator_deleted', 'presence_ended', 'rally_cancelled',
    'rally_closed', 'rally_expired', 'score_submitted',
})


def _game_open_call_attempt_fingerprint(game_id):
    encoded = json.dumps({
        'operation': 'game_open_call_v1',
        'game_id': int(game_id),
    }, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _end_game_open_call(call, reason, now=None):
    """End one court call without deleting its retry/audit record."""
    if not call or not call.active:
        return False
    call.active = False
    call.ended_at = now or utcnow()
    call.end_reason = str(reason or 'closed')[:32]
    return True


def _end_game_open_calls(game, reason, now=None):
    """End every active call for a Game while the caller holds its row lock."""
    if not game:
        return False
    calls = (
        GameOpenCall.query.filter_by(game_id=game.id, active=True)
        .order_by(GameOpenCall.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    changed = False
    for call in calls:
        changed = _end_game_open_call(call, reason, now) or changed
    if changed:
        db.session.flush()
        db.session.expire(game, ['open_calls'])
    return changed


def _notify_saved_court_fans(
    game, actor, label='game', *, excluded_user_ids=None, club_pinged=None,
):
    """Send the existing saved-court opt-in alert for one open game.

    This is shared by planned-game creation and the pulse-match path so an
    ordinary open game has the same recruitment reach no matter how its first
    two players assembled. It is not called by the later court-room open call,
    avoiding duplicate pushes for the same game.
    """
    if not game or game.visibility != 'open' or not game.court or not actor:
        return 0
    excluded = set(excluded_user_ids or ()) | {actor.id}
    club_pinged = set(club_pinged or ())
    fans = FavoriteCourt.query.filter_by(court_id=game.court_id).limit(200).all()
    fan_ids = [fan.user_id for fan in fans]
    recently_pinged = {
        notification.user_id
        for notification in Notification.query.filter(
            Notification.kind == 'court_game',
            Notification.related_user_id == actor.id,
            Notification.created_at >= utcnow() - timedelta(hours=3),
            Notification.user_id.in_(fan_ids),
        )
    } if fan_ids else set()
    sent = 0
    for fan in fans:
        if fan.user_id in excluded or fan.user_id in recently_pinged \
                or fan.user_id in club_pinged:
            continue
        target = fan.user or db.session.get(User, fan.user_id)
        if not target or target.deleted_at:
            continue
        if any(
            is_blocked_between(player_id, fan.user_id)
            for player_id in excluded
        ):
            continue
        if notify(
            fan.user_id,
            'court_game',
            f'New {label} at {game.court.name} — a court you saved',
            related_user_id=actor.id,
            related_game_id=game.id,
        ) is not None:
            sent += 1
    return sent


def _parse_scheduled_at(raw):
    text = str(raw or '').strip().replace('Z', '+00:00')
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _normalized_game_attempt(payload, creator_id):
    """Canonical immutable inputs for one game-creation attempt.

    Normalization makes harmless representation changes (numeric strings,
    timezone spelling, invite order) compare equal while excluding unrelated
    or future request fields from the idempotency contract.
    """
    try:
        court_id = int(payload.get('court_id') or 0)
    except (TypeError, ValueError):
        court_id = 0

    scheduled_at = _parse_scheduled_at(payload.get('scheduled_at'))
    game_type = str(payload.get('game_type') or 'casual').strip().lower()

    try:
        max_players = int(payload.get('max_players') or 4)
    except (TypeError, ValueError):
        max_players = 4
    max_players = min(max(max_players, 2), 12)
    if game_type == 'ranked':
        max_players = 4 if max_players > 2 else 2

    raw_invites = payload.get('invite_user_ids') or []
    if not isinstance(raw_invites, (list, tuple)):
        raw_invites = []
    invite_user_ids = []
    for raw_id in raw_invites[:20]:
        try:
            invitee_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if invitee_id == creator_id or invitee_id in invite_user_ids:
            continue
        invite_user_ids.append(invitee_id)
    invite_user_ids.sort()

    visibility = str(payload.get('visibility') or '').strip().lower()
    if visibility not in GAME_VISIBILITIES:
        visibility = 'private' if invite_user_ids else 'open'

    recurrence = str(payload.get('recurrence') or 'none').strip().lower()
    if recurrence not in GAME_RECURRENCES:
        recurrence = 'none'
    if game_type == 'ranked':
        recurrence = 'none'

    preferred_level = str(payload.get('preferred_level') or 'any').strip().lower()
    if preferred_level not in SKILL_LEVELS:
        preferred_level = 'any'

    raw_club_id = payload.get('club_id')
    if raw_club_id:
        try:
            club_id = int(raw_club_id)
        except (TypeError, ValueError):
            club_id = 0
    else:
        club_id = None

    raw_crew_id = payload.get('crew_id')
    if raw_crew_id:
        try:
            crew_id = int(raw_crew_id)
        except (TypeError, ValueError):
            crew_id = 0
    else:
        crew_id = None
    raw_crew_version = payload.get('expected_crew_version')
    if raw_crew_version is not None:
        try:
            expected_crew_version = int(raw_crew_version)
        except (TypeError, ValueError):
            expected_crew_version = 0
    else:
        expected_crew_version = None

    # A Crew request represents one immutable server-owned roster snapshot.
    # Client invite lists and visibility cannot narrow or expose that roster.
    if crew_id is not None:
        invite_user_ids = []
        visibility = 'private'
        recurrence = 'none'

    return {
        'court_id': court_id,
        'scheduled_at': scheduled_at,
        'game_type': game_type,
        'max_players': max_players,
        'invite_user_ids': invite_user_ids,
        'require_all_invitees': payload.get('require_all_invitees') is True,
        'visibility': visibility,
        'recurrence': recurrence,
        'preferred_level': preferred_level,
        'club_id': club_id,
        'crew_id': crew_id,
        'expected_crew_version': expected_crew_version,
        'notes': str(payload.get('notes') or '').strip()[:500],
    }


def _game_attempt_fingerprint(normalized):
    canonical = {
        **normalized,
        'scheduled_at': (
            normalized['scheduled_at'].isoformat()
            if normalized['scheduled_at'] is not None else None
        ),
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _game_attempt_replay(game, fingerprint, current_user_id):
    # The interim idempotency rollout created a small number of keyed rows
    # without request fingerprints. Their original immutable snapshot cannot
    # be reconstructed safely from game state that may since have changed, so
    # preserve creator-scoped replay compatibility for those legacy rows.
    if game.client_attempt_fingerprint is None:
        return jsonify(game.to_dict(current_user_id)), 200
    if game.client_attempt_fingerprint != fingerprint:
        # The key is creator-scoped, so this only tells the authenticated
        # creator which of their own games already owns it. Clients can recover
        # stale local state without minting a fresh key and duplicating a game.
        return jsonify({
            'error': 'client_attempt_id_conflict',
            'existing_game_id': game.id,
        }), 409
    return jsonify(game.to_dict(current_user_id)), 200


def _rally_attempt(payload):
    """Stable inputs for retrying the one-tap, checked-in rally action."""
    raw_court_id = payload.get('court_id')
    if isinstance(raw_court_id, bool):
        expected_court_id = 0
    elif isinstance(raw_court_id, int):
        expected_court_id = raw_court_id
    elif isinstance(raw_court_id, str) and raw_court_id.strip().isdigit():
        expected_court_id = int(raw_court_id.strip())
    else:
        expected_court_id = 0
    scheduled_at = _parse_scheduled_at(payload.get('scheduled_at'))
    # Accept the short-lived pre-court-binding fingerprint only when its
    # stored game is instant and already belongs to this exact expected court.
    legacy_normalized = {
        'operation': 'instant_rally_v1',
        'scheduled_at': scheduled_at,
    }
    normalized = {
        'operation': 'instant_rally_v2',
        'court_id': expected_court_id,
        'scheduled_at': scheduled_at,
    }
    return (
        expected_court_id,
        scheduled_at,
        _game_attempt_fingerprint(normalized),
        _game_attempt_fingerprint(legacy_normalized),
    )


def _rally_attempt_matches(
    game, expected_court_id, fingerprint, legacy_fingerprint,
):
    """Whether a stored keyed rally is the exact selected-court attempt."""
    if (
        not game.is_instant
        or game.court_id != expected_court_id
    ):
        return False
    stored = game.client_attempt_fingerprint
    return stored is None or stored in (fingerprint, legacy_fingerprint)


def issue_rally_arrival_capability(user_id, game_id, court_id, now=None):
    """Mint a short, viewer-bound capability from bounded rally discovery."""
    now = now or utcnow()
    issued_at = int(now.replace(tzinfo=UTC).timestamp())
    return jwt.encode(
        {
            'typ': 'rally_arrival',
            'sub': str(int(user_id)),
            'game_id': int(game_id),
            'court_id': int(court_id),
            'iat': issued_at,
            'exp': issued_at + RALLY_ARRIVAL_CAPABILITY_SECONDS,
        },
        current_app.config['SECRET_KEY'],
        algorithm=current_app.config.get('JWT_ALGORITHM', 'HS256'),
    )


def _valid_rally_arrival_capability(
    token, user_id, game_id, court_id,
):
    """Validate without returning claims that could become an oracle."""
    if not isinstance(token, str) or not token.strip():
        return False
    try:
        claims = jwt.decode(
            token.strip(),
            current_app.config['SECRET_KEY'],
            algorithms=[current_app.config.get('JWT_ALGORITHM', 'HS256')],
        )
    except jwt.PyJWTError:
        return False
    try:
        return (
            claims.get('typ') == 'rally_arrival'
            and int(claims.get('sub') or 0) == int(user_id)
            and int(claims.get('game_id') or 0) == int(game_id)
            and int(claims.get('court_id') or 0) == int(court_id)
        )
    except (TypeError, ValueError):
        return False


def _arrival_attempt_fingerprint(game_id, eta_minutes):
    encoded = json.dumps(
        {
            'operation': 'rally_arrival_v1',
            'game_id': int(game_id),
            'eta_minutes': int(eta_minutes),
        },
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _play_pulse_attempt_fingerprint(court_id):
    encoded = json.dumps(
        {
            'operation': 'play_availability_pulse_v1',
            'court_id': int(court_id),
        },
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _play_pulse_accept_fingerprint(pulse_id):
    encoded = json.dumps(
        {
            'operation': 'play_availability_pulse_accept_v1',
            'pulse_id': int(pulse_id),
        },
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def issue_play_pulse_accept_capability(viewer_id, pulse_id, court_id, now=None):
    """Mint a short viewer-bound capability from pulse discovery."""
    now = now or utcnow()
    issued_at = int(now.replace(tzinfo=UTC).timestamp())
    return jwt.encode(
        {
            'typ': 'play_pulse_accept',
            'sub': str(int(viewer_id)),
            'pulse_id': int(pulse_id),
            'court_id': int(court_id),
            'iat': issued_at,
            'exp': issued_at + PLAY_PULSE_CAPABILITY_SECONDS,
        },
        current_app.config['SECRET_KEY'],
        algorithm=current_app.config.get('JWT_ALGORITHM', 'HS256'),
    )


def _valid_play_pulse_accept_capability(
    token, viewer_id, pulse_id, court_id,
):
    """Validate a capability without exposing which claim was invalid."""
    if not isinstance(token, str) or not token.strip():
        return False
    try:
        claims = jwt.decode(
            token.strip(),
            current_app.config['SECRET_KEY'],
            algorithms=[current_app.config.get('JWT_ALGORITHM', 'HS256')],
        )
    except jwt.PyJWTError:
        return False
    try:
        return (
            claims.get('typ') == 'play_pulse_accept'
            and int(claims.get('sub') or 0) == int(viewer_id)
            and int(claims.get('pulse_id') or 0) == int(pulse_id)
            and int(claims.get('court_id') or 0) == int(court_id)
        )
    except (TypeError, ValueError):
        return False


def _play_pulse_time_active(pulse, now=None):
    now = now or utcnow()
    return bool(
        pulse
        and pulse.active
        and pulse.ended_at is None
        and pulse.expires_at
        and pulse.expires_at > now
    )


def _end_play_pulse(pulse, reason, now=None):
    """One-way end a pulse while retaining its retry ledger."""
    if not pulse or not pulse.active:
        return False
    now = now or utcnow()
    pulse.active = False
    pulse.ended_at = now
    pulse.end_reason = str(reason or 'ended')[:32]
    return True


def _active_play_pulse_for_user(user_id, now=None, for_update=False):
    """Return a query-time-active pulse, lazily retiring expired rows."""
    now = now or utcnow()
    query = PlayAvailabilityPulse.query.filter_by(
        user_id=user_id, active=True,
    ).order_by(PlayAvailabilityPulse.id.asc())
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = query.all()
    active = None
    changed = False
    for pulse in rows:
        if _play_pulse_time_active(pulse, now) and active is None:
            active = pulse
        else:
            changed = _end_play_pulse(pulse, 'expired', now) or changed
    if changed:
        db.session.flush()
    return active


def _end_active_play_pulse_for_user(user_id, reason, now=None):
    """End a user's active pulse. Caller holds User and any needed Game locks."""
    now = now or utcnow()
    rows = (
        PlayAvailabilityPulse.query.filter_by(user_id=user_id, active=True)
        .order_by(PlayAvailabilityPulse.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    changed = False
    for pulse in rows:
        normalized = reason if _play_pulse_time_active(pulse, now) else 'expired'
        changed = _end_play_pulse(pulse, normalized, now) or changed
    if changed:
        db.session.flush()
    return changed


def _end_play_pulse_for_game(user_id, game, reason, now=None):
    """Consume a pulse only when a durable game overlaps its stated hour."""
    now = now or utcnow()
    rows = (
        PlayAvailabilityPulse.query.filter_by(user_id=user_id, active=True)
        .order_by(PlayAvailabilityPulse.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    changed = False
    for pulse in rows:
        if not _play_pulse_time_active(pulse, now):
            changed = _end_play_pulse(pulse, 'expired', now) or changed
            continue
        overlaps = bool(
            game
            and (
                game.is_instant
                or (
                    game.scheduled_at
                    and pulse.declared_at <= game.scheduled_at <= pulse.expires_at
                )
            )
        )
        if overlaps:
            changed = _end_play_pulse(pulse, reason, now) or changed
    if changed:
        db.session.flush()
    return changed


def _active_live_rally_for_user(user_id, now=None, locked_games=None):
    """Return a live instant rally membership; roster alone is insufficient."""
    now = now or utcnow()
    games = locked_games
    if games is None:
        games = (
            Game.query.join(GamePlayer)
            .filter(
                GamePlayer.user_id == user_id,
                Game.is_instant.is_(True),
                Game.status == 'upcoming',
            )
            .order_by(Game.id.asc())
            .all()
        )
    for game in games:
        if (
            game.is_instant
            and any(player.user_id == user_id for player in game.players)
            and _instant_rally_assembly_active(game, now)
        ):
            return game
    return None


def _active_immediate_game_for_user(user_id, games, starts_at, expires_at):
    """A normal upcoming roster commitment inside an availability window."""
    return next(
        (
            game for game in games
            if not game.is_instant
            and game.status == 'upcoming'
            and game.scheduled_at
            and starts_at <= game.scheduled_at <= expires_at
            and any(player.user_id == user_id for player in game.players)
        ),
        None,
    )


def _pulse_payload(pulse, now=None):
    return pulse.to_dict(now) if pulse else None


def _rally_response(game, outcome, current_user_id, invited_count=0, status=200):
    return jsonify({
        'game': _game_payload(game, current_user_id),
        'outcome': outcome,
        'invited_count': invited_count,
    }), status


def _instant_rally_candidates(court_id, now):
    """Base query for a rally that can still recruit at ``court_id``.

    The durable game row can remain for scoring, but physical recruiting has
    an absolute 90-minute ceiling and a one-way assembly close.
    """
    return Game.query.filter(
        Game.court_id == court_id,
        Game.status == 'upcoming',
        Game.is_instant.is_(True),
        Game.assembly_closed_at.is_(None),
        Game.game_type == 'casual',
        Game.visibility == 'open',
        Game.recurrence == 'none',
        Game.scheduled_at >= now - timedelta(
            minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
        ),
        Game.scheduled_at <= now + timedelta(minutes=15),
    )


def _fresh_instant_roster_checkins(game, now=None, for_update=False):
    """Fresh exact-court presence rows belonging to the durable roster."""
    now = now or utcnow()
    if not game:
        return []
    member_ids = {player.user_id for player in game.players}
    if not member_ids:
        return []
    query = CheckIn.query.filter(
        CheckIn.user_id.in_(member_ids),
        CheckIn.court_id == game.court_id,
        CheckIn.checked_out_at.is_(None),
        CheckIn.checked_in_at >= presence_absolute_cutoff(now),
        CheckIn.last_presence_ping_at >= presence_stale_cutoff(now),
    ).order_by(CheckIn.user_id.asc(), CheckIn.id.asc())
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return query.all()


def _instant_rally_has_fresh_member(game, now=None, for_update=False):
    """Whether a current member still has fresh exact-court presence."""
    return bool(_fresh_instant_roster_checkins(
        game, now, for_update=for_update,
    ))


def _instant_rally_assembly_active(game, now=None, for_update=False):
    """Whether an instant roster is still physically assembling."""
    now = now or utcnow()
    return bool(
        game
        and game.is_instant
        and game.status == 'upcoming'
        and game.assembly_closed_at is None
        and game.scheduled_at >= now - timedelta(
            minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
        )
        and game.scheduled_at <= now + timedelta(minutes=15)
        and _instant_rally_has_fresh_member(
            game, now, for_update=for_update,
        )
    )


def _arrival_time_active(intent, now=None):
    now = now or utcnow()
    return bool(
        intent
        and intent.active
        and intent.ended_at is None
        and intent.expires_at
        and intent.expires_at > now
    )


def _end_arrival_intent(intent, reason, now=None):
    """One-way end an arrival row while retaining retry history."""
    if not intent or not intent.active:
        return False
    now = now or utcnow()
    was_time_active = _arrival_time_active(intent, now)
    normalized_reason = str(reason or 'ended')[:32]
    intent.active = False
    intent.ended_at = now
    intent.end_reason = normalized_reason
    if was_time_active and normalized_reason in RALLY_ARRIVAL_EARLY_END_REASONS:
        notify(
            intent.user_id,
            'rally_arrival_ended',
            'Your live rally ended',
            'Your reserved spot was released before you arrived.',
            related_game_id=intent.game_id,
            unread_dedupe_key=f'rally-arrival-ended:{intent.id}',
        )
    return True


def _retire_expired_arrival_intents(*, game_id=None, user_id=None, now=None):
    """Release expired partial-unique slots under the caller's Game lock."""
    now = now or utcnow()
    query = GameArrivalIntent.query.filter(
        GameArrivalIntent.active.is_(True),
    )
    if game_id is not None:
        query = query.filter(GameArrivalIntent.game_id == game_id)
    if user_id is not None:
        query = query.filter(GameArrivalIntent.user_id == user_id)
    rows = (
        query.order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for intent in rows:
        if not _arrival_time_active(intent, now):
            _end_arrival_intent(intent, 'expired', now)
    if rows:
        db.session.flush()
    return rows


def _end_game_arrivals(game, reason, now=None):
    """Release every protected remote slot when assembly ends."""
    if not game or not game.is_instant:
        return 0
    now = now or utcnow()
    rows = (
        GameArrivalIntent.query.filter_by(game_id=game.id, active=True)
        .order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    changed = sum(
        1 for intent in rows
        if _end_arrival_intent(intent, reason, now)
    )
    if changed:
        db.session.flush()
    return changed


def _raw_active_arrivals(game, now=None, for_update=False,
                         require_live=True):
    """Query-time-live rows, including legacy blocked/member conflicts."""
    now = now or utcnow()
    if not game or not game.is_instant:
        return []
    if require_live and not _instant_rally_assembly_active(
        game, now, for_update=for_update,
    ):
        return []
    query = GameArrivalIntent.query.filter(
        GameArrivalIntent.game_id == game.id,
        GameArrivalIntent.active.is_(True),
        GameArrivalIntent.ended_at.is_(None),
        GameArrivalIntent.expires_at > now,
    ).order_by(GameArrivalIntent.id.asc())
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return query.all()


def _active_remote_arrivals(game, now=None, for_update=False,
                            require_live=True):
    """Active nonmember reservations; never a physical-presence predicate."""
    now = now or utcnow()
    if not game or not game.is_instant:
        return []
    member_ids = {player.user_id for player in game.players}
    return [
        intent for intent in _raw_active_arrivals(
            game, now, for_update=for_update, require_live=require_live,
        )
        if intent.user_id not in member_ids
        and intent.user is not None
        and intent.user.deleted_at is None
        and not any(
            is_blocked_between(intent.user_id, member_id)
            for member_id in member_ids
        )
    ]


def _active_holder_blocks_user(game, user_id, now=None, for_update=False):
    """Whether admitting ``user_id`` would strand a blocked remote holder."""
    if not user_id:
        return False
    return any(
        intent.user_id != user_id
        and is_blocked_between(intent.user_id, user_id)
        for intent in _raw_active_arrivals(
            game, now, for_update=for_update,
        )
    )


def _active_arrival_for_user(game, user_id, now=None):
    if not game or not user_id:
        return None
    now = now or utcnow()
    intent = (
        GameArrivalIntent.query.filter_by(
            game_id=game.id, user_id=user_id, active=True,
        )
        .filter(
            GameArrivalIntent.ended_at.is_(None),
            GameArrivalIntent.expires_at > now,
        )
        .order_by(GameArrivalIntent.id.desc())
        .first()
    )
    if not intent or not _instant_rally_assembly_active(game, now):
        return None
    return intent


def _arrival_capacity(game, now=None, for_update=False):
    now = now or utcnow()
    # Presence controls readiness. Durable roster rows still control admission
    # and scoring ownership. Query CheckIns before intents to preserve the
    # shared Game -> CheckIn -> intent lock order on mutating paths.
    ready_user_ids = {
        checkin.user_id for checkin in _fresh_instant_roster_checkins(
            game, now, for_update=for_update,
        )
    }
    roster_count = len(game.players)
    arrivals = _active_remote_arrivals(
        game, now, for_update=for_update,
    )
    # Do not clamp this diagnostic: if an invariant ever regresses, payloads
    # and tests must expose the over-capacity truth rather than hiding it.
    committed = roster_count + len(arrivals)
    return {
        'arrivals': arrivals,
        'ready_count': len(ready_user_ids),
        'roster_count': roster_count,
        'on_the_way_count': len(arrivals),
        'committed_count': committed,
        'physical_spots_left': max(0, game.max_players - len(ready_user_ids)),
        'spots_left': max(0, game.max_players - committed),
    }


def _arrival_reservation_available(game, capacity=None, now=None):
    """Whether even the shortest supported remote hold can still be valid."""
    now = now or utcnow()
    capacity = capacity or _arrival_capacity(game, now)
    assembly_ceiling = game.scheduled_at + timedelta(
        minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
    )
    return bool(
        capacity['on_the_way_count'] == 0
        and capacity['spots_left'] > 0
        and now + timedelta(minutes=min(RALLY_ARRIVAL_ETA_MINUTES))
        < assembly_ceiling
    )


def _arrival_identity_payload(intent, now=None):
    timing = intent.to_dict(now)
    timing['intent_id'] = timing.pop('id')
    return {
        **intent.user.to_public_dict(),
        'user_id': intent.user_id,
        **timing,
    }


def _close_instant_assembly_without_fresh_members(game, now=None):
    """Persist one-way closure after a locked instant roster mutation.

    The caller must hold the Game row lock and keep ``game.players`` current.
    Qualifying remaining CheckIns are locked too, so a concurrent explicit
    re-check-in cannot resurrect an assembly after the last present member
    leaves the roster.
    """
    now = now or utcnow()
    if (
        not game
        or not game.is_instant
        or game.status != 'upcoming'
        or game.assembly_closed_at is not None
    ):
        return False
    if _instant_rally_has_fresh_member(game, now, for_update=True):
        return False
    game.assembly_closed_at = now
    if len(game.players) <= 1:
        game.status = 'expired'
    _end_game_arrivals(game, 'rally_closed', now)
    return True


def _game_payload(game, viewer_id=None, perspective_user_id=None, now=None):
    """Serialize explicit live-vs-score-pending instant lifecycle truth."""
    now = now or utcnow()
    data = game.to_dict(viewer_id, perspective_user_id)
    if game.is_instant:
        assembly_active = _instant_rally_assembly_active(game, now)
        data['assembly_active'] = assembly_active
        capacity = _arrival_capacity(game, now)
        data.update({
            key: capacity[key]
            for key in (
                'ready_count', 'roster_count', 'on_the_way_count',
                'committed_count', 'physical_spots_left', 'spots_left',
            )
        })
        if assembly_active:
            if capacity['ready_count'] < 2:
                data['assembly_state'] = 'finding'
            elif capacity['spots_left'] > 0:
                data['assembly_state'] = 'ready'
            else:
                data['assembly_state'] = 'full'
        my_arrival = next(
            (
                intent for intent in capacity['arrivals']
                if intent.user_id == viewer_id
            ),
            None,
        )
        data['my_arrival'] = my_arrival.to_dict(now) if my_arrival else None
        member_ids = {player.user_id for player in game.players}
        if viewer_id in member_ids:
            data['arrivals'] = [
                _arrival_identity_payload(intent, now)
                for intent in capacity['arrivals']
            ]
        if game.status == 'upcoming' and not assembly_active:
            if len(game.players) >= 2:
                # Preserve score entry without telling clients that stale
                # roster rows are still waiting at the court.
                data['assembly_state'] = 'score_pending'
            else:
                data['assembly_state'] = 'closed'
    return data


INSTANT_NONMEMBER_GAME_FIELDS = frozenset({
    'id', 'court', 'scheduled_at', 'game_type', 'visibility', 'recurrence',
    'max_players', 'preferred_level', 'is_instant', 'assembly_state',
    'assembly_active', 'can_enter_score', 'status', 'ready_count',
    'roster_count', 'on_the_way_count', 'committed_count', 'physical_spots_left',
    'spots_left', 'my_arrival', 'is_joined',
})


def _instant_nonmember_game_payload(data):
    """Allowlist the aggregate live-rally fields safe for a nonparticipant."""
    sanitized = {
        key: value for key, value in data.items()
        if key in INSTANT_NONMEMBER_GAME_FIELDS
    }
    # Preserve a stable empty collection for existing game-sheet rendering,
    # without exposing creator, score, roster, MVP, notes, Club, or Crew data.
    sanitized['players'] = []
    return sanitized


def _instant_rally_is_actionable(game, now=None):
    """Whether an underfilled instant roster is still a real live signal."""
    now = now or utcnow()
    if (
        not game
        or not game.is_instant
        or game.status != 'upcoming'
    ):
        return False
    return bool(
        _instant_rally_assembly_active(game, now)
        and _arrival_capacity(game, now)['spots_left'] > 0
    )


def _resolve_instant_rally_replay_presence(game, user_id, now=None):
    """Clear a reasserted LFG flag on a response-lost create retry.

    The resource replay remains valid without presence. When a fresh same-court
    row does exist, however, the original successful membership is still the
    authoritative intent and must win over a client's repeated check-in step.
    """
    if not _instant_rally_assembly_active(game, now):
        return
    now = now or utcnow()
    user = (
        User.query.filter(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return
    checkin = active_checkin_for(
        user_id, fresh=True, now=now, for_update=True,
    )
    if (
        checkin
        and checkin.court_id == game.court_id
        and checkin.looking_for_game
    ):
        checkin.looking_for_game = False
        checkin.last_presence_ping_at = now
    _end_active_play_pulse_for_user(user_id, 'instant_rally', now)
    db.session.commit()


def _closed_rally_replay_response(game, now=None):
    """Retire a stale exact attempt and tell the client to mint a new key."""
    now = now or utcnow()
    stale_upcoming = (
        game.status == 'upcoming'
        and (
            game.assembly_closed_at is not None
            or game.scheduled_at < now - timedelta(
                minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
            )
        )
    )
    if game.is_instant and (
        stale_upcoming or game.status in ('expired', 'cancelled')
    ):
        if stale_upcoming:
            if game.assembly_closed_at is None:
                game.assembly_closed_at = now
            if len(game.players) <= 1:
                game.status = 'expired'
        _end_game_arrivals(game, 'rally_closed', now)
        db.session.commit()
        return jsonify({
            'error': 'rally_no_longer_active',
            'game_id': game.id,
            'game': _game_payload(game, game.creator_id, now=now),
            'retry_with_new_attempt': True,
        }), 409
    return None


def auto_confirm_stale_scores():
    """Finalize ranked scores that opponents never confirmed within the window."""
    cutoff = utcnow() - timedelta(hours=SCORE_AUTO_CONFIRM_HOURS)
    stale = Game.query.filter(
        Game.status == 'awaiting_confirmation',
        Game.score_submitted_at < cutoff,
    ).order_by(Game.id.asc()).with_for_update().execution_options(
        populate_existing=True,
    ).all()
    for game in stale:
        _finalize_game(game)
    if stale:
        db.session.commit()


def expire_stale_unscored():
    """Games that started over a week ago and never got a score stop haunting
    the feeds and the live banner. Weekly sessions roll forward instead."""
    cutoff = utcnow() - timedelta(days=UNSCORED_EXPIRY_DAYS)
    stale = Game.query.filter(
        Game.status == 'upcoming',
        Game.recurrence != 'weekly',
        Game.scheduled_at < cutoff,
    ).order_by(Game.id.asc()).with_for_update().execution_options(
        populate_existing=True,
    ).all()
    for game in stale:
        game.status = 'expired'
        _end_game_arrivals(game, 'rally_expired')
        _end_game_open_calls(game, 'expired')
    if stale:
        db.session.commit()


def expire_abandoned_instant_rallies(now=None):
    """One-way close instant assembly after absence or the 90-minute cap.

    Solo shells are expired as soon as their one-way closure is observed.
    Multi-player games remain upcoming for score entry but
    ``assembly_closed_at`` prevents later presence from resurrecting them
    beside a replacement rally.
    """
    now = now or utcnow()
    cutoff = now - timedelta(minutes=INSTANT_RALLY_ASSEMBLY_MINUTES)
    player_count = (
        db.session.query(db.func.count(GamePlayer.id))
        .filter(GamePlayer.game_id == Game.id)
        .correlate(Game)
        .scalar_subquery()
    )
    candidates = Game.query.filter(
        Game.is_instant.is_(True),
        Game.status == 'upcoming',
        Game.scheduled_at <= now + timedelta(minutes=15),
        or_(
            Game.assembly_closed_at.is_(None),
            player_count <= 1,
        ),
    ).order_by(Game.id.asc()).with_for_update().execution_options(
        populate_existing=True,
    ).all()
    for game in candidates:
        db.session.expire(game, ['players'])
    expired = []
    for game in candidates:
        too_old = game.scheduled_at < cutoff
        has_presence = _instant_rally_has_fresh_member(
            game, now, for_update=True,
        )
        already_closed = game.assembly_closed_at is not None
        if not already_closed and not too_old and has_presence:
            continue
        game.assembly_closed_at = now
        if len(game.players) <= 1:
            game.status = 'expired'
            expired.append(game)
        _end_game_arrivals(game, 'rally_closed', now)
    # Release Game/CheckIn locks before callers acquire Court/User locks.
    if candidates:
        db.session.commit()
    return expired


def send_game_reminders():
    """Notify each player about an hour before their game starts. Lazy sweep
    (like auto_confirm_stale_scores) — runs on feed/me reads; reminded_at on
    game_player guarantees at most one reminder per player per occurrence."""
    now = utcnow()
    due = Game.query.filter(
        Game.status == 'upcoming',
        Game.is_instant.is_(False),
        Game.scheduled_at > now,
        Game.scheduled_at <= now + timedelta(minutes=REMINDER_LEAD_MINUTES),
    ).all()
    changed = False
    for game in due:
        court_name = game.court.name if game.court else 'the court'
        for player in game.players:
            if player.reminded_at is not None:
                continue
            body = (
                f'{len(game.players)} signed up — don’t forget your paddle! \U0001F3BE'
                if player.attending_at is not None
                # Unconfirmed players get nudged to vouch, not just reminded.
                else f'{len(game.players)} signed up — tap to confirm you’re coming \U0001F44B'
            )
            notify(
                player.user_id,
                'game_reminder',
                f'Game at {court_name} in about an hour',
                body,
                related_game_id=game.id,
            )
            player.reminded_at = now
            changed = True

    # Day-before nudge for games ~20–28h out (plan-ahead reminder), once each.
    day_due = Game.query.filter(
        Game.status == 'upcoming',
        Game.scheduled_at > now + timedelta(hours=20),
        Game.scheduled_at <= now + timedelta(hours=28),
    ).all()
    for game in day_due:
        court_name = game.court.name if game.court else 'the court'
        for player in game.players:
            if player.day_reminded_at is not None:
                continue
            notify(
                player.user_id,
                'game_reminder',
                f'Game tomorrow at {court_name} — see you on the court! \U0001F3BE',
                related_game_id=game.id,
            )
            player.day_reminded_at = now
            changed = True

    if changed:
        db.session.commit()


def roll_forward_recurring():
    """Advance weekly open-play sessions to their next occurrence once the last
    one is ~3h past, resetting the RSVP list to just the host (re-RSVP weekly)."""
    cutoff = utcnow() - timedelta(hours=3)
    due = Game.query.filter(
        Game.recurrence == 'weekly',
        Game.status == 'upcoming',
        Game.scheduled_at < cutoff,
    ).all()
    changed = False
    now = utcnow()
    for game in due:
        nxt = game.scheduled_at
        while nxt < now:
            nxt += timedelta(days=7)
        game.scheduled_at = nxt
        # Reset attendees to the host for the new week, nudging last week's
        # players to grab their spot again.
        weekday = nxt.strftime('%A')
        court_name = game.court.name if game.court else 'the court'
        for player in list(game.players):
            if player.user_id != game.creator_id:
                game.players.remove(player)
                notify(
                    player.user_id,
                    'session_rsvp',
                    f'{weekday} open play at {court_name} is back — RSVP again?',
                    related_game_id=game.id,
                )
            else:
                player.reminded_at = None      # remind again for the new occurrence
                player.day_reminded_at = None
                player.attending_at = None     # and re-confirm attendance
        changed = True
    if changed:
        db.session.commit()


def _prepare_game_feeds():
    """Run the lazy maintenance shared by every upcoming-games payload."""
    auto_confirm_stale_scores()
    roll_forward_recurring()
    expire_abandoned_instant_rallies()
    expire_stale_unscored()
    send_game_reminders()


def my_games_payload(user, lat=None, lng=None):
    """Endpoint-shaped upcoming/awaiting games for one player's Profile."""
    _prepare_game_feeds()
    games = (
        Game.query.filter(Game.status.in_(['upcoming', 'awaiting_confirmation']))
        .join(GamePlayer)
        .filter(GamePlayer.user_id == user.id)
        .order_by(Game.scheduled_at.asc())
        .limit(150)
        .all()
    )
    items = []
    for game in games:
        item = _game_payload(game, user.id)
        court = game.court
        if lat is not None and lng is not None and court and court.latitude is not None:
            item['distance_miles'] = round(
                haversine_miles(lat, lng, court.latitude, court.longitude), 1,
            )
        items.append(item)
    items = items[:100]
    unread = _chat_unread_for(user.id, [item['id'] for item in items])
    for item in items:
        item['chat_unread'] = unread.get(item['id'], 0)
    return {'items': items}


def _game_has_blocked_participant(game, viewer_id, hidden_ids=None):
    """Hide an unjoined game when any current player is across a block.

    Existing participants retain the sheet so they can leave/cancel and handle
    an already-made commitment; a blocked pair can never newly discover or join
    one another's game.
    """
    if not viewer_id:
        return False
    player_ids = {player.user_id for player in game.players}
    if viewer_id in player_ids:
        return False
    hidden_ids = hidden_ids if hidden_ids is not None else blocked_pair_ids(viewer_id)
    return bool(player_ids & set(hidden_ids))


def _instant_game_discovery_allowed(game, current_user, viewer_friends=None):
    """Whether a live instant game's exact court may be disclosed here.

    Open-rally discovery belongs to the coordinate-bounded /players/looking
    summary. General game/court endpoints expose an instant resource only to
    a participant, a participant's accepted friend, or somebody with fresh
    physical presence at that exact court. Anonymous and unrelated remote
    callers must not turn an open game into a live-location lookup.
    """
    if not game.is_instant or game.status == 'completed':
        return True
    if not current_user:
        return False
    player_ids = {player.user_id for player in game.players}
    if current_user.id in player_ids:
        return True
    # A live arrival reservation is a viewer-scoped capability in durable
    # form. It grants its holder detail access after the short discovery token
    # expires, but disappears with the reservation or assembly lifecycle.
    if _active_arrival_for_user(game, current_user.id):
        return True
    if not _instant_rally_assembly_active(game):
        # Participants retain score access; a stale/cancelled/expired roster
        # is no longer discoverable by friends or a later same-court check-in.
        return False
    viewer_friends = (
        set(viewer_friends)
        if viewer_friends is not None else friend_ids(current_user.id)
    )
    if player_ids & viewer_friends:
        return True
    checkin = active_checkin_for(current_user.id, fresh=True)
    return bool(checkin and checkin.court_id == game.court_id)


def _discovery_game_payload(game, current_user, viewer_friends=None):
    """Serialize a discoverable game without leaking an instant roster."""
    viewer_id = current_user.id if current_user else None
    data = _game_payload(game, viewer_id)
    if game.is_instant and game.status != 'completed':
        if not data['is_joined']:
            data = _instant_nonmember_game_payload(data)
    return data


@games_bp.get('/games')
def list_games():
    """Upcoming games feed, optionally sorted by distance from lat/lng."""
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    truthy = {'1', 'true', 'yes'}
    mine = str(request.args.get('mine') or '').strip() in truthy
    friends_only = str(request.args.get('friends') or '').strip() in truthy
    current_user = optional_current_user()
    viewer_id = current_user.id if current_user else None
    viewer_friends = friend_ids(viewer_id) if viewer_id else set()
    viewer_hidden = blocked_pair_ids(viewer_id) if viewer_id else set()

    if mine:
        if not current_user:
            return jsonify({'error': 'authentication_required'}), 401
        return jsonify(my_games_payload(current_user, lat, lng))

    _prepare_game_feeds()
    if friends_only:
        if not current_user:
            return jsonify({'error': 'authentication_required'}), 401
        if not viewer_friends:
            return jsonify({'items': []})
        # Upcoming games a friend created or joined (creator is always a player).
        query = (
            Game.query.filter(
                Game.scheduled_at >= utcnow() - timedelta(hours=2),
                Game.status == 'upcoming',
            )
            .join(GamePlayer)
            .filter(GamePlayer.user_id.in_(viewer_friends))
            .distinct()
        )
    else:
        query = Game.query.filter(
            Game.scheduled_at >= utcnow() - timedelta(hours=2),
            Game.status == 'upcoming',
        )
    if not friends_only and lat is not None and lng is not None:
        radius = min(max(request.args.get('radius', default=50.0, type=float), 1.0), 200.0)
        lat_delta = radius / 69.0
        lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
        query = query.join(Court).filter(
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )

    games = query.order_by(Game.scheduled_at.asc()).limit(150).all()

    items = []
    for game in games:
        # In the public/nearby and friends feeds, only show games the viewer may see.
        if not game.visible_to(viewer_id, viewer_friends) \
                or _game_has_blocked_participant(game, viewer_id, viewer_hidden) \
                or not _instant_game_discovery_allowed(
                    game, current_user, viewer_friends,
                ):
            continue
        item = _discovery_game_payload(game, current_user, viewer_friends)
        # The Friends feed is about discovering games you're not already in.
        if friends_only and item['is_joined']:
            continue
        court = game.court
        if lat is not None and lng is not None and court and court.latitude is not None:
            item['distance_miles'] = round(
                haversine_miles(lat, lng, court.latitude, court.longitude), 1,
            )
        items.append(item)
    if lat is not None and lng is not None and not friends_only:
        items.sort(key=lambda i: (i.get('distance_miles', 1e9), i['scheduled_at']))
    items = items[:100]
    return jsonify({'items': items})


@games_bp.get('/games/history')
@login_required
def my_game_history():
    return jsonify(game_history_payload(g.current_user))


def game_history_payload(user):
    """Endpoint-shaped completed-game history for one player."""
    games = (
        Game.query.join(GamePlayer)
        .filter(GamePlayer.user_id == user.id, Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(50)
        .all()
    )
    return {'items': [game.to_dict(user.id) for game in games]}


@games_bp.post('/games/log')
@rate_limit(20, 60)
@login_required
def log_past_game():
    """Record a spontaneous pickup game that never went through scheduling.
    Casual only — no rating impact — but counts for stats, court records,
    and history. Only the logger's own participation is asserted; the other
    players are just credited (they can dispute via support if needed)."""
    payload = request.get_json(silent=True) or {}
    court = db.session.get(Court, int(payload.get('court_id') or 0))
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    try:
        score1 = int(payload.get('score_team1'))
        score2 = int(payload.get('score_team2'))
    except (TypeError, ValueError):
        return jsonify({'error': 'scores_required'}), 400
    if score1 == score2 or score1 < 0 or score2 < 0 or max(score1, score2) > 99:
        return jsonify({'error': 'invalid_scores'}), 400

    def clean_team(raw):
        ids = []
        for v in (raw or [])[:2]:
            try:
                uid = int(v)
            except (TypeError, ValueError):
                continue
            if uid not in ids and db.session.get(User, uid) and not db.session.get(User, uid).deleted_at:
                ids.append(uid)
        return ids

    team1 = clean_team(payload.get('team1'))
    team2 = clean_team(payload.get('team2'))
    if not team1 or not team2:
        return jsonify({'error': 'teams_required'}), 400
    if set(team1) & set(team2):
        return jsonify({'error': 'player_on_both_teams'}), 400
    if g.current_user.id not in set(team1) | set(team2):
        return jsonify({'error': 'must_include_self'}), 400
    if len(team1) != len(team2):
        return jsonify({'error': 'uneven_teams'}), 400
    # You can only log games with your friends (and never with someone who
    # blocked you) — otherwise anyone could pin fake results on strangers.
    my_friends = friend_ids(g.current_user.id)
    for uid in (set(team1) | set(team2)) - {g.current_user.id}:
        if uid not in my_friends or is_blocked_between(g.current_user.id, uid):
            return jsonify({'error': 'players_must_be_friends'}), 403

    when = _parse_scheduled_at(payload.get('played_at')) or utcnow()
    if when > utcnow() + timedelta(minutes=5):
        return jsonify({'error': 'not_in_past'}), 400

    game = Game(
        court_id=court.id,
        creator_id=g.current_user.id,
        scheduled_at=when,
        game_type='casual',
        visibility='private',
        max_players=len(team1) + len(team2),
        status='completed',
        score_team1=score1,
        score_team2=score2,
        score_submitted_by_id=g.current_user.id,
        score_submitted_at=utcnow(),
        completed_at=when,
    )
    db.session.add(game)
    db.session.flush()
    for uid in team1:
        db.session.add(GamePlayer(game_id=game.id, user_id=uid, team=1))
    for uid in team2:
        db.session.add(GamePlayer(game_id=game.id, user_id=uid, team=2))

    # Transparency: tell the other players they were logged into this result,
    # so a silent/incorrect log doesn't go unnoticed.
    score_text = f'{score1}–{score2}'
    for uid in set(team1) | set(team2):
        if uid == g.current_user.id:
            continue
        notify(
            uid,
            'game_logged',
            f'{g.current_user.display_name} logged a game with you — {score_text} at {court.name}',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
        )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id)), 201


def _positive_int(raw):
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw if raw > 0 else 0
    if isinstance(raw, str) and raw.strip().isdigit():
        parsed = int(raw.strip())
        return parsed if parsed > 0 else 0
    return 0


def _lock_play_pulse_game_scope(user_ids, extra_game_ids=()):
    """Lock the complete Game scope before presence/arrival/pulse rows."""
    ids = sorted({int(user_id) for user_id in user_ids})
    game_ids = {int(game_id) for game_id in extra_game_ids if game_id}
    if ids:
        scope_now = utcnow()
        game_ids.update(
            row[0] for row in db.session.query(Game.id).join(GamePlayer).filter(
                GamePlayer.user_id.in_(ids),
                Game.status == 'upcoming',
                or_(
                    Game.is_instant.is_(True),
                    Game.scheduled_at.between(
                        scope_now - timedelta(minutes=15),
                        scope_now + timedelta(minutes=75),
                    ),
                ),
            ).all()
        )
        game_ids.update(
            row[0] for row in db.session.query(GameArrivalIntent.game_id).filter(
                GameArrivalIntent.user_id.in_(ids),
                GameArrivalIntent.active.is_(True),
            ).all()
        )
    games = (
        Game.query.filter(Game.id.in_(sorted(game_ids)))
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    ) if game_ids else []
    for game in games:
        db.session.expire(game, ['players'])
    return games


def _lock_play_pulse_presence(user_ids):
    """Lock CheckIns before arrival and pulse rows in canonical order."""
    ids = sorted({int(user_id) for user_id in user_ids})
    checkins = (
        CheckIn.query.filter(
            CheckIn.user_id.in_(ids),
            CheckIn.checked_out_at.is_(None),
        )
        .order_by(CheckIn.user_id.asc(), CheckIn.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    ) if ids else []
    arrivals = (
        GameArrivalIntent.query.filter(
            GameArrivalIntent.user_id.in_(ids),
            GameArrivalIntent.active.is_(True),
        )
        .order_by(GameArrivalIntent.user_id.asc(), GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    ) if ids else []
    return checkins, arrivals


def _fresh_locked_checkin(checkins, user_id, now):
    stale_cutoff = presence_stale_cutoff(now)
    absolute_cutoff = presence_absolute_cutoff(now)
    return next(
        (
            row for row in checkins
            if row.user_id == user_id
            and row.checked_out_at is None
            and row.checked_in_at
            and row.checked_in_at >= absolute_cutoff
            and row.last_presence_ping_at
            and row.last_presence_ping_at >= stale_cutoff
        ),
        None,
    )


def _live_locked_arrival(arrivals, user_id, games_by_id, now):
    """Return a usable arrival and retire expired/closed active ledgers."""
    live = None
    changed = False
    for intent in arrivals:
        if intent.user_id != user_id:
            continue
        game = games_by_id.get(intent.game_id)
        if not _arrival_time_active(intent, now):
            changed = _end_arrival_intent(intent, 'expired', now) or changed
        elif not _instant_rally_assembly_active(game, now):
            changed = _end_arrival_intent(intent, 'rally_closed', now) or changed
        elif live is None:
            live = intent
    if changed:
        db.session.flush()
    return live, changed


def _play_pulse_response(pulse, outcome, *, game=None, status=200, now=None):
    body = {'outcome': outcome, 'pulse': _pulse_payload(pulse, now)}
    if game is not None:
        body['game'] = _game_payload(game, g.current_user.id, now=now)
    return jsonify(body), status


@games_bp.put('/play/pulse')
@rate_limit(20, 3600)
@login_required
def publish_play_pulse():
    """Publish a server-timed, one-hour remote availability signal."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    court_id = _positive_int(payload.get('court_id'))
    if not court_id:
        return jsonify({'error': 'invalid_court_id'}), 400
    client_attempt_id, valid_attempt_id = _client_attempt_id(payload)
    if not valid_attempt_id or not client_attempt_id:
        return jsonify({'error': 'invalid_client_attempt_id'}), 400
    fingerprint = _play_pulse_attempt_fingerprint(court_id)
    now = utcnow()

    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    locked_games = _lock_play_pulse_game_scope([user.id])
    games_by_id = {game.id: game for game in locked_games}
    checkins, arrivals = _lock_play_pulse_presence([user.id])
    pulse_rows = (
        PlayAvailabilityPulse.query.filter_by(user_id=user.id)
        .order_by(PlayAvailabilityPulse.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )

    existing_attempt = next(
        (row for row in pulse_rows if row.client_attempt_id == client_attempt_id),
        None,
    )
    if existing_attempt:
        if existing_attempt.client_attempt_fingerprint != fingerprint:
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        return _play_pulse_response(existing_attempt, 'existing', now=now)

    active = None
    for pulse in pulse_rows:
        if _play_pulse_time_active(pulse, now) and active is None:
            active = pulse
        elif pulse.active:
            _end_play_pulse(pulse, 'expired', now)
    db.session.flush()

    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    if court.closed:
        return jsonify({'error': 'court_closed'}), 409
    if court.latitude is None or court.longitude is None:
        return jsonify({'error': 'court_location_unavailable'}), 409
    if _fresh_locked_checkin(checkins, user.id, now):
        return jsonify({'error': 'active_checkin_present'}), 409
    live_arrival, changed = _live_locked_arrival(
        arrivals, user.id, games_by_id, now,
    )
    if live_arrival:
        if changed:
            db.session.commit()
        return jsonify({'error': 'active_arrival'}), 409
    if _active_live_rally_for_user(user.id, now, locked_games):
        if changed:
            db.session.commit()
        return jsonify({'error': 'active_rally'}), 409
    if _active_immediate_game_for_user(
        user.id,
        locked_games,
        now,
        now + timedelta(minutes=PLAY_PULSE_MINUTES),
    ):
        if changed:
            db.session.commit()
        return jsonify({'error': 'active_game'}), 409
    if active:
        if changed:
            db.session.commit()
        return jsonify({
            'error': 'pulse_already_active',
            'pulse': _pulse_payload(active, now),
        }), 409

    pulse = PlayAvailabilityPulse(
        user_id=user.id,
        court_id=court.id,
        declared_at=now,
        expires_at=now + timedelta(minutes=PLAY_PULSE_MINUTES),
        active=True,
        client_attempt_id=client_attempt_id,
        client_attempt_fingerprint=fingerprint,
    )
    db.session.add(pulse)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        recovered = PlayAvailabilityPulse.query.filter_by(
            user_id=user.id, client_attempt_id=client_attempt_id,
        ).first()
        if recovered and recovered.client_attempt_fingerprint == fingerprint:
            return _play_pulse_response(recovered, 'existing')
        if recovered:
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        return jsonify({'error': 'pulse_conflict', 'retryable': True}), 409
    return _play_pulse_response(pulse, 'created', status=201, now=now)


@games_bp.delete('/play/pulses/<int:pulse_id>')
@rate_limit(30, 3600)
@login_required
def cancel_play_pulse(pulse_id):
    """Targeted owner-only cancellation; unknown IDs remain non-oracular."""
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    probe = PlayAvailabilityPulse.query.filter_by(
        id=pulse_id, user_id=user.id,
    ).first()
    if not probe:
        return jsonify({'error': 'pulse_not_found'}), 404
    _lock_play_pulse_game_scope(
        [user.id], [probe.accepted_game_id] if probe.accepted_game_id else [],
    )
    pulse = (
        PlayAvailabilityPulse.query.filter_by(id=pulse_id, user_id=user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not pulse:
        return jsonify({'error': 'pulse_not_found'}), 404
    now = utcnow()
    if pulse.active:
        _end_play_pulse(
            pulse,
            'cancelled' if _play_pulse_time_active(pulse, now) else 'expired',
            now,
        )
        db.session.commit()
    cancelled = pulse.end_reason == 'cancelled'
    return jsonify({
        # A delayed tap can lose to acceptance. Never tell the owner that the
        # availability was cancelled when its ordinary game already exists.
        'cancelled': cancelled,
        'outcome': 'cancelled' if cancelled else 'already_ended',
        'pulse': _pulse_payload(pulse, now),
    })


def _play_pulse_accept_existing(game, pulse, fingerprint, attempt_id, now):
    if game.client_attempt_fingerprint != fingerprint:
        return jsonify({'error': 'client_attempt_id_conflict'}), 409
    if pulse is not None and not (
        pulse.accepted_by_id == g.current_user.id
        and pulse.accept_client_attempt_id == attempt_id
        and pulse.accept_client_attempt_fingerprint == fingerprint
        and pulse.accepted_game_id == game.id
    ):
        return jsonify({'error': 'client_attempt_id_conflict'}), 409
    return _play_pulse_response(
        pulse, 'existing', game=game, now=now,
    )


@games_bp.post('/play/pulses/<int:pulse_id>/accept')
@rate_limit(20, 3600)
@login_required
def accept_play_pulse(pulse_id):
    """Match a discovered pulse into one ordinary two-player quick game."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    client_attempt_id, valid_attempt_id = _client_attempt_id(payload)
    if not valid_attempt_id or not client_attempt_id:
        return jsonify({'error': 'invalid_client_attempt_id'}), 400
    capability = payload.get('accept_capability')
    fingerprint = _play_pulse_accept_fingerprint(pulse_id)
    now = utcnow()
    actor_id = g.current_user.id

    pulse_probe = PlayAvailabilityPulse.query.filter_by(id=pulse_id).first()
    game_probe = Game.query.filter_by(
        creator_id=actor_id, client_attempt_id=client_attempt_id,
    ).first()
    if not pulse_probe and not game_probe:
        return jsonify({'error': 'pulse_not_found'}), 404
    user_ids = {actor_id}
    if pulse_probe:
        user_ids.add(pulse_probe.user_id)
    locked_users = (
        User.query.filter(User.id.in_(sorted(user_ids)))
        .order_by(User.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    users_by_id = {user.id: user for user in locked_users}
    actor = users_by_id.get(actor_id)
    if not actor or actor.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = actor

    extra_game_ids = []
    if game_probe:
        extra_game_ids.append(game_probe.id)
    if pulse_probe and pulse_probe.accepted_game_id:
        extra_game_ids.append(pulse_probe.accepted_game_id)
    locked_games = _lock_play_pulse_game_scope(user_ids, extra_game_ids)
    games_by_id = {game.id: game for game in locked_games}
    checkins, arrivals = _lock_play_pulse_presence(user_ids)
    pulse_rows = (
        PlayAvailabilityPulse.query.filter(or_(
            PlayAvailabilityPulse.id == pulse_id,
            (
                (PlayAvailabilityPulse.user_id == actor_id)
                & PlayAvailabilityPulse.active.is_(True)
            ),
        ))
        .order_by(PlayAvailabilityPulse.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    pulse = next((row for row in pulse_rows if row.id == pulse_id), None)
    existing_game = next(
        (
            game for game in locked_games
            if game.creator_id == actor_id
            and game.client_attempt_id == client_attempt_id
        ),
        None,
    )
    if existing_game:
        return _play_pulse_accept_existing(
            existing_game, pulse, fingerprint, client_attempt_id, now,
        )
    if not pulse:
        return jsonify({'error': 'pulse_not_found'}), 404

    owner = users_by_id.get(pulse.user_id)
    if (
        not owner
        or owner.deleted_at
        or owner.id == actor.id
        or not _play_pulse_time_active(pulse, now)
        or is_blocked_between(owner.id, actor.id)
        or not _valid_play_pulse_accept_capability(
            capability, actor.id, pulse.id, pulse.court_id,
        )
    ):
        if pulse.active and not _play_pulse_time_active(pulse, now):
            _end_play_pulse(pulse, 'expired', now)
            db.session.commit()
        return jsonify({'error': 'pulse_not_found'}), 404
    court = pulse.court
    if (
        not court
        or court.closed
        or court.latitude is None
        or court.longitude is None
    ):
        _end_play_pulse(pulse, 'court_unavailable', now)
        db.session.commit()
        return jsonify({'error': 'pulse_not_found'}), 404
    owner_arrival, owner_arrival_changed = _live_locked_arrival(
        arrivals, owner.id, games_by_id, now,
    )
    owner_rally = _active_live_rally_for_user(
        owner.id, now, locked_games,
    )
    owner_game = _active_immediate_game_for_user(
        owner.id, locked_games, pulse.declared_at, pulse.expires_at,
    )
    if (
        _fresh_locked_checkin(checkins, owner.id, now)
        or owner_arrival
        or owner_rally
        or owner_game
    ):
        _end_play_pulse(pulse, 'availability_changed', now)
        db.session.commit()
        return jsonify({'error': 'pulse_not_found'}), 404

    actor_arrival, actor_arrival_changed = _live_locked_arrival(
        arrivals, actor.id, games_by_id, now,
    )
    if _fresh_locked_checkin(checkins, actor.id, now):
        if owner_arrival_changed or actor_arrival_changed:
            db.session.commit()
        return jsonify({'error': 'active_checkin_present'}), 409
    if actor_arrival:
        if owner_arrival_changed or actor_arrival_changed:
            db.session.commit()
        return jsonify({'error': 'active_arrival'}), 409
    if _active_live_rally_for_user(actor.id, now, locked_games):
        if owner_arrival_changed or actor_arrival_changed:
            db.session.commit()
        return jsonify({'error': 'active_rally'}), 409
    if _active_immediate_game_for_user(
        actor.id, locked_games, now, pulse.expires_at,
    ):
        if owner_arrival_changed or actor_arrival_changed:
            db.session.commit()
        return jsonify({'error': 'active_game'}), 409

    game = Game(
        court_id=court.id,
        creator_id=actor.id,
        client_attempt_id=client_attempt_id,
        client_attempt_fingerprint=fingerprint,
        scheduled_at=now + timedelta(minutes=PLAY_PULSE_START_LEAD_MINUTES),
        game_type='casual',
        visibility='open',
        recurrence='none',
        max_players=4,
        preferred_level='any',
        notes='Available this hour',
        is_instant=False,
    )
    db.session.add(game)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        recovered_game = Game.query.filter_by(
            creator_id=actor.id, client_attempt_id=client_attempt_id,
        ).first()
        recovered_pulse = PlayAvailabilityPulse.query.filter_by(
            id=pulse_id,
        ).first()
        if recovered_game:
            return _play_pulse_accept_existing(
                recovered_game, recovered_pulse, fingerprint,
                client_attempt_id, utcnow(),
            )
        return jsonify({'error': 'pulse_not_found'}), 409
    db.session.add_all([
        GamePlayer(game=game, user_id=owner.id, attending_at=now),
        GamePlayer(game=game, user_id=actor.id, attending_at=now),
    ])
    _notify_saved_court_fans(
        game,
        actor,
        'game',
        excluded_user_ids={owner.id, actor.id},
    )
    _end_play_pulse(pulse, 'matched', now)
    pulse.accepted_by_id = actor.id
    pulse.accept_client_attempt_id = client_attempt_id
    pulse.accept_client_attempt_fingerprint = fingerprint
    pulse.accepted_game_id = game.id
    for actor_pulse in pulse_rows:
        if actor_pulse.user_id == actor.id and actor_pulse.id != pulse.id:
            _end_play_pulse(actor_pulse, 'matched', now)
    notify(
        owner.id,
        'game_join',
        f'{actor.display_name} accepted your availability pulse',
        'Your quick game starts in about 15 minutes.',
        related_user_id=actor.id,
        related_game_id=game.id,
        action_url=f'/#game/{game.id}',
        unread_dedupe_key=f'play-pulse-match:{pulse.id}',
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        recovered_game = Game.query.filter_by(
            creator_id=actor.id, client_attempt_id=client_attempt_id,
        ).first()
        recovered_pulse = PlayAvailabilityPulse.query.filter_by(
            id=pulse_id,
        ).first()
        if recovered_game:
            return _play_pulse_accept_existing(
                recovered_game, recovered_pulse, fingerprint,
                client_attempt_id, utcnow(),
            )
        return jsonify({'error': 'pulse_not_found'}), 409
    return _play_pulse_response(
        pulse, 'created', game=game, status=201, now=now,
    )


@games_bp.post('/games/rally')
@rate_limit(12, 60)
@login_required
def start_instant_rally():
    """Join or launch a live pickup game at the player's checked-in court.

    The court row is locked while choosing an existing rally versus creating
    one, so two players tapping at nearly the same time converge on one game.
    """
    payload = request.get_json(silent=True)
    if payload is None:
        if request.get_data(cache=True).strip():
            return jsonify({'error': 'invalid_payload'}), 400
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400

    client_attempt_id, valid_attempt_id = _client_attempt_id(payload)
    if not valid_attempt_id or not client_attempt_id:
        return jsonify({'error': 'invalid_client_attempt_id'}), 400
    (
        expected_court_id,
        scheduled_at,
        attempt_fingerprint,
        legacy_attempt_fingerprint,
    ) = _rally_attempt(payload)
    if expected_court_id <= 0:
        return jsonify({'error': 'invalid_court_id'}), 400
    if not scheduled_at:
        return jsonify({'error': 'invalid_scheduled_at'}), 400

    # Probe without a lock first. Only an actual replay enters the isolated
    # User -> Game lock path; a new attempt must not carry its actor User lock
    # into the later Court -> sorted-User closure (which would deadlock two
    # same-court starts with opposite user-id order).
    existing_attempt = Game.query.filter_by(
        creator_id=g.current_user.id,
        client_attempt_id=client_attempt_id,
    ).first()
    if existing_attempt:
        actor = (
            User.query.filter(User.id == g.current_user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        if not actor or actor.deleted_at:
            return jsonify({'error': 'authentication_required'}), 401
        g.current_user = actor
        existing_attempt = Game.query.filter_by(
            creator_id=actor.id,
            client_attempt_id=client_attempt_id,
        ).with_for_update().execution_options(populate_existing=True).first()
        if not existing_attempt:
            # Defensive only (games are not normally hard-deleted): release
            # the replay locks before entering the canonical new-start path.
            db.session.rollback()
            existing_attempt = None
    if existing_attempt:
        if not _rally_attempt_matches(
            existing_attempt,
            expected_court_id,
            attempt_fingerprint,
            legacy_attempt_fingerprint,
        ):
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        stale_response = _closed_rally_replay_response(existing_attempt)
        if stale_response:
            return stale_response
        _resolve_instant_rally_replay_presence(
            existing_attempt, actor.id,
        )
        return _rally_response(
            existing_attempt,
            'existing',
            actor.id,
            invited_count=GameInvite.query.filter_by(game_id=existing_attempt.id).count(),
        )

    now = utcnow()
    if scheduled_at < now - timedelta(minutes=10) or scheduled_at > now + timedelta(minutes=10):
        return jsonify({'error': 'rally_time_out_of_range'}), 400

    checkin = active_checkin_for(g.current_user.id, fresh=True, now=now)
    stale_cutoff = presence_stale_cutoff(now)
    absolute_cutoff = presence_absolute_cutoff(now)
    if not checkin:
        return jsonify({'error': 'active_checkin_required'}), 409
    if checkin.court_id != expected_court_id:
        return jsonify({
            'error': 'active_checkin_court_mismatch',
            'checked_in_court_id': checkin.court_id,
            'requested_court_id': expected_court_id,
        }), 409

    # Lazy cleanup is not the freshness boundary (queries below are), but it
    # keeps abandoned one-person rally rows from accumulating in Profile.
    # Preflight court validation above guarantees an already-switched request
    # returns without creating, joining, or closing a rally as a side effect.
    expire_abandoned_instant_rallies(now)

    # Serialize the discover-or-create decision per court on databases that
    # support row locks. SQLite safely ignores FOR UPDATE in local tests.
    court = (
        Court.query.filter(Court.id == expected_court_id)
        .with_for_update()
        .first()
    )
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    # Take a bounded snapshot before User locks. Check-in, block, and account
    # deletion mutations also lock their affected Users; locking this closure
    # in ascending order makes the later eligibility decision stable.
    preliminary_looking = (
        CheckIn.query.filter(
            CheckIn.court_id == court.id,
            CheckIn.checked_out_at.is_(None),
            CheckIn.looking_for_game.is_(True),
            CheckIn.checked_in_at >= absolute_cutoff,
            CheckIn.last_presence_ping_at >= stale_cutoff,
            CheckIn.user_id != g.current_user.id,
        )
        .order_by(CheckIn.last_presence_ping_at.desc(), CheckIn.id.desc())
        .limit(12)
        .all()
    )
    preliminary_games = (
        _instant_rally_candidates(court.id, now)
        .order_by(Game.scheduled_at.desc(), Game.id.desc())
        .limit(20)
        .all()
    )
    user_ids_to_lock = {g.current_user.id}
    user_ids_to_lock.update(row.user_id for row in preliminary_looking)
    user_ids_to_lock.update(
        player.user_id
        for candidate_game in preliminary_games
        for player in candidate_game.players
    )
    locked_users = (
        User.query.filter(User.id.in_(sorted(user_ids_to_lock)))
        .order_by(User.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    locked_by_id = {user.id: user for user in locked_users}
    actor = locked_by_id.get(g.current_user.id)
    if not actor or actor.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = actor

    # A concurrent same-key request may have passed the unlocked replay probe
    # before this request acquired the Court/User closure. Recheck before
    # locking actor presence: direct joins take Game -> member CheckIn, so the
    # reverse CheckIn -> Game order here would deadlock a retry with a join.
    raced_attempt = (
        Game.query.filter_by(
            creator_id=actor.id,
            client_attempt_id=client_attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if raced_attempt:
        if not _rally_attempt_matches(
            raced_attempt,
            expected_court_id,
            attempt_fingerprint,
            legacy_attempt_fingerprint,
        ):
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        stale_response = _closed_rally_replay_response(raced_attempt, now)
        if stale_response:
            return stale_response
        _resolve_instant_rally_replay_presence(raced_attempt, actor.id, now)
        return _rally_response(
            raced_attempt,
            'existing',
            actor.id,
            invited_count=GameInvite.query.filter_by(
                game_id=raced_attempt.id,
            ).count(),
        )

    # Re-read presence after the User lock. A checkout or court switch that
    # won while this request waited cannot create attendance at the old court.
    checkin = active_checkin_for(
        actor.id, fresh=True, now=now, for_update=True,
    )
    if not checkin:
        return jsonify({'error': 'active_checkin_required'}), 409
    if checkin.court_id != expected_court_id:
        return jsonify({
            'error': 'active_checkin_court_mismatch',
            'checked_in_court_id': checkin.court_id,
            'requested_court_id': expected_court_id,
        }), 409

    # One player cannot silently assemble two live rosters at different
    # courts. Exact create retries were handled above; this is the semantic
    # recovery path for a new button attempt or another device.
    actor_rallies = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == actor.id,
            Game.is_instant.is_(True),
            Game.status == 'upcoming',
        )
        .order_by(Game.scheduled_at.desc(), Game.id.desc())
        .execution_options(populate_existing=True)
        .all()
    )
    for actor_rally in actor_rallies:
        db.session.expire(actor_rally, ['players'])
    active_actor_rallies = [
        game for game in actor_rallies
        if _instant_rally_assembly_active(game, now)
    ]
    same_court_rally = next(
        (game for game in active_actor_rallies if game.court_id == court.id),
        None,
    )
    if same_court_rally:
        checkin.looking_for_game = False
        checkin.last_presence_ping_at = now
        _end_active_play_pulse_for_user(actor.id, 'instant_rally', now)
        db.session.commit()
        return _rally_response(
            same_court_rally, 'existing', actor.id,
            invited_count=GameInvite.query.filter_by(
                game_id=same_court_rally.id,
            ).count(),
        )
    if active_actor_rallies:
        elsewhere = active_actor_rallies[0]
        return jsonify({
            'error': 'active_rally_elsewhere',
            'game_id': elsewhere.id,
            'game': _game_payload(elsewhere, actor.id, now=now),
        }), 409

    # The actor User lock prevents a concurrent arrival declaration from
    # changing this probe while we establish a sorted Game lock closure. Include
    # a possibly remote reserved game even when it is at another court, so the
    # subsequent intent lock always follows its owning Game lock.
    reserved_arrival_probe = GameArrivalIntent.query.filter_by(
        user_id=actor.id, active=True,
    ).first()
    candidate_ids = {candidate.id for candidate in preliminary_games}
    game_ids_to_lock = set(candidate_ids)
    if reserved_arrival_probe:
        game_ids_to_lock.add(reserved_arrival_probe.game_id)
    locked_candidate_scope = (
        Game.query.filter(Game.id.in_(sorted(game_ids_to_lock)))
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    ) if game_ids_to_lock else []
    live_candidates = [
        game for game in locked_candidate_scope if game.id in candidate_ids
    ]
    # preliminary_games loaded these collections before the Game locks. Force
    # a post-lock roster read so a concurrent direct join that took the final
    # spot cannot be followed by this request appending a fifth player.
    for candidate_game in live_candidates:
        db.session.expire(candidate_game, ['players'])
    fresh_at_court_ids = {
        row[0]
        for row in db.session.query(CheckIn.user_id).filter(
            CheckIn.court_id == court.id,
            CheckIn.checked_out_at.is_(None),
            CheckIn.checked_in_at >= absolute_cutoff,
            CheckIn.last_presence_ping_at >= stale_cutoff,
        ).all()
    }
    # A database row is not proof that a real rally is still happening. At
    # least one current member must still have fresh presence at this court.
    live_candidates = [
        game for game in live_candidates
        if {player.user_id for player in game.players} & fresh_at_court_ids
    ]
    reserved_arrival = (
        GameArrivalIntent.query.filter_by(
            user_id=g.current_user.id, active=True,
        )
        .filter(
            GameArrivalIntent.ended_at.is_(None),
            GameArrivalIntent.expires_at > now,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if reserved_arrival:
        reserved_game = next(
            (
                candidate for candidate in live_candidates
                if candidate.id == reserved_arrival.game_id
            ),
            None,
        )
        if reserved_game:
            if len(reserved_game.players) >= reserved_game.max_players:
                _end_arrival_intent(
                    reserved_arrival, 'capacity_lost', now,
                )
                db.session.commit()
                return jsonify({'error': 'game_full'}), 400
            if any(
                is_blocked_between(g.current_user.id, player.user_id)
                for player in reserved_game.players
            ):
                _end_arrival_intent(reserved_arrival, 'blocked', now)
                db.session.commit()
                return jsonify({'error': 'game_not_found'}), 404
            reserved_game.players.append(
                GamePlayer(user_id=g.current_user.id),
            )
            _end_arrival_intent(reserved_arrival, 'arrived', now)
            checkin.looking_for_game = False
            checkin.last_presence_ping_at = now
            personal_invite = GameInvite.query.filter_by(
                game_id=reserved_game.id, user_id=g.current_user.id,
            ).first()
            if personal_invite:
                db.session.delete(personal_invite)
            if reserved_game.creator_id != g.current_user.id:
                notify(
                    reserved_game.creator_id,
                    'game_join',
                    f'{g.current_user.display_name} arrived and joined your live rally',
                    related_user_id=g.current_user.id,
                    related_game_id=reserved_game.id,
                    action_url=f'/#game/{reserved_game.id}',
                    unread_dedupe_key=(
                        f'rally-join:{reserved_game.id}:{g.current_user.id}'
                    ),
                )
            _end_active_play_pulse_for_user(
                g.current_user.id, 'instant_rally', now,
            )
            db.session.commit()
            return _rally_response(
                reserved_game, 'joined', g.current_user.id,
            )
        # A current remote promise is intentionally one-rally-at-a-time. It
        # must be cancelled or expire before Play Now can assemble elsewhere.
        return jsonify({
            'error': 'active_arrival_elsewhere',
            'game_id': reserved_arrival.game_id,
        }), 409
    # Membership wins before capacity or recency: a response-lost retry must
    # return the game this player already joined even if it filled meanwhile.
    for game in live_candidates:
        if any(player.user_id == g.current_user.id for player in game.players):
            checkin.looking_for_game = False
            checkin.last_presence_ping_at = now
            _end_active_play_pulse_for_user(
                g.current_user.id, 'instant_rally', now,
            )
            db.session.commit()
            return _rally_response(game, 'existing', g.current_user.id)

    for game in live_candidates:
        if _active_holder_blocks_user(
            game, g.current_user.id, now, for_update=True,
        ):
            continue
        if _arrival_capacity(game, now, for_update=True)['spots_left'] <= 0:
            continue
        # Blocking is mutual on social surfaces; do not silently assemble a
        # real-world game containing a player either side chose to avoid.
        if any(
            is_blocked_between(g.current_user.id, player.user_id)
            for player in game.players
            if player.user_id != g.current_user.id
        ):
            continue
        checkin.looking_for_game = False
        checkin.last_presence_ping_at = now
        game.players.append(GamePlayer(user_id=g.current_user.id))
        personal_invite = GameInvite.query.filter_by(
            game_id=game.id, user_id=g.current_user.id,
        ).first()
        if personal_invite:
            db.session.delete(personal_invite)
        if game.creator_id != g.current_user.id:
            notify(
                game.creator_id,
                'game_join',
                f'{g.current_user.display_name} joined your live rally',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
                action_url=f'/#game/{game.id}',
                unread_dedupe_key=f'rally-join:{game.id}:{g.current_user.id}',
            )
        _end_active_play_pulse_for_user(
            g.current_user.id, 'instant_rally', now,
        )
        db.session.commit()
        return _rally_response(game, 'joined', g.current_user.id)

    # No usable rally exists: launch one now, then pull in only players whose
    # fresh check-in at this exact court says they are actively looking.
    game = Game(
        court_id=court.id,
        creator_id=g.current_user.id,
        client_attempt_id=client_attempt_id,
        client_attempt_fingerprint=attempt_fingerprint,
        scheduled_at=scheduled_at,
        game_type='casual',
        visibility='open',
        recurrence='none',
        max_players=4,
        preferred_level='any',
        notes='⚡ Instant rally',
        is_instant=True,
    )
    db.session.add(game)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        actor = (
            User.query.filter(User.id == g.current_user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        existing_attempt = Game.query.filter_by(
            creator_id=g.current_user.id,
            client_attempt_id=client_attempt_id,
        ).with_for_update().execution_options(populate_existing=True).first()
        if existing_attempt:
            if not _rally_attempt_matches(
                existing_attempt,
                expected_court_id,
                attempt_fingerprint,
                legacy_attempt_fingerprint,
            ):
                return jsonify({'error': 'client_attempt_id_conflict'}), 409
            stale_response = _closed_rally_replay_response(existing_attempt)
            if stale_response:
                return stale_response
            _resolve_instant_rally_replay_presence(
                existing_attempt, g.current_user.id,
            )
            return _rally_response(existing_attempt, 'existing', g.current_user.id)
        raise

    game.players.append(GamePlayer(user_id=g.current_user.id))
    checkin.looking_for_game = False
    checkin.last_presence_ping_at = now
    eligible = (
        db.session.query(CheckIn, User)
        .join(User, User.id == CheckIn.user_id)
        .filter(
            CheckIn.court_id == court.id,
            CheckIn.checked_out_at.is_(None),
            CheckIn.looking_for_game.is_(True),
            CheckIn.checked_in_at >= absolute_cutoff,
            CheckIn.last_presence_ping_at >= stale_cutoff,
            CheckIn.user_id != g.current_user.id,
            CheckIn.user_id.in_(sorted(user_ids_to_lock)),
            User.deleted_at.is_(None),
        )
        .order_by(CheckIn.last_presence_ping_at.desc(), CheckIn.id.desc())
        .limit(12)
        .all()
    )
    live_player_ids = {
        player.user_id
        for candidate_game in live_candidates
        for player in candidate_game.players
    }
    invited_ids = set()
    for _candidate_checkin, candidate in eligible:
        if candidate.id in invited_ids:
            continue
        if candidate.id in live_player_ids:
            continue
        if is_blocked_between(g.current_user.id, candidate.id):
            continue
        invited_ids.add(candidate.id)
        db.session.add(GameInvite(game_id=game.id, user_id=candidate.id))
        notify(
            candidate.id,
            'game_invite_direct',
            f'{g.current_user.display_name} started a live rally at {court.name}',
            'Tap to join the game happening where you checked in.',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
            action_url=f'/#game/{game.id}',
            unread_dedupe_key=f'game-invite:{game.id}',
        )

    _end_active_play_pulse_for_user(
        g.current_user.id, 'instant_rally', now,
    )
    db.session.commit()
    return _rally_response(
        game,
        'created',
        g.current_user.id,
        invited_count=len(invited_ids),
        status=201,
    )


def _arrival_request_authorized(game, user, capability, active_intent=None):
    """Authorize without disclosing why a live rally was or was not found."""
    if not game or not game.is_instant or not user or user.deleted_at:
        return False
    member_ids = {player.user_id for player in game.players}
    if any(is_blocked_between(user.id, member_id) for member_id in member_ids):
        return False
    if user.id in member_ids:
        return True
    if active_intent and _arrival_time_active(active_intent):
        return True
    if GameInvite.query.filter_by(
        game_id=game.id, user_id=user.id,
    ).first() is not None:
        return True
    if member_ids & friend_ids(user.id):
        return True
    return _valid_rally_arrival_capability(
        capability, user.id, game.id, game.court_id,
    )


def _arrival_blocked_by_roster(game, user_id):
    return bool(
        game and any(
            is_blocked_between(user_id, player.user_id)
            for player in game.players
        )
    )


def _arrival_response(intent, outcome, game, user_id, now=None, status=200):
    now = now or utcnow()
    game_payload = _game_payload(game, user_id, now=now) if game else None
    if game_payload is not None and user_id not in {
        player.user_id for player in game.players
    }:
        game_payload = _instant_nonmember_game_payload(game_payload)
    return jsonify({
        'outcome': outcome,
        'arrival': intent.to_dict(now),
        'game': game_payload,
    }), status


def _recover_arrival_integrity_race(
    game_id, actor_id, client_attempt_id, fingerprint, capability, now,
):
    """Resolve a concurrent winner after rollback under canonical locks."""
    user = (
        User.query.filter(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    replay_probe = GameArrivalIntent.query.filter_by(
        user_id=actor_id, client_attempt_id=client_attempt_id,
    ).first()
    active_probe = GameArrivalIntent.query.filter_by(
        user_id=actor_id, active=True,
    ).first()
    membership_game_ids = {
        row[0] for row in db.session.query(Game.id).join(GamePlayer).filter(
            GamePlayer.user_id == actor_id,
            Game.is_instant.is_(True),
            Game.status == 'upcoming',
            Game.assembly_closed_at.is_(None),
            Game.scheduled_at >= now - timedelta(
                minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
            ),
            Game.scheduled_at <= now + timedelta(minutes=15),
        ).all()
    }
    game_ids = {game_id}
    game_ids.update(membership_game_ids)
    if replay_probe:
        game_ids.add(replay_probe.game_id)
    if active_probe:
        game_ids.add(active_probe.game_id)
    locked_games = (
        Game.query.filter(Game.id.in_(sorted(game_ids)))
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    games_by_id = {game.id: game for game in locked_games}
    replay = (
        GameArrivalIntent.query.filter_by(
            user_id=actor_id, client_attempt_id=client_attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if replay:
        if replay.game_id != game_id \
                or replay.client_attempt_fingerprint != fingerprint:
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        if not _arrival_time_active(replay, now):
            _end_arrival_intent(replay, 'expired', now)
            db.session.commit()
            return _arrival_response(replay, 'existing', None, actor_id, now)
        game = games_by_id.get(game_id)
        member_ids = {player.user_id for player in game.players} if game else set()
        if game and any(
            is_blocked_between(actor_id, member_id)
            for member_id in member_ids
        ):
            _end_arrival_intent(replay, 'blocked', now)
            db.session.commit()
            return jsonify({'error': 'game_not_found'}), 404
        if not _arrival_request_authorized(
            game, user, capability, replay,
        ):
            return jsonify({'error': 'game_not_found'}), 404
        if not _instant_rally_assembly_active(
            game, now, for_update=True,
        ):
            _end_arrival_intent(replay, 'rally_closed', now)
            db.session.commit()
            return _arrival_response(replay, 'existing', None, actor_id, now)
        return _arrival_response(replay, 'existing', game, actor_id, now)

    active_rally_elsewhere = next(
        (
            related_game for related_game in locked_games
            if related_game.id != game_id
            and related_game.id in membership_game_ids
            and any(
                player.user_id == actor_id for player in related_game.players
            )
            and _instant_rally_assembly_active(
                related_game, now, for_update=True,
            )
        ),
        None,
    )
    if active_rally_elsewhere:
        return jsonify({
            'error': 'active_rally_elsewhere',
            'game_id': active_rally_elsewhere.id,
            'game': _game_payload(
                active_rally_elsewhere, actor_id, now=now,
            ),
        }), 409

    active_for_user = (
        GameArrivalIntent.query.filter_by(user_id=actor_id, active=True)
        .order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    retired = False
    for intent in active_for_user:
        if not _arrival_time_active(intent, now):
            retired = _end_arrival_intent(intent, 'expired', now) or retired
    holder = next(
        (intent for intent in active_for_user if _arrival_time_active(intent, now)),
        None,
    )
    if holder:
        if retired:
            db.session.commit()
        return jsonify({
            'error': (
                'arrival_already_active'
                if holder.game_id == game_id else 'active_arrival_elsewhere'
            ),
            'game_id': holder.game_id,
        }), 409
    active_for_game = (
        GameArrivalIntent.query.filter_by(game_id=game_id, active=True)
        .order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for intent in active_for_game:
        if not _arrival_time_active(intent, now):
            retired = _end_arrival_intent(intent, 'expired', now) or retired
    if any(_arrival_time_active(intent, now) for intent in active_for_game):
        if retired:
            db.session.commit()
        return jsonify({'error': 'arrival_slot_taken'}), 409
    if retired:
        db.session.commit()
    return jsonify({'error': 'arrival_conflict', 'retryable': True}), 409


@games_bp.put('/games/<int:game_id>/arrival')
@rate_limit(12, 3600)
@login_required
def declare_rally_arrival(game_id):
    """Protect one remote spot briefly without claiming physical presence."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    try:
        eta_minutes = int(payload.get('eta_minutes'))
    except (TypeError, ValueError):
        eta_minutes = 0
    if eta_minutes not in RALLY_ARRIVAL_ETA_MINUTES:
        return jsonify({'error': 'invalid_eta_minutes'}), 400
    client_attempt_id, valid_attempt_id = _client_attempt_id(payload)
    if not valid_attempt_id or not client_attempt_id:
        return jsonify({'error': 'invalid_client_attempt_id'}), 400
    fingerprint = _arrival_attempt_fingerprint(game_id, eta_minutes)
    capability = payload.get('arrival_capability')
    now = utcnow()

    # Match direct joins: User first, then all possibly affected Games in
    # ascending order, then intent/check-in rows. The unlocked probe only
    # expands the lock closure; it is never trusted as current state.
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    active_probe = GameArrivalIntent.query.filter_by(
        user_id=user.id, active=True,
    ).first()
    attempt_probe = GameArrivalIntent.query.filter_by(
        user_id=user.id, client_attempt_id=client_attempt_id,
    ).first()
    game_ids = {game_id}
    membership_game_ids = {
        row[0] for row in db.session.query(Game.id).join(GamePlayer).filter(
            GamePlayer.user_id == user.id,
            Game.is_instant.is_(True),
            Game.status == 'upcoming',
            Game.assembly_closed_at.is_(None),
            Game.scheduled_at >= now - timedelta(
                minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
            ),
            Game.scheduled_at <= now + timedelta(minutes=15),
        ).all()
    }
    game_ids.update(membership_game_ids)
    if active_probe:
        game_ids.add(active_probe.game_id)
    if attempt_probe:
        game_ids.add(attempt_probe.game_id)
    locked_games = (
        Game.query.filter(Game.id.in_(sorted(game_ids)))
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    games_by_id = {game.id: game for game in locked_games}
    game = games_by_id.get(game_id)
    for locked_game in locked_games:
        db.session.expire(locked_game, ['players'])

    existing_attempt = (
        GameArrivalIntent.query.filter_by(
            user_id=user.id, client_attempt_id=client_attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    current_for_target = (
        GameArrivalIntent.query.filter_by(
            user_id=user.id, game_id=game_id, active=True,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if existing_attempt:
        if (
            existing_attempt.game_id != game_id
            or existing_attempt.client_attempt_fingerprint != fingerprint
        ):
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        if not _arrival_time_active(existing_attempt, now):
            _end_arrival_intent(existing_attempt, 'expired', now)
            db.session.commit()
            # Exact attempt ownership is enough to recover the definitive
            # inactive ledger row, but not to reopen a closed game's detail.
            return _arrival_response(
                existing_attempt, 'existing', None, user.id, now,
            )
        if _arrival_blocked_by_roster(game, user.id):
            _end_arrival_intent(existing_attempt, 'blocked', now)
            db.session.commit()
            return jsonify({'error': 'game_not_found'}), 404
        if not _arrival_request_authorized(
            game, user, capability, existing_attempt,
        ):
            return jsonify({'error': 'game_not_found'}), 404
        if not _instant_rally_assembly_active(
            game, now, for_update=True,
        ):
            _end_arrival_intent(existing_attempt, 'rally_closed', now)
            db.session.commit()
            return _arrival_response(
                existing_attempt, 'existing', None, user.id, now,
            )
        return _arrival_response(existing_attempt, 'existing', game, user.id, now)

    if _arrival_blocked_by_roster(game, user.id):
        if current_for_target:
            _end_arrival_intent(current_for_target, 'blocked', now)
            db.session.commit()
        return jsonify({'error': 'game_not_found'}), 404
    if not _arrival_request_authorized(
        game, user, capability, current_for_target,
    ):
        return jsonify({'error': 'game_not_found'}), 404

    active_rally_elsewhere = next(
        (
            related_game for related_game in locked_games
            if related_game.id != game_id
            and related_game.id in membership_game_ids
            and any(player.user_id == user.id for player in related_game.players)
            and _instant_rally_assembly_active(
                related_game, now, for_update=True,
            )
        ),
        None,
    )
    if active_rally_elsewhere:
        return jsonify({
            'error': 'active_rally_elsewhere',
            'game_id': active_rally_elsewhere.id,
            'game': _game_payload(
                active_rally_elsewhere, user.id, now=now,
            ),
        }), 409

    # Retire query-time-stale rows before the partial unique constraints are
    # asked to admit a new reservation.
    active_for_user = (
        GameArrivalIntent.query.filter_by(user_id=user.id, active=True)
        .order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for intent in active_for_user:
        related_game = games_by_id.get(intent.game_id) or intent.game
        if not _arrival_time_active(intent, now):
            _end_arrival_intent(intent, 'expired', now)
        elif not _instant_rally_assembly_active(
            related_game, now, for_update=True,
        ):
            _end_arrival_intent(intent, 'rally_closed', now)
    db.session.flush()

    if not _instant_rally_assembly_active(game, now, for_update=True):
        if game.assembly_closed_at is None:
            game.assembly_closed_at = now
        if len(game.players) <= 1:
            game.status = 'expired'
        _end_game_arrivals(game, 'rally_closed', now)
        db.session.commit()
        return jsonify({'error': 'rally_no_longer_active'}), 409
    if any(player.user_id == user.id for player in game.players):
        return jsonify({'error': 'already_joined'}), 409

    checkin = active_checkin_for(
        user.id, fresh=True, now=now, for_update=True,
    )
    if checkin:
        if checkin.court_id == game.court_id:
            return jsonify({'error': 'already_at_court'}), 409
        return jsonify({'error': 'active_checkin_elsewhere'}), 409

    active_for_user = next(
        (intent for intent in active_for_user if _arrival_time_active(intent, now)),
        None,
    )
    if active_for_user:
        if active_for_user.game_id == game.id:
            return jsonify({
                'error': 'arrival_already_active',
                'arrival': active_for_user.to_dict(now),
            }), 409
        return jsonify({
            'error': 'active_arrival_elsewhere',
            'game_id': active_for_user.game_id,
        }), 409

    active_for_game = (
        GameArrivalIntent.query.filter_by(game_id=game.id, active=True)
        .order_by(GameArrivalIntent.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for intent in active_for_game:
        if not _arrival_time_active(intent, now):
            _end_arrival_intent(intent, 'expired', now)
    db.session.flush()
    holder = next(
        (intent for intent in active_for_game if _arrival_time_active(intent, now)),
        None,
    )
    if holder:
        return jsonify({'error': 'arrival_slot_taken'}), 409
    if len(game.players) >= game.max_players:
        return jsonify({'error': 'rally_full'}), 409

    arrives_at = now + timedelta(minutes=eta_minutes)
    assembly_ceiling = game.scheduled_at + timedelta(
        minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
    )
    if arrives_at >= assembly_ceiling:
        return jsonify({'error': 'rally_no_longer_active'}), 409
    expires_at = min(
        arrives_at + timedelta(minutes=RALLY_ARRIVAL_GRACE_MINUTES),
        now + timedelta(minutes=RALLY_ARRIVAL_HARD_MAX_MINUTES),
        assembly_ceiling,
    )
    if expires_at <= now:
        return jsonify({'error': 'rally_no_longer_active'}), 409
    previous_intent = (
        GameArrivalIntent.query.filter_by(game_id=game.id, user_id=user.id)
        .order_by(GameArrivalIntent.id.desc())
        .first()
    )
    previous_announcement = (
        previous_intent.last_announced_at if previous_intent else None
    )
    should_announce = not previous_announcement or (
        previous_announcement
        <= now - timedelta(
            minutes=RALLY_ARRIVAL_ANNOUNCEMENT_COOLDOWN_MINUTES,
        )
    )
    intent = GameArrivalIntent(
        game_id=game.id,
        user_id=user.id,
        eta_minutes=eta_minutes,
        declared_at=now,
        arrives_at=arrives_at,
        expires_at=expires_at,
        active=True,
        client_attempt_id=client_attempt_id,
        client_attempt_fingerprint=fingerprint,
        # Carry the last actual announcement across quick cancel/recreate
        # cycles. If every roster member muted this kind, it remains null so a
        # later unmuted reservation is still eligible to announce.
        last_announced_at=previous_announcement,
    )
    actor_id = user.id
    db.session.add(intent)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return _recover_arrival_integrity_race(
            game_id, actor_id, client_attempt_id, fingerprint,
            capability, now,
        )

    # A remote rally reservation supersedes broad availability. Pulse rows
    # are always locked after the Game, CheckIn, and arrival rows above.
    _end_active_play_pulse_for_user(user.id, 'arrival', now)

    announced = False
    if should_announce:
        for player in game.players:
            if player.user_id == user.id:
                continue
            announcement = notify(
                player.user_id,
                'rally_arrival',
                f'{user.display_name} is on the way',
                f'About {eta_minutes} minutes away.',
                related_user_id=user.id,
                related_game_id=game.id,
                action_url=f'/#game/{game.id}',
                unread_dedupe_key=f'rally-arrival:{game.id}:{user.id}',
            )
            announced = announcement is not None or announced
    if announced:
        intent.last_announced_at = now
    db.session.commit()
    return _arrival_response(intent, 'created', game, user.id, now, status=201)


@games_bp.delete('/games/<int:game_id>/arrival')
@rate_limit(30, 3600)
@login_required
def cancel_rally_arrival(game_id):
    """Idempotently release the caller's protected remote spot."""
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    probe = GameArrivalIntent.query.filter_by(
        game_id=game_id, user_id=user.id,
    ).order_by(GameArrivalIntent.id.desc()).first()
    if not probe:
        return jsonify({'cancelled': True, 'arrival': None})
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    intent = (
        GameArrivalIntent.query.filter_by(id=probe.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    now = utcnow()
    if intent and intent.active:
        _end_arrival_intent(
            intent,
            'cancelled' if _arrival_time_active(intent, now) else 'expired',
            now,
        )
        db.session.commit()
    return jsonify({
        'cancelled': True,
        'arrival': intent.to_dict(now) if intent else None,
    })


@games_bp.post('/games')
@rate_limit(20, 60)
@login_required
def create_game():
    payload = request.get_json(silent=True)
    if payload is None:
        # Distinguish an omitted/empty body from JSON `null` or malformed JSON.
        if request.get_data(cache=True).strip():
            return jsonify({'error': 'invalid_payload'}), 400
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400

    client_attempt_id, valid_attempt_id = _client_attempt_id(payload)
    if not valid_attempt_id:
        return jsonify({'error': 'invalid_client_attempt_id'}), 400
    normalized_attempt = _normalized_game_attempt(payload, g.current_user.id)
    attempt_fingerprint = _game_attempt_fingerprint(normalized_attempt)
    if client_attempt_id:
        existing = Game.query.filter_by(
            creator_id=g.current_user.id,
            client_attempt_id=client_attempt_id,
        ).first()
        if existing:
            return _game_attempt_replay(
                existing, attempt_fingerprint, g.current_user.id,
            )

    court = db.session.get(Court, normalized_attempt['court_id'])
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    scheduled_at = normalized_attempt['scheduled_at']
    if not scheduled_at:
        return jsonify({'error': 'invalid_scheduled_at'}), 400
    if scheduled_at < utcnow() - timedelta(minutes=15):
        return jsonify({'error': 'scheduled_in_past'}), 400

    game_type = normalized_attempt['game_type']
    if game_type not in GAME_TYPES:
        return jsonify({'error': 'invalid_game_type'}), 400

    max_players = normalized_attempt['max_players']

    # Crew scheduling is versioned and server-derived. Lock every Crew-related
    # User in canonical order before the parent Crew row, matching block,
    # account-deletion, roster mutation, and Crew-chat writers. Exact game
    # replay intentionally happened above, before this current-state check.
    crew = None
    if normalized_attempt['crew_id'] is not None:
        # Lazy import avoids coupling blueprint registration order while
        # keeping one lock protocol for every Crew writer.
        from backend.routes.crews import _active_crew_after_user_locks
        crew = _active_crew_after_user_locks(normalized_attempt['crew_id'])
        if (
            not crew
            or g.current_user.deleted_at is not None
            or not crew.is_member(g.current_user.id)
        ):
            return jsonify({'error': 'crew_not_found'}), 404
        expected_version = normalized_attempt['expected_crew_version']
        if expected_version is None or expected_version != crew.roster_version:
            return jsonify({
                'error': 'crew_changed',
                'current_roster_version': crew.roster_version,
            }), 409
        roster_ids = sorted(crew.member_ids())
        if len(roster_ids) < 2:
            return jsonify({'error': 'crew_needs_two_players'}), 409
        if len(roster_ids) > 12:
            return jsonify({'error': 'crew_changed'}), 409
        roster_users = User.query.filter(User.id.in_(roster_ids)).all()
        if len(roster_users) != len(roster_ids) or any(
            user.deleted_at for user in roster_users
        ):
            return jsonify({'error': 'crew_changed'}), 409
        if any(
            is_blocked_between(user_a, user_b)
            for index, user_a in enumerate(roster_ids)
            for user_b in roster_ids[index + 1:]
        ):
            return jsonify({'error': 'crew_changed'}), 409
        if normalized_attempt['game_type'] == 'ranked' and len(roster_ids) not in (2, 4):
            return jsonify({'error': 'ranked_crew_size_must_be_2_or_4'}), 400
        max_players = len(roster_ids)

    # Serialize ordinary creation with availability publish/accept and account
    # deletion. Crew creation already holds this row as part of its sorted
    # User closure; the refresh is harmless in that case.
    creator = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not creator or creator.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = creator

    # Collect specifically invited players. A same-crew plan can request an
    # atomic roster: if anyone became unavailable after the sheet opened, fail
    # before creating the game instead of quietly sending a partial invite.
    invited_ids = []
    unavailable_invitees = []
    eligible_pair_ids = [g.current_user.id]
    requested_invite_ids = (
        [user_id for user_id in roster_ids if user_id != g.current_user.id]
        if crew else normalized_attempt['invite_user_ids']
    )
    for invitee_id in requested_invite_ids:
        invitee = db.session.get(User, invitee_id)
        reason = None
        if not invitee or invitee.deleted_at:
            reason = 'user_not_found'
        elif any(
            is_blocked_between(existing_id, invitee_id)
            for existing_id in eligible_pair_ids
        ):
            reason = 'user_blocked'
        if reason:
            unavailable_invitees.append({'user_id': invitee_id, 'reason': reason})
            continue
        invited_ids.append(invitee_id)
        eligible_pair_ids.append(invitee_id)
    if normalized_attempt['require_all_invitees'] and unavailable_invitees:
        return jsonify({
            'error': 'crew_changed',
            'unavailable_user_ids': [row['user_id'] for row in unavailable_invitees],
            'unavailable': unavailable_invitees,
        }), 409

    # Visibility: open (anyone nearby) / friends (all friends) / private (invited only)
    visibility = normalized_attempt['visibility']
    if visibility == 'private' and not invited_ids:
        return jsonify({'error': 'no_invitees'}), 400

    # Recurrence: weekly open-play sessions (casual only — they don't score).
    recurrence = normalized_attempt['recurrence']

    # Preferred level is a hint for joiners, never a hard gate.
    preferred_level = normalized_attempt['preferred_level']

    # Hosting on behalf of a club: members only, and never private (a club
    # game's whole point is that the club can see and join it).
    club = None
    if normalized_attempt['club_id'] is not None:
        if crew:
            return jsonify({'error': 'choose_club_or_crew'}), 400
        from backend.models import Club, ClubMember
        club = db.session.get(Club, normalized_attempt['club_id'])
        if not club:
            return jsonify({'error': 'club_not_found'}), 404
        if not ClubMember.query.filter_by(
            club_id=club.id, user_id=g.current_user.id,
        ).first():
            return jsonify({'error': 'members_only'}), 403
        if visibility == 'private':
            return jsonify({'error': 'club_game_cannot_be_private'}), 400

    game = Game(
        court_id=court.id,
        creator_id=g.current_user.id,
        client_attempt_id=client_attempt_id,
        client_attempt_fingerprint=(
            attempt_fingerprint if client_attempt_id else None
        ),
        club=club,
        crew=crew,
        crew_roster_version=crew.roster_version if crew else None,
        scheduled_at=scheduled_at,
        game_type=game_type,
        visibility=visibility,
        recurrence=recurrence,
        max_players=max_players,
        preferred_level=preferred_level,
        notes=normalized_attempt['notes'],
    )
    db.session.add(game)
    try:
        # Flush the unique creator/attempt pair before adding players, invites,
        # or notifications. A concurrent retry therefore loses here without
        # repeating any downstream side effects.
        db.session.flush()
    except IntegrityError:
        if not client_attempt_id:
            raise
        db.session.rollback()
        existing = Game.query.filter_by(
            creator_id=g.current_user.id,
            client_attempt_id=client_attempt_id,
        ).first()
        if existing:
            return _game_attempt_replay(
                existing, attempt_fingerprint, g.current_user.id,
            )
        raise
    db.session.add(GamePlayer(game_id=game.id, user_id=g.current_user.id))

    label = 'ranked game' if game_type == 'ranked' else 'game'

    # Club members hear about their club's games first-class.
    club_pinged = set()
    if club:
        for member in club.members:
            if member.user_id == g.current_user.id:
                continue
            if is_blocked_between(g.current_user.id, member.user_id):
                continue
            notify(
                member.user_id,
                'club_game',
                f'{club.name}: new {label} at {court.name}',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
            club_pinged.add(member.user_id)

    if visibility == 'private':
        for uid in invited_ids:
            db.session.add(GameInvite(game_id=game.id, user_id=uid))
            notify(
                uid,
                'game_invite_direct',
                f'{g.current_user.display_name} invited you to a {label} at {court.name}',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    elif visibility == 'friends':
        for friend_id in friend_ids(g.current_user.id):
            if friend_id in club_pinged:
                continue
            notify(
                friend_id,
                'game_invite',
                f'{g.current_user.display_name} scheduled a {label} at {court.name}',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    else:
        # Open games ping players who saved this court — they opted into
        # hearing about it. Friends see it in their feed already. One ping
        # per creator per fan per 3h, so create/cancel churn cannot spam.
        _notify_saved_court_fans(
            game,
            g.current_user,
            label,
            excluded_user_ids=set(invited_ids) | {g.current_user.id},
            club_pinged=club_pinged,
        )

    _end_play_pulse_for_game(
        g.current_user.id, game, 'game_created', utcnow(),
    )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id)), 201


def _game_open_call_response(call, outcome, status=200):
    game = call.game
    if game is not None:
        db.session.expire(game, ['open_calls', 'players', 'waitlist'])
    viewer_id = g.current_user.id
    game_visible = bool(
        game
        and game.visible_to(viewer_id, friend_ids(viewer_id))
        and not _game_has_blocked_participant(game, viewer_id)
    )
    if game_visible:
        open_call_payload = call.to_dict(viewer_id)
    else:
        # A former host may legitimately replay the exact idempotency key after
        # ownership changes.  Return enough of the durable receipt to converge
        # that retry, but do not expose the current roster, waitlist, or time if
        # a block or visibility change now hides the game from them.
        open_call_payload = {
            'id': call.id,
            'game_id': call.game_id,
            'created_by_id': call.created_by_id,
            'state': 'closed',
            'active': False,
            'end_reason': call.end_reason or '',
            'can_join': False,
            'can_waitlist': False,
            'can_withdraw': False,
        }
    return jsonify({
        'outcome': outcome,
        'open_call': open_call_payload,
        'game': _game_payload(game, viewer_id) if game_visible else None,
    }), status


def _recover_game_open_call(game_id, actor_id, attempt_id, fingerprint):
    """Converge after a unique-index race without duplicating the Message."""
    actor = (
        User.query.filter(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not actor or actor.deleted_at or not game:
        return jsonify({'error': 'open_call_conflict', 'retryable': True}), 409
    g.current_user = actor
    replay = (
        GameOpenCall.query.filter_by(
            created_by_id=actor_id,
            client_attempt_id=attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if replay:
        if (
            replay.game_id != game_id
            or replay.client_attempt_fingerprint != fingerprint
        ):
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        return _game_open_call_response(replay, 'existing')
    owned = (
        GameOpenCall.query.filter_by(
            game_id=game_id, created_by_id=actor_id,
        )
        .order_by(GameOpenCall.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if owned:
        return _game_open_call_response(owned, 'existing')
    return jsonify({'error': 'open_call_conflict', 'retryable': True}), 409


@games_bp.post('/games/<int:game_id>/open-call')
@rate_limit(10, 3600)
@login_required
def create_game_open_call(game_id):
    """Post one retry-safe, live game card into the game's court room."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    client_attempt_id, valid_attempt_id = _client_attempt_id(payload)
    if not valid_attempt_id or not client_attempt_id:
        return jsonify({'error': 'invalid_client_attempt_id'}), 400
    fingerprint = _game_open_call_attempt_fingerprint(game_id)

    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user

    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )

    # Resolve an exact device retry before checking current host authority.
    # The URL Game is locked first to preserve the shared User -> Game -> Call
    # order. A former host can recover only their own immutable receipt.
    replay = (
        GameOpenCall.query.filter_by(
            created_by_id=user.id,
            client_attempt_id=client_attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if replay:
        if (
            replay.game_id != game_id
            or replay.client_attempt_fingerprint != fingerprint
        ):
            return jsonify({'error': 'client_attempt_id_conflict'}), 409
        return _game_open_call_response(replay, 'existing')

    if not game or game.creator_id != user.id:
        # A non-host cannot use this mutation to probe an invite-only game.
        return jsonify({'error': 'game_not_found'}), 404
    db.session.expire(game, ['players', 'open_calls', 'waitlist'])

    # One host gets one public ask per game. An ended ask remains a receipt,
    # so changing the device key cannot create a second court-room message.
    owned = (
        GameOpenCall.query.filter_by(
            game_id=game.id, created_by_id=user.id,
        )
        .order_by(GameOpenCall.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if owned:
        return _game_open_call_response(owned, 'existing')

    now = utcnow()
    if (
        game.status != 'upcoming'
        or game.visibility != 'open'
        or game.is_instant
        or game.recurrence != 'none'
        or game.crew_id is not None
        or game.scheduled_at < now - timedelta(
            minutes=GAME_OPEN_CALL_CREATE_GRACE_MINUTES,
        )
    ):
        return jsonify({'error': 'open_call_not_available'}), 409
    if not game.court or game.court.closed:
        return jsonify({'error': 'court_closed'}), 409
    if len(game.players) >= game.max_players:
        return jsonify({'error': 'game_full'}), 409

    active = (
        GameOpenCall.query.filter_by(game_id=game.id, active=True)
        .order_by(GameOpenCall.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if active and active.created_by_id == game.creator_id:
        return _game_open_call_response(active, 'existing')
    if active:
        # Defense in depth for a legacy/admin host transfer that bypassed the
        # ordinary leave path. The former host's speech never transfers.
        _end_game_open_call(active, 'host_changed', now)
        db.session.flush()

    message = Message(
        sender_id=user.id,
        court_id=game.court_id,
        body='Open game — see the live roster and join details.',
    )
    db.session.add(message)
    db.session.flush()
    call = GameOpenCall(
        game=game,
        created_by_id=user.id,
        court_message_id=message.id,
        client_attempt_id=client_attempt_id,
        client_attempt_fingerprint=fingerprint,
        active=True,
    )
    db.session.add(call)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _recover_game_open_call(
            game.id, user.id, client_attempt_id, fingerprint,
        )
    return _game_open_call_response(call, 'created', status=201)


@games_bp.delete('/games/<int:game_id>/open-call')
@rate_limit(20, 3600)
@login_required
def withdraw_game_open_call(game_id):
    """End the current host's court call while keeping its message receipt."""
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game or game.creator_id != user.id:
        return jsonify({'error': 'game_not_found'}), 404
    call = (
        GameOpenCall.query.filter_by(
            game_id=game.id, created_by_id=user.id,
        )
        .order_by(GameOpenCall.id.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not call:
        return jsonify({'error': 'open_call_not_found'}), 404
    if not call.active:
        return _game_open_call_response(call, 'already_withdrawn')
    _end_game_open_call(call, 'host_withdrew')
    db.session.commit()
    return _game_open_call_response(call, 'withdrawn')


def _chat_unread_for(user_id, game_ids):
    """{game_id: unread count} across a player's games. No read marker means
    nothing's been read — every message in that thread counts."""
    if not game_ids:
        return {}
    from backend.models import GameChatRead, Message
    markers = {
        m.game_id: m.last_read_message_id
        for m in GameChatRead.query.filter(
            GameChatRead.user_id == user_id,
            GameChatRead.game_id.in_(game_ids),
        )
    }
    counts = {}
    rows = (
        db.session.query(Message.game_id, Message.id)
        .filter(Message.game_id.in_(game_ids))
        .all()
    )
    for gid, mid in rows:
        if mid > markers.get(gid, 0):
            counts[gid] = counts.get(gid, 0) + 1
    return counts


@games_bp.get('/games/<int:game_id>')
def game_detail(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    current_user = optional_current_user()
    viewer_id = current_user.id if current_user else None
    viewer_friends = friend_ids(viewer_id) if viewer_id else set()
    if not game.visible_to(viewer_id, viewer_friends) \
            or _game_has_blocked_participant(game, viewer_id) \
            or not _instant_game_discovery_allowed(
                game, current_user, viewer_friends,
            ):
        # Do not confirm that an invite-only game exists to a stranger.
        return jsonify({'error': 'game_not_found'}), 404
    item = _discovery_game_payload(game, current_user, viewer_friends)
    if item['is_joined']:
        item['chat_unread'] = _chat_unread_for(current_user.id, [game.id]).get(game.id, 0)
    return jsonify(item)


@games_bp.get('/games/<int:game_id>/crew')
@login_required
def completed_game_crew(game_id):
    """Eligible co-players plus viewer-relative connection state.

    This is intentionally participant-only and filters both directions of a
    block. Historical scorecards remain intact, while a post-game planner can
    never use them to reconnect with or invite somebody who opted out.
    """
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    # A completed game's durable crew is the people assigned to the recorded
    # score, not somebody who RSVP'd but did not play.
    player_ids = [
        player.user_id for player in game.players if player.team in (1, 2)
    ]
    if g.current_user.id not in player_ids:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'completed':
        return jsonify({'error': 'game_not_completed'}), 400

    existing_crew = None
    if game.crew_id:
        existing_crew = Crew.query.filter(
            Crew.id == game.crew_id, Crew.archived_at.is_(None),
        ).first()
    if existing_crew is None:
        existing_crew = Crew.query.filter(
            Crew.source_game_id == game.id, Crew.archived_at.is_(None),
        ).first()
    crew_data = None
    if existing_crew and existing_crew.is_member(g.current_user.id):
        crew_data = existing_crew.to_summary_dict(g.current_user.id)
    elif existing_crew:
        invite = CrewInvite.query.filter_by(
            crew_id=existing_crew.id,
            invitee_id=g.current_user.id,
            status='pending',
        ).first()
        if invite:
            crew_data = {
                'id': existing_crew.id,
                'name': existing_crew.name,
                'joined': False,
                'invitation_pending': True,
                'default_court_id': existing_crew.default_court_id,
                'default_court_name': (
                    existing_crew.default_court.name
                    if existing_crew.default_court else None
                ),
            }

    candidate_ids = [uid for uid in player_ids if uid != g.current_user.id]
    if not candidate_ids:
        return jsonify({'items': [], 'crew': crew_data})
    users = {
        user.id: user for user in User.query.filter(
            User.id.in_(candidate_ids), User.deleted_at.is_(None),
        ).all()
        if not is_blocked_between(g.current_user.id, user.id)
    }
    eligible_ids = set(users)
    friendships = Friendship.query.filter(or_(
        (Friendship.requester_id == g.current_user.id)
        & (Friendship.addressee_id.in_(eligible_ids)),
        (Friendship.addressee_id == g.current_user.id)
        & (Friendship.requester_id.in_(eligible_ids)),
    )).all() if eligible_ids else []
    by_user = {
        (row.addressee_id if row.requester_id == g.current_user.id else row.requester_id): row
        for row in friendships
    }

    items = []
    for user_id in candidate_ids:
        user = users.get(user_id)
        if not user:
            continue
        friendship = by_user.get(user_id)
        items.append({
            **user.to_public_dict(),
            'friendship_id': friendship.id if friendship else None,
            'friendship_status': friendship.status if friendship else None,
            'friendship_outgoing': (
                friendship.requester_id == g.current_user.id if friendship else False
            ),
        })
    return jsonify({'items': items, 'crew': crew_data})


@games_bp.post('/games/<int:game_id>/attend')
@rate_limit(30, 60)
@login_required
def confirm_attendance(game_id):
    """'I'm coming 👋' — a player vouches they'll show up for this occurrence."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.is_instant:
        return jsonify({'error': 'instant_rally_use_arrival'}), 409
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    mine = next((p for p in game.players if p.user_id == g.current_user.id), None)
    if not mine:
        return jsonify({'error': 'players_only'}), 403
    if mine.attending_at is None:
        mine.attending_at = utcnow()
        db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/join')
@rate_limit(30, 60)
@login_required
def join_game(game_id):
    # Account deletion and presence mutations lock User first. Match that
    # order, then serialize capacity on the Game row so two serverless workers
    # cannot both consume the final spot.
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    db.session.expire(game, ['players', 'arrival_intents'])
    if _game_has_blocked_participant(game, g.current_user.id):
        return jsonify({'error': 'game_not_found'}), 404
    target_arrival = (
        GameArrivalIntent.query.filter_by(
            game_id=game.id, user_id=g.current_user.id, active=True,
        )
        .filter(
            GameArrivalIntent.ended_at.is_(None),
            GameArrivalIntent.expires_at > utcnow(),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    ) if game.is_instant else None
    if any(p.user_id == g.current_user.id for p in game.players):
        if target_arrival:
            _end_arrival_intent(target_arrival, 'arrived')
        pulse_ended = _end_play_pulse_for_game(
            g.current_user.id,
            game,
            'instant_rally' if game.is_instant else 'game_joined',
            utcnow(),
        )
        if target_arrival or pulse_ended:
            db.session.commit()
        return jsonify(_game_payload(game, g.current_user.id))
    if game.is_instant and _active_holder_blocks_user(
        game, g.current_user.id, for_update=True,
    ):
        # A remote holder is part of the real-world commitment boundary even
        # though their identity is not disclosed to this nonmember.
        return jsonify({'error': 'game_not_found'}), 404
    if game.is_instant and not _instant_game_discovery_allowed(
        game, g.current_user,
    ):
        # Validate discovery before status/presence/court-specific errors so
        # an enumerable ID cannot become an exact-location oracle.
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if game.is_instant:
        now = utcnow()
        if (
            game.assembly_closed_at is not None
            or game.scheduled_at < now - timedelta(
                minutes=INSTANT_RALLY_ASSEMBLY_MINUTES,
            )
        ):
            # Preserve a real multi-player row for scoring, but permanently
            # close physical recruitment while holding the same Game lock.
            if game.assembly_closed_at is None:
                game.assembly_closed_at = now
            if len(game.players) <= 1:
                game.status = 'expired'
            _end_game_arrivals(game, 'rally_closed', now)
            db.session.commit()
            return jsonify({
                'error': 'rally_no_longer_active',
                'game_id': game.id,
            }), 409
        other_rallies = (
            Game.query.join(GamePlayer)
            .filter(
                GamePlayer.user_id == g.current_user.id,
                Game.id != game.id,
                Game.is_instant.is_(True),
                Game.status == 'upcoming',
            )
            .order_by(Game.scheduled_at.desc(), Game.id.desc())
            .execution_options(populate_existing=True)
            .all()
        )
        for other_rally in other_rallies:
            db.session.expire(other_rally, ['players'])
        active_elsewhere = next(
            (
                rally for rally in other_rallies
                if _instant_rally_assembly_active(rally, now)
            ),
            None,
        )
        if active_elsewhere:
            return jsonify({
                'error': 'active_rally_elsewhere',
                'game_id': active_elsewhere.id,
                'game': _game_payload(
                    active_elsewhere, g.current_user.id, now=now,
                ),
            }), 409
        active_arrival_elsewhere = (
            GameArrivalIntent.query.filter(
                GameArrivalIntent.user_id == g.current_user.id,
                GameArrivalIntent.game_id != game.id,
                GameArrivalIntent.active.is_(True),
                GameArrivalIntent.ended_at.is_(None),
                GameArrivalIntent.expires_at > now,
            )
            .first()
        )
        if active_arrival_elsewhere:
            return jsonify({
                'error': 'active_arrival_elsewhere',
                'game_id': active_arrival_elsewhere.game_id,
            }), 409
        checkin = active_checkin_for(
            g.current_user.id, fresh=True, for_update=True,
        )
        if not checkin:
            return jsonify({
                'error': 'active_checkin_required',
                'court_id': game.court_id,
            }), 409
        if checkin.court_id != game.court_id:
            return jsonify({
                'error': 'active_checkin_court_mismatch',
                'court_id': game.court_id,
                'checked_in_court_id': checkin.court_id,
            }), 409
        if not _instant_rally_has_fresh_member(
            game, now, for_update=True,
        ):
            game.assembly_closed_at = now
            if len(game.players) <= 1:
                game.status = 'expired'
            _end_game_arrivals(game, 'rally_closed', now)
            db.session.commit()
            return jsonify({
                'error': 'rally_no_longer_active',
                'game_id': game.id,
                'court_id': game.court_id,
            }), 409
    if game.is_instant:
        capacity = _arrival_capacity(game, now, for_update=True)
        if len(game.players) >= game.max_players:
            if target_arrival:
                _end_arrival_intent(target_arrival, 'capacity_lost', now)
                db.session.commit()
            return jsonify({'error': 'game_full'}), 400
        if capacity['spots_left'] <= 0 and target_arrival is None:
            return jsonify({'error': 'game_full'}), 400
    elif len(game.players) >= game.max_players:
        return jsonify({'error': 'game_full'}), 400
    # Respect visibility: you can only join games you'd be allowed to see.
    if not game.visible_to(g.current_user.id, friend_ids(g.current_user.id)):
        return jsonify({'error': 'not_invited'}), 403

    db.session.add(GamePlayer(game=game, user_id=g.current_user.id))
    if target_arrival:
        _end_arrival_intent(target_arrival, 'arrived', now)
    personal_invite = GameInvite.query.filter_by(
        game_id=game.id, user_id=g.current_user.id,
    ).first()
    if personal_invite:
        db.session.delete(personal_invite)
    checkin = active_checkin_for(g.current_user.id, for_update=True)
    if checkin and checkin.court_id == game.court_id:
        # Joining the game resolves the separate "looking" signal, preventing
        # the player from receiving more same-court rally invitations.
        checkin.looking_for_game = False
        checkin.last_presence_ping_at = utcnow()
    if game.creator_id != g.current_user.id:
        notify(
            game.creator_id,
            'game_join',
            f'{g.current_user.display_name} joined your game',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
            action_url=f'/#game/{game.id}' if game.is_instant else '',
        )
    _end_play_pulse_for_game(
        g.current_user.id,
        game,
        'instant_rally' if game.is_instant else 'game_joined',
        now if game.is_instant else utcnow(),
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        game = db.session.get(Game, game_id)
        if game and any(
            player.user_id == g.current_user.id for player in game.players
        ):
            return jsonify(_game_payload(game, g.current_user.id))
        raise
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/games/<int:game_id>/invite')
@rate_limit(30, 60)
@login_required
def invite_to_game(game_id):
    """Invite one or several people to an upcoming game with open spots.

    The legacy ``user_id`` form keeps its precise error contract; ``user_ids``
    powers the roster-fill sheet and returns partial results in one round trip.
    """
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if not any(p.user_id == g.current_user.id for p in game.players):
        return jsonify({'error': 'players_only'}), 403
    if game.is_instant and not _instant_rally_assembly_active(
        game, for_update=True,
    ):
        now = utcnow()
        if game.assembly_closed_at is None:
            game.assembly_closed_at = now
        if len(game.players) <= 1:
            game.status = 'expired'
        _end_game_arrivals(game, 'rally_closed', now)
        db.session.commit()
        return jsonify({
            'error': 'rally_no_longer_active', 'game_id': game.id,
        }), 409
    if game.is_instant:
        if _arrival_capacity(game, for_update=True)['spots_left'] <= 0:
            return jsonify({'error': 'game_full'}), 400
    elif len(game.players) >= game.max_players:
        return jsonify({'error': 'game_full'}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    is_batch = 'user_ids' in payload
    if is_batch:
        raw_ids = payload.get('user_ids')
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 20:
            return jsonify({'error': 'invalid_user_ids'}), 400
    else:
        raw_ids = [payload.get('user_id')]

    target_ids = []
    for raw_id in raw_ids:
        try:
            target_id = int(raw_id or 0)
        except (TypeError, ValueError):
            target_id = 0
        if target_id > 0 and target_id not in target_ids:
            target_ids.append(target_id)
    if not target_ids:
        error = 'invalid_user_ids' if is_batch else 'user_not_found'
        return jsonify({'error': error}), 400 if is_batch else 404

    player_ids = {player.user_id for player in game.players}
    arrival_holder_ids = {
        intent.user_id for intent in _raw_active_arrivals(
            game, for_update=True,
        )
    } if game.is_instant else set()
    already_invited = game.invited_user_ids()
    inviter_friends = friend_ids(g.current_user.id) if game.is_instant else set()
    newly_invited = []
    skipped = []
    for target_id in target_ids:
        target = db.session.get(User, target_id)
        reason = None
        if not target or target.deleted_at:
            reason = 'user_not_found'
        elif target.id == g.current_user.id:
            reason = 'cannot_invite_self'
        elif target.id in player_ids:
            reason = 'already_joined'
        elif any(
            is_blocked_between(player_id, target.id)
            for player_id in player_ids | arrival_holder_ids
        ):
            reason = 'user_blocked'
        elif game.is_instant and target.id not in inviter_friends:
            reason = 'not_friends'
        elif target.id in already_invited:
            reason = 'already_invited'

        if reason:
            if not is_batch:
                status = {
                    'user_not_found': 404,
                    'cannot_invite_self': 400,
                    'already_joined': 409,
                    'user_blocked': 403,
                    'not_friends': 403,
                }.get(reason, 200)
                if reason == 'already_invited':
                    return jsonify({'invited': True, 'newly_invited': False})
                return jsonify({'error': reason}), status
            skipped.append({'user_id': target_id, 'reason': reason})
            continue

        db.session.add(GameInvite(game_id=game.id, user_id=target.id))
        already_invited.add(target.id)
        newly_invited.append(target.id)
        court_name = game.court.name if game.court else 'a court'
        notify(
            target.id,
            'game_invite_direct',
            f'{g.current_user.display_name} invited you to a game at {court_name}',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
            action_url=f'/#game/{game.id}' if game.is_instant else '',
            unread_dedupe_key=f'game-invite:{game.id}',
        )

    db.session.commit()
    if not is_batch:
        return jsonify({'invited': True, 'newly_invited': True})
    return jsonify({
        'invited': len(newly_invited),
        'invited_user_ids': newly_invited,
        'skipped': skipped,
    })


@games_bp.post('/games/<int:game_id>/invites/decline')
@rate_limit(60, 3600)
@login_required
def decline_invite(game_id):
    """Politely turn down a personal game invite: the invite is removed (the
    game drops off your surfaces) and the host hears about it."""
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if any(p.user_id == g.current_user.id for p in game.players):
        return jsonify({'error': 'already_joined'}), 400
    invite = GameInvite.query.filter_by(
        game_id=game.id, user_id=g.current_user.id,
    ).first()
    if not invite:
        return jsonify({'error': 'not_invited'}), 404

    if game.is_instant:
        intent = (
            GameArrivalIntent.query.filter_by(
                game_id=game.id, user_id=g.current_user.id, active=True,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        if intent:
            _end_arrival_intent(intent, 'invite_declined')

    # Keep the already-loaded visibility relationship in sync with the delete;
    # otherwise expire_on_commit=False can expose this private game once more
    # from the session identity map immediately after a decline.
    game.invites.remove(invite)
    if game.status == 'upcoming':
        court_name = game.court.name if game.court else 'the court'
        notify(
            game.creator_id,
            'invite_declined',
            f"{g.current_user.display_name} can't make your game at {court_name}",
            related_user_id=g.current_user.id,
            related_game_id=game.id,
        )
    db.session.commit()
    return jsonify({'declined': True})


def _lock_users_and_game_for_waitlist_mutation(game_id, actor_id):
    """Lock a bounded promotion closure in User -> Game -> waitlist order."""
    for _attempt in range(3):
        queued_ids = [
            row[0] for row in db.session.query(GameWaitlist.user_id).filter_by(
                game_id=game_id,
            ).order_by(GameWaitlist.id.asc()).limit(12).all()
        ]
        user_ids = sorted({actor_id, *queued_ids})
        users = (
            User.query.filter(User.id.in_(user_ids))
            .order_by(User.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
            .all()
        )
        game = (
            Game.query.filter(Game.id == game_id)
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        if not game:
            return users, None
        db.session.expire(game, ['players', 'waitlist'])
        current_entries = (
            GameWaitlist.query.filter_by(game_id=game_id)
            .order_by(GameWaitlist.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
            .limit(12)
            .all()
        )
        open_spots = max(0, game.max_players - len(game.players))
        promoted_ids = {
            entry.user_id for entry in current_entries[:open_spots]
        }
        if promoted_ids <= {user.id for user in users}:
            return users, game
        db.session.rollback()
    raise RuntimeError('waitlist promotion lock closure kept changing')


def _lock_stable_game_roster_users(game_id, actor_id):
    """Lock every roster User before Game, retrying if the snapshot expands."""
    for _attempt in range(3):
        roster_ids = {
            row[0] for row in db.session.query(GamePlayer.user_id).filter_by(
                game_id=game_id,
            ).all()
        }
        roster_ids.add(actor_id)
        users = (
            User.query.filter(User.id.in_(sorted(roster_ids)))
            .order_by(User.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
            .all()
        )
        game = (
            Game.query.filter(Game.id == game_id)
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        if not game:
            return users, None
        db.session.expire(game, ['players'])
        actual_ids = {player.user_id for player in game.players}
        if actual_ids <= {user.id for user in users}:
            return users, game
        db.session.rollback()
    raise RuntimeError('game roster lock closure kept changing')


def _promote_from_waitlist(game):
    """Fill open spots from the waitlist queue, in order."""
    if game.is_instant:
        # A live rally is physical, immediate attendance. Legacy queue rows
        # must never turn into remote members after somebody leaves.
        legacy_entries = GameWaitlist.query.filter_by(
            game_id=game.id,
        ).with_for_update().all()
        for entry in legacy_entries:
            db.session.delete(entry)
        if legacy_entries:
            db.session.flush()
            db.session.expire(game, ['waitlist'])
        return
    while game.waitlist and len(game.players) < game.max_players:
        entry = game.waitlist[0]
        game.waitlist.remove(entry)
        db.session.add(GamePlayer(game=game, user_id=entry.user_id))
        _end_play_pulse_for_game(
            entry.user_id, game, 'waitlist_promoted', utcnow(),
        )
        notify(
            entry.user_id,
            'game_join',
            # No emoji in titles — the feed prepends the per-kind icon.
            f'A spot opened — you\'re in at {game.court.name if game.court else "the court"}!',
            related_game_id=game.id,
        )


@games_bp.post('/games/<int:game_id>/waitlist')
@rate_limit(30, 600)
@login_required
def join_waitlist(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.is_instant and not _instant_game_discovery_allowed(
        game, g.current_user,
    ):
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if game.is_instant:
        return jsonify({'error': 'instant_rally_no_waitlist'}), 409
    if _game_has_blocked_participant(game, g.current_user.id):
        return jsonify({'error': 'game_not_found'}), 404
    if any(p.user_id == g.current_user.id for p in game.players):
        return jsonify({'error': 'already_joined'}), 400
    if len(game.players) < game.max_players:
        return jsonify({'error': 'game_not_full'}), 400
    if not game.visible_to(g.current_user.id, friend_ids(g.current_user.id)):
        return jsonify({'error': 'not_invited'}), 403
    if not any(w.user_id == g.current_user.id for w in game.waitlist):
        db.session.add(GameWaitlist(game=game, user_id=g.current_user.id))
        db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/waitlist/leave')
@rate_limit(30, 60)
@login_required
def leave_waitlist(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    entry = next((w for w in game.waitlist if w.user_id == g.current_user.id), None)
    viewer_friends = friend_ids(g.current_user.id)
    blocked = _game_has_blocked_participant(game, g.current_user.id)
    visible = game.visible_to(g.current_user.id, viewer_friends) and not blocked
    instant_discoverable = visible and _instant_game_discovery_allowed(
        game, g.current_user, viewer_friends,
    )
    if not entry and not (instant_discoverable if game.is_instant else visible):
        # Idempotent leave is safe only for a caller who may normally see the
        # resource; otherwise it confirms an invite-only/live enumerable ID.
        return jsonify({'error': 'game_not_found'}), 404
    if entry:
        game.waitlist.remove(entry)
        db.session.commit()
    if game.is_instant:
        if instant_discoverable:
            return jsonify(_discovery_game_payload(
                game, g.current_user, viewer_friends,
            ))
        # Let a legacy queued user remove their own row without disclosing the
        # now-private instant court or roster.
        return jsonify({'left_waitlist': True, 'game_id': game.id})
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/leave')
@rate_limit(30, 60)
@login_required
def leave_game(game_id):
    try:
        locked_users, game = _lock_users_and_game_for_waitlist_mutation(
            game_id, g.current_user.id,
        )
    except RuntimeError:
        return jsonify({'error': 'game_changed_retry'}), 409
    user = next(
        (row for row in locked_users if row.id == g.current_user.id), None,
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    player = next((p for p in game.players if p.user_id == g.current_user.id), None)
    if not player:
        return jsonify({'error': 'not_joined'}), 400
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400

    game.players.remove(player)
    court_name = game.court.name if game.court else 'the court'
    if game.creator_id == g.current_user.id:
        remaining = [p for p in game.players if p.user_id != g.current_user.id]
        if remaining:
            _end_game_open_calls(game, 'host_changed')
            game.creator_id = remaining[0].user_id
            # The player who inherits hosting should know.
            notify(
                game.creator_id,
                'player_left',
                f'You\'re now hosting the game at {court_name} — {g.current_user.display_name} left',
                related_game_id=game.id,
            )
        else:
            if game.is_instant:
                game.status = 'expired'
                if game.assembly_closed_at is None:
                    game.assembly_closed_at = utcnow()
                _end_game_arrivals(game, 'rally_closed')
            else:
                game.status = 'cancelled'
                _end_game_open_calls(game, 'cancelled')
    else:
        # Tell the host a spot just opened up in their game.
        notify(
            game.creator_id,
            'player_left',
            f'{g.current_user.display_name} left your game at {court_name} — a spot opened',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
        )
    if game.status == 'upcoming':
        _promote_from_waitlist(game)
        _close_instant_assembly_without_fresh_members(game)
    db.session.commit()
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/games/<int:game_id>/remove/<int:user_id>')
@rate_limit(30, 60)
@login_required
def remove_player(game_id, user_id):
    """Host drops another player from an upcoming game (no-show swap).
    Frees a spot, promotes from the waitlist, and notifies the removed player."""
    try:
        locked_users, game = _lock_users_and_game_for_waitlist_mutation(
            game_id, g.current_user.id,
        )
    except RuntimeError:
        return jsonify({'error': 'game_changed_retry'}), 409
    user = next(
        (row for row in locked_users if row.id == g.current_user.id), None,
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.creator_id != g.current_user.id:
        return jsonify({'error': 'host_only'}), 403
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if user_id == g.current_user.id:
        return jsonify({'error': 'cannot_remove_self'}), 400
    player = next((p for p in game.players if p.user_id == user_id), None)
    if not player:
        return jsonify({'error': 'not_in_game'}), 404

    game.players.remove(player)
    court_name = game.court.name if game.court else 'the court'
    notify(
        user_id,
        'game_cancelled',
        f'{g.current_user.display_name} removed you from the game at {court_name}',
        related_game_id=game.id,
    )
    _promote_from_waitlist(game)
    _close_instant_assembly_without_fresh_members(game)
    db.session.commit()
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/games/<int:game_id>/cancel')
@rate_limit(30, 60)
@login_required
def cancel_game(game_id):
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    db.session.expire(game, ['players', 'waitlist'])
    if game.creator_id != g.current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    game.status = 'cancelled'
    _end_game_open_calls(game, 'cancelled')
    if game.is_instant and game.assembly_closed_at is None:
        game.assembly_closed_at = utcnow()
    _end_game_arrivals(game, 'rally_cancelled')
    for player in game.players:
        if player.user_id != g.current_user.id:
            notify(
                player.user_id,
                'game_cancelled',
                f'Game at {game.court.name if game.court else "court"} was cancelled',
                related_game_id=game.id,
            )
    for entry in list(game.waitlist):
        notify(
            entry.user_id,
            'game_cancelled',
            f'Game at {game.court.name if game.court else "court"} was cancelled',
            related_game_id=game.id,
        )
        game.waitlist.remove(entry)
    db.session.commit()
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/games/<int:game_id>/reschedule')
@rate_limit(20, 60)
@login_required
def reschedule_game(game_id):
    """Host moves an upcoming game to a new time, keeping the roster/RSVPs.
    Re-arms reminders and clears attendance (plans changed), and tells players."""
    try:
        locked_users, game = _lock_stable_game_roster_users(
            game_id, g.current_user.id,
        )
    except RuntimeError:
        return jsonify({'error': 'game_changed_retry'}), 409
    actor = next(
        (user for user in locked_users if user.id == g.current_user.id), None,
    )
    if not actor or actor.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = actor
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.creator_id != g.current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if game.is_instant:
        return jsonify({'error': 'instant_rally_not_reschedulable'}), 409
    if game.recurrence != 'none':
        return jsonify({'error': 'recurring_open_play'}), 400

    when = _parse_scheduled_at((request.get_json(silent=True) or {}).get('scheduled_at'))
    if not when:
        return jsonify({'error': 'invalid_scheduled_at'}), 400
    if when < utcnow() - timedelta(minutes=15):
        return jsonify({'error': 'scheduled_in_past'}), 400

    game.scheduled_at = when
    court_name = game.court.name if game.court else 'the court'
    for player in sorted(game.players, key=lambda row: row.user_id):
        player.reminded_at = None      # re-remind for the new time
        player.day_reminded_at = None
        player.attending_at = None     # re-confirm attendance
        _end_play_pulse_for_game(
            player.user_id, game, 'game_rescheduled', utcnow(),
        )
        if player.user_id != g.current_user.id:
            notify(
                player.user_id,
                'game_reminder',
                f'Game at {court_name} was rescheduled — tap for the new time',
                related_game_id=game.id,
            )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


def _expected_score(rating_a, rating_b):
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _apply_elo(team1_users, team2_users, team1_won):
    """Update ratings + win streaks using team-average ELO; returns {user_id: delta}."""
    avg1 = sum(u.rating for u in team1_users) / len(team1_users)
    avg2 = sum(u.rating for u in team2_users) / len(team2_users)
    expected1 = _expected_score(avg1, avg2)
    actual1 = 1.0 if team1_won else 0.0
    delta1 = round(ELO_K * (actual1 - expected1))
    deltas = {}
    winners = team1_users if team1_won else team2_users
    losers = team2_users if team1_won else team1_users
    for user in team1_users:
        user.rating += delta1
        deltas[user.id] = delta1
    for user in team2_users:
        user.rating -= delta1
        deltas[user.id] = -delta1
    for user in winners:
        user.ranked_wins += 1
        user.current_streak += 1
        user.best_streak = max(user.best_streak, user.current_streak)
    for user in losers:
        user.ranked_losses += 1
        user.current_streak = 0
    # Track peak rating; congratulate on crossing a new round-hundred milestone.
    for user in team1_users + team2_users:
        if user.rating > user.best_rating:
            crossed = (user.rating // 100) > (user.best_rating // 100)
            user.best_rating = user.rating
            if crossed:
                notify(user.id, 'badge_earned',
                       f'New peak rating: {(user.rating // 100) * 100}! 📈')
    return deltas


def _finalize_game(game, actor_id=None):
    """Mark the game completed; for ranked games apply ELO and notify everyone."""
    by_user = {p.user_id: p for p in game.players}
    game.status = 'completed'
    game.completed_at = utcnow()
    _end_game_open_calls(game, 'completed', game.completed_at)
    if game.is_instant:
        if game.assembly_closed_at is None:
            game.assembly_closed_at = game.completed_at
        _end_game_arrivals(game, 'completed', game.completed_at)
    court_name = game.court.name if game.court else 'the court'
    score_text = f'{game.score_team1}–{game.score_team2}'

    if game.game_type == 'ranked':
        team1_ids = [p.user_id for p in game.players if p.team == 1]
        team2_ids = [p.user_id for p in game.players if p.team == 2]
        team1_users = User.query.filter(User.id.in_(team1_ids)).all()
        team2_users = User.query.filter(User.id.in_(team2_ids)).all()
        deltas = _apply_elo(
            team1_users, team2_users,
            team1_won=game.score_team1 > game.score_team2,
        )
        for uid, delta in deltas.items():
            if uid in by_user:
                by_user[uid].rating_delta = delta
        for uid, delta in deltas.items():
            if uid == actor_id:
                continue
            sign = '+' if delta >= 0 else ''
            notify(
                uid,
                'score_confirmed',
                f'Final at {court_name}: {score_text} ({sign}{delta} rating)',
                related_game_id=game.id,
            )
    else:
        for uid in by_user:
            if uid == actor_id:
                continue
            notify(
                uid,
                'score_confirmed',
                f'Game recorded at {court_name}: {score_text}',
                related_game_id=game.id,
            )


@games_bp.post('/games/<int:game_id>/complete')
@rate_limit(20, 60)
@login_required
def submit_score(game_id):
    """Report a score. Casual games finish immediately; ranked scores need an
    opposing player's confirmation before ratings move."""
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    db.session.expire(game, ['players'])
    if game.status not in ('upcoming', 'awaiting_confirmation'):
        return jsonify({'error': 'game_not_open'}), 400
    if game.recurrence != 'none':
        return jsonify({'error': 'recurring_open_play'}), 400
    player_ids = {p.user_id for p in game.players}
    if g.current_user.id not in player_ids:
        return jsonify({'error': 'forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    try:
        score1 = int(payload.get('score_team1'))
        score2 = int(payload.get('score_team2'))
    except (TypeError, ValueError):
        return jsonify({'error': 'scores_required'}), 400
    if score1 == score2 or score1 < 0 or score2 < 0:
        return jsonify({'error': 'invalid_scores'}), 400

    team1_ids = [int(uid) for uid in (payload.get('team1') or [])]
    team2_ids = [int(uid) for uid in (payload.get('team2') or [])]
    if not team1_ids or not team2_ids:
        return jsonify({'error': 'teams_required'}), 400
    if set(team1_ids) & set(team2_ids):
        return jsonify({'error': 'player_on_both_teams'}), 400
    if not (set(team1_ids) | set(team2_ids)) <= player_ids:
        return jsonify({'error': 'unknown_player'}), 400

    by_user = {p.user_id: p for p in game.players}
    for uid in team1_ids:
        by_user[uid].team = 1
    for uid in team2_ids:
        by_user[uid].team = 2

    game.score_team1 = score1
    game.score_team2 = score2
    game.score_submitted_by_id = g.current_user.id
    game.score_submitted_at = utcnow()

    my_team = by_user[g.current_user.id].team
    opposing_ids = team2_ids if my_team == 1 else team1_ids

    if game.game_type == 'ranked' and opposing_ids:
        game.status = 'awaiting_confirmation'
        _end_game_open_calls(game, 'score_submitted', game.score_submitted_at)
        if game.is_instant:
            if game.assembly_closed_at is None:
                game.assembly_closed_at = game.score_submitted_at
            _end_game_arrivals(
                game, 'score_submitted', game.score_submitted_at,
            )
        score_text = f'{score1}–{score2}'
        for uid in opposing_ids:
            notify(
                uid,
                'score_submitted',
                f'{g.current_user.display_name} reported {score_text} — confirm the score',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    else:
        _finalize_game(game, actor_id=g.current_user.id)

    db.session.commit()
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/games/<int:game_id>/confirm')
@rate_limit(20, 60)
@login_required
def confirm_score(game_id):
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    db.session.expire(game, ['players'])
    if game.status != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_confirm'}), 400

    me = next((p for p in game.players if p.user_id == g.current_user.id), None)
    submitter = next(
        (p for p in game.players if p.user_id == game.score_submitted_by_id), None,
    )
    if not me:
        return jsonify({'error': 'forbidden'}), 403
    if (
        not submitter or not me.team or me.user_id == submitter.user_id
        or (submitter.team and me.team == submitter.team)
    ):
        return jsonify({'error': 'opponent_confirmation_required'}), 403

    _finalize_game(game, actor_id=g.current_user.id)
    db.session.commit()
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/games/<int:game_id>/dispute')
@rate_limit(20, 60)
@login_required
def dispute_score(game_id):
    user = (
        User.query.filter(User.id == g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or user.deleted_at:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    game = (
        Game.query.filter(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    db.session.expire(game, ['players'])
    if game.status != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_dispute'}), 400

    me = next((p for p in game.players if p.user_id == g.current_user.id), None)
    if not me:
        return jsonify({'error': 'forbidden'}), 403

    submitter_id = game.score_submitted_by_id
    score_text = f'{game.score_team1}–{game.score_team2}'
    game.status = 'upcoming'
    game.score_team1 = None
    game.score_team2 = None
    game.score_submitted_by_id = None
    game.score_submitted_at = None
    if submitter_id and submitter_id != g.current_user.id:
        notify(
            submitter_id,
            'score_disputed',
            f'{g.current_user.display_name} disputed the score {score_text} — please re-enter it together',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
        )
    db.session.commit()
    return jsonify(_game_payload(game, g.current_user.id))


@games_bp.post('/users/<int:user_id>/challenge')
@rate_limit(20, 60)
@login_required
def challenge_user(user_id):
    """Challenge another player to a ranked match at a court, right now."""
    target = db.session.get(User, user_id)
    if not target:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_challenge_self'}), 400

    payload = request.get_json(silent=True) or {}
    court = db.session.get(Court, int(payload.get('court_id') or 0))
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    game = Game(
        court_id=court.id,
        creator_id=g.current_user.id,
        scheduled_at=utcnow(),
        game_type='ranked',
        visibility='private',
        max_players=2,
        notes=f'⚔️ {g.current_user.display_name} challenged {target.display_name}!',
    )
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=g.current_user.id))
    db.session.add(GameInvite(game_id=game.id, user_id=target.id))
    notify(
        target.id,
        'challenge',
        # No emoji here — the activity feed prepends one per notification kind.
        f'{g.current_user.display_name} challenged you at {court.name}!',
        related_user_id=g.current_user.id,
        related_game_id=game.id,
    )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id)), 201


@games_bp.post('/games/<int:game_id>/decline')
@rate_limit(30, 60)
@login_required
def decline_challenge(game_id):
    """Decline an open challenge-style game you were invited to: cancels it."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if any(p.user_id == g.current_user.id for p in game.players):
        return jsonify({'error': 'already_joined'}), 400
    if len(game.players) > 1:
        return jsonify({'error': 'game_already_started'}), 400

    from backend.models import Notification
    was_challenged = Notification.query.filter_by(
        user_id=g.current_user.id,
        kind='challenge',
        related_game_id=game.id,
    ).first()
    if not was_challenged:
        return jsonify({'error': 'forbidden'}), 403

    game.status = 'cancelled'
    notify(
        game.creator_id,
        'challenge_declined',
        f'{g.current_user.display_name} declined your challenge',
        related_user_id=g.current_user.id,
        related_game_id=game.id,
    )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.get('/games/results')
def recent_results():
    """Feed of recently finished games: yours, your friends', and nearby ones."""
    auto_confirm_stale_scores()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    current_user = optional_current_user()
    viewer_id = current_user.id if current_user else None

    friends = friend_ids(current_user.id) if current_user else set()
    hidden_ids = blocked_pair_ids(viewer_id) if viewer_id else set()

    games = (
        Game.query.filter(Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(100)
        .all()
    )
    items = []
    for game in games:
        if _game_has_blocked_participant(game, viewer_id, hidden_ids):
            continue
        # Results carry the full court, score, and participant roster, so the
        # same visibility boundary as the detail/feed endpoints must run
        # before friendship or proximity ranking. A friend of one participant
        # is not automatically invited to a private Crew game.
        if not game.visible_to(viewer_id, friends):
            continue
        player_ids = {p.user_id for p in game.players}
        involves_me = viewer_id in player_ids
        involves_friend = bool(friends & player_ids)
        distance = None
        court = game.court
        if lat is not None and lng is not None and court and court.latitude is not None:
            distance = haversine_miles(lat, lng, court.latitude, court.longitude)
        nearby = distance is not None and distance <= 100
        # Strangers only see open games nearby; private/friends stay among their people.
        if not (involves_me or involves_friend or (nearby and game.visibility == 'open')):
            continue
        item = game.to_dict(viewer_id)
        item['involves_friend'] = involves_friend
        item['involves_me'] = involves_me
        if distance is not None:
            item['distance_miles'] = round(distance, 1)
        items.append(item)
        if len(items) >= 30:
            break
    return jsonify({'items': items})


@games_bp.post('/games/<int:game_id>/mvp')
@rate_limit(30, 600)
@login_required
def vote_mvp(game_id):
    """Vote a fellow player MVP of a completed game; re-voting changes it."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'completed':
        return jsonify({'error': 'game_not_finished'}), 400
    player_ids = {p.user_id for p in game.players if p.team in (1, 2)}
    if g.current_user.id not in player_ids:
        return jsonify({'error': 'players_only'}), 403
    votee_id = int((request.get_json(silent=True) or {}).get('user_id') or 0)
    if votee_id not in player_ids:
        return jsonify({'error': 'votee_not_in_game'}), 400
    if votee_id == g.current_user.id:
        return jsonify({'error': 'no_self_votes'}), 400
    vote = next((v for v in game.mvp_votes if v.voter_id == g.current_user.id), None)
    if vote:
        vote.votee_id = votee_id
    else:
        db.session.add(GameMvpVote(
            game=game, voter_id=g.current_user.id, votee_id=votee_id,
        ))
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.get('/leaderboard')
def leaderboard():
    """Ranked players, globally or scoped to an area (lat/lng/radius miles).

    Area scoping uses each player's last-known location, falling back to
    their home court — same source as players-nearby discovery."""
    if str(request.args.get('period') or '').strip().lower() == 'month':
        from sqlalchemy import func
        month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = (
            db.session.query(
                User,
                func.coalesce(func.sum(GamePlayer.rating_delta), 0).label('delta'),
                func.count(GamePlayer.id).label('games'),
            )
            .join(GamePlayer, GamePlayer.user_id == User.id)
            .join(Game, Game.id == GamePlayer.game_id)
            .filter(
                Game.status == 'completed',
                Game.game_type == 'ranked',
                Game.completed_at >= month_start,
                GamePlayer.rating_delta.isnot(None),
                User.deleted_at.is_(None),
            )
            .group_by(User.id)
            .order_by(func.sum(GamePlayer.rating_delta).desc())
            .limit(50)
            .all()
        )
        items = []
        for user, delta, games in rows:
            entry = user.to_public_dict()
            entry['month_delta'] = int(delta)
            entry['month_games'] = int(games)
            items.append(entry)
        return jsonify({'items': items, 'period': 'month'})

    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)

    query = User.query.filter(
        User.ranked_wins + User.ranked_losses > 0,
        User.deleted_at.is_(None),
    )

    if lat is not None and lng is not None:
        radius = min(max(request.args.get('radius', default=50.0, type=float), 1.0), 250.0)
        lat_delta = radius / 69.0
        lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
        lat_lo, lat_hi = lat - lat_delta, lat + lat_delta
        lng_lo, lng_hi = lng - lng_delta, lng + lng_delta

        from sqlalchemy import and_, or_
        from sqlalchemy.orm import aliased
        home = aliased(Court)
        candidates = (
            query.outerjoin(home, User.home_court_id == home.id)
            .filter(or_(
                and_(User.last_lat.between(lat_lo, lat_hi),
                     User.last_lng.between(lng_lo, lng_hi)),
                and_(User.last_lat.is_(None),
                     home.latitude.between(lat_lo, lat_hi),
                     home.longitude.between(lng_lo, lng_hi)),
            ))
            .order_by(User.rating.desc())
            .limit(200)
            .all()
        )
        # Exact-distance pass over the bounding-box candidates.
        users = []
        for user in candidates:
            ulat, ulng = user.last_lat, user.last_lng
            if ulat is None and user.home_court:
                ulat, ulng = user.home_court.latitude, user.home_court.longitude
            if ulat is None or ulng is None:
                continue
            if haversine_miles(lat, lng, ulat, ulng) <= radius:
                users.append(user)
            if len(users) >= 50:
                break
    else:
        users = query.order_by(User.rating.desc()).limit(50).all()

    return jsonify({'items': _with_title_counts(users)})


def _with_title_counts(users):
    """Public dicts with each player's tournament-title count (one grouped
    query for the whole board) so the leaderboard can crown champions."""
    from backend.models import Tournament, TournamentEntry
    ids = {u.id for u in users}
    counts = {}
    if ids:
        rows = (
            db.session.query(TournamentEntry)
            .join(Tournament, Tournament.champion_entry_id == TournamentEntry.id)
            .filter(
                Tournament.status == 'completed',
                db.or_(
                    TournamentEntry.player1_id.in_(ids),
                    TournamentEntry.player2_id.in_(ids),
                ),
            )
            .all()
        )
        for entry in rows:
            for uid in (entry.player1_id, entry.player2_id):
                if uid in ids:
                    counts[uid] = counts.get(uid, 0) + 1
    items = []
    for user in users:
        data = user.to_public_dict()
        data['tournament_titles'] = counts.get(user.id, 0)
        items.append(data)
    return items
