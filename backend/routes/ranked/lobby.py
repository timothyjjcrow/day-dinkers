"""Ranked lobby creation and management routes."""
from flask import request, jsonify
from backend.app import db
from backend.models import (
    Court, Match, RankedQueue, RankedLobbyPlayer,
)
from backend.auth_utils import login_required
from backend.routes.ranked import ranked_bp
from backend.routes.ranked.helpers import (
    _coerce_bool, _parse_team_ids, _validate_match_setup,
    _resolve_players, _create_match_record, _checked_in_user_ids,
    _create_lobby, _lobby_to_dict, _update_lobby_status,
    _notify_lobby_players, _prune_queue_for_court, _expire_stale_items,
    _emit_ranked_update, _emit_notification_update,
)
from backend.time_utils import utcnow_naive


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
    start_immediately = _coerce_bool(data.get('start_immediately'))

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

    _prune_queue_for_court(court_id)
    _expire_stale_items(court_id)
    queue_entries = RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(all_ids),
    ).all()
    if len(queue_entries) != len(all_ids):
        return jsonify({'error': 'All selected players must be in the ranked queue at this court'}), 400
    for entry in queue_entries:
        if entry.match_type != match_type:
            return jsonify({'error': 'All selected queue players must share the same match type'}), 400
    if start_immediately:
        checked_in_ids = _checked_in_user_ids(court_id, all_ids)
        if checked_in_ids != all_ids:
            missing_ids = sorted(all_ids - checked_in_ids)
            return jsonify({
                'error': 'All selected players must still be checked in to auto-start this game',
                'missing_player_ids': missing_ids,
            }), 400

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
    match = None
    if start_immediately:
        match = _create_match_record(
            court_id, match_type, team1_ids, team2_ids, players_by_id,
        )
        lobby.status = 'started'
        lobby.started_match_id = match.id
    db.session.commit()
    _emit_ranked_update(
        court_id=court_id,
        reason='lobby_created_and_started_from_queue' if match else 'lobby_created_from_queue',
    )
    response = {'lobby': _lobby_to_dict(lobby)}
    if match:
        response['match'] = match.to_dict()
    return jsonify(response), 201


@ranked_bp.route('/lobby/<int:lobby_id>', methods=['GET'])
def get_lobby(lobby_id):
    from backend.models import RankedLobby
    lobby = db.session.get(RankedLobby, lobby_id)
    if not lobby:
        return jsonify({'error': 'Lobby not found'}), 404
    return jsonify({'lobby': _lobby_to_dict(lobby)})


@ranked_bp.route('/lobby/<int:lobby_id>/respond', methods=['POST'])
@login_required
def respond_to_lobby(lobby_id):
    """Accept or decline a ranked challenge invite."""
    from backend.models import RankedLobby
    lobby = db.session.get(RankedLobby, lobby_id)
    if not lobby:
        return jsonify({'error': 'Lobby not found'}), 404
    if lobby.status != 'pending_acceptance':
        return jsonify({'error': 'Lobby is no longer awaiting responses'}), 400

    participant = RankedLobbyPlayer.query.filter_by(
        lobby_id=lobby_id, user_id=request.current_user.id,
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
    from backend.models import RankedLobby
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
        lobby_id=lobby_id, user_id=request.current_user.id,
    ).first()
    if not starter or starter.acceptance_status != 'accepted':
        return jsonify({'error': 'Only accepted participants can start this game'}), 403

    now = utcnow_naive()
    if lobby.scheduled_for and now < lobby.scheduled_for:
        seconds_until_start = max(1, int((lobby.scheduled_for - now).total_seconds()))
        return jsonify({
            'error': 'This scheduled ranked game cannot start yet',
            'scheduled_for': lobby.scheduled_for.isoformat(),
            'seconds_until_start': seconds_until_start,
        }), 400

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
        lobby.court_id, lobby.match_type, team1_ids, team2_ids, players_by_id,
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
