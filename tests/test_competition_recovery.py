"""Recovery never turns an expired offer/invitation into assumed consent."""
import json
from datetime import timedelta
from test_tournament_waitlist import app, client, full_field, auth
from backend.app import db
from backend.models import Tournament, TournamentEntry, TournamentWaitlist, utcnow
from backend.routes.tournaments import maintain_tournament_waitlists


def test_read_only_capacity_preserves_queue_priority_before_maintenance(client,app):
    host,entered,waiting,url=full_field(client,app)
    client.delete(url+'/register',headers=auth(entered[0]))
    row=TournamentWaitlist.query.filter_by(user_id=waiting[0]['user']['id']).one()
    row.expires_at=utcnow()-timedelta(seconds=1);db.session.commit()
    detail=client.get(url,headers=auth(waiting[0])).get_json()
    assert detail['my_waitlist']['status']=='expired' and detail['registration_spots_left']==0
    assert detail['waitlist_count']==2 and row.status=='offered'  # GET is read-only.
    assert client.post(url+'/register',headers=auth(waiting[2]),json={}).status_code==409
    assert TournamentEntry.query.count()==1


def test_partner_expiry_requires_reviewed_extension_and_fresh_consent(client,app):
    host,entered,waiting,url=full_field(client,app)
    event=Tournament.query.one();event.event_type='doubles';event.starts_at=utcnow()+timedelta(days=3)
    db.session.commit()
    client.delete(url+'/register',headers=auth(entered[0]))
    accepted=client.post(url+'/waitlist/respond',headers=auth(waiting[0]),json={'accept':True,'partner_id':entered[0]['user']['id']})
    assert accepted.status_code==201
    entry=TournamentEntry.query.filter_by(player1_id=waiting[0]['user']['id']).one()
    assert entry.partner_response_deadline_at and entry.player2_id is None
    private=entry.to_dict(waiting[2]['user']['id'],host['user']['id'])
    assert 'partner_history' not in private and 'pending_partner' not in private and 'partner_response_deadline_at' not in private
    entry.partner_response_deadline_at=utcnow()-timedelta(seconds=1);db.session.commit()
    rejected=client.post(url+'/partner/respond',headers=auth(entered[0]),json={'accept':True})
    assert rejected.status_code==409 and rejected.get_json()['error']=='partner_deadline_expired'
    assert entry.player2_id is None and entry.partner_invitee_id is None and entry.partner_status=='needed'
    expired=client.get(url,headers=auth(entered[0])).get_json()
    assert expired['my_partner_action'] is None
    assert expired['my_partner_updates']==[{'entry_id':entry.id,'status':'expired',
        'at':json.loads(entry.partner_history)[-1]['at'],'next_step':'organizer_review'}]
    assert client.get(url,headers=auth(waiting[2])).get_json()['my_partner_updates']==[]
    assert client.patch(url+'/register',headers=auth(waiting[0]),json={'partner_id':entered[0]['user']['id']}).status_code==409
    path=f'{url}/entries/{entry.id}/partner-deadline'
    assert client.post(path,headers=auth(waiting[0]),json={'preview':True}).status_code==403
    plan=client.post(path,headers=auth(host),json={'preview':True}).get_json()
    assert client.post(path,headers=auth(host),json={}).status_code==409
    extended=client.post(path,headers=auth(host),json=plan)
    assert extended.status_code==200,extended.get_json()
    assert entry.partner_invitee_id is None and entry.player2_id is None
    reopened=client.get(url,headers=auth(entered[0])).get_json()['my_partner_updates'][0]
    assert reopened['status']=='expired' and reopened['next_step']=='offer_partner'
    # The recipient projection does not expose the owner's other private history.
    assert set(reopened)=={'entry_id','status','at','next_step'}
    assert client.patch(url+'/register',headers=auth(waiting[0]),json={'partner_id':entered[0]['user']['id']}).status_code==200
    assert client.post(url+'/partner/respond',headers=auth(entered[0]),json={'accept':True}).status_code==200
    assert entry.player2_id==entered[0]['user']['id']
    assert client.get(url,headers=auth(entered[0])).get_json()['my_partner_updates']==[]
    history=json.loads(entry.partner_history)
    assert [row['action'] for row in history]==['invited','deadline_expired','deadline_extended','invited','accepted']
    removed_id=entry.id
    assert client.delete(f'{url}/entries/{removed_id}',headers=auth(host)).status_code==200
    retained=json.loads(event.schedule_history)[-1]
    assert retained['entry_id']==removed_id and retained['partner_history'][-1]['action']=='removed_by_organizer'
    removed=client.get(url,headers=auth(entered[0])).get_json()['my_partner_updates'][0]
    assert removed['status']=='removed' and removed['next_step']=='view_signup'


def test_maintenance_finds_due_partner_without_any_waitlist_and_legacy_null_stays_unknown(client,app):
    host,entered,waiting,url=full_field(client,app)
    event=Tournament.query.one();event.event_type='doubles'
    event.waitlist.clear()
    a,b=event.entries;a.partner_status=b.partner_status='needed'
    a.partner_response_deadline_at=utcnow()-timedelta(seconds=1)
    db.session.commit()
    maintain_tournament_waitlists()
    assert json.loads(a.partner_history)[-1]['action']=='deadline_expired'
    assert b.partner_response_deadline_at is None and json.loads(b.partner_history)==[]
