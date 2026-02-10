from datetime import datetime
from flask import Blueprint, request, jsonify
from backend.app import db
from backend.models import Game, GamePlayer, Court, Notification
from backend.auth_utils import login_required
from backend.time_utils import utcnow_naive

games_bp = Blueprint('games', __name__)


@games_bp.route('', methods=['GET'])
def get_games():
    """List upcoming games with optional filters."""
    court_id = request.args.get('court_id', type=int)
    skill = request.args.get('skill_level', '')
    open_only = request.args.get('open_only', 'false')

    query = Game.query.filter(Game.date_time >= utcnow_naive())
    if court_id:
        query = query.filter_by(court_id=court_id)
    if skill and skill != 'all':
        query = query.filter_by(skill_level=skill)
    if open_only == 'true':
        query = query.filter_by(is_open=True)

    games = query.order_by(Game.date_time.asc()).limit(50).all()
    results = []
    for game in games:
        game_dict = game.to_dict()
        players = GamePlayer.query.filter_by(game_id=game.id, rsvp_status='yes').all()
        game_dict['player_count'] = len(players)
        game_dict['players'] = [p.user.to_dict() for p in players]
        results.append(game_dict)
    return jsonify({'games': results})


@games_bp.route('/my', methods=['GET'])
@login_required
def get_my_games():
    """Get upcoming games the current user has RSVPd to (yes or maybe)."""
    my_game_ids = [gp.game_id for gp in GamePlayer.query.filter(
        GamePlayer.user_id == request.current_user.id,
        GamePlayer.rsvp_status.in_(['yes', 'maybe'])
    ).all()]

    games = Game.query.filter(
        Game.id.in_(my_game_ids),
        Game.date_time >= utcnow_naive()
    ).order_by(Game.date_time.asc()).limit(10).all()

    results = []
    for game in games:
        game_dict = game.to_dict()
        player_count = GamePlayer.query.filter_by(
            game_id=game.id, rsvp_status='yes'
        ).count()
        game_dict['player_count'] = player_count
        results.append(game_dict)
    return jsonify({'games': results})


@games_bp.route('/<int:game_id>', methods=['GET'])
def get_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404

    game_dict = game.to_dict()
    players = GamePlayer.query.filter_by(game_id=game.id).all()
    game_dict['players'] = [{
        'user': p.user.to_dict(), 'rsvp_status': p.rsvp_status
    } for p in players]
    game_dict['player_count'] = sum(1 for p in players if p.rsvp_status == 'yes')
    return jsonify({'game': game_dict})


@games_bp.route('', methods=['POST'])
@login_required
def create_game():
    data = request.get_json()
    required = ['court_id', 'title', 'date_time']
    if not all(data.get(f) for f in required):
        return jsonify({'error': 'Court, title, and date/time are required'}), 400

    court = db.session.get(Court, data['court_id'])
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    game = Game(
        court_id=data['court_id'], creator_id=request.current_user.id,
        title=data['title'], description=data.get('description', ''),
        date_time=datetime.fromisoformat(data['date_time']),
        max_players=data.get('max_players', 4),
        skill_level=data.get('skill_level', 'all'),
        game_type=data.get('game_type', 'open'),
        is_open=data.get('is_open', True),
        recurring=data.get('recurring', ''),
    )
    db.session.add(game)
    db.session.flush()

    # Creator auto-RSVPs as 'yes'
    player = GamePlayer(game_id=game.id, user_id=request.current_user.id, rsvp_status='yes')
    db.session.add(player)
    db.session.commit()
    return jsonify({'game': game.to_dict()}), 201


@games_bp.route('/<int:game_id>/rsvp', methods=['POST'])
@login_required
def rsvp_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404

    data = request.get_json()
    status = data.get('status', 'yes')  # yes, no, maybe

    existing = GamePlayer.query.filter_by(
        game_id=game_id, user_id=request.current_user.id
    ).first()

    if status == 'yes':
        current_count = GamePlayer.query.filter_by(
            game_id=game_id, rsvp_status='yes'
        ).count()
        if current_count >= game.max_players and not existing:
            return jsonify({'error': 'Game is full'}), 400

    if existing:
        existing.rsvp_status = status
    else:
        player = GamePlayer(
            game_id=game_id, user_id=request.current_user.id, rsvp_status=status
        )
        db.session.add(player)

    # Notify the game creator
    if request.current_user.id != game.creator_id:
        notif = Notification(
            user_id=game.creator_id, notif_type='game_rsvp',
            content=f'{request.current_user.username} RSVPed {status} to {game.title}',
            reference_id=game_id,
        )
        db.session.add(notif)

    db.session.commit()
    return jsonify({'message': f'RSVP updated to {status}'})


@games_bp.route('/<int:game_id>/invite', methods=['POST'])
@login_required
def invite_to_game(game_id):
    """Invite friends to a game."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404

    data = request.get_json()
    friend_ids = data.get('friend_ids', [])
    if not friend_ids:
        return jsonify({'error': 'No friends selected'}), 400

    invited = []
    for fid in friend_ids:
        existing = GamePlayer.query.filter_by(game_id=game_id, user_id=fid).first()
        if existing:
            continue
        player = GamePlayer(game_id=game_id, user_id=fid, rsvp_status='invited')
        db.session.add(player)
        notif = Notification(
            user_id=fid, notif_type='game_invite',
            content=f'{request.current_user.username} invited you to "{game.title}"',
            reference_id=game_id,
        )
        db.session.add(notif)
        invited.append(fid)

    db.session.commit()
    return jsonify({'message': f'Invited {len(invited)} friend(s)', 'invited_count': len(invited)})


@games_bp.route('/<int:game_id>', methods=['DELETE'])
@login_required
def delete_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404
    if game.creator_id != request.current_user.id:
        return jsonify({'error': 'Only the creator can delete this game'}), 403

    db.session.delete(game)
    db.session.commit()
    return jsonify({'message': 'Game deleted'})
