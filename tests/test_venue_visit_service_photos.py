"""Public provenance, service continuity and useful gallery metadata on isolated data."""
import base64
import json
from datetime import timedelta

import pytest

from test_business_reviewed_versions import app, client, live, current, versioned, public
from test_business_governance import auth, register
from backend.app import db
from backend.models import BusinessOffering, BusinessProfile, BusinessScheduleItem, Court, CourtPhoto, utcnow
from backend.services.business_governance import business_snapshot, restore_snapshot
from backend.services.court_visiting import normalize_visiting


def test_reviewed_visitor_facts_are_shared_by_map_detail_and_public_profile(app, client, live):
    owner, bid = live
    with app.app_context():
        court = db.session.get(Court, 1)
        court.visitor_info = json.dumps({'entrance':'North gate', 'parking':'Community lot', 'access_type':'public'})
        business = db.session.get(BusinessProfile, bid)
        business.visitor_info = json.dumps({'parking':'Reviewed venue garage', 'play_access':'reservation'})
        db.session.commit()
    saved = client.patch(f'/api/businesses/{bid}', json={'visitor_info': {'parking':'Unreviewed new garage', 'play_access':'both'}}, headers=versioned(owner, current(client, owner, bid)))
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['effective_visiting']['visitor_info']['parking'] == 'Unreviewed new garage'
    assert public(client, bid)['visitor_info']['parking'] == 'Reviewed venue garage'
    detail = client.get('/api/courts/1').get_json()
    shown = next(row for row in client.get('/api/courts?q=Official').get_json()['items'] if row['id'] == 1)
    assert shown['visitor_info'] == detail['visitor_info'] == {
        'entrance':'North gate', 'parking':'Reviewed venue garage', 'access_type':'public', 'play_access':'reservation'}
    assert detail['visitor_info_sources'] == {'entrance':'community','parking':'venue','access_type':'community','play_access':'venue'}
    assert detail['community_visitor_info']['parking'] == 'Community lot'
    with app.app_context():
        db.session.get(BusinessProfile, bid).published = False
        db.session.commit()
    assert client.get('/api/courts/1').get_json()['visitor_info'] == detail['community_visitor_info']


@pytest.mark.parametrize('bad', [{'parking':True}, {'access_type':'free for everyone'}, {'entrance':'x'*401}, {'secret':'entry code'}])
def test_visitor_validation_rejects_unstructured_or_oversized_values(bad):
    with pytest.raises(ValueError):
        normalize_visiting(bad)
    assert normalize_visiting({'entrance':'  North gate  ', 'parking':''}) == {'entrance':'North gate'}


def test_community_visitor_correction_history_records_readable_before_and_after(app, client):
    first = register(client, 'visiting-first@example.test')
    second = register(client, 'visiting-second@example.test')
    with app.app_context():
        db.session.get(Court, 1).visitor_info = json.dumps({'parking':'Old lot'})
        db.session.commit()
    body = {'visitor_info':{'parking':'North lot', 'entrance':'North gate'}}
    for user in [first, second]:
        response = client.post('/api/courts/1/suggest', json=body, headers=auth(user['token']))
        assert response.status_code in (200, 201), response.get_json()
    assert client.get('/api/courts/1').get_json()['visitor_info'] == body['visitor_info']
    history = client.get('/api/courts/1/suggestions', headers=auth(first['token'])).get_json()['my_history']
    assert history[0]['status'] == 'applied'
    assert history[0]['before']['visitor_info'] == {'parking':'Old lot'}
    assert history[0]['changes'] == body


def test_service_dates_reject_cross_venue_links_and_deletion_cleans_all_occurrences(app, client, live):
    owner, bid = live
    with app.app_context():
        business = db.session.get(BusinessProfile, bid)
        service = business.offerings[0]
        service_id = service.id
        other = BusinessProfile(court_id=2, owner_id=owner['user']['id'], name='Other location')
        foreign = BusinessOffering(business=other, name='Other venue service', category='lesson')
        db.session.add(other)
        db.session.add(foreign)
        db.session.commit()
        foreign_id = foreign.id
    row = {'title':'Linked lesson', 'kind':'lesson', 'offering_id':service_id,
        'day_of_week':'monday', 'start_time':'09:00', 'end_time':'10:00', 'timezone':'America/Chicago',
        'occurrence_overrides':{'dates':{'2026-10-12':{'offering_id':service_id}}, 'following':{'2026-10-19':{'offering_id':service_id}}}}
    bad = client.put(f'/api/businesses/{bid}/schedule', json={'items':[{**row, 'offering_id':foreign_id}]}, headers=versioned(owner, current(client, owner, bid)))
    assert bad.status_code == 400 and bad.get_json()['error'] == 'service_not_at_this_venue'
    saved = client.put(f'/api/businesses/{bid}/schedule', json={'items':[row]}, headers=versioned(owner, current(client, owner, bid)))
    assert saved.status_code == 200, saved.get_json()
    session_id = saved.get_json()['schedule'][0]['id']
    bad_date = client.patch(f'/api/businesses/{bid}/schedule/{session_id}/occurrences/2026-10-12', json={'scope':'this_date', 'changes':{'offering_id':foreign_id}}, headers=versioned(owner, saved.get_json()))
    assert bad_date.status_code == 400 and bad_date.get_json()['error'] == 'service_not_at_this_venue'
    removed = client.put(f'/api/businesses/{bid}/offerings', json={'items':[]}, headers=versioned(owner, current(client, owner, bid)))
    assert removed.status_code == 200, removed.get_json()
    schedule = removed.get_json()['schedule'][0]
    assert schedule['id'] == session_id and schedule['offering_id'] is None
    assert all(changes['offering_id'] is None for group in schedule['occurrence_overrides'].values() for changes in group.values())


def test_revision_restore_remaps_services_and_dates_together(app, client, live):
    _, bid = live
    with app.app_context():
        business = db.session.get(BusinessProfile, bid)
        service_id = business.offerings[0].id
        item = BusinessScheduleItem(business=business, title='Clinic', offering_id=service_id,
            day_of_week='monday', start_time='09:00', end_time='10:00', timezone='America/Chicago',
            occurrence_overrides=json.dumps({'dates':{'2026-10-12':{'offering_id':service_id}},'following':{}}))
        db.session.add(item)
        db.session.commit()
        snapshot = business_snapshot(business)
        # Force new IDs to differ when restoring, as they do in Postgres.
        db.session.add(BusinessOffering(business=business, name='Later service', category='other'))
        db.session.commit()
        restore_snapshot(business, snapshot)
        db.session.commit()
        db.session.expire_all()
        restored = db.session.get(BusinessProfile, bid)
        assert len(restored.offerings) == 1 and len(restored.schedule_items) == 1
        assert restored.schedule_items[0].offering_id == restored.offerings[0].id
        assert restored.schedule_items[0].to_dict()['occurrence_overrides']['dates']['2026-10-12']['offering_id'] == restored.offerings[0].id


def test_gallery_metadata_preserves_unknown_dates_and_prefers_court_views(client):
    owner = register(client, 'photo-visitor@example.test')
    def add(category='', on=None, byte=b'x'):
        return client.post('/api/courts/1/photo', json={'photo':'data:image/jpeg;base64,'+base64.b64encode(byte*200).decode(), 'category':category,'captured_on':on}, headers=auth(owner['token']))
    assert add('court', '2026-01-02', b'c').status_code == 201
    assert add('parking', None, b'p').status_code == 201
    assert add('', None, b'l').status_code == 201
    assert client.get('/api/courts/1/photo').data == b'c'*200
    rows = client.get('/api/courts/1/photos').get_json()['items']
    assert rows[0]['category'] == '' and rows[0]['captured_on'] is None
    assert rows[-1]['category'] == 'court' and rows[-1]['captured_on'] == '2026-01-02'
    assert add('fake').get_json()['error'] == 'invalid_photo_category'
    assert add('court', (utcnow()+timedelta(days=1)).date().isoformat()).get_json()['error'] == 'invalid_photo_date'


def test_successor_does_not_publish_former_owner_hours_or_private_visiting_draft(app, client, live):
    from backend.services.businesses import _reset_profile_for_ownership_transfer
    owner, bid = live
    successor = register(client, 'successor-visitor@example.test')
    with app.app_context():
        court = db.session.get(Court, 1)
        community_hours = {'timezone':'America/Chicago','mon':[{'open':'08:00','close':'18:00'}]}
        court.structured_hours = json.dumps(community_hours)
        court.visitor_info = json.dumps({'entrance':'Community north gate'})
        business = db.session.get(BusinessProfile, bid)
        business.structured_hours = json.dumps({'timezone':'America/New_York','mon':[]})
        business.hours_dawn_to_dusk = True
        business.visitor_info = json.dumps({'entrance':'Former owner private draft'})
        business.reviewed_public_snapshot = json.dumps(business_snapshot(business))
        _reset_profile_for_ownership_transfer(business)
        business.owner_id = successor['user']['id']
        business.claim_status = 'verified'
        business.verified_at = utcnow()
        business.published = True
        db.session.commit()
    manager = current(client, successor, bid)
    assert manager['structured_hours'] == {} and manager['hours_dawn_to_dusk'] is False
    assert manager['visitor_info'] == {} and manager['hours'] == ''
    public_court = client.get('/api/courts/1').get_json()
    assert public_court['structured_hours'] == community_hours
    assert public_court['visitor_info'] == {'entrance':'Community north gate'}
    assert 'Former owner' not in json.dumps(public_court)
