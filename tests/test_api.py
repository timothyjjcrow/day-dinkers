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

    make_game(client, a['token'], court_id, game_type='ranked')

    notes = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    kinds = [n['kind'] for n in notes['items']]
    assert 'game_invite' in kinds
    invite = [n for n in notes['items'] if n['kind'] == 'game_invite'][0]
    assert 'ranked game' in invite['title']
    assert 'Larson Park' in invite['title']


def test_game_create_notify_friends_opt_out(client):
    from datetime import timedelta
    from backend.models import utcnow
    a = register(client, 'a@example.com', 'Ana')
    b = register(client, 'b@example.com', 'Ben')
    res = client.post('/api/friends/request', json={'user_id': b['user']['id']}, headers=auth_headers(a['token']))
    fid = res.get_json()['friendship_id']
    client.post(f'/api/friends/{fid}/respond', json={'accept': True}, headers=auth_headers(b['token']))
    court_id = client.get('/api/courts?q=larson').get_json()['items'][0]['id']

    when = (utcnow() + timedelta(hours=24)).isoformat() + 'Z'
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': when,
        'notify_friends': False,
    }, headers=auth_headers(a['token']))
    assert res.status_code == 201

    notes = client.get('/api/notifications', headers=auth_headers(b['token'])).get_json()
    assert all(n['kind'] != 'game_invite' for n in notes['items'])


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

def make_game(client, token, court_id, game_type='casual', hours_ahead=24):
    from datetime import timedelta
    from backend.models import utcnow
    when = (utcnow() + timedelta(hours=hours_ahead)).isoformat() + 'Z'
    res = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': when,
        'game_type': game_type,
        'max_players': 4,
    }, headers=auth_headers(token))
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
