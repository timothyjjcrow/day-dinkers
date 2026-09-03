#!/usr/bin/env python3
"""Trusted operator tooling for provider connections and retry jobs.

Arguments accept only opaque vault references, never credential values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parser():
    parser = argparse.ArgumentParser(
        description='Manage Third Shot provider connections without exposing secrets.',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list')
    listing.add_argument(
        '--status', choices=('all', 'draft', 'connected', 'degraded', 'error', 'disconnected'),
        default='all',
    )
    listing.add_argument('--limit', type=int, default=100)
    attach = commands.add_parser('attach-vault-refs')
    attach.add_argument('connection_id', type=int)
    attach.add_argument('--credential-ref', default='')
    attach.add_argument('--webhook-secret-ref', default='')
    attach.add_argument('--cursor-ref', default='')
    recheck = commands.add_parser('recheck')
    recheck.add_argument('connection_id', type=int)
    disconnect = commands.add_parser('disconnect')
    disconnect.add_argument('connection_id', type=int)
    retry = commands.add_parser('retry-due')
    retry.add_argument('--limit', type=int, default=10)
    health = commands.add_parser('health-due')
    health.add_argument('--limit', type=int, default=10)
    rotation = commands.add_parser('rotate-vault')
    rotation.add_argument('--limit', type=int, default=100)
    return parser


def _configure(target):
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
        'SECRET_KEY': 'connection-operator-process-secret-not-used-for-serving',
        'MFA_ENCRYPTION_KEY': (
            'cptEwcGPWoQwTRpx7LZH3BaiGR5MbnTsyqs1PjdFGgA='
        ),
    })


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if hasattr(args, 'limit') and not 1 <= args.limit <= 500:
        parser.error('--limit must be between 1 and 500')
    from scripts.migrate_production_schema import _validated_target_url
    _configure(_validated_target_url())

    from backend.app import app, db
    from backend.integrations.models import (
        BusinessCredentialSecret,
        BusinessProviderConnection,
    )
    from backend.integrations.services import (
        attach_vault_references,
        disconnect_connection,
        due_sync_runs,
        process_sync_run,
        recheck_connection_links,
        stale_connections,
    )
    from backend.integrations.vault import EncryptedSqlCredentialVault

    actor = os.getenv('BUSINESS_OPERATOR_ID', 'cli-operator')
    with app.app_context():
        if args.command == 'list':
            query = BusinessProviderConnection.query
            if args.status != 'all':
                query = query.filter_by(status=args.status)
            items = query.order_by(BusinessProviderConnection.id).limit(args.limit).all()
            result = {'items': [item.to_owner_dict() for item in items]}
        elif args.command == 'retry-due':
            runs = due_sync_runs(limit=args.limit)
            for run in runs:
                process_sync_run(run)
            db.session.commit()
            result = {'processed': len(runs), 'runs': [run.to_dict() for run in runs]}
        elif args.command == 'health-due':
            connections = stale_connections(limit=args.limit)
            checked = 0
            for connection in connections:
                recheck_connection_links(
                    connection, actor_kind='operator', actor_id=actor,
                )
                checked += 1
            db.session.commit()
            result = {'processed': checked}
        elif args.command == 'rotate-vault':
            current_version = int(app.config['BUSINESS_CREDENTIAL_KEY_VERSION'])
            query = BusinessCredentialSecret.query.filter(
                BusinessCredentialSecret.deleted_at.is_(None),
                BusinessCredentialSecret.key_version != current_version,
            ).order_by(BusinessCredentialSecret.id).with_for_update()
            rows = query.limit(args.limit).all()
            vault = EncryptedSqlCredentialVault()
            for row in rows:
                vault.rotate(row.reference)
            db.session.commit()
            result = {
                'processed': len(rows),
                'remaining': BusinessCredentialSecret.query.filter(
                    BusinessCredentialSecret.deleted_at.is_(None),
                    BusinessCredentialSecret.key_version != current_version,
                ).count(),
                'key_version': current_version,
            }
        else:
            connection = BusinessProviderConnection.query.filter_by(
                id=args.connection_id,
            ).with_for_update().first()
            if not connection:
                raise RuntimeError('connection_not_found')
            if args.command == 'attach-vault-refs':
                attach_vault_references(
                    connection,
                    credential_ref=args.credential_ref,
                    webhook_secret_ref=args.webhook_secret_ref,
                    cursor_ref=args.cursor_ref,
                    actor_id=actor,
                )
            elif args.command == 'recheck':
                recheck_connection_links(
                    connection, actor_kind='operator', actor_id=actor,
                )
            elif args.command == 'disconnect':
                disconnect_connection(
                    connection, actor_kind='operator', actor_id=actor,
                )
            db.session.commit()
            result = {'connection': connection.to_owner_dict()}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'Business connection operation failed: {error}', file=sys.stderr)
        raise SystemExit(1)
