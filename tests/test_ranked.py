"""Tests for ranked queue/challenge/scheduled flow and score confirmation."""
import json
from datetime import timedelta

from backend.app import db
from backend.models import Match, MatchPlayer, RankedLobby, User
from backend.time_utils import utcnow_naive


def _register(client, username, email):
    res = client.post('/api/auth/register', json={
        'username': username,
        'email': email,
        'password': 'password123',
    })
    data = json.loads(res.data)
    return data['token'], data['user']['id']


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _create_court(client, token, name='Ranked Court', county_slug=None):
    slug = str(county_slug or 'humboldt').strip().lower()
    payload = {
        'name': name,
        'latitude': 40.80,
        'longitude': -124.16,
        'city': 'Eureka',
    }
    if slug == 'alameda':
        payload.update({
            'latitude': 37.8044,
            'longitude': -122.2712,
            'city': 'Oakland',
        })
    if county_slug:
        payload['county_slug'] = slug
    res = client.post('/api/courts', json=payload, headers=_auth(token))
    return json.loads(res.data)['court']


def _checkin(client, token, court_id):
    return client.post('/api/presence/checkin', json={'court_id': court_id}, headers=_auth(token))


def _create_match(client, token, court_id, team1, team2, match_type='doubles'):
    return client.post('/api/ranked/match', json={
        'court_id': court_id,
        'match_type': match_type,
        'team1': team1,
        'team2': team2,
    }, headers=_auth(token))


def _make_friends(client, requester_token, recipient_token, requester_id, recipient_id):
    send = client.post('/api/auth/friends/request', json={
        'friend_id': recipient_id,
    }, headers=_auth(requester_token))
    assert send.status_code == 201

    pending = client.get('/api/auth/friends/pending', headers=_auth(recipient_token))
    assert pending.status_code == 200
    requests = json.loads(pending.data)['requests']
    friendship = next((r for r in requests if r['user']['id'] == requester_id), None)
    assert friendship is not None

    accept = client.post('/api/auth/friends/respond', json={
        'friendship_id': friendship['id'],
        'action': 'accept',
    }, headers=_auth(recipient_token))
    assert accept.status_code == 200


def test_join_queue_requires_checkin_at_same_court(client):
    token, _ = _register(client, 'queue_checkin_u1', 'queue_checkin_u1@test.com')
    court = _create_court(client, token, 'Queue Checkin Court')

    no_checkin = client.post('/api/ranked/queue/join', json={
        'court_id': court['id'],
        'match_type': 'doubles',
    }, headers=_auth(token))
    assert no_checkin.status_code == 400
    assert 'Check in at this court' in json.loads(no_checkin.data)['error']

    assert _checkin(client, token, court['id']).status_code == 201
    joined = client.post('/api/ranked/queue/join', json={
        'court_id': court['id'],
        'match_type': 'doubles',
    }, headers=_auth(token))
    assert joined.status_code == 201


def test_joining_queue_at_new_court_replaces_old_entry(client):
    token, user_id = _register(client, 'queue_move_u1', 'queue_move_u1@test.com')
    first_court = _create_court(client, token, 'Queue Move Court 1')
    second_court = _create_court(
        client,
        token,
        'Queue Move Court 2',
        county_slug='alameda',
    )

    assert _checkin(client, token, first_court['id']).status_code == 201
    first_join = client.post('/api/ranked/queue/join', json={
        'court_id': first_court['id'],
        'match_type': 'doubles',
    }, headers=_auth(token))
    assert first_join.status_code == 201

    assert _checkin(client, token, second_court['id']).status_code == 201
    second_join = client.post('/api/ranked/queue/join', json={
        'court_id': second_court['id'],
        'match_type': 'doubles',
    }, headers=_auth(token))
    assert second_join.status_code == 201

    first_queue = client.get(f'/api/ranked/queue/{first_court["id"]}')
    assert first_queue.status_code == 200
    first_queue_user_ids = [entry['user_id'] for entry in json.loads(first_queue.data)['queue']]
    assert user_id not in first_queue_user_ids

    second_queue = client.get(f'/api/ranked/queue/{second_court["id"]}')
    assert second_queue.status_code == 200
    second_queue_user_ids = [entry['user_id'] for entry in json.loads(second_queue.data)['queue']]
    assert user_id in second_queue_user_ids


def test_court_challenge_requires_all_players_checked_in(client):
    t1, id1 = _register(client, 'court_ch_u1', 'court_ch_u1@test.com')
    t2, id2 = _register(client, 'court_ch_u2', 'court_ch_u2@test.com')
    court = _create_court(client, t1, 'Court Challenge Court')

    assert _checkin(client, t1, court['id']).status_code == 201

    challenge = client.post('/api/ranked/challenge/court', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
    }, headers=_auth(t1))
    assert challenge.status_code == 400
    assert id2 in json.loads(challenge.data)['missing_player_ids']

    assert _checkin(client, t2, court['id']).status_code == 201
    challenge = client.post('/api/ranked/challenge/court', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
    }, headers=_auth(t1))
    assert challenge.status_code == 201
    lobby_id = json.loads(challenge.data)['lobby']['id']

    pending = client.get('/api/ranked/challenges/pending', headers=_auth(t2))
    assert pending.status_code == 200
    lobby_ids = [l['id'] for l in json.loads(pending.data)['lobbies']]
    assert lobby_id in lobby_ids


def test_scheduled_challenge_only_appears_after_all_accept(client):
    t1, id1 = _register(client, 'sched_u1', 'sched_u1@test.com')
    t2, id2 = _register(client, 'sched_u2', 'sched_u2@test.com')
    court = _create_court(client, t1, 'Scheduled Ranked Court')
    scheduled_for = (utcnow_naive() + timedelta(hours=2)).isoformat()

    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
        'source': 'friends_challenge',
    }, headers=_auth(t1))
    assert create.status_code == 201
    create_data = json.loads(create.data)
    assert create_data['lobby']['source'] == 'friends_challenge'
    lobby_id = create_data['lobby']['id']

    before = client.get(f'/api/ranked/court/{court["id"]}/lobbies')
    assert before.status_code == 200
    assert not json.loads(before.data)['scheduled_lobbies']

    accept = client.post(f'/api/ranked/lobby/{lobby_id}/respond', json={
        'action': 'accept'
    }, headers=_auth(t2))
    assert accept.status_code == 200
    assert json.loads(accept.data)['all_accepted'] is True

    after = client.get(f'/api/ranked/court/{court["id"]}/lobbies')
    assert after.status_code == 200
    scheduled_ids = [l['id'] for l in json.loads(after.data)['scheduled_lobbies']]
    assert lobby_id in scheduled_ids


def test_start_scheduled_lobby_requires_time_and_checkins(client, app):
    t1, id1 = _register(client, 'start_sched_u1', 'start_sched_u1@test.com')
    t2, id2 = _register(client, 'start_sched_u2', 'start_sched_u2@test.com')
    court = _create_court(client, t1, 'Start Scheduled Court')
    scheduled_for = (utcnow_naive() + timedelta(hours=3)).isoformat()

    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
    }, headers=_auth(t1))
    lobby_id = json.loads(create.data)['lobby']['id']
    client.post(
        f'/api/ranked/lobby/{lobby_id}/respond',
        json={'action': 'accept'},
        headers=_auth(t2),
    )

    too_early = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert too_early.status_code == 400
    too_early_data = json.loads(too_early.data)
    assert 'cannot start yet' in too_early_data['error']
    assert too_early_data['scheduled_for'] is not None
    assert too_early_data['seconds_until_start'] > 0

    with app.app_context():
        lobby = db.session.get(RankedLobby, lobby_id)
        lobby.scheduled_for = utcnow_naive() - timedelta(minutes=1)
        db.session.commit()

    no_checkins = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert no_checkins.status_code == 400
    assert sorted(json.loads(no_checkins.data)['missing_player_ids']) == sorted([id1, id2])

    assert _checkin(client, t1, court['id']).status_code == 201
    assert _checkin(client, t2, court['id']).status_code == 201
    started = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert started.status_code == 200
    started_data = json.loads(started.data)
    assert started_data['match']['status'] == 'in_progress'
    assert started_data['lobby']['status'] == 'started'


def test_checked_in_player_can_schedule_ranked_game_and_start_after_checkins(client, app):
    t1, id1 = _register(client, 'checked_sched_u1', 'checked_sched_u1@test.com')
    t2, id2 = _register(client, 'checked_sched_u2', 'checked_sched_u2@test.com')
    court = _create_court(client, t1, 'Checked Scheduled Court')

    assert _checkin(client, t1, court['id']).status_code == 201

    scheduled_for = (utcnow_naive() + timedelta(hours=2)).isoformat()
    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
        'source': 'friends_challenge',
    }, headers=_auth(t1))
    assert create.status_code == 201
    lobby_id = json.loads(create.data)['lobby']['id']

    accept = client.post(
        f'/api/ranked/lobby/{lobby_id}/respond',
        json={'action': 'accept'},
        headers=_auth(t2),
    )
    assert accept.status_code == 200
    assert json.loads(accept.data)['all_accepted'] is True

    with app.app_context():
        lobby = db.session.get(RankedLobby, lobby_id)
        lobby.scheduled_for = utcnow_naive() - timedelta(minutes=1)
        db.session.commit()

    missing_checkin = client.post(
        f'/api/ranked/lobby/{lobby_id}/start',
        json={},
        headers=_auth(t1),
    )
    assert missing_checkin.status_code == 400
    assert json.loads(missing_checkin.data)['missing_player_ids'] == [id2]

    assert _checkin(client, t2, court['id']).status_code == 201
    started = client.post(
        f'/api/ranked/lobby/{lobby_id}/start',
        json={},
        headers=_auth(t1),
    )
    assert started.status_code == 200
    started_data = json.loads(started.data)
    assert started_data['lobby']['status'] == 'started'
    assert started_data['match']['status'] == 'in_progress'


def test_my_lobbies_endpoint_includes_current_user_scheduled_lobbies(client):
    t1, id1 = _register(client, 'my_lobbies_u1', 'my_lobbies_u1@test.com')
    t2, id2 = _register(client, 'my_lobbies_u2', 'my_lobbies_u2@test.com')
    court = _create_court(client, t1, 'My Lobbies Court')
    scheduled_for = (utcnow_naive() + timedelta(hours=2)).isoformat()

    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
        'source': 'friends_challenge',
    }, headers=_auth(t1))
    assert create.status_code == 201
    lobby_id = json.loads(create.data)['lobby']['id']

    mine_creator = client.get('/api/ranked/lobby/my-lobbies', headers=_auth(t1))
    assert mine_creator.status_code == 200
    creator_ids = [l['id'] for l in json.loads(mine_creator.data)['lobbies']]
    assert lobby_id in creator_ids

    mine_invitee = client.get('/api/ranked/lobby/my-lobbies', headers=_auth(t2))
    assert mine_invitee.status_code == 200
    invitee_ids = [l['id'] for l in json.loads(mine_invitee.data)['lobbies']]
    assert lobby_id in invitee_ids


def test_checked_in_court_challenge_walkthrough_flow(client):
    t1, id1 = _register(client, 'walk_court_u1', 'walk_court_u1@test.com')
    t2, id2 = _register(client, 'walk_court_u2', 'walk_court_u2@test.com')
    court = _create_court(client, t1, 'Walkthrough Court Challenge')

    assert _checkin(client, t1, court['id']).status_code == 201
    assert _checkin(client, t2, court['id']).status_code == 201

    create = client.post('/api/ranked/challenge/court', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
    }, headers=_auth(t1))
    assert create.status_code == 201
    lobby_id = json.loads(create.data)['lobby']['id']

    pending = client.get('/api/ranked/challenges/pending', headers=_auth(t2))
    assert pending.status_code == 200
    pending_ids = [l['id'] for l in json.loads(pending.data)['lobbies']]
    assert lobby_id in pending_ids

    accept = client.post(
        f'/api/ranked/lobby/{lobby_id}/respond',
        json={'action': 'accept'},
        headers=_auth(t2),
    )
    assert accept.status_code == 200
    assert json.loads(accept.data)['all_accepted'] is True

    lobbies = client.get(f'/api/ranked/court/{court["id"]}/lobbies')
    assert lobbies.status_code == 200
    ready_ids = [l['id'] for l in json.loads(lobbies.data)['ready_lobbies']]
    assert lobby_id in ready_ids

    start = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert start.status_code == 200
    match_id = json.loads(start.data)['match']['id']

    score = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 7},
        headers=_auth(t1),
    )
    assert score.status_code == 200
    assert json.loads(score.data)['pending_confirmation'] is True

    confirm = client.post(f'/api/ranked/match/{match_id}/confirm', json={}, headers=_auth(t2))
    assert confirm.status_code == 200
    assert json.loads(confirm.data)['all_confirmed'] is True


def test_ranked_friends_walkthrough_flow(client, app):
    t1, id1 = _register(client, 'walk_friend_u1', 'walk_friend_u1@test.com')
    t2, id2 = _register(client, 'walk_friend_u2', 'walk_friend_u2@test.com')
    court = _create_court(client, t1, 'Walkthrough Friends Ranked Court')

    # Become friends through normal endpoints.
    _make_friends(
        client,
        requester_token=t1,
        recipient_token=t2,
        requester_id=id1,
        recipient_id=id2,
    )

    friends_u1 = client.get('/api/auth/friends', headers=_auth(t1))
    assert friends_u1.status_code == 200
    friend_ids_u1 = {u['id'] for u in json.loads(friends_u1.data)['friends']}
    assert id2 in friend_ids_u1

    scheduled_for = (utcnow_naive() + timedelta(hours=2)).isoformat()
    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
        'source': 'friends_challenge',
    }, headers=_auth(t1))
    assert create.status_code == 201
    create_data = json.loads(create.data)
    assert create_data['lobby']['source'] == 'friends_challenge'
    lobby_id = create_data['lobby']['id']

    mine_creator = client.get('/api/ranked/lobby/my-lobbies', headers=_auth(t1))
    assert mine_creator.status_code == 200
    mine_creator_ids = [l['id'] for l in json.loads(mine_creator.data)['lobbies']]
    assert lobby_id in mine_creator_ids

    pending_invitee = client.get('/api/ranked/challenges/pending', headers=_auth(t2))
    assert pending_invitee.status_code == 200
    pending_invitee_ids = [l['id'] for l in json.loads(pending_invitee.data)['lobbies']]
    assert lobby_id in pending_invitee_ids

    accept = client.post(
        f'/api/ranked/lobby/{lobby_id}/respond',
        json={'action': 'accept'},
        headers=_auth(t2),
    )
    assert accept.status_code == 200
    assert json.loads(accept.data)['all_accepted'] is True

    # Not startable until scheduled time.
    too_early = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert too_early.status_code == 400

    with app.app_context():
        lobby = db.session.get(RankedLobby, lobby_id)
        lobby.scheduled_for = utcnow_naive() - timedelta(minutes=1)
        db.session.commit()

    # Not startable until both players are checked in.
    start_without_checkins = client.post(
        f'/api/ranked/lobby/{lobby_id}/start',
        json={},
        headers=_auth(t1),
    )
    assert start_without_checkins.status_code == 400
    missing_ids = sorted(json.loads(start_without_checkins.data)['missing_player_ids'])
    assert missing_ids == sorted([id1, id2])

    assert _checkin(client, t1, court['id']).status_code == 201
    assert _checkin(client, t2, court['id']).status_code == 201

    start = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert start.status_code == 200
    start_data = json.loads(start.data)
    match_id = start_data['match']['id']
    assert start_data['lobby']['status'] == 'started'

    submit_score = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 9},
        headers=_auth(t1),
    )
    assert submit_score.status_code == 200

    final_confirm = client.post(
        f'/api/ranked/match/{match_id}/confirm',
        json={},
        headers=_auth(t2),
    )
    assert final_confirm.status_code == 200
    assert json.loads(final_confirm.data)['all_confirmed'] is True


def test_friends_scheduled_challenge_decline_removes_active_lobby(client):
    t1, id1 = _register(client, 'decline_friend_u1', 'decline_friend_u1@test.com')
    t2, id2 = _register(client, 'decline_friend_u2', 'decline_friend_u2@test.com')
    court = _create_court(client, t1, 'Decline Friends Challenge Court')

    _make_friends(
        client,
        requester_token=t1,
        recipient_token=t2,
        requester_id=id1,
        recipient_id=id2,
    )

    scheduled_for = (utcnow_naive() + timedelta(hours=2)).isoformat()
    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
        'source': 'friends_challenge',
    }, headers=_auth(t1))
    assert create.status_code == 201
    lobby_id = json.loads(create.data)['lobby']['id']

    decline = client.post(
        f'/api/ranked/lobby/{lobby_id}/respond',
        json={'action': 'decline'},
        headers=_auth(t2),
    )
    assert decline.status_code == 200
    decline_data = json.loads(decline.data)
    assert decline_data['all_accepted'] is False
    assert decline_data['lobby']['status'] == 'declined'

    pending_invitee = client.get('/api/ranked/challenges/pending', headers=_auth(t2))
    assert pending_invitee.status_code == 200
    assert lobby_id not in [l['id'] for l in json.loads(pending_invitee.data)['lobbies']]

    court_lobbies = client.get(f'/api/ranked/court/{court["id"]}/lobbies')
    assert court_lobbies.status_code == 200
    court_data = json.loads(court_lobbies.data)
    all_active_ids = (
        [l['id'] for l in court_data['ready_lobbies']]
        + [l['id'] for l in court_data['scheduled_lobbies']]
        + [l['id'] for l in court_data['pending_lobbies']]
    )
    assert lobby_id not in all_active_ids

    my_creator = client.get('/api/ranked/lobby/my-lobbies', headers=_auth(t1))
    assert my_creator.status_code == 200
    assert lobby_id not in [l['id'] for l in json.loads(my_creator.data)['lobbies']]

    creator_notifs = client.get('/api/auth/notifications', headers=_auth(t1))
    assert creator_notifs.status_code == 200
    creator_types = [n['notif_type'] for n in json.loads(creator_notifs.data)['notifications']]
    assert 'ranked_challenge_declined' in creator_types


def test_friends_scheduled_challenge_ready_notification_and_started_visibility(client, app):
    t1, id1 = _register(client, 'ready_friend_u1', 'ready_friend_u1@test.com')
    t2, id2 = _register(client, 'ready_friend_u2', 'ready_friend_u2@test.com')
    court = _create_court(client, t1, 'Ready Friends Challenge Court')

    _make_friends(
        client,
        requester_token=t1,
        recipient_token=t2,
        requester_id=id1,
        recipient_id=id2,
    )

    scheduled_for = (utcnow_naive() + timedelta(hours=2)).isoformat()
    create = client.post('/api/ranked/challenge/scheduled', json={
        'court_id': court['id'],
        'match_type': 'singles',
        'team1': [id1],
        'team2': [id2],
        'scheduled_for': scheduled_for,
        'source': 'friends_challenge',
    }, headers=_auth(t1))
    assert create.status_code == 201
    lobby_id = json.loads(create.data)['lobby']['id']

    accept = client.post(
        f'/api/ranked/lobby/{lobby_id}/respond',
        json={'action': 'accept'},
        headers=_auth(t2),
    )
    assert accept.status_code == 200
    accept_data = json.loads(accept.data)
    assert accept_data['all_accepted'] is True
    assert accept_data['lobby']['status'] == 'ready'

    creator_notifs = client.get('/api/auth/notifications', headers=_auth(t1))
    assert creator_notifs.status_code == 200
    creator_types = [n['notif_type'] for n in json.loads(creator_notifs.data)['notifications']]
    assert 'ranked_challenge_ready' in creator_types

    with app.app_context():
        lobby = db.session.get(RankedLobby, lobby_id)
        lobby.scheduled_for = utcnow_naive() - timedelta(minutes=1)
        db.session.commit()

    assert _checkin(client, t1, court['id']).status_code == 201
    assert _checkin(client, t2, court['id']).status_code == 201

    started = client.post(
        f'/api/ranked/lobby/{lobby_id}/start',
        json={},
        headers=_auth(t1),
    )
    assert started.status_code == 200
    started_data = json.loads(started.data)
    assert started_data['lobby']['status'] == 'started'

    my_creator = client.get('/api/ranked/lobby/my-lobbies', headers=_auth(t1))
    assert my_creator.status_code == 200
    creator_lobbies = json.loads(my_creator.data)['lobbies']
    creator_entry = next((l for l in creator_lobbies if l['id'] == lobby_id), None)
    assert creator_entry is not None
    assert creator_entry['status'] == 'started'

    my_invitee = client.get('/api/ranked/lobby/my-lobbies', headers=_auth(t2))
    assert my_invitee.status_code == 200
    invitee_lobbies = json.loads(my_invitee.data)['lobbies']
    invitee_entry = next((l for l in invitee_lobbies if l['id'] == lobby_id), None)
    assert invitee_entry is not None
    assert invitee_entry['status'] == 'started'


def test_queue_lobby_flow_end_to_end_with_confirmed_score(client):
    t1, id1 = _register(client, 'qflow_u1', 'qflow_u1@test.com')
    t2, id2 = _register(client, 'qflow_u2', 'qflow_u2@test.com')
    t3, id3 = _register(client, 'qflow_u3', 'qflow_u3@test.com')
    t4, id4 = _register(client, 'qflow_u4', 'qflow_u4@test.com')
    court = _create_court(client, t1, 'Queue Flow Court')

    for token in [t1, t2, t3, t4]:
        assert _checkin(client, token, court['id']).status_code == 201
        joined = client.post('/api/ranked/queue/join', json={
            'court_id': court['id'],
            'match_type': 'doubles',
        }, headers=_auth(token))
        assert joined.status_code == 201

    lobby_res = client.post('/api/ranked/lobby/queue', json={
        'court_id': court['id'],
        'match_type': 'doubles',
        'team1': [id1, id2],
        'team2': [id3, id4],
    }, headers=_auth(t1))
    assert lobby_res.status_code == 201
    lobby = json.loads(lobby_res.data)['lobby']
    assert lobby['status'] == 'ready'
    lobby_id = lobby['id']

    start = client.post(f'/api/ranked/lobby/{lobby_id}/start', json={}, headers=_auth(t1))
    assert start.status_code == 200
    match_id = json.loads(start.data)['match']['id']

    score = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 6},
        headers=_auth(t1),
    )
    assert score.status_code == 200
    assert json.loads(score.data)['pending_confirmation'] is True

    for token in [t2, t3]:
        confirm = client.post(f'/api/ranked/match/{match_id}/confirm', json={}, headers=_auth(token))
        assert confirm.status_code == 200
        assert json.loads(confirm.data)['all_confirmed'] is False

    final_confirm = client.post(f'/api/ranked/match/{match_id}/confirm', json={}, headers=_auth(t4))
    assert final_confirm.status_code == 200
    final_data = json.loads(final_confirm.data)
    assert final_data['all_confirmed'] is True
    assert final_data['match']['status'] == 'completed'

    leaderboard = client.get(f'/api/ranked/leaderboard?court_id={court["id"]}')
    assert leaderboard.status_code == 200
    board = json.loads(leaderboard.data)['leaderboard']
    player_ids = {row['user_id'] for row in board}
    assert {id1, id2, id3, id4}.issubset(player_ids)


def test_queue_lobby_can_auto_start_immediately(client):
    t1, id1 = _register(client, 'auto_start_u1', 'auto_start_u1@test.com')
    t2, id2 = _register(client, 'auto_start_u2', 'auto_start_u2@test.com')
    t3, id3 = _register(client, 'auto_start_u3', 'auto_start_u3@test.com')
    t4, id4 = _register(client, 'auto_start_u4', 'auto_start_u4@test.com')
    court = _create_court(client, t1, 'Auto Start Court')

    for token in [t1, t2, t3, t4]:
        assert _checkin(client, token, court['id']).status_code == 201
        joined = client.post('/api/ranked/queue/join', json={
            'court_id': court['id'],
            'match_type': 'doubles',
        }, headers=_auth(token))
        assert joined.status_code == 201

    create_and_start = client.post('/api/ranked/lobby/queue', json={
        'court_id': court['id'],
        'match_type': 'doubles',
        'team1': [id1, id2],
        'team2': [id3, id4],
        'start_immediately': True,
    }, headers=_auth(t1))
    assert create_and_start.status_code == 201
    data = json.loads(create_and_start.data)
    assert data['lobby']['status'] == 'started'
    assert data['match']['status'] == 'in_progress'
    assert data['lobby']['started_match_id'] == data['match']['id']


def test_leaderboard_and_history_filter_by_county(client, app):
    t1, id1 = _register(client, 'county_lb_u1', 'county_lb_u1@test.com')
    _, id2 = _register(client, 'county_lb_u2', 'county_lb_u2@test.com')
    _, id3 = _register(client, 'county_lb_u3', 'county_lb_u3@test.com')

    humboldt_court = _create_court(
        client,
        t1,
        'County Humboldt Court',
        county_slug='humboldt',
    )
    alameda_court = _create_court(
        client,
        t1,
        'County Alameda Court',
        county_slug='alameda',
    )

    with app.app_context():
        humboldt_match = Match(
            court_id=humboldt_court['id'],
            match_type='singles',
            status='completed',
            team1_score=11,
            team2_score=8,
            winner_team=1,
            submitted_by=id1,
            completed_at=utcnow_naive(),
        )
        alameda_match = Match(
            court_id=alameda_court['id'],
            match_type='singles',
            status='completed',
            team1_score=11,
            team2_score=9,
            winner_team=1,
            submitted_by=id2,
            completed_at=utcnow_naive(),
        )
        db.session.add_all([humboldt_match, alameda_match])
        db.session.flush()
        humboldt_match_id = humboldt_match.id
        alameda_match_id = alameda_match.id

        db.session.add_all([
            MatchPlayer(
                match_id=humboldt_match_id,
                user_id=id1,
                team=1,
                confirmed=True,
            ),
            MatchPlayer(
                match_id=humboldt_match_id,
                user_id=id3,
                team=2,
                confirmed=True,
            ),
            MatchPlayer(
                match_id=alameda_match_id,
                user_id=id2,
                team=1,
                confirmed=True,
            ),
            MatchPlayer(
                match_id=alameda_match_id,
                user_id=id3,
                team=2,
                confirmed=True,
            ),
        ])

        user1 = db.session.get(User, id1)
        user2 = db.session.get(User, id2)
        user3 = db.session.get(User, id3)
        user1.games_played, user1.wins, user1.losses = 1, 1, 0
        user2.games_played, user2.wins, user2.losses = 1, 1, 0
        user3.games_played, user3.wins, user3.losses = 2, 0, 2
        db.session.commit()

    humboldt_lb = client.get('/api/ranked/leaderboard?county_slug=humboldt')
    assert humboldt_lb.status_code == 200
    humboldt_ids = {row['user_id'] for row in json.loads(humboldt_lb.data)['leaderboard']}
    assert id1 in humboldt_ids
    assert id3 in humboldt_ids
    assert id2 not in humboldt_ids

    alameda_lb = client.get('/api/ranked/leaderboard?county_slug=alameda')
    assert alameda_lb.status_code == 200
    alameda_ids = {row['user_id'] for row in json.loads(alameda_lb.data)['leaderboard']}
    assert id2 in alameda_ids
    assert id3 in alameda_ids
    assert id1 not in alameda_ids

    humboldt_history = client.get('/api/ranked/history?county_slug=humboldt')
    assert humboldt_history.status_code == 200
    humboldt_match_ids = {m['id'] for m in json.loads(humboldt_history.data)['matches']}
    assert humboldt_match_id in humboldt_match_ids
    assert alameda_match_id not in humboldt_match_ids

    alameda_history = client.get('/api/ranked/history?county_slug=alameda')
    assert alameda_history.status_code == 200
    alameda_match_ids = {m['id'] for m in json.loads(alameda_history.data)['matches']}
    assert alameda_match_id in alameda_match_ids
    assert humboldt_match_id not in alameda_match_ids


def test_reject_score_notifies_only_original_submitter(client):
    t1, id1 = _register(client, 'reject_notif_u1', 'reject_notif_u1@test.com')
    t2, id2 = _register(client, 'reject_notif_u2', 'reject_notif_u2@test.com')
    t3, id3 = _register(client, 'reject_notif_u3', 'reject_notif_u3@test.com')
    t4, id4 = _register(client, 'reject_notif_u4', 'reject_notif_u4@test.com')
    court = _create_court(client, t1, 'Reject Notify Court')

    match_res = _create_match(
        client,
        t1,
        court['id'],
        team1=[id1, id2],
        team2=[id3, id4],
    )
    assert match_res.status_code == 201
    match_id = json.loads(match_res.data)['match']['id']

    submit = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 8},
        headers=_auth(t1),
    )
    assert submit.status_code == 200

    reject = client.post(f'/api/ranked/match/{match_id}/reject', json={}, headers=_auth(t2))
    assert reject.status_code == 200

    submitter_notifs = client.get('/api/auth/notifications', headers=_auth(t1))
    submitter_types = [n['notif_type'] for n in json.loads(submitter_notifs.data)['notifications']]
    assert 'match_rejected' in submitter_types

    teammate_notifs = client.get('/api/auth/notifications', headers=_auth(t3))
    teammate_types = [n['notif_type'] for n in json.loads(teammate_notifs.data)['notifications']]
    assert 'match_rejected' not in teammate_types


def test_non_player_cannot_submit_match_score(client):
    creator_token, id1 = _register(client, 'ranked_score_u1', 'ranked_score_u1@test.com')
    _, id2 = _register(client, 'ranked_score_u2', 'ranked_score_u2@test.com')
    _, id3 = _register(client, 'ranked_score_u3', 'ranked_score_u3@test.com')
    _, id4 = _register(client, 'ranked_score_u4', 'ranked_score_u4@test.com')
    outsider_token, _ = _register(client, 'ranked_outsider', 'ranked_outsider@test.com')

    court = _create_court(client, creator_token, 'Ranked Score Court')
    match_res = _create_match(
        client,
        creator_token,
        court['id'],
        team1=[id1, id2],
        team2=[id3, id4],
    )
    assert match_res.status_code == 201
    match_id = json.loads(match_res.data)['match']['id']

    score = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 4},
        headers=_auth(outsider_token),
    )
    assert score.status_code == 403


def test_cancel_match(client):
    """A player in a match can cancel it; outsiders cannot."""
    t1, id1 = _register(client, 'cancel_u1', 'cancel_u1@test.com')
    t2, id2 = _register(client, 'cancel_u2', 'cancel_u2@test.com')
    _, id3 = _register(client, 'cancel_u3', 'cancel_u3@test.com')
    _, id4 = _register(client, 'cancel_u4', 'cancel_u4@test.com')
    outsider_token, _ = _register(client, 'cancel_outsider', 'cancel_outsider@test.com')

    court = _create_court(client, t1, 'Cancel Court')
    match_res = _create_match(client, t1, court['id'], [id1, id2], [id3, id4])
    assert match_res.status_code == 201
    match_id = json.loads(match_res.data)['match']['id']

    # Outsider cannot cancel
    res = client.post(f'/api/ranked/match/{match_id}/cancel', headers=_auth(outsider_token))
    assert res.status_code == 403

    # Player in the match can cancel
    res = client.post(f'/api/ranked/match/{match_id}/cancel', headers=_auth(t1))
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['match']['status'] == 'cancelled'

    # Cannot cancel an already-cancelled match
    res = client.post(f'/api/ranked/match/{match_id}/cancel', headers=_auth(t2))
    assert res.status_code == 400

    # Other player got a notification
    notifs = client.get('/api/auth/notifications', headers=_auth(t2))
    types = [n['notif_type'] for n in json.loads(notifs.data)['notifications']]
    assert 'match_cancelled' in types


def test_stale_lobby_expires_during_write(client, app):
    """Stale lobbies are marked expired when a write operation runs."""
    t1, id1 = _register(client, 'stale_u1', 'stale_u1@test.com')
    _, id2 = _register(client, 'stale_u2', 'stale_u2@test.com')
    court = _create_court(client, t1, 'Stale Court')
    _checkin(client, t1, court['id'])

    # Create a lobby directly and backdate it
    with app.app_context():
        lobby = RankedLobby(
            court_id=court['id'], created_by_id=id1,
            match_type='singles', source='court_challenge',
            status='pending_acceptance',
            created_at=utcnow_naive() - timedelta(hours=49),
        )
        db.session.add(lobby)
        db.session.commit()
        lobby_id = lobby.id

    # Joining the queue triggers stale cleanup
    client.post('/api/ranked/queue/join', json={
        'court_id': court['id'], 'match_type': 'doubles',
    }, headers=_auth(t1))

    with app.app_context():
        updated = db.session.get(RankedLobby, lobby_id)
        assert updated.status == 'expired'


def test_stale_match_expires_during_write(client, app):
    """Stale in-progress matches are cancelled when a write operation runs."""
    t1, id1 = _register(client, 'stale_match_u1', 'stale_match_u1@test.com')
    _, id2 = _register(client, 'stale_match_u2', 'stale_match_u2@test.com')
    _, id3 = _register(client, 'stale_match_u3', 'stale_match_u3@test.com')
    _, id4 = _register(client, 'stale_match_u4', 'stale_match_u4@test.com')
    court = _create_court(client, t1, 'Stale Match Court')
    _checkin(client, t1, court['id'])

    match_res = _create_match(client, t1, court['id'], [id1, id2], [id3, id4])
    match_id = json.loads(match_res.data)['match']['id']

    # Backdate the match to simulate staleness
    with app.app_context():
        m = db.session.get(Match, match_id)
        m.created_at = utcnow_naive() - timedelta(hours=25)
        db.session.commit()

    # Joining the queue triggers stale cleanup
    client.post('/api/ranked/queue/join', json={
        'court_id': court['id'], 'match_type': 'doubles',
    }, headers=_auth(t1))

    with app.app_context():
        m = db.session.get(Match, match_id)
        assert m.status == 'cancelled'


def test_court_summary_endpoint(client):
    """The consolidated court summary returns queue, matches, lobbies, leaderboard."""
    t1, id1 = _register(client, 'summary_u1', 'summary_u1@test.com')
    _, id2 = _register(client, 'summary_u2', 'summary_u2@test.com')
    _, id3 = _register(client, 'summary_u3', 'summary_u3@test.com')
    _, id4 = _register(client, 'summary_u4', 'summary_u4@test.com')
    court = _create_court(client, t1, 'Summary Court')

    _checkin(client, t1, court['id'])

    # Create a match so we have active data
    match_res = _create_match(client, t1, court['id'], [id1, id2], [id3, id4])
    assert match_res.status_code == 201

    res = client.get(f'/api/ranked/court/{court["id"]}/summary')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'queue' in data
    assert 'matches' in data
    assert 'ready_lobbies' in data
    assert 'scheduled_lobbies' in data
    assert 'pending_lobbies' in data
    assert 'leaderboard' in data
    assert len(data['matches']) >= 1


def test_reconfirm_completed_match_does_not_change_elo(client, app):
    t1, id1 = _register(client, 'reconfirm_u1', 'reconfirm_u1@test.com')
    t2, id2 = _register(client, 'reconfirm_u2', 'reconfirm_u2@test.com')
    court = _create_court(client, t1, 'Reconfirm ELO Court')

    match_res = _create_match(
        client,
        t1,
        court['id'],
        team1=[id1],
        team2=[id2],
        match_type='singles',
    )
    assert match_res.status_code == 201
    match_id = json.loads(match_res.data)['match']['id']

    submit = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 8},
        headers=_auth(t1),
    )
    assert submit.status_code == 200
    assert json.loads(submit.data)['pending_confirmation'] is True

    finalize = client.post(
        f'/api/ranked/match/{match_id}/confirm',
        json={},
        headers=_auth(t2),
    )
    assert finalize.status_code == 200
    assert json.loads(finalize.data)['all_confirmed'] is True

    with app.app_context():
        user1 = db.session.get(User, id1)
        user2 = db.session.get(User, id2)
        finalized_state = (
            user1.elo_rating, user1.games_played, user1.wins, user1.losses,
            user2.elo_rating, user2.games_played, user2.wins, user2.losses,
        )

    reconfirm = client.post(
        f'/api/ranked/match/{match_id}/confirm',
        json={},
        headers=_auth(t1),
    )
    assert reconfirm.status_code == 400

    with app.app_context():
        user1 = db.session.get(User, id1)
        user2 = db.session.get(User, id2)
        current_state = (
            user1.elo_rating, user1.games_played, user1.wins, user1.losses,
            user2.elo_rating, user2.games_played, user2.wins, user2.losses,
        )
        assert current_state == finalized_state
