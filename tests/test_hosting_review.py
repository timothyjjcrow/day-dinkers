"""A recurring hosting decision acknowledges the displayed dated plans and rule."""
import json
from datetime import timedelta

import pytest

from test_game_recurrence_patterns import app, client, register, auth
from test_game_edit_dates import series
from backend.app import db
from backend.models import Game, GamePlayer, GameHostHandoff, Notification, utcnow


def requested(client, selected_index=0):
    host, first, rows = series(client)
    recipient = register(client, 'new-host@example.test', 'Next host')
    selected = rows[selected_index]
    db.session.add(GamePlayer(game=selected, user_id=recipient['user']['id']))
    db.session.commit()
    path = f'/api/games/{selected.id}'
    request = client.post(path+'/host-handoff', headers=auth(host), json={
        'target_user_id':recipient['user']['id'], 'edit_scope':'following_dates', 'leave_on_accept':True,
    })
    assert request.status_code == 202, request.json
    return host, recipient, rows, path+f"/host-handoff/{request.json['host_handoff']['id']}"


def test_preview_is_read_only_and_separates_moved_date_terms_from_rule(client):
    host, first, rows = series(client)
    selected = rows[1]
    rows[0].scheduled_at = rows[-1].scheduled_at + timedelta(days=1)
    selected.cost_cents = 800
    selected.duration_minutes = 75
    selected.notes = 'Bring a paddle'
    db.session.commit()
    before = (Game.query.count(), GamePlayer.query.count(), Notification.query.count(), rows[0].recurrence_template)
    path = f'/api/games/{selected.id}/hosting-preview?edit_scope=following_dates'
    response = client.get(path, headers=auth(host))
    assert response.status_code == 200, response.json
    review = response.json
    assert review['dates'][0]['id'] == selected.id
    assert review['dates'][-1]['id'] == rows[0].id
    assert review['dates'][0]['cost_cents'] == 800
    assert review['dates'][0]['duration_minutes'] == 75
    assert review['dates'][0]['notes'] == 'Bring a paddle'
    assert review['dates'][0]['ends_at']
    assert review['rule']['cost_cents'] == json.loads(rows[0].recurrence_template)['cost_cents']
    assert before == (Game.query.count(), GamePlayer.query.count(), Notification.query.count(), rows[0].recurrence_template)
    assert client.get(path).status_code == 401
    outsider = register(client, 'outsider@example.test', 'Other')
    assert client.get(path, headers=auth(outsider)).status_code == 403


def test_legacy_weekly_preview_derives_the_clock_without_initializing_a_series(client):
    host, first, rows = series(client)
    legacy = Game(creator_id=host['user']['id'], court_id=rows[0].court_id,
                  scheduled_at=rows[0].scheduled_at, recurrence='weekly', recurrence_timezone='UTC')
    db.session.add(legacy)
    db.session.commit()
    count = Game.query.count()
    response = client.get(f'/api/games/{legacy.id}/hosting-preview?edit_scope=following_dates', headers=auth(host))
    assert response.status_code == 200, response.json
    assert response.json['rule']['recurrence_local_time'] == legacy.scheduled_at.strftime('%H:%M')
    assert response.json['rule']['recurrence_weekdays'] == [legacy.scheduled_at.strftime('%a').lower()]
    assert legacy.recurrence_series_id is None and legacy.recurrence_template is None
    assert Game.query.count() == count


def test_stale_owner_review_cannot_create_a_handoff_or_notify(client):
    host, first, rows = series(client)
    recipient = register(client, 'request-target@example.test', 'Next')
    db.session.add(GamePlayer(game=rows[0], user_id=recipient['user']['id']))
    db.session.commit()
    path = f"/api/games/{first['id']}"
    review = client.get(path+'/hosting-preview?edit_scope=following_dates', headers=auth(host)).json
    rows[-1].cost_cents = 1800
    db.session.commit()
    before = Notification.query.count()
    payload = {'target_user_id':recipient['user']['id'], 'edit_scope':'following_dates', 'expected_host_review':review['token']}
    rejected = client.post(path+'/host-handoff', headers=auth(host), json=payload)
    assert rejected.status_code == 409 and rejected.json['error'] == 'host_review_changed'
    assert GameHostHandoff.query.count() == 0 and Notification.query.count() == before
    assert client.post(path+'/host-handoff', headers=auth(recipient), json=payload).status_code == 403
    fresh = client.get(path+'/hosting-preview?edit_scope=following_dates', headers=auth(host)).json
    assert client.post(path+'/host-handoff', headers=auth(host), json={**payload,'expected_host_review':fresh['token']}).status_code == 202


@pytest.mark.parametrize('change', ['date', 'price', 'duration', 'notes', 'rule', 'membership', 'cancelled'])
def test_acceptance_requires_current_review_before_any_host_or_roster_change(client, change):
    host, recipient, rows, path = requested(client)
    review = client.get(path+'/preview', headers=auth(recipient)).json
    missing = client.post(path+'/respond', headers=auth(recipient), json={'accept':True})
    assert missing.status_code == 409 and missing.json['error'] == 'host_review_required'
    if change == 'date':
        rows[-1].scheduled_at += timedelta(days=1)
    elif change == 'price':
        rows[-1].cost_cents = 1900
    elif change == 'duration':
        rows[-1].duration_minutes = 110
    elif change == 'notes':
        rows[-1].notes = 'Outdoor court, bring water'
    elif change == 'rule':
        template = json.loads(rows[0].recurrence_template)
        template['cost_cents'] = 2300
        rows[0].recurrence_template = json.dumps(template)
    elif change == 'membership':
        db.session.add(GamePlayer(game=rows[-1], user_id=recipient['user']['id']))
    else:
        rows[-1].status = 'cancelled'
    db.session.commit()
    before = (GamePlayer.query.count(), Notification.query.count(), rows[0].recurrence_template)
    stale = client.post(path+'/respond', headers=auth(recipient), json={'accept':True,'expected_host_review':review['token']})
    assert stale.status_code == 409 and stale.json['error'] == 'host_review_changed'
    assert all(row.creator_id == host['user']['id'] for row in rows)
    assert before == (GamePlayer.query.count(), Notification.query.count(), rows[0].recurrence_template)
    assert GameHostHandoff.query.one().status == 'pending'
    fresh = client.get(path+'/preview', headers=auth(recipient)).json
    assert fresh['token'] != review['token']
    response = client.post(path+'/respond', headers=auth(recipient), json={'accept':True,'expected_host_review':fresh['token']})
    assert response.status_code == 200, response.json
    for item in fresh['dates']:
        game = db.session.get(Game, item['id'])
        assert game.creator_id == recipient['user']['id']
        mine = next(p for p in game.players if p.user_id == recipient['user']['id'])
        assert mine.attending_at and not mine.commitment_confirmation_due()


def test_review_confirms_new_host_only_preserving_earlier_dates_and_other_players(client):
    host, recipient, rows, path = requested(client, selected_index=1)
    other = register(client, 'other-player@example.test', 'Other player')
    due = utcnow()
    mine = GamePlayer(game=rows[-1], user_id=recipient['user']['id'], commitment_requested_at=due)
    theirs = GamePlayer(game=rows[-1], user_id=other['user']['id'], commitment_requested_at=due)
    db.session.add_all([mine, theirs])
    db.session.commit()
    review = client.get(path+'/preview', headers=auth(recipient)).json
    response = client.post(path+'/respond', headers=auth(recipient), json={'accept':True,'expected_host_review':review['token']})
    assert response.status_code == 200, response.json
    assert rows[0].creator_id == host['user']['id']
    assert not any(p.user_id == recipient['user']['id'] for p in rows[0].players)
    assert mine.attending_at and mine.commitment_requested_at is None
    assert theirs.attending_at is None and theirs.commitment_requested_at == due
    assert client.post(path+'/respond', headers=auth(recipient), json={'accept':True}).status_code == 200


def test_full_future_date_is_visible_but_cannot_be_accepted(client):
    host, recipient, rows, path = requested(client)
    rows[-1].max_players = 1
    db.session.commit()
    review = client.get(path+'/preview', headers=auth(recipient)).json
    assert review['dates'][-1]['player_count'] == review['dates'][-1]['max_players'] == 1
    assert review['dates'][-1]['already_joined'] is False
    response = client.post(path+'/respond', headers=auth(recipient), json={'accept':True,'expected_host_review':review['token']})
    assert response.status_code == 409 and response.json['error'] == 'future_session_full'
    assert all(row.creator_id == host['user']['id'] for row in rows)


def test_recipient_preview_checks_identity_expiry_and_ownership_without_mutation(client):
    host, recipient, rows, path = requested(client)
    outsider = register(client, 'not-invited@example.test', 'Other')
    assert client.get(path+'/preview', headers=auth(outsider)).status_code == 404
    assert client.get(path+'/preview', headers=auth(host)).status_code == 404
    assert client.get(path+'/preview').status_code == 401
    rows[-1].creator_id = outsider['user']['id']
    db.session.commit()
    response = client.get(path+'/preview', headers=auth(recipient))
    assert response.status_code == 409 and response.json['error'] == 'host_scope_changed'
    proposal = GameHostHandoff.query.one()
    proposal.expires_at = utcnow()-timedelta(seconds=1)
    db.session.commit()
    response = client.get(path+'/preview', headers=auth(recipient))
    assert response.status_code == 409 and response.json['error'] == 'handoff_expired'
    assert proposal.status == 'pending'
