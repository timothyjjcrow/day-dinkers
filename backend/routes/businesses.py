"""Court-business onboarding, public profiles, offerings, and schedules."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta
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
    content_precondition_error,
    record_revision,
)
from backend.services.business_visibility import (
    business_is_public,
    public_business_query,
    reviewed_profile_search_value,
)

businesses_bp = Blueprint('businesses', __name__)

from backend.services.venue_locations import pending_court_request_guard
businesses_bp.before_app_request(pending_court_request_guard)

@businesses_bp.after_request
def business_content_etag(response):
    payload = response.get_json(silent=True) if response.is_json else None
    if isinstance(payload, dict) and payload.get('content_version'):
        response.set_etag(payload['content_version'])
        response.headers['Cache-Control'] = 'private, no-store'
    return response


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
    'created_at', 'updated_at', 'reviewed_public_snapshot',
}
_MAX_ITEMS = 100
_SCHEDULE_CSV_MAX_BYTES = 256 * 1024
_SCHEDULE_CSV_HEADER_ALIASES = {
    'name': 'title',
    'type': 'kind',
    'day': 'day_of_week',
    'start': 'start_time',
    'end': 'end_time',
    'time_zone': 'timezone',
    'level': 'skill_level',
    'audience': 'skill_level',
    'spots': 'spots_remaining',
    'location': 'location_note',
    'host': 'instructor',
    'registration_link': 'booking_url',
    'booking_link': 'booking_url',
    'date': 'event_date',
    'from_date': 'start_date',
    'to_date': 'end_date',
    'visible': 'active',
    'pattern': 'recurrence',
}
_SCHEDULE_CSV_FIELDS = {
    'title', 'kind', 'day_of_week', 'start_time', 'end_time', 'timezone',
    'recurrence', 'event_date', 'start_date', 'end_date', 'skill_level',
    'capacity', 'spots_remaining', 'status', 'location_note', 'instructor',
    'booking_url', 'active',
}


class PayloadError(ValueError):
    pass


class ScheduleCsvError(ValueError):
    def __init__(self, code, *, row=None):
        super().__init__(code)
        self.code = code
        self.row = row


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
    if 'timezone' in payload:
        timezone = _text(payload.get('timezone'), field='timezone', maximum=64)
        if timezone:
            try:
                ZoneInfo(timezone)
            except (ValueError, ZoneInfoNotFoundError):
                raise PayloadError('invalid_timezone')
        business.timezone = timezone
        if 'structured_hours' not in payload and business.structured_hours not in ('{}', ''):
            from backend.services.court_hours import hours_dict, normalize_hours
            try:
                business.structured_hours = json.dumps(normalize_hours({**hours_dict(business.structured_hours), 'timezone':timezone}), sort_keys=True)
            except ValueError as exc:
                raise PayloadError(str(exc))
    if 'structured_hours' in payload:
        from backend.services.court_hours import normalize_hours
        try:
            business.structured_hours = json.dumps(normalize_hours(payload['structured_hours'], business.timezone), sort_keys=True)
        except ValueError as exc:
            raise PayloadError(str(exc))
    if 'visitor_info' in payload:
        from backend.services.court_visiting import normalize_visiting
        try:
            business.visitor_info = json.dumps(normalize_visiting(payload['visitor_info']), sort_keys=True)
        except ValueError as exc:
            raise PayloadError(str(exc))
    if 'hours_dawn_to_dusk' in payload:
        business.hours_dawn_to_dusk = _boolean(payload['hours_dawn_to_dusk'], field='hours_dawn_to_dusk')
    if business.hours_dawn_to_dusk and business.structured_hours not in ('{}', ''):
        raise PayloadError('choose_one_hours_mode')
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
        if publish:
            business.reviewed_public_snapshot = ''


def _profile_payload(business, *, owner=False, manager_role=None):
    data = (
        business.to_dict(include_inactive=True)
        if owner or manager_role else business.to_public_dict()
    )
    data['is_owner'] = bool(owner)
    if owner or manager_role:
        data['is_manager'] = True
        data['is_public'] = business_is_public(business)
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
            reviewed_profile_search_value('name').ilike(like),
            reviewed_profile_search_value('description').ilike(like),
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


def _validated_venue_location(payload):
    if not isinstance(payload, dict):
        raise PayloadError('venue_location_required')
    values = {key: _text(payload.get(key), field=key, maximum=maximum, required=True) for key, maximum in [('name', 120), ('address', 255), ('city', 120), ('state', 2)]}
    values['state'] = values['state'].upper()
    if not re.fullmatch('[A-Z]{2}', values['state']):
        raise PayloadError('invalid_state')
    try:
        values['latitude'] = float(payload.get('latitude'))
        values['longitude'] = float(payload.get('longitude'))
        values['num_courts'] = int(payload.get('num_courts') or 1)
    except (ValueError, TypeError):
        raise PayloadError('invalid_venue_location')
    if not (18 <= values['latitude'] <= 72 and -180 <= values['longitude'] <= -66 and 1 <= values['num_courts'] <= 100):
        raise PayloadError('invalid_venue_location')
    values['indoor'] = _boolean(payload.get('indoor', False), field='indoor')
    return values


def _duplicate_location_response(rows):
    public = [row for row in rows if not row.pending_submission]
    return jsonify({'error': 'venue_location_already_listed', 'message': 'A matching location already exists. Choose the existing venue or contact support about a pending location.', 'items': [{'id': row.id, 'name': row.name, 'address': row.address, 'city': row.city, 'state': row.state} for row in public]}), 409


@businesses_bp.post('/businesses/claims/new-location')
@rate_limit(5, 3600)
@login_required
def submit_missing_venue_claim():
    from backend.services.venue_locations import possible_venue_duplicates, lock_venue_submission_review
    try:
        payload = _request_object()
        values = _validated_venue_location(payload.get('location'))
        _require_authorized_attestation(payload)
        _claim_submission_evidence(payload)
        _text(payload.get('role'), field='role', maximum=80, required=True)
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    lock_venue_submission_review()
    duplicates = possible_venue_duplicates(values)
    if duplicates:
        return _duplicate_location_response(duplicates)
    try:
        court = Court(**values, county_slug='venue_submission', verified=False, pending_submission=True)
        db.session.add(court)
        db.session.flush()
        response = _submit_business_claim({**payload, 'court_id': court.id})
        if isinstance(response, tuple) and response[1] >= 400:
            db.session.rollback()
        return response
    except Exception:
        db.session.rollback()
        raise


@businesses_bp.patch('/businesses/<int:business_id>/location')
@login_required
@rate_limit(10, 3600)
def update_pending_venue_location(business_id):
    from backend.services.venue_locations import possible_venue_duplicates, lock_venue_submission_review
    business, error = _owned_profile_or_error(business_id)
    if error:
        return error
    if g.business_role != 'owner' or not business.court.pending_submission:
        return jsonify({'error': 'pending_location_owner_only'}), 403
    precondition = content_precondition_error(business)
    if precondition:
        return precondition
    try:
        values = _validated_venue_location(_request_object())
        lock_venue_submission_review()
        duplicates = possible_venue_duplicates(values, exclude_id=business.court_id)
        if duplicates:
            return _duplicate_location_response(duplicates)
        before = business_snapshot(business)
        court = business.court
        old_location = {key: getattr(court, key) for key in values}
        if business.name == court.name:
            business.name = values['name']
        for key, value in values.items():
            setattr(court, key, value)
        record_revision(business, actor_user_id=g.current_user.id, action='pending_location_update', before_snapshot=before, sensitive=False)
        from backend.services.business_governance import record_governance_event
        if old_location != values:
            record_governance_event(business, 'pending_location_update', actor_user_id=g.current_user.id, details={'before':old_location, 'after':values})
        db.session.commit()
    except PayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    return jsonify(_profile_payload(business, owner=True, manager_role='owner'))


@businesses_bp.post('/businesses/claims')
@rate_limit(10, 3600)
@login_required
def submit_business_claim():
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    return _submit_business_claim(payload)


def _submit_business_claim(payload):
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
    precondition = content_precondition_error(business)
    if precondition:
        return precondition
    was_public = business_is_public(business)
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
            was_public=was_public,
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


def _validate_schedule_services(business, values):
    from backend.services.business_schedule import overrides_dict
    ids = {values.get('offering_id')} - {None}
    for entries in overrides_dict(values.get('occurrence_overrides')).values():
        for changes in entries.values():
            if changes.get('offering_id') is not None:
                ids.add(changes['offering_id'])
    allowed = {item.id for item in business.offerings}
    if not ids.issubset(allowed):
        raise PayloadError('service_not_at_this_venue')


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
        if model is BusinessScheduleItem:
            _validate_schedule_services(business, values)
            checked = values.pop('availability_checked', False)
            if values.get('spots_remaining') is None:
                values['availability_updated_at'] = None
            elif checked or item.spots_remaining != values.get('spots_remaining'):
                values['availability_updated_at'] = utcnow()
            from backend.services.business_schedule import overrides_dict
            values['occurrence_overrides'] = json.dumps(_stamp_availability_overrides(overrides_dict(item.occurrence_overrides), overrides_dict(values['occurrence_overrides']), {**item.to_dict(), **values}), sort_keys=True)
        changed = any(getattr(item, key, None) != value for key, value in values.items() if key != 'source_updated_at')
        for key, value in values.items():
            if key != 'source_updated_at' or changed:
                setattr(item, key, value)
        retained.append(item)
    if model is BusinessOffering and by_id:
        from backend.services.business_schedule import overrides_dict
        for session in business.schedule_items:
            if session.offering_id in by_id:
                session.offering_id = None
            overrides = overrides_dict(session.occurrence_overrides)
            changed = False
            for entries in overrides.values():
                for changes in entries.values():
                    if changes.get('offering_id') in by_id:
                        changes['offering_id'] = None
                        changed = True
            if changed:
                session.occurrence_overrides = json.dumps(overrides, sort_keys=True)
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
    precondition = content_precondition_error(business)
    if precondition:
        return precondition
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


def _validated_schedule_item(raw, position, *, validate_overrides=True):
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
    overrides = _validated_occurrence_overrides(raw, position) if validate_overrides else {}
    offering_id = raw.get('offering_id')
    if offering_id in ('', None):
        offering_id = None
    elif isinstance(offering_id, bool) or not str(offering_id).isdigit() or not 0 < int(offering_id) < 2147483648:
        raise PayloadError('invalid_offering_id')
    else:
        offering_id = int(offering_id)
    return {
        'offering_id': offering_id,
        'occurrence_overrides': json.dumps(overrides, sort_keys=True),
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
        'availability_checked': _boolean(raw.get('availability_checked', False), field='availability_checked'),
        'source_updated_at': utcnow(),
        'skill_level': _text(
            raw.get('skill_level') or 'all', field='skill_level', maximum=40,
        ),
        'booking_url': _external_url(raw.get('booking_url'), field='booking_url'),
        'active': _boolean(raw.get('active', True), field='active'),
        'sort_order': position,
    }


_OVERRIDE_FIELDS = {'title', 'kind', 'day_of_week', 'start_time', 'end_time', 'timezone', 'offering_id',
                    'skill_level', 'booking_url', 'capacity', 'spots_remaining', 'status',
                    'location_note', 'instructor', 'active', 'availability_checked', 'availability_updated_at'}


def _validated_occurrence_overrides(raw, position):
    from backend.services.business_schedule import overrides_dict
    supplied = raw.get('occurrence_overrides') or {}
    if not isinstance(supplied, (dict, str)):
        raise PayloadError('invalid_occurrence_overrides')
    if isinstance(supplied, str):
        try:
            supplied = json.loads(supplied)
        except ValueError:
            raise PayloadError('invalid_occurrence_overrides')
        if not isinstance(supplied, dict):
            raise PayloadError('invalid_occurrence_overrides')
    rules = supplied
    if set(rules) - {'dates', 'following'}:
        raise PayloadError('invalid_occurrence_overrides')
    result = {}
    for scope in ('dates', 'following'):
        entries = rules.get(scope) or {}
        if not isinstance(entries, dict) or len(entries) > 366:
            raise PayloadError('invalid_occurrence_overrides')
        clean = {}
        for key, changes in entries.items():
            try:
                date.fromisoformat(key)
            except (ValueError, TypeError):
                raise PayloadError('invalid_occurrence_date')
            if not isinstance(changes, dict) or set(changes) - _OVERRIDE_FIELDS:
                raise PayloadError('invalid_occurrence_fields')
            if scope == 'dates' and 'day_of_week' in changes:
                raise PayloadError('occurrence_day_cannot_change')
            normalized = _validated_schedule_item({**raw, **changes, 'occurrence_overrides': {}}, position, validate_overrides=False)
            clean[key] = {field: normalized[field] for field in changes if field != 'availability_updated_at'}
            if 'availability_updated_at' in changes:
                clean[key]['availability_updated_at'] = str(changes['availability_updated_at'] or '')
        if clean:
            result[scope] = clean
    from backend.services.business_schedule import occurrence_fields
    for key in set(result.get('dates', {})) | set(result.get('following', {})):
        effective = occurrence_fields({**raw, 'occurrence_overrides': result}, date.fromisoformat(key))
        _validated_schedule_item(effective, position, validate_overrides=False)
    return result


def _stamp_availability_overrides(before, after, base_row=None):
    """Only server-observed inventory edits may refresh the availability date."""
    stamped = {}
    for scope, entries in after.items():
        stamped[scope] = {}
        for day, changes in entries.items():
            previous = before.get(scope, {}).get(day, {})
            clean = {key: value for key, value in changes.items() if key not in {'availability_updated_at', 'availability_checked'}}
            changed_spots = 'spots_remaining' in clean and clean.get('spots_remaining') != previous.get('spots_remaining')
            if changes.get('availability_checked') or changed_spots:
                from backend.services.business_schedule import occurrence_fields
                effective = occurrence_fields({**(base_row or {}), 'occurrence_overrides': after}, date.fromisoformat(day))
                clean['availability_updated_at'] = utcnow().isoformat() + 'Z' if effective.get('spots_remaining') is not None else None
            elif previous.get('availability_updated_at'):
                clean['availability_updated_at'] = previous['availability_updated_at']
            stamped[scope][day] = clean
    return stamped


def _schedule_csv_header(value):
    header = str(value or '').lstrip('\ufeff').strip().lower()
    header = re.sub(r'[^a-z0-9]+', '_', header).strip('_')
    return _SCHEDULE_CSV_HEADER_ALIASES.get(header, header)


def _schedule_csv_date(value, field):
    raw = str(value or '').strip()
    if not raw:
        return ''
    for pattern in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            continue
    raise PayloadError(f'invalid_{field}')


def _schedule_csv_time(value):
    raw = str(value or '').strip().upper().replace('.', '')
    match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*([AP]M)?', raw)
    if not match:
        raise PayloadError('times_must_use_24_hour_hh_mm')
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if minute > 59 or (meridiem and not 1 <= hour <= 12):
        raise PayloadError('times_must_use_24_hour_hh_mm')
    if meridiem:
        hour = hour % 12 + (12 if meridiem == 'PM' else 0)
    elif hour > 23:
        raise PayloadError('times_must_use_24_hour_hh_mm')
    return f'{hour:02d}:{minute:02d}'


def _schedule_csv_boolean(value):
    raw = str(value or '').strip().lower()
    if not raw:
        return True
    if raw in {'1', 'true', 'yes', 'y', 'visible', 'show'}:
        return True
    if raw in {'0', 'false', 'no', 'n', 'hidden', 'hide'}:
        return False
    raise PayloadError('active_must_be_boolean')


def _schedule_csv_item(values, position, default_timezone):
    item = {key: str(value or '').strip() for key, value in values.items()}
    recurrence = re.sub(
        r'[^a-z0-9]+', '_', item.get('recurrence', '').lower(),
    ).strip('_')
    recurrence = {
        'date': 'dated', 'one_time': 'dated', 'one_off': 'dated',
        'specific_date': 'dated',
        'range': 'date_range', 'weekly_range': 'date_range',
        'weekly_within_a_date_range': 'date_range',
        'repeats_weekly': 'weekly',
    }.get(recurrence, recurrence)
    if not recurrence:
        recurrence = 'dated' if item.get('event_date') else (
            'date_range'
            if item.get('start_date') or item.get('end_date') else 'weekly'
        )
    item['recurrence'] = recurrence

    kind = re.sub(r'[^a-z0-9]+', '_', item.get('kind', '').lower()).strip('_')
    item['kind'] = {
        'openplay': 'open_play',
        'lesson_or_clinic': 'lesson',
        'facility_hours': 'hours',
    }.get(kind, kind or 'other')
    item['status'] = re.sub(
        r'[^a-z0-9]+', '_', item.get('status', '').lower(),
    ).strip('_') or 'scheduled'
    if item['status'] == 'canceled':
        item['status'] = 'cancelled'

    day = item.get('day_of_week', '').lower()
    if day and day not in _DAYS:
        matches = [candidate for candidate in _DAYS if candidate.startswith(day[:3])]
        if len(matches) == 1:
            day = matches[0]
    item['day_of_week'] = day
    item['start_time'] = _schedule_csv_time(item.get('start_time'))
    item['end_time'] = _schedule_csv_time(item.get('end_time'))
    for field in ('event_date', 'start_date', 'end_date'):
        item[field] = _schedule_csv_date(item.get(field), field)
    item['timezone'] = item.get('timezone') or default_timezone
    item['active'] = _schedule_csv_boolean(item.get('active'))
    return _validated_schedule_item(item, position)


def _schedule_csv_payload(values):
    fields = (
        'title', 'kind', 'day_of_week', 'start_time', 'end_time', 'timezone',
        'recurrence', 'start_date', 'end_date', 'event_date', 'capacity',
        'spots_remaining', 'status', 'location_note', 'instructor',
        'skill_level', 'booking_url', 'active',
    )
    payload = {field: values.get(field) for field in fields}
    for field in ('start_date', 'end_date', 'event_date'):
        if payload[field] is not None:
            payload[field] = payload[field].isoformat()
    return payload


def _parse_schedule_csv(contents, *, default_timezone):
    if not isinstance(contents, str) or not contents.strip():
        raise ScheduleCsvError('schedule_csv_required')
    if len(contents.encode('utf-8')) > _SCHEDULE_CSV_MAX_BYTES:
        raise ScheduleCsvError('schedule_csv_too_large')
    try:
        reader = csv.DictReader(io.StringIO(contents, newline=''), strict=True)
        raw_headers = reader.fieldnames
    except csv.Error as exc:
        raise ScheduleCsvError('schedule_csv_invalid_format') from exc
    if not raw_headers:
        raise ScheduleCsvError('schedule_csv_header_required')

    headers = []
    ignored = []
    seen = set()
    for raw_header in raw_headers:
        normalized = _schedule_csv_header(raw_header)
        if not normalized:
            headers.append(None)
            continue
        if normalized not in _SCHEDULE_CSV_FIELDS:
            ignored.append(str(raw_header or '').strip())
            headers.append(None)
            continue
        if normalized in seen:
            raise ScheduleCsvError('schedule_csv_duplicate_column')
        seen.add(normalized)
        headers.append(normalized)
    if not {'title', 'start_time', 'end_time'}.issubset(seen):
        raise ScheduleCsvError('schedule_csv_required_columns')

    items = []
    try:
        for row_number, raw_row in enumerate(reader, start=2):
            if raw_row.get(None):
                raise ScheduleCsvError(
                    'schedule_csv_invalid_format', row=row_number,
                )
            row = {
                header: raw_row.get(raw_header, '')
                for raw_header, header in zip(raw_headers, headers)
                if header is not None
            }
            if not any(str(value or '').strip() for value in row.values()):
                continue
            if len(items) >= _MAX_ITEMS:
                raise ScheduleCsvError('schedule_csv_too_many_rows')
            try:
                values = _schedule_csv_item(row, len(items), default_timezone)
            except PayloadError as exc:
                raise ScheduleCsvError(
                    'schedule_csv_row_invalid', row=row_number,
                ) from exc
            items.append(_schedule_csv_payload(values))
    except csv.Error as exc:
        raise ScheduleCsvError('schedule_csv_invalid_format') from exc
    if not items:
        raise ScheduleCsvError('schedule_csv_empty')
    return items, sorted(set(filter(None, ignored)))


@businesses_bp.post('/businesses/<int:business_id>/schedule/import-preview')
@rate_limit(30, 3600)
@login_required
def preview_business_schedule_csv(business_id):
    actor_error = _lock_current_actor()
    if actor_error:
        return actor_error
    _, err = _owned_profile_or_error(business_id)
    if err:
        return err
    try:
        payload = _request_object()
        timezone = _text(
            payload.get('timezone') or business.venue_timezone() or 'UTC', field='timezone', maximum=64,
            required=True,
        )
        ZoneInfo(timezone)
        items, ignored = _parse_schedule_csv(
            payload.get('csv'), default_timezone=timezone,
        )
    except ZoneInfoNotFoundError:
        return jsonify({'error': 'invalid_timezone'}), 400
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    except ScheduleCsvError as exc:
        body = {'error': exc.code}
        if exc.row is not None:
            body['row'] = exc.row
            if exc.__cause__ is not None:
                body['detail'] = str(exc.__cause__)
        return jsonify(body), 400
    return jsonify({
        'items': items,
        'count': len(items),
        'ignored_columns': ignored,
    })


def _schedule_booking_urls(rows):
    from backend.services.business_schedule import overrides_dict
    urls = set()
    for row in rows:
        if row.get('booking_url'):
            urls.add(row['booking_url'])
        for rules in overrides_dict(row.get('occurrence_overrides')).values():
            for changes in rules.values():
                if changes.get('booking_url'):
                    urls.add(changes['booking_url'])
    return urls


@businesses_bp.get('/businesses/<int:business_id>/agenda')
def business_agenda(business_id):
    from backend.services.business_schedule import dated_occurrences, local_today
    from backend.services.business_visibility import hide_unsafe_public_links
    business, error = _profile_or_404(business_id)
    if error:
        return error
    viewer = optional_current_user()
    role = business_access_role(business, viewer.id) if viewer else None
    draft = request.args.get('draft') == '1'
    if draft and not role:
        return jsonify({'error': 'business_manager_only'}), 403
    if not draft and not business_is_public(business):
        return jsonify({'error': 'business_not_found'}), 404
    snapshot = business_snapshot(business) if draft or not business.reviewed_public_snapshot else business.reviewed_snapshot_dict()
    timezone = business.venue_timezone() if draft else snapshot.get('profile', {}).get('timezone') or business.venue_timezone()
    today = local_today(timezone)
    try:
        start = date.fromisoformat(request.args.get('from') or today.isoformat())
        end = date.fromisoformat(request.args.get('to') or (start + timedelta(days=6)).isoformat())
    except ValueError:
        return jsonify({'error': 'invalid_schedule_range'}), 400
    if end < start or (end - start).days > 92:
        return jsonify({'error': 'invalid_schedule_range'}), 400
    rows = snapshot.get('schedule', [])
    if not draft:
        rows = hide_unsafe_public_links(business, {'schedule': rows})['schedule']
    items = dated_occurrences(rows, start, end, include_hidden=draft)
    payload = {'items': items, 'from': start.isoformat(), 'to': end.isoformat(), 'timezone': timezone, 'draft': draft}
    if draft:
        from backend.services.business_governance import content_version
        payload['content_version'] = content_version(business)
    return jsonify(payload)


@businesses_bp.route('/businesses/<int:business_id>/schedule/<int:item_id>/occurrences/<on>', methods=['PATCH', 'DELETE'])
@rate_limit(60, 3600)
@login_required
def edit_business_occurrence(business_id, item_id, on):
    from backend.services.business_schedule import occurrence_fields, occurs_on, overrides_dict
    business, error = _owned_profile_or_error(business_id)
    if error:
        return error
    precondition = content_precondition_error(business)
    if precondition:
        return precondition
    item = next((row for row in business.schedule_items if row.id == item_id), None)
    if item is None:
        return jsonify({'error': 'schedule_item_not_found'}), 404
    try:
        on_date = date.fromisoformat(on)
        payload = _request_object()
        scope = payload.get('scope', 'this_date')
        if scope not in {'this_date', 'following_dates'} or (scope == 'following_dates' and item.recurrence == 'dated'):
            raise PayloadError('invalid_occurrence_scope')
        row = item.to_dict()
        rules = overrides_dict(row.get('occurrence_overrides'))
        key = 'dates' if scope == 'this_date' else 'following'
        removing_existing_rule = request.method == 'DELETE' and on in rules.get(key, {})
        if not removing_existing_rule and not occurs_on(occurrence_fields(row, on_date), on_date):
            raise PayloadError('occurrence_not_scheduled')
        before = business_snapshot(business)
        rules.setdefault(key, {})
        if request.method == 'DELETE':
            rules[key].pop(on, None)
        else:
            changes = payload.get('changes')
            if not isinstance(changes, dict) or not changes:
                raise PayloadError('occurrence_changes_required')
            rules[key][on] = {**rules[key].get(on, {}), **changes}
        normalized = _validated_schedule_item({**row, 'occurrence_overrides': rules}, item.sort_order)
        _validate_schedule_services(business, normalized)
        item.occurrence_overrides = json.dumps(_stamp_availability_overrides(overrides_dict(item.occurrence_overrides), overrides_dict(normalized['occurrence_overrides']), row), sort_keys=True)
        item.source_updated_at = utcnow()
        record_revision(business, actor_user_id=g.current_user.id, action='schedule_occurrence_update', before_snapshot=before,
                        sensitive=_schedule_booking_urls(before['schedule']) != _schedule_booking_urls([value.to_dict() for value in business.schedule_items]))
        db.session.commit()
    except (ValueError, PayloadError) as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    return jsonify(_profile_payload(business, owner=g.business_role == 'owner', manager_role=g.business_role))


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
    precondition = content_precondition_error(business)
    if precondition:
        return precondition
    before_snapshot = business_snapshot(business)
    try:
        payload = _request_object()
    except PayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        _replace_items(
            business, payload.get('items'),
            relationship='schedule_items', model=BusinessScheduleItem,
            validator=lambda raw, position: _validated_schedule_item({**raw, 'timezone': raw.get('timezone') or business.venue_timezone() or 'UTC'} if isinstance(raw, dict) else raw, position),
            error_name='schedule_item_not_found',
        )
        after_urls = _schedule_booking_urls([item.to_dict() for item in business.schedule_items])
        before_urls = _schedule_booking_urls(before_snapshot.get('schedule', []))
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
