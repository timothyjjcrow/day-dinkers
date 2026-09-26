"""Busy hours: check-ins and played games become relative, privacy-safe hour levels."""
from datetime import UTC, datetime, time, timedelta
import json
from zoneinfo import ZoneInfo

from test_business_governance import app, client, register, auth
from test_business_reviewed_versions import live
from backend.app import db
from backend.models import BlockedUser, BusinessProfile, CheckIn, Court, Game, GamePlayer, utcnow
from backend.routes import courts as courts_module

ZONE = ZoneInfo('America/Chicago')
MON, TUE, WED, SAT = 0, 1, 2, 5


def at_local(weeks_ago, weekday, hour, minute=0):
    """A past court-local time as the naive UTC the database stores."""
    today = datetime.now(ZONE).date()
    day = today - timedelta(days=(today.weekday() - weekday) % 7 + 7 * weeks_ago)
    return datetime.combine(day, time(hour, minute), ZONE).astimezone(UTC).replace(tzinfo=None)


def set_court_timezone(app, zone='America/Chicago'):
    with app.app_context():
        db.session.get(Court, 1).structured_hours = json.dumps({'timezone': zone} if zone else {})
        db.session.commit()


def add_checkin(user, when, court_id=1):
    db.session.add(CheckIn(court_id=court_id, user_id=user['user']['id'], checked_in_at=when,
                           checked_out_at=when + timedelta(hours=1), last_presence_ping_at=when))


def add_game(players, when, status='completed', court_id=1):
    game = Game(court_id=court_id, creator_id=players[0]['user']['id'], title='Doubles',
                scheduled_at=when, max_players=4, visibility='open', status=status)
    db.session.add(game)
    db.session.flush()
    for player in players:
        db.session.add(GamePlayer(game=game, user_id=player['user']['id']))


def history(client, headers=None):
    return client.get('/api/courts/1', headers=headers or {}).get_json()['checkin_history']


def seed_week_pattern(app, a, b, c):
    """Eleven visits: Sat mornings, Wed evenings and one Monday game."""
    with app.app_context():
        for user, weeks in ((a, (1, 2, 3)), (b, (1, 2)), (c, (1,))):
            for week in weeks:
                add_checkin(user, at_local(week, SAT, 9, 20))
        # The same people's game in the same hour must not count twice.
        add_game([a, b, c], at_local(1, SAT, 9))
        add_game([a, b], at_local(2, WED, 18))
        add_game([a, c], at_local(3, WED, 18), status='awaiting_confirmation')
        add_game([b], at_local(4, MON, 7), status='unresolved')
        # Games that were never played, or elsewhere, stay out.
        add_game([a], at_local(4, SAT, 9), status='cancelled')
        add_game([c], at_local(2, TUE, 10), status='expired')
        add_game([a], utcnow() + timedelta(days=2), status='upcoming')
        add_game([a, b, c], at_local(1, TUE, 12), court_id=2)
        db.session.commit()


def test_check_ins_and_played_games_count_once_per_player_hour(app, client):
    a, b, c = (register(client, f'busy-{name}@example.test') for name in 'abc')
    set_court_timezone(app)
    seed_week_pattern(app, a, b, c)

    data = history(client)
    assert data['sample_size'] == 11 and data['unique_players'] == 3
    assert data['sufficient_sample'] is True
    assert data['timezone'] == 'America/Chicago' and data['timezone_source'] == 'venue_local'
    assert data['windows'][0] == {'label': 'Sat 9–11 AM', 'count': 6}
    assert {'label': 'Wed 5–7 PM', 'count': 4} in data['windows']
    assert data['peak'] == 'Sat 9–11 AM'
    hours = data['hours']
    assert len(hours) == 7 and all(len(day) == 18 for day in hours)
    # Relative levels: the busiest hour is 4, everything else scales to it.
    assert hours[SAT][9 - 5] == 4
    assert hours[WED][18 - 5] == 3
    assert hours[MON][7 - 5] == 1
    assert sum(map(sum, hours)) == 8
    assert all(0 <= level <= 4 for day in hours for level in day)
    # The planner keeps its label list.
    assert client.get('/api/courts/1').get_json()['busy_times'] == data['windows']


def test_blocked_players_are_left_out_for_that_viewer_and_small_samples_stay_quiet(app, client):
    a, b, c = (register(client, f'blocked-{name}@example.test') for name in 'abc')
    viewer = register(client, 'blocked-viewer@example.test')
    set_court_timezone(app)
    seed_week_pattern(app, a, b, c)
    with app.app_context():
        db.session.add(BlockedUser(blocker_id=viewer['user']['id'], blocked_id=a['user']['id']))
        db.session.commit()

    mine = history(client, auth(viewer['token']))
    assert mine['sample_size'] == 6 and mine['unique_players'] == 2
    assert mine['sufficient_sample'] is False
    assert mine['hours'] == [] and mine['peak'] is None and mine['windows'] == []
    assert history(client)['sample_size'] == 11


def test_one_big_day_is_not_a_pattern_and_the_hint_needs_repeat_visits(app, client):
    crowd = [register(client, f'event-{index}@example.test') for index in range(12)]
    set_court_timezone(app)
    with app.app_context():
        for player in crowd:
            add_checkin(player, at_local(1, SAT, 9))
        db.session.commit()
    data = history(client)
    assert data['sample_size'] == 12 and data['unique_players'] == 12
    assert data['sufficient_sample'] is False
    assert data['hours'] == [] and data['peak'] is None

    with app.app_context():
        for week in (1, 2):
            add_checkin(crowd[0], at_local(week, TUE, 19))
        db.session.commit()
    data = history(client)
    assert data['sufficient_sample'] is True and len(data['hours']) == 7
    # Twelve visits on a single Saturday are not "usually"; Tue has only two.
    assert data['peak'] is None

    with app.app_context():
        add_checkin(crowd[1], at_local(3, TUE, 20))
        db.session.commit()
    assert history(client)['peak'] == 'Tue 7–9 PM'


def test_signed_out_viewers_get_aggregates_only(app, client):
    a, b, c = (register(client, f'public-{name}@example.test', name=f'Player {name.upper()}') for name in 'abc')
    set_court_timezone(app)
    seed_week_pattern(app, a, b, c)
    data = history(client)
    assert set(data) == {
        'sample_size', 'unique_players', 'range_start', 'range_end', 'timezone', 'timezone_source',
        'sufficient_sample', 'windows', 'hours', 'peak', 'local_now',
    }
    assert data['peak'] == 'Sat 9–11 AM'
    text = json.dumps(data)
    for person in (a, b, c):
        assert person['user']['display_name'] not in text


def test_local_now_uses_court_time(app, client):
    set_court_timezone(app)
    before = datetime.now(ZONE)
    now = history(client)['local_now']
    after = datetime.now(ZONE)
    assert (now['weekday'], now['hour']) in {(before.weekday(), before.hour), (after.weekday(), after.hour)}


def test_courts_without_hours_use_the_venue_timezone(app, client, live):
    set_court_timezone(app, None)
    owner, business_id = live
    assert history(client)['timezone_source'] == 'approximate'
    with app.app_context():
        db.session.get(BusinessProfile, business_id).timezone = 'America/Denver'
        db.session.commit()
    data = history(client)
    assert data['timezone'] == 'America/Denver' and data['timezone_source'] == 'venue_local'


def test_raw_visits_are_cached_briefly_and_filtered_per_viewer_after(app, client):
    a, b, c = (register(client, f'cache-{name}@example.test') for name in 'abc')
    set_court_timezone(app)
    seed_week_pattern(app, a, b, c)
    courts_module._BUSY_CACHE.clear()
    app.config['TESTING'] = False
    try:
        with app.app_context():
            court = db.session.get(Court, 1)
            first = courts_module._busy_times(court, detailed=True)
            add_checkin(c, at_local(5, SAT, 9))
            db.session.commit()
            assert courts_module._busy_times(court, detailed=True)['sample_size'] == first['sample_size'] == 11
            # Viewer-specific hiding still applies to the cached rows.
            hidden = courts_module._busy_times(court, detailed=True, hidden_ids={a['user']['id']})
            assert hidden['sample_size'] == 6
            courts_module._BUSY_CACHE[1]['expires_at'] = 0
            assert courts_module._busy_times(court, detailed=True)['sample_size'] == 12
    finally:
        app.config['TESTING'] = True
        courts_module._BUSY_CACHE.clear()
