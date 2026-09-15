"""Scheduling advice uses public hours without writing or implying bookings."""
import json
import pytest
from test_business_reviewed_versions import app, client, live, current, versioned
from test_business_governance import register, auth
from backend.app import db
from backend.models import Court, Game, BusinessProfile


def check(client, token, starts, duration=90, court_id=1):
    return client.post(f'/api/courts/{court_id}/planning-times', headers=auth(token),
                       json={'starts_at': starts, 'duration_minutes': duration})


def test_split_hours_check_entire_duration_and_preserve_unknown_days(app, client):
    player = register(client, 'planner@example.test')
    with app.app_context():
        db.session.get(Court, 1).structured_hours = json.dumps({'timezone':'America/Los_Angeles',
            'tue':[{'open':'08:00','close':'10:00'},{'open':'11:00','close':'20:00'}]})
        db.session.commit()
    result = check(client, player['token'], ['2026-09-15T14:00:00Z','2026-09-15T15:00:00Z','2026-09-15T16:00:00Z','2026-09-16T16:00:00Z'])
    assert result.status_code == 200
    data = result.get_json()
    assert data['hours_source'] == 'community'
    assert [row['hours_conflict'] for row in data['items']] == [True,False,True,False]
    assert data['items'][-1]['is_open'] is None
    assert check(client, player['token'], ['2026-09-15T16:00:00Z'], 60).get_json()['items'][0]['hours_conflict'] is False
    assert check(client, player['token'], ['2026-09-15T16:00:00Z'], None).get_json()['items'][0]['hours_conflict'] is False
    with app.app_context():
        assert Game.query.count() == 0


def test_overnight_and_exception_use_same_court_rules(app, client):
    player = register(client, 'night@example.test')
    with app.app_context():
        db.session.get(Court, 1).structured_hours = json.dumps({'timezone':'America/Los_Angeles',
            'mon':[{'open':'22:00','close':'02:00'}], 'tue':[],
            'exceptions':{'2026-09-22':[]}})
        db.session.commit()
    rows = check(client, player['token'], ['2026-09-15T08:00:00Z','2026-09-22T08:00:00Z'],30).get_json()['items']
    assert [row['hours_conflict'] for row in rows] == [False,True]


def test_only_reviewed_venue_hours_replace_community(app, client, live):
    owner,bid=live
    with app.app_context():
        db.session.get(Court,1).structured_hours=json.dumps({'timezone':'America/Chicago','tue':[{'open':'06:00','close':'23:00'}]})
        db.session.get(BusinessProfile,bid).structured_hours=json.dumps({'timezone':'America/Chicago','tue':[{'open':'08:00','close':'20:00'}]})
        db.session.commit()
    changed=client.patch(f'/api/businesses/{bid}',headers=versioned(owner,current(client,owner,bid)),json={'structured_hours':{'timezone':'America/Chicago','tue':[{'open':'06:00','close':'23:00'}]}})
    assert changed.status_code == 200
    data=check(client,owner['token'],['2026-09-15T12:00:00Z']).get_json()
    assert data['hours_source']=='venue'
    assert data['items'][0]['hours_conflict'] is True


@pytest.mark.parametrize('starts,duration', [([],90),(['2026-09-15T16:00:00Z']*11,90),(['bad'],90),(['2026-09-15T16:00:00'],90),([None],90),(['9999-09-15T16:00:00Z'],90),(['2026-09-15T16:00:00Z'],True),(['2026-09-15T16:00:00Z'],721)])
def test_invalid_batches_are_rejected(app, client, starts, duration):
    player=register(client,'invalid@example.test')
    assert check(client,player['token'],starts,duration).status_code==400


def test_auth_and_unavailable_court(app, client):
    assert client.post('/api/courts/1/planning-times',json={}).status_code==401
    player=register(client,'closed@example.test')
    with app.app_context():
        db.session.get(Court,1).closed=True
        db.session.commit()
    assert check(client,player['token'],['2026-09-15T16:00:00Z']).status_code==404
