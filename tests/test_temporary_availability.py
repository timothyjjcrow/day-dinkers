"""Away is a temporary suggestion preference, never a participation mutation."""
from datetime import timedelta

from backend.app import db
from backend.models import Court, Game, GameInvite, GamePlayer, User, utcnow, iso
from tests.test_pagination_notifications import app, client, register, auth


def test_away_keeps_usual_times_and_commitments_and_expires_from_discovery(client, app):
    viewer = register(client, 'away-viewer@example.test', 'Viewer')
    player = register(client, 'away-player@example.test', 'Player')
    player_id = player['user']['id']
    with app.app_context():
        user = db.session.get(User, player_id)
        user.home_lat, user.home_lng = 33, -117
        user.availability = '["tue-eve"]'
        court = Court(name='Away Court', city='Town', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        game = Game(court_id=court.id, creator_id=viewer['user']['id'], scheduled_at=utcnow() + timedelta(days=3))
        db.session.add(game)
        db.session.flush()
        db.session.add_all([GamePlayer(game_id=game.id, user_id=player_id), GameInvite(game_id=game.id, user_id=player_id)])
        db.session.commit()
    nearby = '/api/players/nearby?lat=33&lng=-117&radius=10'
    assert player_id in [p['id'] for p in client.get(nearby, headers=auth(viewer)).get_json()['items']]
    until = iso(utcnow() + timedelta(days=10))
    response = client.patch('/api/me', json={'away_until': until}, headers=auth(player))
    assert response.status_code == 200
    assert response.get_json()['user']['availability'] == ['tue-eve']
    assert response.get_json()['user']['away_until'] == until
    assert player_id not in [p['id'] for p in client.get(nearby, headers=auth(viewer)).get_json()['items']]
    assert client.get(f'/api/users/{player_id}', headers=auth(viewer)).get_json()['away_until'] == until
    with app.app_context():
        assert GamePlayer.query.filter_by(user_id=player_id).count() == 1
        assert GameInvite.query.filter_by(user_id=player_id).count() == 1
        db.session.get(User, player_id).away_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert player_id in [p['id'] for p in client.get(nearby, headers=auth(viewer)).get_json()['items']]
    cleared = client.patch('/api/me', json={'away_until': None}, headers=auth(player)).get_json()['user']
    assert cleared['away_until'] is None and cleared['availability'] == ['tue-eve']


def test_away_date_requires_timezone_and_bounded_future_and_owner(client, app):
    player = register(client, 'away-validation@example.test', 'Player')
    for value in ['not-a-date', '2099-01-01T00:00:00', True,
                  iso(utcnow() - timedelta(days=1)), iso(utcnow() + timedelta(days=91))]:
        assert client.patch('/api/me', json={'away_until': value}, headers=auth(player)).status_code == 400
    assert client.patch('/api/me', json={'away_until': iso(utcnow() + timedelta(days=3))}).status_code == 401
    assert client.get('/api/me', headers=auth(player)).get_json()['user']['away_until'] is None
