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
        # Coarse presence heartbeat, throttled so it's one write per ~5 min.
        now = utcnow()
        if user.last_active_at is None or (now - user.last_active_at).total_seconds() > 300:
            user.last_active_at = now
            db.session.commit()
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
    # Who this player faces next — the banner's live-state headline.
    data['my_next_opponent'] = None
    if my_entry and tournament.status == 'active':
        for m in sorted(tournament.matches, key=lambda m: (m.round, m.position)):
            if (m.winner_entry_id is None
                    and my_entry.id in (m.entry1_id, m.entry2_id)
                    and m.entry1_id and m.entry2_id):
                opp_id = m.entry2_id if m.entry1_id == my_entry.id else m.entry1_id
                opp = next((e for e in tournament.entries if e.id == opp_id), None)
                if opp:
                    data['my_next_opponent'] = opp.display_name()
                break
    return data


def _me_payload(user):
    # Lazy import avoids the auth/chat blueprint import cycle at startup.
    from backend.routes.chat import community_room_unread_count

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
        'community_room_unread': community_room_unread_count(user.id),
        'pending_friend_requests': pending_requests,
        'unread_notifications': unread_notifications,
        'games_to_confirm': _games_to_confirm_count(user.id),
        'latest_notification': latest.to_dict() if latest else None,
        'active_game': _active_game_payload(user),
        'active_tournament': _active_tournament_payload(user),
        'muteable_notifications': MUTEABLE_NOTIFICATIONS,
    }


@auth_bp.post('/client-errors')
@rate_limit(10, 300)
def report_client_error():
    """Browser-side crash reports land in the server log (visible on Render)
    — no storage, tight rate limit, hard truncation. Anonymous by design so
    login-screen breakage reports too."""
    payload = request.get_json(silent=True) or {}
    user = optional_current_user()
    current_app.logger.error(
        'CLIENT ERROR%s: %s | at %s | %s',
        f' (user {user.id})' if user else '',
        str(payload.get('message') or '')[:300],
        str(payload.get('url') or '')[:200],
        str(payload.get('stack') or '')[:600],
    )
    return '', 204


@auth_bp.post('/feedback')
@rate_limit(5, 3600)
@login_required
def send_feedback():
    """A direct line from players to the operator: feedback lands in the
    server log (visible on Render) tagged with who sent it."""
    payload = request.get_json(silent=True) or {}
    message = str(payload.get('message') or '').strip()
    if len(message) < 3:
        return jsonify({'error': 'message_required'}), 400
    current_app.logger.warning(
        'USER FEEDBACK from %s (#%s, %s): %s',
        g.current_user.display_name,
        g.current_user.id,
        g.current_user.email,
        message[:2000],
    )
    return jsonify({'sent': True})


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

    # Tournaments won that week headline the recap.
    from backend.models import Tournament, TournamentEntry
    titles_won = (
        Tournament.query
        .join(TournamentEntry, Tournament.champion_entry_id == TournamentEntry.id)
        .filter(
            Tournament.status == 'completed',
            Tournament.completed_at.isnot(None),
            db.or_(
                TournamentEntry.player1_id == user.id,
                TournamentEntry.player2_id == user.id,
            ),
        )
        .order_by(Tournament.completed_at.desc())
        .limit(10)
        .all()
    )
    titles_won = [t for t in titles_won
                  if t.completed_at.isocalendar()[:2] == (year, week)]

    if not played and not titles_won:
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
    if played:
        title = f'Your week on the courts: {len(played)} game{"s" if len(played) != 1 else ""}'
        if wins or losses:
            title += f', {wins}–{losses}'
        if titles_won:
            title += f' — and you won {titles_won[0].name}!' if len(titles_won) == 1 \
                else f' — and {len(titles_won)} tournament titles!'
    else:
        title = (f'Champion week — you won {titles_won[0].name}!'
                 if len(titles_won) == 1
                 else f'Champion week — {len(titles_won)} tournament titles!')
    body = f'{"+" if delta >= 0 else ""}{delta} rating' if delta else 'See your stats on the profile tab'
    notify(user.id, 'weekly_recap', title, body,
           related_tournament_id=titles_won[0].id if titles_won else None)
    db.session.commit()


def _maybe_nearby_games_digest(user):
    """Once a week: 'N open games near you this week'. Quiet weeks and users
    without a home area just advance the marker — no empty pings."""
    from datetime import timedelta

    if user.home_lat is None:
        return
    now = utcnow()
    year, week, _ = now.isocalendar()
    this_week = f'{year}-W{week:02d}'
    if user.last_games_digest_week == this_week:
        return
    user.last_games_digest_week = this_week

    from backend.routes.courts import haversine_miles
    candidates = (
        Game.query.join(Court, Game.court_id == Court.id)
        .filter(
            Game.status == 'upcoming',
            Game.visibility == 'open',
            Game.creator_id != user.id,
            Game.scheduled_at >= now,
            Game.scheduled_at <= now + timedelta(days=7),
            # ~35mi bbox prefilter; the haversine below applies the real radius.
            Court.latitude.between(user.home_lat - 0.5, user.home_lat + 0.5),
            Court.longitude.between(user.home_lng - 0.6, user.home_lng + 0.6),
        )
        .limit(50)
        .all()
    )
    joined = {gp.game_id for gp in GamePlayer.query.filter_by(user_id=user.id).all()}
    nearby = [
        g_ for g_ in candidates
        if g_.id not in joined and g_.court and g_.court.latitude is not None
        and haversine_miles(user.home_lat, user.home_lng,
                            g_.court.latitude, g_.court.longitude) <= 25
    ]
    if nearby:
        n = len(nearby)
        notify(user.id, 'nearby_games',
               f'{n} open game{"" if n == 1 else "s"} near you this week',
               related_game_id=nearby[0].id)
    db.session.commit()


def _maybe_streak_nag(user, now=None):
    """Weekend heads-up when last week's play is about to lapse: played last
    ISO week, nothing yet this week, and it's Sat/Sun. Once per week, muteable.
    `now` is injectable so tests aren't chained to the real weekday."""
    from datetime import timedelta

    now = now or utcnow()
    if now.isoweekday() < 6:
        return
    year, week, _ = now.isocalendar()
    this_week = f'{year}-W{week:02d}'
    if user.last_streak_nag_week == this_week:
        return
    user.last_streak_nag_week = this_week

    recent = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
            Game.completed_at >= now - timedelta(days=15),
        )
        .all()
    )
    prev_week = (now - timedelta(days=7)).isocalendar()[:2]
    cur_week = now.isocalendar()[:2]
    played_prev = any(g_.completed_at.isocalendar()[:2] == prev_week for g_ in recent)
    played_cur = any(g_.completed_at.isocalendar()[:2] == cur_week for g_ in recent)
    if played_prev and not played_cur:
        notify(user.id, 'streak_nag',
               'Your play streak ends Sunday — get a game in this week')
    db.session.commit()


@auth_bp.get('/me')
@login_required
def me():
    # Lazy import: games.py imports from this module at load time.
    from backend.routes.clubs import send_club_digests
    from backend.routes.games import expire_stale_unscored, send_game_reminders
    from backend.routes.leagues import advance_due_league_rounds
    from backend.routes.tournaments import send_tournament_reminders
    expire_stale_unscored()
    send_game_reminders()
    send_tournament_reminders()
    advance_due_league_rounds()
    send_club_digests()
    _maybe_weekly_recap(g.current_user)
    _maybe_nearby_games_digest(g.current_user)
    _maybe_streak_nag(g.current_user)
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
    from backend.models import MessageSendAttempt
    MessageSendAttempt.query.filter_by(
        sender_id=user.id,
    ).delete(synchronize_session=False)
    Message.query.filter(or_(
        Message.sender_id == user.id, Message.recipient_id == user.id,
    )).delete(synchronize_session=False)
    GameInvite.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    FavoriteCourt.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    from backend.models import PushSubscription
    PushSubscription.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    # Leagues: cancel ones they organize that haven't finished; drop their
    # not-yet-started registrations (active/completed keep the anonymized shell).
    from backend.models import League, LeagueMember
    for lg in League.query.filter(
        League.organizer_id == user.id,
        League.status.in_(['registration', 'active']),
    ).all():
        lg.status = 'cancelled'
        for member in lg.members:
            if member.user_id != user.id:
                notify(
                    member.user_id, 'league_update',
                    f'{lg.name} was cancelled', related_league_id=lg.id,
                )
    for membership in LeagueMember.query.filter_by(user_id=user.id).all():
        if membership.league and membership.league.status == 'registration':
            membership.league.members.remove(membership)

    # Clubs: hand owned clubs to the longest-standing member; disband empty ones.
    from backend.models import ClubChatRead, ClubMember
    from backend.routes.clubs import _delete_club
    for membership in ClubMember.query.filter_by(user_id=user.id).all():
        club = membership.club
        if membership.role == 'owner':
            others = sorted(
                (m for m in club.members if m.user_id != user.id),
                key=lambda m: (m.created_at, m.id),
            )
            if others:
                others[0].role = 'owner'
            else:
                _delete_club(club)
                continue
        club.members.remove(membership)  # delete-orphan keeps the collection in sync
    ClubChatRead.query.filter_by(user_id=user.id).delete(synchronize_session=False)
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


def profile_stats_payload(user):
    """Personal play stats: totals, this month, weekly streak, top court."""
    from datetime import timedelta

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

    # Play-pattern insights from scored games: best part of day, busiest
    # weekday, average score margin. Local time is approximated from the home
    # longitude (15° ≈ 1h) — plenty for day-part bucketing.
    insights = None
    scored = []
    for game in completed:
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if mine and mine.team and game.score_team1 is not None:
            my_margin = (game.score_team1 - game.score_team2) if mine.team == 1 \
                else (game.score_team2 - game.score_team1)
            scored.append((game, my_margin > 0, my_margin))
    if len(scored) >= 3:
        offset = timedelta(hours=round((user.home_lng if user.home_lng is not None else -90) / 15))
        parts, days = {}, {}
        margin_total = 0
        for game, won, margin in scored:
            local = game.scheduled_at + offset
            part = ('mornings' if 5 <= local.hour < 12
                    else 'afternoons' if 12 <= local.hour < 17 else 'evenings')
            wins_n, games_n = parts.get(part, (0, 0))
            parts[part] = (wins_n + (1 if won else 0), games_n + 1)
            day = local.strftime('%A')
            days[day] = days.get(day, 0) + 1
            margin_total += margin
        best_part = None
        eligible = {p: wn for p, wn in parts.items() if wn[1] >= 3}
        if eligible:
            part, (wins_n, games_n) = max(
                eligible.items(), key=lambda kv: kv[1][0] / kv[1][1],
            )
            best_part = {'label': part, 'wins': wins_n, 'games': games_n}
        insights = {
            'best_part': best_part,
            'busiest_day': max(days, key=days.get) if days else None,
            'avg_margin': round(margin_total / len(scored), 1),
        }

    from backend.models import (
        badge_progress, league_titles, mvp_award_count, player_badges,
        rating_history_for, tournament_titles,
    )
    rating_history = rating_history_for(user)
    badges = player_badges(user)
    titles = tournament_titles(user)
    lg_titles = league_titles(user)
    mvp_awards = mvp_award_count(user)

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

    return {
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
        'league_titles': lg_titles,
        'mvp_awards': mvp_awards,
        'insights': insights,
        'rating_history': rating_history,
    }


@auth_bp.get('/me/stats')
@login_required
def my_stats():
    return jsonify(profile_stats_payload(g.current_user))


@auth_bp.get('/me/dashboard')
@login_required
def profile_dashboard():
    """All independently rendered Profile sections in one authenticated read."""
    # Lazy imports avoid the route-module cycle: games/courts both import auth.
    from backend.routes.courts import favorite_courts_payload
    from backend.routes.games import game_history_payload, my_games_payload

    user = g.current_user
    return jsonify({
        'games': my_games_payload(user),
        'stats': profile_stats_payload(user),
        'favorites': favorite_courts_payload(user),
        'history': game_history_payload(user),
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
