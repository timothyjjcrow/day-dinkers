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


def test_withdraw_is_owner_only_and_keeps_other_topics_without_a_rejection(client):
    owner=register(client,'withdraw-owner@example.test');other=register(client,'withdraw-other@example.test')
    headers=auth(owner['token'])
    proposal={'fees':'$8','closed':True,'evidence':'Closure notice on the north gate.'}
    assert client.post('/api/courts/1/suggest',headers=headers,json=proposal).status_code==201
    decision={'field':'fees','value':'$8','decision':'withdraw'}
    assert client.post('/api/courts/1/suggestions/decision',headers=auth(other['token']),json=decision).status_code==403
    result=client.post('/api/courts/1/suggestions/decision',headers=headers,json=decision)
    assert result.status_code==200
    data=result.get_json()
    assert [item['field'] for item in data['items']]==['closed']
    assert data['current_values']['closed'] is False
    assert any(row['status']=='withdrawn' and row['changes']=={'fees':'$8'} for row in data['my_history'])
    assert any(row['status']=='pending' and row['changes']=={'closed':True} for row in data['my_history'])
    assert not any(row['status']=='rejected' for row in data['my_history'])
    assert client.post('/api/courts/1/suggestions/decision',headers=headers,json=decision).status_code==404
    final=client.post('/api/courts/1/suggestions/decision',headers=headers,json={'field':'closed','value':True,'decision':'withdraw'}).get_json()
    assert final['items']==[] and final['court']['closed'] is False
    assert len([row for row in final['my_history'] if row['status']=='withdrawn'])==2


def test_decision_returns_fresh_comparison_and_history_without_another_read(client):
    owner=register(client,'review-owner@example.test');reviewer=register(client,'review-confirm@example.test')
    assert client.post('/api/courts/1/suggest',headers=auth(owner['token']),json={'fees':'$8'}).status_code==201
    before=client.get('/api/courts/1/suggestions',headers=auth(reviewer['token'])).get_json()
    assert before['current_values']['fees']!='$8'
    result=client.post('/api/courts/1/suggestions/decision',headers=auth(reviewer['token']),json={'field':'fees','value':'$8','decision':'confirm'}).get_json()
    assert result['current_values']['fees']=='$8' and result['items']==[]
    assert result['my_history'][0]['status']=='applied'
    assert result['my_history'][0]['before']['fees']==before['current_values']['fees']


def test_switching_to_another_pending_value_records_previous_choice_and_keeps_other_fields(client):
    owner=register(client,'switch-owner@example.test');other=register(client,'switch-other@example.test')
    headers=auth(owner['token'])
    assert client.post('/api/courts/1/suggest',headers=headers,json={'fees':'$8','num_courts':9}).status_code==201
    assert client.post('/api/courts/1/suggest',headers=auth(other['token']),json={'fees':'$10'}).status_code==201
    result=client.post('/api/courts/1/suggestions/decision',headers=headers,json={'field':'fees','value':'$10','decision':'confirm'}).get_json()
    assert result['court']['fees']=='$10'
    assert any(row['status']=='withdrawn' and row['changes']=={'fees':'$8'} for row in result['my_history'])
    assert any(row['status']=='applied' and row['changes']=={'fees':'$10'} for row in result['my_history'])
    assert any(row['status']=='pending' and row['changes']=={'num_courts':9} for row in result['my_history'])
