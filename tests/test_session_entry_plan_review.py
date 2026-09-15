"""A new roster commitment applies only to the playing terms actually reviewed."""
from datetime import timedelta
import pytest
from tests.test_game_planning_fields import app, client, register, auth, create_payload
from backend.models import Game, GamePlayer, GameWaitlist, db, utcnow


def offered_plan(client, offer=False):
    host=register(client)
    guest=client.post('/api/auth/register',json={'email':'entry@example.test','display_name':'Entry guest','password':'synthetic-password'}).get_json()
    game=client.post('/api/games',headers=auth(host),json=create_payload(cost_cents=0,max_players=4)).get_json()
    if offer:
        db.session.add(GameWaitlist(game_id=game['id'],user_id=guest['user']['id'],offer_status='offered',offered_at=utcnow(),offer_expires_at=utcnow()+timedelta(minutes=10)))
        db.session.commit()
    return host,guest,game


@pytest.mark.parametrize('offer',[False,True])
@pytest.mark.parametrize('change',[{'cost_cents':1200},{'court_access':'booking_needed'}, {'duration_minutes':120}, {'court_number':'South entrance'}, {'play_style':'mixed'}, {'max_players':6}])
def test_changed_plan_requires_fresh_review_without_taking_a_place(client,offer,change):
    host,guest,game=offered_plan(client,offer)
    path=f"/api/games/{game['id']}"
    viewed=client.get(path,headers=auth(guest)).get_json()
    changed=client.patch(path,headers=auth(host),json=change)
    assert changed.status_code==200,changed.get_json()
    endpoint=path+('/waitlist/respond' if offer else '/join')
    payload={'accept':True,'expected_plan_token':viewed['plan_token']}
    stale=client.post(endpoint,headers=auth(guest),json=payload)
    assert stale.status_code==409,stale.get_json()
    assert stale.get_json()['error']=='game_plan_review_required'
    fresh=stale.get_json()['game']
    assert not fresh['is_joined'] and fresh['plan_token']!=viewed['plan_token']
    assert GamePlayer.query.filter_by(game_id=game['id'],user_id=guest['user']['id']).count()==0
    if offer:assert GameWaitlist.query.filter_by(game_id=game['id'],user_id=guest['user']['id']).one().offer_status=='offered'
    accepted=client.post(endpoint,headers=auth(guest),json={**payload,'expected_plan_token':fresh['plan_token']})
    assert accepted.status_code==200,accepted.get_json()
    assert accepted.get_json()['is_joined'] and accepted.get_json()['rsvp_counts']['confirmed']==2
    assert client.post(endpoint,headers=auth(guest),json=payload).status_code==200


@pytest.mark.parametrize('offer',[False,True])
def test_missing_snapshot_requires_review_and_title_edits_do_not_invalidate_terms(client,offer):
    host,guest,game=offered_plan(client,offer)
    path=f"/api/games/{game['id']}"
    endpoint=path+('/waitlist/respond' if offer else '/join')
    missing=client.post(endpoint,headers=auth(guest),json={'accept':True})
    assert missing.status_code==409 and missing.get_json()['error']=='game_plan_review_required'
    token=missing.get_json()['game']['plan_token']
    assert client.patch(path,headers=auth(host),json={'title':'New friendly title'}).status_code==200
    assert client.post(endpoint,headers=auth(guest),json={'accept':True,'expected_plan_token':token}).status_code==200


def test_plan_review_does_not_expose_private_sessions(client):
    host,guest,game=offered_plan(client)
    row=db.session.get(Game,game['id']);row.visibility='private';db.session.commit()
    response=client.post(f"/api/games/{game['id']}/join",headers=auth(guest),json={})
    assert response.status_code==403
    assert 'game' not in response.get_json()
