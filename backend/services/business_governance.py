"""Business-team authorization, immutable revisions, and governance helpers."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date
from datetime import timedelta

from backend.app import db
from backend.models import (
    BusinessGovernanceEvent,
    BusinessOffering,
    BusinessOrganization,
    BusinessOrganizationMember,
    BusinessProfileRevision,
    BusinessScheduleItem,
    BusinessStaffInvitation,
    utcnow,
)


MANAGE_ROLES = {'owner', 'admin', 'editor'}
ADMIN_ROLES = {'owner', 'admin'}
SENSITIVE_PROFILE_FIELDS = {
    'name', 'email', 'phone', 'website_url', 'booking_url',
    'membership_url', 'logo_url', 'logo_data',
}
PROFILE_SNAPSHOT_FIELDS = (
    'name', 'description', 'announcement', 'contact_email', 'contact_phone',
    'hours', 'amenities', 'website_url', 'booking_url', 'membership_url',
    'logo_url', 'logo_data',
)


class BusinessGovernanceError(ValueError):
    pass


def token_hash(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def new_one_time_token():
    return secrets.token_urlsafe(32)


def ensure_organization(business, actor_user_id=None):
    """Lazily backfill a venue team while preserving legacy owner semantics."""
    organization = business.organization
    if organization is None:
        organization = BusinessOrganization(
            name=business.name,
            created_by_id=actor_user_id or business.owner_id,
        )
        db.session.add(organization)
        db.session.flush()
        business.organization = organization
    owner_member = BusinessOrganizationMember.query.filter_by(
        organization_id=organization.id,
        user_id=business.owner_id,
    ).first()
    if owner_member is None:
        owner_member = BusinessOrganizationMember(
            organization=organization,
            user_id=business.owner_id,
            role='owner',
        )
        db.session.add(owner_member)
    elif owner_member.role != 'owner':
        owner_member.role = 'owner'
    return organization


def business_access_role(business, user_id):
    if not business or not user_id:
        return None
    if business.owner_id == user_id:
        return 'owner'
    organization = business.organization
    if organization is None:
        return None
    member = BusinessOrganizationMember.query.filter_by(
        organization_id=organization.id,
        user_id=user_id,
    ).first()
    return member.role if member else None


def require_business_role(business, user_id, allowed_roles):
    role = business_access_role(business, user_id)
    if role not in set(allowed_roles):
        raise BusinessGovernanceError('business_manager_only')
    if business.governance_status == 'suspended':
        raise BusinessGovernanceError('business_suspended')
    if business.governance_status == 'relinquished':
        raise BusinessGovernanceError('business_relinquished')
    return role


def business_snapshot(business):
    return {
        'profile': {
            field: getattr(business, field)
            for field in PROFILE_SNAPSHOT_FIELDS
        },
        'offerings': [
            item.to_dict()
            for item in sorted(business.offerings, key=lambda value: value.sort_order)
        ],
        'schedule': [
            item.to_dict()
            for item in sorted(
                business.schedule_items, key=lambda value: value.sort_order,
            )
        ],
    }


def snapshot_fingerprint(snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def record_governance_event(
    business, event_type, *, actor_user_id=None, operator_identifier='', details=None,
):
    event = BusinessGovernanceEvent(
        business=business,
        actor_user_id=actor_user_id,
        operator_identifier=str(operator_identifier or '')[:120],
        event_type=str(event_type or '')[:48],
        details=json.dumps(details or {}, sort_keys=True),
    )
    db.session.add(event)
    return event


def record_revision(
    business,
    *,
    actor_user_id,
    action,
    before_snapshot,
    sensitive=False,
    restored_from_id=None,
):
    after_snapshot = business_snapshot(business)
    before_hash = snapshot_fingerprint(before_snapshot)
    after_hash = snapshot_fingerprint(after_snapshot)
    if before_hash == after_hash:
        return None
    needs_review = bool(business.verified and sensitive)
    revision = BusinessProfileRevision(
        business=business,
        actor_user_id=actor_user_id,
        action=str(action)[:40],
        change_summary=f'{before_hash[:10]} → {after_hash[:10]}',
        previous_snapshot=json.dumps(before_snapshot, sort_keys=True),
        snapshot=json.dumps(after_snapshot, sort_keys=True),
        sensitive=bool(sensitive),
        review_status='pending' if needs_review else 'approved',
        reviewed_at=None if needs_review else utcnow(),
        restored_from_id=restored_from_id,
    )
    db.session.add(revision)
    if needs_review:
        business.content_review_status = 'pending'
        business.content_reviewed_at = None
        business.published = False
        record_governance_event(
            business,
            'sensitive_change_pending',
            actor_user_id=actor_user_id,
            details={'action': action},
        )
    else:
        # A harmless follow-up edit must never launder a pending/rejected link,
        # logo, or contact change into an approved state. Only an operator
        # decision resolves an existing sensitive-content hold.
        if not business.verified or business.content_review_status == 'approved':
            business.content_review_status = 'approved'
            business.content_reviewed_at = utcnow()
    return revision


def _clear_children(items):
    for item in list(items):
        db.session.delete(item)


def _snapshot_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise BusinessGovernanceError('revision_snapshot_invalid')


def restore_snapshot(business, snapshot):
    profile = snapshot.get('profile')
    offerings = snapshot.get('offerings')
    schedule = snapshot.get('schedule')
    if not isinstance(profile, dict) or not isinstance(offerings, list) \
            or not isinstance(schedule, list):
        raise BusinessGovernanceError('revision_snapshot_invalid')
    for field in PROFILE_SNAPSHOT_FIELDS:
        if field in profile:
            setattr(business, field, profile[field] or '')
    _clear_children(business.offerings)
    db.session.flush()
    for position, item in enumerate(offerings):
        db.session.add(BusinessOffering(
            business=business,
            name=str(item.get('name') or '')[:120],
            category=str(item.get('category') or 'other')[:32],
            description=str(item.get('description') or '')[:1000],
            price_text=str(item.get('price_text') or '')[:120],
            duration_minutes=item.get('duration_minutes'),
            booking_url=str(item.get('booking_url') or '')[:500],
            active=bool(item.get('active', True)),
            sort_order=position,
        ))
    _clear_children(business.schedule_items)
    db.session.flush()
    for position, item in enumerate(schedule):
        db.session.add(BusinessScheduleItem(
            business=business,
            title=str(item.get('title') or '')[:120],
            kind=str(item.get('kind') or 'other')[:32],
            day_of_week=str(item.get('day_of_week') or '')[:12],
            start_time=str(item.get('start_time') or '')[:5],
            end_time=str(item.get('end_time') or '')[:5],
            skill_level=str(item.get('skill_level') or 'all')[:40],
            booking_url=str(item.get('booking_url') or '')[:500],
            timezone=str(item.get('timezone') or 'UTC')[:64],
            recurrence=str(item.get('recurrence') or 'weekly')[:24],
            start_date=_snapshot_date(item.get('start_date')),
            end_date=_snapshot_date(item.get('end_date')),
            event_date=_snapshot_date(item.get('event_date')),
            capacity=item.get('capacity'),
            spots_remaining=item.get('spots_remaining'),
            status=str(item.get('status') or 'scheduled')[:24],
            location_note=str(item.get('location_note') or '')[:240],
            instructor=str(item.get('instructor') or '')[:120],
            source_updated_at=utcnow(),
            active=bool(item.get('active', True)),
            sort_order=position,
        ))


def expire_pending_invitations(organization):
    now = utcnow()
    changed = False
    for invitation in organization.invitations:
        if invitation.status == 'pending' and invitation.expires_at <= now:
            invitation.status = 'expired'
            changed = True
    return changed


def create_staff_invitation(organization, *, invited_by_id, email, role):
    expire_pending_invitations(organization)
    existing = next(
        (
            invitation for invitation in organization.invitations
            if invitation.email == email and invitation.status == 'pending'
        ),
        None,
    )
    raw_token = new_one_time_token()
    if existing is None:
        existing = BusinessStaffInvitation(
            organization=organization,
            invited_by_id=invited_by_id,
            email=email,
            role=role,
            token_hash=token_hash(raw_token),
            expires_at=utcnow() + timedelta(days=7),
        )
        db.session.add(existing)
    else:
        existing.role = role
        existing.invited_by_id = invited_by_id
        existing.token_hash = token_hash(raw_token)
        existing.expires_at = utcnow() + timedelta(days=7)
    return existing, raw_token
