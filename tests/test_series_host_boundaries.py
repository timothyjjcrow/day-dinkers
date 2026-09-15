"""Recurring host actions must preserve another host's dates and defaults."""
import json
import pytest
from datetime import timedelta
from test_game_recurrence_patterns import app, client, register, auth
from test_game_edit_dates import series
from backend.app import db
from backend.models import Game, GamePlayer, Notification


def test_cancel_rejects_changed_date_scope_before_any_cancellation(client):
    host, first, rows = series(client)
    path = f"/api/games/{first['id']}"
    preview = client.get(path+'/edit-dates', headers=auth(host)).get_json()
    rows[-1].scheduled_at += timedelta(days=1)
    db.session.commit()
    before_notifications = Notification.query.count()
    stale = client.post(path+'/cancel', headers=auth(host), json={
        'edit_scope':'following_dates','expected_edit_dates':preview['token']})
    assert stale.status_code == 409 and stale.json['error'] == 'edit_dates_changed'
    assert all(row.status == 'upcoming' for row in rows)
    assert rows[0].recurrence_stopped_at is None
    assert Notification.query.count() == before_notifications


def test_cancelling_one_legacy_date_keeps_its_repeating_schedule_active(client):
    host, first, rows = series(client)
    legacy = Game(creator_id=host['user']['id'], court_id=rows[0].court_id,
                  scheduled_at=rows[0].scheduled_at, recurrence='weekly')
    db.session.add(legacy)
    db.session.flush()
    db.session.add(GamePlayer(game=legacy, user_id=host['user']['id']))
    db.session.commit()
    response = client.post(f'/api/games/{legacy.id}/cancel', headers=auth(host), json={'edit_scope':'this_date'})
    assert response.status_code == 200, response.json
    assert legacy.status == 'cancelled'
    assert legacy.recurrence_series_id == legacy.id
    assert legacy.recurrence_stopped_at is None


def test_cancel_uses_preview_scope_for_a_nonrepeating_occurrence_in_a_series(client):
    host, first, rows = series(client)
    selected = rows[1]
    selected.recurrence = 'none'
    rows[0].scheduled_at = rows[-1].scheduled_at + timedelta(hours=1)
    db.session.commit()
    path = f'/api/games/{selected.id}'
    preview = client.get(path+'/edit-dates', headers=auth(host)).json
    cancelled = client.post(path+'/cancel', headers=auth(host), json={
        'edit_scope':'following_dates','expected_edit_dates':preview['token']})
    assert cancelled.status_code == 200, cancelled.json
    assert all(db.session.get(Game, row['id']).status == 'cancelled' for row in preview['dates'])
    assert db.session.get(Game, first['id']).recurrence_stopped_at is not None


def test_one_date_host_cannot_change_the_series_defaults_even_without_later_rows(client):
    host, first, rows = series(client)
    other = register(client, 'last-date-host@example.test', 'Date host')
    selected = rows[-1]
    selected.creator_id = other['user']['id']
    db.session.add(GamePlayer(game=selected, user_id=other['user']['id']))
    db.session.commit()
    path = f'/api/games/{selected.id}'
    for response in [
        client.get(path+'/edit-dates', headers=auth(other)),
        client.patch(path, headers=auth(other), json={'edit_scope':'following_dates','title':'Changed'}),
        client.post(path+'/cancel', headers=auth(other), json={'edit_scope':'following_dates'}),
        client.post(path+'/host-handoff', headers=auth(other), json={'edit_scope':'following_dates','target_user_id':host['user']['id']}),
    ]:
        assert response.status_code == 409 and response.json['error'] == 'future_host_changed', response.json
    assert rows[0].recurrence_stopped_at is None
    assert json.loads(rows[0].recurrence_template)['creator_id'] == host['user']['id']
    single = client.post(path+'/cancel', headers=auth(other), json={'edit_scope':'this_date'})
    assert single.status_code == 200
    assert all(row.status == 'upcoming' for row in rows[:-1])
    assert rows[0].recurrence_stopped_at is None


def test_cancel_and_transfer_request_cannot_touch_another_hosts_future_date(client):
    host, first, rows = series(client)
    other = register(client, 'future-owner@example.test', 'Other')
    db.session.add(GamePlayer(game=rows[0], user_id=other['user']['id']))
    rows[-1].creator_id = other['user']['id']
    db.session.commit()
    path = f"/api/games/{first['id']}"
    for endpoint, body in [('cancel', {}), ('host-handoff', {'target_user_id':other['user']['id']})]:
        response = client.post(path+'/'+endpoint, headers=auth(host), json={**body,'edit_scope':'following_dates'})
        assert response.status_code == 409 and response.json['error'] == 'future_host_changed'
    assert all(row.status == 'upcoming' for row in rows)
    assert rows[0].recurrence_stopped_at is None


@pytest.mark.parametrize('change', ['date', 'defaults'])
def test_acceptance_rechecks_ownership_and_still_allows_decline(client, change):
    host, first, rows = series(client)
    next_host = register(client, 'requested-host@example.test', 'Next')
    other = register(client, 'changed-host@example.test', 'Other')
    db.session.add(GamePlayer(game=rows[0], user_id=next_host['user']['id']))
    db.session.commit()
    path = f"/api/games/{first['id']}"
    requested = client.post(path+'/host-handoff', headers=auth(host), json={
        'edit_scope':'following_dates','target_user_id':next_host['user']['id']})
    assert requested.status_code == 202, requested.json
    reply = path+f"/host-handoff/{requested.json['host_handoff']['id']}/respond"
    if change == 'date':
        rows[-1].creator_id = other['user']['id']
    else:
        template = json.loads(rows[0].recurrence_template)
        template['creator_id'] = other['user']['id']
        rows[0].recurrence_template = json.dumps(template)
    db.session.commit()
    before = [row.creator_id for row in rows]
    before_template = rows[0].recurrence_template
    accepted = client.post(reply, headers=auth(next_host), json={'accept':True})
    assert accepted.status_code == 409 and accepted.json['error'] == 'host_scope_changed'
    assert [row.creator_id for row in rows] == before
    assert rows[0].recurrence_template == before_template
    declined = client.post(reply, headers=auth(next_host), json={'accept':False})
    assert declined.status_code == 200 and [row.creator_id for row in rows] == before
