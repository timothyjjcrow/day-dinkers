"""Focused safety tests for the one-time SQLite recovery migration."""

from __future__ import annotations

import importlib.util
import stat
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, MetaData, Table, create_engine, select,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'migrate_sqlite_recovery.py'
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    'migrate_sqlite_recovery', SCRIPT_PATH,
)
assert SCRIPT_SPEC and SCRIPT_SPEC.loader
migration = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(migration)

RECOVERED_UPDATED_AT = datetime(2026, 7, 5, 12, 0)


def _orphan_repair_metadata():
    metadata = MetaData()
    Table('club', metadata, Column('id', Integer, primary_key=True))
    for table_name in ('game', 'league', 'tournament'):
        Table(
            table_name,
            metadata,
            Column('id', Integer, primary_key=True),
            Column('club_id', Integer, ForeignKey('club.id'), nullable=True),
            Column(
                'updated_at', DateTime, nullable=False,
                onupdate=datetime.utcnow,
            ),
        )
    return metadata


def _create_recovery_fixture(path, *, unexpected_game_id=None):
    metadata = _orphan_repair_metadata()
    engine = create_engine(f'sqlite:///{path}')
    metadata.create_all(engine)
    tables = migration._metadata_tables(metadata)
    game_rows = [
        {'id': 54, 'club_id': 9001, 'updated_at': RECOVERED_UPDATED_AT},
        {'id': 59, 'club_id': 9002, 'updated_at': RECOVERED_UPDATED_AT},
    ]
    if unexpected_game_id is not None:
        game_rows.append({
            'id': unexpected_game_id,
            'club_id': 9003,
            'updated_at': RECOVERED_UPDATED_AT,
        })
    with engine.begin() as connection:
        connection.execute(tables['game'].insert(), game_rows)
        connection.execute(
            tables['league'].insert(),
            [{
                'id': 3,
                'club_id': 9001,
                'updated_at': RECOVERED_UPDATED_AT,
            }],
        )
        connection.execute(
            tables['tournament'].insert(),
            [{
                'id': 1,
                'club_id': 9001,
                'updated_at': RECOVERED_UPDATED_AT,
            }],
        )
    engine.dispose()
    return metadata


def _club_references(connection, metadata):
    tables = migration._metadata_tables(metadata)
    return {
        table_name: connection.execute(
            select(tables[table_name].c.id, tables[table_name].c.club_id)
            .order_by(tables[table_name].c.id)
        ).all()
        for table_name in ('game', 'league', 'tournament')
    }


def test_exact_orphan_manifest_is_repaired_only_on_writable_temp_copy(tmp_path):
    source_path = tmp_path / 'preserved.db'
    metadata = _create_recovery_fixture(source_path)
    source_path.chmod(0o400)

    working_dir = tmp_path / 'working'
    working_dir.mkdir()
    working_path = migration._make_working_copy(source_path, working_dir)

    assert stat.S_IMODE(source_path.stat().st_mode) == 0o400
    assert stat.S_IMODE(working_path.stat().st_mode) == 0o600

    working_engine = create_engine(f'sqlite:///{working_path}')
    with working_engine.begin() as connection:
        migration._verify_metadata_alignment(connection, metadata)
        repaired = migration._repair_expected_orphans(connection, metadata)
        assert repaired == migration.EXPECTED_NULLABLE_ORPHAN_REPAIRS
        assert _club_references(connection, metadata) == {
            'game': [(54, None), (59, None)],
            'league': [(3, None)],
            'tournament': [(1, None)],
        }
        for table_name in ('game', 'league', 'tournament'):
            assert set(connection.scalars(
                select(migration._metadata_tables(metadata)[table_name].c.updated_at)
            )) == {RECOVERED_UPDATED_AT}
    working_engine.dispose()

    preserved_engine = create_engine(f'sqlite:///{source_path}')
    with preserved_engine.connect() as connection:
        assert _club_references(connection, metadata) == {
            'game': [(54, 9001), (59, 9002)],
            'league': [(3, 9001)],
            'tournament': [(1, 9001)],
        }
    preserved_engine.dispose()


def test_unexpected_orphan_aborts_before_any_repair(tmp_path):
    source_path = tmp_path / 'unexpected.db'
    metadata = _create_recovery_fixture(source_path, unexpected_game_id=60)
    engine = create_engine(f'sqlite:///{source_path}')

    with engine.begin() as connection:
        before = _club_references(connection, metadata)
        with pytest.raises(RuntimeError, match='exact audited set'):
            migration._repair_expected_orphans(connection, metadata)
        assert _club_references(connection, metadata) == before
    engine.dispose()


def test_metadata_alignment_rejects_missing_columns_and_extra_tables():
    expected = MetaData()
    Table(
        'item',
        expected,
        Column('id', Integer, primary_key=True),
        Column('value', Integer, nullable=False),
    )
    actual = MetaData()
    Table('item', actual, Column('id', Integer, primary_key=True))
    Table('unexpected', actual, Column('id', Integer, primary_key=True))
    engine = create_engine('sqlite:///:memory:')
    actual.create_all(engine)

    with engine.connect() as connection:
        with pytest.raises(RuntimeError) as raised:
            migration._verify_metadata_alignment(connection, expected)
    message = str(raised.value)
    assert "extra tables=['unexpected']" in message
    assert "item: missing=['value'], extra=[]" in message
    engine.dispose()


def test_primary_key_verification_rejects_equal_counts_with_different_ids():
    metadata = MetaData()
    item = Table('item', metadata, Column('id', Integer, primary_key=True))
    engine = create_engine('sqlite:///:memory:')
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(item.insert(), [{'id': 1}, {'id': 3}])
        with pytest.raises(RuntimeError, match='Primary-key-set verification failed'):
            migration._verify_counts_and_primary_keys(
                connection,
                [item],
                {'item': 2},
                {'item': frozenset({1, 2})},
            )
    engine.dispose()


def test_target_foreign_key_verification_rejects_orphan():
    metadata = MetaData()
    Table('parent', metadata, Column('id', Integer, primary_key=True))
    child = Table(
        'child',
        metadata,
        Column('id', Integer, primary_key=True),
        Column('parent_id', Integer, ForeignKey('parent.id'), nullable=False),
    )
    engine = create_engine('sqlite:///:memory:')
    metadata.create_all(engine)
    with engine.begin() as connection:
        # SQLite does not enforce foreign keys unless PRAGMA foreign_keys is on,
        # which lets this test prove the explicit post-copy verifier works.
        connection.execute(child.insert(), {'id': 1, 'parent_id': 404})
        with pytest.raises(RuntimeError, match='Target PostgreSQL foreign-key'):
            migration._verify_no_foreign_key_orphans(
                connection,
                metadata,
                label='Target PostgreSQL',
            )
    engine.dispose()


def test_deferred_fk_restore_preserves_updated_timestamp(tmp_path):
    source_path = tmp_path / 'deferred.db'
    metadata = _create_recovery_fixture(source_path)
    tables = migration._metadata_tables(metadata)
    engine = create_engine(f'sqlite:///{source_path}')

    with engine.begin() as connection:
        migration._apply_deferred_updates(connection, [
            (
                tables['tournament'],
                {'id': 1},
                {'club_id': None},
            ),
        ])
        row = connection.execute(
            select(
                tables['tournament'].c.club_id,
                tables['tournament'].c.updated_at,
            ).where(tables['tournament'].c.id == 1)
        ).one()
        assert row == (None, RECOVERED_UPDATED_AT)
    engine.dispose()
