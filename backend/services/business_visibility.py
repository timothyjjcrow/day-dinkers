"""One canonical rule for exposing a business profile to players.

Every public surface must use this module.  Keeping the rule here prevents a
court summary, business detail, or integration endpoint from accidentally
publishing a suspended or review-pending listing through a weaker predicate.
"""
from __future__ import annotations

from backend.models import BusinessProfile, Court
from sqlalchemy import or_


def public_business_filters():
    """SQLAlchemy filters shared by every public business query."""
    return (
        Court.closed.is_(False),
        Court.pending_submission.is_(False),
        BusinessProfile.published.is_(True),
        BusinessProfile.claim_status == 'verified',
        BusinessProfile.verified_at.is_not(None),
        BusinessProfile.governance_status == 'active',
        or_(BusinessProfile.content_review_status == 'approved',
            BusinessProfile.reviewed_public_snapshot != ''),
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
        and not business.court.pending_submission
        and business.published
        and business.claim_status == 'verified'
        and business.verified_at is not None
        and business.governance_status == 'active'
        and (business.content_review_status == 'approved'
             or bool(business.reviewed_snapshot_dict()))
    )


def hide_unsafe_public_links(business, data, checks=None):
    """Only a destination with a most-recent unsafe finding is held."""
    from backend.integrations.models import BusinessLinkHealthCheck
    from backend.integrations.safety import stable_digest
    if checks is None:
        checks = BusinessLinkHealthCheck.query.filter_by(business_id=business.id).order_by(
            BusinessLinkHealthCheck.checked_at.desc(), BusinessLinkHealthCheck.id.desc(),
        ).all()
    latest = {}
    for check in checks:
        latest.setdefault(check.url_hash, check.status)
    def safe(url):
        return '' if url and latest.get(stable_digest(url)) == 'unsafe' else url
    for key in ('website_url', 'booking_url', 'membership_url'):
        data[key] = safe(data.get(key))
    for key in ('offerings', 'schedule'):
        data[key] = [{**item, 'booking_url': safe(item.get('booking_url'))} for item in data.get(key, [])]
        if key == 'schedule':
            from backend.services.business_schedule import overrides_dict
            for item in data[key]:
                rules = overrides_dict(item.get('occurrence_overrides'))
                item['occurrence_overrides'] = {scope: {on: {**changes, **({'booking_url': safe(changes['booking_url'])} if 'booking_url' in changes else {})} for on, changes in entries.items()} for scope, entries in rules.items()}
    return data


def reviewed_profile_search_value(field):
    """Search the same version players can read, including during draft review."""
    from backend.app import db
    from sqlalchemy import JSON, case, cast, func
    held = BusinessProfile.reviewed_public_snapshot
    if db.engine.dialect.name == 'sqlite':
        reviewed = func.json_extract(held, f'$.profile.{field}')
    else:
        reviewed = cast(held, JSON)['profile'][field].as_string()
    return case((held == '', getattr(BusinessProfile, field)), else_=reviewed)
