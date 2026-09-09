"""Consent, capacity, and preserved attendance are exercised through real APIs."""
import json
from datetime import timedelta

from test_game_detail_manage import app, client, register, auth, create_game
from backend.app import db
from backend.models import Game, GamePlayer, GameWaitlist, GameSessionAttendance, utcnow
from backend.routes.games import maintain_game_consent


def fill_and_wait(client):
    host, player, first, second = [register(client, f'consent-{n}', name) for n, name in
                                  enumerate(('Host', 'Player', 'First', 'Second'))]
    game = create_game(client, host)
    assert client.post(f"/api/games/{game['id']}/join", headers=auth(player)).status_code == 200
    for person in (first, second):
        assert client.post(f"/api/games/{game['id']}/waitlist", headers=auth(person)).status_code == 200
    return game, host, player, first, second


def test_waitlist_offer_needs_consent_and_reserves_capacity(client):
    game, host, player, first, second = fill_and_wait(client)
    url = f"/api/games/{game['id']}"
    assert client.post(url+'/leave', headers=auth(player)).status_code == 200
    pending = client.get(url, headers=auth(first)).get_json()
    assert pending['waitlist_offer'] and not pending['is_joined']
    assert pending['spots_left'] == 0 and len(pending['players']) == 1
    assert client.post(url+'/join', headers=auth(second)).status_code == 400
    accepted = client.post(url+'/waitlist/respond', json={'accept':True}, headers=auth(first))
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()['is_joined']
    assert accepted.get_json()['waitlist_offer'] is None
    assert client.post(url+'/waitlist/respond', json={'accept':True}, headers=auth(first)).status_code == 200
    assert GamePlayer.query.filter_by(game_id=game['id']).count() == 2


def test_offer_expiry_advances_fifo_and_rejects_stale_acceptance(client):
    game, host, player, first, second = fill_and_wait(client)
    url = f"/api/games/{game['id']}"
    client.post(url+'/leave', headers=auth(player))
    offer = GameWaitlist.query.filter_by(game_id=game['id'], user_id=first['user']['id']).one()
    offer.offer_expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    maintain_game_consent()
    assert client.post(url+'/waitlist/respond', json={'accept':True}, headers=auth(first)).status_code == 409
    assert client.get(url, headers=auth(second)).get_json()['waitlist_offer']
    passed = client.post(url+'/waitlist/respond', json={'accept':False}, headers=auth(second))
    assert passed.status_code == 200
    assert len(passed.get_json()['players']) == 1


def test_handoff_does_not_change_host_until_recipient_accepts(client):
    host, player, outsider = [register(client,f'host-{n}',name) for n,name in enumerate(('Host','Next','Other'))]
    game = create_game(client,host)
    url = f"/api/games/{game['id']}"
    client.post(url+'/join', headers=auth(player))
    denied = client.post(url+'/leave', headers=auth(host))
    assert denied.status_code == 409
    requested = client.post(url+'/leave',json={'transfer_to_user_id':player['user']['id']},headers=auth(host))
    assert requested.status_code == 202, requested.get_json()
    pending = requested.get_json()['host_handoff']
    assert requested.get_json()['creator_id'] == host['user']['id']
    assert len(requested.get_json()['players']) == 2
    response_url = url+f"/host-handoff/{pending['id']}/respond"
    assert client.post(response_url,json={'accept':True},headers=auth(host)).status_code == 403
    assert client.post(response_url,json={'accept':True},headers=auth(outsider)).status_code == 404
    accepted = client.post(response_url,json={'accept':True},headers=auth(player))
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()['creator_id'] == player['user']['id']
    assert len(accepted.get_json()['players']) == 1
    assert client.post(response_url,json={'accept':True},headers=auth(player)).status_code == 200


def test_completion_keeps_rsvp_history_and_private_access_for_nonattendees(client):
    host, player, absent, outsider = [register(client,f'attend-{n}',name) for n,name in enumerate(('Host','Player','Absent','Other'))]
    game = create_game(client,host,max_players=3)
    url=f"/api/games/{game['id']}"
    for person in (player,absent):
        client.post(url+'/join',headers=auth(person))
    row=db.session.get(Game,game['id'])
    row.visibility='private'; row.scheduled_at=utcnow()-timedelta(hours=1)
    db.session.commit()
    assert client.post(url+'/complete-session',json={},headers=auth(host)).status_code == 400
    recorded=client.post(url+'/complete-session',json={'attendee_user_ids':[host['user']['id'],player['user']['id']]},headers=auth(host))
    assert recorded.status_code == 200, recorded.get_json()
    body=recorded.get_json()
    assert body['attendance_record']['signed_up_count'] == 3
    assert body['attendance_record']['played_count'] == 2
    assert len(body['players']) == 2
    assert client.get(url,headers=auth(absent)).status_code == 200
    assert client.get(url,headers=auth(outsider)).status_code == 404
    ledger=GameSessionAttendance.query.filter_by(game_id=game['id'],user_id=absent['user']['id']).one()
    assert ledger.attended is False
    assert [event['kind'] for event in json.loads(ledger.history)] == ['joined','attendance']
    corrected=client.patch(url+f"/attendance/{absent['user']['id']}",json={'attended':True},headers=auth(absent))
    assert corrected.status_code == 200, corrected.get_json()
    assert corrected.get_json()['attendance_record']['played_count'] == 3
    assert len(corrected.get_json()['players']) == 3
    assert json.loads(ledger.history)[-1]['kind'] == 'attendance_corrected'
    assert client.patch(url+f"/attendance/{host['user']['id']}",json={'attended':False},headers=auth(absent)).status_code == 403


def test_declined_handoff_preserves_host_and_rsvp(client):
    game, host, player, first, second = fill_and_wait(client)
    url=f"/api/games/{game['id']}"
    request=client.post(url+'/host-handoff',json={'target_user_id':player['user']['id'],'leave_on_accept':True},headers=auth(host))
    assert request.status_code == 202
    reply=client.post(url+f"/host-handoff/{request.get_json()['host_handoff']['id']}/respond",
                      json={'accept':False},headers=auth(player))
    assert reply.status_code == 200
    assert reply.get_json()['creator_id'] == host['user']['id']
    assert len(reply.get_json()['players']) == 2


def test_recurring_handoff_changes_only_accepted_scope_and_future_defaults(client):
    from test_game_recurrence_patterns import recurring_payload
    host=register(client,'rec-host','Host'); player=register(client,'rec-next','Next')
    game=client.post('/api/games',json=recurring_payload(),headers=auth(host)).get_json()
    dates=Game.query.filter_by(recurrence_series_id=game['id']).order_by(Game.scheduled_at).all()
    selected=dates[1]
    url=f'/api/games/{selected.id}'
    client.post(url+'/join',headers=auth(player))
    request=client.post(url+'/host-handoff',json={'target_user_id':player['user']['id'],
                        'edit_scope':'following_dates','leave_on_accept':True},headers=auth(host))
    assert request.status_code == 202, request.get_json()
    result=client.post(url+f"/host-handoff/{request.get_json()['host_handoff']['id']}/respond",json={'accept':True},headers=auth(player))
    assert result.status_code == 200, result.get_json()
    assert dates[0].creator_id == host['user']['id']
    assert all(row.creator_id == player['user']['id'] for row in dates[1:])
    assert json.loads(dates[0].recurrence_template)['creator_id'] == player['user']['id']
    assert all(not any(p.user_id==host['user']['id'] for p in row.players) for row in dates[1:])


def test_offer_is_actionable_in_activity_and_agenda_until_acceptance(client):
    game, host, player, first, second = fill_and_wait(client)
    url=f"/api/games/{game['id']}"
    assert client.post(url+'/leave',headers=auth(player)).status_code == 200
    activity=client.get('/api/notifications?filter=action',headers=auth(first)).get_json()
    offers=[n for n in activity['items'] if n['kind']=='game_waitlist_offer']
    assert len(offers)==1 and offers[0]['needs_action']
    assert offers[0]['action_url']==f"/#game/{game['id']}"
    agenda=client.get('/api/play/home',headers=auth(first)).get_json()['mine']['items']
    offered=next(g for g in agenda if g['id']==game['id'])
    assert offered['waitlist_offer'] and not offered['is_joined'] and offered['spots_left']==0
    assert client.post(url+'/waitlist/respond',json={'accept':True},headers=auth(first)).status_code==200
    assert client.get('/api/notifications?filter=action',headers=auth(first)).get_json()['items']==[]
    history=client.get('/api/notifications',headers=auth(first)).get_json()['items']
    assert next(n for n in history if n['id']==offers[0]['id'])['needs_action'] is False
    joined=client.get('/api/play/home',headers=auth(first)).get_json()['mine']['items']
    assert next(g for g in joined if g['id']==game['id'])['is_joined']


def test_expired_offer_leaves_attention_and_pending_plans_before_maintenance(client):
    game, host, player, first, second = fill_and_wait(client)
    client.post(f"/api/games/{game['id']}/leave",headers=auth(player))
    offer=GameWaitlist.query.filter_by(game_id=game['id'],user_id=first['user']['id']).one()
    offer.offer_expires_at=utcnow()-timedelta(seconds=1)
    db.session.commit()
    assert client.get('/api/notifications?filter=action',headers=auth(first)).get_json()['items']==[]
    agenda=client.get('/api/play/home',headers=auth(first)).get_json()['mine']['items']
    assert game['id'] not in [g['id'] for g in agenda]


def test_handoff_has_its_own_activity_action_and_resolves_after_acceptance(client):
    host, player=[register(client,f'agenda-host-{n}',name) for n,name in enumerate(('Host','Next'))]
    game=create_game(client,host)
    url=f"/api/games/{game['id']}"
    client.post(url+'/join',headers=auth(player))
    pending=client.post(url+'/host-handoff',json={'target_user_id':player['user']['id']},headers=auth(host))
    assert pending.status_code==202
    handoff=pending.get_json()['host_handoff']
    activity=client.get('/api/notifications?filter=action',headers=auth(player)).get_json()['items']
    requests=[n for n in activity if n['kind']=='game_host_handoff']
    assert len(requests)==1 and requests[0]['needs_action']
    assert requests[0]['action_url']==f"/#game/{game['id']}"
    agenda=client.get('/api/play/home',headers=auth(player)).get_json()['mine']['items']
    assert next(g for g in agenda if g['id']==game['id'])['host_handoff']['can_respond']
    assert client.post(url+f"/host-handoff/{handoff['id']}/respond",json={'accept':True},headers=auth(player)).status_code==200
    assert client.get('/api/notifications?filter=action',headers=auth(player)).get_json()['items']==[]


def test_offer_action_disappears_when_session_access_is_revoked(client):
    game,host,player,first,second=fill_and_wait(client)
    client.post(f"/api/games/{game['id']}/leave",headers=auth(player))
    row=db.session.get(Game,game['id']);row.visibility='private';db.session.commit()
    assert client.get('/api/notifications?filter=action',headers=auth(first)).get_json()['items']==[]
