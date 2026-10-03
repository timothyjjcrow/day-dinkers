"""Directory visibility only: aliases do not rewrite court IDs or history."""
from sqlalchemy.orm import aliased

from backend.app import db
from backend.models import Court, CourtAlias


def discoverable_courts(query):
    """Apply before counting, sorting, filters and pagination."""
    aliases = db.session.query(CourtAlias.alias_court_id).filter(CourtAlias.active.is_(True))
    return query.filter(~Court.id.in_(aliases))


def court_search_aliases(canonical_query):
    """Search old names while retaining the canonical venue's filter facts.

    Reading on every request lets reviewed database additions take effect
    across all workers without a deployment or an application cache reset.
    """
    source = aliased(Court)
    eligible_ids = canonical_query.with_entities(Court.id).order_by(None)
    return (
        db.session.query(
            CourtAlias.canonical_court_id, source.name, source.city, source.address,
        )
        .join(source, source.id == CourtAlias.alias_court_id)
        .filter(CourtAlias.active.is_(True), CourtAlias.canonical_court_id.in_(eligible_ids))
        .all()
    )


def court_families(court_ids):
    """Canonical reads include active aliases; direct alias reads stay original.

    A requested alias is its own family, even when the same request also asks
    for its canonical court. No IDs or stored foreign keys are rewritten.
    """
    families = {court_id: {court_id} for court_id in court_ids}
    if families:
        rows = db.session.query(CourtAlias.alias_court_id, CourtAlias.canonical_court_id).filter(
            CourtAlias.active.is_(True), CourtAlias.canonical_court_id.in_(families),
        ).all()
        for alias, canonical in rows:
            families[canonical].add(alias)
    return families
