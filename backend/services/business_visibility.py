"""One canonical rule for exposing a business profile to players.

Every public surface must use this module.  Keeping the rule here prevents a
court summary, business detail, or integration endpoint from accidentally
publishing a suspended or review-pending listing through a weaker predicate.
"""
from __future__ import annotations

from backend.models import BusinessProfile, Court


def public_business_filters():
    """SQLAlchemy filters shared by every public business query."""
    return (
        Court.closed.is_(False),
        BusinessProfile.published.is_(True),
        BusinessProfile.claim_status == 'verified',
        BusinessProfile.verified_at.is_not(None),
        BusinessProfile.governance_status == 'active',
        BusinessProfile.content_review_status == 'approved',
    )


def public_business_query():
    """Return a query containing only player-visible business profiles."""
    return BusinessProfile.query.join(Court).filter(*public_business_filters())


def business_is_public(business):
    """In-memory equivalent used when a manager may also view a private row."""
    return bool(
        business
        and business.court
        and not business.court.closed
        and business.published
        and business.claim_status == 'verified'
        and business.verified_at is not None
        and business.governance_status == 'active'
        and business.content_review_status == 'approved'
    )
