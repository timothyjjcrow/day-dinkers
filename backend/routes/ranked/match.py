"""Ranked match creation and score confirmation routes."""
from flask import request, jsonify
from backend.app import db
from backend.models import Court, Match, MatchPlayer, RankedQueue, Notification
from backend.auth_utils import login_required
from backend.routes.ranked import ranked_bp
from backend.routes.ranked.helpers import (
    _MIN_SCORE, _MAX_SCORE,
    _parse_team_ids, _validate_match_setup, _resolve_players,
    _create_match_record, _apply_elo,
    _emit_ranked_update, _emit_notification_update,
)
from backend.routes.ranked.tournaments_helpers import (
    advance_tournament_after_completed_match,
    should_apply_elo_for_match,
)
from backend.time_utils import utcnow_naive


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
        court_id, match_type, team1_ids, team2_ids, players_by_id,
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

    submitter_mp.confirmed = True

    for mp in match.players:
        if mp.user_id != request.current_user.id:
            db.session.add(Notification(
                user_id=mp.user_id, notif_type='match_confirm',
                content=(
                    f'{request.current_user.username} submitted score: '
                    f'{team1_score}-{team2_score}. Please confirm the result.'
                ),
                reference_id=match.id,
            ))

    db.session.commit()
    _emit_ranked_update(court_id=match.court_id, reason='score_submitted')
    _emit_notification_update(court_id=match.court_id, reason='score_confirmation_requested')

    if all(p.confirmed for p in match.players):
        if should_apply_elo_for_match(match):
            _apply_elo(match)
        else:
            for participant in match.players:
                baseline = participant.elo_before or participant.user.elo_rating
                participant.elo_after = baseline
                participant.elo_change = 0
        match.status = 'completed'
        match.completed_at = utcnow_naive()
        advance_tournament_after_completed_match(match)
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
        if should_apply_elo_for_match(match):
            _apply_elo(match)
        else:
            for participant in match.players:
                baseline = participant.elo_before or participant.user.elo_rating
                participant.elo_after = baseline
                participant.elo_change = 0
        match.status = 'completed'
        match.completed_at = utcnow_naive()
        advance_tournament_after_completed_match(match)

        for p in match.players:
            won = (p.team == match.winner_team)
            sign = '+' if (p.elo_change or 0) >= 0 else ''
            db.session.add(Notification(
                user_id=p.user_id, notif_type='match_result',
                content=(
                    f'{"🏆 Win" if won else "❌ Loss"} — '
                    f'{match.team1_score}-{match.team2_score} | '
                    f'ELO {sign}{p.elo_change:.0f} → {p.elo_after:.0f}'
                ),
                reference_id=match.id,
            ))

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

    submitter_user_id = match.submitted_by

    match.team1_score = None
    match.team2_score = None
    match.winner_team = None
    match.status = 'in_progress'
    match.submitted_by = None

    for p in match.players:
        p.confirmed = False

    if submitter_user_id and submitter_user_id != request.current_user.id:
        db.session.add(Notification(
            user_id=submitter_user_id, notif_type='match_rejected',
            content=(
                f'{request.current_user.username} rejected the match score. '
                f'Please re-enter the score.'
            ),
            reference_id=match.id,
        ))

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
    for mp_entry in pending_mp:
        if mp_entry.match.status == 'pending_confirmation':
            matches.append(mp_entry.match.to_dict())

    matches.sort(key=lambda m: m.get('created_at') or '', reverse=True)
    return jsonify({'matches': matches})


@ranked_bp.route('/match/<int:match_id>/cancel', methods=['POST'])
@login_required
def cancel_match(match_id):
    """Cancel an active match. Any player in the match can cancel."""
    match = db.session.get(Match, match_id)
    if not match:
        return jsonify({'error': 'Match not found'}), 404
    if match.status not in ('in_progress', 'pending_confirmation'):
        return jsonify({'error': 'Only active matches can be cancelled'}), 400

    mp = MatchPlayer.query.filter_by(
        match_id=match_id, user_id=request.current_user.id
    ).first()
    if not mp:
        return jsonify({'error': 'You are not a player in this match'}), 403

    match.status = 'cancelled'

    for p in match.players:
        if p.user_id != request.current_user.id:
            db.session.add(Notification(
                user_id=p.user_id, notif_type='match_cancelled',
                content=f'{request.current_user.username} cancelled the ranked match.',
                reference_id=match.id,
            ))

    db.session.commit()
    _emit_ranked_update(court_id=match.court_id, reason='match_cancelled')
    _emit_notification_update(court_id=match.court_id, reason='match_cancelled')
    return jsonify({'match': match.to_dict(), 'message': 'Match cancelled.'})
