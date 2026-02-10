"""Tests for ranked competitive play routes."""
import json


def _register(client, username, email):
    res = client.post('/api/auth/register', json={
        'username': username,
        'email': email,
        'password': 'password123',
    })
    data = json.loads(res.data)
    return data['token'], data['user']['id']


def _create_court(client, token, name='Ranked Court'):
    res = client.post('/api/courts', json={
        'name': name,
        'latitude': 40.80,
        'longitude': -124.16,
        'city': 'Eureka',
    }, headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['court']


def _create_match(client, token, court_id, team1, team2, match_type='doubles'):
    res = client.post('/api/ranked/match', json={
        'court_id': court_id,
        'match_type': match_type,
        'team1': team1,
        'team2': team2,
    }, headers={'Authorization': f'Bearer {token}'})
    return res


def test_create_match_requires_existing_court(client):
    t1, id1 = _register(client, 'ranked_u1', 'ranked_u1@test.com')
    _, id2 = _register(client, 'ranked_u2', 'ranked_u2@test.com')
    _, id3 = _register(client, 'ranked_u3', 'ranked_u3@test.com')
    _, id4 = _register(client, 'ranked_u4', 'ranked_u4@test.com')

    res = _create_match(
        client,
        t1,
        999999,
        team1=[id1, id2],
        team2=[id3, id4],
    )
    assert res.status_code == 404


def test_non_player_cannot_submit_match_score(client):
    creator_token, id1 = _register(client, 'ranked_score_u1', 'ranked_score_u1@test.com')
    player2_token, id2 = _register(client, 'ranked_score_u2', 'ranked_score_u2@test.com')
    player3_token, id3 = _register(client, 'ranked_score_u3', 'ranked_score_u3@test.com')
    player4_token, id4 = _register(client, 'ranked_score_u4', 'ranked_score_u4@test.com')
    outsider_token, _ = _register(client, 'ranked_outsider', 'ranked_outsider@test.com')

    _ = player2_token
    _ = player3_token
    _ = player4_token

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
        headers={'Authorization': f'Bearer {outsider_token}'},
    )
    assert score.status_code == 403


def test_reject_negative_scores(client):
    creator_token, id1 = _register(client, 'ranked_neg_u1', 'ranked_neg_u1@test.com')
    _, id2 = _register(client, 'ranked_neg_u2', 'ranked_neg_u2@test.com')
    _, id3 = _register(client, 'ranked_neg_u3', 'ranked_neg_u3@test.com')
    _, id4 = _register(client, 'ranked_neg_u4', 'ranked_neg_u4@test.com')

    court = _create_court(client, creator_token, 'Ranked Negative Score Court')
    match_res = _create_match(
        client,
        creator_token,
        court['id'],
        team1=[id1, id2],
        team2=[id3, id4],
    )
    assert match_res.status_code == 201
    match_id = json.loads(match_res.data)['match']['id']

    negative_score = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': -1, 'team2_score': 11},
        headers={'Authorization': f'Bearer {creator_token}'},
    )
    assert negative_score.status_code == 400
