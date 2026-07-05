"""Clubs — player-created groups with a member roster, an optional home
court, and a members-only chat room."""
from flask import Blueprint, g, jsonify, request

from backend.app import db
from backend.models import (
    Club, ClubChatRead, ClubMember, Court, Message, Notification,
    is_blocked_between, notify,
)
from backend.security import rate_limit

clubs_bp = Blueprint('clubs', __name__)

from backend.routes.auth import login_required  # noqa: E402

MAX_CLUBS_OWNED = 5


def _club_or_404(club_id):
    club = db.session.get(Club, club_id)
    if not club:
        return None, (jsonify({'error': 'club_not_found'}), 404)
    return club, None


def _membership(club):
    return ClubMember.query.filter_by(
        club_id=club.id, user_id=g.current_user.id,
    ).first()


def _club_payload(club, membership=None):
    data = club.to_dict()
    data['joined'] = membership is not None
    data['my_role'] = membership.role if membership else None
    return data


@clubs_bp.post('/clubs')
@rate_limit(5, 3600)
@login_required
def create_club():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    description = str(payload.get('description') or '').strip()
    if len(name) < 3 or len(name) > 80:
        return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400

    home_court_id = payload.get('home_court_id')
    if home_court_id is not None:
        home_court_id = int(home_court_id)
        if not db.session.get(Court, home_court_id):
            return jsonify({'error': 'court_not_found'}), 404

    owned = ClubMember.query.filter_by(
        user_id=g.current_user.id, role='owner',
    ).count()
    if owned >= MAX_CLUBS_OWNED:
        return jsonify({'error': 'too_many_clubs_owned'}), 400

    club = Club(
        name=name,
        description=description[:500],
        creator_id=g.current_user.id,
        home_court_id=home_court_id,
    )
    db.session.add(club)
    db.session.add(ClubMember(club=club, user_id=g.current_user.id, role='owner'))
    db.session.commit()
    return jsonify(_club_payload(club, membership=club.members[0])), 201


@clubs_bp.get('/clubs')
@login_required
def list_clubs():
    """Find clubs: by name/description search, by home court, or the most
    popular ones when browsing with no filters."""
    q = str(request.args.get('q') or '').strip()
    court_id = request.args.get('court_id', type=int)

    query = Club.query
    if court_id:
        query = query.filter(Club.home_court_id == court_id)
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            Club.name.ilike(like), Club.description.ilike(like),
        ))
    clubs = query.limit(200).all()
    clubs.sort(key=lambda c: (-len(c.members), c.id))
    clubs = clubs[:25]

    mine = {
        m.club_id: m for m in ClubMember.query.filter_by(
            user_id=g.current_user.id,
        ).all()
    }
    return jsonify({'items': [_club_payload(c, mine.get(c.id)) for c in clubs]})


@clubs_bp.get('/clubs/mine')
@login_required
def my_clubs():
    """My clubs for the Chat tab — each with last message + unread count."""
    me = g.current_user.id
    memberships = ClubMember.query.filter_by(user_id=me).all()
    markers = {
        r.club_id: r.last_read_message_id
        for r in ClubChatRead.query.filter_by(user_id=me).all()
    }
    items = []
    for membership in memberships:
        club = membership.club
        last = (
            Message.query.filter(Message.club_id == club.id)
            .order_by(Message.id.desc())
            .first()
        )
        unread = Message.query.filter(
            Message.club_id == club.id,
            Message.id > markers.get(club.id, 0),
            Message.sender_id != me,
        ).count() if last else 0
        data = _club_payload(club, membership)
        data['last_message'] = last.to_dict() if last else None
        data['unread'] = unread
        items.append(data)
    items.sort(key=lambda i: -(i['last_message']['id'] if i['last_message'] else 0))
    return jsonify({'items': items})


@clubs_bp.get('/clubs/<int:club_id>')
@login_required
def club_detail(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    members = sorted(club.members, key=lambda m: (m.role != 'owner', m.created_at, m.id))
    data = _club_payload(club, _membership(club))

    # Per-member record in completed club games — powers the leaderboard.
    from backend.models import Game, utcnow
    club_record = {}
    finished = Game.query.filter(
        Game.club_id == club.id, Game.status == 'completed',
    ).all()
    for game in finished:
        if game.score_team1 is None or game.score_team2 is None:
            continue
        winning_team = 1 if game.score_team1 > game.score_team2 else 2
        for player in game.players:
            if not player.team:
                continue
            record = club_record.setdefault(player.user_id, [0, 0])
            record[0 if player.team == winning_team else 1] += 1

    data['members'] = [
        {
            **m.user.to_public_dict(),
            'role': m.role,
            'club_wins': club_record.get(m.user_id, [0, 0])[0],
            'club_losses': club_record.get(m.user_id, [0, 0])[1],
        }
        for m in members
        if m.user and not m.user.deleted_at
    ]

    # The club's next few games — hosted under the club banner.
    upcoming = (
        Game.query.filter(
            Game.club_id == club.id,
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow(),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(5)
        .all()
    )
    data['upcoming_games'] = [
        game.to_dict(g.current_user.id) for game in upcoming
    ]

    # Club tournaments still open or underway.
    from backend.models import Tournament
    tournaments = (
        Tournament.query.filter(
            Tournament.club_id == club.id,
            Tournament.status.in_(['registration', 'active']),
        )
        .order_by(Tournament.starts_at.asc())
        .limit(5)
        .all()
    )
    data['tournaments'] = [t.to_dict(g.current_user.id) for t in tournaments]

    # Club box leagues still open or underway.
    from backend.models import League
    leagues = (
        League.query.filter(
            League.club_id == club.id,
            League.status.in_(['registration', 'active']),
        )
        .order_by(League.starts_at.asc())
        .limit(5)
        .all()
    )
    data['leagues'] = [lg.to_dict(g.current_user.id) for lg in leagues]
    return jsonify(data)


@clubs_bp.patch('/clubs/<int:club_id>')
@rate_limit(20, 3600)
@login_required
def edit_club(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not membership or membership.role != 'owner':
        return jsonify({'error': 'owner_only'}), 403

    payload = request.get_json(silent=True) or {}
    if 'name' in payload:
        name = str(payload.get('name') or '').strip()
        if len(name) < 3 or len(name) > 80:
            return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400
        club.name = name
    if 'description' in payload:
        club.description = str(payload.get('description') or '').strip()[:500]
    if 'home_court_id' in payload:
        home_court_id = payload.get('home_court_id')
        if home_court_id is not None:
            home_court_id = int(home_court_id)
            if not db.session.get(Court, home_court_id):
                return jsonify({'error': 'court_not_found'}), 404
        club.home_court_id = home_court_id
    db.session.commit()
    return jsonify(_club_payload(club, membership))


def _delete_club(club):
    """Remove a club and everything hanging off it."""
    Message.query.filter_by(club_id=club.id).delete(synchronize_session=False)
    ClubChatRead.query.filter_by(club_id=club.id).delete(synchronize_session=False)
    Notification.query.filter_by(related_club_id=club.id).delete(synchronize_session=False)
    db.session.delete(club)  # members go with it (delete-orphan cascade)


@clubs_bp.delete('/clubs/<int:club_id>')
@rate_limit(10, 3600)
@login_required
def delete_club(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not membership or membership.role != 'owner':
        return jsonify({'error': 'owner_only'}), 403
    name = club.name
    for member in club.members:
        if member.user_id != g.current_user.id:
            notify(
                member.user_id,
                'club_update',
                f'{name} was disbanded',
                related_user_id=g.current_user.id,
            )
    _delete_club(club)
    db.session.commit()
    return jsonify({'deleted': True})


@clubs_bp.post('/clubs/<int:club_id>/join')
@rate_limit(30, 3600)
@login_required
def join_club(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if _membership(club):
        return jsonify({'error': 'already_member'}), 400
    owner = next((m for m in club.members if m.role == 'owner'), None)
    if owner and is_blocked_between(g.current_user.id, owner.user_id):
        return jsonify({'error': 'cannot_join'}), 403

    # Assign the relationship, not the FK — keeps club.members in sync for
    # the payload below (and for any cached instance of this club).
    db.session.add(ClubMember(club=club, user_id=g.current_user.id))
    if owner:
        notify(
            owner.user_id,
            'club_join',
            f'{g.current_user.display_name} joined {club.name}',
            related_user_id=g.current_user.id,
            related_club_id=club.id,
        )
    db.session.commit()
    return jsonify(_club_payload(club, _membership(club)))


@clubs_bp.post('/clubs/<int:club_id>/leave')
@rate_limit(30, 3600)
@login_required
def leave_club(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not membership:
        return jsonify({'error': 'not_a_member'}), 400

    if membership.role == 'owner':
        # Hand the club to its longest-standing member; disband if empty.
        others = sorted(
            (m for m in club.members if m.user_id != g.current_user.id),
            key=lambda m: (m.created_at, m.id),
        )
        if others:
            others[0].role = 'owner'
            notify(
                others[0].user_id,
                'club_update',
                f'You now run {club.name}',
                related_user_id=g.current_user.id,
                related_club_id=club.id,
            )
        else:
            _delete_club(club)
            db.session.commit()
            return jsonify({'left': True, 'deleted': True})
    # Remove through the relationship (delete-orphan) so club.members stays
    # in sync — session.delete() would leave the cached collection stale.
    club.members.remove(membership)
    ClubChatRead.query.filter_by(
        club_id=club.id, user_id=g.current_user.id,
    ).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'left': True})


@clubs_bp.post('/clubs/<int:club_id>/remove')
@rate_limit(30, 3600)
@login_required
def remove_member(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not membership or membership.role != 'owner':
        return jsonify({'error': 'owner_only'}), 403
    payload = request.get_json(silent=True) or {}
    target_id = int(payload.get('user_id') or 0)
    if target_id == g.current_user.id:
        return jsonify({'error': 'use_leave_instead'}), 400
    target = ClubMember.query.filter_by(club_id=club.id, user_id=target_id).first()
    if not target:
        return jsonify({'error': 'not_a_member'}), 404
    club.members.remove(target)
    ClubChatRead.query.filter_by(
        club_id=club.id, user_id=target_id,
    ).delete(synchronize_session=False)
    notify(
        target_id,
        'club_update',
        f'You were removed from {club.name}',
        related_user_id=g.current_user.id,
    )
    db.session.commit()
    return jsonify({'removed': True})


@clubs_bp.post('/clubs/<int:club_id>/invite')
@rate_limit(30, 3600)
@login_required
def invite_to_club(club_id):
    """Any member can invite a friend — the invite is a tappable notification
    that deep-links to the club's join screen."""
    from backend.models import User
    from backend.routes.social import friend_ids

    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _membership(club):
        return jsonify({'error': 'members_only'}), 403
    payload = request.get_json(silent=True) or {}
    target_id = int(payload.get('user_id') or 0)
    target = db.session.get(User, target_id)
    if not target or target.deleted_at:
        return jsonify({'error': 'user_not_found'}), 404
    if target_id in club.member_ids():
        return jsonify({'error': 'already_member'}), 400
    if target_id not in friend_ids(g.current_user.id):
        return jsonify({'error': 'friends_only'}), 403

    # One pending invite per club per player — re-inviting is a no-op.
    already = Notification.query.filter_by(
        user_id=target_id, kind='club_invite',
        related_club_id=club.id, read=False,
    ).first()
    if not already:
        notify(
            target_id,
            'club_invite',
            f'{g.current_user.display_name} invited you to join {club.name}',
            club.description[:140],
            related_user_id=g.current_user.id,
            related_club_id=club.id,
        )
        db.session.commit()
    return jsonify({'invited': True})


@clubs_bp.get('/clubs/<int:club_id>/chat')
@login_required
def club_chat(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _membership(club):
        return jsonify({'error': 'members_only'}), 403
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.club_id == club_id)
    if since_id:
        messages = query.filter(Message.id > since_id).order_by(Message.id.asc()).all()
    else:
        messages = list(reversed(query.order_by(Message.id.desc()).limit(60).all()))

    # Reading the room marks it read — powers the Chat-tab unread badge.
    latest_id = db.session.query(db.func.max(Message.id)).filter(
        Message.club_id == club_id,
    ).scalar() or 0
    marker = ClubChatRead.query.filter_by(
        user_id=g.current_user.id, club_id=club.id,
    ).first()
    if not marker:
        db.session.add(ClubChatRead(
            user_id=g.current_user.id, club_id=club.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'club': {'id': club.id, 'name': club.name},
        'items': [m.to_dict() for m in messages],
    })


@clubs_bp.post('/clubs/<int:club_id>/chat')
@rate_limit(60, 60)
@login_required
def send_club_message(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _membership(club):
        return jsonify({'error': 'members_only'}), 403
    from backend.routes.chat import message_image_from
    payload = request.get_json(silent=True) or {}
    body = str(payload.get('body') or '').strip()
    image, err = message_image_from(payload)
    if err:
        return err
    if not body and not image:
        return jsonify({'error': 'message_body_required'}), 400
    message = Message(sender_id=g.current_user.id, club_id=club.id,
                      body=body[:2000], image_data=image)
    db.session.add(message)

    # Ping the other members — at most one unread ping per club per member,
    # mirroring game/tournament chat, so busy rooms don't flood the feed.
    for member in club.members:
        if member.user_id == g.current_user.id:
            continue
        already_pinged = Notification.query.filter_by(
            user_id=member.user_id,
            kind='club_message',
            related_club_id=club.id,
            read=False,
        ).first()
        if not already_pinged:
            notify(
                member.user_id,
                'club_message',
                f'{g.current_user.display_name} in {club.name}',
                body[:140],
                related_user_id=g.current_user.id,
                related_club_id=club.id,
            )
    db.session.commit()
    return jsonify(message.to_dict()), 201
