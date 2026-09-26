"""Rain alerts: game-time forecasts, the court weather `at` query and the
tick job that warns players once when rain looks likely."""
from datetime import UTC, timedelta, timezone

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, Notification, User, utcnow
from backend.routes import courts as courts_module


CENTRAL = timezone(timedelta(hours=-5))


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        courts_module._WEATHER_CACHE.clear()
        yield app
        courts_module._WEATHER_CACHE.clear()
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def hour_start():
    now = utcnow()
    return now.replace(minute=0, second=0, microsecond=0)


def periods(chances, start=None):
    """NWS-style hourly periods from this hour on, in a fixed local offset."""
    start = start or hour_start()
    rows = []
    for index in range(24):
        local = (start + timedelta(hours=index)).replace(tzinfo=UTC).astimezone(CENTRAL)
        value = chances.get(index, 0)
        rows.append({
            'startTime': local.isoformat(),
            'temperature': 71,
            'shortForecast': 'Chance Showers',
            'probabilityOfPrecipitation': {'unitCode': 'wmoUnit:percent', 'value': value},
        })
    return rows


def local_label(offset_hours):
    local = (hour_start() + timedelta(hours=offset_hours)).replace(tzinfo=UTC).astimezone(CENTRAL)
    return local.strftime('%-I %p')


def fake_forecast(monkeypatch, chances, calls=None):
    def fetch(lat, lng, timeout=8):
        if calls is not None:
            calls.append((round(lat, 2), round(lng, 2), timeout))
        return courts_module._nws_summary(periods(chances))
    monkeypatch.setattr(courts_module, '_nws_fetch', fetch)


def person(email, name):
    user = User(email=email, display_name=name)
    user.set_password('secret123')
    db.session.add(user)
    db.session.flush()
    return user


def outdoor_court(name='Larson Park', lat=33.66, lng=-117.91, **extra):
    court = Court(name=name, latitude=lat, longitude=lng, **extra)
    db.session.add(court)
    db.session.flush()
    return court


def game_at(court, host, starts_at, players=(), **extra):
    game = Game(
        creator_id=host.id, court_id=court.id, title='Evening play',
        scheduled_at=starts_at, max_players=4, **extra,
    )
    db.session.add(game)
    db.session.flush()
    for user in (host, *players):
        db.session.add(GamePlayer(game_id=game.id, user_id=user.id))
    db.session.commit()
    return game


def weather_alerts():
    return Notification.query.filter_by(kind='game_weather').order_by(Notification.user_id).all()


def send():
    from backend.routes.games import send_rain_alerts
    send_rain_alerts()


def test_summary_keeps_a_day_of_hours_and_the_old_rain_soon_rule():
    rows = periods({0: 10, 3: None, 8: 90})
    rows[3]['probabilityOfPrecipitation'] = None
    summary = courts_module._nws_summary(rows)

    assert summary['temp_f'] == 71 and summary['short'] == 'Chance Showers'
    # rain_soon still only looks at the next six hours.
    assert summary['rain_soon'] is False
    assert len(summary['hourly']) == 24
    assert summary['hourly'][0] == [rows[0]['startTime'], 10]
    assert summary['hourly'][3][1] == 0
    assert summary['hourly'][8][1] == 90


def test_tests_never_reach_the_weather_service(app):
    with pytest.raises(RuntimeError):
        courts_module._nws_fetch(33.66, -117.91)


def test_rain_chance_covers_the_games_hours_and_names_the_peak_hour(app, monkeypatch):
    fake_forecast(monkeypatch, {2: 30, 3: 70, 4: 90, 6: 100})
    court = outdoor_court()
    start = hour_start() + timedelta(hours=2)

    # Default two hours: 2 and 3 o'clock periods, peak at +3.
    peak = (hour_start() + timedelta(hours=3)).isoformat() + 'Z'
    assert courts_module.rain_chance_at(court, start) == {
        'chance': 70, 'label': local_label(3), 'starts_at': peak}
    # A 60 minute game only overlaps its first hour.
    assert courts_module.rain_chance_at(court, start, 60) == {
        'chance': 30, 'label': local_label(2),
        'starts_at': (hour_start() + timedelta(hours=2)).isoformat() + 'Z'}
    # Starting mid-hour still counts the hour it starts in.
    assert courts_module.rain_chance_at(court, start + timedelta(minutes=90), 60)['chance'] == 90
    # Past the forecast: unknown, not dry.
    assert courts_module.rain_chance_at(court, hour_start() + timedelta(hours=30)) is None


def test_weather_endpoint_adds_game_time_rain_only_when_asked(client, app, monkeypatch):
    calls = []
    fake_forecast(monkeypatch, {3: 80}, calls)
    court = outdoor_court()
    db.session.commit()
    at = (hour_start() + timedelta(hours=3)).isoformat() + 'Z'

    plain = client.get(f'/api/courts/{court.id}/weather').get_json()
    assert set(plain) == {'temp_f', 'short', 'rain_soon', 'latest_condition'}

    data = client.get(f'/api/courts/{court.id}/weather?at={at}').get_json()
    assert data['rain_at_game'] == {'chance': 80, 'label': local_label(3), 'starts_at': at}
    assert 'hourly' not in data
    assert len(calls) == 1  # the second call used the cache

    later = (hour_start() + timedelta(hours=5)).isoformat() + 'Z'
    assert client.get(f'/api/courts/{court.id}/weather?at={later}&minutes=60').get_json()['rain_at_game'] == {
        'chance': 0, 'label': local_label(5), 'starts_at': later,
    }
    for bad in ('soon', '9999-12-31T23:59:59', '0001-01-01T00:00:00%2B05:00'):
        response = client.get(f'/api/courts/{court.id}/weather?at={bad}')
        assert response.status_code == 200
        assert response.get_json()['rain_at_game'] is None


def test_host_and_players_get_one_rain_alert(client, app, monkeypatch):
    fake_forecast(monkeypatch, {2: 40, 3: 70})
    host = person('host@example.com', 'Hana')
    guest = person('guest@example.com', 'Gus')
    court = outdoor_court()
    game = game_at(court, host, hour_start() + timedelta(hours=2), players=[guest])

    send()
    send()

    alerts = weather_alerts()
    assert [alert.user_id for alert in alerts] == sorted([host.id, guest.id])
    by_user = {alert.user_id: alert for alert in alerts}
    title = f'Rain likely around {local_label(3)} at Larson Park'
    assert by_user[host.id].title == title
    assert by_user[host.id].body == 'Keep it, move it or cancel it from the game page.'
    assert by_user[guest.id].title == title
    assert by_user[guest.id].body == 'Your host may move it. Check the game page.'
    assert all(alert.related_game_id == game.id for alert in alerts)
    assert all(alert.action_url == f'/#game/{game.id}' for alert in alerts)

    # Reading the alert clears its unread key; the recent-alert lookup still
    # keeps the next tick from sending it again.
    for alert in alerts:
        alert.read = True
        alert.unread_dedupe_key = None
    db.session.commit()
    send()
    assert len(weather_alerts()) == 2


def test_rain_alert_reaches_the_activity_feed(client, app, monkeypatch):
    fake_forecast(monkeypatch, {2: 80})
    response = client.post('/api/auth/register', json={
        'email': 'feed@example.com', 'password': 'secret123', 'display_name': 'Fay',
    })
    token = response.get_json()['token']
    host = db.session.get(User, response.get_json()['user']['id'])
    game = game_at(outdoor_court(), host, hour_start() + timedelta(hours=2))

    send()

    feed = client.get('/api/notifications', headers={'Authorization': f'Bearer {token}'}).get_json()
    alert = next(item for item in feed['items'] if item['kind'] == 'game_weather')
    assert alert['related_game_id'] == game.id


@pytest.mark.parametrize('case', [
    'dry', 'indoor', 'too_soon', 'too_far', 'instant', 'cancelled', 'no_location',
    'time_vote',
])
def test_no_alert_outside_the_rules(app, monkeypatch, case):
    calls = []
    fake_forecast(monkeypatch, {index: 49 if case == 'dry' else 90 for index in range(24)}, calls)
    host = person('host@example.com', 'Hana')
    court = outdoor_court(
        indoor=case == 'indoor',
        lat=None if case == 'no_location' else 33.66,
    )
    starts_at = {
        'too_soon': utcnow() + timedelta(minutes=20),
        'too_far': utcnow() + timedelta(hours=6),
    }.get(case, hour_start() + timedelta(hours=2))
    extra = {'is_instant': True} if case == 'instant' else {}
    if case == 'cancelled':
        extra['status'] = 'cancelled'
    if case == 'time_vote':
        # Friends are still voting, so the earliest option isn't the start.
        extra['time_options'] = '[{"id": "a", "starts_at": "%sZ", "votes": []}]' % (
            starts_at.isoformat())
    game_at(court, host, starts_at, **extra)

    send()

    assert weather_alerts() == []
    # Only the dry-forecast case needed to look at the weather.
    assert len(calls) == (1 if case == 'dry' else 0)


def test_games_at_one_place_share_a_lookup(app, monkeypatch):
    calls = []
    fake_forecast(monkeypatch, {2: 90, 3: 90}, calls)
    host = person('host@example.com', 'Hana')
    other = person('other@example.com', 'Otto')
    first = outdoor_court('Larson Park', 33.661, -117.912)
    second = outdoor_court('Lions Park', 33.662, -117.908)
    game_at(first, host, hour_start() + timedelta(hours=2))
    game_at(second, other, hour_start() + timedelta(hours=3))

    send()

    assert len(calls) == 1
    assert calls[0][2] == 3  # short timeout inside the tick
    assert {alert.title.rsplit(' at ', 1)[1] for alert in weather_alerts()} == {'Larson Park', 'Lions Park'}


def test_upstream_lookups_are_capped_per_tick(app, monkeypatch):
    calls = []
    fake_forecast(monkeypatch, {2: 90}, calls)
    host = person('host@example.com', 'Hana')
    for index in range(6):
        game_at(outdoor_court(f'Court {index}', 30 + index, -97.0), host, hour_start() + timedelta(hours=2))

    send()
    assert len(calls) == 4
    assert len(weather_alerts()) == 4

    # The next tick reaches the rest; alerted games are skipped first.
    send()
    assert len(calls) == 6
    assert len(weather_alerts()) == 6


def test_a_failed_lookup_skips_only_that_place(app, monkeypatch):
    def fetch(lat, lng, timeout=8):
        if round(lat) == 30:
            raise OSError('weather service down')
        return courts_module._nws_summary(periods({2: 90}))
    monkeypatch.setattr(courts_module, '_nws_fetch', fetch)
    host = person('host@example.com', 'Hana')
    game_at(outdoor_court('Down Court', 30.0, -97.0), host, hour_start() + timedelta(hours=2))
    ok = game_at(outdoor_court('Up Court', 40.0, -97.0), host, hour_start() + timedelta(hours=2))

    send()

    assert [alert.related_game_id for alert in weather_alerts()] == [ok.id]


def test_no_lookup_starts_when_the_tick_is_out_of_time(app, monkeypatch):
    from time import monotonic

    from flask import g

    calls = []
    fake_forecast(monkeypatch, {2: 90}, calls)
    host = person('host@example.com', 'Hana')
    game_at(outdoor_court(), host, hour_start() + timedelta(hours=2))

    g.tick_deadline = monotonic() + 6
    send()
    assert calls == [] and weather_alerts() == []

    # A cached forecast needs no lookup, so it is still used.
    courts_module.court_forecast(outdoor_court('Cache Warmer'))
    g.tick_deadline = monotonic() + 6
    send()
    assert len(calls) == 1 and len(weather_alerts()) == 1


def test_tick_sends_rain_alerts(client, app, monkeypatch):
    import backend.security as security

    monkeypatch.setattr(security, '_BUCKETS', {})
    monkeypatch.setattr('backend.services.push.deliver_due_push_now', lambda **kwargs: {})
    fake_forecast(monkeypatch, {2: 75})
    host = person('host@example.com', 'Hana')
    game = game_at(outdoor_court(), host, hour_start() + timedelta(hours=2))

    assert client.post('/api/tick').get_json() == {'ok': True, 'ran': True}

    alert = Notification.query.filter_by(kind='game_weather').one()
    assert alert.user_id == host.id and alert.related_game_id == game.id


def test_places_take_turns_under_the_lookup_cap(app, monkeypatch):
    from backend.routes import games as games_module

    calls = []
    fake_forecast(monkeypatch, {}, calls)
    host = person('host@example.com', 'Hana')
    for index in range(6):
        court = outdoor_court(name=f'Park {index}', lat=30 + index, lng=-97)
        game_at(court, host, hour_start() + timedelta(hours=2))
    seen = set()
    for tick in range(6):
        courts_module._WEATHER_CACHE.clear()
        monkeypatch.setattr(games_module, 'utcnow', lambda tick=tick: utcnow() + timedelta(minutes=5 * tick))
        send()
        seen |= {lat for lat, _, _ in calls}
    assert seen == {float(30 + index) for index in range(6)}


def test_a_moved_game_can_be_warned_again_before_the_old_alert_is_read(app, monkeypatch):
    fake_forecast(monkeypatch, {index: 90 for index in range(24)})
    host = person('host@example.com', 'Hana')
    court = outdoor_court()
    game = game_at(court, host, hour_start() + timedelta(hours=2))
    send()
    old = weather_alerts()
    assert len(old) == 1 and not old[0].read
    old[0].created_at = utcnow() - timedelta(hours=7)
    game.scheduled_at = hour_start() + timedelta(hours=3)
    db.session.commit()
    send()
    assert len(weather_alerts()) == 2
