"""Canonical group identities over the legacy Club and Crew APIs."""

from __future__ import annotations

from backend.app import db
from backend.models import Club, Conversation, Crew, Group, utcnow


def _group_values(kind, entity):
    normalized = str(kind or '').strip().lower()
    if normalized == 'club' and isinstance(entity, Club):
        privacy = 'approval' if (entity.join_policy or 'open') == 'request' \
            else 'open'
        return {
            'kind': 'club',
            'privacy': privacy,
            'legacy_scope_id': entity.id,
            'name': entity.name,
            'description': entity.description or '',
            'owner_id': entity.creator_id,
            'home_court_id': entity.home_court_id,
            'archived_at': entity.archived_at,
        }
    if normalized == 'crew' and isinstance(entity, Crew):
        return {
            'kind': 'crew',
            'privacy': 'invite',
            'legacy_scope_id': entity.id,
            'name': entity.name,
            'description': '',
            'owner_id': entity.owner_id,
            'home_court_id': entity.default_court_id,
            'archived_at': entity.archived_at,
        }
    raise ValueError('unsupported_group_kind')


def _dialect_insert(table):
    dialect = db.session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f'Unsupported group database: {dialect}')
    return insert(table)


def sync_group_identity(kind, entity):
    """Create or reconcile one canonical Group without committing its caller."""
    if getattr(entity, 'id', None) is None:
        db.session.flush()
    values = _group_values(kind, entity)
    now = utcnow()
    table = Group.__table__
    statement = _dialect_insert(table).values(
        **values,
        created_at=getattr(entity, 'created_at', None) or now,
        updated_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.kind, table.c.legacy_scope_id],
        set_={
            'privacy': statement.excluded.privacy,
            'name': statement.excluded.name,
            'description': statement.excluded.description,
            'owner_id': statement.excluded.owner_id,
            'home_court_id': statement.excluded.home_court_id,
            'archived_at': statement.excluded.archived_at,
            'updated_at': now,
        },
    )
    db.session.execute(statement)
    return Group.query.filter_by(
        kind=values['kind'], legacy_scope_id=values['legacy_scope_id'],
    ).one()


def ensure_group_identity(kind, scope_id):
    """Return the canonical identity for an existing Club/Crew scope."""
    normalized = str(kind or '').strip().lower()
    try:
        normalized_scope_id = int(scope_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid_group_scope') from exc
    if normalized not in ('club', 'crew') or normalized_scope_id <= 0:
        raise ValueError('invalid_group_scope')
    existing = Group.query.filter_by(
        kind=normalized, legacy_scope_id=normalized_scope_id,
    ).first()
    if existing:
        return existing
    model = Club if normalized == 'club' else Crew
    entity = db.session.get(model, normalized_scope_id)
    return sync_group_identity(normalized, entity) if entity else None


def delete_group_identity(kind, scope_id):
    """Remove an identity only when its legacy source is hard-deleted."""
    normalized = str(kind or '').strip().lower()
    normalized_scope_id = int(scope_id)
    conversation = Conversation.query.filter_by(
        kind=normalized, scope_id=normalized_scope_id,
    ).first()
    if conversation:
        db.session.delete(conversation)
    Group.query.filter_by(
        kind=normalized, legacy_scope_id=normalized_scope_id,
    ).delete(synchronize_session=False)
