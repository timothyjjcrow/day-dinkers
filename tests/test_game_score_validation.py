"""Focused API regressions for pickleball score and team validation."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court, Friendship, Game, GamePlayer, GameScoreLine, Notification, User,
    utcnow,
)


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Score Validation Court',
            city='Costa Mesa',
            state='CA',
            county_slug='orange-county',
            latitude=33.66,
            longitude=-117.91,
            num_courts=4,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug, name):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(player):
    return {'Authorization': f"Bearer {player['token']}"}


def court_id():
    return Court.query.filter_by(name='Score Validation Court').one().id


def befriend(player1, player2):
    db.session.add(Friendship(
        requester_id=player1['user']['id'],
        addressee_id=player2['user']['id'],
        status='accepted',
    ))
    db.session.commit()


def create_rostered_game(
    client, host, court, players, *, ranked=False, max_players=4,
):
    response = client.post('/api/games', json={
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(minutes=1)).isoformat() + 'Z',
        'game_type': 'ranked' if ranked else 'casual',
        'visibility': 'open',
        'max_players': max_players,
    }, headers=headers(host))
    assert response.status_code == 201, response.get_json()
    game = response.get_json()
    for player in players:
        joined = client.post(
            f"/api/games/{game['id']}/join", headers=headers(player),
        )
        assert joined.status_code == 200, joined.get_json()
    return game


def score_payload(team1, team2, score1=11, score2=7, **extra):
    return {
        'team1': team1,
        'team2': team2,
        'score_team1': score1,
        'score_team2': score2,
        **extra,
    }


def test_standard_11_15_21_win_by_two_scores_work_on_both_endpoints(client):
    ana = register(client, 'standard-ana', 'Ana')
    ben = register(client, 'standard-ben', 'Ben')
    befriend(ana, ben)
    court = court_id()
    team1 = [ana['user']['id']]
    team2 = [ben['user']['id']]

    for score1, score2 in (
        (11, 9), (12, 10), (15, 13),
        (16, 14), (21, 19), (22, 20),
    ):
        logged = client.post('/api/games/log', json={
            'court_id': court,
            **score_payload(team1, team2, score1, score2),
        }, headers=headers(ana))
        assert logged.status_code == 201, logged.get_json()

        game = create_rostered_game(client, ana, court, [ben])
        completed = client.post(
            f"/api/games/{game['id']}/complete",
            json=score_payload(team1, team2, score1, score2),
            headers=headers(ana),
        )
        assert completed.status_code == 200, completed.get_json()
        assert completed.get_json()['status'] == 'completed'


def test_nonstandard_score_requires_exact_true_acknowledgement(client):
    ana = register(client, 'nonstandard-ana', 'Ana')
    ben = register(client, 'nonstandard-ben', 'Ben')
    befriend(ana, ben)
    court = court_id()
    team1 = [ana['user']['id']]
    team2 = [ben['user']['id']]
    nonstandard = score_payload(team1, team2, 11, 10)
    expected = {
        'error': 'nonstandard_pickleball_score',
        'can_confirm': True,
    }

    for acknowledgement in (None, 1):
        payload = {'court_id': court, **nonstandard}
        if acknowledgement is not None:
            payload['accept_nonstandard_score'] = acknowledgement
        response = client.post(
            '/api/games/log', json=payload, headers=headers(ana),
        )
        assert response.status_code == 422
        assert response.get_json() == expected
    accepted_log = client.post('/api/games/log', json={
        'court_id': court,
        **nonstandard,
        'accept_nonstandard_score': True,
    }, headers=headers(ana))
    assert accepted_log.status_code == 201, accepted_log.get_json()

    game = create_rostered_game(client, ana, court, [ben])
    for acknowledgement in (None, 1):
        payload = dict(nonstandard)
        if acknowledgement is not None:
            payload['accept_nonstandard_score'] = acknowledgement
        response = client.post(
            f"/api/games/{game['id']}/complete",
            json=payload,
            headers=headers(ana),
        )
        assert response.status_code == 422
        assert response.get_json() == expected
    accepted_score = client.post(
        f"/api/games/{game['id']}/complete",
        json={**nonstandard, 'accept_nonstandard_score': True},
        headers=headers(ana),
    )
    assert accepted_score.status_code == 200, accepted_score.get_json()


def test_multi_game_match_is_persisted_and_server_derives_match_winner(client):
    ana = register(client, 'series-ana', 'Ana')
    ben = register(client, 'series-ben', 'Ben')
    befriend(ana, ben)
    game = create_rostered_game(
        client, ana, court_id(), [ben], ranked=True,
    )
    team1 = [ana['user']['id']]
    team2 = [ben['user']['id']]
    score_games = [
        {'score_team1': 11, 'score_team2': 7},
        {'score_team1': 8, 'score_team2': 11},
        {'score_team1': 12, 'score_team2': 10},
    ]

    submitted = client.post(
        f"/api/games/{game['id']}/complete",
        json={
            'team1': team1,
            'team2': team2,
            'score_games': score_games,
            # A client summary can never override the server-derived result.
            'score_team1': 0,
            'score_team2': 99,
        },
        headers=headers(ana),
    )
    assert submitted.status_code == 200, submitted.get_json()
    body = submitted.get_json()
    assert body['status'] == 'awaiting_confirmation'
    assert (body['score_team1'], body['score_team2']) == (2, 1)
    assert (body['match_score_team1'], body['match_score_team2']) == (2, 1)
    assert body['score_games'] == [
        {'game_number': index, **row}
        for index, row in enumerate(score_games, start=1)
    ]
    assert [
        (row.game_number, row.score_team1, row.score_team2)
        for row in GameScoreLine.query.filter_by(game_id=game['id'])
        .order_by(GameScoreLine.game_number).all()
    ] == [(1, 11, 7), (2, 8, 11), (3, 12, 10)]

    confirmed = client.post(
        f"/api/games/{game['id']}/confirm", headers=headers(ben),
    )
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()['you_won'] is False


def test_multi_game_match_rejects_tie_bad_rows_and_marks_unusual_game(client):
    ana = register(client, 'series-errors-ana', 'Ana')
    ben = register(client, 'series-errors-ben', 'Ben')
    game = create_rostered_game(client, ana, court_id(), [ben])
    base = {
        'team1': [ana['user']['id']],
        'team2': [ben['user']['id']],
    }

    tied = client.post(
        f"/api/games/{game['id']}/complete",
        json={**base, 'score_games': [
            {'score_team1': 11, 'score_team2': 7},
            {'score_team1': 7, 'score_team2': 11},
        ]},
        headers=headers(ana),
    )
    assert tied.status_code == 400
    assert tied.get_json()['error'] == 'match_score_tied'

    too_many = client.post(
        f"/api/games/{game['id']}/complete",
        json={**base, 'score_games': [
            {'score_team1': 11, 'score_team2': 7},
        ] * 6},
        headers=headers(ana),
    )
    assert too_many.status_code == 400
    assert too_many.get_json()['error'] == 'invalid_score_games'

    unusual = client.post(
        f"/api/games/{game['id']}/complete",
        json={**base, 'score_games': [
            {'score_team1': 11, 'score_team2': 7},
            {'score_team1': 4, 'score_team2': 9},
            {'score_team1': 11, 'score_team2': 8},
        ]},
        headers=headers(ana),
    )
    assert unusual.status_code == 422
    assert unusual.get_json() == {
        'error': 'nonstandard_pickleball_score',
        'can_confirm': True,
        'game_number': 2,
    }
    row = db.session.get(Game, game['id'])
    assert row.status == 'upcoming'
    assert row.score_team1 is None and row.score_team2 is None
    assert GameScoreLine.query.filter_by(game_id=game['id']).count() == 0


def test_invalid_score_values_are_rejected_without_mutating_either_flow(client):
    ana = register(client, 'invalid-ana', 'Ana')
    ben = register(client, 'invalid-ben', 'Ben')
    befriend(ana, ben)
    court = court_id()
    team1 = [ana['user']['id']]
    team2 = [ben['user']['id']]
    game = create_rostered_game(client, ana, court, [ben])

    invalid_cases = (
        ({'score_team2': None}, 'scores_required'),
        ({'score_team1': True}, 'invalid_scores'),
        ({'score_team1': 11.5}, 'invalid_scores'),
        ({'score_team1': -1}, 'invalid_scores'),
        ({'score_team2': 11}, 'invalid_scores'),
        ({'score_team1': 100, 'score_team2': 98}, 'invalid_scores'),
    )
    for changes, expected_error in invalid_cases:
        payload = score_payload(team1, team2)
        payload.update(changes)
        logged = client.post('/api/games/log', json={
            'court_id': court,
            **payload,
        }, headers=headers(ana))
        assert logged.status_code == 400
        assert logged.get_json()['error'] == expected_error

        submitted = client.post(
            f"/api/games/{game['id']}/complete",
            json=payload,
            headers=headers(ana),
        )
        assert submitted.status_code == 400
        assert submitted.get_json()['error'] == expected_error

    row = db.session.get(Game, game['id'])
    db.session.expire(row, ['players'])
    assert row.status == 'upcoming'
    assert row.score_team1 is None and row.score_team2 is None
    assert all(player.team is None for player in row.players)


def test_only_unique_disjoint_1v1_or_2v2_teams_are_accepted(client):
    players = [
        register(client, f'teams-{index}', name)
        for index, name in enumerate(('Ana', 'Ben', 'Cam', 'Dee'))
    ]
    ana, ben, cam, dee = players
    for player in (ben, cam, dee):
        befriend(ana, player)
    court = court_id()
    game = create_rostered_game(client, ana, court, [ben, cam, dee])
    a_id, b_id, c_id, d_id = [player['user']['id'] for player in players]

    invalid_matchups = (
        ([a_id], [b_id, c_id], 'uneven_teams'),
        ([a_id, b_id, c_id], [d_id, 99998, 99999], 'invalid_team_size'),
        ([a_id, a_id], [b_id, c_id], 'duplicate_player'),
        ([a_id, b_id], [b_id, c_id], 'player_on_both_teams'),
    )
    for team1, team2, expected_error in invalid_matchups:
        payload = score_payload(team1, team2)
        logged = client.post('/api/games/log', json={
            'court_id': court,
            **payload,
        }, headers=headers(ana))
        assert logged.status_code == 400
        assert logged.get_json()['error'] == expected_error

        submitted = client.post(
            f"/api/games/{game['id']}/complete",
            json=payload,
            headers=headers(ana),
        )
        assert submitted.status_code == 400
        assert submitted.get_json()['error'] == expected_error

    row = db.session.get(Game, game['id'])
    db.session.expire(row, ['players'])
    assert all(player.team is None for player in row.players)


def test_corrected_lineup_clears_players_omitted_from_the_new_score(client):
    players = [
        register(client, f'correction-{index}', name)
        for index, name in enumerate(('Ana', 'Ben', 'Cam', 'Dee'))
    ]
    ana, ben, cam, dee = players
    court = court_id()
    game = create_rostered_game(
        client, ana, court, [ben, cam, dee], ranked=True,
    )
    a_id, b_id, c_id, d_id = [player['user']['id'] for player in players]

    first = client.post(
        f"/api/games/{game['id']}/complete",
        json=score_payload([a_id, b_id], [c_id, d_id]),
        headers=headers(ana),
    )
    assert first.status_code == 200, first.get_json()
    assert first.get_json()['status'] == 'awaiting_confirmation'

    corrected = client.post(
        f"/api/games/{game['id']}/complete",
        json=score_payload([a_id], [c_id], 15, 8),
        headers=headers(ana),
    )
    assert corrected.status_code == 200, corrected.get_json()
    assert corrected.get_json()['status'] == 'awaiting_confirmation'

    assignments = {
        player.user_id: player.team
        for player in GamePlayer.query.filter_by(game_id=game['id']).all()
    }
    assert assignments == {
        a_id: 1,
        b_id: None,
        c_id: 2,
        d_id: None,
    }


def test_larger_casual_session_wraps_without_forging_one_score(client):
    players = [
        register(client, f'session-{index}', name)
        for index, name in enumerate(('Ana', 'Ben', 'Cam', 'Dee', 'Eli', 'Fox'))
    ]
    host, *others = players
    outsider = register(client, 'session-outsider', 'Outsider')
    court = court_id()
    game = create_rostered_game(
        client, host, court, others, max_players=6,
    )
    ids = [player['user']['id'] for player in players]
    ratings_before = {
        player['user']['id']: player['user']['rating'] for player in players
    }

    # Capacity declares a rotating group session. Even a forged first-four
    # payload cannot turn it into one 2v2 result.
    forged = client.post(
        f"/api/games/{game['id']}/complete",
        json=score_payload(ids[:2], ids[2:4]),
        headers=headers(host),
    )
    assert forged.status_code == 409
    assert forged.get_json() == {
        'error': 'session_requires_wrap_up',
        'can_complete_session': True,
    }
    row = db.session.get(Game, game['id'])
    assert row.status == 'upcoming'
    assert row.score_team1 is None and row.score_team2 is None
    assert all(player.team is None for player in row.players)

    future = client.post(
        f"/api/games/{game['id']}/complete-session", headers=headers(host),
    )
    assert future.status_code == 409
    assert future.get_json()['error'] == 'game_not_started'

    row.scheduled_at = utcnow() - timedelta(minutes=5)
    db.session.commit()
    db.session.expire_all()
    queued = client.post(
        f"/api/games/{game['id']}/waitlist", headers=headers(outsider),
    )
    assert queued.status_code == 200, queued.get_json()
    assert queued.get_json()['waitlist_count'] == 1
    detail = client.get(
        f"/api/games/{game['id']}", headers=headers(host),
    ).get_json()
    assert detail['can_enter_score'] is False
    assert detail['can_complete_session'] is True
    assert detail['completion_kind'] is None

    denied = client.post(
        f"/api/games/{game['id']}/complete-session",
        headers=headers(outsider),
    )
    assert denied.status_code == 403

    invalid_attendee_cases = (
        ('not-a-list', 'invalid_attendees'),
        ([ids[0], True], 'invalid_attendees'),
        ([ids[0], 1.5], 'invalid_attendees'),
        ([ids[0], ids[1], ids[1]], 'invalid_attendees'),
        ([ids[0], outsider['user']['id']], 'unknown_player'),
        ([ids[1], ids[2]], 'must_include_self'),
        ([ids[0]], 'session_needs_two_players'),
    )
    for attendee_user_ids, expected_error in invalid_attendee_cases:
        invalid = client.post(
            f"/api/games/{game['id']}/complete-session",
            json={'attendee_user_ids': attendee_user_ids},
            headers=headers(host),
        )
        assert invalid.status_code in (400, 409), invalid.get_json()
        assert invalid.get_json()['error'] == expected_error

    attending_ids = ids[:-1]
    wrapped = client.post(
        f"/api/games/{game['id']}/complete-session",
        json={'attendee_user_ids': attending_ids},
        headers=headers(host),
    )
    assert wrapped.status_code == 200, wrapped.get_json()
    body = wrapped.get_json()
    assert body['status'] == 'completed'
    assert body['completion_kind'] == 'session'
    assert body['score_team1'] is None and body['score_team2'] is None
    assert body['can_enter_score'] is False
    assert body['can_complete_session'] is False
    assert {player['user_id'] for player in body['players']} == set(attending_ids)
    assert all(player['team'] is None for player in body['players'])
    assert body['waitlist_count'] == 0

    notification_count = Notification.query.filter_by(
        related_game_id=game['id'], kind='session_completed',
    ).count()
    assert notification_count == len(attending_ids) - 1
    assert Notification.query.filter_by(
        related_game_id=game['id'],
        kind='session_completed',
        user_id=ids[-1],
    ).count() == 0
    replay = client.post(
        f"/api/games/{game['id']}/complete-session", headers=headers(host),
    )
    assert replay.status_code == 200
    assert Notification.query.filter_by(
        related_game_id=game['id'], kind='session_completed',
    ).count() == notification_count

    db.session.expire_all()
    persisted = db.session.get(Game, game['id'])
    assert all(player.team is None and player.rating_delta is None
               for player in persisted.players)
    assert {
        player['user']['id']: db.session.get(User, player['user']['id']).rating
        for player in players
    } == ratings_before

    history = client.get('/api/games/history', headers=headers(host)).get_json()
    assert game['id'] in {item['id'] for item in history['items']}
    absent_history = client.get(
        '/api/games/history', headers=headers(players[-1]),
    ).get_json()
    assert game['id'] not in {item['id'] for item in absent_history['items']}
    absent_stats = client.get(
        '/api/me/stats', headers=headers(players[-1]),
    ).get_json()
    assert absent_stats['games_total'] == 0
    results = client.get('/api/games/results', headers=headers(host)).get_json()
    assert game['id'] not in {item['id'] for item in results['items']}
    crew = client.get(
        f"/api/games/{game['id']}/crew", headers=headers(host),
    )
    assert crew.status_code == 200
    assert {item['id'] for item in crew.get_json()['items']} == set(attending_ids[1:])
