#!/usr/bin/env python3
"""Trusted operator inbox for durable business integration requests."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _target_database_url():
    raw = os.getenv('TARGET_DATABASE_URL', '').strip()
    if raw.startswith('postgres://'):
        raw = 'postgresql+psycopg://' + raw[len('postgres://'):]
    elif raw.startswith('postgresql://'):
        raw = 'postgresql+psycopg://' + raw[len('postgresql://'):]
    if not raw.startswith('postgresql+psycopg://'):
        raise RuntimeError(
            'TARGET_DATABASE_URL must be a PostgreSQL connection string.'
        )
    if '-pooler.' in (make_url(raw).host or '').lower():
        raise RuntimeError(
            'TARGET_DATABASE_URL must use the direct/unpooled database host.'
        )
    return raw


def _configure_environment(target):
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
        'SECRET_KEY': 'business-request-process-secret-not-used-for-serving',
    })


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='List and update Third Shot business integration requests.',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    list_command = commands.add_parser('list', help='show the operator inbox')
    list_command.add_argument(
        '--status',
        choices=('submitted', 'contacted', 'completed', 'declined', 'all'),
        default='submitted',
    )
    list_command.add_argument('--limit', type=int, default=100)
    update_command = commands.add_parser('update', help='change request status')
    update_command.add_argument('request_id', type=int)
    update_command.add_argument(
        'status', choices=('contacted', 'completed', 'declined'),
    )
    update_command.add_argument(
        '--operator', required=True,
        help='operator identity stored with this status change',
    )
    update_command.add_argument(
        '--message', required=True,
        help='business-visible status update; never include credentials or secrets',
    )
    args = parser.parse_args(argv)
    if args.command == 'list' and not 1 <= args.limit <= 500:
        parser.error('--limit must be between 1 and 500')

    _configure_environment(_target_database_url())
    from backend.app import app, db
    from backend.models import BusinessIntegrationRequest
    from backend.services.businesses import (
        update_business_integration_request_status,
    )

    with app.app_context():
        if args.command == 'list':
            query = BusinessIntegrationRequest.query
            if args.status != 'all':
                query = query.filter_by(status=args.status)
            items = (
                query.order_by(
                    BusinessIntegrationRequest.created_at.asc(),
                    BusinessIntegrationRequest.id.asc(),
                )
                .limit(args.limit)
                .all()
            )
            result = {'items': [item.to_operator_dict() for item in items]}
        else:
            try:
                result = {
                    'request': update_business_integration_request_status(
                        args.request_id,
                        args.status,
                        operator_identifier=args.operator,
                        status_message=args.message,
                    ),
                }
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Integration request operation failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
