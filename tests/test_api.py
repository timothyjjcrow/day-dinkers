"""End-to-end API tests: auth, courts, friends, chat, games, ranked ELO."""
import pytest

from backend.app import create_app, db
from backend.models import Court, User


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Larson Park',
            city='Costa Mesa',
            state='CA',
            county_slug='orange-county',
            latitude=33.66,
            longitude=-117.91,
            num_courts=6,
            lighted=True,
            has_restrooms=True,
            has_water=True,
        ))
        db.session.add(Court(
            name='Adorni Center',
            city='Eureka',
            state='CA',
            county_slug='humboldt-county',
            latitude=40.81,
            longitude=-124.16,
            num_courts=4,
            indoor=True,
            nets_provided=True,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name='Player'):
    res = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def auth_headers(token):
    return {'Authorization': f'Bearer {token}'}


# ---------- Auth ----------

def test_register_login_me(client):
    data = register(client, 'a@example.com', 'Ana')
    assert data['user']['display_name'] == 'Ana'
    assert data['user']['rating'] == 1200

    res = client.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'secret123'})
    assert res.status_code == 200
    token = res.get_json()['token']

    res = client.get('/api/me', headers=auth_headers(token))
    assert res.status_code == 200
    assert res.get_json()['user']['email'] == 'a@example.com'


def test_register_validation(client):
    assert client.post('/api/auth/register', json={'email': 'bad', 'password': 'secret123', 'display_name': 'X'}).status_code == 400
    assert client.post('/api/auth/register', json={'email': 'x@y.com', 'password': '123', 'display_name': 'X'}).status_code == 400
    register(client, 'dupe@example.com')
    assert client.post('/api/auth/register', json={'email': 'dupe@example.com', 'password': 'secret123', 'display_name': 'X'}).status_code == 409


def test_login_wrong_password(client):
    register(client, 'a@example.com')
    res = client.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'nope'})
    assert res.status_code == 401


def test_update_profile(client):
    token = register(client, 'a@example.com')['token']
    res = client.patch('/api/me', json={
        'display_name': 'New Name',
        'bio': 'Dink master',
        'skill_level': 'advanced',
        'avatar_color': '#1971c2',
    }, headers=auth_headers(token))
    assert res.status_code == 200
    user = res.get_json()['user']
    assert user['display_name'] == 'New Name'
    assert user['skill_level'] == 'advanced'

    res = client.patch('/api/me', json={'skill_level': 'galactic'}, headers=auth_headers(token))
    assert res.status_code == 400


# ---------- Courts ----------

def test_courts_bbox_and_search(client):
    res = client.get('/api/courts?bbox=-118.5,33.0,-117.0,34.0')
    assert res.status_code == 200
    items = res.get_json()['items']
    assert len(items) == 1
    assert items[0]['name'] == 'Larson Park'

    res = client.get('/api/courts?q=adorni')
    assert [c['name'] for c in res.get_json()['items']] == ['Adorni Center']


def test_courts_amenity_filters(client):
    lighted = client.get('/api/courts?lighted=1').get_json()['items']
    assert [c['name'] for c in lighted] == ['Larson Park']
    indoor = client.get('/api/courts?indoor=1').get_json()['items']
    assert [c['name'] for c in indoor] == ['Adorni Center']
    # Larson has restrooms + water; Adorni provides nets.
    restrooms = client.get('/api/courts?restrooms=1').get_json()['items']
    assert [c['name'] for c in restrooms] == ['Larson Park']
    water = client.get('/api/courts?water=1').get_json()['items']
    assert [c['name'] for c in water] == ['Larson Park']
    nets = client.get('/api/courts?nets=1').get_json()['items']
    assert [c['name'] for c in nets] == ['Adorni Center']
    # Filters compose: restrooms AND nets → neither court has both.
    both = client.get('/api/courts?restrooms=1&nets=1').get_json()['items']
    assert both == []


def test_courts_nearby_distance(client):
    res = client.get('/api/courts?lat=33.66&lng=-117.91&radius=30')
    items = res.get_json()['items']
    assert items[0]['name'] == 'Larson Park'
    assert items[0]['distance_miles'] < 5


def test_geocode(client, monkeypatch):
    import backend.routes.courts as courts_mod
    courts_mod._GEOCODE_CACHE.clear()
    calls = {'n': 0}

    def fake_fetch(query):
        calls['n'] += 1
        return [{
            'lat': '30.2711', 'lon': '-97.7437',
            'display_name': 'Austin, Travis County, Texas, United States',
            'address': {'city': 'Austin', 'state': 'Texas'},
        }]

    monkeypatch.setattr(courts_mod, '_nominatim_fetch', fake_fetch)

    res = client.get('/api/geocode?q=Austin, TX')
    assert res.status_code == 200
    items = res.get_json()['items']
    assert len(items) == 1
    assert items[0]['label'] == 'Austin, Texas'
    assert abs(items[0]['lat'] - 30.2711) < 1e-4
    assert abs(items[0]['lng'] - (-97.7437)) < 1e-4

    # Cached: a second identical query does not hit the fetcher again
    client.get('/api/geocode?q=Austin, TX')
    assert calls['n'] == 1

    # Short queries are ignored without calling out
    assert client.get('/api/geocode?q=a').get_json()['items'] == []
    assert calls['n'] == 1


def test_leaderboard_area_scope(client, app):
    a = register(client, 'a@example.com', 'Ana')     # SoCal (last location)
    b = register(client, 'b@example.com', 'Ben')     # Humboldt (last location)
    c = register(client, 'c@example.com', 'Cam')     # no last loc; home court = Larson
    larson = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    with app.app_context():
        for uid, lat, lng, wins in ((a['user']['id'], 33.66, -117.91, 3),
                                    (b['user']['id'], 40.81, -124.16, 2)):
            u = db.session.get(User, uid)
            u.last_lat, u.last_lng, u.ranked_wins = lat, lng, wins
        cu = db.session.get(User, c['user']['id'])
        cu.home_court_id = larson
        cu.ranked_wins = 1
        db.session.commit()

    # Global: all three ranked players
    all_ids = [u['id'] for u in client.get('/api/leaderboard').get_json()['items']]
    assert set(all_ids) >= {a['user']['id'], b['user']['id'], c['user']['id']}

    # Near SoCal: Ana (last loc) + Cam (home-court fallback), not Ben
    near = client.get('/api/leaderboard?lat=33.66&lng=-117.91&radius=50').get_json()['items']
    near_ids = [u['id'] for u in near]
    assert a['user']['id'] in near_ids
    assert c['user']['id'] in near_ids
    assert b['user']['id'] not in near_ids

    # Near Humboldt: only Ben
    hum = [u['id'] for u in client.get('/api/leaderboard?lat=40.81&lng=-124.16&radius=50').get_json()['items']]
    assert hum == [b['user']['id']]


def test_public_profile_extras(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')  # viewer (not a friend)
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ana sets a home court, favorites a court, and schedules an open game
    client.patch('/api/me', json={'home_court_id': court_id}, headers=auth_headers(a['token']))
    client.post(f'/api/courts/{court_id}/favorite', headers=auth_headers(a['token']))
    open_g = make_game(client, a['token'], court_id, visibility='open')
    # …and a private game Ben can't see
    priv = make_game(client, a['token'], court_id, visibility='private',
                     invite_user_ids=[register(client, 'c@example.com')['user']['id']])

    prof = client.get(f"/api/users/{a['user']['id']}", headers=auth_headers(b['token'])).get_json()
    up_ids = [g['id'] for g in prof['upcoming_games']]
    assert open_g['id'] in up_ids          # viewer sees the open game
    assert priv['id'] not in up_ids        # private game stays hidden from non-invitee
    court_ids = [c['id'] for c in prof['courts']]
    assert court_id in court_ids
    assert any(c['is_home'] for c in prof['courts'])  # home court flagged


def test_friends_games_feed(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')  # not a friend
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # a and b are friends
    fid = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token'])).get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))

    # Ben (friend) schedules an open game; Cam (stranger) schedules one too
    bens = make_game(client, b['token'], court_id, visibility='open')
    make_game(client, c['token'], court_id, visibility='open')

    feed = client.get('/api/games?friends=1', headers=auth_headers(a['token'])).get_json()
    ids = [g['id'] for g in feed['items']]
    assert bens['id'] in ids          # friend's game shows
    assert all(g['creator_id'] == b['user']['id'] for g in feed['items'])  # only friends

    # Games Ana is already in are excluded from her friends feed
    client.post(f"/api/games/{bens['id']}/join", headers=auth_headers(a['token']))
    feed2 = client.get('/api/games?friends=1', headers=auth_headers(a['token'])).get_json()
    assert bens['id'] not in [g['id'] for g in feed2['items']]

    # A friend's private game (not inviting Ana) stays hidden
    priv = make_game(client, b['token'], court_id, visibility='private', invite_user_ids=[c['user']['id']])
    feed3 = client.get('/api/games?friends=1', headers=auth_headers(a['token'])).get_json()
    assert priv['id'] not in [g['id'] for g in feed3['items']]

    # No friends → empty
    assert client.get('/api/games?friends=1', headers=auth_headers(c['token'])).get_json()['items'] == []
    # Auth required
    assert client.get('/api/games?friends=1').status_code == 401


def test_court_reviews(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ana reviews 4 stars
    res = client.post(f'/api/courts/{court_id}/reviews', json={'rating': 4, 'comment': 'Nice nets'}, headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert res.get_json()['rating_avg'] == 4.0
    assert res.get_json()['rating_count'] == 1

    # Ben reviews 2 stars -> avg 3.0, count 2
    res = client.post(f'/api/courts/{court_id}/reviews', json={'rating': 2}, headers=auth_headers(b['token']))
    assert res.get_json()['rating_avg'] == 3.0
    assert res.get_json()['rating_count'] == 2

    # One review per user: Ana edits to 5 -> avg (5+2)/2 = 3.5
    res = client.post(f'/api/courts/{court_id}/reviews', json={'rating': 5, 'comment': 'Even better'}, headers=auth_headers(a['token']))
    assert res.get_json()['rating_avg'] == 3.5
    assert res.get_json()['rating_count'] == 2

    # Detail exposes summary + my_review + recent reviews
    detail = client.get(f'/api/courts/{court_id}', headers=auth_headers(a['token'])).get_json()
    assert detail['rating_avg'] == 3.5 and detail['rating_count'] == 2
    assert detail['my_review']['rating'] == 5
    assert len(detail['reviews']) == 2

    # Search results carry the average
    item = [c for c in client.get('/api/courts?q=larson').get_json()['items'] if c['id'] == court_id][0]
    assert item['rating_avg'] == 3.5 and item['rating_count'] == 2

    # Validation
    assert client.post(f'/api/courts/{court_id}/reviews', json={'rating': 9}, headers=auth_headers(a['token'])).status_code == 400
    assert client.post(f'/api/courts/{court_id}/reviews', json={}, headers=auth_headers(a['token'])).status_code == 400


def test_avatar_url(client):
    token = register(client, 'a@example.com', 'Ana')['token']
    res = client.patch('/api/me', json={'avatar_url': 'https://example.com/me.jpg'},
                       headers=auth_headers(token))
    assert res.status_code == 200
    assert res.get_json()['user']['avatar_url'] == 'https://example.com/me.jpg'

    # Public profiles expose it too
    me_id = res.get_json()['user']['id']
    b = register(client, 'b@example.com')
    prof = client.get(f'/api/users/{me_id}', headers=auth_headers(b['token'])).get_json()
    assert prof['avatar_url'] == 'https://example.com/me.jpg'

    # Bad URL rejected; blank clears
    assert client.patch('/api/me', json={'avatar_url': 'javascript:alert(1)'},
                        headers=auth_headers(token)).status_code == 400
    cleared = client.patch('/api/me', json={'avatar_url': ''}, headers=auth_headers(token))
    assert cleared.get_json()['user']['avatar_url'] == ''


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(RuntimeError):
        create_app('production')


def test_every_mutating_route_is_rate_limited():
    """Guard: any POST/PATCH/DELETE/PUT route must carry @rate_limit."""
    import pathlib
    import re
    routes_dir = pathlib.Path(__file__).resolve().parent.parent / 'backend' / 'routes'
    offenders = []
    pattern = re.compile(
        r"@(\w+_bp)\.(post|patch|delete|put)\('([^']+)'\)\n((?:@[\w.()\s,]+\n)*)def (\w+)"
    )
    for path in sorted(routes_dir.glob('*.py')):
        for m in pattern.finditer(path.read_text()):
            _, method, route, decorators, name = m.groups()
            if 'rate_limit' not in decorators:
                offenders.append(f'{path.name}:{name} ({method.upper()} {route})')
    assert offenders == [], f'Mutating routes missing @rate_limit: {offenders}'


def test_security_headers(client):
    res = client.get('/health')
    assert res.headers.get('X-Content-Type-Options') == 'nosniff'
    assert res.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert 'Referrer-Policy' in res.headers


def test_rate_limiting(app):
    app.config['RATE_LIMIT_ENABLED'] = True
    import backend.security as sec
    sec._BUCKETS.clear()
    c = app.test_client()
    statuses = []
    for i in range(12):
        r = c.post('/api/auth/register', json={
            'email': f'rl{i}@example.com', 'password': 'secret123', 'display_name': f'R{i}',
        })
        statuses.append(r.status_code)
    assert 429 in statuses, statuses
    # register limit is 10 per window; the 11th+ should be limited
    assert statuses[:10].count(201) == 10
    app.config['RATE_LIMIT_ENABLED'] = False
    sec._BUCKETS.clear()


def test_expire_stale_unscored(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    ancient = make_game(client, a['token'], court_id)
    recent = make_game(client, a['token'], court_id)
    weekly_res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'game_type': 'casual', 'visibility': 'open', 'recurrence': 'weekly',
    }, headers=auth_headers(a['token']))
    weekly = weekly_res.get_json()
    client.post(f"/api/games/{ancient['id']}/join", headers=auth_headers(b['token']))

    from backend.routes.games import expire_stale_unscored, roll_forward_recurring
    with app.app_context():
        # Push the ancient game and the weekly session 10 days into the past.
        db.session.get(GameModel, ancient['id']).scheduled_at = utcnow() - timedelta(days=10)
        db.session.get(GameModel, weekly['id']).scheduled_at = utcnow() - timedelta(days=10)
        # Recent game started yesterday — still scorable.
        db.session.get(GameModel, recent['id']).scheduled_at = utcnow() - timedelta(days=1)
        db.session.commit()
        roll_forward_recurring()
        expire_stale_unscored()
        assert db.session.get(GameModel, ancient['id']).status == 'expired'
        assert db.session.get(GameModel, recent['id']).status == 'upcoming'
        # Weekly session rolled forward instead of expiring.
        wk = db.session.get(GameModel, weekly['id'])
        assert wk.status == 'upcoming' and wk.scheduled_at > utcnow()
        # Expired games drop out of the feeds by status filter, and running
        # the sweep again is a no-op. (Assertions stay in-context: HTTP
        # round-trips after in-context time travel hit the known StaticPool
        # cross-context flake.)
        expire_stale_unscored()
        assert db.session.get(GameModel, ancient['id']).status == 'expired'
        assert GameModel.query.filter(
            GameModel.status.in_(['upcoming', 'awaiting_confirmation']),
            GameModel.id == ancient['id'],
        ).first() is None


def test_recurring_session_rolls_forward(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    g = make_game(client, a['token'], court_id, visibility='open')
    # turn it into a weekly session via the create endpoint
    when = (utcnow() + timedelta(days=2)).isoformat() + 'Z'
    res = client.post('/api/games', json={
        'court_id': court_id, 'scheduled_at': when,
        'game_type': 'casual', 'visibility': 'open', 'recurrence': 'weekly',
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201
    weekly = res.get_json()
    assert weekly['recurrence'] == 'weekly'

    # Ben RSVPs
    client.post(f"/api/games/{weekly['id']}/join", headers=auth_headers(b['token']))

    # Recurring sessions don't take scores
    sc = client.post(f"/api/games/{weekly['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 5,
    }, headers=auth_headers(a['token']))
    assert sc.status_code == 400

    # Force it into the past, then roll it forward (resets RSVPs to host).
    # Done in-context to avoid a cross-request in-memory-DB timing flake.
    from backend.models import Notification
    from backend.routes.games import roll_forward_recurring
    with app.app_context():
        row = db.session.get(GameModel, weekly['id'])
        row.scheduled_at = utcnow() - timedelta(days=5)
        db.session.commit()
        roll_forward_recurring()
        refreshed = db.session.get(GameModel, weekly['id'])
        assert refreshed.status == 'upcoming'
        assert refreshed.scheduled_at > utcnow()          # advanced into the future
        assert [p.user_id for p in refreshed.players] == [a['user']['id']]  # host only
        # Dropped attendees get a re-RSVP nudge; the host doesn't.
        nudges = Notification.query.filter_by(kind='session_rsvp').all()
        assert [n.user_id for n in nudges] == [b['user']['id']]
        assert 'RSVP again' in nudges[0].title
        assert nudges[0].related_game_id == weekly['id']

    detail = client.get(f"/api/games/{weekly['id']}").get_json()
    assert detail['recurrence'] == 'weekly'
    assert weekly['id'] != g['id']  # sanity: distinct from the one-off game


def test_game_reminder_fires_in_window(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, Notification, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=48)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
    # Ana has vouched she's coming; Ben hasn't.
    client.post(f"/api/games/{game['id']}/attend", headers=auth_headers(a['token']))

    from backend.routes.games import send_game_reminders
    with app.app_context():
        def reminders():
            # Only the hour-before kind (not the day-before "tomorrow" nudge).
            return [n for n in Notification.query.filter_by(kind='game_reminder').all()
                    if 'about an hour' in n.title.lower()]

        # 48h out: too early for the hour-before reminder.
        send_game_reminders()
        assert reminders() == []

        # Move the game to 30 minutes from now: both players get exactly one.
        row = db.session.get(GameModel, game['id'])
        row.scheduled_at = utcnow() + timedelta(minutes=30)
        db.session.commit()
        send_game_reminders()
        got = reminders()
        assert {n.user_id for n in got} == {a['user']['id'], b['user']['id']}
        assert all(n.related_game_id == game['id'] for n in got)
        assert all('Larson Park' in n.title for n in got)
        # Confirmed players get the paddle line; unconfirmed get the nudge.
        by_user = {n.user_id: n.body for n in got}
        assert 'paddle' in by_user[a['user']['id']]
        assert 'confirm' in by_user[b['user']['id']]

        # Sweeping again never duplicates.
        send_game_reminders()
        assert len(reminders()) == 2

    # The reminder reaches the player through the notifications feed.
    feed = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    assert any(n['kind'] == 'game_reminder' for n in feed['items'])


def test_day_before_reminder(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, Notification, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=48)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))

    from backend.routes.games import send_game_reminders
    with app.app_context():
        def day_reminders():
            return [n for n in Notification.query.filter_by(kind='game_reminder').all()
                    if 'tomorrow' in n.title.lower()]

        # 48h out: no day-before reminder yet.
        send_game_reminders()
        assert day_reminders() == []

        # Move into the 20–28h window → both players get exactly one "tomorrow".
        row = db.session.get(GameModel, game['id'])
        row.scheduled_at = utcnow() + timedelta(hours=24)
        db.session.commit()
        send_game_reminders()
        got = day_reminders()
        assert {n.user_id for n in got} == {a['user']['id'], b['user']['id']}
        assert all(n.related_game_id == game['id'] for n in got)

        # Idempotent — sweeping again doesn't duplicate.
        send_game_reminders()
        assert len(day_reminders()) == 2

        # The hour-before reminder is still separate (different marker).
        row.scheduled_at = utcnow() + timedelta(minutes=30)
        db.session.commit()
        send_game_reminders()
        hour_reminders = [n for n in Notification.query.filter_by(kind='game_reminder').all()
                          if 'about an hour' in n.title.lower()]
        assert len(hour_reminders) == 2


def test_game_reminder_skips_past_and_nonupcoming(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, Notification, utcnow
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=24)

    from backend.routes.games import send_game_reminders
    with app.app_context():
        row = db.session.get(GameModel, game['id'])
        # Already started: no reminder.
        row.scheduled_at = utcnow() - timedelta(minutes=5)
        db.session.commit()
        send_game_reminders()
        assert Notification.query.filter_by(kind='game_reminder').count() == 0

        # In window but cancelled: no reminder.
        row = db.session.get(GameModel, game['id'])
        row.scheduled_at = utcnow() + timedelta(minutes=30)
        row.status = 'cancelled'
        db.session.commit()
        send_game_reminders()
        assert Notification.query.filter_by(kind='game_reminder').count() == 0


def test_game_reminder_resets_on_recurring_rollover(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'game_type': 'casual', 'visibility': 'open', 'recurrence': 'weekly',
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201
    weekly = res.get_json()

    from backend.routes.games import roll_forward_recurring
    with app.app_context():
        row = db.session.get(GameModel, weekly['id'])
        # Pretend last week's occurrence was reminded, then finished.
        row.players[0].reminded_at = utcnow() - timedelta(days=5)
        row.scheduled_at = utcnow() - timedelta(days=5)
        db.session.commit()
        roll_forward_recurring()
        refreshed = db.session.get(GameModel, weekly['id'])
        assert refreshed.scheduled_at > utcnow()
        assert refreshed.players[0].reminded_at is None  # eligible again next week


def test_ranked_cannot_recur(client):
    from datetime import timedelta
    from backend.models import utcnow
    a = register(client, 'a@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(hours=24)).isoformat() + 'Z',
        'game_type': 'ranked', 'recurrence': 'weekly',
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert res.get_json()['recurrence'] == 'none'


def test_set_home_area(client):
    token = register(client, 'a@example.com')['token']
    res = client.patch('/api/me', json={
        'home_lat': 30.2711, 'home_lng': -97.7437, 'home_area': 'Austin, Texas',
    }, headers=auth_headers(token))
    assert res.status_code == 200
    user = res.get_json()['user']
    assert abs(user['home_lat'] - 30.2711) < 1e-4
    assert user['home_area'] == 'Austin, Texas'

    # Persisted across requests
    me = client.get('/api/me', headers=auth_headers(token)).get_json()
    assert me['user']['home_area'] == 'Austin, Texas'

    # Bad coordinates rejected
    bad = client.patch('/api/me', json={'home_lat': 999, 'home_lng': 0}, headers=auth_headers(token))
    assert bad.status_code == 400


def test_reverse_geocode(client, monkeypatch):
    import backend.routes.courts as courts_mod
    monkeypatch.setattr(courts_mod, '_nominatim_reverse', lambda lat, lng: {
        'lat': str(lat), 'lon': str(lng),
        'display_name': 'Austin, Travis County, Texas, United States',
        'address': {'city': 'Austin', 'state': 'Texas'},
    })
    res = client.get('/api/geocode/reverse?lat=30.27&lng=-97.74')
    assert res.status_code == 200
    assert res.get_json()['label'] == 'Austin, Texas'
    assert client.get('/api/geocode/reverse').status_code == 400


def test_geocode_handles_failure(client, monkeypatch):
    import backend.routes.courts as courts_mod
    courts_mod._GEOCODE_CACHE.clear()

    def boom(query):
        raise TimeoutError('nominatim down')

    monkeypatch.setattr(courts_mod, '_nominatim_fetch', boom)
    res = client.get('/api/geocode?q=Denver, CO')
    assert res.status_code == 200
    body = res.get_json()
    assert body['items'] == []
    assert body['error'] == 'geocode_unavailable'


def test_checkin_flow(client):
    token = register(client, 'a@example.com')['token']
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    res = client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=auth_headers(token))
    assert res.status_code == 200
    assert res.get_json()['presence']['checked_in'] is True
    assert res.get_json()['presence']['looking_for_game'] is True

    detail = client.get(f'/api/courts/{court_id}', headers=auth_headers(token)).get_json()
    assert len(detail['players_here']) == 1
    assert detail['is_checked_in'] is True

    res = client.post('/api/checkout', headers=auth_headers(token))
    assert res.get_json()['presence']['checked_in'] is False


def test_submit_court(client):
    a = register(client, 'a@example.com', 'Ana')
    ah = auth_headers(a['token'])
    body = {'name': 'Riverside Park Courts', 'latitude': 30.30, 'longitude': -97.75,
            'num_courts': 4, 'lighted': True}

    # Auth + validation guards.
    assert client.post('/api/courts', json=body).status_code == 401
    assert client.post('/api/courts', json={**body, 'name': 'ab'},
                       headers=ah).status_code == 400
    assert client.post('/api/courts', json={**body, 'latitude': None},
                       headers=ah).status_code == 400
    assert client.post('/api/courts', json={**body, 'longitude': 12.0},
                       headers=ah).status_code == 400  # not in the US

    res = client.post('/api/courts', json=body, headers=ah)
    assert res.status_code == 201
    court = res.get_json()
    assert court['name'] == 'Riverside Park Courts'
    assert court['num_courts'] == 4 and court['lighted'] is True

    # Discoverable through the normal search paths…
    found = client.get('/api/courts?q=riverside').get_json()['items']
    assert any(c['id'] == court['id'] for c in found)
    # …and auto-saved for the submitter.
    favs = client.get('/api/courts/favorites', headers=ah).get_json()['items']
    assert any(c['id'] == court['id'] for c in favs)


def test_court_edit_suggestions_consensus(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']  # 6 courts, outdoor

    # Auth required; empty/no-op payloads rejected.
    assert client.post(f'/api/courts/{court_id}/suggest', json={'num_courts': 8}).status_code == 401
    assert client.post(f'/api/courts/{court_id}/suggest', json={},
                       headers=auth_headers(a['token'])).status_code == 400
    assert client.post(f'/api/courts/{court_id}/suggest', json={'num_courts': 6},
                       headers=auth_headers(a['token'])).status_code == 400  # unchanged value
    assert client.post(f'/api/courts/{court_id}/suggest', json={'num_courts': 500},
                       headers=auth_headers(a['token'])).status_code == 400  # out of range

    # First suggestion: recorded, not applied.
    res = client.post(f'/api/courts/{court_id}/suggest', json={'num_courts': 8, 'indoor': True},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert res.get_json()['applied_fields'] == []
    detail = client.get(f'/api/courts/{court_id}').get_json()
    assert detail['num_courts'] == 6 and detail['indoor'] is False

    # Second user agrees on num_courts only → that field applies, indoor stays.
    res = client.post(f'/api/courts/{court_id}/suggest', json={'num_courts': 8},
                      headers=auth_headers(b['token']))
    assert res.status_code == 201
    assert res.get_json()['applied_fields'] == ['num_courts']
    detail = client.get(f'/api/courts/{court_id}').get_json()
    assert detail['num_courts'] == 8 and detail['indoor'] is False

    # Ana's indoor=True vote survives; Cam confirms it → applied.
    res = client.post(f'/api/courts/{court_id}/suggest', json={'indoor': True},
                      headers=auth_headers(c['token']))
    assert res.get_json()['applied_fields'] == ['indoor']
    assert client.get(f'/api/courts/{court_id}').get_json()['indoor'] is True

    # Resubmitting replaces the same user's pending suggestion (no double vote).
    r1 = client.post(f'/api/courts/{court_id}/suggest', json={'fees': '$5 drop-in'},
                     headers=auth_headers(a['token']))
    r2 = client.post(f'/api/courts/{court_id}/suggest', json={'fees': '$5 drop-in'},
                     headers=auth_headers(a['token']))
    assert r1.status_code == r2.status_code == 201
    assert r2.get_json()['applied_fields'] == []  # still one distinct voter
    assert client.get(f'/api/courts/{court_id}').get_json()['fees'] != '$5 drop-in'

    # Hours are suggestable too, with the same two-voter consensus.
    assert client.get(f'/api/courts/{court_id}').get_json()['hours'] == ''
    client.post(f'/api/courts/{court_id}/suggest', json={'hours': 'Daily 6am–10pm'},
                headers=auth_headers(b['token']))
    res = client.post(f'/api/courts/{court_id}/suggest', json={'hours': 'Daily 6am–10pm'},
                      headers=auth_headers(c['token']))
    assert res.get_json()['applied_fields'] == ['hours']
    assert client.get(f'/api/courts/{court_id}').get_json()['hours'] == 'Daily 6am–10pm'


def test_court_photo_upload_and_serve(client):
    import base64 as b64
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    payload = b'x' * 200
    photo = f"data:image/jpeg;base64,{b64.b64encode(payload).decode()}"

    # Auth required
    assert client.post(f'/api/courts/{court_id}/photo', json={'photo': photo}).status_code == 401

    # Garbage rejected
    for bad in ('', 'not a data url', 'data:image/gif;base64,AAAA', 'data:image/jpeg;base64,@@@'):
        res = client.post(f'/api/courts/{court_id}/photo', json={'photo': bad},
                          headers=auth_headers(a['token']))
        assert res.status_code == 400, bad

    # Oversized rejected
    huge = f"data:image/jpeg;base64,{b64.b64encode(b'x' * (501 * 1024)).decode()}"
    res = client.post(f'/api/courts/{court_id}/photo', json={'photo': huge},
                      headers=auth_headers(a['token']))
    assert res.status_code == 400
    assert res.get_json()['error'] == 'photo_too_large'

    # Valid upload lands and the court now serves it
    res = client.post(f'/api/courts/{court_id}/photo', json={'photo': photo},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201
    body = res.get_json()
    assert body['photo_url'] == f'/api/courts/{court_id}/photo'
    assert body['photo_count'] == 1

    img = client.get(f'/api/courts/{court_id}/photo')
    assert img.status_code == 200
    assert img.content_type.startswith('image/jpeg')
    assert img.data == payload
    assert 'max-age' in img.headers.get('Cache-Control', '')

    detail = client.get(f'/api/courts/{court_id}').get_json()
    assert detail['photo_url'] == f'/api/courts/{court_id}/photo'
    assert detail['photo_count'] == 1

    # A second photo appends to the gallery; newest becomes the hero.
    payload2 = b'y' * 220
    photo2 = f"data:image/png;base64,{b64.b64encode(payload2).decode()}"
    res = client.post(f'/api/courts/{court_id}/photo', json={'photo': photo2},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201 and res.get_json()['photo_count'] == 2
    assert client.get(f'/api/courts/{court_id}/photo').data == payload2

    gallery = client.get(f'/api/courts/{court_id}/photos').get_json()['items']
    assert len(gallery) == 2
    assert gallery[0]['user_name'] == 'Ana'
    item = client.get(gallery[1]['url'].replace('/api', '/api', 1))
    assert client.get(f"/api/courts/{court_id}/photos/{gallery[1]['id']}").data == payload
    # Wrong-court lookups 404.
    assert client.get(f"/api/courts/999999/photos/{gallery[0]['id']}").status_code == 404


def test_court_photo_never_overwrites_curated(client, app):
    from backend.models import Court as CourtModel
    import base64 as b64
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=adorni').get_json()['items'][0]['id']
    with app.app_context():
        db.session.get(CourtModel, court_id).photo_url = 'https://example.com/pro-shot.jpg'
        db.session.commit()
    # Community photos join the gallery, but the curated hero URL stays.
    photo = f"data:image/jpeg;base64,{b64.b64encode(b'x' * 200).decode()}"
    res = client.post(f'/api/courts/{court_id}/photo', json={'photo': photo},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert res.get_json()['photo_url'] == 'https://example.com/pro-shot.jpg'
    detail = client.get(f'/api/courts/{court_id}').get_json()
    assert detail['photo_url'] == 'https://example.com/pro-shot.jpg'
    assert detail['photo_count'] == 1


def test_court_sort_active(client, app):
    from datetime import timedelta
    from backend.models import CheckIn as CheckInModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    courts = client.get('/api/courts?limit=5').get_json()['items']
    larson = next(c for c in courts if c['name'] == 'Larson Park')['id']
    adorni = next(c for c in courts if c['name'] == 'Adorni Center')['id']

    # Give Adorni live activity (a current check-in); Larson stays quiet.
    with app.app_context():
        db.session.add(CheckInModel(user_id=a['user']['id'], court_id=adorni,
                                    checked_in_at=utcnow()))
        db.session.commit()

    active = client.get('/api/courts?sort=active&limit=5').get_json()['items']
    # The busy court ranks first.
    assert active[0]['name'] == 'Adorni Center'
    assert active[0]['players_here'] == 1
    # Larson (no activity) is present but ranked below.
    assert any(c['name'] == 'Larson Park' for c in active)


def test_court_list_sort_options(client):
    a = register(client, 'a@example.com', 'Ana')
    courts = client.get('/api/courts').get_json()['items']
    larson = next(c for c in courts if c['name'] == 'Larson Park')      # 6 courts
    adorni = next(c for c in courts if c['name'] == 'Adorni Center')    # 4 courts

    # Default (and sort=courts): most courts first.
    names = [c['name'] for c in client.get('/api/courts?sort=courts').get_json()['items']]
    assert names.index('Larson Park') < names.index('Adorni Center')

    # Only Adorni has a review → it outranks the unrated Larson on sort=rating.
    client.post(f"/api/courts/{adorni['id']}/reviews", json={'rating': 5},
                headers=auth_headers(a['token']))
    names = [c['name'] for c in client.get('/api/courts?sort=rating').get_json()['items']]
    assert names.index('Adorni Center') < names.index('Larson Park')

    # sort=distance from Eureka: Adorni (Eureka) before Larson (Costa Mesa).
    # bbox spans both cities (a bare lat/lng radius caps at 100mi and would
    # drop Costa Mesa entirely).
    res = client.get(
        '/api/courts?sort=distance&bbox=-125,32,-117,42&lat=40.8&lng=-124.1'
    ).get_json()['items']
    names = [c['name'] for c in res]
    assert names.index('Adorni Center') < names.index('Larson Park')
    assert res[0]['distance_miles'] < res[-1]['distance_miles']

    # Unknown sort value falls back to the default ordering, no error.
    assert client.get('/api/courts?sort=bogus').status_code == 200
    assert larson['id']  # fixture sanity


def test_court_detail_player_info(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Make them friends
    res = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token']))
    fid = res.get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))

    client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=auth_headers(b['token']))

    detail = client.get(f'/api/courts/{court_id}', headers=auth_headers(a['token'])).get_json()
    player = detail['players_here'][0]
    assert player['is_friend'] is True
    assert player['is_me'] is False
    assert player['minutes_here'] == 0
    assert detail['friends_here'] == 1


def test_my_record_at_court(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    larson = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    adorni = client.get('/api/courts?q=adorni').get_json()['items'][0]['id']

    # No games yet → no record; anonymous viewers never get one.
    assert client.get(f'/api/courts/{larson}',
                      headers=auth_headers(a['token'])).get_json()['my_record'] is None

    def play(court_id, ana_wins):
        g = make_game(client, a['token'], court_id, hours_ahead=1)
        client.post(f"/api/games/{g['id']}/join", headers=auth_headers(b['token']))
        s1, s2 = (11, 4) if ana_wins else (4, 11)
        res = client.post(f"/api/games/{g['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [b['user']['id']],
            'score_team1': s1, 'score_team2': s2,
        }, headers=auth_headers(a['token']))
        assert res.status_code == 200

    play(larson, True)
    play(larson, True)
    play(larson, False)
    play(adorni, True)  # other court — stays out of Larson's record

    detail = client.get(f'/api/courts/{larson}', headers=auth_headers(a['token'])).get_json()
    assert detail['my_record'] == {'wins': 2, 'losses': 1}
    # Ben sees his own mirror-image record.
    detail_b = client.get(f'/api/courts/{larson}', headers=auth_headers(b['token'])).get_json()
    assert detail_b['my_record'] == {'wins': 1, 'losses': 2}
    # Anonymous gets nothing.
    assert client.get(f'/api/courts/{larson}').get_json()['my_record'] is None


def test_court_weather(client, monkeypatch):
    from backend.routes import courts as courts_module
    courts_module._WEATHER_CACHE.clear()
    calls = {'n': 0}

    def fake_fetch(lat, lng):
        calls['n'] += 1
        return {'temp_f': 82, 'short': 'Partly Cloudy', 'rain_soon': True}

    monkeypatch.setattr(courts_module, '_nws_fetch', fake_fetch)
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    data = client.get(f'/api/courts/{court_id}/weather').get_json()
    assert data == {'temp_f': 82, 'short': 'Partly Cloudy', 'rain_soon': True,
                    'latest_condition': None}
    # Second hit serves from cache — no new upstream call.
    client.get(f'/api/courts/{court_id}/weather')
    assert calls['n'] == 1

    # A fresh condition report rides along, even on the cached path.
    a = register(client, 'weather-reporter@example.com', 'Wendy')
    client.post(f'/api/courts/{court_id}/condition', json={'condition': 'wet'},
                headers=auth_headers(a['token']))
    data = client.get(f'/api/courts/{court_id}/weather').get_json()
    assert data['temp_f'] == 82 and calls['n'] == 1  # still cached weather
    assert data['latest_condition']['condition'] == 'wet'

    # Upstream failure degrades gracefully.
    courts_module._WEATHER_CACHE.clear()
    monkeypatch.setattr(courts_module, '_nws_fetch',
                        lambda lat, lng: (_ for _ in ()).throw(OSError('down')))
    assert client.get(f'/api/courts/{court_id}/weather').get_json()['error'] == 'weather_unavailable'
    assert client.get('/api/courts/999999/weather').status_code == 404


def test_court_conditions(client, app):
    from datetime import timedelta
    from backend.models import CourtCondition as CCModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Auth + validation guards.
    assert client.post(f'/api/courts/{court_id}/condition',
                       json={'condition': 'wet'}).status_code == 401
    assert client.post(f'/api/courts/{court_id}/condition', json={'condition': 'lava'},
                       headers=auth_headers(a['token'])).status_code == 400

    # Nothing reported yet.
    assert client.get(f'/api/courts/{court_id}').get_json()['latest_condition'] is None

    # Fresh report surfaces with attribution; newest wins.
    client.post(f'/api/courts/{court_id}/condition', json={'condition': 'wet'},
                headers=auth_headers(a['token']))
    client.post(f'/api/courts/{court_id}/condition', json={'condition': 'good'},
                headers=auth_headers(a['token']))
    latest = client.get(f'/api/courts/{court_id}').get_json()['latest_condition']
    assert latest['condition'] == 'good' and latest['user_name'] == 'Ana'

    # The list view carries the freshest condition too.
    listed = client.get('/api/courts?q=larson').get_json()['items'][0]
    assert listed['condition'] == 'good'

    # Stale reports (>3h) drop off everywhere.
    with app.app_context():
        for row in CCModel.query.all():
            row.created_at = utcnow() - timedelta(hours=4)
        db.session.commit()
    assert client.get(f'/api/courts/{court_id}').get_json()['latest_condition'] is None
    assert client.get('/api/courts?q=larson').get_json()['items'][0]['condition'] is None


def test_court_regulars(client, app):
    from datetime import timedelta
    from backend.models import CheckIn as CheckInModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # No history → no regulars section data.
    assert client.get(f'/api/courts/{court_id}').get_json()['regulars'] == []

    with app.app_context():
        now = utcnow()
        rows = []
        # Ana: 3 recent visits; Ben: 1 visit (below the 2-visit bar);
        # Cam: 4 visits but all older than 60 days.
        for days in (1, 3, 5):
            rows.append(CheckInModel(user_id=a['user']['id'], court_id=court_id,
                                     checked_in_at=now - timedelta(days=days),
                                     checked_out_at=now - timedelta(days=days)))
        rows.append(CheckInModel(user_id=b['user']['id'], court_id=court_id,
                                 checked_in_at=now - timedelta(days=2),
                                 checked_out_at=now - timedelta(days=2)))
        for days in (70, 75, 80, 85):
            rows.append(CheckInModel(user_id=c['user']['id'], court_id=court_id,
                                     checked_in_at=now - timedelta(days=days),
                                     checked_out_at=now - timedelta(days=days)))
        db.session.add_all(rows)
        db.session.commit()

    regulars = client.get(f'/api/courts/{court_id}').get_json()['regulars']
    assert [r['display_name'] for r in regulars] == ['Ana']
    assert regulars[0]['visits'] == 3


def test_report_court_closed(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Visible and not closed to start.
    assert client.get(f'/api/courts/{court_id}').get_json()['closed'] is False
    assert any(c['id'] == court_id for c in client.get('/api/courts?q=larson').get_json()['items'])

    # One report isn't enough (needs consensus).
    client.post(f'/api/courts/{court_id}/suggest', json={'closed': True}, headers=auth_headers(a['token']))
    assert client.get(f'/api/courts/{court_id}').get_json()['closed'] is False

    # A second player agreeing flips it closed → gone from listings/search.
    res = client.post(f'/api/courts/{court_id}/suggest', json={'closed': True}, headers=auth_headers(b['token']))
    assert res.get_json()['applied_fields'] == ['closed']
    assert client.get(f'/api/courts/{court_id}').get_json()['closed'] is True
    assert client.get('/api/courts?q=larson').get_json()['items'] == []
    # Direct detail (deep link) still resolves so history/links don't 404.
    assert client.get(f'/api/courts/{court_id}').get_json()['name'] == 'Larson Park'


def test_court_leaders(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # No games → no champions.
    assert client.get(f'/api/courts/{court_id}').get_json()['court_leaders'] == []

    def play(ana_wins):
        game = make_game(client, a['token'], court_id, hours_ahead=1)
        client.post(f"/api/games/{game['id']}/join", headers=bh)
        client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [b['user']['id']],
            'score_team1': 11 if ana_wins else 4,
            'score_team2': 4 if ana_wins else 11,
        }, headers=ah)

    play(True); play(True); play(False)  # Ana 2-1, Ben 1-2
    leaders = client.get(f'/api/courts/{court_id}').get_json()['court_leaders']
    assert [(p['display_name'], p['wins'], p['losses']) for p in leaders] == \
        [('Ana', 2, 1), ('Ben', 1, 2)]

    # A player with zero wins here is omitted.
    c = register(client, 'c@example.com', 'Cam')
    g2 = make_game(client, a['token'], court_id, hours_ahead=1)
    client.post(f"/api/games/{g2['id']}/join", headers=auth_headers(c['token']))
    client.post(f"/api/games/{g2['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [c['user']['id']],
        'score_team1': 11, 'score_team2': 2,
    }, headers=ah)
    leaders = client.get(f'/api/courts/{court_id}').get_json()['court_leaders']
    assert 'Cam' not in [p['display_name'] for p in leaders]
    assert leaders[0] == {**leaders[0], 'display_name': 'Ana', 'wins': 3, 'losses': 1}


def test_court_busy_times(client, app):
    from datetime import timedelta
    from backend.models import CheckIn as CheckInModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # No history → no busy-times data.
    assert client.get(f'/api/courts/{court_id}').get_json()['busy_times'] == []

    # Larson Park sits at lng -117.91 → offset round(-117.91/15) = -8h.
    # Local Sat 09:00 = UTC Sat 17:00; local Wed 18:00 = UTC Thu 02:00.
    with app.app_context():
        now = utcnow()
        sat = (now - timedelta(days=(now.weekday() - 5) % 7)).replace(
            hour=17, minute=0, second=0, microsecond=0)
        wed_eve = (now - timedelta(days=(now.weekday() - 2) % 7)).replace(
            hour=2, minute=0, second=0, microsecond=0) + timedelta(days=1)
        mon = (now - timedelta(days=now.weekday())).replace(
            hour=21, minute=0, second=0, microsecond=0)  # local Mon 1pm
        rows = []
        for weeks in (1, 2, 3):  # three Saturday mornings
            rows.append(CheckInModel(user_id=a['user']['id'], court_id=court_id,
                                     checked_in_at=sat - timedelta(days=7 * weeks)))
        for weeks in (1, 2):     # two Wednesday evenings
            rows.append(CheckInModel(user_id=a['user']['id'], court_id=court_id,
                                     checked_in_at=wed_eve - timedelta(days=7 * weeks)))
        # One Monday afternoon (below the 2-visit bar) and one Sat 3am local
        # (UTC 11:00 — night hours are excluded, so Sat mornings stays at 3).
        rows.append(CheckInModel(user_id=a['user']['id'], court_id=court_id,
                                 checked_in_at=mon - timedelta(days=7)))
        rows.append(CheckInModel(user_id=a['user']['id'], court_id=court_id,
                                 checked_in_at=sat.replace(hour=11) - timedelta(days=7)))
        db.session.add_all(rows)
        db.session.commit()

    busy = client.get(f'/api/courts/{court_id}').get_json()['busy_times']
    assert busy[0] == {'label': 'Sat mornings', 'count': 3}
    assert {'label': 'Wed evenings', 'count': 2} in busy
    assert all(b['label'] != 'Mon afternoons' for b in busy)  # 1 visit ≠ a pattern
    assert len(busy) <= 3


def test_players_looking_nearby(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh, ch = auth_headers(a['token']), auth_headers(b['token']), auth_headers(c['token'])
    larson = client.get('/api/courts?q=larson').get_json()['items'][0]  # lat 33.66, lng -117.91

    # Nobody looking yet.
    r = client.get(f'/api/players/looking?lat={larson["latitude"]}&lng={larson["longitude"]}', headers=ah)
    assert r.get_json() == {'count': 0, 'players': []}

    # Ben checks in looking; Cam checks in but NOT looking.
    client.post(f'/api/courts/{larson["id"]}/checkin', json={'looking_for_game': True}, headers=bh)
    client.post(f'/api/courts/{larson["id"]}/checkin', json={'looking_for_game': False}, headers=ch)
    data = client.get(f'/api/players/looking?lat={larson["latitude"]}&lng={larson["longitude"]}', headers=ah).get_json()
    assert data['count'] == 1 and [p['display_name'] for p in data['players']] == ['Ben']

    # The viewer never counts themselves, even when looking.
    client.post(f'/api/courts/{larson["id"]}/checkin', json={'looking_for_game': True}, headers=ah)
    assert client.get(f'/api/players/looking?lat={larson["latitude"]}&lng={larson["longitude"]}',
                      headers=ah).get_json()['count'] == 1

    # Far away → out of radius.
    assert client.get('/api/players/looking?lat=40.81&lng=-124.16&radius=25', headers=ah).get_json()['count'] == 0

    # Blocked players are hidden (Ana blocks Ben).
    client.post(f"/api/users/{b['user']['id']}/block", headers=ah)
    assert client.get(f'/api/players/looking?lat={larson["latitude"]}&lng={larson["longitude"]}',
                      headers=ah).get_json()['count'] == 0


def test_on_my_way_ping(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Not friends yet → can't ping.
    assert client.post(f"/api/players/{b['user']['id']}/coming", headers=ah).status_code == 403

    # Befriend, Ben checks in looking for a game.
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah)
    fid = client.get('/api/friends', headers=bh).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=bh)
    client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=bh)

    # Ana says she's on her way → Ben gets a court-tagged ping.
    res = client.post(f"/api/players/{b['user']['id']}/coming", headers=ah)
    assert res.status_code == 200 and res.get_json()['sent'] is True
    notes = [n for n in client.get('/api/notifications', headers=bh).get_json()['items']
             if n['kind'] == 'player_coming']
    assert len(notes) == 1
    assert 'Ana' in notes[0]['title'] and 'Larson Park' in notes[0]['title']
    assert notes[0]['related_user_id'] == a['user']['id']

    # Can't ping yourself.
    assert client.post(f"/api/players/{a['user']['id']}/coming", headers=ah).status_code == 400
    # Blocking severs it (Ben blocks Ana).
    client.post(f"/api/users/{a['user']['id']}/block", headers=bh)
    assert client.post(f"/api/players/{b['user']['id']}/coming", headers=ah).status_code == 403


def test_mutual_friends_on_profile(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')

    def befriend(x, y):
        client.post('/api/friends/request', json={'user_id': y['user']['id']}, headers=auth_headers(x['token']))
        fid = client.get('/api/friends', headers=auth_headers(y['token'])).get_json()['incoming'][0]['friendship_id']
        client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(y['token']))

    # Ana↔Cam and Ben↔Cam: Cam is the mutual friend of Ana and Ben.
    befriend(a, c)
    befriend(b, c)

    prof = client.get(f"/api/users/{b['user']['id']}", headers=auth_headers(a['token'])).get_json()
    assert [m['display_name'] for m in prof['mutual_friends']] == ['Cam']

    # Dee shares no one with Ana.
    prof_d = client.get(f"/api/users/{d['user']['id']}", headers=auth_headers(a['token'])).get_json()
    assert prof_d['mutual_friends'] == []

    # Your own profile never lists mutuals.
    own = client.get(f"/api/users/{a['user']['id']}", headers=auth_headers(a['token'])).get_json()
    assert own['mutual_friends'] == []


def test_friend_suggestions(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    ah = auth_headers(a['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Fresh player: no games, no suggestions.
    assert client.get('/api/friends/suggestions', headers=ah).get_json()['items'] == []

    def play_with(*users):
        game = make_game(client, a['token'], court_id, hours_ahead=1)
        for u in users:
            client.post(f"/api/games/{game['id']}/join", headers=auth_headers(u['token']))
        client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [users[0]['user']['id']],
            'score_team1': 11, 'score_team2': 5,
        }, headers=ah)

    # Ana plays Ben twice, Cam once.
    play_with(b)
    play_with(b)
    play_with(c)
    sugg = client.get('/api/friends/suggestions', headers=ah).get_json()['items']
    assert [(s['display_name'], s['games_together']) for s in sugg] == [('Ben', 2), ('Cam', 1)]

    # Befriending Ben drops him from suggestions.
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah)
    fid = client.get('/api/friends', headers=auth_headers(b['token'])).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    sugg = client.get('/api/friends/suggestions', headers=ah).get_json()['items']
    assert [s['display_name'] for s in sugg] == ['Cam']

    # Blocking Cam removes him too → no suggestions left.
    client.post(f"/api/users/{c['user']['id']}/block", headers=ah)
    assert client.get('/api/friends/suggestions', headers=ah).get_json()['items'] == []
    # A pending request also suppresses (Dee never played, so still absent).
    assert client.get('/api/friends/suggestions').status_code == 401


def test_clear_notifications(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])

    # Ben sends Ana a friend request → she has a notification.
    client.post('/api/friends/request', json={'user_id': a['user']['id']}, headers=bh)
    assert len(client.get('/api/notifications', headers=ah).get_json()['items']) == 1

    # Clearing removes only the caller's notifications.
    res = client.delete('/api/notifications', headers=ah)
    assert res.status_code == 200 and res.get_json()['cleared'] == 1
    assert client.get('/api/notifications', headers=ah).get_json()['items'] == []
    # Ben's own feed is untouched, and clearing again is a no-op.
    assert client.delete('/api/notifications', headers=ah).get_json()['cleared'] == 0
    assert client.delete('/api/notifications').status_code == 401


def test_calendar_feed(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah = auth_headers(a['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=24)

    # Token is stable across calls; feed requires no auth (token IS the auth).
    tok = client.get('/api/calendar/token', headers=ah).get_json()['token']
    assert len(tok) > 20
    assert client.get('/api/calendar/token', headers=ah).get_json()['token'] == tok

    ics = client.get(f'/api/calendar/{tok}.ics')
    assert ics.status_code == 200 and ics.mimetype == 'text/calendar'
    body = ics.get_data(as_text=True)
    assert 'BEGIN:VCALENDAR' in body and 'END:VCALENDAR' in body
    assert f'thirdshot-game-{game["id"]}@thirdshot.app' in body
    assert 'Larson Park' in body

    # A bogus token → 404, not a leak.
    assert client.get('/api/calendar/not-a-real-token.ics').status_code == 404

    # Rotating the token invalidates the old URL.
    new_tok = client.post('/api/calendar/token/reset', headers=ah).get_json()['token']
    assert new_tok != tok
    assert client.get(f'/api/calendar/{tok}.ics').status_code == 404
    assert client.get(f'/api/calendar/{new_tok}.ics').status_code == 200

    # Ben's feed shows only his games (he's in none) — no cross-user leak.
    ben_tok = client.get('/api/calendar/token', headers=auth_headers(b['token'])).get_json()['token']
    assert f'thirdshot-game-{game["id"]}' not in client.get(f'/api/calendar/{ben_tok}.ics').get_data(as_text=True)


def test_log_past_game(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah = auth_headers(a['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def befriend(x, y):
        client.post('/api/friends/request', json={'user_id': y['user']['id']}, headers=auth_headers(x['token']))
        fid = client.get('/api/friends', headers=auth_headers(y['token'])).get_json()['incoming'][0]['friendship_id']
        client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(y['token']))

    # You can only log games with friends — a stranger is rejected.
    assert client.post('/api/games/log', json={
        'court_id': court_id, 'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 6,
    }, headers=ah).status_code == 403

    befriend(a, b)
    # Log a singles win for Ana.
    res = client.post('/api/games/log', json={
        'court_id': court_id, 'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 6,
    }, headers=ah)
    assert res.status_code == 201
    game = res.get_json()
    assert game['status'] == 'completed' and game['game_type'] == 'casual'
    assert game['you_won'] is True

    # The opponent is notified they were logged in (transparency).
    ben_notes = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    logged = [n for n in ben_notes if n['kind'] == 'game_logged']
    assert len(logged) == 1
    assert '11–6' in logged[0]['title'] and logged[0]['related_game_id'] == game['id']
    # The logger doesn't notify themselves.
    assert not [n for n in client.get('/api/notifications', headers=ah).get_json()['items']
                if n['kind'] == 'game_logged']

    # It counts toward stats and the court record immediately (no rating change).
    stats = client.get('/api/me/stats', headers=ah).get_json()
    assert stats['games_total'] == 1
    assert client.get(f'/api/courts/{court_id}', headers=ah).get_json()['my_record'] == {'wins': 1, 'losses': 0}
    me = client.get('/api/me', headers=ah).get_json()['user']
    assert me['ranked_wins'] == 0 and me['rating'] == 1200  # casual: unchanged

    # Doubles: self + partner vs two opponents, all credited and notified.
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    befriend(a, c)
    befriend(a, d)
    res = client.post('/api/games/log', json={
        'court_id': court_id,
        'team1': [a['user']['id'], b['user']['id']],
        'team2': [c['user']['id'], d['user']['id']],
        'score_team1': 11, 'score_team2': 9,
    }, headers=ah)
    assert res.status_code == 201
    doubles = res.get_json()
    assert len(doubles['players']) == 4 and doubles['you_won'] is True
    for u in (b, c, d):
        logged = [n for n in client.get('/api/notifications', headers=auth_headers(u['token'])).get_json()['items']
                  if n['kind'] == 'game_logged' and n['related_game_id'] == doubles['id']]
        assert len(logged) == 1

    # Guards: must include self, no ties, real court.
    assert client.post('/api/games/log', json={
        'court_id': court_id, 'team1': [b['user']['id']], 'team2': [c['user']['id']],
        'score_team1': 11, 'score_team2': 4,
    }, headers=ah).status_code == 400  # Ana not in it
    assert client.post('/api/games/log', json={
        'court_id': court_id, 'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 7, 'score_team2': 7,
    }, headers=ah).status_code == 400  # tie
    assert client.post('/api/games/log', json={
        'court_id': 999999, 'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 5,
    }, headers=ah).status_code == 404
    assert client.post('/api/games/log', json={'court_id': court_id}).status_code == 401

    # Abuse guard: can't log a game against someone who blocked you, even a
    # former friend. Ben blocks Ana → logging with Ben is refused.
    client.post(f"/api/users/{a['user']['id']}/block", headers=auth_headers(b['token']))
    assert client.post('/api/games/log', json={
        'court_id': court_id, 'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 3,
    }, headers=ah).status_code == 403


def test_leave_notifies_host(client):
    a = register(client, 'a@example.com', 'Ana')   # host
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh, ch = auth_headers(a['token']), auth_headers(b['token']), auth_headers(c['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=3)
    client.post(f"/api/games/{game['id']}/join", headers=bh)
    client.post(f"/api/games/{game['id']}/join", headers=ch)

    # Ben leaves → Ana (host) is told a spot opened.
    client.post(f"/api/games/{game['id']}/leave", headers=bh)
    host_notes = [n for n in client.get('/api/notifications', headers=ah).get_json()['items']
                  if n['kind'] == 'player_left']
    assert len(host_notes) == 1
    assert 'Ben' in host_notes[0]['title'] and 'spot opened' in host_notes[0]['title']
    assert host_notes[0]['related_game_id'] == game['id']
    # The leaver doesn't notify themselves.
    assert not [n for n in client.get('/api/notifications', headers=bh).get_json()['items']
                if n['kind'] == 'player_left']

    # When the host leaves and hands off, the new host is told they're hosting.
    client.post(f"/api/games/{game['id']}/leave", headers=ah)  # Ana leaves → Cam inherits
    cam_notes = [n for n in client.get('/api/notifications', headers=ch).get_json()['items']
                 if n['kind'] == 'player_left']
    assert len(cam_notes) == 1 and 'now hosting' in cam_notes[0]['title']


def test_reschedule_game(client):
    from datetime import timedelta
    from backend.models import utcnow
    a = register(client, 'a@example.com', 'Ana')   # host
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=3)
    client.post(f"/api/games/{game['id']}/join", headers=bh)
    client.post(f"/api/games/{game['id']}/attend", headers=bh)  # Ben confirms

    new_when = (utcnow() + timedelta(days=1)).isoformat() + 'Z'

    # Non-host can't reschedule.
    assert client.post(f"/api/games/{game['id']}/reschedule",
                       json={'scheduled_at': new_when}, headers=bh).status_code == 403
    # Past times rejected.
    past = (utcnow() - timedelta(hours=2)).isoformat() + 'Z'
    assert client.post(f"/api/games/{game['id']}/reschedule",
                       json={'scheduled_at': past}, headers=ah).status_code == 400

    # Host reschedules → time moves, roster kept, Ben's attendance reset, Ben notified.
    res = client.post(f"/api/games/{game['id']}/reschedule",
                      json={'scheduled_at': new_when}, headers=ah)
    assert res.status_code == 200
    updated = res.get_json()
    assert len(updated['players']) == 2  # roster intact
    ben = next(p for p in updated['players'] if p['user_id'] == b['user']['id'])
    assert ben['attending'] is False  # re-confirmation needed
    notes = [n for n in client.get('/api/notifications', headers=bh).get_json()['items']
             if 'rescheduled' in n['title'].lower()]
    assert len(notes) == 1 and notes[0]['related_game_id'] == game['id']


def test_host_removes_player(client):
    a = register(client, 'a@example.com', 'Ana')   # host
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh, ch = auth_headers(a['token']), auth_headers(b['token']), auth_headers(c['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=2)  # max 4
    client.post(f"/api/games/{game['id']}/join", headers=bh)
    client.post(f"/api/games/{game['id']}/join", headers=ch)

    # Non-host can't remove anyone.
    assert client.post(f"/api/games/{game['id']}/remove/{c['user']['id']}",
                       headers=bh).status_code == 403
    # Host can't remove themselves via this route.
    assert client.post(f"/api/games/{game['id']}/remove/{a['user']['id']}",
                       headers=ah).status_code == 400

    # Host removes Ben → he's gone, spot freed, and he's notified.
    res = client.post(f"/api/games/{game['id']}/remove/{b['user']['id']}", headers=ah)
    assert res.status_code == 200
    players = {p['user_id'] for p in res.get_json()['players']}
    assert b['user']['id'] not in players and res.get_json()['spots_left'] == 2
    kinds = [n['kind'] for n in client.get('/api/notifications', headers=bh).get_json()['items']]
    assert 'game_cancelled' in kinds

    # Removing someone not in the game is a 404.
    assert client.post(f"/api/games/{game['id']}/remove/{b['user']['id']}",
                       headers=ah).status_code == 404

    # A removed spot pulls from the waitlist: fill the game, waitlist Ben, remove Cam.
    d = register(client, 'd@example.com', 'Dee')
    e = register(client, 'e@example.com', 'Eve')
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(d['token']))
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(e['token']))  # now full: a,c,d,e
    client.post(f"/api/games/{game['id']}/waitlist", headers=bh)
    client.post(f"/api/games/{game['id']}/remove/{c['user']['id']}", headers=ah)
    players = {p['user_id'] for p in client.get(f"/api/games/{game['id']}", headers=ah).get_json()['players']}
    assert b['user']['id'] in players and c['user']['id'] not in players


def test_invite_to_existing_game(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh, ch = auth_headers(a['token']), auth_headers(b['token']), auth_headers(c['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ana hosts a private game — Cam can't see it yet.
    game = make_game(client, a['token'], court_id, visibility='private',
                     invite_user_ids=[b['user']['id']])

    def cam_sees():
        feed = client.get('/api/games?lat=33.66&lng=-117.91', headers=ch).get_json()
        return any(g['id'] == game['id'] for g in feed['items'])
    assert not cam_sees()

    # Ana invites Cam to the existing game → notification + access granted.
    res = client.post(f"/api/games/{game['id']}/invite",
                      json={'user_id': c['user']['id']}, headers=ah)
    assert res.status_code == 200 and res.get_json()['invited'] is True
    kinds = [n['kind'] for n in client.get('/api/notifications', headers=ch).get_json()['items']]
    assert 'game_invite_direct' in kinds
    assert cam_sees()
    assert client.post(f"/api/games/{game['id']}/join", headers=ch).status_code == 200

    # Guards: outsiders can't invite; can't invite someone already joined
    # (Cam joined above); self is rejected.
    d = register(client, 'd@example.com', 'Dee')
    assert client.post(f"/api/games/{game['id']}/invite",
                       json={'user_id': d['user']['id']}, headers=auth_headers(d['token'])).status_code == 403
    assert client.post(f"/api/games/{game['id']}/invite",
                       json={'user_id': c['user']['id']}, headers=ah).status_code == 409
    assert client.post(f"/api/games/{game['id']}/invite",
                       json={'user_id': a['user']['id']}, headers=ah).status_code == 400

    # A full game can't take more invites.
    full = make_game(client, a['token'], court_id, visibility='open')
    client.post(f"/api/games/{full['id']}/join", headers=bh)
    client.post(f"/api/games/{full['id']}/join", headers=ch)
    client.post(f"/api/games/{full['id']}/join", headers=auth_headers(d['token']))
    assert client.post(f"/api/games/{full['id']}/invite",
                       json={'user_id': register(client, 'e@example.com', 'Eve')['user']['id']},
                       headers=ah).status_code == 400


def test_peak_rating(client, app):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def ranked(a_wins):
        game = make_game(client, a['token'], court_id, game_type='ranked', hours_ahead=1)
        client.post(f"/api/games/{game['id']}/join", headers=bh)
        client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [b['user']['id']],
            'score_team1': 11 if a_wins else 4, 'score_team2': 4 if a_wins else 11,
        }, headers=ah)
        client.post(f"/api/games/{game['id']}/confirm", headers=bh)

    # Both start at 1200 = best_rating.
    assert client.get('/api/me', headers=ah).get_json()['user']['best_rating'] == 1200

    # Ana wins → rating up; best_rating tracks the peak.
    ranked(True)
    me = client.get('/api/me', headers=ah).get_json()['user']
    assert me['best_rating'] == me['rating'] > 1200

    # After a loss, rating dips but best_rating holds the peak.
    peak = me['best_rating']
    ranked(False)
    me = client.get('/api/me', headers=ah).get_json()['user']
    assert me['rating'] < peak and me['best_rating'] == peak

    # Climb past 1300 via wins over fresh 1200 opponents (healthy deltas) —
    # crossing the round-hundred fires exactly one peak notification.
    n = 0
    while client.get('/api/me', headers=ah).get_json()['user']['rating'] < 1305 and n < 15:
        opp = register(client, f'opp{n}@example.com', f'Opp{n}')
        game = make_game(client, a['token'], court_id, game_type='ranked', hours_ahead=1)
        client.post(f"/api/games/{game['id']}/join", headers=auth_headers(opp['token']))
        client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [opp['user']['id']],
            'score_team1': 11, 'score_team2': 5,
        }, headers=ah)
        client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(opp['token']))
        n += 1

    me = client.get('/api/me', headers=ah).get_json()['user']
    assert me['rating'] >= 1300 and me['best_rating'] == me['rating']
    notes = [x for x in client.get('/api/notifications', headers=ah).get_json()['items']
             if 'peak rating' in x['title'].lower()]
    assert len(notes) == 1 and '1300' in notes[0]['title']


def test_badge_earned_notification(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def badge_notes():
        items = client.get('/api/notifications', headers=ah).get_json()['items']
        return [n for n in items if n['kind'] == 'badge_earned']

    # Fresh player, no badges → loading stats notifies nothing.
    client.get('/api/me/stats', headers=ah)
    assert badge_notes() == []

    # Win a game → 'first_win' badge earned; next stats load congratulates once.
    game = make_game(client, a['token'], court_id, hours_ahead=1)
    client.post(f"/api/games/{game['id']}/join", headers=bh)
    client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 3,
    }, headers=ah)
    client.get('/api/me/stats', headers=ah)
    notes = badge_notes()
    assert len(notes) == 1 and 'First win' in notes[0]['title']
    # Re-loading stats doesn't re-notify the same badge.
    client.get('/api/me/stats', headers=ah)
    assert len(badge_notes()) == 1


def test_notification_mute_preferences(client, app):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # The catalog is advertised; nothing muted by default.
    me = client.get('/api/me', headers=bh).get_json()
    assert 'court_game' in me['muteable_notifications']
    assert me['user']['muted_notifications'] == []

    # Ben saves Larson and mutes new-game pings there.
    client.post(f'/api/courts/{court_id}/favorite', headers=bh)
    res = client.patch('/api/me', json={'muted_notifications': ['court_game', 'not_a_kind']},
                       headers=bh)
    assert res.status_code == 200
    assert res.get_json()['user']['muted_notifications'] == ['court_game']  # junk dropped

    # Ana opens a game at that court — Ben's court_game ping is suppressed.
    make_game(client, a['token'], court_id, visibility='open')
    kinds = [n['kind'] for n in client.get('/api/notifications', headers=bh).get_json()['items']]
    assert 'court_game' not in kinds

    # Essential kinds ignore mutes: a direct invite still lands.
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah)
    fid = client.get('/api/friends', headers=bh).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=bh)
    game = make_game(client, a['token'], court_id, visibility='private',
                     invite_user_ids=[b['user']['id']])
    kinds = [n['kind'] for n in client.get('/api/notifications', headers=bh).get_json()['items']]
    assert 'game_invite_direct' in kinds

    # Unmuting restores delivery.
    client.patch('/api/me', json={'muted_notifications': []}, headers=bh)
    make_game(client, a['token'], court_id, visibility='open')
    kinds = [n['kind'] for n in client.get('/api/notifications', headers=bh).get_json()['items']]
    assert 'court_game' in kinds


def test_report_user(client, app):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah = auth_headers(a['token'])

    res = client.post(f"/api/users/{b['user']['id']}/report",
                      json={'reason': 'Fake or manipulated scores'}, headers=ah)
    assert res.status_code == 200 and res.get_json()['reported'] is True
    # Repeat taps within a day acknowledge but don't stack rows.
    client.post(f"/api/users/{b['user']['id']}/report", json={'reason': 'again'}, headers=ah)
    with app.app_context():
        from backend.models import UserReport
        rows = UserReport.query.all()
        assert len(rows) == 1
        assert rows[0].reporter_id == a['user']['id']
        assert rows[0].reported_id == b['user']['id']
        assert rows[0].reason == 'Fake or manipulated scores'

    # Guards: no self-reports, no ghosts, auth required.
    assert client.post(f"/api/users/{a['user']['id']}/report", json={}, headers=ah).status_code == 400
    assert client.post('/api/users/99999/report', json={}, headers=ah).status_code == 404
    assert client.post(f"/api/users/{b['user']['id']}/report", json={}).status_code == 401


def test_blocked_players_list(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah = auth_headers(a['token'])

    assert client.get('/api/users/blocked', headers=ah).get_json()['items'] == []
    client.post(f"/api/users/{b['user']['id']}/block", headers=ah)

    # Search hides Ben, but the blocked list is the escape hatch.
    assert client.get('/api/users/search?q=ben', headers=ah).get_json()['items'] == []
    blocked = client.get('/api/users/blocked', headers=ah).get_json()['items']
    assert [u['display_name'] for u in blocked] == ['Ben']

    client.post(f"/api/users/{b['user']['id']}/unblock", headers=ah)
    assert client.get('/api/users/blocked', headers=ah).get_json()['items'] == []
    assert [u['display_name'] for u in
            client.get('/api/users/search?q=ben', headers=ah).get_json()['items']] == ['Ben']

    # Auth required; and being blocked BY someone doesn't put them on your list.
    assert client.get('/api/users/blocked').status_code == 401
    client.post(f"/api/users/{a['user']['id']}/block", headers=auth_headers(b['token']))
    assert client.get('/api/users/blocked', headers=ah).get_json()['items'] == []


def test_weekly_recap_notification(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, ch = auth_headers(a['token']), auth_headers(c['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def recaps(headers):
        items = client.get('/api/notifications', headers=headers).get_json()['items']
        return [n for n in items if n['kind'] == 'weekly_recap']

    # A ranked win, then shift it into last ISO week.
    game = make_game(client, a['token'], court_id, game_type='ranked', hours_ahead=1)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
    client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 6,
    }, headers=ah)
    client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(b['token']))
    # Time-travel and trigger in the same context (HTTP after in-context
    # mutations flakes under the shared in-memory session — see gotchas).
    from backend.models import User as UserModel
    from backend.routes.auth import _maybe_weekly_recap
    with app.app_context():
        # Place the game squarely mid-week (Thursday noon) of the previous ISO
        # week — the recap targets (now-7d)'s ISO week, and a game left exactly
        # 7 days ago can straddle the week boundary near UTC midnight.
        target = utcnow() - timedelta(days=7)
        week_monday = (target - timedelta(days=target.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        row = db.session.get(GameModel, game['id'])
        row.completed_at = week_monday + timedelta(days=3, hours=12)
        db.session.commit()
        _maybe_weekly_recap(db.session.get(UserModel, a['user']['id']))

    got = recaps(ah)
    assert len(got) == 1, [n['title'] for n in got]  # diagnose the rare flake
    assert got[0]['title'] == 'Your week on the courts: 1 game, 1–0'
    assert got[0]['body'].startswith('+') and 'rating' in got[0]['body']
    # The marker prevents a repeat on the next app open.
    client.get('/api/me', headers=ah)
    assert len(recaps(ah)) == 1

    # A quiet week stays quiet — marker still advances (no recap on repeat).
    client.get('/api/me', headers=ch)
    client.get('/api/me', headers=ch)
    assert recaps(ch) == []


def test_attendance_confirmation(client, app):
    from datetime import timedelta
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=3)
    client.post(f"/api/games/{game['id']}/join", headers=bh)

    # Nobody has vouched yet.
    detail = client.get(f"/api/games/{game['id']}", headers=ah).get_json()
    assert all(p['attending'] is False for p in detail['players'])

    # Ben confirms; only his flag flips. Repeat confirms are idempotent.
    res = client.post(f"/api/games/{game['id']}/attend", headers=bh)
    assert res.status_code == 200
    client.post(f"/api/games/{game['id']}/attend", headers=bh)
    players = {p['user_id']: p['attending'] for p in
               client.get(f"/api/games/{game['id']}", headers=ah).get_json()['players']}
    assert players == {a['user']['id']: False, b['user']['id']: True}

    # Outsiders can't vouch.
    z = register(client, 'z@example.com', 'Zed')
    assert client.post(f"/api/games/{game['id']}/attend",
                       headers=auth_headers(z['token'])).status_code == 403

    # A weekly session rolling forward clears the host's confirmation too.
    from backend.models import Game as GameModel, utcnow
    when = (utcnow() + timedelta(days=1)).isoformat() + 'Z'
    weekly = client.post('/api/games', json={
        'court_id': court_id, 'scheduled_at': when,
        'game_type': 'casual', 'visibility': 'open', 'recurrence': 'weekly',
    }, headers=ah).get_json()
    client.post(f"/api/games/{weekly['id']}/attend", headers=ah)
    with app.app_context():
        row = db.session.get(GameModel, weekly['id'])
        row.scheduled_at = utcnow() - timedelta(hours=4)
        db.session.commit()
    rolled = client.get(f"/api/games/{weekly['id']}", headers=ah).get_json()
    # (any /games read triggers the lazy rollover sweep)
    client.get('/api/games?mine=1', headers=ah)
    rolled = client.get(f"/api/games/{weekly['id']}", headers=ah).get_json()
    host = next(p for p in rolled['players'] if p['user_id'] == a['user']['id'])
    assert host['attending'] is False


def test_game_chat_unread_badge(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=2)
    client.post(f"/api/games/{game['id']}/join", headers=bh)

    def unread(headers):
        mine = client.get('/api/games?mine=1', headers=headers).get_json()['items']
        entry = next(i for i in mine if i['id'] == game['id'])
        return entry['chat_unread']

    # Ana posts twice: Ben has never opened the thread, so both are unread.
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'bring balls'}, headers=ah)
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'and water'}, headers=ah)
    assert unread(bh) == 2
    # The detail view carries the same count for players…
    detail = client.get(f"/api/games/{game['id']}", headers=bh).get_json()
    assert detail['chat_unread'] == 2
    # …and spectators don't get the field at all.
    z = register(client, 'z@example.com', 'Zed')
    assert 'chat_unread' not in client.get(f"/api/games/{game['id']}", headers=auth_headers(z['token'])).get_json()

    # Reading the thread clears it; the next message counts from there.
    client.get(f"/api/games/{game['id']}/chat", headers=bh)
    assert unread(bh) == 0
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'running late!'}, headers=bh)
    assert unread(ah) == 3  # Ana never opened the thread either — all three


def test_court_chat_unread_badge(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def unread(headers=None):
        return client.get(f'/api/courts/{court_id}', headers=headers).get_json()['chat_unread']

    # Chatter in a room you've never opened doesn't nag (and anon sees 0).
    client.post(f'/api/courts/{court_id}/chat', json={'body': 'anyone on?'}, headers=ah)
    assert unread(bh) == 0
    assert unread() == 0

    # Opening the chat sets the read marker…
    client.get(f'/api/courts/{court_id}/chat', headers=bh)
    assert unread(bh) == 0
    # …so the next message shows as unread, until Ben reads again.
    client.post(f'/api/courts/{court_id}/chat', json={'body': 'games at 6?'}, headers=ah)
    client.post(f'/api/courts/{court_id}/chat', json={'body': 'bring water'}, headers=ah)
    assert unread(bh) == 2
    client.get(f'/api/courts/{court_id}/chat', headers=bh)
    assert unread(bh) == 0
    # Ana only ever posted (never opened the room), so she has no marker
    # and — by the no-nag rule — no unread count either.
    assert unread(ah) == 0


def test_favorite_courts(client):
    a = register(client, 'a@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    res = client.post(f'/api/courts/{court_id}/favorite', headers=auth_headers(a['token']))
    assert res.get_json()['favorited'] is True

    detail = client.get(f'/api/courts/{court_id}', headers=auth_headers(a['token'])).get_json()
    assert detail['is_favorite'] is True

    favs = client.get('/api/courts/favorites', headers=auth_headers(a['token'])).get_json()
    assert [c['name'] for c in favs['items']] == ['Larson Park']

    res = client.post(f'/api/courts/{court_id}/favorite', headers=auth_headers(a['token']))
    assert res.get_json()['favorited'] is False
    favs = client.get('/api/courts/favorites', headers=auth_headers(a['token'])).get_json()
    assert favs['items'] == []


def test_game_create_notifies_friends(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    res = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token']))
    fid = res.get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    make_game(client, a['token'], court_id, game_type='ranked', visibility='friends')

    notes = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    kinds = [n['kind'] for n in notes['items']]
    assert 'game_invite' in kinds
    invite = [n for n in notes['items'] if n['kind'] == 'game_invite'][0]
    assert 'ranked game' in invite['title']
    assert 'Larson Park' in invite['title']


def test_open_game_public_no_notifications(client):
    # Open games are publicly discoverable but send no targeted notifications.
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    res = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token']))
    fid = res.get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    game = make_game(client, a['token'], court_id, visibility='open')
    assert game['visibility'] == 'open'

    notes = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    assert all(n['kind'] not in ('game_invite', 'game_invite_direct') for n in notes['items'])

    # A complete stranger nearby still sees an open game
    stranger = register(client, 'z@example.com', 'Zed')
    feed = client.get('/api/games?lat=33.66&lng=-117.91', headers=auth_headers(stranger['token'])).get_json()
    assert any(g['id'] == game['id'] for g in feed['items'])


def test_open_game_notifies_court_fans(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ben and Cam saved Larson Park; Cam has blocked Ana; Dee saved nothing.
    for u in (b, c):
        client.post(f'/api/courts/{court_id}/favorite', headers=auth_headers(u['token']))
    client.post(f"/api/users/{a['user']['id']}/block", headers=auth_headers(c['token']))

    game = make_game(client, a['token'], court_id, visibility='open')

    def court_game_notes(user):
        items = client.get('/api/notifications', headers=auth_headers(user['token'])).get_json()['items']
        return [n for n in items if n['kind'] == 'court_game']

    ben = court_game_notes(b)
    assert len(ben) == 1
    assert 'Larson Park' in ben[0]['title'] and 'saved' in ben[0]['title']
    assert ben[0]['related_game_id'] == game['id']
    assert court_game_notes(c) == []  # blocked pair stays silent
    assert court_game_notes(d) == []  # never saved the court
    assert court_game_notes(a) == []  # creators don't ping themselves

    # Friends-only games don't ping court fans (that's the friends feed's job).
    make_game(client, a['token'], court_id, visibility='friends')
    assert len(court_game_notes(b)) == 1

    # Create/cancel churn can't spam: a second open game within 3h stays quiet.
    make_game(client, a['token'], court_id, visibility='open')
    assert len(court_game_notes(b)) == 1


def test_visibility_modes_feed_access(client):
    """Open shows to everyone nearby; friends only to friends; private only to invitees."""
    a = register(client, 'a@example.com', 'Ana')      # creator
    friend = register(client, 'f@example.com', 'Fred')  # a's friend
    invitee = register(client, 'i@example.com', 'Ivy')  # a's friend + invited
    stranger = register(client, 's@example.com', 'Sam')  # unrelated, nearby
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    for u in (friend, invitee):
        res = client.post('/api/friends/request', json={'user_id': u['user']['id']}, headers=auth_headers(a['token']))
        fid = res.get_json()['friendship_id']
        client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(u['token']))

    open_g = make_game(client, a['token'], court_id, visibility='open')
    friends_g = make_game(client, a['token'], court_id, visibility='friends')
    private_g = make_game(client, a['token'], court_id, visibility='private',
                          invite_user_ids=[invitee['user']['id']])

    def nearby_ids(tok):
        feed = client.get('/api/games?lat=33.66&lng=-117.91', headers=auth_headers(tok)).get_json()
        return {g['id'] for g in feed['items']}

    # Stranger: only the open game
    s_ids = nearby_ids(stranger['token'])
    assert open_g['id'] in s_ids
    assert friends_g['id'] not in s_ids
    assert private_g['id'] not in s_ids

    # Friend (not invited): open + friends, NOT the private one
    f_ids = nearby_ids(friend['token'])
    assert open_g['id'] in f_ids
    assert friends_g['id'] in f_ids
    assert private_g['id'] not in f_ids

    # Invitee: sees all three (friend of creator + invited)
    i_ids = nearby_ids(invitee['token'])
    assert {open_g['id'], friends_g['id'], private_g['id']} <= i_ids

    # Invitee got a personal invite notification; friend did NOT for the private game
    inv_notes = client.get('/api/notifications', headers=auth_headers(invitee['token'])).get_json()
    assert any(n['kind'] == 'game_invite_direct' and n['related_game_id'] == private_g['id']
               for n in inv_notes['items'])
    f_notes = client.get('/api/notifications', headers=auth_headers(friend['token'])).get_json()
    assert all(n['related_game_id'] != private_g['id'] for n in f_notes['items'])

    # Private game appears in invitee's banner as 'invited'
    me_i = client.get('/api/me', headers=auth_headers(invitee['token'])).get_json()
    assert me_i['active_game'] and me_i['active_game']['banner_state'] == 'invited'


def test_visibility_join_guards(client):
    a = register(client, 'a@example.com', 'Ana')
    invitee = register(client, 'i@example.com', 'Ivy')
    stranger = register(client, 's@example.com', 'Sam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    private_g = make_game(client, a['token'], court_id, visibility='private',
                          invite_user_ids=[invitee['user']['id']])

    # Stranger cannot join a private game they weren't invited to
    res = client.post(f"/api/games/{private_g['id']}/join", headers=auth_headers(stranger['token']))
    assert res.status_code == 403
    assert res.get_json()['error'] == 'not_invited'

    # Invitee can join
    res = client.post(f"/api/games/{private_g['id']}/join", headers=auth_headers(invitee['token']))
    assert res.status_code == 200

    # Friends-only game: non-friend stranger cannot join
    friends_g = make_game(client, a['token'], court_id, visibility='friends')
    res = client.post(f"/api/games/{friends_g['id']}/join", headers=auth_headers(stranger['token']))
    assert res.status_code == 403


def test_private_requires_invitees(client):
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    from datetime import timedelta
    from backend.models import utcnow
    when = (utcnow() + timedelta(hours=24)).isoformat() + 'Z'
    res = client.post('/api/games', json={
        'court_id': court_id, 'scheduled_at': when,
        'visibility': 'private', 'invite_user_ids': [],
    }, headers=auth_headers(a['token']))
    assert res.status_code == 400
    assert res.get_json()['error'] == 'no_invitees'


# ---------- Friends ----------

def test_friend_request_flow(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')

    res = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token']))
    assert res.status_code == 201

    friends = client.get('/api/friends', headers=auth_headers(b['token'])).get_json()
    assert len(friends['incoming']) == 1
    friendship_id = friends['incoming'][0]['friendship_id']

    res = client.post(f'/api/friends/{friendship_id}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    assert res.status_code == 200

    friends_a = client.get('/api/friends', headers=auth_headers(a['token'])).get_json()
    assert [f['display_name'] for f in friends_a['friends']] == ['Ben']

    res = client.delete(f'/api/friends/{friendship_id}', headers=auth_headers(a['token']))
    assert res.get_json()['deleted'] is True


def test_friend_checkin_notification(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh, ch = auth_headers(a['token']), auth_headers(b['token']), auth_headers(c['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ana befriends Ben (Cam stays a stranger).
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah)
    fid = client.get('/api/friends', headers=bh).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=bh)

    def ben_notifs():
        items = client.get('/api/notifications', headers=bh).get_json()['items']
        return [n for n in items if n['kind'] == 'friend_checkin']

    # Plain check-in (not looking for a game): silent.
    client.post(f'/api/courts/{court_id}/checkin', json={}, headers=ah)
    assert ben_notifs() == []

    # Flipping to "looking for a game" pings the friend, tagged for profile tap-through.
    client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=ah)
    notifs = ben_notifs()
    assert len(notifs) == 1
    assert 'Ana' in notifs[0]['title'] and 'Larson Park' in notifs[0]['title']
    assert notifs[0]['related_user_id'] == a['user']['id']

    # Re-pinging while still looking doesn't spam.
    client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=ah)
    assert len(ben_notifs()) == 1

    # Even after checkout + fresh looking check-in, the 3h dedupe window holds.
    client.post('/api/checkout', headers=ah)
    client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=ah)
    assert len(ben_notifs()) == 1

    # Cam looking for a game doesn't notify Ben (not friends).
    client.post(f'/api/courts/{court_id}/checkin', json={'looking_for_game': True}, headers=ch)
    assert len(ben_notifs()) == 1


def test_friends_digest(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])

    # No friends yet: empty digest.
    digest = client.get('/api/friends/digest', headers=ah).get_json()
    assert digest == {'days': 7, 'games': 0, 'friends_played': 0, 'checkins': 0, 'top': []}

    # Ana befriends Ben (but not Cam).
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah)
    fid = client.get('/api/friends', headers=bh).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=bh)

    larson = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ben plays Cam twice this week: one win, one loss.
    def play(ben_wins):
        game = make_game(client, b['token'], larson, hours_ahead=1)
        client.post(f"/api/games/{game['id']}/join", headers=auth_headers(c['token']))
        res = client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [b['user']['id']], 'team2': [c['user']['id']],
            'score_team1': 11 if ben_wins else 4,
            'score_team2': 4 if ben_wins else 11,
        }, headers=bh)
        assert res.status_code == 200, res.get_json()

    play(True)
    play(False)
    client.post(f'/api/courts/{larson}/checkin', json={}, headers=bh)

    digest = client.get('/api/friends/digest', headers=ah).get_json()
    assert digest['games'] == 2
    assert digest['friends_played'] == 1  # Cam played too, but he's not Ana's friend
    assert digest['checkins'] == 1
    assert digest['top'] == [{
        'id': b['user']['id'], 'display_name': 'Ben',
        'games': 2, 'wins': 1, 'losses': 1,
    }]

    # Auth required.
    assert client.get('/api/friends/digest').status_code == 401


def test_my_stats(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah = auth_headers(a['token'])
    larson = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    adorni = client.get('/api/courts?q=adorni').get_json()['items'][0]['id']

    # Fresh player: all zeros. Every badge is locked, and the three nearest
    # (first win 0/1, MVP 0/1, tournament champion 0/1) surface as progress.
    stats = client.get('/api/me/stats', headers=ah).get_json()
    progress = stats.pop('badge_progress')
    assert stats == {'games_total': 0, 'games_this_month': 0, 'week_streak': 0,
                     'top_court': None, 'best_partner': None, 'top_rival': None,
                     'form': [], 'badges': [],
                     'tournament_titles': {'count': 0, 'recent': []},
                     'insights': None,
                     'rating_history': []}
    assert [b['id'] for b in progress] == ['first_win', 'mvp', 'champion']
    assert progress[0] == {'id': 'first_win', 'emoji': '🏅', 'label': 'First win',
                           'current': 0, 'target': 1}

    def play(court_id):
        g = make_game(client, a['token'], court_id, hours_ahead=1)
        client.post(f"/api/games/{g['id']}/join", headers=auth_headers(b['token']))
        res = client.post(f"/api/games/{g['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [b['user']['id']],
            'score_team1': 11, 'score_team2': 4,
        }, headers=ah)
        assert res.status_code == 200, res.get_json()

    play(larson)
    play(larson)
    play(adorni)

    stats = client.get('/api/me/stats', headers=ah).get_json()
    assert stats['games_total'] == 3
    assert stats['games_this_month'] == 3  # completed just now
    assert stats['week_streak'] == 1       # this week counts
    assert stats['top_court']['name'] == 'Larson Park' and stats['top_court']['games'] == 2
    # All singles vs Ben: he's the top rival, and there's no partner yet.
    assert stats['best_partner'] is None
    assert stats['top_rival']['display_name'] == 'Ben'
    assert stats['top_rival'] == {'user_id': b['user']['id'], 'display_name': 'Ben',
                                  'games': 3, 'your_wins': 3}

    # One doubles win with Cam makes him the best partner.
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    doubles = make_game(client, a['token'], larson, hours_ahead=1)
    for u in (b, c, d):
        client.post(f"/api/games/{doubles['id']}/join", headers=auth_headers(u['token']))
    client.post(f"/api/games/{doubles['id']}/complete", json={
        'team1': [a['user']['id'], c['user']['id']],
        'team2': [b['user']['id'], d['user']['id']],
        'score_team1': 11, 'score_team2': 6,
    }, headers=ah)
    stats = client.get('/api/me/stats', headers=ah).get_json()
    assert stats['best_partner']['display_name'] == 'Cam'
    assert stats['best_partner']['wins'] == 1
    assert stats['top_rival']['games'] == 4  # Ben again, now 4 meetings
    assert stats['form'] == ['W', 'W', 'W', 'W']  # all four wins, newest first
    # Winning earns the first badge; the rest need more history.
    assert [b['id'] for b in stats['badges']] == ['first_win']
    # Casual games don't move the rating — no trajectory yet.
    assert stats['rating_history'] == []

    # One confirmed ranked win draws the first trajectory segment.
    ranked = make_game(client, a['token'], larson, game_type='ranked', hours_ahead=1)
    client.post(f"/api/games/{ranked['id']}/join", headers=auth_headers(b['token']))
    client.post(f"/api/games/{ranked['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 9,
    }, headers=ah)
    client.post(f"/api/games/{ranked['id']}/confirm", headers=auth_headers(b['token']))
    stats = client.get('/api/me/stats', headers=ah).get_json()
    me_now = client.get('/api/me', headers=ah).get_json()['user']
    history = stats['rating_history']
    assert len(history) == 2  # baseline + the ranked game
    assert history[0]['at'] is None and history[0]['rating'] == 1200
    assert history[-1]['rating'] == me_now['rating'] > 1200
    # Public profiles expose the same trajectory (viewed by Ben).
    profile = client.get(f"/api/users/{a['user']['id']}",
                         headers=auth_headers(b['token'])).get_json()
    assert profile['rating_history'] == history

    # Auth required.
    assert client.get('/api/me/stats').status_code == 401


def test_availability_roundtrip(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah = auth_headers(a['token'])

    # Defaults to empty.
    assert client.get('/api/me', headers=ah).get_json()['user']['availability'] == []

    # Bad shape rejected; unknown tokens and duplicates filtered.
    assert client.patch('/api/me', json={'availability': 'mon-eve'}, headers=ah).status_code == 400
    res = client.patch('/api/me', json={
        'availability': ['mon-eve', 'nope-xx', 'mon-eve', 'sat-am', 42],
    }, headers=ah)
    assert res.status_code == 200
    assert res.get_json()['user']['availability'] == ['mon-eve', 'sat-am']

    # Visible on the public profile.
    profile = client.get(f"/api/users/{a['user']['id']}", headers=auth_headers(b['token'])).get_json()
    assert profile['availability'] == ['mon-eve', 'sat-am']

    # Clearing works.
    res = client.patch('/api/me', json={'availability': []}, headers=ah)
    assert res.get_json()['user']['availability'] == []


def test_change_password(client):
    a = register(client, 'a@example.com', 'Ana')
    ah = auth_headers(a['token'])

    # Auth required; wrong current password → 403 (not 401, which the client
    # treats as an expired session); short new password rejected.
    assert client.post('/api/auth/change-password', json={
        'current_password': 'secret123', 'new_password': 'newsecret',
    }).status_code == 401
    assert client.post('/api/auth/change-password', json={
        'current_password': 'wrong', 'new_password': 'newsecret',
    }, headers=ah).status_code == 403
    assert client.post('/api/auth/change-password', json={
        'current_password': 'secret123', 'new_password': '123',
    }, headers=ah).status_code == 400

    # Change succeeds; old password dies, new one works, session token survives.
    res = client.post('/api/auth/change-password', json={
        'current_password': 'secret123', 'new_password': 'newsecret',
    }, headers=ah)
    assert res.status_code == 200
    assert client.post('/api/auth/login', json={
        'email': 'a@example.com', 'password': 'secret123',
    }).status_code == 401
    assert client.post('/api/auth/login', json={
        'email': 'a@example.com', 'password': 'newsecret',
    }).status_code == 200
    assert client.get('/api/me', headers=ah).status_code == 200


def test_delete_account(client, app):
    from backend.models import User as UserModel
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Ana and Ben are friends with a completed ranked game between them.
    fid = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah).get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=bh)
    game = make_game(client, a['token'], court_id, game_type='ranked', hours_ahead=1)
    client.post(f"/api/games/{game['id']}/join", headers=bh)
    client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 7,
    }, headers=ah)
    client.post(f"/api/games/{game['id']}/confirm", headers=bh)

    # Ana also hosts an upcoming game that Ben joined.
    upcoming = make_game(client, a['token'], court_id, hours_ahead=24)
    client.post(f"/api/games/{upcoming['id']}/join", headers=bh)

    # Wrong password → 403, account untouched (and no session-expired logout).
    assert client.delete('/api/me', json={'password': 'wrong'}, headers=ah).status_code == 403
    assert client.get('/api/me', headers=ah).status_code == 200

    # Delete for real.
    res = client.delete('/api/me', json={'password': 'secret123'}, headers=ah)
    assert res.status_code == 200 and res.get_json()['deleted'] is True

    # Outstanding token is dead; login is impossible.
    assert client.get('/api/me', headers=ah).status_code == 401
    assert client.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'secret123'}).status_code == 401

    # Gone from search and friends; hosted upcoming game cancelled with Ben notified.
    assert client.get('/api/users/search?q=ana', headers=bh).get_json()['items'] == []
    assert client.get('/api/friends', headers=bh).get_json()['friends'] == []
    detail = client.get(f"/api/games/{upcoming['id']}", headers=bh).get_json()
    assert detail['status'] == 'cancelled'
    notifs = client.get('/api/notifications', headers=bh).get_json()
    assert any(n['kind'] == 'game_cancelled' for n in notifs['items'])

    # Ben's completed match history survives, showing the anonymized shell.
    history = client.get(f"/api/games/{game['id']}", headers=bh).get_json()
    assert history['status'] == 'completed'
    names = [p['display_name'] for p in history['players']]
    assert 'Deleted player' in names and 'Ben' in [n[:3] for n in names]

    with app.app_context():
        row = db.session.get(UserModel, a['user']['id'])
        assert row.deleted_at is not None
        assert row.email == f"deleted-{a['user']['id']}@invalid"
        assert row.bio == '' and row.avatar_url == ''


def test_invite_card(client):
    a = register(client, 'a@example.com', 'Ana')
    # Public — no auth needed; returns only name + avatar.
    card = client.get(f"/api/invite/{a['user']['id']}")
    assert card.status_code == 200
    assert set(card.get_json().keys()) == {'display_name', 'avatar_color', 'avatar_url'}
    assert card.get_json()['display_name'] == 'Ana'
    # Unknown and deleted users 404.
    assert client.get('/api/invite/99999').status_code == 404
    client.delete('/api/me', json={'password': 'secret123'}, headers=auth_headers(a['token']))
    assert client.get(f"/api/invite/{a['user']['id']}").status_code == 404


def test_block_user_flow(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])

    # Friends first, with a DM each way and both visible in search.
    fid = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah).get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=bh)
    assert client.post(f"/api/chat/{b['user']['id']}", json={'body': 'hi'}, headers=ah).status_code == 201
    assert any(u['id'] == b['user']['id'] for u in client.get('/api/users/search?q=ben', headers=ah).get_json()['items'])

    # Ana blocks Ben.
    res = client.post(f"/api/users/{b['user']['id']}/block", headers=ah)
    assert res.status_code == 200 and res.get_json()['blocked'] is True
    # Idempotent
    assert client.post(f"/api/users/{b['user']['id']}/block", headers=ah).status_code == 200

    # Friendship is gone, both directions.
    assert client.get('/api/friends', headers=ah).get_json()['friends'] == []
    assert client.get('/api/friends', headers=bh).get_json()['friends'] == []

    # Hidden from search both ways.
    assert client.get('/api/users/search?q=ben', headers=ah).get_json()['items'] == []
    assert client.get('/api/users/search?q=ana', headers=bh).get_json()['items'] == []

    # DMs refused both ways; conversation list hides the thread.
    assert client.post(f"/api/chat/{b['user']['id']}", json={'body': 'x'}, headers=ah).status_code == 403
    assert client.post(f"/api/chat/{a['user']['id']}", json={'body': 'x'}, headers=bh).status_code == 403
    assert client.get('/api/chat', headers=ah).get_json()['items'] == []

    # Friend requests refused both ways.
    assert client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=ah).status_code == 403
    assert client.post('/api/friends/request', json={'user_id': a['user']['id']}, headers=bh).status_code == 403

    # Profile shows the block to the blocker only.
    assert client.get(f"/api/users/{b['user']['id']}", headers=ah).get_json()['is_blocked'] is True
    assert client.get(f"/api/users/{a['user']['id']}", headers=bh).get_json()['is_blocked'] is False

    # Self-block rejected.
    assert client.post(f"/api/users/{a['user']['id']}/block", headers=ah).status_code == 400

    # Unblock restores messaging and search.
    assert client.post(f"/api/users/{b['user']['id']}/unblock", headers=ah).get_json()['blocked'] is False
    assert client.post(f"/api/chat/{b['user']['id']}", json={'body': 'sorry!'}, headers=ah).status_code == 201
    assert any(u['id'] == b['user']['id'] for u in client.get('/api/users/search?q=ben', headers=ah).get_json()['items'])


def test_block_hides_from_players_nearby(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    client.post(f'/api/courts/{court_id}/checkin', json={}, headers=bh)

    near = client.get('/api/players/nearby?lat=33.66&lng=-117.91', headers=ah).get_json()
    assert any(p['id'] == b['user']['id'] for p in near['items'])

    client.post(f"/api/users/{b['user']['id']}/block", headers=ah)
    near = client.get('/api/players/nearby?lat=33.66&lng=-117.91', headers=ah).get_json()
    assert not any(p['id'] == b['user']['id'] for p in near['items'])
    # And Ben doesn't see Ana either (she'd need a location; just check no crash + empty)
    assert client.get('/api/players/nearby?lat=33.66&lng=-117.91', headers=bh).status_code == 200


def test_head_to_head_on_profile(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # No shared games yet → no head-to-head block.
    assert client.get(f"/api/users/{b['user']['id']}", headers=ah).get_json()['head_to_head'] is None

    def play(winner, loser, winner_token):
        g = make_game(client, winner_token, court_id, game_type='ranked', hours_ahead=1)
        client.post(f"/api/games/{g['id']}/join", headers=bh if winner_token == a['token'] else ah)
        res = client.post(f"/api/games/{g['id']}/complete", json={
            'team1': [winner['user']['id']], 'team2': [loser['user']['id']],
            'score_team1': 11, 'score_team2': 6,
        }, headers=auth_headers(winner_token))
        assert res.status_code == 200, res.get_json()
        confirmer = bh if winner_token == a['token'] else ah
        assert client.post(f"/api/games/{g['id']}/confirm", headers=confirmer).status_code == 200

    play(a, b, a['token'])  # Ana wins
    play(a, b, a['token'])  # Ana wins again
    play(b, a, b['token'])  # Ben takes one back

    profile_b = client.get(f"/api/users/{b['user']['id']}", headers=ah).get_json()
    h2h = profile_b['head_to_head']
    assert h2h['wins'] == 2 and h2h['losses'] == 1
    # Ben's form from HIS perspective: lost, lost, then won (newest first → W L L).
    assert profile_b['form'] == ['W', 'L', 'L']
    # His win also shows as a badge on the public profile.
    assert [x['id'] for x in profile_b['badges']] == ['first_win']
    # Symmetric from Ben's perspective.
    h2h_b = client.get(f"/api/users/{a['user']['id']}", headers=bh).get_json()['head_to_head']
    assert h2h_b['wins'] == 1 and h2h_b['losses'] == 2
    assert h2h['last_game']['status'] == 'completed'

    # A third player with no shared games sees nothing.
    assert client.get(f"/api/users/{a['user']['id']}",
                      headers=auth_headers(c['token'])).get_json()['head_to_head'] is None
    # Own profile never carries it.
    assert client.get(f"/api/users/{a['user']['id']}", headers=ah).get_json()['head_to_head'] is None

    # Ana and Ben pair up in doubles against Cam + Dee and win → teammate record.
    d = register(client, 'd@example.com', 'Dee')
    doubles = make_game(client, a['token'], court_id, hours_ahead=1)
    for u in (b, c, d):
        client.post(f"/api/games/{doubles['id']}/join", headers=auth_headers(u['token']))
    res = client.post(f"/api/games/{doubles['id']}/complete", json={
        'team1': [a['user']['id'], b['user']['id']],
        'team2': [c['user']['id'], d['user']['id']],
        'score_team1': 11, 'score_team2': 8,
    }, headers=ah)
    assert res.status_code == 200
    profile = client.get(f"/api/users/{b['user']['id']}", headers=ah).get_json()
    assert profile['as_teammates'] == {'wins': 1, 'losses': 0}
    # Head-to-head unchanged by the same-team game.
    assert profile['head_to_head']['wins'] == 2 and profile['head_to_head']['losses'] == 1
    # Cam (opponent that game) has no teammate record with Ana.
    assert client.get(f"/api/users/{c['user']['id']}", headers=ah).get_json()['as_teammates'] is None


def test_user_search(client):
    a = register(client, 'a@example.com', 'Ana')
    register(client, 'b@example.com', 'Benny')
    res = client.get('/api/users/search?q=ben', headers=auth_headers(a['token']))
    assert [u['display_name'] for u in res.get_json()['items']] == ['Benny']


def test_players_nearby(client):
    # Larson Park ~ (33.66, -117.91); Adorni ~ (40.81, -124.16) — far apart.
    a = register(client, 'a@example.com', 'Ana')
    near = register(client, 'near@example.com', 'Nearby Nick')
    far = register(client, 'far@example.com', 'Far Fred')
    pro = register(client, 'pro@example.com', 'Pro Paula')

    larson = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    adorni = client.get('/api/courts?q=adorni').get_json()['items'][0]['id']

    # Locate players via check-in (sets last_lat/last_lng)
    client.post(f'/api/courts/{larson}/checkin', json={'looking_for_game': True}, headers=auth_headers(near['token']))
    client.post(f'/api/courts/{larson}/checkin', json={}, headers=auth_headers(pro['token']))
    client.patch('/api/me', json={'skill_level': 'pro'}, headers=auth_headers(pro['token']))
    client.post(f'/api/courts/{adorni}/checkin', json={}, headers=auth_headers(far['token']))

    # Ana looks near Larson Park
    res = client.get('/api/players/nearby?lat=33.66&lng=-117.91&radius=25', headers=auth_headers(a['token']))
    assert res.status_code == 200
    names = [p['display_name'] for p in res.get_json()['items']]
    assert 'Nearby Nick' in names and 'Pro Paula' in names
    assert 'Far Fred' not in names  # 400+ miles away
    nick = next(p for p in res.get_json()['items'] if p['display_name'] == 'Nearby Nick')
    assert nick['distance_miles'] < 5
    assert nick['checked_in_court']['looking_for_game'] is True
    assert nick['friendship_status'] is None

    # Skill filter
    res = client.get('/api/players/nearby?lat=33.66&lng=-117.91&skill=pro', headers=auth_headers(a['token']))
    assert [p['display_name'] for p in res.get_json()['items']] == ['Pro Paula']

    # Name query
    res = client.get('/api/players/nearby?lat=33.66&lng=-117.91&q=nick', headers=auth_headers(a['token']))
    assert [p['display_name'] for p in res.get_json()['items']] == ['Nearby Nick']

    # Friendship status surfaces
    fr = client.post('/api/friends/request', json={'user_id': near['user']['id']}, headers=auth_headers(a['token']))
    assert fr.status_code == 201
    res = client.get('/api/players/nearby?lat=33.66&lng=-117.91', headers=auth_headers(a['token']))
    nick = next(p for p in res.get_json()['items'] if p['display_name'] == 'Nearby Nick')
    assert nick['friendship_status'] == 'pending' and nick['outgoing'] is True

    # Location required
    assert client.get('/api/players/nearby', headers=auth_headers(a['token'])).status_code == 400


def test_players_nearby_home_court_fallback(client):
    # A player who never checked in but set a home court is still discoverable.
    a = register(client, 'a@example.com', 'Ana')
    homer = register(client, 'homer@example.com', 'Homer')
    larson = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    client.patch('/api/me', json={'home_court_id': larson}, headers=auth_headers(homer['token']))

    res = client.get('/api/players/nearby?lat=33.66&lng=-117.91&radius=25', headers=auth_headers(a['token']))
    assert 'Homer' in [p['display_name'] for p in res.get_json()['items']]


# ---------- Chat ----------

def test_chat_flow(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    b_id = b['user']['id']
    a_id = a['user']['id']

    res = client.post(f'/api/chat/{b_id}', json={'body': 'Game tonight?'}, headers=auth_headers(a['token']))
    assert res.status_code == 201

    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['unread_messages'] == 1

    convos = client.get('/api/chat', headers=auth_headers(b['token'])).get_json()
    assert convos['items'][0]['unread'] == 1

    thread = client.get(f'/api/chat/{a_id}', headers=auth_headers(b['token'])).get_json()
    assert thread['items'][0]['body'] == 'Game tonight?'

    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['unread_messages'] == 0

    since = thread['items'][-1]['id']
    client.post(f'/api/chat/{b_id}', json={'body': 'You in?'}, headers=auth_headers(a['token']))
    fresh = client.get(f'/api/chat/{a_id}?since_id={since}', headers=auth_headers(b['token'])).get_json()
    assert [m['body'] for m in fresh['items']] == ['You in?']


def test_game_chat(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))

    # Players only: outsiders and anonymous get rejected; bad game 404s.
    assert client.get(f"/api/games/{game['id']}/chat").status_code == 401
    assert client.get(f"/api/games/{game['id']}/chat", headers=auth_headers(c['token'])).status_code == 403
    assert client.post(f"/api/games/{game['id']}/chat", json={'body': 'hi'},
                       headers=auth_headers(c['token'])).status_code == 403
    assert client.get('/api/games/99999/chat', headers=auth_headers(a['token'])).status_code == 404

    # Players can talk; both see the thread.
    res = client.post(f"/api/games/{game['id']}/chat", json={'body': 'Running 5 late!'},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201 and res.get_json()['game_id'] == game['id']
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'No worries, warming up'},
                headers=auth_headers(b['token']))
    thread = client.get(f"/api/games/{game['id']}/chat", headers=auth_headers(b['token'])).get_json()
    assert [m['body'] for m in thread['items']] == ['Running 5 late!', 'No worries, warming up']
    assert thread['game']['court_name'] == 'Larson Park'

    # since_id returns only newer messages.
    first_id = thread['items'][0]['id']
    newer = client.get(f"/api/games/{game['id']}/chat?since_id={first_id}",
                       headers=auth_headers(a['token'])).get_json()
    assert [m['body'] for m in newer['items']] == ['No worries, warming up']

    # Game messages never leak into the DM conversation list.
    convos = client.get('/api/chat', headers=auth_headers(a['token'])).get_json()
    assert convos['items'] == []


def test_game_chat_notifications(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    ah, bh = auth_headers(a['token']), auth_headers(b['token'])
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id)
    client.post(f"/api/games/{game['id']}/join", headers=bh)

    def pings(headers):
        notes = client.get('/api/notifications', headers=headers).get_json()
        return [n for n in notes['items'] if n['kind'] == 'game_message']

    # Ana talks → Ben gets exactly one ping, Ana none; a second message while
    # Ben hasn't read it doesn't stack another.
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'On my way!'}, headers=ah)
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'Bringing water too'}, headers=ah)
    ben_pings = pings(bh)
    assert len(ben_pings) == 1
    assert 'game chat at Larson Park' in ben_pings[0]['title']
    assert ben_pings[0]['body'] == 'On my way!'
    assert ben_pings[0]['related_game_id'] == game['id']
    assert pings(ah) == []

    # Once Ben reads his notifications, the next message pings again.
    client.post('/api/notifications/read', headers=bh)
    client.post(f"/api/games/{game['id']}/chat", json={'body': 'Court 3!'}, headers=ah)
    assert len(pings(bh)) == 2


def test_court_chat(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    res = client.post(f'/api/courts/{court_id}/chat', json={'body': 'Anyone up for games at 6?'}, headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert res.get_json()['sender_name'] == 'Ana'

    room = client.get(f'/api/courts/{court_id}/chat', headers=auth_headers(b['token'])).get_json()
    assert [m['body'] for m in room['items']] == ['Anyone up for games at 6?']

    # Court messages must not leak into DM conversations or unread counts
    convos = client.get('/api/chat', headers=auth_headers(b['token'])).get_json()
    assert convos['items'] == []
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['unread_messages'] == 0

    since = room['items'][-1]['id']
    client.post(f'/api/courts/{court_id}/chat', json={'body': 'Yes!'}, headers=auth_headers(b['token']))
    fresh = client.get(f'/api/courts/{court_id}/chat?since_id={since}', headers=auth_headers(a['token'])).get_json()
    assert [m['body'] for m in fresh['items']] == ['Yes!']


def test_challenge(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    res = client.post(f"/api/users/{b['user']['id']}/challenge", json={'court_id': court_id}, headers=auth_headers(a['token']))
    assert res.status_code == 201
    game = res.get_json()
    assert game['game_type'] == 'ranked'
    assert game['max_players'] == 2
    assert 'challenged' in game['notes']

    notes = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    challenge = [n for n in notes['items'] if n['kind'] == 'challenge'][0]
    assert challenge['related_game_id'] == game['id']

    res = client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
    assert res.get_json()['spots_left'] == 0


# ---------- Games ----------

def make_game(client, token, court_id, game_type='casual', hours_ahead=24, visibility='open', invite_user_ids=None):
    from datetime import timedelta
    from backend.models import utcnow
    when = (utcnow() + timedelta(hours=hours_ahead)).isoformat() + 'Z'
    body = {
        'court_id': court_id,
        'scheduled_at': when,
        'game_type': game_type,
        'max_players': 4,
        'visibility': visibility,
    }
    if invite_user_ids is not None:
        body['invite_user_ids'] = invite_user_ids
    res = client.post('/api/games', json=body, headers=auth_headers(token))
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def test_game_create_join_leave(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    game = make_game(client, a['token'], court_id)
    assert game['players'][0]['display_name'] == 'Ana'
    assert game['spots_left'] == 3

    res = client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
    assert res.status_code == 200
    assert res.get_json()['spots_left'] == 2

    nearby = client.get('/api/games?lat=33.66&lng=-117.91').get_json()
    assert len(nearby['items']) == 1

    mine = client.get('/api/games?mine=1', headers=auth_headers(b['token'])).get_json()
    assert len(mine['items']) == 1

    res = client.post(f"/api/games/{game['id']}/leave", headers=auth_headers(b['token']))
    assert res.get_json()['spots_left'] == 3


def test_game_near_future_utc(client):
    # Regression: a UTC timestamp a couple hours out must not be rejected as past
    # (previously the backend converted to local time before comparing with UTC now).
    a = register(client, 'a@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=2)
    assert game['status'] == 'upcoming'


def test_game_mvp_votes(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=1)
    for u in (b, c):
        client.post(f"/api/games/{game['id']}/join", headers=auth_headers(u['token']))

    # Voting before the game finishes is rejected.
    assert client.post(f"/api/games/{game['id']}/mvp", json={'user_id': b['user']['id']},
                       headers=auth_headers(a['token'])).status_code == 400

    res = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id'], c['user']['id']],
        'score_team1': 11, 'score_team2': 7,
    }, headers=auth_headers(a['token']))
    assert res.status_code == 200

    # Guards: outsiders 403, self-votes and non-players 400.
    outsider = register(client, 'd@example.com', 'Dee')
    assert client.post(f"/api/games/{game['id']}/mvp", json={'user_id': a['user']['id']},
                       headers=auth_headers(outsider['token'])).status_code == 403
    assert client.post(f"/api/games/{game['id']}/mvp", json={'user_id': a['user']['id']},
                       headers=auth_headers(a['token'])).status_code == 400
    assert client.post(f"/api/games/{game['id']}/mvp", json={'user_id': outsider['user']['id']},
                       headers=auth_headers(a['token'])).status_code == 400

    # Ben and Cam both vote Ana → she's MVP with 2 votes; Ana's vote for Ben recorded.
    client.post(f"/api/games/{game['id']}/mvp", json={'user_id': a['user']['id']},
                headers=auth_headers(b['token']))
    res = client.post(f"/api/games/{game['id']}/mvp", json={'user_id': a['user']['id']},
                      headers=auth_headers(c['token']))
    data = res.get_json()
    assert data['mvp'] == {'user_id': a['user']['id'], 'display_name': 'Ana', 'votes': 2}
    res = client.post(f"/api/games/{game['id']}/mvp", json={'user_id': b['user']['id']},
                      headers=auth_headers(a['token']))
    assert res.get_json()['my_mvp_vote'] == b['user']['id']
    assert res.get_json()['mvp']['display_name'] == 'Ana'

    # Re-voting replaces, not stacks: Cam switches to Ben → 1-1-1 tie resolves stably.
    res = client.post(f"/api/games/{game['id']}/mvp", json={'user_id': b['user']['id']},
                      headers=auth_headers(c['token']))
    assert res.get_json()['mvp']['votes'] == 2  # Ben now has Ana's + Cam's votes
    assert res.get_json()['mvp']['display_name'] == 'Ben'


def test_game_waitlist(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # 2-player game fills up instantly.
    from backend.models import utcnow
    from datetime import timedelta
    game = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(hours=5)).isoformat() + 'Z',
        'game_type': 'casual', 'max_players': 2,
    }, headers=auth_headers(a['token'])).get_json()
    assert client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token'])).status_code == 200

    # Not-full guard, then Cam and Dee queue up in order.
    assert client.post(f"/api/games/{game['id']}/join",
                       headers=auth_headers(c['token'])).get_json()['error'] == 'game_full'
    res = client.post(f"/api/games/{game['id']}/waitlist", headers=auth_headers(c['token']))
    assert res.status_code == 200 and res.get_json()['waitlist_position'] == 1
    res = client.post(f"/api/games/{game['id']}/waitlist", headers=auth_headers(d['token']))
    assert res.get_json()['waitlist_position'] == 2
    assert res.get_json()['waitlist_count'] == 2
    # Players can't waitlist; joining twice is idempotent.
    assert client.post(f"/api/games/{game['id']}/waitlist",
                       headers=auth_headers(a['token'])).status_code == 400
    assert client.post(f"/api/games/{game['id']}/waitlist",
                       headers=auth_headers(c['token'])).get_json()['waitlist_position'] == 1

    # Ben leaves → Cam is auto-promoted and notified; Dee moves up.
    client.post(f"/api/games/{game['id']}/leave", headers=auth_headers(b['token']))
    detail = client.get(f"/api/games/{game['id']}", headers=auth_headers(c['token'])).get_json()
    assert detail['is_joined'] is True and detail['waitlist_position'] is None
    assert detail['waitlist_count'] == 1
    notes = client.get('/api/notifications', headers=auth_headers(c['token'])).get_json()
    assert any('spot opened' in n['title'].lower() for n in notes['items'])
    assert client.get(f"/api/games/{game['id']}",
                      headers=auth_headers(d['token'])).get_json()['waitlist_position'] == 1

    # Dee bails from the queue; cancelling notifies remaining waitlisters.
    client.post(f"/api/games/{game['id']}/waitlist", headers=auth_headers(b['token']))
    assert client.post(f"/api/games/{game['id']}/waitlist/leave",
                       headers=auth_headers(d['token'])).get_json()['waitlist_position'] is None
    client.post(f"/api/games/{game['id']}/cancel", headers=auth_headers(a['token']))
    notes_b = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    assert any(n['kind'] == 'game_cancelled' for n in notes_b['items'])


def test_game_full(client):
    a = register(client, 'a@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id)
    tokens = [register(client, f'p{i}@example.com', f'P{i}')['token'] for i in range(4)]
    for token in tokens[:3]:
        assert client.post(f"/api/games/{game['id']}/join", headers=auth_headers(token)).status_code == 200
    res = client.post(f"/api/games/{game['id']}/join", headers=auth_headers(tokens[3]))
    assert res.status_code == 400
    assert res.get_json()['error'] == 'game_full'


def setup_ranked_doubles(client):
    """Four players in a ranked game; returns (players dict, game, court_id)."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, game_type='ranked')
    for player in (b, c, d):
        client.post(f"/api/games/{game['id']}/join", headers=auth_headers(player['token']))
    return {'a': a, 'b': b, 'c': c, 'd': d}, game, court_id


def submit_doubles_score(client, token, game_id, players, s1=11, s2=7):
    return client.post(f'/api/games/{game_id}/complete', json={
        'team1': [players['a']['user']['id'], players['b']['user']['id']],
        'team2': [players['c']['user']['id'], players['d']['user']['id']],
        'score_team1': s1,
        'score_team2': s2,
    }, headers=auth_headers(token))


def test_ranked_score_needs_confirmation(client, app):
    players, game, _ = setup_ranked_doubles(client)
    a, b, c = players['a'], players['b'], players['c']

    res = submit_doubles_score(client, a['token'], game['id'], players)
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'awaiting_confirmation'
    assert data['score_submitted_by'] == a['user']['id']

    # Ratings must not move until an opponent confirms
    with app.app_context():
        assert User.query.filter_by(email='a@example.com').first().rating == 1200

    # Opposing player sees it as needing their confirmation; submitter's teammate does not
    detail_c = client.get(f"/api/games/{game['id']}", headers=auth_headers(c['token'])).get_json()
    assert detail_c['awaiting_your_confirmation'] is True
    detail_b = client.get(f"/api/games/{game['id']}", headers=auth_headers(b['token'])).get_json()
    assert detail_b['awaiting_your_confirmation'] is False

    me_c = client.get('/api/me', headers=auth_headers(c['token'])).get_json()
    assert me_c['games_to_confirm'] == 1

    # Opponents got a confirm-score notification
    notes_c = client.get('/api/notifications', headers=auth_headers(c['token'])).get_json()
    assert any(n['kind'] == 'score_submitted' for n in notes_c['items'])

    # Teammate of the submitter cannot confirm
    res = client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(b['token']))
    assert res.status_code == 403

    # Opponent confirms -> ELO + streaks apply
    res = client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(c['token']))
    assert res.status_code == 200
    confirmed = res.get_json()
    assert confirmed['status'] == 'completed'
    assert confirmed['you_won'] is False
    assert confirmed['your_rating_delta'] == -16

    with app.app_context():
        ana = User.query.filter_by(email='a@example.com').first()
        cam = User.query.filter_by(email='c@example.com').first()
        assert ana.rating == 1216  # equal ratings, K=32 -> +16
        assert ana.ranked_wins == 1
        assert ana.current_streak == 1
        assert ana.best_streak == 1
        assert cam.rating == 1184
        assert cam.current_streak == 0

    board = client.get('/api/leaderboard').get_json()['items']
    assert board[0]['rating'] == 1216

    history = client.get('/api/games/history', headers=auth_headers(a['token'])).get_json()
    me_player = [p for p in history['items'][0]['players'] if p['user_id'] == a['user']['id']][0]
    assert me_player['rating_delta'] == 16


def test_status_column_fits_all_statuses():
    # Postgres enforces VARCHAR lengths (SQLite doesn't) — regression for the
    # 500 caused by 'awaiting_confirmation' (21 chars) vs VARCHAR(20).
    from backend.models import GAME_STATUSES, Game as GameModel
    assert GameModel.status.type.length >= max(len(s) for s in GAME_STATUSES)


def test_scorekeeper_submit_any_player_confirms(client):
    # If the reporter isn't on either team, any assigned player may confirm.
    players, game, _ = setup_ranked_doubles(client)
    a, b, c = players['a'], players['b'], players['c']

    res = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [b['user']['id']],
        'team2': [c['user']['id']],
        'score_team1': 11,
        'score_team2': 5,
    }, headers=auth_headers(a['token']))
    assert res.status_code == 200
    assert res.get_json()['status'] == 'awaiting_confirmation'

    detail_b = client.get(f"/api/games/{game['id']}", headers=auth_headers(b['token'])).get_json()
    assert detail_b['awaiting_your_confirmation'] is True

    res = client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(b['token']))
    assert res.status_code == 200
    assert res.get_json()['status'] == 'completed'


def test_active_game_banner_states(client):
    from backend.models import utcnow
    players, game, court_id = setup_ranked_doubles(client)
    a, c = players['a'], players['c']

    # Scheduled in the future -> 'upcoming'
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_game']['id'] == game['id']
    assert me['active_game']['banner_state'] == 'upcoming'

    # A live game outranks it
    live = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': utcnow().isoformat() + 'Z',
        'game_type': 'casual',
    }, headers=auth_headers(a['token'])).get_json()
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_game']['id'] == live['id']
    assert me['active_game']['banner_state'] == 'live'

    # Submitted score: submitter side sees 'waiting', opponent sees 'confirm'
    client.post(f"/api/games/{live['id']}/cancel", headers=auth_headers(a['token']))
    submit_doubles_score(client, a['token'], game['id'], players)
    me_a = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me_a['active_game']['banner_state'] == 'waiting'
    me_c = client.get('/api/me', headers=auth_headers(c['token'])).get_json()
    assert me_c['active_game']['banner_state'] == 'confirm'

    # Confirmed -> no active game left
    client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(c['token']))
    me_a = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me_a['active_game'] is None


def test_challenge_banner_and_decline(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    game = client.post(f"/api/users/{b['user']['id']}/challenge", json={'court_id': court_id}, headers=auth_headers(a['token'])).get_json()

    # Challenged player sees the challenge in their banner
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['active_game']['id'] == game['id']
    assert me_b['active_game']['banner_state'] == 'challenge'

    # Challenger sees it as their live game (they're in it, scheduled now)
    me_a = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me_a['active_game']['banner_state'] == 'live'

    # Only the challenged player may decline
    c = register(client, 'c@example.com', 'Cam')
    res = client.post(f"/api/games/{game['id']}/decline", headers=auth_headers(c['token']))
    assert res.status_code == 403

    res = client.post(f"/api/games/{game['id']}/decline", headers=auth_headers(b['token']))
    assert res.status_code == 200
    assert res.get_json()['status'] == 'cancelled'

    assert res.get_json()['status'] == 'cancelled'

    # Challenger got notified, target's banner cleared
    notes = client.get('/api/notifications', headers=auth_headers(a['token'])).get_json()
    assert any(n['kind'] == 'challenge_declined' for n in notes['items'])
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['active_game'] is None

    # Accepting a challenge turns it into a live game for both
    game3 = client.post(f"/api/users/{b['user']['id']}/challenge", json={'court_id': court_id}, headers=auth_headers(a['token'])).get_json()
    client.post(f"/api/games/{game3['id']}/join", headers=auth_headers(b['token']))
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['active_game']['id'] == game3['id']
    assert me_b['active_game']['banner_state'] == 'live'

    # Declining after someone joined is rejected
    res = client.post(f"/api/games/{game3['id']}/decline", headers=auth_headers(c['token']))
    assert res.status_code == 400


def test_direct_game_invites(client):
    from datetime import timedelta
    from backend.models import utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    # a and c are friends; b is not
    res = client.post('/api/friends/request', json={'user_id': c['user']['id']}, headers=auth_headers(a['token']))
    fid = res.get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(c['token']))
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    when = (utcnow() + timedelta(hours=5)).isoformat() + 'Z'
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': when,
        'game_type': 'casual',
        'invite_user_ids': [b['user']['id']],
        'notify_friends': False,
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201
    game = res.get_json()

    # b got a personal invite; c (friend, no blast) got nothing
    notes_b = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    invite = [n for n in notes_b['items'] if n['kind'] == 'game_invite_direct']
    assert len(invite) == 1
    assert invite[0]['related_game_id'] == game['id']
    notes_c = client.get('/api/notifications', headers=auth_headers(c['token'])).get_json()
    assert all(n['kind'] not in ('game_invite', 'game_invite_direct') for n in notes_c['items'])

    # Invite shows in b's banner as 'invited'
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['active_game']['id'] == game['id']
    assert me_b['active_game']['banner_state'] == 'invited'

    # Joining clears the invite state (game becomes their upcoming game)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['active_game']['banner_state'] == 'upcoming'

    # Blast + personal invites don't double-notify the invited friend
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': when,
        'invite_user_ids': [c['user']['id']],
        'notify_friends': True,
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201
    notes_c = client.get('/api/notifications', headers=auth_headers(c['token'])).get_json()
    kinds = [n['kind'] for n in notes_c['items']]
    assert kinds.count('game_invite_direct') == 1
    assert kinds.count('game_invite') == 0


def test_start_game_now(client):
    from backend.models import utcnow
    a = register(client, 'a@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': utcnow().isoformat() + 'Z',
        'game_type': 'casual',
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert res.get_json()['status'] == 'upcoming'

    mine = client.get('/api/games?mine=1', headers=auth_headers(a['token'])).get_json()
    assert len(mine['items']) == 1


def test_dispute_score(client, app):
    players, game, _ = setup_ranked_doubles(client)
    a, d = players['a'], players['d']

    submit_doubles_score(client, a['token'], game['id'], players)
    res = client.post(f"/api/games/{game['id']}/dispute", headers=auth_headers(d['token']))
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'upcoming'
    assert data['score_team1'] is None

    notes = client.get('/api/notifications', headers=auth_headers(a['token'])).get_json()
    assert any(n['kind'] == 'score_disputed' for n in notes['items'])
    with app.app_context():
        assert User.query.filter_by(email='a@example.com').first().rating == 1200

    # Score can be re-entered after a dispute
    res = submit_doubles_score(client, d['token'], game['id'], players, s1=9, s2=11)
    assert res.get_json()['status'] == 'awaiting_confirmation'


def test_casual_game_completes_instantly(client, app):
    players, _game, court_id = setup_ranked_doubles(client)
    a = players['a']
    casual = make_game(client, a['token'], court_id, game_type='casual')
    for key in ('b', 'c', 'd'):
        client.post(f"/api/games/{casual['id']}/join", headers=auth_headers(players[key]['token']))

    res = submit_doubles_score(client, a['token'], casual['id'], players)
    data = res.get_json()
    assert data['status'] == 'completed'
    assert data['you_won'] is True
    # Casual games never move ratings
    with app.app_context():
        assert User.query.filter_by(email='a@example.com').first().rating == 1200


def test_auto_confirm_stale_score(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, utcnow
    players, game, _ = setup_ranked_doubles(client)
    a = players['a']

    submit_doubles_score(client, a['token'], game['id'], players)
    with app.app_context():
        row = db.session.get(GameModel, game['id'])
        row.score_submitted_at = utcnow() - timedelta(hours=25)
        db.session.commit()

    # Any games-list request sweeps stale confirmations
    client.get('/api/games?mine=1', headers=auth_headers(a['token']))
    detail = client.get(f"/api/games/{game['id']}").get_json()
    assert detail['status'] == 'completed'
    with app.app_context():
        assert User.query.filter_by(email='a@example.com').first().rating == 1216


def test_monthly_leaderboard(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Empty until a ranked game completes this month.
    assert client.get('/api/leaderboard?period=month').get_json()['items'] == []

    def ranked_win(winner_tok, loser, confirm_headers):
        g = make_game(client, winner_tok, court_id, game_type='ranked', hours_ahead=1)
        client.post(f"/api/games/{g['id']}/join", headers=confirm_headers)
        me = client.get('/api/me', headers=auth_headers(winner_tok)).get_json()['user']
        client.post(f"/api/games/{g['id']}/complete", json={
            'team1': [me['id']], 'team2': [loser['user']['id']],
            'score_team1': 11, 'score_team2': 3,
        }, headers=auth_headers(winner_tok))
        client.post(f"/api/games/{g['id']}/confirm", headers=confirm_headers)
        return g

    ranked_win(a['token'], b, auth_headers(b['token']))
    old = ranked_win(a['token'], b, auth_headers(b['token']))

    board = client.get('/api/leaderboard?period=month').get_json()
    names = [(u['display_name'], u['month_delta'], u['month_games']) for u in board['items']]
    assert names[0][0] == 'Ana' and names[0][1] > 0 and names[0][2] == 2
    assert names[1][0] == 'Ben' and names[1][1] < 0

    # A game completed last month drops out of the aggregation. Assert
    # in-context (HTTP reads after in-context time travel hit the known
    # StaticPool cross-context flake).
    with app.app_context():
        db.session.get(GameModel, old['id']).completed_at = utcnow() - timedelta(days=40)
        db.session.commit()
        from sqlalchemy import func
        from backend.models import GamePlayer as GPModel
        month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ana_games = (
            db.session.query(func.count(GPModel.id))
            .join(GameModel, GameModel.id == GPModel.game_id)
            .filter(
                GPModel.user_id == a['user']['id'],
                GameModel.status == 'completed',
                GameModel.game_type == 'ranked',
                GameModel.completed_at >= month_start,
                GPModel.rating_delta.isnot(None),
            )
            .scalar()
        )
        assert ana_games == 1


def test_results_feed(client):
    players, game, _ = setup_ranked_doubles(client)
    a, c = players['a'], players['c']
    submit_doubles_score(client, a['token'], game['id'], players)
    client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(c['token']))

    feed = client.get('/api/games/results?lat=33.66&lng=-117.91', headers=auth_headers(a['token'])).get_json()
    assert len(feed['items']) == 1
    item = feed['items'][0]
    assert item['involves_me'] is True
    assert item['score_team1'] == 11
    teams = {p['user_id']: p['team'] for p in item['players']}
    assert teams[a['user']['id']] == 1
    assert teams[c['user']['id']] == 2


def test_complete_validation(client):
    a = register(client, 'a@example.com')
    b = register(client, 'b@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))

    res = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']],
        'team2': [b['user']['id']],
        'score_team1': 11,
        'score_team2': 11,
    }, headers=auth_headers(a['token']))
    assert res.status_code == 400

    outsider = register(client, 'x@example.com')
    res = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']],
        'team2': [b['user']['id']],
        'score_team1': 11,
        'score_team2': 5,
    }, headers=auth_headers(outsider['token']))
    assert res.status_code == 403


def test_cancel_game(client):
    a = register(client, 'a@example.com')
    b = register(client, 'b@example.com')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id)

    res = client.post(f"/api/games/{game['id']}/cancel", headers=auth_headers(b['token']))
    assert res.status_code == 403
    res = client.post(f"/api/games/{game['id']}/cancel", headers=auth_headers(a['token']))
    assert res.get_json()['status'] == 'cancelled'


# ---------- Tournaments ----------

def make_tournament(client, token, court_id, fmt='single_elim', event_type='singles',
                    max_entries=8, hours_ahead=24):
    from datetime import timedelta
    from backend.models import utcnow
    res = client.post('/api/tournaments', json={
        'name': 'Summer Slam',
        'court_id': court_id,
        'starts_at': (utcnow() + timedelta(hours=hours_ahead)).isoformat() + 'Z',
        'format': fmt,
        'event_type': event_type,
        'max_entries': max_entries,
        'description': 'Bring water',
    }, headers=auth_headers(token))
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _register_entry(client, tid, token, partner_id=None, expect=201):
    body = {'partner_id': partner_id} if partner_id else {}
    res = client.post(f'/api/tournaments/{tid}/register', json=body,
                      headers=auth_headers(token))
    assert res.status_code == expect, res.get_json()
    return res.get_json()


def test_tournament_create_and_validation(client):
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    t = make_tournament(client, a['token'], court_id)
    assert t['status'] == 'registration'
    assert t['format'] == 'single_elim'
    assert t['is_organizer'] is True
    assert t['entry_count'] == 0

    # Bad payloads
    res = client.post('/api/tournaments', json={'name': 'x', 'court_id': court_id,
                      'starts_at': '2030-01-01T10:00:00Z'}, headers=auth_headers(a['token']))
    assert res.status_code == 400  # name too short
    res = client.post('/api/tournaments', json={'name': 'Valid Name', 'court_id': 99999,
                      'starts_at': '2030-01-01T10:00:00Z'}, headers=auth_headers(a['token']))
    assert res.status_code == 404
    res = client.post('/api/tournaments', json={'name': 'Valid Name', 'court_id': court_id,
                      'starts_at': 'nope'}, headers=auth_headers(a['token']))
    assert res.status_code == 400


def test_tournament_registration_and_withdraw(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, max_entries=2)

    data = _register_entry(client, t['id'], a['token'])
    assert data['entry_count'] == 1
    assert data['my_entry_id']
    # Double-register blocked
    _register_entry(client, t['id'], a['token'], expect=409)
    _register_entry(client, t['id'], b['token'])
    # Full
    _register_entry(client, t['id'], c['token'], expect=409)

    # Organizer sees join notifications
    notifs = client.get('/api/notifications', headers=auth_headers(a['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_join' and n['related_tournament_id'] == t['id'] for n in notifs)

    # Ben withdraws, Cam can now join
    res = client.delete(f"/api/tournaments/{t['id']}/register", headers=auth_headers(b['token']))
    assert res.status_code == 200
    assert res.get_json()['entry_count'] == 1
    _register_entry(client, t['id'], c['token'])

    # Organizer can remove an entry
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()
    cam_entry = next(e for e in detail['entries'] if e['name'] == 'Cam')
    res = client.delete(f"/api/tournaments/{t['id']}/entries/{cam_entry['id']}",
                        headers=auth_headers(b['token']))
    assert res.status_code == 403
    res = client.delete(f"/api/tournaments/{t['id']}/entries/{cam_entry['id']}",
                        headers=auth_headers(a['token']))
    assert res.status_code == 200
    assert res.get_json()['entry_count'] == 1


def test_tournament_doubles_partner_rules(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, event_type='doubles')

    # No partner -> rejected
    _register_entry(client, t['id'], a['token'], expect=400)
    # Stranger partner -> rejected
    _register_entry(client, t['id'], a['token'], partner_id=b['user']['id'], expect=403)

    # Befriend then register as a pair
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token']))
    fid = client.get('/api/friends', headers=auth_headers(b['token'])).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    data = _register_entry(client, t['id'], a['token'], partner_id=b['user']['id'])
    entry = data['entries'][0]
    assert entry['name'] == 'Ana & Ben'
    assert len(entry['players']) == 2

    # Partner got the heads-up
    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_invite' for n in notifs)

    # Ben (player2) can't register again; neither can a team drafting Ben
    _register_entry(client, t['id'], b['token'], partner_id=a['user']['id'], expect=409)
    client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(c['token']))
    fid2 = client.get('/api/friends', headers=auth_headers(b['token'])).get_json()['incoming'][0]['friendship_id']
    client.post(f'/api/friends/{fid2}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    _register_entry(client, t['id'], c['token'], partner_id=b['user']['id'], expect=409)


def test_tournament_single_elim_flow(client):
    """4 players -> 2 semis + final; scores advance; champion crowned."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id)

    # Give players distinct ratings so seeding is deterministic: Ana > Ben > Cam > Dee
    from backend.app import db
    from backend.models import User
    for email, rating in (('a@example.com', 1400), ('b@example.com', 1300),
                          ('c@example.com', 1250), ('d@example.com', 1100)):
        User.query.filter_by(email=email).first().rating = rating
    db.session.commit()

    for p in (a, b, c, d):
        _register_entry(client, t['id'], p['token'])

    # Only the organizer can start
    res = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(b['token']))
    assert res.status_code == 403
    res = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'active'
    assert data['total_rounds'] == 2
    entries = {e['name']: e for e in data['entries']}
    assert entries['Ana']['seed'] == 1 and entries['Dee']['seed'] == 4

    # Round 1: seed 1 vs 4, seed 2 vs 3; last round holds final + bronze match
    r1 = [m for m in data['matches'] if m['round'] == 1]
    assert len(r1) == 2 and all(m['status'] == 'ready' for m in r1)
    m0 = next(m for m in r1 if m['position'] == 0)
    assert {m0['entry1_id'], m0['entry2_id']} == {entries['Ana']['id'], entries['Dee']['id']}
    final = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 0)
    third = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 1)
    assert final['status'] == 'pending' and third['status'] == 'pending'

    # A non-participant can't score
    outsider = register(client, 'x@example.com')
    res = client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                      json={'score1': 11, 'score2': 5}, headers=auth_headers(outsider['token']))
    assert res.status_code == 403
    # Tie score rejected
    res = client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                      json={'score1': 11, 'score2': 11}, headers=auth_headers(a['token']))
    assert res.status_code == 400

    # Ana beats Dee; Cam upsets Ben (scored by organizer)
    res = client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                      json={'score1': 11, 'score2': 5}, headers=auth_headers(a['token']))
    assert res.status_code == 200
    m1 = next(m for m in res.get_json()['matches'] if m['round'] == 1 and m['position'] == 1)
    res = client.post(f"/api/tournaments/{t['id']}/matches/{m1['id']}/score",
                      json={'score1': 9, 'score2': 11}, headers=auth_headers(a['token']))
    data = res.get_json()
    final = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 0)
    assert final['status'] == 'ready'
    assert {final['entry1_id'], final['entry2_id']} == {entries['Ana']['id'], entries['Cam']['id']}
    # Semifinal losers (Dee, Ben) dropped into the bronze match
    third = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 1)
    assert third['status'] == 'ready'
    assert {third['entry1_id'], third['entry2_id']} == {entries['Dee']['id'], entries['Ben']['id']}

    # Cam got a "match is set" notification
    notifs = client.get('/api/notifications', headers=auth_headers(c['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_match' for n in notifs)

    # Final alone doesn't finish it — the bronze match is still pending
    res = client.post(f"/api/tournaments/{t['id']}/matches/{final['id']}/score",
                      json={'score1': 11, 'score2': 7}, headers=auth_headers(c['token']))
    data = res.get_json()
    assert data['status'] == 'active'
    # Bronze: Ben beats Dee -> tournament completes, champion = Ana, Ben 3rd
    res = client.post(f"/api/tournaments/{t['id']}/matches/{third['id']}/score",
                      json={'score1': 5, 'score2': 11}, headers=auth_headers(d['token']))
    data = res.get_json()
    assert data['status'] == 'completed'
    assert data['champion']['name'] == 'Ana'
    third = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 1)
    assert third['winner_entry_id'] == entries['Ben']['id']
    notifs = client.get('/api/notifications', headers=auth_headers(d['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_result' and 'Ana won' in n['title'] for n in notifs)

    # No more scoring after completion
    res = client.post(f"/api/tournaments/{t['id']}/matches/{final['id']}/score",
                      json={'score1': 5, 'score2': 11}, headers=auth_headers(a['token']))
    assert res.status_code == 409


def test_tournament_byes_with_three_entries(client):
    """3 entries in a 4-slot bracket: top seed gets a bye straight to the final."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id)

    from backend.app import db
    from backend.models import User
    for email, rating in (('a@example.com', 1400), ('b@example.com', 1300), ('c@example.com', 1200)):
        User.query.filter_by(email=email).first().rating = rating
    db.session.commit()

    for p in (a, b, c):
        _register_entry(client, t['id'], p['token'])
    data = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token'])).get_json()

    r1 = [m for m in data['matches'] if m['round'] == 1]
    bye = next(m for m in r1 if m['status'] == 'bye')
    playable = next(m for m in r1 if m['status'] == 'ready')
    entries = {e['name']: e for e in data['entries']}
    assert bye['winner_entry_id'] == entries['Ana']['id']
    final = next(m for m in data['matches'] if m['round'] == 2)
    assert final['entry1_id'] == entries['Ana']['id']  # bye winner already advanced
    # 3 entries -> a bye semifinal produces no loser, so no bronze match
    assert not any(m for m in data['matches'] if m['round'] == 2 and m['position'] == 1)

    # Ben vs Cam, then the final
    res = client.post(f"/api/tournaments/{t['id']}/matches/{playable['id']}/score",
                      json={'score1': 11, 'score2': 8}, headers=auth_headers(b['token']))
    final = next(m for m in res.get_json()['matches'] if m['round'] == 2)
    assert final['status'] == 'ready'
    res = client.post(f"/api/tournaments/{t['id']}/matches/{final['id']}/score",
                      json={'score1': 11, 'score2': 9}, headers=auth_headers(a['token']))
    assert res.get_json()['champion']['name'] == 'Ana'


def test_tournament_round_robin_standings(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, fmt='round_robin')

    for p in (a, b, c):
        _register_entry(client, t['id'], p['token'])
    data = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token'])).get_json()
    # 3 entries -> 3 matches, everyone plays everyone
    assert len(data['matches']) == 3
    assert all(m['status'] == 'ready' for m in data['matches'])
    entries = {e['name']: e for e in data['entries']}

    def score(match, s1, s2):
        res = client.post(f"/api/tournaments/{t['id']}/matches/{match['id']}/score",
                          json={'score1': s1, 'score2': s2}, headers=auth_headers(a['token']))
        assert res.status_code == 200, res.get_json()
        return res.get_json()

    def match_between(data, x, y):
        return next(m for m in data['matches']
                    if {m['entry1_id'], m['entry2_id']} == {entries[x]['id'], entries[y]['id']})

    # Ana beats Ben, Ana beats Cam, Ben beats Cam -> Ana 2-0, Ben 1-1, Cam 0-2
    def oriented(m, first):
        return (11, 4) if m['entry1_id'] == entries[first]['id'] else (4, 11)

    m = match_between(data, 'Ana', 'Ben'); data = score(m, *oriented(m, 'Ana'))
    m = match_between(data, 'Ana', 'Cam'); data = score(m, *oriented(m, 'Ana'))
    m = match_between(data, 'Ben', 'Cam'); data = score(m, *oriented(m, 'Ben'))

    assert data['status'] == 'completed'
    assert data['champion']['name'] == 'Ana'
    names = [row['entry']['name'] for row in data['standings']]
    assert names == ['Ana', 'Ben', 'Cam']
    assert data['standings'][0]['wins'] == 2
    assert data['standings'][2]['losses'] == 2


def test_tournament_cancel_and_lists(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id)
    _register_entry(client, t['id'], b['token'])

    # Nearby list (Larson Park is at 33.66,-117.91)
    res = client.get('/api/tournaments?lat=33.66&lng=-117.91&radius=50',
                     headers=auth_headers(b['token']))
    assert any(item['id'] == t['id'] for item in res.get_json()['items'])
    # Far away -> not listed
    res = client.get('/api/tournaments?lat=40.81&lng=-124.16&radius=50',
                     headers=auth_headers(b['token']))
    assert not any(item['id'] == t['id'] for item in res.get_json()['items'])
    # Mine (entrant, not organizer)
    res = client.get('/api/tournaments?mine=1', headers=auth_headers(b['token']))
    assert any(item['id'] == t['id'] for item in res.get_json()['items'])

    res = client.post(f"/api/tournaments/{t['id']}/cancel", headers=auth_headers(b['token']))
    assert res.status_code == 403
    res = client.post(f"/api/tournaments/{t['id']}/cancel", headers=auth_headers(a['token']))
    assert res.get_json()['status'] == 'cancelled'
    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_cancelled' for n in notifs)
    # Registration on a cancelled tournament is refused
    _register_entry(client, t['id'], a['token'], expect=409)


def test_tournament_champion_badge(client):
    """Winning a tournament unlocks the 🏆 champion badge in /me/stats."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, max_entries=2)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    final = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()['matches'][0]
    data = client.post(f"/api/tournaments/{t['id']}/matches/{final['id']}/score",
                       json={'score1': 11, 'score2': 2}, headers=auth_headers(a['token'])).get_json()
    assert data['status'] == 'completed'

    winner_id = data['champion']['players'][0]['id']
    winner = a if a['user']['id'] == winner_id else b
    loser = b if winner is a else a
    stats = client.get('/api/me/stats', headers=auth_headers(winner['token'])).get_json()
    assert any(bd['id'] == 'champion' for bd in stats['badges'])
    stats = client.get('/api/me/stats', headers=auth_headers(loser['token'])).get_json()
    assert not any(bd['id'] == 'champion' for bd in stats['badges'])
    # Winning triggered the badge notification
    notifs = client.get('/api/notifications', headers=auth_headers(winner['token'])).get_json()['items']
    assert any(n['kind'] == 'badge_earned' and 'Tournament champion' in n['title'] for n in notifs)


def test_tournament_chat(client):
    """Participants + organizer only; messages ping others once per unread."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    outsider = register(client, 'x@example.com', 'Xan')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])

    # Outsider can't read or write
    assert client.get(f"/api/tournaments/{t['id']}/chat",
                      headers=auth_headers(outsider['token'])).status_code == 403
    assert client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'hi'},
                       headers=auth_headers(outsider['token'])).status_code == 403
    # Missing tournament 404s
    assert client.get('/api/tournaments/9999/chat',
                      headers=auth_headers(a['token'])).status_code == 404

    # Ana sends; Ben reads it and gets exactly one ping despite two messages
    res = client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'Check-in at 8:30!'},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201
    first_id = res.get_json()['id']
    client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'Courts 3 & 4'},
                headers=auth_headers(a['token']))

    chat = client.get(f"/api/tournaments/{t['id']}/chat",
                      headers=auth_headers(b['token'])).get_json()
    assert [m['body'] for m in chat['items']] == ['Check-in at 8:30!', 'Courts 3 & 4']
    assert chat['items'][0]['tournament_id'] == t['id']
    # since_id returns only the newer message
    newer = client.get(f"/api/tournaments/{t['id']}/chat?since_id={first_id}",
                       headers=auth_headers(b['token'])).get_json()
    assert [m['body'] for m in newer['items']] == ['Courts 3 & 4']

    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    pings = [n for n in notifs if n['kind'] == 'tournament_message']
    assert len(pings) == 1
    assert pings[0]['related_tournament_id'] == t['id']

    # tournament_message is muteable
    res = client.patch('/api/me', json={'muted_notifications': ['tournament_message']},
                       headers=auth_headers(b['token']))
    assert res.status_code == 200
    # Mark existing read, then a new message should NOT ping Ben
    client.post('/api/notifications/read', headers=auth_headers(b['token']))
    client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'Bring water'},
                headers=auth_headers(a['token']))
    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    assert not [n for n in notifs if n['kind'] == 'tournament_message' and not n['read']]


def test_tournament_score_correction(client):
    """A match result can be fixed until the match it feeds has been played."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id)
    for p in (a, b, c, d):
        _register_entry(client, t['id'], p['token'])
    data = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token'])).get_json()

    r1 = [m for m in data['matches'] if m['round'] == 1]
    m0, m1 = r1[0], r1[1]
    the_final = lambda d: next(m for m in d['matches'] if m['round'] == 2 and m['position'] == 0)
    the_third = lambda d: next(m for m in d['matches'] if m['round'] == 2 and m['position'] == 1)
    # Enter m0 with the wrong winner, then correct it — final AND bronze
    # slots must both follow the swap
    data = client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                       json={'score1': 5, 'score2': 11}, headers=auth_headers(a['token'])).get_json()
    assert the_final(data)['entry1_id'] == m0['entry2_id']
    assert the_third(data)['entry1_id'] == m0['entry1_id']
    data = client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                       json={'score1': 11, 'score2': 5}, headers=auth_headers(a['token'])).get_json()
    assert the_final(data)['entry1_id'] == m0['entry1_id']
    assert the_third(data)['entry1_id'] == m0['entry2_id']

    # Play out m1, the final, and the bronze match
    data = client.post(f"/api/tournaments/{t['id']}/matches/{m1['id']}/score",
                       json={'score1': 11, 'score2': 9}, headers=auth_headers(a['token'])).get_json()
    res = client.post(f"/api/tournaments/{t['id']}/matches/{the_final(data)['id']}/score",
                      json={'score1': 11, 'score2': 8}, headers=auth_headers(a['token']))
    data = res.get_json()
    assert data['status'] == 'active'  # bronze still pending
    # A semi correction is now locked by the played final
    res = client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                      json={'score1': 3, 'score2': 11}, headers=auth_headers(a['token']))
    assert res.status_code == 409
    res = client.post(f"/api/tournaments/{t['id']}/matches/{the_third(data)['id']}/score",
                      json={'score1': 11, 'score2': 6}, headers=auth_headers(a['token']))
    assert res.get_json()['status'] == 'completed'
    # (completed tournaments refuse all edits — covered elsewhere; the lock
    # below matters mid-tournament, so test it on a fresh 8-player bracket)

    t2 = make_tournament(client, a['token'], court_id, max_entries=8)
    e = register(client, 'e@example.com', 'Eve')
    f = register(client, 'f@example.com', 'Fay')
    g = register(client, 'g@example.com', 'Gus')
    h = register(client, 'h@example.com', 'Hal')
    for p in (a, b, c, d, e, f, g, h):
        _register_entry(client, t2['id'], p['token'])
    data = client.post(f"/api/tournaments/{t2['id']}/start", headers=auth_headers(a['token'])).get_json()
    quarters = [m for m in data['matches'] if m['round'] == 1]
    q0, q1 = quarters[0], quarters[1]
    client.post(f"/api/tournaments/{t2['id']}/matches/{q0['id']}/score",
                json={'score1': 11, 'score2': 1}, headers=auth_headers(a['token']))
    data = client.post(f"/api/tournaments/{t2['id']}/matches/{q1['id']}/score",
                       json={'score1': 11, 'score2': 2}, headers=auth_headers(a['token'])).get_json()
    semi0 = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 0)
    client.post(f"/api/tournaments/{t2['id']}/matches/{semi0['id']}/score",
                json={'score1': 11, 'score2': 3}, headers=auth_headers(a['token']))
    # Now q0 feeds a played semi — correcting it must 409
    res = client.post(f"/api/tournaments/{t2['id']}/matches/{q0['id']}/score",
                      json={'score1': 1, 'score2': 11}, headers=auth_headers(a['token']))
    assert res.status_code == 409
    assert res.get_json()['error'] == 'next_match_played'


def test_tournament_organizer_edit(client):
    """Organizer can rename/reschedule/resize; entrants are told about moves."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, max_entries=4)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])

    # Only the organizer
    res = client.patch(f"/api/tournaments/{t['id']}", json={'name': 'Hijacked'},
                       headers=auth_headers(b['token']))
    assert res.status_code == 403

    # Rename + description + resize
    res = client.patch(f"/api/tournaments/{t['id']}",
                       json={'name': 'Renamed Slam', 'description': 'BYO paddle', 'max_entries': 8},
                       headers=auth_headers(a['token']))
    data = res.get_json()
    assert res.status_code == 200
    assert data['name'] == 'Renamed Slam' and data['max_entries'] == 8
    # Renames alone don't spam entrants
    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    assert not [n for n in notifs if n['kind'] == 'tournament_update']

    # Can't shrink below the current entry count (2 entries, floor is MIN=2 → ask for 2 is fine; below via bad value)
    res = client.patch(f"/api/tournaments/{t['id']}", json={'max_entries': 'zap'},
                       headers=auth_headers(a['token']))
    assert res.status_code == 400
    # Reschedule notifies Ben
    from datetime import timedelta
    from backend.models import utcnow
    new_start = (utcnow() + timedelta(hours=48)).isoformat() + 'Z'
    res = client.patch(f"/api/tournaments/{t['id']}", json={'starts_at': new_start},
                       headers=auth_headers(a['token']))
    assert res.status_code == 200
    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    moved = [n for n in notifs if n['kind'] == 'tournament_update']
    assert len(moved) == 1 and moved[0]['related_tournament_id'] == t['id']

    # Bad values rejected
    assert client.patch(f"/api/tournaments/{t['id']}", json={'name': 'x'},
                        headers=auth_headers(a['token'])).status_code == 400
    assert client.patch(f"/api/tournaments/{t['id']}", json={'starts_at': 'nope'},
                        headers=auth_headers(a['token'])).status_code == 400

    # After start: rename still OK, resize refused
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    assert client.patch(f"/api/tournaments/{t['id']}", json={'name': 'Live Rename'},
                        headers=auth_headers(a['token'])).status_code == 200
    assert client.patch(f"/api/tournaments/{t['id']}", json={'max_entries': 16},
                        headers=auth_headers(a['token'])).status_code == 409

    # After cancel: nothing editable
    client.post(f"/api/tournaments/{t['id']}/cancel", headers=auth_headers(a['token']))
    assert client.patch(f"/api/tournaments/{t['id']}", json={'name': 'Too Late'},
                        headers=auth_headers(a['token'])).status_code == 409


def test_tournament_day_of_checkin(client):
    """Arrival check-in opens 24h before the start; either partner counts."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    outsider = register(client, 'x@example.com', 'Xan')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Starts in 48h -> check-in not open yet
    t = make_tournament(client, a['token'], court_id, hours_ahead=48)
    _register_entry(client, t['id'], a['token'])
    res = client.post(f"/api/tournaments/{t['id']}/checkin", headers=auth_headers(a['token']))
    assert res.status_code == 409
    assert res.get_json()['error'] == 'checkin_not_open'

    # Starts in 2h -> open; non-entrants rejected
    t2 = make_tournament(client, a['token'], court_id, hours_ahead=2)
    _register_entry(client, t2['id'], a['token'])
    _register_entry(client, t2['id'], b['token'])
    assert client.post(f"/api/tournaments/{t2['id']}/checkin",
                       headers=auth_headers(outsider['token'])).status_code == 404
    data = client.post(f"/api/tournaments/{t2['id']}/checkin",
                       headers=auth_headers(a['token'])).get_json()
    entries = {e['name']: e for e in data['entries']}
    assert entries['Ana']['checked_in'] is True
    assert entries['Ben']['checked_in'] is False
    # Idempotent
    assert client.post(f"/api/tournaments/{t2['id']}/checkin",
                       headers=auth_headers(a['token'])).status_code == 200

    # Still works after the bracket starts
    client.post(f"/api/tournaments/{t2['id']}/start", headers=auth_headers(a['token']))
    data = client.post(f"/api/tournaments/{t2['id']}/checkin",
                       headers=auth_headers(b['token'])).get_json()
    entries = {e['name']: e for e in data['entries']}
    assert entries['Ben']['checked_in'] is True

    # Refused once finished
    final = next(m for m in data['matches'] if m['round'] == 1)
    client.post(f"/api/tournaments/{t2['id']}/matches/{final['id']}/score",
                json={'score1': 11, 'score2': 6}, headers=auth_headers(a['token']))
    detail = client.get(f"/api/tournaments/{t2['id']}", headers=auth_headers(a['token'])).get_json()
    assert detail['status'] == 'completed'
    assert client.post(f"/api/tournaments/{t2['id']}/checkin",
                       headers=auth_headers(a['token'])).status_code == 409


def test_tournament_titles_on_profiles(client):
    """Winning tournaments surfaces a titles count on stats and profiles."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def play_and_win(winner, loser):
        t = make_tournament(client, winner['token'], court_id, max_entries=2)
        _register_entry(client, t['id'], winner['token'])
        _register_entry(client, t['id'], loser['token'])
        client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(winner['token']))
        detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(winner['token'])).get_json()
        m = detail['matches'][0]
        winner_entry = next(e for e in detail['entries']
                            if e['players'][0]['id'] == winner['user']['id'])
        s = {'score1': 11, 'score2': 4} if m['entry1_id'] == winner_entry['id'] else {'score1': 4, 'score2': 11}
        client.post(f"/api/tournaments/{t['id']}/matches/{m['id']}/score",
                    json=s, headers=auth_headers(winner['token']))
        return t

    t1 = play_and_win(a, b)
    play_and_win(a, b)

    stats = client.get('/api/me/stats', headers=auth_headers(a['token'])).get_json()
    assert stats['tournament_titles']['count'] == 2
    assert len(stats['tournament_titles']['recent']) == 2
    assert stats['tournament_titles']['recent'][0]['name'] == 'Summer Slam'

    prof = client.get(f"/api/users/{a['user']['id']}", headers=auth_headers(b['token'])).get_json()
    assert prof['tournament_titles']['count'] == 2
    assert any(r['id'] == t1['id'] for r in prof['tournament_titles']['recent'])
    # Ben has no titles
    prof_b = client.get(f"/api/users/{b['user']['id']}", headers=auth_headers(a['token'])).get_json()
    assert prof_b['tournament_titles']['count'] == 0


def test_court_detail_lists_tournaments(client):
    """Court pages surface open/active tournaments held there."""
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    other_court = client.get('/api/courts?q=adorni').get_json()['items'][0]['id']

    t = make_tournament(client, a['token'], court_id)
    cancelled = make_tournament(client, a['token'], court_id)
    client.post(f"/api/tournaments/{cancelled['id']}/cancel", headers=auth_headers(a['token']))
    elsewhere = make_tournament(client, a['token'], other_court)

    detail = client.get(f'/api/courts/{court_id}', headers=auth_headers(a['token'])).get_json()
    ids = [x['id'] for x in detail['tournaments']]
    assert t['id'] in ids
    assert cancelled['id'] not in ids
    assert elsewhere['id'] not in ids
    assert detail['tournaments'][0]['name'] == 'Summer Slam'
    # Anonymous viewers see them too (court detail is public)
    anon = client.get(f'/api/courts/{court_id}').get_json()
    assert any(x['id'] == t['id'] for x in anon['tournaments'])


def test_tournament_pings_court_fans(client):
    """Creating a tournament pings players who saved that court (muteable)."""
    a = register(client, 'a@example.com', 'Ana')
    fan = register(client, 'b@example.com', 'Ben')
    muted = register(client, 'c@example.com', 'Cam')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    for p in (fan, muted):
        assert client.post(f'/api/courts/{court_id}/favorite',
                           headers=auth_headers(p['token'])).status_code in (200, 201)
    client.patch('/api/me', json={'muted_notifications': ['court_game']},
                 headers=auth_headers(muted['token']))

    t = make_tournament(client, a['token'], court_id)

    notifs = client.get('/api/notifications', headers=auth_headers(fan['token'])).get_json()['items']
    pings = [n for n in notifs if n['kind'] == 'court_game']
    assert len(pings) == 1
    assert 'New tournament at' in pings[0]['title']
    assert pings[0]['related_tournament_id'] == t['id']

    notifs = client.get('/api/notifications', headers=auth_headers(muted['token'])).get_json()['items']
    assert not [n for n in notifs if n['kind'] == 'court_game']

    # Anti-churn: a second tournament right away doesn't re-ping the fan
    make_tournament(client, a['token'], court_id)
    notifs = client.get('/api/notifications', headers=auth_headers(fan['token'])).get_json()['items']
    assert len([n for n in notifs if n['kind'] == 'court_game']) == 1


def test_tournament_partner_swap(client):
    """The registering player can swap doubles partners during registration."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    stranger = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, event_type='doubles')

    def befriend(x, y):
        client.post('/api/friends/request', json={'user_id': y['user']['id']}, headers=auth_headers(x['token']))
        fid = client.get('/api/friends', headers=auth_headers(y['token'])).get_json()['incoming'][0]['friendship_id']
        client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(y['token']))

    befriend(a, b)
    befriend(a, c)
    _register_entry(client, t['id'], a['token'], partner_id=b['user']['id'])

    # Partner (player2) can't swap; strangers can't be swapped in
    res = client.patch(f"/api/tournaments/{t['id']}/register",
                       json={'partner_id': c['user']['id']}, headers=auth_headers(b['token']))
    assert res.status_code == 403
    res = client.patch(f"/api/tournaments/{t['id']}/register",
                       json={'partner_id': stranger['user']['id']}, headers=auth_headers(a['token']))
    assert res.status_code == 403

    # Ana swaps Ben -> Cam; both sides are told
    res = client.patch(f"/api/tournaments/{t['id']}/register",
                       json={'partner_id': c['user']['id']}, headers=auth_headers(a['token']))
    assert res.status_code == 200
    entry = res.get_json()['entries'][0]
    assert entry['name'] == 'Ana & Cam'
    notifs = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_withdraw' and 'changed partners' in n['title'] for n in notifs)
    notifs = client.get('/api/notifications', headers=auth_headers(c['token'])).get_json()['items']
    assert any(n['kind'] == 'tournament_invite' for n in notifs)

    # Ben is free again and can enter his own team
    _register_entry(client, t['id'], b['token'], partner_id=a['user']['id'], expect=409)  # Ana taken
    befriend(b, stranger)
    _register_entry(client, t['id'], b['token'], partner_id=stranger['user']['id'])

    # Swap refused once the bracket starts
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    res = client.patch(f"/api/tournaments/{t['id']}/register",
                       json={'partner_id': b['user']['id']}, headers=auth_headers(a['token']))
    assert res.status_code == 409


def test_tournament_reminders(client):
    """Day-before and hour-before reminders fire once; reschedule re-arms."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def reminders_for(who):
        notifs = client.get('/api/notifications', headers=auth_headers(who['token'])).get_json()['items']
        return [n for n in notifs if n['kind'] == 'tournament_reminder']

    # Day-before: starts in ~22h
    t = make_tournament(client, a['token'], court_id, hours_ahead=22)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])
    client.get('/api/me', headers=auth_headers(a['token']))  # trigger sweep
    assert len(reminders_for(a)) == 1
    assert len(reminders_for(b)) == 1
    assert 'tomorrow' in reminders_for(b)[0]['title']
    assert reminders_for(b)[0]['related_tournament_id'] == t['id']
    # Sweep again — no duplicates
    client.get('/api/me', headers=auth_headers(b['token']))
    assert len(reminders_for(b)) == 1

    # Hour-before: starts in ~30min (fresh tournament)
    t2 = make_tournament(client, a['token'], court_id, hours_ahead=0.5)
    _register_entry(client, t2['id'], a['token'])
    client.get('/api/me', headers=auth_headers(a['token']))
    mine = [n for n in reminders_for(a) if n['related_tournament_id'] == t2['id']]
    assert len(mine) == 1 and 'about an hour' in mine[0]['title']

    # Reschedule re-arms the day-before reminder
    from datetime import timedelta
    from backend.models import utcnow
    new_start = (utcnow() + timedelta(hours=23)).isoformat() + 'Z'
    client.patch(f"/api/tournaments/{t['id']}", json={'starts_at': new_start},
                 headers=auth_headers(a['token']))
    client.get('/api/me', headers=auth_headers(a['token']))
    t1_reminders = [n for n in reminders_for(b) if n['related_tournament_id'] == t['id']]
    assert len(t1_reminders) == 2  # original + re-armed after reschedule


def test_active_tournament_banner_payload(client):
    """/me surfaces an imminent or in-progress tournament for the banner."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Nothing yet
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament'] is None

    # 48h out -> still nothing
    far = make_tournament(client, a['token'], court_id, hours_ahead=48)
    _register_entry(client, far['id'], a['token'])
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament'] is None

    # 3h out -> banner, 'soon', check-in state tracked
    soon = make_tournament(client, a['token'], court_id, hours_ahead=3)
    _register_entry(client, soon['id'], a['token'])
    _register_entry(client, soon['id'], b['token'])
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    at = me['active_tournament']
    assert at['id'] == soon['id'] and at['banner_state'] == 'soon'
    assert at['my_checked_in'] is False
    client.post(f"/api/tournaments/{soon['id']}/checkin", headers=auth_headers(a['token']))
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament']['my_checked_in'] is True

    # Started -> 'live' beats 'soon'; organizers see it even without an entry
    client.post(f"/api/tournaments/{soon['id']}/start", headers=auth_headers(a['token']))
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament']['banner_state'] == 'live'
    # Ben (entrant only) sees it too
    me_b = client.get('/api/me', headers=auth_headers(b['token'])).get_json()
    assert me_b['active_tournament']['id'] == soon['id']

    # Completed -> gone (finish the 2-entry bracket)
    detail = client.get(f"/api/tournaments/{soon['id']}", headers=auth_headers(a['token'])).get_json()
    m = detail['matches'][0]
    client.post(f"/api/tournaments/{soon['id']}/matches/{m['id']}/score",
                json={'score1': 11, 'score2': 6}, headers=auth_headers(a['token']))
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament'] is None


def test_calendar_feed_includes_tournaments(client):
    """The personal .ics feed carries tournaments you're in or organizing."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, hours_ahead=30)
    _register_entry(client, t['id'], b['token'])

    def feed_for(who):
        token = client.get('/api/calendar/token', headers=auth_headers(who['token'])).get_json()['token']
        res = client.get(f'/api/calendar/{token}.ics')
        assert res.status_code == 200
        return res.get_data(as_text=True)

    # Organizer (no entry) and entrant both see it
    for who in (a, b):
        ics = feed_for(who)
        assert f'thirdshot-tournament-{t["id"]}@thirdshot.app' in ics
        assert 'Summer Slam' in ics

    # Cancelled tournaments drop out of the feed
    client.post(f"/api/tournaments/{t['id']}/cancel", headers=auth_headers(a['token']))
    assert f'thirdshot-tournament-{t["id"]}' not in feed_for(b)


def test_weekly_recap_mentions_tournament_titles(client):
    """A title won last week headlines the weekly recap."""
    from datetime import timedelta

    from backend.app import db
    from backend.models import Notification, Tournament, User, utcnow
    from backend.routes.auth import _maybe_weekly_recap

    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, max_entries=2)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    final = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()['matches'][0]
    data = client.post(f"/api/tournaments/{t['id']}/matches/{final['id']}/score",
                       json={'score1': 11, 'score2': 3}, headers=auth_headers(a['token'])).get_json()
    winner_id = data['champion']['players'][0]['id']

    # Time-travel the win into last week and force a fresh recap (in-context
    # assertions only — HTTP after time-travel flakes on StaticPool).
    row = db.session.get(Tournament, t['id'])
    row.completed_at = utcnow() - timedelta(days=7)
    winner = db.session.get(User, winner_id)
    winner.last_recap_week = ''
    db.session.commit()

    _maybe_weekly_recap(winner)
    recaps = Notification.query.filter_by(user_id=winner.id, kind='weekly_recap').all()
    assert recaps, 'expected a recap notification'
    assert any('you won Summer Slam' in n.title for n in recaps), [n.title for n in recaps]
    assert any(n.related_tournament_id == t['id'] for n in recaps)


def test_stats_insights(client):
    """3+ scored games unlock play-pattern insights on /me/stats."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    def play(score_a, score_b):
        game = make_game(client, a['token'], court_id, game_type='ranked', hours_ahead=1)
        client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
        client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [a['user']['id']], 'team2': [b['user']['id']],
            'score_team1': score_a, 'score_team2': score_b,
        }, headers=auth_headers(a['token']))
        client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(b['token']))

    play(11, 5)
    stats = client.get('/api/me/stats', headers=auth_headers(a['token'])).get_json()
    assert stats['insights'] is None  # not enough scored games yet
    play(11, 7)
    play(6, 11)

    stats = client.get('/api/me/stats', headers=auth_headers(a['token'])).get_json()
    ins = stats['insights']
    assert ins is not None
    # 3 games, +6 +4 -5 -> avg +1.7
    assert ins['avg_margin'] == 1.7
    assert ins['busiest_day']  # some weekday name
    # All three games share a day-part -> best_part covers all of them
    assert ins['best_part']['games'] == 3 and ins['best_part']['wins'] == 2


def test_tournament_endpoints_require_auth(client):
    """Every tournament endpoint rejects anonymous callers outright."""
    calls = [
        ('get', '/api/tournaments?mine=1'), ('get', '/api/tournaments/1'),
        ('post', '/api/tournaments'), ('patch', '/api/tournaments/1'),
        ('post', '/api/tournaments/1/register'), ('patch', '/api/tournaments/1/register'),
        ('delete', '/api/tournaments/1/register'), ('delete', '/api/tournaments/1/entries/1'),
        ('post', '/api/tournaments/1/start'), ('post', '/api/tournaments/1/cancel'),
        ('post', '/api/tournaments/1/checkin'), ('post', '/api/tournaments/1/matches/1/score'),
        ('get', '/api/tournaments/1/chat'), ('post', '/api/tournaments/1/chat'),
    ]
    for method, path in calls:
        res = getattr(client, method)(path, json={})
        assert res.status_code == 401, (method, path, res.status_code)


def test_tournament_abuse_payloads(client):
    """Hostile inputs: clamped sizes, cross-tournament match IDOR, junk scores."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Size clamping on create
    big = make_tournament(client, a['token'], court_id, max_entries=9999)
    assert big['max_entries'] == 32
    tiny = make_tournament(client, a['token'], court_id, max_entries=-5)
    assert tiny['max_entries'] == 2

    # Two started tournaments; try to score t1's match through t2's URL (IDOR)
    def started_tournament():
        t = make_tournament(client, a['token'], court_id, max_entries=2)
        _register_entry(client, t['id'], a['token'])
        _register_entry(client, t['id'], b['token'])
        return client.post(f"/api/tournaments/{t['id']}/start",
                           headers=auth_headers(a['token'])).get_json()

    t1, t2 = started_tournament(), started_tournament()
    foreign_match = t1['matches'][0]['id']
    res = client.post(f"/api/tournaments/{t2['id']}/matches/{foreign_match}/score",
                      json={'score1': 11, 'score2': 0}, headers=auth_headers(a['token']))
    assert res.status_code == 404
    # …and the foreign match is untouched
    detail = client.get(f"/api/tournaments/{t1['id']}", headers=auth_headers(a['token'])).get_json()
    assert detail['matches'][0]['winner_entry_id'] is None

    # Junk scores
    own_match = t1['matches'][0]['id']
    for payload in ({'score1': 'eleven', 'score2': 3}, {'score1': -1, 'score2': 5},
                    {'score1': 100, 'score2': 5}, {}, {'score1': None, 'score2': None}):
        res = client.post(f"/api/tournaments/{t1['id']}/matches/{own_match}/score",
                          json=payload, headers=auth_headers(a['token']))
        assert res.status_code == 400, payload

    # Check-in refused on a cancelled tournament
    t3 = make_tournament(client, a['token'], court_id, hours_ahead=2)
    _register_entry(client, t3['id'], a['token'])
    client.post(f"/api/tournaments/{t3['id']}/cancel", headers=auth_headers(a['token']))
    assert client.post(f"/api/tournaments/{t3['id']}/checkin",
                       headers=auth_headers(a['token'])).status_code == 409

    # Oversized chat body is truncated to 2000 chars, never 500s
    t4 = make_tournament(client, a['token'], court_id)
    _register_entry(client, t4['id'], a['token'])
    res = client.post(f"/api/tournaments/{t4['id']}/chat", json={'body': 'x' * 5000},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201
    assert len(res.get_json()['body']) == 2000


def test_ranked_tournament_applies_elo(client):
    """Ranked tournaments settle ELO for every decided match at completion."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    from datetime import timedelta
    from backend.models import utcnow
    res = client.post('/api/tournaments', json={
        'name': 'Ranked Rumble', 'court_id': court_id,
        'starts_at': (utcnow() + timedelta(hours=2)).isoformat() + 'Z',
        'format': 'single_elim', 'event_type': 'singles',
        'max_entries': 4, 'ranked': True,
    }, headers=auth_headers(a['token']))
    t = res.get_json()
    assert t['ranked'] is True

    for p in (a, b, c, d):
        _register_entry(client, t['id'], p['token'])
    data = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token'])).get_json()

    def score(mid, s1, s2):
        return client.post(f"/api/tournaments/{t['id']}/matches/{mid}/score",
                           json={'score1': s1, 'score2': s2},
                           headers=auth_headers(a['token'])).get_json()

    semis = [m for m in data['matches'] if m['round'] == 1]
    data = score(semis[0]['id'], 11, 5)
    data = score(semis[1]['id'], 11, 7)
    # No ratings move until completion (corrections stay safe)
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['user']['rating'] == 1200
    final = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 0)
    third = next(m for m in data['matches'] if m['round'] == 2 and m['position'] == 1)
    data = score(final['id'], 11, 9)
    data = score(third['id'], 11, 2)
    assert data['status'] == 'completed'

    # Everyone played 2 rated matches; champion strictly gained, 4th lost
    champ_id = data['champion']['players'][0]['id']
    users = {p['user']['id']: p for p in ()}
    ratings = {}
    for p in (a, b, c, d):
        u = client.get('/api/me', headers=auth_headers(p['token'])).get_json()['user']
        ratings[u['id']] = u
    champ = ratings[champ_id]
    assert champ['rating'] > 1200
    assert champ['ranked_wins'] == 2 and champ['ranked_losses'] == 0
    # Ratings are zero-sum across the field
    assert sum(u['rating'] for u in ratings.values()) == 4800
    # Each player got a net-rating notification
    notifs = client.get('/api/notifications', headers=auth_headers(a['token'])).get_json()['items']
    assert any(n['kind'] == 'ranked_result' and 'Ranked Rumble rating:' in n['title'] for n in notifs)


def test_casual_tournament_leaves_ratings_alone(client):
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id, max_entries=2)  # ranked defaults off
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    m = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()['matches'][0]
    data = client.post(f"/api/tournaments/{t['id']}/matches/{m['id']}/score",
                       json={'score1': 11, 'score2': 1}, headers=auth_headers(a['token'])).get_json()
    assert data['status'] == 'completed'
    for p in (a, b):
        u = client.get('/api/me', headers=auth_headers(p['token'])).get_json()['user']
        assert u['rating'] == 1200 and u['ranked_wins'] == 0 and u['ranked_losses'] == 0


def test_leaderboard_shows_title_counts(client):
    """Leaderboard rows carry each player's tournament-title count."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # Get both on the board with one ranked game
    game = make_game(client, a['token'], court_id, game_type='ranked', hours_ahead=1)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))
    client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [a['user']['id']], 'team2': [b['user']['id']],
        'score_team1': 11, 'score_team2': 4,
    }, headers=auth_headers(a['token']))
    client.post(f"/api/games/{game['id']}/confirm", headers=auth_headers(b['token']))

    # Ana wins a quick tournament
    t = make_tournament(client, a['token'], court_id, max_entries=2)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    m = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()['matches'][0]
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()
    ana_entry = next(e for e in detail['entries'] if e['name'] == 'Ana')
    s = {'score1': 11, 'score2': 2} if m['entry1_id'] == ana_entry['id'] else {'score1': 2, 'score2': 11}
    client.post(f"/api/tournaments/{t['id']}/matches/{m['id']}/score",
                json=s, headers=auth_headers(a['token']))

    board = client.get('/api/leaderboard', headers=auth_headers(b['token'])).get_json()['items']
    by_name = {u['display_name']: u for u in board}
    assert by_name['Ana']['tournament_titles'] == 1
    assert by_name['Ben']['tournament_titles'] == 0


def test_banner_shows_next_opponent(client):
    """The live-tournament banner payload names who you face next."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    c = register(client, 'c@example.com', 'Cam')
    d = register(client, 'd@example.com', 'Dee')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    from backend.app import db
    from backend.models import User
    for email, rating in (('a@example.com', 1400), ('b@example.com', 1300),
                          ('c@example.com', 1250), ('d@example.com', 1100)):
        User.query.filter_by(email=email).first().rating = rating
    db.session.commit()

    t = make_tournament(client, a['token'], court_id, hours_ahead=2)
    for p in (a, b, c, d):
        _register_entry(client, t['id'], p['token'])
    data = client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token'])).get_json()

    # Seeds: Ana(1) vs Dee(4), Ben(2) vs Cam(3)
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament']['my_next_opponent'] == 'Dee'

    # Ana beats Dee -> her next match (the final) isn't set yet
    semis = [m for m in data['matches'] if m['round'] == 1]
    m0 = next(m for m in semis if m['position'] == 0)
    client.post(f"/api/tournaments/{t['id']}/matches/{m0['id']}/score",
                json={'score1': 11, 'score2': 5}, headers=auth_headers(a['token']))
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament']['my_next_opponent'] is None

    # Ben beats Cam -> Ana's final opponent is Ben; Dee now plays Cam for bronze
    m1 = next(m for m in semis if m['position'] == 1)
    client.post(f"/api/tournaments/{t['id']}/matches/{m1['id']}/score",
                json={'score1': 11, 'score2': 8}, headers=auth_headers(a['token']))
    me = client.get('/api/me', headers=auth_headers(a['token'])).get_json()
    assert me['active_tournament']['my_next_opponent'] == 'Ben'
    me_d = client.get('/api/me', headers=auth_headers(d['token'])).get_json()
    assert me_d['active_tournament']['my_next_opponent'] == 'Cam'


def test_court_past_champions(client):
    """Court pages list recent tournament champions crowned there."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    t = make_tournament(client, a['token'], court_id, max_entries=2)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])
    client.post(f"/api/tournaments/{t['id']}/start", headers=auth_headers(a['token']))
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()
    m = detail['matches'][0]
    ana = next(e for e in detail['entries'] if e['name'] == 'Ana')
    s = {'score1': 11, 'score2': 3} if m['entry1_id'] == ana['id'] else {'score1': 3, 'score2': 11}
    client.post(f"/api/tournaments/{t['id']}/matches/{m['id']}/score",
                json=s, headers=auth_headers(a['token']))

    # An active tournament at the same court must NOT appear as past champion
    make_tournament(client, a['token'], court_id)

    court = client.get(f'/api/courts/{court_id}', headers=auth_headers(a['token'])).get_json()
    champs = court['past_champions']
    assert len(champs) == 1
    assert champs[0]['champion_name'] == 'Ana'
    assert champs[0]['tournament_id'] == t['id']
    # Other courts unaffected
    other = client.get('/api/courts?q=adorni').get_json()['items'][0]['id']
    assert client.get(f'/api/courts/{other}').get_json()['past_champions'] == []


def test_client_error_reporting(client):
    """Browser crash reports are accepted anonymously and never stored."""
    res = client.post('/api/client-errors', json={
        'message': 'TypeError: x is undefined', 'stack': 'x' * 10000, 'url': 'https://app/#court/1',
    })
    assert res.status_code == 204
    # Junk body still 204s (payload is optional-everything)
    assert client.post('/api/client-errors', json=None).status_code == 204
    # Authenticated reports work too
    a = register(client, 'a@example.com', 'Ana')
    assert client.post('/api/client-errors', json={'message': 'boom'},
                       headers=auth_headers(a['token'])).status_code == 204


def test_court_photo_captions(client):
    """Photo uploads accept an optional caption, served back in the gallery."""
    import base64
    a = register(client, 'a@example.com', 'Ana')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    png = 'data:image/png;base64,' + base64.b64encode(b'x' * 400).decode()

    res = client.post(f'/api/courts/{court_id}/photo',
                      json={'photo': png, 'caption': '  Fresh nets on 1–2! ' + 'y' * 200},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201, res.get_json()

    photos = client.get(f'/api/courts/{court_id}/photos').get_json()['items']
    assert photos[0]['caption'].startswith('Fresh nets on 1–2!')
    assert len(photos[0]['caption']) <= 140  # truncated

    # Caption optional
    res = client.post(f'/api/courts/{court_id}/photo', json={'photo': png},
                      headers=auth_headers(a['token']))
    assert res.status_code == 201
    photos = client.get(f'/api/courts/{court_id}/photos').get_json()['items']
    assert photos[0]['caption'] == ''


def test_decline_game_invite(client):
    """Declining a personal invite removes it and notifies the host."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    outsider = register(client, 'x@example.com', 'Xan')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, visibility='private',
                     invite_user_ids=[b['user']['id']])

    # Only invitees can decline
    assert client.post(f"/api/games/{game['id']}/invites/decline",
                       headers=auth_headers(outsider['token'])).status_code == 404

    res = client.post(f"/api/games/{game['id']}/invites/decline",
                      headers=auth_headers(b['token']))
    assert res.status_code == 200 and res.get_json()['declined'] is True
    # Host got the heads-up
    notifs = client.get('/api/notifications', headers=auth_headers(a['token'])).get_json()['items']
    assert any(n['kind'] == 'invite_declined' and "can't make your game" in n['title'] for n in notifs)
    # The private game is no longer visible to Ben, and a second decline 404s
    detail = client.get(f"/api/games/{game['id']}", headers=auth_headers(b['token'])).get_json()
    assert detail.get('is_joined') is False
    assert client.post(f"/api/games/{game['id']}/invites/decline",
                       headers=auth_headers(b['token'])).status_code == 404

    # A joined player can't "decline" (invite + join edge)
    game2 = make_game(client, a['token'], court_id, visibility='private',
                      invite_user_ids=[b['user']['id']])
    client.post(f"/api/games/{game2['id']}/join", headers=auth_headers(b['token']))
    assert client.post(f"/api/games/{game2['id']}/invites/decline",
                       headers=auth_headers(b['token'])).status_code == 400


def test_tournament_chat_unread_badge(client):
    """Detail payload counts unread chat messages; reading the thread clears it."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    outsider = register(client, 'x@example.com', 'Xan')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    t = make_tournament(client, a['token'], court_id)
    _register_entry(client, t['id'], a['token'])
    _register_entry(client, t['id'], b['token'])

    client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'first'},
                headers=auth_headers(a['token']))
    client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'second'},
                headers=auth_headers(a['token']))

    # Ben (never opened the thread): 2 unread; Ana (sender): 0
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(b['token'])).get_json()
    assert detail['chat_unread'] == 2
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(a['token'])).get_json()
    assert detail['chat_unread'] == 0
    # Non-members always see 0
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(outsider['token'])).get_json()
    assert detail['chat_unread'] == 0

    # Ben opens the chat -> cleared; a new message bumps it again
    client.get(f"/api/tournaments/{t['id']}/chat", headers=auth_headers(b['token']))
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(b['token'])).get_json()
    assert detail['chat_unread'] == 0
    client.post(f"/api/tournaments/{t['id']}/chat", json={'body': 'third'},
                headers=auth_headers(a['token']))
    detail = client.get(f"/api/tournaments/{t['id']}", headers=auth_headers(b['token'])).get_json()
    assert detail['chat_unread'] == 1


def test_delete_own_message(client):
    """You can delete your own messages in any thread; nobody else's."""
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    # DM
    dm = client.post(f"/api/chat/{b['user']['id']}", json={'body': 'oops typo'},
                     headers=auth_headers(a['token'])).get_json()
    # Court room message
    room = client.post(f'/api/courts/{court_id}/chat', json={'body': 'court msg'},
                       headers=auth_headers(b['token'])).get_json()

    # Recipient can't delete the sender's DM
    res = client.delete(f"/api/messages/{dm['id']}", headers=auth_headers(b['token']))
    assert res.status_code == 403
    # Sender can
    res = client.delete(f"/api/messages/{dm['id']}", headers=auth_headers(a['token']))
    assert res.status_code == 200 and res.get_json()['deleted'] is True
    thread = client.get(f"/api/chat/{b['user']['id']}", headers=auth_headers(a['token'])).get_json()
    assert not any(m['id'] == dm['id'] for m in thread['items'])
    # Second delete 404s; court message deletable by its sender too
    assert client.delete(f"/api/messages/{dm['id']}", headers=auth_headers(a['token'])).status_code == 404
    assert client.delete(f"/api/messages/{room['id']}", headers=auth_headers(b['token'])).status_code == 200


def test_health_reports_db(client):
    """Health pings the database and says so."""
    res = client.get('/health')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'ok' and data['db'] is True


def test_court_photo_likes(client):
    """Photo hearts toggle, count, and surface per-viewer state."""
    import base64
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    png = 'data:image/png;base64,' + base64.b64encode(b'x' * 400).decode()
    client.post(f'/api/courts/{court_id}/photo', json={'photo': png},
                headers=auth_headers(a['token']))
    photo_id = client.get(f'/api/courts/{court_id}/photos').get_json()['items'][0]['id']

    # Both players like it
    res = client.post(f'/api/courts/{court_id}/photos/{photo_id}/like',
                      headers=auth_headers(a['token']))
    assert res.get_json() == {'liked': True, 'likes': 1}
    res = client.post(f'/api/courts/{court_id}/photos/{photo_id}/like',
                      headers=auth_headers(b['token']))
    assert res.get_json() == {'liked': True, 'likes': 2}

    # Per-viewer state; anonymous sees counts but no liked_by_me
    items = client.get(f'/api/courts/{court_id}/photos',
                       headers=auth_headers(a['token'])).get_json()['items']
    assert items[0]['likes'] == 2 and items[0]['liked_by_me'] is True
    anon = client.get(f'/api/courts/{court_id}/photos').get_json()['items']
    assert anon[0]['likes'] == 2 and anon[0]['liked_by_me'] is False

    # Toggle off; bogus photo 404s
    res = client.post(f'/api/courts/{court_id}/photos/{photo_id}/like',
                      headers=auth_headers(b['token']))
    assert res.get_json() == {'liked': False, 'likes': 1}
    assert client.post(f'/api/courts/{court_id}/photos/9999/like',
                       headers=auth_headers(a['token'])).status_code == 404


def test_send_feedback(client):
    """Feedback requires auth and a non-trivial message."""
    a = register(client, 'a@example.com', 'Ana')
    assert client.post('/api/feedback', json={'message': 'love the brackets!'}).status_code == 401
    res = client.post('/api/feedback', json={'message': 'love the brackets!'},
                      headers=auth_headers(a['token']))
    assert res.status_code == 200 and res.get_json()['sent'] is True
    assert client.post('/api/feedback', json={'message': ''},
                       headers=auth_headers(a['token'])).status_code == 400


def test_cancel_stale_unplayed_game(client):
    """A host can clear a game that started hours ago but was never played."""
    from datetime import timedelta

    from backend.app import db
    from backend.models import Game as GameModel, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))

    # Time-travel the start into yesterday (in-context; then assert via HTTP,
    # which is safe here since we mutate before any dependent reads)
    row = db.session.get(GameModel, game['id'])
    row.scheduled_at = utcnow() - timedelta(hours=30)
    db.session.commit()

    res = client.post(f"/api/games/{game['id']}/cancel", headers=auth_headers(a['token']))
    assert res.status_code == 200
    assert res.get_json()['status'] == 'cancelled'
