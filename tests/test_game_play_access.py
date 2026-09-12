"""Capacity, play style and court access are independent, durable commitments."""
import pytest
from tests.test_game_planning_fields import app, client, register, auth, create_payload
from backend.models import Game
from backend.routes.games import _game_attempt_fingerprint


def test_create_and_edit_preserve_style_and_host_reported_access(client):
    person = register(client)
    saved = client.post('/api/games', headers=auth(person), json=create_payload(
        play_style='rotating_doubles', court_access='host_reserved', court_count=2,
    ))
    assert saved.status_code == 201, saved.get_json()
    game = saved.get_json()
    assert (game['max_players'], game['play_style'], game['court_access'], game['court_count']) == (8, 'rotating_doubles', 'host_reserved', 2)
    edited = client.patch(f"/api/games/{game['id']}", headers=auth(person), json={
        'court_access':'public_drop_in', 'play_style':'mixed',
    })
    assert edited.status_code == 200, edited.get_json()
    changed = edited.get_json()
    assert changed['court_access'] == 'public_drop_in'
    assert changed['court_count'] is None
    assert changed['play_style'] == 'mixed'
    unchanged = client.patch(f"/api/games/{game['id']}", headers=auth(person), json={'title':'Tuesday rotations'})
    assert unchanged.status_code == 200
    assert unchanged.get_json()['court_access'] == 'public_drop_in'


@pytest.mark.parametrize('fields,error', [
    ({'play_style':'doubles tournament'}, 'invalid_play_style'),
    ({'play_style':True}, 'invalid_play_style'),
    ({'court_access':'verified_booking'}, 'invalid_court_access'),
    ({'court_access':[]}, 'invalid_court_access'),
    ({'play_style':'rotating_doubles','max_players':2}, 'invalid_play_style'),
    ({'play_style':'mixed','game_type':'ranked','max_players':4}, 'invalid_play_style'),
    ({'court_access':'booking_needed','court_count':2}, 'invalid_court_access'),
])
def test_invalid_or_contradictory_choices_create_no_game(client, fields, error):
    person=register(client)
    response=client.post('/api/games',headers=auth(person),json=create_payload(**fields))
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['error'] == error
    assert Game.query.count() == 0


def test_empty_new_fields_preserve_pre_upgrade_retry_fingerprint():
    old={'game_type':'casual','max_players':8, 'court_id':1, 'scheduled_at':None}
    assert _game_attempt_fingerprint(old) == _game_attempt_fingerprint({**old,'play_style':None,'court_access':None})
    assert _game_attempt_fingerprint(old) != _game_attempt_fingerprint({**old,'play_style':'mixed'})


def test_weekly_dates_copy_metadata_and_future_edit_preserves_earlier_date(client):
    person=register(client)
    response=client.post('/api/games',headers=auth(person),json=create_payload(
        recurrence='weekly', recurrence_timezone='UTC', play_style='rotating_doubles',
        court_access='host_reserved', court_count=2,
    ))
    assert response.status_code == 201, response.get_json()
    root=response.get_json()['id']
    rows=Game.query.filter_by(recurrence_series_id=root).order_by(Game.scheduled_at).all()
    assert len(rows)>=4
    assert all((row.play_style,row.court_access,row.court_count)==('rotating_doubles','host_reserved',2) for row in rows)
    earlier_id=rows[0].id
    changed=client.patch(f'/api/games/{rows[1].id}',headers=auth(person),json={
        'edit_scope':'following_dates','play_style':'mixed','court_access':'booking_needed',
    })
    assert changed.status_code == 200, changed.get_json()
    rows=Game.query.filter_by(recurrence_series_id=root).order_by(Game.scheduled_at).all()
    assert rows[0].id == earlier_id
    assert (rows[0].play_style,rows[0].court_access,rows[0].court_count)==('rotating_doubles','host_reserved',2)
    assert all((row.play_style,row.court_access,row.court_count)==('mixed','booking_needed',None) for row in rows[1:])


def test_keyed_retry_keeps_metadata_and_rejects_a_changed_commitment(client):
    person=register(client)
    payload=create_payload(client_attempt_id='play-access-attempt',play_style='mixed',court_access='booking_needed')
    first=client.post('/api/games',headers=auth(person),json=payload)
    assert first.status_code == 201
    retried=client.post('/api/games',headers=auth(person),json=payload)
    assert retried.status_code == 200
    assert retried.get_json()['id'] == first.get_json()['id']
    conflict=client.post('/api/games',headers=auth(person),json={**payload,'court_access':'host_reserved'})
    assert conflict.status_code == 409
    assert Game.query.count() == 1


def test_public_preview_keeps_practical_facts_and_omits_roster(client):
    person=register(client)
    response=client.post('/api/games',headers=auth(person),json=create_payload(
        max_players=3,play_style='singles',court_access='public_drop_in',cost_cents=0))
    assert response.status_code == 201
    preview=client.get('/api/share-preview',query_string={'kind':'game','id':response.get_json()['id']})
    assert preview.status_code == 200
    facts=preview.get_json()['details']
    assert (facts['max_players'],facts['play_style'],facts['court_access'],facts['cost_cents']) == (3,'singles','public_drop_in',0)
    assert 'players' not in facts
