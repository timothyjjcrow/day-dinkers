"""Shared room-conversation contract across every legacy chat scope."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Club, ClubChatRead, ClubMember, Court, CourtChatRead, Crew, CrewChatRead,
    Game, GameChatRead, GamePlayer, League, LeagueChatRead, LeagueMember,
    Message, Tournament, TournamentChatRead, utcnow,
)
from backend.services.conversations import (
    SPECS, conversation_ref, conversation_ref_for_message,
)


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client):
    response = client.post('/api/auth/register', json={
        'email': 'conversation-owner@example.com',
        'password': 'secret123',
        'display_name': 'Conversation Owner',
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_conversation_ref_has_one_stable_identity_for_each_room_kind():
    expected = {
        'court': ('courts', 'place', 'signed_in', 'court_id'),
        'game': ('games', 'play', 'members', 'game_id'),
        'tournament': (
            'tournaments', 'competition', 'participants', 'tournament_id',
        ),
        'club': ('clubs', 'group', 'members', 'club_id'),
        'crew': ('crews', 'group', 'members', 'crew_id'),
        'league': ('leagues', 'competition', 'members', 'league_id'),
    }
    assert set(SPECS) == set(expected)
    for kind, (collection, category, access, field) in expected.items():
        ref = conversation_ref(kind, '17')
        assert ref.id == f'{kind}:17'
        assert ref.message_scope == {field: 17}
        assert ref.to_dict('Room name') == {
            'id': f'{kind}:17',
            'kind': kind,
            'scope_id': 17,
            'category': category,
            'access': access,
            'name': 'Room name',
            'messages_url': f'/api/{collection}/17/chat',
            'action_url': f'/#{kind}/17',
        }

    for kind, scope_id, error in (
        ('unknown', 1, 'unsupported_conversation_kind'),
        ('court', 0, 'invalid_conversation_id'),
        ('court', True, 'invalid_conversation_id'),
    ):
        with pytest.raises(ValueError, match=error):
            conversation_ref(kind, scope_id)

    with pytest.raises(ValueError, match='message_conversation_scope_invalid'):
        conversation_ref_for_message(Message(sender_id=1, body='No room'))
    with pytest.raises(ValueError, match='message_conversation_scope_invalid'):
        conversation_ref_for_message(Message(
            sender_id=1, body='Two rooms', court_id=1, game_id=2,
        ))


def test_all_legacy_room_endpoints_share_dto_scope_and_read_adapter(client, app):
    player = register(client)
    user_id = player['user']['id']
    auth = {'Authorization': f"Bearer {player['token']}"}
    with app.app_context():
        court = Court(
            name='Conversation Court', city='Irvine', state='CA',
            county_slug='orange-county', latitude=33.68,
            longitude=-117.82, num_courts=4,
        )
        db.session.add(court)
        db.session.flush()
        game = Game(
            court_id=court.id, creator_id=user_id,
            scheduled_at=utcnow() + timedelta(hours=2),
            game_type='casual', visibility='private', max_players=4,
        )
        club = Club(
            name='Conversation Community', description='A local community',
            creator_id=user_id, home_court_id=court.id,
        )
        crew = Crew(
            name='Conversation Play Group', owner_id=user_id,
            default_court_id=court.id,
        )
        tournament = Tournament(
            name='Conversation Tournament', court_id=court.id,
            organizer_id=user_id, starts_at=utcnow() + timedelta(days=2),
        )
        league = League(
            name='Conversation League', court_id=court.id,
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
        resources = {
            'court': (court.id, court.name, 'court', CourtChatRead, 'court_id'),
            'game': (game.id, court.name, 'game', GameChatRead, 'game_id'),
            'tournament': (
                tournament.id, tournament.name, 'tournament',
                TournamentChatRead, 'tournament_id',
            ),
            'club': (
                club.id, club.name, 'club', ClubChatRead, 'club_id',
            ),
            'crew': (
                crew.id, crew.name, 'crew', CrewChatRead, 'crew_id',
            ),
            'league': (
                league.id, league.name, 'league', LeagueChatRead, 'league_id',
            ),
        }

    for kind, (scope_id, name, legacy_key, _marker, _marker_field) in resources.items():
        collection = SPECS[kind].collection
        response = client.get(
            f'/api/{collection}/{scope_id}/chat', headers=auth,
        )
        assert response.status_code == 200, (kind, response.get_json())
        payload = response.get_json()
        assert legacy_key in payload
        assert payload['conversation'] == conversation_ref(
            kind, scope_id,
        ).to_dict(name)

        sent = client.post(
            f'/api/{collection}/{scope_id}/chat',
            json={
                'body': f'Hello {kind}',
                'client_attempt_id': f'unified-{kind}-message',
            }, headers=auth,
        )
        assert sent.status_code == 201, (kind, sent.get_json())
        assert sent.get_json()['conversation'] == payload['conversation']

        reread = client.get(
            f'/api/{collection}/{scope_id}/chat', headers=auth,
        )
        assert reread.status_code == 200, (kind, reread.get_json())
        assert reread.get_json()['items'][-1]['body'] == f'Hello {kind}'

    with app.app_context():
        messages = Message.query.order_by(Message.id).all()
        assert len(messages) == len(resources)
        assert {
            conversation_ref_for_message(message).id for message in messages
        } == {
            f'{kind}:{values[0]}' for kind, values in resources.items()
        }
        for kind, (scope_id, _name, _legacy_key, marker_model,
                   marker_field) in resources.items():
            marker = marker_model.query.filter_by(**{
                'user_id': user_id, marker_field: scope_id,
            }).one()
            message_id = next(
                message.id for message in messages
                if conversation_ref_for_message(message).kind == kind
            )
            assert marker.last_read_message_id == message_id
