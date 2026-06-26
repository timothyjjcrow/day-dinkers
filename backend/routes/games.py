"""Game scheduling, joining, and ranked match results."""
import math
from datetime import UTC, datetime, timedelta

from flask import Blueprint, g, jsonify, request

from backend.app import db
from backend.models import (
    Court,
    GAME_RECURRENCES,
    GAME_TYPES,
    GAME_VISIBILITIES,
    Game,
    GameInvite,
    GamePlayer,
    User,
    notify,
    utcnow,
)
from backend.routes.auth import login_required, optional_current_user
from backend.routes.courts import haversine_miles
from backend.routes.social import friend_ids

games_bp = Blueprint('games', __name__)

ELO_K = 32
SCORE_AUTO_CONFIRM_HOURS = 24


def _parse_scheduled_at(raw):
    text = str(raw or '').strip().replace('Z', '+00:00')
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def auto_confirm_stale_scores():
    """Finalize ranked scores that opponents never confirmed within the window."""
    cutoff = utcnow() - timedelta(hours=SCORE_AUTO_CONFIRM_HOURS)
    stale = Game.query.filter(
        Game.status == 'awaiting_confirmation',
        Game.score_submitted_at < cutoff,
    ).all()
    for game in stale:
        _finalize_game(game)
    if stale:
        db.session.commit()


def roll_forward_recurring():
    """Advance weekly open-play sessions to their next occurrence once the last
    one is ~3h past, resetting the RSVP list to just the host (re-RSVP weekly)."""
    cutoff = utcnow() - timedelta(hours=3)
    due = Game.query.filter(
        Game.recurrence == 'weekly',
        Game.status == 'upcoming',
        Game.scheduled_at < cutoff,
    ).all()
    changed = False
    now = utcnow()
    for game in due:
        nxt = game.scheduled_at
        while nxt < now:
            nxt += timedelta(days=7)
        game.scheduled_at = nxt
        # Reset attendees to the host for the new week
        for player in list(game.players):
            if player.user_id != game.creator_id:
                game.players.remove(player)
        changed = True
    if changed:
        db.session.commit()


@games_bp.get('/games')
def list_games():
    """Upcoming games feed, optionally sorted by distance from lat/lng."""
    auto_confirm_stale_scores()
    roll_forward_recurring()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    mine = str(request.args.get('mine') or '').strip() in {'1', 'true', 'yes'}
    current_user = optional_current_user()

    if mine:
        if not current_user:
            return jsonify({'error': 'authentication_required'}), 401
        # My games: everything still in play, including scores awaiting confirmation.
        query = Game.query.filter(
            Game.status.in_(['upcoming', 'awaiting_confirmation']),
        ).join(GamePlayer).filter(GamePlayer.user_id == current_user.id)
    else:
        query = Game.query.filter(
            Game.scheduled_at >= utcnow() - timedelta(hours=2),
            Game.status == 'upcoming',
        )
    if not mine and lat is not None and lng is not None:
        radius = min(max(request.args.get('radius', default=50.0, type=float), 1.0), 200.0)
        lat_delta = radius / 69.0
        lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
        query = query.join(Court).filter(
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )

    games = query.order_by(Game.scheduled_at.asc()).limit(150).all()
    viewer_id = current_user.id if current_user else None
    viewer_friends = friend_ids(viewer_id) if viewer_id else set()

    items = []
    for game in games:
        # In the public/nearby feed, only show games the viewer is allowed to see.
        if not mine and not game.visible_to(viewer_id, viewer_friends):
            continue
        item = game.to_dict(viewer_id)
        court = game.court
        if lat is not None and lng is not None and court and court.latitude is not None:
            item['distance_miles'] = round(
                haversine_miles(lat, lng, court.latitude, court.longitude), 1,
            )
        items.append(item)
    if lat is not None and lng is not None and not mine:
        items.sort(key=lambda i: (i.get('distance_miles', 1e9), i['scheduled_at']))
    return jsonify({'items': items[:100]})


@games_bp.get('/games/history')
@login_required
def my_game_history():
    games = (
        Game.query.join(GamePlayer)
        .filter(GamePlayer.user_id == g.current_user.id, Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(50)
        .all()
    )
    return jsonify({'items': [game.to_dict(g.current_user.id) for game in games]})


@games_bp.post('/games')
@login_required
def create_game():
    payload = request.get_json(silent=True) or {}
    court = db.session.get(Court, int(payload.get('court_id') or 0))
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    scheduled_at = _parse_scheduled_at(payload.get('scheduled_at'))
    if not scheduled_at:
        return jsonify({'error': 'invalid_scheduled_at'}), 400
    if scheduled_at < utcnow() - timedelta(minutes=15):
        return jsonify({'error': 'scheduled_in_past'}), 400

    game_type = str(payload.get('game_type') or 'casual').strip().lower()
    if game_type not in GAME_TYPES:
        return jsonify({'error': 'invalid_game_type'}), 400

    try:
        max_players = int(payload.get('max_players') or 4)
    except (TypeError, ValueError):
        max_players = 4
    max_players = min(max(max_players, 2), 12)
    if game_type == 'ranked':
        max_players = 4 if max_players > 2 else 2

    # Collect any specifically-invited players (valid, real, not self)
    invited_ids = []
    for raw_id in (payload.get('invite_user_ids') or [])[:20]:
        try:
            invitee_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if invitee_id == g.current_user.id or invitee_id in invited_ids:
            continue
        if not db.session.get(User, invitee_id):
            continue
        invited_ids.append(invitee_id)

    # Visibility: open (anyone nearby) / friends (all friends) / private (invited only)
    visibility = str(payload.get('visibility') or '').strip().lower()
    if visibility not in GAME_VISIBILITIES:
        visibility = 'private' if invited_ids else 'open'
    if visibility == 'private' and not invited_ids:
        return jsonify({'error': 'no_invitees'}), 400

    # Recurrence: weekly open-play sessions (casual only — they don't score).
    recurrence = str(payload.get('recurrence') or 'none').strip().lower()
    if recurrence not in GAME_RECURRENCES:
        recurrence = 'none'
    if game_type == 'ranked':
        recurrence = 'none'

    game = Game(
        court_id=court.id,
        creator_id=g.current_user.id,
        scheduled_at=scheduled_at,
        game_type=game_type,
        visibility=visibility,
        recurrence=recurrence,
        max_players=max_players,
        notes=str(payload.get('notes') or '').strip()[:500],
    )
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=g.current_user.id))

    label = 'ranked game' if game_type == 'ranked' else 'game'

    if visibility == 'private':
        for uid in invited_ids:
            db.session.add(GameInvite(game_id=game.id, user_id=uid))
            notify(
                uid,
                'game_invite_direct',
                f'{g.current_user.display_name} invited you to a {label} at {court.name}',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    elif visibility == 'friends':
        for friend_id in friend_ids(g.current_user.id):
            notify(
                friend_id,
                'game_invite',
                f'{g.current_user.display_name} scheduled a {label} at {court.name}',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    # open: publicly discoverable in the nearby feed, no targeted notifications

    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id)), 201


@games_bp.get('/games/<int:game_id>')
def game_detail(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    current_user = optional_current_user()
    return jsonify(game.to_dict(current_user.id if current_user else None))


@games_bp.post('/games/<int:game_id>/join')
@login_required
def join_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if any(p.user_id == g.current_user.id for p in game.players):
        return jsonify(game.to_dict(g.current_user.id))
    if len(game.players) >= game.max_players:
        return jsonify({'error': 'game_full'}), 400
    # Respect visibility: you can only join games you'd be allowed to see.
    if not game.visible_to(g.current_user.id, friend_ids(g.current_user.id)):
        return jsonify({'error': 'not_invited'}), 403

    db.session.add(GamePlayer(game=game, user_id=g.current_user.id))
    if game.creator_id != g.current_user.id:
        notify(
            game.creator_id,
            'game_join',
            f'{g.current_user.display_name} joined your game',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
        )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/leave')
@login_required
def leave_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    player = next((p for p in game.players if p.user_id == g.current_user.id), None)
    if not player:
        return jsonify({'error': 'not_joined'}), 400
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400

    game.players.remove(player)
    if game.creator_id == g.current_user.id:
        remaining = [p for p in game.players if p.user_id != g.current_user.id]
        if remaining:
            game.creator_id = remaining[0].user_id
        else:
            game.status = 'cancelled'
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/cancel')
@login_required
def cancel_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.creator_id != g.current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    game.status = 'cancelled'
    for player in game.players:
        if player.user_id != g.current_user.id:
            notify(
                player.user_id,
                'game_cancelled',
                f'Game at {game.court.name if game.court else "court"} was cancelled',
                related_game_id=game.id,
            )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


def _expected_score(rating_a, rating_b):
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _apply_elo(team1_users, team2_users, team1_won):
    """Update ratings + win streaks using team-average ELO; returns {user_id: delta}."""
    avg1 = sum(u.rating for u in team1_users) / len(team1_users)
    avg2 = sum(u.rating for u in team2_users) / len(team2_users)
    expected1 = _expected_score(avg1, avg2)
    actual1 = 1.0 if team1_won else 0.0
    delta1 = round(ELO_K * (actual1 - expected1))
    deltas = {}
    winners = team1_users if team1_won else team2_users
    losers = team2_users if team1_won else team1_users
    for user in team1_users:
        user.rating += delta1
        deltas[user.id] = delta1
    for user in team2_users:
        user.rating -= delta1
        deltas[user.id] = -delta1
    for user in winners:
        user.ranked_wins += 1
        user.current_streak += 1
        user.best_streak = max(user.best_streak, user.current_streak)
    for user in losers:
        user.ranked_losses += 1
        user.current_streak = 0
    return deltas


def _finalize_game(game, actor_id=None):
    """Mark the game completed; for ranked games apply ELO and notify everyone."""
    by_user = {p.user_id: p for p in game.players}
    game.status = 'completed'
    game.completed_at = utcnow()
    court_name = game.court.name if game.court else 'the court'
    score_text = f'{game.score_team1}–{game.score_team2}'

    if game.game_type == 'ranked':
        team1_ids = [p.user_id for p in game.players if p.team == 1]
        team2_ids = [p.user_id for p in game.players if p.team == 2]
        team1_users = User.query.filter(User.id.in_(team1_ids)).all()
        team2_users = User.query.filter(User.id.in_(team2_ids)).all()
        deltas = _apply_elo(
            team1_users, team2_users,
            team1_won=game.score_team1 > game.score_team2,
        )
        for uid, delta in deltas.items():
            if uid in by_user:
                by_user[uid].rating_delta = delta
        for uid, delta in deltas.items():
            if uid == actor_id:
                continue
            sign = '+' if delta >= 0 else ''
            notify(
                uid,
                'score_confirmed',
                f'Final at {court_name}: {score_text} ({sign}{delta} rating)',
                related_game_id=game.id,
            )
    else:
        for uid in by_user:
            if uid == actor_id:
                continue
            notify(
                uid,
                'score_confirmed',
                f'Game recorded at {court_name}: {score_text}',
                related_game_id=game.id,
            )


@games_bp.post('/games/<int:game_id>/complete')
@login_required
def submit_score(game_id):
    """Report a score. Casual games finish immediately; ranked scores need an
    opposing player's confirmation before ratings move."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status not in ('upcoming', 'awaiting_confirmation'):
        return jsonify({'error': 'game_not_open'}), 400
    if game.recurrence != 'none':
        return jsonify({'error': 'recurring_open_play'}), 400
    player_ids = {p.user_id for p in game.players}
    if g.current_user.id not in player_ids:
        return jsonify({'error': 'forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    try:
        score1 = int(payload.get('score_team1'))
        score2 = int(payload.get('score_team2'))
    except (TypeError, ValueError):
        return jsonify({'error': 'scores_required'}), 400
    if score1 == score2 or score1 < 0 or score2 < 0:
        return jsonify({'error': 'invalid_scores'}), 400

    team1_ids = [int(uid) for uid in (payload.get('team1') or [])]
    team2_ids = [int(uid) for uid in (payload.get('team2') or [])]
    if not team1_ids or not team2_ids:
        return jsonify({'error': 'teams_required'}), 400
    if set(team1_ids) & set(team2_ids):
        return jsonify({'error': 'player_on_both_teams'}), 400
    if not (set(team1_ids) | set(team2_ids)) <= player_ids:
        return jsonify({'error': 'unknown_player'}), 400

    by_user = {p.user_id: p for p in game.players}
    for uid in team1_ids:
        by_user[uid].team = 1
    for uid in team2_ids:
        by_user[uid].team = 2

    game.score_team1 = score1
    game.score_team2 = score2
    game.score_submitted_by_id = g.current_user.id
    game.score_submitted_at = utcnow()

    my_team = by_user[g.current_user.id].team
    opposing_ids = team2_ids if my_team == 1 else team1_ids

    if game.game_type == 'ranked' and opposing_ids:
        game.status = 'awaiting_confirmation'
        score_text = f'{score1}–{score2}'
        for uid in opposing_ids:
            notify(
                uid,
                'score_submitted',
                f'{g.current_user.display_name} reported {score_text} — confirm the score',
                related_user_id=g.current_user.id,
                related_game_id=game.id,
            )
    else:
        _finalize_game(game, actor_id=g.current_user.id)

    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/confirm')
@login_required
def confirm_score(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_confirm'}), 400

    me = next((p for p in game.players if p.user_id == g.current_user.id), None)
    submitter = next(
        (p for p in game.players if p.user_id == game.score_submitted_by_id), None,
    )
    if not me:
        return jsonify({'error': 'forbidden'}), 403
    if (
        not submitter or not me.team or me.user_id == submitter.user_id
        or (submitter.team and me.team == submitter.team)
    ):
        return jsonify({'error': 'opponent_confirmation_required'}), 403

    _finalize_game(game, actor_id=g.current_user.id)
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/games/<int:game_id>/dispute')
@login_required
def dispute_score(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_dispute'}), 400

    me = next((p for p in game.players if p.user_id == g.current_user.id), None)
    if not me:
        return jsonify({'error': 'forbidden'}), 403

    submitter_id = game.score_submitted_by_id
    score_text = f'{game.score_team1}–{game.score_team2}'
    game.status = 'upcoming'
    game.score_team1 = None
    game.score_team2 = None
    game.score_submitted_by_id = None
    game.score_submitted_at = None
    if submitter_id and submitter_id != g.current_user.id:
        notify(
            submitter_id,
            'score_disputed',
            f'{g.current_user.display_name} disputed the score {score_text} — please re-enter it together',
            related_user_id=g.current_user.id,
            related_game_id=game.id,
        )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.post('/users/<int:user_id>/challenge')
@login_required
def challenge_user(user_id):
    """Challenge another player to a ranked match at a court, right now."""
    target = db.session.get(User, user_id)
    if not target:
        return jsonify({'error': 'user_not_found'}), 404
    if target.id == g.current_user.id:
        return jsonify({'error': 'cannot_challenge_self'}), 400

    payload = request.get_json(silent=True) or {}
    court = db.session.get(Court, int(payload.get('court_id') or 0))
    if not court:
        return jsonify({'error': 'court_not_found'}), 404

    game = Game(
        court_id=court.id,
        creator_id=g.current_user.id,
        scheduled_at=utcnow(),
        game_type='ranked',
        visibility='private',
        max_players=2,
        notes=f'⚔️ {g.current_user.display_name} challenged {target.display_name}!',
    )
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=g.current_user.id))
    db.session.add(GameInvite(game_id=game.id, user_id=target.id))
    notify(
        target.id,
        'challenge',
        f'⚔️ {g.current_user.display_name} challenged you at {court.name}!',
        related_user_id=g.current_user.id,
        related_game_id=game.id,
    )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id)), 201


@games_bp.post('/games/<int:game_id>/decline')
@login_required
def decline_challenge(game_id):
    """Decline an open challenge-style game you were invited to: cancels it."""
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'upcoming':
        return jsonify({'error': 'game_not_open'}), 400
    if any(p.user_id == g.current_user.id for p in game.players):
        return jsonify({'error': 'already_joined'}), 400
    if len(game.players) > 1:
        return jsonify({'error': 'game_already_started'}), 400

    from backend.models import Notification
    was_challenged = Notification.query.filter_by(
        user_id=g.current_user.id,
        kind='challenge',
        related_game_id=game.id,
    ).first()
    if not was_challenged:
        return jsonify({'error': 'forbidden'}), 403

    game.status = 'cancelled'
    notify(
        game.creator_id,
        'challenge_declined',
        f'{g.current_user.display_name} declined your challenge',
        related_user_id=g.current_user.id,
        related_game_id=game.id,
    )
    db.session.commit()
    return jsonify(game.to_dict(g.current_user.id))


@games_bp.get('/games/results')
def recent_results():
    """Feed of recently finished games: yours, your friends', and nearby ones."""
    auto_confirm_stale_scores()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    current_user = optional_current_user()
    viewer_id = current_user.id if current_user else None

    friends = friend_ids(current_user.id) if current_user else set()

    games = (
        Game.query.filter(Game.status == 'completed')
        .order_by(Game.completed_at.desc())
        .limit(100)
        .all()
    )
    items = []
    for game in games:
        player_ids = {p.user_id for p in game.players}
        involves_me = viewer_id in player_ids
        involves_friend = bool(friends & player_ids)
        distance = None
        court = game.court
        if lat is not None and lng is not None and court and court.latitude is not None:
            distance = haversine_miles(lat, lng, court.latitude, court.longitude)
        nearby = distance is not None and distance <= 100
        # Strangers only see open games nearby; private/friends stay among their people.
        if not (involves_me or involves_friend or (nearby and game.visibility == 'open')):
            continue
        item = game.to_dict(viewer_id)
        item['involves_friend'] = involves_friend
        item['involves_me'] = involves_me
        if distance is not None:
            item['distance_miles'] = round(distance, 1)
        items.append(item)
        if len(items) >= 30:
            break
    return jsonify({'items': items})


@games_bp.get('/leaderboard')
def leaderboard():
    users = (
        User.query.filter(User.ranked_wins + User.ranked_losses > 0)
        .order_by(User.rating.desc())
        .limit(50)
        .all()
    )
    return jsonify({'items': [u.to_public_dict() for u in users]})
