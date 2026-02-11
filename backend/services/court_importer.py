"""Reusable court import/upsert helpers for county datasets."""

import json
from pathlib import Path

from backend.app import db
from backend.models import Court
from backend.services.court_payloads import (
    DEFAULT_COUNTY_SLUG,
    apply_court_changes,
    normalize_county_slug,
    normalize_court_payload,
)


COURT_DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'courts' / 'ca'


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


def import_courts_payload(courts_payload, county_slug=None, commit=True):
    """Validate and upsert courts. Returns import stats."""
    if not isinstance(courts_payload, list):
        raise ValueError('Court payload must be a list.')

    forced_county_slug = normalize_county_slug(county_slug, fallback='') if county_slug else ''
    normalized = []
    errors = []

    for idx, raw in enumerate(courts_payload):
        if not isinstance(raw, dict):
            errors.append(f'Item #{idx + 1}: expected object.')
            continue
        payload = dict(raw)
        if forced_county_slug:
            payload['county_slug'] = forced_county_slug
        elif not payload.get('county_slug'):
            payload['county_slug'] = DEFAULT_COUNTY_SLUG

        court_data, item_errors = normalize_court_payload(payload, partial=False)
        if item_errors:
            title = payload.get('name') or f'item #{idx + 1}'
            errors.append(f'{title}: {", ".join(item_errors)}')
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
