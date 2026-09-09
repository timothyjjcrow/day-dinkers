"""Local-time recurrence rules and per-player series RSVP lifecycle."""

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.app import create_app, db
from backend.models import (
    Court,
    Game,
    GameInvite,
    GamePlayer,
    GameRecurrenceRsvp,
    Notification,
    User,
    utcnow,
)


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Series Court', city='Irvine', state='CA',
            county_slug='orange-county', latitude=33.68,
            longitude=-117.82, num_courts=8,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(person):
    return {'Authorization': f"Bearer {person['token']}"}


def recurring_payload(**overrides):
    start = (utcnow() + timedelta(days=10)).replace(
        hour=18, minute=30, second=0, microsecond=0,
    )
    local_start = start.replace(tzinfo=UTC).astimezone(
        ZoneInfo('America/Los_Angeles'),
    )
    payload = {
        'court_id': Court.query.one().id,
        'scheduled_at': start.isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'recurrence': 'weekly',
        'recurrence_timezone': 'America/Los_Angeles',
        'recurrence_weekdays': ['fri', 'mon'],
        'recurrence_ends_on': (
            local_start.date() + timedelta(days=35)
        ).isoformat(),
        'max_players': 4,
    }
    payload.update(overrides)
    return payload


def test_create_serializes_multi_weekday_local_rule_and_validates_it(client):
    host = register(client, 'host@example.com', 'Host')
    response = client.post(
        '/api/games', json=recurring_payload(), headers=auth(host),
    )

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body['recurrence'] == 'weekly'
    assert body['recurrence_timezone'] == 'America/Los_Angeles'
    assert body['recurrence_weekdays'] == ['mon', 'fri']
    assert body['recurrence_local_time']
    assert body['recurrence_ends_on']
    assert body['recurrence_occurrence_on']
    assert body['my_recurrence_rsvp'] == {
        'standing_rsvp': True,
        'skipped_occurrence_on': None,
        'last_rsvp_occurrence_on': body['recurrence_occurrence_on'],
        'is_skipped': False,
    }

    row = db.session.get(Game, body['id'])
    assert json.loads(row.recurrence_weekdays) == ['mon', 'fri']
    assert row.recurrence_timezone == 'America/Los_Angeles'

    invalid_timezone = client.post(
        '/api/games',
        json=recurring_payload(recurrence_timezone='Mars/Olympus_Mons'),
        headers=auth(host),
    )
    assert invalid_timezone.status_code == 400
    assert invalid_timezone.get_json()['error'] == 'invalid_recurrence_timezone'

    invalid_days = client.post(
        '/api/games',
        json=recurring_payload(recurrence_weekdays=[]),
        headers=auth(host),
    )
    assert invalid_days.status_code == 400
    assert invalid_days.get_json()['error'] == 'invalid_recurrence_weekdays'

    start = datetime.fromisoformat(
        recurring_payload()['scheduled_at'].removesuffix('Z'),
    ).replace(tzinfo=UTC).astimezone(ZoneInfo('America/Los_Angeles'))
    invalid_end = client.post(
        '/api/games',
        json=recurring_payload(
            recurrence_ends_on=(start.date() - timedelta(days=1)).isoformat(),
        ),
        headers=auth(host),
    )
    assert invalid_end.status_code == 400
    assert invalid_end.get_json()['error'] == 'recurrence_end_before_start'


def test_recurrence_attempts_are_replay_safe_and_legacy_weekly_payloads_work(client):
    host = register(client, 'host@example.com', 'Host')
    payload = recurring_payload(client_attempt_id='series-create-attempt-1')

    created = client.post('/api/games', json=payload, headers=auth(host))
    replay = client.post('/api/games', json=payload, headers=auth(host))
    conflict = client.post('/api/games', json={
        **payload,
        'recurrence_weekdays': ['wed'],
    }, headers=auth(host))

    assert created.status_code == 201, created.get_json()
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['id'] == created.get_json()['id']
    assert conflict.status_code == 409
    assert conflict.get_json()['error'] == 'client_attempt_id_conflict'

    legacy_start = (utcnow() + timedelta(days=12)).replace(
        second=0, microsecond=0,
    )
    legacy = client.post('/api/games', json={
        'court_id': Court.query.one().id,
        'scheduled_at': legacy_start.isoformat() + 'Z',
        'recurrence': 'weekly',
        'client_attempt_id': 'legacy-weekly-attempt-1',
    }, headers=auth(host))
    assert legacy.status_code == 201, legacy.get_json()
    legacy_body = legacy.get_json()
    assert legacy_body['recurrence_timezone'] == 'UTC'
    assert legacy_body['recurrence_weekdays'] == [
        ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')[
            legacy_start.weekday()
        ]
    ]
    assert legacy_body['my_recurrence_rsvp']['standing_rsvp'] is True


def test_standing_preference_skip_and_rejoin_preserve_series_membership(client):
    host = register(client, 'host@example.com', 'Host')
    player = register(client, 'player@example.com', 'Player')
    created = client.post(
        '/api/games', json=recurring_payload(), headers=auth(host),
    ).get_json()

    joined = client.post(
        f"/api/games/{created['id']}/join",
        json={'standing_rsvp': True},
        headers=auth(player),
    )
    assert joined.status_code == 200, joined.get_json()
    assert joined.get_json()['my_recurrence_rsvp']['standing_rsvp'] is True

    toggled = client.patch(
        f"/api/games/{created['id']}/recurrence-rsvp",
        json={'standing_rsvp': False},
        headers=auth(player),
    )
    assert toggled.status_code == 200, toggled.get_json()
    assert toggled.get_json()['my_recurrence_rsvp']['standing_rsvp'] is False

    host_required = client.patch(
        f"/api/games/{created['id']}/recurrence-rsvp",
        json={'standing_rsvp': False},
        headers=auth(host),
    )
    assert host_required.status_code == 409
    assert host_required.get_json()['error'] == 'host_standing_rsvp_required'

    skipped = client.post(
        f"/api/games/{created['id']}/skip-occurrence",
        headers=auth(player),
    )
    assert skipped.status_code == 200, skipped.get_json()
    skipped_body = skipped.get_json()
    assert skipped_body['is_joined'] is False
    assert skipped_body['my_recurrence_rsvp']['is_skipped'] is True
    preference = GameRecurrenceRsvp.query.filter_by(
        game_id=created['id'], user_id=player['user']['id'],
    ).one()
    assert preference.standing_rsvp is False
    assert preference.skipped_occurrence_on is not None
    assert GameInvite.query.filter_by(
        game_id=created['id'], user_id=player['user']['id'],
    ).one_or_none() is not None

    retry = client.post(
        f"/api/games/{created['id']}/skip-occurrence",
        headers=auth(player),
    )
    assert retry.status_code == 200, retry.get_json()
    assert retry.get_json()['my_recurrence_rsvp']['is_skipped'] is True

    rejoined = client.post(
        f"/api/games/{created['id']}/join",
        json={'standing_rsvp': True},
        headers=auth(player),
    )
    assert rejoined.status_code == 200, rejoined.get_json()
    rejoined_body = rejoined.get_json()
    assert rejoined_body['is_joined'] is True
    assert rejoined_body['my_recurrence_rsvp']['standing_rsvp'] is True
    assert rejoined_body['my_recurrence_rsvp']['is_skipped'] is False
    assert GameInvite.query.filter_by(
        game_id=created['id'], user_id=player['user']['id'],
    ).one_or_none() is None

    skipped_again = client.post(
        f"/api/games/{created['id']}/skip-occurrence",
        headers=auth(player),
    )
    assert skipped_again.status_code == 200
    left_series = client.post(
        f"/api/games/{created['id']}/leave",
        headers=auth(player),
    )
    assert left_series.status_code == 200, left_series.get_json()
    assert left_series.get_json() == {
        'left_series': True, 'game_id': created['id'],
    }
    assert GameRecurrenceRsvp.query.filter_by(
        game_id=created['id'], user_id=player['user']['id'],
    ).one_or_none() is None
    assert GameInvite.query.filter_by(
        game_id=created['id'], user_id=player['user']['id'],
    ).one_or_none() is None


def test_host_edits_local_rule_without_wiping_current_roster_or_preferences(client):
    host = register(client, 'host@example.com', 'Host')
    player = register(client, 'player@example.com', 'Player')
    created = client.post(
        '/api/games', json=recurring_payload(), headers=auth(host),
    ).get_json()
    assert client.post(
        f"/api/games/{created['id']}/join",
        json={'standing_rsvp': True}, headers=auth(player),
    ).status_code == 200
    end_on = (utcnow().replace(tzinfo=UTC).astimezone(
        ZoneInfo('America/New_York'),
    ).date() + timedelta(days=60)).isoformat()

    edited = client.patch(f"/api/games/{created['id']}", json={
        'edit_scope': 'following_dates',
        'recurrence_timezone': 'America/New_York',
        'recurrence_weekdays': ['thu', 'tue'],
        'recurrence_ends_on': end_on,
    }, headers=auth(host))

    assert edited.status_code == 200, edited.get_json()
    body = edited.get_json()
    assert body['recurrence_timezone'] == 'America/New_York'
    assert body['recurrence_weekdays'] == ['tue', 'thu']
    assert body['recurrence_ends_on'] == end_on
    assert len(body['players']) == 2
    assert GameRecurrenceRsvp.query.filter_by(
        game_id=created['id'], standing_rsvp=True,
    ).count() == 2

    invalid = client.patch(
        f"/api/games/{created['id']}",
        json={'recurrence_weekdays': [], 'edit_scope': 'following_dates'}, headers=auth(host),
    )
    assert invalid.status_code == 400
    assert invalid.get_json()['error'] == 'invalid_recurrence_weekdays'

    stopped = client.patch(
        f"/api/games/{created['id']}",
        json={'recurrence': 'none', 'edit_scope': 'following_dates'}, headers=auth(host),
    )
    assert stopped.status_code == 200, stopped.get_json()
    assert stopped.get_json()['recurrence'] == 'none'
    assert len(stopped.get_json()['players']) == 2
    assert GameRecurrenceRsvp.query.filter_by(
        game_id=created['id'],
    ).count() == 2
    assert db.session.get(Game, created['id']).recurrence_stopped_at


def test_rollover_keeps_local_time_across_dst_and_reinvites_nonstanding(
    client, monkeypatch,
):
    host_data = register(client, 'host@example.com', 'Host')
    standing_data = register(client, 'standing@example.com', 'Standing')
    ask_data = register(client, 'ask@example.com', 'Ask')
    host = db.session.get(User, host_data['user']['id'])
    standing = db.session.get(User, standing_data['user']['id'])
    ask = db.session.get(User, ask_data['user']['id'])
    court = Court.query.one()
    current_occurrence = date(2026, 10, 30)
    game = Game(
        court=court,
        creator=host,
        scheduled_at=datetime(2026, 10, 30, 17, 0),  # 10:00 PDT
        game_type='casual',
        visibility='open',
        recurrence='weekly',
        recurrence_timezone='America/Los_Angeles',
        recurrence_local_time='10:00',
        recurrence_weekdays='["mon","fri"]',
        recurrence_ends_on=date(2026, 11, 30),
        max_players=4,
    )
    db.session.add_all([
        game,
        GamePlayer(game=game, user=host),
        GamePlayer(game=game, user=standing),
        GamePlayer(game=game, user=ask),
        GameRecurrenceRsvp(
            game=game, user=host, standing_rsvp=True,
            last_rsvp_occurrence_on=current_occurrence,
        ),
        GameRecurrenceRsvp(
            game=game, user=standing, standing_rsvp=True,
            last_rsvp_occurrence_on=current_occurrence,
        ),
        GameRecurrenceRsvp(
            game=game, user=ask, standing_rsvp=False,
            last_rsvp_occurrence_on=current_occurrence,
        ),
    ])
    db.session.commit()

    fixed_now = datetime(2026, 10, 30, 21, 0)
    import backend.routes.games as games_route
    monkeypatch.setattr(games_route, 'utcnow', lambda: fixed_now)
    games_route.roll_forward_recurring()

    db.session.refresh(game)
    assert game.scheduled_at == datetime(2026, 10, 30, 17, 0)
    assert game.status == 'expired'
    assert {row.user_id for row in game.players} == {host.id, standing.id, ask.id}
    original_id = game.id
    game = Game.query.filter(Game.recurrence_series_id == original_id, Game.id != original_id).order_by(Game.scheduled_at).first()
    # The new date has a new ID and follows the local clock across DST.
    assert game.id != original_id
    assert game.scheduled_at == datetime(2026, 11, 2, 18, 0)
    next_local = game.scheduled_at.replace(tzinfo=UTC).astimezone(
        ZoneInfo('America/Los_Angeles'),
    )
    assert (next_local.date(), next_local.hour, next_local.minute) == (
        date(2026, 11, 2), 10, 0,
    )
    assert {row.user_id for row in game.players} == {host.id, standing.id}
    assert GameRecurrenceRsvp.query.filter_by(
        game_id=original_id, user_id=ask.id,
    ).one_or_none() is not None
    assert GameInvite.query.filter_by(
        game_id=game.id, user_id=ask.id,
    ).one_or_none() is not None
    ask_nudge = Notification.query.filter_by(
        kind='session_rsvp', user_id=ask.id,
    ).one()
    assert 'Another date' in ask_nudge.title
    assert ask_nudge.related_game_id == game.id


def test_nonexistent_spring_forward_wall_time_is_skipped_not_shifted(client):
    from backend.routes.games import _next_recurrence_start

    rule = Game(
        scheduled_at=datetime(2026, 3, 1, 10, 30),  # Sunday 02:30 PST
        recurrence='weekly',
        recurrence_timezone='America/Los_Angeles',
        recurrence_local_time='02:30',
        recurrence_weekdays='["sun"]',
    )

    next_start, occurrence_on = _next_recurrence_start(
        rule, datetime(2026, 3, 1, 14, 0),
    )

    # Mar 8 has no 02:30 in this zone. The rule keeps its promised wall time
    # and resumes Mar 15 instead of silently moving that date to 03:30.
    assert occurrence_on == date(2026, 3, 15)
    assert next_start == datetime(2026, 3, 15, 9, 30)
    local = next_start.replace(tzinfo=UTC).astimezone(
        ZoneInfo('America/Los_Angeles'),
    )
    assert (local.hour, local.minute) == (2, 30)


def test_recurrence_end_date_is_inclusive_and_closes_after_last_occurrence(
    client, monkeypatch,
):
    host_data = register(client, 'host@example.com', 'Host')
    host = db.session.get(User, host_data['user']['id'])
    game = Game(
        court=Court.query.one(),
        creator=host,
        scheduled_at=datetime(2026, 11, 6, 18, 0),  # Friday 10:00 PST
        game_type='casual',
        visibility='open',
        recurrence='weekly',
        recurrence_timezone='America/Los_Angeles',
        recurrence_local_time='10:00',
        recurrence_weekdays='["fri"]',
        recurrence_ends_on=date(2026, 11, 6),
        max_players=4,
    )
    db.session.add_all([
        game,
        GamePlayer(game=game, user=host),
        GameRecurrenceRsvp(game=game, user=host, standing_rsvp=True),
    ])
    db.session.commit()

    import backend.routes.games as games_route
    monkeypatch.setattr(
        games_route, 'utcnow', lambda: datetime(2026, 11, 6, 22, 0),
    )
    games_route.roll_forward_recurring()

    db.session.refresh(game)
    assert game.status == 'expired'
    assert game.recurrence == 'weekly'
    assert game.recurrence_stopped_at


def dated_rows(series_id):
    return Game.query.filter_by(recurrence_series_id=series_id).order_by(
        Game.scheduled_at, Game.id,
    ).all()


def test_real_dates_are_materialized_once_and_keep_independent_history(client, monkeypatch):
    host = register(client, 'dated-host@example.com', 'Host')
    player = register(client, 'dated-player@example.com', 'Player')
    payload = recurring_payload(duration_minutes=360, client_attempt_id='dated-create-1')
    response = client.post('/api/games', json=payload, headers=auth(host))
    assert response.status_code == 201, response.get_json()
    first = response.get_json()
    assert first['recurrence_series_id'] == first['id']
    assert first['recurrence_edit_scopes'] == ['this_date', 'following_dates']
    rows = dated_rows(first['id'])
    assert len(rows) >= 5
    identities = {(row.id, row.scheduled_at, row.recurrence_occurrence_on) for row in rows}
    assert len({row.recurrence_occurrence_on for row in rows}) == len(rows)
    assert client.post('/api/games', json=payload, headers=auth(host)).status_code == 200
    assert identities == {(row.id, row.scheduled_at, row.recurrence_occurrence_on) for row in dated_rows(first['id'])}
    assert client.post(f"/api/games/{first['id']}/join", json={'standing_rsvp': True}, headers=auth(player)).status_code == 200
    original_start = rows[0].scheduled_at
    import backend.routes.games as games_route
    import backend.models as models
    after_three_hours = original_start + timedelta(hours=3, minutes=1)
    monkeypatch.setattr(games_route, 'utcnow', lambda: after_three_hours)
    monkeypatch.setattr(models, 'utcnow', lambda: after_three_hours)
    games_route.roll_forward_recurring()
    assert db.session.get(Game, first['id']).status == 'upcoming'
    after_end = original_start + timedelta(hours=6, minutes=1)
    monkeypatch.setattr(games_route, 'utcnow', lambda: after_end)
    monkeypatch.setattr(models, 'utcnow', lambda: after_end)
    games_route.roll_forward_recurring()
    expired = db.session.get(Game, first['id'])
    assert expired.status == 'expired'
    assert expired.to_dict(host['user']['id'])['can_complete_session'] is True
    complete = client.post(f"/api/games/{first['id']}/complete-session", json={
        'attendee_user_ids': [host['user']['id'], player['user']['id']],
    }, headers=auth(host))
    assert complete.status_code == 200, complete.get_json()
    assert complete.get_json()['status'] == 'completed'
    games_route.roll_forward_recurring()
    original = client.get(f"/api/games/{first['id']}", headers=auth(player)).get_json()
    assert original['scheduled_at'] == original_start.isoformat() + 'Z'
    assert original['status'] == 'completed'
    assert len(original['players']) == 2
    assert all(row.status == 'upcoming' for row in dated_rows(first['id'])[1:])
    assert all(row.score_team1 is None for row in dated_rows(first['id'])[1:])


def test_multiple_skips_are_dated_and_standing_changes_preserve_explicit_rsvps(client):
    host = register(client, 'skip-host@example.com', 'Host')
    player = register(client, 'skip-player@example.com', 'Player')
    first = client.post('/api/games', json=recurring_payload(), headers=auth(host)).get_json()
    assert client.post(f"/api/games/{first['id']}/join", json={'standing_rsvp': True}, headers=auth(player)).status_code == 200
    dates = dated_rows(first['id'])
    second, third, fourth = dates[1:4]
    for occurrence in (second, third):
        result = client.post(f'/api/games/{occurrence.id}/skip-occurrence', headers=auth(player))
        assert result.status_code == 200, result.get_json()
    for occurrence in (second, third):
        result = client.get(f'/api/games/{occurrence.id}', headers=auth(player)).get_json()
        assert result['my_recurrence_rsvp']['is_skipped'] is True
        assert result['is_joined'] is False
    menu = client.get(f"/api/games/{first['id']}/occurrences", headers=auth(player)).get_json()
    assert {row['id'] for row in menu['occurrences'] if row['is_skipped']} == {second.id, third.id}
    assert client.post(f'/api/games/{fourth.id}/attend', headers=auth(player)).status_code == 200
    result = client.patch(f"/api/games/{first['id']}/recurrence-rsvp", json={'standing_rsvp': False}, headers=auth(player))
    assert result.status_code == 200, result.get_json()
    for occurrence in (dates[0], fourth):
        assert any(row.user_id == player['user']['id'] for row in occurrence.players)
    assert all(not any(row.user_id == player['user']['id'] for row in occurrence.players) for occurrence in dates[4:])
    preference = GameRecurrenceRsvp.query.filter_by(game_id=first['id'], user_id=player['user']['id']).one()
    assert len(preference.skipped_dates()) == 2


def test_scope_is_explicit_and_one_date_edit_does_not_change_future_defaults(client):
    host = register(client, 'edit-host@example.com', 'Host')
    first = client.post('/api/games', json=recurring_payload(title='Sunday players'), headers=auth(host)).get_json()
    selected = dated_rows(first['id'])[1]
    rejected = client.patch(f'/api/games/{selected.id}', json={'title': 'Special date'}, headers=auth(host))
    assert rejected.status_code == 400
    assert rejected.get_json()['error'] == 'recurrence_scope_required'
    moved_start = selected.scheduled_at + timedelta(hours=2)
    result = client.patch(f'/api/games/{selected.id}', json={
        'title': 'Special date', 'scheduled_at': moved_start.isoformat() + 'Z',
        'edit_scope': 'this_date',
    }, headers=auth(host))
    assert result.status_code == 200, result.get_json()
    assert result.get_json()['id'] == selected.id
    root = db.session.get(Game, first['id'])
    assert json.loads(root.recurrence_template)['title'] == 'Sunday players'
    assert all(row.title == 'Sunday players' for row in dated_rows(first['id']) if row.id != selected.id)
    result = client.patch(f'/api/games/{selected.id}', json={
        'title': 'New regular name', 'edit_scope': 'following_dates',
    }, headers=auth(host))
    assert result.status_code == 200, result.get_json()
    assert root.title == 'Sunday players'
    assert all(row.title == 'New regular name' for row in dated_rows(first['id']) if row.scheduled_at >= selected.scheduled_at)
    assert json.loads(root.recurrence_template)['title'] == 'New regular name'


def test_cancel_only_selected_date_then_stop_following_dates_keeps_earlier_plan(client):
    host = register(client, 'cancel-host@example.com', 'Host')
    first = client.post('/api/games', json=recurring_payload(), headers=auth(host)).get_json()
    dates = dated_rows(first['id'])
    assert client.post(f'/api/games/{dates[1].id}/cancel', headers=auth(host)).status_code == 400
    cancelled = client.post(f'/api/games/{dates[1].id}/cancel', json={'edit_scope': 'this_date'}, headers=auth(host))
    assert cancelled.status_code == 200, cancelled.get_json()
    assert dates[0].status == dates[2].status == 'upcoming'
    stopped = client.post(f'/api/games/{dates[2].id}/cancel', json={'edit_scope': 'following_dates'}, headers=auth(host))
    assert stopped.status_code == 200, stopped.get_json()
    assert dates[0].status == 'upcoming'
    assert all(row.status == 'cancelled' for row in dates[1:])
    from backend.routes.games import roll_forward_recurring
    before = {row.id for row in dates}
    roll_forward_recurring()
    assert {row.id for row in dated_rows(first['id'])} == before


def test_private_occurrences_are_not_visible_to_strangers(client):
    host = register(client, 'private-host@example.com', 'Host')
    stranger = register(client, 'private-stranger@example.com', 'Stranger')
    # Seed private series directly: the create API separately enforces who a
    # host may invite; this test targets enumeration of independently dated IDs.
    root = Game(court_id=Court.query.one().id, creator_id=host['user']['id'],
                scheduled_at=utcnow() + timedelta(days=1), visibility='private',
                game_type='casual', recurrence='weekly', max_players=4)
    db.session.add(root)
    db.session.flush()
    db.session.add(GamePlayer(game=root, user_id=host['user']['id']))
    db.session.commit()
    from backend.routes.games import roll_forward_recurring
    roll_forward_recurring()
    visible = client.get(f'/api/games/{root.id}/occurrences', headers=auth(host))
    assert visible.status_code == 200
    assert len(visible.get_json()['occurrences']) >= 4
    for row in visible.get_json()['occurrences']:
        assert client.get(f"/api/games/{row['id']}/occurrences", headers=auth(stranger)).status_code == 404
        assert client.patch(f"/api/games/{row['id']}/recurrence-rsvp", json={'standing_rsvp': True}, headers=auth(stranger)).status_code == 404


def test_legacy_cancelled_series_and_deleted_host_never_generate_new_dates(client):
    host = register(client, 'old-host@example.com', 'Host')
    first = Game(court_id=Court.query.one().id, creator_id=host['user']['id'],
                 scheduled_at=utcnow() - timedelta(days=8), visibility='open',
                 game_type='casual', recurrence='weekly', max_players=4,
                 status='cancelled')
    second = Game(court_id=Court.query.one().id, creator_id=host['user']['id'],
                  scheduled_at=utcnow() - timedelta(days=1), visibility='open',
                  game_type='casual', recurrence='weekly', max_players=4)
    db.session.add_all([first, second])
    db.session.commit()
    db.session.get(User, host['user']['id']).deleted_at = utcnow()
    db.session.commit()
    from backend.routes.games import roll_forward_recurring
    roll_forward_recurring()
    assert Game.query.count() == 2
    assert first.status == 'cancelled'
    assert first.recurrence_stopped_at is not None
    assert second.recurrence_stopped_at is not None


def test_scored_occurrence_does_not_score_or_move_other_dates(client, monkeypatch):
    host = register(client, 'score-host@example.com', 'Host')
    player = register(client, 'score-player@example.com', 'Player')
    first = client.post('/api/games', json=recurring_payload(max_players=2), headers=auth(host)).get_json()
    assert client.post(f"/api/games/{first['id']}/join", json={'standing_rsvp': True}, headers=auth(player)).status_code == 200
    game = db.session.get(Game, first['id'])
    scheduled_at = game.scheduled_at
    import backend.routes.games as games_route
    monkeypatch.setattr(games_route, 'utcnow', lambda: scheduled_at + timedelta(minutes=45))
    scored = client.post(f"/api/games/{first['id']}/complete", json={
        'score_team1': 11, 'score_team2': 7,
        'team1': [host['user']['id']],
        'team2': [player['user']['id']],
    }, headers=auth(host))
    assert scored.status_code == 200, scored.get_json()
    assert scored.get_json()['status'] == 'completed'
    assert game.score_team1 == 11 and game.score_team2 == 7
    assert game.scheduled_at == scheduled_at
    for other in dated_rows(first['id'])[1:]:
        assert other.status == 'upcoming'
        assert other.score_team1 is None and other.score_team2 is None
        assert all(row.team is None for row in other.players)


def test_leaving_series_from_later_date_removes_all_future_rsvps(client):
    host = register(client, 'leave-host@example.com', 'Host')
    player = register(client, 'leave-player@example.com', 'Player')
    first = client.post('/api/games', json=recurring_payload(), headers=auth(host)).get_json()
    assert client.post(f"/api/games/{first['id']}/join", json={'standing_rsvp': True}, headers=auth(player)).status_code == 200
    later = dated_rows(first['id'])[3]
    response = client.post(f'/api/games/{later.id}/leave', headers=auth(player))
    assert response.status_code == 200, response.get_json()
    for occurrence in dated_rows(first['id']):
        assert not any(row.user_id == player['user']['id'] for row in occurrence.players)
    assert GameRecurrenceRsvp.query.filter_by(game_id=first['id'], user_id=player['user']['id']).first() is None


def test_following_edit_normalizes_legacy_empty_local_rule(client):
    host = register(client, 'legacy-edit@example.com', 'Host')
    root = Game(court_id=Court.query.one().id, creator_id=host['user']['id'],
                scheduled_at=utcnow() + timedelta(days=1), visibility='open',
                game_type='casual', recurrence='weekly', max_players=4,
                recurrence_timezone='America/Los_Angeles',
                recurrence_local_time='', recurrence_weekdays='[]')
    db.session.add(root)
    db.session.flush()
    db.session.add(GamePlayer(game=root, user_id=host['user']['id']))
    db.session.commit()
    from backend.routes.games import roll_forward_recurring
    roll_forward_recurring()
    date = dated_rows(root.id)[1]
    result = client.patch(f'/api/games/{date.id}', json={
        'title': 'Renamed regulars', 'edit_scope': 'following_dates',
    }, headers=auth(host))
    assert result.status_code == 200, result.get_json()
    assert all(row.title == 'Renamed regulars' for row in dated_rows(root.id)[1:])
