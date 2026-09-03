"""Clubs — player-created groups with a member roster, an optional home
court, and a members-only chat room."""
import math

from flask import Blueprint, g, jsonify, request
from sqlalchemy.orm import selectinload

from backend.app import db
from backend.models import (
    Club, ClubBan, ClubChatRead, ClubJoinRequest, ClubMember, Court, Game,
    League, Message, Notification, Tournament, blocked_pair_ids,
    is_blocked_between, notify, utcnow,
)
from backend.security import rate_limit
from backend.services.groups import delete_group_identity, sync_group_identity
from backend.services.conversations import conversation_ref, delete_conversation_read

clubs_bp = Blueprint('clubs', __name__)

from backend.routes.auth import login_required  # noqa: E402

MAX_CLUBS_OWNED = 5
DIGEST_EVERY_DAYS = 7
CLUB_LIST_DEFAULT_LIMIT = 25
CLUB_LIST_MAX_LIMIT = 50


def send_club_digests(limit=5):
    """Weekly per-club activity digest, swept lazily from /me.

    First sweep of a club just baselines its watermark (no day-one spam);
    after that, once a week, members get one summary ping — but only when
    the club actually had a week worth talking about. Quiet clubs stay
    quiet and merely move their watermark forward.
    """
    from datetime import timedelta

    from sqlalchemy import or_

    from backend.models import Game, utcnow

    now = utcnow()
    cutoff = now - timedelta(days=DIGEST_EVERY_DAYS)
    due = (
        Club.query
        .filter(Club.archived_at.is_(None))
        .filter(or_(Club.last_digest_at.is_(None), Club.last_digest_at <= cutoff))
        .order_by(Club.id)
        .limit(limit)
        .all()
    )
    for club in due:
        window_start = club.last_digest_at
        club.last_digest_at = now
        if window_start is None:
            continue  # baseline only — digests start a week from now

        games = Game.query.filter(
            Game.club_id == club.id,
            Game.status != 'cancelled',
            Game.scheduled_at >= window_start,
            Game.scheduled_at <= now,
        ).count()
        new_members = sum(
            1 for m in club.members
            if m.created_at and m.created_at >= window_start
        )
        if not games and not new_members:
            continue

        parts = []
        if games:
            parts.append(
                f'{games} Community play session{"" if games == 1 else "s"}'
            )
        if new_members:
            parts.append(f'{new_members} new member{"" if new_members == 1 else "s"}')
        title = f'{club.name} this week: {" and ".join(parts)}'
        for member in club.members:
            if (member.notification_level or 'all') == 'off':
                continue
            notify(member.user_id, 'club_update', title, related_club_id=club.id)
    if due:
        db.session.commit()


def _club_or_404(club_id):
    club = db.session.get(Club, club_id)
    if not club or club.archived_at is not None:
        return None, (jsonify({'error': 'club_not_found'}), 404)
    return club, None


def _membership(club):
    return ClubMember.query.filter_by(
        club_id=club.id, user_id=g.current_user.id,
    ).first()


def _is_manager(membership):
    return bool(membership and membership.role in ('owner', 'admin'))


def _pending_join_request(club_id, user_id):
    return ClubJoinRequest.query.filter_by(
        club_id=club_id, user_id=user_id, status='pending',
    ).first()


def _club_payload(club, membership=None):
    data = club.to_dict()
    data['joined'] = membership is not None
    data['my_role'] = membership.role if membership else None
    data['my_notification_level'] = (
        membership.notification_level if membership else None
    )
    data['can_manage'] = _is_manager(membership)
    return data


@clubs_bp.post('/clubs')
@rate_limit(5, 3600)
@login_required
def create_club():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    description = str(payload.get('description') or '').strip()
    join_policy = str(payload.get('join_policy') or 'open').strip().lower()
    if len(name) < 3 or len(name) > 80:
        return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400
    if join_policy not in ('open', 'request'):
        return jsonify({'error': 'invalid_join_policy'}), 400

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
        join_policy=join_policy,
        creator_id=g.current_user.id,
        home_court_id=home_court_id,
    )
    db.session.add(club)
    db.session.add(ClubMember(club=club, user_id=g.current_user.id, role='owner'))
    db.session.flush()
    sync_group_identity('club', club)
    db.session.commit()
    return jsonify(_club_payload(club, membership=club.members[0])), 201


@clubs_bp.get('/clubs')
@login_required
def list_clubs():
    """Find clubs: by name/description search, by home court, or the most
    popular ones when browsing with no filters."""
    q = str(request.args.get('q') or '').strip()
    court_id = request.args.get('court_id', type=int)
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if (lat is None) != (lng is None):
        return jsonify({'error': 'lat_and_lng_required_together'}), 400
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({'error': 'invalid_coordinates'}), 400
    page = max(1, request.args.get('page', default=1, type=int) or 1)
    limit = min(
        CLUB_LIST_MAX_LIMIT,
        max(
            1,
            request.args.get(
                'limit', default=CLUB_LIST_DEFAULT_LIMIT, type=int,
            ) or CLUB_LIST_DEFAULT_LIMIT,
        ),
    )

    query = Club.query.filter(Club.archived_at.is_(None))
    if court_id:
        query = query.filter(Club.home_court_id == court_id)
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            Club.name.ilike(like), Club.description.ilike(like),
        ))
    total = query.count()

    # Rank in SQL before slicing so a large Community directory does not lose
    # popular results after an arbitrary 200-row pre-cap. Select-in loading
    # keeps serialization to a fixed number of queries per page.
    member_counts = (
        db.session.query(
            ClubMember.club_id.label('club_id'),
            db.func.count(ClubMember.id).label('member_count'),
        )
        .group_by(ClubMember.club_id)
        .subquery()
    )
    popularity = db.func.coalesce(member_counts.c.member_count, 0)
    offset = (page - 1) * limit
    ranked = query.outerjoin(
        member_counts, member_counts.c.club_id == Club.id,
    )
    ordering = [popularity.desc(), Club.id.asc()]
    if lat is not None:
        # A portable squared-degree expression is sufficient for SQL paging;
        # the response exposes exact haversine distance below. Longitude is
        # scaled at the requested latitude so east/west ordering stays useful.
        lng_scale = max(0.05, math.cos(math.radians(lat)))
        ranked = ranked.outerjoin(Court, Club.home_court_id == Court.id)
        lat_delta = Court.latitude - lat
        lng_delta = (Court.longitude - lng) * lng_scale
        distance_rank = lat_delta * lat_delta + lng_delta * lng_delta
        ordering = [
            db.case((Court.latitude.is_(None), 1), else_=0),
            distance_rank.asc(),
            popularity.desc(),
            Club.id.asc(),
        ]
    page_rows = (
        ranked
        .options(selectinload(Club.members), selectinload(Club.home_court))
        .order_by(*ordering)
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    has_more = len(page_rows) > limit
    clubs = page_rows[:limit]

    club_ids = [club.id for club in clubs]
    mine = {
        m.club_id: m for m in ClubMember.query.filter_by(
            user_id=g.current_user.id,
        ).filter(ClubMember.club_id.in_(club_ids)).all()
    }
    items = []
    if lat is not None:
        from backend.routes.courts import haversine_miles
    for club in clubs:
        item = _club_payload(club, mine.get(club.id))
        if (
            lat is not None and club.home_court
            and club.home_court.latitude is not None
            and club.home_court.longitude is not None
        ):
            item['distance_miles'] = round(haversine_miles(
                lat, lng, club.home_court.latitude, club.home_court.longitude,
            ), 1)
        else:
            item['distance_miles'] = None
        items.append(item)
    return jsonify({
        'items': items,
        'count': total,
        'page': page,
        'limit': limit,
        'has_more': has_more,
        'next_page': page + 1 if has_more else None,
    })


@clubs_bp.get('/clubs/mine')
@login_required
def my_clubs():
    """My clubs for the Chat tab — each with last message + unread count."""
    me = g.current_user.id
    memberships = (
        ClubMember.query.filter_by(user_id=me)
        .join(Club, ClubMember.club_id == Club.id)
        .filter(Club.archived_at.is_(None))
        .options(
            selectinload(ClubMember.club).selectinload(Club.members),
            selectinload(ClubMember.club).selectinload(Club.home_court),
            selectinload(ClubMember.club).selectinload(
                Club.announcement_author,
            ),
        )
        .all()
    )
    club_ids = [membership.club_id for membership in memberships]
    if not club_ids:
        return jsonify({'items': []})

    last_ids = dict(
        db.session.query(
            Message.club_id, db.func.max(Message.id),
        )
        .filter(Message.club_id.in_(club_ids))
        .group_by(Message.club_id)
        .all()
    )
    last_messages = {
        message.club_id: message
        for message in Message.query.filter(
            Message.id.in_(list(last_ids.values())),
        ).options(
            selectinload(Message.sender), selectinload(Message.hearts),
        ).all()
    } if last_ids else {}
    unread_counts = dict(
        db.session.query(Message.club_id, db.func.count(Message.id))
        .outerjoin(
            ClubChatRead,
            db.and_(
                ClubChatRead.club_id == Message.club_id,
                ClubChatRead.user_id == me,
            ),
        )
        .filter(
            Message.club_id.in_(club_ids),
            Message.sender_id != me,
            Message.id > db.func.coalesce(
                ClubChatRead.last_read_message_id, 0,
            ),
        )
        .group_by(Message.club_id)
        .all()
    )
    pending_counts = dict(
        db.session.query(
            ClubJoinRequest.club_id, db.func.count(ClubJoinRequest.id),
        )
        .filter(
            ClubJoinRequest.club_id.in_(club_ids),
            ClubJoinRequest.status == 'pending',
        )
        .group_by(ClubJoinRequest.club_id)
        .all()
    )
    items = []
    for membership in memberships:
        club = membership.club
        last = last_messages.get(club.id)
        data = _club_payload(club, membership)
        data['last_message'] = last.to_dict() if last else None
        data['unread'] = int(unread_counts.get(club.id, 0))
        data['pending_join_requests'] = (
            int(pending_counts.get(club.id, 0))
            if _is_manager(membership) else 0
        )
        items.append(data)
    items.sort(key=lambda i: -(i['last_message']['id'] if i['last_message'] else 0))
    return jsonify({'items': items})


@clubs_bp.get('/clubs/<int:club_id>')
@login_required
def club_detail(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    data = _club_payload(club, membership)
    pending_request = None if membership else _pending_join_request(
        club.id, g.current_user.id,
    )
    data['join_request_status'] = (
        pending_request.status if pending_request else None
    )
    data['pending_join_requests'] = (
        ClubJoinRequest.query.filter_by(
            club_id=club.id, status='pending',
        ).count()
        if _is_manager(membership) else 0
    )
    data['roster_visible'] = membership is not None
    data['members'] = []

    # The roster and each player's community-game record are member-only.
    # Public discovery still exposes the aggregate member_count from
    # Club.to_dict(), but never turns a discoverable group into a player
    # directory for unrelated signed-in accounts.
    from backend.models import Game, utcnow
    if membership is not None:
        members = sorted(
            club.members,
            key=lambda member: (
                member.role != 'owner', member.created_at, member.id,
            ),
        )
        hidden_member_ids = blocked_pair_ids(g.current_user.id)
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
                **member.user.to_public_dict(),
                'role': member.role,
                'notification_level': (
                    member.notification_level
                    if member.user_id == g.current_user.id else None
                ),
                'club_wins': club_record.get(member.user_id, [0, 0])[0],
                'club_losses': club_record.get(member.user_id, [0, 0])[1],
            }
            for member in members
            if (
                member.user and not member.user.deleted_at
                and member.user_id not in hidden_member_ids
            )
        ]

    # The club's next few games — hosted under the club banner.
    upcoming = (
        Game.query.filter(
            Game.club_id == club.id,
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow(),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(100)
        .all()
    )
    # A Club association is presentation/provenance, not a visibility grant.
    # In particular, a public Club sheet must not serialize the roster of a
    # friends-only or legacy private session to an unrelated viewer.
    from backend.routes.social import friend_ids
    from backend.routes.games import _game_has_blocked_participant
    viewer_friends = friend_ids(g.current_user.id)
    data['upcoming_games'] = [
        game.to_dict(g.current_user.id)
        for game in upcoming
        if game.visible_to(g.current_user.id, viewer_friends)
        and not _game_has_blocked_participant(game, g.current_user.id)
    ][:5]

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
    if 'join_policy' in payload:
        join_policy = str(payload.get('join_policy') or '').strip().lower()
        if join_policy not in ('open', 'request'):
            return jsonify({'error': 'invalid_join_policy'}), 400
        club.join_policy = join_policy
    if 'announcement' in payload:
        announcement = str(payload.get('announcement') or '').strip()[:500]
        changed = announcement != club.announcement
        club.announcement = announcement
        club.announcement_author_id = (
            g.current_user.id if announcement else None
        )
        club.announcement_author = g.current_user if announcement else None
        club.announcement_posted_at = utcnow() if announcement else None
        # A fresh (non-empty) notice pings the roster once.
        if changed and announcement:
            for member in club.members:
                if member.user_id == g.current_user.id:
                    continue
                if (member.notification_level or 'all') == 'off':
                    continue
                notify(member.user_id, 'club_update',
                       f'{club.name}: new announcement from the organizer',
                       related_club_id=club.id)
    if 'home_court_id' in payload:
        home_court_id = payload.get('home_court_id')
        if home_court_id is not None:
            home_court_id = int(home_court_id)
            if not db.session.get(Court, home_court_id):
                return jsonify({'error': 'court_not_found'}), 404
        club.home_court_id = home_court_id
    sync_group_identity('club', club)
    db.session.commit()
    return jsonify(_club_payload(club, membership))


@clubs_bp.post('/clubs/<int:club_id>/announcement')
@rate_limit(20, 3600)
@login_required
def post_club_announcement(club_id):
    """Publish a dated organizer announcement independently of settings."""
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not _is_manager(membership):
        return jsonify({'error': 'organizer_only'}), 403
    payload = request.get_json(silent=True) or {}
    announcement = str(payload.get('announcement') or '').strip()
    if not announcement:
        return jsonify({'error': 'announcement_required'}), 400
    if len(announcement) > 500:
        return jsonify({'error': 'announcement_too_long'}), 400
    changed = announcement != club.announcement
    club.announcement = announcement
    club.announcement_author_id = g.current_user.id
    club.announcement_author = g.current_user
    club.announcement_posted_at = utcnow()
    if changed:
        for member in club.members:
            if member.user_id == g.current_user.id:
                continue
            if (member.notification_level or 'all') == 'off':
                continue
            notify(
                member.user_id, 'club_update',
                f'{club.name}: new announcement from {g.current_user.display_name}',
                announcement[:140], related_user_id=g.current_user.id,
                related_club_id=club.id,
            )
    db.session.commit()
    return jsonify(_club_payload(club, membership))


@clubs_bp.delete('/clubs/<int:club_id>/announcement')
@rate_limit(20, 3600)
@login_required
def clear_club_announcement(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not _is_manager(membership):
        return jsonify({'error': 'organizer_only'}), 403
    club.announcement = ''
    club.announcement_author_id = None
    club.announcement_author = None
    club.announcement_posted_at = None
    db.session.commit()
    return jsonify(_club_payload(club, membership))


def _delete_club(club):
    """Permanently remove an orphaned club during account erasure only."""
    # Games and competitions remain useful after a club disbands. Clear their
    # optional foreign keys before PostgreSQL checks the club deletion.
    for model, column in (
        (Game, Game.club_id),
        (Tournament, Tournament.club_id),
        (League, League.club_id),
    ):
        model.query.filter(column == club.id).update(
            {column: None}, synchronize_session=False,
        )

    # Club-room messages, club-specific notifications, and read markers have no
    # meaning without the club. Memberships use Club.members' orphan cascade.
    Message.query.filter_by(club_id=club.id).delete(synchronize_session=False)
    ClubChatRead.query.filter_by(club_id=club.id).delete(synchronize_session=False)
    ClubJoinRequest.query.filter_by(club_id=club.id).delete(synchronize_session=False)
    ClubBan.query.filter_by(club_id=club.id).delete(synchronize_session=False)
    Notification.query.filter_by(related_club_id=club.id).delete(synchronize_session=False)
    delete_group_identity('club', club.id)
    db.session.delete(club)


def _archive_club(club):
    """Recoverably close a Community while retaining its room and history."""
    club.archived_at = utcnow()
    sync_group_identity('club', club)


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
                f'The {name} Community was closed',
                related_user_id=g.current_user.id,
            )
    _archive_club(club)
    db.session.commit()
    return jsonify({
        'deleted': True,
        'recoverable': True,
        'club_id': club.id,
    })


@clubs_bp.post('/clubs/<int:club_id>/restore')
@rate_limit(10, 3600)
@login_required
def restore_club(club_id):
    """Undo a close action. Only the owner of the archived row may restore."""
    club = db.session.get(Club, club_id)
    if not club or club.archived_at is None:
        return jsonify({'error': 'club_not_found'}), 404
    membership = ClubMember.query.filter_by(
        club_id=club.id, user_id=g.current_user.id, role='owner',
    ).first()
    if not membership:
        return jsonify({'error': 'owner_only'}), 403
    club.archived_at = None
    sync_group_identity('club', club)
    db.session.commit()
    return jsonify(_club_payload(club, membership))


@clubs_bp.post('/clubs/<int:club_id>/join')
@rate_limit(30, 3600)
@login_required
def join_club(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if _membership(club):
        return jsonify({'error': 'already_member'}), 400
    if ClubBan.query.filter_by(
        club_id=club.id, user_id=g.current_user.id,
    ).first():
        return jsonify({'error': 'cannot_join'}), 403
    owner = next((m for m in club.members if m.role == 'owner'), None)
    if owner and is_blocked_between(g.current_user.id, owner.user_id):
        return jsonify({'error': 'cannot_join'}), 403

    if (club.join_policy or 'open') == 'request':
        join_request = ClubJoinRequest.query.filter_by(
            club_id=club.id, user_id=g.current_user.id,
        ).first()
        if join_request and join_request.status == 'pending':
            data = _club_payload(club, None)
            data['join_request_status'] = 'pending'
            return jsonify(data), 202
        if not join_request:
            join_request = ClubJoinRequest(
                club_id=club.id, user_id=g.current_user.id,
            )
            db.session.add(join_request)
        else:
            join_request.status = 'pending'
            join_request.resolved_by_id = None
            join_request.resolved_at = None
        for manager in club.members:
            if manager.role not in ('owner', 'admin'):
                continue
            notify(
                manager.user_id, 'club_join',
                f'{g.current_user.display_name} asked to join {club.name}',
                related_user_id=g.current_user.id,
                related_club_id=club.id,
                action_url=f'/#club/{club.id}',
                unread_dedupe_key=(
                    f'club_join_request:{club.id}:{g.current_user.id}'
                ),
            )
        db.session.commit()
        data = _club_payload(club, None)
        data['join_request_status'] = 'pending'
        return jsonify(data), 202

    # Assign the relationship, not the FK — keeps club.members in sync for
    # the payload below (and for any cached instance of this club).
    db.session.add(ClubMember(club=club, user_id=g.current_user.id))
    if owner:
        notify(
            owner.user_id,
            'club_join',
            f'{g.current_user.display_name} joined the {club.name} Community',
            related_user_id=g.current_user.id,
            related_club_id=club.id,
        )
    db.session.commit()
    return jsonify(_club_payload(club, _membership(club)))


@clubs_bp.delete('/clubs/<int:club_id>/join-request')
@rate_limit(30, 3600)
@login_required
def cancel_club_join_request(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    join_request = _pending_join_request(club.id, g.current_user.id)
    if not join_request:
        return jsonify({'error': 'join_request_not_found'}), 404
    join_request.status = 'cancelled'
    join_request.resolved_at = utcnow()
    db.session.commit()
    return jsonify({'cancelled': True})


@clubs_bp.get('/clubs/<int:club_id>/join-requests')
@login_required
def club_join_requests(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _is_manager(_membership(club)):
        return jsonify({'error': 'organizer_only'}), 403
    rows = ClubJoinRequest.query.filter_by(
        club_id=club.id, status='pending',
    ).options(selectinload(ClubJoinRequest.user)).order_by(
        ClubJoinRequest.created_at.asc(), ClubJoinRequest.id.asc(),
    ).all()
    return jsonify({'items': [
        {
            'id': row.id,
            'player': row.user.to_public_dict(),
            'requested_at': row.created_at.isoformat() + 'Z',
        }
        for row in rows if row.user and not row.user.deleted_at
    ]})


@clubs_bp.post('/clubs/<int:club_id>/join-requests/<int:request_id>/decision')
@rate_limit(60, 3600)
@login_required
def decide_club_join_request(club_id, request_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not _is_manager(membership):
        return jsonify({'error': 'organizer_only'}), 403
    row = db.session.get(ClubJoinRequest, request_id)
    if not row or row.club_id != club.id or row.status != 'pending':
        return jsonify({'error': 'join_request_not_found'}), 404
    decision = str((request.get_json(silent=True) or {}).get(
        'decision',
    ) or '').strip().lower()
    if decision not in ('approve', 'decline'):
        return jsonify({'error': 'invalid_decision'}), 400
    if ClubBan.query.filter_by(
        club_id=club.id, user_id=row.user_id,
    ).first():
        return jsonify({'error': 'player_is_banned'}), 409
    row.status = 'approved' if decision == 'approve' else 'declined'
    row.resolved_by_id = g.current_user.id
    row.resolved_at = utcnow()
    if decision == 'approve' and not ClubMember.query.filter_by(
        club_id=club.id, user_id=row.user_id,
    ).first():
        db.session.add(ClubMember(club=club, user_id=row.user_id))
    notify(
        row.user_id, 'club_update',
        (
            f'You were approved to join {club.name}'
            if decision == 'approve'
            else f'Your request to join {club.name} was declined'
        ),
        related_user_id=g.current_user.id,
        related_club_id=club.id,
        action_url=f'/#club/{club.id}',
    )
    db.session.commit()
    return jsonify({'decision': decision, 'request_id': row.id})


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
            key=lambda m: (
                m.role != 'admin', m.created_at, m.id,
            ),
        )
        if others:
            others[0].role = 'owner'
            club.creator_id = others[0].user_id
            sync_group_identity('club', club)
            notify(
                others[0].user_id,
                'club_update',
                f'You now organize the {club.name} Community',
                related_user_id=g.current_user.id,
                related_club_id=club.id,
            )
        else:
            _archive_club(club)
            db.session.commit()
            return jsonify({
                'left': True, 'deleted': True, 'recoverable': True,
                'club_id': club.id,
            })
    # Remove through the relationship (delete-orphan) so club.members stays
    # in sync — session.delete() would leave the cached collection stale.
    club.members.remove(membership)
    ClubChatRead.query.filter_by(
        club_id=club.id, user_id=g.current_user.id,
    ).delete(synchronize_session=False)
    delete_conversation_read(
        conversation_ref('club', club.id), g.current_user.id,
    )
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
    if not _is_manager(membership):
        return jsonify({'error': 'organizer_only'}), 403
    payload = request.get_json(silent=True) or {}
    target_id = int(payload.get('user_id') or 0)
    if target_id == g.current_user.id:
        return jsonify({'error': 'use_leave_instead'}), 400
    target = ClubMember.query.filter_by(club_id=club.id, user_id=target_id).first()
    if not target:
        return jsonify({'error': 'not_a_member'}), 404
    if target.role == 'owner' or (
        membership.role == 'admin' and target.role == 'admin'
    ):
        return jsonify({'error': 'owner_only'}), 403
    club.members.remove(target)
    ClubChatRead.query.filter_by(
        club_id=club.id, user_id=target_id,
    ).delete(synchronize_session=False)
    delete_conversation_read(conversation_ref('club', club.id), target_id)
    banned = bool(payload.get('ban'))
    if banned:
        ban = ClubBan.query.filter_by(
            club_id=club.id, user_id=target_id,
        ).first()
        if not ban:
            db.session.add(ClubBan(
                club_id=club.id,
                user_id=target_id,
                banned_by_id=g.current_user.id,
                reason=str(payload.get('reason') or '').strip()[:300],
            ))
        ClubJoinRequest.query.filter_by(
            club_id=club.id, user_id=target_id, status='pending',
        ).update({
            'status': 'declined',
            'resolved_by_id': g.current_user.id,
            'resolved_at': utcnow(),
        }, synchronize_session=False)
    notify(
        target_id,
        'club_update',
        (
            f'You were removed and blocked from the {club.name} Community'
            if banned else f'You were removed from the {club.name} Community'
        ),
        related_user_id=g.current_user.id,
    )
    db.session.commit()
    return jsonify({'removed': True, 'banned': banned})


@clubs_bp.patch('/clubs/<int:club_id>/members/<int:user_id>')
@rate_limit(30, 3600)
@login_required
def set_club_member_role(club_id, user_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not membership or membership.role != 'owner':
        return jsonify({'error': 'owner_only'}), 403
    if user_id == g.current_user.id:
        return jsonify({'error': 'cannot_change_owner_role'}), 400
    target = ClubMember.query.filter_by(
        club_id=club.id, user_id=user_id,
    ).first()
    if not target:
        return jsonify({'error': 'not_a_member'}), 404
    role = str((request.get_json(silent=True) or {}).get('role') or '').lower()
    if role not in ('admin', 'member'):
        return jsonify({'error': 'invalid_club_role'}), 400
    target.role = role
    notify(
        target.user_id, 'club_update',
        (
            f'You are now an organizer for {club.name}'
            if role == 'admin'
            else f'Your organizer role in {club.name} ended'
        ),
        related_user_id=g.current_user.id,
        related_club_id=club.id,
    )
    db.session.commit()
    return jsonify({'user_id': target.user_id, 'role': target.role})


@clubs_bp.get('/clubs/<int:club_id>/bans')
@login_required
def club_bans(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _is_manager(_membership(club)):
        return jsonify({'error': 'organizer_only'}), 403
    bans = ClubBan.query.filter_by(club_id=club.id).options(
        selectinload(ClubBan.user),
    ).order_by(ClubBan.created_at.desc()).all()
    return jsonify({'items': [
        {
            'user': row.user.to_public_dict(),
            'reason': row.reason,
            'created_at': row.created_at.isoformat() + 'Z',
        }
        for row in bans if row.user and not row.user.deleted_at
    ]})


@clubs_bp.delete('/clubs/<int:club_id>/bans/<int:user_id>')
@rate_limit(30, 3600)
@login_required
def unban_club_member(club_id, user_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _is_manager(_membership(club)):
        return jsonify({'error': 'organizer_only'}), 403
    ban = ClubBan.query.filter_by(club_id=club.id, user_id=user_id).first()
    if not ban:
        return jsonify({'error': 'ban_not_found'}), 404
    db.session.delete(ban)
    db.session.commit()
    return jsonify({'unbanned': True})


@clubs_bp.post('/clubs/<int:club_id>/invite')
@rate_limit(30, 3600)
@login_required
def invite_to_club(club_id):
    """Any member can invite another visible player.

    A friendship is intentionally not required: Communities are a discovery
    surface and the invite itself never enrolls the recipient.
    """
    from backend.models import User

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
    if is_blocked_between(g.current_user.id, target_id):
        return jsonify({'error': 'cannot_invite'}), 403
    if ClubBan.query.filter_by(club_id=club.id, user_id=target_id).first():
        return jsonify({'error': 'player_is_banned'}), 409

    # One pending invite per club per player — re-inviting is a no-op.
    already = Notification.query.filter_by(
        user_id=target_id, kind='club_invite',
        related_club_id=club.id, read=False,
    ).first()
    if not already:
        notify(
            target_id,
            'club_invite',
            f'{g.current_user.display_name} invited you to join the {club.name} Community',
            club.description[:140],
            related_user_id=g.current_user.id,
            related_club_id=club.id,
        )
        db.session.commit()
    return jsonify({'invited': True})


@clubs_bp.get('/clubs/<int:club_id>/chat')
@login_required
def club_chat(club_id):
    from backend.services.conversations import (
        advance_conversation_read, conversation_ref,
    )
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _membership(club):
        return jsonify({'error': 'members_only'}), 403
    conversation = conversation_ref('club', club.id)
    query = conversation.message_query()
    from backend.routes.chat import (
        chat_messages_window, chat_read_marker_target, chat_window_args,
    )
    window, window_error = chat_window_args(initial_limit=60)
    if window_error:
        return window_error
    since_id, before_id, history_limit = window
    messages, has_more, has_older, next_before_id = chat_messages_window(
        query, since_id, before_id,
        initial_limit=60, history_limit=history_limit,
    )

    # Reading the room marks it read — powers the Chat-tab unread badge.
    latest_id = chat_read_marker_target(
        query, messages, since_id, before_id, has_more,
    )
    advance_conversation_read(conversation, g.current_user.id, latest_id)
    db.session.commit()

    from backend.routes.chat import room_heart_counts
    return jsonify({
        'conversation': conversation.to_dict(club.name),
        'club': {'id': club.id, 'name': club.name},
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('club_id', club_id),
        'has_more': has_more,
        'has_older': has_older,
        'next_before_id': next_before_id,
    })


@clubs_bp.patch('/clubs/<int:club_id>/notification-settings')
@rate_limit(60, 3600)
@login_required
def club_notification_settings(club_id):
    club, err = _club_or_404(club_id)
    if err:
        return err
    membership = _membership(club)
    if not membership:
        return jsonify({'error': 'members_only'}), 403
    level = str((request.get_json(silent=True) or {}).get(
        'level',
    ) or '').strip().lower()
    if level not in ('all', 'mentions', 'off'):
        return jsonify({'error': 'invalid_notification_level'}), 400
    membership.notification_level = level
    db.session.commit()
    return jsonify({'level': level})


def _club_message_should_notify(member, body):
    level = member.notification_level or 'all'
    if level == 'off':
        return False
    if level == 'all':
        return True
    if level != 'mentions' or not member.user:
        return False
    lowered = body.casefold()
    display_name = (member.user.display_name or '').strip().casefold()
    first_name = display_name.split()[0] if display_name else ''
    return bool(
        (display_name and f'@{display_name}' in lowered)
        or (first_name and f'@{first_name}' in lowered)
    )


@clubs_bp.post('/clubs/<int:club_id>/chat')
@rate_limit(60, 60)
@login_required
def send_club_message(club_id):
    from backend.services.conversations import conversation_ref
    club, err = _club_or_404(club_id)
    if err:
        return err
    if not _membership(club):
        return jsonify({'error': 'members_only'}), 403
    conversation = conversation_ref('club', club.id)
    from backend.routes.chat import prepare_chat_message
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        conversation=conversation,
    )
    if err:
        return err
    if replayed:
        return jsonify(conversation.decorate_message(message, club.name)), 200

    # Ping the other members — at most one unread ping per club per member,
    # mirroring game/tournament chat, so busy rooms don't flood the feed.
    for member in club.members:
        if member.user_id == g.current_user.id:
            continue
        if not _club_message_should_notify(member, body):
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
                f'{g.current_user.display_name} in the {club.name} Community',
                body[:140],
                related_user_id=g.current_user.id,
                related_club_id=club.id,
                unread_dedupe_key=f'club_message:{club.id}',
            )
    db.session.commit()
    return jsonify(conversation.decorate_message(message, club.name)), 201
