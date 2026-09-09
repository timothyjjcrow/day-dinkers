"""Conversation discovery and one-off invitations retain real privacy boundaries."""
from datetime import timedelta

from backend.app import db
from backend.models import (Club, ClubMember, Court, CourtChatSubscription, Crew, CrewMember,
    Friendship, Game, GameInvite, GamePlayer, League, LeagueMember, Message, Tournament,
    TournamentEntry, User, utcnow)
from tests.test_community_chat_completion import app, client, register, auth


def test_search_finds_permitted_conversation_types_without_exposing_other_rooms(client, app):
    viewer = register(client, 'searcher@social.test', 'Viewer')
    other = register(client, 'other@social.test', 'Cedar Player')
    me, them = viewer['user']['id'], other['user']['id']
    with app.app_context():
        court = Court(name='Cedar Courts', city='Town', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        db.session.add(CourtChatSubscription(user_id=me, court_id=court.id))
        game = Game(court_id=court.id, creator_id=me, status='completed',
                    scheduled_at=utcnow() - timedelta(days=90), completed_at=utcnow() - timedelta(days=90))
        hidden_game = Game(court_id=court.id, creator_id=them, visibility='private', scheduled_at=utcnow())
        club = Club(name='Cedar Public Group', creator_id=them)
        crew = Crew(name='Cedar Private Group', owner_id=them)
        private_crew = Crew(name='Cedar Other Private Group', owner_id=them)
        league = League(name='Cedar League', court_id=court.id, organizer_id=me, starts_at=utcnow())
        tournament = Tournament(name='Cedar Tournament', court_id=court.id, organizer_id=them, starts_at=utcnow())
        db.session.add_all([game, hidden_game, club, crew, private_crew, league, tournament])
        db.session.flush()
        db.session.add_all([GamePlayer(game_id=game.id, user_id=me), ClubMember(club_id=club.id, user_id=me),
            CrewMember(crew_id=crew.id, user_id=me), LeagueMember(league_id=league.id, user_id=me),
            TournamentEntry(tournament_id=tournament.id, player1_id=me),
            Message(sender_id=them, recipient_id=me, body='Hello')])
        db.session.commit()
        forbidden = {('crew', private_crew.id), ('game', hidden_game.id)}
    response = client.get('/api/inbox/search?q=cedar', headers=auth(viewer))
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result['errors'] == {}
    assert {row['kind'] for row in result['items']} == {'dm', 'court', 'game', 'club', 'crew', 'league', 'tournament'}
    assert all(row['event_at'] for row in result['items'] if row['kind'] in {'game', 'league', 'tournament'})
    assert not ({(row['kind'], row['id']) for row in result['items']} & forbidden)
    assert client.get('/api/inbox/search?q=cedar').status_code == 401
    client.post(f'/api/users/{them}/block', headers=auth(viewer))
    assert not any(row['kind'] == 'dm' for row in client.get('/api/inbox/search?q=cedar', headers=auth(viewer)).get_json()['items'])


def test_search_reaches_older_direct_chats_and_pages_matching_results(client, app):
    viewer = register(client, 'search-page@social.test', 'Viewer')
    with app.app_context():
        for index in range(55):
            user = User(email=f'player{index}@social.test', password_hash='unused', display_name=f'Search Player {index:02d}')
            db.session.add(user)
            db.session.flush()
            db.session.add(Message(sender_id=user.id, recipient_id=viewer['user']['id'], body='Past conversation'))
        db.session.commit()
    ordinary = client.get('/api/chat', headers=auth(viewer)).get_json()
    assert len(ordinary['items']) == 50
    oldest = client.get('/api/inbox/search?q=Player%2000', headers=auth(viewer)).get_json()
    assert [row['title'] for row in oldest['items']] == ['Search Player 00']
    page = client.get('/api/inbox/search?q=Search%20Player', headers=auth(viewer)).get_json()
    assert len(page['items']) == 30 and page['has_more'] is True
    older = client.get(f"/api/inbox/search?q=Search%20Player&offset={page['next_offset']}", headers=auth(viewer)).get_json()
    assert len(older['items']) == 25 and older['has_more'] is False
    assert len({row['id'] for row in page['items'] + older['items']}) == 55


def test_nearby_beginner_and_radius_filters_preserve_hidden_and_blocked_players(client, app):
    viewer = register(client, 'nearby@social.test', 'Viewer')
    with app.app_context():
        for name, rating, latitude, visibility in [('New player', 2.5, 33.01, 'everyone'),
            ('New without number', None, 33.02, 'everyone'), ('Experienced', 3.5, 33.01, 'everyone'),
            ('Far beginner', 2.5, 33.4, 'everyone'), ('Hidden beginner', 2.5, 33.01, 'hidden')]:
            db.session.add(User(email=name.replace(' ', '') + '@social.test', password_hash='unused', display_name=name,
                skill_level='beginner', skill_rating=rating, home_lat=latitude, home_lng=-117, nearby_visibility=visibility))
        db.session.commit()
    path = '/api/players/nearby?lat=33&lng=-117&level=beginner'
    narrow = client.get(path + '&radius=10', headers=auth(viewer)).get_json()['items']
    assert {row['display_name'] for row in narrow} == {'New player', 'New without number'}
    wide = client.get(path + '&radius=50', headers=auth(viewer)).get_json()['items']
    assert {row['display_name'] for row in wide} == {'New player', 'New without number', 'Far beginner'}
    client.post(f"/api/users/{narrow[0]['id']}/block", headers=auth(viewer))
    assert len(client.get(path + '&radius=10', headers=auth(viewer)).get_json()['items']) == 1


def test_search_reaches_read_court_rooms_beyond_compact_cap_and_reports_partial_failure(client, app, monkeypatch):
    viewer = register(client, 'court-search@social.test', 'Viewer')
    with app.app_context():
        for index in range(25):
            court = Court(name=f'Old Court {index:02d}', city='Town', state='CA', latitude=33, longitude=-117)
            db.session.add(court)
            db.session.flush()
            db.session.add(CourtChatSubscription(user_id=viewer['user']['id'], court_id=court.id))
        db.session.commit()
    assert len(client.get('/api/chat/courts', headers=auth(viewer)).get_json()['items']) == 20
    found = client.get('/api/inbox/search?q=Old%20Court', headers=auth(viewer)).get_json()
    assert len(found['items']) == 25
    import backend.routes.chat as chat_routes
    monkeypatch.setattr(chat_routes, 'my_court_rooms', lambda: (_ for _ in ()).throw(RuntimeError('offline')))
    partial = client.get('/api/inbox/search?q=Old%20Court', headers=auth(viewer)).get_json()
    assert partial['errors'] == {'courts': 'unavailable'}


def test_played_together_invites_without_friendship_and_does_not_rsvp_the_other_player(client, app):
    viewer = register(client, 'reinvite@social.test', 'Viewer')
    other = register(client, 'coplayer@social.test', 'Past player')
    me, them = viewer['user']['id'], other['user']['id']
    with app.app_context():
        court = Court(name='Shared Courts', city='Town', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        game = Game(court_id=court.id, creator_id=me, status='completed', visibility='private',
            scheduled_at=utcnow() - timedelta(days=2), completed_at=utcnow() - timedelta(days=2))
        db.session.add(game)
        db.session.flush()
        db.session.add_all([GamePlayer(game_id=game.id, user_id=me), GamePlayer(game_id=game.id, user_id=them)])
        db.session.commit()
        court_id = court.id
    person = client.get('/api/players/recent', headers=auth(viewer)).get_json()['items'][0]
    assert person['can_invite'] is True and person['is_friend'] is False
    assert person['last_played_court']['id'] == court_id
    body = {'court_id': court_id, 'scheduled_at': (utcnow() + timedelta(days=3)).isoformat() + 'Z',
        'game_type': 'casual', 'visibility': 'private', 'max_players': 4, 'invite_user_ids': [them], 'require_all_invitees': True}
    response = client.post('/api/games', json=body, headers=auth(viewer))
    assert response.status_code == 201, response.get_json()
    plan = client.get(f'/api/chat/{them}', headers=auth(viewer)).get_json()['shared_plan']
    assert plan['id'] == response.get_json()['id']
    assert plan['viewer_status'] == 'going' and plan['partner_status'] == 'invited'
    assert plan['court']['id'] == court_id
    with app.app_context():
        assert Friendship.query.count() == 0
        assert GameInvite.query.filter_by(game_id=response.get_json()['id'], user_id=them).count() == 1
        assert GamePlayer.query.filter_by(game_id=response.get_json()['id'], user_id=them).count() == 0
        count = Game.query.count()
    client.post(f'/api/users/{them}/block', headers=auth(viewer))
    assert client.get('/api/players/recent', headers=auth(viewer)).get_json()['items'] == []
    assert client.post('/api/games', json=body, headers=auth(viewer)).status_code == 409
    with app.app_context():
        assert Game.query.count() == count


def test_shared_dm_plan_is_private_and_disappears_when_cancelled(client, app):
    viewer = register(client, 'dm-plan@social.test', 'Viewer')
    invited = register(client, 'invited@social.test', 'Invited player')
    unrelated = register(client, 'unrelated@social.test', 'Other contact')
    with app.app_context():
        court = Court(name='Private plan court', city='Town', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        game = Game(court_id=court.id, creator_id=viewer['user']['id'], scheduled_at=utcnow() + timedelta(days=2), visibility='private')
        db.session.add(game)
        db.session.flush()
        db.session.add_all([GamePlayer(game_id=game.id, user_id=viewer['user']['id']),
            GameInvite(game_id=game.id, user_id=invited['user']['id']),
            Message(sender_id=viewer['user']['id'], recipient_id=invited['user']['id'], body='Existing chat'),
            Message(sender_id=viewer['user']['id'], recipient_id=unrelated['user']['id'], body='Other chat')])
        db.session.commit()
        game_id = game.id
    def plan(contact):
        return client.get(f"/api/chat/{contact['user']['id']}", headers=auth(viewer)).get_json()['shared_plan']
    assert plan(invited)['id'] == game_id
    assert plan(unrelated) is None
    with app.app_context():
        db.session.get(Game, game_id).status = 'cancelled'
        db.session.commit()
    assert plan(invited) is None
