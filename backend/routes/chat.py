"""Direct messaging between players, plus per-court chat rooms."""
import hashlib
import json
import re
from datetime import timedelta

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    Court, CourtChatRead, Game, GameChatRead, GameOpenCall, GamePlayer, League,
    LeagueChatRead, LeagueMember, Message, MessageSendAttempt, Notification,
    Tournament, TournamentChatRead, TournamentEntry, User,
    blocked_pair_ids, is_blocked_between, iso, notify, utcnow,
)
from backend.security import rate_limit

chat_bp = Blueprint('chat', __name__)

CLIENT_MESSAGE_ATTEMPT_ID_MAX_LENGTH = 64
CLIENT_MESSAGE_ATTEMPT_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')
CHAT_DELTA_LIMIT = 200

from backend.routes.auth import login_required  # noqa: E402


def message_image_from(payload):
    """Validate an optional photo attachment. Returns (image, error_response);
    exactly one is set when an image was supplied."""
    image = str(payload.get('image') or '').strip()
    if not image:
        return None, None
    if not image.startswith('data:image/') or len(image) > 700000:
        return None, (jsonify({'error': 'invalid_image'}), 400)
    return image, None


def _client_message_attempt_id(payload):
    """Return (value, valid) for an optional, device-generated retry key."""
    if 'client_attempt_id' not in payload or payload.get('client_attempt_id') is None:
        return None, True
    raw = payload.get('client_attempt_id')
    if not isinstance(raw, str):
        return None, False
    if not raw or len(raw) > CLIENT_MESSAGE_ATTEMPT_ID_MAX_LENGTH:
        return None, False
    if not CLIENT_MESSAGE_ATTEMPT_ID_RE.fullmatch(raw):
        return None, False
    return raw, True


def _message_attempt_fingerprint(scope, body, image):
    canonical = {
        **scope,
        'body': body,
        # Photos can be hundreds of kilobytes; include their digest rather than
        # copying the full data URL into the canonical JSON a second time.
        'image_sha256': hashlib.sha256(image.encode('utf-8')).hexdigest()
        if image else None,
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _stored_message_attempt_fingerprint(message):
    if message.client_attempt_fingerprint is not None:
        return message.client_attempt_fingerprint
    stored_scope = {
        column: getattr(message, column)
        for column in (
            'recipient_id', 'court_id', 'game_id', 'tournament_id',
            'club_id', 'crew_id', 'league_id',
        )
        if getattr(message, column) is not None
    }
    return _message_attempt_fingerprint(
        stored_scope,
        str(message.body or '').strip()[:2000],
        message.image_data,
    )


def _message_attempt_replay(message, fingerprint):
    # Early additive deployments could leave a keyed row without its companion
    # fingerprint. Reconstruct that immutable value from the stored message;
    # blindly accepting the retry would let the same key cross rooms or mutate
    # content.
    stored_fingerprint = _stored_message_attempt_fingerprint(message)
    if stored_fingerprint != fingerprint:
        return None, False, (
            jsonify({'error': 'client_attempt_id_conflict'}), 409,
        )
    return message, True, None


def _message_send_attempt_replay(attempt, fingerprint):
    message = db.session.get(Message, attempt.message_id) \
        if attempt.message_id is not None else None
    stored_fingerprint = attempt.client_attempt_fingerprint
    if stored_fingerprint is None and message is not None:
        stored_fingerprint = _stored_message_attempt_fingerprint(message)
    if stored_fingerprint is None or stored_fingerprint != fingerprint:
        return None, False, (
            jsonify({'error': 'client_attempt_id_conflict'}), 409,
        )
    if attempt.deleted_at is None and message is not None:
        return message, True, None
    # A matching retry is already durably resolved, but its message was later
    # removed by the sender. A 200 lets an offline outbox stop retrying without
    # resurrecting or rendering the deleted content.
    return None, True, (
        jsonify({
            'deleted': True,
            'client_attempt_id': attempt.client_attempt_id,
        }), 200,
    )


def prepare_chat_message(payload, sender_id, **scope):
    """Validate, reserve, and deduplicate one message before side effects.

    The unique sender/key pair is flushed before notifications are created, so
    concurrent retries cannot emit duplicate messages or duplicate pings.
    Returns ``(message, replayed, normalized_body, error_response)``.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return None, False, '', (jsonify({'error': 'invalid_payload'}), 400)
    body = str(payload.get('body') or '').strip()[:2000]
    image, err = message_image_from(payload)
    if err:
        return None, False, body, err
    if not body and not image:
        return None, False, body, (
            jsonify({'error': 'message_body_required'}), 400,
        )
    attempt_id, valid_attempt_id = _client_message_attempt_id(payload)
    if not valid_attempt_id:
        return None, False, body, (
            jsonify({'error': 'invalid_client_attempt_id'}), 400,
        )

    fingerprint = _message_attempt_fingerprint(scope, body, image)
    if attempt_id:
        attempt = MessageSendAttempt.query.filter_by(
            sender_id=sender_id, client_attempt_id=attempt_id,
        ).first()
        if attempt:
            message, replayed, replay_err = _message_send_attempt_replay(
                attempt, fingerprint,
            )
            return message, replayed, body, replay_err

        # Compatibility fallback for a keyed row written immediately before
        # the ledger migration/backfill. The Message uniqueness invariant still
        # keeps this replay safe.
        existing = Message.query.filter_by(
            sender_id=sender_id, client_attempt_id=attempt_id,
        ).first()
        if existing:
            message, replayed, replay_err = _message_attempt_replay(
                existing, fingerprint,
            )
            return message, replayed, body, replay_err

        # Reserve the device key before the Message or any notifications exist.
        # A concurrent contender blocks on this unique flush, then reloads the
        # committed ledger row and returns the same outcome.
        attempt = MessageSendAttempt(
            sender_id=sender_id,
            client_attempt_id=attempt_id,
            client_attempt_fingerprint=fingerprint,
        )
        db.session.add(attempt)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            winner = MessageSendAttempt.query.filter_by(
                sender_id=sender_id, client_attempt_id=attempt_id,
            ).first()
            if winner:
                message, replayed, replay_err = _message_send_attempt_replay(
                    winner, fingerprint,
                )
                return message, replayed, body, replay_err
            existing = Message.query.filter_by(
                sender_id=sender_id, client_attempt_id=attempt_id,
            ).first()
            if existing:
                message, replayed, replay_err = _message_attempt_replay(
                    existing, fingerprint,
                )
                return message, replayed, body, replay_err
            raise
    else:
        attempt = None

    message = Message(
        sender_id=sender_id,
        body=body,
        image_data=image,
        client_attempt_id=attempt_id,
        client_attempt_fingerprint=fingerprint if attempt_id else None,
        **scope,
    )
    db.session.add(message)
    try:
        db.session.flush()
    except IntegrityError:
        if not attempt_id:
            raise
        db.session.rollback()
        existing = Message.query.filter_by(
            sender_id=sender_id, client_attempt_id=attempt_id,
        ).first()
        if existing:
            message, replayed, replay_err = _message_attempt_replay(
                existing, fingerprint,
            )
            return message, replayed, body, replay_err
        raise
    if attempt is not None:
        attempt.message_id = message.id
        db.session.flush()
    return message, False, body, None


def chat_messages_page(query, since_id, initial_limit=60):
    """Return one bounded room page and whether another delta page exists."""
    if since_id:
        rows = (
            query.filter(Message.id > since_id)
            .order_by(Message.id.asc())
            .limit(CHAT_DELTA_LIMIT + 1)
            .all()
        )
        return rows[:CHAT_DELTA_LIMIT], len(rows) > CHAT_DELTA_LIMIT
    return list(reversed(
        query.order_by(Message.id.desc()).limit(initial_limit).all()
    )), False


def _game_open_call_visible_to(call, viewer_id, hidden_ids=None):
    """Whether a typed court card is safe in this viewer's public room."""
    if not call or not call.court_message_id or not viewer_id:
        return False
    game = call.game
    message = call.court_message
    if (
        not game
        or not message
        or message.court_id != game.court_id
        or game.visibility != 'open'
        or game.is_instant
        or game.recurrence != 'none'
    ):
        return False
    roster_ids = {player.user_id for player in game.players}
    hidden_ids = (
        blocked_pair_ids(viewer_id) if hidden_ids is None else set(hidden_ids)
    )
    return not bool(roster_ids & hidden_ids)


def _game_open_calls_by_message(messages, viewer_id, hidden_ids=None):
    message_ids = [message.id for message in messages]
    if not message_ids:
        return {}
    calls = GameOpenCall.query.filter(
        GameOpenCall.court_message_id.in_(message_ids),
    ).all()
    return {
        call.court_message_id: call
        for call in calls
        if _game_open_call_visible_to(call, viewer_id, hidden_ids)
    }


def _hidden_game_open_call_message_ids(viewer_id, court_ids, hidden_ids=None):
    court_ids = {int(court_id) for court_id in court_ids if court_id}
    if not court_ids:
        return set()
    calls = (
        GameOpenCall.query.join(Game, Game.id == GameOpenCall.game_id)
        .filter(
            GameOpenCall.court_message_id.isnot(None),
            Game.court_id.in_(court_ids),
        )
        .all()
    )
    return {
        call.court_message_id
        for call in calls
        if not _game_open_call_visible_to(call, viewer_id, hidden_ids)
    }


def _court_message_payload(
    message, viewer_id, calls_by_message=None, hidden_ids=None, now=None,
):
    payload = message.to_dict()
    calls_by_message = calls_by_message or _game_open_calls_by_message(
        [message], viewer_id, hidden_ids,
    )
    call = calls_by_message.get(message.id)
    if call:
        payload['open_call'] = call.to_dict(viewer_id, now)
    return payload


def _court_open_call_snapshot(
    court_id, viewer_id, hidden_ids=None, now=None, limit=CHAT_DELTA_LIMIT,
):
    calls = (
        GameOpenCall.query.join(Game, Game.id == GameOpenCall.game_id)
        .filter(
            Game.court_id == court_id,
            GameOpenCall.court_message_id.isnot(None),
        )
        .order_by(GameOpenCall.id.desc())
        .limit(limit)
        .all()
    )
    return [
        call.to_dict(viewer_id, now)
        for call in calls
        if _game_open_call_visible_to(call, viewer_id, hidden_ids)
    ]


def room_message_payload(message, allowed_heart_user_ids=None):
    """Serialize a room message, optionally applying a live reaction ACL."""
    payload = message.to_dict()
    if allowed_heart_user_ids is None:
        return payload
    allowed = set(allowed_heart_user_ids)
    heart_user_ids = [
        heart.user_id for heart in message.hearts if heart.user_id in allowed
    ]
    payload['heart_user_ids'] = heart_user_ids
    payload['heart_count'] = len(heart_user_ids)
    return payload


def visible_crew_reactor_ids(crew, viewer_id):
    """Current, active Crew members whose reactions this viewer may see."""
    member_ids = crew.member_ids()
    if not member_ids:
        return set()
    active_ids = {
        user_id for user_id, in db.session.query(User.id).filter(
            User.id.in_(member_ids), User.deleted_at.is_(None),
        ).all()
    }
    return active_ids - blocked_pair_ids(viewer_id)


def room_heart_counts(
    column_name, value, allowed_heart_user_ids=None,
    excluded_message_ids=None,
):
    """Authoritative heart counts for the room's newest bounded window.

    Include zeroes so an idle poll can remove a badge after the last heart is
    toggled off.  Restricting the snapshot to the newest chat window keeps the
    response bounded without erasing older, out-of-window badges in the UI.
    """
    from backend.models import MessageHeart
    join_condition = MessageHeart.message_id == Message.id
    if allowed_heart_user_ids is not None:
        join_condition = and_(
            join_condition,
            MessageHeart.user_id.in_(set(allowed_heart_user_ids)),
        )
    query = (
        db.session.query(Message.id, db.func.count(MessageHeart.id))
        .outerjoin(MessageHeart, join_condition)
        .filter(getattr(Message, column_name) == value)
    )
    if excluded_message_ids:
        query = query.filter(Message.id.notin_(set(excluded_message_ids)))
    rows = (
        query.group_by(Message.id)
        .order_by(Message.id.desc())
        .limit(CHAT_DELTA_LIMIT)
        .all()
    )
    return {str(mid): n for mid, n in rows}


@chat_bp.get('/courts/<int:court_id>/chat')
@login_required
def court_chat(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.court_id == court_id)
    hidden_ids = blocked_pair_ids(g.current_user.id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    request_now = utcnow()
    hidden_call_message_ids = _hidden_game_open_call_message_ids(
        g.current_user.id, {court_id}, hidden_ids,
    )
    if hidden_call_message_ids:
        query = query.filter(Message.id.notin_(hidden_call_message_ids))
    messages, has_more = chat_messages_page(query, since_id)
    calls_by_message = _game_open_calls_by_message(
        messages, g.current_user.id, hidden_ids,
    )

    # Reading the room marks it read — powers the unread badge on court detail.
    latest_id = messages[-1].id if since_id and has_more else (
        db.session.query(db.func.max(Message.id)).filter(
            Message.court_id == court_id,
        ).scalar() or 0
    )
    marker = CourtChatRead.query.filter_by(
        user_id=g.current_user.id, court_id=court.id,
    ).first()
    if not marker:
        db.session.add(CourtChatRead(
            user_id=g.current_user.id, court_id=court.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'court': {'id': court.id, 'name': court.name},
        'items': [
            _court_message_payload(
                message, g.current_user.id, calls_by_message,
                hidden_ids, request_now,
            )
            for message in messages
        ],
        'open_calls': _court_open_call_snapshot(
            court.id, g.current_user.id, hidden_ids, request_now,
        ),
        'heart_counts': room_heart_counts(
            'court_id', court_id,
            excluded_message_ids=hidden_call_message_ids,
        ),
        'has_more': has_more,
    })


@chat_bp.post('/courts/<int:court_id>/chat')
@rate_limit(60, 60)
@login_required
def send_court_message(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    message, replayed, _body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id, court_id=court.id,
    )
    if err:
        return err
    if replayed:
        return jsonify(message.to_dict()), 200
    db.session.commit()
    return jsonify(message.to_dict()), 201


def _game_member_or_403(game_id):
    game = db.session.get(Game, game_id)
    if not game:
        return None, (jsonify({'error': 'game_not_found'}), 404)
    is_member = GamePlayer.query.filter_by(
        game_id=game.id, user_id=g.current_user.id,
    ).first() is not None
    if not is_member:
        return None, (jsonify({'error': 'players_only'}), 403)
    return game, None


@chat_bp.get('/games/<int:game_id>/chat')
@login_required
def game_chat(game_id):
    game, err = _game_member_or_403(game_id)
    if err:
        return err
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.game_id == game_id)
    hidden_ids = blocked_pair_ids(g.current_user.id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    messages, has_more = chat_messages_page(query, since_id)

    # Reading the thread marks it read — powers unread badges on game cards.
    latest_id = messages[-1].id if since_id and has_more else (
        db.session.query(db.func.max(Message.id)).filter(
            Message.game_id == game_id,
        ).scalar() or 0
    )
    marker = GameChatRead.query.filter_by(
        user_id=g.current_user.id, game_id=game.id,
    ).first()
    if not marker:
        db.session.add(GameChatRead(
            user_id=g.current_user.id, game_id=game.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'game': {'id': game.id, 'court_name': game.court.name if game.court else 'Court'},
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('game_id', game_id),
        'has_more': has_more,
    })


@chat_bp.post('/games/<int:game_id>/chat')
@rate_limit(60, 60)
@login_required
def send_game_message(game_id):
    game, err = _game_member_or_403(game_id)
    if err:
        return err
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id, game_id=game.id,
    )
    if err:
        return err
    if replayed:
        return jsonify(message.to_dict()), 200

    # Tell the other players — at most one unread ping per game per player, so
    # an active back-and-forth doesn't flood the activity feed.
    court_name = game.court.name if game.court else 'your game'
    for player in game.players:
        if player.user_id == g.current_user.id:
            continue
        already_pinged = Notification.query.filter_by(
            user_id=player.user_id,
            kind='game_message',
            related_game_id=game.id,
            read=False,
        ).first()
        if not already_pinged:
            notify(
                player.user_id,
                'game_message',
                f'{g.current_user.display_name} in game chat at {court_name}',
                body[:140],
                related_user_id=g.current_user.id,
                related_game_id=game.id,
                unread_dedupe_key=f'game_message:{game.id}',
            )
    db.session.commit()
    return jsonify(message.to_dict()), 201


def _tournament_member_or_403(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return None, (jsonify({'error': 'tournament_not_found'}), 404)
    uid = g.current_user.id
    if uid != tournament.organizer_id and uid not in tournament.participant_ids():
        return None, (jsonify({'error': 'participants_only'}), 403)
    return tournament, None


@chat_bp.get('/tournaments/<int:tournament_id>/chat')
@login_required
def tournament_chat(tournament_id):
    tournament, err = _tournament_member_or_403(tournament_id)
    if err:
        return err
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.tournament_id == tournament_id)
    hidden_ids = blocked_pair_ids(g.current_user.id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    messages, has_more = chat_messages_page(query, since_id)

    # Reading the thread marks it read — powers the tournament-screen badge.
    from backend.models import TournamentChatRead
    latest_id = messages[-1].id if since_id and has_more else (
        db.session.query(db.func.max(Message.id)).filter(
            Message.tournament_id == tournament_id,
        ).scalar() or 0
    )
    marker = TournamentChatRead.query.filter_by(
        user_id=g.current_user.id, tournament_id=tournament.id,
    ).first()
    if not marker:
        db.session.add(TournamentChatRead(
            user_id=g.current_user.id, tournament_id=tournament.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    return jsonify({
        'tournament': {'id': tournament.id, 'name': tournament.name},
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('tournament_id', tournament_id),
        'has_more': has_more,
    })


@chat_bp.post('/tournaments/<int:tournament_id>/chat')
@rate_limit(60, 60)
@login_required
def send_tournament_message(tournament_id):
    tournament, err = _tournament_member_or_403(tournament_id)
    if err:
        return err
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        tournament_id=tournament.id,
    )
    if err:
        return err
    if replayed:
        return jsonify(message.to_dict()), 200

    # Ping everyone else — at most one unread ping per tournament per player,
    # mirroring game chat, so a busy thread doesn't flood the activity feed.
    for uid in tournament.participant_ids() | {tournament.organizer_id}:
        if uid == g.current_user.id:
            continue
        already_pinged = Notification.query.filter_by(
            user_id=uid,
            kind='tournament_message',
            related_tournament_id=tournament.id,
            read=False,
        ).first()
        if not already_pinged:
            notify(
                uid,
                'tournament_message',
                f'{g.current_user.display_name} in {tournament.name} chat',
                body[:140],
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
                unread_dedupe_key=f'tournament_message:{tournament.id}',
            )
    db.session.commit()
    return jsonify(message.to_dict()), 201


@chat_bp.delete('/messages/<int:message_id>')
@rate_limit(60, 3600)
@login_required
def delete_message(message_id):
    """Remove one of your own messages — works across DMs, court rooms, game
    threads, and tournament chats. Open threads elsewhere catch up on reload."""
    message = db.session.get(Message, message_id)
    if not message:
        return jsonify({'error': 'message_not_found'}), 404
    if message.sender_id != g.current_user.id:
        return jsonify({'error': 'not_your_message'}), 403
    open_call = (
        GameOpenCall.query.filter_by(court_message_id=message.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if open_call is not None:
        # Keep the retry ledger but detach the user-authored message. Deleting
        # the generic chat row can never revive or silently orphan its card.
        if open_call.active:
            open_call.active = False
            open_call.ended_at = utcnow()
            open_call.end_reason = 'message_deleted'
        open_call.court_message_id = None
    if message.client_attempt_id:
        fingerprint = _stored_message_attempt_fingerprint(message)
        attempt = MessageSendAttempt.query.filter_by(
            sender_id=message.sender_id,
            client_attempt_id=message.client_attempt_id,
        ).first()
        if attempt is None:
            # Compatibility for a message that predates the ledger backfill.
            # The savepoint contains two simultaneous delete requests without
            # rolling either request's outer transaction back.
            try:
                with db.session.begin_nested():
                    attempt = MessageSendAttempt(
                        sender_id=message.sender_id,
                        client_attempt_id=message.client_attempt_id,
                        client_attempt_fingerprint=fingerprint,
                        message_id=message.id,
                    )
                    db.session.add(attempt)
                    db.session.flush()
            except IntegrityError:
                attempt = MessageSendAttempt.query.filter_by(
                    sender_id=message.sender_id,
                    client_attempt_id=message.client_attempt_id,
                ).first()
        if attempt is not None:
            if attempt.client_attempt_fingerprint is None:
                attempt.client_attempt_fingerprint = fingerprint
            attempt.message_id = None
            attempt.deleted_at = utcnow()
    db.session.delete(message)
    db.session.commit()
    return jsonify({'deleted': True})


@chat_bp.post('/messages/<int:message_id>/heart')
@rate_limit(60, 60)
@login_required
def heart_message(message_id):
    """Toggle a ❤️ on a message. DMs flip a boolean (two people); room chats
    keep one MessageHeart row per admirer, gated by thread membership."""
    from backend.models import MessageHeart
    message = db.session.get(Message, message_id)
    if not message:
        return jsonify({'error': 'message_not_found'}), 404
    me = g.current_user.id

    if message.recipient_id is not None:
        if message.recipient_id != me:
            return jsonify({'error': 'not_your_thread'}), 403
        message.hearted = not message.hearted
        db.session.commit()
        return jsonify({'hearted': message.hearted})

    if not _can_read_message(message, me):
        return jsonify({'error': 'forbidden'}), 403
    existing = MessageHeart.query.filter_by(message_id=message.id, user_id=me).first()
    if existing:
        db.session.delete(existing)
        hearted = False
    else:
        db.session.add(MessageHeart(message_id=message.id, user_id=me))
        hearted = True
    db.session.commit()
    count_query = MessageHeart.query.filter_by(message_id=message.id)
    if message.crew_id is not None:
        from backend.models import Crew
        crew = db.session.get(Crew, message.crew_id)
        allowed_ids = visible_crew_reactor_ids(crew, me) if crew else set()
        count_query = count_query.filter(MessageHeart.user_id.in_(allowed_ids))
    count = count_query.count()
    return jsonify({'hearted': hearted, 'heart_count': count})


@chat_bp.get('/chat/courts')
@login_required
def my_court_rooms():
    """Court chat rooms this player is part of — rooms they've opened (read
    marker exists) plus favorited courts with any chatter. Newest-message
    first, with unread counts."""
    me = g.current_user.id
    hidden_ids = blocked_pair_ids(me)
    from backend.models import FavoriteCourt
    marker_rows = CourtChatRead.query.filter_by(user_id=me).all()
    markers = {m.court_id: m.last_read_message_id for m in marker_rows}
    fav_ids = {
        f.court_id for f in FavoriteCourt.query.filter_by(user_id=me).all()
    }
    court_ids = set(markers) | fav_ids
    if not court_ids:
        return jsonify({'items': []})

    request_now = utcnow()
    hidden_call_message_ids = _hidden_game_open_call_message_ids(
        me, court_ids, hidden_ids,
    )

    latest_ids = (
        db.session.query(db.func.max(Message.id))
        .filter(Message.court_id.in_(court_ids))
        .group_by(Message.court_id)
    )
    if hidden_ids:
        latest_ids = latest_ids.filter(Message.sender_id.notin_(hidden_ids))
    if hidden_call_message_ids:
        latest_ids = latest_ids.filter(
            Message.id.notin_(hidden_call_message_ids),
        )
    latest = Message.query.filter(Message.id.in_(latest_ids)).all()
    by_court = {message.court_id: message for message in latest}
    calls_by_message = _game_open_calls_by_message(latest, me, hidden_ids)
    items = []
    for court_id, last in by_court.items():
        court = db.session.get(Court, court_id)
        if not court:
            continue
        unread_query = Message.query.filter(
            Message.court_id == court_id,
            Message.id > markers.get(court_id, 0),
            Message.sender_id != me,
        )
        if hidden_ids:
            unread_query = unread_query.filter(Message.sender_id.notin_(hidden_ids))
        if hidden_call_message_ids:
            unread_query = unread_query.filter(
                Message.id.notin_(hidden_call_message_ids),
            )
        unread = unread_query.count()
        items.append({
            'court': court.to_summary_dict(),
            'last_message': _court_message_payload(
                last, me, calls_by_message, hidden_ids, request_now,
            ),
            'unread': unread,
        })
    items.sort(key=lambda item: -(item['last_message']['id']))
    # Every unread room stays reachable; recent read rooms fill the compact
    # default window without hiding older attention behind an arbitrary cap.
    unread_items = [item for item in items if item['unread']]
    read_items = [item for item in items if not item['unread']]
    selected = unread_items + read_items[:max(0, 20 - len(unread_items))]
    selected.sort(key=lambda item: -(item['last_message']['id']))
    return jsonify({'items': selected})


def _competition_room_unread(scope_column, marker_model, marker_scope_column,
                             room_ids, user_id):
    """Count unread messages for many rooms without one query per entity."""
    if not room_ids:
        return {}
    query = (
        db.session.query(scope_column, db.func.count(Message.id))
        .outerjoin(marker_model, and_(
            marker_scope_column == scope_column,
            marker_model.user_id == user_id,
        ))
        .filter(
            scope_column.in_(room_ids),
            Message.sender_id != user_id,
            Message.id > db.func.coalesce(
                marker_model.last_read_message_id, 0,
            ),
        )
        .group_by(scope_column)
    )
    hidden_ids = blocked_pair_ids(user_id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    rows = query.all()
    return {room_id: count for room_id, count in rows}


def _latest_competition_room_messages(scope_column, room_ids):
    if not room_ids:
        return {}
    latest_ids = (
        db.session.query(db.func.max(Message.id))
        .filter(scope_column.in_(room_ids))
        .group_by(scope_column)
    )
    messages = Message.query.filter(Message.id.in_(latest_ids)).all()
    return {
        getattr(message, scope_column.key): message for message in messages
    }


def _select_competition_rooms(rooms, limit=40):
    """Keep every active/unread room, then fill by conversation recency."""
    protected = [room for room in rooms if room['_active'] or room['unread']]
    optional = [room for room in rooms if room not in protected]
    optional.sort(
        key=lambda room: room['last_message']['id'] if room['last_message'] else 0,
        reverse=True,
    )
    selected = protected + optional[:max(0, limit - len(protected))]
    messaged = sorted(
        (room for room in selected if room['last_message']),
        key=lambda room: room['last_message']['id'], reverse=True,
    )
    silent = sorted(
        (room for room in selected if not room['last_message']),
        key=lambda room: room['event_at'] or '9999-12-31T23:59:59Z',
    )
    selected = messaged + silent
    for room in selected:
        room.pop('_active', None)
    return selected


def _competition_room_ids(me):
    game_ids = {
        game_id for (game_id,) in db.session.query(GamePlayer.game_id)
        .filter(GamePlayer.user_id == me).all()
    }
    tournament_ids = {
        tournament_id for (tournament_id,) in db.session.query(
            TournamentEntry.tournament_id,
        ).filter(or_(
            TournamentEntry.player1_id == me,
            TournamentEntry.player2_id == me,
        )).all()
    }
    tournament_ids.update(
        tournament_id for (tournament_id,) in db.session.query(Tournament.id)
        .filter(Tournament.organizer_id == me).all()
    )
    league_ids = {
        league_id for (league_id,) in db.session.query(LeagueMember.league_id)
        .filter(LeagueMember.user_id == me).all()
    }
    return game_ids, tournament_ids, league_ids


def _competition_rooms_payload(me):
    """One privacy-safe inbox for every competition conversation.

    Active rooms are reachable before anybody speaks. Recent finished rooms
    remain available when they contain chat, while old silent competitions do
    not turn Community into an archive browser.
    """
    game_ids, tournament_ids, league_ids = _competition_room_ids(me)

    games = {
        row.id: row for row in Game.query.filter(Game.id.in_(game_ids)).all()
    } if game_ids else {}
    tournaments = {
        row.id: row for row in Tournament.query.filter(
            Tournament.id.in_(tournament_ids),
        ).all()
    } if tournament_ids else {}
    leagues = {
        row.id: row for row in League.query.filter(
            League.id.in_(league_ids),
        ).all()
    } if league_ids else {}

    latest = {
        **{('game', room_id): message for room_id, message in
           _latest_competition_room_messages(Message.game_id, games).items()},
        **{('tournament', room_id): message for room_id, message in
           _latest_competition_room_messages(
               Message.tournament_id, tournaments,
           ).items()},
        **{('league', room_id): message for room_id, message in
           _latest_competition_room_messages(Message.league_id, leagues).items()},
    }

    game_unread = _competition_room_unread(
        Message.game_id, GameChatRead, GameChatRead.game_id, games, me,
    )
    tournament_unread = _competition_room_unread(
        Message.tournament_id, TournamentChatRead,
        TournamentChatRead.tournament_id, tournaments, me,
    )
    league_unread = _competition_room_unread(
        Message.league_id, LeagueChatRead, LeagueChatRead.league_id,
        leagues, me,
    )

    cutoff = utcnow() - timedelta(days=30)
    rooms = []

    def add_room(kind, entity, title, status, event_at, court_name, unread):
        last = latest.get((kind, entity.id))
        active_statuses = {
            'game': {'upcoming', 'awaiting_confirmation'},
            'tournament': {'registration', 'active'},
            'league': {'registration', 'active'},
        }[kind]
        if status not in active_statuses and not unread and not (
            last and last.created_at and last.created_at >= cutoff
        ):
            return
        rooms.append({
            'kind': kind,
            'id': entity.id,
            'title': title,
            'status': status,
            'event_at': iso(event_at),
            'court_name': court_name or '',
            'last_message': last.to_dict() if last else None,
            'unread': int(unread or 0),
            '_active': status in active_statuses,
        })

    for game in games.values():
        court_name = game.court.name if game.court else 'Court'
        add_room(
            'game', game, f'Game at {court_name}', game.status,
            game.scheduled_at, court_name, game_unread.get(game.id),
        )
    for tournament in tournaments.values():
        add_room(
            'tournament', tournament, tournament.name, tournament.status,
            tournament.starts_at,
            tournament.court.name if tournament.court else '',
            tournament_unread.get(tournament.id),
        )
    for league in leagues.values():
        add_room(
            'league', league, league.name, league.status, league.starts_at,
            league.court.name if league.court else '',
            league_unread.get(league.id),
        )

    # Never cap away a room that is active or needs attention. Fill the rest
    # of the compact inbox with the freshest completed/read conversations.
    rooms = _select_competition_rooms(rooms)
    return {
        'items': rooms,
        'unread': sum(room['unread'] for room in rooms),
    }


@chat_bp.get('/chat/competitions')
@login_required
def competition_rooms():
    return jsonify(_competition_rooms_payload(g.current_user.id))


def community_room_unread_count(user_id):
    """Unread total for every room shown in Community, suitable for /me."""
    from backend.models import (
        ClubChatRead, ClubMember, Crew, CrewChatRead, CrewMember, FavoriteCourt,
    )

    marker_courts = {
        row.court_id for row in CourtChatRead.query.filter_by(user_id=user_id).all()
    }
    favorite_courts = {
        row.court_id for row in FavoriteCourt.query.filter_by(user_id=user_id).all()
    }
    court_ids = marker_courts | favorite_courts
    court_unread = sum(_competition_room_unread(
        Message.court_id, CourtChatRead, CourtChatRead.court_id,
        court_ids, user_id,
    ).values())

    club_ids = {
        row.club_id for row in ClubMember.query.filter_by(user_id=user_id).all()
    }
    club_unread = sum(_competition_room_unread(
        Message.club_id, ClubChatRead, ClubChatRead.club_id,
        club_ids, user_id,
    ).values())
    crew_ids = {
        row.crew_id for row in CrewMember.query.join(
            Crew, Crew.id == CrewMember.crew_id,
        ).filter(
            CrewMember.user_id == user_id,
            Crew.archived_at.is_(None),
        ).all()
    } | {
        row.id for row in Crew.query.filter(
            Crew.owner_id == user_id, Crew.archived_at.is_(None),
        ).all()
    }
    crew_unread = sum(_competition_room_unread(
        Message.crew_id, CrewChatRead, CrewChatRead.crew_id,
        crew_ids, user_id,
    ).values())
    game_ids, tournament_ids, league_ids = _competition_room_ids(user_id)
    competition_unread = sum(_competition_room_unread(
        Message.game_id, GameChatRead, GameChatRead.game_id,
        game_ids, user_id,
    ).values())
    competition_unread += sum(_competition_room_unread(
        Message.tournament_id, TournamentChatRead,
        TournamentChatRead.tournament_id, tournament_ids, user_id,
    ).values())
    competition_unread += sum(_competition_room_unread(
        Message.league_id, LeagueChatRead, LeagueChatRead.league_id,
        league_ids, user_id,
    ).values())
    return int(court_unread + club_unread + crew_unread + competition_unread)


@chat_bp.get('/chat')
@login_required
def conversations():
    """Conversation list: latest message per partner with unread counts."""
    me = g.current_user.id
    messages = (
        Message.query.filter(
            Message.recipient_id.is_not(None),
            Message.court_id.is_(None),
            Message.game_id.is_(None),
            Message.tournament_id.is_(None),
            Message.club_id.is_(None),
            Message.crew_id.is_(None),
            Message.league_id.is_(None),
            or_(Message.sender_id == me, Message.recipient_id == me),
        )
        .order_by(Message.id.desc())
        .limit(500)
        .all()
    )
    hidden = blocked_pair_ids(me)
    by_partner = {}
    unread = {}
    for message in messages:
        partner_id = message.recipient_id if message.sender_id == me else message.sender_id
        if partner_id in hidden:
            continue
        if partner_id not in by_partner:
            by_partner[partner_id] = message
        if message.recipient_id == me and message.read_at is None:
            unread[partner_id] = unread.get(partner_id, 0) + 1

    partners = {u.id: u for u in User.query.filter(User.id.in_(by_partner.keys())).all()}
    items = []
    for partner_id, last_message in by_partner.items():
        partner = partners.get(partner_id)
        if not partner:
            continue
        items.append({
            'user': partner.to_public_dict(),
            'last_message': last_message.to_dict(),
            'unread': unread.get(partner_id, 0),
        })
    items.sort(key=lambda i: i['last_message']['id'], reverse=True)
    return jsonify({'items': items})


@chat_bp.get('/chat/<int:user_id>')
@login_required
def thread(user_id):
    me = g.current_user.id
    partner = db.session.get(User, user_id)
    if not partner or partner.deleted_at is not None:
        return jsonify({'error': 'user_not_found'}), 404

    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(
        Message.court_id.is_(None),
        or_(
            (Message.sender_id == me) & (Message.recipient_id == user_id),
            (Message.sender_id == user_id) & (Message.recipient_id == me),
        ),
    )
    messages, has_more = chat_messages_page(query, since_id, initial_limit=100)

    now = utcnow()
    changed = False
    for message in messages:
        if message.recipient_id == me and message.read_at is None:
            message.read_at = now
            changed = True
    if changed:
        db.session.commit()

    # Watermark for live ✓✓: the newest of my messages the partner has read.
    # Polls carry it too, so receipts flip without reopening the thread.
    read_up_to = db.session.query(db.func.max(Message.id)).filter(
        Message.sender_id == me,
        Message.recipient_id == user_id,
        Message.read_at.isnot(None),
    ).scalar() or 0

    # Hearts on MY messages ride every poll so they light up live.
    hearted_ids = [row[0] for row in db.session.query(Message.id).filter(
        Message.sender_id == me,
        Message.recipient_id == user_id,
        Message.hearted.is_(True),
    ).order_by(Message.id.desc()).limit(100).all()]

    return jsonify({
        'user': partner.to_public_dict(),
        'items': [m.to_dict() for m in messages],
        'partner_read_up_to': read_up_to,
        'hearted_ids': hearted_ids,
        'has_more': has_more,
    })


@chat_bp.post('/chat/<int:user_id>')
@rate_limit(60, 60)
@login_required
def send_message(user_id):
    partner = db.session.get(User, user_id)
    if not partner or partner.deleted_at is not None:
        return jsonify({'error': 'user_not_found'}), 404
    if partner.id == g.current_user.id:
        return jsonify({'error': 'cannot_message_self'}), 400
    if is_blocked_between(g.current_user.id, partner.id):
        return jsonify({'error': 'user_blocked'}), 403

    message, replayed, _body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        recipient_id=partner.id,
    )
    if err:
        return err
    if replayed:
        return jsonify(message.to_dict()), 200
    db.session.commit()
    return jsonify(message.to_dict()), 201


@chat_bp.get('/messages/<int:message_id>/image')
@login_required
def message_image(message_id):
    """The photo attached to a message — visible to exactly whoever can read
    that thread (DM pair, game players, tournament/club/crew/league members;
    court rooms are open to any signed-in player)."""
    message = db.session.get(Message, message_id)
    if not message or not message.image_data:
        return jsonify({'error': 'image_not_found'}), 404
    if not _can_read_message(message, g.current_user.id):
        return jsonify({'error': 'forbidden'}), 403
    return jsonify({'image': message.image_data})


def _can_read_message(message, me):
    """Thread-scoped read access — shared by image fetches and reactions."""
    # Room list endpoints already hide messages from either side of a block.
    # Apply that same boundary to separately fetched image payloads and heart
    # actions so a cached message id cannot bypass the filtered thread.
    if message.sender_id != me and is_blocked_between(me, message.sender_id):
        return False
    if message.recipient_id is not None:
        return me in (message.sender_id, message.recipient_id)
    if message.court_id is not None:
        open_call = GameOpenCall.query.filter_by(
            court_message_id=message.id,
        ).first()
        if open_call is not None:
            return _game_open_call_visible_to(open_call, me)
        return True  # court rooms are readable by any signed-in player
    if message.game_id is not None:
        return GamePlayer.query.filter_by(
            game_id=message.game_id, user_id=me,
        ).first() is not None
    if message.tournament_id is not None:
        tournament = db.session.get(Tournament, message.tournament_id)
        return tournament is not None and (
            me == tournament.organizer_id or me in tournament.participant_ids()
        )
    if message.club_id is not None:
        from backend.models import ClubMember
        return ClubMember.query.filter_by(
            club_id=message.club_id, user_id=me,
        ).first() is not None
    if message.crew_id is not None:
        from backend.models import Crew, CrewMember
        crew = Crew.query.filter(
            Crew.id == message.crew_id, Crew.archived_at.is_(None),
        ).first()
        return crew is not None and (
            crew.owner_id == me
            or CrewMember.query.filter_by(
                crew_id=message.crew_id, user_id=me,
            ).first() is not None
        )
    if message.league_id is not None:
        from backend.models import LeagueMember
        return LeagueMember.query.filter_by(
            league_id=message.league_id, user_id=me,
        ).first() is not None
    return False
