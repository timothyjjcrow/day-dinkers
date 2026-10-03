#!/usr/bin/env python3
"""Review and publish directory aliases without importing the Flask runtime.

Only explicit TARGET_DATABASE_URL is used. Planning and apply are read-only
unless --apply is supplied. No Court/history row is ever updated or deleted.
An exclusive, immutable intent artifact is flushed before the transaction's
first write; the report records actual before/after values and the plan hash.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url


PG_SCHEMA = 'picklepals'
ALIAS_LOCK = 75490123
IDENTITY_FIELDS = (
    'id', 'name', 'address', 'city', 'state', 'county_slug', 'latitude',
    'longitude', 'num_courts', 'closed', 'pending_submission',
)
ALIAS_FIELDS = (
    'alias_court_id', 'canonical_court_id', 'active', 'reason', 'source_urls',
    'reviewed_by', 'created_at', 'updated_at',
)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def now():
    # Match TimestampMixin's naive UTC storage, including PostgreSQL reflection.
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def snapshot(row):
    if row is None:
        return None
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in dict(row).items()}


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Court IDs must be positive integers')
    return value


def validate_research(research):
    records = research.get('records', []) if isinstance(research, dict) else []
    if not 1 <= len(records) <= 100:
        raise ValueError('Research must contain between 1 and 100 mappings')
    aliases = set()
    for item in records:
        alias = _positive_id(item['alias_court_id'])
        target = _positive_id(item['canonical_court_id'])
        if alias == target or alias in aliases:
            raise ValueError('Self links and repeated aliases are forbidden')
        aliases.add(alias)
        if not isinstance(item.get('reason'), str) or len(item['reason'].strip()) < 20:
            raise ValueError('Every mapping needs a concrete reviewed reason')
        sources = item.get('sources')
        if not isinstance(sources, list) or not sources:
            raise ValueError('Every mapping needs source evidence')
        for source in sources:
            parts = urlsplit(source.get('url', ''))
            if (parts.scheme not in {'http', 'https'} or not parts.hostname
                    or parts.username or parts.password or parts.fragment):
                raise ValueError('Sources must use public HTTP(S) URLs without credentials')
            facts = source.get('facts')
            if (not isinstance(source.get('title'), str) or not source['title'].strip()
                    or not isinstance(facts, list) or not facts
                    or any(not isinstance(fact, str) or not fact.strip() for fact in facts)):
                raise ValueError('Every source needs a title and supporting facts')
            try:
                datetime.fromisoformat(source['accessed_at'].replace('Z', '+00:00'))
            except (KeyError, TypeError, ValueError):
                raise ValueError('Every source needs an ISO access date') from None
    return records


def _tables(connection):
    schema = PG_SCHEMA if connection.dialect.name == 'postgresql' else None
    metadata = MetaData(schema=schema)
    tables = set(inspect(connection).get_table_names(schema=schema))
    if not {'court', 'court_alias'}.issubset(tables):
        raise ValueError('Existing Court and migrated CourtAlias tables are required; no DDL is performed')
    return (schema, metadata,
            Table('court', metadata, autoload_with=connection),
            Table('court_alias', metadata, autoload_with=connection))


def dependency_counts(connection, court_ids, schema=None):
    """Discover actual database FKs rather than a potentially stale model list."""
    inspector = inspect(connection)
    counts = {str(court_id): {} for court_id in court_ids}
    metadata = MetaData(schema=schema)
    for name in inspector.get_table_names(schema=schema):
        if name == 'court_alias':  # Alias configuration is not player history.
            continue
        for fk in inspector.get_foreign_keys(name, schema=schema):
            if fk['referred_table'] != 'court' or fk.get('referred_schema') not in {None, schema}:
                continue
            if fk['referred_columns'] != ['id'] or len(fk['constrained_columns']) != 1:
                raise ValueError('Unexpected Court foreign key shape; manual review required')
            column = fk['constrained_columns'][0]
            table = Table(name, metadata, autoload_with=connection, extend_existing=True)
            totals = dict(connection.execute(select(table.c[column], func.count())
                .where(table.c[column].in_(court_ids)).group_by(table.c[column])).all())
            for court_id in court_ids:
                counts[str(court_id)][f'{name}.{column}'] = totals.get(court_id, 0)
    # Conversation scope is deliberately polymorphic and has no Court FK.
    if 'conversation' in inspector.get_table_names(schema=schema):
        table = Table('conversation', metadata, autoload_with=connection, extend_existing=True)
        if {'kind', 'scope_id'}.issubset(table.c.keys()):
            totals = dict(connection.execute(select(table.c.scope_id, func.count())
                .where(table.c.kind == 'court', table.c.scope_id.in_(court_ids))
                .group_by(table.c.scope_id)).all())
            for court_id in court_ids:
                counts[str(court_id)]['conversation.court_scope_id'] = totals.get(court_id, 0)
    return counts


def _identities(connection, court, ids, lock=False):
    query = select(*(court.c[name] for name in IDENTITY_FIELDS)).where(court.c.id.in_(ids)).order_by(court.c.id)
    if lock and connection.dialect.name == 'postgresql':
        query = query.with_for_update()
    rows = {str(row['id']): snapshot(row) for row in connection.execute(query).mappings()}
    if len(rows) != len(ids):
        raise ValueError('Every source and canonical court must exist')
    return rows


def _validate_graph(mappings):
    active = {row['alias_court_id']: row['canonical_court_id']
              for row in mappings.values() if row['active']}
    if any(alias == target for alias, target in active.items()):
        raise ValueError('Self links are forbidden')
    if set(active) & set(active.values()):
        raise ValueError('Alias chains and cycles are forbidden')


def build_plan(connection, research, reviewed_by):
    records = validate_research(research)
    if not isinstance(reviewed_by, str) or not 3 <= len(reviewed_by.strip()) <= 120:
        raise ValueError('A named reviewer is required')
    schema, _, court, aliases = _tables(connection)
    ids = sorted({item[key] for item in records for key in ('alias_court_id', 'canonical_court_id')})
    identities = _identities(connection, court, ids)
    mappings = {row['alias_court_id']: snapshot(row)
                for row in connection.execute(select(aliases)).mappings()}
    dependencies = dependency_counts(connection, ids, schema)
    timestamp = now()
    planned = []
    for item in records:
        alias, target = item['alias_court_id'], item['canonical_court_id']
        before = mappings.get(alias)
        if before and before['canonical_court_id'] != target:
            raise ValueError('Reassigning an existing alias requires a separate resolution')
        after = {
            'alias_court_id': alias, 'canonical_court_id': target, 'active': True,
            'reason': item['reason'].strip(),
            'source_urls': canonical_json([source['url'] for source in item['sources']]),
            'reviewed_by': reviewed_by.strip(),
            'created_at': before['created_at'] if before else timestamp,
            'updated_at': timestamp,
        }
        if before and all(before[key] == after[key] for key in ALIAS_FIELDS if key != 'updated_at'):
            after = before.copy()
        mappings[alias] = after
        pair = [str(alias), str(target)]
        planned.append({
            'alias_court_id': alias, 'canonical_court_id': target,
            'identity_before': {key: identities[key] for key in pair},
            'dependencies_before': {key: dependencies[key] for key in pair},
            'before': before, 'after': after, 'sources': item['sources'],
        })
    _validate_graph(mappings)
    plan = {'version': 1, 'kind': 'court-alias-plan', 'created_at': timestamp,
            'reviewed_by': reviewed_by.strip(), 'research_sha256': digest(research), 'records': planned}
    validate_plan(plan)
    return plan


def validate_plan(plan):
    if plan.get('version') != 1 or plan.get('kind') != 'court-alias-plan':
        raise ValueError('Unsupported alias plan')
    reviewer = plan.get('reviewed_by')
    if not isinstance(reviewer, str) or not 3 <= len(reviewer.strip()) <= 120:
        raise ValueError('A named reviewer is required')
    research_hash = plan.get('research_sha256')
    if (not isinstance(research_hash, str) or len(research_hash) != 64
            or any(char not in '0123456789abcdef' for char in research_hash)):
        raise ValueError('Research content hash is required')
    records = validate_research({'records': [
        {**row, 'reason': row['after']['reason']} for row in plan['records']
    ]})
    for row in records:
        after = row['after']
        if set(after) != set(ALIAS_FIELDS) or type(after['active']) is not bool or not after['active']:
            raise ValueError('Plan must explicitly activate exactly the supported alias fields')
        if (after['alias_court_id'] != row['alias_court_id']
                or after['canonical_court_id'] != row['canonical_court_id']
                or after['reviewed_by'] != plan['reviewed_by']
                or after['source_urls'] != canonical_json([source['url'] for source in row['sources']])):
            raise ValueError('Alias values must match reviewed provenance')
        for key in ('created_at', 'updated_at'):
            value = datetime.fromisoformat(after[key])
            if value.tzinfo is not None:
                raise ValueError('Alias timestamps must be naive UTC')
        before = row['before']
        if before and (set(before) != set(ALIAS_FIELDS)
                       or before['alias_court_id'] != row['alias_court_id']
                       or before['canonical_court_id'] != row['canonical_court_id']):
            raise ValueError('Existing alias reassignment is forbidden')
        if set(row['identity_before']) != {str(row['alias_court_id']), str(row['canonical_court_id'])}:
            raise ValueError('Both source and canonical identity guards are required')
        for key, identity in row['identity_before'].items():
            if set(identity) != set(IDENTITY_FIELDS) or str(identity['id']) != key:
                raise ValueError('All supported court identity guards are required')
        target = row['identity_before'][str(row['canonical_court_id'])]
        if target['closed'] or target['pending_submission'] or target['latitude'] is None or target['longitude'] is None:
            raise ValueError('Canonical court must be open, approved and located')
    return records


@contextmanager
def transaction(engine, writable=False):
    with engine.connect() as connection:
        if connection.dialect.name == 'sqlite' and writable:
            # SQLite tests serialize writers just as the production advisory lock does.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            if connection.dialect.name == 'postgresql':
                if not writable:
                    connection.execute(text('SET TRANSACTION READ ONLY'))
                connection.execute(text('SET LOCAL search_path TO picklepals, public'))
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                if writable:
                    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': ALIAS_LOCK})
            yield connection
            connection.commit() if writable else connection.rollback()
        except BaseException:
            connection.rollback()
            raise


def execute_plan(engine, plan, *, apply=False, rollback=False, before_write=None):
    records = validate_plan(plan)
    if apply and before_write is None:
        raise ValueError('An immutable intent audit is required before writing')
    with transaction(engine, writable=apply) as connection:
        schema, _, court, aliases = _tables(connection)
        ids = sorted({row[key] for row in records for key in ('alias_court_id', 'canonical_court_id')})
        identities = _identities(connection, court, ids, lock=apply)
        query = select(aliases).order_by(aliases.c.alias_court_id)
        if apply and connection.dialect.name == 'postgresql':
            query = query.with_for_update()
        mappings = {row['alias_court_id']: snapshot(row) for row in connection.execute(query).mappings()}
        dependencies = dependency_counts(connection, ids, schema)
        proposed, changes = mappings.copy(), []
        for row in records:
            alias = row['alias_court_id']
            current = mappings.get(alias)
            source = row['after'] if rollback else row['before']
            # Rollback preserves the additive alias row, disabled, as operator history.
            target = (row['before'] or {**row['after'], 'active': False}) if rollback else row['after']
            if current == target:
                status = 'already_applied'
            else:
                if current != source:
                    raise ValueError(f'Stale mapping guard for court {alias}')
                if not rollback and any(identities[key] != value for key, value in row['identity_before'].items()):
                    raise ValueError(f'Stale court identity guard for court {alias}')
                if target['active'] and (not current or not current['active']):
                    if any(count for key in row['identity_before'] for count in dependencies[key].values()):
                        raise ValueError(f'Player or business dependencies require an aggregation review for court {alias}')
                status = 'ready'
            proposed[alias] = target
            changes.append({'alias_court_id': alias, 'before': current, 'after': target,
                            'status': status, 'identities': {key: identities[key] for key in row['identity_before']},
                            'dependencies': {key: dependencies[key] for key in row['identity_before']}})
        _validate_graph(proposed)
        report = {'version': 1, 'kind': 'court-alias-publication', 'plan_sha256': digest(plan),
                  'checked_at': now(), 'mode': 'rollback' if rollback else 'activate',
                  'applied': apply, 'records': changes}
        if apply:
            before_write(report)
            for change in changes:
                if change['status'] != 'ready':
                    continue
                values = {key: datetime.fromisoformat(value) if key in {'created_at', 'updated_at'} else value
                          for key, value in change['after'].items()}
                if change['before'] is None:
                    connection.execute(aliases.insert().values(**values))
                else:
                    connection.execute(aliases.update().where(aliases.c.alias_court_id == change['alias_court_id']).values(**values))
            for change in changes:
                actual = snapshot(connection.execute(select(aliases).where(
                    aliases.c.alias_court_id == change['alias_court_id'])).mappings().first())
                if actual != change['after']:
                    raise ValueError('Alias write verification failed')
                change['after'] = actual
            if identities != _identities(connection, court, ids):
                raise ValueError('Court rows changed unexpectedly')
    report['completed_at'] = now()
    return report


def write_json_exclusive(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def target_engine():
    # This module does not load dotenv, import backend.app or run startup DDL.
    target = os.environ.get('TARGET_DATABASE_URL', '').strip()
    if target.startswith('postgres://'):
        target = 'postgresql+psycopg://' + target[len('postgres://'):]
    elif target.startswith('postgresql://'):
        target = 'postgresql+psycopg://' + target[len('postgresql://'):]
    url = make_url(target)
    if url.drivername != 'postgresql+psycopg' or '-pooler.' in (url.host or ''):
        raise ValueError('TARGET_DATABASE_URL must explicitly use PostgreSQL with a direct/unpooled endpoint')
    return create_engine(url, hide_parameters=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    planning = commands.add_parser('plan')
    planning.add_argument('--research', required=True, type=Path)
    planning.add_argument('--reviewed-by', required=True)
    planning.add_argument('--output', required=True, type=Path)
    publishing = commands.add_parser('apply')
    publishing.add_argument('--plan', required=True, type=Path)
    publishing.add_argument('--report', required=True, type=Path)
    publishing.add_argument('--apply', action='store_true', help='Commit writes; otherwise dry-run')
    publishing.add_argument('--rollback', action='store_true', help='Disable new aliases or restore prior mappings')
    args = parser.parse_args(argv)
    try:
        output = args.output if args.command == 'plan' else args.report
        intent = Path(str(output) + '.intent.json')
        if output.exists() or (args.command == 'apply' and args.apply and intent.exists()):
            raise ValueError('Audit output already exists; choose a new path')
        engine = target_engine()
        if args.command == 'plan':
            research = json.loads(args.research.read_text())
            with transaction(engine) as connection:
                result = build_plan(connection, research, args.reviewed_by)
        else:
            plan = json.loads(args.plan.read_text())
            result = execute_plan(engine, plan, apply=args.apply, rollback=args.rollback,
                                  before_write=lambda value: write_json_exclusive(intent, {
                                      **value, 'kind': 'court-alias-publication-intent',
                                      'applied': False, 'requested_apply': True,
                                  }))
        write_json_exclusive(output, result)
        print(f"Saved {args.command} audit to {output}; {len(result['records'])} mappings")
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'Alias operation rejected: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        # Driver exceptions can contain the URL or SQL values; don't print them.
        print(f'Alias operation failed ({type(error).__name__}); no uncommitted changes retained', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
