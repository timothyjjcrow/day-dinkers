"""Business verification, teams, revisions, safety reports, and operator RBAC."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

from flask import Blueprint, Response, current_app, g, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.email_delivery import (
    EmailDeliveryError,
    EmailDeliveryUnavailable,
    send_transactional_email,
)
from backend.models import (
    BUSINESS_CLAIM_VERIFICATION_METHODS,
    BusinessClaim,
    BusinessIntegrationRequest,
    BusinessOperatorAction,
    BusinessOrganizationMember,
    BusinessProfile,
    BusinessProfileReport,
    BusinessProfileRevision,
    BusinessStaffInvitation,
    BusinessVerificationEvidence,
    User,
    iso,
    notify,
    utcnow,
)
from backend.routes.auth import login_required, optional_current_user
from backend.security import rate_limit
from backend.services.business_governance import (
    ADMIN_ROLES,
    MANAGE_ROLES,
    business_access_role,
    business_snapshot,
    create_staff_invitation,
    ensure_organization,
    expire_pending_invitations,
    record_governance_event,
    record_revision,
    restore_snapshot,
    token_hash,
)
from backend.services.businesses import (
    BusinessClaimReviewError,
    BusinessIntegrationRequestError,
    _reset_profile_for_ownership_transfer,
    review_business_claim,
    update_business_integration_request_status,
)


business_governance_bp = Blueprint('business_governance', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_REPORT_ALIASES = {
    'incorrect_details': 'incorrect_info',
    'unsafe_link': 'safety',
}
_REPORT_CATEGORIES = {'broken_link', 'incorrect_info', 'ownership', 'safety', 'other'}
_TEAM_ROLES = {'owner', 'admin', 'editor', 'viewer'}
_INVITE_ROLES = {'admin', 'editor', 'viewer'}
_EVIDENCE_TYPES = set(BUSINESS_CLAIM_VERIFICATION_METHODS)
_MAX_LOGO_BYTES = 512 * 1024
_CONSUMER_EMAIL_DOMAINS = {
    'aol.com', 'gmail.com', 'googlemail.com', 'hotmail.com', 'icloud.com',
    'live.com', 'mail.com', 'me.com', 'msn.com', 'outlook.com', 'proton.me',
    'protonmail.com', 'yahoo.com', 'ymail.com',
}


class GovernancePayloadError(ValueError):
    pass


def _object_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise GovernancePayloadError('json_object_required')
    return payload


def _text(value, field, maximum, *, required=False):
    value = str(value or '').strip()
    if required and not value:
        raise GovernancePayloadError(f'{field}_required')
    if len(value) > maximum:
        raise GovernancePayloadError(f'{field}_too_long')
    return value


def _business(business_id, *, lock=False):
    query = BusinessProfile.query.filter_by(id=business_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return query.first()


def _role_error(business, allowed_roles):
    if business is None:
        return None, (jsonify({'error': 'business_not_found'}), 404)
    role = business_access_role(business, g.current_user.id)
    if role not in set(allowed_roles):
        return None, (jsonify({'error': 'business_manager_only'}), 403)
    return role, None


def _operator_error(*, admin=False, mutating=False):
    role = str(g.current_user.operator_role or '')
    allowed = {'admin'} if admin else {'reviewer', 'admin'}
    if role not in allowed:
        return jsonify({'error': 'business_operator_required'}), 403
    if mutating and not g.current_user.mfa_enabled:
        return jsonify({'error': 'operator_mfa_required'}), 403
    if mutating:
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        from backend.services.mfa import MFAError, verify_user_mfa
        try:
            valid, _ = verify_user_mfa(
                g.current_user, payload.get('mfa_code'), allow_recovery=False,
            )
        except MFAError:
            return jsonify({'error': 'mfa_unavailable'}), 503
        if not valid:
            return jsonify({'error': 'operator_mfa_required'}), 403
    return None


def _operator_identifier(user=None):
    user = user or g.current_user
    return f'user:{user.id}:{user.email}'[:120]


def _reauthentication_error(payload):
    user = g.current_user
    if not user.check_password(str(payload.get('current_password') or '')):
        return jsonify({'error': 'invalid_credentials'}), 403
    if user.mfa_enabled:
        from backend.services.mfa import MFAError, verify_user_mfa
        try:
            valid, _ = verify_user_mfa(
                user, payload.get('mfa_code'), allow_recovery=False,
            )
        except MFAError:
            return jsonify({'error': 'mfa_unavailable'}), 503
        if not valid:
            return jsonify({'error': 'invalid_mfa_code'}), 403
    return None


def _email_error_response(exc):
    current_app.logger.warning('Transactional business email was not accepted: %s', type(exc).__name__)
    if isinstance(exc, EmailDeliveryUnavailable):
        return jsonify({'error': 'email_delivery_unavailable'}), 503
    return jsonify({'error': 'email_delivery_failed'}), 502


def _official_business_domains(business):
    """Return independently listed HTTPS venue domains, never claimant input."""
    domains = set()
    # Court.website is maintained outside the business claim draft. A verified
    # listing's approved website may also serve as an operator-approved domain.
    candidates = [business.court.website if business.court else '']
    if business.verified and business.content_review_status == 'approved':
        candidates.append(business.website_url)
    for value in candidates:
        try:
            parsed = urlsplit(str(value or '').strip())
        except ValueError:
            continue
        if parsed.scheme != 'https' or not parsed.hostname:
            continue
        host = parsed.hostname.lower().rstrip('.')
        if host.startswith('www.'):
            host = host[4:]
        if host:
            domains.add(host)
    return domains


def _business_email_domain_matches(business, email):
    domain = str(email or '').rsplit('@', 1)[-1].lower().rstrip('.')
    if not domain or domain in _CONSUMER_EMAIL_DOMAINS:
        return False
    return any(
        domain == official or domain.endswith(f'.{official}')
        for official in _official_business_domains(business)
    )


def _email_code_hash(code):
    key = str(current_app.config['SECRET_KEY']).encode('utf-8')
    return hmac.new(
        key,
        f'business-email-challenge:v1:{code}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _send_email_evidence_challenge(evidence, business):
    raw_token = f'{secrets.randbelow(1_000_000):06d}'
    evidence.challenge_token_hash = _email_code_hash(raw_token)
    evidence.challenge_expires_at = utcnow() + timedelta(minutes=30)
    evidence.challenge_failed_attempts = 0
    evidence.challenge_locked_at = None
    evidence.status = 'challenge_sent'
    db.session.flush()
    base_url = str(current_app.config.get('PUBLIC_APP_URL') or '').rstrip('/') + '/'
    link = urljoin(
        base_url,
        f'#business-email-verification={business.id}:{evidence.id}:{raw_token}',
    )
    send_transactional_email(
        to=evidence.evidence_value,
        subject=f'Verify your email for {business.name}',
        text=(
            f'Use this one-time code to verify control evidence for '
            f'{business.name}:\n\n{raw_token}\n\nOr open {link}\n\n'
            'This code expires in 30 minutes. Verification confirms '
            'mailbox access and is still subject to operator review.'
        ),
        idempotency_key=(
            f'business-email-challenge:{evidence.id}:'
            f'{evidence.challenge_token_hash[:20]}'
        ),
    )


def _claim_operator_payload(claim):
    now = utcnow()
    closed = claim.status != 'pending'
    due_at = claim.due_at
    age_seconds = max(0, int((now - claim.created_at).total_seconds()))
    overdue = bool(not closed and due_at and due_at < now)
    if closed:
        sla_state = 'closed'
    elif overdue:
        sla_state = 'overdue'
    elif due_at and due_at - now <= timedelta(hours=12):
        sla_state = 'due_soon'
    else:
        sla_state = 'on_track'
    data = claim.to_dict()
    data.update({
        'claimant_user_id': claim.user_id,
        'claimant_name': claim.user.display_name if claim.user else None,
        'claimant_email': claim.user.email if claim.user else None,
        'business_name': claim.business.name if claim.business else None,
        'business_profile': (
            claim.business.to_dict(include_inactive=True)
            if claim.business else None
        ),
        'current_owner_user_id': claim.business.owner_id if claim.business else None,
        'ownership_transfer': bool(
            claim.business and claim.business.owner_id != claim.user_id
        ),
        'assigned_operator_id': claim.assigned_operator_id,
        'assigned_operator_identifier': claim.assigned_operator_identifier,
        'due_at': iso(due_at),
        'age_seconds': age_seconds,
        'overdue': overdue,
        'sla_state': sla_state,
        'evidence': [item.to_operator_dict() for item in claim.evidence],
        'review_history': [item.to_operator_dict() for item in claim.review_events],
    })
    return data


def _sla_payload(item, *, closed):
    now = utcnow()
    due_at = item.due_at
    overdue = bool(not closed and due_at and due_at < now)
    if closed:
        state = 'closed'
    elif overdue:
        state = 'overdue'
    elif due_at and due_at - now <= timedelta(hours=12):
        state = 'due_soon'
    else:
        state = 'on_track'
    return {
        'due_at': iso(due_at),
        'age_seconds': max(0, int((now - item.created_at).total_seconds())),
        'overdue': overdue,
        'sla_state': state,
    }


@business_governance_bp.get('/businesses/<int:business_id>/verification')
@login_required
def business_verification(business_id):
    business = _business(business_id)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    operator = g.current_user.operator_role in {'reviewer', 'admin'}
    own_claims = [
        item for item in business.claims if item.user_id == g.current_user.id
    ]
    role = business_access_role(business, g.current_user.id)
    if not operator and not role and not own_claims:
        return jsonify({'error': 'business_claimant_only'}), 403
    claims = business.claims if operator else own_claims
    return jsonify({
        'business_id': business.id,
        'claim_status': business.claim_status,
        'verified': business.verified,
        'verified_at': iso(business.verified_at),
        'claims': [
            {
                **(_claim_operator_payload(claim) if operator else claim.to_dict()),
                'evidence': [
                    item.to_operator_dict() if operator else item.to_owner_dict()
                    for item in claim.evidence
                ],
            }
            for claim in claims
        ],
        'pending_competing_claims': sum(
            1 for claim in business.claims
            if claim.status == 'pending' and claim.user_id != g.current_user.id
        ) if role or operator else 0,
        'verification_meaning': 'control_confirmed_not_endorsed',
    })


@business_governance_bp.post('/businesses/<int:business_id>/verification/evidence')
@rate_limit(8, 3600)
@login_required
def submit_verification_evidence(business_id):
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    claim = (
        BusinessClaim.query.filter_by(
            business_id=business.id, user_id=g.current_user.id,
        ).with_for_update().execution_options(populate_existing=True).first()
    )
    if claim is None:
        return jsonify({'error': 'business_claimant_only'}), 403
    if claim.status != 'pending':
        return jsonify({'error': 'claim_not_pending'}), 409
    try:
        payload = _object_payload()
        evidence_type = _text(
            payload.get('type'), 'evidence_type', 32, required=True,
        ).lower().replace('-', '_').replace(' ', '_')
        if evidence_type not in _EVIDENCE_TYPES:
            raise GovernancePayloadError('invalid_evidence_type')
        value = _text(payload.get('value'), 'evidence_value', 500, required=True)
        note = _text(payload.get('note'), 'note', 1000)
        if evidence_type == 'business_email':
            value = value.lower()
            if not _EMAIL_RE.fullmatch(value):
                raise GovernancePayloadError('invalid_business_email')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400

    evidence = BusinessVerificationEvidence(
        claim=claim,
        submitted_by_id=g.current_user.id,
        evidence_type=evidence_type,
        evidence_value=value,
        note=note,
        status='submitted',
    )
    db.session.add(evidence)
    if evidence_type == 'business_email':
        evidence.domain_match = _business_email_domain_matches(business, value)
    try:
        if evidence_type == 'business_email':
            _send_email_evidence_challenge(evidence, business)
        else:
            db.session.flush()
        db.session.commit()
    except (EmailDeliveryUnavailable, EmailDeliveryError) as exc:
        db.session.rollback()
        return _email_error_response(exc)
    return jsonify({'evidence': evidence.to_owner_dict()}), 201


@business_governance_bp.post(
    '/businesses/<int:business_id>/verification/evidence/<int:evidence_id>/verify'
)
@rate_limit(12, 3600)
@login_required
def verify_business_email_evidence(business_id, evidence_id):
    try:
        payload = _object_payload()
        raw_token = _text(payload.get('token'), 'token', 200, required=True)
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    evidence = (
        BusinessVerificationEvidence.query.join(BusinessClaim)
        .filter(
            BusinessVerificationEvidence.id == evidence_id,
            BusinessClaim.business_id == business_id,
            BusinessClaim.user_id == g.current_user.id,
        ).with_for_update().execution_options(populate_existing=True).first()
    )
    if evidence is None:
        return jsonify({'error': 'evidence_not_found'}), 404
    if evidence.evidence_type != 'business_email':
        return jsonify({'error': 'evidence_challenge_not_supported'}), 409
    if evidence.status == 'verified':
        return jsonify({'evidence': evidence.to_owner_dict()})
    if evidence.challenge_locked_at is not None:
        return jsonify({'error': 'verification_challenge_locked'}), 423
    if (
        evidence.status != 'challenge_sent'
        or not evidence.challenge_token_hash
        or not evidence.challenge_expires_at
        or evidence.challenge_expires_at <= utcnow()
    ):
        evidence.challenge_token_hash = ''
        evidence.challenge_expires_at = None
        db.session.commit()
        return jsonify({'error': 'verification_challenge_expired'}), 410
    if not hmac.compare_digest(
        evidence.challenge_token_hash, _email_code_hash(raw_token),
    ):
        evidence.challenge_failed_attempts = int(
            evidence.challenge_failed_attempts or 0
        ) + 1
        if evidence.challenge_failed_attempts >= 5:
            evidence.challenge_locked_at = utcnow()
            evidence.challenge_token_hash = ''
            evidence.challenge_expires_at = None
        db.session.commit()
        return jsonify({'error': 'invalid_verification_token'}), 400
    domain_match = bool(evidence.domain_match)
    evidence.status = 'verified' if domain_match else 'submitted'
    evidence.challenge_verified_at = utcnow()
    evidence.challenge_token_hash = ''
    evidence.challenge_expires_at = None
    db.session.commit()
    return jsonify({
        'evidence': evidence.to_owner_dict(),
        'domain_match': domain_match,
        'requires_manual_review': not domain_match,
    })


@business_governance_bp.post(
    '/businesses/<int:business_id>/verification/evidence/<int:evidence_id>/resend'
)
@rate_limit(5, 3600)
@login_required
def resend_business_email_evidence(business_id, evidence_id):
    evidence = (
        BusinessVerificationEvidence.query.join(BusinessClaim)
        .filter(
            BusinessVerificationEvidence.id == evidence_id,
            BusinessClaim.business_id == business_id,
            BusinessClaim.user_id == g.current_user.id,
        ).with_for_update().execution_options(populate_existing=True).first()
    )
    if evidence is None:
        return jsonify({'error': 'evidence_not_found'}), 404
    if evidence.evidence_type != 'business_email':
        return jsonify({'error': 'evidence_challenge_not_supported'}), 409
    if evidence.status in {'verified', 'accepted'}:
        return jsonify({'error': 'evidence_already_verified'}), 409
    try:
        _send_email_evidence_challenge(evidence, evidence.claim.business)
        db.session.commit()
    except (EmailDeliveryUnavailable, EmailDeliveryError) as exc:
        db.session.rollback()
        return _email_error_response(exc)
    return jsonify({'evidence': evidence.to_owner_dict()})


@business_governance_bp.get('/businesses/<int:business_id>/team')
@login_required
def business_team(business_id):
    business = _business(business_id, lock=True)
    role, error = _role_error(business, _TEAM_ROLES)
    if error:
        return error
    organization = ensure_organization(business, g.current_user.id)
    expire_pending_invitations(organization)
    db.session.commit()
    return jsonify({
        'organization': organization.to_dict(),
        'role': role,
        'members': [item.to_dict() for item in organization.members],
        'invitations': [
            item.to_dict() for item in organization.invitations
            if item.status == 'pending'
        ] if role in ADMIN_ROLES else [],
        'locations': [
            {
                'id': item.id,
                'name': item.name,
                'court_id': item.court_id,
                'owner_id': item.owner_id,
                'governance_status': item.governance_status,
            }
            for item in organization.businesses
        ],
    })


@business_governance_bp.post('/businesses/<int:business_id>/team/invitations')
@rate_limit(10, 86400)
@login_required
def invite_business_staff(business_id):
    business = _business(business_id, lock=True)
    role, error = _role_error(business, ADMIN_ROLES)
    if error:
        return error
    try:
        payload = _object_payload()
        email = _text(payload.get('email'), 'email', 255, required=True).lower()
        invited_role = _text(payload.get('role'), 'role', 20, required=True).lower()
        if not _EMAIL_RE.fullmatch(email):
            raise GovernancePayloadError('invalid_email')
        if invited_role not in _INVITE_ROLES:
            raise GovernancePayloadError('invalid_team_role')
        if invited_role == 'admin' and role != 'owner':
            raise GovernancePayloadError('business_owner_required_for_admin_invite')
        if email == g.current_user.email:
            raise GovernancePayloadError('cannot_invite_self')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    organization = ensure_organization(business, g.current_user.id)
    if any(
        item.user and item.user.email.lower() == email
        for item in organization.members
    ):
        return jsonify({'error': 'already_team_member'}), 409
    invitation, raw_token = create_staff_invitation(
        organization,
        invited_by_id=g.current_user.id,
        email=email,
        role=invited_role,
    )
    try:
        db.session.flush()
        base_url = str(current_app.config.get('PUBLIC_APP_URL') or '').rstrip('/') + '/'
        link = urljoin(base_url, f'#business-invitation={raw_token}')
        send_transactional_email(
            to=email,
            subject=f'Join {organization.name} on Third Shot',
            text=(
                f'{g.current_user.display_name} invited you to help manage '
                f'{organization.name} as {invited_role}.\n\nAccept: {link}\n\n'
                'This one-time invitation expires in 7 days.'
            ),
            idempotency_key=(
                f'business-team-invitation:{invitation.id}:'
                f'{invitation.token_hash[:20]}'
            ),
        )
        record_governance_event(
            business,
            'team_invitation_sent',
            actor_user_id=g.current_user.id,
            details={'invitation_id': invitation.id, 'role': invited_role},
        )
        db.session.commit()
    except (EmailDeliveryUnavailable, EmailDeliveryError) as exc:
        db.session.rollback()
        return _email_error_response(exc)
    return jsonify({'invitation': invitation.to_dict()}), 201


@business_governance_bp.post('/business-invitations/<string:raw_token>/accept')
@rate_limit(12, 3600)
@login_required
def accept_business_staff_invitation(raw_token):
    invitation = (
        BusinessStaffInvitation.query.filter_by(token_hash=token_hash(raw_token))
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if invitation is None:
        return jsonify({'error': 'invitation_not_found'}), 404
    if invitation.status != 'pending':
        return jsonify({'error': 'invitation_not_pending'}), 409
    if invitation.expires_at <= utcnow():
        invitation.status = 'expired'
        db.session.commit()
        return jsonify({'error': 'invitation_expired'}), 410
    if invitation.email.lower() != g.current_user.email.lower():
        return jsonify({'error': 'invitation_email_mismatch'}), 403
    member = BusinessOrganizationMember.query.filter_by(
        organization_id=invitation.organization_id,
        user_id=g.current_user.id,
    ).first()
    if member is None:
        member = BusinessOrganizationMember(
            organization_id=invitation.organization_id,
            user_id=g.current_user.id,
            role=invitation.role,
        )
        db.session.add(member)
    else:
        member.role = invitation.role
    invitation.status = 'accepted'
    invitation.accepted_by_id = g.current_user.id
    invitation.accepted_at = utcnow()
    invitation.token_hash = token_hash(f'consumed:{invitation.id}:{utcnow().isoformat()}')
    for business in invitation.organization.businesses:
        record_governance_event(
            business,
            'team_invitation_accepted',
            actor_user_id=g.current_user.id,
            details={'member_user_id': g.current_user.id, 'role': member.role},
        )
    db.session.commit()
    return jsonify({
        'accepted': True,
        'member': member.to_dict(),
        'organization': invitation.organization.to_dict(),
    })


def _team_member_target(business_id, member_id):
    business = _business(business_id, lock=True)
    role, error = _role_error(business, ADMIN_ROLES)
    if error:
        return None, None, None, error
    organization = ensure_organization(business, g.current_user.id)
    member = BusinessOrganizationMember.query.filter_by(
        id=member_id, organization_id=organization.id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if member is None:
        return business, organization, role, (
            jsonify({'error': 'team_member_not_found'}), 404
        )
    return business, organization, role, member


@business_governance_bp.patch('/businesses/<int:business_id>/team/<int:member_id>')
@rate_limit(30, 3600)
@login_required
def update_business_team_member(business_id, member_id):
    business, organization, actor_role, result = _team_member_target(
        business_id, member_id,
    )
    if isinstance(result, tuple):
        return result
    member = result
    try:
        payload = _object_payload()
        new_role = _text(payload.get('role'), 'role', 20, required=True).lower()
        if new_role not in _INVITE_ROLES:
            raise GovernancePayloadError('invalid_team_role')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    if member.role == 'owner':
        return jsonify({'error': 'transfer_owner_before_role_change'}), 409
    if actor_role != 'owner' and (member.role == 'admin' or new_role == 'admin'):
        return jsonify({'error': 'business_owner_required'}), 403
    member.role = new_role
    for location in organization.businesses:
        record_governance_event(
            location,
            'team_role_changed',
            actor_user_id=g.current_user.id,
            details={'member_user_id': member.user_id, 'role': new_role},
        )
    db.session.commit()
    return jsonify({'member': member.to_dict()})


@business_governance_bp.delete('/businesses/<int:business_id>/team/<int:member_id>')
@rate_limit(20, 3600)
@login_required
def remove_business_team_member(business_id, member_id):
    business, organization, actor_role, result = _team_member_target(
        business_id, member_id,
    )
    if isinstance(result, tuple):
        return result
    member = result
    if member.role == 'owner':
        return jsonify({'error': 'transfer_owner_before_removal'}), 409
    if actor_role != 'owner' and member.role == 'admin':
        return jsonify({'error': 'business_owner_required'}), 403
    removed_user_id = member.user_id
    db.session.delete(member)
    for location in organization.businesses:
        record_governance_event(
            location,
            'team_member_removed',
            actor_user_id=g.current_user.id,
            details={'member_user_id': removed_user_id},
        )
    db.session.commit()
    return jsonify({'removed': True, 'member_id': member_id})


@business_governance_bp.delete(
    '/businesses/<int:business_id>/team/invitations/<int:invitation_id>'
)
@rate_limit(20, 3600)
@login_required
def revoke_business_invitation(business_id, invitation_id):
    business = _business(business_id, lock=True)
    _, error = _role_error(business, ADMIN_ROLES)
    if error:
        return error
    organization = ensure_organization(business, g.current_user.id)
    invitation = BusinessStaffInvitation.query.filter_by(
        id=invitation_id, organization_id=organization.id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if invitation is None:
        return jsonify({'error': 'invitation_not_found'}), 404
    if invitation.status != 'pending':
        return jsonify({'error': 'invitation_not_pending'}), 409
    invitation.status = 'revoked'
    invitation.token_hash = token_hash(f'revoked:{invitation.id}:{utcnow().isoformat()}')
    db.session.commit()
    return jsonify({'revoked': True, 'invitation': invitation.to_dict()})


@business_governance_bp.post(
    '/businesses/<int:business_id>/organization/locations'
)
@rate_limit(10, 3600)
@login_required
def attach_business_location(business_id):
    business = _business(business_id, lock=True)
    role, error = _role_error(business, {'owner'})
    if error:
        return error
    try:
        payload = _object_payload()
        target_id = int(payload.get('business_id'))
    except (GovernancePayloadError, TypeError, ValueError):
        return jsonify({'error': 'business_id_required'}), 400
    if target_id == business.id:
        return jsonify({'error': 'location_already_attached'}), 409
    target = _business(target_id, lock=True)
    if target is None:
        return jsonify({'error': 'business_not_found'}), 404
    if target.owner_id != g.current_user.id:
        return jsonify({'error': 'both_locations_must_share_owner'}), 403
    organization = ensure_organization(business, g.current_user.id)
    if target.organization_id and target.organization_id != organization.id:
        return jsonify({'error': 'location_already_in_another_organization'}), 409
    target.organization = organization
    ensure_organization(target, g.current_user.id)
    record_governance_event(
        target,
        'location_attached',
        actor_user_id=g.current_user.id,
        details={'organization_id': organization.id, 'source_business_id': business.id},
    )
    db.session.commit()
    return jsonify({'organization': organization.to_dict()}), 201


@business_governance_bp.get('/businesses/<int:business_id>/revisions')
@login_required
def list_business_revisions(business_id):
    business = _business(business_id)
    role, error = _role_error(business, _TEAM_ROLES)
    if error:
        return error
    limit = min(max(request.args.get('limit', default=50, type=int), 1), 100)
    revisions = BusinessProfileRevision.query.filter_by(
        business_id=business.id,
    ).order_by(BusinessProfileRevision.id.desc()).limit(limit).all()
    include_snapshot = role in MANAGE_ROLES
    return jsonify({
        'items': [
            item.to_dict(include_snapshot=include_snapshot) for item in revisions
        ],
        'content_review_status': business.content_review_status,
    })


@business_governance_bp.post(
    '/businesses/<int:business_id>/revisions/<int:revision_id>/restore'
)
@rate_limit(10, 3600)
@login_required
def restore_business_revision(business_id, revision_id):
    business = _business(business_id, lock=True)
    role, error = _role_error(business, MANAGE_ROLES)
    if error:
        return error
    revision = BusinessProfileRevision.query.filter_by(
        id=revision_id, business_id=business.id,
    ).first()
    if revision is None:
        return jsonify({'error': 'revision_not_found'}), 404
    before = business_snapshot(business)
    try:
        restore_snapshot(business, revision.snapshot_dict())
        restored = record_revision(
            business,
            actor_user_id=g.current_user.id,
            action='revision_restore',
            before_snapshot=before,
            sensitive=True,
            restored_from_id=revision.id,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        code = str(exc) if isinstance(exc, ValueError) else 'revision_restore_failed'
        return jsonify({'error': code}), 400
    business_payload = business.to_dict(include_inactive=True)
    business_payload.update({
        'is_owner': role == 'owner',
        'is_manager': True,
        'manager_role': role,
    })
    return jsonify({
        'restored': True,
        'revision': restored.to_dict() if restored else None,
        'business': business_payload,
    })


def _decode_logo(payload):
    mime_type = str(payload.get('mime_type') or '').strip().lower()
    encoded = str(payload.get('data_base64') or payload.get('data') or '').strip()
    if encoded.startswith('data:'):
        match = re.fullmatch(
            r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)',
            encoded,
        )
        if not match:
            raise GovernancePayloadError('invalid_logo_data_url')
        declared = match.group(1)
        if mime_type and mime_type != declared:
            raise GovernancePayloadError('logo_mime_mismatch')
        mime_type = declared
        encoded = match.group(2)
    if mime_type not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise GovernancePayloadError('unsupported_logo_type')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise GovernancePayloadError('invalid_logo_base64')
    if not raw or len(raw) > _MAX_LOGO_BYTES:
        raise GovernancePayloadError('logo_too_large')
    valid_magic = (
        mime_type == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')
        or mime_type == 'image/jpeg' and raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9')
        or mime_type == 'image/webp' and len(raw) >= 12
        and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'
    )
    if not valid_magic:
        raise GovernancePayloadError('logo_mime_mismatch')
    canonical = base64.b64encode(raw).decode('ascii')
    return mime_type, raw, f'data:{mime_type};base64,{canonical}'


@business_governance_bp.route(
    '/businesses/<int:business_id>/logo', methods=['GET', 'POST', 'DELETE'],
)
@rate_limit(60, 3600)
def business_logo(business_id):
    business = _business(business_id, lock=request.method != 'GET')
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    if request.method == 'GET':
        viewer = optional_current_user()
        manager = bool(viewer and business_access_role(business, viewer.id))
        if not manager and not (
            business.published and business.verified
            and business.governance_status == 'active'
            and business.content_review_status == 'approved'
        ):
            return jsonify({'error': 'logo_not_found'}), 404
        if not business.logo_data:
            return jsonify({'error': 'logo_not_found'}), 404
        prefix, encoded = business.logo_data.split(',', 1)
        mime_type = prefix[5:].split(';', 1)[0]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            return jsonify({'error': 'logo_not_found'}), 404
        response = Response(raw, mimetype=mime_type)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = (
            'private, no-store' if manager and not business.published
            else 'public, max-age=300, must-revalidate'
        )
        response.set_etag(hashlib.sha256(raw).hexdigest())
        return response.make_conditional(request)

    user = optional_current_user()
    if user is None:
        return jsonify({'error': 'authentication_required'}), 401
    g.current_user = user
    role, error = _role_error(business, MANAGE_ROLES)
    if error:
        return error
    before = business_snapshot(business)
    if request.method == 'DELETE':
        business.logo_data = ''
        business.logo_url = ''
        action = 'logo_remove'
    else:
        try:
            payload = _object_payload()
            _mime, _raw, data_url = _decode_logo(payload)
        except GovernancePayloadError as exc:
            return jsonify({'error': str(exc)}), 400
        business.logo_data = data_url
        business.logo_url = f'/api/businesses/{business.id}/logo'
        action = 'logo_upload'
    revision = record_revision(
        business,
        actor_user_id=user.id,
        action=action,
        before_snapshot=before,
        sensitive=True,
    )
    db.session.commit()
    return jsonify({
        'uploaded': bool(business.logo_data),
        'logo_url': business.logo_url,
        'revision': revision.to_dict() if revision else None,
    })


@business_governance_bp.post('/businesses/<int:business_id>/transfer')
@rate_limit(5, 3600)
@login_required
def transfer_business_ownership(business_id):
    business = _business(business_id, lock=True)
    role, error = _role_error(business, {'owner'})
    if error:
        return error
    try:
        payload = _object_payload()
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    auth_error = _reauthentication_error(payload)
    if auth_error:
        return auth_error
    organization = ensure_organization(business, g.current_user.id)
    try:
        target_member_id = int(payload.get('member_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'member_id_required'}), 400
    target = BusinessOrganizationMember.query.filter_by(
        id=target_member_id, organization_id=organization.id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if target is None:
        return jsonify({'error': 'team_member_not_found'}), 404
    if target.user_id == business.owner_id:
        return jsonify({'error': 'already_business_owner'}), 409
    if target.role not in {'admin', 'owner'}:
        return jsonify({'error': 'new_owner_must_be_admin'}), 409
    previous_owner_id = business.owner_id
    locations = list(organization.businesses)
    if any(item.owner_id != previous_owner_id for item in locations):
        return jsonify({'error': 'organization_owner_invariant_violation'}), 409
    for location in locations:
        location.owner_id = target.user_id
    target.role = 'owner'
    for member in organization.members:
        if member.id != target.id and member.role == 'owner':
            member.role = 'admin'
    for location in locations:
        location.published = False
        record_governance_event(
            location,
            'organization_owner_transfer_by_owner',
            actor_user_id=g.current_user.id,
            details={
                'organization_id': organization.id,
                'previous_owner_id': previous_owner_id,
                'new_owner_id': target.user_id,
                'all_location_ids': [item.id for item in locations],
            },
        )
    notify(
        target.user_id,
        'business_claim',
        'Organization ownership transferred to you',
        f'You now own {organization.name} and all of its venue listings.',
    )
    db.session.commit()
    return jsonify({
        'transferred': True,
        'business_id': business.id,
        'organization_id': organization.id,
        'business_ids': [item.id for item in locations],
        'previous_owner_id': previous_owner_id,
        'owner_id': target.user_id,
        'scope': 'organization_all_locations',
    })


@business_governance_bp.delete('/businesses/<int:business_id>/claim')
@rate_limit(5, 3600)
@login_required
def release_business_claim(business_id):
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    payload = request.get_json(silent=True) or {}
    if business.owner_id == g.current_user.id:
        auth_error = _reauthentication_error(payload)
        if auth_error:
            return auth_error
        _reset_profile_for_ownership_transfer(business)
        business.governance_status = 'relinquished'
        business.claim_status = 'rejected'
        business.verified_at = None
        business.published = False
        for item in BusinessClaim.query.filter_by(
            business_id=business.id,
        ).with_for_update().all():
            if item.status != 'rejected':
                item.status = 'rejected'
                item.reviewed_at = utcnow()
                item.claimant_feedback = (
                    'The current owner released control of this venue listing.'
                )
        record_governance_event(
            business,
            'claim_relinquished',
            actor_user_id=g.current_user.id,
        )
    else:
        claim = BusinessClaim.query.filter_by(
            business_id=business.id, user_id=g.current_user.id,
        ).with_for_update().execution_options(populate_existing=True).first()
        if claim is None:
            return jsonify({'error': 'business_claimant_only'}), 403
        db.session.delete(claim)
    db.session.commit()
    return jsonify({'released': True, 'business_id': business.id})


@business_governance_bp.post('/businesses/<int:business_id>/reports')
@rate_limit(10, 3600)
@login_required
def create_business_report(business_id):
    business = _business(business_id)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    try:
        payload = _object_payload()
        category = _text(payload.get('category'), 'category', 32, required=True)
        category = _REPORT_ALIASES.get(category, category)
        if category not in _REPORT_CATEGORIES:
            raise GovernancePayloadError('invalid_report_category')
        details = _text(payload.get('details'), 'details', 2000, required=True)
        if len(details) < 10:
            raise GovernancePayloadError('report_details_too_short')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    report = BusinessProfileReport(
        business=business,
        reporter_id=g.current_user.id,
        category=category,
        details=details,
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({'report': report.to_dict()}), 201


@business_governance_bp.get('/businesses/<int:business_id>/reports')
@login_required
def list_business_reports(business_id):
    business = _business(business_id)
    role, error = _role_error(business, ADMIN_ROLES)
    if error:
        return error
    reports = BusinessProfileReport.query.filter_by(
        business_id=business.id,
    ).order_by(BusinessProfileReport.id.desc()).limit(100).all()
    return jsonify({'items': [item.to_dict() for item in reports], 'role': role})


def _new_operator_action(business, action_type, payload, *, claim=None):
    now = utcnow()
    for action in BusinessOperatorAction.query.filter_by(
        business_id=business.id, action_type=action_type, status='proposed',
    ).with_for_update().all():
        if action.expires_at <= now:
            action.status = 'expired'
        else:
            raise GovernancePayloadError('operator_action_already_pending')
    action = BusinessOperatorAction(
        business=business,
        claim=claim,
        action_type=action_type,
        payload=json.dumps(payload, sort_keys=True),
        proposed_by_id=g.current_user.id,
        expires_at=now + timedelta(hours=24),
    )
    db.session.add(action)
    record_governance_event(
        business,
        f'{action_type}_proposed',
        actor_user_id=g.current_user.id,
        operator_identifier=_operator_identifier(),
        details={'claim_id': claim.id if claim else None},
    )
    return action


@business_governance_bp.post('/businesses/<int:business_id>/suspend')
@rate_limit(10, 3600)
@login_required
def owner_suspend_business(business_id):
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    if business.owner_id != g.current_user.id:
        return jsonify({'error': 'business_owner_only'}), 403
    if business.governance_status == 'suspended':
        return jsonify({'error': 'business_already_suspended'}), 409
    try:
        payload = _object_payload()
        reason = _text(payload.get('reason'), 'reason', 500, required=True)
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    auth_error = _reauthentication_error(payload)
    if auth_error:
        return auth_error
    business.governance_status = 'suspended'
    business.suspension_reason = reason
    business.suspended_at = utcnow()
    business.suspended_by = f'owner:{g.current_user.id}'
    business.published = False
    record_governance_event(
        business,
        'business_self_suspended',
        actor_user_id=g.current_user.id,
        details={'reason': reason},
    )
    db.session.commit()
    return jsonify({'suspended': True, 'business': business.to_dict(include_inactive=True)})


@business_governance_bp.post('/operator/businesses/<int:business_id>/suspend')
@rate_limit(10, 3600)
@login_required
def propose_business_suspension(business_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    if business.governance_status == 'suspended':
        return jsonify({'error': 'business_already_suspended'}), 409
    try:
        payload = _object_payload()
        reason = _text(payload.get('reason'), 'reason', 500, required=True)
        action = _new_operator_action(business, 'suspend', {'reason': reason})
        db.session.commit()
    except GovernancePayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 409
    return jsonify({'action': action.to_dict(), 'requires_second_admin': True}), 202


@business_governance_bp.post('/businesses/<int:business_id>/resume')
@rate_limit(10, 3600)
@login_required
def resume_business(business_id):
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    if business.owner_id != g.current_user.id:
        return jsonify({'error': 'business_owner_only'}), 403
    if business.governance_status != 'suspended':
        return jsonify({'error': 'business_not_suspended'}), 409
    try:
        payload = _object_payload()
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    auth_error = _reauthentication_error(payload)
    if auth_error:
        return auth_error
    try:
        reason = _text(payload.get('reason'), 'reason', 500, required=True)
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    business.governance_status = 'active'
    business.suspension_reason = ''
    business.suspended_at = None
    business.suspended_by = ''
    business.published = False
    record_governance_event(
        business,
        'business_self_resumed',
        actor_user_id=g.current_user.id,
        details={'reason': reason, 'publication_requires_owner_review': True},
    )
    db.session.commit()
    return jsonify({'resumed': True, 'business': business.to_dict(include_inactive=True)})


@business_governance_bp.post('/operator/businesses/<int:business_id>/resume')
@rate_limit(10, 3600)
@login_required
def operator_resume_business(business_id):
    operator_error = _operator_error(admin=True, mutating=True)
    if operator_error:
        return operator_error
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    if business.governance_status != 'suspended':
        return jsonify({'error': 'business_not_suspended'}), 409
    payload = request.get_json(silent=True) or {}
    try:
        reason = _text(payload.get('reason'), 'reason', 500, required=True)
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    business.governance_status = 'active'
    business.suspension_reason = ''
    business.suspended_at = None
    business.suspended_by = ''
    business.published = False
    record_governance_event(
        business,
        'business_resumed_by_operator',
        actor_user_id=g.current_user.id,
        operator_identifier=_operator_identifier(),
        details={'reason': reason, 'publication_requires_owner_review': True},
    )
    db.session.commit()
    return jsonify({'resumed': True, 'business': business.to_dict(include_inactive=True)})


@business_governance_bp.post('/operator/businesses/<int:business_id>/revoke')
@rate_limit(10, 3600)
@login_required
def propose_business_revocation(business_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    business = _business(business_id, lock=True)
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    try:
        payload = _object_payload()
        reason = _text(payload.get('reason'), 'reason', 500, required=True)
        action = _new_operator_action(business, 'revoke', {'reason': reason})
        db.session.commit()
    except GovernancePayloadError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 409
    return jsonify({'action': action.to_dict(), 'requires_second_admin': True}), 202


@business_governance_bp.post('/operator/business/claims/<int:claim_id>/review')
@rate_limit(30, 3600)
@login_required
def operator_review_claim(claim_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    claim = BusinessClaim.query.filter_by(id=claim_id).with_for_update().first()
    if claim is None:
        return jsonify({'error': 'claim_not_found'}), 404
    try:
        payload = _object_payload()
        decision = _text(payload.get('decision'), 'decision', 16, required=True).lower()
        method = _text(
            payload.get('verification_method'), 'verification_method', 32,
            required=True,
        ).lower()
        note = _text(payload.get('review_note'), 'review_note', 1000, required=True)
        claimant_feedback = _text(
            payload.get('claimant_feedback') or note,
            'claimant_feedback', 1000, required=True,
        )
        if decision not in {'approve', 'reject'}:
            raise GovernancePayloadError('invalid_decision')
        if method not in _EVIDENCE_TYPES:
            raise GovernancePayloadError('invalid_verification_method')
        if decision == 'approve' and not any(
            item.status in {'verified', 'accepted'} for item in claim.evidence
        ):
            raise GovernancePayloadError('verified_evidence_required')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    claim.assigned_operator_id = claim.assigned_operator_id or g.current_user.id
    claim.assigned_operator_identifier = (
        claim.assigned_operator_identifier or _operator_identifier()
    )
    claim.claimant_feedback = claimant_feedback
    ownership_transfer = bool(
        decision == 'approve' and claim.business
        and claim.business.owner_id != claim.user_id
    )
    if ownership_transfer:
        try:
            action = _new_operator_action(
                claim.business,
                'claim_transfer',
                {
                    'decision': decision,
                    'verification_method': method,
                    'review_note': note,
                    'claimant_feedback': claimant_feedback,
                },
                claim=claim,
            )
            db.session.commit()
        except GovernancePayloadError as exc:
            db.session.rollback()
            return jsonify({'error': str(exc)}), 409
        return jsonify({
            'action': action.to_dict(),
            'requires_second_admin': True,
        }), 202
    try:
        result = review_business_claim(
            claim.id,
            decision,
            reviewer_identifier=_operator_identifier(),
            verification_method=method,
            review_note=note,
        )
        claim.claimant_feedback = claimant_feedback
        result['claim']['feedback'] = claimant_feedback
        db.session.commit()
    except BusinessClaimReviewError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 409
    return jsonify(result)


@business_governance_bp.post(
    '/operator/business/evidence/<int:evidence_id>/review'
)
@rate_limit(30, 3600)
@login_required
def operator_review_evidence(evidence_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    evidence = BusinessVerificationEvidence.query.filter_by(
        id=evidence_id,
    ).with_for_update().first()
    if evidence is None:
        return jsonify({'error': 'evidence_not_found'}), 404
    if evidence.reviewed_at is not None or evidence.status in {'accepted', 'rejected'}:
        return jsonify({'error': 'evidence_already_reviewed'}), 409
    try:
        payload = _object_payload()
        decision = _text(payload.get('decision'), 'decision', 16, required=True).lower()
        note = _text(payload.get('review_note'), 'review_note', 1000, required=True)
        if decision not in {'accept', 'reject'}:
            raise GovernancePayloadError('invalid_decision')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    evidence.status = 'accepted' if decision == 'accept' else 'rejected'
    evidence.reviewed_by = _operator_identifier()
    evidence.review_note = note
    evidence.reviewed_at = utcnow()
    evidence.challenge_token_hash = ''
    evidence.challenge_expires_at = None
    db.session.commit()
    return jsonify({'evidence': evidence.to_operator_dict()})


@business_governance_bp.post(
    '/operator/business/actions/<int:action_id>/confirm'
)
@rate_limit(20, 3600)
@login_required
def confirm_business_operator_action(action_id):
    operator_error = _operator_error(admin=True, mutating=True)
    if operator_error:
        return operator_error
    action = BusinessOperatorAction.query.filter_by(
        id=action_id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if action is None:
        return jsonify({'error': 'operator_action_not_found'}), 404
    if action.status != 'proposed':
        return jsonify({'error': 'operator_action_not_pending'}), 409
    if action.expires_at <= utcnow():
        action.status = 'expired'
        db.session.commit()
        return jsonify({'error': 'operator_action_expired'}), 410
    if action.proposed_by_id == g.current_user.id:
        return jsonify({'error': 'different_admin_required'}), 409
    payload = action.payload_dict()
    business = BusinessProfile.query.filter_by(
        id=action.business_id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if business is None:
        return jsonify({'error': 'business_not_found'}), 404
    result = None
    try:
        if action.action_type == 'claim_transfer':
            result = review_business_claim(
                action.claim_id,
                'approve',
                reviewer_identifier=(
                    f'{_operator_identifier()} confirming proposal '
                    f'by user:{action.proposed_by_id}'
                )[:120],
                verification_method=payload.get('verification_method'),
                review_note=payload.get('review_note'),
                confirm_transfer=True,
                two_person_approved=True,
            )
            if action.claim:
                action.claim.claimant_feedback = str(
                    payload.get('claimant_feedback') or ''
                )[:1000]
        elif action.action_type == 'suspend':
            business.governance_status = 'suspended'
            business.suspension_reason = str(payload.get('reason') or '')[:500]
            business.suspended_at = utcnow()
            business.suspended_by = _operator_identifier()
            business.published = False
            result = {'suspended': True}
        elif action.action_type == 'revoke':
            _reset_profile_for_ownership_transfer(business)
            business.governance_status = 'relinquished'
            business.claim_status = 'rejected'
            business.verified_at = None
            business.published = False
            for claim in business.claims:
                if claim.status != 'rejected':
                    claim.status = 'rejected'
                    claim.reviewed_at = utcnow()
                    claim.claimant_feedback = (
                        'Listing control was revoked after a safety review.'
                    )
            result = {'revoked': True}
        else:
            raise GovernancePayloadError('unsupported_operator_action')
        action.status = 'confirmed'
        action.confirmed_by_id = g.current_user.id
        action.confirmed_at = utcnow()
        record_governance_event(
            business,
            f'{action.action_type}_confirmed',
            actor_user_id=g.current_user.id,
            operator_identifier=_operator_identifier(),
            details={
                'action_id': action.id,
                'proposed_by_id': action.proposed_by_id,
                'reason': payload.get('reason'),
            },
        )
        db.session.commit()
    except (BusinessClaimReviewError, GovernancePayloadError) as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 409
    return jsonify({'action': action.to_dict(), 'result': result})


@business_governance_bp.post(
    '/operator/business/revisions/<int:revision_id>/review'
)
@rate_limit(30, 3600)
@login_required
def operator_review_revision(revision_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    revision = BusinessProfileRevision.query.filter_by(
        id=revision_id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if revision is None:
        return jsonify({'error': 'revision_not_found'}), 404
    if revision.review_status != 'pending':
        return jsonify({'error': 'revision_not_pending'}), 409
    latest_pending = BusinessProfileRevision.query.filter_by(
        business_id=revision.business_id, review_status='pending',
    ).order_by(
        BusinessProfileRevision.created_at.desc(),
        BusinessProfileRevision.id.desc(),
    ).first()
    if latest_pending is None or latest_pending.id != revision.id:
        return jsonify({
            'error': 'latest_pending_revision_required',
            'latest_revision_id': latest_pending.id if latest_pending else None,
        }), 409
    try:
        payload = _object_payload()
        decision = _text(payload.get('decision'), 'decision', 16, required=True).lower()
        note = _text(payload.get('review_note'), 'review_note', 1000, required=True)
        if decision not in {'approve', 'reject'}:
            raise GovernancePayloadError('invalid_decision')
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    business = BusinessProfile.query.filter_by(
        id=revision.business_id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if decision == 'reject':
        try:
            restore_snapshot(business, revision.previous_snapshot_dict())
        except ValueError as exc:
            db.session.rollback()
            return jsonify({'error': str(exc)}), 409
        revision.review_status = 'rejected'
    else:
        # The latest snapshot incorporates all earlier pending edits, so its
        # approval covers those exact antecedent revisions as one review unit.
        covered = BusinessProfileRevision.query.filter(
            BusinessProfileRevision.business_id == business.id,
            BusinessProfileRevision.review_status == 'pending',
            BusinessProfileRevision.id <= revision.id,
        ).all()
        for item in covered:
            item.review_status = 'approved'
            item.reviewer_identifier = _operator_identifier()
            item.review_note = (
                note if item.id == revision.id
                else f'Covered by approval of revision {revision.id}. {note}'[:1000]
            )
            item.reviewed_at = utcnow()
    revision.reviewer_identifier = _operator_identifier()
    revision.review_note = note
    revision.reviewed_at = utcnow()
    remaining_pending = BusinessProfileRevision.query.filter(
        BusinessProfileRevision.business_id == business.id,
        BusinessProfileRevision.review_status == 'pending',
        BusinessProfileRevision.id != revision.id,
    ).count()
    business.content_review_status = (
        'pending' if remaining_pending else 'approved'
    )
    business.content_reviewed_at = None if remaining_pending else utcnow()
    business.published = False
    record_governance_event(
        business,
        f'sensitive_change_{"approved" if decision == "approve" else "rejected"}',
        actor_user_id=g.current_user.id,
        operator_identifier=_operator_identifier(),
        details={'revision_id': revision.id, 'review_note': note},
    )
    db.session.commit()
    return jsonify({
        'revision': revision.to_dict(),
        'business': business.to_dict(include_inactive=True),
        'publication_requires_owner_review': True,
    })


def _assign_operator_item(item, payload):
    raw_user_id = payload.get('operator_user_id', g.current_user.id)
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise GovernancePayloadError('invalid_operator_user_id')
    operator = db.session.get(User, user_id)
    if operator is None or operator.deleted_at is not None \
            or operator.operator_role not in {'reviewer', 'admin'}:
        raise GovernancePayloadError('operator_not_found')
    item.assigned_operator_id = operator.id
    item.assigned_operator_identifier = _operator_identifier(operator)
    if 'due_at' in payload:
        value = _text(payload.get('due_at'), 'due_at', 40, required=True)
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            raise GovernancePayloadError('invalid_due_at')
        if parsed <= utcnow():
            raise GovernancePayloadError('due_at_must_be_future')
        item.due_at = parsed
    return operator


@business_governance_bp.post('/operator/business/claims/<int:item_id>/assign')
@business_governance_bp.post(
    '/operator/business/integration-requests/<int:item_id>/assign'
)
@business_governance_bp.post('/operator/business/reports/<int:item_id>/assign')
@rate_limit(60, 3600)
@login_required
def assign_business_queue_item(item_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    path = request.path
    if '/claims/' in path:
        model, serializer = BusinessClaim, _claim_operator_payload
    elif '/integration-requests/' in path:
        model, serializer = BusinessIntegrationRequest, lambda item: item.to_operator_dict()
    else:
        model, serializer = BusinessProfileReport, lambda item: item.to_dict(operator=True)
    item = model.query.filter_by(id=item_id).with_for_update().first()
    if item is None:
        return jsonify({'error': 'queue_item_not_found'}), 404
    try:
        payload = _object_payload()
        _assign_operator_item(item, payload)
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    db.session.commit()
    return jsonify({'item': serializer(item)})


@business_governance_bp.patch(
    '/operator/business/integration-requests/<int:request_id>'
)
@business_governance_bp.post(
    '/operator/business/integration-requests/<int:request_id>/status'
)
@rate_limit(30, 3600)
@login_required
def operator_update_integration_request(request_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    try:
        payload = _object_payload()
        status = _text(payload.get('status'), 'status', 20, required=True)
        message = _text(
            payload.get('status_message'), 'status_message', 1000, required=True,
        )
        item = BusinessIntegrationRequest.query.filter_by(
            id=request_id,
        ).with_for_update().first()
        if item is None:
            return jsonify({'error': 'integration_request_not_found'}), 404
        if 'operator_user_id' in payload or 'due_at' in payload:
            _assign_operator_item(item, payload)
        data = update_business_integration_request_status(
            item.id,
            status,
            operator_identifier=_operator_identifier(),
            status_message=message,
        )
        db.session.commit()
    except (GovernancePayloadError, BusinessIntegrationRequestError) as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400
    return jsonify({'request': data})


@business_governance_bp.patch('/operator/business/reports/<int:report_id>')
@business_governance_bp.post('/operator/business/reports/<int:report_id>/status')
@rate_limit(30, 3600)
@login_required
def operator_update_report(report_id):
    operator_error = _operator_error(mutating=True)
    if operator_error:
        return operator_error
    report = BusinessProfileReport.query.filter_by(
        id=report_id,
    ).with_for_update().execution_options(populate_existing=True).first()
    if report is None:
        return jsonify({'error': 'report_not_found'}), 404
    try:
        payload = _object_payload()
        status = _text(payload.get('status'), 'status', 20, required=True)
        if status not in {'reviewing', 'resolved', 'dismissed'}:
            raise GovernancePayloadError('invalid_report_status')
        message = _text(
            payload.get('status_message'), 'status_message', 1000, required=True,
        )
        if 'operator_user_id' in payload or 'due_at' in payload:
            _assign_operator_item(report, payload)
    except GovernancePayloadError as exc:
        return jsonify({'error': str(exc)}), 400
    if report.status in {'resolved', 'dismissed'} and report.status != status:
        return jsonify({'error': 'report_is_closed'}), 409
    report.status = status
    report.status_message = message
    report.handled_by = _operator_identifier()
    report.status_changed_at = utcnow()
    notify(
        report.reporter_id,
        'business_integration',
        'Business report update',
        message,
    )
    db.session.commit()
    return jsonify({'report': report.to_dict(operator=True)})


@business_governance_bp.get('/operator/business/queue')
@login_required
def operator_business_queue():
    operator_error = _operator_error()
    if operator_error:
        return operator_error
    claims = BusinessClaim.query.filter_by(status='pending').order_by(
        BusinessClaim.due_at.asc(), BusinessClaim.id.asc(),
    ).limit(200).all()
    # Only the latest pending snapshot can be acted on. Approval of that
    # snapshot covers its exact antecedents, and rejection restores its direct
    # predecessor, so showing older rows creates guaranteed 409s for operators.
    ranked_revisions = db.session.query(
        BusinessProfileRevision.id.label('revision_id'),
        func.row_number().over(
            partition_by=BusinessProfileRevision.business_id,
            order_by=(
                BusinessProfileRevision.created_at.desc(),
                BusinessProfileRevision.id.desc(),
            ),
        ).label('business_position'),
    ).filter(
        BusinessProfileRevision.review_status == 'pending',
    ).subquery()
    revisions = BusinessProfileRevision.query.join(
        ranked_revisions,
        ranked_revisions.c.revision_id == BusinessProfileRevision.id,
    ).filter(
        ranked_revisions.c.business_position == 1,
    ).order_by(
        BusinessProfileRevision.created_at.asc(),
        BusinessProfileRevision.id.asc(),
    ).limit(200).all()
    integration_requests = BusinessIntegrationRequest.query.filter(
        BusinessIntegrationRequest.status.in_(['submitted', 'contacted']),
    ).order_by(BusinessIntegrationRequest.due_at.asc()).limit(200).all()
    reports = BusinessProfileReport.query.filter(
        BusinessProfileReport.status.in_(['submitted', 'reviewing']),
    ).order_by(BusinessProfileReport.due_at.asc()).limit(200).all()
    actions = BusinessOperatorAction.query.filter_by(status='proposed').order_by(
        BusinessOperatorAction.expires_at.asc(),
    ).limit(200).all()

    connection_alerts = []
    try:
        from backend.integrations.models import (
            BusinessLinkHealthCheck,
            BusinessProviderConnection,
        )
        from backend.integrations import provider_registry
        from backend.integrations.errors import IntegrationError
        from backend.integrations.safety import stable_digest
        bad_connections = BusinessProviderConnection.query.filter(db.or_(
            BusinessProviderConnection.status.in_(['degraded', 'error']),
            BusinessProviderConnection.health_status.in_(
                ['degraded', 'unreachable', 'unsafe'],
            ),
        )).order_by(BusinessProviderConnection.updated_at.asc()).limit(200).all()
        for connection in bad_connections:
            item = connection.to_owner_dict()
            item['alert_type'] = 'connection'
            connection_alerts.append(item)
        latest_link_checks = db.session.query(
            func.max(BusinessLinkHealthCheck.id).label('id'),
        ).group_by(
            BusinessLinkHealthCheck.business_id,
            BusinessLinkHealthCheck.connection_id,
            BusinessLinkHealthCheck.link_kind,
            BusinessLinkHealthCheck.url_hash,
        ).subquery()
        failed_links = BusinessLinkHealthCheck.query.join(
            latest_link_checks,
            latest_link_checks.c.id == BusinessLinkHealthCheck.id,
        ).filter(
            BusinessLinkHealthCheck.status.in_(['broken', 'unreachable', 'unsafe']),
        ).order_by(BusinessLinkHealthCheck.checked_at.desc()).limit(200).all()

        def current_link_check(check):
            if check.connection_id:
                connection = check.connection
                if not connection or connection.status == 'disconnected':
                    return False
                try:
                    current_url = dict(
                        provider_registry.get(connection.provider_key).health_urls(
                            connection.config_dict(),
                        )
                    ).get(check.link_kind, '')
                except IntegrationError:
                    return False
            else:
                field = {
                    'profile_website': 'website_url',
                    'profile_booking': 'booking_url',
                    'profile_membership': 'membership_url',
                }.get(check.link_kind)
                current_url = getattr(check.business, field, '') \
                    if check.business and field else ''
            return bool(current_url and stable_digest(current_url) == check.url_hash)

        failed_links = [check for check in failed_links if current_link_check(check)]
        profile_link_alerts = []
        for check in failed_links:
            alert = {
                'alert_type': 'link_health',
                'business_name': check.business.name if check.business else None,
                **check.to_dict(),
            }
            if check.connection_id:
                connection_alerts.append(alert)
            else:
                profile_link_alerts.append(alert)
    except (ImportError, AttributeError):
        profile_link_alerts = []

    def operator_revision_payload(revision):
        def safe_snapshot(snapshot):
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            profile = dict(snapshot.get('profile') or {})
            profile['has_logo_upload'] = bool(profile.pop('logo_data', ''))
            return {
                'profile': profile,
                'offerings': list(snapshot.get('offerings') or []),
                'schedule': list(snapshot.get('schedule') or []),
            }

        return {
            **revision.to_dict(),
            'business_name': revision.business.name if revision.business else None,
            'before_snapshot': safe_snapshot(revision.previous_snapshot_dict()),
            'after_snapshot': safe_snapshot(revision.snapshot_dict()),
        }
    return jsonify({
        'claims': [_claim_operator_payload(item) for item in claims],
        'revisions': [operator_revision_payload(item) for item in revisions],
        'integration_requests': [
            {**item.to_operator_dict(), **_sla_payload(
                item, closed=item.status in {'completed', 'declined'},
            )}
            for item in integration_requests
        ],
        'reports': [
            {**item.to_dict(operator=True), **_sla_payload(
                item, closed=item.status in {'resolved', 'dismissed'},
            )}
            for item in reports
        ],
        'actions_requiring_second_admin': [item.to_dict() for item in actions],
        'connection_alerts': connection_alerts,
        'profile_link_alerts': profile_link_alerts,
    })
