"""Venue time, occurrence changes and reviewed public schedule integration."""
import pytest
from test_business_reviewed_versions import app, client, live, current, versioned, public
from backend.app import db
from backend.models import BusinessProfile, BusinessScheduleItem, utcnow


@pytest.fixture()
def schedule(app, client, live):
    owner, bid = live
    with app.app_context():
        business = db.session.get(BusinessProfile, bid)
        business.timezone = 'America/Los_Angeles'
        item = BusinessScheduleItem(business=business, title='Sunday open play', kind='open_play',
            day_of_week='sunday', start_time='08:00', end_time='10:00', timezone='America/Los_Angeles',
            recurrence='weekly', capacity=16, spots_remaining=4, source_updated_at=utcnow(), availability_updated_at=utcnow())
        db.session.add(item)
        db.session.commit()
        item_id = item.id
    return owner, bid, item_id


def agenda(client, bid, *, draft=False, owner=None):
    from test_business_governance import auth
    response = client.get(f'/api/businesses/{bid}/agenda?from=2026-10-25&to=2026-11-08' + ('&draft=1' if draft else ''),
        headers=auth(owner['token']) if owner else {})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def edit(client, schedule, on, changes, scope='this_date'):
    owner, bid, item_id = schedule
    response = client.patch(f'/api/businesses/{bid}/schedule/{item_id}/occurrences/{on}',
        json={'changes':changes, 'scope':scope}, headers=versioned(owner, current(client, owner, bid)))
    return response


def test_weekly_agenda_keeps_venue_wall_clock_through_dst(client, schedule):
    owner, bid, _ = schedule
    data = agenda(client, bid)
    assert [item['event_date'] for item in data['items']] == ['2026-10-25', '2026-11-01', '2026-11-08']
    assert [item['starts_at'] for item in data['items']] == ['2026-10-25T15:00:00Z', '2026-11-01T16:00:00Z', '2026-11-08T16:00:00Z']
    assert all(item['start_time'] == '08:00' for item in data['items'])
    assert all(item['availability_label'].startswith('4 places · checked ') for item in data['items'])


def test_cancel_this_date_preserves_other_dates_and_future_edits_keep_history(client, schedule):
    owner, bid, _ = schedule
    assert edit(client, schedule, '2026-11-01', {'status':'cancelled'}).status_code == 200
    rows = agenda(client, bid)['items']
    assert [row['status'] for row in rows] == ['scheduled', 'cancelled', 'scheduled']
    assert edit(client, schedule, '2026-11-08', {'title':'Holiday play'}).status_code == 200
    assert edit(client, schedule, '2026-11-01', {'start_time':'09:00', 'end_time':'11:00'}, 'following_dates').status_code == 200
    rows = agenda(client, bid)['items']
    assert [row['start_time'] for row in rows] == ['08:00', '09:00', '09:00']
    assert rows[1]['status'] == 'cancelled'
    assert rows[2]['title'] == 'Holiday play'


def test_changed_occurrence_booking_link_waits_for_review(client, schedule):
    owner, bid, _ = schedule
    saved = edit(client, schedule, '2026-11-01', {'booking_url':'https://official.example/new-event'})
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['content_review_status'] == 'pending'
    assert saved.get_json()['is_public'] is True
    shown = agenda(client, bid)['items'][1]
    assert shown['booking_url'] == ''
    draft = agenda(client, bid, draft=True, owner=owner)['items'][1]
    assert draft['booking_url'] == 'https://official.example/new-event'
    assert 'occurrence_overrides' not in public(client, bid)['schedule'][0]


def test_schedule_inherits_venue_timezone_and_rejects_invalid_override(client, schedule):
    owner, bid, _ = schedule
    data = current(client, owner, bid)
    response = client.put(f'/api/businesses/{bid}/schedule', json={'items':[
        {'title':'New clinic', 'kind':'clinic', 'day_of_week':'monday', 'start_time':'18:00', 'end_time':'20:00'}
    ]}, headers=versioned(owner, data))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['schedule'][0]['timezone'] == 'America/Los_Angeles'
    data = response.get_json()
    malformed = client.put(f'/api/businesses/{bid}/schedule', json={'items':[
        {**data['schedule'][0], 'occurrence_overrides': {'dates': {'bad-date': {'status':'cancelled'}}}}
    ]}, headers=versioned(owner, data))
    assert malformed.status_code == 400


def test_combined_future_and_date_times_are_validated(client, schedule):
    assert edit(client, schedule, '2026-11-01', {'start_time':'09:00', 'end_time':'11:00'}, 'following_dates').status_code == 200
    invalid = edit(client, schedule, '2026-11-08', {'end_time':'08:30'})
    assert invalid.status_code == 400
    assert invalid.get_json()['error'] == 'end_time_must_be_after_start_time'


def test_anonymous_cannot_read_draft_or_modify_occurrences(client, schedule):
    _, bid, item_id = schedule
    assert client.get(f'/api/businesses/{bid}/agenda?draft=1').status_code == 403
    assert client.patch(f'/api/businesses/{bid}/schedule/{item_id}/occurrences/2026-11-01', json={'changes':{'status':'cancelled'}}).status_code == 401


def test_title_edits_do_not_make_old_inventory_fresh_and_date_recheck_is_scoped(app, client, schedule):
    from datetime import timedelta
    owner, bid, item_id = schedule
    with app.app_context():
        item = db.session.get(BusinessScheduleItem, item_id)
        item.availability_updated_at = utcnow() - timedelta(days=3)
        db.session.commit()
    assert all(item['availability_label'] == 'Check availability' for item in agenda(client, bid)['items'])
    assert edit(client, schedule, '2026-11-01', {'title':'Renamed play'}).status_code == 200
    assert agenda(client, bid)['items'][1]['availability_label'] == 'Check availability'
    rechecked = edit(client, schedule, '2026-11-01', {'availability_checked':True})
    assert rechecked.status_code == 200, rechecked.get_json()
    rows = agenda(client, bid)['items']
    assert [row['availability_fresh'] for row in rows] == [False, True, False]
    assert rows[1]['availability_label'].startswith('4 places · checked ')


def test_spoofed_inventory_timestamp_is_ignored(client, schedule):
    saved = edit(client, schedule, '2026-11-01', {'title':'Renamed', 'availability_updated_at':'2099-01-01T00:00:00Z'})
    assert saved.status_code == 200, saved.get_json()
    row = agenda(client, schedule[1])['items'][1]
    assert not row['availability_updated_at'].startswith('2099')
