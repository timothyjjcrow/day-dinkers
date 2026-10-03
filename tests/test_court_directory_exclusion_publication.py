"""Atomic, audited foreign exclusions preserve dependencies and stale guards."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, event, select
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import Court, CourtDirectoryExclusion
from scripts.publish_court_directory_exclusions import (
    build_plan, digest, execute_plan, transaction, validate_plan, validate_research,
    write_json_exclusive,
)


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'exclusions.db'),
                           connect_args={'check_same_thread': False, 'timeout': 10})
    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata = MetaData()
    court = Court.__table__.to_metadata(metadata)
    CourtDirectoryExclusion.__table__.to_metadata(metadata)
    Table('unexpected_court_history', metadata,
          Column('id', Integer, primary_key=True), Column('venue_id', ForeignKey('court.id')))
    Table('conversation', metadata, Column('id', Integer, primary_key=True),
          Column('kind', String), Column('scope_id', Integer))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(court.insert(), [
            {'id': i, 'name': f'Foreign Court {i}', 'address': 'Wrong US address',
             'city': 'Wrong US city', 'state': 'IL', 'latitude': 41.8,
             'longitude': -87.9, 'num_courts': 4} for i in (1, 2)
        ])
    yield engine
    engine.dispose()


def research(*ids):
    return {'records': [{'court_id': court_id, 'reason_code': 'foreign_venue',
        'reason': 'Official operator confirms physical venue in Bradford, England.',
        'foreign_location': {'country_code': 'GB', 'country': 'United Kingdom',
                             'locality': 'Bradford', 'address': 'Highgate, Emm Lane, BD9 5PH'},
        'location_source_urls': ['https://example.test/operator'],
        'sources': [{'url': 'https://example.test/operator', 'title': 'Official club contact',
                     'authority': 'operator',
                     'accessed_at': '2026-10-03', 'facts': ['Physical venue in Bradford, England.']}]}
        for court_id in ids]}


def plan_for(engine, *ids):
    with transaction(engine) as connection:
        return build_plan(connection, research(*ids), 'Reviewed Operator')


def state(engine):
    with engine.connect() as connection:
        metadata = MetaData()
        return {name: [dict(r) for r in connection.execute(select(
            Table(name, metadata, autoload_with=connection))).mappings()]
            for name in ('court_directory_exclusion', 'court', 'unexpected_court_history', 'conversation')}


def test_dry_run_apply_retry_rollback_preserve_courts_and_dependencies(engine):
    plan = plan_for(engine, 1, 2)
    # New history may arrive after planning: visibility changes never move it.
    with engine.begin() as connection:
        history = Table('unexpected_court_history', MetaData(), autoload_with=connection)
        conversation = Table('conversation', MetaData(), autoload_with=connection)
        connection.execute(history.insert().values(venue_id=1))
        connection.execute(conversation.insert().values(kind='court', scope_id=2))
    before = state(engine)
    dry = execute_plan(engine, plan)
    assert not dry['applied'] and state(engine) == before
    assert dry['records'][0]['dependencies']['unexpected_court_history.venue_id'] == 1
    intents = []
    applied = execute_plan(engine, plan, apply=True, before_write=lambda x: intents.append(deepcopy(x)))
    assert len(intents) == 1 and applied['plan_sha256'] == digest(plan)
    first = state(engine)
    again = execute_plan(engine, plan, apply=True, before_write=intents.append)
    assert all(r['status'] == 'already_applied' for r in again['records'])
    assert state(engine) == first
    execute_plan(engine, plan, apply=True, rollback=True, before_write=intents.append)
    disabled = state(engine)
    assert all(not r['active'] for r in disabled['court_directory_exclusion'])
    assert {k:v for k,v in disabled.items() if k != 'court_directory_exclusion'} == {
        k:v for k,v in before.items() if k != 'court_directory_exclusion'}
    execute_plan(engine, plan, apply=True, rollback=True, before_write=intents.append)
    assert state(engine) == disabled
    with pytest.raises(ValueError, match='Stale exclusion'):
        execute_plan(engine, plan, apply=True, before_write=intents.append)
    fresh = plan_for(engine, 1, 2)
    execute_plan(engine, fresh, apply=True, before_write=intents.append)
    execute_plan(engine, fresh, apply=True, rollback=True, before_write=intents.append)
    assert state(engine) == disabled


def test_stale_identity_and_exclusion_abort_the_entire_batch(engine):
    plan = plan_for(engine, 1, 2)
    with engine.begin() as connection:
        court = Table('court', MetaData(), autoload_with=connection)
        connection.execute(court.update().where(court.c.id == 2).values(name='Different venue'))
    before = state(engine)
    with pytest.raises(ValueError, match='Stale court identity'):
        execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    assert state(engine) == before
    plan = plan_for(engine, 1, 2)
    execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    with engine.begin() as connection:
        exclusion = Table('court_directory_exclusion', MetaData(), autoload_with=connection)
        connection.execute(exclusion.update().where(exclusion.c.court_id == 2).values(reason='Another reviewed decision'))
    before = state(engine)
    with pytest.raises(ValueError, match='Stale exclusion'):
        execute_plan(engine, plan, apply=True, rollback=True, before_write=lambda _: None)
    assert state(engine) == before


def test_failed_immutable_intent_prevents_all_writes(engine):
    plan = plan_for(engine, 1)
    with pytest.raises(ValueError, match='immutable intent'):
        execute_plan(engine, plan, apply=True)
    def failed(_):
        raise OSError('Audit storage unavailable')
    with pytest.raises(OSError):
        execute_plan(engine, plan, apply=True, before_write=failed)
    assert state(engine)['court_directory_exclusion'] == []


def test_concurrent_same_plan_is_idempotent(engine):
    plan = plan_for(engine, 1)
    def publish(_):
        return execute_plan(engine, plan, apply=True, before_write=lambda _: None)['records'][0]['status']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish, [1, 2])) == ['already_applied', 'ready']


@pytest.mark.parametrize('mutation', [
    lambda r:r['records'][0].update(reason_code='closed'),
    lambda r:r['records'][0].update(reason='Too short'),
    lambda r:r['records'][0]['foreign_location'].update(country_code='US'),
    lambda r:r['records'][0]['foreign_location'].update(country_code='PR'),
    lambda r:r['records'][0]['foreign_location'].update(address=''),
    lambda r:r['records'][0].update(location_source_urls=['https://unreviewed.test/']),
    lambda r:r['records'][0]['sources'][0].update(url='https://user:password@example.test/'),
    lambda r:r['records'][0]['sources'][0].update(url='https://example.test/#fragment'),
    lambda r:r['records'][0]['sources'][0].update(facts='Unsourced text'),
    lambda r:r['records'][0]['sources'][0].update(accessed_at='bad-date'),
    lambda r:r['records'][0]['sources'][0].update(authority='directory'),
    lambda r:r['records'][0].update(court_id=True),
])
def test_unsupported_exclusions_and_unreviewed_sources_are_rejected(mutation):
    value = research(1)
    mutation(value)
    with pytest.raises(ValueError):
        validate_research(value)


def test_missing_repeated_ids_and_mutated_provenance_are_rejected(engine):
    with pytest.raises(ValueError):
        plan_for(engine, 999)
    with pytest.raises(ValueError):
        plan_for(engine, 1, 1)
    plan = plan_for(engine, 1)
    plan['records'][0]['after']['source_urls'] = '[]'
    with pytest.raises(ValueError, match='provenance'):
        validate_plan(plan)


@pytest.mark.parametrize('reason', ['closed', 'noncourt', 'seasonal', 'program'])
def test_general_directory_exclusion_reasons_remain_unsupported(reason):
    value = research(1)
    value['records'][0]['reason_code'] = reason
    with pytest.raises(ValueError, match='Only foreign venues'):
        validate_research(value)


def test_operator_import_never_bootstraps_runtime_or_startup_ddl():
    result = subprocess.run([sys.executable, '-c',
        'import scripts.publish_court_directory_exclusions; import sys; '
        'assert "backend.app" not in sys.modules'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_explicit_source_publisher_fixture_admission_can_be_reversibly_published(engine):
    value = research(1)
    row = value['records'][0]
    row['reason_code'] = 'invalid_test_record'
    row['reason'] = 'Source publisher explicitly says this location is a nonexistent test fixture at a gas station.'
    row['sources'][0]['authority'] = 'source_publisher'
    row['sources'][0]['facts'] = ['Listing is a nonexistent test fixture at a gas station.']
    row.pop('foreign_location')
    row.pop('location_source_urls')
    with pytest.raises(ValueError, match='source-publisher admission'):
        validate_research(value)
    row['source_publisher_admission'] = {
        'kind': 'nonexistent_test_fixture',
        'text': 'Publisher explicitly identifies a nonexistent testing fixture, not a pickleball venue.',
        'source_urls': ['https://example.test/operator'],
    }
    validate_research(value)
    with transaction(engine) as connection:
        plan = build_plan(connection, value, 'Reviewed Operator')
    before = state(engine)['court']
    execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    assert state(engine)['court_directory_exclusion'][0]['reason_code'] == 'invalid_test_record'
    execute_plan(engine, plan, apply=True, rollback=True, before_write=lambda _: None)
    assert not state(engine)['court_directory_exclusion'][0]['active']
    assert state(engine)['court'] == before
    row['source_publisher_admission']['kind'] = 'seasonal_program'
    with pytest.raises(ValueError, match='source-publisher admission'):
        validate_research(value)
    row['source_publisher_admission']['kind'] = 'nonexistent_test_fixture'
    row['sources'][0]['authority'] = 'directory'
    with pytest.raises(ValueError, match='source-publisher admission'):
        validate_research(value)


def test_constraints_preserve_ids_and_reject_unsupported_codes(engine):
    plan = plan_for(engine, 1)
    execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    row = state(engine)['court_directory_exclusion'][0]
    for values in [dict(row), {**row, 'court_id': 999},
                   {**row, 'court_id': 2, 'reason_code': 'closed'}]:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            table = Table('court_directory_exclusion', MetaData(), autoload_with=connection)
            connection.execute(table.insert().values(**values))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        court = Table('court', MetaData(), autoload_with=connection)
        connection.execute(court.delete().where(court.c.id == 1))
    assert len(state(engine)['court']) == 2


def test_cli_defaults_to_dry_run_and_keeps_immutable_intent_and_readback(engine, tmp_path, monkeypatch):
    from scripts import publish_court_directory_exclusions as publisher
    monkeypatch.setattr(publisher, 'target_engine', lambda: engine)
    research_path = tmp_path / 'research.json'
    research_path.write_text(json.dumps(research(1)))
    plan_path = tmp_path / 'plan.json'
    assert publisher.main(['plan', '--research', str(research_path), '--reviewed-by',
                           'Reviewed Operator', '--output', str(plan_path)]) == 0
    dry_path = tmp_path / 'dry.json'
    assert publisher.main(['apply', '--plan', str(plan_path), '--report', str(dry_path)]) == 0
    assert state(engine)['court_directory_exclusion'] == []
    applied_path = tmp_path / 'applied.json'
    assert publisher.main(['apply', '--plan', str(plan_path), '--report', str(applied_path), '--apply']) == 0
    intent_path = Path(str(applied_path) + '.intent.json')
    intent_bytes = intent_path.read_bytes()
    intent = json.loads(intent_bytes)
    report = json.loads(applied_path.read_text())
    assert intent['requested_apply'] is True and intent['applied'] is False
    assert report['applied'] is True and intent['plan_sha256'] == report['plan_sha256']
    assert intent['records'][0]['after'] == report['records'][0]['after']
    before = state(engine)
    assert publisher.main(['apply', '--plan', str(plan_path), '--report', str(applied_path), '--apply']) == 1
    assert intent_path.read_bytes() == intent_bytes and state(engine) == before
    with pytest.raises(FileExistsError):
        write_json_exclusive(applied_path, {})
