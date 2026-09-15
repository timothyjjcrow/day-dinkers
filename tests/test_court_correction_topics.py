"""Independent court topics preserve pending work and apply through ordinary consensus."""
from test_business_governance import app, client, auth, register
from backend.app import db
from backend.models import CourtEditSuggestion
from backend.routes.courts import _court_suggestion_payload


def test_later_topic_keeps_prior_pending_correction_and_partial_confirmation_history(client):
    first=register(client,'topic-first@example.test');second=register(client,'topic-second@example.test')
    original=client.get('/api/courts/1').get_json()
    for payload in [{'num_courts':6},{'fees':'$8 drop-in'}]:
        res=client.post('/api/courts/1/suggest',headers=auth(first['token']),json=payload)
        assert res.status_code==201,res.get_json()
        assert res.get_json()['applied_fields']==[]
    pending=client.get('/api/courts/1/suggestions',headers=auth(first['token'])).get_json()
    assert pending['my_history'][0]['changes']=={'num_courts':6,'fees':'$8 drop-in'}
    assert pending['my_history'][0]['before']=={'num_courts':original['num_courts'],'fees':original['fees']}
    result=client.post('/api/courts/1/suggestions/decision',headers=auth(second['token']),json={'field':'num_courts','value':6,'decision':'confirm'}).get_json()
    assert result['applied_fields']==['num_courts'] and result['court']['num_courts']==6
    history=client.get('/api/courts/1/suggestions',headers=auth(first['token'])).get_json()['my_history']
    assert any(row['status']=='pending' and row['changes']=={'fees':'$8 drop-in'} for row in history)
    assert any(row['status']=='applied' and row['changes']=={'num_courts':6} for row in history)


def test_other_topic_preserves_pending_closure_evidence_and_revises_only_its_own_value(app,client):
    person=register(client,'topic-closure@example.test')
    headers=auth(person['token'])
    for body in [{'closed':True,'evidence':'Posted closure notice at the north gate.'},{'fees':'$8 drop-in'},{'fees':'$10 drop-in'}]:
        assert client.post('/api/courts/1/suggest',headers=headers,json=body).status_code==201
    with app.app_context():
        row=CourtEditSuggestion.query.filter_by(court_id=1,user_id=person['user']['id'],status='pending').one()
        payload=_court_suggestion_payload(row)
        assert payload['closed'] is True and payload['_evidence']=='Posted closure notice at the north gate.'
        assert payload['fees']=='$10 drop-in'
    assert client.get('/api/courts/1').get_json()['closed'] is False
