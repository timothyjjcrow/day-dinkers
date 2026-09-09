"""Owner analytics and operator task filters reflect recorded, scoped activity."""
from datetime import timedelta
from test_business_reviewed_versions import app, client, live, current, versioned
from test_business_governance import auth, register, create_profile, make_operator
from backend.app import db
from backend.models import BusinessProfile, BusinessScheduleItem, BusinessClaim, utcnow
from backend.integrations.models import BusinessBookingEvent


def test_manual_session_click_subject_is_reviewed_validated_and_preserved(app, client, live):
    owner, bid = live
    with app.app_context():
        today = utcnow().date()
        session = BusinessScheduleItem(business_id=bid, title='Approved morning doubles', kind='open_play', day_of_week='daily', start_time='09:00', end_time='11:00', timezone='UTC', booking_url='https://official.example/session')
        db.session.add(session); db.session.commit(); sid = session.id
    original = current(client, owner, bid)
    changed = client.put(f'/api/businesses/{bid}/schedule', json={'items':[{**original['schedule'][0], 'title':'Unreviewed title', 'booking_url':'https://official.example/proposed'}]}, headers=versioned(owner, original))
    assert changed.status_code == 200
    body = {'client_event_id':'session-click-1', 'action':'open_play', 'schedule_item_id':sid, 'schedule_occurrence_on':today.isoformat(), 'subject_label':'Forged label'}
    recorded = client.post(f'/api/businesses/{bid}/booking-clicks', json=body)
    assert recorded.status_code == 201, recorded.get_json()
    assert client.post(f'/api/businesses/{bid}/booking-clicks', json=body).status_code == 200
    invalid = client.post(f'/api/businesses/{bid}/booking-clicks', json={**body,'client_event_id':'bad','schedule_item_id':99999})
    assert invalid.status_code == 400
    analytics = client.get(f'/api/businesses/{bid}/analytics?range=7d', headers=auth(owner['token'])).get_json()
    assert analytics['top_sessions'] == [{'title':'Approved morning doubles','date':today.isoformat(),'source':'venue schedule','clicks':1}]
    assert 'Forged label' not in str(analytics) and 'Unreviewed title' not in str(analytics)
    assert analytics['conversions'] is None and analytics['conversion_rate'] is None


def test_analytics_comparison_excludes_future_and_preserves_unknown_names(app, client, live):
    owner, bid = live
    with app.app_context():
        now = utcnow()
        for key, days, action, label, on in [('prior',-8,'booking','',None),('now',-1,'booking','',None),('future',1,'booking','',None),('legacy',-1,'lesson','',now.date())]:
            db.session.add(BusinessBookingEvent(business_id=bid,event_type='click',event_key=key,action=action,occurred_at=now+timedelta(days=days),subject_label=label,schedule_occurrence_on=on))
        db.session.commit()
    result = client.get(f'/api/businesses/{bid}/analytics?range=7d',headers=auth(owner['token']))
    assert result.status_code == 200, result.get_json()
    data=result.get_json()
    assert data['booking_clicks'] == 1 and data['previous']['booking_clicks'] == 1
    assert data['lesson_clicks'] == 1
    assert data['top_sessions'][0]['title'] == 'Session name not recorded'
    assert data['conversion_rate'] is None
    assert client.get(f'/api/businesses/{bid}/analytics?range=bad',headers=auth(owner['token'])).status_code == 400
    assert client.get(f'/api/businesses/{bid}/analytics').status_code == 401


def test_operator_search_assignment_and_overdue_filter_before_queue_limit(app, client):
    owner=register(client,'venue-owner-task@example.test')
    first=create_profile(client,owner['token'],1,name='Seaside Club')
    second=create_profile(client,owner['token'],2,name='Mountain Club')
    reviewer=register(client,'venue-reviewer-task@example.test')
    make_operator(app,reviewer['user']['id'],'reviewer')
    with app.app_context():
        early=BusinessClaim.query.filter_by(business_id=first['id']).first()
        later=BusinessClaim.query.filter_by(business_id=second['id']).first()
        early.due_at=utcnow()-timedelta(days=1)
        early.assigned_operator_id=reviewer['user']['id']
        later.due_at=utcnow()+timedelta(days=1)
        db.session.commit()
    root='/api/operator/business/queue'
    assert client.get(root,headers=auth(owner['token'])).status_code == 403
    headers=auth(reviewer['token'])
    assert [item['business_name'] for item in client.get(root+'?q=seaside',headers=headers).get_json()['claims']] == ['Seaside Club']
    assert [item['business_name'] for item in client.get(root+'?assignment=mine&overdue=1',headers=headers).get_json()['claims']] == ['Seaside Club']
    assert [item['business_name'] for item in client.get(root+'?assignment=unassigned',headers=headers).get_json()['claims']] == ['Mountain Club']
    assert client.get(root+'?assignment=invalid',headers=headers).status_code == 400
    assert client.get(root+'?q=%25',headers=headers).get_json()['claims'] == []


def test_team_resend_rotates_pending_token_and_recovers_expired_invitation(app, client, live, monkeypatch):
    import re
    from backend.models import BusinessStaffInvitation
    from backend.email_delivery import EmailDeliveryUnavailable
    owner, bid = live
    staff = register(client, 'new-venue-editor@example.test')
    path = f'/api/businesses/{bid}/team/invitations'
    headers = auth(owner['token'])
    payload = {'email': staff['user']['email'], 'role': 'editor'}

    def invite():
        response = client.post(path, json=payload, headers=headers)
        assert response.status_code == 201, response.get_json()
        token = re.search(r'#business-invitation=([^\s]+)', app.extensions['email_outbox'][-1]['text']).group(1)
        return response.get_json()['invitation'], token

    original, old_token = invite()
    replacement, replacement_token = invite()
    assert original['id'] == replacement['id']
    assert replacement_token != old_token
    assert client.post(f'/api/business-invitations/{old_token}/accept', headers=auth(staff['token'])).status_code == 404
    with app.app_context():
        expired = db.session.get(BusinessStaffInvitation, replacement['id'])
        expired.expires_at = utcnow()-timedelta(minutes=1)
        db.session.commit()
        assert db.session.get(BusinessStaffInvitation, replacement['id']).expires_at < utcnow()
    team = client.get(f'/api/businesses/{bid}/team', headers=headers).get_json()
    assert len(team['invitations']) == 1
    assert team['invitations'][0]['status'] == 'expired', (team['invitations'], utcnow().isoformat())
    renewed, renewed_token = invite()
    assert renewed['id'] != replacement['id']
    team = client.get(f'/api/businesses/{bid}/team', headers=headers).get_json()
    assert [row['id'] for row in team['invitations']] == [renewed['id']]
    assert team['invitations'][0]['status'] == 'pending'
    with monkeypatch.context() as patch:
        def unavailable(**kwargs):
            raise EmailDeliveryUnavailable('Synthetic delivery failure')
        patch.setattr('backend.routes.business_governance.send_transactional_email', unavailable)
        assert client.post(path, json=payload, headers=headers).status_code == 503
    # Failed delivery must not invalidate the most recently delivered link.
    accepted = client.post(f'/api/business-invitations/{renewed_token}/accept', headers=auth(staff['token']))
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()['member']['role'] == 'editor'
    assert client.post(f'/api/business-invitations/{replacement_token}/accept', headers=auth(staff['token'])).status_code in {404, 409}
