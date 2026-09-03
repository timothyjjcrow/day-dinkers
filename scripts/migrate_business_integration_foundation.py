#!/usr/bin/env python3
"""Install, converge, and exhaustively verify provider-integration tables.

The target is accepted only after the existing Third Shot schema is identified.
Every additive model column, index, foreign key, unique constraint, and check
constraint is repaired when safe and verified before the command succeeds.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


REQUIRED_COLUMNS = {
    'business_credential_secret': {
        'id', 'public_id', 'purpose', 'ciphertext', 'key_version',
        'created_by_id', 'last_accessed_at', 'deleted_at', 'created_at',
        'updated_at',
    },
    'business_provider_connection': {
        'id', 'public_id', 'business_id', 'created_by_id', 'provider_key',
        'display_name', 'external_account_id', 'status', 'health_status',
        'capabilities', 'public_config',
        'credential_ref', 'webhook_secret_ref', 'cursor_ref',
        'last_sync_started_at', 'last_sync_succeeded_at',
        'last_sync_failed_at', 'last_health_checked_at', 'last_pull_at',
        'next_sync_at', 'pull_etag', 'pull_last_modified',
        'consecutive_failures', 'last_error_code', 'last_error_message',
        'disconnected_at', 'operator_reconnect_required', 'version',
        'created_at', 'updated_at',
    },
    'business_integration_sync_run': {
        'id', 'connection_id', 'trigger', 'status', 'idempotency_key',
        'attempt', 'max_attempts', 'scheduled_for', 'started_at',
        'completed_at', 'next_retry_at', 'source_version',
        'reconciliation_hash', 'payload_json', 'error_code', 'error_message',
        'metrics', 'created_at', 'updated_at',
    },
    'business_webhook_receipt': {
        'id', 'connection_id', 'provider_event_id', 'idempotency_key',
        'signature_digest', 'payload_digest', 'status', 'processed_at',
        'error_code', 'created_at', 'updated_at',
    },
    'business_schedule_occurrence': {
        'id', 'business_id', 'connection_id', 'external_id', 'title', 'kind',
        'recurrence', 'start_date', 'end_date', 'event_date', 'start_time',
        'end_time', 'starts_at', 'ends_at', 'timezone', 'capacity',
        'spots_remaining', 'status', 'skill_level', 'location_note',
        'instructor', 'price_text', 'booking_url', 'source_updated_at',
        'synced_at', 'payload_hash', 'created_at', 'updated_at',
    },
    'business_booking_event': {
        'id', 'business_id', 'connection_id', 'occurrence_id', 'event_type',
        'event_key', 'external_event_id', 'action', 'occurred_at',
        'value_minor', 'currency', 'source', 'created_at', 'updated_at',
    },
    'business_link_health_check': {
        'id', 'business_id', 'connection_id', 'link_kind', 'url_hash', 'final_url_hash',
        'status', 'http_status', 'latency_ms', 'error_code', 'checked_at',
        'next_check_at', 'created_at', 'updated_at',
    },
    'business_integration_audit_event': {
        'id', 'business_id', 'connection_id', 'actor_kind', 'actor_id',
        'action', 'metadata_json', 'created_at', 'updated_at',
    },
}

REQUIRED_INDEXES = {
    'business_credential_secret': {
        'ix_business_credential_secret_public_id': (('public_id',), True),
        'ix_business_credential_secret_purpose': (('purpose',), False),
        'ix_business_credential_secret_key_version': (('key_version',), False),
        'ix_business_credential_secret_created_by_id': (('created_by_id',), False),
        'ix_business_credential_secret_deleted_at': (('deleted_at',), False),
    },
    'business_provider_connection': {
        'ix_business_provider_connection_public_id': (('public_id',), True),
        'ix_business_provider_connection_business_id': (('business_id',), False),
        'ix_business_provider_connection_created_by_id': (('created_by_id',), False),
        'ix_business_provider_connection_provider_key': (('provider_key',), False),
        'ix_business_provider_connection_status': (('status',), False),
        'ix_business_provider_connection_health_status': (('health_status',), False),
        'ix_business_provider_connection_next_sync_at': (('next_sync_at',), False),
    },
    'business_integration_sync_run': {
        'ix_business_integration_sync_run_connection_id': (('connection_id',), False),
        'ix_business_integration_sync_run_status': (('status',), False),
        'ix_business_integration_sync_run_scheduled_for': (('scheduled_for',), False),
        'ix_business_integration_sync_run_next_retry_at': (('next_retry_at',), False),
    },
    'business_webhook_receipt': {
        'ix_business_webhook_receipt_connection_id': (('connection_id',), False),
        'ix_business_webhook_receipt_status': (('status',), False),
    },
    'business_schedule_occurrence': {
        'ix_business_schedule_occurrence_business_id': (('business_id',), False),
        'ix_business_schedule_occurrence_connection_id': (('connection_id',), False),
        'ix_business_schedule_occurrence_kind': (('kind',), False),
        'ix_business_schedule_occurrence_event_date': (('event_date',), False),
        'ix_business_schedule_occurrence_starts_at': (('starts_at',), False),
        'ix_business_schedule_occurrence_status': (('status',), False),
    },
    'business_booking_event': {
        'ix_business_booking_event_business_id': (('business_id',), False),
        'ix_business_booking_event_connection_id': (('connection_id',), False),
        'ix_business_booking_event_occurrence_id': (('occurrence_id',), False),
        'ix_business_booking_event_event_type': (('event_type',), False),
        'ix_business_booking_event_occurred_at': (('occurred_at',), False),
    },
    'business_link_health_check': {
        'ix_business_link_health_check_business_id': (('business_id',), False),
        'ix_business_link_health_check_connection_id': (('connection_id',), False),
        'ix_business_link_health_check_url_hash': (('url_hash',), False),
        'ix_business_link_health_check_status': (('status',), False),
        'ix_business_link_health_check_checked_at': (('checked_at',), False),
        'ix_business_link_health_check_next_check_at': (('next_check_at',), False),
    },
    'business_integration_audit_event': {
        'ix_business_integration_audit_event_business_id': (('business_id',), False),
        'ix_business_integration_audit_event_connection_id': (('connection_id',), False),
        'ix_business_integration_audit_event_action': (('action',), False),
    },
}

REQUIRED_UNIQUES = {
    'business_provider_connection': {
        'uq_business_provider_connection': ('business_id', 'provider_key'),
    },
    'business_integration_sync_run': {
        'uq_business_sync_run_idempotency': ('connection_id', 'idempotency_key'),
    },
    'business_webhook_receipt': {
        'uq_business_webhook_receipt': ('connection_id', 'idempotency_key'),
    },
    'business_schedule_occurrence': {
        'uq_business_occurrence_external': ('connection_id', 'external_id'),
    },
    'business_booking_event': {
        'uq_business_booking_event_key': ('event_key',),
    },
}

REQUIRED_PRIMARY_KEYS = {
    table: ('id',) for table in REQUIRED_COLUMNS
}

REQUIRED_CHECKS = {
    'business_credential_secret': {
        'ck_business_credential_key_version': 'key_version >= 1',
        'ck_business_credential_purpose': (
            "purpose IN ('credential','webhook','cursor')"
        ),
    },
    'business_provider_connection': {
        'ck_business_provider_connection_status': (
            "status IN ('draft','connected','degraded','error','disconnected')"
        ),
        'ck_business_provider_connection_health': (
            "health_status IN ('unknown','healthy','degraded','unreachable',"
            "'unsafe','disabled')"
        ),
    },
    'business_integration_sync_run': {
        'ck_business_sync_run_status': (
            "status IN ('queued','running','succeeded','retry_scheduled',"
            "'failed','cancelled')"
        ),
        'ck_business_sync_run_trigger': (
            "trigger IN ('owner_push','webhook','manual','scheduled','reconcile')"
        ),
    },
    'business_webhook_receipt': {
        'ck_business_webhook_receipt_status': (
            "status IN ('received','processed','duplicate','rejected','failed')"
        ),
    },
    'business_schedule_occurrence': {
        'ck_business_occurrence_status': (
            "status IN ('scheduled','cancelled','sold_out','completed')"
        ),
        'ck_business_occurrence_capacity': (
            'capacity IS NULL OR capacity >= 0'
        ),
        'ck_business_occurrence_spots': (
            'spots_remaining IS NULL OR spots_remaining >= 0'
        ),
        'ck_business_occurrence_spots_capacity': (
            'capacity IS NULL OR spots_remaining IS NULL '
            'OR spots_remaining <= capacity'
        ),
    },
    'business_booking_event': {
        'ck_business_booking_event_type': (
            "event_type IN ('click','conversion')"
        ),
    },
    'business_link_health_check': {
        'ck_business_link_health_status': (
            "status IN ('healthy','broken','unreachable','unsafe')"
        ),
    },
}

REQUIRED_FOREIGN_KEYS = {
    'business_credential_secret': {
        'business_credential_secret_created_by_id_fkey': (
            ('created_by_id',), 'user', ('id',),
        ),
    },
    'business_provider_connection': {
        'business_provider_connection_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_provider_connection_created_by_id_fkey': (
            ('created_by_id',), 'user', ('id',),
        ),
    },
    'business_integration_sync_run': {
        'business_sync_run_connection_id_fkey': (
            ('connection_id',), 'business_provider_connection', ('id',),
        ),
    },
    'business_webhook_receipt': {
        'business_webhook_receipt_connection_id_fkey': (
            ('connection_id',), 'business_provider_connection', ('id',),
        ),
    },
    'business_schedule_occurrence': {
        'business_occurrence_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_occurrence_connection_id_fkey': (
            ('connection_id',), 'business_provider_connection', ('id',),
        ),
    },
    'business_booking_event': {
        'business_booking_event_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_booking_event_connection_id_fkey': (
            ('connection_id',), 'business_provider_connection', ('id',),
        ),
        'business_booking_event_occurrence_id_fkey': (
            ('occurrence_id',), 'business_schedule_occurrence', ('id',),
        ),
    },
    'business_link_health_check': {
        'business_link_health_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_link_health_connection_id_fkey': (
            ('connection_id',), 'business_provider_connection', ('id',),
        ),
    },
    'business_integration_audit_event': {
        'business_integration_audit_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_integration_audit_connection_id_fkey': (
            ('connection_id',), 'business_provider_connection', ('id',),
        ),
    },
}


def _normalized_check_sql(value):
    return ' '.join(
        str(value or '').lower().replace('"', '').replace('(', ' ').replace(
            ')', ' '
        ).split()
    )


def _check_expression_matches(expected, actual):
    expected_normalized = _normalized_check_sql(expected)
    actual_normalized = _normalized_check_sql(actual)
    expected_literals = set(re.findall(r"'([^']*)'", expected_normalized))
    if expected_literals:
        actual_literals = set(re.findall(r"'([^']*)'", actual_normalized))
        expected_column = expected_normalized.split(' in ', 1)[0].strip()
        return (
            actual_literals == expected_literals
            and expected_column in actual_normalized
            and (' in ' in actual_normalized or '= any ' in actual_normalized)
            and ' or ' not in actual_normalized
            and ' and ' not in actual_normalized
        )
    return actual_normalized == expected_normalized


def _index_matches(item, columns, unique):
    if item is None:
        return False
    options = item.get('dialect_options') or {}
    predicate = options.get('postgresql_where')
    if predicate is None:
        predicate = options.get('sqlite_where')
    include = options.get('postgresql_include') or ()
    method = str(options.get('postgresql_using') or 'btree').lower()
    expressions = item.get('expressions')
    return (
        tuple(item.get('column_names') or ()) == columns
        and bool(item.get('unique')) is unique
        and predicate is None
        and not include
        and method == 'btree'
        and (
            expressions is None
            or tuple(expressions) == columns
        )
    )


def _foreign_key_matches(
    item, name, columns, referred_table, referred_columns, schema,
):
    options = item.get('options') or {}
    return (
        item.get('name') == name
        and tuple(item.get('constrained_columns') or ()) == columns
        and item.get('referred_table') == referred_table
        and tuple(item.get('referred_columns') or ()) == referred_columns
        and item.get('referred_schema') in (None, schema)
        and str(options.get('ondelete') or 'NO ACTION').upper() == 'NO ACTION'
        and str(options.get('onupdate') or 'NO ACTION').upper() == 'NO ACTION'
        and options.get('deferrable') in (None, False)
        and options.get('initially') is None
        and options.get('match') in (None, 'NONE')
    )


def schema_gaps(inspector, schema):
    tables = set(inspector.get_table_names(schema=schema))
    gaps = []
    for table, expected in REQUIRED_COLUMNS.items():
        if table not in tables:
            gaps.append(f'missing table {table}')
            continue
        actual = {
            column['name'] for column in inspector.get_columns(table, schema=schema)
        }
        if missing := sorted(expected - actual):
            gaps.append(f'{table} missing columns {missing}')
    for table, expected in REQUIRED_INDEXES.items():
        if table not in tables:
            continue
        actual = {
            item.get('name'): item
            for item in inspector.get_indexes(table, schema=schema)
        }
        for name, (columns, unique) in expected.items():
            item = actual.get(name)
            if item is None:
                gaps.append(f'{table} missing index {name}')
            elif not _index_matches(item, columns, unique):
                gaps.append(
                    f'{table} index {name} has wrong columns or uniqueness'
                )
    for table, expected in REQUIRED_UNIQUES.items():
        if table not in tables:
            continue
        actual = {
            item.get('name'): tuple(item.get('column_names') or ())
            for item in inspector.get_unique_constraints(table, schema=schema)
        }
        for name, columns in expected.items():
            if name not in actual:
                gaps.append(f'{table} missing unique constraint {name}')
            elif actual[name] != columns:
                gaps.append(
                    f'{table} unique constraint {name} has wrong columns'
                )
    for table, columns in REQUIRED_PRIMARY_KEYS.items():
        if table not in tables:
            continue
        primary_key = inspector.get_pk_constraint(table, schema=schema) or {}
        if tuple(primary_key.get('constrained_columns') or ()) != columns:
            gaps.append(
                f'{table} primary key has wrong columns; expected {list(columns)}'
            )
    for table, expected in REQUIRED_CHECKS.items():
        if table not in tables:
            continue
        actual = {
            item.get('name'): _normalized_check_sql(item.get('sqltext'))
            for item in inspector.get_check_constraints(table, schema=schema)
        }
        for name, expected_expression in expected.items():
            sql = actual.get(name)
            if sql is None:
                gaps.append(f'{table} missing check constraint {name}')
            elif not _check_expression_matches(expected_expression, sql):
                gaps.append(
                    f'{table} check constraint {name} has wrong expression'
                )
    for table, expected in REQUIRED_FOREIGN_KEYS.items():
        if table not in tables:
            continue
        actual = inspector.get_foreign_keys(table, schema=schema)
        for name, (columns, referred_table, referred_columns) in expected.items():
            if any(
                _foreign_key_matches(
                    item, name, columns, referred_table, referred_columns,
                    schema,
                )
                for item in actual
            ):
                continue
            if any(item.get('name') == name for item in actual):
                gaps.append(
                    f'{table} foreign key {name} has wrong target or columns'
                )
            else:
                gaps.append(f'{table} missing foreign key {name}')
    return gaps


def _foundation_model_tables():
    from backend.integrations.models import (
        BusinessBookingEvent,
        BusinessCredentialSecret,
        BusinessIntegrationAuditEvent,
        BusinessIntegrationSyncRun,
        BusinessLinkHealthCheck,
        BusinessProviderConnection,
        BusinessScheduleOccurrence,
        BusinessWebhookReceipt,
    )

    return [
        BusinessCredentialSecret.__table__,
        BusinessProviderConnection.__table__,
        BusinessIntegrationSyncRun.__table__,
        BusinessWebhookReceipt.__table__,
        BusinessScheduleOccurrence.__table__,
        BusinessBookingEvent.__table__,
        BusinessLinkHealthCheck.__table__,
        BusinessIntegrationAuditEvent.__table__,
    ]


def _qualified_name(connection, schema, name):
    quote = connection.dialect.identifier_preparer.quote
    return f'{quote(schema)}.{quote(name)}' if schema else quote(name)


def _volatile_digest(length=32):
    first = "md5(random()::text || clock_timestamp()::text || txid_current()::text)"
    if length <= 32:
        return first
    second = "md5(random()::text || clock_timestamp()::text || txid_current()::text)"
    return f'({first} || {second})'


def _volatile_uuid():
    return f'({_volatile_digest()}::uuid)::text'


def _safe_column_default(table_name, column):
    """Return a PostgreSQL expression safe for legacy-row backfills."""
    from sqlalchemy import Boolean, DateTime, Integer, String, Text

    key = (table_name, column.name)
    explicit = {
        ('business_credential_secret', 'public_id'): _volatile_uuid(),
        ('business_credential_secret', 'purpose'): "'credential'",
        ('business_credential_secret', 'key_version'): '1',
        ('business_provider_connection', 'public_id'): _volatile_uuid(),
        ('business_provider_connection', 'provider_key'): _volatile_digest(),
        ('business_provider_connection', 'display_name'): "'Recovered integration'",
        ('business_provider_connection', 'status'): "'draft'",
        ('business_provider_connection', 'health_status'): "'unknown'",
        ('business_provider_connection', 'capabilities'): "'[]'",
        ('business_provider_connection', 'public_config'): "'{}'",
        ('business_provider_connection', 'version'): '1',
        ('business_integration_sync_run', 'trigger'): "'manual'",
        ('business_integration_sync_run', 'status'): "'queued'",
        ('business_integration_sync_run', 'idempotency_key'): _volatile_digest(),
        ('business_integration_sync_run', 'max_attempts'): '5',
        ('business_integration_sync_run', 'payload_json'): "'{}'",
        ('business_integration_sync_run', 'metrics'): "'{}'",
        ('business_webhook_receipt', 'provider_event_id'): _volatile_digest(),
        ('business_webhook_receipt', 'idempotency_key'): _volatile_digest(),
        ('business_webhook_receipt', 'signature_digest'): _volatile_digest(64),
        ('business_webhook_receipt', 'payload_digest'): _volatile_digest(64),
        ('business_webhook_receipt', 'status'): "'received'",
        ('business_schedule_occurrence', 'external_id'): _volatile_digest(),
        ('business_schedule_occurrence', 'title'): "'Recovered schedule item'",
        ('business_schedule_occurrence', 'kind'): "'other'",
        ('business_schedule_occurrence', 'timezone'): "'UTC'",
        ('business_schedule_occurrence', 'status'): "'scheduled'",
        ('business_schedule_occurrence', 'skill_level'): "'all'",
        ('business_schedule_occurrence', 'payload_hash'): _volatile_digest(64),
        ('business_booking_event', 'event_type'): "'click'",
        ('business_booking_event', 'event_key'): _volatile_digest(),
        ('business_booking_event', 'action'): "'booking'",
        ('business_booking_event', 'source'): "'third_shot'",
        ('business_link_health_check', 'link_kind'): "'website'",
        ('business_link_health_check', 'url_hash'): _volatile_digest(64),
        ('business_link_health_check', 'status'): "'unreachable'",
        ('business_integration_audit_event', 'actor_kind'): "'system'",
        ('business_integration_audit_event', 'action'): "'schema_repair'",
        ('business_integration_audit_event', 'metadata_json'): "'{}'",
    }
    if key in explicit:
        return explicit[key]
    if isinstance(column.type, Boolean):
        return 'FALSE'
    if isinstance(column.type, DateTime):
        return 'CURRENT_TIMESTAMP'
    if isinstance(column.type, Integer):
        return '0'
    if isinstance(column.type, (String, Text)):
        return "''"
    raise RuntimeError(
        f'No safe integration schema backfill for {table_name}.{column.name}'
    )


def _add_missing_columns(connection, schema, model_tables):
    from sqlalchemy import inspect, text

    for table in model_tables:
        inspector = inspect(connection)
        if table.name not in set(inspector.get_table_names(schema=schema)):
            continue
        existing = {
            item['name']
            for item in inspector.get_columns(table.name, schema=schema)
        }
        qualified = _qualified_name(connection, schema, table.name)
        quote = connection.dialect.identifier_preparer.quote
        for column in table.columns:
            if column.name in existing:
                continue
            if column.primary_key:
                definition = f'{quote(column.name)} SERIAL'
            else:
                type_sql = column.type.compile(dialect=connection.dialect)
                definition = f'{quote(column.name)} {type_sql}'
                if not column.nullable and not column.foreign_keys:
                    definition += (
                        ' NOT NULL DEFAULT '
                        + _safe_column_default(table.name, column)
                    )
            connection.execute(text(
                f'ALTER TABLE {qualified} ADD COLUMN {definition}'
            ))


def _backfill_integration_foreign_keys(connection, schema):
    from sqlalchemy import text

    q = lambda name: _qualified_name(connection, schema, name)
    statements = (
        f'UPDATE {q("business_provider_connection")} AS item '
        f'SET created_by_id = profile.owner_id FROM {q("business_profile")} AS profile '
        'WHERE item.created_by_id IS NULL AND item.business_id = profile.id',
        f'UPDATE {q("business_schedule_occurrence")} AS item '
        f'SET business_id = provider.business_id FROM {q("business_provider_connection")} AS provider '
        'WHERE item.business_id IS NULL AND item.connection_id = provider.id',
        f'UPDATE {q("business_booking_event")} AS item '
        f'SET business_id = provider.business_id FROM {q("business_provider_connection")} AS provider '
        'WHERE item.business_id IS NULL AND item.connection_id = provider.id',
        f'UPDATE {q("business_booking_event")} AS item '
        f'SET business_id = occurrence.business_id FROM {q("business_schedule_occurrence")} AS occurrence '
        'WHERE item.business_id IS NULL AND item.occurrence_id = occurrence.id',
        f'UPDATE {q("business_link_health_check")} AS item '
        f'SET business_id = provider.business_id FROM {q("business_provider_connection")} AS provider '
        'WHERE item.business_id IS NULL AND item.connection_id = provider.id',
        f'UPDATE {q("business_integration_audit_event")} AS item '
        f'SET business_id = provider.business_id FROM {q("business_provider_connection")} AS provider '
        'WHERE item.business_id IS NULL AND item.connection_id = provider.id',
    )
    for statement in statements:
        connection.execute(text(statement))


def _converge_column_nullability(connection, schema, model_tables):
    from sqlalchemy import inspect, text

    _backfill_integration_foreign_keys(connection, schema)
    quote = connection.dialect.identifier_preparer.quote
    for table in model_tables:
        inspector = inspect(connection)
        if table.name not in set(inspector.get_table_names(schema=schema)):
            continue
        reflected = {
            item['name']: item
            for item in inspector.get_columns(table.name, schema=schema)
        }
        qualified = _qualified_name(connection, schema, table.name)
        for column in table.columns:
            item = reflected.get(column.name)
            if item is None:
                continue
            column_name = quote(column.name)
            if column.nullable:
                if not item.get('nullable', True):
                    connection.execute(text(
                        f'ALTER TABLE {qualified} ALTER COLUMN '
                        f'{column_name} DROP NOT NULL'
                    ))
                continue
            if item.get('nullable', True):
                if column.primary_key:
                    remaining = connection.execute(text(
                        f'SELECT COUNT(*) FROM {qualified} '
                        f'WHERE {column_name} IS NULL'
                    )).scalar_one()
                    if remaining:
                        raise RuntimeError(
                            f'Cannot safely backfill {table.name}.{column.name}'
                        )
                elif column.foreign_keys:
                    remaining = connection.execute(text(
                        f'SELECT COUNT(*) FROM {qualified} '
                        f'WHERE {column_name} IS NULL'
                    )).scalar_one()
                    if remaining:
                        raise RuntimeError(
                            'Cannot safely backfill integration foreign key '
                            f'{table.name}.{column.name}; repair the orphaned '
                            'rows before retrying.'
                        )
                else:
                    connection.execute(text(
                        f'UPDATE {qualified} SET {column_name} = '
                        f'{_safe_column_default(table.name, column)} '
                        f'WHERE {column_name} IS NULL'
                    ))
                connection.execute(text(
                    f'ALTER TABLE {qualified} ALTER COLUMN '
                    f'{column_name} SET NOT NULL'
                ))


def _repair_indexes(connection, schema, model_tables):
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateIndex

    by_table = {table.name: table for table in model_tables}
    for table_name, expected in REQUIRED_INDEXES.items():
        table = by_table[table_name]
        metadata_indexes = {item.name: item for item in table.indexes}
        inspector = inspect(connection)
        actual = {
            item.get('name'): item
            for item in inspector.get_indexes(table_name, schema=schema)
        }
        for name, (columns, unique) in expected.items():
            item = actual.get(name)
            correct = _index_matches(item, columns, unique)
            if correct:
                continue
            if item is not None:
                connection.execute(text(
                    f'DROP INDEX {_qualified_name(connection, schema, name)}'
                ))
            connection.execute(CreateIndex(metadata_indexes[name]))


def _repair_primary_keys(connection, schema, model_tables):
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import AddConstraint

    quote = connection.dialect.identifier_preparer.quote
    for table in model_tables:
        inspector = inspect(connection)
        actual = inspector.get_pk_constraint(table.name, schema=schema) or {}
        expected = REQUIRED_PRIMARY_KEYS[table.name]
        if tuple(actual.get('constrained_columns') or ()) == expected:
            continue
        qualified = _qualified_name(connection, schema, table.name)
        if actual.get('name'):
            connection.execute(text(
                f'ALTER TABLE {qualified} DROP CONSTRAINT '
                f'{quote(actual["name"])}'
            ))
        # ``AddConstraint`` otherwise isolates the model constraint from future
        # ``CREATE TABLE`` statements by mutating its private ``_create_rule``.
        # Migration planning must never change the live application metadata.
        connection.execute(AddConstraint(
            table.primary_key,
            isolate_from_table=False,
        ))


def _repair_constraints(connection, schema, model_tables):
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import AddConstraint

    by_table = {table.name: table for table in model_tables}
    quote = connection.dialect.identifier_preparer.quote
    for table_name, table in by_table.items():
        constraints = {
            item.name: item for item in table.constraints if item.name
        }
        qualified = _qualified_name(connection, schema, table_name)

        for name, columns in REQUIRED_UNIQUES.get(table_name, {}).items():
            inspector = inspect(connection)
            actual = {
                item.get('name'): tuple(item.get('column_names') or ())
                for item in inspector.get_unique_constraints(
                    table_name, schema=schema,
                )
            }
            if actual.get(name) == columns:
                continue
            if name in actual:
                connection.execute(text(
                    f'ALTER TABLE {qualified} DROP CONSTRAINT {quote(name)}'
                ))
            connection.execute(AddConstraint(
                constraints[name],
                isolate_from_table=False,
            ))

        for name, expected_expression in REQUIRED_CHECKS.get(table_name, {}).items():
            inspector = inspect(connection)
            actual = {
                item.get('name'): _normalized_check_sql(item.get('sqltext'))
                for item in inspector.get_check_constraints(
                    table_name, schema=schema,
                )
            }
            correct = name in actual and _check_expression_matches(
                expected_expression, actual[name],
            )
            if correct:
                continue
            if name in actual:
                connection.execute(text(
                    f'ALTER TABLE {qualified} DROP CONSTRAINT {quote(name)}'
                ))
            connection.execute(AddConstraint(
                constraints[name],
                isolate_from_table=False,
            ))

        for name, expected in REQUIRED_FOREIGN_KEYS.get(table_name, {}).items():
            columns, referred_table, referred_columns = expected
            inspector = inspect(connection)
            actual = inspector.get_foreign_keys(table_name, schema=schema)
            correct = any(
                _foreign_key_matches(
                    item, name, columns, referred_table, referred_columns,
                    schema,
                )
                for item in actual
            )
            if correct:
                continue
            conflicting = [
                item for item in actual
                if item.get('name') == name
                or tuple(item.get('constrained_columns') or ()) == columns
            ]
            for item in conflicting:
                if not item.get('name'):
                    raise RuntimeError(
                        f'Cannot replace unnamed foreign key on {table_name}'
                    )
                connection.execute(text(
                    f'ALTER TABLE {qualified} DROP CONSTRAINT '
                    f'{quote(item["name"])}'
                ))
            connection.execute(AddConstraint(
                constraints[name],
                isolate_from_table=False,
            ))


def _upgrade_existing_foundation(engine, schema):
    """Converge every existing provider table to the complete model schema."""
    from sqlalchemy import inspect, text

    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Integration schema repair requires PostgreSQL.')
    model_tables = _foundation_model_tables()
    with engine.begin() as connection:
        connection.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext("
            "'third-shot:business-integration-foundation'))"
        ))
        if schema:
            quote = connection.dialect.identifier_preparer.quote
            connection.execute(text(
                f'SET LOCAL search_path TO {quote(schema)}, public'
            ))
        # PostgreSQL DDL is transactional. Creating missing tables under the
        # same advisory lock means concurrent operators cannot interleave a
        # partial foundation, and a later repair failure rolls it all back.
        for table in model_tables:
            table.create(connection, checkfirst=True)
        _add_missing_columns(connection, schema, model_tables)
        _converge_column_nullability(connection, schema, model_tables)
        _repair_primary_keys(connection, schema, model_tables)
        _repair_indexes(connection, schema, model_tables)
        _repair_constraints(connection, schema, model_tables)
        gaps = schema_gaps(inspect(connection), schema)
        if gaps:
            raise RuntimeError(
                'Integration schema repair incomplete: ' + '; '.join(gaps)
            )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Install or verify provider integration foundation tables.',
    )
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, inspect
    from backend.config import PG_SCHEMA
    from scripts.migrate_production_schema import (
        _preflight_existing_app,
        _validated_target_url,
    )

    target = _validated_target_url()
    engine = create_engine(
        target, pool_pre_ping=True,
        connect_args={'options': f'-csearch_path={PG_SCHEMA}'},
    )
    try:
        _preflight_existing_app(engine)
        inspector = inspect(engine)
        if 'business_profile' not in set(
            inspector.get_table_names(schema=PG_SCHEMA)
        ):
            raise RuntimeError(
                'business_profile is required; run the main production '
                'schema migration first.'
            )
        before = schema_gaps(inspector, PG_SCHEMA)
    finally:
        engine.dispose()
    if args.check_only:
        if before:
            raise RuntimeError('Integration schema verification failed: ' + '; '.join(before))
        print('Business integration foundation schema check passed.')
        return 0

    os.environ.update({
        'APP_ENV': 'production',
        'MFA_ENCRYPTION_KEY': 'cptEwcGPWoQwTRpx7LZH3BaiGR5MbnTsyqs1PjdFGgA=',
        'BUSINESS_CREDENTIAL_VAULT': 'disabled',
        'SERVERLESS_RUNTIME': 'true',
        'SCHEMA_MANAGEMENT_ENABLED': 'false',
        'AUTO_CREATE_DB': 'false',
        'AUTO_SEED_COURTS': 'false',
        'RATE_LIMIT_ENABLED': 'false',
        'PUSH_DELIVERY_ENABLED': 'false',
        'DATABASE_URL': target,
        'SECRET_KEY': 'integration-migration-process-secret-not-used-for-serving',
        'MFA_ENCRYPTION_KEY': (
            'cptEwcGPWoQwTRpx7LZH3BaiGR5MbnTsyqs1PjdFGgA='
        ),
    })
    from backend.app import app, db
    with app.app_context():
        _upgrade_existing_foundation(db.engine, PG_SCHEMA)
        gaps = schema_gaps(inspect(db.engine), PG_SCHEMA)
        if gaps:
            raise RuntimeError('Integration schema verification failed: ' + '; '.join(gaps))
    print('Business integration foundation migration completed and verified.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'Integration foundation migration failed: {error}', file=sys.stderr)
        raise SystemExit(1)
