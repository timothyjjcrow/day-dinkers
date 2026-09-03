"""Configured game flows preserve type, format, and court availability."""

from datetime import timedelta
import pytest

from backend.app import create_app, db
from backend.models import (
    CheckIn,
    Court,
    Game,
    GameArrivalIntent,
    PlayAvailabilityPulse,
    utcnow,
)
from backend.routes.games import _game_attempt_fingerprint, _parse_scheduled_at


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
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _register(client, slug):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': slug.replace('-', ' ').title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def _court_id(client):
    return client.get('/api/courts?q=larson').get_json()['items'][0]['id']


def _other_court_id():
    court = Court(
        name='Rally Annex',
        city='Costa Mesa',
        state='CA',
        county_slug='orange-county',
        latitude=33.67,
        longitude=-117.92,
        num_courts=2,
    )
    db.session.add(court)
    db.session.commit()
    return court.id


def _check_in(client, person, court_id, looking=False):
    response = client.post(
        f'/api/courts/{court_id}/checkin',
        json={'looking_for_game': looking},
        headers=_headers(person),
    )
    assert response.status_code == 200, response.get_json()


def _rally_payload(court_id, attempt, **configuration):
    return {
        'court_id': court_id,
        'scheduled_at': utcnow().isoformat() + 'Z',
        'client_attempt_id': attempt,
        **configuration,
    }


def test_omitted_configuration_defaults_to_casual_doubles_and_replays(client):
    player = _register(client, 'default-rally')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    payload = _rally_payload(court_id, 'configured-default-attempt')

    created = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert created.status_code == 201, created.get_json()
    game = created.get_json()['game']
    assert game['game_type'] == 'casual'
    assert game['max_players'] == 4

    replay = client.post('/api/games/rally', json={
        **payload,
        'game_type': 'CASUAL',
        'max_players': '4',
    }, headers=_headers(player))
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['game']['id'] == game['id']
    assert Game.query.count() == 1


def test_rallies_only_join_an_exact_type_and_capacity(client):
    ranked_host = _register(client, 'ranked-host')
    casual_host = _register(client, 'casual-host')
    ranked_joiner = _register(client, 'ranked-joiner')
    court_id = _court_id(client)
    for player in (ranked_host, casual_host, ranked_joiner):
        _check_in(client, player, court_id, looking=True)

    ranked = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'configured-ranked-host',
        game_type='ranked',
        max_players=2,
    ), headers=_headers(ranked_host))
    assert ranked.status_code == 201, ranked.get_json()
    ranked_game = ranked.get_json()['game']
    assert ranked_game['game_type'] == 'ranked'
    assert ranked_game['max_players'] == 2

    different = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'configured-casual-host',
        game_type='casual',
        max_players=2,
    ), headers=_headers(casual_host))
    assert different.status_code == 201, different.get_json()
    casual_game = different.get_json()['game']
    assert casual_game['id'] != ranked_game['id']
    assert casual_game['game_type'] == 'casual'
    assert casual_game['max_players'] == 2

    joined = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'configured-ranked-joiner',
        game_type='ranked',
        max_players=2,
    ), headers=_headers(ranked_joiner))
    assert joined.status_code == 200, joined.get_json()
    assert joined.get_json()['outcome'] == 'joined'
    assert joined.get_json()['game']['id'] == ranked_game['id']
    assert len(joined.get_json()['game']['players']) == 2
    assert Game.query.count() == 2


def test_same_player_same_court_different_configuration_is_a_conflict(client):
    player = _register(client, 'configuration-conflict')
    court_id = _court_id(client)
    _check_in(client, player, court_id)

    created = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'configuration-conflict-original',
        game_type='casual',
        max_players=4,
    ), headers=_headers(player))
    assert created.status_code == 201, created.get_json()
    original = created.get_json()['game']

    conflict = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'configuration-conflict-new-key',
        game_type='ranked',
        max_players=2,
    ), headers=_headers(player))
    assert conflict.status_code == 409, conflict.get_json()
    body = conflict.get_json()
    assert body['error'] == 'active_rally_configuration_conflict'
    assert body['game_id'] == original['id']
    assert body['game']['id'] == original['id']
    assert body['game']['game_type'] == 'casual'
    assert body['game']['max_players'] == 4
    assert Game.query.count() == 1


def test_closed_court_cannot_start_a_new_configured_rally(client):
    player = _register(client, 'closed-court-rally')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    court = db.session.get(Court, court_id)
    court.closed = True
    db.session.commit()

    response = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'closed-court-rally-attempt',
        game_type='ranked',
        max_players=2,
    ), headers=_headers(player))

    assert response.status_code == 409
    assert response.get_json() == {'error': 'court_closed'}
    assert Game.query.count() == 0


def test_closed_court_cannot_schedule_a_game(client):
    player = _register(client, 'closed-court-schedule')
    court_id = _court_id(client)
    court = db.session.get(Court, court_id)
    court.closed = True
    db.session.commit()

    response = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(days=1)).isoformat() + 'Z',
        'client_attempt_id': 'closed-court-schedule-attempt',
        'game_type': 'casual',
        'max_players': 4,
        'visibility': 'open',
    }, headers=_headers(player))

    assert response.status_code == 409
    assert response.get_json() == {'error': 'court_closed'}
    assert Game.query.count() == 0


@pytest.mark.parametrize(('field', 'value', 'error'), [
    ('game_type', 'competitive', 'invalid_game_type'),
    ('game_type', None, 'invalid_game_type'),
    ('max_players', 3, 'invalid_max_players'),
    ('max_players', True, 'invalid_max_players'),
])
def test_invalid_rally_configuration_is_rejected(client, field, value, error):
    player = _register(client, f'invalid-{field}-{value}')
    payload = _rally_payload(
        _court_id(client), f'invalid-{field}-{value}-attempt',
    )
    payload[field] = value

    response = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert response.status_code == 400
    assert response.get_json() == {'error': error}
    assert Game.query.count() == 0


def test_v3_idempotency_binds_game_type_and_capacity(client):
    player = _register(client, 'configured-idempotency')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    payload = _rally_payload(
        court_id,
        'configured-idempotency-attempt',
        game_type='ranked',
        max_players=2,
    )

    created = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert created.status_code == 201, created.get_json()
    replay = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['game']['id'] == created.get_json()['game']['id']

    changed = client.post('/api/games/rally', json={
        **payload,
        'game_type': 'casual',
    }, headers=_headers(player))
    assert changed.status_code == 409
    assert changed.get_json() == {'error': 'client_attempt_id_conflict'}
    assert Game.query.count() == 1


def test_legacy_confirm_flag_cannot_create_remote_presence(client):
    player = _register(client, 'remote-presence-create')
    court_id = _court_id(client)
    payload = _rally_payload(
        court_id,
        'remote-presence-create-attempt',
        game_type='ranked',
        max_players=2,
        confirm_court_presence=True,
    )

    refused = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )

    assert refused.status_code == 409, refused.get_json()
    assert refused.get_json() == {'error': 'active_checkin_required'}
    assert CheckIn.query.filter_by(user_id=player['user']['id']).count() == 0
    assert Game.query.count() == 0

    # The field remains accepted for older clients after they use the explicit
    # check-in endpoint; it only asks for presence details in the response.
    _check_in(client, player, court_id)
    started = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert started.status_code == 201, started.get_json()
    body = started.get_json()
    assert body['outcome'] == 'created'
    assert body['presence']['checked_in'] is True
    assert body['presence']['court_id'] == court_id
    assert body['presence']['looking_for_game'] is False
    assert body['presence_confirmed'] is True
    checkin = CheckIn.query.filter_by(user_id=player['user']['id']).one()
    assert checkin.court_id == court_id
    assert checkin.checked_out_at is None


def test_legacy_confirm_flag_cannot_switch_courts_to_join(client):
    host = _register(client, 'remote-presence-host')
    joiner = _register(client, 'remote-presence-joiner')
    court_id = _court_id(client)
    other_court_id = _other_court_id()
    _check_in(client, host, court_id)
    _check_in(client, joiner, other_court_id, looking=True)
    created = client.post('/api/games/rally', json=_rally_payload(
        court_id, 'remote-presence-host-attempt', game_type='casual',
        max_players=2,
    ), headers=_headers(host))
    assert created.status_code == 201, created.get_json()

    joined = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'remote-presence-joiner-attempt',
        game_type='casual',
        max_players=2,
        confirm_court_presence=True,
    ), headers=_headers(joiner))

    assert joined.status_code == 409, joined.get_json()
    assert joined.get_json() == {
        'error': 'active_checkin_court_mismatch',
        'checked_in_court_id': other_court_id,
        'requested_court_id': court_id,
    }
    active = CheckIn.query.filter_by(user_id=joiner['user']['id']).filter(
        CheckIn.checked_out_at.is_(None),
    ).one()
    assert active.court_id == other_court_id
    assert len(created.get_json()['game']['players']) == 1


def test_legacy_confirm_flag_cannot_revive_a_stale_checkin(client):
    player = _register(client, 'remote-presence-stale')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    checkin = CheckIn.query.filter_by(
        user_id=player['user']['id'], checked_out_at=None,
    ).one()
    stale_ping = utcnow() - timedelta(hours=3)
    checkin.last_presence_ping_at = stale_ping
    db.session.commit()

    response = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'remote-presence-stale-attempt',
        confirm_court_presence=True,
    ), headers=_headers(player))

    assert response.status_code == 409, response.get_json()
    assert response.get_json() == {'error': 'active_checkin_required'}
    db.session.refresh(checkin)
    assert checkin.last_presence_ping_at == stale_ping
    assert Game.query.count() == 0


def test_legacy_confirm_replay_never_recreates_a_checked_out_presence(client):
    player = _register(client, 'remote-presence-replay')
    anchor = _register(client, 'remote-presence-replay-anchor')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    _check_in(client, anchor, court_id)
    payload = _rally_payload(
        court_id,
        'remote-presence-replay-attempt',
        confirm_court_presence=True,
    )
    created = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert created.status_code == 201, created.get_json()
    joined = client.post('/api/games/rally', json=_rally_payload(
        court_id, 'remote-presence-replay-anchor-attempt',
    ), headers=_headers(anchor))
    assert joined.status_code == 200, joined.get_json()
    checked_out = client.post('/api/checkout', headers=_headers(player))
    assert checked_out.status_code == 200, checked_out.get_json()

    replay = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )

    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['presence']['checked_in'] is False
    assert replay.get_json()['presence_confirmed'] is False
    assert CheckIn.query.filter_by(
        user_id=player['user']['id'], checked_out_at=None,
    ).count() == 0


def test_confirm_presence_failure_keeps_old_presence_pulse_and_arrival(client):
    player = _register(client, 'atomic-presence-failure')
    court_id = _court_id(client)
    other_court_id = _other_court_id()
    _check_in(client, player, other_court_id, looking=True)
    now = utcnow()
    reserved_game = Game(
        court_id=other_court_id,
        creator_id=player['user']['id'],
        scheduled_at=now,
        game_type='casual',
        visibility='open',
        recurrence='none',
        max_players=4,
        preferred_level='any',
        is_instant=False,
    )
    db.session.add(reserved_game)
    db.session.flush()
    arrival = GameArrivalIntent(
        game_id=reserved_game.id,
        user_id=player['user']['id'],
        eta_minutes=10,
        declared_at=now,
        arrives_at=now + timedelta(minutes=10),
        expires_at=now + timedelta(minutes=15),
        active=True,
        client_attempt_id='atomic-presence-arrival',
        client_attempt_fingerprint='a' * 64,
    )
    pulse = PlayAvailabilityPulse(
        user_id=player['user']['id'],
        court_id=other_court_id,
        declared_at=now,
        expires_at=now + timedelta(minutes=30),
        active=True,
        client_attempt_id='atomic-presence-pulse',
        client_attempt_fingerprint='b' * 64,
    )
    db.session.add_all([arrival, pulse])
    db.session.commit()

    response = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'atomic-presence-failure-attempt',
        confirm_court_presence=True,
    ), headers=_headers(player))

    assert response.status_code == 409
    assert response.get_json() == {
        'error': 'active_checkin_court_mismatch',
        'checked_in_court_id': other_court_id,
        'requested_court_id': court_id,
    }
    active = CheckIn.query.filter_by(user_id=player['user']['id']).filter(
        CheckIn.checked_out_at.is_(None),
    ).one()
    assert active.court_id == other_court_id
    assert db.session.get(GameArrivalIntent, arrival.id).active is True
    assert db.session.get(PlayAvailabilityPulse, pulse.id).active is True
    assert Game.query.filter_by(court_id=court_id, is_instant=True).count() == 0


def test_confirm_presence_active_rally_conflict_keeps_the_original_court(client):
    player = _register(client, 'atomic-presence-existing-rally')
    target_court_id = _court_id(client)
    original_court_id = _other_court_id()
    _check_in(client, player, original_court_id)
    original = client.post('/api/games/rally', json=_rally_payload(
        original_court_id,
        'atomic-presence-existing-rally-original',
        confirm_court_presence=True,
    ), headers=_headers(player))
    assert original.status_code == 201, original.get_json()

    conflict = client.post('/api/games/rally', json=_rally_payload(
        target_court_id,
        'atomic-presence-existing-rally-conflict',
        confirm_court_presence=True,
    ), headers=_headers(player))

    assert conflict.status_code == 409, conflict.get_json()
    assert conflict.get_json() == {
        'error': 'active_checkin_court_mismatch',
        'checked_in_court_id': original_court_id,
        'requested_court_id': target_court_id,
    }
    active = CheckIn.query.filter_by(
        user_id=player['user']['id'], checked_out_at=None,
    ).one()
    original_game = db.session.get(Game, original.get_json()['game']['id'])
    assert active.court_id == original_court_id
    assert original_game.status == 'upcoming'
    assert original_game.assembly_closed_at is None
    assert Game.query.filter_by(
        court_id=target_court_id, is_instant=True,
    ).count() == 0


def test_closed_court_recovers_existing_rally_but_rejects_new_presence(client):
    player = _register(client, 'closed-court-recovery')
    roster_anchor = _register(client, 'closed-court-roster-anchor')
    newcomer = _register(client, 'closed-court-newcomer')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    _check_in(client, roster_anchor, court_id)
    original_payload = _rally_payload(
        court_id,
        'closed-court-recovery-original',
        game_type='casual',
        max_players=4,
        confirm_court_presence=True,
    )
    created = client.post(
        '/api/games/rally', json=original_payload, headers=_headers(player),
    )
    assert created.status_code == 201, created.get_json()
    game = created.get_json()['game']
    joined = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'closed-court-roster-anchor-attempt',
        game_type='casual',
        max_players=4,
        confirm_court_presence=True,
    ), headers=_headers(roster_anchor))
    assert joined.status_code == 200, joined.get_json()
    assert joined.get_json()['outcome'] == 'joined'
    checkin = CheckIn.query.filter_by(user_id=player['user']['id']).one()
    checked_in_at = checkin.checked_in_at
    last_ping_at = checkin.last_presence_ping_at
    court = db.session.get(Court, court_id)
    court.closed = True
    db.session.commit()

    exact = client.post(
        '/api/games/rally', json=original_payload, headers=_headers(player),
    )
    assert exact.status_code == 200, exact.get_json()
    assert exact.get_json()['outcome'] == 'existing'
    assert exact.get_json()['game']['id'] == game['id']
    assert exact.get_json()['presence_confirmed'] is True
    db.session.refresh(checkin)
    assert checkin.checked_in_at == checked_in_at
    assert checkin.last_presence_ping_at == last_ping_at

    # The roster remains live through the second player, but this actor has
    # since moved. Recover the game without falsely claiming the now-closed
    # court accepted the requested presence switch.
    other_court_id = _other_court_id()
    _check_in(client, player, other_court_id)
    partial_recovery = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'closed-court-recovery-new-attempt',
        game_type='casual',
        max_players=4,
        confirm_court_presence=True,
    ), headers=_headers(player))
    assert partial_recovery.status_code == 409, partial_recovery.get_json()
    assert partial_recovery.get_json() == {
        'error': 'active_checkin_court_mismatch',
        'checked_in_court_id': other_court_id,
        'requested_court_id': court_id,
    }

    conflict = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'closed-court-recovery-different-configuration',
        game_type='ranked',
        max_players=2,
        confirm_court_presence=True,
    ), headers=_headers(player))
    assert conflict.status_code == 409, conflict.get_json()
    assert conflict.get_json() == {
        'error': 'active_checkin_court_mismatch',
        'checked_in_court_id': other_court_id,
        'requested_court_id': court_id,
    }

    new_start = client.post('/api/games/rally', json=_rally_payload(
        court_id,
        'closed-court-new-attempt',
        confirm_court_presence=True,
    ), headers=_headers(newcomer))
    assert new_start.status_code == 409
    assert new_start.get_json() == {'error': 'active_checkin_required'}
    assert CheckIn.query.filter_by(
        user_id=newcomer['user']['id'], checked_out_at=None,
    ).count() == 0


@pytest.mark.parametrize('value', [1, 'true', None])
def test_confirm_presence_must_be_boolean(client, value):
    player = _register(client, f'invalid-confirm-{value}')
    response = client.post('/api/games/rally', json=_rally_payload(
        _court_id(client),
        f'invalid-confirm-{value}-attempt',
        confirm_court_presence=value,
    ), headers=_headers(player))
    assert response.status_code == 400
    assert response.get_json() == {'error': 'invalid_confirm_court_presence'}


@pytest.mark.parametrize('legacy_version', ['v1', 'v2'])
def test_legacy_default_rally_fingerprints_still_replay(client, legacy_version):
    player = _register(client, f'legacy-{legacy_version}')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    payload = _rally_payload(
        court_id, f'legacy-{legacy_version}-configured-attempt',
    )
    created = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert created.status_code == 201, created.get_json()

    scheduled_at = _parse_scheduled_at(payload['scheduled_at'])
    legacy = {
        'operation': f'instant_rally_{legacy_version}',
        'scheduled_at': scheduled_at,
    }
    if legacy_version == 'v2':
        legacy['court_id'] = court_id
    row = db.session.get(Game, created.get_json()['game']['id'])
    row.client_attempt_fingerprint = _game_attempt_fingerprint(legacy)
    db.session.commit()

    replay = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['outcome'] == 'existing'
    assert replay.get_json()['game']['id'] == row.id


def test_legacy_fingerprint_cannot_replay_a_nondefault_configuration(client):
    player = _register(client, 'legacy-nondefault')
    court_id = _court_id(client)
    _check_in(client, player, court_id)
    payload = _rally_payload(
        court_id,
        'legacy-nondefault-attempt',
        game_type='ranked',
        max_players=2,
    )
    created = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert created.status_code == 201, created.get_json()

    row = db.session.get(Game, created.get_json()['game']['id'])
    row.client_attempt_fingerprint = _game_attempt_fingerprint({
        'operation': 'instant_rally_v2',
        'court_id': court_id,
        'scheduled_at': _parse_scheduled_at(payload['scheduled_at']),
    })
    db.session.commit()

    replay = client.post(
        '/api/games/rally', json=payload, headers=_headers(player),
    )
    assert replay.status_code == 409
    assert replay.get_json() == {'error': 'client_attempt_id_conflict'}
