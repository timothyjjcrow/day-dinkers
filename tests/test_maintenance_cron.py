"""Scheduled maintenance owns lifecycle mutations, never ordinary reads."""
import pytest

from backend.app import create_app, db


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv('CRON_SECRET', 'maintenance-test-secret')
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def test_maintenance_cron_requires_secret(client):
    assert client.get('/api/cron/maintenance').status_code == 401
    assert client.get('/api/cron/push').status_code == 401
    assert client.get('/api/cron/maintenance', headers={
        'Authorization': 'Bearer wrong',
    }).status_code == 401


def test_push_cron_drains_with_the_shared_secret(client, monkeypatch):
    delivered = []
    monkeypatch.setattr(
        'backend.services.push.drain_push_outbox',
        lambda **kwargs: delivered.append(kwargs) or {'sent': 2},
    )
    response = client.get('/api/cron/push', headers={
        'Authorization': 'Bearer maintenance-test-secret',
    })
    assert response.status_code == 200
    assert response.get_json()['push'] == {'sent': 2}
    assert delivered[0]['limit'] == 250
    assert delivered[0]['deadline'] > 0


def test_maintenance_cron_runs_isolated_jobs(client, monkeypatch):
    calls = []
    import backend.routes.maintenance as maintenance

    monkeypatch.setattr(maintenance, '_maintenance_jobs', lambda: [
        ('first', lambda: calls.append('first')),
        ('second', lambda: calls.append('second')),
    ])
    monkeypatch.setattr(maintenance, '_run_user_nudges', lambda deadline: {
        'processed': 3, 'remaining': 0,
    })
    response = client.get('/api/cron/maintenance', headers={
        'Authorization': 'Bearer maintenance-test-secret',
    })
    assert response.status_code == 200
    assert calls == ['first', 'second']
    assert response.get_json()['jobs'] == {'first': 'ok', 'second': 'ok'}
    assert response.get_json()['nudges'] == {'processed': 3, 'remaining': 0}


def test_competition_result_jobs_run_before_league_advancement(app):
    import backend.routes.maintenance as maintenance

    names = [name for name, _job in maintenance._maintenance_jobs()]

    assert names.index('tournament_result_maintenance') < names.index(
        'league_advancement'
    )
    assert names.index('league_result_maintenance') < names.index(
        'league_advancement'
    )


def test_maintenance_cron_reports_one_failure_without_skipping_next_job(client, monkeypatch):
    calls = []
    import backend.routes.maintenance as maintenance

    def fail():
        raise RuntimeError('expected test failure')

    monkeypatch.setattr(maintenance, '_maintenance_jobs', lambda: [
        ('broken', fail),
        ('healthy', lambda: calls.append('healthy')),
    ])
    monkeypatch.setattr(maintenance, '_run_user_nudges', lambda deadline: {
        'processed': 0, 'remaining': 0,
    })
    response = client.get('/api/cron/maintenance', headers={
        'Authorization': 'Bearer maintenance-test-secret',
    })
    assert response.status_code == 207
    assert calls == ['healthy']
    assert response.get_json()['failed'] == ['broken']


def test_me_and_game_feed_no_longer_run_lazy_maintenance():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    auth_source = (root / 'backend' / 'routes' / 'auth.py').read_text()
    games_source = (root / 'backend' / 'routes' / 'games.py').read_text()
    me_section = auth_source[auth_source.index("@auth_bp.get('/me')"):auth_source.index("@auth_bp.post('/auth/change-password')")]
    feed_section = games_source[games_source.index('def _prepare_game_feeds'):games_source.index('def my_games_payload')]
    assert 'send_game_reminders()' not in me_section
    assert 'expire_stale_unscored()' not in me_section
    assert 'return None' in feed_section


@pytest.fixture()
def fresh_tick(monkeypatch):
    import backend.security as security

    monkeypatch.setattr(security, '_BUCKETS', {})


def test_tick_is_public_and_runs_once_per_window(client, monkeypatch, fresh_tick):
    import backend.routes.maintenance as maintenance

    calls = []
    monkeypatch.setattr(maintenance, '_tick_jobs', lambda: [
        ('reminders', lambda: calls.append('reminders')),
    ])
    monkeypatch.setattr(
        'backend.services.push.deliver_due_push_now',
        lambda **kwargs: calls.append('push') or {'sent': 0},
    )

    first = client.post('/api/tick')
    second = client.post('/api/tick')

    assert first.status_code == 200
    assert first.get_json() == {'ok': True, 'ran': True}
    assert second.get_json() == {'ok': True, 'ran': False}
    assert calls == ['reminders', 'push']


def test_tick_runs_again_in_the_next_window(client, monkeypatch, fresh_tick):
    import backend.routes.maintenance as maintenance

    calls = []
    clock = [1_800_000_000.0]
    monkeypatch.setattr(maintenance.time, 'time', lambda: clock[0])
    monkeypatch.setattr(maintenance, '_tick_jobs', lambda: [
        ('reminders', lambda: calls.append('reminders')),
    ])
    monkeypatch.setattr(
        'backend.services.push.deliver_due_push_now', lambda **kwargs: {},
    )

    assert client.post('/api/tick').get_json()['ran'] is True
    clock[0] += maintenance.TICK_INTERVAL_SECONDS
    assert client.post('/api/tick').get_json()['ran'] is True
    assert calls == ['reminders', 'reminders']


def test_tick_covers_time_sensitive_jobs(app):
    import backend.routes.maintenance as maintenance

    names = [name for name, _job in maintenance._tick_jobs()]
    assert names == [
        'presence_cleanup', 'instant_game_expiry', 'game_reminders',
        'tournament_reminders', 'league_schedule_reminders',
    ]


def test_tick_sends_an_hour_before_reminder(client, app, monkeypatch, fresh_tick):
    from datetime import timedelta

    from backend.models import Court, Game, GamePlayer, Notification, User, utcnow

    user = User(email='tick-host@example.com', display_name='Tick Host')
    user.set_password('secret123')
    court = Court(name='Tick Court', latitude=30.0, longitude=-97.0)
    db.session.add_all([user, court])
    db.session.flush()
    game = Game(
        creator_id=user.id, court_id=court.id, title='Evening play',
        scheduled_at=utcnow() + timedelta(minutes=40), max_players=4,
    )
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=user.id))
    db.session.commit()

    response = client.post('/api/tick')

    assert response.get_json() == {'ok': True, 'ran': True}
    reminder = Notification.query.filter_by(user_id=user.id, kind='game_reminder').one()
    assert reminder.title == 'Game at Tick Court in about an hour'
