"""PostgreSQL transaction contracts without accessing a live database."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from scripts import migrate_court_directory_exclusion_policy as migration


OLD_CHECK = "CHECK (((reason_code)::text = ANY ((ARRAY['foreign_venue'::character varying, 'invalid_test_record'::character varying])::text[])))"
NEW_CHECK = "CHECK (reason_code IN ('foreign_venue', 'invalid_test_record', 'pickleball_prohibited'))"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class Connection:
    def __init__(self, engine):
        self.engine = engine
        self.original = deepcopy(engine.state)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def begin(self):
        self.engine.log.append('BEGIN')

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.engine.log.append(sql)
        if sql == migration.CONSTRAINT_SQL:
            assert parameters == {'schema': 'picklepals', 'table': 'court_directory_exclusion',
                                  'constraint': 'ck_court_directory_exclusion_reason'}
            value = self.engine.state['definition']
            return Result([] if value is None else [
                {'definition': value, 'validated': self.engine.state['validated']}])
        if sql == migration.ROWS_SQL:
            return Result(deepcopy(self.engine.state['rows']))
        if sql == migration.DDL[0]:
            assert any('pg_advisory_xact_lock' in line for line in self.engine.log)
            self.engine.state['definition'] = None
        elif sql == migration.DDL[1]:
            if self.engine.fail_add:
                raise RuntimeError('Simulated constraint validation failure')
            self.engine.state['definition'] = NEW_CHECK
            if self.engine.corrupt_readback:
                self.engine.state['rows'][0]['active'] = False
        else:
            assert sql.startswith('SET ') or sql.startswith('SELECT pg_advisory_xact_lock')
        return Result([])

    def commit(self):
        self.committed = True
        self.engine.log.append('COMMIT')

    def rollback(self):
        if not self.committed:
            self.engine.state = deepcopy(self.original)
        self.engine.log.append('ROLLBACK')


class Engine:
    dialect = SimpleNamespace(name='postgresql')

    def __init__(self, definition=OLD_CHECK):
        self.state = {'definition': definition, 'validated': True,
                      'rows': [{'court_id': 11, 'active': True, 'reason_code': 'foreign_venue',
                                'reason': 'Reviewed foreign venue.', 'reviewed_by': 'Private audit name'},
                               {'court_id': 12, 'active': False, 'reason_code': 'invalid_test_record',
                                'reason': 'Admitted test fixture.'}]}
        self.log = []
        self.fail_add = False
        self.corrupt_readback = False

    def connect(self):
        return Connection(self)

    def dispose(self):
        pass


@pytest.mark.parametrize('definition,codes', [(OLD_CHECK, migration.OLD_REASONS),
                                             (NEW_CHECK, migration.NEW_REASONS)])
def test_postgres_casted_or_plain_reason_check_is_recognized(definition, codes):
    assert migration.reason_codes(definition) == codes


@pytest.mark.parametrize('definition', [
    "CHECK (reason_code IN ('foreign_venue'))",
    "CHECK (reason_code IN ('foreign_venue','invalid_test_record','closed'))",
    "CHECK (reason_code IN ('foreign_venue','invalid_test_record') OR true)",
    "CHECK (reason_code NOT IN ('foreign_venue','invalid_test_record'))",
    "CHECK (other_column IN ('foreign_venue','invalid_test_record'))",
    "CHECK (reason_code IN ('foreign_venue','foreign_venue','invalid_test_record'))",
])
def test_unexpected_existing_guard_requires_manual_review(definition):
    with pytest.raises(ValueError):
        migration.reason_codes(definition)


def test_dry_run_is_read_only_and_plans_only_two_constraint_statements():
    engine = Engine()
    original = deepcopy(engine.state)
    report = migration.migrate(engine)
    assert report['status'] == 'ready' and report['applied'] is False
    assert report['operations'] == list(migration.DDL)
    assert report['before']['row_count'] == 2
    assert engine.state == original
    assert 'SET TRANSACTION READ ONLY' in engine.log
    assert not any(line.startswith('ALTER ') for line in engine.log)
    assert engine.log[-1] == 'ROLLBACK'
    assert 'Private audit name' not in str(report)


def test_apply_intent_atomic_readback_and_idempotency_preserve_rows():
    engine = Engine()
    rows = deepcopy(engine.state['rows'])
    intents = []
    def save_intent(report):
        assert not any(line.startswith('ALTER ') for line in engine.log)
        intents.append(report)
    report = migration.migrate(engine, apply=True, before_write=save_intent)
    assert len(intents) == 1 and intents[0]['applied'] is False
    assert report['applied'] is True and report['status'] == 'migrated'
    assert engine.state['rows'] == rows
    assert report['before']['row_sha256'] == report['after']['row_sha256']
    assert set(report['after']['allowed_reasons']) == migration.NEW_REASONS
    assert [line for line in engine.log if line.startswith('ALTER ')] == list(migration.DDL)
    engine.log.clear()
    again = migration.migrate(engine, apply=True, before_write=intents.append)
    assert again['status'] == 'already_current' and again['applied'] is True
    assert again['operations'] == [] and engine.state['rows'] == rows
    assert not any(line.startswith('ALTER ') for line in engine.log)


@pytest.mark.parametrize('failure', ['fail_add', 'corrupt_readback'])
def test_failed_constraint_or_row_readback_rolls_back_entire_transaction(failure):
    engine = Engine()
    original = deepcopy(engine.state)
    setattr(engine, failure, True)
    with pytest.raises((ValueError, RuntimeError)):
        migration.migrate(engine, apply=True, before_write=lambda _: None)
    assert engine.state == original
    assert 'COMMIT' not in engine.log and engine.log[-1] == 'ROLLBACK'


def test_missing_unvalidated_and_invalid_values_never_run_ddl():
    for change in ('missing', 'unvalidated', 'invalid_value'):
        engine = Engine()
        if change == 'missing':
            engine.state['definition'] = None
        elif change == 'unvalidated':
            engine.state['validated'] = False
        else:
            engine.state['rows'][0]['reason_code'] = 'closed'
        with pytest.raises(ValueError):
            migration.migrate(engine, apply=True, before_write=lambda _: None)
        assert not any(line.startswith('ALTER ') for line in engine.log)


def test_missing_or_failed_immutable_intent_prevents_ddl():
    engine = Engine()
    with pytest.raises(ValueError, match='immutable intent'):
        migration.migrate(engine, apply=True)
    def failed(_):
        raise OSError('No audit storage')
    with pytest.raises(OSError):
        migration.migrate(engine, apply=True, before_write=failed)
    assert not any(line.startswith('ALTER ') for line in engine.log)


def test_cli_defaults_to_dry_and_refuses_to_overwrite_proof(tmp_path, monkeypatch):
    engine = Engine()
    monkeypatch.setattr(migration, 'target_engine', lambda: engine)
    output = tmp_path / 'dry.json'
    assert migration.main(['--output', str(output)]) == 0
    original = output.read_bytes()
    assert migration.main(['--apply', '--output', str(output)]) == 1
    assert output.read_bytes() == original
    assert not any(line.startswith('ALTER ') for line in engine.log)


def test_helper_has_no_runtime_import_and_is_not_uploaded_or_run_by_build():
    result = subprocess.run([sys.executable, '-c',
        'import scripts.migrate_court_directory_exclusion_policy; import sys; '
        'assert "backend.app" not in sys.modules'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    name = 'scripts/migrate_court_directory_exclusion_policy.py'
    root = Path(__file__).resolve().parents[1]
    ignore = (root / '.vercelignore').read_text()
    assert 'scripts/*' in ignore and '!' + name not in ignore
    assert 'migrate_court_directory_exclusion_policy' not in (root / 'scripts/vercel_build.py').read_text()
    assert 'migrate_court_directory_exclusion_policy' not in (root / 'backend/app.py').read_text()
