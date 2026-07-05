"""Box leagues: rating-seeded boxes, round robin within each box, promotion/
relegation between rounds, champion crowned from box 1 at completion."""
from flask import Blueprint, g, jsonify, request

from backend.app import db
from backend.models import (
    Court, League, LeagueMatch, LeagueMember, is_blocked_between, notify,
    utcnow,
)
from backend.security import rate_limit

leagues_bp = Blueprint('leagues', __name__)

from backend.routes.auth import login_required  # noqa: E402
from backend.routes.games import _parse_scheduled_at  # noqa: E402

MIN_PLAYERS = 3


def _league_or_404(league_id):
    league = db.session.get(League, league_id)
    if not league:
        return None, (jsonify({'error': 'league_not_found'}), 404)
    return league, None


def _round_robin_pairs(user_ids):
    """All pairings within one box."""
    return [
        (user_ids[i], user_ids[j])
        for i in range(len(user_ids))
        for j in range(i + 1, len(user_ids))
    ]


def _boxes_of(league):
    """{box_number: [members sorted by standing]} for an active league."""
    boxes = {}
    for member in league.members:
        if member.box:
            boxes.setdefault(member.box, []).append(member)
    for box_members in boxes.values():
        box_members.sort(
            key=lambda m: (-m.points, -m.wins, -(m.user.rating if m.user else 0)),
        )
    return boxes


def _generate_round(league):
    """Round-robin matches inside every box for the league's current round."""
    for box_number, box_members in _boxes_of(league).items():
        for p1, p2 in _round_robin_pairs([m.user_id for m in box_members]):
            # Assign the relationship, not the FK — keeps league.matches in
            # sync for the payload built later in this same request.
            db.session.add(LeagueMatch(
                league=league, round=league.current_round,
                box=box_number, player1_id=p1, player2_id=p2,
            ))


@leagues_bp.post('/leagues')
@rate_limit(10, 3600)
@login_required
def create_league():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()[:120]
    if len(name) < 3:
        return jsonify({'error': 'name_required'}), 400
    court = db.session.get(Court, int(payload.get('court_id') or 0))
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    starts_at = _parse_scheduled_at(payload.get('starts_at'))
    if not starts_at:
        return jsonify({'error': 'invalid_starts_at'}), 400

    try:
        box_size = int(payload.get('box_size') or 4)
    except (TypeError, ValueError):
        box_size = 4
    box_size = min(max(box_size, 3), 6)
    try:
        max_players = int(payload.get('max_players') or 16)
    except (TypeError, ValueError):
        max_players = 16
    max_players = min(max(max_players, MIN_PLAYERS), 48)
    try:
        round_days = int(payload.get('round_days') or 7)
    except (TypeError, ValueError):
        round_days = 7
    round_days = min(max(round_days, 3), 28)

    # Running under a club banner: members only.
    club = None
    if payload.get('club_id'):
        from backend.models import Club, ClubMember
        club = db.session.get(Club, int(payload.get('club_id')))
        if not club:
            return jsonify({'error': 'club_not_found'}), 404
        if not ClubMember.query.filter_by(
            club_id=club.id, user_id=g.current_user.id,
        ).first():
            return jsonify({'error': 'members_only'}), 403

    league = League(
        name=name,
        description=str(payload.get('description') or '').strip()[:500],
        court_id=court.id,
        organizer_id=g.current_user.id,
        club=club,
        starts_at=starts_at,
        box_size=box_size,
        round_days=round_days,
        max_players=max_players,
    )
    db.session.add(league)
    db.session.add(LeagueMember(league=league, user_id=g.current_user.id))
    db.session.flush()  # notifications below need league.id

    # Club members hear about their club's league first-class.
    if club:
        for member in club.members:
            if member.user_id == g.current_user.id:
                continue
            if is_blocked_between(g.current_user.id, member.user_id):
                continue
            notify(
                member.user_id,
                'club_game',
                f'{club.name}: new box league — {name}. Signups open',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify(league.to_dict(g.current_user.id, detail=True)), 201


@leagues_bp.get('/leagues')
@login_required
def list_leagues():
    """Open and running leagues (newest start first), plus any of mine."""
    court_id = request.args.get('court_id', type=int)
    query = League.query.filter(League.status.in_(['registration', 'active']))
    if court_id:
        query = query.filter(League.court_id == court_id)
    leagues = query.order_by(League.starts_at.asc()).limit(50).all()
    mine_extra = [
        m.league for m in LeagueMember.query.filter_by(user_id=g.current_user.id)
        if m.league and m.league not in leagues
        and m.league.status != 'cancelled'  # keep completed for history, drop noise
        and (not court_id or m.league.court_id == court_id)
    ]
    return jsonify({
        'items': [
            lg.to_dict(g.current_user.id)
            for lg in leagues + sorted(mine_extra, key=lambda x: x.id, reverse=True)[:10]
        ],
    })


@leagues_bp.get('/leagues/<int:league_id>')
@login_required
def league_detail(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    data = league.to_dict(g.current_user.id, detail=True)
    if league.member_for(g.current_user.id):
        from backend.models import LeagueChatRead, Message
        marker = LeagueChatRead.query.filter_by(
            user_id=g.current_user.id, league_id=league.id,
        ).first()
        data['chat_unread'] = Message.query.filter(
            Message.league_id == league.id,
            Message.id > (marker.last_read_message_id if marker else 0),
            Message.sender_id != g.current_user.id,
        ).count()
    return jsonify(data)


@leagues_bp.post('/leagues/<int:league_id>/join')
@rate_limit(30, 3600)
@login_required
def join_league(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 400
    if league.member_for(g.current_user.id):
        return jsonify({'error': 'already_joined'}), 400
    if len(league.members) >= league.max_players:
        return jsonify({'error': 'league_full'}), 400
    if is_blocked_between(g.current_user.id, league.organizer_id):
        return jsonify({'error': 'cannot_join'}), 403
    db.session.add(LeagueMember(league=league, user_id=g.current_user.id))
    if league.organizer_id != g.current_user.id:
        notify(
            league.organizer_id,
            'league_update',
            f'{g.current_user.display_name} joined {league.name}',
            related_user_id=g.current_user.id,
            related_league_id=league.id,
        )
    db.session.commit()
    return jsonify(league.to_dict(g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/leave')
@rate_limit(30, 3600)
@login_required
def leave_league(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.status != 'registration':
        return jsonify({'error': 'league_already_started'}), 400
    if league.organizer_id == g.current_user.id:
        return jsonify({'error': 'organizer_must_cancel'}), 400
    member = league.member_for(g.current_user.id)
    if not member:
        return jsonify({'error': 'not_a_member'}), 400
    league.members.remove(member)  # delete-orphan keeps the collection in sync
    db.session.commit()
    return jsonify({'left': True})


@leagues_bp.post('/leagues/<int:league_id>/start')
@rate_limit(10, 3600)
@login_required
def start_league(league_id):
    """Seed boxes by rating and generate round 1."""
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'registration':
        return jsonify({'error': 'already_started'}), 400
    if len(league.members) < MIN_PLAYERS:
        return jsonify({'error': 'need_more_players'}), 400

    seeded = sorted(
        league.members,
        key=lambda m: -(m.user.rating if m.user else 0),
    )
    # Chunk into boxes; a too-small trailing box folds into the previous one.
    boxes = [seeded[i:i + league.box_size] for i in range(0, len(seeded), league.box_size)]
    if len(boxes) > 1 and len(boxes[-1]) < MIN_PLAYERS:
        boxes[-2].extend(boxes.pop())
    for box_number, box_members in enumerate(boxes, start=1):
        for member in box_members:
            member.box = box_number

    league.status = 'active'
    league.current_round = 1
    league.round_started_at = utcnow()
    _generate_round(league)

    for member in league.members:
        if member.user_id != g.current_user.id:
            notify(
                member.user_id,
                'league_update',
                f'{league.name} has started — you are in box {member.box}',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify(league.to_dict(g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/score')
@rate_limit(30, 60)
@login_required
def report_match(league_id, match_id):
    """Either player reports; win = 3 points, loss = 1 (played > sat out)."""
    league, err = _league_or_404(league_id)
    if err:
        return err
    match = db.session.get(LeagueMatch, match_id)
    if not match or match.league_id != league.id:
        return jsonify({'error': 'match_not_found'}), 404
    if league.status != 'active' or match.round != league.current_round:
        return jsonify({'error': 'round_closed'}), 400
    if g.current_user.id not in (match.player1_id, match.player2_id):
        return jsonify({'error': 'players_only'}), 403
    if match.winner_id is not None:
        return jsonify({'error': 'already_reported'}), 400

    payload = request.get_json(silent=True) or {}
    try:
        score1 = int(payload.get('score1'))
        score2 = int(payload.get('score2'))
    except (TypeError, ValueError):
        return jsonify({'error': 'scores_required'}), 400
    if score1 == score2 or score1 < 0 or score2 < 0 or max(score1, score2) > 99:
        return jsonify({'error': 'invalid_scores'}), 400

    match.score1 = score1
    match.score2 = score2
    match.winner_id = match.player1_id if score1 > score2 else match.player2_id
    match.reported_by_id = g.current_user.id

    loser_id = match.player2_id if match.winner_id == match.player1_id else match.player1_id
    winner = league.member_for(match.winner_id)
    loser = league.member_for(loser_id)
    if winner:
        winner.points += 3
        winner.wins += 1
    if loser:
        loser.points += 1
        loser.losses += 1

    opponent_id = (
        match.player2_id if g.current_user.id == match.player1_id else match.player1_id
    )
    notify(
        opponent_id,
        'league_match',
        f'{g.current_user.display_name} recorded your {league.name} match: {score1}–{score2}',
        related_user_id=g.current_user.id,
        related_league_id=league.id,
    )
    db.session.commit()
    return jsonify(match.to_dict())


def _do_advance(league, actor_id=None):
    """Close the round: box winners move up, last place moves down, next
    round's matches are generated. Unplayed matches simply score no points."""
    boxes = _boxes_of(league)
    box_numbers = sorted(boxes)
    for box_number in box_numbers:
        standing = boxes[box_number]
        if box_number > box_numbers[0] and standing:
            standing[0].box = box_number - 1          # winner moves up
        if box_number < box_numbers[-1] and len(standing) > 1:
            standing[-1].box = box_number + 1         # last place drops
    league.current_round += 1
    league.round_started_at = utcnow()
    _generate_round(league)

    for member in league.members:
        if member.user_id != actor_id:
            notify(
                member.user_id,
                'league_update',
                f'{league.name}: round {league.current_round} is up — you are in box {member.box}',
                related_user_id=actor_id,
                related_league_id=league.id,
            )


def advance_due_league_rounds():
    """Lazy sweep (runs on /me reads, like tournament reminders): once a
    round's window has elapsed, close it exactly like the organizer button
    would — one round per sweep so a dormant league doesn't fast-forward.
    Also nags members who still have unplayed matches when the round enters
    its final two days (once per round)."""
    from datetime import timedelta
    now = utcnow()
    for league in League.query.filter_by(status='active').all():
        if not league.round_started_at:
            league.round_started_at = now  # legacy rows from before this column
            continue
        deadline = league.round_started_at + timedelta(days=league.round_days)
        if now >= deadline:
            _do_advance(league, actor_id=None)
            continue
        if now >= deadline - timedelta(days=2):
            days_left = max(1, (deadline - now).days + (1 if (deadline - now).seconds else 0))
            unplayed_by_user = {}
            for match in league.matches:
                if match.round == league.current_round and match.winner_id is None:
                    unplayed_by_user.setdefault(match.player1_id, 0)
                    unplayed_by_user.setdefault(match.player2_id, 0)
                    unplayed_by_user[match.player1_id] += 1
                    unplayed_by_user[match.player2_id] += 1
            for member in league.members:
                pending = unplayed_by_user.get(member.user_id, 0)
                if not pending or member.reminded_round >= league.current_round:
                    continue
                member.reminded_round = league.current_round
                notify(
                    member.user_id,
                    'league_match',
                    f'{league.name}: {days_left} day{"" if days_left == 1 else "s"} left '
                    f'to play your {pending} box match{"" if pending == 1 else "es"}',
                    related_league_id=league.id,
                )
    db.session.commit()


@leagues_bp.post('/leagues/<int:league_id>/advance')
@rate_limit(10, 3600)
@login_required
def advance_round(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'not_active'}), 400
    _do_advance(league, actor_id=g.current_user.id)
    db.session.commit()
    return jsonify(league.to_dict(g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/complete')
@rate_limit(10, 3600)
@login_required
def complete_league(league_id):
    """End the season: whoever tops box 1 is champion."""
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'not_active'}), 400

    league.status = 'completed'
    league.completed_at = utcnow()
    boxes = _boxes_of(league)
    champion = boxes[min(boxes)][0] if boxes else None
    # Assign the relationship, not the FK — champion_name serializes in this
    # same request.
    league.champion = champion.user if champion else None
    for member in league.members:
        if member.user_id == g.current_user.id:
            continue
        is_champ = champion and member.user_id == champion.user_id
        notify(
            member.user_id,
            'league_update',
            (f'You won {league.name}! Champion of the season'
             if is_champ else f'{league.name} has wrapped up — thanks for playing'),
            related_user_id=g.current_user.id,
            related_league_id=league.id,
        )
    db.session.commit()
    data = league.to_dict(g.current_user.id, detail=True)
    data['champion'] = champion.to_dict() if champion else None
    return jsonify(data)


@leagues_bp.post('/leagues/<int:league_id>/cancel')
@rate_limit(10, 3600)
@login_required
def cancel_league(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status in ('completed', 'cancelled'):
        return jsonify({'error': 'already_finished'}), 400
    league.status = 'cancelled'
    for member in league.members:
        if member.user_id != g.current_user.id:
            notify(
                member.user_id,
                'league_update',
                f'{league.name} was cancelled',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify({'cancelled': True})


@leagues_bp.get('/leagues/<int:league_id>/chat')
@login_required
def league_chat(league_id):
    from backend.models import LeagueChatRead, Message
    league, err = _league_or_404(league_id)
    if err:
        return err
    if not league.member_for(g.current_user.id):
        return jsonify({'error': 'members_only'}), 403
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.league_id == league_id)
    if since_id:
        messages = query.filter(Message.id > since_id).order_by(Message.id.asc()).all()
    else:
        messages = list(reversed(query.order_by(Message.id.desc()).limit(60).all()))

    # Reading the room marks it read — powers the league-screen badge.
    latest_id = db.session.query(db.func.max(Message.id)).filter(
        Message.league_id == league_id,
    ).scalar() or 0
    marker = LeagueChatRead.query.filter_by(
        user_id=g.current_user.id, league_id=league.id,
    ).first()
    if not marker:
        db.session.add(LeagueChatRead(
            user_id=g.current_user.id, league_id=league.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'league': {'id': league.id, 'name': league.name},
        'items': [m.to_dict() for m in messages],
    })


@leagues_bp.post('/leagues/<int:league_id>/chat')
@rate_limit(60, 60)
@login_required
def send_league_message(league_id):
    from backend.models import Message, Notification
    league, err = _league_or_404(league_id)
    if err:
        return err
    if not league.member_for(g.current_user.id):
        return jsonify({'error': 'members_only'}), 403
    payload = request.get_json(silent=True) or {}
    body = str(payload.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'message_body_required'}), 400
    message = Message(sender_id=g.current_user.id, league_id=league.id, body=body[:2000])
    db.session.add(message)

    # One unread ping per league per member, mirroring the other room chats.
    for member in league.members:
        if member.user_id == g.current_user.id:
            continue
        already_pinged = Notification.query.filter_by(
            user_id=member.user_id,
            kind='league_message',
            related_league_id=league.id,
            read=False,
        ).first()
        if not already_pinged:
            notify(
                member.user_id,
                'league_message',
                f'{g.current_user.display_name} in {league.name}',
                body[:140],
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify(message.to_dict()), 201
