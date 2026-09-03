"""Canonical conversation persistence with rolling legacy compatibility."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, or_

from backend.app import db
from backend.models import (
    ClubChatRead, CourtChatRead, CrewChatRead, GameChatRead, LeagueChatRead,
    Conversation, ConversationRead, Message, TournamentChatRead, utcnow,
)


@dataclass(frozen=True)
class ConversationSpec:
    kind: str
    collection: str
    category: str
    access: str
    message_field: str
    read_model: type
    read_field: str


SPECS = {
    spec.kind: spec
    for spec in (
        ConversationSpec(
            'court', 'courts', 'place', 'signed_in',
            'court_id', CourtChatRead, 'court_id',
        ),
        ConversationSpec(
            'game', 'games', 'play', 'members',
            'game_id', GameChatRead, 'game_id',
        ),
        ConversationSpec(
            'tournament', 'tournaments', 'competition', 'participants',
            'tournament_id', TournamentChatRead, 'tournament_id',
        ),
        ConversationSpec(
            'club', 'clubs', 'group', 'members',
            'club_id', ClubChatRead, 'club_id',
        ),
        ConversationSpec(
            'crew', 'crews', 'group', 'members',
            'crew_id', CrewChatRead, 'crew_id',
        ),
        ConversationSpec(
            'league', 'leagues', 'competition', 'members',
            'league_id', LeagueChatRead, 'league_id',
        ),
    )
}


@dataclass(frozen=True)
class ConversationRef:
    spec: ConversationSpec
    scope_id: int

    @property
    def id(self):
        return f'{self.spec.kind}:{self.scope_id}'

    @property
    def kind(self):
        return self.spec.kind

    @property
    def message_column(self):
        return getattr(Message, self.spec.message_field)

    @property
    def message_scope(self):
        return {self.spec.message_field: self.scope_id}

    def ensure_persisted(self):
        """Materialize this room's canonical row without committing."""
        group_id = None
        if self.kind in ('club', 'crew'):
            from backend.services.groups import ensure_group_identity
            group = ensure_group_identity(self.kind, self.scope_id)
            group_id = group.id if group else None

        table = Conversation.__table__
        dialect = db.session.get_bind().dialect.name
        if dialect == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise RuntimeError(f'Unsupported conversation database: {dialect}')
        now = utcnow()
        statement = insert(table).values(
            kind=self.kind,
            scope_id=self.scope_id,
            group_id=group_id,
            created_at=now,
            updated_at=now,
        ).on_conflict_do_nothing(
            index_elements=[table.c.kind, table.c.scope_id],
        )
        db.session.execute(statement)
        persisted = Conversation.query.filter_by(
            kind=self.kind, scope_id=self.scope_id,
        ).one()
        if group_id is not None and persisted.group_id != group_id:
            persisted.group_id = group_id
        return persisted

    def message_query(self):
        persisted = self.ensure_persisted()
        # Legacy-only rows remain visible until the production backfill has
        # completed; new rows match both sides of this compatibility predicate.
        return Message.query.filter(or_(
            Message.conversation_id == persisted.id,
            self.message_column == self.scope_id,
        ))

    def to_dict(self, name):
        """Stable additive DTO shared by all legacy room endpoints."""
        return {
            'id': self.id,
            'kind': self.kind,
            'scope_id': self.scope_id,
            'category': self.spec.category,
            'access': self.spec.access,
            'name': str(name or ''),
            'messages_url': f'/api/{self.spec.collection}/{self.scope_id}/chat',
            'action_url': f'/#{self.kind}/{self.scope_id}',
        }

    def decorate_message(self, payload, name):
        data = payload.to_dict() if hasattr(payload, 'to_dict') else dict(payload)
        data['conversation'] = self.to_dict(name)
        return data


def conversation_ref(kind, scope_id):
    spec = SPECS.get(str(kind or '').strip().lower())
    if spec is None:
        raise ValueError('unsupported_conversation_kind')
    if isinstance(scope_id, bool):
        raise ValueError('invalid_conversation_id')
    try:
        normalized_id = int(scope_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid_conversation_id') from exc
    if normalized_id <= 0:
        raise ValueError('invalid_conversation_id')
    return ConversationRef(spec, normalized_id)


def conversation_ref_for_message(message):
    """Return the one room identity on a Message, rejecting corrupt shapes."""
    refs = [
        conversation_ref(spec.kind, getattr(message, spec.message_field))
        for spec in SPECS.values()
        if getattr(message, spec.message_field, None) is not None
    ]
    canonical = getattr(message, 'conversation', None)
    if len(refs) == 0 and canonical is not None:
        return conversation_ref(canonical.kind, canonical.scope_id)
    if len(refs) != 1:
        raise ValueError('message_conversation_scope_invalid')
    if canonical is not None and (
        canonical.kind != refs[0].kind
        or canonical.scope_id != refs[0].scope_id
    ):
        raise ValueError('message_conversation_scope_invalid')
    return refs[0]


def advance_conversation_read(conversation, user_id, latest_message_id):
    """Atomically advance canonical and legacy markers during rollout."""
    if not isinstance(conversation, ConversationRef):
        raise TypeError('conversation must be a ConversationRef')
    try:
        normalized_user_id = int(user_id)
        normalized_latest_id = max(0, int(latest_message_id or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid_conversation_read_marker') from exc
    if normalized_user_id <= 0:
        raise ValueError('invalid_conversation_read_marker')

    persisted = conversation.ensure_persisted()
    dialect = db.session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f'Unsupported conversation database: {dialect}')

    now = utcnow()
    canonical_table = ConversationRead.__table__
    canonical_values = {
        'user_id': normalized_user_id,
        'conversation_id': persisted.id,
        'last_read_message_id': normalized_latest_id,
        'created_at': now,
        'updated_at': now,
    }
    canonical_statement = insert(canonical_table).values(**canonical_values)
    canonical_statement = canonical_statement.on_conflict_do_update(
        index_elements=[
            canonical_table.c.user_id,
            canonical_table.c.conversation_id,
        ],
        set_={
            'last_read_message_id': case(
                (
                    canonical_statement.excluded.last_read_message_id
                    > canonical_table.c.last_read_message_id,
                    canonical_statement.excluded.last_read_message_id,
                ),
                else_=canonical_table.c.last_read_message_id,
            ),
            'updated_at': now,
        },
    )
    db.session.execute(canonical_statement)

    table = conversation.spec.read_model.__table__
    values = {
        'user_id': normalized_user_id,
        conversation.spec.read_field: conversation.scope_id,
        'last_read_message_id': normalized_latest_id,
        'created_at': now,
        'updated_at': now,
    }
    # CrewChatRead also carries its room-alert preference. A first read starts
    # at the documented default; conflict updates below deliberately preserve
    # whichever preference the player selected.
    if 'notification_level' in table.c:
        values['notification_level'] = 'all'
    statement = insert(table).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.user_id,
            table.c[conversation.spec.read_field],
        ],
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


def delete_conversation_read(conversation, user_id):
    """Remove a departed user's canonical marker without creating a room."""
    if not isinstance(conversation, ConversationRef):
        raise TypeError('conversation must be a ConversationRef')
    persisted = Conversation.query.filter_by(
        kind=conversation.kind, scope_id=conversation.scope_id,
    ).first()
    if not persisted:
        return 0
    return ConversationRead.query.filter_by(
        user_id=int(user_id), conversation_id=persisted.id,
    ).delete(synchronize_session=False)
