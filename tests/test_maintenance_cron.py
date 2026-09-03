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
