"""Ranked challenge routes — court and scheduled challenges."""
from flask import request, jsonify
from backend.app import db
from backend.models import Court
from backend.auth_utils import login_required
from backend.routes.ranked import ranked_bp
from backend.routes.ranked.helpers import (
    _ALLOWED_LOBBY_SOURCES,
    _parse_team_ids, _validate_match_setup, _resolve_players,
    _user_checked_in_at_court, _checked_in_user_ids,
    _create_lobby, _lobby_to_dict, _notify_lobby_players,
    _parse_iso_datetime, _expire_stale_items,
    _emit_ranked_update, _emit_notification_update,
)
from backend.time_utils import utcnow_naive


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

    _expire_stale_items(court_id)

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

    _expire_stale_items(court_id)

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
