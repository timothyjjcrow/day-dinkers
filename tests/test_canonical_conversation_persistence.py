"""Persistence and backfill contracts for canonical groups/conversations."""

from datetime import timedelta

import pytest
from sqlalchemy import inspect

from backend.app import create_app, db
from backend.models import (
    Club,
    ClubChatRead,
    ClubMember,
    Conversation,
    ConversationRead,
    Court,
    CourtChatRead,
    Crew,
    CrewChatRead,
    CrewMember,
    Game,
    GameChatRead,
    GamePlayer,
    Group,
    League,
    LeagueChatRead,
    LeagueMember,
    Message,
    Tournament,
    TournamentChatRead,
    utcnow,
)
from backend.services.conversation_migration import (
    ensure_canonical_communication_schema,
)
from backend.services.conversations import SPECS, conversation_ref
from scripts.migrate_production_schema import (
    REQUIRED_CHECK_CONSTRAINTS,
    REQUIRED_COLUMNS,
    REQUIRED_FOREIGN_KEYS,
    REQUIRED_INDEXES,
    REQUIRED_UNIQUES,
)


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug='canonical-owner'):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': slug.replace('-', ' ').title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def create_room_sources(user_id):
    court = Court(
        name='Canonical Courts', city='Irvine', state='CA',
        county_slug='orange-county', latitude=33.68,
        longitude=-117.82, num_courts=6,
    )
    db.session.add(court)
    db.session.flush()
    game = Game(
        court_id=court.id, creator_id=user_id,
        scheduled_at=utcnow() + timedelta(hours=2),
        game_type='casual', visibility='private', max_players=4,
    )
    club = Club(
        name='Canonical Community', description='Public group',
        join_policy='request', creator_id=user_id, home_court_id=court.id,
    )
    crew = Crew(
        name='Canonical Private Group', owner_id=user_id,
        default_court_id=court.id,
    )
    tournament = Tournament(
        name='Canonical Tournament', court_id=court.id,
        organizer_id=user_id, starts_at=utcnow() + timedelta(days=2),
    )
    league = League(
        name='Canonical League', court_id=court.id,
        organizer_id=user_id, starts_at=utcnow() + timedelta(days=3),
    )
    db.session.add_all([game, club, crew, tournament, league])
    db.session.flush()
    db.session.add_all([
        GamePlayer(game_id=game.id, user_id=user_id),
        ClubMember(club_id=club.id, user_id=user_id, role='owner'),
        LeagueMember(league_id=league.id, user_id=user_id),
    ])
    db.session.commit()
    return {
        'court': court,
        'game': game,
        'tournament': tournament,
        'club': club,
        'crew': crew,
        'league': league,
    }


def test_group_identity_dual_writes_existing_club_and_crew_routes(client, app):
    owner = register(client)
    headers = auth(owner)
    court = Court(
        name='Group Identity Courts', city='Irvine', state='CA',
        latitude=33.67, longitude=-117.81, num_courts=4,
    )
    db.session.add(court)
    db.session.commit()

    club_response = client.post('/api/clubs', headers=headers, json={
        'name': 'Approval Community',
        'description': 'Ask before joining',
        'join_policy': 'request',
        'home_court_id': court.id,
    })
    assert club_response.status_code == 201, club_response.get_json()
    club_id = club_response.get_json()['id']
    crew_response = client.post('/api/crews', headers=headers, json={
        'name': 'Invite Group', 'invite_user_ids': [],
        'default_court_id': court.id,
    })
    assert crew_response.status_code == 201, crew_response.get_json()
    crew_id = crew_response.get_json()['crew']['id']

    club_group = Group.query.filter_by(
        kind='club', legacy_scope_id=club_id,
    ).one()
    crew_group = Group.query.filter_by(
        kind='crew', legacy_scope_id=crew_id,
    ).one()
    assert club_group.privacy == 'approval'
    assert crew_group.privacy == 'invite'
    assert club_group.id != crew_group.id
    assert club_group.owner_id == crew_group.owner_id == owner['user']['id']

    changed_club = client.patch(f'/api/clubs/{club_id}', headers=headers, json={
        'name': 'Open Community', 'join_policy': 'open',
    })
    changed_crew = client.patch(f'/api/crews/{crew_id}', headers=headers, json={
        'name': 'Renamed Private Group',
    })
    assert changed_club.status_code == changed_crew.status_code == 200
    db.session.expire_all()
    assert club_group.privacy == 'open'
    assert club_group.name == 'Open Community'
    assert crew_group.privacy == 'invite'
    assert crew_group.name == 'Renamed Private Group'


def test_all_six_chat_apis_dual_write_canonical_messages_and_reads(client, app):
    owner = register(client, 'six-scope-owner')
    headers = auth(owner)
    sources = create_room_sources(owner['user']['id'])

    for kind, entity in sources.items():
        collection = SPECS[kind].collection
        sent = client.post(
            f'/api/{collection}/{entity.id}/chat',
            headers=headers,
            json={
                'body': f'Canonical {kind}',
                'client_attempt_id': f'canonical-{kind}',
            },
        )
        assert sent.status_code == 201, (kind, sent.get_json())
        opened = client.get(
            f'/api/{collection}/{entity.id}/chat', headers=headers,
        )
        assert opened.status_code == 200, (kind, opened.get_json())

    assert Conversation.query.count() == 6
    assert ConversationRead.query.count() == 6
    assert Message.query.count() == 6
    for kind, entity in sources.items():
        conversation = Conversation.query.filter_by(
            kind=kind, scope_id=entity.id,
        ).one()
        message = Message.query.filter_by(
            **{SPECS[kind].message_field: entity.id},
        ).one()
        marker = ConversationRead.query.filter_by(
            user_id=owner['user']['id'], conversation_id=conversation.id,
        ).one()
        assert message.conversation_id == conversation.id
        assert marker.last_read_message_id == message.id
        assert conversation_ref(kind, entity.id).message_query().one().id == message.id
        if kind in ('club', 'crew'):
            assert conversation.group_id == Group.query.filter_by(
                kind=kind, legacy_scope_id=entity.id,
            ).one().id


def test_schema_migration_backfills_legacy_rows_idempotently_without_dropping_them(
        client, app):
    owner = register(client, 'legacy-room-owner')
    user_id = owner['user']['id']
    sources = create_room_sources(user_id)
    marker_models = {
        'court': (CourtChatRead, 'court_id'),
        'game': (GameChatRead, 'game_id'),
        'tournament': (TournamentChatRead, 'tournament_id'),
        'club': (ClubChatRead, 'club_id'),
        'crew': (CrewChatRead, 'crew_id'),
        'league': (LeagueChatRead, 'league_id'),
    }
    for offset, (kind, entity) in enumerate(sources.items(), start=1):
        message = Message(
            sender_id=user_id,
            body=f'Legacy {kind}',
            **{SPECS[kind].message_field: entity.id},
        )
        db.session.add(message)
        db.session.flush()
        marker_model, marker_field = marker_models[kind]
        marker_values = {
            'user_id': user_id,
            marker_field: entity.id,
            'last_read_message_id': message.id,
        }
        if kind == 'crew':
            marker_values['notification_level'] = 'mentions'
        db.session.add(marker_model(**marker_values))
    db.session.commit()
    assert Conversation.query.count() == 0
    assert ConversationRead.query.count() == 0
    assert Group.query.count() == 0

    ensure_canonical_communication_schema(app)
    first_ids = {
        (row.kind, row.scope_id): row.id
        for row in Conversation.query.all()
    }
    assert len(first_ids) == 6
    assert ConversationRead.query.count() == 6
    assert Group.query.count() == 2
    assert Message.query.filter(Message.conversation_id.is_(None)).count() == 0

    ensure_canonical_communication_schema(app)
    assert {
        (row.kind, row.scope_id): row.id
        for row in Conversation.query.all()
    } == first_ids
    assert ConversationRead.query.count() == 6
    assert Group.query.count() == 2
    assert sum(model.query.count() for model, _ in marker_models.values()) == 6


def test_canonical_tables_are_in_runtime_and_production_schema_contracts(app):
    inspector = inspect(db.engine)
    assert {'community_group', 'conversation', 'conversation_read'} <= set(
        inspector.get_table_names()
    )
    assert 'conversation_id' in {
        column['name'] for column in inspector.get_columns('message')
    }
    message_fks = inspector.get_foreign_keys('message')
    assert any(
        item.get('referred_table') == 'conversation'
        and item.get('constrained_columns') == ['conversation_id']
        for item in message_fks
    )

    for table in ('community_group', 'conversation', 'conversation_read'):
        assert table in REQUIRED_COLUMNS
        assert table in REQUIRED_INDEXES
        assert table in REQUIRED_UNIQUES
        assert table in REQUIRED_FOREIGN_KEYS
    assert 'community_group' in REQUIRED_CHECK_CONSTRAINTS
    assert 'conversation' in REQUIRED_CHECK_CONSTRAINTS


def test_subscription_preferences_and_block_keep_canonical_reads_in_sync(
        client, app):
    owner = register(client, 'canonical-marker-owner')
    member = register(client, 'canonical-marker-member')
    owner_headers = auth(owner)
    member_headers = auth(member)
    court = Court(
        name='Marker Courts', city='Irvine', state='CA',
        latitude=33.67, longitude=-117.81, num_courts=4,
    )
    db.session.add(court)
    db.session.flush()
    crew = Crew(
        name='Marker Private Group', owner_id=owner['user']['id'],
        default_court_id=court.id,
    )
    db.session.add(crew)
    db.session.flush()
    db.session.add(CrewMember(
        crew_id=crew.id, user_id=member['user']['id'],
    ))
    db.session.commit()

    preference = client.patch(
        f'/api/crews/{crew.id}/notification-settings',
        headers=owner_headers,
        json={'level': 'mentions'},
    )
    assert preference.status_code == 200, preference.get_json()
    crew_conversation = Conversation.query.filter_by(
        kind='crew', scope_id=crew.id,
    ).one()
    owner_marker = ConversationRead.query.filter_by(
        user_id=owner['user']['id'], conversation_id=crew_conversation.id,
    ).one()
    assert owner_marker.last_read_message_id == 0
    assert CrewChatRead.query.filter_by(
        user_id=owner['user']['id'], crew_id=crew.id,
    ).one().notification_level == 'mentions'

    court_message = client.post(
        f'/api/courts/{court.id}/chat', headers=owner_headers,
        json={
            'body': 'Already in the place room',
            'client_attempt_id': 'canonical-marker-court',
        },
    )
    assert court_message.status_code == 201, court_message.get_json()
    subscribed = client.put(
        f'/api/courts/{court.id}/chat/subscription',
        headers=member_headers, json={'joined': True, 'muted': False},
    )
    assert subscribed.status_code == 200, subscribed.get_json()
    court_conversation = Conversation.query.filter_by(
        kind='court', scope_id=court.id,
    ).one()
    assert ConversationRead.query.filter_by(
        user_id=member['user']['id'], conversation_id=court_conversation.id,
    ).one().last_read_message_id == court_message.get_json()['id']

    crew_message = client.post(
        f'/api/crews/{crew.id}/chat', headers=owner_headers,
        json={
            'body': 'Private group update',
            'client_attempt_id': 'canonical-marker-crew',
        },
    )
    assert crew_message.status_code == 201, crew_message.get_json()
    opened = client.get(
        f'/api/crews/{crew.id}/chat', headers=member_headers,
    )
    assert opened.status_code == 200, opened.get_json()
    assert ConversationRead.query.filter_by(
        user_id=member['user']['id'], conversation_id=crew_conversation.id,
    ).one().last_read_message_id == crew_message.get_json()['id']

    blocked = client.post(
        f"/api/users/{member['user']['id']}/block", headers=owner_headers,
    )
    assert blocked.status_code == 200, blocked.get_json()
    assert ConversationRead.query.filter_by(
        user_id=member['user']['id'], conversation_id=crew_conversation.id,
    ).first() is None
