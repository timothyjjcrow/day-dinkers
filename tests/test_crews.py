"""Focused API contract tests for private, durable shared Crews."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app import (
    CREW_REFERENCE_FOREIGN_KEYS,
    _missing_crew_reference_foreign_keys,
    create_app,
    db,
)
from backend.models import Court, Game, GamePlayer, utcnow


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Crew Court',
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


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth_headers(player):
    return {'Authorization': f"Bearer {player['token']}"}


def court_id(client):
    return client.get('/api/courts?q=crew').get_json()['items'][0]['id']


def completed_game(client, owner, court, team1, team2, *, no_shows=()):
    """Create a completed game, including legacy oversized score fixtures."""
    participants = {
        player['user']['id']: player
        for player in (*team1, *team2, *no_shows)
    }
    owner_id = owner['user']['id']
    assert owner_id in participants

    created = client.post('/api/games', json={
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(hours=1)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': len(participants),
    }, headers=auth_headers(owner))
    assert created.status_code == 201, created.get_json()
    game = created.get_json()

    for user_id, player in participants.items():
        if user_id == owner_id:
            continue
        joined = client.post(
            f"/api/games/{game['id']}/join",
            headers=auth_headers(player),
        )
        assert joined.status_code == 200, joined.get_json()

    score_size = min(len(team1), len(team2), 2)
    assert score_size in (1, 2)
    if len(participants) > 4:
        # Historical compatibility fixture: old releases allowed a scored
        # result on a >4-capacity row. New writes must use session wrap-up,
        # so construct the legacy row directly instead of weakening the API.
        row = db.session.get(Game, game['id'])
        row.status = 'completed'
        row.completed_at = utcnow()
        row.score_team1 = 11
        row.score_team2 = 7
        row.score_submitted_by_id = owner_id
        for player in row.players:
            if player.user_id in {item['user']['id'] for item in team1[:score_size]}:
                player.team = 1
            elif player.user_id in {item['user']['id'] for item in team2[:score_size]}:
                player.team = 2
        db.session.commit()
        scored = client.get(
            f"/api/games/{game['id']}", headers=auth_headers(owner),
        )
    else:
        scored = client.post(f"/api/games/{game['id']}/complete", json={
            'team1': [player['user']['id'] for player in team1[:score_size]],
            'team2': [player['user']['id'] for player in team2[:score_size]],
            'score_team1': 11,
            'score_team2': 7,
        }, headers=auth_headers(owner))
    assert scored.status_code == 200, scored.get_json()
    assert scored.get_json()['status'] == 'completed'

    # Some Crew tests intentionally exercise historical rows written before
    # score entry enforced 1v1/2v2. Keep that legacy-data coverage without
    # asking the public endpoint to create a new non-pickleball matchup.
    if len(team1) != score_size or len(team2) != score_size:
        for team_number, players in ((1, team1), (2, team2)):
            for player in players:
                row = GamePlayer.query.filter_by(
                    game_id=game['id'], user_id=player['user']['id'],
                ).one()
                row.team = team_number
        db.session.commit()
        refreshed = client.get(
            f"/api/games/{game['id']}", headers=auth_headers(owner),
        )
        assert refreshed.status_code == 200, refreshed.get_json()
        return refreshed.get_json()
    return scored.get_json()


def create_crew(client, game_id, owner):
    response = client.post(
        f'/api/games/{game_id}/crew',
        headers=auth_headers(owner),
    )
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    assert payload['created'] is True
    return payload


def accept_crew(client, crew_id, player):
    response = client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True},
        headers=auth_headers(player),
    )
    assert response.status_code == 200, response.get_json()


def crew_detail(client, crew_id, player):
    response = client.get(
        f'/api/crews/{crew_id}',
        headers=auth_headers(player),
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def member_ids(crew):
    return {member['id'] for member in crew['members']}


class _CrewForeignKeyInspector:
    """Small reflection double for the PostgreSQL additive-FK planner."""

    default_schema_name = 'picklepals'

    def __init__(self):
        self.foreign_keys = {
            table: [] for table, _, _ in CREW_REFERENCE_FOREIGN_KEYS
        }

    def get_table_names(self):
        return ['crew', *self.foreign_keys]

    def get_columns(self, table):
        local_column = next(
            column
            for required_table, column, _ in CREW_REFERENCE_FOREIGN_KEYS
            if required_table == table
        )
        return [{'name': 'id'}, {'name': local_column}]

    def get_foreign_keys(self, table):
        return self.foreign_keys[table]


def _reflected_crew_fk(table, column, name):
    return {
        'name': name,
        'constrained_columns': [column],
        'referred_schema': 'picklepals',
        'referred_table': 'crew',
        'referred_columns': ['id'],
    }


def test_additive_crew_fk_planner_is_idempotent_and_rejects_name_collision():
    inspector = _CrewForeignKeyInspector()
    assert _missing_crew_reference_foreign_keys(inspector) == list(
        CREW_REFERENCE_FOREIGN_KEYS
    )

    for table, column, name in CREW_REFERENCE_FOREIGN_KEYS:
        inspector.foreign_keys[table].append(
            _reflected_crew_fk(table, column, name)
        )
    assert _missing_crew_reference_foreign_keys(inspector) == []

    table, _, name = CREW_REFERENCE_FOREIGN_KEYS[0]
    inspector.foreign_keys[table][0]['constrained_columns'] = ['sender_id']
    with pytest.raises(RuntimeError, match=f'{name} exists with the wrong shape'):
        _missing_crew_reference_foreign_keys(inspector)


def test_canonical_user_lock_is_ordered_for_update_and_refreshes_identity_map(app):
    from sqlalchemy.dialects import postgresql

    from backend.routes.auth import _users_for_update_query

    query = _users_for_update_query([9, 2, 9])
    sql = ' '.join(str(query.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={'literal_binds': True},
    )).split())

    assert 'WHERE "user".id IN (2, 9)' in sql
    assert 'ORDER BY "user".id ASC FOR UPDATE' in sql
    assert query.get_execution_options()['populate_existing'] is True


def test_account_deletion_locks_verified_user_before_crew_reconciliation(
        client, monkeypatch):
    from backend.routes import auth as auth_routes

    player = register(client, 'lock-delete@example.com', 'Delete Me')
    headers = auth_headers(player)
    events = []
    real_lock = auth_routes._lock_users_for_update
    real_reconcile = auth_routes._reconcile_crews_for_account_deletion

    def recording_lock(user_ids):
        events.append(('user_lock', tuple(user_ids)))
        return real_lock(user_ids)

    def recording_reconcile(user_id, affected_crew_ids):
        events.append((
            'crew_reconcile', user_id, tuple(sorted(affected_crew_ids)),
        ))
        return real_reconcile(user_id, affected_crew_ids)

    monkeypatch.setattr(auth_routes, '_lock_users_for_update', recording_lock)
    monkeypatch.setattr(
        auth_routes,
        '_reconcile_crews_for_account_deletion',
        recording_reconcile,
    )

    wrong = client.delete(
        '/api/me', json={'password': 'wrong'}, headers=headers,
    )
    assert wrong.status_code == 403
    assert events == []

    deleted = client.delete(
        '/api/me', json={'password': 'secret123'}, headers=headers,
    )
    assert deleted.status_code == 200, deleted.get_json()
    assert events == [
        ('user_lock', (player['user']['id'],)),
        ('crew_reconcile', player['user']['id'], ()),
    ]


def test_account_deletion_lock_snapshot_includes_full_crew_consent_closure(
        client):
    from backend.routes.auth import _account_deletion_crew_lock_snapshot

    owner = register(client, 'snapshot-owner@example.com', 'Owner')
    member = register(client, 'snapshot-member@example.com', 'Member')
    pending = register(client, 'snapshot-pending@example.com', 'Pending')
    source = completed_game(
        client, owner, court_id(client), [owner, member], [pending],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, member)

    crew_ids, user_ids = _account_deletion_crew_lock_snapshot(
        owner['user']['id'],
    )

    assert crew_ids == frozenset({crew_id})
    assert user_ids == frozenset({
        owner['user']['id'], member['user']['id'], pending['user']['id'],
    })


def test_account_deletion_retries_an_expanded_crew_user_snapshot(
        client, monkeypatch):
    from backend.models import User
    from backend.routes import auth as auth_routes

    deleting = register(client, 'snapshot-race@example.com', 'Deleting')
    newly_related = register(client, 'snapshot-new@example.com', 'New Player')
    deleting_id = deleting['user']['id']
    new_id = newly_related['user']['id']
    snapshots = iter((
        (frozenset(), frozenset({deleting_id})),
        (frozenset({9876}), frozenset({deleting_id, new_id})),
        (frozenset({9876}), frozenset({deleting_id, new_id})),
        (frozenset({9876}), frozenset({deleting_id, new_id})),
    ))
    lock_sets = []
    real_lock = auth_routes._lock_users_for_update

    def changing_snapshot(_user_id):
        return next(snapshots)

    def recording_lock(user_ids):
        lock_sets.append(tuple(sorted(user_ids)))
        return real_lock(user_ids)

    monkeypatch.setattr(
        auth_routes,
        '_account_deletion_crew_lock_snapshot',
        changing_snapshot,
    )
    monkeypatch.setattr(auth_routes, '_lock_users_for_update', recording_lock)

    response = client.delete(
        '/api/me', json={'password': 'secret123'},
        headers=auth_headers(deleting),
    )

    assert response.status_code == 200, response.get_json()
    assert lock_sets == [(deleting_id,), tuple(sorted((deleting_id, new_id)))]
    assert db.session.get(User, new_id).deleted_at is None


def test_deletion_winning_user_lock_prevents_stale_owner_from_creating_crew(
        client, app, monkeypatch):
    from backend.models import Crew
    from backend.routes import crews as crew_routes

    owner = register(client, 'race-owner@example.com', 'Race Owner')
    opponent = register(client, 'race-opponent@example.com', 'Opponent')
    source = completed_game(
        client, owner, court_id(client), [owner], [opponent],
    )
    real_lock = crew_routes._lock_users_for_update

    def deletion_wins(user_ids):
        locked = real_lock(user_ids)
        next(
            user for user in locked
            if user.id == owner['user']['id']
        ).deleted_at = utcnow()
        return locked

    monkeypatch.setattr(crew_routes, '_lock_users_for_update', deletion_wins)
    response = client.post(
        f"/api/games/{source['id']}/crew",
        headers=auth_headers(owner),
    )

    assert response.status_code == 401
    assert response.get_json()['error'] == 'authentication_required'
    db.session.rollback()
    assert Crew.query.count() == 0


def test_completed_game_crew_is_filtered_durable_and_retry_safe(client, app):
    owner = register(client, 'filter-owner@example.com', 'Owner')
    teammate = register(client, 'filter-teammate@example.com', 'Teammate')
    opponent = register(client, 'filter-opponent@example.com', 'Opponent')
    blocked = register(client, 'filter-blocked@example.com', 'Blocked')
    deleted = register(client, 'filter-deleted@example.com', 'Deleted')
    no_show = register(client, 'filter-noshow@example.com', 'No Show')
    outsider = register(client, 'filter-outsider@example.com', 'Outsider')
    game = completed_game(
        client,
        owner,
        court_id(client),
        [owner, teammate, deleted],
        [opponent, blocked],
        no_shows=[no_show],
    )

    # Eligibility is evaluated when the Crew is created, not frozen from the
    # historical RSVP list. Deleted accounts and blocks in either direction
    # are excluded even though those users were assigned to the score.
    assert client.post(
        f"/api/users/{owner['user']['id']}/block",
        headers=auth_headers(blocked),
    ).status_code == 200
    assert client.delete(
        '/api/me',
        json={'password': 'secret123'},
        headers=auth_headers(deleted),
    ).status_code == 200

    for forbidden_actor in (no_show, outsider):
        forbidden = client.post(
            f"/api/games/{game['id']}/crew",
            headers=auth_headers(forbidden_actor),
        )
        assert forbidden.status_code == 404

    first = create_crew(client, game['id'], owner)
    crew = first['crew']
    crew_id = crew['id']
    assert crew['owner_id'] == owner['user']['id']
    assert member_ids(crew) == {owner['user']['id']}
    assert crew['pending_count'] == 2
    assert first['invited_count'] == 2

    # Rows, rather than transient notifications, are the invitation source of
    # truth. The owner is implicit and is not duplicated as a CrewMember.
    from backend.models import Crew, CrewInvite, CrewMember

    assert Crew.query.count() == 1
    assert Crew.query.one().source_game_id == game['id']
    assert CrewMember.query.filter_by(crew_id=crew_id).count() == 0
    invitations = CrewInvite.query.filter_by(crew_id=crew_id).all()
    assert {
        (invite.invitee_id, invite.invited_by_id, invite.status)
        for invite in invitations
    } == {
        (teammate['user']['id'], owner['user']['id'], 'pending'),
        (opponent['user']['id'], owner['user']['id'], 'pending'),
    }

    replay = client.post(
        f"/api/games/{game['id']}/crew",
        headers=auth_headers(owner),
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['created'] is False
    assert replay.get_json()['crew']['id'] == crew_id
    assert Crew.query.count() == 1
    assert CrewInvite.query.filter_by(crew_id=crew_id).count() == 2
    assert CrewMember.query.filter_by(crew_id=crew_id).count() == 0

    # Force subsequent requests to reload from the database: both accepted
    # Crews and pending invitations must survive beyond the create session.
    db.session.remove()
    owner_mine = client.get(
        '/api/crews/mine', headers=auth_headers(owner),
    ).get_json()
    assert [item['id'] for item in owner_mine['items']] == [crew_id]
    assert owner_mine['invitations'] == []
    for invitee in (teammate, opponent):
        mine = client.get(
            '/api/crews/mine', headers=auth_headers(invitee),
        ).get_json()
        assert mine['items'] == []
        assert [item['id'] for item in mine['invitations']] == [crew_id]
    for excluded in (blocked, no_show, outsider):
        mine = client.get(
            '/api/crews/mine', headers=auth_headers(excluded),
        ).get_json()
        assert mine == {'items': [], 'invitations': []}


def test_crew_responses_are_invitee_only_and_acceptance_versions_the_roster(client):
    owner = register(client, 'respond-owner@example.com', 'Owner')
    alice = register(client, 'respond-alice@example.com', 'Alice')
    ben = register(client, 'respond-ben@example.com', 'Ben')
    outsider = register(client, 'respond-outsider@example.com', 'Outsider')
    game = completed_game(
        client, owner, court_id(client), [owner, alice], [ben],
    )
    crew = create_crew(client, game['id'], owner)['crew']
    crew_id = crew['id']
    initial_version = crew['roster_version']

    # Pending invitations do not grant detail access, and knowing a Crew id is
    # not enough to answer somebody else's invitation.
    for hidden_from in (alice, ben, outsider):
        assert client.get(
            f'/api/crews/{crew_id}', headers=auth_headers(hidden_from),
        ).status_code == 404
    for wrong_actor in (owner, outsider):
        assert client.post(
            f'/api/crews/{crew_id}/respond',
            json={'accept': True},
            headers=auth_headers(wrong_actor),
        ).status_code == 404

    accept_crew(client, crew_id, alice)
    accepted = crew_detail(client, crew_id, alice)
    assert accepted['roster_version'] == initial_version + 1
    assert accepted['owner_id'] == owner['user']['id']
    assert member_ids(accepted) == {
        owner['user']['id'], alice['user']['id'],
    }

    # A response can be lost too. Replaying the same acceptance returns the
    # joined Crew without inserting another member or advancing the version.
    accept_replay = client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True},
        headers=auth_headers(alice),
    )
    assert accept_replay.status_code == 200, accept_replay.get_json()
    assert accept_replay.get_json()['joined'] is True
    assert accept_replay.get_json()['crew']['roster_version'] == (
        accepted['roster_version']
    )
    assert crew_detail(
        client, crew_id, owner,
    )['roster_version'] == accepted['roster_version']

    # Declining resolves the durable invitation but does not change the
    # accepted roster, so it cannot advance roster_version.
    declined = client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': False},
        headers=auth_headers(ben),
    )
    assert declined.status_code == 200, declined.get_json()
    after_decline = crew_detail(client, crew_id, owner)
    assert after_decline['roster_version'] == accepted['roster_version']
    assert member_ids(after_decline) == member_ids(accepted)

    alice_mine = client.get(
        '/api/crews/mine', headers=auth_headers(alice),
    ).get_json()
    assert [item['id'] for item in alice_mine['items']] == [crew_id]
    assert alice_mine['invitations'] == []
    ben_mine = client.get(
        '/api/crews/mine', headers=auth_headers(ben),
    ).get_json()
    assert ben_mine == {'items': [], 'invitations': []}

    from backend.models import CrewInvite, CrewMember

    assert CrewMember.query.filter_by(
        crew_id=crew_id, user_id=alice['user']['id'],
    ).count() == 1
    assert CrewMember.query.filter_by(crew_id=crew_id).count() == 1
    assert CrewInvite.query.filter_by(
        crew_id=crew_id, invitee_id=alice['user']['id'], status='accepted',
    ).count() == 1
    assert CrewInvite.query.filter_by(
        crew_id=crew_id, invitee_id=ben['user']['id'], status='declined',
    ).count() == 1


def test_crew_chat_is_accepted_only_and_message_retries_do_not_duplicate(client):
    owner = register(client, 'chat-owner@example.com', 'Owner')
    member = register(client, 'chat-member@example.com', 'Member')
    pending = register(client, 'chat-pending@example.com', 'Pending')
    outsider = register(client, 'chat-outsider@example.com', 'Outsider')
    game = completed_game(
        client, owner, court_id(client), [owner, member], [pending],
    )
    crew_id = create_crew(client, game['id'], owner)['crew']['id']
    accept_crew(client, crew_id, member)

    assert client.get(f'/api/crews/{crew_id}/chat').status_code == 401
    for hidden_from in (pending, outsider):
        hidden_headers = auth_headers(hidden_from)
        assert client.get(
            f'/api/crews/{crew_id}/chat', headers=hidden_headers,
        ).status_code == 404
        assert client.post(
            f'/api/crews/{crew_id}/chat',
            json={'body': 'This must stay private'},
            headers=hidden_headers,
        ).status_code == 404

    attempt_id = 'crew-message-550e8400-e29b-41d4-a716'
    payload = {
        'body': 'Same four tomorrow?',
        'client_attempt_id': attempt_id,
    }
    first = client.post(
        f'/api/crews/{crew_id}/chat',
        json=payload,
        headers=auth_headers(owner),
    )
    assert first.status_code == 201, first.get_json()
    replay = client.post(
        f'/api/crews/{crew_id}/chat',
        json=payload,
        headers=auth_headers(owner),
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['id'] == first.get_json()['id']
    assert replay.get_json()['client_attempt_id'] == attempt_id

    thread = client.get(
        f'/api/crews/{crew_id}/chat', headers=auth_headers(member),
    )
    assert thread.status_code == 200, thread.get_json()
    assert [message['body'] for message in thread.get_json()['items']] == [
        'Same four tomorrow?',
    ]

    from backend.models import Message, MessageSendAttempt

    assert Message.query.filter_by(crew_id=crew_id).count() == 1
    assert MessageSendAttempt.query.filter_by(
        sender_id=owner['user']['id'], client_attempt_id=attempt_id,
    ).count() == 1

    reply = client.post(
        f'/api/crews/{crew_id}/chat',
        json={'body': 'I am in.'},
        headers=auth_headers(member),
    )
    assert reply.status_code == 201, reply.get_json()
    owner_thread = client.get(
        f'/api/crews/{crew_id}/chat', headers=auth_headers(owner),
    ).get_json()
    assert [message['body'] for message in owner_thread['items']] == [
        'Same four tomorrow?', 'I am in.',
    ]


def test_blocked_crew_sender_cannot_leak_a_cached_message_image(client):
    owner = register(client, 'image-owner@example.com', 'Owner')
    sender = register(client, 'image-sender@example.com', 'Sender')
    source = completed_game(
        client, owner, court_id(client), [owner], [sender],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, sender)
    image = 'data:image/jpeg;base64,' + 'B' * 100
    sent = client.post(
        f'/api/crews/{crew_id}/chat',
        json={'body': 'before the block', 'image': image},
        headers=auth_headers(sender),
    )
    assert sent.status_code == 201, sent.get_json()
    message_id = sent.get_json()['id']
    assert client.get(
        f'/api/messages/{message_id}/image', headers=auth_headers(owner),
    ).status_code == 200

    blocked = client.post(
        f"/api/users/{sender['user']['id']}/block",
        headers=auth_headers(owner),
    )
    assert blocked.status_code == 200, blocked.get_json()
    assert client.get(
        f'/api/crews/{crew_id}/chat', headers=auth_headers(owner),
    ).get_json()['items'] == []
    assert client.get(
        f'/api/messages/{message_id}/image', headers=auth_headers(owner),
    ).status_code == 403


def test_crew_game_creation_is_private_atomic_and_replay_precedes_version_check(
        client, app):
    owner = register(client, 'game-owner@example.com', 'Owner')
    alice = register(client, 'game-alice@example.com', 'Alice')
    ben = register(client, 'game-ben@example.com', 'Ben')
    cam = register(client, 'game-cam@example.com', 'Cam')
    outsider = register(client, 'game-outsider@example.com', 'Outsider')
    court = court_id(client)
    source = completed_game(
        client, owner, court, [owner, alice], [ben, cam],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, alice)
    accept_crew(client, crew_id, ben)
    roster = crew_detail(client, crew_id, owner)
    expected_version = roster['roster_version']
    assert member_ids(roster) == {
        owner['user']['id'], alice['user']['id'], ben['user']['id'],
    }

    attempt_id = 'crew-game-550e8400-e29b-41d4-a716'
    payload = {
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'game_type': 'casual',
        # Group-only plans are an exact immutable Crew roster snapshot.
        'visibility': 'private',
        'crew_id': crew_id,
        'expected_crew_version': expected_version,
        'client_attempt_id': attempt_id,
    }
    created = client.post('/api/games', json=payload, headers=auth_headers(owner))
    assert created.status_code == 201, created.get_json()
    created_game = created.get_json()
    assert created_game['visibility'] == 'private'
    assert created_game['crew_id'] == crew_id

    from backend.models import Game, GameInvite, GamePlayer, Notification

    stored = db.session.get(Game, created_game['id'])
    assert stored.crew_id == crew_id
    assert {
        invite.user_id
        for invite in GameInvite.query.filter_by(game_id=stored.id).all()
    } == {alice['user']['id'], ben['user']['id']}
    assert {
        player.user_id
        for player in GamePlayer.query.filter_by(game_id=stored.id).all()
    } == {owner['user']['id']}
    for invitee in (alice, ben):
        assert client.get(
            f'/api/games/{stored.id}', headers=auth_headers(invitee),
        ).status_code == 200
    for hidden_from in (cam, outsider):
        assert client.get(
            f'/api/games/{stored.id}', headers=auth_headers(hidden_from),
        ).status_code == 404

    # Cam accepting changes the authoritative roster after the game response
    # was committed. An exact retry must recover that game before looking at
    # the now-stale expected_crew_version.
    accept_crew(client, crew_id, cam)
    current_version = crew_detail(client, crew_id, owner)['roster_version']
    assert current_version == expected_version + 1
    counts_after_create = {
        'games': Game.query.count(),
        'invites': GameInvite.query.count(),
        'players': GamePlayer.query.count(),
        'notifications': Notification.query.filter_by(
            kind='game_invite_direct',
        ).count(),
    }
    replay = client.post('/api/games', json=payload, headers=auth_headers(owner))
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['id'] == stored.id
    assert {
        'games': Game.query.count(),
        'invites': GameInvite.query.count(),
        'players': GamePlayer.query.count(),
        'notifications': Notification.query.filter_by(
            kind='game_invite_direct',
        ).count(),
    } == counts_after_create

    # A distinct attempt carrying the old version is rejected atomically: no
    # game, creator membership, direct invite, notification, or retry row can
    # be left behind.
    stale_payload = {
        **payload,
        'scheduled_at': (utcnow() + timedelta(days=3)).isoformat() + 'Z',
        'client_attempt_id': 'crew-game-stale-550e8400-e29b-41d4-a716',
    }
    stale = client.post(
        '/api/games', json=stale_payload, headers=auth_headers(owner),
    )
    assert stale.status_code == 409, stale.get_json()
    assert stale.get_json()['error'] == 'crew_changed'
    assert {
        'games': Game.query.count(),
        'invites': GameInvite.query.count(),
        'players': GamePlayer.query.count(),
        'notifications': Notification.query.filter_by(
            kind='game_invite_direct',
        ).count(),
    } == counts_after_create
    assert Game.query.filter_by(
        creator_id=owner['user']['id'],
        client_attempt_id=stale_payload['client_attempt_id'],
    ).count() == 0


def test_crew_session_accepts_selected_members_capacity_and_weekly_recurrence(client):
    owner = register(client, 'selected-owner@example.com', 'Owner')
    alice = register(client, 'selected-alice@example.com', 'Alice')
    ben = register(client, 'selected-ben@example.com', 'Ben')
    pending = register(client, 'selected-pending@example.com', 'Pending')
    court = court_id(client)
    source = completed_game(
        client, owner, court, [owner, alice], [ben, pending],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, alice)
    accept_crew(client, crew_id, ben)
    crew = crew_detail(client, crew_id, owner)
    scheduled = (utcnow() + timedelta(days=8)).replace(
        hour=18, minute=0, second=0, microsecond=0,
    )
    weekday = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][
        scheduled.weekday()
    ]

    created = client.post('/api/games', json={
        'court_id': court,
        'scheduled_at': scheduled.isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'private',
        'max_players': 4,
        'invite_user_ids': [alice['user']['id']],
        'require_all_invitees': True,
        'crew_id': crew_id,
        'expected_crew_version': crew['roster_version'],
        'recurrence': 'weekly',
        'recurrence_timezone': 'UTC',
        'recurrence_weekdays': [weekday],
        'client_attempt_id': 'crew-selected-weekly-550e8400-e29b-41d4-a716',
    }, headers=auth_headers(owner))
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body['crew_id'] == crew_id
    assert body['max_players'] == 4
    assert body['recurrence'] == 'weekly'
    assert body['recurrence_weekdays'] == [weekday]

    from backend.models import GameInvite

    assert {
        invite.user_id
        for invite in GameInvite.query.filter_by(game_id=body['id']).all()
    } == {alice['user']['id']}
    assert client.get(
        f"/api/games/{body['id']}", headers=auth_headers(alice),
    ).status_code == 200
    for deselected in (ben, pending):
        assert client.get(
            f"/api/games/{body['id']}", headers=auth_headers(deselected),
        ).status_code == 404

    one_time = client.patch(
        f"/api/games/{body['id']}", json={'recurrence': 'none'},
        headers=auth_headers(owner),
    )
    assert one_time.status_code == 200, one_time.get_json()
    repeated = client.patch(f"/api/games/{body['id']}", json={
        'recurrence': 'weekly',
        'recurrence_timezone': 'UTC',
        'recurrence_weekdays': [weekday],
    }, headers=auth_headers(owner))
    assert repeated.status_code == 200, repeated.get_json()
    assert repeated.get_json()['recurrence'] == 'weekly'


def test_completed_private_crew_game_stays_out_of_participant_friend_results(client):
    owner = register(client, 'results-owner@example.com', 'Owner')
    member = register(client, 'results-member@example.com', 'Member')
    outsider = register(client, 'results-outsider@example.com', 'Outsider')
    court = court_id(client)
    source = completed_game(client, owner, court, [owner], [member])
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, member)
    crew = crew_detail(client, crew_id, owner)
    linked = client.post('/api/games', json={
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(hours=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'crew_id': crew_id,
        'expected_crew_version': crew['roster_version'],
        'client_attempt_id': 'private-results-550e8400-e29b-41d4-a716',
    }, headers=auth_headers(owner))
    assert linked.status_code == 201, linked.get_json()
    game_id = linked.get_json()['id']
    assert client.post(
        f'/api/games/{game_id}/join', headers=auth_headers(member),
    ).status_code == 200
    completed = client.post(f'/api/games/{game_id}/complete', json={
        'team1': [owner['user']['id']],
        'team2': [member['user']['id']],
        'score_team1': 11,
        'score_team2': 8,
    }, headers=auth_headers(owner))
    assert completed.status_code == 200, completed.get_json()

    friendship = client.post('/api/friends/request', json={
        'user_id': member['user']['id'],
    }, headers=auth_headers(outsider)).get_json()['friendship_id']
    assert client.post(
        f'/api/friends/{friendship}/respond',
        json={'accept': True},
        headers=auth_headers(member),
    ).status_code == 200

    assert client.get(
        f'/api/games/{game_id}', headers=auth_headers(outsider),
    ).status_code == 404
    outsider_results = client.get(
        '/api/games/results?lat=33.66&lng=-117.91',
        headers=auth_headers(outsider),
    ).get_json()['items']
    assert game_id not in {item['id'] for item in outsider_results}
    owner_results = client.get(
        '/api/games/results?lat=33.66&lng=-117.91',
        headers=auth_headers(owner),
    ).get_json()['items']
    assert game_id in {item['id'] for item in owner_results}


def test_notification_read_and_clear_do_not_remove_durable_crew_invitation(client):
    owner = register(client, 'notify-owner@example.com', 'Owner')
    invitee = register(client, 'notify-invitee@example.com', 'Invitee')
    source = completed_game(
        client, owner, court_id(client), [owner], [invitee],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    invitee_headers = auth_headers(invitee)

    notifications = client.get(
        '/api/notifications', headers=invitee_headers,
    ).get_json()['items']
    crew_notification = next(
        item for item in notifications
        if item['kind'] == 'crew_invite' and item['related_crew_id'] == crew_id
    )
    assert crew_notification['read'] is False
    assert [item['id'] for item in client.get(
        '/api/crews/mine', headers=invitee_headers,
    ).get_json()['invitations']] == [crew_id]

    marked = client.post('/api/notifications/read', headers=invitee_headers)
    assert marked.status_code == 200, marked.get_json()
    marked_notifications = client.get(
        '/api/notifications', headers=invitee_headers,
    ).get_json()['items']
    assert next(
        item for item in marked_notifications
        if item['kind'] == 'crew_invite' and item['related_crew_id'] == crew_id
    )['read'] is True
    assert [item['id'] for item in client.get(
        '/api/crews/mine', headers=invitee_headers,
    ).get_json()['invitations']] == [crew_id]

    cleared = client.delete('/api/notifications', headers=invitee_headers)
    assert cleared.status_code == 200, cleared.get_json()
    assert cleared.get_json()['cleared'] >= 1
    assert client.get(
        '/api/notifications', headers=invitee_headers,
    ).get_json()['items'] == []

    # Notification rows are delivery hints only. Clearing them must leave the
    # consent row discoverable and actionable through /crews/mine.
    db.session.remove()
    mine_after_clear = client.get(
        '/api/crews/mine', headers=invitee_headers,
    ).get_json()
    assert mine_after_clear['items'] == []
    assert [item['id'] for item in mine_after_clear['invitations']] == [crew_id]

    from backend.models import CrewInvite

    assert CrewInvite.query.filter_by(
        crew_id=crew_id,
        invitee_id=invitee['user']['id'],
        status='pending',
    ).count() == 1
    accept_crew(client, crew_id, invitee)
    accepted_mine = client.get(
        '/api/crews/mine', headers=invitee_headers,
    ).get_json()
    assert [item['id'] for item in accepted_mine['items']] == [crew_id]
    assert accepted_mine['invitations'] == []


def test_account_deletion_versions_transfers_and_archives_without_breaking_history(
        client):
    owner = register(client, 'delete-owner@example.com', 'Owner')
    oldest = register(client, 'delete-oldest@example.com', 'Oldest Member')
    newer = register(client, 'delete-newer@example.com', 'Newer Member')
    departing = register(client, 'delete-departing@example.com', 'Departing Member')
    court = court_id(client)
    source = completed_game(
        client,
        owner,
        court,
        [owner, oldest],
        [newer, departing],
    )
    shared_id = create_crew(client, source['id'], owner)['crew']['id']
    # Acceptance order is the deterministic ownership succession order.
    for invitee in (oldest, newer, departing):
        accept_crew(client, shared_id, invitee)
    initial = crew_detail(client, shared_id, owner)
    assert member_ids(initial) == {
        player['user']['id'] for player in (owner, oldest, newer, departing)
    }

    # Give this Crew immutable game history before any account disappears.
    linked = client.post('/api/games', json={
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'crew_id': shared_id,
        'expected_crew_version': initial['roster_version'],
        'client_attempt_id': 'crew-delete-history-550e8400-e29b-41d4',
    }, headers=auth_headers(owner))
    assert linked.status_code == 201, linked.get_json()
    linked_game = linked.get_json()
    assert linked_game['crew_id'] == shared_id
    for invitee in (oldest, newer, departing):
        joined = client.post(
            f"/api/games/{linked_game['id']}/join",
            headers=auth_headers(invitee),
        )
        assert joined.status_code == 200, joined.get_json()
    completed = client.post(
        f"/api/games/{linked_game['id']}/complete",
        json={
            'team1': [owner['user']['id'], oldest['user']['id']],
            'team2': [newer['user']['id'], departing['user']['id']],
            'score_team1': 11,
            'score_team2': 8,
        },
        headers=auth_headers(owner),
    )
    assert completed.status_code == 200, completed.get_json()
    assert completed.get_json()['status'] == 'completed'

    # Deleting a nonowner removes exactly one accepted roster slot and advances
    # the optimistic concurrency version once.
    before_nonowner_delete = crew_detail(
        client, shared_id, owner,
    )['roster_version']
    deleted_nonowner = client.delete(
        '/api/me',
        json={'password': 'secret123'},
        headers=auth_headers(departing),
    )
    assert deleted_nonowner.status_code == 200, deleted_nonowner.get_json()
    after_nonowner_delete = crew_detail(client, shared_id, owner)
    assert after_nonowner_delete['roster_version'] == before_nonowner_delete + 1
    assert member_ids(after_nonowner_delete) == {
        owner['user']['id'], oldest['user']['id'], newer['user']['id'],
    }

    from backend.models import Crew, CrewMember, Game

    db.session.remove()
    assert CrewMember.query.filter_by(
        crew_id=shared_id, user_id=departing['user']['id'],
    ).count() == 0
    persisted_history = db.session.get(Game, linked_game['id'])
    assert persisted_history.status == 'completed'
    assert persisted_history.crew_id == shared_id

    # Deleting the owner transfers to the oldest accepted member. The promoted
    # owner becomes implicit and must not remain duplicated in CrewMember.
    before_owner_delete = db.session.get(Crew, shared_id).roster_version
    deleted_owner = client.delete(
        '/api/me',
        json={'password': 'secret123'},
        headers=auth_headers(owner),
    )
    assert deleted_owner.status_code == 200, deleted_owner.get_json()
    transferred = crew_detail(client, shared_id, oldest)
    assert transferred['owner_id'] == oldest['user']['id']
    assert transferred['roster_version'] == before_owner_delete + 1
    assert member_ids(transferred) == {
        oldest['user']['id'], newer['user']['id'],
    }

    db.session.remove()
    stored_crew = db.session.get(Crew, shared_id)
    assert stored_crew.owner_id == oldest['user']['id']
    assert {
        membership.user_id
        for membership in CrewMember.query.filter_by(crew_id=shared_id).all()
    } == {newer['user']['id']}
    assert CrewMember.query.filter_by(
        crew_id=shared_id, user_id=oldest['user']['id'],
    ).count() == 0
    persisted_history = db.session.get(Game, linked_game['id'])
    assert persisted_history.status == 'completed'
    assert persisted_history.crew_id == shared_id

    # A separate owner-only Crew has nobody to inherit it. Account deletion
    # archives the durable row instead of hard-deleting it.
    solo_owner = register(client, 'delete-solo@example.com', 'Solo Owner')
    solo_opponent = register(client, 'delete-solo-opponent@example.com', 'Opponent')
    solo_source = completed_game(
        client, solo_owner, court, [solo_owner], [solo_opponent],
    )
    solo = create_crew(client, solo_source['id'], solo_owner)['crew']
    declined = client.post(
        f"/api/crews/{solo['id']}/respond",
        json={'accept': False},
        headers=auth_headers(solo_opponent),
    )
    assert declined.status_code == 200, declined.get_json()
    solo_version = solo['roster_version']
    assert client.delete(
        '/api/me',
        json={'password': 'secret123'},
        headers=auth_headers(solo_owner),
    ).status_code == 200

    db.session.remove()
    archived = db.session.get(Crew, solo['id'])
    assert archived is not None
    assert archived.archived_at is not None
    assert archived.roster_version == solo_version + 1
    assert CrewMember.query.filter_by(crew_id=solo['id']).count() == 0
    # Archiving another Crew must not null or delete unrelated historical links.
    assert db.session.get(Game, linked_game['id']).crew_id == shared_id


def test_departures_and_blocks_revoke_old_consent_instead_of_allowing_rejoin(client):
    owner = register(client, 'rejoin-owner@example.com', 'Owner')
    leaver = register(client, 'rejoin-leaver@example.com', 'Leaver')
    stayer = register(client, 'rejoin-stayer@example.com', 'Stayer')
    removed = register(client, 'rejoin-removed@example.com', 'Removed')
    pending = register(client, 'rejoin-pending@example.com', 'Pending')
    source = completed_game(
        client, owner, court_id(client), [owner, leaver, stayer], [removed, pending],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, leaver)
    accept_crew(client, crew_id, stayer)
    accept_crew(client, crew_id, removed)

    assert client.post(
        f'/api/crews/{crew_id}/leave', headers=auth_headers(leaver),
    ).status_code == 200
    assert client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True}, headers=auth_headers(leaver),
    ).status_code == 404

    assert client.delete(
        f"/api/crews/{crew_id}/members/{removed['user']['id']}",
        headers=auth_headers(owner),
    ).status_code == 200
    assert client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True}, headers=auth_headers(removed),
    ).status_code == 404

    # A block against any accepted member revokes a still-pending invitation,
    # even when the Crew owner was the original inviter.
    assert client.post(
        f"/api/users/{pending['user']['id']}/block",
        headers=auth_headers(stayer),
    ).status_code == 200
    mine = client.get(
        '/api/crews/mine', headers=auth_headers(pending),
    ).get_json()
    assert mine['invitations'] == []
    assert client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True}, headers=auth_headers(pending),
    ).status_code == 404


def test_blocking_reconciles_shared_crews_with_owner_preserving_rule(client):
    owner = register(client, 'block-owner@example.com', 'Owner')
    alice = register(client, 'block-alice@example.com', 'Alice')
    ben = register(client, 'block-ben@example.com', 'Ben')
    cam = register(client, 'block-cam@example.com', 'Cam')
    dee = register(client, 'block-dee@example.com', 'Dee')
    source = completed_game(
        client,
        owner,
        court_id(client),
        [owner, alice, ben],
        [cam, dee],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    for invitee in (alice, ben, cam, dee):
        accept_crew(client, crew_id, invitee)

    full_roster = crew_detail(client, crew_id, owner)
    assert member_ids(full_roster) == {
        player['user']['id'] for player in (owner, alice, ben, cam, dee)
    }

    # Owner blocks member: keep the owner and remove the blocked member.
    before = full_roster['roster_version']
    assert client.post(
        f"/api/users/{alice['user']['id']}/block",
        headers=auth_headers(owner),
    ).status_code == 200
    after_owner_blocks = crew_detail(client, crew_id, owner)
    assert after_owner_blocks['roster_version'] == before + 1
    assert member_ids(after_owner_blocks) == {
        owner['user']['id'], ben['user']['id'],
        cam['user']['id'], dee['user']['id'],
    }
    assert client.get(
        f'/api/crews/{crew_id}', headers=auth_headers(alice),
    ).status_code == 404

    # Repeating the same block is idempotent and cannot version the same
    # removal twice.
    assert client.post(
        f"/api/users/{alice['user']['id']}/block",
        headers=auth_headers(owner),
    ).status_code == 200
    assert crew_detail(
        client, crew_id, owner,
    )['roster_version'] == after_owner_blocks['roster_version']

    # Member blocks owner: preserve ownership and remove the blocker.
    before = after_owner_blocks['roster_version']
    assert client.post(
        f"/api/users/{owner['user']['id']}/block",
        headers=auth_headers(ben),
    ).status_code == 200
    after_owner_is_blocked = crew_detail(client, crew_id, owner)
    assert after_owner_is_blocked['roster_version'] == before + 1
    assert member_ids(after_owner_is_blocked) == {
        owner['user']['id'], cam['user']['id'], dee['user']['id'],
    }
    assert client.get(
        f'/api/crews/{crew_id}', headers=auth_headers(ben),
    ).status_code == 404

    # Two non-owner members block: remove the blocker, leaving a deterministic
    # roster regardless of request retries or query order.
    before = after_owner_is_blocked['roster_version']
    assert client.post(
        f"/api/users/{dee['user']['id']}/block",
        headers=auth_headers(cam),
    ).status_code == 200
    final_roster = crew_detail(client, crew_id, owner)
    assert final_roster['roster_version'] == before + 1
    assert member_ids(final_roster) == {
        owner['user']['id'], dee['user']['id'],
    }
    assert client.get(
        f'/api/crews/{crew_id}', headers=auth_headers(cam),
    ).status_code == 404
    assert client.get(
        f'/api/crews/{crew_id}', headers=auth_headers(dee),
    ).status_code == 200


def test_promoted_owner_leave_and_stale_acceptance_cannot_restore_membership(client):
    owner = register(client, 'promote-owner@example.com', 'Owner')
    promoted = register(client, 'promote-first@example.com', 'First Member')
    successor = register(client, 'promote-second@example.com', 'Second Member')
    stale = register(client, 'promote-stale@example.com', 'Stale Member')
    source = completed_game(
        client,
        owner,
        court_id(client),
        [owner, promoted],
        [successor, stale],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    for invitee in (promoted, successor, stale):
        accept_crew(client, crew_id, invitee)

    # Ownership follows acceptance order. The promoted owner is represented
    # implicitly, but their original accepted invite still needs retiring
    # when they later leave the Crew.
    owner_left = client.post(
        f'/api/crews/{crew_id}/leave', headers=auth_headers(owner),
    )
    assert owner_left.status_code == 200, owner_left.get_json()
    assert crew_detail(client, crew_id, promoted)['owner_id'] == promoted['user']['id']

    promoted_left = client.post(
        f'/api/crews/{crew_id}/leave', headers=auth_headers(promoted),
    )
    assert promoted_left.status_code == 200, promoted_left.get_json()
    assert crew_detail(client, crew_id, successor)['owner_id'] == successor['user']['id']

    from backend.models import CrewInvite, CrewMember

    db.session.remove()
    promoted_invite = CrewInvite.query.filter_by(
        crew_id=crew_id, invitee_id=promoted['user']['id'],
    ).one()
    assert promoted_invite.status == 'revoked'
    assert CrewMember.query.filter_by(
        crew_id=crew_id, user_id=promoted['user']['id'],
    ).count() == 0
    assert client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True},
        headers=auth_headers(promoted),
    ).status_code == 404

    # Defense in depth for a legacy/inconsistent row: an accepted invite with
    # no matching membership is past consent, never permission to recreate a
    # CrewMember through the response endpoint.
    CrewMember.query.filter_by(
        crew_id=crew_id, user_id=stale['user']['id'],
    ).delete(synchronize_session=False)
    db.session.commit()
    stale_retry = client.post(
        f'/api/crews/{crew_id}/respond',
        json={'accept': True},
        headers=auth_headers(stale),
    )
    assert stale_retry.status_code == 404, stale_retry.get_json()
    db.session.remove()
    assert CrewMember.query.filter_by(
        crew_id=crew_id, user_id=stale['user']['id'],
    ).count() == 0
    assert CrewInvite.query.filter_by(
        crew_id=crew_id,
        invitee_id=stale['user']['id'],
        status='revoked',
    ).count() == 1


def test_linked_rematch_never_replaces_an_archived_or_missing_crew(client):
    owner = register(client, 'linked-owner@example.com', 'Owner')
    member = register(client, 'linked-member@example.com', 'Member')
    court = court_id(client)
    source = completed_game(client, owner, court, [owner], [member])
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, member)
    crew = crew_detail(client, crew_id, owner)

    created = client.post('/api/games', json={
        'court_id': court,
        'scheduled_at': (utcnow() + timedelta(days=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'crew_id': crew_id,
        'expected_crew_version': crew['roster_version'],
        'client_attempt_id': 'crew-linked-archive-550e8400-e29b',
    }, headers=auth_headers(owner))
    assert created.status_code == 201, created.get_json()
    linked = created.get_json()
    assert client.post(
        f"/api/games/{linked['id']}/join",
        headers=auth_headers(member),
    ).status_code == 200
    completed = client.post(
        f"/api/games/{linked['id']}/complete",
        json={
            'team1': [owner['user']['id']],
            'team2': [member['user']['id']],
            'score_team1': 11,
            'score_team2': 6,
        },
        headers=auth_headers(owner),
    )
    assert completed.status_code == 200, completed.get_json()

    assert client.delete(
        f'/api/crews/{crew_id}', headers=auth_headers(owner),
    ).status_code == 200

    from backend.models import Crew, Game

    archived_retry = client.post(
        f"/api/games/{linked['id']}/crew",
        headers=auth_headers(owner),
    )
    assert archived_retry.status_code == 409, archived_retry.get_json()
    assert archived_retry.get_json()['error'] == 'crew_archived'
    assert Crew.query.count() == 1

    # Legacy data may retain a Crew id after its row was lost. The immutable
    # link still prevents silently forming a different Crew from that rematch.
    stored = db.session.get(Game, linked['id'])
    stored.crew_id = crew_id + 100000
    db.session.commit()
    missing_retry = client.post(
        f"/api/games/{linked['id']}/crew",
        headers=auth_headers(owner),
    )
    assert missing_retry.status_code == 409, missing_retry.get_json()
    assert missing_retry.get_json()['error'] == 'crew_archived'
    assert Crew.query.count() == 1


def test_crew_chat_read_marker_upsert_is_single_row_and_monotonic(client):
    owner = register(client, 'marker-owner@example.com', 'Owner')
    member = register(client, 'marker-member@example.com', 'Member')
    source = completed_game(
        client, owner, court_id(client), [owner], [member],
    )
    crew_id = create_crew(client, source['id'], owner)['crew']['id']
    accept_crew(client, crew_id, member)

    message_ids = []
    for body in ('First update', 'Second update'):
        sent = client.post(
            f'/api/crews/{crew_id}/chat',
            json={'body': body},
            headers=auth_headers(owner),
        )
        assert sent.status_code == 201, sent.get_json()
        message_ids.append(sent.get_json()['id'])

    # Repeated first reads converge on the unique marker instead of racing a
    # select-then-insert path.
    for _ in range(2):
        response = client.get(
            f'/api/crews/{crew_id}/chat', headers=auth_headers(member),
        )
        assert response.status_code == 200, response.get_json()

    from backend.models import CrewChatRead
    from backend.routes.crews import _advance_chat_read_marker

    marker = CrewChatRead.query.filter_by(
        user_id=member['user']['id'], crew_id=crew_id,
    ).one()
    assert marker.last_read_message_id == message_ids[-1]

    # A slower request that computed an older page must not move the shared
    # marker backward after a newer request has committed.
    _advance_chat_read_marker(member['user']['id'], crew_id, message_ids[0])
    db.session.commit()
    db.session.expire_all()
    marker = CrewChatRead.query.filter_by(
        user_id=member['user']['id'], crew_id=crew_id,
    ).one()
    assert marker.last_read_message_id == message_ids[-1]
    assert CrewChatRead.query.filter_by(
        user_id=member['user']['id'], crew_id=crew_id,
    ).count() == 1
