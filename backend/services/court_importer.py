"""Reusable court import/upsert helpers for county datasets."""

import json
from pathlib import Path

from backend.app import db
from backend.models import Court
from backend.services.california_county_bounds import (
    is_point_within_county_bounds,
    resolve_county_slug_for_point,
)
from backend.services.court_payloads import (
    DEFAULT_COUNTY_SLUG,
    apply_court_changes,
    normalize_county_slug,
    normalize_court_payload,
)

COURT_DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'courts' / 'ca'
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / 'output'


def _identity_key(court_data):
    name = str(court_data.get('name') or '').strip().lower()
    city = str(court_data.get('city') or '').strip().lower()
    return f'{name}|{city}'


def load_county_file(county_slug):
    slug = normalize_county_slug(county_slug, fallback=DEFAULT_COUNTY_SLUG)
    path = COURT_DATA_DIR / f'{slug}.json'
    if not path.exists():
        raise FileNotFoundError(f'County data file not found: {path}')
    with path.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError('County payload must be a JSON array of court objects.')
    return payload, slug


def list_county_files():
    if not COURT_DATA_DIR.exists():
        return []
    return sorted(path.stem for path in COURT_DATA_DIR.glob('*.json') if path.is_file())


def load_state_file(state_slug):
    """Load the combined courts file for a state from output/<state>/."""
    state_dir = OUTPUT_DIR / state_slug
    combined_path = state_dir / f'{state_slug}_courts.json'
    if combined_path.exists():
        with combined_path.open('r', encoding='utf-8') as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return payload
    # Fallback: load and merge all county files in the state directory
    if not state_dir.exists():
        raise FileNotFoundError(f'State data directory not found: {state_dir}')
    all_courts = []
    for county_file in sorted(state_dir.glob('*_courts.json')):
        if county_file.name == f'{state_slug}_courts.json':
            continue
        with county_file.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
        if isinstance(data, list):
            all_courts.extend(data)
    if not all_courts:
        raise FileNotFoundError(f'No court data files found in: {state_dir}')
    return all_courts


def list_state_dirs():
    """List state slugs that have data directories in output/."""
    if not OUTPUT_DIR.exists():
        return []
    return sorted(
        d.name for d in OUTPUT_DIR.iterdir()
        if d.is_dir() and not d.name.startswith('.')
    )


def import_courts_payload(courts_payload, county_slug=None, commit=True,
                          skip_bounds_check=False):
    """Validate and upsert courts. Returns import stats."""
    if not isinstance(courts_payload, list):
        raise ValueError('Court payload must be a list.')

    forced_county_slug = normalize_county_slug(county_slug, fallback='') if county_slug else ''
    normalized = []
    errors = []
    out_of_bounds_corrected = 0
    out_of_bounds_skipped = 0

    for idx, raw in enumerate(courts_payload):
        if not isinstance(raw, dict):
            errors.append(f'Item #{idx + 1}: expected object.')
            continue
        payload = dict(raw)
        if forced_county_slug:
            payload['county_slug'] = forced_county_slug
        elif not payload.get('county_slug'):
            payload['county_slug'] = DEFAULT_COUNTY_SLUG

        court_data, item_errors = normalize_court_payload(
            payload,
            partial=False,
            validate_county_bounds=False,
        )
        if item_errors:
            title = payload.get('name') or f'item #{idx + 1}'
            errors.append(f'{title}: {", ".join(item_errors)}')
            continue

        if not skip_bounds_check and not is_point_within_county_bounds(
            court_data.get('latitude'),
            court_data.get('longitude'),
            court_data.get('county_slug'),
        ):
            resolved_slug = resolve_county_slug_for_point(
                court_data.get('latitude'),
                court_data.get('longitude'),
                preferred_slug=court_data.get('county_slug'),
            )
            if resolved_slug:
                if resolved_slug != court_data.get('county_slug'):
                    out_of_bounds_corrected += 1
                court_data['county_slug'] = resolved_slug
            else:
                out_of_bounds_skipped += 1
                continue
        normalized.append(court_data)

    if errors:
        raise ValueError('Invalid court payload:\n- ' + '\n- '.join(errors))

    deduped = {}
    duplicate_input_count = 0
    for item in normalized:
        key = (item['county_slug'], _identity_key(item))
        if key in deduped:
            duplicate_input_count += 1
        deduped[key] = item

    target_counties = sorted({item['county_slug'] for item in deduped.values()})
    existing_by_key = {}
    for slug in target_counties:
        existing_rows = Court.query.filter_by(county_slug=slug).all()
        for court in existing_rows:
            existing_by_key[(slug, _identity_key(court.to_dict()))] = court

    created = 0
    updated = 0
    processed = 0

    for key, item in deduped.items():
        processed += 1
        existing = existing_by_key.get(key)
        if existing:
            apply_court_changes(existing, item)
            updated += 1
        else:
            new_court = Court(**item)
            db.session.add(new_court)
            created += 1

    if commit:
        db.session.commit()
    else:
        db.session.flush()

    return {
        'processed': processed,
        'created': created,
        'updated': updated,
        'duplicate_input_rows_ignored': duplicate_input_count,
        'out_of_bounds_corrected': out_of_bounds_corrected,
        'out_of_bounds_skipped': out_of_bounds_skipped,
        'counties': target_counties,
    }


def import_county_from_file(file_path, county_slug=None, commit=True):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f'File not found: {path}')
    with path.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)
    return import_courts_payload(payload, county_slug=county_slug, commit=commit)


def import_county_slug(county_slug, commit=True):
    payload, normalized_slug = load_county_file(county_slug)
    return import_courts_payload(payload, county_slug=normalized_slug, commit=commit)


def import_state(state_slug, commit=True):
    """Import all courts for a state from its output directory.

    Non-CA states skip county bounds validation since we only have
    California bounds data.
    """
    payload = load_state_file(state_slug)
    skip_bounds = (state_slug != 'california')
    return import_courts_payload(
        payload,
        commit=commit,
        skip_bounds_check=skip_bounds,
    )
