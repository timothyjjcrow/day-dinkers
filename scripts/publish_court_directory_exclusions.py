#!/usr/bin/env python3
"""Publish reversible, reviewed foreign venues and admitted nonexistent test fixtures.

Only explicit direct TARGET_DATABASE_URL is used; no runtime import or DDL.
Planning and execution are read-only unless --apply is supplied. Original Court
and history rows never change. An immutable intent is flushed before writes.
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
EXCLUSION_LOCK = 75490124  # Independent of the alias publisher's advisory lock.
IDENTITY_FIELDS = (
    'id', 'name', 'address', 'city', 'state', 'zip_code', 'county_slug',
    'latitude', 'longitude', 'phone', 'website', 'indoor', 'court_type',
    'num_courts', 'closed', 'pending_submission',
)
EXCLUSION_FIELDS = (
    'court_id', 'active', 'reason_code', 'reason', 'source_urls', 'reviewed_by',
    'reviewed_at', 'created_at', 'updated_at',
)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def snapshot(row):
    return None if row is None else {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in dict(row).items()
    }


def validate_research(research):
    records = research.get('records', []) if isinstance(research, dict) else []
    if not isinstance(records, list) or not 1 <= len(records) <= 100:
        raise ValueError('Research must contain between 1 and 100 reviewed records')
    seen = set()
    for item in records:
        court_id = item.get('court_id')
        if type(court_id) is not int or court_id <= 0 or court_id in seen:
            raise ValueError('Unique positive integer Court IDs are required')
        seen.add(court_id)
        if item.get('reason_code') not in {'foreign_venue', 'invalid_test_record'}:
            raise ValueError('Only foreign venues and admitted nonexistent test fixtures are supported')
        if not isinstance(item.get('reason'), str) or len(item['reason'].strip()) < 20:
            raise ValueError('A concrete reviewed exclusion reason is required')
        sources = item.get('sources')
        if not isinstance(sources, list) or not sources:
            raise ValueError('Every venue requires public source evidence')
        urls = set()
        primary_urls = set()
        for source in sources:
            parts = urlsplit(source.get('url', ''))
            if (parts.scheme not in {'http', 'https'} or not parts.hostname
                    or parts.username or parts.password or parts.fragment):
                raise ValueError('Sources need public HTTP(S) URLs without credentials or fragments')
            facts = source.get('facts')
            if (not isinstance(source.get('title'), str) or not source['title'].strip()
                    or not isinstance(facts, list) or not facts
                    or any(not isinstance(fact, str) or not fact.strip() for fact in facts)):
                raise ValueError('Sources require titles and reviewed facts lists')
            try:
                datetime.fromisoformat(source['accessed_at'].replace('Z', '+00:00'))
            except (KeyError, TypeError, ValueError):
                raise ValueError('Sources require ISO access dates') from None
            urls.add(source['url'])
            if source.get('authority') in {'operator', 'government', 'sports_governing_body'}:
                primary_urls.add(source['url'])
        if item['reason_code'] == 'invalid_test_record':
            admission = item.get('source_publisher_admission')
            publisher_urls = {s['url'] for s in sources if s.get('authority') == 'source_publisher'}
            if (not isinstance(admission, dict) or set(admission) != {'kind', 'text', 'source_urls'}
                    or admission['kind'] != 'nonexistent_test_fixture'
                    or not isinstance(admission['text'], str) or len(admission['text'].strip()) < 20
                    or not isinstance(admission['source_urls'], list) or not admission['source_urls']
                    or any(not isinstance(url, str) or url not in publisher_urls for url in admission['source_urls'])):
                raise ValueError('Test exclusions require an explicit source-publisher admission of a nonexistent test fixture')
            continue
        location = item.get('foreign_location')
        if not isinstance(location, dict) or set(location) != {'country_code', 'country', 'locality', 'address'}:
            raise ValueError('Explicit foreign country, locality and street address are required')
        if any(not isinstance(v, str) or not v.strip() for v in location.values()):
            raise ValueError('Foreign location fields must be nonempty strings')
        code = location['country_code']
        if (len(code) != 2 or not code.isascii() or not code.isalpha() or code != code.upper()
                or code in {'US', 'AS', 'GU', 'MP', 'PR', 'UM', 'VI'}):
            raise ValueError('US locations and territories cannot use foreign-venue exclusion')
        citations = item.get('location_source_urls')
        if (not isinstance(citations, list) or not citations
                or any(not isinstance(url, str) or url not in urls for url in citations)
                or not primary_urls.intersection(citations)):
            raise ValueError('Foreign location needs explicit reviewed primary-source citations')
    return records


def _tables(connection):
    schema = PG_SCHEMA if connection.dialect.name == 'postgresql' else None
    metadata = MetaData(schema=schema)
    if not {'court', 'court_directory_exclusion'} <= set(inspect(connection).get_table_names(schema=schema)):
        raise ValueError('Existing Court and migrated CourtDirectoryExclusion tables are required; no DDL is performed')
    return schema, metadata, Table('court', metadata, autoload_with=connection), Table(
        'court_directory_exclusion', metadata, autoload_with=connection)


def _identities(connection, court, ids, lock=False):
    query = select(*(court.c[field] for field in IDENTITY_FIELDS)).where(court.c.id.in_(ids)).order_by(court.c.id)
    if lock and connection.dialect.name == 'postgresql':
        query = query.with_for_update()
    rows = {row['id']: snapshot(row) for row in connection.execute(query).mappings()}
    if len(rows) != len(ids):
        raise ValueError('Every reviewed court must exist')
    return rows


def dependency_counts(connection, court_ids, schema=None):
    """Audit actual history references; exclusions neither move nor erase them."""
    inspector = inspect(connection)
    counts = {court_id: {} for court_id in court_ids}
    metadata = MetaData(schema=schema)
    names = inspector.get_table_names(schema=schema)
    for name in names:
        if name in {'court_alias', 'court_directory_exclusion'}:
            continue
        for fk in inspector.get_foreign_keys(name, schema=schema):
            if fk['referred_table'] != 'court' or fk.get('referred_schema') not in {None, schema}:
                continue
            if fk['referred_columns'] != ['id'] or len(fk['constrained_columns']) != 1:
                raise ValueError('Unexpected Court foreign key shape requires manual audit')
            column = fk['constrained_columns'][0]
            table = Table(name, metadata, autoload_with=connection, extend_existing=True)
            totals = dict(connection.execute(select(table.c[column], func.count()).where(
                table.c[column].in_(court_ids)).group_by(table.c[column])).all())
            for court_id in court_ids:
                counts[court_id][f'{name}.{column}'] = totals.get(court_id, 0)
    if 'conversation' in names:
        table = Table('conversation', metadata, autoload_with=connection, extend_existing=True)
        if {'kind', 'scope_id'} <= set(table.c.keys()):
            totals = dict(connection.execute(select(table.c.scope_id, func.count()).where(
                table.c.kind == 'court', table.c.scope_id.in_(court_ids)).group_by(table.c.scope_id)).all())
            for court_id in court_ids:
                counts[court_id]['conversation.court_scope_id'] = totals.get(court_id, 0)
    return counts


def build_plan(connection, research, reviewed_by):
    records = validate_research(research)
    if not isinstance(reviewed_by, str) or not 3 <= len(reviewed_by.strip()) <= 120:
        raise ValueError('A named reviewer is required')
    schema, _, court, exclusions = _tables(connection)
    ids = sorted(item['court_id'] for item in records)
    identities = _identities(connection, court, ids)
    existing = {row['court_id']: snapshot(row) for row in connection.execute(
        select(exclusions).where(exclusions.c.court_id.in_(ids))).mappings()}
    dependencies = dependency_counts(connection, ids, schema)
    timestamp = now()
    planned = []
    for item in records:
        court_id = item['court_id']
        before = existing.get(court_id)
        after = {'court_id': court_id, 'active': True, 'reason_code': item['reason_code'],
                 'reason': item['reason'].strip(),
                 'source_urls': canonical_json([source['url'] for source in item['sources']]),
                 'reviewed_by': reviewed_by.strip(), 'reviewed_at': timestamp,
                 'created_at': before['created_at'] if before else timestamp, 'updated_at': timestamp}
        planned.append({**item, 'identity_before': identities[court_id],
                        'dependencies_before': dependencies[court_id], 'before': before, 'after': after})
    plan = {'version': 1, 'kind': 'court-directory-exclusion-plan', 'created_at': timestamp,
            'reviewed_by': reviewed_by.strip(), 'research_sha256': digest(research), 'records': planned}
    validate_plan(plan)
    return plan


def validate_plan(plan):
    if plan.get('version') != 1 or plan.get('kind') != 'court-directory-exclusion-plan':
        raise ValueError('Unsupported exclusion plan')
    reviewer = plan.get('reviewed_by')
    if not isinstance(reviewer, str) or not 3 <= len(reviewer.strip()) <= 120:
        raise ValueError('A named reviewer is required')
    research_hash = plan.get('research_sha256')
    if (not isinstance(research_hash, str) or len(research_hash) != 64
            or any(char not in '0123456789abcdef' for char in research_hash)):
        raise ValueError('Research content hash is required')
    records = validate_research(plan)
    for row in records:
        after, before = row['after'], row['before']
        if (set(after) != set(EXCLUSION_FIELDS) or type(after['active']) is not bool
                or not after['active'] or after['court_id'] != row['court_id']
                or after['reason_code'] != row['reason_code'] or after['reason'] != row['reason'].strip()
                or after['reviewed_by'] != reviewer
                or after['source_urls'] != canonical_json([s['url'] for s in row['sources']])):
            raise ValueError('Exclusion values must match reviewed provenance exactly')
        for field in ('created_at', 'updated_at', 'reviewed_at'):
            if datetime.fromisoformat(after[field]).tzinfo is not None:
                raise ValueError('Exclusion timestamps must be naive UTC')
        if before and (set(before) != set(EXCLUSION_FIELDS) or before['court_id'] != row['court_id']):
            raise ValueError('Existing exclusion guard must be complete')
        identity = row['identity_before']
        if set(identity) != set(IDENTITY_FIELDS) or identity['id'] != row['court_id']:
            raise ValueError('Exact original court identity guard is required')
        if not isinstance(row.get('dependencies_before'), dict):
            raise ValueError('Original dependency audit is required')
    return records


@contextmanager
def transaction(engine, writable=False):
    with engine.connect() as connection:
        if connection.dialect.name == 'sqlite' and writable:
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
                    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': EXCLUSION_LOCK})
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
        schema, _, court, exclusions = _tables(connection)
        ids = sorted(row['court_id'] for row in records)
        identities = _identities(connection, court, ids, lock=apply)
        query = select(exclusions).where(exclusions.c.court_id.in_(ids)).order_by(exclusions.c.court_id)
        if apply and connection.dialect.name == 'postgresql':
            query = query.with_for_update()
        existing = {row['court_id']: snapshot(row) for row in connection.execute(query).mappings()}
        dependencies = dependency_counts(connection, ids, schema)
        changes = []
        for row in records:
            court_id = row['court_id']
            current = existing.get(court_id)
            source = row['after'] if rollback else row['before']
            # Keep a disabled additive row as audit history when no row existed.
            target = (row['before'] or {**row['after'], 'active': False}) if rollback else row['after']
            if not rollback and identities[court_id] != row['identity_before']:
                raise ValueError(f'Stale court identity guard for court {court_id}')
            if current == target:
                status = 'already_applied'
            elif current != source:
                raise ValueError(f'Stale exclusion guard for court {court_id}')
            else:
                status = 'ready'
            changes.append({'court_id': court_id, 'before': current, 'after': target,
                            'status': status, 'identity': identities[court_id],
                            'dependencies': dependencies[court_id]})
        report = {'version': 1, 'kind': 'court-directory-exclusion-publication',
                  'plan_sha256': digest(plan), 'checked_at': now(),
                  'mode': 'rollback' if rollback else 'activate', 'applied': apply, 'records': changes}
        if apply:
            before_write(report)
            for change in changes:
                if change['status'] != 'ready':
                    continue
                values = {key: datetime.fromisoformat(value)
                          if key in {'created_at', 'updated_at', 'reviewed_at'} else value
                          for key, value in change['after'].items()}
                if change['before'] is None:
                    connection.execute(exclusions.insert().values(**values))
                else:
                    connection.execute(exclusions.update().where(
                        exclusions.c.court_id == change['court_id']).values(**values))
            for change in changes:
                actual = snapshot(connection.execute(select(exclusions).where(
                    exclusions.c.court_id == change['court_id'])).mappings().first())
                if actual != change['after']:
                    raise ValueError('Exclusion write verification failed')
                change['after'] = actual
            if identities != _identities(connection, court, ids):
                raise ValueError('Original court identities changed unexpectedly')
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
    target = os.environ.get('TARGET_DATABASE_URL', '').strip()
    if target.startswith('postgres://'):
        target = 'postgresql+psycopg://' + target[len('postgres://'):]
    elif target.startswith('postgresql://'):
        target = 'postgresql+psycopg://' + target[len('postgresql://'):]
    url = make_url(target)
    if url.drivername != 'postgresql+psycopg' or '-pooler.' in (url.host or ''):
        raise ValueError('TARGET_DATABASE_URL must explicitly use a direct PostgreSQL endpoint')
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
    publishing.add_argument('--apply', action='store_true')
    publishing.add_argument('--rollback', action='store_true')
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
                    **value, 'kind': 'court-directory-exclusion-publication-intent',
                    'applied': False, 'requested_apply': True,
                }))
        write_json_exclusive(output, result)
        print(f"Saved {args.command} audit to {output}; {len(result['records'])} exclusions")
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'Exclusion operation rejected: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'Exclusion operation failed ({type(error).__name__}); no uncommitted changes retained', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
