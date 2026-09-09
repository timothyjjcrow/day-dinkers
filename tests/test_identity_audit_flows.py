"""API behavior for complete-history filtering and current Activity decisions."""
from datetime import timedelta

from backend.app import db
from backend.models import Court, Friendship, Game, GameInvite, GamePlayer, Notification, User, utcnow
from tests.test_pagination_notifications import app, client, register, auth


def test_tournament_place_offer_actions_follow_current_hold_not_read_state(client, app):
    from backend.models import Tournament, TournamentEntry, TournamentWaitlist, MUTEABLE_NOTIFICATIONS
    player = register(client, 'offered-tournament@example.test', 'Player')
    user_id = player['user']['id']
    with app.app_context():
        court = Court(name='Offer Court', city='Town', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        tournament = Tournament(name='Held place', court_id=court.id, organizer_id=user_id,
                                starts_at=utcnow() + timedelta(days=2), status='registration')
        db.session.add(tournament)
        db.session.flush()
        now = utcnow()
        row = TournamentWaitlist(tournament_id=tournament.id, user_id=user_id, status='offered',
                                 offered_at=now, expires_at=now + timedelta(minutes=30))
        stale = Notification(user_id=user_id, kind='tournament_waitlist_offer', related_tournament_id=tournament.id,
                             title='Old expired offer', created_at=now - timedelta(days=1))
        current = Notification(user_id=user_id, kind='tournament_waitlist_offer', related_tournament_id=tournament.id,
                               title='Place held', read=True, created_at=now + timedelta(seconds=1))
        db.session.add_all([row, stale, current])
        db.session.commit()
        row_id, tournament_id, current_id = row.id, tournament.id, current.id
    assert 'tournament_waitlist_offer' not in MUTEABLE_NOTIFICATIONS
    actions = client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items']
    assert [item['id'] for item in actions] == [current_id]
    assert actions[0]['category'] == 'games' and actions[0]['needs_action'] is True
    for status in ['queued', 'expired', 'closed', 'accepted', 'left']:
        with app.app_context():
            db.session.get(TournamentWaitlist, row_id).status = status
            db.session.commit()
        assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []
    with app.app_context():
        row = db.session.get(TournamentWaitlist, row_id)
        row.status = 'offered'
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []
    with app.app_context():
        db.session.get(TournamentWaitlist, row_id).expires_at = utcnow() + timedelta(minutes=30)
        db.session.get(Tournament, tournament_id).status = 'active'
        db.session.commit()
    assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []
    with app.app_context():
        db.session.get(Tournament, tournament_id).status = 'registration'
        db.session.add(TournamentEntry(tournament_id=tournament_id, player1_id=user_id))
        db.session.commit()
    assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []


def test_activity_filters_before_pagination_and_preserves_account_unread(client, app):
    account = register(client, 'categories@example.test', 'Player')
    with app.app_context():
        for kind in ['crew_update', 'crew_update', 'business_claim', 'safety_report_update', *(['game_updated'] * 25)]:
            db.session.add(Notification(user_id=account['user']['id'], kind=kind, title=kind))
        db.session.commit()
    groups = client.get('/api/notifications?limit=1&filter=groups', headers=auth(account)).get_json()
    assert groups['items'][0]['category'] == 'groups'
    assert groups['unread'] == 29
    assert groups['has_more'] is True
    older = client.get(f"/api/notifications?limit=1&filter=groups&before_id={groups['next_cursor']}", headers=auth(account)).get_json()
    assert older['items'][0]['id'] < groups['items'][0]['id']
    assert older['has_more'] is False
    for category in ['business', 'safety']:
        data = client.get(f'/api/notifications?filter={category}', headers=auth(account)).get_json()
        assert len(data['items']) == 1
        assert data['items'][0]['category'] == category
    assert client.get('/api/notifications?filter=invalid', headers=auth(account)).status_code == 400


def test_unread_filter_finds_older_unread_items_before_pagination(client, app):
    account = register(client, 'unread-filter@example.test', 'Player')
    with app.app_context():
        oldest = Notification(user_id=account['user']['id'], kind='game_updated', title='Still unread')
        db.session.add(oldest)
        db.session.flush()
        oldest_id = oldest.id
        for index in range(25):
            db.session.add(Notification(user_id=account['user']['id'], kind='game_updated', title=f'Read {index}', read=True))
        db.session.commit()
    page = client.get('/api/notifications?filter=unread&limit=1', headers=auth(account)).get_json()
    assert [item['id'] for item in page['items']] == [oldest_id]
    assert page['has_more'] is False and page['unread'] == 1


def test_pending_decisions_follow_current_entity_state_even_after_reading(client, app):
    player = register(client, 'decisions@example.test', 'Player')
    friend = register(client, 'sender@example.test', 'Sender')
    with app.app_context():
        relationship = Friendship(requester_id=friend['user']['id'], addressee_id=player['user']['id'], status='pending')
        db.session.add(relationship)
        db.session.add(Notification(user_id=player['user']['id'], kind='friend_request', related_user_id=friend['user']['id'], read=True))
        db.session.commit()
        relationship_id = relationship.id
    pending = client.get('/api/notifications?filter=action', headers=auth(player)).get_json()
    assert len(pending['items']) == 1
    assert pending['items'][0]['needs_action'] is True
    client.post(f'/api/friends/{relationship_id}/respond', json={'accept': True}, headers=auth(player))
    assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []
    history = client.get('/api/notifications', headers=auth(player)).get_json()['items']
    assert history[0]['needs_action'] is False


def test_game_invitation_actions_disappear_after_join_or_cancellation(client, app):
    player = register(client, 'invitee@example.test', 'Player')
    host = register(client, 'host@example.test', 'Host')
    with app.app_context():
        court = Court(name='Test court', city='Test', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        game = Game(court_id=court.id, creator_id=host['user']['id'], scheduled_at=utcnow() + timedelta(days=1), status='upcoming')
        db.session.add(game)
        db.session.flush()
        db.session.add_all([GameInvite(game_id=game.id, user_id=player['user']['id']), Notification(user_id=player['user']['id'], kind='game_invite_direct', related_game_id=game.id)])
        db.session.commit()
        game_id = game.id
    assert len(client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items']) == 1
    with app.app_context():
        db.session.add(GamePlayer(game_id=game_id, user_id=player['user']['id']))
        db.session.commit()
    assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []
    with app.app_context():
        GamePlayer.query.filter_by(game_id=game_id).delete()
        db.session.get(Game, game_id).status = 'cancelled'
        db.session.commit()
    assert client.get('/api/notifications?filter=action', headers=auth(player)).get_json()['items'] == []


def test_history_filters_find_older_results_and_paginate_inside_selected_scope(client, app):
    player = register(client, 'history@example.test', 'Player')
    with app.app_context():
        court = Court(name='History court', city='Test', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        for index in range(8):
            ranked = index < 3
            game = Game(court_id=court.id, creator_id=player['user']['id'], scheduled_at=utcnow() - timedelta(days=10-index),
                        completed_at=utcnow() - timedelta(days=10-index), status='completed',
                        game_type='ranked' if ranked else 'casual', score_team1=11 if ranked else None, score_team2=7 if ranked else None)
            db.session.add(game)
            db.session.flush()
            db.session.add(GamePlayer(game_id=game.id, user_id=player['user']['id'], team=2 if index == 0 else 1))
        db.session.commit()
    first = client.get('/api/games/history?limit=1&filter=wins', headers=auth(player)).get_json()
    assert first['items'][0]['you_won'] is True
    assert first['total'] == 2
    second = client.get(f"/api/games/history?limit=1&filter=wins&cursor={first['next_cursor']}", headers=auth(player)).get_json()
    assert second['items'][0]['id'] != first['items'][0]['id']
    assert second['has_more'] is False
    assert client.get('/api/games/history?filter=losses', headers=auth(player)).get_json()['total'] == 1
    assert client.get('/api/games/history?filter=ranked', headers=auth(player)).get_json()['total'] == 3
    assert client.get('/api/games/history?filter=casual', headers=auth(player)).get_json()['total'] == 5
    assert client.get('/api/games/history?filter=invalid', headers=auth(player)).status_code == 400


def test_new_password_policy_is_consistent_without_locking_out_legacy_logins(client, app):
    assert client.post('/api/auth/register', json={'email': 'short@example.test', 'display_name': 'Short', 'password': '1234567'}).status_code == 400
    player = register(client, 'password@example.test', 'Player')
    assert client.post('/api/auth/change-password', headers=auth(player), json={'current_password': 'secret123', 'new_password': '1234567'}).status_code == 400
    assert client.post('/api/auth/reset-password', json={'new_password': '1234567'}).status_code == 400
    with app.app_context():
        user = db.session.get(User, player['user']['id'])
        user.set_password('old123')
        db.session.commit()
    assert client.post('/api/auth/login', json={'email': 'password@example.test', 'password': 'old123'}).status_code == 200
