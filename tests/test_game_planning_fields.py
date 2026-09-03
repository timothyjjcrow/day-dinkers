"""Durable optional planning details for scheduled games."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GameInvite, utcnow
from backend.routes.games import _game_attempt_fingerprint


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Planner Court', city='Irvine', state='CA',
            county_slug='orange-county', latitude=33.68,
            longitude=-117.82, num_courts=8,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client):
    response = client.post('/api/auth/register', json={
        'email': 'planner@example.com',
        'password': 'secret123',
        'display_name': 'Planner',
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(person):
    return {'Authorization': f"Bearer {person['token']}"}


def create_payload(**overrides):
    payload = {
        'court_id': Court.query.one().id,
        'scheduled_at': (utcnow() + timedelta(hours=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 8,
    }
    payload.update(overrides)
    return payload


def test_create_persists_and_serializes_complete_game_plan(client):
    person = register(client)
    start = utcnow() + timedelta(hours=3)
    end = start + timedelta(minutes=135)
    response = client.post('/api/games', json=create_payload(
        scheduled_at=start.isoformat() + 'Z',
        ends_at=end.isoformat() + 'Z',
        title='Saturday ladder',
        description='Rotate winners and split after every game.',
        cost_cents=1250,
        court_number='Courts 3–4',
        court_count=2,
    ), headers=auth(person))

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body['title'] == 'Saturday ladder'
    assert body['description'] == 'Rotate winners and split after every game.'
    assert body['duration_minutes'] == 135
    assert body['ends_at'] == end.isoformat() + 'Z'
    assert body['cost_cents'] == 1250
    assert body['court_number'] == 'Courts 3–4'
    assert body['court_count'] == 2

    row = db.session.get(Game, body['id'])
    assert row.title == 'Saturday ladder'
    assert row.description.startswith('Rotate winners')
    assert row.duration_minutes == 135
    assert row.cost_cents == 1250
    assert row.court_number == 'Courts 3–4'
    assert row.court_count == 2


def test_legacy_create_remains_valid_with_empty_planning_defaults(client):
    person = register(client)
    response = client.post(
        '/api/games', json=create_payload(), headers=auth(person),
    )

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body['title'] == ''
    assert body['description'] == ''
    assert body['duration_minutes'] is None
    assert body['ends_at'] is None
    assert body['cost_cents'] is None
    assert body['court_number'] == ''
    assert body['court_count'] is None


def test_friends_only_requires_a_real_friend_and_direct_open_invites_are_additive(client):
    host = register(client)

    orphan = client.post(
        '/api/games', json=create_payload(visibility='friends'), headers=auth(host),
    )
    assert orphan.status_code == 400, orphan.get_json()
    assert orphan.get_json()['error'] == 'no_friends'

    guest_response = client.post('/api/auth/register', json={
        'email': 'guest@example.com',
        'password': 'secret123',
        'display_name': 'Guest Player',
    })
    assert guest_response.status_code == 201, guest_response.get_json()
    guest = guest_response.get_json()
    created = client.post('/api/games', json=create_payload(
        visibility='open', invite_user_ids=[guest['user']['id']],
    ), headers=auth(host))

    assert created.status_code == 201, created.get_json()
    game_id = created.get_json()['id']
    assert GameInvite.query.filter_by(
        game_id=game_id, user_id=guest['user']['id'],
    ).one_or_none() is not None


def test_calendar_uses_saved_title_duration_location_and_cost(client):
    person = register(client)
    start = (utcnow() + timedelta(hours=5)).replace(microsecond=0)
    created = client.post('/api/games', json=create_payload(
        scheduled_at=start.isoformat() + 'Z',
        title='Evening ladder',
        description='Rotate after each game.',
        duration_minutes=120,
        cost_cents=500,
        court_number='Court 7',
        court_count=2,
    ), headers=auth(person))
    assert created.status_code == 201, created.get_json()
    token = client.get('/api/calendar/token', headers=auth(person)).get_json()['token']

    calendar = client.get(f'/api/calendar/{token}.ics')
    text = calendar.get_data(as_text=True)
    assert calendar.status_code == 200
    assert f'DTSTART:{start.strftime("%Y%m%dT%H%M%SZ")}' in text
    assert (
        f'DTEND:{(start + timedelta(minutes=120)).strftime("%Y%m%dT%H%M%SZ")}'
        in text
    )
    assert 'SUMMARY:Evening ladder at Planner Court' in text
    assert r'LOCATION:Planner Court\, Court 7\, Irvine' in text
    assert 'Rotate after each game.' in text
    assert '$5.00 per player' in text
    assert '2 courts reserved' in text


@pytest.mark.parametrize(('extra', 'error'), (
    ({'title': 42}, 'invalid_title'),
    ({'title': 'x' * 121}, 'invalid_title'),
    ({'description': 'x' * 1001}, 'invalid_description'),
    ({'duration_minutes': 14}, 'invalid_duration_minutes'),
    ({'duration_minutes': 721}, 'invalid_duration_minutes'),
    ({'duration_minutes': 90.5}, 'invalid_duration_minutes'),
    ({'ends_at': 'not-a-time'}, 'invalid_ends_at'),
    ({'cost_cents': -1}, 'invalid_cost_cents'),
    ({'cost_cents': 1.5}, 'invalid_cost_cents'),
    ({'cost_cents': 1_000_001}, 'invalid_cost_cents'),
    ({'court_number': 'x' * 41}, 'invalid_court_number'),
    ({'court_count': 0}, 'invalid_court_count'),
    ({'court_count': 25}, 'invalid_court_count'),
))
def test_create_rejects_invalid_planning_details(client, extra, error):
    person = register(client)
    response = client.post(
        '/api/games', json=create_payload(**extra), headers=auth(person),
    )

    assert response.status_code == 400, response.get_json()
    assert response.get_json()['error'] == error


def test_end_time_must_match_duration_and_start(client):
    person = register(client)
    start = utcnow() + timedelta(hours=3)

    mismatch = client.post('/api/games', json=create_payload(
        scheduled_at=start.isoformat() + 'Z',
        duration_minutes=60,
        ends_at=(start + timedelta(minutes=90)).isoformat() + 'Z',
    ), headers=auth(person))
    assert mismatch.status_code == 400
    assert mismatch.get_json()['error'] == 'duration_end_mismatch'

    before_start = client.post('/api/games', json=create_payload(
        scheduled_at=start.isoformat() + 'Z',
        ends_at=(start - timedelta(minutes=15)).isoformat() + 'Z',
    ), headers=auth(person))
    assert before_start.status_code == 400
    assert before_start.get_json()['error'] == 'invalid_ends_at'


def test_host_can_edit_or_clear_every_planning_detail(client):
    person = register(client)
    created = client.post('/api/games', json=create_payload(
        duration_minutes=60,
    ), headers=auth(person)).get_json()
    new_start = utcnow() + timedelta(days=1)
    new_end = new_start + timedelta(minutes=150)

    edited = client.patch(f"/api/games/{created['id']}", json={
        'scheduled_at': new_start.isoformat() + 'Z',
        'ends_at': new_end.isoformat() + 'Z',
        'title': 'League practice',
        'description': 'Three courts, rotating partners.',
        'cost_cents': 0,
        'court_number': 'North bank',
        'court_count': 3,
    }, headers=auth(person))

    assert edited.status_code == 200, edited.get_json()
    body = edited.get_json()
    assert set(body['updated_fields']) == {
        'scheduled_at', 'duration_minutes', 'title', 'description',
        'cost_cents', 'court_number', 'court_count',
    }
    assert body['duration_minutes'] == 150
    assert body['ends_at'] == new_end.isoformat() + 'Z'
    assert body['cost_cents'] == 0

    cleared = client.patch(f"/api/games/{created['id']}", json={
        'title': None,
        'description': None,
        'duration_minutes': None,
        'cost_cents': None,
        'court_number': None,
        'court_count': None,
    }, headers=auth(person))
    assert cleared.status_code == 200, cleared.get_json()
    assert cleared.get_json()['title'] == ''
    assert cleared.get_json()['description'] == ''
    assert cleared.get_json()['ends_at'] is None
    assert cleared.get_json()['cost_cents'] is None
    assert cleared.get_json()['court_number'] == ''
    assert cleared.get_json()['court_count'] is None


def test_empty_new_fields_preserve_pre_upgrade_attempt_fingerprint():
    legacy = {
        'court_id': 9,
        'scheduled_at': utcnow().replace(microsecond=0),
        'game_type': 'casual',
        'max_players': 4,
        'invite_user_ids': [],
        'require_all_invitees': False,
        'visibility': 'open',
        'recurrence': 'none',
        'preferred_level': 'any',
        'club_id': None,
        'crew_id': None,
        'expected_crew_version': None,
        'notes': '',
    }
    upgraded = {
        **legacy,
        'title': '',
        'description': '',
        'duration_minutes': None,
        'cost_cents': None,
        'court_number': '',
        'court_count': None,
    }

    assert _game_attempt_fingerprint(upgraded) == _game_attempt_fingerprint(legacy)
    assert _game_attempt_fingerprint({**upgraded, 'title': 'Named plan'}) \
        != _game_attempt_fingerprint(legacy)
