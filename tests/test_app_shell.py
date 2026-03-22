"""Tests for app bootstrap, schedule banner, and court hub read models."""
import json
from datetime import timedelta

from backend.app import db
from backend.models import Message
from backend.time_utils import utcnow_naive


def _register(client, username, email):
    res = client.post('/api/auth/register', json={
        'username': username,
        'email': email,
        'password': 'password123',
        'name': username.replace('_', ' ').title(),
    })
    data = json.loads(res.data)
    return data['token'], data['user']['id']


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _create_court(client, token, name='Shell Court'):
    res = client.post('/api/courts', json={
        'name': name,
        'latitude': 40.80,
        'longitude': -124.16,
        'city': 'Eureka',
    }, headers=_auth(token))
    return json.loads(res.data)['court']


def _checkin(client, token, court_id):
    return client.post('/api/presence/checkin', json={'court_id': court_id}, headers=_auth(token))


def _make_friends(client, requester_token, recipient_token, requester_id, recipient_id):
    send = client.post('/api/auth/friends/request', json={
        'friend_id': recipient_id,
    }, headers=_auth(requester_token))
    assert send.status_code == 201

    pending = client.get('/api/auth/friends/pending', headers=_auth(recipient_token))
    requests = json.loads(pending.data)['requests']
    friendship = next((item for item in requests if item['user']['id'] == requester_id), None)
    assert friendship is not None

    accept = client.post('/api/auth/friends/respond', json={
        'friendship_id': friendship['id'],
        'action': 'accept',
    }, headers=_auth(recipient_token))
    assert accept.status_code == 200


def test_app_bootstrap_returns_auth_and_friend_context(client):
    viewer_token, viewer_id = _register(client, 'shell_viewer', 'shell_viewer@test.com')
    friend_token, friend_id = _register(client, 'shell_friend', 'shell_friend@test.com')
    _make_friends(client, viewer_token, friend_token, viewer_id, friend_id)

    res = client.get('/api/app/bootstrap?state=CA&county_slug=humboldt', headers=_auth(viewer_token))
    assert res.status_code == 200
    data = json.loads(res.data)

    assert data['authenticated'] is True
    assert data['user']['id'] == viewer_id
    assert friend_id in data['friend_ids']
    assert any(friend['id'] == friend_id for friend in data['friends'])
    assert data['location']['selected_state_abbr'] == 'CA'
    assert data['location']['selected_county_slug'] == 'humboldt'
    assert 'states' in data['location']
    assert 'counties' in data['location']
    assert 'schedule_banner' in data


def test_schedule_banner_combines_sessions_lobbies_and_tournaments(client):
    host_token, host_id = _register(client, 'banner_host', 'banner_host@test.com')
    guest_token, guest_id = _register(client, 'banner_guest', 'banner_guest@test.com')
    court = _create_court(client, host_token, 'Banner Court')

    scheduled_session = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'scheduled',
        'start_time': (utcnow_naive() + timedelta(hours=2)).isoformat(),
        'game_type': 'open',
    }, headers=_auth(host_token))
    assert scheduled_session.status_code == 201

    scheduled_lobby = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [host_id],
        'team2': [guest_id],
        'scheduled_for': (utcnow_naive() + timedelta(hours=3)).isoformat(),
        'source': 'friends_challenge',
    }, headers=_auth(host_token))
    assert scheduled_lobby.status_code == 201

    tournament = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Shell Showcase',
        'start_time': (utcnow_naive() + timedelta(hours=4)).isoformat(),
        'tournament_format': 'single_elimination',
        'access_mode': 'open',
        'match_type': 'singles',
        'max_players': 8,
        'min_participants': 2,
        'check_in_required': True,
        'no_show_policy': 'auto_forfeit',
        'affects_elo': True,
    }, headers=_auth(host_token))
    assert tournament.status_code == 201

    res = client.get(f'/api/sessions/banner?court_id={court["id"]}', headers=_auth(host_token))
    assert res.status_code == 200
    data = json.loads(res.data)
    item_types = {item['item_type'] for item in data['items']}

    assert 'session' in item_types
    assert 'ranked_lobby' in item_types
    assert 'tournament' in item_types
    assert data['context']['court_id'] == court['id']


def test_schedule_banner_includes_session_viewer_state_and_capacity(client):
    host_token, host_id = _register(client, 'banner_state_host', 'banner_state_host@test.com')
    guest_token, guest_id = _register(client, 'banner_state_guest', 'banner_state_guest@test.com')
    court = _create_court(client, host_token, 'Banner State Court')
    _make_friends(client, host_token, guest_token, host_id, guest_id)

    scheduled_session = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'scheduled',
        'start_time': (utcnow_naive() + timedelta(hours=2)).isoformat(),
        'game_type': 'doubles',
        'visibility': 'friends',
        'max_players': 6,
        'invite_friends': [guest_id],
        'notes': 'Friday Ladder',
    }, headers=_auth(host_token))
    assert scheduled_session.status_code == 201
    session_id = json.loads(scheduled_session.data)['session']['id']

    res = client.get(f'/api/sessions/banner?court_id={court["id"]}', headers=_auth(guest_token))
    assert res.status_code == 200
    data = json.loads(res.data)
    item = next(item for item in data['items'] if item['item_type'] == 'session' and item['reference_id'] == session_id)

    assert item['viewer_status'] == 'invited'
    assert item['is_mine'] is True
    assert item['max_players'] == 6
    assert item['spots_taken'] == 1
    assert item['spots_remaining'] == 5
    assert item['is_friend_only'] is True
    assert item['creator_name'] == 'Banner State Host'


def test_court_hub_sorts_friends_first_and_prioritizes_score_confirmation(client):
    viewer_token, viewer_id = _register(client, 'hub_viewer', 'hub_viewer@test.com')
    friend_token, friend_id = _register(client, 'hub_friend', 'hub_friend@test.com')
    stranger_token, stranger_id = _register(client, 'hub_stranger', 'hub_stranger@test.com')
    court = _create_court(client, viewer_token, 'Hub Court')
    _make_friends(client, viewer_token, friend_token, viewer_id, friend_id)

    assert _checkin(client, stranger_token, court['id']).status_code == 201
    assert _checkin(client, viewer_token, court['id']).status_code == 201
    assert _checkin(client, friend_token, court['id']).status_code == 201

    create_match = client.post('/api/ranked/match', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [viewer_id],
        'team2': [friend_id],
    }, headers=_auth(viewer_token))
    assert create_match.status_code == 201
    match_id = json.loads(create_match.data)['match']['id']

    scored = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 8},
        headers=_auth(viewer_token),
    )
    assert scored.status_code == 200

    res = client.get(f'/api/courts/{court["id"]}/hub', headers=_auth(friend_token))
    assert res.status_code == 200
    data = json.loads(res.data)

    challengeable = data['ranked']['challengeable_players']
    assert challengeable[0]['id'] == viewer_id
    assert challengeable[0]['is_friend'] is True
    assert challengeable[1]['id'] == stranger_id
    assert data['action_center']['type'] == 'confirm_score'
    assert data['action_center']['match']['id'] == match_id


def test_court_hub_promotes_ready_queue_cohort_for_current_user(client):
    viewer_token, viewer_id = _register(client, 'queue_viewer', 'queue_viewer@test.com')
    other_token, other_id = _register(client, 'queue_other', 'queue_other@test.com')
    court = _create_court(client, viewer_token, 'Queue Hub Court')

    assert _checkin(client, viewer_token, court['id']).status_code == 201
    assert _checkin(client, other_token, court['id']).status_code == 201

    join_one = client.post('/api/ranked/queue/join', json={
        'court_id': court['id'],
        'match_type': 'singles',
    }, headers=_auth(viewer_token))
    assert join_one.status_code == 201

    join_two = client.post('/api/ranked/queue/join', json={
        'court_id': court['id'],
        'match_type': 'singles',
    }, headers=_auth(other_token))
    assert join_two.status_code == 201

    res = client.get(f'/api/courts/{court["id"]}/hub', headers=_auth(viewer_token))
    assert res.status_code == 200
    data = json.loads(res.data)

    assert data['action_center']['type'] == 'start_next_queue_game'
    cohort = data['ranked']['queue_ready_cohort']
    assert cohort['match_type'] == 'singles'
    assert cohort['current_user_in_cohort'] is True


def test_court_hub_join_queue_action_keeps_court_context(client):
    viewer_token, _viewer_id = _register(client, 'queue_context_viewer', 'queue_context_viewer@test.com')
    court = _create_court(client, viewer_token, 'Queue Context Court')

    assert _checkin(client, viewer_token, court['id']).status_code == 201

    res = client.get(f'/api/courts/{court["id"]}/hub', headers=_auth(viewer_token))
    assert res.status_code == 200
    data = json.loads(res.data)

    assert data['action_center']['type'] == 'join_queue'
    assert data['action_center']['primary_action']['court_id'] == court['id']
    assert data['action_center']['secondary_action']['court_id'] == court['id']


def test_bootstrap_and_presence_status_include_live_court_context(client):
    viewer_token, _viewer_id = _register(client, 'presence_viewer', 'presence_viewer@test.com')
    court_res = client.post('/api/courts', json={
        'name': 'Presence Court',
        'latitude': 40.82,
        'longitude': -124.11,
        'city': 'Eureka',
        'photo_url': 'https://example.com/court.png',
    }, headers=_auth(viewer_token))
    assert court_res.status_code == 201
    court = json.loads(court_res.data)['court']

    checked_in = _checkin(client, viewer_token, court['id'])
    assert checked_in.status_code == 201

    bootstrap = client.get('/api/app/bootstrap', headers=_auth(viewer_token))
    assert bootstrap.status_code == 200
    bootstrap_payload = json.loads(bootstrap.data)

    assert bootstrap_payload['presence']['checked_in'] is True
    assert bootstrap_payload['presence']['court_id'] == court['id']
    assert bootstrap_payload['presence']['court_name'] == 'Presence Court'

    status = client.get('/api/presence/status', headers=_auth(viewer_token))
    assert status.status_code == 200
    status_payload = json.loads(status.data)
    assert status_payload['checked_in'] is True
    assert status_payload['court_id'] == court['id']
    assert status_payload['court_name'] == 'Presence Court'

    ping = client.post('/api/presence/ping', json={}, headers=_auth(viewer_token))
    assert ping.status_code == 200
    ping_payload = json.loads(ping.data)
    assert ping_payload['checked_in'] is True
    assert ping_payload['court_id'] == court['id']
    assert ping_payload['court_name'] == 'Presence Court'
