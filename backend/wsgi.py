"""WSGI entrypoint used by Render/Gunicorn."""
import os
import threading

from backend.app import create_app
from backend.services.court_importer import (
    import_county_slug, import_state,
    list_county_files, list_state_dirs,
)
from backend.services.court_payloads import DEFAULT_COUNTY_SLUG
from backend.services.court_seeder import seed_courts, seed_missing_states


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


def _parse_sync_states(raw_value):
    raw = str(raw_value or '').strip().lower()
    if not raw or raw in {'all', '*'}:
        return list_state_dirs()
    return [item.strip().lower() for item in raw.split(',') if item.strip()]


config_name = os.environ.get('FLASK_ENV', 'production')
app = create_app(config_name)


def _background_seed_and_sync():
    """Run seeding and syncing in a background thread so gunicorn can bind the port immediately."""
    if _env_bool('AUTO_SEED_COURTS', False):
        with app.app_context():
            seeded = seed_courts()
            if seeded:
                print(f"Seeded {seeded} courts")
            else:
                added = seed_missing_states()
                if added:
                    print(f"Added {added} courts from new states")

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

    if _env_bool('AUTO_SYNC_STATES', False):
        with app.app_context():
            states = _parse_sync_states(os.environ.get('COURT_SYNC_STATES', ''))
            total_created = 0
            total_updated = 0
            for state_slug in states:
                try:
                    result = import_state(state_slug, commit=True)
                    total_created += int(result.get('created', 0))
                    total_updated += int(result.get('updated', 0))
                    print(
                        f'Synced courts for {state_slug}: '
                        f'created={result.get("created", 0)} updated={result.get("updated", 0)}'
                    )
                except FileNotFoundError:
                    print(f'Skipped state sync for {state_slug}: data not found')
                except Exception as exc:
                    print(f'Failed to sync courts for {state_slug}: {exc}')
            print(f'State sync summary: created={total_created} updated={total_updated}')

    print('Background seed/sync complete.')


_needs_background = (
    _env_bool('AUTO_SEED_COURTS', False)
    or _env_bool('AUTO_SYNC_COURTS', False)
    or _env_bool('AUTO_SYNC_STATES', False)
)
if _needs_background:
    threading.Thread(target=_background_seed_and_sync, daemon=True).start()
