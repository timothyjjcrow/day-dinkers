"""Conditional-read coverage for live tournament and league detail polling."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, utcnow


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Validator Courts', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82, num_courts=8,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': slug.replace('-', ' ').title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def conditional_headers(account, etag):
    return {**auth(account), 'If-None-Match': etag}


def assert_private_validator(response):
    assert response.status_code == 200
    assert response.headers['ETag'].startswith('"')
    assert response.headers['Cache-Control'] == 'private, no-cache'
    assert 'Authorization' in response.headers.get('Vary', '')


def test_league_detail_etag_is_stable_viewer_scoped_and_invalidated(client, app):
    organizer = register(client, 'league-etag-organizer')
    viewer = register(client, 'league-etag-viewer')
    other_viewer = register(client, 'league-etag-other')
    court_id = Court.query.one().id
    created = client.post('/api/leagues', headers=auth(organizer), json={
        'name': 'Conditional Ladder',
        'court_id': court_id,
        'starts_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'box_size': 3,
        'max_players': 8,
        'round_days': 7,
    })
    assert created.status_code == 201, created.get_json()
    detail_url = f"/api/leagues/{created.get_json()['id']}"

    first = client.get(detail_url, headers=auth(viewer))
    assert_private_validator(first)
    etag = first.headers['ETag']
    repeated = client.get(detail_url, headers=auth(viewer))
    assert repeated.headers['ETag'] == etag

    unchanged = client.get(detail_url, headers=conditional_headers(viewer, etag))
    assert unchanged.status_code == 304
    assert unchanged.get_data() == b''
    assert unchanged.headers['ETag'] == etag

    # Even equivalent outsider views receive account-bound validators.
    other = client.get(detail_url, headers=auth(other_viewer))
    assert_private_validator(other)
    assert other.headers['ETag'] != etag

    joined = client.post(f'{detail_url}/join', headers=auth(viewer))
    assert joined.status_code == 200, joined.get_json()
    changed = client.get(detail_url, headers=conditional_headers(viewer, etag))
    assert_private_validator(changed)
    assert changed.headers['ETag'] != etag
    assert changed.get_json()['joined'] is True


def test_tournament_detail_etag_is_stable_viewer_scoped_and_invalidated(client, app):
    organizer = register(client, 'tournament-etag-organizer')
    viewer = register(client, 'tournament-etag-viewer')
    other_viewer = register(client, 'tournament-etag-other')
    court_id = Court.query.one().id
    created = client.post('/api/tournaments', headers=auth(organizer), json={
        'name': 'Conditional Open',
        'court_id': court_id,
        'starts_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'format': 'single_elim',
        'event_type': 'singles',
        'max_entries': 8,
    })
    assert created.status_code == 201, created.get_json()
    detail_url = f"/api/tournaments/{created.get_json()['id']}"

    first = client.get(detail_url, headers=auth(viewer))
    assert_private_validator(first)
    etag = first.headers['ETag']
    repeated = client.get(detail_url, headers=auth(viewer))
    assert repeated.headers['ETag'] == etag

    unchanged = client.get(detail_url, headers=conditional_headers(viewer, etag))
    assert unchanged.status_code == 304
    assert unchanged.get_data() == b''
    assert unchanged.headers['ETag'] == etag

    other = client.get(detail_url, headers=auth(other_viewer))
    assert_private_validator(other)
    assert other.headers['ETag'] != etag

    registered = client.post(f'{detail_url}/register', headers=auth(viewer), json={})
    assert registered.status_code == 201, registered.get_json()
    changed = client.get(detail_url, headers=conditional_headers(viewer, etag))
    assert_private_validator(changed)
    assert changed.headers['ETag'] != etag
    assert changed.get_json()['my_entry_id'] is not None
