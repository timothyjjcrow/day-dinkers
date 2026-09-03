"""Idempotent additive migration for canonical groups and conversations."""

from __future__ import annotations

from sqlalchemy import case, inspect as sa_inspect, text

from backend.app import db
from backend.models import (
    Club,
    ClubChatRead,
    Conversation,
    ConversationRead,
    CourtChatRead,
    Crew,
    CrewChatRead,
    GameChatRead,
    LeagueChatRead,
    Message,
    TournamentChatRead,
    utcnow,
)
from backend.services.conversations import SPECS, conversation_ref
from backend.services.groups import sync_group_identity


READ_SOURCES = (
    ('court', CourtChatRead, 'court_id'),
    ('game', GameChatRead, 'game_id'),
    ('tournament', TournamentChatRead, 'tournament_id'),
    ('club', ClubChatRead, 'club_id'),
    ('crew', CrewChatRead, 'crew_id'),
    ('league', LeagueChatRead, 'league_id'),
)


def _dialect_insert(table):
    dialect = db.session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f'Unsupported conversation database: {dialect}')
    return insert(table)


def _upsert_canonical_read(user_id, conversation_id, message_id,
                           created_at=None, updated_at=None):
    table = ConversationRead.__table__
    now = utcnow()
    statement = _dialect_insert(table).values(
        user_id=int(user_id),
        conversation_id=int(conversation_id),
        last_read_message_id=max(0, int(message_id or 0)),
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.user_id, table.c.conversation_id],
        set_={
            'last_read_message_id': case(
                (
                    statement.excluded.last_read_message_id
                    > table.c.last_read_message_id,
                    statement.excluded.last_read_message_id,
                ),
                else_=table.c.last_read_message_id,
            ),
            'updated_at': case(
                (
                    statement.excluded.updated_at > table.c.updated_at,
                    statement.excluded.updated_at,
                ),
                else_=table.c.updated_at,
            ),
        },
    )
    db.session.execute(statement)


def backfill_canonical_communications():
    """Backfill identities, message links, and read positions idempotently.

    The legacy columns remain intact. This makes the migration safe across a
    rolling deployment: old code keeps reading its original schema while new
    code can use the canonical rows immediately.
    """
    for club in Club.query.order_by(Club.id).yield_per(200):
        sync_group_identity('club', club)
    for crew in Crew.query.order_by(Crew.id).yield_per(200):
        sync_group_identity('crew', crew)

    room_keys = set()
    message_rows = db.session.query(
        Message.id,
        Message.recipient_id,
        Message.conversation_id,
        *(getattr(Message, spec.message_field) for spec in SPECS.values()),
    ).yield_per(500)
    normalized_messages = []
    for row in message_rows:
        values = {
            spec.kind: getattr(row, spec.message_field)
            for spec in SPECS.values()
            if getattr(row, spec.message_field) is not None
        }
        if not values:
            # Direct messages intentionally remain participant-addressed.
            continue
        if len(values) != 1 or row.recipient_id is not None:
            raise RuntimeError(
                f'Message {row.id} has an invalid legacy conversation scope'
            )
        kind, scope_id = next(iter(values.items()))
        room_keys.add((kind, int(scope_id)))
        normalized_messages.append((row.id, kind, int(scope_id), row.conversation_id))

    marker_rows = []
    for kind, marker_model, scope_field in READ_SOURCES:
        rows = marker_model.query.order_by(marker_model.id).yield_per(500)
        for marker in rows:
            scope_id = int(getattr(marker, scope_field))
            room_keys.add((kind, scope_id))
            marker_rows.append((kind, scope_id, marker))

    persisted = {}
    for kind, scope_id in sorted(room_keys):
        persisted[(kind, scope_id)] = conversation_ref(
            kind, scope_id,
        ).ensure_persisted()
    db.session.flush()

    for message_id, kind, scope_id, current_conversation_id in normalized_messages:
        expected_id = persisted[(kind, scope_id)].id
        if current_conversation_id not in (None, expected_id):
            raise RuntimeError(
                f'Message {message_id} points at the wrong conversation'
            )
        if current_conversation_id is None:
            db.session.execute(
                Message.__table__.update()
                .where(Message.id == message_id)
                .values(conversation_id=expected_id)
            )

    for kind, scope_id, marker in marker_rows:
        _upsert_canonical_read(
            marker.user_id,
            persisted[(kind, scope_id)].id,
            marker.last_read_message_id,
            marker.created_at,
            marker.updated_at,
        )
    db.session.flush()


def _ensure_message_conversation_foreign_key(connection):
    if connection.dialect.name != 'postgresql':
        # Fresh SQLite schemas receive the model FK through create_all. SQLite
        # cannot add a foreign key to a long-lived table without rebuilding it.
        return
    inspector = sa_inspect(connection)
    matches = any(
        tuple(item.get('constrained_columns') or ()) == ('conversation_id',)
        and item.get('referred_table') == 'conversation'
        and tuple(item.get('referred_columns') or ()) == ('id',)
        for item in inspector.get_foreign_keys('message')
    )
    if matches:
        return
    connection.execute(text(
        'ALTER TABLE message ADD CONSTRAINT message_conversation_id_fkey '
        'FOREIGN KEY (conversation_id) REFERENCES conversation (id) '
        'ON DELETE SET NULL'
    ))


def ensure_canonical_communication_schema(app):
    """Install, backfill, and verify the additive canonical persistence."""
    inspector = sa_inspect(db.engine)
    tables = set(inspector.get_table_names())
    required_sources = {
        'user', 'court', 'club', 'crew', 'message',
        *(spec.read_model.__table__.name for spec in SPECS.values()),
    }
    if not required_sources <= tables:
        return

    columns = {item['name'] for item in inspector.get_columns('message')}
    if 'conversation_id' not in columns:
        with db.engine.begin() as connection:
            connection.execute(text(
                'ALTER TABLE message ADD COLUMN conversation_id INTEGER'
            ))

    # Explicit creation supports production's AUTO_CREATE_DB=false contract.
    from backend.models import Group
    Group.__table__.create(db.engine, checkfirst=True)
    Conversation.__table__.create(db.engine, checkfirst=True)
    ConversationRead.__table__.create(db.engine, checkfirst=True)
    with db.engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtext("
                "'third-shot:canonical-conversations'))"
            ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_message_conversation_id '
            'ON message (conversation_id)'
        ))
        _ensure_message_conversation_foreign_key(connection)

    backfill_canonical_communications()
    db.session.commit()

    # Fail closed: a partial backfill would make canonical unread counts or
    # room history silently disagree with the compatibility APIs.
    missing_message = db.session.query(Message.id).filter(
        Message.conversation_id.is_(None),
        Message.recipient_id.is_(None),
        db.or_(
            *(getattr(Message, spec.message_field).is_not(None)
              for spec in SPECS.values())
        ),
    ).first()
    if missing_message:
        raise RuntimeError(
            f'Canonical conversation backfill missed message {missing_message[0]}'
        )
    expected_reads = sum(model.query.count() for _, model, _ in READ_SOURCES)
    if ConversationRead.query.count() < expected_reads:
        raise RuntimeError('Canonical conversation-read backfill is incomplete')
