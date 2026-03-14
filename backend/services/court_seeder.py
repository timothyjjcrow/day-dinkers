"""Seed the database with court datasets from all available states."""

from backend.app import db
from backend.models import Court
from backend.services.court_importer import (
    import_county_slug, import_state,
    list_county_files, list_state_dirs,
)
from backend.services.court_payloads import DEFAULT_COUNTY_SLUG


def seed_courts():
    """Insert court datasets only when the database is empty."""
    if Court.query.first():
        return 0

    total_created = 0
    states_seeded = []

    try:
        # Seed California from individual county files (backend/data/courts/ca/)
        counties = list_county_files()
        if counties:
            if DEFAULT_COUNTY_SLUG in counties:
                counties = [DEFAULT_COUNTY_SLUG] + [c for c in counties if c != DEFAULT_COUNTY_SLUG]
            for county_slug in counties:
                result = import_county_slug(county_slug, commit=False)
                total_created += int(result.get('created', 0))
            states_seeded.append('california')

        # Seed all other states from output/<state>/ directories
        for state_slug in list_state_dirs():
            if state_slug == 'california':
                continue
            try:
                result = import_state(state_slug, commit=False)
                created = int(result.get('created', 0))
                if created > 0:
                    total_created += created
                    states_seeded.append(state_slug)
            except (FileNotFoundError, ValueError) as exc:
                print(f'Skipped seeding {state_slug}: {exc}')

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    if states_seeded:
        print(f'Seeded {len(states_seeded)} states: {total_created} courts total')

    return total_created
