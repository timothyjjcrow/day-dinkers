#!/usr/bin/env python3
"""Apply and verify Third Shot's additive schema upgrades on PostgreSQL.

Runtime/serverless instances intentionally never run DDL. This operator-only
command reads Neon's direct connection URL from TARGET_DATABASE_URL, refuses a
pooled endpoint or a database without the existing ``picklepals`` app schema,
runs the application's idempotent additive migrations, and verifies every
table, column, index, foreign key, and uniqueness constraint required by this
release.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PG_SCHEMA = 'picklepals'
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_TABLES = {
    'user', 'court', 'check_in', 'game', 'message', 'notification',
}
REQUIRED_COLUMNS = {
    'message': {'crew_id'},
    'game': {
        'crew_id', 'crew_roster_version', 'is_instant',
        'assembly_closed_at', 'creator_id', 'client_attempt_id',
        'client_attempt_fingerprint',
    },
    'check_in': {
        'user_id', 'court_id', 'looking_for_game', 'checked_in_at',
        'checked_out_at', 'last_presence_ping_at',
    },
    'game_arrival_intent': {
        'id', 'created_at', 'updated_at', 'game_id', 'user_id',
        'eta_minutes', 'declared_at', 'arrives_at', 'expires_at',
        'active', 'ended_at', 'end_reason', 'client_attempt_id',
        'client_attempt_fingerprint', 'last_announced_at',
    },
    'play_availability_pulse': {
        'id', 'created_at', 'updated_at', 'user_id', 'court_id',
        'declared_at', 'expires_at', 'active', 'ended_at', 'end_reason',
        'client_attempt_id', 'client_attempt_fingerprint', 'accepted_by_id',
        'accept_client_attempt_id', 'accept_client_attempt_fingerprint',
        'accepted_game_id',
    },
    'game_open_call': {
        'id', 'created_at', 'updated_at', 'game_id', 'created_by_id',
        'court_message_id', 'client_attempt_id',
        'client_attempt_fingerprint', 'active', 'ended_at', 'end_reason',
    },
    'notification': {'related_crew_id'},
    'crew': {
        'id', 'created_at', 'updated_at', 'owner_id', 'name',
        'source_game_id', 'default_court_id', 'roster_version', 'archived_at',
    },
    'crew_member': {'id', 'created_at', 'updated_at', 'crew_id', 'user_id'},
    'crew_invite': {
        'id', 'created_at', 'updated_at', 'crew_id', 'invitee_id',
        'invited_by_id', 'status', 'resolved_at',
    },
    'crew_chat_read': {
        'id', 'created_at', 'updated_at', 'user_id', 'crew_id',
        'last_read_message_id',
    },
}
REQUIRED_INDEXES = {
    'message': {'ix_message_crew_id'},
    'game': {'ix_game_crew_id', 'ix_game_is_instant'},
    'notification': {'ix_notification_related_crew_id'},
    'game_arrival_intent': {
        'ix_game_arrival_intent_game_id',
        'ix_game_arrival_intent_user_id',
        'ix_game_arrival_intent_expires_at',
    },
    'play_availability_pulse': {
        'ix_play_availability_pulse_user_id',
        'ix_play_availability_pulse_court_id',
        'ix_play_availability_pulse_expires_at',
        'ix_play_availability_pulse_active',
        'ix_play_availability_pulse_accepted_by_id',
        'ix_play_availability_pulse_accepted_game_id',
    },
    'game_open_call': {
        'ix_game_open_call_game_id',
        'ix_game_open_call_created_by_id',
        'ix_game_open_call_active',
    },
}
REQUIRED_PARTIAL_UNIQUE_INDEXES = {
    'check_in': {
        'uq_check_in_active_user': (
            ('user_id',), 'checked_out_at is null',
        ),
    },
    'game_arrival_intent': {
        'uq_game_arrival_active_user': (
            ('user_id',), 'active is true',
        ),
        'uq_game_arrival_active_game': (
            ('game_id',), 'active is true',
        ),
    },
    'play_availability_pulse': {
        'uq_play_availability_pulse_active_user': (
            ('user_id',), 'active is true',
        ),
    },
    'game_open_call': {
        'uq_game_open_call_active_game': (
            ('game_id',), 'active is true',
        ),
    },
}
REQUIRED_EXACT_UNIQUE_INDEXES = {
    'game': {
        'uq_game_creator_attempt': ('creator_id', 'client_attempt_id'),
    },
}
REQUIRED_UNIQUES = {
    'crew': {'uq_crew_source_game'},
    'crew_member': {'uq_crew_member'},
    'crew_invite': {'uq_crew_invitee'},
    'crew_chat_read': {'uq_crew_chat_read'},
    'game_arrival_intent': {'uq_game_arrival_user_attempt'},
    'play_availability_pulse': {
        'uq_play_availability_pulse_user_attempt',
        'uq_play_availability_pulse_accept_attempt',
    },
    'game_open_call': {
        'uq_game_open_call_creator_attempt',
        'uq_game_open_call_game_creator',
        'uq_game_open_call_message',
    },
}
REQUIRED_CHECK_CONSTRAINTS = {
    'play_availability_pulse': {
        'ck_play_availability_pulse_positive_window',
    },
}
REQUIRED_FOREIGN_KEYS = {
    'message': {
        'message_crew_id_fkey': (
            ('crew_id',), 'crew', ('id',),
        ),
    },
    'game': {
        'game_crew_id_fkey': (
            ('crew_id',), 'crew', ('id',),
        ),
    },
    'notification': {
        'notification_related_crew_id_fkey': (
            ('related_crew_id',), 'crew', ('id',),
        ),
    },
    'game_arrival_intent': {
        'game_arrival_intent_game_id_fkey': (
            ('game_id',), 'game', ('id',),
        ),
        'game_arrival_intent_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'play_availability_pulse': {
        'play_availability_pulse_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'play_availability_pulse_court_id_fkey': (
            ('court_id',), 'court', ('id',),
        ),
        'play_availability_pulse_accepted_by_id_fkey': (
            ('accepted_by_id',), 'user', ('id',),
        ),
        'play_availability_pulse_accepted_game_id_fkey': (
            ('accepted_game_id',), 'game', ('id',),
        ),
    },
    'game_open_call': {
        'game_open_call_game_id_fkey': (
            ('game_id',), 'game', ('id',),
        ),
        'game_open_call_created_by_id_fkey': (
            ('created_by_id',), 'user', ('id',),
        ),
        'game_open_call_court_message_id_fkey': (
            ('court_message_id',), 'message', ('id',),
        ),
    },
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _normalize_postgres_url(url: str) -> str:
    if url.startswith('postgres://'):
        return 'postgresql+psycopg://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


def _validated_target_url() -> str:
    from sqlalchemy.engine import make_url

    target = _normalize_postgres_url(
        os.getenv('TARGET_DATABASE_URL', '').strip(),
    )
    if not target.startswith('postgresql+psycopg://'):
        raise RuntimeError(
            'TARGET_DATABASE_URL must be a PostgreSQL connection string.'
        )
    host = (make_url(target).host or '').lower()
    if '-pooler.' in host:
        raise RuntimeError(
            "TARGET_DATABASE_URL must be Neon's direct/unpooled URL; "
            'the deployed DATABASE_URL remains pooled.'
        )
    return target


def _schema_gaps(inspector, schema=PG_SCHEMA) -> list[str]:
    tables = set(inspector.get_table_names(schema=schema))
    gaps = []
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            gaps.append(f'missing table {table}')
            continue
        actual = {
            column['name']
            for column in inspector.get_columns(table, schema=schema)
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing columns {missing}')
    for table, required in REQUIRED_INDEXES.items():
        if table not in tables:
            continue
        actual = {
            index.get('name')
            for index in inspector.get_indexes(table, schema=schema)
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing indexes {missing}')
    for table, required in REQUIRED_PARTIAL_UNIQUE_INDEXES.items():
        if table not in tables:
            continue
        reflected = {
            index.get('name'): index
            for index in inspector.get_indexes(table, schema=schema)
        }
        for name, (expected_columns, expected_predicate) in required.items():
            index = reflected.get(name)
            if index is None:
                gaps.append(f'{table} missing partial unique index {name}')
                continue
            options = index.get('dialect_options') or {}
            predicate = options.get('postgresql_where')
            if predicate is None:
                predicate = options.get('sqlite_where')
            normalized_predicate = ' '.join(
                str(predicate if predicate is not None else '')
                .lower()
                .replace('"', '')
                .replace('(', ' ')
                .replace(')', ' ')
                .split()
            )
            if (
                not index.get('unique')
                or tuple(index.get('column_names') or ()) != expected_columns
                or normalized_predicate != expected_predicate
            ):
                gaps.append(
                    f'{table} index {name} must be unique on '
                    f'{list(expected_columns)} where {expected_predicate}'
                )
    for table, required in REQUIRED_EXACT_UNIQUE_INDEXES.items():
        if table not in tables:
            continue
        reflected = {
            index.get('name'): index
            for index in inspector.get_indexes(table, schema=schema)
        }
        for name, expected_columns in required.items():
            index = reflected.get(name)
            if index is None:
                gaps.append(f'{table} missing exact unique index {name}')
                continue
            options = index.get('dialect_options') or {}
            if (
                not index.get('unique')
                or tuple(index.get('column_names') or ()) != expected_columns
                or options.get('postgresql_where') is not None
                or options.get('sqlite_where') is not None
                or bool(options.get('postgresql_nulls_not_distinct'))
            ):
                gaps.append(
                    f'{table} index {name} must be a nonpartial unique index '
                    f'on {list(expected_columns)} with distinct nulls'
                )
    for table, required in REQUIRED_UNIQUES.items():
        if table not in tables:
            continue
        actual = {
            constraint.get('name')
            for constraint in inspector.get_unique_constraints(
                table, schema=schema,
            )
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing unique constraints {missing}')
    for table, required in REQUIRED_CHECK_CONSTRAINTS.items():
        if table not in tables:
            continue
        actual = {
            constraint.get('name')
            for constraint in inspector.get_check_constraints(
                table, schema=schema,
            )
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing check constraints {missing}')
    for table, required in REQUIRED_FOREIGN_KEYS.items():
        if table not in tables:
            continue
        actual = [
            (
                constraint.get('name'),
                tuple(constraint.get('constrained_columns') or ()),
                constraint.get('referred_table'),
                tuple(constraint.get('referred_columns') or ()),
                constraint.get('referred_schema'),
            )
            for constraint in inspector.get_foreign_keys(table, schema=schema)
        ]
        for name, expected in required.items():
            local_columns, referred_table, referred_columns = expected
            if any(
                found[1:4]
                == (local_columns, referred_table, referred_columns)
                and found[4] in (None, schema)
                for found in actual
            ):
                continue
            if any(found[0] == name for found in actual):
                gaps.append(
                    f'{table} foreign key {name} has wrong target or columns'
                )
            else:
                gaps.append(f'{table} missing foreign key {name}')
    return gaps


def _preflight_existing_app(engine) -> None:
    from sqlalchemy import inspect, text

    with engine.connect() as connection:
        if connection.scalar(
            text('SELECT to_regnamespace(:schema)'), {'schema': PG_SCHEMA},
        ) is None:
            raise RuntimeError(
                f'Target is missing the existing {PG_SCHEMA!r} schema; '
                'refusing to initialize an unexpected database.'
            )
    tables = set(inspect(engine).get_table_names(schema=PG_SCHEMA))
    missing = sorted(BASE_TABLES - tables)
    if missing:
        raise RuntimeError(
            'Target is not the expected Third Shot database; missing base '
            f'tables: {missing}'
        )


def _configure_runtime_role_search_path(connection) -> None:
    from sqlalchemy import text

    role_name = connection.scalar(text('SELECT current_user'))
    preparer = connection.dialect.identifier_preparer
    connection.execute(text(
        f'ALTER ROLE {preparer.quote(role_name)} SET search_path TO '
        f'{preparer.quote(PG_SCHEMA)}, public'
    ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Apply verified additive schema upgrades to Third Shot PostgreSQL.',
    )
    parser.add_argument(
        '--check-only', action='store_true',
        help='verify the deployed schema without applying migrations',
    )
    args = parser.parse_args()

    from sqlalchemy import create_engine, inspect

    target_url = _validated_target_url()
    engine = create_engine(
        target_url,
        pool_pre_ping=True,
        connect_args={'options': f'-csearch_path={PG_SCHEMA}'},
    )
    try:
        _preflight_existing_app(engine)
        before = _schema_gaps(inspect(engine))
    finally:
        engine.dispose()

    if args.check_only:
        if before:
            raise RuntimeError('Schema verification failed: ' + '; '.join(before))
        print('Production schema check passed: release schema is ready.')
        return 0

    # Import only after the target has passed read-only identity checks. App
    # startup then runs the same idempotent additive migration path exercised by
    # local recovery tests, without creating unrelated missing application data.
    os.environ.update({
        'APP_ENV': 'production',
        'SERVERLESS_RUNTIME': 'false',
        'SCHEMA_MANAGEMENT_ENABLED': 'true',
        'AUTO_CREATE_DB': 'false',
        'AUTO_SEED_COURTS': 'false',
        'RATE_LIMIT_ENABLED': 'false',
        'PUSH_DELIVERY_ENABLED': 'false',
        'DATABASE_URL': target_url,
        'SECRET_KEY': 'migration-only-process-secret-not-used-for-serving',
    })
    from backend.app import app, db

    with app.app_context():
        gaps = _schema_gaps(inspect(db.engine))
        if gaps:
            raise RuntimeError(
                'Schema verification failed after migration: ' + '; '.join(gaps)
            )
        with db.engine.begin() as connection:
            _configure_runtime_role_search_path(connection)

    print('Production schema migration completed and verified: release schema is ready.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Migration failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
