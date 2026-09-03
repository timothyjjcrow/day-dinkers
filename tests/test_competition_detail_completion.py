"""Focused API coverage for the completed competition-detail contracts."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, League, Notification, Tournament, User, utcnow
from backend.routes.leagues import advance_due_league_rounds
from backend.routes.tournaments import send_tournament_reminders


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        court = Court(
            name='Competition Courts', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82, num_courts=8,
        )
        db.session.add(court)
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': slug.replace('-', ' ').title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def court_id(app):
    return Court.query.one().id


def create_league(client, app, organizer, *, starts_at=None):
    response = client.post('/api/leagues', headers=auth(organizer), json={
        'name': 'Tuesday Ladder',
        'court_id': court_id(app),
        'starts_at': (starts_at or utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'box_size': 3,
        'max_players': 8,
        'round_days': 7,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def create_tournament(client, app, organizer):
    response = client.post('/api/tournaments', headers=auth(organizer), json={
        'name': 'September Singles',
        'court_id': court_id(app),
        'starts_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'format': 'single_elim',
        'event_type': 'singles',
        'max_entries': 4,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def set_skill_rating(account, rating):
    user = db.session.get(User, account['user']['id'])
    user.skill_rating = rating
    db.session.commit()


def test_league_settings_are_visible_editable_and_lock_after_start(client, app):
    organizer = register(client, 'league-organizer')
    members = [register(client, f'league-member-{index}') for index in range(3)]
    stranger = register(client, 'league-stranger')
    league = create_league(client, app, organizer)
    alternate_court = Court(
        name='Alternate Competition Courts', city='Tustin', state='CA',
        latitude=33.74, longitude=-117.82, num_courts=6,
    )
    db.session.add(alternate_court)
    db.session.commit()
    for member in members:
        assert client.post(
            f"/api/leagues/{league['id']}/join", headers=auth(member),
        ).status_code == 200

    forbidden = client.patch(
        f"/api/leagues/{league['id']}", headers=auth(stranger),
        json={'name': 'Hijacked League'},
    )
    assert forbidden.status_code == 403

    updated = client.patch(
        f"/api/leagues/{league['id']}", headers=auth(organizer),
        json={
            'name': 'Tuesday Night Ladder',
            'description': 'Play every opponent before the deadline.',
            'court_id': alternate_court.id,
            'starts_at': (utcnow() + timedelta(days=3)).isoformat() + 'Z',
            'box_size': 4,
            'max_players': 12,
            'round_days': 10,
        },
    )
    assert updated.status_code == 200, updated.get_json()
    body = updated.get_json()
    assert body['name'] == 'Tuesday Night Ladder'
    assert (body['box_size'], body['max_players'], body['round_days']) == (4, 12, 10)
    assert body['court']['id'] == alternate_court.id
    assert body['available_rounds'] == []
    assert body['match_history'] == []

    too_small = client.patch(
        f"/api/leagues/{league['id']}", headers=auth(organizer),
        json={'max_players': 3},
    )
    assert too_small.status_code == 409
    assert too_small.get_json()['error'] == 'max_players_below_roster'

    forbidden_removal = client.delete(
        f"/api/leagues/{league['id']}/members/{members[2]['user']['id']}",
        headers=auth(stranger),
    )
    assert forbidden_removal.status_code == 403
    removed = client.delete(
        f"/api/leagues/{league['id']}/members/{members[2]['user']['id']}",
        headers=auth(organizer),
    )
    assert removed.status_code == 200, removed.get_json()
    assert removed.get_json()['member_count'] == 3
    assert members[2]['user']['id'] not in {
        member['user']['id'] for member in removed.get_json()['members']
    }
    removed_notices = client.get(
        '/api/notifications', headers=auth(members[2]),
    ).get_json()['items']
    assert any(
        'removed your signup' in notice['title'].lower()
        for notice in removed_notices
    )

    member_notices = client.get(
        '/api/notifications', headers=auth(members[0]),
    ).get_json()['items']
    assert any(
        notice['title'] == 'Tuesday Night Ladder settings were updated'
        and notice['action_url'] == f"/#league/{league['id']}"
        for notice in member_notices
    )

    started = client.post(
        f"/api/leagues/{league['id']}/start", headers=auth(organizer),
    )
    assert started.status_code == 200
    locked = client.patch(
        f"/api/leagues/{league['id']}", headers=auth(organizer),
        json={'round_days': 5},
    )
    assert locked.status_code == 409
    assert locked.get_json()['error'] == 'settings_locked_after_start'
    renamed = client.patch(
        f"/api/leagues/{league['id']}", headers=auth(organizer),
        json={'name': 'Tuesday Finals Ladder'},
    )
    assert renamed.status_code == 200
    assert renamed.get_json()['name'] == 'Tuesday Finals Ladder'


def test_league_round_history_and_manual_start_nudge_are_durable(client, app):
    organizer = register(client, 'history-organizer')
    members = [register(client, f'history-member-{index}') for index in range(2)]
    past = utcnow() - timedelta(minutes=10)
    league = create_league(client, app, organizer, starts_at=past)
    for member in members:
        client.post(f"/api/leagues/{league['id']}/join", headers=auth(member))

    advance_due_league_rounds(now=utcnow())
    advance_due_league_rounds(now=utcnow())
    reminders = Notification.query.filter_by(
        related_league_id=league['id'],
        title='Tuesday Ladder is ready for you to start',
    ).all()
    assert len(reminders) == 1
    assert reminders[0].action_url == f"/#league/{league['id']}"

    started = client.post(
        f"/api/leagues/{league['id']}/start", headers=auth(organizer),
    ).get_json()
    round_one_ids = {match['id'] for match in started['matches']}
    advanced = client.post(
        f"/api/leagues/{league['id']}/advance", headers=auth(organizer),
    )
    assert advanced.status_code == 200, advanced.get_json()
    assert round_one_ids.isdisjoint({match['id'] for match in advanced.get_json()['matches']})

    detail = client.get(
        f"/api/leagues/{league['id']}", headers=auth(organizer),
    ).get_json()
    assert detail['available_rounds'] == [1, 2]
    assert {match['id'] for match in detail['match_history']} == round_one_ids
    assert {match['round'] for match in detail['match_history']} == {1}
    assert {match['round'] for match in detail['matches']} == {2}


def test_tournament_forfeit_advances_without_inventing_a_score(client, app):
    organizer = register(client, 'forfeit-organizer')
    player_one = register(client, 'forfeit-player-one')
    player_two = register(client, 'forfeit-player-two')
    tournament = create_tournament(client, app, organizer)
    for player in (player_one, player_two):
        response = client.post(
            f"/api/tournaments/{tournament['id']}/register",
            headers=auth(player), json={},
        )
        assert response.status_code == 201, response.get_json()
    started = client.post(
        f"/api/tournaments/{tournament['id']}/start", headers=auth(organizer),
    ).get_json()
    match = started['matches'][0]
    assert match['can_forfeit_result'] is True

    participant_view = client.get(
        f"/api/tournaments/{tournament['id']}", headers=auth(player_one),
    ).get_json()
    participant_match = next(item for item in participant_view['matches'] if item['id'] == match['id'])
    assert participant_match['can_forfeit_result'] is False
    assert participant_view['pending_action_count'] == 1

    forbidden = client.post(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/forfeit",
        headers=auth(player_one),
        json={'forfeiting_entry_id': match['entry1_id'], 'result_version': 0},
    )
    assert forbidden.status_code == 403

    decided = client.post(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/forfeit",
        headers=auth(organizer),
        json={
            'forfeiting_entry_id': match['entry1_id'],
            'result_version': 0,
            'reason': 'Player reported they could not attend',
        },
    )
    assert decided.status_code == 200, decided.get_json()
    body = decided.get_json()
    assert body['status'] == 'completed'
    result = next(item for item in body['matches'] if item['id'] == match['id'])
    assert result['winner_entry_id'] == match['entry2_id']
    assert result['score1'] is None and result['score2'] is None
    assert result['result_state'] == 'confirmed'
    assert result['resolution_kind'] == 'organizer_forfeit'
    assert result['result_history'][-1]['action'] == 'resolved'


def test_tournament_manual_start_nudge_is_once_only(client, app):
    organizer = register(client, 'start-nudge-organizer')
    players = [register(client, f'start-nudge-player-{index}') for index in range(2)]
    tournament = create_tournament(client, app, organizer)
    for player in players:
        client.post(
            f"/api/tournaments/{tournament['id']}/register",
            headers=auth(player), json={},
        )
    stored = db.session.get(Tournament, tournament['id'])
    stored.starts_at = utcnow() - timedelta(minutes=5)
    db.session.commit()
    send_tournament_reminders()
    send_tournament_reminders()
    notices = Notification.query.filter_by(
        related_tournament_id=tournament['id'],
        title='September Singles is ready for you to start',
    ).all()
    assert len(notices) == 1
    assert notices[0].action_url == f"/#tournament/{tournament['id']}"

    items = client.get(
        '/api/tournaments?mine=1', headers=auth(organizer),
    ).get_json()['items']
    summary = next(item for item in items if item['id'] == tournament['id'])
    assert summary['start_action_pending'] is True
    assert summary['pending_action_count'] == 1


def test_tournament_division_format_scores_and_match_schedule_are_enforced(client, app):
    organizer = register(client, 'format-organizer')
    player_one = register(client, 'format-player-one')
    player_two = register(client, 'format-player-two')
    outside_player = register(client, 'format-outside-player')
    set_skill_rating(player_one, 3.6)
    set_skill_rating(player_two, 3.9)
    set_skill_rating(outside_player, 4.5)

    created = client.post('/api/tournaments', headers=auth(organizer), json={
        'name': 'Advanced Intermediate Round Robin',
        'court_id': court_id(app),
        'starts_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'format': 'single_elim',
        'event_type': 'singles',
        'max_entries': 4,
    })
    assert created.status_code == 201, created.get_json()
    tournament = created.get_json()
    assert tournament['division_name'] == 'Open'
    assert tournament['game_format'] == 'single_11'
    assert tournament['court_count'] == 1
    assert tournament['match_minutes'] == 30

    configured = client.patch(
        f"/api/tournaments/{tournament['id']}",
        headers=auth(organizer),
        json={
            'court_id': court_id(app),
            'format': 'round_robin',
            'event_type': 'singles',
            'division_name': 'Advanced intermediate',
            'division_min_rating': 3.5,
            'division_max_rating': 4.0,
            'game_format': 'best_of_3_11',
            'court_count': 3,
            'match_minutes': 40,
            'ranked': True,
        },
    )
    assert configured.status_code == 200, configured.get_json()
    configured_body = configured.get_json()
    assert configured_body['format'] == 'round_robin'
    assert configured_body['division_name'] == 'Advanced intermediate'
    assert configured_body['division_min_rating'] == 3.5
    assert configured_body['division_max_rating'] == 4.0
    assert configured_body['game_format'] == 'best_of_3_11'
    assert configured_body['court_count'] == 3
    assert configured_body['match_minutes'] == 40
    assert configured_body['ranked'] is True

    outside = client.post(
        f"/api/tournaments/{tournament['id']}/register",
        headers=auth(outside_player), json={},
    )
    assert outside.status_code == 409
    assert outside.get_json()['error'] == 'outside_division'

    first_entry = client.post(
        f"/api/tournaments/{tournament['id']}/register",
        headers=auth(player_one), json={},
    )
    assert first_entry.status_code == 201, first_entry.get_json()
    locked = client.patch(
        f"/api/tournaments/{tournament['id']}",
        headers=auth(organizer), json={'game_format': 'single_15'},
    )
    assert locked.status_code == 409
    assert locked.get_json()['error'] == 'entries_lock_tournament_format'
    second_entry = client.post(
        f"/api/tournaments/{tournament['id']}/register",
        headers=auth(player_two), json={},
    )
    assert second_entry.status_code == 201, second_entry.get_json()

    started = client.post(
        f"/api/tournaments/{tournament['id']}/start",
        headers=auth(organizer),
    )
    assert started.status_code == 200, started.get_json()
    match = started.get_json()['matches'][0]
    assert match['scheduled_at'] is not None
    assert match['court_number'] == 1
    assert match['game_scores'] == []

    forbidden_schedule = client.patch(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/schedule",
        headers=auth(player_one), json={'court_number': 2},
    )
    assert forbidden_schedule.status_code == 403
    invalid_court = client.patch(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/schedule",
        headers=auth(organizer), json={'court_number': 4},
    )
    assert invalid_court.status_code == 400
    assert invalid_court.get_json()['error'] == 'invalid_match_court_number'
    new_match_time = utcnow() + timedelta(days=2, hours=1)
    rescheduled = client.patch(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/schedule",
        headers=auth(organizer),
        json={'court_number': 2, 'scheduled_at': new_match_time.isoformat() + 'Z'},
    )
    assert rescheduled.status_code == 200, rescheduled.get_json()
    changed_match = next(
        item for item in rescheduled.get_json()['matches'] if item['id'] == match['id']
    )
    assert changed_match['court_number'] == 2
    assert changed_match['scheduled_at'].startswith(new_match_time.isoformat())

    decided_too_early = client.post(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/score",
        headers=auth(player_one),
        json={'games': [
            {'score1': 11, 'score2': 7},
            {'score1': 11, 'score2': 8},
            {'score1': 8, 'score2': 11},
        ]},
    )
    assert decided_too_early.status_code == 400
    assert decided_too_early.get_json()['error'] == 'invalid_score'
    impossible_deuce = client.post(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/score",
        headers=auth(player_one),
        json={'games': [
            {'score1': 12, 'score2': 9},
            {'score1': 7, 'score2': 11},
            {'score1': 11, 'score2': 5},
        ]},
    )
    assert impossible_deuce.status_code == 400

    reported = client.post(
        f"/api/tournaments/{tournament['id']}/matches/{match['id']}/score",
        headers=auth(player_one),
        json={'games': [
            {'score1': 11, 'score2': 7},
            {'score1': 8, 'score2': 11},
            {'score1': 12, 'score2': 10},
        ]},
    )
    assert reported.status_code == 200, reported.get_json()
    reported_match = next(
        item for item in reported.get_json()['matches'] if item['id'] == match['id']
    )
    assert reported_match['result_state'] == 'awaiting_confirmation'
    assert (reported_match['score1'], reported_match['score2']) == (2, 1)
    assert reported_match['game_scores'] == [
        {'score1': 11, 'score2': 7},
        {'score1': 8, 'score2': 11},
        {'score1': 12, 'score2': 10},
    ]
