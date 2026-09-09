"""A player's scoped place is independent of leaderboard pagination."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, User, utcnow


@pytest.fixture()
def board():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        client = app.test_client()
        response = client.post('/api/auth/register', json={
            'email': 'rank-viewer@example.test', 'password': 'test-password', 'display_name': 'Viewer',
        }).get_json()
        viewer = db.session.get(User, response['user']['id'])
        viewer.ranked_losses = 1
        viewer.rating = 700
        viewer.last_lat, viewer.last_lng = 45.5, -122.7
        others = [User(email=f'rank-{n}@example.test', display_name=f'Player {n}',
                       password_hash='unused', rating=1000+n, ranked_wins=1,
                       last_lat=45.5, last_lng=-122.7) for n in range(65)]
        db.session.add_all(others)
        db.session.commit()
        yield client, {'Authorization': f"Bearer {response['token']}"}, viewer, others
        db.session.remove()
        db.drop_all()


def test_viewer_below_first_page_is_ranked_even_with_only_a_loss(board):
    client, auth, viewer, others = board
    first = client.get('/api/leaderboard', headers=auth).get_json()
    assert len(first['items']) == 50 and first['total'] == 66
    assert viewer.id not in {p['id'] for p in first['items']}
    assert first['viewer']['status'] == 'ranked'
    assert first['viewer']['rank'] == 66
    assert first['viewer']['player']['ranked_losses'] == 1
    second = client.get('/api/leaderboard', headers=auth,
                        query_string={'cursor': first['next_cursor']}).get_json()
    assert second['viewer'] == first['viewer']
    assert [row['rank'] for row in first['items']+second['items']] == list(range(1, 67))
    assert len({row['id'] for row in first['items']+second['items']}) == 66
    assert second['items'][-1]['id'] == viewer.id and not second['has_more']


def test_viewer_state_distinguishes_scope_missing_location_and_no_results(board):
    client, auth, viewer, others = board
    assert client.get('/api/leaderboard?scope=friends', headers=auth).get_json()['viewer']['rank'] == 1
    near_url = '/api/leaderboard?lat=45.5&lng=-122.7&radius=10'
    viewer.last_lat = 40
    db.session.commit()
    outside = client.get(near_url, headers=auth).get_json()
    assert outside['total'] == 65 and outside['viewer']['status'] == 'outside_area'
    viewer.last_lat = viewer.last_lng = None
    db.session.commit()
    assert client.get(near_url, headers=auth).get_json()['viewer']['status'] == 'location_not_set'
    viewer.ranked_losses = 0
    db.session.commit()
    empty = client.get('/api/leaderboard', headers=auth).get_json()['viewer']
    assert empty == {'status': 'no_ranked_results', 'rank': None, 'player': None}


def test_monthly_position_uses_monthly_results_and_explains_inactive_month(board):
    client, auth, viewer, others = board
    url = '/api/leaderboard?period=month&limit=1'
    empty = client.get(url, headers=auth).get_json()
    assert empty['viewer']['status'] == 'no_results_this_month'
    court = Court(name='Rating Court', city='Portland', state='OR', latitude=45.5, longitude=-122.7)
    db.session.add(court)
    db.session.flush()
    for completed_at, change in [(utcnow(), -12),
                                 (utcnow().replace(day=1)-timedelta(days=1), 40)]:
        game = Game(court_id=court.id, creator_id=viewer.id, scheduled_at=completed_at-timedelta(hours=1),
                    game_type='ranked', status='completed', completed_at=completed_at, max_players=2)
        db.session.add(game)
        db.session.flush()
        db.session.add_all([GamePlayer(game_id=game.id, user_id=viewer.id, team=1, rating_delta=change),
                            GamePlayer(game_id=game.id, user_id=others[0].id, team=2, rating_delta=-change)])
    db.session.commit()
    first = client.get(url, headers=auth).get_json()
    assert first['viewer']['rank'] == 2 and first['viewer']['status'] == 'ranked'
    assert first['viewer']['player']['month_delta'] == -12
    assert first['viewer']['player']['month_games'] == 1
    assert first['items'][0]['month_delta'] == 12
    second = client.get(url+'&cursor='+first['next_cursor'], headers=auth).get_json()
    assert second['items'][0] == first['viewer']['player']
    assert second['viewer'] == first['viewer']


def test_rankings_keep_authentication_and_cursor_validation(board):
    client, auth, viewer, others = board
    assert client.get('/api/leaderboard').status_code == 401
    assert client.get('/api/leaderboard?cursor=bad', headers=auth).status_code == 400
