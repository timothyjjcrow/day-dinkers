"""Operator activation guards, atomicity, concurrency and immutable evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, event, select
from sqlalchemy.exc import IntegrityError

from backend.app import db  # Initialize models in the normal application order.
from backend.models import Court, CourtAlias
from scripts.publish_court_aliases import (
    build_plan, digest, execute_plan, transaction, validate_plan,
    validate_research, write_json_exclusive,
)


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'aliases.db'),
                           connect_args={'check_same_thread': False, 'timeout': 10})
    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    metadata = MetaData()
    court = Court.__table__.to_metadata(metadata)
    CourtAlias.__table__.to_metadata(metadata)
    Table('unexpected_court_history', metadata,
          Column('id', Integer, primary_key=True), Column('venue_id', ForeignKey('court.id')))
    Table('conversation', metadata, Column('id', Integer, primary_key=True),
          Column('kind', String), Column('scope_id', Integer))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(court.insert(), [
            {'id': index, 'name': f'Court {index}', 'address': '420 SW 2nd Avenue',
             'city': 'Cape Coral', 'state': 'FL', 'latitude': 26.64,
             'longitude': -81.98, 'num_courts': 32}
            for index in range(1, 5)
        ])
    yield engine
    engine.dispose()


def research(*pairs):
    return {'records': [
        {'alias_court_id': source, 'canonical_court_id': target,
         'reason': 'Official city and operator identify the same venue.',
         'sources': [{'url': 'https://example.test/official', 'title': 'Official venue',
                      'accessed_at': '2026-10-03', 'facts': ['Same address and venue.']}]}
        for source, target in pairs
    ]}


def plan_for(engine, *pairs):
    with transaction(engine) as connection:
        return build_plan(connection, research(*pairs), 'Reviewed Operator')


def state(engine):
    with engine.connect() as connection:
        metadata = MetaData()
        aliases = Table('court_alias', metadata, autoload_with=connection)
        court = Table('court', metadata, autoload_with=connection)
        return ([dict(row) for row in connection.execute(select(aliases)).mappings()],
                [dict(row) for row in connection.execute(select(court)).mappings()])


def test_dry_run_preserves_every_court_and_alias_row(engine):
    before = state(engine)
    plan = plan_for(engine, (2, 1), (3, 1))
    report = execute_plan(engine, plan)
    assert not report['applied']
    assert all(row['status'] == 'ready' for row in report['records'])
    assert report['plan_sha256'] == digest(plan)
    assert report['records'][0]['dependencies']['2']['unexpected_court_history.venue_id'] == 0
    assert state(engine) == before


def test_apply_idempotency_rollback_and_original_rows(engine):
    court_before = state(engine)[1]
    plan = plan_for(engine, (2, 1), (3, 1))
    intents = []
    report = execute_plan(engine, plan, apply=True, before_write=lambda value: intents.append(deepcopy(value)))
    assert len(intents) == 1
    assert all(row['before'] is None for row in intents[0]['records'])
    assert all(row['after']['active'] for row in report['records'])
    first = state(engine)
    again = execute_plan(engine, plan, apply=True, before_write=intents.append)
    assert all(row['status'] == 'already_applied' for row in again['records'])
    assert state(engine) == first
    rollback = execute_plan(engine, plan, apply=True, rollback=True, before_write=intents.append)
    assert all(row['after']['active'] is False for row in rollback['records'])
    disabled = state(engine)
    execute_plan(engine, plan, apply=True, rollback=True, before_write=intents.append)
    assert state(engine) == disabled
    assert disabled[1] == court_before
    # An intentional fresh plan is needed to reactivate after rollback.
    with pytest.raises(ValueError, match='Stale mapping'):
        execute_plan(engine, plan, apply=True, before_write=intents.append)


@pytest.mark.parametrize('court_id', [1, 2])
def test_new_foreign_key_dependencies_block_whole_batch(engine, court_id):
    plan = plan_for(engine, (2, 1), (4, 3))
    with engine.begin() as connection:
        table = Table('unexpected_court_history', MetaData(), autoload_with=connection)
        connection.execute(table.insert().values(venue_id=court_id))
    before = state(engine)
    with pytest.raises(ValueError, match='dependencies'):
        execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    assert state(engine) == before


def test_polymorphic_court_conversation_dependency_is_also_detected(engine):
    plan = plan_for(engine, (2, 1))
    with engine.begin() as connection:
        table = Table('conversation', MetaData(), autoload_with=connection)
        connection.execute(table.insert().values(kind='court', scope_id=2))
    with pytest.raises(ValueError, match='dependencies'):
        execute_plan(engine, plan)
    assert state(engine)[0] == []


def test_changed_identity_and_failed_intent_audit_prevent_writes(engine):
    plan = plan_for(engine, (2, 1))
    with pytest.raises(ValueError, match='immutable intent'):
        execute_plan(engine, plan, apply=True)
    def failed_audit(_):
        raise OSError('Audit storage unavailable')
    with pytest.raises(OSError, match='Audit storage'):
        execute_plan(engine, plan, apply=True, before_write=failed_audit)
    assert state(engine)[0] == []
    with engine.begin() as connection:
        court = Table('court', MetaData(), autoload_with=connection)
        connection.execute(court.update().where(court.c.id == 1).values(name='Changed operator'))
    before = state(engine)
    with pytest.raises(ValueError, match='identity'):
        execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    assert state(engine) == before


@pytest.mark.parametrize('pairs', [((1, 1),), ((2, 1), (1, 3)), ((2, 1), (1, 2)), ((2, 1), (2, 3)), ((2, 999),)])
def test_self_links_chains_cycles_repeated_aliases_and_missing_ids_are_rejected(engine, pairs):
    with pytest.raises(ValueError):
        plan_for(engine, *pairs)
    assert state(engine)[0] == []


def test_existing_graph_and_mapping_reassignment_are_rejected(engine):
    plan = plan_for(engine, (2, 1))
    execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    with pytest.raises(ValueError, match='chains'):
        plan_for(engine, (1, 3))
    with pytest.raises(ValueError, match='Reassigning'):
        plan_for(engine, (2, 3))


def test_concurrent_opposing_alias_publications_cannot_form_a_cycle(engine):
    first, second = plan_for(engine, (2, 1)), plan_for(engine, (1, 2))
    def publish(plan):
        try:
            execute_plan(engine, plan, apply=True, before_write=lambda _: None)
            return 'published'
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, [first, second]))
    assert results.count('published') == 1
    assert any('chains' in result for result in results)
    assert len(state(engine)[0]) == 1


def test_concurrent_retry_of_same_plan_is_idempotent(engine):
    plan = plan_for(engine, (2, 1))
    def publish(_):
        return execute_plan(engine, plan, apply=True, before_write=lambda _: None)['records'][0]['status']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, [1, 2]))
    assert sorted(results) == ['already_applied', 'ready']
    assert len(state(engine)[0]) == 1


def test_database_constraints_restrict_self_links_missing_ids_and_duplicate_sources(engine):
    plan = plan_for(engine, (2, 1))
    execute_plan(engine, plan, apply=True, before_write=lambda _: None)
    row = state(engine)[0][0]
    for values in [dict(row), {**row, 'alias_court_id': 3, 'canonical_court_id': 3},
                   {**row, 'alias_court_id': 4, 'canonical_court_id': 999}]:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            table = Table('court_alias', MetaData(), autoload_with=connection)
            connection.execute(table.insert().values(**values))
    assert len(state(engine)[0]) == 1


def test_plan_provenance_is_bound_and_audit_files_are_exclusive(engine, tmp_path):
    plan = plan_for(engine, (2, 1))
    mutated = deepcopy(plan)
    mutated['records'][0]['after']['source_urls'] = '[]'
    with pytest.raises(ValueError, match='provenance'):
        validate_plan(mutated)
    bad_source = research((2, 1))
    bad_source['records'][0]['sources'][0]['url'] = 'https://user:password@example.test/'
    with pytest.raises(ValueError, match='credentials'):
        validate_research(bad_source)
    path = tmp_path / 'audit.json'
    write_json_exclusive(path, plan)
    with pytest.raises(FileExistsError):
        write_json_exclusive(path, {'changed': True})
    assert json.loads(path.read_text()) == plan


def test_operator_cli_requires_explicit_apply_and_saves_immutable_intent(engine, tmp_path, monkeypatch):
    from scripts import publish_court_aliases as publisher
    monkeypatch.setattr(publisher, 'target_engine', lambda: engine)
    research_path = tmp_path / 'research.json'
    research_path.write_text(json.dumps(research((2, 1))))
    plan_path = tmp_path / 'plan.json'
    assert publisher.main(['plan', '--research', str(research_path), '--reviewed-by',
                           'Reviewed Operator', '--output', str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text())
    dry_report = tmp_path / 'dry.json'
    assert publisher.main(['apply', '--plan', str(plan_path), '--report', str(dry_report)]) == 0
    assert state(engine)[0] == []
    assert not Path(str(dry_report) + '.intent.json').exists()
    applied_report = tmp_path / 'applied.json'
    assert publisher.main(['apply', '--plan', str(plan_path), '--report', str(applied_report), '--apply']) == 0
    saved = json.loads(applied_report.read_text())
    intent_path = Path(str(applied_report) + '.intent.json')
    intent_bytes = intent_path.read_bytes()
    intent = json.loads(intent_bytes)
    assert intent['kind'] == 'court-alias-publication-intent'
    assert intent['requested_apply'] is True and intent['applied'] is False
    assert intent['plan_sha256'] == saved['plan_sha256'] == digest(plan)
    assert saved['applied'] is True
    assert intent['records'][0]['before'] is None
    assert saved['records'][0]['after'] == intent['records'][0]['after']
    before = state(engine)
    assert publisher.main(['apply', '--plan', str(plan_path), '--report', str(applied_report), '--apply']) == 1
    assert intent_path.read_bytes() == intent_bytes
    assert state(engine) == before
