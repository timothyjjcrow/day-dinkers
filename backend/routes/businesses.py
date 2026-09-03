"""Court-business onboarding, public profiles, offerings, and schedules."""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    BUSINESS_INTEGRATION_CAPABILITIES,
    BUSINESS_OFFERING_CATEGORIES,
    BUSINESS_SCHEDULE_KINDS,
    BusinessClaim,
    BusinessIntegrationRequest,
    BusinessOffering,
    BusinessProfile,
    BusinessScheduleItem,
    BusinessVerificationEvidence,
    Court,
    User,
    notify,
    utcnow,
)
from backend.routes.auth import login_required, optional_current_user
from backend.security import rate_limit
from backend.services.business_governance import (
    ADMIN_ROLES,
    MANAGE_ROLES,
    SENSITIVE_PROFILE_FIELDS,
    BusinessGovernanceError,
    business_access_role,
    business_snapshot,
    record_revision,
)
from backend.services.business_visibility import (
    business_is_public,
    public_business_query,
)

businesses_bp = Blueprint('businesses', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_TIME_RE = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
_LIKELY_SECRET_RE = re.compile(
    r'(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|'
    r'\b(?:api[ _-]?key|client[ _-]?secret|password|access[ _-]?token)\s*[:=]|'
    r'\bbearer\s+[A-Za-z0-9._~+/=-]{12,}|\bsk-[A-Za-z0-9_-]{12,})',
    flags=re.IGNORECASE,
)
_DAYS = {
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
    'sunday', 'daily', 'weekdays', 'weekends',
}
_PROTECTED_PROFILE_FIELDS = {
    'id', 'owner_id', 'verified', 'verified_at', 'claim_status',
    'governance_status', 'suspension_reason', 'suspended_at', 'suspended_by',
    'content_review_status', 'content_reviewed_at', 'logo_data',
    'created_at', 'updated_at',
}
_MAX_ITEMS = 100


class PayloadError(ValueError):
    pass


def _request_object():
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise PayloadError('json_object_required')
    return payload


def _text(value, *, field, maximum, required=False):
    result = str(value or '').strip()
    if required and not result:
        raise PayloadError(f'{field}_required')
    if len(result) > maximum:
        raise PayloadError(f'{field}_too_long')
    return result


def _external_url(value, *, field):
    value = _text(value, field=field, maximum=500)
    if not value:
        return ''
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value) and not re.match(
        r'^https?://', value, flags=re.IGNORECASE,
    ):
        raise PayloadError(f'invalid_{field}')
    if '://' not in value:
        value = f'https://{value}'
    parsed = urlsplit(value)
    if (
        parsed.scheme != 'https'
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in parsed.netloc)
    ):
        raise PayloadError(f'invalid_{field}')
    return urlunsplit(parsed)


def _boolean(value, *, field):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise PayloadError(f'{field}_must_be_boolean')


def _require_authorized_attestation(payload):
    """Require an explicit request-time assertion of listing authority."""
    if payload.get('authorized_attestation') is not True:
        raise PayloadError('authorized_attestation_required')


def _normalize_kind(value, allowed, *, field):
    value = _text(value, field=field, maximum=32).lower()
    value = value.replace('-', '_').replace(' ', '_') or 'other'
    if value not in allowed:
        raise PayloadError(f'invalid_{field}')
    return value


def _profile_or_404(business_id):
    business = db.session.get(BusinessProfile, business_id)
    if not business:
        return None, (jsonify({'error': 'business_not_found'}), 404)
    return business, None


def _owned_profile_or_error(business_id, *, allowed_roles=MANAGE_ROLES):
    business = (
        BusinessProfile.query.filter_by(id=business_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if not business:
        return None, (jsonify({'error': 'business_not_found'}), 404)
    role = business_access_role(business, g.current_user.id)
    if role not in set(allowed_roles):
        return None, (jsonify({'error': 'business_owner_only'}), 403)
    if business.governance_status == 'suspended':
        return None, (jsonify({'error': 'business_suspended'}), 409)
    if business.governance_status == 'relinquished':
        return None, (jsonify({'error': 'business_relinquished'}), 409)
    g.business_role = role
    return business, None


def _lock_current_actor():
    """Serialize business mutations with account deletion."""
    user = (
        User.query.filter_by(id=g.current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if user is None or user.deleted_at is not None:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    return None


def _ensure_no_verification_override(payload):
    protected = sorted(_PROTECTED_PROFILE_FIELDS.intersection(payload))
    if protected:
        raise PayloadError('verification_fields_are_server_managed')


def _apply_profile_payload(business, payload, *, creating=False):
    _ensure_no_verification_override(payload)
    if creating or 'name' in payload:
        business.name = _text(
            payload.get('name') if 'name' in payload else business.name,
            field='name', maximum=120, required=True,
        )
    if 'role' in payload:
        business.claimant_role = _text(
            payload.get('role'), field='role', maximum=80,
        )
    for field, attr, maximum in (
        ('description', 'description', 2000),
        ('announcement', 'announcement', 500),
        ('phone', 'contact_phone', 40),
        ('hours', 'hours', 1000),
    ):
        if field in payload:
            setattr(
                business, attr,
                _text(payload.get(field), field=field, maximum=maximum),
            )
    if 'email' in payload:
        email = _text(payload.get('email'), field='email', maximum=255).lower()
        if email and not _EMAIL_RE.fullmatch(email):
            raise PayloadError('invalid_email')
        business.contact_email = email
    for field in ('website_url', 'booking_url', 'membership_url', 'logo_url'):
        if field in payload:
            setattr(
                business, field,
                _external_url(payload.get(field), field=field),
            )
    if 'amenities' in payload:
        raw = payload.get('amenities')
        if not isinstance(raw, list):
            raise PayloadError('amenities_must_be_a_list')
        if len(raw) > 30:
            raise PayloadError('too_many_amenities')
        amenities = []
        seen = set()
        for value in raw:
            amenity = _text(value, field='amenity', maximum=60)
            key = amenity.casefold()
            if amenity and key not in seen:
                amenities.append(amenity)
                seen.add(key)
        business.amenities = json.dumps(amenities)
    if 'published' in payload:
        publish = _boolean(payload.get('published'), field='published')
        if publish and not business.verified:
            raise PayloadError('business_verification_required')
        if publish and business.governance_status != 'active':
            raise PayloadError('business_not_publishable')
        if publish and business.content_review_status != 'approved':
            raise PayloadError('business_content_review_required')
        business.published = publish


def _profile_payload(business, *, owner=False, manager_role=None):
    data = (
        business.to_dict(include_inactive=True)
        if owner or manager_role else business.to_public_dict()
    )
    data['is_owner'] = bool(owner)
    if owner or manager_role:
        data['is_manager'] = True
        role = manager_role or 'owner'
        data['manager_role'] = role
        # Integration requests contain private contact, requester, and vendor
        # context. Editors and viewers can see the resulting public links and
        # connection health, but request workflow data is admin-only.
        if role not in ADMIN_ROLES:
            data.pop('integration_requests', None)
    return data


def _claim_submission_evidence(payload):
    """Validate and normalize evidence supplied with the initial claim form."""
    email = _text(
        payload.get('verification_contact_email'),
        field='verification_contact_email', maximum=255,
    ).lower()
    if email and not _EMAIL_RE.fullmatch(email):
        raise PayloadError('invalid_verification_contact_email')
    evidence_url = _external_url(
        payload.get('evidence_url'), field='evidence_url',
    )
    notes = _text(
        payload.get('evidence_notes'), field='evidence_notes', maximum=800,
    )
    if notes and _LIKELY_SECRET_RE.search(notes):
        raise PayloadError('claim_evidence_may_contain_secret')
    return email, evidence_url, notes


def _persist_claim_submission_evidence(claim, *, email='', evidence_url='', notes=''):
    """Make every populated claim field durable and operator-reviewable."""
    if not any((email, evidence_url, notes)):
        return
    submitted = [
        ('business_email', email, ''),
        ('website_domain', evidence_url, ''),
        ('other', 'Claimant-provided verification context', notes),
    ]
    active_keys = {
        (item.evidence_type, item.evidence_value, item.note)
        for item in claim.evidence
        if item.status != 'rejected'
    }
    for evidence_type, value, note in submitted:
        if not value or (evidence_type, value, note) in active_keys:
            continue
        claim.evidence.append(BusinessVerificationEvidence(
            submitted_by_id=g.current_user.id,
            evidence_type=evidence_type,
            evidence_value=value,
            note=note,
            status='submitted',
        ))


def _log_business_claim_submission(claim):
    current_app.logger.warning(
        'Business claim submitted id=%s court_id=%s business_id=%s status=%s',
        claim.id, claim.court_id, claim.business_id, claim.status,
    )


@businesses_bp.get('/businesses')
def list_businesses():
    """Public, published business integrations for player discovery."""
    query = public_business_query()
    court_id = request.args.get('court_id', type=int)
    q = str(request.args.get('q') or '').strip()
    if court_id is not None:
        query = query.filter(BusinessProfile.court_id == court_id)
    if q:
        like = f'%{q[:120]}%'
        query = query.filter(db.or_(
            BusinessProfile.name.ilike(like),
            BusinessProfile.description.ilike(like),
        ))
    items = query.order_by(BusinessProfile.verified_at.desc(), BusinessProfile.id).limit(100)
    return jsonify({'items': [_profile_payload(item) for item in items]})


@businesses_bp.get('/businesses/mine')
@login_required
def my_businesses():
    from backend.models import BusinessOrganization, BusinessOrganizationMember
    managed_organization_ids = db.session.query(BusinessOrganization.id).join(
        BusinessOrganizationMember,
        BusinessOrganizationMember.organization_id == BusinessOrganization.id,
    ).filter(BusinessOrganizationMember.user_id == g.current_user.id)
    profiles = BusinessProfile.query.filter(db.or_(
        BusinessProfile.owner_id == g.current_user.id,
        BusinessProfile.organization_id.in_(managed_organization_ids),
    )).order_by(BusinessProfile.id).all()
    claims = (
        BusinessClaim.query.filter_by(user_id=g.current_user.id)
        .order_by(BusinessClaim.id)
        .all()
    )
    return jsonify({
        'items': [
            _profile_payload(
                item,
                owner=item.owner_id == g.current_user.id,
                manager_role=business_access_role(item, g.current_user.id),
            )
            for item in profiles
        ],
        'claims': [claim.to_dict() for claim in claims],
    })


@businesses_bp.get('/businesses/<int:business_id>')
def business_detail(business_id):
    business, err = _profile_or_404(business_id)
    if err:
        return err
    viewer = optional_current_user()
    manager_role = business_access_role(business, viewer.id) if viewer else None
    owner = bool(viewer and viewer.id == business.owner_id)
    if not manager_role and not business_is_public(business):
        return jsonify({'error': 'business_not_found'}), 404
    return jsonify(_profile_payload(
        business, owner=owner, manager_role=manager_role,
    ))


@businesses_bp.get('/courts/<int:court_id>/business')
def court_business(court_id):
    court = db.session.get(Court, court_id)
    if not court or court.closed:
        return jsonify({'error': 'court_not_found'}), 404
    business = public_business_query().filter(
        BusinessProfile.court_id == court_id,
    ).first()
    viewer = optional_current_user()
    return jsonify({
        'business': _profile_payload(
            business,
            owner=bool(viewer and business and viewer.id == business.owner_id),
            manager_role=(
                business_access_role(business, viewer.id)
                if viewer and business else None
            ),
        ) if business else None,
    })


@businesses_bp.post('/businesses/claims')
@rate_limit(10, 3600)
@login_required
def submit_business_claim():
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        court_id = int(payload.get('court_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'court_id_required'}), 400
    court = db.session.get(Court, court_id)
    if not court or court.closed:
        return jsonify({'error': 'court_not_found'}), 404
    try:
        role = _text(payload.get('role'), field='role', maximum=80, required=True)
        _require_authorized_attestation(payload)
        evidence_email, evidence_url, evidence_notes = _claim_submission_evidence(payload)
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error

    business = (
        BusinessProfile.query.filter_by(court_id=court_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    claim = (
        BusinessClaim.query.filter_by(
            user_id=g.current_user.id, court_id=court_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    created = claim is None
    reopened = bool(claim is not None and claim.status == 'rejected')
    if business is None:
        business = BusinessProfile(
            owner_id=g.current_user.id,
            court_id=court_id,
            name=court.name,
            claimant_role=role,
            claim_status='pending',
            published=False,
        )
        db.session.add(business)
        db.session.flush()
    elif business.owner and business.owner.deleted_at is not None:
        # Account deletion retires the public listing but preserves the venue
        # draft. A later representative may take over that orphaned draft;
        # it goes back through verification before any link becomes public.
        business.owner_id = g.current_user.id
        business.claimant_role = role
        business.claim_status = 'pending'
        business.verified_at = None
        business.published = False
    elif business.owner_id == g.current_user.id:
        business.claimant_role = role
        if business.claim_status == 'rejected':
            business.claim_status = 'pending'
            business.verified_at = None
            business.published = False

    if claim is None:
        claim = BusinessClaim(
            user_id=g.current_user.id,
            court_id=court_id,
            business_id=business.id,
            role=role,
            status='pending',
        )
        db.session.add(claim)
    else:
        claim.role = role
        claim.business_id = business.id
        if claim.status == 'rejected':
            claim.status = 'pending'
            claim.reviewed_at = None
    _persist_claim_submission_evidence(
        claim,
        email=evidence_email,
        evidence_url=evidence_url,
        notes=evidence_notes,
    )
    if (
        (created or reopened)
        and business.owner_id != g.current_user.id
        and business.owner is not None
        and business.owner.deleted_at is None
    ):
        notify(
            business.owner_id,
            'business_claim',
            'Another representative claimed your venue',
            f'A new control claim for {court.name} is awaiting review. '
            'Your listing has not changed.',
            unread_dedupe_key=f'business-claim-pending:{business.id}',
        )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'claim_already_pending'}), 409
    if created or reopened:
        _log_business_claim_submission(claim)
    return jsonify({
        'claim': claim.to_dict(),
        # A competing claimant may track their claim, but cannot inspect an
        # unpublished owner's draft or its external action links.
        'business': (
            _profile_payload(business, owner=True)
            if business.owner_id == g.current_user.id else None
        ),
    }), (201 if created else 200)


@businesses_bp.post('/businesses')
@rate_limit(10, 3600)
@login_required
def create_business():
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        court_id = int(payload.get('court_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'court_id_required'}), 400
    court = db.session.get(Court, court_id)
    if not court or court.closed:
        return jsonify({'error': 'court_not_found'}), 404
    try:
        _text(payload.get('role'), field='role', maximum=80, required=True)
        _require_authorized_attestation(payload)
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error

    business = (
        BusinessProfile.query.filter_by(court_id=court_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    created = business is None
    if business is not None and business.owner_id != g.current_user.id:
        return jsonify({'error': 'court_business_already_managed'}), 409
    if business is None:
        business = BusinessProfile(
            owner_id=g.current_user.id,
            court_id=court_id,
            name=court.name,
            claim_status='pending',
            published=False,
        )
        db.session.add(business)
    elif business.claim_status == 'rejected':
        # Completing or resubmitting one's own rejected draft reopens the
        # control review. A competing user was rejected above and cannot reach
        # this branch.
        business.claim_status = 'pending'
        business.verified_at = None
        business.published = False
    try:
        _apply_profile_payload(business, payload, creating=True)
        db.session.flush()
        claim = (
            BusinessClaim.query.filter_by(
                user_id=g.current_user.id, court_id=court_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
            .first()
        )
        claim_created = claim is None
        claim_reopened = bool(claim is not None and claim.status == 'rejected')
        if claim is None:
            claim = BusinessClaim(
                user_id=g.current_user.id,
                court_id=court_id,
                business_id=business.id,
                role=business.claimant_role,
                status='pending',
            )
            db.session.add(claim)
        else:
            claim.business_id = business.id
            claim.role = business.claimant_role or claim.role
            if claim.status == 'rejected':
                claim.status = 'pending'
                claim.reviewed_at = None
        db.session.commit()
    except PayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'court_business_already_managed'}), 409
    if claim_created or claim_reopened:
        _log_business_claim_submission(claim)
    return jsonify(_profile_payload(business, owner=True)), (201 if created else 200)


@businesses_bp.patch('/businesses/<int:business_id>')
@rate_limit(30, 3600)
@login_required
def update_business(business_id):
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    business, err = _owned_profile_or_error(business_id)
    if err:
        return err
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    if 'published' in payload and g.business_role not in ADMIN_ROLES:
        return jsonify({'error': 'business_admin_only'}), 403
    if 'court_id' in payload:
        try:
            requested_court_id = int(payload.get('court_id'))
        except (TypeError, ValueError):
            return jsonify({'error': 'court_id_cannot_change'}), 400
        if requested_court_id != business.court_id:
            return jsonify({'error': 'court_id_cannot_change'}), 400
    before_snapshot = business_snapshot(business)
    sensitive = False
    try:
        for field in SENSITIVE_PROFILE_FIELDS.intersection(payload):
            attr = {
                'email': 'contact_email', 'phone': 'contact_phone',
            }.get(field, field)
            current = getattr(business, attr, None)
            requested = payload.get(field)
            if str(current or '').strip() != str(requested or '').strip():
                sensitive = True
                break
        _apply_profile_payload(business, payload)
        record_revision(
            business,
            actor_user_id=g.current_user.id,
            action='profile_update',
            before_snapshot=before_snapshot,
            sensitive=sensitive,
        )
        db.session.commit()
    except PayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    return jsonify(_profile_payload(
        business,
        owner=g.business_role == 'owner',
        manager_role=g.business_role,
    ))


def _validated_offering(raw, position):
    if not isinstance(raw, dict):
        raise PayloadError('offering_must_be_an_object')
    duration = raw.get('duration_minutes')
    if duration in ('', None):
        duration = None
    else:
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            raise PayloadError('invalid_duration_minutes')
        if duration < 5 or duration > 1440:
            raise PayloadError('invalid_duration_minutes')
    return {
        'id': raw.get('id'),
        'name': _text(raw.get('name'), field='offering_name', maximum=120, required=True),
        'category': _normalize_kind(
            raw.get('category'), BUSINESS_OFFERING_CATEGORIES, field='offering_category',
        ),
        'description': _text(
            raw.get('description'), field='offering_description', maximum=1000,
        ),
        'price_text': _text(raw.get('price_text'), field='price_text', maximum=120),
        'duration_minutes': duration,
        'booking_url': _external_url(raw.get('booking_url'), field='booking_url'),
        'active': _boolean(raw.get('active', True), field='active'),
        'sort_order': position,
    }


def _replace_items(business, raw_items, *, relationship, model, validator, error_name):
    if not isinstance(raw_items, list):
        raise PayloadError('items_must_be_a_list')
    if len(raw_items) > _MAX_ITEMS:
        raise PayloadError('too_many_items')
    validated = [validator(raw, position) for position, raw in enumerate(raw_items)]
    current_items = list(getattr(business, relationship))
    by_id = {item.id: item for item in current_items}
    retained = []
    for values in validated:
        raw_id = values.pop('id', None)
        if raw_id is None:
            item = model(business=business)
        else:
            try:
                item = by_id.pop(int(raw_id))
            except (TypeError, ValueError, KeyError):
                raise PayloadError(error_name)
        for key, value in values.items():
            setattr(item, key, value)
        retained.append(item)
    for item in by_id.values():
        db.session.delete(item)
    setattr(business, relationship, retained)


@businesses_bp.put('/businesses/<int:business_id>/offerings')
@rate_limit(30, 3600)
@login_required
def replace_business_offerings(business_id):
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    business, err = _owned_profile_or_error(business_id)
    if err:
        return err
    before_snapshot = business_snapshot(business)
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        _replace_items(
            business, payload.get('items'),
            relationship='offerings', model=BusinessOffering,
            validator=_validated_offering, error_name='offering_not_found',
        )
        after_urls = {
            item.booking_url for item in business.offerings if item.booking_url
        }
        before_urls = {
            str(item.get('booking_url') or '')
            for item in before_snapshot.get('offerings', [])
            if item.get('booking_url')
        }
        record_revision(
            business,
            actor_user_id=g.current_user.id,
            action='offerings_replace',
            before_snapshot=before_snapshot,
            sensitive=after_urls != before_urls,
        )
        db.session.commit()
    except PayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    return jsonify(_profile_payload(
        business,
        owner=g.business_role == 'owner',
        manager_role=g.business_role,
    ))


def _validated_schedule_item(raw, position):
    if not isinstance(raw, dict):
        raise PayloadError('schedule_item_must_be_an_object')

    recurrence = _text(
        raw.get('recurrence') or 'weekly', field='recurrence', maximum=24,
    ).lower().replace('-', '_').replace(' ', '_')
    if recurrence not in {'weekly', 'dated', 'date_range'}:
        raise PayloadError('invalid_recurrence')

    def parsed_date(field):
        value = _text(raw.get(field), field=field, maximum=10)
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise PayloadError(f'invalid_{field}')

    start_date = parsed_date('start_date')
    end_date = parsed_date('end_date')
    event_date = parsed_date('event_date')
    if recurrence == 'dated':
        if event_date is None:
            raise PayloadError('event_date_required')
        start_date = end_date = None
    elif recurrence == 'date_range':
        if start_date is None or end_date is None:
            raise PayloadError('date_range_required')
        if end_date < start_date:
            raise PayloadError('end_date_before_start_date')
        event_date = None
    else:
        start_date = end_date = event_date = None

    day = _text(
        raw.get('day_of_week'), field='day_of_week', maximum=12,
        required=recurrence != 'dated',
    ).lower()
    if recurrence == 'dated' and not day:
        day = event_date.strftime('%A').lower()
    if day not in _DAYS:
        raise PayloadError('invalid_day_of_week')

    timezone = _text(
        raw.get('timezone') or 'UTC', field='timezone', maximum=64,
        required=True,
    )
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise PayloadError('invalid_timezone')

    start = _text(raw.get('start_time'), field='start_time', maximum=5, required=True)
    end = _text(raw.get('end_time'), field='end_time', maximum=5, required=True)
    if not _TIME_RE.fullmatch(start) or not _TIME_RE.fullmatch(end):
        raise PayloadError('times_must_use_24_hour_hh_mm')
    if start >= end:
        raise PayloadError('end_time_must_be_after_start_time')

    def optional_integer(field):
        value = raw.get(field)
        if value in ('', None):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise PayloadError(f'invalid_{field}')
        if number < 0 or number > 10_000:
            raise PayloadError(f'invalid_{field}')
        return number

    capacity = optional_integer('capacity')
    spots_remaining = optional_integer('spots_remaining')
    if spots_remaining is not None and capacity is None:
        raise PayloadError('capacity_required_with_spots_remaining')
    if spots_remaining is not None and spots_remaining > capacity:
        raise PayloadError('spots_remaining_exceeds_capacity')
    status = _text(
        raw.get('status') or 'scheduled', field='status', maximum=24,
    ).lower()
    if status not in {'scheduled', 'cancelled', 'sold_out', 'completed'}:
        raise PayloadError('invalid_schedule_status')
    if spots_remaining == 0 and status == 'scheduled':
        status = 'sold_out'
    return {
        'id': raw.get('id'),
        'title': _text(
            raw.get('title'), field='schedule_title', maximum=120, required=True,
        ),
        'kind': _normalize_kind(
            raw.get('kind'), BUSINESS_SCHEDULE_KINDS, field='schedule_kind',
        ),
        'day_of_week': day,
        'start_time': start,
        'end_time': end,
        'timezone': timezone,
        'recurrence': recurrence,
        'start_date': start_date,
        'end_date': end_date,
        'event_date': event_date,
        'capacity': capacity,
        'spots_remaining': spots_remaining,
        'status': status,
        'location_note': _text(
            raw.get('location_note'), field='location_note', maximum=240,
        ),
        'instructor': _text(
            raw.get('instructor'), field='instructor', maximum=120,
        ),
        'source_updated_at': utcnow(),
        'skill_level': _text(
            raw.get('skill_level') or 'all', field='skill_level', maximum=40,
        ),
        'booking_url': _external_url(raw.get('booking_url'), field='booking_url'),
        'active': _boolean(raw.get('active', True), field='active'),
        'sort_order': position,
    }


@businesses_bp.put('/businesses/<int:business_id>/schedule')
@rate_limit(30, 3600)
@login_required
def replace_business_schedule(business_id):
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    business, err = _owned_profile_or_error(business_id)
    if err:
        return err
    before_snapshot = business_snapshot(business)
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        _replace_items(
            business, payload.get('items'),
            relationship='schedule_items', model=BusinessScheduleItem,
            validator=_validated_schedule_item,
            error_name='schedule_item_not_found',
        )
        after_urls = {
            item.booking_url for item in business.schedule_items if item.booking_url
        }
        before_urls = {
            str(item.get('booking_url') or '')
            for item in before_snapshot.get('schedule', [])
            if item.get('booking_url')
        }
        record_revision(
            business,
            actor_user_id=g.current_user.id,
            action='schedule_replace',
            before_snapshot=before_snapshot,
            sensitive=after_urls != before_urls,
        )
        db.session.commit()
    except PayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    return jsonify(_profile_payload(
        business,
        owner=g.business_role == 'owner',
        manager_role=g.business_role,
    ))


def _validated_integration_request(payload, business):
    provider = _text(payload.get('provider'), field='provider', maximum=120)
    details = _text(payload.get('details'), field='details', maximum=2000)
    raw_capabilities = payload.get('capabilities', [])
    if not isinstance(raw_capabilities, list):
        raise PayloadError('capabilities_must_be_a_list')
    if len(raw_capabilities) > len(BUSINESS_INTEGRATION_CAPABILITIES):
        raise PayloadError('too_many_capabilities')
    capabilities = []
    for raw in raw_capabilities:
        capability = _text(
            raw, field='capability', maximum=40, required=True,
        ).lower().replace('-', '_').replace(' ', '_')
        if capability not in BUSINESS_INTEGRATION_CAPABILITIES:
            raise PayloadError('invalid_capability')
        if capability not in capabilities:
            capabilities.append(capability)
    if not provider and not capabilities and len(details) < 3:
        raise PayloadError('integration_request_details_required')
    if _LIKELY_SECRET_RE.search(f'{provider}\n{details}'):
        raise PayloadError('integration_request_may_contain_secret')

    if 'contact_email' in payload:
        contact_email = _text(
            payload.get('contact_email'), field='contact_email', maximum=255,
            required=True,
        ).lower()
        if contact_email and not _EMAIL_RE.fullmatch(contact_email):
            raise PayloadError('invalid_contact_email')
    else:
        contact_email = business.contact_email or g.current_user.email
    return {
        'provider': provider,
        'capabilities': json.dumps(capabilities),
        'details': details,
        'contact_email': contact_email,
    }


@businesses_bp.post('/businesses/<int:business_id>/integration-requests')
@rate_limit(10, 86400)
@login_required
def create_business_integration_request(business_id):
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    business, err = _owned_profile_or_error(
        business_id, allowed_roles=ADMIN_ROLES,
    )
    if err:
        return err
    if not business.verified:
        db.session.rollback()
        return jsonify({'error': 'business_verification_required'}), 409
    try:
        payload = _request_object()
        values = _validated_integration_request(payload, business)
    except PayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    item = BusinessIntegrationRequest(
        business=business,
        requested_by_id=g.current_user.id,
        status='submitted',
        **values,
    )
    db.session.add(item)
    db.session.commit()
    current_app.logger.warning(
        'Business integration request submitted id=%s business_id=%s '
        'capability_count=%s',
        item.id, business.id, len(item.capabilities_list()),
    )
    return jsonify({'request': item.to_dict()}), 201


@businesses_bp.get('/businesses/<int:business_id>/integration-requests')
@login_required
def list_business_integration_requests(business_id):
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    business, err = _owned_profile_or_error(
        business_id, allowed_roles=ADMIN_ROLES,
    )
    if err:
        return err
    items = (
        BusinessIntegrationRequest.query.filter_by(
            business_id=business.id,
        )
        .order_by(BusinessIntegrationRequest.created_at.desc(), BusinessIntegrationRequest.id.desc())
        .limit(100)
        .all()
    )
    return jsonify({'items': [item.to_dict() for item in items]})
