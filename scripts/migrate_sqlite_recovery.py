#!/usr/bin/env python3
"""Copy a recovered Third Shot SQLite database into empty PostgreSQL.

The target connection string is read only from TARGET_DATABASE_URL so it does
not appear in command arguments. The script upgrades a temporary copy of the
SQLite source with the application's normal additive migrations, refuses to
write into a non-empty target, preserves primary keys, repairs the one deferred
foreign-key cycle, resets PostgreSQL sequences, and verifies row counts, exact
primary-key sets, and foreign-key integrity.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


PG_SCHEMA = 'picklepals'
CHUNK_SIZE = 500
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# These are the only references in the recovered database that violate the
# current model foreign keys.  All three columns are nullable: the associated
# clubs were deleted while SQLite did not have FK declarations on columns that
# had been added by an earlier additive migration.  Keep this manifest exact so
# the recovery never grows into a general-purpose, silently lossy repair tool.
EXPECTED_NULLABLE_ORPHAN_REPAIRS = {
    ('game', 'club_id', 'club', 'id'): frozenset({54, 59}),
    ('league', 'club_id', 'club', 'id'): frozenset({3}),
    ('tournament', 'club_id', 'club', 'id'): frozenset({1}),
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _normalize_postgres_url(url: str) -> str:
    if url.startswith('postgres://'):
        return 'postgresql+psycopg://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


def _check_sqlite(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f'SQLite source does not exist: {path}')
    uri = f'file:{path.as_posix()}?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        result = conn.execute('PRAGMA integrity_check').fetchone()
    if not result or result[0] != 'ok':
        raise RuntimeError(f'SQLite integrity check failed: {result!r}')


def _make_working_copy(source_path: Path, temp_dir: Path) -> Path:
    working_copy = temp_dir / 'recovery.db'
    shutil.copy2(source_path, working_copy)
    # The preserved recovery is intentionally mode 0400. copy2 retains that
    # mode, so make only the disposable copy owner-readable/writable before the
    # app applies additive SQLite migrations.
    working_copy.chmod(0o600)
    return working_copy


def _set_source_environment(path: Path) -> None:
    os.environ['APP_ENV'] = 'development'
    os.environ['SERVERLESS_RUNTIME'] = 'false'
    os.environ['SCHEMA_MANAGEMENT_ENABLED'] = 'true'
    os.environ['DATABASE_URL'] = _sqlite_url(path)
    os.environ['AUTO_CREATE_DB'] = 'true'
    os.environ['AUTO_SEED_COURTS'] = 'false'
    os.environ['RATE_LIMIT_ENABLED'] = 'false'
    os.environ['RATE_LIMIT_BACKEND'] = 'memory'


def _chunks(rows, size=CHUNK_SIZE):
    batch = []
    for row in rows:
        batch.append(dict(row))
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _metadata_tables(metadata):
    return {table.name: table for table in metadata.sorted_tables}


def _verify_metadata_alignment(connection, metadata) -> None:
    """Require the upgraded SQLite copy to match every current model column."""
    from sqlalchemy import inspect

    expected = _metadata_tables(metadata)
    inspector = inspect(connection)
    actual_names = set(inspector.get_table_names())
    expected_names = set(expected)
    missing_tables = sorted(expected_names - actual_names)
    extra_tables = sorted(actual_names - expected_names)

    column_mismatches = []
    for table_name in sorted(expected_names & actual_names):
        expected_columns = {column.name for column in expected[table_name].columns}
        actual_columns = {
            column['name'] for column in inspector.get_columns(table_name)
        }
        missing = sorted(expected_columns - actual_columns)
        extra = sorted(actual_columns - expected_columns)
        if missing or extra:
            column_mismatches.append(
                f'{table_name}: missing={missing}, extra={extra}'
            )

    if missing_tables or extra_tables or column_mismatches:
        details = []
        if missing_tables:
            details.append(f'missing tables={missing_tables}')
        if extra_tables:
            details.append(f'extra tables={extra_tables}')
        details.extend(column_mismatches)
        raise RuntimeError(
            'Upgraded SQLite metadata does not match current models: '
            + '; '.join(details)
        )


def _row_identity(row, primary_keys):
    values = tuple(row[index] for index in range(len(primary_keys)))
    return values[0] if len(values) == 1 else values


def _foreign_key_orphan_manifest(connection, metadata):
    """Return target-model FK orphans keyed by their complete relationship."""
    from sqlalchemy import select

    manifest = {}
    for table in metadata.sorted_tables:
        primary_keys = list(table.primary_key.columns)
        if not primary_keys:
            raise RuntimeError(
                f'Cannot verify foreign keys for table without a primary key: '
                f'{table.name}'
            )
        for foreign_key in sorted(
            table.foreign_keys,
            key=lambda item: (
                item.parent.name,
                item.column.table.name,
                item.column.name,
            ),
        ):
            remote_table = foreign_key.column.table
            remote_alias = remote_table.alias(
                f'_recovery_fk_{table.name}_{foreign_key.parent.name}'
            )
            remote_column = remote_alias.c[foreign_key.column.name]
            statement = (
                select(*primary_keys)
                .select_from(
                    table.outerjoin(
                        remote_alias,
                        foreign_key.parent == remote_column,
                    )
                )
                .where(
                    foreign_key.parent.is_not(None),
                    remote_column.is_(None),
                )
                .order_by(*primary_keys)
            )
            identities = frozenset(
                _row_identity(row, primary_keys)
                for row in connection.execute(statement)
            )
            if identities:
                relationship = (
                    table.name,
                    foreign_key.parent.name,
                    remote_table.name,
                    foreign_key.column.name,
                )
                manifest[relationship] = identities
    return manifest


def _format_orphan_difference(actual, expected) -> str:
    details = []
    for relationship in sorted(set(actual) | set(expected)):
        actual_ids = actual.get(relationship, frozenset())
        expected_ids = expected.get(relationship, frozenset())
        if actual_ids == expected_ids:
            continue
        source_table, source_column, target_table, target_column = relationship
        details.append(
            f'{source_table}.{source_column}->{target_table}.{target_column}: '
            f'actual={sorted(actual_ids, key=repr)!r}, '
            f'expected={sorted(expected_ids, key=repr)!r}'
        )
    return '; '.join(details)


def _repair_expected_orphans(connection, metadata):
    """Null only the exact, audited orphan set on the temporary SQLite copy."""
    expected = EXPECTED_NULLABLE_ORPHAN_REPAIRS
    actual = _foreign_key_orphan_manifest(connection, metadata)
    if actual != expected:
        raise RuntimeError(
            'SQLite foreign-key orphan manifest was not the exact audited set; '
            'no repairs were applied: '
            + _format_orphan_difference(actual, expected)
        )

    tables = _metadata_tables(metadata)
    for relationship, identities in expected.items():
        table_name, column_name, remote_table, remote_column = relationship
        table = tables.get(table_name)
        if table is None:
            raise RuntimeError(f'Orphan repair table is missing: {table_name}')
        column = table.c[column_name]
        matching_foreign_keys = {
            (foreign_key.column.table.name, foreign_key.column.name)
            for foreign_key in column.foreign_keys
        }
        if (remote_table, remote_column) not in matching_foreign_keys:
            raise RuntimeError(
                f'Orphan repair no longer matches model FK: '
                f'{table_name}.{column_name}'
            )
        if not column.nullable:
            raise RuntimeError(
                f'Orphan repair column is no longer nullable: '
                f'{table_name}.{column_name}'
            )
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) != 1:
            raise RuntimeError(
                f'Orphan repair requires one primary key: {table_name}'
            )
        repair_values = {column_name: None}
        if 'updated_at' in table.c:
            # TimestampMixin supplies a client-side onupdate hook. Explicitly
            # retain the historical timestamp while repairing this one FK so
            # extraction does not make old rows look newly edited.
            repair_values['updated_at'] = table.c.updated_at
        result = connection.execute(
            table.update()
            .where(
                primary_keys[0].in_(identities),
                column.is_not(None),
            )
            .values(repair_values)
        )
        if result.rowcount != len(identities):
            raise RuntimeError(
                f'Orphan repair changed {result.rowcount} rows in {table_name}; '
                f'expected {len(identities)}'
            )

    remaining = _foreign_key_orphan_manifest(connection, metadata)
    if remaining:
        raise RuntimeError(
            'Foreign-key orphans remain after audited repair: '
            + _format_orphan_difference(remaining, {})
        )
    return actual


def _table_row_counts(connection, tables):
    from sqlalchemy import func, select

    return {
        table.name: connection.scalar(select(func.count()).select_from(table))
        for table in tables
    }


def _table_primary_key_sets(connection, tables):
    from sqlalchemy import select

    results = {}
    for table in tables:
        primary_keys = list(table.primary_key.columns)
        if not primary_keys:
            raise RuntimeError(
                f'Cannot verify rows for table without a primary key: {table.name}'
            )
        rows = connection.execute(
            select(*primary_keys).order_by(*primary_keys)
        )
        results[table.name] = frozenset(
            _row_identity(row, primary_keys) for row in rows
        )
    return results


def _verify_counts_and_primary_keys(
    target_connection,
    tables,
    source_counts,
    source_primary_keys,
) -> None:
    target_counts = _table_row_counts(target_connection, tables)
    count_mismatches = [
        f'{table.name}: source={source_counts[table.name]}, '
        f'target={target_counts[table.name]}'
        for table in tables
        if target_counts[table.name] != source_counts[table.name]
    ]
    if count_mismatches:
        raise RuntimeError(
            'Row-count verification failed: ' + '; '.join(count_mismatches)
        )

    target_primary_keys = _table_primary_key_sets(target_connection, tables)
    key_mismatches = []
    for table in tables:
        source_keys = source_primary_keys[table.name]
        target_keys = target_primary_keys[table.name]
        if source_keys != target_keys:
            key_mismatches.append(
                f'{table.name}: source_only={len(source_keys - target_keys)}, '
                f'target_only={len(target_keys - source_keys)}'
            )
    if key_mismatches:
        raise RuntimeError(
            'Primary-key-set verification failed: ' + '; '.join(key_mismatches)
        )


def _verify_no_foreign_key_orphans(connection, metadata, *, label: str) -> None:
    manifest = _foreign_key_orphan_manifest(connection, metadata)
    if manifest:
        raise RuntimeError(
            f'{label} foreign-key verification failed: '
            + _format_orphan_difference(manifest, {})
        )


def _deferred_foreign_keys(tables):
    """Columns whose referenced table is inserted later in dependency order."""
    positions = {table: index for index, table in enumerate(tables)}
    deferred = {}
    for table in tables:
        columns = {
            foreign_key.parent.name
            for foreign_key in table.foreign_keys
            if positions[foreign_key.column.table] >= positions[table]
        }
        if columns:
            deferred[table.name] = columns
    return deferred


def _apply_deferred_updates(connection, pending_updates) -> None:
    """Restore cycle-breaking FK values without changing audit timestamps."""
    for table, primary_key, values in pending_updates:
        predicate = None
        for column_name, value in primary_key.items():
            clause = table.c[column_name] == value
            predicate = clause if predicate is None else predicate & clause
        update_values = dict(values)
        if 'updated_at' in table.c and 'updated_at' not in update_values:
            update_values['updated_at'] = table.c.updated_at
        connection.execute(
            table.update().where(predicate).values(**update_values)
        )


def _qualified_table_name(table_name: str) -> str:
    safe_schema = PG_SCHEMA.replace('"', '""')
    safe_table = table_name.replace('"', '""')
    return f'"{safe_schema}"."{safe_table}"'


def _is_neon_pooler_url(url: str) -> bool:
    from sqlalchemy.engine import make_url

    host = make_url(url).host or ''
    return '-pooler.' in host.lower()


def _configure_runtime_role_search_path(connection) -> None:
    """Make the app schema work through Neon transaction pooling."""
    from sqlalchemy import text

    role_name = connection.scalar(text('SELECT current_user'))
    preparer = connection.dialect.identifier_preparer
    quoted_role = preparer.quote(role_name)
    quoted_schema = preparer.quote(PG_SCHEMA)
    connection.execute(text(
        f'ALTER ROLE {quoted_role} SET search_path TO {quoted_schema}, public'
    ))


def _copy_to_postgres(source_engine, target_engine, metadata):
    from sqlalchemy import Integer, func, select, text

    tables = list(metadata.sorted_tables)
    deferred_columns = _deferred_foreign_keys(tables)

    with target_engine.begin() as target:
        target.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PG_SCHEMA}"'))
    metadata.create_all(target_engine)

    nonempty = []
    with target_engine.connect() as target:
        for table in tables:
            count = target.scalar(select(func.count()).select_from(table))
            if count:
                nonempty.append(f'{table.name}={count}')
    if nonempty:
        raise RuntimeError(
            'Target schema is not empty; migration stopped without copying data: '
            + ', '.join(nonempty)
        )

    pending_updates = []
    with source_engine.connect() as source, target_engine.begin() as target:
        source_counts = _table_row_counts(source, tables)
        source_primary_keys = _table_primary_key_sets(source, tables)
        for table in tables:
            rows = source.execute(select(table)).mappings()
            for batch in _chunks(rows):
                for row in batch:
                    deferred_values = {
                        column: row[column]
                        for column in deferred_columns.get(table.name, ())
                        if row[column] is not None
                    }
                    if deferred_values:
                        primary_key = {
                            column.name: row[column.name]
                            for column in table.primary_key.columns
                        }
                        pending_updates.append((table, primary_key, deferred_values))
                        for column in deferred_values:
                            row[column] = None
                target.execute(table.insert(), batch)

        _apply_deferred_updates(target, pending_updates)

        for table in tables:
            primary_keys = list(table.primary_key.columns)
            if len(primary_keys) != 1 or not isinstance(primary_keys[0].type, Integer):
                continue
            primary_key = primary_keys[0]
            maximum = target.scalar(select(func.max(primary_key)))
            if maximum is None:
                continue
            sequence = target.scalar(
                text('SELECT pg_get_serial_sequence(:table_name, :column_name)'),
                {
                    'table_name': _qualified_table_name(table.name),
                    'column_name': primary_key.name,
                },
            )
            if sequence:
                target.execute(
                    text('SELECT setval(CAST(:sequence AS regclass), :value, true)'),
                    {'sequence': sequence, 'value': maximum},
                )

        _verify_counts_and_primary_keys(
            target,
            tables,
            source_counts,
            source_primary_keys,
        )
        _verify_no_foreign_key_orphans(
            target,
            metadata,
            label='Target PostgreSQL',
        )
        _configure_runtime_role_search_path(target)

    return source_counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Migrate a recovered Third Shot SQLite DB into empty PostgreSQL.',
    )
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument(
        '--check-only', action='store_true',
        help='upgrade and validate a temporary copy without contacting PostgreSQL',
    )
    args = parser.parse_args()

    source_path = args.source.expanduser().resolve()
    _check_sqlite(source_path)

    with tempfile.TemporaryDirectory(prefix='third-shot-recovery-') as temp_dir:
        working_copy = _make_working_copy(source_path, Path(temp_dir))
        _set_source_environment(working_copy)

        # Import only after selecting the temporary SQLite copy. App startup
        # applies the same additive migrations and invariant checks as a deploy.
        from backend.app import app, db
        from sqlalchemy import create_engine

        with app.app_context():
            _check_sqlite(working_copy)
            tables = list(db.metadata.sorted_tables)
            with db.engine.begin() as source:
                _verify_metadata_alignment(source, db.metadata)
                _repair_expected_orphans(source, db.metadata)
                source_counts = _table_row_counts(source, tables)

            if args.check_only:
                nonzero = sum(1 for count in source_counts.values() if count)
                total = sum(source_counts.values())
                print(
                    f'Source recovery check passed: {len(tables)} tables, '
                    f'{nonzero} non-empty, {total} total rows.'
                )
                return 0

            raw_target_url = os.getenv('TARGET_DATABASE_URL', '').strip()
            target_url = _normalize_postgres_url(raw_target_url)
            if not target_url.startswith('postgresql+psycopg://'):
                raise RuntimeError(
                    'TARGET_DATABASE_URL must be a PostgreSQL connection string.'
                )
            if _is_neon_pooler_url(target_url):
                raise RuntimeError(
                    "TARGET_DATABASE_URL must be Neon's direct/unpooled URL; "
                    'use the pooled URL only as the deployed DATABASE_URL.'
                )

            target_engine = create_engine(
                target_url,
                pool_pre_ping=True,
                connect_args={'options': f'-csearch_path={PG_SCHEMA}'},
            )
            try:
                copied_counts = _copy_to_postgres(db.engine, target_engine, db.metadata)
            finally:
                target_engine.dispose()

    nonzero = sum(1 for count in copied_counts.values() if count)
    total = sum(copied_counts.values())
    print(
        f'Migration completed and verified: {len(copied_counts)} tables, '
        f'{nonzero} non-empty, {total} total rows.'
    )
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Migration failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
