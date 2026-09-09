"""Arrival provenance and disruptive court corrections have honest server contracts."""
import time
from datetime import timedelta

from test_business_governance import app, client, auth, register, make_operator, enable_mfa
from backend.app import db
from backend.models import CheckIn, Court, CourtEditSuggestion, Game, GamePlayer, Notification, User, utcnow
from backend.services.mfa import _totp_at


def checkin(client, person, payload):
    return client.post('/api/courts/1/checkin', headers=auth(person['token']), json=payload)


def test_explicit_self_report_requires_confirmation_and_never_mints_proof(client):
    person = register(client, 'indoor@example.test')
    assert checkin(client, person, {'presence_intent':'self_reported'}).status_code == 400
    assert checkin(client, person, {'presence_intent':'self_reported', 'confirm_at_court':True, 'presence_location':{}}).status_code == 400
    response = checkin(client, person, {'presence_intent':'self_reported', 'confirm_at_court':True, 'looking_for_game':True})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['presence']['presence_source'] == 'self_reported'
    assert data['presence']['location_verified_at'] is None
    assert data['presence_verified'] is False
    assert not data.get('instant_rally_presence_proof')
    assert client.get('/api/courts/1').get_json()['players_here'] == []


def test_legacy_presence_is_unverified_and_verified_presence_expires(client):
    person = register(client, 'legacy-arrival@example.test')
    legacy = checkin(client, person, {})
    assert legacy.get_json()['presence']['presence_source'] == 'self_reported'
    verified = checkin(client, person, {'presence_intent':'manual_checkin', 'presence_location':{'latitude':30.1,'longitude':-97.1,'accuracy_meters':10}})
    assert verified.status_code == 200, verified.get_json()
    assert verified.get_json()['presence']['location_verified_at']
    row = CheckIn.query.filter_by(user_id=person['user']['id'],checked_out_at=None).one()
    row.checked_in_at = row.last_presence_ping_at = utcnow() - timedelta(days=1)
    db.session.commit()
    assert client.get('/api/me',headers=auth(person['token'])).get_json()['presence']['checked_in'] is False
    renewed = checkin(client, person, {'presence_intent':'self_reported','confirm_at_court':True})
    assert renewed.get_json()['presence']['location_verified_at'] is None


def test_map_detail_sources_and_private_identities_agree(client):
    first = register(client, 'verified-presence@example.test')
    second = register(client, 'quiet-presence@example.test')
    checkin(client, first, {'presence_intent':'manual_checkin','presence_location':{'latitude':30.1,'longitude':-97.1,'accuracy_meters':10},'looking_for_game':True})
    checkin(client, second, {'presence_intent':'self_reported','confirm_at_court':True})
    detail = client.get('/api/courts/1').get_json()
    card = next(row for row in client.get('/api/courts').get_json()['items'] if row['id'] == 1)
    assert detail['presence_summary'] == card['presence_summary']
    assert card['presence_summary']['location_confirmed'] == 1
    assert card['presence_summary']['self_reported'] == 1
    assert detail['players_here'] == []
    viewer = register(client, 'presence-viewer@example.test')
    people = client.get('/api/courts/1',headers=auth(viewer['token'])).get_json()['players_here']
    assert [row['id'] for row in people] == [first['user']['id']]
    assert people[0]['presence_source'] == 'location_confirmed'
    db.session.get(User, second['user']['id']).deleted_at = utcnow()
    db.session.commit()
    assert next(row for row in client.get('/api/courts').get_json()['items'] if row['id']==1)['players_here'] == 1


def test_two_reports_keep_court_open_until_review_and_preview_impact(client, app):
    people = [register(client, f'closure-{i}@example.test') for i in range(2)]
    for person in people:
        response = client.post('/api/courts/1/suggest', headers=auth(person['token']), json={'closed':True,'evidence':'City notice says courts were removed on Monday.'})
        assert response.status_code == 201, response.get_json()
        assert response.get_json()['applied_fields'] == []
    assert client.get('/api/courts/1').get_json()['closed'] is False
    suggestions = client.get('/api/courts/1/suggestions',headers=auth(people[0]['token'])).get_json()
    assert suggestions['items'][0]['requires_review'] is True
    assert suggestions['items'][0]['needed'] is None
    assert client.get('/api/operator/courts/corrections',headers=auth(people[0]['token'])).status_code == 403
    reviewer = register(client,'closure-reviewer@example.test')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    game = Game(court_id=1,creator_id=people[0]['user']['id'],scheduled_at=utcnow()+timedelta(days=1),game_type='casual',max_players=4)
    db.session.add(game); db.session.flush()
    db.session.add(GamePlayer(game_id=game.id,user_id=people[0]['user']['id']))
    db.session.commit()
    queued = client.get('/api/operator/courts/corrections',headers=auth(reviewer['token'])).get_json()['items'][0]
    assert queued['impact']['upcoming_sessions'] == 1 and queued['impact']['players'] == 1
    path = f"/api/operator/courts/corrections/{queued['id']}/review"
    body = {'decision':'approve','expected_closed':False,'review_note':'Checked the dated city removal notice and location.'}
    assert client.post(path,json=body,headers=auth(reviewer['token'])).status_code == 403
    secret, token, _ = enable_mfa(client, reviewer['token'])
    body['mfa_code'] = _totp_at(secret,time.time())
    response = client.post(path,json={**body,'expected_closed':True},headers=auth(token))
    assert response.status_code == 409
    response = client.post(path,json=body,headers=auth(token))
    assert response.status_code == 200, response.get_json()
    assert client.get('/api/courts/1').get_json()['closed'] is True
    assert db.session.get(Game,game.id).status == 'upcoming'
    assert Notification.query.filter_by(kind='court_status',related_game_id=game.id).count() == 1
    assert CourtEditSuggestion.query.filter_by(status='applied').count() == 2
    assert client.post(path,json=body,headers=auth(token)).status_code == 409
    history = client.get('/api/operator/courts/corrections/'+str(queued['id']),headers=auth(token)).get_json()['history']
    assert history[0]['review_note'] == body['review_note']


def test_closure_review_preserves_unconfirmed_amenity_and_allows_reopening(client, app):
    person = register(client,'closure-mixed@example.test')
    reviewer = register(client,'closure-mixed-reviewer@example.test')
    make_operator(app,reviewer['user']['id'],'reviewer')
    secret, token, _ = enable_mfa(client,reviewer['token'])
    assert client.post('/api/courts/1/suggest',headers=auth(person['token']),json={'closed':True}).status_code == 400
    response = client.post('/api/courts/1/suggest',headers=auth(person['token']),json={'closed':True,'num_courts':9,'evidence':'New sign at the entrance says permanently closed.'})
    assert response.status_code == 201
    item = client.get('/api/operator/courts/corrections',headers=auth(token)).get_json()['items'][0]
    response = client.post(f"/api/operator/courts/corrections/{item['id']}/review",headers=auth(token),json={'decision':'reject','expected_closed':False,'mfa_code':_totp_at(secret,time.time()),'review_note':'Confirmed this is a temporary resurfacing closure.'})
    assert response.status_code == 200, response.get_json()
    pending = client.get('/api/courts/1/suggestions',headers=auth(person['token'])).get_json()
    assert [row['field'] for row in pending['items']] == ['num_courts']
    assert any(row['status']=='declined' for row in pending['my_history'])
