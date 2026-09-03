"""Doubles entries require partner consent and support a visible partner pool."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, TournamentEntry, utcnow


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Larson Park', city='Costa Mesa', state='CA',
            county_slug='orange-county', latitude=33.66,
            longitude=-117.91, num_courts=6,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _headers(account):
    return {'Authorization': f"Bearer {account['token']}"}


def _register(client, slug, name):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _create_doubles(client, organizer, court_id, slug='consent', max_entries=8):
    response = client.post('/api/tournaments', json={
        'name': f'{slug.title()} Doubles',
        'court_id': court_id,
        'starts_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'format': 'single_elim',
        'event_type': 'doubles',
        'max_entries': max_entries,
    }, headers=_headers(organizer))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _court_id(client):
    return client.get('/api/courts?q=larson').get_json()['items'][0]['id']


def test_stranger_partner_is_pending_until_they_accept(client):
    organizer = _register(client, 'partner-organizer', 'Organizer')
    owner = _register(client, 'partner-owner', 'Ana')
    stranger = _register(client, 'partner-stranger', 'Ben')
    tournament = _create_doubles(client, organizer, _court_id(client))

    missing = client.post(
        f"/api/tournaments/{tournament['id']}/register",
        json={}, headers=_headers(owner),
    )
    assert missing.status_code == 400
    assert missing.get_json()['error'] == 'partner_choice_required'

    invited = client.post(
        f"/api/tournaments/{tournament['id']}/register",
        json={'partner_id': stranger['user']['id']}, headers=_headers(owner),
    )
    assert invited.status_code == 201, invited.get_json()
    entry = invited.get_json()['entries'][0]
    assert entry['partner_status'] == 'pending'
    assert entry['partner_ready'] is False
    assert [player['id'] for player in entry['players']] == [owner['user']['id']]
    assert entry['pending_partner']['id'] == stranger['user']['id']

    candidate_view = client.get(
        f"/api/tournaments/{tournament['id']}", headers=_headers(stranger),
    ).get_json()
    assert candidate_view['my_entry_id'] is None
    assert candidate_view['partner_action_pending'] is True
    assert candidate_view['pending_action_count'] == 1
    assert candidate_view['my_partner_action']['pending_on'] == 'invitee'
    assert candidate_view['my_partner_action']['owner']['id'] == owner['user']['id']
    mine = client.get('/api/tournaments?mine=1', headers=_headers(stranger)).get_json()
    assert [item['id'] for item in mine['items']] == [tournament['id']]

    notices = client.get('/api/notifications', headers=_headers(stranger)).get_json()['items']
    invitation = next(item for item in notices if item['kind'] == 'tournament_invite')
    assert 'invited you to partner' in invitation['title']
    assert invitation['action_url'] == f"/#tournament/{tournament['id']}"

    accepted = client.post(
        f"/api/tournaments/{tournament['id']}/partner/respond",
        json={'accept': True}, headers=_headers(stranger),
    )
    assert accepted.status_code == 200, accepted.get_json()
    accepted_entry = accepted.get_json()['entries'][0]
    assert accepted_entry['partner_status'] == 'accepted'
    assert accepted_entry['partner_ready'] is True
    assert [player['id'] for player in accepted_entry['players']] == [
        owner['user']['id'], stranger['user']['id'],
    ]
    assert accepted.get_json()['my_entry_id'] == accepted_entry['id']

    with client.application.app_context():
        stored = TournamentEntry.query.one()
        assert stored.player2_id == stranger['user']['id']
        assert stored.partner_invitee_id is None


def test_decline_returns_entry_to_pool_and_offer_needs_owner_consent(client):
    organizer = _register(client, 'pool-organizer', 'Organizer')
    owner = _register(client, 'pool-owner', 'Ana')
    invited = _register(client, 'pool-invited', 'Ben')
    candidate = _register(client, 'pool-candidate', 'Cam')
    tournament = _create_doubles(client, organizer, _court_id(client), 'pool')

    created = client.post(
        f"/api/tournaments/{tournament['id']}/register",
        json={'partner_id': invited['user']['id']}, headers=_headers(owner),
    ).get_json()
    entry_id = created['entries'][0]['id']
    declined = client.post(
        f"/api/tournaments/{tournament['id']}/partner/respond",
        json={'accept': False}, headers=_headers(invited),
    )
    assert declined.status_code == 200, declined.get_json()
    pool_entry = declined.get_json()['entries'][0]
    assert pool_entry['needs_partner'] is True
    assert pool_entry['partner_status'] == 'needed'

    offered = client.post(
        f"/api/tournaments/{tournament['id']}/entries/{entry_id}/partner-offer",
        headers=_headers(candidate),
    )
    assert offered.status_code == 200, offered.get_json()
    candidate_view = offered.get_json()
    assert candidate_view['my_entry_id'] is None
    assert candidate_view['my_pending_partner_offer']['pending_on'] == 'owner'

    owner_view = client.get(
        f"/api/tournaments/{tournament['id']}", headers=_headers(owner),
    ).get_json()
    assert owner_view['my_partner_action']['decision_for_me'] is True
    assert owner_view['my_partner_action']['candidate']['id'] == candidate['user']['id']
    assert owner_view['entries'][0]['partner_invite_pending'] is True

    accepted = client.post(
        f"/api/tournaments/{tournament['id']}/partner/respond",
        json={'accept': True}, headers=_headers(owner),
    )
    assert accepted.status_code == 200, accepted.get_json()
    assert [player['id'] for player in accepted.get_json()['entries'][0]['players']] == [
        owner['user']['id'], candidate['user']['id'],
    ]


def test_incomplete_doubles_entries_hold_slots_but_cannot_start(client):
    organizer = _register(client, 'start-organizer', 'Organizer')
    first = _register(client, 'start-first', 'Ana')
    second = _register(client, 'start-second', 'Ben')
    tournament = _create_doubles(
        client, organizer, _court_id(client), 'start', max_entries=2,
    )
    for player in (first, second):
        response = client.post(
            f"/api/tournaments/{tournament['id']}/register",
            json={'needs_partner': True}, headers=_headers(player),
        )
        assert response.status_code == 201, response.get_json()

    full = client.get(
        f"/api/tournaments/{tournament['id']}", headers=_headers(organizer),
    ).get_json()
    assert full['entry_count'] == 2
    assert full['ready_entry_count'] == 0
    assert full['partner_pool_count'] == 2
    started = client.post(
        f"/api/tournaments/{tournament['id']}/start",
        headers=_headers(organizer),
    )
    assert started.status_code == 409
    assert started.get_json() == {'error': 'pending_partner_entries', 'count': 2}
