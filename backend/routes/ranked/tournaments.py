"""Ranked tournaments routes (v1 single-elimination)."""
from datetime import datetime, timedelta
from flask import request, jsonify
from sqlalchemy import func, case
from backend.app import db
from backend.models import (
    User, Court, Match, CheckIn, Notification,
    Tournament, TournamentParticipant, TournamentResult,
)
from backend.auth_utils import login_required
from backend.routes.ranked import ranked_bp
from backend.routes.ranked.helpers import _emit_ranked_update, _emit_notification_update
from backend.routes.ranked.tournaments_helpers import (
    ALLOWED_TOURNAMENT_FORMATS,
    ALLOWED_TOURNAMENT_ACCESS,
    ALLOWED_NO_SHOW_POLICIES,
    ALLOWED_TOURNAMENT_STATUSES,
    is_power_of_two,
    create_initial_single_elim_matches,
    serialize_tournament,
)
from backend.time_utils import utcnow_naive

_MAX_LIMIT = 100
_MIN_LIMIT = 1


def _parse_iso_datetime(raw_value):
    raw = str(raw_value or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _coerce_bool(raw_value, default=False):
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return raw_value == 1
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _normalize_id_list(raw_ids, max_items=250):
    if not isinstance(raw_ids, list):
        return []
    normalized = []
    seen = set()
    for raw_id in raw_ids:
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(item_id)
        if len(normalized) >= max_items:
            break
    return normalized


def _registration_rows(tournament):
    return [
        row for row in (tournament.participants or [])
        if row.participant_status in {'registered', 'checked_in'}
    ]


def _is_host(tournament, user_id):
    return bool(tournament and int(tournament.host_user_id) == int(user_id))


def _optional_current_user_id():
    import jwt
    from flask import current_app
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ')[1]
    try:
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=['HS256'],
        )
    except Exception:
        return None
    return payload.get('user_id')


def _create_invite_notifications(invited_user_ids, host_user, tournament):
    for user_id in invited_user_ids:
        db.session.add(Notification(
            user_id=user_id,
            notif_type='tournament_invite',
            content=(
                f'{host_user.username} invited you to tournament '
                f'"{tournament.name}" at {tournament.court.name}.'
            ),
            reference_id=tournament.id,
        ))


def _participant_for_user(tournament_id, user_id):
    return TournamentParticipant.query.filter_by(
        tournament_id=tournament_id,
        user_id=user_id,
    ).first()


@ranked_bp.route('/tournaments', methods=['GET'])
def get_tournaments():
    court_id = request.args.get('court_id', type=int)
    status = (request.args.get('status') or '').strip().lower()
    upcoming_only = _coerce_bool(request.args.get('upcoming'), default=False)
    limit = request.args.get('limit', 25, type=int)
    limit = max(_MIN_LIMIT, min(limit or 25, _MAX_LIMIT))

    query = Tournament.query
    if court_id:
        query = query.filter(Tournament.court_id == court_id)
    if status:
        if status not in ALLOWED_TOURNAMENT_STATUSES:
            return jsonify({'error': 'Invalid status filter'}), 400
        query = query.filter(Tournament.status == status)
    if upcoming_only:
        now = utcnow_naive()
        query = query.filter(
            Tournament.status.in_(['upcoming', 'live']),
            Tournament.start_time >= (now - timedelta(hours=6)),
        )

    tournaments = query.order_by(
        Tournament.start_time.asc(),
        Tournament.created_at.desc(),
    ).limit(limit).all()
    return jsonify({
        'tournaments': [
            tournament.to_dict(include_participants=False, include_results=False)
            for tournament in tournaments
        ],
    })


@ranked_bp.route('/tournaments', methods=['POST'])
@login_required
def create_tournament():
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

    name = str(data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Tournament name required'}), 400
    name = name[:200]
    description = str(data.get('description') or '').strip()[:4000]

    start_time = _parse_iso_datetime(data.get('start_time'))
    if not start_time:
        return jsonify({'error': 'start_time must be a valid ISO datetime'}), 400
    if start_time <= utcnow_naive():
        return jsonify({'error': 'start_time must be in the future'}), 400

    registration_close_time = _parse_iso_datetime(data.get('registration_close_time'))
    if registration_close_time and registration_close_time > start_time:
        return jsonify({'error': 'registration_close_time must be before start_time'}), 400
    if registration_close_time is None:
        registration_close_time = start_time

    tournament_format = str(data.get('tournament_format') or 'single_elimination').strip().lower()
    if tournament_format not in ALLOWED_TOURNAMENT_FORMATS:
        return jsonify({'error': 'Only single_elimination is supported in v1'}), 400

    access_mode = str(data.get('access_mode') or 'open').strip().lower()
    if access_mode not in ALLOWED_TOURNAMENT_ACCESS:
        return jsonify({'error': 'Invalid access_mode'}), 400

    match_type = str(data.get('match_type') or 'singles').strip().lower()
    if match_type != 'singles':
        return jsonify({'error': 'Only singles tournament matches are supported in v1'}), 400

    affects_elo = _coerce_bool(data.get('affects_elo'), default=True)
    check_in_required = _coerce_bool(data.get('check_in_required'), default=True)

    try:
        max_players = int(data.get('max_players', 16))
        min_participants = int(data.get('min_participants', 4))
        no_show_grace_minutes = int(data.get('no_show_grace_minutes', 10))
    except (TypeError, ValueError):
        return jsonify({'error': 'max_players, min_participants, and no_show_grace_minutes must be numbers'}), 400

    if max_players < 2 or max_players > 128:
        return jsonify({'error': 'max_players must be between 2 and 128'}), 400
    if min_participants < 2 or min_participants > max_players:
        return jsonify({'error': 'min_participants must be between 2 and max_players'}), 400
    if no_show_grace_minutes < 0 or no_show_grace_minutes > 180:
        return jsonify({'error': 'no_show_grace_minutes must be between 0 and 180'}), 400

    no_show_policy = str(data.get('no_show_policy') or 'auto_forfeit').strip().lower()
    if no_show_policy not in ALLOWED_NO_SHOW_POLICIES:
        return jsonify({'error': 'Invalid no_show_policy'}), 400

    tournament = Tournament(
        court_id=court_id,
        host_user_id=request.current_user.id,
        name=name,
        description=description,
        tournament_format=tournament_format,
        access_mode=access_mode,
        match_type=match_type,
        affects_elo=affects_elo,
        status='upcoming',
        start_time=start_time,
        registration_close_time=registration_close_time,
        max_players=max_players,
        min_participants=min_participants,
        check_in_required=check_in_required,
        no_show_policy=no_show_policy,
        no_show_grace_minutes=no_show_grace_minutes,
    )
    db.session.add(tournament)
    db.session.flush()

    host_participant = TournamentParticipant(
        tournament_id=tournament.id,
        user_id=request.current_user.id,
        invited_by_user_id=request.current_user.id,
        invite_status='accepted',
        participant_status='registered',
    )
    db.session.add(host_participant)

    invite_ids = _normalize_id_list(data.get('invite_user_ids') or [])
    invite_ids = [uid for uid in invite_ids if uid != request.current_user.id]
    invited_users = User.query.filter(User.id.in_(invite_ids)).all() if invite_ids else []
    invited_ids = set()
    for invited_user in invited_users:
        invited_ids.add(invited_user.id)
        existing = _participant_for_user(tournament.id, invited_user.id)
        if existing:
            existing.invite_status = 'invited'
            existing.participant_status = 'invited'
            existing.invited_by_user_id = request.current_user.id
            continue
        db.session.add(TournamentParticipant(
            tournament_id=tournament.id,
            user_id=invited_user.id,
            invited_by_user_id=request.current_user.id,
            invite_status='invited',
            participant_status='invited',
        ))
    _create_invite_notifications(sorted(invited_ids), request.current_user, tournament)

    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_created')
    if invited_ids:
        _emit_notification_update(court_id=tournament.court_id, reason='tournament_invited')
    return jsonify({'tournament': serialize_tournament(tournament)}), 201


@ranked_bp.route('/tournaments/<int:tournament_id>', methods=['GET'])
def get_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    payload = serialize_tournament(tournament)
    user_id = _optional_current_user_id()
    if user_id:
        mine = _participant_for_user(tournament.id, user_id)
        payload['my_participation'] = mine.to_dict() if mine else None
    return jsonify({'tournament': payload})


@ranked_bp.route('/tournaments/<int:tournament_id>/join', methods=['POST'])
@login_required
def join_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Tournament is no longer open for registration'}), 400
    if tournament.registration_close_time and utcnow_naive() > tournament.registration_close_time:
        return jsonify({'error': 'Tournament registration is closed'}), 400

    participant = _participant_for_user(tournament.id, request.current_user.id)
    active_rows = _registration_rows(tournament)
    if not participant and len(active_rows) >= tournament.max_players:
        return jsonify({'error': 'Tournament is full'}), 400

    if tournament.access_mode == 'invite_only':
        if not participant:
            return jsonify({'error': 'This tournament is invite-only'}), 403
        if participant.invite_status not in {'invited', 'accepted'}:
            return jsonify({'error': 'You are not invited to this tournament'}), 403
        participant.invite_status = 'accepted'
        participant.participant_status = 'registered'
    else:
        if participant:
            if participant.participant_status in {'registered', 'checked_in'}:
                return jsonify({'message': 'Already registered'})
            participant.invite_status = 'accepted'
            participant.participant_status = 'registered'
        else:
            db.session.add(TournamentParticipant(
                tournament_id=tournament.id,
                user_id=request.current_user.id,
                invited_by_user_id=request.current_user.id,
                invite_status='accepted',
                participant_status='registered',
            ))
    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_join')
    return jsonify({'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/<int:tournament_id>/leave', methods=['POST'])
@login_required
def leave_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Cannot leave after tournament has started'}), 400
    if _is_host(tournament, request.current_user.id):
        return jsonify({'error': 'Host cannot leave this tournament'}), 400

    participant = _participant_for_user(tournament.id, request.current_user.id)
    if not participant:
        return jsonify({'error': 'You are not registered for this tournament'}), 404
    db.session.delete(participant)
    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_leave')
    return jsonify({'message': 'Left tournament'})


@ranked_bp.route('/tournaments/<int:tournament_id>/withdraw', methods=['POST'])
@login_required
def withdraw_tournament(tournament_id):
    """Withdraw from a tournament before it starts."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Cannot withdraw after tournament has started'}), 400
    if _is_host(tournament, request.current_user.id):
        return jsonify({'error': 'Host cannot withdraw from their tournament'}), 400

    participant = _participant_for_user(tournament.id, request.current_user.id)
    if not participant:
        return jsonify({'error': 'You are not in this tournament'}), 404

    participant.invite_status = 'declined'
    participant.participant_status = 'withdrawn'
    db.session.add(Notification(
        user_id=tournament.host_user_id,
        notif_type='tournament_withdrawal',
        content=(
            f'{request.current_user.username} withdrew from '
            f'"{tournament.name}".'
        ),
        reference_id=tournament.id,
    ))
    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_withdraw')
    _emit_notification_update(court_id=tournament.court_id, reason='tournament_withdraw')
    return jsonify({'message': 'Withdrawn from tournament', 'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/<int:tournament_id>/invite', methods=['POST'])
@login_required
def invite_players_to_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if not _is_host(tournament, request.current_user.id):
        return jsonify({'error': 'Only the host can invite players'}), 403
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Tournament invites are closed'}), 400

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    user_ids = _normalize_id_list(data.get('user_ids') or [])
    user_ids = [uid for uid in user_ids if uid != request.current_user.id]
    if not user_ids:
        return jsonify({'error': 'Provide at least one player ID'}), 400

    users = User.query.filter(User.id.in_(user_ids)).all()
    invited_ids = []
    skipped_ids = []
    for user in users:
        participant = _participant_for_user(tournament.id, user.id)
        if participant and participant.participant_status in {'registered', 'checked_in'}:
            skipped_ids.append(user.id)
            continue
        if participant:
            participant.invite_status = 'invited'
            participant.participant_status = 'invited'
            participant.invited_by_user_id = request.current_user.id
        else:
            db.session.add(TournamentParticipant(
                tournament_id=tournament.id,
                user_id=user.id,
                invited_by_user_id=request.current_user.id,
                invite_status='invited',
                participant_status='invited',
            ))
        invited_ids.append(user.id)
    _create_invite_notifications(invited_ids, request.current_user, tournament)
    db.session.commit()
    if invited_ids:
        _emit_notification_update(court_id=tournament.court_id, reason='tournament_invited')
    return jsonify({
        'message': f'Invited {len(invited_ids)} player(s)',
        'invited_ids': invited_ids,
        'skipped_ids': skipped_ids,
    })


@ranked_bp.route('/tournaments/<int:tournament_id>/respond', methods=['POST'])
@login_required
def respond_tournament_invite(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Tournament invite responses are closed'}), 400

    participant = _participant_for_user(tournament.id, request.current_user.id)
    if not participant:
        return jsonify({'error': 'You are not invited to this tournament'}), 404

    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '').strip().lower()
    if action not in {'accept', 'decline'}:
        return jsonify({'error': 'action must be accept or decline'}), 400

    if action == 'accept':
        participant.invite_status = 'accepted'
        participant.participant_status = 'registered'
    else:
        participant.invite_status = 'declined'
        participant.participant_status = 'declined'
    db.session.add(Notification(
        user_id=tournament.host_user_id,
        notif_type='tournament_invite_response',
        content=(
            f'{request.current_user.username} '
            f'{"accepted" if action == "accept" else "declined"} '
            f'your tournament invite for "{tournament.name}".'
        ),
        reference_id=tournament.id,
    ))
    db.session.commit()
    _emit_notification_update(court_id=tournament.court_id, reason='tournament_invite_response')
    return jsonify({'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/<int:tournament_id>/check-in', methods=['POST'])
@login_required
def check_in_tournament_participant(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Tournament check-in is closed'}), 400

    participant = _participant_for_user(tournament.id, request.current_user.id)
    if not participant or participant.participant_status not in {'registered', 'checked_in'}:
        return jsonify({'error': 'You are not registered for this tournament'}), 400

    active_checkin = CheckIn.query.filter_by(
        user_id=request.current_user.id,
        court_id=tournament.court_id,
        checked_out_at=None,
    ).first()
    if not active_checkin:
        return jsonify({'error': 'Check in at this court first'}), 400

    participant.checked_in_at = utcnow_naive()
    participant.participant_status = 'checked_in'
    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_checkin')
    return jsonify({'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/<int:tournament_id>/participants/<int:user_id>/no-show', methods=['POST'])
@login_required
def mark_tournament_no_show(tournament_id, user_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if not _is_host(tournament, request.current_user.id):
        return jsonify({'error': 'Only the host can mark no-shows'}), 403
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Cannot mark no-shows after tournament starts'}), 400

    participant = _participant_for_user(tournament.id, user_id)
    if not participant:
        return jsonify({'error': 'Participant not found'}), 404
    if participant.checked_in_at:
        return jsonify({'error': 'Checked-in participants cannot be marked no-show'}), 400
    participant.participant_status = 'no_show'
    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_no_show_marked')
    return jsonify({'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/<int:tournament_id>/start', methods=['POST'])
@login_required
def start_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if not _is_host(tournament, request.current_user.id):
        return jsonify({'error': 'Only the host can start this tournament'}), 403
    if tournament.status != 'upcoming':
        return jsonify({'error': 'Tournament has already started or ended'}), 400

    participants = _registration_rows(tournament)
    now = utcnow_naive()
    grace_cutoff = tournament.start_time + timedelta(minutes=tournament.no_show_grace_minutes or 0)
    if len(participants) < tournament.min_participants:
        return jsonify({'error': f'Need at least {tournament.min_participants} participants to start'}), 400

    if tournament.check_in_required:
        missing = []
        for participant in participants:
            if participant.checked_in_at:
                continue
            active_checkin = CheckIn.query.filter_by(
                user_id=participant.user_id,
                court_id=tournament.court_id,
                checked_out_at=None,
            ).first()
            if active_checkin:
                participant.checked_in_at = utcnow_naive()
                participant.participant_status = 'checked_in'
            else:
                missing.append(participant.user_id)
        if missing and tournament.no_show_policy == 'auto_forfeit' and now < grace_cutoff:
            return jsonify({
                'error': 'Waiting for player check-ins before no-show auto-forfeit',
                'missing_player_ids': sorted(missing),
                'grace_ends_at': grace_cutoff.isoformat(),
            }), 400
        if missing and tournament.no_show_policy == 'host_mark':
            return jsonify({
                'error': 'Some participants are not checked in',
                'missing_player_ids': sorted(missing),
            }), 400
        if missing and tournament.no_show_policy == 'auto_forfeit':
            missing_set = set(missing)
            for participant in participants:
                if participant.user_id in missing_set:
                    participant.participant_status = 'no_show'
        participants = _registration_rows(tournament)

    if len(participants) < tournament.min_participants:
        return jsonify({'error': 'Not enough checked-in participants to start'}), 400
    if len(participants) > tournament.max_players:
        return jsonify({'error': 'Too many participants for tournament capacity'}), 400
    if not is_power_of_two(len(participants)):
        return jsonify({
            'error': 'Single-elimination requires a power-of-two participant count (4, 8, 16, ...)',
            'participant_count': len(participants),
        }), 400

    create_initial_single_elim_matches(tournament, participants)
    tournament.status = 'live'
    tournament.started_at = utcnow_naive()

    for participant in participants:
        db.session.add(Notification(
            user_id=participant.user_id,
            notif_type='tournament_started',
            content=f'Tournament "{tournament.name}" is now live.',
            reference_id=tournament.id,
        ))

    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_started')
    _emit_notification_update(court_id=tournament.court_id, reason='tournament_started')
    return jsonify({'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/<int:tournament_id>/cancel', methods=['POST'])
@login_required
def cancel_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'Tournament not found'}), 404
    if not _is_host(tournament, request.current_user.id):
        return jsonify({'error': 'Only the host can cancel this tournament'}), 403
    if tournament.status in {'completed', 'cancelled'}:
        return jsonify({'error': 'Tournament already ended'}), 400

    tournament.status = 'cancelled'
    tournament.cancelled_at = utcnow_naive()
    Match.query.filter(
        Match.tournament_id == tournament.id,
        Match.status.in_(['in_progress', 'pending_confirmation']),
    ).update({'status': 'cancelled'}, synchronize_session=False)
    for participant in tournament.participants:
        if participant.participant_status in {'registered', 'checked_in', 'invited'}:
            participant.participant_status = 'withdrawn'
        db.session.add(Notification(
            user_id=participant.user_id,
            notif_type='tournament_cancelled',
            content=f'Tournament "{tournament.name}" was cancelled.',
            reference_id=tournament.id,
        ))
    db.session.commit()
    _emit_ranked_update(court_id=tournament.court_id, reason='tournament_cancelled')
    _emit_notification_update(court_id=tournament.court_id, reason='tournament_cancelled')
    return jsonify({'message': 'Tournament cancelled', 'tournament': serialize_tournament(tournament)})


@ranked_bp.route('/tournaments/leaderboard', methods=['GET'])
def get_tournament_leaderboard():
    court_id = request.args.get('court_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    limit = max(_MIN_LIMIT, min(limit or 50, _MAX_LIMIT))

    query = db.session.query(
        TournamentResult.user_id.label('user_id'),
        func.sum(TournamentResult.points).label('points'),
        func.sum(TournamentResult.wins).label('wins'),
        func.sum(TournamentResult.losses).label('losses'),
        func.count(TournamentResult.id).label('tournaments_played'),
        func.sum(case((TournamentResult.placement == 1, 1), else_=0)).label('titles'),
        func.avg(TournamentResult.placement).label('avg_placement'),
        func.min(TournamentResult.placement).label('best_finish'),
    )
    if court_id:
        query = query.filter(TournamentResult.court_id == court_id)
    rows = query.group_by(
        TournamentResult.user_id,
    ).order_by(
        func.sum(TournamentResult.points).desc(),
        func.sum(case((TournamentResult.placement == 1, 1), else_=0)).desc(),
        func.sum(TournamentResult.wins).desc(),
        func.avg(TournamentResult.placement).asc(),
        func.sum(TournamentResult.losses).asc(),
    ).limit(limit).all()

    user_ids = [int(row.user_id) for row in rows]
    users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    users_by_id = {user.id: user for user in users}
    leaderboard = []
    for rank, row in enumerate(rows, start=1):
        user = users_by_id.get(int(row.user_id))
        wins = int(row.wins or 0)
        losses = int(row.losses or 0)
        played = int(row.tournaments_played or 0)
        total_decisions = wins + losses
        win_rate = round((wins / total_decisions) if total_decisions else 0.0, 3)
        points = int(row.points or 0)
        leaderboard.append({
            'rank': rank,
            'user_id': int(row.user_id),
            'name': user.name if user else '',
            'username': user.username if user else '',
            'points': points,
            'wins': wins,
            'losses': losses,
            'tournaments_played': played,
            'titles': int(row.titles or 0),
            'avg_placement': round(float(row.avg_placement or 0), 2),
            'best_finish': int(row.best_finish or 0),
            'win_rate': win_rate,
            'points_per_tournament': round((points / played) if played else 0.0, 2),
            'sort_order': ['points_desc', 'titles_desc', 'wins_desc', 'avg_placement_asc', 'losses_asc'],
        })
    return jsonify({'leaderboard': leaderboard, 'court_id': court_id})


@ranked_bp.route('/tournaments/results', methods=['GET'])
def get_tournament_results():
    user_id = request.args.get('user_id', type=int)
    court_id = request.args.get('court_id', type=int)
    limit = request.args.get('limit', 30, type=int)
    limit = max(_MIN_LIMIT, min(limit or 30, _MAX_LIMIT))

    query = TournamentResult.query
    if user_id:
        query = query.filter(TournamentResult.user_id == user_id)
    if court_id:
        query = query.filter(TournamentResult.court_id == court_id)
    results = query.order_by(
        TournamentResult.created_at.desc(),
    ).limit(limit).all()
    return jsonify({'results': [result.to_dict() for result in results]})


@ranked_bp.route('/tournaments/upcoming', methods=['GET'])
def get_upcoming_tournaments():
    court_id = request.args.get('court_id', type=int)
    days = request.args.get('days', 30, type=int)
    days = max(1, min(days or 30, 120))
    now = utcnow_naive()
    upper = now + timedelta(days=days)

    query = Tournament.query.filter(
        Tournament.status.in_(['upcoming', 'live']),
        Tournament.start_time >= now - timedelta(hours=6),
        Tournament.start_time <= upper,
    )
    if court_id:
        query = query.filter(Tournament.court_id == court_id)

    tournaments = query.order_by(Tournament.start_time.asc()).limit(_MAX_LIMIT).all()
    payload = []
    for tournament in tournaments:
        data = tournament.to_dict(include_participants=False, include_results=False)
        data['item_type'] = 'tournament'
        data['visibility'] = tournament.access_mode
        data['participants_count'] = len(_registration_rows(tournament))
        payload.append(data)
    return jsonify({'tournaments': payload})
