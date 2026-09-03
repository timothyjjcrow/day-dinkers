"""Focused regressions for community privacy, challenges, and venue discovery."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from backend.app import create_app, db
from backend.integrations.models import BusinessScheduleOccurrence
from backend.models import (
    BusinessOffering,
    BusinessProfile,
    BusinessScheduleItem,
    Court,
    Friendship,
    Game,
    GameInvite,
    GamePlayer,
    Notification,
    User,
    utcnow,
)


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add_all([
            Court(
                name='Audit Open Court', city='Costa Mesa', state='CA',
                county_slug='orange-county', latitude=33.66,
                longitude=-117.91, num_courts=6,
            ),
            Court(
                name='Audit Second Court', city='Irvine', state='CA',
                county_slug='orange-county', latitude=33.68,
                longitude=-117.82, num_courts=4,
            ),
            Court(
                name='Audit Closed Court', city='Irvine', state='CA',
                county_slug='orange-county', latitude=33.69,
                longitude=-117.81, num_courts=4, closed=True,
            ),
        ])
        db.session.commit()
        yield app
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


def courts_by_name(app):
    with app.app_context():
        return {court.name: court.id for court in Court.query.all()}


def challenge_write_counts(app):
    with app.app_context():
        return {
            'games': Game.query.count(),
            'players': GamePlayer.query.count(),
            'invites': GameInvite.query.count(),
            'notifications': Notification.query.filter_by(kind='challenge').count(),
        }


def test_public_club_detail_applies_game_visibility_and_block_privacy(client, app):
    owner = register(client, 'club-owner', 'Owner')
    friend = register(client, 'club-friend', 'Friend')
    invitee = register(client, 'club-invitee', 'Invitee')
    outsider = register(client, 'club-outsider', 'Outsider')
    blocked_viewer = register(client, 'club-blocked', 'Blocked viewer')
    court_id = courts_by_name(app)['Audit Open Court']

    club_response = client.post('/api/clubs', json={
        'name': 'Audit Community',
        'home_court_id': court_id,
    }, headers=headers(owner))
    assert club_response.status_code == 201, club_response.get_json()
    club_id = club_response.get_json()['id']

    with app.app_context():
        db.session.add(Friendship(
            requester_id=owner['user']['id'],
            addressee_id=friend['user']['id'],
            status='accepted',
        ))
        games = []
        for index, visibility in enumerate(('open', 'friends', 'private')):
            game = Game(
                court_id=court_id,
                creator_id=owner['user']['id'],
                club_id=club_id,
                scheduled_at=utcnow() + timedelta(days=1, minutes=index),
                game_type='casual',
                visibility=visibility,
                max_players=4,
                notes=f'{visibility} roster marker',
            )
            db.session.add(game)
            db.session.flush()
            db.session.add(GamePlayer(
                game_id=game.id, user_id=owner['user']['id'],
            ))
            if visibility == 'private':
                db.session.add(GameInvite(
                    game_id=game.id, user_id=invitee['user']['id'],
                ))
            games.append(game)
        db.session.commit()
        game_ids = {game.visibility: game.id for game in games}

    assert client.post(
        f'/api/clubs/{club_id}/join', headers=headers(blocked_viewer),
    ).status_code == 200
    assert client.post(
        f"/api/users/{blocked_viewer['user']['id']}/block",
        headers=headers(owner),
    ).status_code == 200

    def visible_game_ids(player):
        response = client.get(
            f'/api/clubs/{club_id}', headers=headers(player),
        )
        assert response.status_code == 200, response.get_json()
        return {game['id'] for game in response.get_json()['upcoming_games']}

    assert visible_game_ids(outsider) == {game_ids['open']}
    assert visible_game_ids(friend) == {
        game_ids['open'], game_ids['friends'],
    }

    assert visible_game_ids(invitee) == {
        game_ids['open'], game_ids['private'],
    }
    assert visible_game_ids(owner) == set(game_ids.values())
    assert visible_game_ids(blocked_viewer) == set()

    # Discovery exposes only the aggregate member count. Friendship or a
    # private-game invitation is not Club membership and must not reveal the
    # roster or the per-member community record.
    for nonmember in (outsider, friend, invitee):
        public_detail = client.get(
            f'/api/clubs/{club_id}', headers=headers(nonmember),
        ).get_json()
        assert public_detail['joined'] is False
        assert public_detail['member_count'] == 2
        assert public_detail['roster_visible'] is False
        assert public_detail['members'] == []

    owner_detail = client.get(
        f'/api/clubs/{club_id}', headers=headers(owner),
    ).get_json()
    blocked_detail = client.get(
        f'/api/clubs/{club_id}', headers=headers(blocked_viewer),
    ).get_json()
    assert owner_detail['joined'] is True
    assert owner_detail['roster_visible'] is True
    assert blocked_detail['joined'] is True
    assert blocked_detail['roster_visible'] is True
    owner_members = {member['id'] for member in owner_detail['members']}
    blocked_members = {member['id'] for member in blocked_detail['members']}
    assert blocked_viewer['user']['id'] not in owner_members
    assert owner['user']['id'] not in blocked_members
    assert blocked_members == {blocked_viewer['user']['id']}

    assert client.get(
        f"/api/games/{game_ids['friends']}", headers=headers(outsider),
    ).status_code == 404
    assert client.get(
        f"/api/games/{game_ids['private']}", headers=headers(outsider),
    ).status_code == 404


def test_general_game_discovery_requires_an_explicit_coordinate_pair(client):
    player = register(client, 'location-boundary', 'Location Boundary')
    auth = headers(player)

    for path in ('/api/games', '/api/games?lat=33.66', '/api/games?lng=-117.91'):
        response = client.get(path, headers=auth)
        assert response.status_code == 400
        assert response.get_json() == {'error': 'location_required'}

    assert client.get('/api/games?lat=33.66&lng=-117.91', headers=auth).status_code == 200
    assert client.get('/api/games?mine=1', headers=auth).status_code == 200
    assert client.get('/api/games?friends=1', headers=auth).status_code == 200


def test_challenge_validation_has_no_side_effects_and_retries_converge(client, app):
    actor = register(client, 'challenge-actor', 'Actor')
    target = register(client, 'challenge-target', 'Target')
    deleted = register(client, 'challenge-deleted', 'Deleted')
    blocked = register(client, 'challenge-blocked', 'Blocked')
    other = register(client, 'challenge-other', 'Other')
    courts = courts_by_name(app)
    open_court = courts['Audit Open Court']
    second_court = courts['Audit Second Court']
    closed_court = courts['Audit Closed Court']

    baseline = challenge_write_counts(app)
    malformed_payloads = (
        None,
        [],
        {},
        {'court_id': 'not-a-number'},
        {'court_id': True},
        {'court_id': 1.5},
        {'court_id': 0},
    )
    for payload in malformed_payloads:
        kwargs = {'headers': headers(actor)}
        if payload is not None:
            kwargs['json'] = payload
        response = client.post(
            f"/api/users/{target['user']['id']}/challenge", **kwargs,
        )
        assert response.status_code == 400, response.get_json()
        assert challenge_write_counts(app) == baseline

    with app.app_context():
        db.session.get(User, deleted['user']['id']).deleted_at = utcnow()
        db.session.commit()
    deleted_response = client.post(
        f"/api/users/{deleted['user']['id']}/challenge",
        json={'court_id': open_court}, headers=headers(actor),
    )
    assert deleted_response.status_code == 404
    assert deleted_response.get_json() == {'error': 'user_not_found'}
    assert challenge_write_counts(app) == baseline

    assert client.post(
        f"/api/users/{blocked['user']['id']}/block",
        headers=headers(actor),
    ).status_code == 200
    blocked_baseline = challenge_write_counts(app)
    blocked_response = client.post(
        f"/api/users/{blocked['user']['id']}/challenge",
        json={'court_id': open_court}, headers=headers(actor),
    )
    assert blocked_response.status_code == 403
    assert blocked_response.get_json() == {'error': 'user_blocked'}
    assert challenge_write_counts(app) == blocked_baseline

    closed_response = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json={'court_id': closed_court}, headers=headers(actor),
    )
    assert closed_response.status_code == 409
    assert closed_response.get_json() == {'error': 'court_closed'}
    assert challenge_write_counts(app) == blocked_baseline

    payload = {
        'court_id': open_court,
        'client_attempt_id': 'ranked-challenge-audit-1',
    }
    created = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json=payload, headers=headers(actor),
    )
    assert created.status_code == 201, created.get_json()
    game_id = created.get_json()['id']
    created_counts = challenge_write_counts(app)
    assert created_counts == {
        'games': baseline['games'] + 1,
        'players': baseline['players'] + 1,
        'invites': baseline['invites'] + 1,
        'notifications': baseline['notifications'] + 1,
    }

    exact_retry = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json=payload, headers=headers(actor),
    )
    assert exact_retry.status_code == 200, exact_retry.get_json()
    assert exact_retry.get_json()['id'] == game_id
    assert challenge_write_counts(app) == created_counts

    keyless_retry = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json={'court_id': open_court}, headers=headers(actor),
    )
    assert keyless_retry.status_code == 200, keyless_retry.get_json()
    assert keyless_retry.get_json()['id'] == game_id
    assert challenge_write_counts(app) == created_counts

    conflict = client.post(
        f"/api/users/{other['user']['id']}/challenge",
        json={
            'court_id': second_court,
            'client_attempt_id': payload['client_attempt_id'],
        },
        headers=headers(actor),
    )
    assert conflict.status_code == 409
    assert conflict.get_json() == {
        'error': 'client_attempt_id_conflict',
        'existing_game_id': game_id,
    }
    assert challenge_write_counts(app) == created_counts

    cancelled = client.post(
        f'/api/games/{game_id}/cancel', headers=headers(actor),
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    replay_after_cancel = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json=payload, headers=headers(actor),
    )
    assert replay_after_cancel.status_code == 200
    assert replay_after_cancel.get_json()['id'] == game_id
    assert replay_after_cancel.get_json()['status'] == 'cancelled'

    fresh = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json={'court_id': open_court}, headers=headers(actor),
    )
    assert fresh.status_code == 201, fresh.get_json()
    assert fresh.get_json()['id'] != game_id
    assert client.post(
        f"/api/games/{fresh.get_json()['id']}/join",
        headers=headers(target),
    ).status_code == 200
    submitted = client.post(
        f"/api/games/{fresh.get_json()['id']}/complete",
        json={
            'team1': [actor['user']['id']],
            'team2': [target['user']['id']],
            'score_team1': 11,
            'score_team2': 7,
        },
        headers=headers(actor),
    )
    assert submitted.status_code == 200, submitted.get_json()
    assert client.post(
        f"/api/games/{fresh.get_json()['id']}/confirm",
        headers=headers(target),
    ).status_code == 200
    after_completed = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json={'court_id': open_court}, headers=headers(actor),
    )
    assert after_completed.status_code == 201, after_completed.get_json()
    assert after_completed.get_json()['id'] not in {game_id, fresh.get_json()['id']}


def test_compact_business_booking_signal_includes_only_active_offering_links(
        client, app):
    owner = register(client, 'booking-owner', 'Venue Owner')
    now = utcnow()
    with app.app_context():
        rows = []
        for index, suffix in enumerate(('Active', 'Inactive', 'Unpublished')):
            court = Court(
                name=f'Booking Audit {suffix}', city='Austin', state='TX',
                county_slug='travis-county', latitude=30.20 + index * .01,
                longitude=-97.70, num_courts=4,
            )
            db.session.add(court)
            db.session.flush()
            profile = BusinessProfile(
                owner_id=owner['user']['id'], court_id=court.id,
                name=f'{suffix} Venue', claim_status='verified',
                verified_at=now, published=suffix != 'Unpublished',
                booking_url='',
            )
            db.session.add(profile)
            db.session.flush()
            db.session.add(BusinessOffering(
                business_id=profile.id,
                name='Book a lesson', category='lesson',
                booking_url=f'https://book.example/{suffix.lower()}',
                active=suffix != 'Inactive',
            ))
            rows.append((court.id, profile.id))
        db.session.commit()

    statements = []
    with app.app_context():
        def record_statement(_conn, _cursor, statement, _params, _ctx, _many):
            statements.append(statement.lower())

        event.listen(db.engine, 'before_cursor_execute', record_statement)
        try:
            response = client.get('/api/courts?q=Booking Audit')
        finally:
            event.remove(db.engine, 'before_cursor_execute', record_statement)
    assert response.status_code == 200, response.get_json()
    by_name = {item['name']: item for item in response.get_json()['items']}
    active = by_name['Booking Audit Active']['business']
    inactive = by_name['Booking Audit Inactive']['business']
    unpublished = by_name['Booking Audit Unpublished']['business']
    assert active['booking_available'] is True
    assert active['schedule_available'] is False
    assert active['programs_available'] is True
    assert inactive['booking_available'] is False
    assert inactive['programs_available'] is False
    assert unpublished is None
    assert set(active) == {
        'id', 'name', 'logo_url', 'verified',
        'booking_available', 'membership_available',
        'schedule_available', 'programs_available',
    }
    assert 'booking_url' not in active
    assert len([
        statement for statement in statements
        if 'from business_offering' in statement
    ]) == 1


def test_compact_business_booking_requires_actionable_schedule_inventory(
        client, app):
    owner = register(client, 'schedule-owner', 'Schedule Owner')
    with app.app_context():
        court = Court.query.filter_by(name='Audit Open Court').one()
        profile = BusinessProfile(
            owner_id=owner['user']['id'], court_id=court.id,
            name='Schedule Audit Venue', claim_status='verified',
            verified_at=utcnow(), published=True,
        )
        db.session.add(profile)
        db.session.flush()
        schedule = BusinessScheduleItem(
            business_id=profile.id, title='Full open play', kind='open_play',
            booking_url='https://book.example/full', status='sold_out',
            capacity=16, spots_remaining=0,
        )
        db.session.add(schedule)
        db.session.commit()
        profile_id = profile.id

    def compact_signal():
        response = client.get('/api/courts?q=Audit Open Court')
        assert response.status_code == 200, response.get_json()
        return response.get_json()['items'][0]['business']

    sold_out = compact_signal()
    assert sold_out['booking_available'] is False
    assert sold_out['schedule_available'] is True
    assert sold_out['programs_available'] is True

    with app.app_context():
        schedule = BusinessScheduleItem.query.one()
        schedule.status = 'cancelled'
        db.session.commit()
    cancelled = compact_signal()
    assert cancelled['booking_available'] is False
    assert cancelled['schedule_available'] is False
    assert cancelled['programs_available'] is False

    with app.app_context():
        schedule = BusinessScheduleItem.query.one()
        schedule.status = 'scheduled'
        db.session.commit()
    zero_spots = compact_signal()
    assert zero_spots['booking_available'] is False

    with app.app_context():
        schedule = BusinessScheduleItem.query.one()
        schedule.spots_remaining = 1
        db.session.commit()
    assert compact_signal()['booking_available'] is True

    with app.app_context():
        schedule = BusinessScheduleItem.query.one()
        schedule.recurrence = 'dated'
        schedule.event_date = (utcnow() - timedelta(days=1)).date()
        db.session.commit()
    stale = compact_signal()
    assert stale['booking_available'] is False
    assert stale['schedule_available'] is False
    assert stale['programs_available'] is False
    public_business = client.get(f'/api/businesses/{profile_id}').get_json()
    assert public_business['schedule'] == []


def test_schedule_currency_uses_the_venue_timezone_at_utc_midnight(app):
    instant = datetime(2026, 9, 2, 0, 30)
    local_day = (instant - timedelta(days=1)).date()
    with app.app_context():
        item = BusinessScheduleItem(
            business_id=1, title='Evening open play', kind='open_play',
            recurrence='dated', event_date=local_day,
            start_time='18:00', end_time='20:00',
            timezone='America/Los_Angeles', status='scheduled',
        )
        assert item.is_current(instant) is True
        item.timezone = 'UTC'
        assert item.is_current(instant) is False
        occurrence = BusinessScheduleOccurrence(
            business_id=1, connection_id=1, external_id='timezone-boundary',
            title='Evening open play', kind='open_play', event_date=local_day,
            timezone='America/Los_Angeles', status='scheduled',
            payload_hash='test',
        )
        assert occurrence.is_current(instant) is True
        occurrence.timezone = 'UTC'
        assert occurrence.is_current(instant) is False
