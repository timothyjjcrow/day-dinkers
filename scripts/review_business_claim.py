#!/usr/bin/env python3
"""Approve or reject a Third Shot business claim through a direct DB URL."""
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


def _parse_args(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    description = (
        'List or review business control claims. Verification confirms '
        'listing control; it is not an endorsement.'
    )
    if values and values[0] == 'list':
        parser = argparse.ArgumentParser(description=description)
        parser.add_argument('command', choices=('list',))
        parser.add_argument(
            '--status', choices=('pending', 'verified', 'rejected', 'all'),
            default='pending',
        )
        parser.add_argument('--limit', type=int, default=100)
        args = parser.parse_args(values)
        if not 1 <= args.limit <= 500:
            parser.error('--limit must be between 1 and 500')
        return args

    # Every decision must leave an attributable evidence summary. Approving a
    # competing claim additionally requires an explicit transfer acknowledgment.
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('claim_id', type=int)
    parser.add_argument('decision', choices=('approve', 'reject'))
    parser.add_argument(
        '--reviewer', required=True,
        help='operator identity recorded in the immutable review event',
    )
    parser.add_argument(
        '--method', required=True,
        choices=(
            'business_email', 'business_phone', 'website_domain', 'documents',
            'in_person', 'other',
        ),
        help='how listing control was checked',
    )
    parser.add_argument(
        '--note', required=True,
        help='concise internal evidence/reason summary; never include secrets',
    )
    parser.add_argument(
        '--emergency-transfer-override', '--confirm-transfer',
        dest='confirm_transfer', action='store_true',
        help=(
            'emergency direct-DB override: acknowledge that approval will '
            'replace the current listing owner; this is immutably logged and '
            'the normal HTTP workflow requires a different administrator'
        ),
    )
    args = parser.parse_args(values)
    args.command = 'review'
    if args.confirm_transfer and args.decision != 'approve':
        parser.error('--confirm-transfer is only valid with approve')
    return args


def _operator_claim_payload(claim):
    ownership_transfer = bool(
        claim.business and claim.business.owner_id != claim.user_id
    )
    data = claim.to_dict()
    data.update({
        'claimant_user_id': claim.user_id,
        'claimant_name': claim.user.display_name if claim.user else None,
        'claimant_email': claim.user.email if claim.user else None,
        'business_name': claim.business.name if claim.business else None,
        'business_published': (
            bool(claim.business.published) if claim.business else False
        ),
        'current_owner_user_id': (
            claim.business.owner_id if claim.business else None
        ),
        'current_owner_name': (
            claim.business.owner.display_name
            if claim.business and claim.business.owner else None
        ),
        'current_owner_email': (
            claim.business.owner.email
            if claim.business and claim.business.owner else None
        ),
        'ownership_transfer': ownership_transfer,
        'approval_publication_policy': (
            'reset_unpublished_for_new_owner_review'
            if ownership_transfer else 'verified_owner_must_publish'
        ),
        'court_address': claim.court.address if claim.court else None,
        'court_city': claim.court.city if claim.court else None,
        'court_state': claim.court.state if claim.court else None,
        'review_history': [
            event.to_operator_dict() for event in claim.review_events
        ],
    })
    return data


def main(argv=None):
    args = _parse_args(argv)

    target = _target_database_url()
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
        'SECRET_KEY': 'business-review-process-secret-not-used-for-serving',
    })

    from backend.app import app, db
    from backend.models import BusinessClaim
    from backend.services.businesses import review_business_claim

    with app.app_context():
        if args.command == 'list':
            query = BusinessClaim.query
            if args.status != 'all':
                query = query.filter_by(status=args.status)
            items = (
                query.order_by(BusinessClaim.created_at.asc(), BusinessClaim.id.asc())
                .limit(args.limit)
                .all()
            )
            result = {'items': [_operator_claim_payload(item) for item in items]}
        else:
            try:
                result = review_business_claim(
                    args.claim_id,
                    args.decision,
                    reviewer_identifier=args.reviewer,
                    verification_method=args.method,
                    review_note=args.note,
                    confirm_transfer=args.confirm_transfer,
                )
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
        print(f'Business claim review failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
