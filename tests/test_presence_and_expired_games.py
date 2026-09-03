"""Presence freshness and durable unscored-game lifecycle regressions."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    EXPIRED_SCORE_GRACE_DAYS,
    GAME_STATUSES,
    CheckIn,
    Court,
    Game,
    Notification,
    utcnow,
)


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Freshness Park',
            city='Costa Mesa',
            state='CA',
            latitude=33.66,
            longitude=-117.91,
            num_courts=6,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def create_ranked_singles(client, host, opponent, court_id):
    response = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'game_type': 'ranked',
        'max_players': 2,
        'visibility': 'open',
    }, headers=auth(host))
    assert response.status_code == 201, response.get_json()
    game = response.get_json()
    joined = client.post(
        f"/api/games/{game['id']}/join", headers=auth(opponent),
    )
    assert joined.status_code == 200, joined.get_json()
    return game


def score_payload(host, opponent):
    return {
        'team1': [host['user']['id']],
        'team2': [opponent['user']['id']],
        'score_team1': 11,
        'score_team2': 5,
    }


def test_presence_expires_after_thirty_minutes_and_stale_ping_cannot_revive_it(
    client, app,
):
    from backend.routes.auth import presence_stale_cutoff

    player = register(client, 'present@example.com', 'Present Player')
    viewer = register(client, 'viewer@example.com', 'Viewer')
    court = Court.query.filter_by(name='Freshness Park').one()
    assert app.config['PRESENCE_STALE_AFTER_SECONDS'] == 30 * 60
    now = utcnow()
    assert presence_stale_cutoff(now) == now - timedelta(minutes=30)

    checkin_response = client.post(
        f'/api/courts/{court.id}/checkin',
        json={'looking_for_game': True},
        headers=auth(player),
    )
    assert checkin_response.status_code == 200
    checkin = CheckIn.query.filter_by(
        user_id=player['user']['id'], checked_out_at=None,
    ).one()
    checkin.checked_in_at = now - timedelta(minutes=40)
    checkin.last_presence_ping_at = now - timedelta(minutes=29)
    db.session.commit()

    detail = client.get(
        f'/api/courts/{court.id}', headers=auth(viewer),
    ).get_json()
    assert detail['players_here_count'] == 1
    assert detail['players_here'][0]['id'] == player['user']['id']
    assert detail['players_here_last_confirmed_at'] is not None
    presence = client.get('/api/me', headers=auth(player)).get_json()['presence']
    assert presence['checked_in'] is True
    assert presence['last_confirmed_at']
    assert presence['expires_at']

    checkin.last_presence_ping_at = utcnow() - timedelta(minutes=31)
    db.session.commit()
    stale_detail = client.get(
        f'/api/courts/{court.id}', headers=auth(viewer),
    ).get_json()
    assert stale_detail['players_here_count'] == 0
    assert stale_detail['players_here'] == []
    assert stale_detail['players_here_last_confirmed_at'] is None
    assert client.get('/api/me', headers=auth(player)).get_json()['presence'] == {
        'checked_in': False,
    }

    # A delayed background/reopen heartbeat closes the stale row. Only the
    # explicit check-in endpoint is allowed to establish presence again.
    ping = client.post('/api/presence/ping', headers=auth(player))
    assert ping.status_code == 200
    assert ping.get_json()['presence'] == {'checked_in': False}
    db.session.refresh(checkin)
    assert checkin.checked_out_at is not None
    assert checkin.looking_for_game is False

    renewed = client.post(
        f'/api/courts/{court.id}/checkin', json={}, headers=auth(player),
    ).get_json()['presence']
    assert renewed['checked_in'] is True
    assert renewed['expires_at']


def test_expired_game_stays_in_history_calendar_and_uses_normal_score_review(
    client, app,
):
    from backend.routes.games import expire_stale_unscored

    host = register(client, 'late-host@example.com', 'Late Host')
    opponent = register(client, 'late-opponent@example.com', 'Late Opponent')
    court = Court.query.filter_by(name='Freshness Park').one()
    game = create_ranked_singles(client, host, opponent, court.id)

    played_at = utcnow() - timedelta(days=8)
    stored = db.session.get(Game, game['id'])
    stored.scheduled_at = played_at
    db.session.commit()

    expire_stale_unscored()
    db.session.refresh(stored)
    assert 'expired' in GAME_STATUSES
    assert stored.status == 'expired'

    for account in (host, opponent):
        notifications = client.get(
            '/api/notifications', headers=auth(account),
        ).get_json()['items']
        expiry = [
            item for item in notifications
            if item['kind'] == 'game_expired'
            and item['related_game_id'] == game['id']
        ]
        assert len(expiry) == 1
        assert expiry[0]['action_url'] == f"/#game/{game['id']}"
        assert 'add the score' in expiry[0]['title'].lower()

    # Re-running maintenance is side-effect free because expired is a durable
    # lifecycle state, not an out-of-band string used only to hide a row.
    expire_stale_unscored()
    assert Notification.query.filter_by(
        user_id=host['user']['id'],
        kind='game_expired',
        related_game_id=game['id'],
    ).count() == 1

    history = client.get(
        '/api/games/history', headers=auth(host),
    ).get_json()
    unscored = next(item for item in history['items'] if item['id'] == game['id'])
    assert history['unscored_count'] == 1
    assert unscored['status'] == 'expired'
    assert unscored['can_enter_score'] is True
    assert unscored['expired_score_deadline_at']

    token = client.get('/api/calendar/token', headers=auth(host)).get_json()['token']
    calendar = client.get(f'/api/calendar/{token}.ics').get_data(as_text=True)
    assert f'UID:thirdshot-game-{game["id"]}@thirdshot.app' in calendar
    assert 'Unscored pickleball match' in calendar

    reported = client.post(
        f"/api/games/{game['id']}/complete",
        json=score_payload(host, opponent),
        headers=auth(host),
    )
    assert reported.status_code == 200, reported.get_json()
    assert reported.get_json()['status'] == 'awaiting_confirmation'

    disputed = client.post(
        f"/api/games/{game['id']}/dispute",
        json={'details': 'The final score was different.'}, headers=auth(opponent),
    )
    assert disputed.status_code == 200, disputed.get_json()
    assert disputed.get_json()['status'] == 'expired'
    assert disputed.get_json()['can_enter_score'] is True
    assert game['id'] in {
        item['id'] for item in client.get(
            '/api/games/history', headers=auth(host),
        ).get_json()['items']
    }

    reported_again = client.post(
        f"/api/games/{game['id']}/complete",
        json=score_payload(host, opponent),
        headers=auth(host),
    )
    assert reported_again.get_json()['status'] == 'awaiting_confirmation'
    confirmed = client.post(
        f"/api/games/{game['id']}/confirm", headers=auth(opponent),
    )
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()['status'] == 'completed'
    final_history = client.get(
        '/api/games/history', headers=auth(host),
    ).get_json()
    assert final_history['completed_count'] == 1
    assert final_history['unscored_count'] == 0


def test_expired_score_window_closes_after_thirty_days(client):
    from backend.routes.games import expire_stale_unscored

    host = register(client, 'old-host@example.com', 'Old Host')
    opponent = register(client, 'old-opponent@example.com', 'Old Opponent')
    court = Court.query.filter_by(name='Freshness Park').one()
    game = create_ranked_singles(client, host, opponent, court.id)
    stored = db.session.get(Game, game['id'])
    stored.scheduled_at = utcnow() - timedelta(
        days=EXPIRED_SCORE_GRACE_DAYS + 1,
    )
    db.session.commit()
    expire_stale_unscored()

    history = client.get(
        '/api/games/history', headers=auth(host),
    ).get_json()
    unscored = next(item for item in history['items'] if item['id'] == game['id'])
    assert unscored['status'] == 'expired'
    assert unscored['can_enter_score'] is False
    assert history['unscored_count'] == 1
    rejected = client.post(
        f"/api/games/{game['id']}/complete",
        json=score_payload(host, opponent),
        headers=auth(host),
    )
    assert rejected.status_code == 400
    assert rejected.get_json() == {'error': 'game_not_open'}
    assert Notification.query.filter_by(
        user_id=host['user']['id'],
        kind='game_expired',
        related_game_id=game['id'],
    ).count() == 0

    token = client.get('/api/calendar/token', headers=auth(host)).get_json()['token']
    calendar = client.get(f'/api/calendar/{token}.ics').get_data(as_text=True)
    assert f'UID:thirdshot-game-{game["id"]}@thirdshot.app' not in calendar
