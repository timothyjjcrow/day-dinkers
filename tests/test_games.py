"""Tests for game routes."""
import json
from datetime import datetime, timedelta, timezone


def _auth(client, username='gameuser', email='game@test.com'):
    res = client.post('/api/auth/register', json={
        'username': username, 'email': email, 'password': 'password123',
    })
    return json.loads(res.data)['token']


def _create_court(client, token):
    res = client.post('/api/courts', json={
        'name': 'Game Court', 'latitude': 40.80, 'longitude': -124.16,
        'city': 'Eureka',
    }, headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['court']


def _create_game(client, token, court_id, **overrides):
    data = {
        'title': 'Test Game', 'court_id': court_id,
        'date_time': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'max_players': 4, 'skill_level': 'all', 'game_type': 'open',
    }
    data.update(overrides)
    res = client.post('/api/games', json=data,
        headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['game']


def test_create_game(client):
    token = _auth(client)
    court = _create_court(client, token)
    game = _create_game(client, token, court['id'])
    assert game['title'] == 'Test Game'
    assert game['game_type'] == 'open'
    assert game['status'] == 'upcoming'


def test_get_games(client):
    token = _auth(client)
    court = _create_court(client, token)
    _create_game(client, token, court['id'])

    res = client.get('/api/games')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert len(data['games']) >= 1


def test_get_game_detail(client):
    token = _auth(client)
    court = _create_court(client, token)
    game = _create_game(client, token, court['id'])

    res = client.get(f'/api/games/{game["id"]}')
    assert res.status_code == 200
    data = json.loads(res.data)['game']
    assert data['player_count'] == 1  # Creator auto-RSVPs


def test_rsvp(client):
    token1 = _auth(client, 'creator', 'creator@test.com')
    token2 = _auth(client, 'joiner', 'joiner@test.com')
    court = _create_court(client, token1)
    game = _create_game(client, token1, court['id'])

    res = client.post(f'/api/games/{game["id"]}/rsvp',
        json={'status': 'yes'},
        headers={'Authorization': f'Bearer {token2}'},
    )
    assert res.status_code == 200

    res = client.get(f'/api/games/{game["id"]}')
    data = json.loads(res.data)['game']
    assert data['player_count'] == 2


def test_rsvp_game_full(client):
    token1 = _auth(client, 'host', 'host@test.com')
    court = _create_court(client, token1)
    game = _create_game(client, token1, court['id'], max_players=2)

    token2 = _auth(client, 'p2', 'p2@test.com')
    client.post(f'/api/games/{game["id"]}/rsvp', json={'status': 'yes'},
        headers={'Authorization': f'Bearer {token2}'})

    token3 = _auth(client, 'p3', 'p3@test.com')
    res = client.post(f'/api/games/{game["id"]}/rsvp', json={'status': 'yes'},
        headers={'Authorization': f'Bearer {token3}'})
    assert res.status_code == 400


def test_delete_game(client):
    token = _auth(client)
    court = _create_court(client, token)
    game = _create_game(client, token, court['id'])

    res = client.delete(f'/api/games/{game["id"]}',
        headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 200


def test_delete_game_not_creator(client):
    token1 = _auth(client, 'owner', 'owner@test.com')
    token2 = _auth(client, 'other', 'other@test.com')
    court = _create_court(client, token1)
    game = _create_game(client, token1, court['id'])

    res = client.delete(f'/api/games/{game["id"]}',
        headers={'Authorization': f'Bearer {token2}'})
    assert res.status_code == 403
