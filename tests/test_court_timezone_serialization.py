"""Stored geographic zones remain public even when opening hours are unknown."""
import json

import pytest

from test_business_reviewed_versions import app, client, live
from backend.app import db
from backend.models import BusinessProfile, Court
from backend.services.court_hours import project_hours


def public_court_payloads(client, court_id):
    detail = client.get(f'/api/courts/{court_id}')
    assert detail.status_code == 200
    listing = client.get('/api/courts?limit=100')
    assert listing.status_code == 200
    item = next(row for row in listing.get_json()['items'] if row['id'] == court_id)
    return detail.get_json(), item


def test_stored_timezone_survives_empty_hours_in_model_detail_and_list(app, client):
    with app.app_context():
        court = db.session.get(Court, 1)
        court.timezone = 'America/Phoenix'
        court.structured_hours = '{}'
        court.hours = ''
        court.hours_dawn_to_dusk = False
        db.session.commit()
        assert court.to_dict()['timezone'] == 'America/Phoenix'
        assert court.to_summary_dict()['timezone'] == 'America/Phoenix'
    for payload in public_court_payloads(client, 1):
        assert payload['timezone'] == 'America/Phoenix'
        assert payload['structured_hours'] == {}
        assert payload['hours'] == ''
        assert payload['open_status']['state'] == 'unavailable'
        assert payload['open_status']['is_open'] is None


@pytest.mark.parametrize('stored', [None, '', 'Mars/Phobos', '/etc/localtime'])
def test_invalid_or_missing_stored_zone_uses_valid_hours_fallback(app, client, stored):
    schedule = {'timezone': 'America/Chicago', 'mon': [{'open': '08:00', 'close': '17:00'}]}
    with app.app_context():
        court = db.session.get(Court, 1)
        court.timezone = stored
        court.structured_hours = json.dumps(schedule)
        db.session.commit()
    for payload in public_court_payloads(client, 1):
        assert payload['timezone'] == 'America/Chicago'
        assert payload['structured_hours'] == schedule


def test_absent_or_invalid_zone_never_invents_hours_or_utc(app, client):
    with app.app_context():
        court = db.session.get(Court, 1)
        court.timezone = 'Mars/Phobos'
        court.structured_hours = '{}'
        court.hours = ''
        db.session.commit()
    for payload in public_court_payloads(client, 1):
        assert payload['timezone'] is None
        assert payload['structured_hours'] == {}
        assert payload['hours'] == ''
        assert payload['open_status']['is_open'] is None


def test_hours_projection_keeps_stored_zone_without_changing_reviewed_schedule(app, client, live):
    _, business_id = live
    schedule = {'timezone': 'America/Los_Angeles', 'mon': [{'open': '08:00', 'close': '17:00'}]}
    with app.app_context():
        court = db.session.get(Court, 1)
        court.timezone = 'America/Phoenix'
        business = db.session.get(BusinessProfile, business_id)
        business.structured_hours = json.dumps(schedule)
        db.session.commit()
        projection = project_hours(court, {'structured_hours': schedule})
        assert projection['timezone'] == 'America/Phoenix'
        assert projection['structured_hours'] == schedule
    for payload in public_court_payloads(client, 1):
        assert payload['timezone'] == 'America/Phoenix'
        assert payload['structured_hours'] == schedule
        assert payload['hours_source'] == 'venue'
