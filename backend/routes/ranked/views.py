"""Ranked read-only views — leaderboard, history, active matches, court summary."""
from flask import request, jsonify
from backend.app import db
from backend.models import (
    User, Court, Match, MatchPlayer, RankedQueue, CheckIn,
    RankedLobbyPlayer,
)
from backend.auth_utils import login_required
from backend.routes.ranked import ranked_bp
from backend.routes.ranked.helpers import _lobby_to_dict, _categorize_court_lobbies


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


@ranked_bp.route('/active/<int:court_id>', methods=['GET'])
def get_active_matches(court_id):
    """Get in-progress and pending-confirmation matches at a court."""
    matches = Match.query.filter(
        Match.court_id == court_id,
        Match.status.in_(['in_progress', 'pending_confirmation'])
    ).all()
    return jsonify({'matches': [m.to_dict() for m in matches]})


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
    ready, scheduled, pending = _categorize_court_lobbies(court_id)
    return jsonify({
        'ready_lobbies': ready,
        'scheduled_lobbies': scheduled,
        'pending_lobbies': pending,
    })


@ranked_bp.route('/court/<int:court_id>/summary', methods=['GET'])
def get_court_summary(court_id):
    """Single endpoint returning all ranked data for a court view.

    Consolidates queue, active matches, lobbies, and mini leaderboard
    into one response — replacing 5 separate API calls with 1.
    """
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    # Queue — read-only filter to checked-in players
    active_user_ids = db.session.query(CheckIn.user_id).filter(
        CheckIn.court_id == court_id,
        CheckIn.checked_out_at.is_(None),
    )
    queue_entries = RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(active_user_ids),
    ).order_by(RankedQueue.joined_at.asc()).all()

    # Active matches (in_progress + pending_confirmation)
    active_matches = Match.query.filter(
        Match.court_id == court_id,
        Match.status.in_(['in_progress', 'pending_confirmation']),
    ).all()

    # Lobbies
    ready, scheduled, pending = _categorize_court_lobbies(court_id)

    # Mini leaderboard
    lb_limit = request.args.get('leaderboard_limit', 10, type=int)
    player_ids = db.session.query(MatchPlayer.user_id).join(Match).filter(
        Match.court_id == court_id, Match.status == 'completed',
    ).distinct().subquery()
    players = User.query.filter(
        User.id.in_(db.session.query(player_ids)),
    ).filter(User.games_played > 0).order_by(
        User.elo_rating.desc(),
    ).limit(lb_limit).all()

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

    return jsonify({
        'queue': [e.to_dict() for e in queue_entries],
        'matches': [m.to_dict() for m in active_matches],
        'ready_lobbies': ready,
        'scheduled_lobbies': scheduled,
        'pending_lobbies': pending,
        'leaderboard': leaderboard,
    })
