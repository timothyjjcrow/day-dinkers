"""Open-to-Play sessions — create, join, schedule, invite."""
from datetime import datetime, timedelta
from flask import Blueprint, current_app, request, jsonify
from sqlalchemy import func, and_, or_
from sqlalchemy.exc import IntegrityError
from backend.app import db
from backend.models import (
    PlaySession, PlaySessionPlayer, Court, Notification, Friendship, CheckIn,
    RecurringSessionSeries, RecurringSessionSeriesItem,
    RankedLobby, RankedLobbyPlayer, Tournament, TournamentParticipant,
)
from backend.auth_utils import login_required
from backend.time_utils import utcnow_naive
from backend.services.court_payloads import normalize_county_slug

sessions_bp = Blueprint('sessions', __name__)

_ALLOWED_SESSION_TYPES = {'now', 'scheduled'}
_ALLOWED_GAME_TYPES = {'open', 'doubles', 'singles'}
_ALLOWED_SKILL_LEVELS = {'all', 'beginner', 'intermediate', 'advanced'}
_ALLOWED_VISIBILITY = {'all', 'friends'}
_MIN_MAX_PLAYERS = 2
_MAX_MAX_PLAYERS = 100


def _normalize_int_id_list(raw_ids, max_items=200):
    if not isinstance(raw_ids, list):
        return None
    seen = set()
    normalized = []
    for raw in raw_ids:
        try:
            item_id = int(raw)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(item_id)
        if len(normalized) >= max_items:
            break
    return normalized


def _get_friend_ids(user_id):
    friendships = Friendship.query.filter(
        ((Friendship.user_id == user_id) | (Friendship.friend_id == user_id))
        & (Friendship.status == 'accepted')
    ).all()
    return [
        f.friend_id if f.user_id == user_id else f.user_id
        for f in friendships
    ]


def _can_view_session(session, current_user_id):
    if session.visibility != 'friends':
        return True
    if not current_user_id:
        return False
    if session.creator_id == current_user_id:
        return True
    if any(p.user_id == current_user_id for p in (session.players or [])):
        return True
    return session.creator_id in set(_get_friend_ids(current_user_id))


def _normalize_friend_invite_ids(raw_ids, inviter_user_id):
    normalized = _normalize_int_id_list(raw_ids)
    if normalized is None:
        return None, []
    friend_ids = set(_get_friend_ids(inviter_user_id))
    allowed = []
    invalid = []
    for friend_id in normalized:
        if friend_id == inviter_user_id:
            continue
        if friend_id not in friend_ids:
            invalid.append(friend_id)
            continue
        allowed.append(friend_id)
    return allowed, invalid


def _joined_count(session_id):
    """Count joined participants (excluding creator)."""
    return PlaySessionPlayer.query.filter_by(
        session_id=session_id, status='joined'
    ).count()


def _complete_expired_now_sessions():
    """Mark timed 'now' sessions as completed once their end time has passed."""
    now = utcnow_naive()
    expired = PlaySession.query.filter(
        PlaySession.status == 'active',
        PlaySession.session_type == 'now',
        PlaySession.end_time.isnot(None),
        PlaySession.end_time <= now,
    ).all()
    if not expired:
        return
    for session in expired:
        session.status = 'completed'
    db.session.commit()


def _scheduled_session_retention_days():
    raw_days = current_app.config.get('SCHEDULED_SESSION_RETENTION_DAYS', 3)
    try:
        parsed_days = int(raw_days)
    except (TypeError, ValueError):
        parsed_days = 3
    return max(1, parsed_days)


def _complete_stale_scheduled_sessions():
    """Complete scheduled sessions whose occurrence finished beyond retention."""
    cutoff = utcnow_naive() - timedelta(days=_scheduled_session_retention_days())
    reference_time = func.coalesce(
        PlaySession.end_time,
        PlaySession.start_time,
        PlaySession.created_at,
    )
    stale = PlaySession.query.filter(
        PlaySession.status == 'active',
        PlaySession.session_type == 'scheduled',
        reference_time <= cutoff,
    ).all()
    if not stale:
        return
    for session in stale:
        session.status = 'completed'
    db.session.commit()


def _expire_stale_sessions():
    _complete_expired_now_sessions()
    _complete_stale_scheduled_sessions()


def _active_sessions_query():
    now = utcnow_naive()
    now_ok = and_(
        PlaySession.session_type == 'now',
        or_(PlaySession.end_time.is_(None), PlaySession.end_time > now),
    )
    scheduled_ok = and_(
        PlaySession.session_type == 'scheduled',
        or_(
            and_(PlaySession.end_time.isnot(None), PlaySession.end_time > now),
            and_(PlaySession.end_time.is_(None), or_(
                PlaySession.start_time.is_(None),
                PlaySession.start_time > now,
            )),
        ),
    )
    return PlaySession.query.filter(
        PlaySession.status == 'active',
        or_(now_ok, scheduled_ok),
    )


def _serialize_session(session):
    """Serialize a session and include recurring-series metadata if present."""
    data = session.to_dict()
    link = RecurringSessionSeriesItem.query.filter_by(session_id=session.id).first()
    if link and link.series:
        data['series'] = {
            'id': link.series_id,
            'sequence': link.sequence,
            'occurrences': link.series.occurrences,
            'interval_weeks': link.series.interval_weeks,
            'recurrence': link.series.recurrence,
        }
    return data


def _schedule_item_day_key(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string.replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        return None


def _session_viewer_status(session, current_user_id):
    user_id = int(current_user_id or 0)
    if not user_id:
        return 'none'
    if session.creator_id == user_id:
        return 'creator'
    participant = next(
        (player for player in (session.players or []) if player.user_id == user_id),
        None,
    )
    if not participant:
        return 'none'
    if participant.status in {'joined', 'invited', 'waitlisted'}:
        return participant.status
    return 'participant'


def _session_spot_summary(session):
    joined_players = [
        participant for participant in (session.players or [])
        if participant.status == 'joined'
    ]
    spots_taken = 1 + len(joined_players)
    max_players = None
    spots_remaining = None
    if session.max_players is not None:
        try:
            max_players = int(session.max_players)
        except (TypeError, ValueError):
            max_players = None
    if max_players is not None:
        spots_remaining = max(0, max_players - spots_taken)
    return spots_taken, max_players, spots_remaining


def _session_creator_name(session):
    if not session.creator:
        return None
    return session.creator.name or session.creator.username


def _notify_session_participants(session, notif_type, content):
    recipient_ids = {
        participant.user_id
        for participant in (session.players or [])
        if participant.user_id and participant.user_id != session.creator_id
    }
    for user_id in recipient_ids:
        db.session.add(Notification(
            user_id=user_id,
            notif_type=notif_type,
            content=content,
            reference_id=session.id,
        ))


def _build_schedule_banner_days(items):
    grouped = {}
    for item in items:
        day_key = _schedule_item_day_key(item.get('start_time'))
        if not day_key:
            continue
        if day_key not in grouped:
            date_obj = datetime.fromisoformat(day_key)
            grouped[day_key] = {
                'day_key': day_key,
                'label': date_obj.strftime('%a'),
                'date_label': date_obj.strftime('%b %d'),
                'count': 0,
            }
        grouped[day_key]['count'] += 1
    return [grouped[key] for key in sorted(grouped.keys())]


def _session_banner_items(current_user_id=None, court_id=None, county_slug='', user_only=False, days=7):
    now = utcnow_naive()
    horizon = now + timedelta(days=max(1, int(days or 7)))
    query = _active_sessions_query().filter(
        PlaySession.session_type == 'scheduled',
        PlaySession.start_time.isnot(None),
        PlaySession.start_time >= now,
        PlaySession.start_time <= horizon,
    )
    if court_id:
        query = query.filter(PlaySession.court_id == court_id)
    elif county_slug:
        query = query.join(Court, Court.id == PlaySession.court_id).filter(
            Court.county_slug == county_slug,
        )

    sessions = query.order_by(PlaySession.start_time.asc()).limit(80).all()
    items = []
    current_user_id = int(current_user_id or 0)
    for session in sessions:
        viewer_status = _session_viewer_status(session, current_user_id)
        spots_taken, max_players, spots_remaining = _session_spot_summary(session)
        is_mine = bool(current_user_id and viewer_status != 'none')
        if user_only and not is_mine:
            continue
        if session.visibility == 'friends' and not current_user_id:
            continue
        if session.visibility == 'friends' and not _can_view_session(session, current_user_id):
            continue

        items.append({
            'id': f'session-{session.id}',
            'reference_id': session.id,
            'item_type': 'session',
            'title': session.notes.strip() or 'Open Play',
            'subtitle': session.court.name if session.court else 'Court',
            'court_id': session.court_id,
            'court_name': session.court.name if session.court else 'Court',
            'county_slug': session.court.county_slug if session.court else '',
            'state': session.court.state if session.court else '',
            'start_time': session.start_time.isoformat() if session.start_time else None,
            'end_time': session.end_time.isoformat() if session.end_time else None,
            'visibility': session.visibility,
            'game_type': session.game_type,
            'status': session.status,
            'is_mine': bool(is_mine),
            'is_friend_only': session.visibility == 'friends',
            'participant_count': spots_taken,
            'viewer_status': viewer_status,
            'creator_name': _session_creator_name(session),
            'max_players': max_players,
            'spots_taken': spots_taken,
            'spots_remaining': spots_remaining,
        })
    return items


def _ranked_lobby_banner_items(current_user_id=None, court_id=None, county_slug='', user_only=False, days=7):
    user_id = int(current_user_id or 0)
    if not user_id:
        return []
    now = utcnow_naive()
    horizon = now + timedelta(days=max(1, int(days or 7)))
    participant_lobby_ids = db.session.query(RankedLobbyPlayer.lobby_id).filter(
        RankedLobbyPlayer.user_id == user_id,
    )
    query = RankedLobby.query.filter(
        RankedLobby.id.in_(participant_lobby_ids),
        RankedLobby.status.in_(['pending_acceptance', 'ready']),
        RankedLobby.scheduled_for.isnot(None),
        RankedLobby.scheduled_for >= now,
        RankedLobby.scheduled_for <= horizon,
    )
    if court_id:
        query = query.filter(RankedLobby.court_id == court_id)
    elif county_slug:
        query = query.join(Court, Court.id == RankedLobby.court_id).filter(
            Court.county_slug == county_slug,
        )

    lobbies = query.order_by(RankedLobby.scheduled_for.asc()).limit(40).all()
    items = []
    for lobby in lobbies:
        me = next(
            (player for player in (lobby.players or []) if player.user_id == user_id),
            None,
        )
        if user_only and not me:
            continue
        items.append({
            'id': f'ranked-lobby-{lobby.id}',
            'reference_id': lobby.id,
            'item_type': 'ranked_lobby',
            'title': 'Ranked Challenge',
            'subtitle': lobby.court.name if lobby.court else 'Court',
            'court_id': lobby.court_id,
            'court_name': lobby.court.name if lobby.court else 'Court',
            'county_slug': lobby.court.county_slug if lobby.court else '',
            'state': lobby.court.state if lobby.court else '',
            'start_time': lobby.scheduled_for.isoformat() if lobby.scheduled_for else None,
            'end_time': None,
            'visibility': 'private',
            'game_type': lobby.match_type,
            'status': lobby.status,
            'is_mine': True,
            'participant_count': len(lobby.players or []),
            'acceptance_status': me.acceptance_status if me else None,
            'source': lobby.source,
        })
    return items


def _tournament_banner_items(current_user_id=None, court_id=None, county_slug='', user_only=False, days=7):
    now = utcnow_naive()
    horizon = now + timedelta(days=max(1, int(days or 7)))
    query = Tournament.query.filter(
        Tournament.status == 'upcoming',
        Tournament.start_time.isnot(None),
        Tournament.start_time >= now,
        Tournament.start_time <= horizon,
    )
    if court_id:
        query = query.filter(Tournament.court_id == court_id)
    elif county_slug:
        query = query.join(Court, Court.id == Tournament.court_id).filter(
            Court.county_slug == county_slug,
        )

    user_id = int(current_user_id or 0)
    if user_only:
        if not user_id:
            return []
        participant_tournament_ids = db.session.query(TournamentParticipant.tournament_id).filter(
            TournamentParticipant.user_id == user_id,
        )
        query = query.filter(
            db.or_(
                Tournament.host_user_id == user_id,
                Tournament.id.in_(participant_tournament_ids),
            )
        )

    tournaments = query.order_by(Tournament.start_time.asc()).limit(40).all()
    items = []
    for tournament in tournaments:
        is_mine = bool(
            user_id and (
                tournament.host_user_id == user_id
                or any(participant.user_id == user_id for participant in (tournament.participants or []))
            )
        )
        items.append({
            'id': f'tournament-{tournament.id}',
            'reference_id': tournament.id,
            'item_type': 'tournament',
            'title': tournament.name,
            'subtitle': tournament.court.name if tournament.court else 'Court',
            'court_id': tournament.court_id,
            'court_name': tournament.court.name if tournament.court else 'Court',
            'county_slug': tournament.court.county_slug if tournament.court else '',
            'state': tournament.court.state if tournament.court else '',
            'start_time': tournament.start_time.isoformat() if tournament.start_time else None,
            'end_time': None,
            'visibility': tournament.access_mode,
            'game_type': tournament.match_type,
            'status': tournament.status,
            'is_mine': is_mine,
            'participant_count': sum(
                1 for participant in (tournament.participants or [])
                if participant.participant_status in {'registered', 'checked_in'}
            ),
        })
    return items


def _build_schedule_banner_payload(current_user_id=None, court_id=None, county_slug='', user_only=False, days=7):
    normalized_county = normalize_county_slug(county_slug, fallback='') if county_slug else ''
    items = [
        *_session_banner_items(
            current_user_id=current_user_id,
            court_id=court_id,
            county_slug=normalized_county,
            user_only=user_only,
            days=days,
        ),
        *_ranked_lobby_banner_items(
            current_user_id=current_user_id,
            court_id=court_id,
            county_slug=normalized_county,
            user_only=user_only,
            days=days,
        ),
        *_tournament_banner_items(
            current_user_id=current_user_id,
            court_id=court_id,
            county_slug=normalized_county,
            user_only=user_only,
            days=days,
        ),
    ]
    items.sort(key=lambda item: (item.get('start_time') or '', item.get('item_type') or ''))
    return {
        'items': items,
        'days': _build_schedule_banner_days(items),
        'context': {
            'court_id': court_id,
            'county_slug': normalized_county or None,
            'user_only': bool(user_only),
        },
    }


@sessions_bp.route('', methods=['GET'])
def get_sessions():
    """List active sessions, filtered by visibility for the requester."""
    _expire_stale_sessions()
    court_id = request.args.get('court_id', type=int)
    session_type = request.args.get('type', '')  # 'now', 'scheduled', or ''
    visibility_filter = (request.args.get('visibility') or 'all').strip().lower()
    skill_filter = (request.args.get('skill_level') or 'all').strip().lower()

    if visibility_filter not in ('all', 'friends'):
        visibility_filter = 'all'
    if skill_filter not in ('all', 'beginner', 'intermediate', 'advanced'):
        skill_filter = 'all'

    query = _active_sessions_query()
    if court_id:
        query = query.filter(PlaySession.court_id == court_id)
    if session_type:
        query = query.filter(PlaySession.session_type == session_type)

    sessions = query.order_by(PlaySession.created_at.desc()).limit(50).all()

    # Visibility filtering
    current_user_id = _get_optional_user_id()
    friend_ids = set(_get_friend_ids(current_user_id)) if current_user_id else set()

    results = []
    for s in sessions:
        if visibility_filter == 'friends' and s.visibility != 'friends':
            continue
        if skill_filter != 'all' and s.skill_level != skill_filter:
            continue

        if s.visibility == 'friends':
            is_participant = current_user_id and any(
                p.user_id == current_user_id for p in (s.players or [])
            )
            if current_user_id and (
                s.creator_id == current_user_id
                or s.creator_id in friend_ids
                or is_participant
            ):
                results.append(_serialize_session(s))
        else:
            results.append(_serialize_session(s))

    return jsonify({'sessions': results})


@sessions_bp.route('/banner', methods=['GET'])
def get_schedule_banner():
    """Normalized next-7-days banner payload for the React shell."""
    _expire_stale_sessions()
    current_user_id = _get_optional_user_id()
    court_id = request.args.get('court_id', type=int)
    county_slug = normalize_county_slug(
        request.args.get('county_slug'),
        fallback='',
    ) if request.args.get('county_slug') else ''
    days = request.args.get('days', 7, type=int)
    user_only = str(request.args.get('user_only') or '').strip().lower() in {'1', 'true', 'yes'}
    payload = _build_schedule_banner_payload(
        current_user_id=current_user_id,
        court_id=court_id,
        county_slug=county_slug,
        user_only=user_only,
        days=days,
    )
    return jsonify(payload)


@sessions_bp.route('/my', methods=['GET'])
@login_required
def get_my_sessions():
    """Get sessions the current user created or joined."""
    _expire_stale_sessions()
    uid = request.current_user.id

    # Sessions I created
    created = _active_sessions_query().filter(
        PlaySession.creator_id == uid
    ).all()

    # Sessions I joined
    joined_ids = [sp.session_id for sp in PlaySessionPlayer.query.filter_by(
        user_id=uid, status='joined'
    ).all()]
    joined = _active_sessions_query().filter(
        PlaySession.id.in_(joined_ids),
    ).all() if joined_ids else []

    # Merge and deduplicate
    seen = set()
    results = []
    for s in created + joined:
        if s.id not in seen:
            seen.add(s.id)
            results.append(_serialize_session(s))

    return jsonify({'sessions': results})


@sessions_bp.route('', methods=['POST'])
@login_required
def create_session():
    """Create an open-to-play session (now or scheduled)."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    session_type = str(data.get('session_type', 'now')).strip().lower()
    if session_type not in _ALLOWED_SESSION_TYPES:
        return jsonify({'error': 'Invalid session type'}), 400

    game_type = str(data.get('game_type', 'open')).strip().lower()
    if game_type not in _ALLOWED_GAME_TYPES:
        return jsonify({'error': 'Invalid game type'}), 400

    skill_level = str(data.get('skill_level', 'all')).strip().lower()
    if skill_level not in _ALLOWED_SKILL_LEVELS:
        return jsonify({'error': 'Invalid skill level'}), 400

    visibility = str(data.get('visibility', 'all')).strip().lower()
    if visibility not in _ALLOWED_VISIBILITY:
        return jsonify({'error': 'Invalid visibility setting'}), 400

    try:
        max_players = int(data.get('max_players', 4))
    except (TypeError, ValueError):
        return jsonify({'error': 'Max players must be a number'}), 400
    if max_players < _MIN_MAX_PLAYERS or max_players > _MAX_MAX_PLAYERS:
        return jsonify({
            'error': f'Max players must be between {_MIN_MAX_PLAYERS} and {_MAX_MAX_PLAYERS}'
        }), 400

    notes = str(data.get('notes', '')).strip()[:2000]

    # Scheduled sessions must include explicit future times.
    # "Now" sessions use current time and can include a duration/end time.
    start_time = None
    end_time = None
    recurrence = 'none'
    recurrence_count = 1
    interval_weeks = 1
    if session_type == 'scheduled':
        raw_start = str(data.get('start_time') or '').strip()
        if not raw_start:
            return jsonify({'error': 'Start time required for scheduled sessions'}), 400
        try:
            start_time = datetime.fromisoformat(raw_start)
        except ValueError:
            return jsonify({'error': 'Start time must be a valid ISO datetime'}), 400

        raw_end = str(data.get('end_time') or '').strip()
        if raw_end:
            try:
                end_time = datetime.fromisoformat(raw_end)
            except ValueError:
                return jsonify({'error': 'End time must be a valid ISO datetime'}), 400
            if end_time <= start_time:
                return jsonify({'error': 'End time must be after start time'}), 400

        recurrence = (data.get('recurrence') or 'none').strip().lower()
        if recurrence not in ('none', 'weekly', 'biweekly'):
            recurrence = 'none'
        try:
            recurrence_count = int(data.get('recurrence_count', 1))
        except (TypeError, ValueError):
            recurrence_count = 1
        recurrence_count = max(1, min(recurrence_count, 12))
        if recurrence == 'none':
            recurrence_count = 1
        interval_weeks = 2 if recurrence == 'biweekly' else 1
    else:
        active_checkin = CheckIn.query.filter_by(
            user_id=request.current_user.id,
            checked_out_at=None,
        ).first()
        if not active_checkin or active_checkin.court_id != court_id:
            return jsonify({
                'error': 'Check in at this court before starting a looking-to-play session'
            }), 400

        start_time = utcnow_naive()

        raw_duration = data.get('duration_minutes')
        if raw_duration not in (None, ''):
            try:
                duration_minutes = int(raw_duration)
            except (TypeError, ValueError):
                return jsonify({'error': 'duration_minutes must be a number'}), 400
            if duration_minutes < 30 or duration_minutes > 480:
                return jsonify({'error': 'duration_minutes must be between 30 and 480'}), 400
            end_time = start_time + timedelta(minutes=duration_minutes)

        raw_end = str(data.get('end_time') or '').strip()
        if raw_end:
            try:
                parsed_end = datetime.fromisoformat(raw_end)
            except ValueError:
                return jsonify({'error': 'End time must be a valid ISO datetime'}), 400
            if parsed_end <= start_time:
                return jsonify({'error': 'End time must be after start time'}), 400
            end_time = parsed_end

    # Cancel any existing active "now" session by this user.
    if session_type == 'now':
        existing = PlaySession.query.filter_by(
            creator_id=request.current_user.id,
            status='active', session_type='now',
        ).all()
        for ex in existing:
            ex.status = 'completed'

    # Normalize invite list once and reuse for all generated sessions.
    raw_invites = data.get('invite_friends') or []
    if raw_invites and not isinstance(raw_invites, list):
        return jsonify({'error': 'invite_friends must be a list of user IDs'}), 400
    invite_ids, invalid_invite_ids = _normalize_friend_invite_ids(
        raw_invites,
        request.current_user.id,
    )
    if invite_ids is None:
        return jsonify({'error': 'invite_friends must be a list of user IDs'}), 400
    if invalid_invite_ids:
        return jsonify({
            'error': 'You can only invite accepted friends',
            'invalid_friend_ids': invalid_invite_ids,
        }), 400

    created_sessions = []
    series = None
    if session_type == 'scheduled' and recurrence_count > 1:
        series = RecurringSessionSeries(
            creator_id=request.current_user.id,
            recurrence=recurrence,
            interval_weeks=interval_weeks,
            occurrences=recurrence_count,
        )
        db.session.add(series)
        db.session.flush()

    for i in range(recurrence_count):
        occurrence_start = None
        occurrence_end = None
        if session_type == 'scheduled':
            weeks = i * interval_weeks
            occurrence_start = start_time + timedelta(weeks=weeks)
            if end_time:
                occurrence_end = end_time + timedelta(weeks=weeks)
        else:
            occurrence_start = start_time
            occurrence_end = end_time

        session = PlaySession(
            creator_id=request.current_user.id,
            court_id=court_id,
            session_type=session_type,
            start_time=occurrence_start,
            end_time=occurrence_end,
            game_type=game_type,
            skill_level=skill_level,
            max_players=max_players,
            visibility=visibility,
            notes=notes,
        )
        db.session.add(session)
        db.session.flush()
        created_sessions.append(session)

        if series:
            link = RecurringSessionSeriesItem(
                series_id=series.id,
                session_id=session.id,
                sequence=i + 1,
            )
            db.session.add(link)

        for fid in invite_ids:
            psp = PlaySessionPlayer(
                session_id=session.id, user_id=fid, status='invited',
            )
            db.session.add(psp)
            notif = Notification(
                user_id=fid, notif_type='session_invite',
                content=f'{request.current_user.username} invited you to play '
                        f'at {court.name}',
                reference_id=session.id,
            )
            db.session.add(notif)

    db.session.commit()

    response = {'session': _serialize_session(created_sessions[0])}
    if len(created_sessions) > 1:
        response['sessions'] = [_serialize_session(s) for s in created_sessions]
        response['series_id'] = series.id if series else None
        response['created_count'] = len(created_sessions)
    return jsonify(response), 201


@sessions_bp.route('/<int:session_id>', methods=['GET'])
def get_session(session_id):
    """Get session details."""
    _expire_stale_sessions()
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    current_user_id = _get_optional_user_id()
    if not _can_view_session(session, current_user_id):
        return jsonify({'error': 'Session is private'}), 403
    return jsonify({'session': _serialize_session(session)})


@sessions_bp.route('/<int:session_id>/join', methods=['POST'])
@login_required
def join_session(session_id):
    """Join an open-to-play session."""
    _expire_stale_sessions()
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if session.status != 'active':
        return jsonify({'error': 'Session is no longer active'}), 400

    uid = request.current_user.id
    if session.creator_id == uid:
        return jsonify({'error': 'You created this session'}), 400

    existing = PlaySessionPlayer.query.filter_by(
        session_id=session_id, user_id=uid,
    ).first()
    if session.visibility == 'friends':
        creator_friend_ids = set(_get_friend_ids(session.creator_id))
        is_participant = bool(existing)
        if not (uid in creator_friend_ids or is_participant):
            return jsonify({'error': 'This is a friends-only session'}), 403

    if session.max_players is None or session.max_players < _MIN_MAX_PLAYERS:
        return jsonify({'error': 'Session capacity is invalid'}), 400

    joined_count = _joined_count(session_id)
    is_full = joined_count + 1 >= session.max_players  # +1 for creator
    waitlisted = False

    if existing and existing.status == 'joined':
        return jsonify({
            'message': 'Already joined',
            'waitlisted': False,
            'session': _serialize_session(session),
        })

    if existing:
        if is_full:
            existing.status = 'waitlisted'
            waitlisted = True
        else:
            existing.status = 'joined'
            existing.joined_at = utcnow_naive()
    else:
        psp = PlaySessionPlayer(
            session_id=session_id,
            user_id=uid,
            status='waitlisted' if is_full else 'joined',
        )
        waitlisted = is_full
        db.session.add(psp)

    # Notify the creator when someone actively joins the session.
    if not waitlisted:
        notif = Notification(
            user_id=session.creator_id, notif_type='session_join',
            content=f'{request.current_user.username} joined your play session',
            reference_id=session_id,
        )
        db.session.add(notif)
    try:
        db.session.commit()
    except IntegrityError:
        # Concurrent joins can race; unique(session_id, user_id) is the source of truth.
        db.session.rollback()
        existing_after_race = PlaySessionPlayer.query.filter_by(
            session_id=session_id,
            user_id=uid,
        ).first()
        current_session = db.session.get(PlaySession, session_id)
        if existing_after_race and current_session:
            waitlisted_after_race = existing_after_race.status == 'waitlisted'
            return jsonify({
                'message': 'Already joined' if not waitlisted_after_race else 'Session full — added to waitlist',
                'waitlisted': waitlisted_after_race,
                'session': _serialize_session(current_session),
            })
        return jsonify({'error': 'Could not join session right now. Please retry.'}), 409

    return jsonify({
        'message': 'Joined session' if not waitlisted else 'Session full — added to waitlist',
        'waitlisted': waitlisted,
        'session': _serialize_session(session),
    })


@sessions_bp.route('/<int:session_id>/leave', methods=['POST'])
@login_required
def leave_session(session_id):
    """Leave a session."""
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    removed = PlaySessionPlayer.query.filter_by(
        session_id=session_id, user_id=request.current_user.id,
    ).first()
    if not removed:
        return jsonify({'error': 'You are not in this session'}), 400
    db.session.delete(removed)

    promoted_user_id = None
    if session.status == 'active':
        joined_count = _joined_count(session_id)
        has_open_slot = joined_count + 1 < session.max_players  # +1 for creator
        if has_open_slot:
            next_waitlisted = PlaySessionPlayer.query.filter_by(
                session_id=session_id, status='waitlisted'
            ).order_by(PlaySessionPlayer.joined_at.asc()).first()
            if next_waitlisted:
                next_waitlisted.status = 'joined'
                next_waitlisted.joined_at = utcnow_naive()
                promoted_user_id = next_waitlisted.user_id
                notif = Notification(
                    user_id=next_waitlisted.user_id,
                    notif_type='session_spot_opened',
                    content=f'A spot opened up in a session at {session.court.name}. You were moved in!',
                    reference_id=session_id,
                )
                db.session.add(notif)

    db.session.commit()
    return jsonify({
        'message': 'Left session',
        'promoted_user_id': promoted_user_id,
    })


@sessions_bp.route('/<int:session_id>/invite', methods=['POST'])
@login_required
def invite_to_session(session_id):
    """Invite friends to a session."""
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if session.creator_id != request.current_user.id:
        return jsonify({'error': 'Only the session creator can invite players'}), 403
    if session.status != 'active':
        return jsonify({'error': 'Session is no longer active'}), 400

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    friend_ids, invalid_friend_ids = _normalize_friend_invite_ids(
        data.get('friend_ids', []),
        request.current_user.id,
    )
    if friend_ids is None:
        return jsonify({'error': 'friend_ids must be a list'}), 400
    if invalid_friend_ids:
        return jsonify({
            'error': 'You can only invite accepted friends',
            'invalid_friend_ids': invalid_friend_ids,
        }), 400
    if not friend_ids:
        return jsonify({'error': 'No friends selected'}), 400

    count = 0
    for fid in friend_ids:
        existing = PlaySessionPlayer.query.filter_by(
            session_id=session_id, user_id=fid,
        ).first()
        if existing:
            continue
        psp = PlaySessionPlayer(
            session_id=session_id, user_id=fid, status='invited',
        )
        db.session.add(psp)
        notif = Notification(
            user_id=fid, notif_type='session_invite',
            content=f'{request.current_user.username} invited you to play '
                    f'at {session.court.name}',
            reference_id=session_id,
        )
        db.session.add(notif)
        count += 1

    db.session.commit()
    return jsonify({'message': f'Invited {count} friend(s)'})


@sessions_bp.route('/<int:session_id>/end', methods=['POST'])
@login_required
def end_session(session_id):
    """End / complete a session (creator only)."""
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if session.creator_id != request.current_user.id:
        return jsonify({'error': 'Only the creator can end this session'}), 403
    session.status = 'completed'
    db.session.commit()
    return jsonify({'message': 'Session ended'})


@sessions_bp.route('/<int:session_id>', methods=['DELETE'])
@login_required
def cancel_session(session_id):
    """Cancel a session (creator only)."""
    session = db.session.get(PlaySession, session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if session.creator_id != request.current_user.id:
        return jsonify({'error': 'Only the creator can cancel this session'}), 403
    session.status = 'cancelled'
    _notify_session_participants(
        session,
        'session_cancelled',
        f'{request.current_user.username} cancelled a play session at {session.court.name}',
    )
    db.session.commit()
    return jsonify({'message': 'Session cancelled'})


@sessions_bp.route('/series/<int:series_id>/cancel', methods=['POST'])
@login_required
def cancel_series(series_id):
    """Cancel active upcoming sessions for a recurring series (creator only)."""
    series = db.session.get(RecurringSessionSeries, series_id)
    if not series:
        return jsonify({'error': 'Series not found'}), 404
    if series.creator_id != request.current_user.id:
        return jsonify({'error': 'Only the series creator can cancel this series'}), 403

    data = request.get_json() or {}
    include_started = bool(data.get('include_started', False))
    now_local = datetime.now()

    cancelled_ids = []
    links = RecurringSessionSeriesItem.query.filter_by(
        series_id=series_id
    ).order_by(RecurringSessionSeriesItem.sequence.asc()).all()
    for link in links:
        session = db.session.get(PlaySession, link.session_id)
        if not session or session.status != 'active':
            continue
        if session.session_type != 'scheduled':
            continue
        if session.start_time and session.start_time < now_local and not include_started:
            continue
        session.status = 'cancelled'
        _notify_session_participants(
            session,
            'session_cancelled',
            f'{request.current_user.username} cancelled a play session at {session.court.name}',
        )
        cancelled_ids.append(session.id)

    db.session.commit()
    return jsonify({
        'message': f'Cancelled {len(cancelled_ids)} session(s) in this series',
        'cancelled_session_ids': cancelled_ids,
        'cancelled_count': len(cancelled_ids),
        'series_id': series_id,
    })


def _get_optional_user_id():
    """Try to extract the current user's ID from the auth token (no error if absent)."""
    import jwt
    from flask import current_app
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        try:
            payload = jwt.decode(
                auth_header.split(' ')[1],
                current_app.config['SECRET_KEY'],
                algorithms=['HS256'],
            )
            return payload.get('user_id')
        except Exception:
            pass
    return None
