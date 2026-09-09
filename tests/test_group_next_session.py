"""Both group chats expose current permitted session context, never a roster grant."""
from datetime import timedelta

from backend.app import db
from backend.models import Club, ClubMember, Court, Game, GameInvite, GamePlayer, utcnow
from tests.test_community_chat_completion import app, client, register, auth


def test_public_chat_next_session_preserves_audience_and_participation_state(client, app):
    owner = register(client, 'public-next-owner@example.test', 'Owner')
    member = register(client, 'public-next-member@example.test', 'Member')
    outsider = register(client, 'public-next-outsider@example.test', 'Outsider')
    with app.app_context():
        court = Court(name='Public Next Court', city='Town', state='CA', latitude=33, longitude=-117)
        club = Club(name='Public Next Group', creator_id=owner['user']['id'])
        db.session.add_all([court, club]); db.session.flush()
        db.session.add_all([ClubMember(club_id=club.id, user_id=owner['user']['id'], role='owner'),
                           ClubMember(club_id=club.id, user_id=member['user']['id'])])
        games = []
        for hours, visibility in [(1, 'private'), (2, 'friends'), (3, 'open'), (4, 'open')]:
            game = Game(court_id=court.id, creator_id=owner['user']['id'], club_id=club.id,
                        scheduled_at=utcnow() + timedelta(hours=hours), visibility=visibility, title=f'Session {hours}')
            db.session.add(game); db.session.flush(); games.append(game)
            db.session.add(GamePlayer(game_id=game.id, user_id=owner['user']['id']))
        db.session.commit()
        club_id, ids = club.id, [game.id for game in games]
    route = f'/api/clubs/{club_id}/chat'
    assert client.get(route, headers=auth(outsider)).status_code == 403
    data = client.get(route, headers=auth(member)).get_json()
    assert data['club']['joined'] is True
    assert data['next_game']['id'] == ids[2]
    assert data['next_game']['title'] == 'Session 3'
    assert data['next_game']['is_joined'] is False
    assert 'players' not in data['next_game']
    with app.app_context():
        db.session.add(GameInvite(game_id=ids[0], user_id=member['user']['id']))
        db.session.commit()
    assert client.get(route, headers=auth(member)).get_json()['next_game']['is_invited'] is True
    with app.app_context():
        db.session.add(GamePlayer(game_id=ids[0], user_id=member['user']['id']))
        db.session.commit()
    assert client.get(route, headers=auth(member)).get_json()['next_game']['is_joined'] is True
    with app.app_context():
        db.session.get(Game, ids[0]).status = 'cancelled'
        db.session.get(Game, ids[2]).scheduled_at = utcnow() - timedelta(days=1)
        db.session.commit()
    assert client.get(route, headers=auth(member)).get_json()['next_game']['id'] == ids[3]
    client.post(f"/api/users/{owner['user']['id']}/block", headers=auth(member))
    assert client.get(route, headers=auth(member)).get_json()['next_game'] is None
