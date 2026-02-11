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
