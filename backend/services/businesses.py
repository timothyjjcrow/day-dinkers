"""Privileged business-claim review operations.

These helpers back the authenticated operator HTTP workflow and the trusted
emergency review CLI. HTTP transfer paths enforce operator RBAC, fresh MFA,
and two-person confirmation before calling them.
"""
from __future__ import annotations

import copy

from backend.app import db
from backend.models import (
    BUSINESS_CLAIM_VERIFICATION_METHODS,
    BusinessClaim,
    BusinessClaimReviewEvent,
    BusinessIntegrationRequest,
    BusinessProfile,
    BusinessProfileRevision,
    User,
    notify,
    utcnow,
)


class BusinessClaimReviewError(ValueError):
    pass


class BusinessIntegrationRequestError(ValueError):
    pass


def _reset_profile_for_ownership_transfer(business):
    """Remove the prior representative's untrusted draft and action links."""
    former_organization = business.organization
    business.name = business.court.name
    business.description = ''
    business.announcement = ''
    business.contact_email = ''
    business.contact_phone = ''
    business.hours = ''
    business.amenities = '[]'
    business.website_url = ''
    business.booking_url = ''
    business.membership_url = ''
    business.logo_url = ''
    business.logo_data = ''
    business.published = False
    business.governance_status = 'active'
    business.suspension_reason = ''
    business.suspended_at = None
    business.suspended_by = ''
    business.content_review_status = 'approved'
    business.content_reviewed_at = utcnow()
    business.offerings.clear()
    business.schedule_items.clear()
    # These rows can include a prior representative's private contact details
    # and vendor notes, so they cannot move to the new account either.
    business.integration_requests.clear()
    # Team access and revision snapshots belong to the former controller. A
    # shared multi-location organization keeps its other venues, but cannot
    # retain access to this transferred location.
    business.organization_id = None
    business.organization = None
    if former_organization is not None:
        has_other_locations = BusinessProfile.query.filter(
            BusinessProfile.organization_id == former_organization.id,
            BusinessProfile.id != business.id,
        ).first() is not None
        if not has_other_locations:
            db.session.delete(former_organization)
    BusinessProfileRevision.query.filter_by(business_id=business.id).delete(
        synchronize_session=False,
    )
    # Provider vault references/configuration must never cross an ownership
    # transfer. Optional import keeps the core business feature independent.
    try:
        from backend.integrations.models import (
            BusinessBookingEvent,
            BusinessCredentialSecret,
            BusinessIntegrationAuditEvent,
            BusinessIntegrationSyncRun,
            BusinessLinkHealthCheck,
            BusinessProviderConnection,
            BusinessScheduleOccurrence,
            BusinessWebhookReceipt,
        )
        connection_ids = [
            row[0] for row in db.session.query(BusinessProviderConnection.id)
            .filter_by(business_id=business.id).all()
        ]
        vault_public_ids = set()
        for connection in BusinessProviderConnection.query.filter_by(
            business_id=business.id,
        ).all():
            for reference in (
                connection.credential_ref,
                connection.webhook_secret_ref,
                connection.cursor_ref,
            ):
                if str(reference or '').startswith('vault://'):
                    vault_public_ids.add(str(reference)[len('vault://'):])
        if vault_public_ids:
            for secret in BusinessCredentialSecret.query.filter(
                BusinessCredentialSecret.public_id.in_(vault_public_ids),
                BusinessCredentialSecret.deleted_at.is_(None),
            ).with_for_update().all():
                secret.ciphertext = ''
                secret.deleted_at = utcnow()
        BusinessBookingEvent.query.filter_by(business_id=business.id).delete(
            synchronize_session=False,
        )
        BusinessScheduleOccurrence.query.filter_by(business_id=business.id).delete(
            synchronize_session=False,
        )
        BusinessIntegrationAuditEvent.query.filter_by(
            business_id=business.id,
        ).delete(synchronize_session=False)
        # Profile-level health checks have no connection_id, so deleting only
        # provider-linked rows would retain hashes and status for the former
        # owner's URLs. The business_id scope removes both kinds.
        BusinessLinkHealthCheck.query.filter_by(
            business_id=business.id,
        ).delete(synchronize_session=False)
        if connection_ids:
            BusinessWebhookReceipt.query.filter(
                BusinessWebhookReceipt.connection_id.in_(connection_ids),
            ).delete(synchronize_session=False)
            BusinessIntegrationSyncRun.query.filter(
                BusinessIntegrationSyncRun.connection_id.in_(connection_ids),
            ).delete(synchronize_session=False)
        BusinessProviderConnection.query.filter_by(business_id=business.id).delete(
            synchronize_session=False,
        )
    except (ImportError, AttributeError):
        pass


def _required_operator_text(value, field, maximum):
    value = str(value or '').strip()
    if not value:
        raise BusinessClaimReviewError(f'{field}_required')
    if len(value) > maximum:
        raise BusinessClaimReviewError(f'{field}_too_long')
    return value


def review_business_claim(
    claim_id,
    decision,
    *,
    reviewer_identifier,
    verification_method,
    review_note,
    confirm_transfer=False,
    two_person_approved=False,
):
    """Approve or reject a claim under row locks; caller commits the session.

    Verification confirms control of the listing, not an endorsement of the
    facility, its services, or its external links.
    """
    if decision not in {'approve', 'reject'}:
        raise BusinessClaimReviewError('decision_must_be_approve_or_reject')
    reviewer_identifier = _required_operator_text(
        reviewer_identifier, 'reviewer_identifier', 120,
    )
    review_note = _required_operator_text(review_note, 'review_note', 1000)
    verification_method = str(verification_method or '').strip().lower()
    if verification_method not in BUSINESS_CLAIM_VERIFICATION_METHODS:
        raise BusinessClaimReviewError('invalid_verification_method')
    try:
        claim_id = int(claim_id)
    except (TypeError, ValueError):
        raise BusinessClaimReviewError('invalid_claim_id')

    claim_snapshot = db.session.get(BusinessClaim, claim_id)
    if claim_snapshot is None:
        raise BusinessClaimReviewError('claim_not_found')
    claimant = (
        User.query.filter_by(id=claim_snapshot.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if claimant is None or claimant.deleted_at is not None:
        raise BusinessClaimReviewError('claimant_account_deleted')
    business = (
        BusinessProfile.query.filter_by(court_id=claim_snapshot.court_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if business is None:
        raise BusinessClaimReviewError('business_profile_not_found')
    if business.court is None or business.court.closed:
        raise BusinessClaimReviewError('court_closed')
    claim = (
        BusinessClaim.query.filter_by(id=claim_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if (
        claim is None
        or claim.user_id != claimant.id
        or claim.court_id != business.court_id
    ):
        raise BusinessClaimReviewError('claim_changed_retry')
    if claim.status != 'pending':
        raise BusinessClaimReviewError('claim_not_pending')

    now = utcnow()
    previous_owner_id = business.owner_id
    ownership_transferred = bool(
        decision == 'approve' and previous_owner_id != claim.user_id
    )
    if ownership_transferred and not confirm_transfer:
        raise BusinessClaimReviewError('ownership_transfer_requires_confirmation')
    if decision == 'approve':
        # A successful control check explicitly transfers the venue draft to
        # this claimant. Competing requests are closed in the same transaction.
        if ownership_transferred:
            _reset_profile_for_ownership_transfer(business)
        business.owner_id = claim.user_id
        business.claimant_role = claim.role
        business.claim_status = 'verified'
        business.verified_at = now
        business.governance_status = 'active'
        business.suspension_reason = ''
        business.suspended_at = None
        business.suspended_by = ''
        # Verification and publication are separate decisions. The verified
        # representative must inspect and explicitly publish the listing after
        # approval; the operator review CLI can never expose a draft.
        business.published = False
        # Control verification is not link/content approval. Any action URL,
        # uploaded/external logo, changed venue name, or contact endpoint that
        # predates verification enters the separate sensitive-content queue.
        from backend.services.business_governance import (
            business_snapshot,
            record_revision,
        )
        current_snapshot = business_snapshot(business)
        safe_snapshot = copy.deepcopy(current_snapshot)
        safe_profile = safe_snapshot.get('profile', {})
        safe_profile['name'] = business.court.name
        for field in (
            'contact_email', 'contact_phone', 'website_url', 'booking_url',
            'membership_url', 'logo_url', 'logo_data',
        ):
            safe_profile[field] = ''
        for item in safe_snapshot.get('offerings', []):
            item['booking_url'] = ''
        for item in safe_snapshot.get('schedule', []):
            item['booking_url'] = ''
        initial_sensitive = current_snapshot != safe_snapshot
        if initial_sensitive:
            record_revision(
                business,
                actor_user_id=claim.user_id,
                action='initial_verification_content',
                before_snapshot=safe_snapshot,
                sensitive=True,
            )
        else:
            business.content_review_status = 'approved'
            business.content_reviewed_at = now
        claim.business_id = business.id
        claim.status = 'verified'
        claim.reviewed_at = now
        claim.claimant_feedback = (
            'Control was verified. Review the draft and publish when ready.'
        )
        for other in BusinessClaim.query.filter(
            BusinessClaim.court_id == claim.court_id,
            BusinessClaim.id != claim.id,
            BusinessClaim.status != 'rejected',
        ).with_for_update().all():
            other.status = 'rejected'
            other.reviewed_at = now
            other.claimant_feedback = (
                'Another representative was verified for this venue.'
            )
            db.session.add(BusinessClaimReviewEvent(
                claim=other,
                reviewer_identifier=reviewer_identifier,
                verification_method=verification_method,
                decision='reject',
                review_note=(
                    f'Closed because claim {claim.id} was approved. {review_note}'
                )[:1000],
                ownership_transferred=False,
            ))
            notify(
                other.user_id,
                'business_claim',
                'Venue claim closed',
                f'Another representative was verified for {business.court.name}. '
                'Open Business Hub if you believe this needs another review.',
            )
    else:
        claim.status = 'rejected'
        claim.reviewed_at = now
        claim.claimant_feedback = review_note
        if business.owner_id == claim.user_id:
            business.claim_status = 'rejected'
            business.verified_at = None
            business.published = False

    review_event = BusinessClaimReviewEvent(
        claim=claim,
        reviewer_identifier=reviewer_identifier,
        verification_method=verification_method,
        decision=decision,
        review_note=review_note,
        ownership_transferred=ownership_transferred,
        previous_owner_id=(previous_owner_id if ownership_transferred else None),
    )
    db.session.add(review_event)
    if ownership_transferred:
        from backend.services.business_governance import record_governance_event
        record_governance_event(
            business,
            'ownership_transferred',
            operator_identifier=reviewer_identifier,
            details={
                'claim_id': claim.id,
                'previous_owner_id': previous_owner_id,
                'new_owner_id': claim.user_id,
                'authorization': (
                    'two_person_http'
                    if two_person_approved else 'emergency_direct_override'
                ),
            },
        )
    if decision == 'approve':
        notify(
            claim.user_id,
            'business_claim',
            'Venue claim approved',
            f'Your claim for {business.court.name} was approved. '
            'Review the draft and publish it when it is ready.',
        )
        if ownership_transferred and previous_owner_id:
            notify(
                previous_owner_id,
                'business_claim',
                'Venue listing control changed',
                f'Control of {business.court.name} was transferred after review. '
                'The listing was unpublished during the handoff.',
            )
    else:
        notify(
            claim.user_id,
            'business_claim',
            'Venue claim needs attention',
            f'We could not verify your control of {business.court.name}. '
            'Open Business Hub to review and resubmit the claim.',
        )

    db.session.flush()
    return {
        'claim': claim.to_dict(),
        'business': business.to_dict(include_inactive=True),
        'verification_meaning': (
            'control_confirmed_not_endorsed'
            if decision == 'approve' else 'control_not_confirmed'
        ),
        'ownership_transferred': ownership_transferred,
        'publication_requires_owner_review': decision == 'approve',
        'review_event': review_event.to_operator_dict(),
    }


def update_business_integration_request_status(
    request_id, status, *, operator_identifier, status_message,
):
    """Advance an integration request from the trusted operator workflow."""
    if status not in {'contacted', 'completed', 'declined'}:
        raise BusinessIntegrationRequestError('invalid_integration_request_status')
    operator_identifier = str(operator_identifier or '').strip()
    status_message = str(status_message or '').strip()
    if not operator_identifier:
        raise BusinessIntegrationRequestError('operator_identifier_required')
    if len(operator_identifier) > 120:
        raise BusinessIntegrationRequestError('operator_identifier_too_long')
    if not status_message:
        raise BusinessIntegrationRequestError('status_message_required')
    if len(status_message) > 1000:
        raise BusinessIntegrationRequestError('status_message_too_long')
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        raise BusinessIntegrationRequestError('invalid_integration_request_id')
    item = (
        BusinessIntegrationRequest.query.filter_by(id=request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if item is None:
        raise BusinessIntegrationRequestError('integration_request_not_found')
    if item.status == status:
        return item.to_operator_dict()
    if item.status in {'completed', 'declined'}:
        raise BusinessIntegrationRequestError('integration_request_is_closed')
    item.status = status
    item.handled_by = operator_identifier
    item.status_message = status_message
    item.status_changed_at = utcnow()
    status_labels = {
        'contacted': 'Integration request update',
        'completed': 'Integration request handled',
        'declined': 'Integration request closed',
    }
    notify(
        item.requested_by_id,
        'business_integration',
        status_labels[status],
        status_message,
    )
    db.session.flush()
    return item.to_operator_dict()
