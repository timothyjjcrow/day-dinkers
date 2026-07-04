"""Authentication: register, login, current-user profile."""
import re
import time
from functools import wraps

import jwt
from flask import Blueprint, current_app, g, jsonify, request

from backend.app import db
from backend.security import rate_limit
from backend.models import (
    BlockedUser,
    CheckIn,
    Court,
    CourtReview,
    FavoriteCourt,
    Friendship,
    Game,
    GameInvite,
    GamePlayer,
    Message,
    Notification,
    MUTEABLE_NOTIFICATIONS,
    SKILL_LEVELS,
    User,
    notify,
    utcnow,
)

auth_bp = Blueprint('auth', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _issue_token(user):
    now = int(time.time())
    return jwt.encode(
        {
            'user_id': user.id,
            'iat': now,
            'exp': now + int(current_app.config.get('JWT_TTL_SECONDS', 2592000)),
        },
        current_app.config['SECRET_KEY'],
        algorithm=current_app.config.get('JWT_ALGORITHM', 'HS256'),
    )


def optional_current_user():
    auth_header = str(request.headers.get('Authorization') or '').strip()
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=[current_app.config.get('JWT_ALGORITHM', 'HS256')],
        )
    except Exception:
        return None
    user_id = payload.get('user_id')
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    # A deleted account's outstanding tokens must die immediately.
    if user is not None and user.deleted_at is not None:
        return None
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = optional_current_user()
        if not user:
            return jsonify({'error': 'authentication_required'}), 401
        g.current_user = user
        return view(*args, **kwargs)
    return wrapped


def active_checkin_for(user_id):
    return (
        CheckIn.query.filter_by(user_id=user_id, checked_out_at=None)
        .order_by(CheckIn.checked_in_at.desc(), CheckIn.id.desc())
        .first()
    )


def presence_payload(user_id):
    checkin = active_checkin_for(user_id)
    if not checkin:
        return {'checked_in': False}
    court = checkin.court
    return {
        'checked_in': True,
        'court_id': checkin.court_id,
        'court_name': court.name if court else 'Court',
        'court_latitude': court.latitude if court else None,
        'court_longitude': court.longitude if court else None,
        'looking_for_game': bool(checkin.looking_for_game),
        'checked_in_at': checkin.checked_in_at.isoformat() + 'Z' if checkin.checked_in_at else None,
    }


def _games_to_confirm_count(user_id):
    """Games whose reported score is waiting on this user (opposing team) to confirm."""
    games = (
        Game.query.filter(Game.status == 'awaiting_confirmation')
        .join(GamePlayer)
        .filter(GamePlayer.user_id == user_id)
        .all()
    )
    count = 0
    for game in games:
        me = next((p for p in game.players if p.user_id == user_id), None)
        submitter = next(
            (p for p in game.players if p.user_id == game.score_submitted_by_id), None,
        )
        if me and submitter and me.team and submitter.team and me.team != submitter.team:
            count += 1
    return count


def _active_game_payload(user):
    """The single most relevant game for the banner.

    Priority: live game you're in > incoming challenge > score waiting on you
    > your score waiting on opponents > your next upcoming game."""
    now = utcnow()
    candidates = []

    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status.in_(['upcoming', 'awaiting_confirmation']),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(25)
        .all()
    )
    from datetime import timedelta
    for game in games:
        data = game.to_dict(user.id)
        if game.status == 'upcoming' and game.scheduled_at <= now:
            # A game hours past its start isn't "live" — the Play tab's
            # enter-the-score section owns the nagging from there.
            if game.scheduled_at < now - timedelta(hours=4):
                continue
            rank, banner_state = 0, 'live'
        elif data['awaiting_your_confirmation']:
            rank, banner_state = 2, 'confirm'
        elif game.status == 'awaiting_confirmation':
            rank, banner_state = 4, 'waiting'
        else:
            rank, banner_state = 5, 'upcoming'
        data['banner_state'] = banner_state
        candidates.append((rank, data))

    # Private games you've been invited to (challenges + personal invites) and
    # haven't joined yet. The invite list is the source of truth, not notifications.
    invited_games = (
        Game.query.join(GameInvite)
        .filter(GameInvite.user_id == user.id, Game.status == 'upcoming')
        .order_by(Game.scheduled_at.asc())
        .limit(15)
        .all()
    )
    for game in invited_games:
        data = game.to_dict(user.id)
        if data['is_joined'] or data['spots_left'] <= 0:
            continue
        is_challenge = game.notes.startswith('⚔️')
        if is_challenge:
            data['banner_state'] = 'challenge'
            candidates.append((1, data))
        else:
            data['banner_state'] = 'invited'
            candidates.append((3, data))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]['scheduled_at'] or ''))
    return candidates[0][1]


def _active_tournament_payload(user):
    """The most relevant tournament for the banner: one that's under way, or
    one you're entered in (or organizing) that starts within 24h. The game
    banner still wins client-side when both exist."""
    from datetime import timedelta

    from backend.models import Tournament, TournamentEntry
    now = utcnow()
    entered = db.session.query(TournamentEntry.tournament_id).filter(
        db.or_(
            TournamentEntry.player1_id == user.id,
            TournamentEntry.player2_id == user.id,
        )
    )
    rows = (
        Tournament.query.filter(
            db.or_(Tournament.id.in_(entered), Tournament.organizer_id == user.id),
            db.or_(
                Tournament.status == 'active',
                db.and_(
                    Tournament.status == 'registration',
                    Tournament.starts_at <= now + timedelta(hours=24),
                    Tournament.starts_at >= now - timedelta(hours=6),
                ),
            ),
        )
        .order_by(Tournament.starts_at.asc())
        .limit(5)
        .all()
    )
    if not rows:
        return None
    # An in-progress bracket beats one that's merely imminent.
    rows.sort(key=lambda t: (t.status != 'active', t.starts_at))
    tournament = rows[0]
    data = tournament.to_dict(user.id)
    data['banner_state'] = 'live' if tournament.status == 'active' else 'soon'
    my_entry = tournament.entry_for(user.id)
    data['my_checked_in'] = bool(my_entry and my_entry.checked_in_at)
    return data


def _me_payload(user):
    unread_messages = Message.query.filter_by(recipient_id=user.id, read_at=None).count()
    pending_requests = Friendship.query.filter_by(
        addressee_id=user.id, status='pending',
    ).count()
    unread_notifications = Notification.query.filter_by(
        user_id=user.id, read=False,
    ).count()
    latest = (
        Notification.query.filter_by(user_id=user.id)
        .order_by(Notification.id.desc())
        .first()
    )
    return {
        'user': user.to_dict(),
        'presence': presence_payload(user.id),
        'unread_messages': unread_messages,
        'pending_friend_requests': pending_requests,
        'unread_notifications': unread_notifications,
        'games_to_confirm': _games_to_confirm_count(user.id),
        'latest_notification': latest.to_dict() if latest else None,
        'active_game': _active_game_payload(user),
        'active_tournament': _active_tournament_payload(user),
        'muteable_notifications': MUTEABLE_NOTIFICATIONS,
    }


@auth_bp.post('/auth/register')
@rate_limit(10, 600)
def register():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email') or '').strip().lower()
    password = str(payload.get('password') or '')
    display_name = str(payload.get('display_name') or '').strip()

    if not _EMAIL_RE.match(email):
        return jsonify({'error': 'invalid_email'}), 400
    if len(password) < 6:
        return jsonify({'error': 'password_too_short'}), 400
    if not display_name:
        return jsonify({'error': 'display_name_required'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'email_taken'}), 409

    user = User(email=email, display_name=display_name[:120])
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({'token': _issue_token(user), **_me_payload(user)}), 201


@auth_bp.post('/auth/login')
@rate_limit(20, 300)
def login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email') or '').strip().lower()
    password = str(payload.get('password') or '')

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'invalid_credentials'}), 401
    return jsonify({'token': _issue_token(user), **_me_payload(user)})


def _maybe_weekly_recap(user):
    """First app-open each ISO week recaps the previous week's play as a
    notification. The marker advances even on quiet weeks, so this stays
    one cheap string compare on every later /me."""
    from datetime import timedelta

    now = utcnow()
    year, week, _ = (now - timedelta(days=7)).isocalendar()
    prev_week = f'{year}-W{week:02d}'
    if user.last_recap_week == prev_week:
        return
    user.last_recap_week = prev_week

    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
        .order_by(Game.completed_at.desc())
        .limit(60)
        .all()
    )
    played = [g_ for g_ in games if g_.completed_at.isocalendar()[:2] == (year, week)]
    if not played:
        db.session.commit()  # persist the marker; nothing to say
        return

    wins = losses = 0
    delta = 0
    for game in played:
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if not mine:
            continue
        if mine.rating_delta is not None:
            delta += mine.rating_delta
        if mine.team and game.score_team1 is not None:
            if (game.score_team1 > game.score_team2) == (mine.team == 1):
                wins += 1
            else:
                losses += 1
    title = f'Your week on the courts: {len(played)} game{"s" if len(played) != 1 else ""}'
    if wins or losses:
        title += f', {wins}–{losses}'
    body = f'{"+" if delta >= 0 else ""}{delta} rating' if delta else 'See your stats on the profile tab'
    notify(user.id, 'weekly_recap', title, body)
    db.session.commit()


@auth_bp.get('/me')
@login_required
def me():
    # Lazy import: games.py imports from this module at load time.
    from backend.routes.games import expire_stale_unscored, send_game_reminders
    from backend.routes.tournaments import send_tournament_reminders
    expire_stale_unscored()
    send_game_reminders()
    send_tournament_reminders()
    _maybe_weekly_recap(g.current_user)
    return jsonify(_me_payload(g.current_user))


@auth_bp.post('/auth/change-password')
@rate_limit(10, 3600)
@login_required
def change_password():
    user = g.current_user
    payload = request.get_json(silent=True) or {}
    # 403, not 401: the client treats 401 as an expired session and logs out.
    if not user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    new_password = str(payload.get('new_password') or '')
    if len(new_password) < 6:
        return jsonify({'error': 'password_too_short'}), 400
    user.set_password(new_password)
    db.session.commit()
    return jsonify({'ok': True})


@auth_bp.delete('/me')
@rate_limit(5, 3600)
@login_required
def delete_me():
    """Delete the account: wipe personal data and social graph, keep an
    anonymized shell so opponents' completed match history stays intact."""
    import secrets

    from sqlalchemy import or_

    user = g.current_user
    payload = request.get_json(silent=True) or {}
    # 403, not 401: the client treats 401 as an expired session and logs out,
    # which would swallow a simple wrong-password mistake.
    if not user.check_password(str(payload.get('password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403

    # Cancel upcoming games they host, letting joiners know.
    hosted = Game.query.filter_by(creator_id=user.id, status='upcoming').all()
    for game in hosted:
        game.status = 'cancelled'
        for player in game.players:
            if player.user_id != user.id:
                notify(
                    player.user_id,
                    'game_cancelled',
                    f'Game at {game.court.name if game.court else "court"} was cancelled',
                    related_game_id=game.id,
                )
    # Free up their spot in other people's upcoming games.
    GamePlayer.query.filter(
        GamePlayer.user_id == user.id,
        GamePlayer.game_id.in_(
            db.session.query(Game.id).filter(Game.status == 'upcoming'),
        ),
    ).delete(synchronize_session='fetch')

    # Social graph, messages, and activity are personal data — remove them.
    Friendship.query.filter(or_(
        Friendship.requester_id == user.id, Friendship.addressee_id == user.id,
    )).delete(synchronize_session=False)
    BlockedUser.query.filter(or_(
        BlockedUser.blocker_id == user.id, BlockedUser.blocked_id == user.id,
    )).delete(synchronize_session=False)
    Message.query.filter(or_(
        Message.sender_id == user.id, Message.recipient_id == user.id,
    )).delete(synchronize_session=False)
    GameInvite.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    FavoriteCourt.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    CourtReview.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    CheckIn.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    Notification.query.filter(or_(
        Notification.user_id == user.id, Notification.related_user_id == user.id,
    )).delete(synchronize_session=False)

    # Anonymize the shell that remains in completed games.
    user.display_name = 'Deleted player'
    user.email = f'deleted-{user.id}@invalid'
    user.set_password(secrets.token_hex(32))
    user.bio = ''
    user.avatar_url = ''
    user.home_court_id = None
    user.home_lat = user.home_lng = None
    user.home_area = ''
    user.last_lat = user.last_lng = None
    user.last_location_at = None
    user.deleted_at = utcnow()
    db.session.commit()
    return jsonify({'deleted': True})


@auth_bp.get('/me/stats')
@login_required
def my_stats():
    """Personal play stats: totals, this month, weekly streak, top court."""
    from datetime import timedelta

    user = g.current_user
    now = utcnow()
    completed = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
        .order_by(Game.completed_at.desc())
        .limit(500)
        .all()
    )
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    games_this_month = sum(1 for game in completed if game.completed_at >= month_start)

    # Weekly play streak: consecutive ISO weeks with ≥1 completed game. The
    # current week only extends the streak — a quiet week so far doesn't end it.
    weeks = {game.completed_at.isocalendar()[:2] for game in completed}
    streak = 0
    cursor = now if now.isocalendar()[:2] in weeks else now - timedelta(days=7)
    while cursor.isocalendar()[:2] in weeks:
        streak += 1
        cursor -= timedelta(days=7)

    top_court = None
    counts = {}
    for game in completed:
        counts[game.court_id] = counts.get(game.court_id, 0) + 1
    if counts:
        top_id = max(counts, key=counts.get)
        court = db.session.get(Court, top_id)
        if court:
            top_court = {'id': court.id, 'name': court.name, 'games': counts[top_id]}

    # Last-5 form, newest first.
    form = []
    for game in completed:
        if len(form) >= 5:
            break
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if not mine or not mine.team or game.score_team1 is None:
            continue
        form.append('W' if (game.score_team1 > game.score_team2) == (mine.team == 1) else 'L')

    # Who you win with, and who you battle most.
    partners, rivals = {}, {}
    for game in completed:
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if not mine or not mine.team or game.score_team1 is None:
            continue
        i_won = (game.score_team1 > game.score_team2) == (mine.team == 1)
        for p in game.players:
            if p.user_id == user.id or not p.team or not p.user or p.user.deleted_at:
                continue
            bucket = partners if p.team == mine.team else rivals
            entry = bucket.setdefault(p.user_id, {'user': p.user, 'games': 0, 'wins': 0})
            entry['games'] += 1
            entry['wins'] += 1 if i_won else 0

    best_partner = None
    if partners:
        top = max(partners.values(), key=lambda e: (e['wins'], e['games']))
        if top['wins'] > 0:
            best_partner = {
                'user_id': top['user'].id,
                'display_name': top['user'].display_name,
                'wins': top['wins'],
                'games': top['games'],
            }
    top_rival = None
    if rivals:
        top = max(rivals.values(), key=lambda e: e['games'])
        top_rival = {
            'user_id': top['user'].id,
            'display_name': top['user'].display_name,
            'games': top['games'],
            'your_wins': top['wins'],
        }

    from backend.models import (
        badge_progress, player_badges, rating_history_for, tournament_titles,
    )
    rating_history = rating_history_for(user)
    badges = player_badges(user)
    titles = tournament_titles(user)

    # Congratulate the player once for each newly-earned badge.
    import json as _json
    try:
        already = set(_json.loads(user.notified_badges or '[]'))
    except (ValueError, TypeError):
        already = set()
    fresh = [b for b in badges if b['id'] not in already]
    if fresh:
        for b in fresh:
            notify(user.id, 'badge_earned', f'Badge unlocked: {b["emoji"]} {b["label"]}')
        user.notified_badges = _json.dumps([b['id'] for b in badges])
        db.session.commit()

    return jsonify({
        'games_total': len(completed),
        'games_this_month': games_this_month,
        'week_streak': streak,
        'top_court': top_court,
        'best_partner': best_partner,
        'top_rival': top_rival,
        'form': form,
        'badges': badges,
        'badge_progress': badge_progress(user),
        'tournament_titles': titles,
        'rating_history': rating_history,
    })


@auth_bp.patch('/me')
@rate_limit(30, 600)
@login_required
def update_me():
    user = g.current_user
    payload = request.get_json(silent=True) or {}

    if 'display_name' in payload:
        name = str(payload.get('display_name') or '').strip()
        if not name:
            return jsonify({'error': 'display_name_required'}), 400
        user.display_name = name[:120]
    if 'bio' in payload:
        user.bio = str(payload.get('bio') or '').strip()[:500]
    if 'skill_level' in payload:
        level = str(payload.get('skill_level') or '').strip().lower()
        if level not in SKILL_LEVELS:
            return jsonify({'error': 'invalid_skill_level'}), 400
        user.skill_level = level
    if 'availability' in payload:
        import json as _json

        from backend.models import AVAILABILITY_SLOTS
        slots = payload.get('availability')
        if not isinstance(slots, list):
            return jsonify({'error': 'invalid_availability'}), 400
        cleaned = [s for s in dict.fromkeys(slots) if s in AVAILABILITY_SLOTS]
        user.availability = _json.dumps(cleaned)
    if 'muted_notifications' in payload:
        import json as _json

        from backend.models import MUTEABLE_NOTIFICATIONS
        muted = payload.get('muted_notifications')
        if not isinstance(muted, list):
            return jsonify({'error': 'invalid_muted_notifications'}), 400
        cleaned = [k for k in dict.fromkeys(muted) if k in MUTEABLE_NOTIFICATIONS]
        user.muted_notifications = _json.dumps(cleaned)
    if 'avatar_color' in payload:
        color = str(payload.get('avatar_color') or '').strip()
        if not re.match(r'^#[0-9a-fA-F]{6}$', color):
            return jsonify({'error': 'invalid_avatar_color'}), 400
        user.avatar_color = color
    if 'avatar_url' in payload:
        url = str(payload.get('avatar_url') or '').strip()
        if url and not re.match(r'^https?://\S+$', url):
            return jsonify({'error': 'invalid_avatar_url'}), 400
        user.avatar_url = url[:500]
    if 'home_court_id' in payload:
        court_id = payload.get('home_court_id')
        if court_id in (None, '', 0):
            user.home_court_id = None
        else:
            court = db.session.get(Court, int(court_id))
            if not court:
                return jsonify({'error': 'court_not_found'}), 404
            user.home_court_id = court.id

    if 'home_lat' in payload or 'home_lng' in payload:
        try:
            home_lat = float(payload.get('home_lat'))
            home_lng = float(payload.get('home_lng'))
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid_home_location'}), 400
        if not (-90 <= home_lat <= 90 and -180 <= home_lng <= 180):
            return jsonify({'error': 'invalid_home_location'}), 400
        user.home_lat = home_lat
        user.home_lng = home_lng
        user.home_area = str(payload.get('home_area') or '').strip()[:120]

    db.session.commit()
    return jsonify(_me_payload(user))
