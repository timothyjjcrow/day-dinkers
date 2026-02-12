"""Tests for open-to-play sessions filters and waitlist behavior."""
import json
from datetime import datetime, timedelta


def _register(client, username, email):
    res = client.post('/api/auth/register', json={
        'username': username,
        'email': email,
        'password': 'password123',
    })
    data = json.loads(res.data)
    return data['token'], data['user']['id']


def _create_court(client, token, name='Session Court'):
    res = client.post('/api/courts', json={
        'name': name,
        'latitude': 40.80,
        'longitude': -124.16,
        'city': 'Eureka',
    }, headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['court']


def _create_session(client, token, court_id, **overrides):
    payload = {
        'court_id': court_id,
        'session_type': 'now',
        'game_type': 'open',
        'skill_level': 'all',
        'max_players': 4,
        'visibility': 'all',
        'notes': '',
    }
    payload.update(overrides)
    headers = {'Authorization': f'Bearer {token}'}
    if payload.get('session_type', 'now') == 'now':
        client.post('/api/presence/checkin', json={'court_id': court_id}, headers=headers)
    res = client.post('/api/sessions', json=payload, headers=headers)
    return json.loads(res.data)['session']


def test_now_session_requires_checkin_at_court(client):
    token, _ = _register(client, 'now_checkin', 'now_checkin@test.com')
    court = _create_court(client, token, 'Now Checkin Court')
    headers = {'Authorization': f'Bearer {token}'}

    payload = {
        'court_id': court['id'],
        'session_type': 'now',
        'game_type': 'open',
        'skill_level': 'all',
        'max_players': 4,
        'visibility': 'all',
    }
    res = client.post('/api/sessions', json=payload, headers=headers)
    assert res.status_code == 400
    assert 'Check in at this court' in json.loads(res.data)['error']

    checkin = client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=headers)
    assert checkin.status_code == 201

    res = client.post(
        '/api/sessions',
        json={**payload, 'duration_minutes': 120},
        headers=headers,
    )
    assert res.status_code == 201


def test_now_session_duration_sets_times_and_expires(client, app):
    token, _ = _register(client, 'now_duration', 'now_duration@test.com')
    court = _create_court(client, token, 'Now Duration Court')
    headers = {'Authorization': f'Bearer {token}'}

    checkin = client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=headers)
    assert checkin.status_code == 201

    create = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'now',
        'duration_minutes': 60,
    }, headers=headers)
    assert create.status_code == 201

    session = json.loads(create.data)['session']
    assert session['start_time']
    assert session['end_time']
    start_time = datetime.fromisoformat(session['start_time'])
    end_time = datetime.fromisoformat(session['end_time'])
    minutes = round((end_time - start_time).total_seconds() / 60)
    assert 59 <= minutes <= 61

    from backend.app import db
    from backend.models import PlaySession
    from backend.time_utils import utcnow_naive

    with app.app_context():
        db_session = db.session.get(PlaySession, session['id'])
        db_session.end_time = utcnow_naive() - timedelta(minutes=1)
        db.session.commit()

    list_res = client.get(f'/api/sessions?court_id={court["id"]}')
    assert list_res.status_code == 200
    listed_ids = [s['id'] for s in json.loads(list_res.data)['sessions']]
    assert session['id'] not in listed_ids

    with app.app_context():
        refreshed = db.session.get(PlaySession, session['id'])
        assert refreshed.status == 'completed'


def test_sessions_visibility_and_skill_filters(client):
    token_a, _ = _register(client, 'sess_a', 'sess_a@test.com')
    token_b, uid_b = _register(client, 'sess_b', 'sess_b@test.com')
    token_c, _ = _register(client, 'sess_c', 'sess_c@test.com')

    court = _create_court(client, token_a)

    # Make A and B friends so B can view A's friends-only session.
    client.post('/api/auth/friends/request', json={'friend_id': uid_b},
        headers={'Authorization': f'Bearer {token_a}'})
    pending = client.get('/api/auth/friends/pending', headers={'Authorization': f'Bearer {token_b}'})
    pending_id = json.loads(pending.data)['requests'][0]['id']
    client.post('/api/auth/friends/respond', json={
        'friendship_id': pending_id, 'action': 'accept'
    }, headers={'Authorization': f'Bearer {token_b}'})

    friends_session = _create_session(
        client, token_a, court['id'],
        visibility='friends', skill_level='beginner', notes='Friends beginner session',
    )
    open_session = _create_session(
        client, token_c, court['id'],
        visibility='all', skill_level='advanced', notes='Open advanced session',
    )

    res = client.get('/api/sessions?visibility=friends',
        headers={'Authorization': f'Bearer {token_b}'})
    data = json.loads(res.data)['sessions']
    ids = [s['id'] for s in data]
    assert friends_session['id'] in ids
    assert open_session['id'] not in ids

    res = client.get('/api/sessions?skill_level=advanced')
    data = json.loads(res.data)['sessions']
    ids = [s['id'] for s in data]
    assert open_session['id'] in ids
    assert friends_session['id'] not in ids


def test_session_waitlist_and_auto_promotion(client):
    host_token, _ = _register(client, 'sess_host', 'sess_host@test.com')
    p2_token, _ = _register(client, 'sess_p2', 'sess_p2@test.com')
    p3_token, _ = _register(client, 'sess_p3', 'sess_p3@test.com')

    court = _create_court(client, host_token, 'Waitlist Court')
    session = _create_session(client, host_token, court['id'], max_players=2)
    session_id = session['id']

    # First player joins as normal.
    res = client.post(f'/api/sessions/{session_id}/join', json={},
        headers={'Authorization': f'Bearer {p2_token}'})
    assert res.status_code == 200
    assert json.loads(res.data)['waitlisted'] is False

    # Session is now full (host + p2), so p3 should be waitlisted.
    res = client.post(f'/api/sessions/{session_id}/join', json={},
        headers={'Authorization': f'Bearer {p3_token}'})
    assert res.status_code == 200
    assert json.loads(res.data)['waitlisted'] is True

    # p2 leaves; p3 should be auto-promoted into joined.
    res = client.post(f'/api/sessions/{session_id}/leave', json={},
        headers={'Authorization': f'Bearer {p2_token}'})
    assert res.status_code == 200
    assert json.loads(res.data)['promoted_user_id'] is not None

    res = client.get(f'/api/sessions/{session_id}')
    players = json.loads(res.data)['session']['players']
    statuses = {p['user']['username']: p['status'] for p in players}
    assert statuses.get('sess_p3') == 'joined'
    assert 'sess_p2' not in statuses

    # Promoted player receives an actionable notification.
    notif_res = client.get('/api/auth/notifications', headers={'Authorization': f'Bearer {p3_token}'})
    notifs = json.loads(notif_res.data)['notifications']
    notif_types = [n['notif_type'] for n in notifs]
    assert 'session_spot_opened' in notif_types


def test_create_recurring_series(client):
    host_token, _ = _register(client, 'series_host', 'series_host@test.com')
    court = _create_court(client, host_token, 'Series Court')

    start = (datetime.now() + timedelta(days=1)).replace(second=0, microsecond=0)
    end = start + timedelta(hours=2)
    res = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'scheduled',
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
        'game_type': 'doubles',
        'skill_level': 'intermediate',
        'max_players': 4,
        'visibility': 'all',
        'recurrence': 'weekly',
        'recurrence_count': 3,
    }, headers={'Authorization': f'Bearer {host_token}'})

    assert res.status_code == 201
    data = json.loads(res.data)
    assert data['created_count'] == 3
    assert data['series_id']
    assert len(data['sessions']) == 3

    sessions = data['sessions']
    created_times = [datetime.fromisoformat(s['start_time']) for s in sessions]
    assert (created_times[1] - created_times[0]).days == 7
    assert (created_times[2] - created_times[1]).days == 7

    for idx, session in enumerate(sessions, start=1):
        assert session['series']['id'] == data['series_id']
        assert session['series']['sequence'] == idx
        assert session['series']['occurrences'] == 3


def test_cancel_recurring_series_future_sessions(client):
    host_token, _ = _register(client, 'series_cancel_host', 'series_cancel_host@test.com')
    other_token, _ = _register(client, 'series_cancel_other', 'series_cancel_other@test.com')
    court = _create_court(client, host_token, 'Cancel Series Court')

    start = (datetime.now() + timedelta(days=2)).replace(second=0, microsecond=0)
    end = start + timedelta(hours=2)
    create_res = client.post('/api/sessions', json={
        'court_id': court['id'],
        'session_type': 'scheduled',
        'start_time': start.isoformat(),
        'end_time': end.isoformat(),
        'recurrence': 'weekly',
        'recurrence_count': 3,
    }, headers={'Authorization': f'Bearer {host_token}'})
    create_data = json.loads(create_res.data)
    series_id = create_data['series_id']
    created_ids = [s['id'] for s in create_data['sessions']]

    # Non-creator cannot cancel someone else's series.
    res = client.post(
        f'/api/sessions/series/{series_id}/cancel',
        json={},
        headers={'Authorization': f'Bearer {other_token}'},
    )
    assert res.status_code == 403

    # Creator cancels all future sessions in the series.
    res = client.post(
        f'/api/sessions/series/{series_id}/cancel',
        json={},
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['cancelled_count'] == 3
    assert set(data['cancelled_session_ids']) == set(created_ids)

    # Cancelled sessions should no longer appear in active session lists.
    list_res = client.get('/api/sessions?type=scheduled',
        headers={'Authorization': f'Bearer {host_token}'})
    active_ids = [s['id'] for s in json.loads(list_res.data)['sessions']]
    assert all(session_id not in active_ids for session_id in created_ids)


def test_friends_only_session_blocks_non_friend_access(client):
    host_token, _ = _register(client, 'friends_host', 'friends_host@test.com')
    outsider_token, _ = _register(client, 'friends_outsider', 'friends_outsider@test.com')
    court = _create_court(client, host_token, 'Friends Privacy Court')

    session = _create_session(
        client,
        host_token,
        court['id'],
        visibility='friends',
        notes='Friends only session',
    )
    session_id = session['id']

    detail = client.get(
        f'/api/sessions/{session_id}',
        headers={'Authorization': f'Bearer {outsider_token}'},
    )
    assert detail.status_code == 403

    join = client.post(
        f'/api/sessions/{session_id}/join',
        json={},
        headers={'Authorization': f'Bearer {outsider_token}'},
    )
    assert join.status_code == 403


def test_session_invites_require_creator_and_real_friends(client):
    host_token, host_id = _register(client, 'invite_host', 'invite_host@test.com')
    friend_token, friend_id = _register(client, 'invite_friend', 'invite_friend@test.com')
    other_token, other_id = _register(client, 'invite_other', 'invite_other@test.com')
    court = _create_court(client, host_token, 'Invite Rules Court')
    session = _create_session(client, host_token, court['id'])

    # Only creator can invite.
    not_creator = client.post(
        f'/api/sessions/{session["id"]}/invite',
        json={'friend_ids': [friend_id]},
        headers={'Authorization': f'Bearer {other_token}'},
    )
    assert not_creator.status_code == 403

    # Creator cannot invite non-friends.
    invalid_friend = client.post(
        f'/api/sessions/{session["id"]}/invite',
        json={'friend_ids': [other_id]},
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert invalid_friend.status_code == 400

    # Become friends first, then invite succeeds.
    client.post(
        '/api/auth/friends/request',
        json={'friend_id': friend_id},
        headers={'Authorization': f'Bearer {host_token}'},
    )
    pending = client.get(
        '/api/auth/friends/pending',
        headers={'Authorization': f'Bearer {friend_token}'},
    )
    pending_id = json.loads(pending.data)['requests'][0]['id']
    client.post(
        '/api/auth/friends/respond',
        json={'friendship_id': pending_id, 'action': 'accept'},
        headers={'Authorization': f'Bearer {friend_token}'},
    )

    allowed = client.post(
        f'/api/sessions/{session["id"]}/invite',
        json={'friend_ids': [friend_id, host_id]},
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert allowed.status_code == 200


def test_invalid_scheduled_datetime_returns_400(client):
    host_token, _ = _register(client, 'time_host', 'time_host@test.com')
    court = _create_court(client, host_token, 'Datetime Validation Court')

    res = client.post(
        '/api/sessions',
        json={
            'court_id': court['id'],
            'session_type': 'scheduled',
            'start_time': 'not-a-date',
        },
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert res.status_code == 400


def test_session_rejects_invalid_enum_values_and_capacity(client):
    host_token, _ = _register(client, 'enum_host', 'enum_host@test.com')
    court = _create_court(client, host_token, 'Enum Validation Court')

    res = client.post(
        '/api/sessions',
        json={
            'court_id': court['id'],
            'session_type': 'now',
            'visibility': 'private_weird',
            'skill_level': 'expert+++',
            'game_type': 'triple',
            'max_players': -5,
        },
        headers={'Authorization': f'Bearer {host_token}'},
    )
    assert res.status_code == 400
