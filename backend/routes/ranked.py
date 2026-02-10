"""Ranked competitive play — queue, matchmaking, scoring with confirmation, leaderboards."""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from backend.app import db
from backend.models import (
    User, Court, Match, MatchPlayer, RankedQueue, CheckIn, Notification
)
from backend.auth_utils import login_required
from backend.services.elo import calculate_elo_changes

ranked_bp = Blueprint('ranked', __name__)
_ALLOWED_MATCH_TYPES = {'singles', 'doubles'}
_MIN_SCORE = 0
_MAX_SCORE = 99


# ── Queue Management ──────────────────────────────────────────────────

@ranked_bp.route('/queue/<int:court_id>', methods=['GET'])
def get_queue(court_id):
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

    existing = RankedQueue.query.filter_by(
        user_id=request.current_user.id, court_id=court_id
    ).first()
    if existing:
        return jsonify({'error': 'Already in queue at this court'}), 409

    entry = RankedQueue(
        user_id=request.current_user.id,
        court_id=court_id,
        match_type=match_type,
    )
    db.session.add(entry)
    db.session.commit()
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
    return jsonify({'message': 'Left queue'})


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
    if match_type not in _ALLOWED_MATCH_TYPES:
        return jsonify({'error': 'Invalid match type'}), 400

    if not isinstance(data.get('team1', []), list) or not isinstance(data.get('team2', []), list):
        return jsonify({'error': 'Both teams must be lists of player IDs'}), 400

    try:
        team1_ids = [int(uid) for uid in data.get('team1', [])]
        team2_ids = [int(uid) for uid in data.get('team2', [])]
    except (TypeError, ValueError):
        return jsonify({'error': 'Team player IDs must be numeric'}), 400

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if not team1_ids or not team2_ids:
        return jsonify({'error': 'Both teams must have players'}), 400
    if any(uid <= 0 for uid in team1_ids + team2_ids):
        return jsonify({'error': 'Player IDs must be positive'}), 400

    expected_per_team = 1 if match_type == 'singles' else 2
    if len(team1_ids) != expected_per_team or len(team2_ids) != expected_per_team:
        return jsonify({
            'error': f'{match_type.capitalize()} requires {expected_per_team} per team'
        }), 400

    all_ids = set(team1_ids + team2_ids)
    if len(all_ids) != len(team1_ids) + len(team2_ids):
        return jsonify({'error': 'Duplicate players across teams'}), 400
    players = User.query.filter(User.id.in_(all_ids)).all()
    if len(players) != len(all_ids):
        return jsonify({'error': 'One or more players not found'}), 404

    match = Match(
        court_id=court_id, match_type=match_type, status='in_progress',
    )
    db.session.add(match)
    db.session.flush()

    for uid in team1_ids:
        user = next(p for p in players if p.id == uid)
        mp = MatchPlayer(
            match_id=match.id, user_id=uid, team=1,
            elo_before=user.elo_rating,
        )
        db.session.add(mp)
    for uid in team2_ids:
        user = next(p for p in players if p.id == uid)
        mp = MatchPlayer(
            match_id=match.id, user_id=uid, team=2,
            elo_before=user.elo_rating,
        )
        db.session.add(mp)

    RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(all_ids)
    ).delete(synchronize_session=False)

    db.session.commit()
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

    # Check if somehow all confirmed (e.g., submitter is the only player)
    if all(p.confirmed for p in match.players):
        _apply_elo(match)
        match.status = 'completed'
        match.completed_at = datetime.now(timezone.utc)
        db.session.commit()

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
        match.completed_at = datetime.now(timezone.utc)

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
