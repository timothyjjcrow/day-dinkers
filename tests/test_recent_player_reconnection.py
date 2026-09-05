"""Real relationship and participation boundaries behind Played together."""
from datetime import timedelta

from backend.app import db
from backend.models import Court, Friendship, Game, GamePlayer, User, utcnow
from tests.test_api import app, client, register, auth_headers


def shared_game(app, viewer, other, *, days=1, scored=True, viewer_team=1):
    with app.app_context():
        game = Game(court_id=Court.query.first().id,
                    creator_id=viewer['user']['id'],
                    scheduled_at=utcnow() - timedelta(days=days),
                    completed_at=utcnow() - timedelta(days=days),
                    status='completed', visibility='private', game_type='casual',
                    score_team1=11 if scored else None,
                    score_team2=8 if scored else None)
        db.session.add(game)
        db.session.flush()
        db.session.add_all([
            GamePlayer(game_id=game.id, user_id=viewer['user']['id'], team=viewer_team),
            GamePlayer(game_id=game.id, user_id=other['user']['id'], team=2 if scored else None),
        ])
        db.session.commit()


def test_reconnection_actions_follow_real_relationship_changes(client, app):
    viewer = register(client, 'viewer@example.com')
    other = register(client, 'other@example.com')
    shared_game(app, viewer, other)
    headers = auth_headers(viewer['token'])
    def recent():
        return client.get('/api/players/recent', headers=headers).get_json()['items'][0]
    assert recent()['friendship_status'] is None
    request = client.post('/api/friends/request', json={'user_id': viewer['user']['id']},
                          headers=auth_headers(other['token'])).get_json()
    person = recent()
    assert person['friendship_status'] == 'pending'
    assert person['outgoing'] is False
    assert person['friendship_id'] == request['friendship_id']
    response = client.post(f"/api/friends/{person['friendship_id']}/respond",
                           json={'accept': True}, headers=headers)
    assert response.status_code == 200
    assert recent()['is_friend'] is True
    assert recent()['friendship_status'] == 'accepted'


def test_outgoing_request_stays_pending_after_refresh(client, app):
    viewer = register(client, 'viewer@example.com')
    other = register(client, 'other@example.com')
    shared_game(app, viewer, other)
    headers = auth_headers(viewer['token'])
    client.post('/api/friends/request', json={'user_id': other['user']['id']}, headers=headers)
    person = client.get('/api/players/recent', headers=headers).get_json()['items'][0]
    assert person['outgoing'] is True
    assert person['friendship_status'] == 'pending'
    assert person['is_friend'] is False


def test_roster_only_viewer_is_not_told_they_played(client, app):
    viewer = register(client, 'viewer@example.com')
    other = register(client, 'other@example.com')
    shared_game(app, viewer, other, viewer_team=None)
    headers = auth_headers(viewer['token'])
    assert client.get('/api/players/recent', headers=headers).get_json()['items'] == []
    shared_game(app, viewer, other, scored=False, viewer_team=None)
    person = client.get('/api/players/recent', headers=headers).get_json()['items'][0]
    assert person['games_together'] == 1


def test_deleted_players_do_not_consume_visible_limit(client, app):
    viewer = register(client, 'viewer@example.com')
    deleted = register(client, 'deleted@example.com')
    other = register(client, 'other@example.com')
    shared_game(app, viewer, deleted, days=1)
    shared_game(app, viewer, other, days=2)
    with app.app_context():
        db.session.get(User, deleted['user']['id']).deleted_at = utcnow()
        db.session.commit()
    people = client.get('/api/players/recent?limit=1', headers=auth_headers(viewer['token'])).get_json()['items']
    assert [person['id'] for person in people] == [other['user']['id']]


def test_recent_players_are_private_and_block_safe_in_both_directions(client, app):
    viewer = register(client, 'viewer@example.com')
    other = register(client, 'other@example.com')
    shared_game(app, viewer, other)
    assert client.get('/api/players/recent').status_code == 401
    client.post(f"/api/users/{viewer['user']['id']}/block", headers=auth_headers(other['token']))
    assert client.get('/api/players/recent', headers=auth_headers(viewer['token'])).get_json()['items'] == []
