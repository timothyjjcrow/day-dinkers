"""Tests for county court import/upsert helpers."""

from backend.models import Court
from backend.services.court_importer import import_courts_payload


def test_import_courts_payload_creates_and_updates(app):
    with app.app_context():
        payload = [{
            'name': 'Lincoln Park Courts',
            'city': 'Oakland',
            'state': 'CA',
            'county_slug': 'alameda',
            'latitude': 37.8020,
            'longitude': -122.2070,
            'indoor': False,
            'num_courts': 6,
            'court_type': 'dedicated',
        }]
        first = import_courts_payload(payload, commit=True)
        assert first['created'] == 1
        assert first['updated'] == 0

        changed = [{
            'name': 'Lincoln Park Courts',
            'city': 'Oakland',
            'state': 'CA',
            'county_slug': 'alameda',
            'latitude': 37.8020,
            'longitude': -122.2070,
            'indoor': False,
            'num_courts': 8,
            'hours': 'Daily 7am-9pm',
            'court_type': 'dedicated',
        }]
        second = import_courts_payload(changed, commit=True)
        assert second['created'] == 0
        assert second['updated'] == 1

        court = Court.query.filter_by(name='Lincoln Park Courts', county_slug='alameda').first()
        assert court is not None
        assert court.num_courts == 8
        assert court.hours == 'Daily 7am-9pm'


def test_import_courts_payload_forces_county_slug(app):
    with app.app_context():
        payload = [{
            'name': 'Temescal Court',
            'city': 'Oakland',
            'state': 'CA',
            'latitude': 37.84,
            'longitude': -122.26,
            'court_type': 'shared',
        }]
        result = import_courts_payload(payload, county_slug='Alameda County', commit=True)
        assert result['created'] == 1

        court = Court.query.filter_by(name='Temescal Court').first()
        assert court is not None
        assert court.county_slug == 'alameda'


def test_import_courts_payload_corrects_out_of_bounds_county(app):
    with app.app_context():
        payload = [{
            'name': 'County Correction Court',
            'city': 'San Diego',
            'state': 'CA',
            'county_slug': 'humboldt',
            'latitude': 32.747603,
            'longitude': -116.991209,
            'court_type': 'dedicated',
        }]
        result = import_courts_payload(payload, commit=True)
        assert result['created'] == 1
        assert result['out_of_bounds_corrected'] == 1
        assert result['out_of_bounds_skipped'] == 0

        court = Court.query.filter_by(name='County Correction Court').first()
        assert court is not None
        assert court.county_slug == 'san-diego'


def test_import_courts_payload_skips_unresolvable_out_of_bounds(app):
    with app.app_context():
        payload = [{
            'name': 'Out Of State Court',
            'city': 'Somewhere',
            'state': 'CA',
            'county_slug': 'humboldt',
            'latitude': 17.130364,
            'longitude': -61.849449,
            'court_type': 'shared',
        }]
        result = import_courts_payload(payload, commit=True)
        assert result['created'] == 0
        assert result['updated'] == 0
        assert result['out_of_bounds_corrected'] == 0
        assert result['out_of_bounds_skipped'] == 1

        court = Court.query.filter_by(name='Out Of State Court').first()
        assert court is None
