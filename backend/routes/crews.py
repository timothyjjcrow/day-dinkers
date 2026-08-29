"""Private, consent-based crews formed from completed local games."""
from datetime import timedelta

from flask import Blueprint, g, jsonify, request
from sqlalchemy import case, or_
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    Crew, CrewChatRead, CrewInvite, CrewMember, Game, Message, Notification,
    User, blocked_pair_ids, is_blocked_between, notify, utcnow,
)
from backend.routes.auth import _lock_users_for_update, login_required
from backend.security import rate_limit


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


def _active_crew_after_user_locks(crew_id):
    """Lock every Crew-related User in canonical order, then the Crew row.

    Block reconciliation uses the same User-before-Crew order. Invitations are
    durable, so including every invitee makes this set stable when a player
    moves from pending to accepted or later leaves the active roster.
    """
    owner_id = db.session.query(Crew.owner_id).filter(
        Crew.id == crew_id,
    ).scalar()
    if owner_id is None:
        return None
    user_ids = {owner_id}
    user_ids.update(
        row[0] for row in db.session.query(CrewMember.user_id).filter(
            CrewMember.crew_id == crew_id,
        ).all()
    )
    user_ids.update(
        row[0] for row in db.session.query(CrewInvite.invitee_id).filter(
            CrewInvite.crew_id == crew_id,
        ).all()
    )
    _lock_users_for_update(user_ids)
    return _active_crew(crew_id, lock=True)


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
    """Atomically create or monotonically advance one Crew read marker."""
    table = CrewChatRead.__table__
    dialect = db.session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:  # The application supports PostgreSQL in production and SQLite locally.
        raise RuntimeError(f'Unsupported Crew chat database: {dialect}')

    now = utcnow()
    statement = insert(table).values(
        user_id=user_id,
        crew_id=crew_id,
        last_read_message_id=latest_id,
        created_at=now,
        updated_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.user_id, table.c.crew_id],
        set_={
            'last_read_message_id': case(
                (
                    statement.excluded.last_read_message_id
                    > table.c.last_read_message_id,
                    statement.excluded.last_read_message_id,
                ),
                else_=table.c.last_read_message_id,
            ),
            'updated_at': now,
        },
    )
    db.session.execute(statement)


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
    data['members'] = _crew_member_payloads(crew)

    # A later join must not reveal a private game whose immutable invite
    # snapshot did not include that player.
    upcoming = (
        Game.query.filter(
            Game.crew_id == crew.id,
            Game.status == 'upcoming',
            Game.scheduled_at >= utcnow() - timedelta(minutes=15),
        )
        .order_by(Game.scheduled_at.asc())
        .limit(8)
        .all()
    )
    data['upcoming_games'] = [
        game.to_dict(viewer_id) for game in upcoming
        if game.visible_to(viewer_id)
    ]
    return data


def _minimal_invitation_payload(invite):
    crew = invite.crew
    inviter = invite.invited_by
    return {
        'id': crew.id,
        'name': crew.name,
        'owner_id': crew.owner_id,
        'invited_by_name': inviter.display_name if inviter and not inviter.deleted_at else 'A player',
        'source_game_id': crew.source_game_id,
        'default_court_id': crew.default_court_id,
        'default_court_name': crew.default_court.name if crew.default_court else None,
        'created_at': crew.created_at.isoformat() + 'Z' if crew.created_at else None,
    }


def archive_crew(crew):
    """Close a crew without breaking immutable historical Game.crew_id links."""
    crew.archived_at = utcnow()
    for invite in crew.invites:
        if invite.status == 'pending':
            invite.status = 'revoked'
            invite.resolved_at = utcnow()
    Notification.query.filter_by(related_crew_id=crew.id).delete(
        synchronize_session=False,
    )


@crews_bp.post('/games/<int:game_id>/crew')
@rate_limit(20, 3600)
@login_required
def create_crew_from_game(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({'error': 'game_not_found'}), 404
    scored_ids = [
        player.user_id for player in game.players if player.team in (1, 2)
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
        if game.court else f'{game.scheduled_at.strftime("%A")} Crew'
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
            f'{g.current_user.display_name} invited you to {crew.name}',
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
    from backend.routes.chat import room_message_payload, visible_crew_reactor_ids
    member_crew_ids = db.session.query(CrewMember.crew_id).filter(
        CrewMember.user_id == me,
    )
    crews = Crew.query.filter(
        Crew.archived_at.is_(None),
        or_(Crew.owner_id == me, Crew.id.in_(member_crew_ids)),
    ).all()
    markers = {
        row.crew_id: row.last_read_message_id
        for row in CrewChatRead.query.filter_by(user_id=me).all()
    }
    hidden_ids = blocked_pair_ids(me)
    items = []
    for crew in crews:
        last_query = Message.query.filter(Message.crew_id == crew.id)
        if hidden_ids:
            last_query = last_query.filter(Message.sender_id.notin_(hidden_ids))
        last = last_query.order_by(Message.id.desc()).first()
        unread_query = Message.query.filter(
            Message.crew_id == crew.id,
            Message.id > markers.get(crew.id, 0),
            Message.sender_id != me,
        )
        if hidden_ids:
            unread_query = unread_query.filter(Message.sender_id.notin_(hidden_ids))
        data = crew.to_summary_dict(me)
        data['last_message'] = room_message_payload(
            last, visible_crew_reactor_ids(crew, me),
        ) if last else None
        data['unread'] = unread_query.count() if last else 0
        items.append(data)
    items.sort(key=lambda item: -(
        item['last_message']['id'] if item['last_message'] else 0
    ))

    invitations = []
    pending = CrewInvite.query.join(Crew).filter(
        CrewInvite.invitee_id == me,
        CrewInvite.status == 'pending',
        Crew.archived_at.is_(None),
    ).order_by(CrewInvite.id.desc()).all()
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
            f'{g.current_user.display_name} joined {crew.name}',
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
    crew, err = _member_crew_or_404(crew_id, lock=True)
    if err:
        return err
    if crew.owner_id != g.current_user.id:
        return jsonify({'error': 'crew_not_found'}), 404
    payload = request.get_json(silent=True) or {}
    if 'name' in payload:
        name = str(payload.get('name') or '').strip()
        if len(name) < 3 or len(name) > 80:
            return jsonify({'error': 'name_must_be_3_to_80_chars'}), 400
        crew.name = name
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
        notify(
            successor.user_id,
            'crew_update',
            f'You now run {crew.name}',
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
            f'{crew.name} was disbanded',
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
    notify(
        user_id,
        'crew_update',
        f'You were removed from {crew.name}',
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
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.crew_id == crew.id)
    hidden_ids = blocked_pair_ids(g.current_user.id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    from backend.routes.chat import (
        chat_messages_page, room_heart_counts, room_message_payload,
        visible_crew_reactor_ids,
    )
    messages, has_more = chat_messages_page(query, since_id)
    visible_reactor_ids = visible_crew_reactor_ids(crew, g.current_user.id)
    latest_id = messages[-1].id if since_id and has_more else (
        db.session.query(db.func.max(Message.id)).filter(
            Message.crew_id == crew.id,
        ).scalar() or 0
    )
    _advance_chat_read_marker(g.current_user.id, crew.id, latest_id)
    db.session.commit()
    return jsonify({
        'crew': {'id': crew.id, 'name': crew.name},
        'items': [
            room_message_payload(message, visible_reactor_ids)
            for message in messages
        ],
        'heart_counts': room_heart_counts(
            'crew_id', crew.id, visible_reactor_ids,
        ),
        'has_more': has_more,
    })


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
    from backend.routes.chat import (
        prepare_chat_message, room_message_payload, visible_crew_reactor_ids,
    )
    message, replayed, body, send_err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id, crew_id=crew.id,
    )
    if send_err:
        return send_err
    if replayed:
        return jsonify(room_message_payload(
            message, visible_crew_reactor_ids(crew, g.current_user.id),
        )), 200
    for user_id in _member_ids(crew):
        if user_id == g.current_user.id or is_blocked_between(user_id, g.current_user.id):
            continue
        notify(
            user_id,
            'crew_message',
            f'{g.current_user.display_name} in {crew.name}',
            body[:140],
            related_user_id=g.current_user.id,
            related_crew_id=crew.id,
            action_url=f'/#crew/{crew.id}',
            unread_dedupe_key=f'crew-message:{crew.id}',
        )
    db.session.commit()
    return jsonify(room_message_payload(
        message, visible_crew_reactor_ids(crew, g.current_user.id),
    )), 201
