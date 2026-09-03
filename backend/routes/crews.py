"""Private, consent-based crews for recurring groups of local players."""
from datetime import timedelta

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload

from backend.app import db
from backend.models import (
    Court, Crew, CrewChatRead, CrewInvite, CrewMember, Friendship, Game,
    Message, Notification, User, blocked_pair_ids, is_blocked_between, notify,
    utcnow,
)
from backend.routes.auth import _lock_users_for_update, login_required
from backend.security import rate_limit
from backend.services.groups import sync_group_identity
from backend.services.conversations import conversation_ref, delete_conversation_read


crews_bp = Blueprint('crews', __name__)
MAX_CREW_SIZE = 12


def _active_crew(crew_id, lock=False):
    query = Crew.query.filter(Crew.id == crew_id, Crew.archived_at.is_(None))
    if lock:
        query = query.with_for_update()
    return query.first()


def _member_ids(crew):
    return crew.member_ids()


def _is_member(crew, user_id):
    return user_id in _member_ids(crew)


def _member_crew_or_404(crew_id, lock=False):
    crew = _active_crew(crew_id, lock=lock)
    if not crew or not _is_member(crew, g.current_user.id):
        return None, (jsonify({'error': 'crew_not_found'}), 404)
    return crew, None


def _crew_related_user_ids(crew_id):
    """Return the complete User closure whose state can affect a Crew write."""
    owner_id = db.session.query(Crew.owner_id).filter(
        Crew.id == crew_id,
    ).scalar()
    if owner_id is None:
        return set()
    user_ids = {owner_id}
    user_ids.update(
        row[0] for row in db.session.query(CrewMember.user_id).filter(
            CrewMember.crew_id == crew_id,
        ).all()
    )
    for invitee_id, invited_by_id in db.session.query(
        CrewInvite.invitee_id, CrewInvite.invited_by_id,
    ).filter(CrewInvite.crew_id == crew_id).all():
        user_ids.update((invitee_id, invited_by_id))
    return user_ids


def _active_crew_after_user_locks(
        crew_id, additional_user_ids=(), max_attempts=5):
    """Lock every Crew-related User in canonical order, then the Crew row.

    Block reconciliation uses the same User-before-Crew order. Invitations are
    durable, so including every invitee and inviter makes this set stable when
    a player moves from pending to accepted or later leaves the active roster.
    New invitation targets are included before the Crew lock too. If a writer
    committed while this request was waiting for the owner User, retry from an
    expanded snapshot rather than acquiring a new User below a Crew lock.
    """
    additional = {int(user_id) for user_id in additional_user_ids}
    for _ in range(max_attempts):
        user_ids = _crew_related_user_ids(crew_id) | additional
        if not user_ids and not additional:
            return None
        _lock_users_for_update(user_ids)
        current_user_ids = _crew_related_user_ids(crew_id) | additional
        if current_user_ids.issubset(user_ids):
            return _active_crew(crew_id, lock=True)
        db.session.rollback()
    raise RuntimeError('Could not stabilize Crew User lock snapshot')


def _member_crew_for_update_or_404(crew_id):
    crew = _active_crew_after_user_locks(crew_id)
    # Authorization is deliberately re-evaluated only after both lock layers.
    if (
        not crew
        or g.current_user.deleted_at is not None
        or not _is_member(crew, g.current_user.id)
    ):
        return None, (jsonify({'error': 'crew_not_found'}), 404)
    return crew, None


def _pending_invite(crew_id, user_id):
    return CrewInvite.query.filter_by(
        crew_id=crew_id, invitee_id=user_id, status='pending',
    ).first()


def _revoke_accepted_invite(crew_id, user_id):
    """Retire durable consent when an accepted player leaves the roster."""
    invite = CrewInvite.query.filter_by(
        crew_id=crew_id, invitee_id=user_id, status='accepted',
    ).first()
    if invite:
        invite.status = 'revoked'
        invite.resolved_at = utcnow()


def _advance_chat_read_marker(user_id, crew_id, latest_id):
    """Compatibility wrapper around the shared conversation read adapter."""
    from backend.services.conversations import (
        advance_conversation_read, conversation_ref,
    )
    advance_conversation_read(
        conversation_ref('crew', crew_id), user_id, latest_id,
    )


def _set_crew_notification_level(user_id, crew_id, level):
    """Atomically create or update a Crew preference without moving its read marker."""
    table = CrewChatRead.__table__
    dialect = db.session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f'Unsupported Crew chat database: {dialect}')

    current_last_read = db.session.query(
        CrewChatRead.last_read_message_id,
    ).filter_by(user_id=user_id, crew_id=crew_id).scalar() or 0
    now = utcnow()
    statement = insert(table).values(
        user_id=user_id,
        crew_id=crew_id,
        last_read_message_id=0,
        notification_level=level,
        created_at=now,
        updated_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.user_id, table.c.crew_id],
        set_={'notification_level': level, 'updated_at': now},
    )
    db.session.execute(statement)
    # The legacy row also stores the notification preference; the canonical
    # marker stores only the cross-scope read position. Dual-write the latter
    # without allowing this settings-only mutation to advance it.
    _advance_chat_read_marker(user_id, crew_id, current_last_read)


def _crew_notification_level(crew_id, user_id):
    preference = CrewChatRead.query.filter_by(
        crew_id=crew_id, user_id=user_id,
    ).first()
    level = preference.notification_level if preference else 'all'
    return level if level in ('all', 'mentions', 'off') else 'all'


def _crew_member_payloads(crew):
    rows = []
    if crew.owner and not crew.owner.deleted_at:
        rows.append({**crew.owner.to_public_dict(), 'is_owner': True})
    for membership in sorted(crew.members, key=lambda row: (row.created_at, row.id)):
        if membership.user and not membership.user.deleted_at:
            rows.append({**membership.user.to_public_dict(), 'is_owner': False})
    return rows


def _crew_detail_payload(crew, viewer_id):
    data = crew.to_summary_dict(viewer_id)
    data['my_notification_level'] = _crew_notification_level(crew.id, viewer_id)
    data['notifications_muted'] = data['my_notification_level'] == 'off'
    data['members'] = _crew_member_payloads(crew)
    if viewer_id == crew.owner_id:
        data['pending_invites'] = _owner_pending_invitation_payloads(crew)

    # A later join must not reveal a private game whose immutable invite
    # snapshot did not include that player.
    upcoming = (
        Game.query.filter(
            Game.crew_id == crew.id,
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow() - timedelta(minutes=15),
        )
        .order_by(Game.scheduled_at.asc())
        .all()
    )
    # Match the normal game-discovery boundary. In particular, an accepted
    # friend who joined the Crew after a friends-visible game was scheduled may
    # see it, while a later join still cannot see an old private invite snapshot.
    from backend.routes.games import _game_has_blocked_participant
    from backend.routes.social import friend_ids

    viewer_friends = friend_ids(viewer_id)
    viewer_hidden = blocked_pair_ids(viewer_id)
    data['upcoming_games'] = [
        game.to_dict(viewer_id) for game in upcoming
        if game.visible_to(viewer_id, viewer_friends)
        and not _game_has_blocked_participant(game, viewer_id, viewer_hidden)
    ][:8]
    return data


def _minimal_invitation_payload(invite):
    crew = invite.crew
    inviter = invite.invited_by
    return {
        'id': crew.id,
        'name': crew.name,
        'owner_id': crew.owner_id,
        'invited_by_name': inviter.display_name if inviter and not inviter.deleted_at else 'A player',
        'inviter': (
            inviter.to_public_dict()
            if inviter and not inviter.deleted_at else None
        ),
        'member_count': len(crew.member_ids()),
        'source_game_id': crew.source_game_id,
        'default_court_id': crew.default_court_id,
        'default_court_name': crew.default_court.name if crew.default_court else None,
        'created_at': crew.created_at.isoformat() + 'Z' if crew.created_at else None,
    }


def _owner_pending_invitation_payloads(crew, hidden_ids=None):
    """Pending identities are management data and never leave the owner view."""
    rows = []
    for invite in sorted(crew.invites, key=lambda row: (row.created_at, row.id)):
        invitee = invite.invitee
        if (
            invite.status != 'pending'
            or not invitee
            or invitee.deleted_at is not None
            or (
                invitee.id in hidden_ids if hidden_ids is not None
                else is_blocked_between(crew.owner_id, invitee.id)
            )
        ):
            continue
        rows.append({
            'id': invite.id,
            'user': invitee.to_public_dict(),
            'status': 'pending',
            'invited_at': (
                (invite.updated_at or invite.created_at).isoformat() + 'Z'
                if (invite.updated_at or invite.created_at) else None
            ),
        })
    return rows


def _parse_invite_user_ids(payload, *, required):
    if 'invite_user_ids' not in payload:
        return (None, 'invite_user_ids_required') if required else ([], None)
    raw_ids = payload.get('invite_user_ids')
    if not isinstance(raw_ids, list) or (required and not raw_ids):
        return None, 'invalid_invite_user_ids'
    target_ids = []
    for raw_id in raw_ids:
        try:
            target_id = int(raw_id)
        except (TypeError, ValueError):
            return None, 'invalid_invite_user_ids'
        if target_id <= 0:
            return None, 'invalid_invite_user_ids'
        if target_id not in target_ids:
            target_ids.append(target_id)
    if len(target_ids) > MAX_CREW_SIZE - 1:
        return None, 'too_many_invitees'
    return target_ids, None


def _validated_default_court(payload, *, required=False):
    """Return ``(provided, court_id, error_response)`` for Crew mutations."""
    if 'default_court_id' not in payload:
        if required:
            return False, None, (jsonify({'error': 'default_court_id_required'}), 400)
        return False, None, None
    raw_court_id = payload.get('default_court_id')
    if raw_court_id is None:
        return True, None, None
    try:
        court_id = int(raw_court_id)
    except (TypeError, ValueError):
        return True, None, (jsonify({'error': 'invalid_default_court_id'}), 400)
    if court_id <= 0:
        return True, None, (jsonify({'error': 'invalid_default_court_id'}), 400)
    court = db.session.get(Court, court_id)
    if not court:
        return True, None, (jsonify({'error': 'court_not_found'}), 404)
    if court.closed:
        return True, None, (jsonify({'error': 'court_closed'}), 409)
    return True, court.id, None


def _accepted_friend_ids(user_id):
    rows = Friendship.query.filter(
        Friendship.status == 'accepted',
        or_(
            Friendship.requester_id == user_id,
            Friendship.addressee_id == user_id,
        ),
    ).all()
    return {
        row.addressee_id if row.requester_id == user_id else row.requester_id
        for row in rows
    } - blocked_pair_ids(user_id)


def _invite_users_to_crew(crew, target_ids, locked_by_id):
    """Open or reopen eligible owner-friend invitations under Crew locks."""
    owner_id = crew.owner_id
    member_ids = crew.member_ids()
    pending_by_user = {
        invite.invitee_id: invite
        for invite in crew.invites
        if invite.status == 'pending'
    }
    all_invites = {invite.invitee_id: invite for invite in crew.invites}
    accepted_friends = _accepted_friend_ids(owner_id)
    # Pending consent consumes a future roster slot. This keeps simultaneous
    # acceptances from ever growing the Crew beyond its hard cap.
    committed_ids = set(member_ids) | set(pending_by_user)
    invited_user_ids = []
    skipped = []
    for target_id in target_ids:
        target = locked_by_id.get(target_id)
        reason = None
        if target_id == owner_id:
            reason = 'cannot_invite_self'
        elif target_id in member_ids:
            reason = 'already_member'
        elif (
            not target
            or target.deleted_at is not None
            or target_id not in accepted_friends
        ):
            reason = 'not_eligible'
        elif any(
            is_blocked_between(target_id, existing_id)
            for existing_id in committed_ids
            if existing_id != target_id
        ):
            reason = 'not_eligible'
        elif target_id in pending_by_user:
            reason = 'already_pending'
        elif len(committed_ids) >= MAX_CREW_SIZE:
            reason = 'crew_full'

        if reason:
            if reason == 'not_eligible' and target_id in pending_by_user:
                stale = pending_by_user.pop(target_id)
                stale.status = 'revoked'
                stale.resolved_at = utcnow()
                committed_ids.discard(target_id)
                Notification.query.filter_by(
                    user_id=target_id,
                    related_crew_id=crew.id,
                    kind='crew_invite',
                ).delete(synchronize_session=False)
            skipped.append({'user_id': target_id, 'reason': reason})
            continue

        invite = all_invites.get(target_id)
        if invite is None:
            invite = CrewInvite(
                crew=crew,
                invitee_id=target_id,
                invited_by_id=owner_id,
                status='pending',
            )
            db.session.add(invite)
            all_invites[target_id] = invite
        else:
            # Unique durable consent rows are reopened instead of duplicated.
            invite.status = 'pending'
            invite.invited_by_id = owner_id
            invite.resolved_at = None
        pending_by_user[target_id] = invite
        committed_ids.add(target_id)
        invited_user_ids.append(target_id)
        notify(
            target_id,
            'crew_invite',
            f'{crew.owner.display_name} invited you to the {crew.name} play group',
            'Join the play group to plan your next casual play session together.',
            related_user_id=owner_id,
            related_crew_id=crew.id,
            action_url=f'/#crew/{crew.id}',
            unread_dedupe_key=f'crew-invite:{crew.id}',
        )
    return invited_user_ids, skipped


def archive_crew(crew):
    """Close a crew without breaking immutable historical Game.crew_id links."""
    crew.archived_at = utcnow()
    sync_group_identity('crew', crew)
    for invite in crew.invites:
        if invite.status == 'pending':
            invite.status = 'revoked'
            invite.resolved_at = utcnow()
    Notification.query.filter_by(related_crew_id=crew.id).delete(
        synchronize_session=False,
    )


@crews_bp.post('/crews')
@rate_limit(20, 3600)
@login_required
def create_crew():
    """Create a private group and invite accepted friends with durable consent."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    name = str(payload.get('name') or '').strip()
    if len(name) < 3 or len(name) > 80:
        return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400
    target_ids, invite_error = _parse_invite_user_ids(payload, required=False)
    if invite_error:
        return jsonify({'error': invite_error}), 400
    _, default_court_id, court_error = _validated_default_court(payload)
    if court_error:
        return court_error

    locked_users = _lock_users_for_update(
        {g.current_user.id, *target_ids},
    )
    locked_by_id = {user.id: user for user in locked_users}
    owner = locked_by_id.get(g.current_user.id)
    if not owner or owner.deleted_at is not None:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = owner

    crew = Crew(
        owner_id=owner.id,
        name=name,
        source_game_id=None,
        default_court_id=default_court_id,
        roster_version=1,
    )
    db.session.add(crew)
    db.session.flush()
    sync_group_identity('crew', crew)
    invited_user_ids, skipped = _invite_users_to_crew(
        crew, target_ids, locked_by_id,
    )
    db.session.commit()
    return jsonify({
        'crew': _crew_detail_payload(crew, owner.id),
        'created': True,
        'invited_count': len(invited_user_ids),
        'invited_user_ids': invited_user_ids,
        'skipped': skipped,
    }), 201


@crews_bp.post('/games/<int:game_id>/crew')
@rate_limit(20, 3600)
@login_required
def create_crew_from_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    scored_ids = [
        player.user_id for player in game.players
        if game.completion_kind == 'session' or player.team in (1, 2)
    ]
    # Keep this non-enumerating: somebody who did not actually play cannot use
    # a guessed completed-game id to learn whether its Crew exists.
    if g.current_user.id not in scored_ids:
        return jsonify({'error': 'game_not_found'}), 404
    if game.status != 'completed':
        return jsonify({'error': 'game_not_completed'}), 400

    # A completed game already linked to a Crew should keep that community,
    # rather than creating a new group after every rematch.
    existing = None
    if game.crew_id:
        existing = _active_crew(game.crew_id)
        if existing is None:
            # A linked game's Crew identity is immutable historical
            # provenance. Never replace an archived or missing Crew with a
            # fresh group when somebody retries the postgame action.
            return jsonify({'error': 'crew_archived'}), 409
    source_crew = Crew.query.filter_by(source_game_id=game.id).first()
    if existing is None and source_crew and source_crew.archived_at is None:
        existing = source_crew
    if existing:
        if _is_member(existing, g.current_user.id):
            payload = _crew_detail_payload(existing, g.current_user.id)
        elif _pending_invite(existing.id, g.current_user.id):
            invite = _pending_invite(existing.id, g.current_user.id)
            payload = _minimal_invitation_payload(invite)
        else:
            return jsonify({'error': 'crew_not_found'}), 404
        return jsonify({'crew': payload, 'created': False, 'invited_count': 0}), 200
    if source_crew and source_crew.archived_at is not None:
        # Source identity is permanent: disbanding must not let a delayed
        # offline retry resurrect the same crew under a fresh row.
        return jsonify({'error': 'crew_archived'}), 409

    # Serialize eligibility against blocking and account deletion. Social
    # pair locks use the same ascending User order, so a block cannot commit in
    # the gap between our check and the durable CrewInvite rows.
    locked_users = _lock_users_for_update(scored_ids)
    locked_by_id = {user.id: user for user in locked_users}
    locked_owner = locked_by_id.get(g.current_user.id)
    if locked_owner is None or locked_owner.deleted_at is not None:
        # Authentication loaded the account before waiting on another
        # transaction's deletion lock. Do not let stale identity-map state
        # create a Crew owned by an anonymized account.
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = locked_owner

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    requested_name = str(payload.get('name') or '').strip()
    default_name = (
        f'{game.scheduled_at.strftime("%A")} at {game.court.name}'
        if game.court else f'{game.scheduled_at.strftime("%A")} Play Group'
    )
    name = (requested_name or default_name)[:80]
    if len(name) < 3:
        return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400

    crew = Crew(
        owner_id=g.current_user.id,
        name=name,
        source_game_id=game.id,
        default_court_id=game.court_id,
        roster_version=1,
    )
    db.session.add(crew)
    try:
        # Win the canonical source-game identity before any invitations or
        # notifications are written. Concurrent/retried creators converge.
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        winner = Crew.query.filter_by(source_game_id=game.id).filter(
            Crew.archived_at.is_(None),
        ).first()
        if winner and (
            _is_member(winner, g.current_user.id)
            or _pending_invite(winner.id, g.current_user.id)
        ):
            body = _crew_detail_payload(winner, g.current_user.id) \
                if _is_member(winner, g.current_user.id) \
                else _minimal_invitation_payload(_pending_invite(winner.id, g.current_user.id))
            return jsonify({'crew': body, 'created': False, 'invited_count': 0}), 200
        if winner:
            return jsonify({'error': 'crew_not_found'}), 404
        raise

    sync_group_identity('crew', crew)

    candidates = {
        user_id: user for user_id, user in locked_by_id.items()
        if user_id != g.current_user.id and user.deleted_at is None
    }
    invited_count = 0
    for user_id in scored_ids:
        if user_id == g.current_user.id:
            continue
        candidate = candidates.get(user_id)
        if not candidate or is_blocked_between(g.current_user.id, user_id):
            continue
        db.session.add(CrewInvite(
            crew=crew,
            invitee_id=user_id,
            invited_by_id=g.current_user.id,
            status='pending',
        ))
        notify(
            user_id,
            'crew_invite',
            f'{g.current_user.display_name} invited you to the {crew.name} play group',
            f'You played together at {game.court.name if game.court else "a local court"}.',
            related_user_id=g.current_user.id,
            related_crew_id=crew.id,
            action_url=f'/#crew/{crew.id}',
            unread_dedupe_key=f'crew-invite:{crew.id}',
        )
        invited_count += 1
    db.session.commit()
    return jsonify({
        'crew': _crew_detail_payload(crew, g.current_user.id),
        'created': True,
        'invited_count': invited_count,
    }), 201


@crews_bp.get('/crews/mine')
@login_required
def my_crews():
    me = g.current_user.id
    from backend.routes.chat import room_message_payload
    member_crew_ids = db.session.query(CrewMember.crew_id).filter(
        CrewMember.user_id == me,
    )
    # Every relationship used below is eagerly loaded in a fixed number of
    # queries. The inbox must not become slower one query at a time as a player
    # joins more play groups.
    crews = (
        Crew.query
        .options(
            selectinload(Crew.owner),
            selectinload(Crew.default_court),
            selectinload(Crew.members).selectinload(CrewMember.user),
            selectinload(Crew.invites).selectinload(CrewInvite.invitee),
            selectinload(Crew.invites).selectinload(CrewInvite.invited_by),
        )
        .filter(
            Crew.archived_at.is_(None),
            or_(Crew.owner_id == me, Crew.id.in_(member_crew_ids)),
        )
        .all()
    )
    crew_ids = [crew.id for crew in crews]
    hidden_ids = blocked_pair_ids(me)

    preferences = {}
    latest_by_crew = {}
    unread_by_crew = {}
    if crew_ids:
        preferences = {
            row.crew_id: row
            for row in CrewChatRead.query.filter(
                CrewChatRead.user_id == me,
                CrewChatRead.crew_id.in_(crew_ids),
            ).all()
        }

        latest_query = db.session.query(
            Message.crew_id, func.max(Message.id).label('last_message_id'),
        ).filter(Message.crew_id.in_(crew_ids))
        if hidden_ids:
            latest_query = latest_query.filter(
                Message.sender_id.notin_(hidden_ids),
            )
        latest_rows = latest_query.group_by(Message.crew_id).all()
        latest_ids = [row.last_message_id for row in latest_rows]
        if latest_ids:
            latest_messages = (
                Message.query
                .options(
                    selectinload(Message.sender),
                    selectinload(Message.hearts),
                )
                .filter(Message.id.in_(latest_ids))
                .all()
            )
            latest_by_crew = {
                message.crew_id: message for message in latest_messages
            }

        marker = aliased(CrewChatRead)
        unread_query = (
            db.session.query(Message.crew_id, func.count(Message.id))
            .outerjoin(
                marker,
                and_(
                    marker.crew_id == Message.crew_id,
                    marker.user_id == me,
                ),
            )
            .filter(
                Message.crew_id.in_(crew_ids),
                Message.sender_id != me,
                Message.id > func.coalesce(marker.last_read_message_id, 0),
            )
        )
        if hidden_ids:
            unread_query = unread_query.filter(
                Message.sender_id.notin_(hidden_ids),
            )
        unread_by_crew = dict(unread_query.group_by(Message.crew_id).all())

    items = []
    for crew in crews:
        last = latest_by_crew.get(crew.id)
        data = crew.to_summary_dict(me)
        preference = preferences.get(crew.id)
        level = preference.notification_level if preference else 'all'
        if level not in ('all', 'mentions', 'off'):
            level = 'all'
        data['my_notification_level'] = level
        data['notifications_muted'] = level == 'off'
        if crew.owner_id == me:
            data['pending_invites'] = _owner_pending_invitation_payloads(
                crew, hidden_ids,
            )
        active_member_ids = {
            membership.user_id
            for membership in crew.members
            if membership.user and membership.user.deleted_at is None
        }
        if crew.owner and crew.owner.deleted_at is None:
            active_member_ids.add(crew.owner_id)
        visible_reactor_ids = active_member_ids - hidden_ids
        data['last_message'] = room_message_payload(
            last, visible_reactor_ids,
        ) if last else None
        data['unread'] = unread_by_crew.get(crew.id, 0) if last else 0
        items.append(data)
    items.sort(key=lambda item: -(
        item['last_message']['id'] if item['last_message'] else 0
    ))

    invitations = []
    pending = (
        CrewInvite.query
        .options(
            selectinload(CrewInvite.invited_by),
            selectinload(CrewInvite.crew).selectinload(Crew.default_court),
            selectinload(CrewInvite.crew).selectinload(Crew.members),
        )
        .join(Crew)
        .filter(
            CrewInvite.invitee_id == me,
            CrewInvite.status == 'pending',
            Crew.archived_at.is_(None),
        )
        .order_by(CrewInvite.id.desc())
        .all()
    )
    for invite in pending:
        if invite.crew.owner_id in hidden_ids:
            continue
        invitations.append(_minimal_invitation_payload(invite))
    return jsonify({'items': items, 'invitations': invitations})


@crews_bp.get('/crews/<int:crew_id>')
@login_required
def crew_detail(crew_id):
    crew, err = _member_crew_or_404(crew_id)
    if err:
        return err
    return jsonify(_crew_detail_payload(crew, g.current_user.id))


@crews_bp.post('/crews/<int:crew_id>/invites')
@rate_limit(30, 3600)
@login_required
def invite_to_crew(crew_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    target_ids, invite_error = _parse_invite_user_ids(payload, required=True)
    if invite_error:
        return jsonify({'error': invite_error}), 400

    crew = _active_crew_after_user_locks(
        crew_id, additional_user_ids=target_ids,
    )
    if (
        not crew
        or g.current_user.deleted_at is not None
        or crew.owner_id != g.current_user.id
    ):
        return jsonify({'error': 'crew_not_found'}), 404
    locked_targets = User.query.filter(User.id.in_(target_ids)).all()
    locked_by_id = {user.id: user for user in locked_targets}
    invited_user_ids, skipped = _invite_users_to_crew(
        crew, target_ids, locked_by_id,
    )
    db.session.commit()
    return jsonify({
        'crew': _crew_detail_payload(crew, g.current_user.id),
        'invited_count': len(invited_user_ids),
        'invited_user_ids': invited_user_ids,
        'skipped': skipped,
    })


@crews_bp.post('/crews/<int:crew_id>/respond')
@rate_limit(30, 3600)
@login_required
def respond_to_crew(crew_id):
    crew = _active_crew_after_user_locks(crew_id)
    invite = CrewInvite.query.filter_by(
        crew_id=crew_id, invitee_id=g.current_user.id,
    ).first() if crew else None
    if not crew or not invite or invite.status not in ('pending', 'accepted', 'declined'):
        return jsonify({'error': 'crew_not_found'}), 404
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get('accept'), bool):
        return jsonify({'error': 'accept_required'}), 400

    accepting = payload['accept']
    if not accepting:
        if invite.status == 'accepted':
            return jsonify({'error': 'already_joined'}), 409
        if invite.status != 'declined':
            invite.status = 'declined'
            invite.resolved_at = utcnow()
        Notification.query.filter_by(
            user_id=g.current_user.id, related_crew_id=crew.id, kind='crew_invite',
        ).update({'read': True, 'unread_dedupe_key': None}, synchronize_session=False)
        db.session.commit()
        return jsonify({'declined': True})

    existing_member = CrewMember.query.filter_by(
        crew_id=crew.id, user_id=g.current_user.id,
    ).first()
    if invite.status == 'accepted':
        if existing_member or crew.owner_id == g.current_user.id:
            return jsonify({
                'joined': True,
                'crew': _crew_detail_payload(crew, g.current_user.id),
            })
        # An accepted invite is evidence of past consent, not a standing
        # invitation to recreate membership after a departure or repair.
        invite.status = 'revoked'
        invite.resolved_at = utcnow()
        db.session.commit()
        return jsonify({'error': 'crew_not_found'}), 404
    if invite.status == 'declined':
        return jsonify({'error': 'crew_not_found'}), 404

    accepted_ids = _member_ids(crew)
    accepted_users = User.query.filter(User.id.in_(accepted_ids)).all()
    if (
        len(accepted_ids) >= MAX_CREW_SIZE
        or len(accepted_users) != len(accepted_ids)
        or any(user.deleted_at for user in accepted_users)
        or any(is_blocked_between(g.current_user.id, user_id) for user_id in accepted_ids)
    ):
        return jsonify({'error': 'crew_changed'}), 409

    if not existing_member and g.current_user.id != crew.owner_id:
        db.session.add(CrewMember(crew=crew, user_id=g.current_user.id))
        crew.roster_version += 1
    invite.status = 'accepted'
    invite.resolved_at = utcnow()

    # Invitations created at the same postgame moment may predate a block
    # between two invitees. Once one accepts, retire any invitation that could
    # no longer be accepted without violating the pairwise privacy invariant.
    for pending in CrewInvite.query.filter_by(
        crew_id=crew.id, status='pending',
    ).all():
        if pending.invitee_id == g.current_user.id:
            continue
        if is_blocked_between(g.current_user.id, pending.invitee_id):
            pending.status = 'revoked'
            pending.resolved_at = utcnow()
            Notification.query.filter_by(
                user_id=pending.invitee_id,
                related_crew_id=crew.id,
                kind='crew_invite',
            ).delete(synchronize_session=False)
    Notification.query.filter_by(
        user_id=g.current_user.id, related_crew_id=crew.id, kind='crew_invite',
    ).update({'read': True, 'unread_dedupe_key': None}, synchronize_session=False)
    if crew.owner_id != g.current_user.id:
        notify(
            crew.owner_id,
            'crew_update',
            f'{g.current_user.display_name} joined the {crew.name} play group',
            related_user_id=g.current_user.id,
            related_crew_id=crew.id,
            action_url=f'/#crew/{crew.id}',
        )
    try:
        db.session.commit()
    except IntegrityError:
        # PostgreSQL's parent lock serializes this path; the unique membership
        # row remains a final guard for SQLite or a legacy writer that skipped
        # that lock. A concurrent identical acceptance converges on the winner.
        db.session.rollback()
        crew = _active_crew(crew_id)
        accepted = CrewInvite.query.filter_by(
            crew_id=crew_id,
            invitee_id=g.current_user.id,
            status='accepted',
        ).first() if crew else None
        member = CrewMember.query.filter_by(
            crew_id=crew_id, user_id=g.current_user.id,
        ).first() if crew else None
        if crew and accepted and (member or crew.owner_id == g.current_user.id):
            return jsonify({
                'joined': True,
                'crew': _crew_detail_payload(crew, g.current_user.id),
            })
        raise
    return jsonify({'joined': True, 'crew': _crew_detail_payload(crew, g.current_user.id)})


@crews_bp.patch('/crews/<int:crew_id>')
@rate_limit(20, 3600)
@login_required
def edit_crew(crew_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    crew, err = _member_crew_for_update_or_404(crew_id)
    if err:
        return err
    if crew.owner_id != g.current_user.id:
        return jsonify({'error': 'crew_not_found'}), 404
    if 'name' in payload:
        name = str(payload.get('name') or '').strip()
        if len(name) < 3 or len(name) > 80:
            return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400
        crew.name = name
    provided_court, default_court_id, court_error = _validated_default_court(payload)
    if court_error:
        return court_error
    if provided_court:
        crew.default_court_id = default_court_id
    sync_group_identity('crew', crew)
    db.session.commit()
    return jsonify(_crew_detail_payload(crew, g.current_user.id))


@crews_bp.post('/crews/<int:crew_id>/leave')
@rate_limit(20, 3600)
@login_required
def leave_crew(crew_id):
    crew, err = _member_crew_for_update_or_404(crew_id)
    if err:
        return err
    me = g.current_user.id
    if crew.owner_id == me:
        # A promoted owner originally joined via CrewInvite. Once that owner
        # leaves, their old accepted consent must not become a rejoin token.
        _revoke_accepted_invite(crew.id, me)
        others = sorted(crew.members, key=lambda row: (row.created_at, row.id))
        if not others:
            archive_crew(crew)
            db.session.commit()
            return jsonify({'left': True, 'archived': True})
        successor = others[0]
        crew.owner_id = successor.user_id
        crew.members.remove(successor)
        sync_group_identity('crew', crew)
        notify(
            successor.user_id,
            'crew_update',
            f'You now organize the {crew.name} play group',
            related_user_id=me,
            related_crew_id=crew.id,
            action_url=f'/#crew/{crew.id}',
        )
    else:
        membership = CrewMember.query.filter_by(
            crew_id=crew.id, user_id=me,
        ).first()
        if not membership:
            return jsonify({'error': 'crew_not_found'}), 404
        crew.members.remove(membership)
        _revoke_accepted_invite(crew.id, me)
    crew.roster_version += 1
    CrewChatRead.query.filter_by(crew_id=crew.id, user_id=me).delete(
        synchronize_session=False,
    )
    delete_conversation_read(conversation_ref('crew', crew.id), me)
    db.session.commit()
    return jsonify({'left': True, 'archived': False})


@crews_bp.delete('/crews/<int:crew_id>')
@rate_limit(10, 3600)
@login_required
def disband_crew(crew_id):
    crew, err = _member_crew_for_update_or_404(crew_id)
    if err:
        return err
    if crew.owner_id != g.current_user.id:
        return jsonify({'error': 'crew_not_found'}), 404
    for user_id in _member_ids(crew):
        if user_id == g.current_user.id:
            continue
        # This final heads-up intentionally has no Crew foreign key: archiving
        # removes all old room notifications and the destination is gone.
        notify(
            user_id,
            'crew_update',
            f'The {crew.name} play group was closed',
            related_user_id=g.current_user.id,
        )
    archive_crew(crew)
    db.session.commit()
    return jsonify({'deleted': True})


@crews_bp.delete('/crews/<int:crew_id>/members/<int:user_id>')
@rate_limit(30, 3600)
@login_required
def remove_crew_member(crew_id, user_id):
    crew, err = _member_crew_for_update_or_404(crew_id)
    if err:
        return err
    if crew.owner_id != g.current_user.id or user_id == crew.owner_id:
        return jsonify({'error': 'crew_not_found'}), 404
    membership = CrewMember.query.filter_by(crew_id=crew.id, user_id=user_id).first()
    if not membership:
        return jsonify({'error': 'crew_not_found'}), 404
    crew.members.remove(membership)
    _revoke_accepted_invite(crew.id, user_id)
    crew.roster_version += 1
    CrewChatRead.query.filter_by(crew_id=crew.id, user_id=user_id).delete(
        synchronize_session=False,
    )
    delete_conversation_read(conversation_ref('crew', crew.id), user_id)
    notify(
        user_id,
        'crew_update',
        f'You were removed from the {crew.name} play group',
        related_user_id=g.current_user.id,
        related_crew_id=crew.id,
    )
    db.session.commit()
    return jsonify({'removed': True})


@crews_bp.get('/crews/<int:crew_id>/chat')
@login_required
def crew_chat(crew_id):
    crew, err = _member_crew_or_404(crew_id)
    if err:
        return err
    from backend.services.conversations import conversation_ref
    from backend.routes.chat import (
        chat_messages_window, chat_read_marker_target, chat_window_args,
        room_heart_counts, room_message_payload, visible_crew_reactor_ids,
    )
    conversation = conversation_ref('crew', crew.id)
    window, window_err = chat_window_args()
    if window_err:
        return window_err
    since_id, before_id, history_limit = window
    query = conversation.message_query()
    hidden_ids = blocked_pair_ids(g.current_user.id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    messages, has_more, has_older, next_before_id = chat_messages_window(
        query, since_id, before_id, history_limit=history_limit,
    )
    visible_reactor_ids = visible_crew_reactor_ids(crew, g.current_user.id)
    latest_id = chat_read_marker_target(
        query, messages, since_id, before_id, has_more,
    )
    _advance_chat_read_marker(g.current_user.id, crew.id, latest_id)
    db.session.commit()
    return jsonify({
        'conversation': conversation.to_dict(crew.name),
        'crew': {'id': crew.id, 'name': crew.name},
        'items': [
            room_message_payload(message, visible_reactor_ids)
            for message in messages
        ],
        'heart_counts': room_heart_counts(
            'crew_id', crew.id, visible_reactor_ids,
        ),
        'has_more': has_more,
        'has_older': has_older,
        'next_before_id': next_before_id,
    })


@crews_bp.patch('/crews/<int:crew_id>/notification-settings')
@rate_limit(60, 3600)
@login_required
def crew_notification_settings(crew_id):
    crew, err = _member_crew_or_404(crew_id)
    if err:
        return err
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    level = str(payload.get('level') or '').strip().lower()
    if level not in ('all', 'mentions', 'off'):
        return jsonify({'error': 'invalid_notification_level'}), 400
    _set_crew_notification_level(g.current_user.id, crew.id, level)
    db.session.commit()
    return jsonify({
        'level': level,
        'muted': level == 'off',
    })


def _crew_message_should_notify(level, recipient, body):
    level = level or 'all'
    if level == 'off':
        return False
    if level == 'all':
        return True
    if level != 'mentions' or not recipient:
        return False
    lowered = body.casefold()
    display_name = (recipient.display_name or '').strip().casefold()
    first_name = display_name.split()[0] if display_name else ''
    return bool(
        (display_name and f'@{display_name}' in lowered)
        or (first_name and f'@{first_name}' in lowered)
    )


@crews_bp.post('/crews/<int:crew_id>/chat')
@rate_limit(60, 60)
@login_required
def send_crew_message(crew_id):
    # Serialize the final membership check with leave/remove/disband so a
    # departed player cannot commit a message after losing room access. User
    # locks come first to match the social-block privacy boundary.
    crew, err = _member_crew_for_update_or_404(crew_id)
    if err:
        return err
    from backend.services.conversations import conversation_ref
    from backend.routes.chat import (
        prepare_chat_message, room_message_payload, visible_crew_reactor_ids,
    )
    conversation = conversation_ref('crew', crew.id)
    message, replayed, body, send_err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        conversation=conversation,
    )
    if send_err:
        return send_err
    if replayed:
        payload = room_message_payload(
            message, visible_crew_reactor_ids(crew, g.current_user.id),
        )
        return jsonify(
            conversation.decorate_message(payload, crew.name)
        ), 200

    recipient_ids = _member_ids(crew) - {g.current_user.id}
    blocked_recipient_ids = blocked_pair_ids(g.current_user.id)
    recipients = {
        user.id: user for user in User.query.filter(
            User.id.in_(recipient_ids), User.deleted_at.is_(None),
        ).all()
    } if recipient_ids else {}
    levels = {
        row.user_id: row.notification_level
        for row in CrewChatRead.query.filter(
            CrewChatRead.crew_id == crew.id,
            CrewChatRead.user_id.in_(recipient_ids),
        ).all()
    } if recipient_ids else {}
    for user_id in recipient_ids:
        recipient = recipients.get(user_id)
        if user_id in blocked_recipient_ids or not recipient:
            continue
        if not _crew_message_should_notify(
            levels.get(user_id, 'all'), recipient, body,
        ):
            continue
        notify(
            user_id,
            'crew_message',
            f'{g.current_user.display_name} in the {crew.name} play group',
            body[:140],
            related_user_id=g.current_user.id,
            related_crew_id=crew.id,
            action_url=f'/#crew/{crew.id}',
            unread_dedupe_key=f'crew-message:{crew.id}',
        )
    db.session.commit()
    payload = room_message_payload(
        message, visible_crew_reactor_ids(crew, g.current_user.id),
    )
    return jsonify(conversation.decorate_message(payload, crew.name)), 201
