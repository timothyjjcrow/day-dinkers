"""Tests for ranked tournaments lifecycle and integrations."""
import json
from datetime import timedelta
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


def _create_court(client, token, name='Tournament Court'):
    res = client.post('/api/courts', json={
        'name': name,
        'latitude': 40.80,
        'longitude': -124.16,
        'city': 'Eureka',
    }, headers=_auth(token))
    return json.loads(res.data)['court']


def _complete_singles_match(client, submitter_token, confirmer_token, match_id):
    scored = client.post(
        f'/api/ranked/match/{match_id}/score',
        json={'team1_score': 11, 'team2_score': 6},
        headers=_auth(submitter_token),
    )
    assert scored.status_code == 200
    confirm = client.post(
        f'/api/ranked/match/{match_id}/confirm',
        json={},
        headers=_auth(confirmer_token),
    )
    assert confirm.status_code == 200


def test_open_tournament_single_elim_lifecycle(client):
    host_token, host_id = _register(client, 'tour_host', 'tour_host@test.com')
    p2_token, p2_id = _register(client, 'tour_p2', 'tour_p2@test.com')
    p3_token, p3_id = _register(client, 'tour_p3', 'tour_p3@test.com')
    p4_token, p4_id = _register(client, 'tour_p4', 'tour_p4@test.com')
    tokens_by_user_id = {
        host_id: host_token,
        p2_id: p2_token,
        p3_id: p3_token,
        p4_id: p4_token,
    }

    court = _create_court(client, host_token, 'Open Tournament Court')
    start_time = (utcnow_naive() + timedelta(hours=2)).isoformat()
    create = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Sunday Ladder Cup',
        'start_time': start_time,
        'tournament_format': 'single_elimination',
        'access_mode': 'open',
        'match_type': 'singles',
        'max_players': 8,
        'min_participants': 4,
        'check_in_required': True,
        'no_show_policy': 'auto_forfeit',
        'affects_elo': True,
    }, headers=_auth(host_token))
    assert create.status_code == 201
    tournament_id = json.loads(create.data)['tournament']['id']

    for token in [p2_token, p3_token, p4_token]:
        joined = client.post(
            f'/api/ranked/tournaments/{tournament_id}/join',
            json={},
            headers=_auth(token),
        )
        assert joined.status_code == 200

    # Check in at court and check in for tournament.
    for token in [host_token, p2_token, p3_token, p4_token]:
        presence = client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=_auth(token))
        assert presence.status_code == 201
        tournament_checkin = client.post(
            f'/api/ranked/tournaments/{tournament_id}/check-in',
            json={},
            headers=_auth(token),
        )
        assert tournament_checkin.status_code == 200

    started = client.post(
        f'/api/ranked/tournaments/{tournament_id}/start',
        json={},
        headers=_auth(host_token),
    )
    assert started.status_code == 200
    started_data = json.loads(started.data)['tournament']
    assert started_data['status'] == 'live'
    assert started_data['bracket_size'] == 4
    assert started_data['total_rounds'] == 2
    assert started_data['bracket']['total_matches'] == 2

    # Complete round 1
    detail = client.get(f'/api/ranked/tournaments/{tournament_id}')
    tournament = json.loads(detail.data)['tournament']
    round_one_matches = tournament['bracket']['rounds'][0]['matches']
    assert len(round_one_matches) == 2
    for match in round_one_matches:
        team1_user_id = match['team1'][0]['user_id']
        team2_user_id = match['team2'][0]['user_id']
        _complete_singles_match(
            client,
            submitter_token=tokens_by_user_id[team1_user_id],
            confirmer_token=tokens_by_user_id[team2_user_id],
            match_id=match['id'],
        )

    # Final should be created after semis complete.
    detail = client.get(f'/api/ranked/tournaments/{tournament_id}')
    tournament = json.loads(detail.data)['tournament']
    rounds = tournament['bracket']['rounds']
    assert len(rounds) == 2
    final_match = rounds[1]['matches'][0]
    final_team1_id = final_match['team1'][0]['user_id']
    final_team2_id = final_match['team2'][0]['user_id']
    _complete_singles_match(
        client,
        submitter_token=tokens_by_user_id[final_team1_id],
        confirmer_token=tokens_by_user_id[final_team2_id],
        match_id=final_match['id'],
    )

    done = client.get(f'/api/ranked/tournaments/{tournament_id}')
    done_data = json.loads(done.data)['tournament']
    assert done_data['status'] == 'completed'
    assert len(done_data['results']) >= 4

    leaderboard = client.get(f'/api/ranked/tournaments/leaderboard?court_id={court["id"]}')
    assert leaderboard.status_code == 200
    lb_rows = json.loads(leaderboard.data)['leaderboard']
    assert len(lb_rows) >= 4
    assert lb_rows[0]['points'] >= lb_rows[1]['points']


def test_invite_only_tournament_requires_invite_or_response(client):
    host_token, host_id = _register(client, 'invite_host_t', 'invite_host_t@test.com')
    invited_token, invited_id = _register(client, 'invite_user_t', 'invite_user_t@test.com')
    outsider_token, _ = _register(client, 'invite_outsider_t', 'invite_outsider_t@test.com')
    court = _create_court(client, host_token, 'Invite Tournament Court')

    start_time = (utcnow_naive() + timedelta(hours=3)).isoformat()
    created = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Invite Cup',
        'start_time': start_time,
        'access_mode': 'invite_only',
        'match_type': 'singles',
        'min_participants': 2,
        'max_players': 8,
        'invite_user_ids': [invited_id],
    }, headers=_auth(host_token))
    assert created.status_code == 201
    tournament_id = json.loads(created.data)['tournament']['id']

    blocked = client.post(
        f'/api/ranked/tournaments/{tournament_id}/join',
        json={},
        headers=_auth(outsider_token),
    )
    assert blocked.status_code == 403

    accepted = client.post(
        f'/api/ranked/tournaments/{tournament_id}/respond',
        json={'action': 'accept'},
        headers=_auth(invited_token),
    )
    assert accepted.status_code == 200
    payload = json.loads(accepted.data)['tournament']
    participant_ids = [p['user_id'] for p in payload['participants']]
    assert host_id in participant_ids
    assert invited_id in participant_ids


def test_host_mark_no_show_policy_requires_host_action(client):
    host_token, _ = _register(client, 'noshow_host', 'noshow_host@test.com')
    p2_token, _ = _register(client, 'noshow_p2', 'noshow_p2@test.com')
    p3_token, _ = _register(client, 'noshow_p3', 'noshow_p3@test.com')
    p4_token, _ = _register(client, 'noshow_p4', 'noshow_p4@test.com')
    court = _create_court(client, host_token, 'No Show Policy Court')

    start_time = (utcnow_naive() + timedelta(hours=2)).isoformat()
    created = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Host Mark No Show',
        'start_time': start_time,
        'access_mode': 'open',
        'match_type': 'singles',
        'min_participants': 4,
        'max_players': 8,
        'check_in_required': True,
        'no_show_policy': 'host_mark',
    }, headers=_auth(host_token))
    assert created.status_code == 201
    tournament_id = json.loads(created.data)['tournament']['id']

    for token in [p2_token, p3_token, p4_token]:
        joined = client.post(f'/api/ranked/tournaments/{tournament_id}/join', json={}, headers=_auth(token))
        assert joined.status_code == 200

    # Check in everyone except p4.
    for token in [host_token, p2_token, p3_token]:
        assert client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=_auth(token)).status_code == 201
        assert client.post(f'/api/ranked/tournaments/{tournament_id}/check-in', json={}, headers=_auth(token)).status_code == 200

    blocked_start = client.post(
        f'/api/ranked/tournaments/{tournament_id}/start',
        json={},
        headers=_auth(host_token),
    )
    assert blocked_start.status_code == 400
    blocked_data = json.loads(blocked_start.data)
    assert 'missing_player_ids' in blocked_data
    assert len(blocked_data['missing_player_ids']) >= 1


def test_withdraw_upcoming_tournament_and_rejoin(client):
    host_token, _ = _register(client, 'withdraw_host', 'withdraw_host@test.com')
    player_token, player_id = _register(client, 'withdraw_player', 'withdraw_player@test.com')
    court = _create_court(client, host_token, 'Withdraw Tournament Court')
    start_time = (utcnow_naive() + timedelta(hours=2)).isoformat()

    created = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Withdraw Flow Cup',
        'start_time': start_time,
        'access_mode': 'open',
        'match_type': 'singles',
        'min_participants': 2,
        'max_players': 8,
    }, headers=_auth(host_token))
    assert created.status_code == 201
    tournament_id = json.loads(created.data)['tournament']['id']

    joined = client.post(
        f'/api/ranked/tournaments/{tournament_id}/join',
        json={},
        headers=_auth(player_token),
    )
    assert joined.status_code == 200

    withdrawn = client.post(
        f'/api/ranked/tournaments/{tournament_id}/withdraw',
        json={},
        headers=_auth(player_token),
    )
    assert withdrawn.status_code == 200
    withdrawn_participant = next(
        p for p in json.loads(withdrawn.data)['tournament']['participants']
        if p['user_id'] == player_id
    )
    assert withdrawn_participant['participant_status'] == 'withdrawn'

    rejoin = client.post(
        f'/api/ranked/tournaments/{tournament_id}/join',
        json={},
        headers=_auth(player_token),
    )
    assert rejoin.status_code == 200
    rejoin_participant = next(
        p for p in json.loads(rejoin.data)['tournament']['participants']
        if p['user_id'] == player_id
    )
    assert rejoin_participant['participant_status'] == 'registered'
    assert rejoin_participant['invite_status'] == 'accepted'


def test_invite_only_decline_requires_reinvite_before_join(client):
    host_token, _ = _register(client, 'phase2_invite_host', 'phase2_invite_host@test.com')
    invited_token, invited_id = _register(client, 'phase2_invited', 'phase2_invited@test.com')
    court = _create_court(client, host_token, 'Invite Transition Court')
    start_time = (utcnow_naive() + timedelta(hours=2)).isoformat()

    created = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Invite Transition Cup',
        'start_time': start_time,
        'access_mode': 'invite_only',
        'match_type': 'singles',
        'min_participants': 2,
        'max_players': 8,
        'invite_user_ids': [invited_id],
    }, headers=_auth(host_token))
    assert created.status_code == 201
    tournament_id = json.loads(created.data)['tournament']['id']

    declined = client.post(
        f'/api/ranked/tournaments/{tournament_id}/respond',
        json={'action': 'decline'},
        headers=_auth(invited_token),
    )
    assert declined.status_code == 200

    blocked_join = client.post(
        f'/api/ranked/tournaments/{tournament_id}/join',
        json={},
        headers=_auth(invited_token),
    )
    assert blocked_join.status_code == 403

    reinvite = client.post(
        f'/api/ranked/tournaments/{tournament_id}/invite',
        json={'user_ids': [invited_id]},
        headers=_auth(host_token),
    )
    assert reinvite.status_code == 200

    joined_after_reinvite = client.post(
        f'/api/ranked/tournaments/{tournament_id}/join',
        json={},
        headers=_auth(invited_token),
    )
    assert joined_after_reinvite.status_code == 200
    participant = next(
        p for p in json.loads(joined_after_reinvite.data)['tournament']['participants']
        if p['user_id'] == invited_id
    )
    assert participant['participant_status'] == 'registered'


def test_cancel_live_tournament_mid_bracket_cancels_active_matches(client):
    host_token, host_id = _register(client, 'cancel_mid_host', 'cancel_mid_host@test.com')
    p2_token, p2_id = _register(client, 'cancel_mid_p2', 'cancel_mid_p2@test.com')
    p3_token, p3_id = _register(client, 'cancel_mid_p3', 'cancel_mid_p3@test.com')
    p4_token, p4_id = _register(client, 'cancel_mid_p4', 'cancel_mid_p4@test.com')
    tokens_by_user_id = {
        host_id: host_token,
        p2_id: p2_token,
        p3_id: p3_token,
        p4_id: p4_token,
    }
    court = _create_court(client, host_token, 'Cancel Mid Bracket Court')
    start_time = (utcnow_naive() + timedelta(hours=2)).isoformat()

    created = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Cancel Mid Bracket Cup',
        'start_time': start_time,
        'access_mode': 'open',
        'match_type': 'singles',
        'min_participants': 4,
        'max_players': 8,
        'check_in_required': False,
    }, headers=_auth(host_token))
    assert created.status_code == 201
    tournament_id = json.loads(created.data)['tournament']['id']

    for token in [p2_token, p3_token, p4_token]:
        joined = client.post(f'/api/ranked/tournaments/{tournament_id}/join', json={}, headers=_auth(token))
        assert joined.status_code == 200

    started = client.post(f'/api/ranked/tournaments/{tournament_id}/start', json={}, headers=_auth(host_token))
    assert started.status_code == 200

    detail = client.get(f'/api/ranked/tournaments/{tournament_id}')
    round_one = json.loads(detail.data)['tournament']['bracket']['rounds'][0]['matches']
    first_match = round_one[0]
    submitter_id = first_match['team1'][0]['user_id']
    scored = client.post(
        f"/api/ranked/match/{first_match['id']}/score",
        json={'team1_score': 11, 'team2_score': 8},
        headers=_auth(tokens_by_user_id[submitter_id]),
    )
    assert scored.status_code == 200

    cancelled = client.post(f'/api/ranked/tournaments/{tournament_id}/cancel', json={}, headers=_auth(host_token))
    assert cancelled.status_code == 200
    payload = json.loads(cancelled.data)['tournament']
    assert payload['status'] == 'cancelled'

    detail_after = client.get(f'/api/ranked/tournaments/{tournament_id}')
    rounds = json.loads(detail_after.data)['tournament']['bracket']['rounds']
    statuses = [
        m['status']
        for rnd in rounds
        for m in (rnd.get('matches') or [])
    ]
    assert 'cancelled' in statuses
    assert not any(status in {'in_progress', 'pending_confirmation'} for status in statuses)


def test_auto_forfeit_waits_for_grace_then_starts(client, app):
    from backend.app import db
    from backend.models import Tournament

    host_token, host_id = _register(client, 'autof_host', 'autof_host@test.com')
    p2_token, p2_id = _register(client, 'autof_p2', 'autof_p2@test.com')
    p3_token, p3_id = _register(client, 'autof_p3', 'autof_p3@test.com')
    p4_token, p4_id = _register(client, 'autof_p4', 'autof_p4@test.com')

    court = _create_court(client, host_token, 'Auto Forfeit Grace Court')
    start_time = (utcnow_naive() + timedelta(hours=2)).isoformat()
    created = client.post('/api/ranked/tournaments', json={
        'court_id': court['id'],
        'name': 'Auto Forfeit Grace Cup',
        'start_time': start_time,
        'access_mode': 'open',
        'match_type': 'singles',
        'min_participants': 2,
        'max_players': 8,
        'check_in_required': True,
        'no_show_policy': 'auto_forfeit',
        'no_show_grace_minutes': 30,
    }, headers=_auth(host_token))
    assert created.status_code == 201
    tournament_id = json.loads(created.data)['tournament']['id']

    for token in [p2_token, p3_token, p4_token]:
        joined = client.post(f'/api/ranked/tournaments/{tournament_id}/join', json={}, headers=_auth(token))
        assert joined.status_code == 200

    # Check in only two players (host + p2), leave p3/p4 unchecked in.
    for token in [host_token, p2_token]:
        assert client.post('/api/presence/checkin', json={'court_id': court['id']}, headers=_auth(token)).status_code == 201
        assert client.post(f'/api/ranked/tournaments/{tournament_id}/check-in', json={}, headers=_auth(token)).status_code == 200

    blocked = client.post(f'/api/ranked/tournaments/{tournament_id}/start', json={}, headers=_auth(host_token))
    assert blocked.status_code == 400
    blocked_data = json.loads(blocked.data)
    assert blocked_data.get('error') == 'Waiting for player check-ins before no-show auto-forfeit'
    assert sorted(blocked_data.get('missing_player_ids', [])) == sorted([p3_id, p4_id])

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        tournament.start_time = utcnow_naive() - timedelta(hours=2)
        db.session.commit()

    started = client.post(f'/api/ranked/tournaments/{tournament_id}/start', json={}, headers=_auth(host_token))
    assert started.status_code == 200
    started_payload = json.loads(started.data)['tournament']
    assert started_payload['status'] == 'live'
    assert started_payload['bracket_size'] == 2

    detail = client.get(f'/api/ranked/tournaments/{tournament_id}')
    participants = json.loads(detail.data)['tournament']['participants']
    status_by_user = {row['user_id']: row['participant_status'] for row in participants}
    assert status_by_user[host_id] in {'checked_in', 'registered'}
    assert status_by_user[p2_id] in {'checked_in', 'registered'}
    assert status_by_user[p3_id] == 'no_show'
    assert status_by_user[p4_id] == 'no_show'
