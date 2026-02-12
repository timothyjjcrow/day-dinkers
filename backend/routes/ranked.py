"""Ranked competitive play — queue, challenges, scheduling, and score confirmation."""
from datetime import datetime
from flask import Blueprint, request, jsonify
from backend.app import db, socketio
from backend.models import (
    User, Court, Match, MatchPlayer, RankedQueue, CheckIn, Notification,
    RankedLobby, RankedLobbyPlayer,
)
from backend.auth_utils import login_required
from backend.services.elo import calculate_elo_changes
from backend.time_utils import utcnow_naive

ranked_bp = Blueprint('ranked', __name__)
_ALLOWED_MATCH_TYPES = {'singles', 'doubles'}
_ALLOWED_LOBBY_SOURCES = {
    'queue',
    'court_challenge',
    'scheduled_challenge',
    'friends_challenge',
    'leaderboard_challenge',
    'manual',
}
_MIN_SCORE = 0
_MAX_SCORE = 99


def _parse_team_ids(raw_ids):
    if not isinstance(raw_ids, list):
        return None
    ids = []
    for raw in raw_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        ids.append(value)
    return ids


def _validate_match_setup(match_type, team1_ids, team2_ids):
    if match_type not in _ALLOWED_MATCH_TYPES:
        return 'Invalid match type'
    if not team1_ids or not team2_ids:
        return 'Both teams must have players'

    expected_per_team = 1 if match_type == 'singles' else 2
    if len(team1_ids) != expected_per_team or len(team2_ids) != expected_per_team:
        return f'{match_type.capitalize()} requires {expected_per_team} per team'

    all_ids = team1_ids + team2_ids
    if len(set(all_ids)) != len(all_ids):
        return 'Duplicate players across teams'
    return None


def _resolve_players(user_ids):
    unique_ids = set(user_ids)
    players = User.query.filter(User.id.in_(unique_ids)).all()
    if len(players) != len(unique_ids):
        return None
    return {player.id: player for player in players}


def _create_match_record(court_id, match_type, team1_ids, team2_ids, players_by_id):
    match = Match(
        court_id=court_id,
        match_type=match_type,
        status='in_progress',
    )
    db.session.add(match)
    db.session.flush()

    for uid in team1_ids:
        db.session.add(MatchPlayer(
            match_id=match.id,
            user_id=uid,
            team=1,
            elo_before=players_by_id[uid].elo_rating,
        ))
    for uid in team2_ids:
        db.session.add(MatchPlayer(
            match_id=match.id,
            user_id=uid,
            team=2,
            elo_before=players_by_id[uid].elo_rating,
        ))
    return match


def _checked_in_user_ids(court_id, user_ids=None):
    query = CheckIn.query.filter_by(court_id=court_id, checked_out_at=None)
    if user_ids is not None:
        query = query.filter(CheckIn.user_id.in_(list(user_ids)))
    return {ci.user_id for ci in query.all()}


def _user_checked_in_at_court(user_id, court_id):
    active = CheckIn.query.filter_by(user_id=user_id, checked_out_at=None).first()
    return bool(active and active.court_id == court_id)


def _update_lobby_status(lobby):
    statuses = [p.acceptance_status for p in (lobby.players or [])]
    if any(status == 'declined' for status in statuses):
        lobby.status = 'declined'
        return
    if statuses and all(status == 'accepted' for status in statuses):
        lobby.status = 'ready'
        return
    lobby.status = 'pending_acceptance'


def _lobby_to_dict(lobby):
    data = lobby.to_dict()
    accepted_count = 0
    for player in data.get('players', []):
        if player.get('acceptance_status') == 'accepted':
            accepted_count += 1
    data['accepted_count'] = accepted_count
    data['total_players'] = len(data.get('players', []))
    return data


def _create_lobby(
    *,
    court_id,
    created_by_id,
    match_type,
    team1_ids,
    team2_ids,
    source,
    scheduled_for=None,
    accepted_user_ids=None,
):
    lobby = RankedLobby(
        court_id=court_id,
        created_by_id=created_by_id,
        match_type=match_type,
        source=source if source in _ALLOWED_LOBBY_SOURCES else 'manual',
        scheduled_for=scheduled_for,
        status='pending_acceptance',
    )
    db.session.add(lobby)
    db.session.flush()

    accepted_set = set(accepted_user_ids or [])
    now = utcnow_naive()

    for uid in team1_ids:
        accepted = uid in accepted_set
        db.session.add(RankedLobbyPlayer(
            lobby_id=lobby.id,
            user_id=uid,
            team=1,
            acceptance_status='accepted' if accepted else 'pending',
            responded_at=now if accepted else None,
        ))
    for uid in team2_ids:
        accepted = uid in accepted_set
        db.session.add(RankedLobbyPlayer(
            lobby_id=lobby.id,
            user_id=uid,
            team=2,
            acceptance_status='accepted' if accepted else 'pending',
            responded_at=now if accepted else None,
        ))

    db.session.flush()
    _update_lobby_status(lobby)
    return lobby


def _notify_lobby_players(lobby, notif_type, content, exclude_user_ids=None):
    excluded = set(exclude_user_ids or [])
    for participant in lobby.players:
        if participant.user_id in excluded:
            continue
        db.session.add(Notification(
            user_id=participant.user_id,
            notif_type=notif_type,
            content=content,
            reference_id=lobby.id,
        ))


def _parse_iso_datetime(raw_value):
    raw = str(raw_value or '').strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _prune_queue_for_court(court_id):
    active_subquery = db.session.query(CheckIn.user_id).filter(
        CheckIn.court_id == court_id,
        CheckIn.checked_out_at.is_(None),
    )
    RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        ~RankedQueue.user_id.in_(active_subquery),
    ).delete(synchronize_session=False)


def _emit_ranked_update(court_id=None, reason=''):
    payload = {
        'court_id': court_id,
        'reason': reason,
        'updated_at': utcnow_naive().isoformat(),
    }
    socketio.emit('ranked_update', payload)


def _emit_notification_update(court_id=None, reason=''):
    payload = {
        'court_id': court_id,
        'reason': reason,
        'updated_at': utcnow_naive().isoformat(),
    }
    socketio.emit('notification_update', payload)


# ── Queue Management ──────────────────────────────────────────────────

@ranked_bp.route('/queue/<int:court_id>', methods=['GET'])
def get_queue(court_id):
    _prune_queue_for_court(court_id)
    db.session.commit()
    entries = RankedQueue.query.filter_by(court_id=court_id)\
        .order_by(RankedQueue.joined_at.asc()).all()
    return jsonify({'queue': [e.to_dict() for e in entries]})


@ranked_bp.route('/queue/join', methods=['POST'])
@login_required
def join_queue():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    match_type = str(data.get('match_type', 'doubles')).strip().lower()

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    if match_type not in _ALLOWED_MATCH_TYPES:
        return jsonify({'error': 'Invalid match type'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if not _user_checked_in_at_court(request.current_user.id, court_id):
        return jsonify({'error': 'Check in at this court before joining the ranked queue'}), 400

    _prune_queue_for_court(court_id)

    existing = RankedQueue.query.filter_by(
        user_id=request.current_user.id, court_id=court_id
    ).first()
    if existing:
        return jsonify({'error': 'Already in queue at this court'}), 409

    RankedQueue.query.filter(
        RankedQueue.user_id == request.current_user.id,
        RankedQueue.court_id != court_id,
    ).delete(synchronize_session=False)

    entry = RankedQueue(
        user_id=request.current_user.id,
        court_id=court_id,
        match_type=match_type,
    )
    db.session.add(entry)
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='queue_join')
    return jsonify({'message': 'Joined queue', 'entry': entry.to_dict()}), 201


@ranked_bp.route('/queue/leave', methods=['POST'])
@login_required
def leave_queue():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    RankedQueue.query.filter_by(
        user_id=request.current_user.id, court_id=court_id
    ).delete()
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='queue_leave')
    return jsonify({'message': 'Left queue'})


# ── Ranked Lobby / Challenge Flows ────────────────────────────────────

@ranked_bp.route('/lobby/queue', methods=['POST'])
@login_required
def create_lobby_from_queue():
    """Create a ready-to-start ranked lobby from queued players."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    match_type = str(data.get('match_type', 'doubles')).strip().lower()
    team1_ids = _parse_team_ids(data.get('team1', []))
    team2_ids = _parse_team_ids(data.get('team2', []))

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if team1_ids is None or team2_ids is None:
        return jsonify({'error': 'Both teams must be lists of numeric player IDs'}), 400

    setup_error = _validate_match_setup(match_type, team1_ids, team2_ids)
    if setup_error:
        return jsonify({'error': setup_error}), 400

    all_ids = set(team1_ids + team2_ids)
    if not _resolve_players(all_ids):
        return jsonify({'error': 'One or more players not found'}), 404

    _prune_queue_for_court(court_id)
    queue_entries = RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(all_ids),
    ).all()
    if len(queue_entries) != len(all_ids):
        return jsonify({'error': 'All selected players must be in the ranked queue at this court'}), 400
    for entry in queue_entries:
        if entry.match_type != match_type:
            return jsonify({'error': 'All selected queue players must share the same match type'}), 400

    lobby = _create_lobby(
        court_id=court_id,
        created_by_id=request.current_user.id,
        match_type=match_type,
        team1_ids=team1_ids,
        team2_ids=team2_ids,
        source='queue',
        accepted_user_ids=all_ids,
    )
    RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(all_ids),
    ).delete(synchronize_session=False)
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='lobby_created_from_queue')
    return jsonify({'lobby': _lobby_to_dict(lobby)}), 201


@ranked_bp.route('/challenge/court', methods=['POST'])
@login_required
def create_court_challenge():
    """Challenge checked-in players at a court to a ranked lobby."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    match_type = str(data.get('match_type', 'singles')).strip().lower()
    team1_ids = _parse_team_ids(data.get('team1', []))
    team2_ids = _parse_team_ids(data.get('team2', []))

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if team1_ids is None or team2_ids is None:
        return jsonify({'error': 'Both teams must be lists of numeric player IDs'}), 400

    setup_error = _validate_match_setup(match_type, team1_ids, team2_ids)
    if setup_error:
        return jsonify({'error': setup_error}), 400

    all_ids = set(team1_ids + team2_ids)
    if request.current_user.id not in all_ids:
        return jsonify({'error': 'You must be included in the challenge teams'}), 400
    if not _resolve_players(all_ids):
        return jsonify({'error': 'One or more players not found'}), 404

    if not _user_checked_in_at_court(request.current_user.id, court_id):
        return jsonify({'error': 'Check in at this court before challenging players'}), 400

    checked_in_ids = _checked_in_user_ids(court_id, all_ids)
    if checked_in_ids != all_ids:
        missing_ids = sorted(all_ids - checked_in_ids)
        return jsonify({
            'error': 'All challenged players must be checked in at this court',
            'missing_player_ids': missing_ids,
        }), 400

    lobby = _create_lobby(
        court_id=court_id,
        created_by_id=request.current_user.id,
        match_type=match_type,
        team1_ids=team1_ids,
        team2_ids=team2_ids,
        source='court_challenge',
        accepted_user_ids={request.current_user.id},
    )
    _notify_lobby_players(
        lobby,
        notif_type='ranked_challenge_invite',
        content=(
            f'{request.current_user.username} challenged you to a {match_type} ranked game '
            f'at {court.name}. Accept to join.'
        ),
        exclude_user_ids={request.current_user.id},
    )
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='court_challenge_created')
    _emit_notification_update(court_id=court_id, reason='court_challenge_invite')
    return jsonify({'lobby': _lobby_to_dict(lobby)}), 201


@ranked_bp.route('/challenge/scheduled', methods=['POST'])
@login_required
def create_scheduled_challenge():
    """Create a scheduled ranked challenge invite."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400

    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    match_type = str(data.get('match_type', 'singles')).strip().lower()
    team1_ids = _parse_team_ids(data.get('team1', []))
    team2_ids = _parse_team_ids(data.get('team2', []))
    scheduled_for = _parse_iso_datetime(data.get('scheduled_for') or data.get('scheduled_time'))
    source = str(data.get('source') or 'scheduled_challenge').strip().lower()

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if not scheduled_for:
        return jsonify({'error': 'scheduled_for is required and must be a valid ISO datetime'}), 400
    if scheduled_for.tzinfo is not None:
        scheduled_for = scheduled_for.astimezone().replace(tzinfo=None)
    if scheduled_for <= utcnow_naive():
        return jsonify({'error': 'scheduled_for must be in the future'}), 400
    if team1_ids is None or team2_ids is None:
        return jsonify({'error': 'Both teams must be lists of numeric player IDs'}), 400

    setup_error = _validate_match_setup(match_type, team1_ids, team2_ids)
    if setup_error:
        return jsonify({'error': setup_error}), 400

    all_ids = set(team1_ids + team2_ids)
    if request.current_user.id not in all_ids:
        return jsonify({'error': 'You must be included in the scheduled challenge teams'}), 400
    if not _resolve_players(all_ids):
        return jsonify({'error': 'One or more players not found'}), 404

    if source not in _ALLOWED_LOBBY_SOURCES:
        source = 'scheduled_challenge'

    lobby = _create_lobby(
        court_id=court_id,
        created_by_id=request.current_user.id,
        match_type=match_type,
        team1_ids=team1_ids,
        team2_ids=team2_ids,
        source=source,
        scheduled_for=scheduled_for,
        accepted_user_ids={request.current_user.id},
    )
    _notify_lobby_players(
        lobby,
        notif_type='ranked_challenge_invite',
        content=(
            f'{request.current_user.username} invited you to a scheduled {match_type} ranked game '
            f'at {court.name}. Accept to confirm.'
        ),
        exclude_user_ids={request.current_user.id},
    )
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='scheduled_challenge_created')
    _emit_notification_update(court_id=court_id, reason='scheduled_challenge_invite')
    return jsonify({'lobby': _lobby_to_dict(lobby)}), 201


@ranked_bp.route('/lobby/<int:lobby_id>', methods=['GET'])
def get_lobby(lobby_id):
    lobby = db.session.get(RankedLobby, lobby_id)
    if not lobby:
        return jsonify({'error': 'Lobby not found'}), 404
    return jsonify({'lobby': _lobby_to_dict(lobby)})


@ranked_bp.route('/lobby/<int:lobby_id>/respond', methods=['POST'])
@login_required
def respond_to_lobby(lobby_id):
    """Accept or decline a ranked challenge invite."""
    lobby = db.session.get(RankedLobby, lobby_id)
    if not lobby:
        return jsonify({'error': 'Lobby not found'}), 404
    if lobby.status != 'pending_acceptance':
        return jsonify({'error': 'Lobby is no longer awaiting responses'}), 400

    participant = RankedLobbyPlayer.query.filter_by(
        lobby_id=lobby_id,
        user_id=request.current_user.id,
    ).first()
    if not participant:
        return jsonify({'error': 'You are not invited to this lobby'}), 403

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    action = str(data.get('action') or '').strip().lower()
    if action not in {'accept', 'decline'}:
        return jsonify({'error': 'Action must be accept or decline'}), 400

    participant.acceptance_status = 'accepted' if action == 'accept' else 'declined'
    participant.responded_at = utcnow_naive()

    previous_status = lobby.status
    _update_lobby_status(lobby)

    if action == 'decline':
        _notify_lobby_players(
            lobby,
            notif_type='ranked_challenge_declined',
            content=f'{request.current_user.username} declined ranked challenge #{lobby.id}.',
            exclude_user_ids={request.current_user.id},
        )
    elif previous_status != 'ready' and lobby.status == 'ready':
        _notify_lobby_players(
            lobby,
            notif_type='ranked_challenge_ready',
            content='All players accepted. The ranked lobby is ready to start.',
        )

    db.session.commit()
    _emit_ranked_update(court_id=lobby.court_id, reason='lobby_response')
    if action == 'decline' or lobby.status == 'ready':
        _emit_notification_update(court_id=lobby.court_id, reason='lobby_response_notification')
    return jsonify({
        'lobby': _lobby_to_dict(lobby),
        'all_accepted': lobby.status == 'ready',
    })


@ranked_bp.route('/lobby/<int:lobby_id>/start', methods=['POST'])
@login_required
def start_lobby_match(lobby_id):
    """Start a ranked match from a ready lobby."""
    lobby = db.session.get(RankedLobby, lobby_id)
    if not lobby:
        return jsonify({'error': 'Lobby not found'}), 404

    if lobby.status == 'started' and lobby.started_match_id:
        existing_match = db.session.get(Match, lobby.started_match_id)
        if existing_match:
            return jsonify({'match': existing_match.to_dict(), 'lobby': _lobby_to_dict(lobby)})
    if lobby.status != 'ready':
        return jsonify({'error': 'Lobby is not ready to start'}), 400

    starter = RankedLobbyPlayer.query.filter_by(
        lobby_id=lobby_id,
        user_id=request.current_user.id,
    ).first()
    if not starter or starter.acceptance_status != 'accepted':
        return jsonify({'error': 'Only accepted participants can start this game'}), 403

    if lobby.scheduled_for and utcnow_naive() < lobby.scheduled_for:
        return jsonify({'error': 'This scheduled ranked game cannot start yet'}), 400

    accepted_players = [p for p in lobby.players if p.acceptance_status == 'accepted']
    team1_ids = [p.user_id for p in accepted_players if p.team == 1]
    team2_ids = [p.user_id for p in accepted_players if p.team == 2]
    setup_error = _validate_match_setup(lobby.match_type, team1_ids, team2_ids)
    if setup_error:
        return jsonify({'error': setup_error}), 400

    all_ids = set(team1_ids + team2_ids)
    checked_in_ids = _checked_in_user_ids(lobby.court_id, all_ids)
    if checked_in_ids != all_ids:
        missing_ids = sorted(all_ids - checked_in_ids)
        return jsonify({
            'error': 'All players must be checked in at this court to start the ranked game',
            'missing_player_ids': missing_ids,
        }), 400

    players_by_id = _resolve_players(all_ids)
    if not players_by_id:
        return jsonify({'error': 'One or more players not found'}), 404

    match = _create_match_record(
        lobby.court_id,
        lobby.match_type,
        team1_ids,
        team2_ids,
        players_by_id,
    )
    lobby.status = 'started'
    lobby.started_match_id = match.id

    RankedQueue.query.filter(
        RankedQueue.court_id == lobby.court_id,
        RankedQueue.user_id.in_(all_ids),
    ).delete(synchronize_session=False)

    db.session.commit()
    _emit_ranked_update(court_id=lobby.court_id, reason='lobby_started')
    return jsonify({'match': match.to_dict(), 'lobby': _lobby_to_dict(lobby)})


@ranked_bp.route('/challenges/pending', methods=['GET'])
@login_required
def get_pending_challenges():
    """Get challenge lobbies where the current user still needs to respond."""
    pending_rows = RankedLobbyPlayer.query.filter_by(
        user_id=request.current_user.id,
        acceptance_status='pending',
    ).all()
    lobbies = []
    for row in pending_rows:
        if row.lobby and row.lobby.status == 'pending_acceptance':
            lobbies.append(_lobby_to_dict(row.lobby))
    return jsonify({'lobbies': lobbies})


@ranked_bp.route('/court/<int:court_id>/lobbies', methods=['GET'])
def get_court_lobbies(court_id):
    """List active ranked lobbies (ready + pending) for a court."""
    all_lobbies = RankedLobby.query.filter(
        RankedLobby.court_id == court_id,
        RankedLobby.status.in_(['pending_acceptance', 'ready']),
    ).order_by(RankedLobby.scheduled_for.asc(), RankedLobby.created_at.desc()).all()

    ready_lobbies = []
    scheduled_lobbies = []
    pending_lobbies = []
    for lobby in all_lobbies:
        data = _lobby_to_dict(lobby)
        if lobby.status == 'ready' and lobby.scheduled_for:
            scheduled_lobbies.append(data)
        elif lobby.status == 'ready':
            ready_lobbies.append(data)
        else:
            pending_lobbies.append(data)

    return jsonify({
        'ready_lobbies': ready_lobbies,
        'scheduled_lobbies': scheduled_lobbies,
        'pending_lobbies': pending_lobbies,
    })


# ── Match Creation ────────────────────────────────────────────────────

@ranked_bp.route('/match', methods=['POST'])
@login_required
def create_match():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    match_type = str(data.get('match_type', 'doubles')).strip().lower()
    team1_ids = _parse_team_ids(data.get('team1', []))
    team2_ids = _parse_team_ids(data.get('team2', []))

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if team1_ids is None or team2_ids is None:
        return jsonify({'error': 'Both teams must be lists of numeric player IDs'}), 400

    setup_error = _validate_match_setup(match_type, team1_ids, team2_ids)
    if setup_error:
        return jsonify({'error': setup_error}), 400

    all_ids = set(team1_ids + team2_ids)
    players_by_id = _resolve_players(all_ids)
    if not players_by_id:
        return jsonify({'error': 'One or more players not found'}), 404

    match = _create_match_record(
        court_id,
        match_type,
        team1_ids,
        team2_ids,
        players_by_id,
    )

    RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(all_ids)
    ).delete(synchronize_session=False)

    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='match_created')
    return jsonify({'match': match.to_dict()}), 201


@ranked_bp.route('/match/<int:match_id>', methods=['GET'])
def get_match(match_id):
    match = db.session.get(Match, match_id)
    if not match:
        return jsonify({'error': 'Match not found'}), 404
    return jsonify({'match': match.to_dict()})


# ── Score Submission (now requires confirmation) ─────────────────────

@ranked_bp.route('/match/<int:match_id>/score', methods=['POST'])
@login_required
def submit_score(match_id):
    """Submit the score. Sets match to pending_confirmation.
    ELO is NOT applied until all players confirm.
    The submitter is auto-confirmed.
    """
    match = db.session.get(Match, match_id)
    if not match:
        return jsonify({'error': 'Match not found'}), 404
    if match.status not in ('in_progress',):
        return jsonify({'error': 'Match cannot be scored in its current state'}), 400

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    try:
        team1_score = int(data.get('team1_score'))
        team2_score = int(data.get('team2_score'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Both scores must be integers'}), 400
    if team1_score < _MIN_SCORE or team2_score < _MIN_SCORE:
        return jsonify({'error': 'Scores must be non-negative'}), 400
    if team1_score > _MAX_SCORE or team2_score > _MAX_SCORE:
        return jsonify({'error': f'Scores must be at most {_MAX_SCORE}'}), 400
    if team1_score == team2_score:
        return jsonify({'error': 'Scores cannot be tied — there must be a winner'}), 400

    submitter_mp = MatchPlayer.query.filter_by(
        match_id=match_id, user_id=request.current_user.id
    ).first()
    if not submitter_mp:
        return jsonify({'error': 'You are not a player in this match'}), 403

    match.team1_score = team1_score
    match.team2_score = team2_score
    match.winner_team = 1 if team1_score > team2_score else 2
    match.status = 'pending_confirmation'
    match.submitted_by = request.current_user.id

    # Auto-confirm for the submitter
    submitter_mp.confirmed = True

    # Notify all OTHER players to confirm
    for mp in match.players:
        if mp.user_id != request.current_user.id:
            notif = Notification(
                user_id=mp.user_id, notif_type='match_confirm',
                content=(
                    f'{request.current_user.username} submitted score: '
                    f'{team1_score}-{team2_score}. Please confirm the result.'
                ),
                reference_id=match.id,
            )
            db.session.add(notif)

    db.session.commit()
    _emit_ranked_update(court_id=match.court_id, reason='score_submitted')
    _emit_notification_update(court_id=match.court_id, reason='score_confirmation_requested')

    # Check if somehow all confirmed (e.g., submitter is the only player)
    if all(p.confirmed for p in match.players):
        _apply_elo(match)
        match.status = 'completed'
        match.completed_at = utcnow_naive()
        db.session.commit()
        _emit_ranked_update(court_id=match.court_id, reason='match_completed_auto_confirm')
        _emit_notification_update(court_id=match.court_id, reason='match_completed_auto_confirm')

    return jsonify({
        'match': match.to_dict(),
        'pending_confirmation': match.status == 'pending_confirmation',
    })


@ranked_bp.route('/match/<int:match_id>/confirm', methods=['POST'])
@login_required
def confirm_match(match_id):
    """Player confirms/accepts the submitted match result."""
    match = db.session.get(Match, match_id)
    if not match:
        return jsonify({'error': 'Match not found'}), 404
    if match.status != 'pending_confirmation':
        return jsonify({'error': 'Match is not pending confirmation'}), 400

    mp = MatchPlayer.query.filter_by(
        match_id=match_id, user_id=request.current_user.id
    ).first()
    if not mp:
        return jsonify({'error': 'You are not a player in this match'}), 403

    mp.confirmed = True
    db.session.flush()

    all_confirmed = all(p.confirmed for p in match.players)
    if all_confirmed:
        _apply_elo(match)
        match.status = 'completed'
        match.completed_at = utcnow_naive()

        # Notify all players of final results
        for p in match.players:
            won = (p.team == match.winner_team)
            sign = '+' if (p.elo_change or 0) >= 0 else ''
            notif = Notification(
                user_id=p.user_id, notif_type='match_result',
                content=(
                    f'{"🏆 Win" if won else "❌ Loss"} — '
                    f'{match.team1_score}-{match.team2_score} | '
                    f'ELO {sign}{p.elo_change:.0f} → {p.elo_after:.0f}'
                ),
                reference_id=match.id,
            )
            db.session.add(notif)

    db.session.commit()
    _emit_ranked_update(court_id=match.court_id, reason='match_confirmation_updated')
    if all_confirmed:
        _emit_notification_update(court_id=match.court_id, reason='match_completed')
    return jsonify({
        'match': match.to_dict(),
        'all_confirmed': all_confirmed,
    })


@ranked_bp.route('/match/<int:match_id>/reject', methods=['POST'])
@login_required
def reject_match(match_id):
    """Player rejects the submitted score. Resets the match to in_progress."""
    match = db.session.get(Match, match_id)
    if not match:
        return jsonify({'error': 'Match not found'}), 404
    if match.status != 'pending_confirmation':
        return jsonify({'error': 'Match is not pending confirmation'}), 400

    mp = MatchPlayer.query.filter_by(
        match_id=match_id, user_id=request.current_user.id
    ).first()
    if not mp:
        return jsonify({'error': 'You are not a player in this match'}), 403

    # Reset match score and confirmation state
    match.team1_score = None
    match.team2_score = None
    match.winner_team = None
    match.status = 'in_progress'
    match.submitted_by = None

    for p in match.players:
        p.confirmed = False

    # Notify the submitter
    for p in match.players:
        if p.user_id != request.current_user.id:
            notif = Notification(
                user_id=p.user_id, notif_type='match_rejected',
                content=(
                    f'{request.current_user.username} rejected the match score. '
                    f'Please re-enter the score.'
                ),
                reference_id=match.id,
            )
            db.session.add(notif)

    db.session.commit()
    _emit_ranked_update(court_id=match.court_id, reason='score_rejected')
    _emit_notification_update(court_id=match.court_id, reason='score_rejected')
    return jsonify({'match': match.to_dict(), 'message': 'Score rejected. Re-submit when ready.'})


@ranked_bp.route('/pending', methods=['GET'])
@login_required
def get_pending_confirmations():
    """Get matches pending confirmation for the current user."""
    pending_mp = MatchPlayer.query.filter_by(
        user_id=request.current_user.id, confirmed=False
    ).all()

    matches = []
    for mp in pending_mp:
        if mp.match.status == 'pending_confirmation':
            matches.append(mp.match.to_dict())

    return jsonify({'matches': matches})


# ── ELO Application (called only after all players confirm) ──────────

def _apply_elo(match):
    """Calculate and apply ELO changes to all players in the match."""
    team1_mps = [mp for mp in match.players if mp.team == 1]
    team2_mps = [mp for mp in match.players if mp.team == 2]

    team1_data = [{
        'elo_rating': mp.elo_before or mp.user.elo_rating,
        'games_played': mp.user.games_played,
    } for mp in team1_mps]

    team2_data = [{
        'elo_rating': mp.elo_before or mp.user.elo_rating,
        'games_played': mp.user.games_played,
    } for mp in team2_mps]

    team1_changes, team2_changes = calculate_elo_changes(
        team1_data, team2_data,
        match.team1_score, match.team2_score,
    )

    for mp, change in zip(team1_mps, team1_changes):
        mp.elo_after = (mp.elo_before or mp.user.elo_rating) + change
        mp.elo_change = change
        mp.user.elo_rating = mp.elo_after
        mp.user.games_played += 1
        if match.winner_team == 1:
            mp.user.wins += 1
        else:
            mp.user.losses += 1

    for mp, change in zip(team2_mps, team2_changes):
        mp.elo_after = (mp.elo_before or mp.user.elo_rating) + change
        mp.elo_change = change
        mp.user.elo_rating = mp.elo_after
        mp.user.games_played += 1
        if match.winner_team == 2:
            mp.user.wins += 1
        else:
            mp.user.losses += 1


# ── Leaderboard ───────────────────────────────────────────────────────

@ranked_bp.route('/leaderboard', methods=['GET'])
def get_leaderboard():
    court_id = request.args.get('court_id', type=int)
    limit = request.args.get('limit', 50, type=int)

    if court_id:
        player_ids = db.session.query(MatchPlayer.user_id).join(Match).filter(
            Match.court_id == court_id, Match.status == 'completed'
        ).distinct().subquery()
        players = User.query.filter(
            User.id.in_(db.session.query(player_ids))
        ).filter(User.games_played > 0)\
            .order_by(User.elo_rating.desc()).limit(limit).all()
    else:
        players = User.query.filter(User.games_played > 0)\
            .order_by(User.elo_rating.desc()).limit(limit).all()

    leaderboard = []
    for rank, player in enumerate(players, 1):
        win_rate = round((player.wins / player.games_played) * 100) \
            if player.games_played > 0 else 0
        leaderboard.append({
            'rank': rank, 'user_id': player.id,
            'name': player.name or player.username,
            'username': player.username,
            'elo_rating': round(player.elo_rating),
            'wins': player.wins, 'losses': player.losses,
            'games_played': player.games_played, 'win_rate': win_rate,
        })
    return jsonify({'leaderboard': leaderboard, 'court_id': court_id})


# ── Match History ─────────────────────────────────────────────────────

@ranked_bp.route('/history', methods=['GET'])
def get_match_history():
    user_id = request.args.get('user_id', type=int)
    court_id = request.args.get('court_id', type=int)
    limit = request.args.get('limit', 20, type=int)

    query = Match.query.filter_by(status='completed')
    if court_id:
        query = query.filter_by(court_id=court_id)
    if user_id:
        query = query.filter(
            Match.id.in_(
                db.session.query(MatchPlayer.match_id).filter_by(user_id=user_id)
            )
        )
    matches = query.order_by(Match.completed_at.desc()).limit(limit).all()
    return jsonify({'matches': [m.to_dict() for m in matches]})


# ── Active / Pending Matches at a Court ──────────────────────────────

@ranked_bp.route('/active/<int:court_id>', methods=['GET'])
def get_active_matches(court_id):
    """Get in-progress and pending-confirmation matches at a court."""
    matches = Match.query.filter(
        Match.court_id == court_id,
        Match.status.in_(['in_progress', 'pending_confirmation'])
    ).all()
    return jsonify({'matches': [m.to_dict() for m in matches]})
