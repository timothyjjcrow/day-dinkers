"""Numeric game matching and untruncated list contracts."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, User, utcnow


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Range Court', city='Portland', state='OR',
            county_slug='multnomah', latitude=45.52, longitude=-122.68,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def register(client, suffix):
    response = client.post('/api/auth/register', json={
        'email': f'{suffix}@example.com',
        'password': 'secret123',
        'display_name': suffix.title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_create_edit_and_filter_numeric_game_range(app, client):
    player = register(client, 'numeric-host')
    token = player['token']
    scheduled = (utcnow() + timedelta(days=2)).isoformat() + 'Z'
    created = client.post('/api/games', headers=auth(token), json={
        'court_id': 1,
        'scheduled_at': scheduled,
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 4,
        'preferred_level': 'any',
        'level_min': 3.0,
        'level_max': 4.0,
    })
    assert created.status_code == 201, created.get_json()
    game = created.get_json()
    assert (game['level_min'], game['level_max']) == (3.0, 4.0)

    matching = client.get(
        '/api/games?lat=45.52&lng=-122.68&level=3.5', headers=auth(token),
    )
    assert matching.status_code == 200
    assert [item['id'] for item in matching.get_json()['items']] == [game['id']]
    outside = client.get(
        '/api/games?lat=45.52&lng=-122.68&level=4.5', headers=auth(token),
    )
    assert outside.status_code == 200
    assert outside.get_json()['items'] == []

    edited = client.patch(
        f"/api/games/{game['id']}", headers=auth(token),
        json={'level_min': 4.0, 'level_max': 4.5},
    )
    assert edited.status_code == 200, edited.get_json()
    assert (edited.get_json()['level_min'], edited.get_json()['level_max']) == (4.0, 4.5)

    invalid = client.post('/api/games', headers=auth(token), json={
        'court_id': 1,
        'scheduled_at': scheduled,
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 4,
        'level_min': 4.5,
        'level_max': 3.0,
    })
    assert invalid.status_code == 400
    assert invalid.get_json()['error'] == 'invalid_level_range'


def test_history_results_and_leaderboard_have_followable_pages(app, client):
    viewer = register(client, 'pager')
    token = viewer['token']
    with app.app_context():
        court = db.session.get(Court, 1)
        user = db.session.get(User, viewer['user']['id'])
        now = utcnow()
        for index in range(5):
            game = Game(
                court=court, creator_id=user.id,
                scheduled_at=now - timedelta(days=index + 1),
                completed_at=now - timedelta(days=index + 1),
                game_type='ranked', visibility='open', max_players=2,
                status='completed', score_team1=11, score_team2=5,
            )
            db.session.add(game)
            db.session.flush()
            db.session.add(GamePlayer(game=game, user_id=user.id, team=1))
        for index in range(4):
            db.session.add(User(
                email=f'board-{index}@example.com',
                display_name=f'Board {index}',
                password_hash='unused', rating=1400 - index,
                ranked_wins=1, ranked_losses=1,
            ))
        user.ranked_wins = 3
        db.session.commit()

    history = client.get('/api/games/history?limit=2', headers=auth(token))
    assert history.status_code == 200, history.get_json()
    first = history.get_json()
    assert first['count'] == 2
    assert first['total'] == 5
    assert first['has_more'] is True
    assert first['next_cursor']
    assert 'bio' not in first['items'][0]['players'][0]
    second = client.get(
        f"/api/games/history?limit=2&cursor={first['next_cursor']}",
        headers=auth(token),
    ).get_json()
    assert second['count'] == 2
    assert {item['id'] for item in first['items']}.isdisjoint(
        {item['id'] for item in second['items']},
    )

    results = client.get(
        '/api/games/results?scope=all&limit=2', headers=auth(token),
    ).get_json()
    assert results['total'] == 5
    assert results['count'] == 2
    assert results['has_more'] is True
    next_results = client.get(
        f"/api/games/results?scope=all&limit=2&cursor={results['next_cursor']}",
        headers=auth(token),
    ).get_json()
    assert next_results['count'] == 2

    board = client.get('/api/leaderboard?limit=2', headers=auth(token)).get_json()
    assert board['count'] == 2
    assert board['total'] == 5
    assert board['has_more'] is True
    next_board = client.get(
        f"/api/leaderboard?limit=2&cursor={board['next_cursor']}",
        headers=auth(token),
    ).get_json()
    assert next_board['count'] == 2
    assert {item['id'] for item in board['items']}.isdisjoint(
        {item['id'] for item in next_board['items']},
    )


def test_invalid_list_cursor_and_level_are_explicit(client):
    player = register(client, 'bad-page')
    headers = auth(player['token'])
    assert client.get(
        '/api/games/history?cursor=not-a-cursor', headers=headers,
    ).get_json()['error'] == 'invalid_cursor'
    invalid_level = client.get(
        '/api/games?lat=45.52&lng=-122.68&level=3.2', headers=headers,
    )
    assert invalid_level.status_code == 400
    assert invalid_level.get_json()['error'] == 'invalid_level'
