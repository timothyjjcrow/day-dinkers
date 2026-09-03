"""Direct messaging between players, plus per-court chat rooms."""
import hashlib
import json
import re
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import and_, case, or_
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    Club, ClubChatRead, ClubMember, Court, CourtChatRead,
    CourtChatSubscription, Crew, CrewChatRead, CrewMember,
    DirectChatPreference, Friendship, Game, GameChatRead,
    GameOpenCall, GamePlayer, League, LeagueChatRead, LeagueMember, Message,
    MessageSendAttempt, Notification, Tournament, TournamentChatRead,
    TournamentEntry, User,
    blocked_pair_ids, can_direct_message, is_blocked_between, iso, notify, utcnow,
)
from backend.security import rate_limit
from backend.services.conversations import (
    advance_conversation_read, conversation_ref,
)

chat_bp = Blueprint('chat', __name__)

CLIENT_MESSAGE_ATTEMPT_ID_MAX_LENGTH = 64
CLIENT_MESSAGE_ATTEMPT_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')
CHAT_DELTA_LIMIT = 200
CHAT_HISTORY_LIMIT_MAX = 100
CHAT_INBOX_DEFAULT_LIMIT = 50
CHAT_CURSOR_MAX = (1 << 63) - 1

from backend.routes.auth import login_required  # noqa: E402


def _direct_chat_muted(user_id, partner_id):
    return DirectChatPreference.query.filter_by(
        user_id=user_id, partner_id=partner_id,
    ).filter(DirectChatPreference.muted_at.isnot(None)).first() is not None


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


def prepare_chat_message(payload, sender_id, conversation=None, **scope):
    """Validate, reserve, and deduplicate one message before side effects.

    The unique sender/key pair is flushed before notifications are created, so
    concurrent retries cannot emit duplicate messages or duplicate pings.
    Returns ``(message, replayed, normalized_body, error_response)``.
    """
    if conversation is not None:
        if scope:
            raise ValueError('conversation and legacy scope cannot be combined')
        # Fingerprints intentionally retain the legacy scope-only shape so a
        # device retry created before this additive migration still resolves
        # to the same durable send attempt. New rows carry both identities.
        scope = conversation.message_scope
        fingerprint_scope = dict(scope)
        scope['conversation_id'] = conversation.ensure_persisted().id
    else:
        fingerprint_scope = scope
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

    fingerprint = _message_attempt_fingerprint(fingerprint_scope, body, image)
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


def chat_window_args(initial_limit=60, allow_since=True):
    """Parse one room-history window without silently changing its meaning.

    ``since_id`` remains the forward-poll cursor used by existing clients.
    ``before_id`` is deliberately exclusive with it: treating an invalid or
    ambiguous history cursor as an initial load could unexpectedly return the
    newest messages and mark them read.
    """
    raw_since = request.args.get('since_id') if allow_since else None
    raw_before = request.args.get('before_id')
    if not allow_since and request.args.get('since_id') is not None:
        return None, (jsonify({'error': 'invalid_since_id'}), 400)
    if raw_since is not None and raw_before is not None:
        return None, (jsonify({'error': 'conflicting_chat_cursors'}), 400)

    since_id = None
    if raw_since is not None:
        try:
            since_id = int(raw_since)
        except (TypeError, ValueError):
            return None, (jsonify({'error': 'invalid_since_id'}), 400)
        if since_id < 0 or since_id > CHAT_CURSOR_MAX:
            return None, (jsonify({'error': 'invalid_since_id'}), 400)

    before_id = None
    if raw_before is not None:
        try:
            before_id = int(raw_before)
        except (TypeError, ValueError):
            return None, (jsonify({'error': 'invalid_before_id'}), 400)
        if before_id <= 0 or before_id > CHAT_CURSOR_MAX:
            return None, (jsonify({'error': 'invalid_before_id'}), 400)

    raw_limit = request.args.get('limit')
    if raw_limit is None:
        history_limit = initial_limit
    else:
        try:
            history_limit = int(raw_limit)
        except (TypeError, ValueError):
            return None, (jsonify({'error': 'invalid_limit'}), 400)
        if history_limit <= 0:
            return None, (jsonify({'error': 'invalid_limit'}), 400)
        history_limit = min(history_limit, CHAT_HISTORY_LIMIT_MAX)
    return (since_id, before_id, history_limit), None


def chat_messages_window(
        query, since_id, before_id=None, initial_limit=60,
        history_limit=None):
    """Return a bounded forward poll or a newest-first history window.

    History rows are serialized in their original chronological order.  The
    cursor always points at the oldest returned row, so the next request with
    ``before_id`` neither repeats nor skips a message from a stable result set.
    ``has_more`` intentionally retains its legacy meaning: another *forward*
    delta page exists after a capped ``since_id`` poll.
    """
    if since_id:
        rows = (
            query.filter(Message.id > since_id)
            .order_by(Message.id.asc())
            .limit(CHAT_DELTA_LIMIT + 1)
            .all()
        )
        return (
            rows[:CHAT_DELTA_LIMIT], len(rows) > CHAT_DELTA_LIMIT,
            False, None,
        )

    limit = history_limit or initial_limit
    history_query = query
    if before_id is not None:
        history_query = history_query.filter(Message.id < before_id)
    rows = (
        history_query.order_by(Message.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_older = len(rows) > limit
    messages = list(reversed(rows[:limit]))
    return (
        messages,
        False,
        has_older,
        messages[0].id if has_older and messages else None,
    )


def chat_messages_page(query, since_id, initial_limit=60):
    """Legacy two-value wrapper used by clients not yet paging backward."""
    messages, has_more, _has_older, _next_before_id = chat_messages_window(
        query, since_id, initial_limit=initial_limit,
        history_limit=initial_limit,
    )
    return messages, has_more


def chat_read_marker_target(
        query, messages, since_id, before_id, has_more):
    """Choose the newest message this particular response actually reveals."""
    if before_id is not None:
        return messages[-1].id if messages else 0
    if since_id and has_more:
        return messages[-1].id if messages else since_id
    return query.with_entities(db.func.max(Message.id)).scalar() or 0


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


def court_room_summaries(user_id, court_ids, muted_court_ids=None):
    """One ACL-consistent preview/unread contract for every court surface."""
    court_ids = {int(court_id) for court_id in court_ids if court_id}
    if not court_ids:
        return {}
    hidden_ids = blocked_pair_ids(user_id)
    muted_court_ids = set(muted_court_ids or ())
    markers = {
        row.court_id: row.last_read_message_id
        for row in CourtChatRead.query.filter(
            CourtChatRead.user_id == user_id,
            CourtChatRead.court_id.in_(court_ids),
        ).all()
    }
    hidden_call_message_ids = _hidden_game_open_call_message_ids(
        user_id, court_ids, hidden_ids,
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
    latest_by_court = {message.court_id: message for message in latest}
    calls_by_message = _game_open_calls_by_message(
        latest, user_id, hidden_ids,
    )
    request_now = utcnow()
    summaries = {}
    for court_id in court_ids:
        last = latest_by_court.get(court_id)
        unread = 0
        if court_id not in muted_court_ids:
            unread_query = Message.query.filter(
                Message.court_id == court_id,
                Message.id > markers.get(court_id, 0),
                Message.sender_id != user_id,
            )
            if hidden_ids:
                unread_query = unread_query.filter(
                    Message.sender_id.notin_(hidden_ids),
                )
            if hidden_call_message_ids:
                unread_query = unread_query.filter(
                    Message.id.notin_(hidden_call_message_ids),
                )
            unread = unread_query.count()
        summaries[court_id] = {
            'last_message': (
                _court_message_payload(
                    last, user_id, calls_by_message, hidden_ids, request_now,
                )
                if last else None
            ),
            'unread': unread,
        }
    return summaries


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
    conversation = conversation_ref('court', court.id)
    window, window_err = chat_window_args()
    if window_err:
        return window_err
    since_id, before_id, history_limit = window
    query = conversation.message_query()
    hidden_ids = blocked_pair_ids(g.current_user.id)
    if hidden_ids:
        query = query.filter(Message.sender_id.notin_(hidden_ids))
    request_now = utcnow()
    hidden_call_message_ids = _hidden_game_open_call_message_ids(
        g.current_user.id, {court_id}, hidden_ids,
    )
    if hidden_call_message_ids:
        query = query.filter(Message.id.notin_(hidden_call_message_ids))
    messages, has_more, has_older, next_before_id = chat_messages_window(
        query, since_id, before_id, history_limit=history_limit,
    )
    calls_by_message = _game_open_calls_by_message(
        messages, g.current_user.id, hidden_ids,
    )

    # Reading the room marks it read — powers the unread badge on court detail.
    latest_id = chat_read_marker_target(
        query, messages, since_id, before_id, has_more,
    )
    advance_conversation_read(conversation, g.current_user.id, latest_id)
    db.session.commit()

    subscription = CourtChatSubscription.query.filter_by(
        user_id=g.current_user.id, court_id=court.id,
    ).first()
    return jsonify({
        'conversation': conversation.to_dict(court.name),
        'court': {'id': court.id, 'name': court.name},
        'subscription': (
            subscription.to_dict() if subscription
            else {'joined': False, 'muted': False, 'joined_at': None, 'muted_at': None}
        ),
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
        'has_older': has_older,
        'next_before_id': next_before_id,
    })


@chat_bp.post('/courts/<int:court_id>/chat')
@rate_limit(60, 60)
@login_required
def send_court_message(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    conversation = conversation_ref('court', court.id)
    message, replayed, _body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        conversation=conversation,
    )
    if err:
        return err
    if replayed:
        return jsonify(conversation.decorate_message(message, court.name)), 200
    db.session.commit()
    return jsonify(conversation.decorate_message(message, court.name)), 201


@chat_bp.put('/courts/<int:court_id>/chat/subscription')
@rate_limit(60, 60)
@login_required
def update_court_chat_subscription(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {'joined', 'muted'}:
        return jsonify({'error': 'invalid_subscription'}), 400
    joined = payload.get('joined')
    if not isinstance(joined, bool):
        return jsonify({'error': 'invalid_subscription'}), 400
    muted = payload.get('muted', False)
    if not isinstance(muted, bool):
        return jsonify({'error': 'invalid_subscription'}), 400

    subscription = (
        CourtChatSubscription.query.filter_by(
            user_id=g.current_user.id, court_id=court.id,
        )
        .with_for_update()
        .first()
    )
    if not joined:
        if subscription:
            db.session.delete(subscription)
        db.session.commit()
        return jsonify({
            'joined': False, 'muted': False,
            'joined_at': None, 'muted_at': None,
        })

    now = utcnow()
    if not subscription:
        subscription = CourtChatSubscription(
            user_id=g.current_user.id,
            court_id=court.id,
            joined_at=now,
        )
        db.session.add(subscription)
        # Joining starts from now; reading remains a separate, durable marker.
        latest_id = db.session.query(db.func.max(Message.id)).filter(
            Message.court_id == court.id,
        ).scalar() or 0
        advance_conversation_read(
            conversation_ref('court', court.id),
            g.current_user.id,
            latest_id,
        )
    subscription.muted_at = now if muted else None
    db.session.commit()
    return jsonify(subscription.to_dict())


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
    conversation = conversation_ref('game', game.id)
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

    # Reading the thread marks it read — powers unread badges on game cards.
    latest_id = chat_read_marker_target(
        query, messages, since_id, before_id, has_more,
    )
    advance_conversation_read(conversation, g.current_user.id, latest_id)
    db.session.commit()

    return jsonify({
        'conversation': conversation.to_dict(
            game.court.name if game.court else 'Play session',
        ),
        'game': {
            'id': game.id,
            'game_type': game.game_type,
            'court_name': game.court.name if game.court else 'Court',
            'scheduled_at': iso(game.scheduled_at),
            'players': [
                player.user.to_public_dict()
                for player in sorted(game.players, key=lambda row: row.id)
                if player.user and not player.user.deleted_at
            ],
        },
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('game_id', game_id),
        'has_more': has_more,
        'has_older': has_older,
        'next_before_id': next_before_id,
    })


@chat_bp.post('/games/<int:game_id>/chat')
@rate_limit(60, 60)
@login_required
def send_game_message(game_id):
    game, err = _game_member_or_403(game_id)
    if err:
        return err
    conversation = conversation_ref('game', game.id)
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        conversation=conversation,
    )
    if err:
        return err
    conversation_name = game.court.name if game.court else 'Play session'
    if replayed:
        return jsonify(
            conversation.decorate_message(message, conversation_name)
        ), 200

    # Tell the other players — at most one unread ping per game per player, so
    # an active back-and-forth doesn't flood the activity feed.
    court_name = game.court.name if game.court else 'a local court'
    play_label = (
        'ranked match' if game.game_type == 'ranked'
        else 'casual play session'
    )
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
                f'{g.current_user.display_name} in {play_label} chat at {court_name}',
                body[:140],
                related_user_id=g.current_user.id,
                related_game_id=game.id,
                unread_dedupe_key=f'game_message:{game.id}',
            )
    db.session.commit()
    return jsonify(
        conversation.decorate_message(message, conversation_name)
    ), 201


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
    conversation = conversation_ref('tournament', tournament.id)
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

    # Reading the thread marks it read — powers the tournament-screen badge.
    latest_id = chat_read_marker_target(
        query, messages, since_id, before_id, has_more,
    )
    advance_conversation_read(conversation, g.current_user.id, latest_id)
    db.session.commit()

    return jsonify({
        'conversation': conversation.to_dict(tournament.name),
        'tournament': {'id': tournament.id, 'name': tournament.name},
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('tournament_id', tournament_id),
        'has_more': has_more,
        'has_older': has_older,
        'next_before_id': next_before_id,
    })


@chat_bp.post('/tournaments/<int:tournament_id>/chat')
@rate_limit(60, 60)
@login_required
def send_tournament_message(tournament_id):
    tournament, err = _tournament_member_or_403(tournament_id)
    if err:
        return err
    conversation = conversation_ref('tournament', tournament.id)
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        conversation=conversation,
    )
    if err:
        return err
    if replayed:
        return jsonify(
            conversation.decorate_message(message, tournament.name)
        ), 200

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
    return jsonify(
        conversation.decorate_message(message, tournament.name)
    ), 201


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
    """Explicitly joined court chats plus a discoverable home-court room."""
    me = g.current_user.id
    subscriptions = CourtChatSubscription.query.filter_by(user_id=me).all()
    subscriptions_by_court = {row.court_id: row for row in subscriptions}
    court_ids = set(subscriptions_by_court)
    if g.current_user.home_court_id:
        court_ids.add(g.current_user.home_court_id)
    if not court_ids:
        return jsonify({'items': []})
    muted_ids = {
        row.court_id for row in subscriptions if row.muted_at is not None
    }
    summaries = court_room_summaries(me, court_ids, muted_ids)
    courts = {
        court.id: court
        for court in Court.query.filter(Court.id.in_(court_ids)).all()
    }
    items = []
    for court_id in court_ids:
        court = courts.get(court_id)
        if not court:
            continue
        subscription = subscriptions_by_court.get(court_id)
        summary = summaries.get(court_id, {'last_message': None, 'unread': 0})
        items.append({
            'court': court.to_summary_dict(),
            'last_message': summary['last_message'],
            'unread': summary['unread'] if subscription else 0,
            'joined': subscription is not None,
            'muted': bool(subscription and subscription.muted_at),
            'is_home': court_id == g.current_user.home_court_id,
        })
    items.sort(key=lambda item: -(
        item['last_message']['id'] if item['last_message'] else 0
    ))
    # Every unread room stays reachable; recent read rooms fill the compact
    # default window without hiding older attention behind an arbitrary cap.
    unread_items = [item for item in items if item['unread']]
    read_items = [item for item in items if not item['unread']]
    selected = unread_items + read_items[:max(0, 20 - len(unread_items))]
    selected.sort(key=lambda item: -(
        item['last_message']['id'] if item['last_message'] else 0
    ))
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
        play_title = (
            'Ranked match' if game.game_type == 'ranked'
            else 'Casual play session'
        )
        add_room(
            'game', game, f'{play_title} at {court_name}', game.status,
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


def _response_json(result):
    response = result[0] if isinstance(result, tuple) else result
    status = result[1] if isinstance(result, tuple) and len(result) > 1 \
        else getattr(response, 'status_code', 200)
    return response.get_json(), int(status)


@chat_bp.get('/inbox')
@login_required
def unified_inbox():
    """One resilient round-trip for every Community conversation type."""
    # Import lazily to preserve the blueprint dependency boundary while
    # reusing each scope's optimized, privacy-aware payload implementation.
    from backend.routes.clubs import my_clubs
    from backend.routes.crews import my_crews

    components = {}
    errors = {}
    for key, loader in (
        ('direct', conversations),
        ('courts', my_court_rooms),
        ('clubs', my_clubs),
        ('competitions', competition_rooms),
        ('crews', my_crews),
    ):
        try:
            view = getattr(loader, '__wrapped__', loader)
            payload, status = _response_json(view())
            if status >= 400:
                raise RuntimeError(f'{key} inbox source returned {status}')
            components[key] = payload
        except Exception:
            # One room family must not blank the user's working inbox. The
            # response identifies the missing family so the client can keep
            # useful rows visible and offer one scoped retry.
            current_app.logger.exception('community inbox source failed: %s', key)
            components[key] = {'items': []}
            if key == 'crews':
                components[key]['invitations'] = []
            errors[key] = 'unavailable'
    return jsonify({**components, 'errors': errors})


def _mark_room_set_read(kind, message_scope_column, room_ids, user_id):
    room_ids = set(room_ids or ())
    if not room_ids:
        return 0
    latest_by_room = {
        int(room_id): int(latest_id)
        for room_id, latest_id in db.session.query(
            message_scope_column, db.func.max(Message.id),
        ).filter(
            message_scope_column.in_(room_ids),
        ).group_by(message_scope_column).all()
        if room_id is not None and latest_id is not None
    }
    if not latest_by_room:
        return 0
    for room_id, latest_id in latest_by_room.items():
        advance_conversation_read(
            conversation_ref(kind, room_id), user_id, latest_id,
        )
    return len(latest_by_room)


@chat_bp.post('/chat/read-all')
@rate_limit(30, 60)
@login_required
def mark_all_chats_read():
    """Advance every conversation the player explicitly belongs to."""
    me = g.current_user.id
    now = utcnow()
    direct_count = Message.query.filter(
        Message.recipient_id == me,
        Message.read_at.is_(None),
        Message.court_id.is_(None),
        Message.game_id.is_(None),
        Message.tournament_id.is_(None),
        Message.club_id.is_(None),
        Message.crew_id.is_(None),
        Message.league_id.is_(None),
    ).update({'read_at': now}, synchronize_session=False)

    court_ids = {
        row.court_id for row in CourtChatSubscription.query.filter_by(
            user_id=me,
        ).all()
    }
    club_ids = {
        row.club_id for row in ClubMember.query.join(
            Club, Club.id == ClubMember.club_id,
        ).filter(
            ClubMember.user_id == me,
            Club.archived_at.is_(None),
        ).all()
    }
    crew_ids = {
        row.crew_id for row in CrewMember.query.join(
            Crew, Crew.id == CrewMember.crew_id,
        ).filter(
            CrewMember.user_id == me,
            Crew.archived_at.is_(None),
        ).all()
    } | {
        row.id for row in Crew.query.filter(
            Crew.owner_id == me, Crew.archived_at.is_(None),
        ).all()
    }
    game_ids, tournament_ids, league_ids = _competition_room_ids(me)
    room_count = 0
    for kind, message_column, room_ids in (
        ('court', Message.court_id, court_ids),
        ('club', Message.club_id, club_ids),
        ('crew', Message.crew_id, crew_ids),
        ('game', Message.game_id, game_ids),
        ('tournament', Message.tournament_id, tournament_ids),
        ('league', Message.league_id, league_ids),
    ):
        room_count += _mark_room_set_read(
            kind, message_column, room_ids, me,
        )
    db.session.commit()
    return jsonify({
        'ok': True,
        'direct_messages_marked': int(direct_count or 0),
        'rooms_marked': room_count,
    })


def community_room_unread_counts(user_id):
    """Unread room totals split across the Messages and Groups lanes."""
    from backend.models import ClubChatRead, ClubMember, Crew, CrewChatRead, CrewMember

    active_court_subscriptions = CourtChatSubscription.query.filter(
        CourtChatSubscription.user_id == user_id,
        CourtChatSubscription.muted_at.is_(None),
    ).all()
    court_ids = {row.court_id for row in active_court_subscriptions}
    court_unread = sum(
        summary['unread']
        for summary in court_room_summaries(user_id, court_ids).values()
    )

    club_ids = {
        row.club_id for row in ClubMember.query.filter(
            ClubMember.user_id == user_id,
            ClubMember.notification_level != 'off',
        ).all()
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
    game_unread = sum(_competition_room_unread(
        Message.game_id, GameChatRead, GameChatRead.game_id,
        game_ids, user_id,
    ).values())
    tournament_unread = sum(_competition_room_unread(
        Message.tournament_id, TournamentChatRead,
        TournamentChatRead.tournament_id, tournament_ids, user_id,
    ).values())
    league_unread = sum(_competition_room_unread(
        Message.league_id, LeagueChatRead, LeagueChatRead.league_id,
        league_ids, user_id,
    ).values())
    messages = int(game_unread)
    groups = int(
        court_unread + club_unread + crew_unread
        + tournament_unread + league_unread
    )
    return {'messages': messages, 'groups': groups, 'total': messages + groups}


def community_room_unread_count(user_id):
    """Backward-compatible aggregate unread total for every Community room."""
    return community_room_unread_counts(user_id)['total']


@chat_bp.get('/chat')
@login_required
def conversations():
    """Cursor-paged DM inbox: latest message per visible partner."""
    me = g.current_user.id
    window, window_err = chat_window_args(
        initial_limit=CHAT_INBOX_DEFAULT_LIMIT, allow_since=False,
    )
    if window_err:
        return window_err
    _since_id, before_id, limit = window

    direct_filters = (
        Message.recipient_id.is_not(None),
        Message.court_id.is_(None),
        Message.game_id.is_(None),
        Message.tournament_id.is_(None),
        Message.club_id.is_(None),
        Message.crew_id.is_(None),
        Message.league_id.is_(None),
        or_(Message.sender_id == me, Message.recipient_id == me),
    )
    partner_id = case(
        (Message.sender_id == me, Message.recipient_id),
        else_=Message.sender_id,
    )
    latest_message_id = db.func.max(Message.id)
    hidden = blocked_pair_ids(me)
    latest_by_partner = db.session.query(
        partner_id.label('partner_id'),
        latest_message_id.label('last_message_id'),
    ).filter(*direct_filters, partner_id != me)
    if hidden:
        latest_by_partner = latest_by_partner.filter(
            partner_id.notin_(hidden),
        )
    latest_by_partner = latest_by_partner.group_by(partner_id)
    if before_id is not None:
        latest_by_partner = latest_by_partner.having(
            latest_message_id < before_id,
        )
    page_rows = (
        latest_by_partner.order_by(latest_message_id.desc())
        .limit(limit + 1)
        .all()
    )
    has_older = len(page_rows) > limit
    rows = page_rows[:limit]
    partner_ids = [int(row.partner_id) for row in rows]
    last_message_ids = [int(row.last_message_id) for row in rows]

    partners = {
        user.id: user for user in User.query.filter(
            User.id.in_(partner_ids),
        ).all()
    } if partner_ids else {}
    last_messages = {
        message.id: message for message in Message.query.filter(
            Message.id.in_(last_message_ids),
        ).all()
    } if last_message_ids else {}
    unread = {
        int(sender_id): int(count)
        for sender_id, count in db.session.query(
            Message.sender_id, db.func.count(Message.id),
        ).filter(
            Message.sender_id.in_(partner_ids),
            Message.recipient_id == me,
            Message.court_id.is_(None),
            Message.game_id.is_(None),
            Message.tournament_id.is_(None),
            Message.club_id.is_(None),
            Message.crew_id.is_(None),
            Message.league_id.is_(None),
            Message.read_at.is_(None),
        ).group_by(Message.sender_id).all()
    } if partner_ids else {}
    friend_partner_ids = set()
    if partner_ids:
        friendships = Friendship.query.filter(
            Friendship.status == 'accepted',
            or_(
                and_(
                    Friendship.requester_id == me,
                    Friendship.addressee_id.in_(partner_ids),
                ),
                and_(
                    Friendship.addressee_id == me,
                    Friendship.requester_id.in_(partner_ids),
                ),
            ),
        ).all()
        friend_partner_ids = {
            row.addressee_id if row.requester_id == me else row.requester_id
            for row in friendships
        }
    muted_partner_ids = {
        int(row[0]) for row in db.session.query(
            DirectChatPreference.partner_id,
        ).filter(
            DirectChatPreference.user_id == me,
            DirectChatPreference.partner_id.in_(partner_ids),
            DirectChatPreference.muted_at.isnot(None),
        ).all()
    } if partner_ids else set()

    items = []
    for row in rows:
        current_partner_id = int(row.partner_id)
        partner = partners.get(current_partner_id)
        last_message = last_messages.get(int(row.last_message_id))
        if not partner or not last_message:
            continue
        items.append({
            'user': partner.to_public_dict(),
            'last_message': last_message.to_dict(),
            'unread': (
                0 if current_partner_id in muted_partner_ids
                else unread.get(current_partner_id, 0)
            ),
            'message_request': current_partner_id not in friend_partner_ids,
            'muted': current_partner_id in muted_partner_ids,
        })
    return jsonify({
        'items': items,
        'has_older': has_older,
        'next_before_id': (
            int(rows[-1].last_message_id) if has_older and rows else None
        ),
    })


@chat_bp.get('/chat/<int:user_id>')
@login_required
def thread(user_id):
    me = g.current_user.id
    partner = db.session.get(User, user_id)
    if not partner or partner.deleted_at is not None:
        return jsonify({'error': 'user_not_found'}), 404
    if is_blocked_between(me, user_id):
        return jsonify({'error': 'user_blocked'}), 403
    if not can_direct_message(me, user_id):
        return jsonify({'error': 'message_not_allowed'}), 403
    is_friend = Friendship.query.filter(
        Friendship.status == 'accepted',
        or_(
            and_(Friendship.requester_id == me, Friendship.addressee_id == user_id),
            and_(Friendship.requester_id == user_id, Friendship.addressee_id == me),
        ),
    ).first() is not None

    window, window_err = chat_window_args(initial_limit=100)
    if window_err:
        return window_err
    since_id, before_id, history_limit = window
    query = Message.query.filter(
        Message.court_id.is_(None),
        or_(
            (Message.sender_id == me) & (Message.recipient_id == user_id),
            (Message.sender_id == user_id) & (Message.recipient_id == me),
        ),
    )
    messages, has_more, has_older, next_before_id = chat_messages_window(
        query, since_id, before_id, initial_limit=100,
        history_limit=history_limit,
    )

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
        'has_older': has_older,
        'next_before_id': next_before_id,
        'message_request': not is_friend,
        'muted': _direct_chat_muted(me, user_id),
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
    if not can_direct_message(g.current_user.id, partner.id):
        return jsonify({'error': 'message_not_allowed'}), 403

    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        recipient_id=partner.id,
    )
    if err:
        return err
    if replayed:
        return jsonify(message.to_dict()), 200
    is_friend = Friendship.query.filter(
        Friendship.status == 'accepted',
        or_(
            and_(Friendship.requester_id == g.current_user.id, Friendship.addressee_id == partner.id),
            and_(Friendship.requester_id == partner.id, Friendship.addressee_id == g.current_user.id),
        ),
    ).first() is not None
    if not _direct_chat_muted(partner.id, g.current_user.id):
        notify(
            partner.id,
            'direct_message',
            f'{"New message" if is_friend else "Message request"} from {g.current_user.display_name}',
            body[:140] if body else 'Sent you a photo',
            related_user_id=g.current_user.id,
            action_url=f'/#chat/{g.current_user.id}',
            unread_dedupe_key=f'direct_message:{g.current_user.id}',
        )
    db.session.commit()
    return jsonify(message.to_dict()), 201


@chat_bp.put('/chat/<int:user_id>/settings')
@rate_limit(60, 60)
@login_required
def direct_chat_settings(user_id):
    partner = db.session.get(User, user_id)
    if not partner or partner.deleted_at is not None or partner.id == g.current_user.id:
        return jsonify({'error': 'user_not_found'}), 404
    if is_blocked_between(g.current_user.id, partner.id):
        return jsonify({'error': 'user_blocked'}), 403
    if not can_direct_message(g.current_user.id, partner.id):
        return jsonify({'error': 'message_not_allowed'}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {'muted'} \
            or not isinstance(payload.get('muted'), bool):
        return jsonify({'error': 'invalid_settings'}), 400
    preference = DirectChatPreference.query.filter_by(
        user_id=g.current_user.id, partner_id=partner.id,
    ).with_for_update().first()
    if not preference:
        preference = DirectChatPreference(
            user_id=g.current_user.id, partner_id=partner.id,
        )
        db.session.add(preference)
    preference.muted_at = utcnow() if payload['muted'] else None
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        preference = DirectChatPreference.query.filter_by(
            user_id=g.current_user.id, partner_id=partner.id,
        ).with_for_update().first()
        preference.muted_at = utcnow() if payload['muted'] else None
        db.session.commit()
    return jsonify(preference.to_dict())


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
