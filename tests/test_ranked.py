"""Tests for ranked queue/challenge/scheduled flow and score confirmation."""
import json
from datetime import timedelta

from backend.app import db
from backend.models import RankedLobby
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


def _create_court(client, token, name='Ranked Court'):
    res = client.post('/api/courts', json={
        'name': name,
        'latitude': 40.80,
        'longitude': -124.16,
        'city': 'Eureka',
    }, headers=_auth(token))
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
    lobby_id = json.loads(create.data)['lobby']['id']

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
    assert 'cannot start yet' in json.loads(too_early.data)['error']

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
