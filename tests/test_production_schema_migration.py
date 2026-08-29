"""Safety contracts for the operator-only PostgreSQL schema migration."""

import pytest

from scripts.migrate_production_schema import (
    REQUIRED_CHECK_CONSTRAINTS,
    REQUIRED_COLUMNS,
    REQUIRED_EXACT_UNIQUE_INDEXES,
    REQUIRED_FOREIGN_KEYS,
    REQUIRED_INDEXES,
    REQUIRED_PARTIAL_UNIQUE_INDEXES,
    REQUIRED_UNIQUES,
    _normalize_postgres_url,
    _schema_gaps,
    _validated_target_url,
)


class FakeInspector:
    def __init__(self):
        self.columns = {
            table: set(columns) for table, columns in REQUIRED_COLUMNS.items()
        }
        self.indexes = {
            table: set(indexes) for table, indexes in REQUIRED_INDEXES.items()
        }
        self.partial_indexes = {
            table: {
                name: {
                    'name': name,
                    'unique': True,
                    'column_names': list(shape[0]),
                    'dialect_options': {'postgresql_where': shape[1]},
                }
                for name, shape in indexes.items()
            }
            for table, indexes in REQUIRED_PARTIAL_UNIQUE_INDEXES.items()
        }
        self.exact_indexes = {
            table: {
                name: {
                    'name': name,
                    'unique': True,
                    'column_names': list(columns),
                    'dialect_options': {},
                }
                for name, columns in indexes.items()
            }
            for table, indexes in REQUIRED_EXACT_UNIQUE_INDEXES.items()
        }
        self.uniques = {
            table: set(names) for table, names in REQUIRED_UNIQUES.items()
        }
        self.checks = {
            table: set(names)
            for table, names in REQUIRED_CHECK_CONSTRAINTS.items()
        }
        self.foreign_keys = {
            table: {
                name: {
                    'name': name,
                    'constrained_columns': list(shape[0]),
                    'referred_table': shape[1],
                    'referred_columns': list(shape[2]),
                    'referred_schema': 'picklepals',
                }
                for name, shape in constraints.items()
            }
            for table, constraints in REQUIRED_FOREIGN_KEYS.items()
        }

    def get_table_names(self, schema=None):
        return sorted(self.columns)

    def get_columns(self, table, schema=None):
        return [{'name': name} for name in sorted(self.columns[table])]

    def get_indexes(self, table, schema=None):
        regular = [
            {'name': name, 'unique': False}
            for name in sorted(self.indexes.get(table, set()))
        ]
        return (
            regular
            + list(self.partial_indexes.get(table, {}).values())
            + list(self.exact_indexes.get(table, {}).values())
        )

    def get_unique_constraints(self, table, schema=None):
        return [
            {'name': name}
            for name in sorted(self.uniques.get(table, set()))
        ]

    def get_foreign_keys(self, table, schema=None):
        return list(self.foreign_keys.get(table, {}).values())

    def get_check_constraints(self, table, schema=None):
        return [
            {'name': name}
            for name in sorted(self.checks.get(table, set()))
        ]


def test_crew_schema_verifier_detects_missing_table_column_index_unique_and_fk():
    inspector = FakeInspector()
    assert _schema_gaps(inspector) == []

    inspector.columns.pop('crew_chat_read')
    inspector.columns['game'].remove('crew_roster_version')
    inspector.indexes['message'].remove('ix_message_crew_id')
    inspector.uniques['crew_invite'].remove('uq_crew_invitee')
    inspector.foreign_keys['message'].pop('message_crew_id_fkey')
    gaps = _schema_gaps(inspector)
    assert 'missing table crew_chat_read' in gaps
    assert "game missing columns ['crew_roster_version']" in gaps
    assert "message missing indexes ['ix_message_crew_id']" in gaps
    assert "crew_invite missing unique constraints ['uq_crew_invitee']" in gaps
    assert 'message missing foreign key message_crew_id_fkey' in gaps


def test_crew_schema_verifier_rejects_named_fk_with_wrong_columns_or_schema():
    inspector = FakeInspector()
    inspector.foreign_keys['game']['game_crew_id_fkey'][
        'constrained_columns'
    ] = ['creator_id']
    inspector.foreign_keys['notification'][
        'notification_related_crew_id_fkey'
    ]['referred_schema'] = 'public'

    gaps = _schema_gaps(inspector)

    assert (
        'game foreign key game_crew_id_fkey has wrong target or columns'
        in gaps
    )
    assert (
        'notification foreign key notification_related_crew_id_fkey has wrong '
        'target or columns'
        in gaps
    )


def test_crew_schema_verifier_accepts_equivalent_fk_with_legacy_name():
    inspector = FakeInspector()
    reflected = inspector.foreign_keys['message'].pop(
        'message_crew_id_fkey'
    )
    reflected['name'] = 'legacy_message_crew_reference'
    inspector.foreign_keys['message'][reflected['name']] = reflected

    assert _schema_gaps(inspector) == []


def test_release_schema_verifier_requires_instant_provenance_and_exact_presence_index():
    inspector = FakeInspector()
    inspector.columns['game'].remove('is_instant')
    active_index = inspector.partial_indexes['check_in'][
        'uq_check_in_active_user'
    ]
    active_index['unique'] = False
    active_index['column_names'] = ['user_id', 'court_id']
    active_index['dialect_options']['postgresql_where'] = (
        'checked_out_at IS NULL AND court_id > 0'
    )

    gaps = _schema_gaps(inspector)

    assert "game missing columns ['is_instant']" in gaps
    assert (
        'check_in index uq_check_in_active_user must be unique on '
        "['user_id'] where checked_out_at is null"
    ) in gaps


def test_release_schema_verifier_requires_exact_game_attempt_contract():
    inspector = FakeInspector()
    inspector.columns['game'].remove('client_attempt_fingerprint')
    attempt_index = inspector.exact_indexes['game']['uq_game_creator_attempt']
    attempt_index['column_names'] = ['client_attempt_id', 'creator_id']
    attempt_index['dialect_options'] = {
        'postgresql_where': 'client_attempt_id IS NOT NULL',
        'postgresql_nulls_not_distinct': True,
    }

    gaps = _schema_gaps(inspector)

    assert "game missing columns ['client_attempt_fingerprint']" in gaps
    assert (
        'game index uq_game_creator_attempt must be a nonpartial unique index '
        "on ['creator_id', 'client_attempt_id'] with distinct nulls"
    ) in gaps

    inspector = FakeInspector()
    inspector.exact_indexes['game'].pop('uq_game_creator_attempt')
    assert (
        'game missing exact unique index uq_game_creator_attempt'
        in _schema_gaps(inspector)
    )


def test_release_schema_verifier_requires_arrival_history_and_both_active_slots():
    inspector = FakeInspector()
    inspector.columns['game_arrival_intent'].remove('last_announced_at')
    inspector.partial_indexes['game_arrival_intent'][
        'uq_game_arrival_active_user'
    ]['column_names'] = ['user_id', 'game_id']
    inspector.partial_indexes['game_arrival_intent'][
        'uq_game_arrival_active_game'
    ]['dialect_options']['postgresql_where'] = (
        'active IS TRUE AND ended_at IS NULL'
    )
    inspector.uniques['game_arrival_intent'].remove(
        'uq_game_arrival_user_attempt'
    )
    inspector.foreign_keys['game_arrival_intent'].pop(
        'game_arrival_intent_user_id_fkey'
    )

    gaps = _schema_gaps(inspector)

    assert (
        "game_arrival_intent missing columns ['last_announced_at']" in gaps
    )
    assert (
        'game_arrival_intent index uq_game_arrival_active_user must be unique '
        "on ['user_id'] where active is true"
    ) in gaps
    assert (
        'game_arrival_intent index uq_game_arrival_active_game must be unique '
        "on ['game_id'] where active is true"
    ) in gaps
    assert (
        'game_arrival_intent missing unique constraints '
        "['uq_game_arrival_user_attempt']"
    ) in gaps
    assert (
        'game_arrival_intent missing foreign key '
        'game_arrival_intent_user_id_fkey'
    ) in gaps


def test_release_schema_verifier_requires_play_pulse_retry_ledger():
    inspector = FakeInspector()
    inspector.columns['play_availability_pulse'].remove(
        'accept_client_attempt_fingerprint'
    )
    inspector.partial_indexes['play_availability_pulse'][
        'uq_play_availability_pulse_active_user'
    ]['dialect_options']['postgresql_where'] = (
        'active IS TRUE AND ended_at IS NULL'
    )
    inspector.uniques['play_availability_pulse'].remove(
        'uq_play_availability_pulse_accept_attempt'
    )
    inspector.foreign_keys['play_availability_pulse'].pop(
        'play_availability_pulse_accepted_game_id_fkey'
    )
    inspector.checks['play_availability_pulse'].remove(
        'ck_play_availability_pulse_positive_window'
    )

    gaps = _schema_gaps(inspector)

    assert (
        'play_availability_pulse missing columns '
        "['accept_client_attempt_fingerprint']"
    ) in gaps
    assert (
        'play_availability_pulse index '
        'uq_play_availability_pulse_active_user must be unique on '
        "['user_id'] where active is true"
    ) in gaps
    assert (
        'play_availability_pulse missing unique constraints '
        "['uq_play_availability_pulse_accept_attempt']"
    ) in gaps
    assert (
        'play_availability_pulse missing foreign key '
        'play_availability_pulse_accepted_game_id_fkey'
    ) in gaps
    assert (
        'play_availability_pulse missing check constraints '
        "['ck_play_availability_pulse_positive_window']"
    ) in gaps


def test_release_schema_verifier_requires_game_open_call_ledger():
    inspector = FakeInspector()
    inspector.columns['game_open_call'].remove('court_message_id')
    inspector.partial_indexes['game_open_call'][
        'uq_game_open_call_active_game'
    ]['column_names'] = ['game_id', 'created_by_id']
    inspector.uniques['game_open_call'].remove(
        'uq_game_open_call_game_creator'
    )
    inspector.foreign_keys['game_open_call'].pop(
        'game_open_call_court_message_id_fkey'
    )

    gaps = _schema_gaps(inspector)

    assert "game_open_call missing columns ['court_message_id']" in gaps
    assert (
        'game_open_call index uq_game_open_call_active_game must be unique '
        "on ['game_id'] where active is true"
    ) in gaps
    assert (
        'game_open_call missing unique constraints '
        "['uq_game_open_call_game_creator']"
    ) in gaps
    assert (
        'game_open_call missing foreign key '
        'game_open_call_court_message_id_fkey'
    ) in gaps


def test_production_migration_requires_a_direct_postgres_url(monkeypatch):
    assert _normalize_postgres_url('postgres://db.example/app') == (
        'postgresql+psycopg://db.example/app'
    )
    monkeypatch.setenv(
        'TARGET_DATABASE_URL',
        'postgresql://user:pass@sample-pooler.us-east-1.aws.neon.tech/app',
    )
    with pytest.raises(RuntimeError, match='direct/unpooled'):
        _validated_target_url()

    monkeypatch.setenv('TARGET_DATABASE_URL', 'sqlite:///wrong.db')
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        _validated_target_url()
