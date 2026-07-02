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

    detail = client.get(f"/api/games/{weekly['id']}").get_json()
    assert detail['recurrence'] == 'weekly'
    assert weekly['id'] != g['id']  # sanity: distinct from the one-off game


def test_game_reminder_fires_in_window(client, app):
    from datetime import timedelta
    from backend.models import Game as GameModel, Notification, utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']
    game = make_game(client, a['token'], court_id, hours_ahead=24)
    client.post(f"/api/games/{game['id']}/join", headers=auth_headers(b['token']))

    from backend.routes.games import send_game_reminders
    with app.app_context():
        def reminders():
            return Notification.query.filter_by(kind='game_reminder').all()

        # 24h out: too early, nothing fires.
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

        # Sweeping again never duplicates.
        send_game_reminders()
        assert len(reminders()) == 2

    # The reminder reaches the player through the notifications feed.
    feed = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    assert any(n['kind'] == 'game_reminder' for n in feed['items'])


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
