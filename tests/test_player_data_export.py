"""The download is complete within its stated scope and requires fresh ownership proof."""
import json
import time
from datetime import timedelta
from backend.app import db
from backend.models import (CompetitionResultEvent, Court, Game, GamePlayer,
    GameSessionAttendance, League, LeagueMatch, LeagueMember, Message,
    Tournament, TournamentEntry, TournamentMatch, User, utcnow)
from tests.test_community_chat_completion import app, client, register, auth


def test_export_contains_all_authored_records_without_other_messages_or_credentials(client, app):
    player = register(client, 'export-player@example.test', 'Player')
    other = register(client, 'export-other@example.test', 'Other')
    with app.app_context():
        user = db.session.get(User, player['user']['id'])
        user.calendar_token = 'private-calendar-token'
        user.mfa_secret_encrypted = 'encrypted-secret-value'
        user.mfa_recovery_codes = '["hashed-recovery-value"]'
        user.availability = '["tue-eve"]'
        for index in range(205):
            db.session.add(Message(sender_id=user.id, recipient_id=other['user']['id'], body=f'Own message {index}',
                                   image_data='data:image/jpeg;base64,c3ludGhldGlj' if index == 204 else None))
        db.session.add(Message(sender_id=other['user']['id'], recipient_id=user.id, body='Other author private body'))
        db.session.commit()
    response = client.post('/api/me/export', json={'current_password': 'secret123', 'user_id': other['user']['id']}, headers=auth(player))
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['Content-Disposition'].startswith('attachment; filename="third-shot-player-data-')
    data = response.get_json()
    assert data['format'] == 'third-shot-player-data-v1'
    assert data['profile']['id'] == player['user']['id']
    assert data['profile']['availability'] == ['tue-eve']
    assert len(data['sent_messages']) == 205
    assert data['sent_messages'][-1]['image_data'].startswith('data:image/jpeg')
    raw = response.get_data(as_text=True)
    for forbidden in ['Other author private body', 'private-calendar-token', 'encrypted-secret-value',
                      'hashed-recovery-value', 'password_hash', 'auth_version', 'mfa_recovery_codes']:
        assert forbidden not in raw
    assert client.get('/api/me', headers=auth(player)).status_code == 200


def test_export_reauthenticates_and_requires_current_mfa_without_consuming_recovery(client, app, monkeypatch):
    player = register(client, 'export-mfa@example.test', 'Player')
    assert client.post('/api/me/export', json={'current_password': 'secret123'}).status_code == 401
    assert client.post('/api/me/export', json=['invalid'], headers=auth(player)).status_code == 400
    assert client.post('/api/me/export', json={'current_password': 'wrong'}, headers=auth(player)).status_code == 403
    with app.app_context():
        db.session.get(User, player['user']['id']).mfa_enabled = True
        db.session.commit()
    calls = []
    def verify(user, code, *, allow_recovery):
        calls.append((user.id, code, allow_recovery))
        return code == '123456', False
    monkeypatch.setattr('backend.services.mfa.verify_user_mfa', verify)
    assert client.post('/api/me/export', json={'current_password': 'secret123'}, headers=auth(player), buffered=True).status_code == 403
    result = client.post('/api/me/export', json={'current_password': 'secret123', 'mfa_code': '123456'}, headers=auth(player), buffered=True)
    assert result.status_code == 200 and result.get_json()['profile']['id'] == player['user']['id']
    assert calls[-1] == (player['user']['id'], '123456', False)


def test_export_stream_survives_request_session_teardown_with_deferred_profile_photo(client, app):
    player = register(client, 'export-wsgi@example.test', 'Stream player')
    user = db.session.get(User, player['user']['id'])
    user.avatar_data = 'data:image/jpeg;base64,c3ludGhldGlj'
    db.session.commit()
    with app.test_request_context('/api/me/export', method='POST',
            json={'current_password':'secret123'}, headers=auth(player)):
        # Unlike Flask's convenience test-client response, real WSGI can close
        # the route session before iterating a streamed response body.
        response = app.full_dispatch_request()
        assert response.status_code == 200
        db.session.remove()
    data = json.loads(b''.join(response.iter_encoded()))
    response.close()
    assert data['profile']['avatar_data'] == 'data:image/jpeg;base64,c3ludGhldGlj'
    assert data['format'] == 'third-shot-player-data-v1'
    assert data['sessions'] == [] and data['competition_result_history'] == []


def test_ordinary_player_mfa_enrollment_recovery_and_export_require_current_code(client, app):
    from cryptography.fernet import Fernet
    from backend.services.mfa import _totp_at
    app.config['MFA_ENCRYPTION_KEY'] = Fernet.generate_key().decode('ascii')
    player = register(client, 'ordinary-mfa@example.test', 'Ordinary player')
    setup = client.post('/api/auth/mfa/setup', json={'current_password':'secret123'}, headers=auth(player))
    assert setup.status_code == 200
    secret = setup.get_json()['secret']
    enabled = client.post('/api/auth/mfa/enable', json={'code':_totp_at(secret,time.time())}, headers=auth(player))
    assert enabled.status_code == 200
    codes = enabled.get_json()['recovery_codes']
    assert len(codes) == 10 and len(set(codes)) == 10
    login = {'email':'ordinary-mfa@example.test','password':'secret123'}
    assert client.post('/api/auth/login',json=login).get_json()['error'] == 'mfa_required'
    recovered = client.post('/api/auth/login',json={**login,'mfa_code':codes[0]})
    assert recovered.status_code == 200
    account = recovered.get_json()
    assert account['mfa']['recovery_codes_remaining'] == 9
    assert client.post('/api/auth/login',json={**login,'mfa_code':codes[0]}).status_code == 401
    # Export requires a current authenticator code and does not consume a
    # recovery code when it refuses one. Auth credentials never enter the file.
    assert client.post('/api/me/export',json={'current_password':'secret123','mfa_code':codes[1]},headers=auth(account),buffered=True).status_code == 403
    response = client.post('/api/me/export',json={'current_password':'secret123','mfa_code':_totp_at(secret,time.time())},headers=auth(account),buffered=True)
    assert response.status_code == 200
    raw = response.get_data(as_text=True)
    assert secret not in raw and all(code not in raw for code in codes)
    assert client.get('/api/me',headers=auth(account)).get_json()['mfa']['recovery_codes_remaining'] == 9


def test_export_readable_play_history_is_scoped_to_personal_participation(client, app):
    player = register(client, 'export-history@example.test', 'Player')
    other = register(client, 'export-opponent@example.test', 'Opponent')
    me, them = player['user']['id'], other['user']['id']
    court = Court(name='Northgate Courts', city='Long Beach', state='CA', latitude=33, longitude=-117)
    db.session.add(court); db.session.flush()
    played = Game(court_id=court.id, creator_id=them, scheduled_at=utcnow()-timedelta(days=3),
        title='Tuesday singles', status='completed', game_type='ranked', score_team1=11, score_team2=7,
        score_confirmation_kind='opponent', score_version=2,
        score_history=json.dumps([{'kind':'reported','score1':11,'score2':7},{'kind':'confirmed'}]))
    left = Game(court_id=court.id, creator_id=them, scheduled_at=utcnow()-timedelta(days=2),
        title='Session I left', status='completed', score_history='[{"kind":"Private later result"}]')
    private = Game(court_id=court.id, creator_id=them, scheduled_at=utcnow(), title='Not my private session', visibility='private')
    league = League(name='Autumn ladder', court_id=court.id, organizer_id=them, starts_at=utcnow())
    tournament = Tournament(name='Harbor singles cup', court_id=court.id, organizer_id=them, starts_at=utcnow())
    db.session.add_all([played, left, private, league, tournament]); db.session.flush()
    db.session.add_all([GamePlayer(game_id=played.id,user_id=me,team=1,rating_delta=12),
        GamePlayer(game_id=played.id,user_id=them,team=2,rating_delta=-12),
        GameSessionAttendance(game_id=left.id,user_id=me,rsvp_status='left',history='[{"kind":"left"}]'),
        LeagueMember(league_id=league.id,user_id=me,box=1)])
    league_match = LeagueMatch(league_id=league.id,round=1,box=1,player1_id=me,player2_id=them,
        scheduled_at=utcnow(), scheduled_court_id=court.id,score1=11,score2=5,winner_id=me,result_version=2)
    unrelated_league_match = LeagueMatch(league_id=league.id,round=2,box=1,player1_id=them,player2_id=them)
    mine = TournamentEntry(tournament_id=tournament.id,player1_id=me)
    theirs = TournamentEntry(tournament_id=tournament.id,player1_id=them)
    db.session.add_all([league_match,unrelated_league_match,mine,theirs]); db.session.flush()
    tournament_match = TournamentMatch(tournament_id=tournament.id,entry1_id=mine.id,entry2_id=theirs.id,
        score1=11,score2=8,winner_entry_id=mine.id,game_scores_json='[{"score1":11,"score2":8}]',result_version=2)
    original = Message(sender_id=them,recipient_id=me,body='Never export this quoted original')
    db.session.add_all([tournament_match,original]); db.session.flush()
    db.session.add(Message(sender_id=me,recipient_id=them,body='Only my reply belongs here',reply_to_id=original.id))
    db.session.add_all([
        CompetitionResultEvent(competition_type='league',match_id=league_match.id,actor_id=them,action='confirmed',version=2,score1=11,score2=5),
        CompetitionResultEvent(competition_type='league',match_id=unrelated_league_match.id,actor_id=them,action='disputed',version=1,reason='Other match confidential reason'),
        CompetitionResultEvent(competition_type='tournament',match_id=tournament_match.id,actor_id=them,action='confirmed',version=2,score1=11,score2=8),
    ]); db.session.commit()
    response = client.post('/api/me/export', json={'current_password':'secret123'}, headers=auth(player), buffered=True)
    assert response.status_code == 200
    data = response.get_json()
    assert [row['title'] for row in data['sessions']] == ['Tuesday singles','Session I left']
    assert data['sessions'][0]['court']['name'] == 'Northgate Courts'
    assert data['sessions'][0]['result']['your_rating_change'] == 12
    assert data['sessions'][0]['result']['history'][-1]['kind'] == 'confirmed'
    assert data['sessions'][1]['result'] is None
    assert data['attendance'][0]['history'] == [{'kind':'left'}]
    assert data['league_memberships'][0]['league']['name'] == 'Autumn ladder'
    assert len(data['league_matches']) == 1
    assert data['league_matches'][0]['player2']['name'] == 'Opponent'
    assert data['tournament_matches'][0]['tournament']['name'] == 'Harbor singles cup'
    assert data['tournament_matches'][0]['games'] == [{'score1':11,'score2':8}]
    assert len(data['competition_result_history']) == 2
    assert data['sent_messages'][0]['reply_to_id'] == original.id
    for forbidden in ['Not my private session','Private later result','Never export this quoted original','Other match confidential reason']:
        assert forbidden not in response.get_data(as_text=True)
