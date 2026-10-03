#!/usr/bin/env python3
"""Widen only the existing PostgreSQL directory-exclusion reason check.

Dry-run by default. Use an explicit direct TARGET_DATABASE_URL and --apply
only after reviewing the immutable dry report. This operator helper is never
imported by app startup or run by a Vercel build. It changes no Court/history
rows, table columns, keys, or existing exclusion values.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from sqlalchemy import text

from scripts.publish_court_directory_exclusions import (
    EXCLUSION_LOCK, target_engine, write_json_exclusive,
)


SCHEMA = 'picklepals'
TABLE = 'court_directory_exclusion'
CONSTRAINT = 'ck_court_directory_exclusion_reason'
OLD_REASONS = frozenset({'foreign_venue', 'invalid_test_record'})
NEW_REASONS = OLD_REASONS | {'pickleball_prohibited'}
DDL = (
    f'ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT {CONSTRAINT}',
    f'ALTER TABLE {SCHEMA}.{TABLE} ADD CONSTRAINT {CONSTRAINT} '
    "CHECK (reason_code IN ('foreign_venue', 'invalid_test_record', 'pickleball_prohibited'))",
)
CONSTRAINT_SQL = '''SELECT pg_get_constraintdef(c.oid) AS definition,
    c.convalidated AS validated
    FROM pg_constraint c
    JOIN pg_class t ON t.oid=c.conrelid
    JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE n.nspname=:schema AND t.relname=:table AND c.conname=:constraint
      AND c.contype='c' '''
ROWS_SQL = f'SELECT * FROM {SCHEMA}.{TABLE} ORDER BY court_id'


def reason_codes(definition):
    """Recognize only simple IN/ANY reason checks, including PostgreSQL casts."""
    if not isinstance(definition, str):
        raise ValueError('A recognized existing reason check is required')
    normalized = re.sub(r'::(?:character varying|varchar|text)(?:\[\])?', '', definition,
                        flags=re.IGNORECASE)
    normalized = re.sub(r'[\s()"]', '', normalized).lower()
    if normalized.startswith('check'):
        normalized = normalized[5:]
    literals = r"('[a-z_]+'(?:,'[a-z_]+')*)"
    match = re.fullmatch(r'reason_codein' + literals, normalized) or re.fullmatch(
        r'reason_code=anyarray\[' + literals + r'\]', normalized)
    if not match:
        raise ValueError('Existing reason check has an unsupported shape; manual review required')
    codes = re.findall(r"'([a-z_]+)'", match.group(1))
    if len(codes) != len(set(codes)) or set(codes) not in {OLD_REASONS, NEW_REASONS}:
        raise ValueError('Existing reason check must allow exactly the approved old or new reasons')
    return frozenset(codes)


def _state(connection):
    constraint = connection.execute(text(CONSTRAINT_SQL), {
        'schema': SCHEMA, 'table': TABLE, 'constraint': CONSTRAINT,
    }).mappings().one_or_none()
    if not constraint or constraint['validated'] is not True:
        raise ValueError('The existing validated named reason check is required; no table is created')
    codes = reason_codes(constraint['definition'])
    # Hash the small exclusion table in memory; never save reviewer/source rows.
    rows = [dict(row) for row in connection.execute(text(ROWS_SQL)).mappings()]
    if any(row['reason_code'] not in codes for row in rows):
        raise ValueError('Existing exclusion values do not satisfy the named check')
    encoded = json.dumps(rows, sort_keys=True, separators=(',', ':'),
                         default=lambda value: value.isoformat()).encode()
    return {'constraint': CONSTRAINT, 'definition': constraint['definition'],
            'validated': True, 'allowed_reasons': sorted(codes), 'row_count': len(rows),
            'row_sha256': hashlib.sha256(encoded).hexdigest(),
            'reason_counts': {code: sum(row['reason_code'] == code for row in rows)
                              for code in sorted(codes)}}


def migrate(engine, *, apply=False, before_write=None):
    if engine.dialect.name != 'postgresql':
        raise ValueError('This operator migration supports PostgreSQL only')
    if apply and before_write is None:
        raise ValueError('An immutable intent report is required before DDL')
    with engine.connect() as connection:
        connection.begin()
        try:
            if not apply:
                connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            connection.execute(text("SET LOCAL statement_timeout='30s'"))
            if apply:
                connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': EXCLUSION_LOCK})
            before = _state(connection)
            needed = set(before['allowed_reasons']) == OLD_REASONS
            report = {'version': 1, 'kind': 'court-directory-exclusion-policy-migration',
                      'started_at': datetime.now(timezone.utc).isoformat(),
                      'schema': SCHEMA, 'table': TABLE, 'constraint': CONSTRAINT,
                      'requested_apply': apply, 'applied': False,
                      'status': 'ready' if needed else 'already_current',
                      'operations': list(DDL) if needed else [], 'before': before,
                      'after': None, 'court_or_history_rows_changed': False}
            if apply:
                before_write(deepcopy(report))
                if needed:
                    for statement in DDL:
                        connection.execute(text(statement))
                after = _state(connection)
                if (set(after['allowed_reasons']) != NEW_REASONS
                        or before['row_count'] != after['row_count']
                        or before['row_sha256'] != after['row_sha256']):
                    raise ValueError('Migration readback failed; transaction must roll back')
                report['after'] = after
                connection.commit()
                report['applied'] = True
                report['status'] = 'migrated' if needed else 'already_current'
            else:
                connection.rollback()
        except BaseException:
            connection.rollback()
            raise
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    intent = Path(str(args.output) + '.intent.json')
    engine = None
    try:
        if args.output.exists() or (args.apply and intent.exists()):
            raise ValueError('Audit output already exists; choose a new path')
        engine = target_engine()
        report = migrate(engine, apply=args.apply,
                         before_write=lambda value: write_json_exclusive(intent, value))
        write_json_exclusive(args.output, report)
        print(f'Saved {report["status"]} migration audit to {args.output}')
        return 0
    except (ValueError, OSError) as error:
        print(f'Migration rejected: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'Migration failed ({type(error).__name__}); uncommitted DDL rolled back', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
