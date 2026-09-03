"""Authentication: register, login, current-user profile."""
import base64
import hashlib
import json
import re
import secrets
import time
from datetime import timedelta
from functools import wraps
from html import escape as html_escape

import jwt
from flask import Blueprint, Response, current_app, g, jsonify, make_response, request

from backend.app import db
from backend.security import rate_limit
from backend.models import (
    AccountActionToken,
    BlockedUser,
    CheckIn,
    Court,
    CourtReview,
    Crew,
    CrewInvite,
    DirectChatPreference,
    FavoriteCourt,
    Friendship,
    Game,
    GameArrivalIntent,
    GameInvite,
    GamePlayer,
    Message,
    Notification,
    PlayerFeedback,
    MUTEABLE_NOTIFICATIONS,
    PlayAvailabilityPulse,
    SELF_RATING_LEVELS,
    SKILL_LEVELS,
    User,
    blocked_pair_ids,
    is_blocked_between,
    notify,
    utcnow,
)

auth_bp = Blueprint('auth', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_ACCOUNT_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{32,160}$')
_AVATAR_DATA_RE = re.compile(
    r'^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$',
)
_MAX_AVATAR_BYTES = 500 * 1024
_PASSWORD_RESET_MINUTES = 30
_EMAIL_ACTION_MINUTES = 60 * 24


def _login_rate_identity():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return ''
    email = str(payload.get('email') or '').strip().lower()
    if not email:
        return ''
    return 'account:' + hashlib.sha256(email.encode('utf-8')).hexdigest()


def _issue_token(user):
    now = int(time.time())
    return jwt.encode(
        {
            'user_id': user.id,
            'auth_version': int(user.auth_version or 1),
            'iat': now,
            'exp': now + int(current_app.config.get('JWT_TTL_SECONDS', 2592000)),
        },
        current_app.config['SECRET_KEY'],
        algorithm=current_app.config.get('JWT_ALGORITHM', 'HS256'),
    )


def _account_token_hash(raw_token):
    return hashlib.sha256(str(raw_token).encode('utf-8')).hexdigest()


def _decode_avatar_data(value):
    """Validate an uploaded image and return its canonical data URL and bytes."""
    data_url = str(value or '').strip()
    match = _AVATAR_DATA_RE.fullmatch(data_url)
    if not match:
        raise ValueError('invalid_avatar_data')
    mime_type, encoded = match.groups()
    # Reject obviously oversized input before allocating the decoded payload.
    if len(encoded) > ((_MAX_AVATAR_BYTES + 2) // 3) * 4 + 4:
        raise ValueError('avatar_too_large')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError('invalid_avatar_data') from None
    if not raw:
        raise ValueError('invalid_avatar_data')
    if len(raw) > _MAX_AVATAR_BYTES:
        raise ValueError('avatar_too_large')
    valid_magic = (
        mime_type == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')
        or mime_type == 'image/jpeg'
        and raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9')
        or mime_type == 'image/webp' and len(raw) >= 12
        and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'
    )
    if not valid_magic:
        raise ValueError('avatar_mime_mismatch')
    canonical = base64.b64encode(raw).decode('ascii')
    return mime_type, raw, f'data:{mime_type};base64,{canonical}'


def _new_account_action(user, purpose, *, pending_email='', lifetime_minutes):
    """Replace older live links for one account action and return the secret."""
    now = utcnow()
    AccountActionToken.query.filter_by(
        user_id=user.id, purpose=purpose, consumed_at=None,
    ).update({'consumed_at': now}, synchronize_session=False)
    raw_token = secrets.token_urlsafe(32)
    action = AccountActionToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=_account_token_hash(raw_token),
        pending_email=str(pending_email or '').strip().lower(),
        expires_at=now + timedelta(minutes=lifetime_minutes),
    )
    db.session.add(action)
    db.session.flush()
    return action, raw_token


def _account_action_url(kind, raw_token):
    origin = str(current_app.config.get('PUBLIC_APP_URL') or '').strip().rstrip('/')
    if not origin.startswith(('https://', 'http://')):
        raise RuntimeError('public_app_url_not_configured')
    return f'{origin}/#{kind}={raw_token}'


def _send_account_action_email(*, user, action, raw_token):
    from backend.email_delivery import send_transactional_email

    if action.purpose == 'password_reset':
        subject = 'Reset your Third Shot password'
        destination = _account_action_url('reset-password', raw_token)
        intro = 'Use this secure link to choose a new Third Shot password.'
        expiry = 'The link expires in 30 minutes and works once.'
        recipient = user.email
    elif action.purpose == 'email_change':
        subject = 'Confirm your new Third Shot email'
        destination = _account_action_url('confirm-email', raw_token)
        intro = 'Use this secure link to confirm your new email address.'
        expiry = 'The link expires in 24 hours and works once.'
        recipient = action.pending_email
    else:
        subject = 'Verify your Third Shot email'
        destination = _account_action_url('verify-email', raw_token)
        intro = 'Use this secure link to verify your Third Shot email address.'
        expiry = 'The link expires in 24 hours and works once.'
        recipient = user.email

    safe_destination = html_escape(destination, quote=True)
    send_transactional_email(
        to=recipient,
        subject=subject,
        html=(
            f'<p>{html_escape(intro)}</p>'
            f'<p><a href="{safe_destination}">Continue to Third Shot</a></p>'
            f'<p>{html_escape(expiry)} If you did not request this, ignore it.</p>'
        ),
        text=f'{intro}\n\n{destination}\n\n{expiry} If you did not request this, ignore it.',
        idempotency_key=f'account-action-{action.id}',
    )


def _active_account_action(raw_token, purpose):
    token = str(raw_token or '').strip()
    if not _ACCOUNT_TOKEN_RE.fullmatch(token):
        return None
    return (
        AccountActionToken.query.filter_by(
            token_hash=_account_token_hash(token), purpose=purpose,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )


def optional_current_user():
    auth_header = str(request.headers.get('Authorization') or '').strip()
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=[current_app.config.get('JWT_ALGORITHM', 'HS256')],
        )
    except Exception:
        return None
    user_id = payload.get('user_id')
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    # A deleted or suspended account's outstanding tokens must die
    # immediately. Keep the suspension reason private from public endpoints.
    if user is not None and user.deleted_at is not None:
        return None
    if user is not None and user.suspended_at is not None:
        g.auth_rejection = 'account_suspended'
        return None
    token_version = payload.get('auth_version', 1)
    try:
        token_version = int(token_version)
    except (TypeError, ValueError):
        return None
    if user is not None and token_version != int(user.auth_version or 1):
        return None
    g.auth_payload = payload
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = optional_current_user()
        if not user:
            if getattr(g, 'auth_rejection', '') == 'account_suspended':
                return jsonify({'error': 'account_suspended'}), 403
            return jsonify({'error': 'authentication_required'}), 401
        g.current_user = user
        # Coarse presence heartbeat, throttled so it's one write per ~5 min.
        now = utcnow()
        if user.last_active_at is None or (now - user.last_active_at).total_seconds() > 300:
            user.last_active_at = now
            db.session.commit()
        result = view(*args, **kwargs)
        issued_at = int(getattr(g, 'auth_payload', {}).get('iat') or 0)
        refresh_after = int(current_app.config.get(
            'JWT_REFRESH_AFTER_SECONDS', 60 * 60 * 24 * 7,
        ))
        if issued_at and int(time.time()) - issued_at >= max(60, refresh_after):
            response = make_response(result)
            response.headers['X-Session-Token'] = _issue_token(user)
            return response
        return result
    return wrapped


def _users_for_update_query(user_ids):
    """Build the canonical ordered User lock used before Crew mutations.

    ``populate_existing`` is essential when authentication already placed a
    User instance in the identity map: a request that waited behind account
    deletion must observe the winner's ``deleted_at`` value, not stale state.
    """
    ids = sorted({int(user_id) for user_id in user_ids})
    return (
        User.query.filter(User.id.in_(ids))
        .order_by(User.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _lock_users_for_update(user_ids):
    """Lock User rows in ascending id order and refresh identity-map state."""
    ids = sorted({int(user_id) for user_id in user_ids})
    if not ids:
        return []
    return _users_for_update_query(ids).all()


def presence_stale_cutoff(now=None):
    """Query-time presence boundary used by every live discovery surface."""
    now = now or utcnow()
    seconds = int(
        current_app.config.get('PRESENCE_STALE_AFTER_SECONDS', 1800) or 1800
    )
    return now - timedelta(seconds=max(1, seconds))


def presence_absolute_cutoff(now=None):
    """Oldest check-in that may be renewed without explicit reconfirmation."""
    now = now or utcnow()
    seconds = int(
        current_app.config.get('PRESENCE_MAX_AGE_SECONDS', 4 * 60 * 60)
        or 4 * 60 * 60
    )
    return now - timedelta(seconds=max(1, seconds))


def checkin_expires_at(checkin):
    if (
        not checkin
        or not checkin.last_presence_ping_at
        or not checkin.checked_in_at
    ):
        return None
    stale_seconds = int(
        current_app.config.get('PRESENCE_STALE_AFTER_SECONDS', 1800) or 1800
    )
    max_seconds = int(
        current_app.config.get('PRESENCE_MAX_AGE_SECONDS', 4 * 60 * 60)
        or 4 * 60 * 60
    )
    return min(
        checkin.last_presence_ping_at + timedelta(
            seconds=max(1, stale_seconds),
        ),
        checkin.checked_in_at + timedelta(seconds=max(1, max_seconds)),
    )


def checkin_is_fresh(checkin, now=None):
    return bool(
        checkin
        and checkin.checked_out_at is None
        and checkin.checked_in_at
        and checkin.checked_in_at >= presence_absolute_cutoff(now)
        and checkin.last_presence_ping_at
        and checkin.last_presence_ping_at >= presence_stale_cutoff(now)
    )


def active_checkin_for(user_id, *, fresh=False, now=None, for_update=False):
    query = CheckIn.query.filter_by(user_id=user_id, checked_out_at=None)
    if fresh:
        query = query.filter(
            CheckIn.checked_in_at >= presence_absolute_cutoff(now),
            CheckIn.last_presence_ping_at >= presence_stale_cutoff(now)
        )
    query = query.order_by(CheckIn.checked_in_at.desc(), CheckIn.id.desc())
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return query.first()


def presence_payload(user_id):
    checkin = active_checkin_for(user_id, fresh=True)
    if not checkin:
        return {'checked_in': False}
    court = checkin.court
    expires_at = checkin_expires_at(checkin)
    return {
        'checked_in': True,
        'court_id': checkin.court_id,
        'court_name': court.name if court else 'Court',
        'court_latitude': court.latitude if court else None,
        'court_longitude': court.longitude if court else None,
        'looking_for_game': bool(checkin.looking_for_game),
        'looking_expires_at': (
            expires_at.isoformat() + 'Z'
            if checkin.looking_for_game and expires_at else None
        ),
        'expires_at': expires_at.isoformat() + 'Z' if expires_at else None,
        'last_confirmed_at': (
            checkin.last_presence_ping_at.isoformat() + 'Z'
            if checkin.last_presence_ping_at else None
        ),
        'checked_in_at': checkin.checked_in_at.isoformat() + 'Z' if checkin.checked_in_at else None,
    }


def _games_to_confirm_count(user_id):
    """Games whose reported score is waiting on this user (opposing team) to confirm."""
    games = (
        Game.query.filter(Game.status == 'awaiting_confirmation')
        .join(GamePlayer)
        .filter(GamePlayer.user_id == user_id)
        .all()
    )
    count = 0
    for game in games:
        me = next((p for p in game.players if p.user_id == user_id), None)
        submitter = next(
            (p for p in game.players if p.user_id == game.score_submitted_by_id), None,
        )
        if me and submitter and me.team and submitter.team and me.team != submitter.team:
            count += 1
    return count


def _active_game_payload(user):
    """The single most relevant game for the banner.

    Priority: live game you're in > incoming challenge > score waiting on you
    > your score waiting on opponents > your next upcoming game."""
    now = utcnow()
    # Runtime import avoids the auth/games blueprint import cycle.
    from backend.routes.games import (
        _game_payload,
        _instant_nonmember_game_payload,
        _instant_rally_assembly_active,
    )
    candidates = []
    hidden_ids = blocked_pair_ids(user.id)
    fresh_checkin = active_checkin_for(user.id, fresh=True, now=now)

    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status.in_(['upcoming', 'awaiting_confirmation']),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(25)
        .all()
    )
    from datetime import timedelta
    for game in games:
        if game.is_instant and game.status == 'upcoming':
            # Keep a multi-player row available for result entry, but never
            # let a closed/no-presence roster suppress the next real event.
            if not _instant_rally_assembly_active(game, now):
                continue
        data = _game_payload(game, user.id, now=now)
        if game.is_instant and game.status == 'upcoming':
            # An instant game's primary job is to assemble real people at its
            # court. Do not infer that scoring is the next action merely from
            # its timestamp.
            rank = 0
            banner_state = (
                'assembling'
                if data['assembly_state'] == 'finding' else 'ready'
            )
        elif game.status == 'upcoming' and game.scheduled_at <= now:
            # A game hours past its start isn't "live" — the Play tab's
            # enter-the-score section owns the nagging from there.
            if game.scheduled_at < now - timedelta(hours=4):
                continue
            rank, banner_state = 0, 'live'
        elif data['awaiting_your_confirmation']:
            rank, banner_state = 2, 'confirm'
        elif game.status == 'awaiting_confirmation':
            rank, banner_state = 4, 'waiting'
        else:
            # The docked banner is for play that is actually approaching.
            # Future joined games remain in My schedule instead of occupying
            # the top of every screen for days.
            if game.scheduled_at > now + timedelta(minutes=60):
                continue
            rank, banner_state = 5, 'upcoming'
        data['banner_state'] = banner_state
        candidates.append((rank, data))

    # Private games you've been invited to (challenges + personal invites) and
    # haven't joined yet. The invite list is the source of truth, not notifications.
    invited_games = (
        Game.query.join(GameInvite)
        .filter(GameInvite.user_id == user.id, Game.status == 'upcoming')
        .order_by(Game.scheduled_at.asc())
        .limit(15)
        .all()
    )
    for game in invited_games:
        if any(player.user_id in hidden_ids for player in game.players):
            continue
        if game.is_instant and (
            not fresh_checkin
            or fresh_checkin.court_id != game.court_id
            or not _instant_rally_assembly_active(game, now)
        ):
            # A rally invitation is a live at-court action, not a durable
            # remote RSVP. It becomes irrelevant when physical presence does.
            continue
        data = _game_payload(game, user.id, now=now)
        if game.is_instant:
            data = _instant_nonmember_game_payload(data)
        if data['is_joined'] or data['spots_left'] <= 0:
            continue
        if game.is_direct_challenge:
            data['banner_state'] = 'challenge'
            candidates.append((1, data))
        else:
            data['banner_state'] = 'invited'
            candidates.append((3, data))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]['scheduled_at'] or ''))
    return candidates[0][1]


def _active_arrival_payload(user):
    """The caller's live remote reservation, never physical presence."""
    now = utcnow()
    intent = (
        GameArrivalIntent.query.filter_by(user_id=user.id, active=True)
        .filter(
            GameArrivalIntent.ended_at.is_(None),
            GameArrivalIntent.expires_at > now,
        )
        .order_by(GameArrivalIntent.id.desc())
        .first()
    )
    if not intent or not intent.game:
        return None
    from backend.routes.games import (
        _arrival_capacity,
        _instant_rally_assembly_active,
    )
    game = intent.game
    if not _instant_rally_assembly_active(game, now):
        return None
    member_ids = {player.user_id for player in game.players}
    if any(is_blocked_between(user.id, member_id) for member_id in member_ids):
        return None
    capacity = _arrival_capacity(game, now)
    if not any(item.id == intent.id for item in capacity['arrivals']):
        return None
    return {
        **intent.to_dict(now),
        'max_players': game.max_players,
        'court': game.court.to_summary_dict() if game.court else None,
        **{
            key: capacity[key]
            for key in (
                'ready_count', 'roster_count', 'on_the_way_count',
                'committed_count', 'physical_spots_left', 'spots_left',
            )
        },
    }


def _active_play_pulse_payload(user):
    """The caller's query-time-valid remote availability signal."""
    now = utcnow()
    pulse = (
        PlayAvailabilityPulse.query.filter(
            PlayAvailabilityPulse.user_id == user.id,
            PlayAvailabilityPulse.active.is_(True),
            PlayAvailabilityPulse.ended_at.is_(None),
            PlayAvailabilityPulse.expires_at > now,
        )
        .order_by(PlayAvailabilityPulse.id.desc())
        .first()
    )
    if (
        not pulse
        or not pulse.court
        or pulse.court.closed
        or pulse.court.latitude is None
        or pulse.court.longitude is None
        or active_checkin_for(user.id, fresh=True, now=now)
    ):
        return None
    if GameArrivalIntent.query.filter(
        GameArrivalIntent.user_id == user.id,
        GameArrivalIntent.active.is_(True),
        GameArrivalIntent.ended_at.is_(None),
        GameArrivalIntent.expires_at > now,
    ).first():
        return None
    if Game.query.join(GamePlayer).filter(
        GamePlayer.user_id == user.id,
        Game.status == 'upcoming',
        Game.is_instant.is_(False),
        Game.scheduled_at >= pulse.declared_at,
        Game.scheduled_at <= pulse.expires_at,
    ).first():
        return None
    from backend.routes.games import _active_live_rally_for_user
    if _active_live_rally_for_user(user.id, now):
        return None
    return pulse.to_dict(now)


def _active_tournament_payload(user):
    """The most relevant tournament for the banner: one that's under way, or
    one you're entered in (or organizing) that starts within 24h. The game
    banner still wins client-side when both exist."""
    from datetime import timedelta

    from backend.models import Tournament, TournamentEntry
    now = utcnow()
    entered = db.session.query(TournamentEntry.tournament_id).filter(
        db.or_(
            TournamentEntry.player1_id == user.id,
            TournamentEntry.player2_id == user.id,
        )
    )
    rows = (
        Tournament.query.filter(
            db.or_(Tournament.id.in_(entered), Tournament.organizer_id == user.id),
            db.or_(
                Tournament.status == 'active',
                db.and_(
                    Tournament.status == 'registration',
                    Tournament.starts_at <= now + timedelta(hours=24),
                    Tournament.starts_at >= now - timedelta(hours=6),
                ),
            ),
        )
        .order_by(Tournament.starts_at.asc())
        .limit(5)
        .all()
    )
    if not rows:
        return None
    # An in-progress bracket beats one that's merely imminent.
    rows.sort(key=lambda t: (t.status != 'active', t.starts_at))
    tournament = rows[0]
    data = tournament.to_dict(user.id)
    data['banner_state'] = 'live' if tournament.status == 'active' else 'soon'
    my_entry = tournament.entry_for(user.id)
    data['my_checked_in'] = bool(my_entry and my_entry.checked_in_at)
    # Who this player faces next — the banner's live-state headline.
    data['my_next_opponent'] = None
    if my_entry and tournament.status == 'active':
        for m in sorted(tournament.matches, key=lambda m: (m.round, m.position)):
            if (m.winner_entry_id is None
                    and my_entry.id in (m.entry1_id, m.entry2_id)
                    and m.entry1_id and m.entry2_id):
                opp_id = m.entry2_id if m.entry1_id == my_entry.id else m.entry1_id
                opp = next((e for e in tournament.entries if e.id == opp_id), None)
                if opp:
                    data['my_next_opponent'] = opp.display_name()
                break
    return data


def _active_league_payload(user):
    """The active league action that most needs this player's attention."""
    from backend.models import League, LeagueMember
    from backend.routes.leagues import (
        _league_payload,
        _league_result_confirmer_id,
        _round_deadline,
    )

    joined = db.session.query(LeagueMember.league_id).filter(
        LeagueMember.user_id == user.id,
    )
    leagues = (
        League.query
        .filter(
            League.status == 'active',
            db.or_(League.id.in_(joined), League.organizer_id == user.id),
        )
        .order_by(League.round_started_at.asc(), League.id.asc())
        .limit(20)
        .all()
    )
    if not leagues:
        return None

    ranked = []
    for league in leagues:
        current = [
            match for match in league.matches
            if match.round == league.current_round
        ]
        confirmations = [
            match for match in current
            if match.effective_result_state() == 'awaiting_confirmation'
            and _league_result_confirmer_id(match) == user.id
        ]
        organizer_results = [
            match for match in current
            if league.organizer_id == user.id
            and match.effective_result_state() in (
                'awaiting_confirmation', 'disputed',
            )
        ]
        unplayed = [
            match for match in current
            if match.effective_result_state() == 'unreported'
            and user.id in (match.player1_id, match.player2_id)
        ]
        if confirmations:
            priority, state, matches = 0, 'confirm', confirmations
        elif organizer_results:
            priority, state, matches = 1, 'resolve', organizer_results
        elif unplayed:
            priority, state, matches = 2, 'play', unplayed
        else:
            priority, state, matches = 3, 'active', []
        action_match = min(
            matches,
            key=lambda match: (match.reported_at or match.created_at, match.id),
            default=None,
        )
        ranked.append((
            priority,
            _round_deadline(league) or league.round_started_at or league.starts_at,
            league.id,
            league,
            state,
            action_match,
        ))
    _, _, _, league, banner_state, action_match = min(ranked)
    data = _league_payload(league, user.id)
    data['banner_state'] = banner_state
    if action_match:
        data['action_match_id'] = action_match.id
    return data


def _competition_actions_payload(user):
    """Account-wide competition work for the persistent Compete badge."""
    from backend.models import League, LeagueMember, Tournament, TournamentEntry
    from backend.routes.leagues import _league_action_summary
    from backend.routes.tournaments import _tournament_action_summary

    tournament_ids = db.session.query(TournamentEntry.tournament_id).filter(
        db.or_(
            TournamentEntry.player1_id == user.id,
            TournamentEntry.player2_id == user.id,
            TournamentEntry.partner_invitee_id == user.id,
        )
    )
    tournaments = Tournament.query.filter(
        Tournament.status.in_(['registration', 'active']),
        db.or_(
            Tournament.organizer_id == user.id,
            Tournament.id.in_(tournament_ids),
        ),
    ).all()
    tournament_count = sum(
        _tournament_action_summary(tournament, user.id)['pending_action_count']
        for tournament in tournaments
    )

    league_ids = db.session.query(LeagueMember.league_id).filter(
        LeagueMember.user_id == user.id,
    )
    leagues = League.query.filter(
        League.status == 'active',
        db.or_(League.organizer_id == user.id, League.id.in_(league_ids)),
    ).all()
    league_count = sum(
        _league_action_summary(league, user.id)['pending_action_count']
        for league in leagues
    )
    return {
        'count': tournament_count + league_count,
        'tournaments': tournament_count,
        'leagues': league_count,
    }


def _me_payload(user):
    # Lazy import avoids the auth/chat blueprint import cycle at startup.
    from backend.routes.chat import community_room_unread_counts

    hidden_ids = blocked_pair_ids(user.id)
    unread_message_query = Message.query.filter_by(recipient_id=user.id, read_at=None)
    muted_partner_ids = db.session.query(DirectChatPreference.partner_id).filter(
        DirectChatPreference.user_id == user.id,
        DirectChatPreference.muted_at.isnot(None),
    )
    unread_message_query = unread_message_query.filter(
        Message.sender_id.notin_(muted_partner_ids),
    )
    pending_request_query = Friendship.query.filter_by(
        addressee_id=user.id, status='pending',
    )
    unread_notification_query = Notification.query.filter_by(
        user_id=user.id, read=False,
    )
    latest_query = Notification.query.filter_by(user_id=user.id)
    if hidden_ids:
        unread_message_query = unread_message_query.filter(
            Message.sender_id.notin_(hidden_ids),
        )
        pending_request_query = pending_request_query.filter(
            Friendship.requester_id.notin_(hidden_ids),
        )
        unread_notification_query = unread_notification_query.filter(db.or_(
            Notification.related_user_id.is_(None),
            Notification.related_user_id.notin_(hidden_ids),
        ))
        latest_query = latest_query.filter(db.or_(
            Notification.related_user_id.is_(None),
            Notification.related_user_id.notin_(hidden_ids),
        ))
    unread_messages = unread_message_query.count()
    pending_requests = pending_request_query.count()
    unread_notifications = unread_notification_query.count()
    latest = latest_query.order_by(Notification.id.desc()).first()
    community_unread = community_room_unread_counts(user.id)
    pending_crew_invites = _pending_crew_invites_payload(user.id, hidden_ids)
    return {
        'user': user.to_dict(),
        'presence': presence_payload(user.id),
        'unread_messages': unread_messages,
        'community_room_unread': community_unread['total'],
        'community_message_unread': community_unread['messages'],
        'community_group_unread': community_unread['groups'],
        'pending_friend_requests': pending_requests,
        'pending_crew_invites': pending_crew_invites,
        'unread_notifications': unread_notifications,
        'games_to_confirm': _games_to_confirm_count(user.id),
        'latest_notification': latest.to_dict() if latest else None,
        'active_game': _active_game_payload(user),
        'active_arrival': _active_arrival_payload(user),
        'active_play_pulse': _active_play_pulse_payload(user),
        'active_tournament': _active_tournament_payload(user),
        'active_league': _active_league_payload(user),
        'competition_actions': _competition_actions_payload(user),
        'muteable_notifications': MUTEABLE_NOTIFICATIONS,
        'operator_role': user.operator_role or None,
        'can_review_businesses': user.operator_role in {'reviewer', 'admin'},
        'mfa': {
            'enabled': bool(user.mfa_enabled),
            'recovery_codes_remaining': _recovery_code_count(user),
        },
    }


def _pending_crew_invites_payload(user_id, hidden_ids=None):
    """Serialize actionable Crew consent independently of notifications.

    Notification rows are delivery hints that a player may read or clear. The
    CrewInvite row is the durable source of truth for both the badge count and
    the invitation sheet, so `/me` must derive both from the same filtered
    collection.
    """
    hidden_ids = set(hidden_ids or ())
    pending = (
        CrewInvite.query
        .join(Crew, Crew.id == CrewInvite.crew_id)
        .filter(
            CrewInvite.invitee_id == user_id,
            CrewInvite.status == 'pending',
            Crew.archived_at.is_(None),
        )
        .order_by(CrewInvite.updated_at.desc(), CrewInvite.id.desc())
        .all()
    )
    items = []
    for invite in pending:
        crew = invite.crew
        if crew.owner_id in hidden_ids or invite.invited_by_id in hidden_ids:
            continue
        inviter = invite.invited_by
        invited_at = invite.updated_at or invite.created_at
        items.append({
            'invite_id': invite.id,
            'crew_id': crew.id,
            'crew_name': crew.name,
            'owner_id': crew.owner_id,
            'invited_by_id': invite.invited_by_id,
            'invited_by_name': (
                inviter.display_name
                if inviter and inviter.deleted_at is None else 'A player'
            ),
            'source_game_id': crew.source_game_id,
            'default_court_id': crew.default_court_id,
            'default_court_name': (
                crew.default_court.name if crew.default_court else None
            ),
            'invited_at': invited_at.isoformat() + 'Z' if invited_at else None,
        })
    return {'count': len(items), 'items': items}


def _recovery_code_count(user):
    try:
        values = json.loads(user.mfa_recovery_codes or '[]')
    except (TypeError, ValueError):
        return 0
    return len(values) if isinstance(values, list) else 0


@auth_bp.post('/client-errors')
@rate_limit(10, 300)
def report_client_error():
    """Browser-side crash reports land in the hosted service log
    — no storage, tight rate limit, hard truncation. Anonymous by design so
    login-screen breakage reports too."""
    payload = request.get_json(silent=True) or {}
    user = optional_current_user()
    current_app.logger.error(
        'CLIENT ERROR%s: %s | at %s | %s',
        f' (user {user.id})' if user else '',
        str(payload.get('message') or '')[:300],
        str(payload.get('url') or '')[:200],
        str(payload.get('stack') or '')[:600],
    )
    return '', 204


@auth_bp.post('/feedback')
@rate_limit(5, 3600)
@login_required
def send_feedback():
    """Persist player feedback for the operator queue and log a short trace."""
    payload = request.get_json(silent=True) or {}
    message = str(payload.get('message') or '').strip()
    if len(message) < 3:
        return jsonify({'error': 'message_required'}), 400
    feedback = PlayerFeedback(
        user_id=g.current_user.id,
        message=message[:2000],
        context=str(payload.get('context') or payload.get('url') or '').strip()[:300],
    )
    db.session.add(feedback)
    db.session.commit()
    current_app.logger.warning(
        'USER FEEDBACK #%s from %s (#%s, %s): %s',
        feedback.id,
        g.current_user.display_name,
        g.current_user.id,
        g.current_user.email,
        message[:2000],
    )
    return jsonify({'sent': True, 'feedback_id': feedback.id})


@auth_bp.post('/auth/register')
@rate_limit(10, 600)
def register():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email') or '').strip().lower()
    password = str(payload.get('password') or '')
    display_name = str(payload.get('display_name') or '').strip()

    if not _EMAIL_RE.match(email):
        return jsonify({'error': 'invalid_email'}), 400
    if len(password) < 6:
        return jsonify({'error': 'password_too_short'}), 400
    if not display_name:
        return jsonify({'error': 'display_name_required'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'email_taken'}), 409

    user = User(email=email, display_name=display_name[:120])
    invited_by_user_id = payload.get('invited_by_user_id')
    if invited_by_user_id not in (None, '', 0):
        try:
            inviter = db.session.get(User, int(invited_by_user_id))
        except (TypeError, ValueError):
            inviter = None
        if inviter and inviter.deleted_at is None:
            user.invited_by_user_id = inviter.id
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    verification, verification_token = _new_account_action(
        user,
        'email_verification',
        lifetime_minutes=_EMAIL_ACTION_MINUTES,
    )
    db.session.commit()
    verification_sent = False
    try:
        _send_account_action_email(
            user=user, action=verification, raw_token=verification_token,
        )
        verification_sent = True
    except Exception:
        # Registration remains usable if transactional delivery is temporarily
        # unavailable; Settings exposes an explicit retry with honest status.
        current_app.logger.exception(
            'Registration verification email failed for user %s', user.id,
        )
    return jsonify({
        'token': _issue_token(user),
        'verification_sent': verification_sent,
        **_me_payload(user),
    }), 201


@auth_bp.post('/auth/forgot-password')
@rate_limit(5, 900)
def forgot_password():
    """Send a one-time reset link without revealing whether an email exists."""
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email') or '').strip().lower()
    if _EMAIL_RE.fullmatch(email):
        user = User.query.filter_by(email=email, deleted_at=None).first()
        if user:
            action, raw_token = _new_account_action(
                user,
                'password_reset',
                lifetime_minutes=_PASSWORD_RESET_MINUTES,
            )
            db.session.commit()
            try:
                _send_account_action_email(
                    user=user, action=action, raw_token=raw_token,
                )
            except Exception:
                current_app.logger.exception(
                    'Password reset delivery failed for user %s', user.id,
                )
    # Keep status/body identical for unknown, malformed, and known addresses.
    return jsonify({
        'ok': True,
        'message': 'If that email is registered, a reset link is on its way.',
    }), 202


@auth_bp.post('/auth/reset-password')
@rate_limit(10, 900)
def reset_password():
    payload = request.get_json(silent=True) or {}
    new_password = str(payload.get('new_password') or '')
    if len(new_password) < 8:
        return jsonify({'error': 'reset_password_too_short'}), 400
    action = _active_account_action(payload.get('token'), 'password_reset')
    if not action or not action.is_active():
        return jsonify({'error': 'reset_link_invalid_or_expired'}), 400
    user = db.session.get(User, action.user_id)
    if not user or user.deleted_at is not None:
        return jsonify({'error': 'reset_link_invalid_or_expired'}), 400
    now = utcnow()
    user.set_password(new_password)
    user.auth_version = int(user.auth_version or 1) + 1
    action.consumed_at = now
    AccountActionToken.query.filter(
        AccountActionToken.user_id == user.id,
        AccountActionToken.purpose == 'password_reset',
        AccountActionToken.consumed_at.is_(None),
    ).update({'consumed_at': now}, synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True})


@auth_bp.post('/auth/verify-email/request')
@rate_limit(5, 3600)
@login_required
def request_email_verification():
    user = g.current_user
    if user.email_verified_at is not None:
        return jsonify({'ok': True, 'already_verified': True})
    action, raw_token = _new_account_action(
        user,
        'email_verification',
        lifetime_minutes=_EMAIL_ACTION_MINUTES,
    )
    db.session.commit()
    try:
        _send_account_action_email(
            user=user, action=action, raw_token=raw_token,
        )
    except Exception:
        current_app.logger.exception(
            'Email verification delivery failed for user %s', user.id,
        )
        return jsonify({'error': 'email_delivery_unavailable'}), 503
    return jsonify({'ok': True, 'sent': True})


@auth_bp.post('/auth/verify-email')
@rate_limit(10, 900)
def verify_email():
    payload = request.get_json(silent=True) or {}
    action = _active_account_action(payload.get('token'), 'email_verification')
    if not action or not action.is_active():
        return jsonify({'error': 'verification_link_invalid_or_expired'}), 400
    user = db.session.get(User, action.user_id)
    if not user or user.deleted_at is not None:
        return jsonify({'error': 'verification_link_invalid_or_expired'}), 400
    now = utcnow()
    user.email_verified_at = now
    action.consumed_at = now
    AccountActionToken.query.filter(
        AccountActionToken.user_id == user.id,
        AccountActionToken.purpose == 'email_verification',
        AccountActionToken.consumed_at.is_(None),
    ).update({'consumed_at': now}, synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True})


@auth_bp.post('/auth/change-email')
@rate_limit(5, 3600)
@login_required
def request_email_change():
    payload = request.get_json(silent=True) or {}
    user = g.current_user
    if not user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    new_email = str(payload.get('new_email') or '').strip().lower()
    if not _EMAIL_RE.fullmatch(new_email):
        return jsonify({'error': 'invalid_email'}), 400
    if new_email == user.email:
        return jsonify({'error': 'email_unchanged'}), 409
    if User.query.filter(User.email == new_email, User.id != user.id).first():
        return jsonify({'error': 'email_taken'}), 409
    action, raw_token = _new_account_action(
        user,
        'email_change',
        pending_email=new_email,
        lifetime_minutes=_EMAIL_ACTION_MINUTES,
    )
    db.session.commit()
    try:
        _send_account_action_email(
            user=user, action=action, raw_token=raw_token,
        )
    except Exception:
        current_app.logger.exception(
            'Email-change delivery failed for user %s', user.id,
        )
        return jsonify({'error': 'email_delivery_unavailable'}), 503
    return jsonify({'ok': True, 'sent': True})


@auth_bp.post('/auth/confirm-email-change')
@rate_limit(10, 900)
def confirm_email_change():
    payload = request.get_json(silent=True) or {}
    action = _active_account_action(payload.get('token'), 'email_change')
    if not action or not action.is_active() or not action.pending_email:
        return jsonify({'error': 'verification_link_invalid_or_expired'}), 400
    user = db.session.get(User, action.user_id)
    if not user or user.deleted_at is not None:
        return jsonify({'error': 'verification_link_invalid_or_expired'}), 400
    if User.query.filter(
        User.email == action.pending_email, User.id != user.id,
    ).first():
        return jsonify({'error': 'email_taken'}), 409
    now = utcnow()
    user.email = action.pending_email
    user.email_verified_at = now
    user.auth_version = int(user.auth_version or 1) + 1
    action.consumed_at = now
    AccountActionToken.query.filter(
        AccountActionToken.user_id == user.id,
        AccountActionToken.purpose == 'email_change',
        AccountActionToken.consumed_at.is_(None),
    ).update({'consumed_at': now}, synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True})


@auth_bp.post('/auth/login')
@rate_limit(20, 300, key_func=_login_rate_identity)
@rate_limit(100, 300)
def login():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_request'}), 400
    email = str(payload.get('email') or '').strip().lower()
    password = str(payload.get('password') or '')

    # Recovery codes are one-time credentials. Serialize login against this
    # account so two concurrent requests cannot both consume the same hash.
    user = (
        User.query.filter_by(email=email)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not user or not user.check_password(password):
        return jsonify({'error': 'invalid_credentials'}), 401
    if user.suspended_at is not None:
        return jsonify({'error': 'account_suspended'}), 403
    if user.mfa_enabled:
        from backend.services.mfa import MFAError, verify_user_mfa
        mfa_code = str(payload.get('mfa_code') or '').strip()
        if not mfa_code:
            return jsonify({'error': 'mfa_required'}), 401
        try:
            valid, used_recovery = verify_user_mfa(user, mfa_code)
        except MFAError:
            current_app.logger.exception('MFA secret unavailable for user %s', user.id)
            return jsonify({'error': 'mfa_unavailable'}), 503
        if not valid:
            return jsonify({'error': 'invalid_mfa_code'}), 401
        if used_recovery:
            # Recovery use is a security event: consume it transactionally and
            # revoke any sessions that may have been issued before recovery.
            user.auth_version = int(user.auth_version or 1) + 1
            db.session.commit()
    return jsonify({'token': _issue_token(user), **_me_payload(user)})


def _maybe_weekly_recap(user):
    """First app-open each ISO week recaps the previous week's play as a
    notification. The marker advances even on quiet weeks, so this stays
    one cheap string compare on every later /me."""
    from datetime import timedelta

    now = utcnow()
    year, week, _ = (now - timedelta(days=7)).isocalendar()
    prev_week = f'{year}-W{week:02d}'
    if user.last_recap_week == prev_week:
        return
    user.last_recap_week = prev_week

    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
        .order_by(Game.completed_at.desc())
        .limit(60)
        .all()
    )
    played = [g_ for g_ in games if g_.completed_at.isocalendar()[:2] == (year, week)]

    # Tournaments won that week headline the recap.
    from backend.models import Tournament, TournamentEntry
    titles_won = (
        Tournament.query
        .join(TournamentEntry, Tournament.champion_entry_id == TournamentEntry.id)
        .filter(
            Tournament.status == 'completed',
            Tournament.completed_at.isnot(None),
            db.or_(
                TournamentEntry.player1_id == user.id,
                TournamentEntry.player2_id == user.id,
            ),
        )
        .order_by(Tournament.completed_at.desc())
        .limit(10)
        .all()
    )
    titles_won = [t for t in titles_won
                  if t.completed_at.isocalendar()[:2] == (year, week)]

    if not played and not titles_won:
        db.session.commit()  # persist the marker; nothing to say
        return

    wins = losses = 0
    delta = 0
    for game in played:
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if not mine:
            continue
        if mine.rating_delta is not None:
            delta += mine.rating_delta
        if mine.team and game.score_team1 is not None:
            if (game.score_team1 > game.score_team2) == (mine.team == 1):
                wins += 1
            else:
                losses += 1
    if played:
        casual_count = sum(game.game_type != 'ranked' for game in played)
        ranked_count = len(played) - casual_count
        play_parts = []
        if casual_count:
            play_parts.append(
                f'{casual_count} casual play session'
                f'{"s" if casual_count != 1 else ""}'
            )
        if ranked_count:
            play_parts.append(
                f'{ranked_count} ranked match'
                f'{"es" if ranked_count != 1 else ""}'
            )
        title = f'Your week on the courts: {" and ".join(play_parts)}'
        if wins or losses:
            title += f', {wins}–{losses}'
        if titles_won:
            title += f' — and you won {titles_won[0].name}!' if len(titles_won) == 1 \
                else f' — and {len(titles_won)} tournament titles!'
    else:
        title = (f'Champion week — you won {titles_won[0].name}!'
                 if len(titles_won) == 1
                 else f'Champion week — {len(titles_won)} tournament titles!')
    body = f'{"+" if delta >= 0 else ""}{delta} rating' if delta else 'See your stats on the profile tab'
    notify(user.id, 'weekly_recap', title, body,
           related_tournament_id=titles_won[0].id if titles_won else None)
    db.session.commit()


def _maybe_nearby_games_digest(user):
    """Weekly nearby play-session/match digest, skipping empty local weeks."""
    from datetime import timedelta

    if user.home_lat is None:
        return
    now = utcnow()
    year, week, _ = now.isocalendar()
    this_week = f'{year}-W{week:02d}'
    if user.last_games_digest_week == this_week:
        return
    user.last_games_digest_week = this_week

    from backend.routes.courts import haversine_miles
    candidates = (
        Game.query.join(Court, Game.court_id == Court.id)
        .filter(
            Game.status == 'upcoming',
            Game.visibility == 'open',
            Game.creator_id != user.id,
            Game.scheduled_at >= now,
            Game.scheduled_at <= now + timedelta(days=7),
            # ~35mi bbox prefilter; the haversine below applies the real radius.
            Court.latitude.between(user.home_lat - 0.5, user.home_lat + 0.5),
            Court.longitude.between(user.home_lng - 0.6, user.home_lng + 0.6),
        )
        .limit(50)
        .all()
    )
    joined = {gp.game_id for gp in GamePlayer.query.filter_by(user_id=user.id).all()}
    nearby = [
        g_ for g_ in candidates
        if g_.id not in joined and g_.court and g_.court.latitude is not None
        and haversine_miles(user.home_lat, user.home_lng,
                            g_.court.latitude, g_.court.longitude) <= 25
    ]
    if nearby:
        casual_count = sum(game.game_type != 'ranked' for game in nearby)
        ranked_count = len(nearby) - casual_count
        play_parts = []
        if casual_count:
            play_parts.append(
                f'{casual_count} open play session'
                f'{"s" if casual_count != 1 else ""}'
            )
        if ranked_count:
            play_parts.append(
                f'{ranked_count} open ranked match'
                f'{"es" if ranked_count != 1 else ""}'
            )
        notify(user.id, 'nearby_games',
               f'{" and ".join(play_parts)} near you this week',
               related_game_id=nearby[0].id)
    db.session.commit()


def _maybe_streak_nag(user, now=None):
    """Weekend heads-up when last week's play is about to lapse: played last
    ISO week, nothing yet this week, and it's Sat/Sun. Once per week, muteable.
    `now` is injectable so tests aren't chained to the real weekday."""
    from datetime import timedelta

    now = now or utcnow()
    if now.isoweekday() < 6:
        return
    year, week, _ = now.isocalendar()
    this_week = f'{year}-W{week:02d}'
    if user.last_streak_nag_week == this_week:
        return
    user.last_streak_nag_week = this_week

    recent = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
            Game.completed_at >= now - timedelta(days=15),
        )
        .all()
    )
    prev_week = (now - timedelta(days=7)).isocalendar()[:2]
    cur_week = now.isocalendar()[:2]
    played_prev = any(g_.completed_at.isocalendar()[:2] == prev_week for g_ in recent)
    played_cur = any(g_.completed_at.isocalendar()[:2] == cur_week for g_ in recent)
    if played_prev and not played_cur:
        notify(user.id, 'streak_nag',
               'Your play streak ends Sunday — plan a play session or match this week')
    db.session.commit()


@auth_bp.get('/me')
@login_required
def me():
    # Reads stay side-effect free. Lifecycle sweeps, reminders, and weekly
    # nudges run through the authenticated scheduled maintenance endpoint.
    return jsonify(_me_payload(g.current_user))


@auth_bp.post('/auth/change-password')
@rate_limit(10, 3600)
@login_required
def change_password():
    user = g.current_user
    payload = request.get_json(silent=True) or {}
    # 403, not 401: the client treats 401 as an expired session and logs out.
    if not user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    new_password = str(payload.get('new_password') or '')
    if len(new_password) < 6:
        return jsonify({'error': 'password_too_short'}), 400
    user.set_password(new_password)
    user.auth_version = int(user.auth_version or 1) + 1
    db.session.commit()
    return jsonify({'ok': True, 'token': _issue_token(user)})


@auth_bp.post('/auth/mfa/setup')
@rate_limit(5, 3600)
@login_required
def setup_mfa():
    """Create an encrypted pending TOTP seed and reveal it exactly once."""
    payload = request.get_json(silent=True) or {}
    if not g.current_user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    if g.current_user.mfa_enabled:
        return jsonify({'error': 'mfa_already_enabled'}), 409
    from backend.services.mfa import (
        MFAError, encrypt_secret, new_totp_secret, otpauth_uri,
    )
    secret = new_totp_secret()
    try:
        encrypted = encrypt_secret(secret)
    except MFAError:
        return jsonify({'error': 'mfa_unavailable'}), 503
    g.current_user.mfa_secret_encrypted = encrypted
    g.current_user.mfa_recovery_codes = '[]'
    db.session.commit()
    return jsonify({
        'secret': secret,
        'otpauth_uri': otpauth_uri(secret, g.current_user.email),
        'enabled': False,
    })


@auth_bp.post('/auth/mfa/enable')
@rate_limit(10, 3600)
@login_required
def enable_mfa():
    payload = request.get_json(silent=True) or {}
    user = g.current_user
    if user.mfa_enabled:
        return jsonify({'error': 'mfa_already_enabled'}), 409
    if not user.mfa_secret_encrypted:
        return jsonify({'error': 'mfa_setup_required'}), 409
    from backend.services.mfa import (
        MFAError, decrypt_secret, hash_recovery_codes, new_recovery_codes,
        verify_totp,
    )
    try:
        valid = verify_totp(
            decrypt_secret(user.mfa_secret_encrypted), payload.get('code'),
        )
    except MFAError:
        return jsonify({'error': 'mfa_unavailable'}), 503
    if not valid:
        return jsonify({'error': 'invalid_mfa_code'}), 400
    codes = new_recovery_codes()
    user.mfa_enabled = True
    user.mfa_enabled_at = utcnow()
    user.mfa_recovery_codes = hash_recovery_codes(codes)
    user.auth_version = int(user.auth_version or 1) + 1
    db.session.commit()
    return jsonify({
        'enabled': True,
        'recovery_codes': codes,
        'token': _issue_token(user),
    })


@auth_bp.post('/auth/mfa/disable')
@rate_limit(5, 3600)
@login_required
def disable_mfa():
    payload = request.get_json(silent=True) or {}
    user = g.current_user
    if not user.mfa_enabled:
        return jsonify({'error': 'mfa_not_enabled'}), 409
    if not user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    from backend.services.mfa import MFAError, decrypt_secret, verify_totp
    try:
        valid = verify_totp(
            decrypt_secret(user.mfa_secret_encrypted), payload.get('code'),
        )
    except MFAError:
        return jsonify({'error': 'mfa_unavailable'}), 503
    if not valid:
        return jsonify({'error': 'invalid_mfa_code'}), 400
    user.mfa_enabled = False
    user.mfa_enabled_at = None
    user.mfa_secret_encrypted = ''
    user.mfa_recovery_codes = '[]'
    user.auth_version = int(user.auth_version or 1) + 1
    db.session.commit()
    return jsonify({'enabled': False, 'token': _issue_token(user)})


@auth_bp.post('/auth/sessions/revoke-others')
@rate_limit(5, 3600)
@login_required
def revoke_other_sessions():
    """Revoke every older JWT and replace the caller's current credential."""
    payload = request.get_json(silent=True) or {}
    user = g.current_user
    if not user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    if user.mfa_enabled:
        from backend.services.mfa import MFAError, verify_user_mfa
        try:
            valid, _used_recovery = verify_user_mfa(
                user, payload.get('mfa_code'), allow_recovery=False,
            )
        except MFAError:
            return jsonify({'error': 'mfa_unavailable'}), 503
        if not valid:
            return jsonify({'error': 'invalid_mfa_code'}), 403
    user.auth_version = int(user.auth_version or 1) + 1
    db.session.commit()
    return jsonify({'revoked': True, 'token': _issue_token(user)})


def _account_deletion_crew_lock_snapshot(user_id):
    """Discover the Crew/User closure deletion may mutate, without row locks.

    Crew creation locks every participant User before inserting its Crew and
    all existing Crew mutations follow the same User-before-Crew order. This
    read-only snapshot can therefore be stabilized by locking its complete
    User set, checking once more for an expansion, and retrying if a creator
    committed while the deletion request was waiting.
    """
    from backend.models import Crew, CrewChatRead, CrewInvite, CrewMember

    crew_ids = {
        row[0] for row in db.session.query(Crew.id).filter(
            Crew.owner_id == user_id,
        ).all()
    }
    crew_ids.update(
        row[0] for row in db.session.query(CrewMember.crew_id).filter(
            CrewMember.user_id == user_id,
        ).all()
    )
    crew_ids.update(
        row[0] for row in db.session.query(CrewInvite.crew_id).filter(db.or_(
            CrewInvite.invitee_id == user_id,
            CrewInvite.invited_by_id == user_id,
        )).all()
    )
    # A stale marker is personal data too. Normally leave/remove already
    # deletes it, but including it keeps deletion's defensive cleanup under
    # the same Crew lock protocol.
    crew_ids.update(
        row[0] for row in db.session.query(CrewChatRead.crew_id).filter(
            CrewChatRead.user_id == user_id,
        ).all()
    )

    user_ids = {user_id}
    if crew_ids:
        user_ids.update(
            row[0] for row in db.session.query(Crew.owner_id).filter(
                Crew.id.in_(crew_ids),
            ).all()
        )
        user_ids.update(
            row[0] for row in db.session.query(CrewMember.user_id).filter(
                CrewMember.crew_id.in_(crew_ids),
            ).all()
        )
        for invitee_id, invited_by_id in db.session.query(
            CrewInvite.invitee_id, CrewInvite.invited_by_id,
        ).filter(CrewInvite.crew_id.in_(crew_ids)).all():
            user_ids.update((invitee_id, invited_by_id))

    return frozenset(crew_ids), frozenset(user_ids)


def _lock_stable_account_deletion_crew_users(user_id, max_attempts=5):
    """Lock a stable Crew-related User closure before any Crew row lock."""
    for _ in range(max_attempts):
        crew_ids, user_ids = _account_deletion_crew_lock_snapshot(user_id)
        locked_users = _lock_users_for_update(user_ids)
        current_crew_ids, current_user_ids = (
            _account_deletion_crew_lock_snapshot(user_id)
        )
        if (
            current_crew_ids.issubset(crew_ids)
            and current_user_ids.issubset(user_ids)
        ):
            return locked_users, current_crew_ids

        # A creator that locked the deleting User first may have committed a
        # new Crew while this request waited. Release the partial lock set and
        # retry from the expanded snapshot so every transaction keeps the same
        # ascending User -> Crew order.
        db.session.rollback()

    raise RuntimeError(
        'Could not stabilize account-deletion Crew lock snapshot'
    )


def _reconcile_crews_for_account_deletion(user_id, affected_crew_ids):
    """Remove personal crew state while retaining durable crew/game history."""
    # Lazy imports avoid coupling auth startup to the crew route module.
    from backend.models import Crew, CrewChatRead, CrewInvite, CrewMember

    now = utcnow()
    crews = (
        Crew.query
        .filter(Crew.id.in_(affected_crew_ids))
        .order_by(Crew.id.asc())
        .with_for_update()
        .all()
    )

    # Invitations addressed to or authored by the deleted account are personal
    # data. Any other pending invitations on an owned crew are revoked below
    # before ownership transfers.
    CrewInvite.query.filter(db.or_(
        CrewInvite.invitee_id == user_id,
        CrewInvite.invited_by_id == user_id,
    )).delete(
        synchronize_session=False,
    )
    for crew in crews:
        # Crew.invites is select-in loaded; invalidate the collection after the
        # bulk privacy delete so pending_count cannot serialize stale consent.
        db.session.expire(crew, ['invites'])

    for crew in crews:
        roster_changed = False
        membership = CrewMember.query.filter_by(
            crew_id=crew.id, user_id=user_id,
        ).first()
        if membership is not None:
            crew.members.remove(membership)
            roster_changed = True

        CrewChatRead.query.filter_by(
            crew_id=crew.id, user_id=user_id,
        ).delete(synchronize_session=False)

        if crew.owner_id == user_id:
            for invite in CrewInvite.query.filter_by(
                crew_id=crew.id, status='pending',
            ).order_by(CrewInvite.id.asc()).all():
                invite.status = 'revoked'
                invite.resolved_at = now

            replacement = (
                CrewMember.query
                .join(User, User.id == CrewMember.user_id)
                .filter(
                    CrewMember.crew_id == crew.id,
                    CrewMember.user_id != user_id,
                    User.deleted_at.is_(None),
                )
                .order_by(CrewMember.created_at.asc(), CrewMember.id.asc())
                .first()
            )
            if replacement is not None:
                crew.owner_id = replacement.user_id
                # The owner is implicit in Crew.owner_id and must not also
                # remain in the accepted non-owner membership table.
                crew.members.remove(replacement)
            else:
                # Keep the row so historical Game.crew_id references remain
                # valid; an empty crew is retired rather than hard-deleted.
                crew.archived_at = crew.archived_at or now
            roster_changed = True

        if roster_changed:
            crew.roster_version = int(crew.roster_version or 0) + 1
            from backend.services.groups import sync_group_identity
            sync_group_identity('crew', crew)

    # Defense in depth for stale read markers whose crew was already removed or
    # otherwise absent from the affected-row query above.
    CrewChatRead.query.filter_by(user_id=user_id).delete(
        synchronize_session=False,
    )


def _account_deletion_business_replacement(business, user_id):
    """Choose the same safe business successor for preview and deletion."""
    from backend.models import BusinessOrganizationMember

    if not business.organization_id:
        return None
    return (
        BusinessOrganizationMember.query
        .join(User, User.id == BusinessOrganizationMember.user_id)
        .filter(
            BusinessOrganizationMember.organization_id
            == business.organization_id,
            BusinessOrganizationMember.user_id != user_id,
            BusinessOrganizationMember.role.in_(['owner', 'admin']),
            User.deleted_at.is_(None),
        )
        .order_by(
            db.case(
                (BusinessOrganizationMember.role == 'owner', 0),
                else_=1,
            ),
            BusinessOrganizationMember.accepted_at.asc(),
            BusinessOrganizationMember.id.asc(),
        )
        .first()
    )


def _account_deletion_impact(user):
    """Describe every account-owned product surface deletion will change.

    This endpoint is intentionally generated from the same rows and successor
    rules as deletion itself.  The client can therefore name the user's real
    tournaments, leagues, Communities, and businesses instead of presenting a
    generic warning that drifts from server behaviour.
    """
    from backend.models import (
        BusinessProfile,
        ClubMember,
        League,
        Tournament,
    )

    tournaments = []
    for tournament in (
        Tournament.query.filter(
            Tournament.organizer_id == user.id,
            Tournament.status.in_(['registration', 'active']),
        )
        .order_by(Tournament.starts_at.asc(), Tournament.id.asc())
        .all()
    ):
        entrant_ids = set()
        for entry in tournament.entries:
            entrant_ids.update(filter(None, (
                entry.player1_id,
                entry.player2_id,
                entry.partner_invitee_id,
            )))
        entrant_ids.discard(user.id)
        tournaments.append({
            'id': tournament.id,
            'name': tournament.name,
            'status': tournament.status,
            'action': 'cancel',
            'people_notified': len(entrant_ids),
        })

    leagues = []
    for league in (
        League.query.filter(
            League.organizer_id == user.id,
            League.status.in_(['registration', 'active']),
        )
        .order_by(League.starts_at.asc(), League.id.asc())
        .all()
    ):
        leagues.append({
            'id': league.id,
            'name': league.name,
            'status': league.status,
            'action': 'cancel',
            'people_notified': len({
                member.user_id for member in league.members
                if member.user_id != user.id
            }),
        })

    communities = []
    owned_memberships = (
        ClubMember.query.filter_by(user_id=user.id, role='owner')
        .order_by(ClubMember.club_id.asc())
        .all()
    )
    for membership in owned_memberships:
        club = membership.club
        if not club:
            continue
        successors = sorted(
            (
                member for member in club.members
                if member.user_id != user.id
                and member.user is not None
                and member.user.deleted_at is None
            ),
            key=lambda member: (member.created_at, member.id),
        )
        successor = successors[0] if successors else None
        communities.append({
            'id': club.id,
            'name': club.name,
            'action': 'transfer' if successor else 'delete',
            'successor_name': successor.user.display_name if successor else None,
        })

    businesses = []
    for business in (
        BusinessProfile.query.filter_by(owner_id=user.id)
        .order_by(BusinessProfile.id.asc())
        .all()
    ):
        replacement = _account_deletion_business_replacement(business, user.id)
        businesses.append({
            'id': business.id,
            'name': business.name,
            'action': 'transfer' if replacement else 'unpublish',
            'successor_name': (
                replacement.user.display_name
                if replacement and replacement.user else None
            ),
        })

    return {
        'profile_and_private_data': 'delete',
        'completed_match_history': 'anonymize',
        'tournaments': tournaments,
        'leagues': leagues,
        'communities': communities,
        'businesses': businesses,
    }


@auth_bp.get('/me/deletion-impact')
@login_required
def account_deletion_impact():
    """Return the exact consequences the destructive confirmation must show."""
    return jsonify(_account_deletion_impact(g.current_user))


@auth_bp.delete('/me')
@rate_limit(5, 3600)
@login_required
def delete_me():
    """Delete the account: wipe personal data and social graph, keep an
    anonymized shell so opponents' completed match history stays intact."""
    import secrets

    from sqlalchemy import or_

    user = g.current_user
    payload = request.get_json(silent=True) or {}
    password = str(payload.get('password') or '')
    # 403, not 401: the client treats 401 as an expired session and logs out,
    # which would swallow a simple wrong-password mistake.
    if not user.check_password(password):
        return jsonify({'error': 'invalid_credentials'}), 403

    user_id = user.id
    # Discover and lock every affected Crew's complete User closure in one
    # canonical ascending order before reconciliation locks any Crew row. If a
    # creator committed while this request waited, the helper rolls back and
    # retries from the expanded closure.
    try:
        locked_users, affected_crew_ids = (
            _lock_stable_account_deletion_crew_users(user_id)
        )
    except RuntimeError:
        current_app.logger.warning(
            'Account deletion Crew lock snapshot kept changing for user %s',
            user_id,
        )
        return jsonify({'error': 'account_changed_retry'}), 409
    locked_by_id = {locked_user.id: locked_user for locked_user in locked_users}
    user = locked_by_id.get(user_id)
    if user is None or user.deleted_at is not None:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    # A concurrent password change may have committed between the initial
    # inexpensive rejection and the row lock. Never authorize deletion using
    # credentials that no longer match the canonical locked row.
    if not user.check_password(password):
        return jsonify({'error': 'invalid_credentials'}), 403
    deletion_impact = _account_deletion_impact(user)

    # Serialize upcoming roster removal with join/leave/score mutations. The
    # deleting User is already locked, so this follows the shared User -> Game
    # -> CheckIn order. Instant assemblies are closed one-way when deleting
    # the last physically present member; a later re-check-in cannot revive
    # an abandoned roster before a discovery cleanup happens to run.
    from backend.routes.games import (
        _close_instant_assembly_without_fresh_members,
        _end_game_arrivals,
        _end_game_open_calls,
    )

    deletion_now = utcnow()
    affected_games = (
        Game.query
        .filter(
            Game.status == 'upcoming',
            or_(
                Game.creator_id == user.id,
                Game.id.in_(
                    db.session.query(GamePlayer.game_id).filter(
                        GamePlayer.user_id == user.id,
                    ),
                ),
                Game.id.in_(
                    db.session.query(GameArrivalIntent.game_id).filter(
                        GameArrivalIntent.user_id == user.id,
                        GameArrivalIntent.active.is_(True),
                    ),
                ),
            ),
        )
        .order_by(Game.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for game in affected_games:
        db.session.expire(game, ['players'])
        if game.creator_id == user.id:
            game.status = 'cancelled'
            _end_game_open_calls(game, 'creator_deleted', deletion_now)
            if game.is_instant and game.assembly_closed_at is None:
                game.assembly_closed_at = deletion_now
            _end_game_arrivals(game, 'creator_deleted', deletion_now)
            for player in game.players:
                if player.user_id != user.id:
                    play_label = (
                        'Ranked match'
                        if game.game_type == 'ranked'
                        else 'Casual play session'
                    )
                    notify(
                        player.user_id,
                        'game_cancelled',
                        f'{play_label} at '
                        f'{game.court.name if game.court else "court"} was cancelled',
                        related_game_id=game.id,
                    )
            continue

        membership = next(
            (player for player in game.players if player.user_id == user.id),
            None,
        )
        if membership is not None:
            game.players.remove(membership)

    # Presence is personal data and must disappear before the remaining
    # instant rosters are evaluated under their Game locks.
    db.session.flush()
    CheckIn.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    GameArrivalIntent.query.filter_by(user_id=user.id).delete(
        synchronize_session=False,
    )
    db.session.flush()
    for game in affected_games:
        _close_instant_assembly_without_fresh_members(game, deletion_now)

    # Availability is precise short-lived location intent. Remove owned rows
    # entirely, and detach this actor from anybody else's ended acceptance
    # ledger. The associated ordinary Game remains the durable play record.
    pulse_rows = (
        PlayAvailabilityPulse.query.filter(or_(
            PlayAvailabilityPulse.user_id == user.id,
            PlayAvailabilityPulse.accepted_by_id == user.id,
        ))
        .order_by(PlayAvailabilityPulse.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    )
    for pulse in pulse_rows:
        if pulse.user_id == user.id:
            db.session.delete(pulse)
        else:
            pulse.accepted_by_id = None
            pulse.accept_client_attempt_id = None
            pulse.accept_client_attempt_fingerprint = None

    # Social graph, messages, and activity are personal data — remove them.
    Friendship.query.filter(or_(
        Friendship.requester_id == user.id, Friendship.addressee_id == user.id,
    )).delete(synchronize_session=False)
    BlockedUser.query.filter(or_(
        BlockedUser.blocker_id == user.id, BlockedUser.blocked_id == user.id,
    )).delete(synchronize_session=False)
    from backend.models import GameOpenCall, MessageHeart, MessageSendAttempt
    for open_call in (
        GameOpenCall.query.filter_by(created_by_id=user.id)
        .order_by(GameOpenCall.id.asc())
        .with_for_update()
        .all()
    ):
        if open_call.active:
            open_call.active = False
            open_call.ended_at = deletion_now
            open_call.end_reason = 'creator_deleted'
        # Keep the retry ledger after its user-authored court message is
        # removed as personal data; a later exact retry cannot resurrect it.
        open_call.court_message_id = None
    MessageSendAttempt.query.filter_by(
        sender_id=user.id,
    ).delete(synchronize_session=False)
    MessageHeart.query.filter_by(user_id=user.id).delete(
        synchronize_session=False,
    )
    Message.query.filter(or_(
        Message.sender_id == user.id, Message.recipient_id == user.id,
    )).delete(synchronize_session=False)
    GameInvite.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    FavoriteCourt.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    from backend.models import PushOutbox, PushSubscription
    PushSubscription.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    PushOutbox.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    # Leagues: cancel ones they organize that haven't finished; drop their
    # not-yet-started registrations (active/completed keep the anonymized shell).
    from backend.models import League, LeagueMember
    for lg in League.query.filter(
        League.organizer_id == user.id,
        League.status.in_(['registration', 'active']),
    ).all():
        lg.status = 'cancelled'
        for member in lg.members:
            if member.user_id != user.id:
                notify(
                    member.user_id, 'league_update',
                    f'{lg.name} was cancelled', related_league_id=lg.id,
                )
    for membership in LeagueMember.query.filter_by(user_id=user.id).all():
        if membership.league and membership.league.status == 'registration':
            membership.league.members.remove(membership)

    # Tournaments use the same lifecycle rule as leagues.  Registration and
    # active events cannot retain an anonymized organizer, so cancel them and
    # tell every accepted or pending entrant.  Completed brackets keep the
    # anonymized user shell so other players' competition history remains valid.
    from backend.models import Tournament
    for tournament in (
        Tournament.query.filter(
            Tournament.organizer_id == user.id,
            Tournament.status.in_(['registration', 'active']),
        )
        .order_by(Tournament.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    ):
        tournament.status = 'cancelled'
        entrant_ids = set()
        for entry in tournament.entries:
            entrant_ids.update(filter(None, (
                entry.player1_id,
                entry.player2_id,
                entry.partner_invitee_id,
            )))
        for entrant_id in entrant_ids - {user.id}:
            notify(
                entrant_id,
                'tournament_cancelled',
                f'{tournament.name} was cancelled because its organizer '
                'deleted their account',
                related_tournament_id=tournament.id,
            )

    # Clubs: hand owned clubs to the longest-standing member; disband empty ones.
    from backend.models import ClubChatRead, ClubMember
    from backend.routes.clubs import _delete_club
    for membership in ClubMember.query.filter_by(user_id=user.id).all():
        club = membership.club
        if membership.role == 'owner':
            others = sorted(
                (
                    m for m in club.members
                    if m.user_id != user.id
                    and m.user is not None
                    and m.user.deleted_at is None
                ),
                key=lambda m: (m.created_at, m.id),
            )
            if others:
                others[0].role = 'owner'
                club.creator_id = others[0].user_id
                notify(
                    others[0].user_id,
                    'club_update',
                    f'You now own the {club.name} Community',
                    related_club_id=club.id,
                )
            else:
                _delete_club(club)
                continue
        club.members.remove(membership)  # delete-orphan keeps the collection in sync
        from backend.services.groups import sync_group_identity
        sync_group_identity('club', club)
    ClubChatRead.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    _reconcile_crews_for_account_deletion(user.id, affected_crew_ids)
    from backend.models import ConversationRead
    ConversationRead.query.filter_by(user_id=user.id).delete(
        synchronize_session=False,
    )

    # A verified venue must never stay public with an anonymized account as
    # its only manager. Preserve the business draft for a future claimant, but
    # retire its public actions and invalidate its verification atomically with
    # account deletion. A later claim can adopt the orphan only as unverified.
    from backend.models import (
        BusinessClaim,
        BusinessIntegrationRequest,
        BusinessOrganizationMember,
        BusinessProfile,
        BusinessStaffInvitation,
    )
    from backend.services.business_governance import record_governance_event
    from backend.services.businesses import _reset_profile_for_ownership_transfer
    for business in (
        BusinessProfile.query.filter_by(owner_id=user.id)
        .order_by(BusinessProfile.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
        .all()
    ):
        replacement = _account_deletion_business_replacement(business, user.id)
        if replacement is not None:
            business.owner_id = replacement.user_id
            replacement.role = 'owner'
            business.published = False
            record_governance_event(
                business,
                'owner_transfer_account_deletion',
                actor_user_id=user.id,
                details={
                    'previous_owner_id': user.id,
                    'new_owner_id': replacement.user_id,
                },
            )
            notify(
                replacement.user_id,
                'business_claim',
                'Business ownership transferred to you',
                f'You now own {business.name}. Review the listing before publishing.',
            )
        else:
            _reset_profile_for_ownership_transfer(business)
            business.claim_status = 'rejected'
            business.verified_at = None
            business.claimant_role = ''
            business.governance_status = 'relinquished'
    for claim in BusinessClaim.query.filter_by(user_id=user.id).all():
        db.session.delete(claim)
    BusinessIntegrationRequest.query.filter_by(
        requested_by_id=user.id,
    ).delete(synchronize_session=False)
    BusinessOrganizationMember.query.filter_by(user_id=user.id).delete(
        synchronize_session=False,
    )
    for invitation in BusinessStaffInvitation.query.filter_by(
        email=user.email,
    ).all():
        if invitation.status == 'pending':
            invitation.status = 'revoked'
            invitation.token_hash = secrets.token_hex(32)

    CourtReview.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    Notification.query.filter(or_(
        Notification.user_id == user.id, Notification.related_user_id == user.id,
    )).delete(synchronize_session=False)

    # Anonymize the shell that remains in completed games.
    user.display_name = 'Deleted player'
    user.email = f'deleted-{user.id}@invalid'
    user.set_password(secrets.token_hex(32))
    user.bio = ''
    user.avatar_url = ''
    user.avatar_data = None
    user.home_court_id = None
    user.home_lat = user.home_lng = None
    user.home_area = ''
    user.last_lat = user.last_lng = None
    user.last_location_at = None
    user.auth_version = int(user.auth_version or 1) + 1
    user.operator_role = ''
    user.mfa_secret_encrypted = ''
    user.mfa_enabled = False
    user.mfa_enabled_at = None
    user.mfa_recovery_codes = '[]'
    user.deleted_at = utcnow()
    db.session.commit()
    return jsonify({'deleted': True, 'effects': deletion_impact})


def profile_stats_payload(user):
    """Exact personal stats assembled with bounded/aggregate database reads.

    This helper is used by both dashboard endpoints, so it must never create
    notifications, commit, or mutate the current user.
    """
    from sqlalchemy.orm import aliased

    now = utcnow()
    base_filters = (
        GamePlayer.user_id == user.id,
        Game.status == 'completed',
        Game.completed_at.isnot(None),
    )
    completed = Game.query.join(GamePlayer).filter(*base_filters)
    games_total = int(completed.with_entities(db.func.count(Game.id)).scalar() or 0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    games_this_month = int(completed.filter(
        Game.completed_at >= month_start,
    ).with_entities(db.func.count(Game.id)).scalar() or 0)
    games_this_week = int(completed.filter(
        Game.completed_at >= week_start,
    ).with_entities(db.func.count(Game.id)).scalar() or 0)

    # A streak needs only one existence read per consecutive week rather than
    # hydrating an arbitrary 500-game history. A quiet current week does not
    # erase the run that ended last week.
    streak = 0
    cursor_start = week_start if games_this_week else week_start - timedelta(days=7)
    while completed.filter(
        Game.completed_at >= cursor_start,
        Game.completed_at < cursor_start + timedelta(days=7),
    ).with_entities(Game.id).first() is not None:
        streak += 1
        cursor_start -= timedelta(days=7)

    top_court = None
    game_count = db.func.count(Game.id).label('game_count')
    top_row = (
        db.session.query(Game.court_id, Court.name, game_count)
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .join(Court, Court.id == Game.court_id)
        .filter(*base_filters)
        .group_by(Game.court_id, Court.name)
        .order_by(game_count.desc(), Game.court_id.asc())
        .first()
    )
    if top_row:
        top_court = {
            'id': top_row.court_id,
            'name': top_row.name,
            'games': int(top_row.game_count),
        }

    scored_filters = (
        *base_filters,
        GamePlayer.team.in_((1, 2)),
        Game.score_team1.isnot(None),
        Game.score_team2.isnot(None),
    )
    recent_scored = completed.filter(*scored_filters[3:]).order_by(
        Game.completed_at.desc(), Game.id.desc(),
    ).limit(5).all()
    form = []
    for game in recent_scored:
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if mine:
            form.append('W' if (game.score_team1 > game.score_team2) == (mine.team == 1) else 'L')

    # Aggregate teammate/opponent meetings and wins without loading every
    # historical Game and every roster into Python.
    mine = aliased(GamePlayer)
    other = aliased(GamePlayer)
    same_team = (mine.team == other.team).label('same_team')
    i_won = db.case((db.or_(
        db.and_(mine.team == 1, Game.score_team1 > Game.score_team2),
        db.and_(mine.team == 2, Game.score_team2 > Game.score_team1),
    ), 1), else_=0)
    relationship_rows = (
        db.session.query(
            other.user_id,
            User.display_name,
            same_team,
            db.func.count(Game.id).label('games'),
            db.func.sum(i_won).label('wins'),
        )
        .select_from(Game)
        .join(mine, mine.game_id == Game.id)
        .join(other, other.game_id == Game.id)
        .join(User, User.id == other.user_id)
        .filter(
            mine.user_id == user.id,
            other.user_id != user.id,
            mine.team.in_((1, 2)),
            other.team.in_((1, 2)),
            Game.status == 'completed',
            Game.completed_at.isnot(None),
            Game.score_team1.isnot(None),
            Game.score_team2.isnot(None),
            User.deleted_at.is_(None),
        )
        .group_by(other.user_id, User.display_name, same_team)
        .all()
    )
    partners, rivals = {}, {}
    for row in relationship_rows:
        bucket = partners if row.same_team else rivals
        bucket[row.user_id] = {
            'user_id': row.user_id,
            'display_name': row.display_name,
            'games': int(row.games or 0),
            'wins': int(row.wins or 0),
        }
    best_partner = None
    if partners:
        top = max(partners.values(), key=lambda entry: (
            entry['wins'], entry['games'], -entry['user_id'],
        ))
        if top['wins'] > 0:
            best_partner = dict(top)
    top_rival = None
    if rivals:
        top = max(rivals.values(), key=lambda entry: (
            entry['games'], entry['wins'], -entry['user_id'],
        ))
        top_rival = {
            'user_id': top['user_id'],
            'display_name': top['display_name'],
            'games': top['games'],
            'your_wins': top['wins'],
        }

    # Day-part insights need timestamps, but only four scalar values per scored
    # game cross the database boundary; no roster or court objects are loaded.
    scored = (
        db.session.query(
            Game.scheduled_at, Game.score_team1, Game.score_team2, GamePlayer.team,
        )
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .filter(*scored_filters)
        .all()
    )
    insights = None
    if len(scored) >= 3:
        offset = timedelta(hours=round(
            (user.home_lng if user.home_lng is not None else -90) / 15,
        ))
        parts, days = {}, {}
        margin_total = 0
        for scheduled_at, score_team1, score_team2, team in scored:
            margin = (score_team1 - score_team2) if team == 1 \
                else (score_team2 - score_team1)
            local = scheduled_at + offset
            part = ('mornings' if 5 <= local.hour < 12
                    else 'afternoons' if 12 <= local.hour < 17 else 'evenings')
            wins_n, games_n = parts.get(part, (0, 0))
            parts[part] = (wins_n + (1 if margin > 0 else 0), games_n + 1)
            day = local.strftime('%A')
            days[day] = days.get(day, 0) + 1
            margin_total += margin
        best_part = None
        eligible = {p: wn for p, wn in parts.items() if wn[1] >= 3}
        if eligible:
            part, (wins_n, games_n) = max(
                eligible.items(), key=lambda kv: kv[1][0] / kv[1][1],
            )
            best_part = {'label': part, 'wins': wins_n, 'games': games_n}
        insights = {
            'best_part': best_part,
            'busiest_day': max(days, key=days.get) if days else None,
            'avg_margin': round(margin_total / len(scored), 1),
        }

    from backend.models import (
        badge_progress, league_titles, mvp_award_count, player_badges,
        rating_history_for, tournament_titles,
    )
    rating_history = rating_history_for(user)
    badges = player_badges(user)
    titles = tournament_titles(user)
    lg_titles = league_titles(user)
    mvp_awards = mvp_award_count(user)

    return {
        'games_total': games_total,
        'games_this_week': games_this_week,
        'games_this_month': games_this_month,
        'week_streak': streak,
        'top_court': top_court,
        'best_partner': best_partner,
        'top_rival': top_rival,
        'form': form,
        'badges': badges,
        'badge_progress': badge_progress(user),
        'new_badges': [],
        'tournament_titles': titles,
        'league_titles': lg_titles,
        'mvp_awards': mvp_awards,
        'insights': insights,
        'rating_history': rating_history,
    }


@auth_bp.get('/me/stats')
@login_required
def my_stats():
    return jsonify(profile_stats_payload(g.current_user))


@auth_bp.get('/users/<int:user_id>/avatar')
def user_avatar(user_id):
    """Serve a validated managed profile photo without exposing its data URL."""
    user = db.session.get(User, user_id)
    if not user or user.deleted_at is not None or not user.avatar_data:
        return jsonify({'error': 'avatar_not_found'}), 404
    try:
        mime_type, raw, _canonical = _decode_avatar_data(user.avatar_data)
    except ValueError:
        # A corrupt legacy row should fail closed and never become active HTML.
        return jsonify({'error': 'avatar_not_found'}), 404
    response = Response(raw, mimetype=mime_type)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'public, max-age=300, must-revalidate'
    response.set_etag(hashlib.sha256(raw).hexdigest())
    return response.make_conditional(request)


@auth_bp.get('/me/dashboard')
@login_required
def profile_dashboard():
    """All independently rendered Profile sections in one authenticated read."""
    # Lazy imports avoid the route-module cycle: games/courts both import auth.
    from backend.routes.courts import favorite_courts_payload
    from backend.routes.games import game_history_payload, my_games_payload

    user = g.current_user
    return jsonify({
        'games': my_games_payload(user),
        'stats': profile_stats_payload(user),
        'favorites': favorite_courts_payload(user),
        'history': game_history_payload(user),
    })


@auth_bp.patch('/me')
@rate_limit(30, 600)
@login_required
def update_me():
    user = g.current_user
    payload = request.get_json(silent=True) or {}

    if 'display_name' in payload:
        name = str(payload.get('display_name') or '').strip()
        if not name:
            return jsonify({'error': 'display_name_required'}), 400
        user.display_name = name[:120]
    if 'bio' in payload:
        user.bio = str(payload.get('bio') or '').strip()[:500]
    if 'skill_level' in payload:
        level = str(payload.get('skill_level') or '').strip().lower()
        if level not in SKILL_LEVELS:
            return jsonify({'error': 'invalid_skill_level'}), 400
        user.skill_level = level
    if 'skill_rating' in payload:
        raw_rating = payload.get('skill_rating')
        if raw_rating in (None, ''):
            user.skill_rating = None
        else:
            try:
                skill_rating = float(raw_rating)
            except (TypeError, ValueError):
                return jsonify({'error': 'invalid_skill_rating'}), 400
            if skill_rating not in SELF_RATING_LEVELS:
                return jsonify({'error': 'invalid_skill_rating'}), 400
            user.skill_rating = skill_rating
            # Keep legacy match filters coherent while new surfaces adopt the
            # standard numeric self-rating.
            user.skill_level = (
                'beginner' if skill_rating <= 2.5
                else 'intermediate' if skill_rating <= 3.5
                else 'advanced' if skill_rating <= 4.0
                else 'pro'
            )
    if 'dupr_rating' in payload:
        raw_dupr = payload.get('dupr_rating')
        if raw_dupr in (None, ''):
            user.dupr_rating = None
        else:
            try:
                dupr_rating = round(float(raw_dupr), 3)
            except (TypeError, ValueError):
                return jsonify({'error': 'invalid_dupr_rating'}), 400
            if not 2.0 <= dupr_rating <= 8.0:
                return jsonify({'error': 'invalid_dupr_rating'}), 400
            user.dupr_rating = dupr_rating
    if 'dupr_id' in payload:
        dupr_id = str(payload.get('dupr_id') or '').strip()
        if dupr_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', dupr_id):
            return jsonify({'error': 'invalid_dupr_id'}), 400
        user.dupr_id = dupr_id
    if 'availability' in payload:
        import json as _json

        from backend.models import AVAILABILITY_SLOTS
        slots = payload.get('availability')
        if not isinstance(slots, list):
            return jsonify({'error': 'invalid_availability'}), 400
        cleaned = [s for s in dict.fromkeys(slots) if s in AVAILABILITY_SLOTS]
        user.availability = _json.dumps(cleaned)
    if 'nearby_visibility' in payload:
        nearby_visibility = str(payload.get('nearby_visibility') or '').strip().lower()
        if nearby_visibility not in {'everyone', 'friends', 'hidden'}:
            return jsonify({'error': 'invalid_nearby_visibility'}), 400
        user.nearby_visibility = nearby_visibility
    if 'muted_notifications' in payload:
        import json as _json

        from backend.models import MUTEABLE_NOTIFICATIONS
        muted = payload.get('muted_notifications')
        if not isinstance(muted, list):
            return jsonify({'error': 'invalid_muted_notifications'}), 400
        cleaned = [k for k in dict.fromkeys(muted) if k in MUTEABLE_NOTIFICATIONS]
        user.muted_notifications = _json.dumps(cleaned)
    if 'avatar_color' in payload:
        color = str(payload.get('avatar_color') or '').strip()
        if not re.match(r'^#[0-9a-fA-F]{6}$', color):
            return jsonify({'error': 'invalid_avatar_color'}), 400
        user.avatar_color = color
    uploaded_avatar = payload.get('avatar_data') if 'avatar_data' in payload else None
    if uploaded_avatar:
        try:
            _mime_type, raw_avatar, canonical_avatar = _decode_avatar_data(
                uploaded_avatar,
            )
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        revision = hashlib.sha256(raw_avatar).hexdigest()[:16]
        user.avatar_data = canonical_avatar
        user.avatar_url = f'/api/users/{user.id}/avatar?v={revision}'
    elif 'avatar_url' in payload:
        url = str(payload.get('avatar_url') or '').strip()
        if url and not re.match(r'^https?://\S+$', url):
            return jsonify({'error': 'invalid_avatar_url'}), 400
        user.avatar_url = url[:500]
        user.avatar_data = None
    elif 'avatar_data' in payload:
        # An explicit null/empty value removes a managed photo and restores
        # the colored-initials fallback.
        user.avatar_url = ''
        user.avatar_data = None
    if 'home_court_id' in payload:
        court_id = payload.get('home_court_id')
        if court_id in (None, '', 0):
            user.home_court_id = None
        else:
            court = db.session.get(Court, int(court_id))
            if not court:
                return jsonify({'error': 'court_not_found'}), 404
            user.home_court_id = court.id

    if 'home_lat' in payload or 'home_lng' in payload:
        try:
            home_lat = float(payload.get('home_lat'))
            home_lng = float(payload.get('home_lng'))
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid_home_location'}), 400
        if not (-90 <= home_lat <= 90 and -180 <= home_lng <= 180):
            return jsonify({'error': 'invalid_home_location'}), 400
        user.home_lat = home_lat
        user.home_lng = home_lng
        user.home_area = str(payload.get('home_area') or '').strip()[:120]

    # Completion is deliberately monotonic. A client can safely retry the
    # final onboarding request, but cannot accidentally put an established
    # account back into first-run mode.
    if payload.get('onboarding_complete') is True:
        missing = []
        if user.skill_rating not in SELF_RATING_LEVELS:
            missing.append('skill_rating')
        if not user.availability_list():
            missing.append('availability')
        if not str(user.avatar_url or '').strip():
            missing.append('avatar')
        if not user.home_court_id:
            missing.append('home_court')
        if missing:
            return jsonify({
                'error': 'profile_setup_incomplete',
                'missing': missing,
            }), 400
        user.onboarding_completed_at = user.onboarding_completed_at or utcnow()

    db.session.commit()
    return jsonify(_me_payload(user))
