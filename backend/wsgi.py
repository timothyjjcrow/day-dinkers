"""WSGI entrypoint used by Render/Gunicorn."""
import os

from backend.app import create_app
from backend.services.court_importer import import_county_slug, list_county_files
from backend.services.court_payloads import DEFAULT_COUNTY_SLUG
from backend.services.court_seeder import seed_courts


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _parse_sync_counties(raw_value):
    raw = str(raw_value or '').strip().lower()
    if not raw:
        return [DEFAULT_COUNTY_SLUG]
    if raw in {'all', '*'}:
        return list_county_files()
    counties = [item.strip().lower() for item in raw.split(',') if item.strip()]
    return counties or [DEFAULT_COUNTY_SLUG]


config_name = os.environ.get('FLASK_ENV', 'production')
app = create_app(config_name)

if _env_bool('AUTO_SEED_COURTS', False):
    with app.app_context():
        seeded = seed_courts()
        if seeded:
            print(f"Seeded {seeded} courts")


if _env_bool('AUTO_SYNC_COURTS', False):
    with app.app_context():
        counties = _parse_sync_counties(os.environ.get('COURT_SYNC_COUNTIES', DEFAULT_COUNTY_SLUG))
        total_created = 0
        total_updated = 0
        for county_slug in counties:
            try:
                result = import_county_slug(county_slug, commit=True)
                total_created += int(result.get('created', 0))
                total_updated += int(result.get('updated', 0))
                print(
                    f'Synced courts for {county_slug}: '
                    f'created={result.get("created", 0)} updated={result.get("updated", 0)}'
                )
            except FileNotFoundError:
                print(f'Skipped sync for {county_slug}: county data file not found')
            except Exception as exc:
                print(f'Failed to sync courts for {county_slug}: {exc}')
        print(f'Court sync summary: created={total_created} updated={total_updated}')
