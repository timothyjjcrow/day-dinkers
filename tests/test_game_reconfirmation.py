"""A material plan change retains a place without silently renewing consent."""
from datetime import timedelta
import pytest
from tests.test_game_planning_fields import app, client, register, auth, create_payload
from backend.models import Game, GamePlayer, db, utcnow


def joined_plan(client, **fields):
    host=register(client)
    guest=client.post('/api/auth/register',json={'email':'guest@example.test','display_name':'Guest','password':'synthetic-password'}).get_json()
    game=client.post('/api/games',headers=auth(host),json=create_payload(cost_cents=0,**fields)).get_json()
    assert client.post(f"/api/games/{game['id']}/join",headers=auth(guest),json={}).status_code==200
    return host,guest,game


@pytest.mark.parametrize('change',[{'cost_cents':1200},{'court_access':'booking_needed'},{'court_number':'South entrance'},{'play_style':'mixed'},{'duration_minutes':120}])
def test_changed_commitment_requires_review_on_attend_and_repeated_join(client,change):
    host,guest,game=joined_plan(client)
    path=f"/api/games/{game['id']}"
    assert client.patch(path,headers=auth(host),json=change).status_code==200
    for who in [host,guest]:
        detail=client.get(path,headers=auth(who)).get_json()
        by_id={p['user_id']:p for p in detail['players']}
        assert by_id[guest['user']['id']]['rsvp_status']=='needs_confirmation'
        assert detail['rsvp_counts']=={'confirmed':1,'needs_confirmation':1,'reserved':0}
    detail=client.get(path,headers=auth(guest)).get_json()
    assert detail['is_joined'] and detail['commitment_confirmation_due'] and detail['attendance_confirmation_due']
    assert detail['spots_left']==6
    for route in ['/attend','/join']:
        rejected=client.post(path+route,headers=auth(guest),json={})
        assert rejected.status_code==409 and rejected.get_json()['error']=='game_commitment_changed'
    expected=detail['my_commitment_requested_at']
    accepted=client.post(path+'/attend',headers=auth(guest),json={'expected_commitment_requested_at':expected})
    assert accepted.status_code==200,accepted.get_json()
    assert not accepted.get_json()['attendance_confirmation_due']
    assert accepted.get_json()['rsvp_counts']['confirmed']==2
    assert client.post(path+'/attend',headers=auth(guest),json={'expected_commitment_requested_at':expected}).status_code==200


def test_second_edit_rejects_a_stale_confirmation_and_title_only_does_not_reask(client):
    host,guest,game=joined_plan(client)
    path=f"/api/games/{game['id']}"
    client.patch(path,headers=auth(host),json={'cost_cents':1000})
    old=client.get(path,headers=auth(guest)).get_json()['my_commitment_requested_at']
    client.patch(path,headers=auth(host),json={'cost_cents':1500})
    stale=client.post(path+'/attend',headers=auth(guest),json={'expected_commitment_requested_at':old})
    assert stale.status_code==409
    fresh=stale.get_json()['game']
    assert fresh['cost_cents']==1500
    accepted=client.post(path+'/attend',headers=auth(guest),json={'expected_commitment_requested_at':fresh['my_commitment_requested_at']})
    assert accepted.status_code==200
    client.patch(path,headers=auth(host),json={'title':'New title'})
    assert not client.get(path,headers=auth(guest)).get_json()['attendance_confirmation_due']


def test_reschedule_requests_confirmation_immediately_without_waiting_for_reminders(client):
    host,guest,game=joined_plan(client)
    path=f"/api/games/{game['id']}"
    response=client.post(path+'/reschedule',headers=auth(host),json={'scheduled_at':(utcnow()+timedelta(days=3)).isoformat()+'Z'})
    assert response.status_code==200,response.get_json()
    detail=client.get(path,headers=auth(guest)).get_json()
    assert detail['attendance_confirmation_due'] and detail['commitment_confirmation_due']
    row=GamePlayer.query.filter_by(game_id=game['id'],user_id=guest['user']['id']).one()
    assert row.day_reminded_at is None and row.reminded_at is None
    assert row.commitment_requested_at is not None


def test_following_date_edit_preserves_prior_confirmation(client):
    host,guest,game=joined_plan(client,recurrence='weekly',recurrence_timezone='UTC')
    dates=Game.query.filter_by(recurrence_series_id=game['id']).order_by(Game.scheduled_at).all()
    # Join a second dated occurrence explicitly, then change that and future dates.
    later=dates[1]
    assert client.post(f'/api/games/{later.id}/join',headers=auth(guest),json={}).status_code==200
    changed=client.patch(f'/api/games/{later.id}',headers=auth(host),json={'edit_scope':'following_dates','cost_cents':800})
    assert changed.status_code==200,changed.get_json()
    earlier=client.get(f"/api/games/{game['id']}",headers=auth(guest)).get_json()
    future=client.get(f'/api/games/{later.id}',headers=auth(guest)).get_json()
    assert not earlier['commitment_confirmation_due'] and earlier['cost_cents']==0
    assert future['commitment_confirmation_due'] and future['cost_cents']==800
