#!/usr/bin/env python3
"""Provision or revoke business operator RBAC through a direct database URL."""
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
        raise RuntimeError('TARGET_DATABASE_URL must be PostgreSQL.')
    if '-pooler.' in (make_url(raw).host or '').lower():
        raise RuntimeError('TARGET_DATABASE_URL must use the direct database host.')
    return raw


def _parser():
    parser = argparse.ArgumentParser(
        description='List, grant, or revoke reviewer/admin business roles.',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    grant = commands.add_parser('grant')
    grant.add_argument('email')
    grant.add_argument('role', choices=('reviewer', 'admin'))
    grant.add_argument('--actor', required=True)
    grant.add_argument('--reason', required=True)
    revoke = commands.add_parser('revoke')
    revoke.add_argument('email')
    revoke.add_argument('--actor', required=True)
    revoke.add_argument('--reason', required=True)
    return parser


def _require_mfa_before_grant(user, command):
    if command == 'grant' and not bool(user.mfa_enabled):
        raise RuntimeError('operator_mfa_must_be_enabled_before_grant')


def main(argv=None):
    args = _parser().parse_args(argv)
    target = _target_database_url()
    os.environ.update({
        'APP_ENV': 'production',
        'MFA_ENCRYPTION_KEY': 'cptEwcGPWoQwTRpx7LZH3BaiGR5MbnTsyqs1PjdFGgA=',
        'BUSINESS_CREDENTIAL_VAULT': 'disabled',
        'SERVERLESS_RUNTIME': 'true',
        'SCHEMA_MANAGEMENT_ENABLED': 'false',
        'AUTO_CREATE_DB': 'false',
        'DATABASE_URL': target,
        'SECRET_KEY': 'operator-role-process-secret-not-used-for-serving',
    })
    from backend.app import app, db
    from backend.models import OperatorSecurityEvent, User

    with app.app_context():
        if args.command == 'list':
            users = User.query.filter(
                User.operator_role.in_(['reviewer', 'admin']),
                User.deleted_at.is_(None),
            ).order_by(User.operator_role, User.email).all()
            result = {
                'items': [
                    {
                        'user_id': user.id,
                        'email': user.email,
                        'display_name': user.display_name,
                        'operator_role': user.operator_role,
                        'mfa_enabled': bool(user.mfa_enabled),
                    }
                    for user in users
                ],
            }
        else:
            email = args.email.strip().lower()
            user = User.query.filter_by(email=email).with_for_update().first()
            if user is None or user.deleted_at is not None:
                raise RuntimeError('active_user_not_found')
            _require_mfa_before_grant(user, args.command)
            previous = user.operator_role or ''
            new_role = args.role if args.command == 'grant' else ''
            if previous == new_role:
                raise RuntimeError('operator_role_unchanged')
            user.operator_role = new_role
            user.auth_version = int(user.auth_version or 1) + 1
            event = OperatorSecurityEvent(
                actor_identifier=args.actor.strip()[:120],
                target_user_id=user.id,
                action='operator_role_granted' if new_role else 'operator_role_revoked',
                previous_role=previous,
                new_role=new_role,
                reason=args.reason.strip()[:1000],
            )
            if not event.actor_identifier or not event.reason:
                raise RuntimeError('actor_and_reason_required')
            db.session.add(event)
            db.session.commit()
            result = {
                'user_id': user.id,
                'email': user.email,
                'operator_role': user.operator_role or None,
                'sessions_revoked': True,
                'audit_event': event.to_dict(),
            }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Operator role operation failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
