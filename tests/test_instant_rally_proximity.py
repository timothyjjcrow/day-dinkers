"""Instant rallies require a short-lived, court-bound proximity proof."""

import pytest

from backend.app import create_app, db
from backend.models import CheckIn, Court, Game, utcnow


@pytest.fixture()
def app():
    app = create_app('testing')
    app.config['INSTANT_RALLY_PROXIMITY_REQUIRED'] = True
    with app.app_context():
        db.create_all()
        db.session.add_all([
            Court(
                name='Proof Court', city='Costa Mesa', state='CA',
                county_slug='orange-county', latitude=33.6600,
                longitude=-117.9100, num_courts=4,
            ),
            Court(
                name='Other Court', city='Costa Mesa', state='CA',
                county_slug='orange-county', latitude=33.6700,
                longitude=-117.9200, num_courts=2,
            ),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _register(client, slug):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': slug.replace('-', ' ').title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def _courts():
    rows = Court.query.order_by(Court.id.asc()).all()
    return rows[0], rows[1]


def _verified_checkin(client, person, court):
    response = client.post(
        f'/api/courts/{court.id}/checkin',
        headers=_headers(person),
        json={
            'looking_for_game': True,
            'presence_intent': 'instant_rally',
            'presence_location': {
                'latitude': court.latitude,
                'longitude': court.longitude,
                'accuracy_meters': 12,
            },
        },
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['presence_verified'] is True
    return response.get_json()['instant_rally_presence_proof']


def _rally_payload(court, attempt, proof=None):
    payload = {
        'court_id': court.id,
        'scheduled_at': utcnow().isoformat() + 'Z',
        'client_attempt_id': attempt,
        'game_type': 'casual',
        'max_players': 4,
    }
    if proof is not None:
        payload['presence_proof'] = proof
    return payload


def test_remote_or_imprecise_device_fix_cannot_create_a_checkin(client):
    person = _register(client, 'remote-player')
    court, _ = _courts()

    remote = client.post(
        f'/api/courts/{court.id}/checkin', headers=_headers(person), json={
            'presence_intent': 'instant_rally',
            'presence_location': {
                'latitude': court.latitude + 0.02,
                'longitude': court.longitude,
                'accuracy_meters': 10,
            },
        },
    )
    assert remote.status_code == 409
    assert remote.get_json() == {'error': 'court_proximity_mismatch'}

    coarse = client.post(
        f'/api/courts/{court.id}/checkin', headers=_headers(person), json={
            'presence_intent': 'instant_rally',
            'presence_location': {
                'latitude': court.latitude,
                'longitude': court.longitude,
                'accuracy_meters': 100,
            },
        },
    )
    assert coarse.status_code == 409
    assert coarse.get_json() == {'error': 'location_accuracy_too_low'}
    assert CheckIn.query.filter_by(user_id=person['user']['id']).count() == 0


def test_new_rally_requires_and_accepts_verified_presence_proof(client):
    person = _register(client, 'onsite-player')
    court, _ = _courts()

    # A legacy check-in row alone is not physical proof in production mode.
    legacy = client.post(
        f'/api/courts/{court.id}/checkin', json={}, headers=_headers(person),
    )
    assert legacy.status_code == 200
    refused = client.post(
        '/api/games/rally',
        json=_rally_payload(court, 'proof-required-attempt'),
        headers=_headers(person),
    )
    assert refused.status_code == 409
    assert refused.get_json() == {'error': 'presence_proof_required'}
    assert Game.query.count() == 0

    proof = _verified_checkin(client, person, court)
    started = client.post(
        '/api/games/rally',
        json=_rally_payload(court, 'verified-rally-attempt', proof),
        headers=_headers(person),
    )
    assert started.status_code == 201, started.get_json()
    assert started.get_json()['game']['court']['id'] == court.id


def test_presence_proof_is_bound_to_its_user_and_court(client):
    owner = _register(client, 'proof-owner')
    other = _register(client, 'proof-other')
    court, other_court = _courts()
    proof = _verified_checkin(client, owner, court)

    _verified_checkin(client, other, court)
    wrong_user = client.post(
        '/api/games/rally',
        json=_rally_payload(court, 'wrong-user-proof', proof),
        headers=_headers(other),
    )
    assert wrong_user.status_code == 409
    assert wrong_user.get_json() == {'error': 'invalid_presence_proof'}

    _verified_checkin(client, owner, other_court)
    wrong_court = client.post(
        '/api/games/rally',
        json=_rally_payload(other_court, 'wrong-court-proof', proof),
        headers=_headers(owner),
    )
    assert wrong_court.status_code == 409
    assert wrong_court.get_json() == {'error': 'invalid_presence_proof'}


def test_successful_location_check_does_not_persist_device_coordinates(client):
    person = _register(client, 'private-location')
    court, _ = _courts()
    _verified_checkin(client, person, court)
    row = CheckIn.query.filter_by(user_id=person['user']['id']).one()

    assert not hasattr(row, 'latitude')
    assert not hasattr(row, 'longitude')
    assert row.court_id == court.id
