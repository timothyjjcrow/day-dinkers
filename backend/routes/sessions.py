"""Open-to-Play sessions — create, join, schedule, invite."""
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from backend.app import db
from backend.models import (
    PlaySession, PlaySessionPlayer, Court, Notification, Friendship,
    RecurringSessionSeries, RecurringSessionSeriesItem,
)
from backend.auth_utils import login_required
from backend.time_utils import utcnow_naive

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


@sessions_bp.route('', methods=['GET'])
def get_sessions():
    """List active sessions, filtered by visibility for the requester."""
    court_id = request.args.get('court_id', type=int)
    session_type = request.args.get('type', '')  # 'now', 'scheduled', or ''
    visibility_filter = (request.args.get('visibility') or 'all').strip().lower()
    skill_filter = (request.args.get('skill_level') or 'all').strip().lower()

    if visibility_filter not in ('all', 'friends'):
        visibility_filter = 'all'
    if skill_filter not in ('all', 'beginner', 'intermediate', 'advanced'):
        skill_filter = 'all'

    query = PlaySession.query.filter_by(status='active')
    if court_id:
        query = query.filter_by(court_id=court_id)
    if session_type:
        query = query.filter_by(session_type=session_type)

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


@sessions_bp.route('/my', methods=['GET'])
@login_required
def get_my_sessions():
    """Get sessions the current user created or joined."""
    uid = request.current_user.id

    # Sessions I created
    created = PlaySession.query.filter_by(
        creator_id=uid, status='active'
    ).all()

    # Sessions I joined
    joined_ids = [sp.session_id for sp in PlaySessionPlayer.query.filter_by(
        user_id=uid, status='joined'
    ).all()]
    joined = PlaySession.query.filter(
        PlaySession.id.in_(joined_ids),
        PlaySession.status == 'active',
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

    # For scheduled sessions, require start_time
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

    # Cancel any existing active "now" session by this user at this court
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
    db.session.commit()

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
