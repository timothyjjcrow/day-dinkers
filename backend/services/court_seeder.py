"""Seed the database with county court datasets."""

from backend.app import db
from backend.models import Court
from backend.services.court_importer import import_county_slug, list_county_files
from backend.services.court_payloads import DEFAULT_COUNTY_SLUG


def seed_courts():
    """Insert county court datasets only when the database is empty."""
    if Court.query.first():
        return 0

    counties = list_county_files()
    if not counties:
        return 0

    # Keep default county first for deterministic startup data order.
    if DEFAULT_COUNTY_SLUG in counties:
        counties = [DEFAULT_COUNTY_SLUG] + [c for c in counties if c != DEFAULT_COUNTY_SLUG]

    total_created = 0
    try:
        for county_slug in counties:
            result = import_county_slug(county_slug, commit=False)
            total_created += int(result.get('created', 0))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return total_created
