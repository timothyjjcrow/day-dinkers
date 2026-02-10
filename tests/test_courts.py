"""Tests for court routes."""
import json
from datetime import datetime, timedelta, timezone

from backend.app import db
from backend.models import User


def _auth_user(client, username='courtuser', email='court@test.com'):
    res = client.post('/api/auth/register', json={
        'username': username, 'email': email, 'password': 'password123',
    })
    return json.loads(res.data)['token']


def _auth(client):
    return _auth_user(client)


def _set_admin(client, email):
    with client.application.app_context():
        user = User.query.filter_by(email=email).first()
        assert user is not None
        user.is_admin = True
        db.session.commit()


def _seed_court(client, token, **overrides):
    data = {
        'name': 'Test Court', 'latitude': 40.8029, 'longitude': -124.1637,
        'city': 'Eureka', 'indoor': True, 'num_courts': 4,
        'surface_type': 'Sport Court', 'court_type': 'dedicated',
        'description': 'A test court', 'fees': 'Free',
        'has_restrooms': True, 'has_parking': True,
    }
    data.update(overrides)
    res = client.post('/api/courts', json=data,
        headers={'Authorization': f'Bearer {token}'})
    return json.loads(res.data)['court']


def test_get_courts_empty(client):
    res = client.get('/api/courts')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data['courts'] == []


def test_add_court(client):
    token = _auth(client)
    court = _seed_court(client, token)
    assert court['name'] == 'Test Court'
    assert court['city'] == 'Eureka'
    assert court['has_restrooms'] is True
    assert court['court_type'] == 'dedicated'


def test_get_court_detail(client):
    token = _auth(client)
    court = _seed_court(client, token)
    res = client.get(f'/api/courts/{court["id"]}')
    assert res.status_code == 200
    data = json.loads(res.data)['court']
    assert data['name'] == 'Test Court'
    assert 'active_players' in data
    assert 'busyness' in data
    assert 'checked_in_users' in data
    assert 'upcoming_games' in data


def test_search_courts(client):
    token = _auth(client)
    _seed_court(client, token, name='HealthSPORT Eureka')
    _seed_court(client, token, name='Arcata Community Center',
                latitude=40.8622, longitude=-124.0785, city='Arcata')

    res = client.get('/api/courts?search=HealthSPORT')
    data = json.loads(res.data)
    assert len(data['courts']) == 1
    assert 'HealthSPORT' in data['courts'][0]['name']


def test_filter_by_city(client):
    token = _auth(client)
    _seed_court(client, token, name='Eureka Court', city='Eureka')
    _seed_court(client, token, name='Arcata Court',
                latitude=40.8622, longitude=-124.0785, city='Arcata')

    res = client.get('/api/courts?city=Arcata')
    data = json.loads(res.data)
    assert len(data['courts']) == 1
    assert data['courts'][0]['city'] == 'Arcata'


def test_filter_indoor(client):
    token = _auth(client)
    _seed_court(client, token, name='Indoor Court', indoor=True)
    _seed_court(client, token, name='Outdoor Court', indoor=False,
                latitude=40.86, longitude=-124.08)

    res = client.get('/api/courts?indoor=true')
    data = json.loads(res.data)
    assert all(c['indoor'] for c in data['courts'])


def test_proximity_search(client):
    token = _auth(client)
    _seed_court(client, token, name='Near Court',
                latitude=40.8029, longitude=-124.1637)
    _seed_court(client, token, name='Far Court',
                latitude=34.05, longitude=-118.25)

    res = client.get('/api/courts?lat=40.80&lng=-124.16&radius=10')
    data = json.loads(res.data)
    assert len(data['courts']) == 1
    assert data['courts'][0]['name'] == 'Near Court'
    assert 'distance' in data['courts'][0]


def test_update_court(client):
    token = _auth(client)
    _set_admin(client, 'court@test.com')
    court = _seed_court(client, token)
    res = client.put(f'/api/courts/{court["id"]}',
        json={'hours': '9am-5pm', 'open_play_schedule': 'Mon/Wed 10am-12pm'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 200
    data = json.loads(res.data)['court']
    assert data['hours'] == '9am-5pm'


def test_non_admin_cannot_update_court(client):
    owner_token = _auth_user(client, username='court_owner', email='court_owner@test.com')
    other_token = _auth_user(client, username='court_other', email='court_other@test.com')
    court = _seed_court(client, owner_token)
    res = client.put(
        f'/api/courts/{court["id"]}',
        json={'hours': '10am-6pm'},
        headers={'Authorization': f'Bearer {other_token}'},
    )
    assert res.status_code == 403


def test_report_court(client):
    token = _auth(client)
    court = _seed_court(client, token)
    res = client.post(f'/api/courts/{court["id"]}/report',
        json={'reason': 'wrong_info', 'description': 'Hours are wrong'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 201


def test_court_busyness(client):
    token = _auth(client)
    court = _seed_court(client, token)
    res = client.get(f'/api/courts/{court["id"]}/busyness')
    assert res.status_code == 200
    data = json.loads(res.data)
    assert 'busyness' in data


def test_submit_court_update_for_review(client):
    token = _auth(client)
    court = _seed_court(client, token)
    payload = {
        'summary': 'Hours and access info changed at this court',
        'confidence_level': 'high',
        'source_notes': 'Verified on-site today',
        'hours': {
            'hours': 'Mon-Sun 7am-9pm',
            'open_play_schedule': 'Mon/Wed/Fri 8am-11am',
        },
        'community_notes': {
            'access_notes': 'Main gate now opens at 6:45am.',
            'additional_info': 'Wind screens were added.',
        },
        'images': [
            {'image_url': 'https://example.com/court-photo.jpg', 'caption': 'Newly resurfaced court'},
        ],
    }
    res = client.post(
        f'/api/courts/{court["id"]}/updates',
        json=payload,
        headers={'Authorization': f'Bearer {token}'},
    )
    assert res.status_code == 201
    submission = json.loads(res.data)['submission']
    assert submission['status'] in ('pending', 'approved')
    assert submission['summary'] == payload['summary']

    mine = client.get(
        f'/api/courts/{court["id"]}/updates/mine',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert mine.status_code == 200
    mine_items = json.loads(mine.data)['submissions']
    assert len(mine_items) == 1
    assert mine_items[0]['summary'] == payload['summary']


def test_reviewer_approves_submission_and_updates_court_details(client):
    submitter_token = _auth_user(client, username='submitter', email='submitter@test.com')
    reviewer_token = _auth_user(client, username='reviewer', email='reviewer@test.com')
    _set_admin(client, 'reviewer@test.com')

    court = _seed_court(client, submitter_token, name='Community Court')
    start_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    payload = {
        'summary': 'Added updated hours, event, and photos',
        'confidence_level': 'high',
        'hours': {
            'hours': 'Daily 6am-10pm',
            'open_play_schedule': 'Tue/Thu 9am-11am',
            'hours_notes': 'Holiday schedule may vary.',
        },
        'community_notes': {
            'parking_notes': 'Overflow parking behind the gym.',
            'additional_info': 'Two courts recently resurfaced.',
        },
        'images': [
            {'image_url': 'https://example.com/new-court-image.jpg', 'caption': 'Court after resurfacing'},
        ],
        'events': [
            {
                'title': 'Spring Round Robin',
                'start_time': start_time,
                'description': 'All levels welcome.',
                'organizer': 'Local Club',
            },
        ],
    }
    submit_res = client.post(
        f'/api/courts/{court["id"]}/updates',
        json=payload,
        headers={'Authorization': f'Bearer {submitter_token}'},
    )
    assert submit_res.status_code == 201
    submission = json.loads(submit_res.data)['submission']
    assert submission['status'] == 'pending'

    review_res = client.post(
        f'/api/courts/updates/{submission["id"]}/review',
        json={'action': 'approve', 'reviewer_notes': 'Looks accurate.'},
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert review_res.status_code == 200
    reviewed = json.loads(review_res.data)['submission']
    assert reviewed['status'] == 'approved'

    detail_res = client.get(f'/api/courts/{court["id"]}')
    assert detail_res.status_code == 200
    court_data = json.loads(detail_res.data)['court']
    assert court_data['hours'] == 'Daily 6am-10pm'
    assert court_data['open_play_schedule'] == 'Tue/Thu 9am-11am'
    assert court_data['community_info']['parking_notes'] == 'Overflow parking behind the gym.'
    assert len(court_data['images']) == 1
    assert court_data['images'][0]['caption'] == 'Court after resurfacing'
    assert len(court_data['upcoming_events']) == 1
    assert court_data['upcoming_events'][0]['title'] == 'Spring Round Robin'


def test_reviewer_permissions_for_queue(client):
    submitter_token = _auth_user(client, username='submitterx', email='submitterx@test.com')
    reviewer_token = _auth_user(client, username='reviewerx', email='reviewerx@test.com')
    non_reviewer_token = _auth_user(client, username='otherx', email='otherx@test.com')
    _set_admin(client, 'reviewerx@test.com')

    court = _seed_court(client, submitter_token)
    submit_res = client.post(
        f'/api/courts/{court["id"]}/updates',
        json={'summary': 'Court name correction', 'court_info': {'name': 'Renamed Court'}},
        headers={'Authorization': f'Bearer {submitter_token}'},
    )
    assert submit_res.status_code == 201

    queue_for_non_reviewer = client.get(
        '/api/courts/updates/review',
        headers={'Authorization': f'Bearer {non_reviewer_token}'},
    )
    assert queue_for_non_reviewer.status_code == 403

    queue_for_reviewer = client.get(
        '/api/courts/updates/review',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert queue_for_reviewer.status_code == 200


def test_admin_status_endpoint(client):
    user_token = _auth_user(client, username='statususer', email='statususer@test.com')
    status_before = client.get(
        '/api/courts/updates/admin-status',
        headers={'Authorization': f'Bearer {user_token}'},
    )
    assert status_before.status_code == 200
    assert json.loads(status_before.data)['is_admin'] is False

    _set_admin(client, 'statususer@test.com')
    status_after = client.get(
        '/api/courts/updates/admin-status',
        headers={'Authorization': f'Bearer {user_token}'},
    )
    assert status_after.status_code == 200
    assert json.loads(status_after.data)['is_admin'] is True


def test_reviewer_can_list_and_resolve_court_reports(client):
    reporter_token = _auth_user(client, username='reporter1', email='reporter1@test.com')
    reviewer_token = _auth_user(client, username='reviewer1', email='reviewer1@test.com')
    _set_admin(client, 'reviewer1@test.com')

    court = _seed_court(client, reporter_token, name='Report Test Court')
    create_report = client.post(
        f'/api/courts/{court["id"]}/report',
        json={'reason': 'wrong_info', 'description': 'Hours are outdated.'},
        headers={'Authorization': f'Bearer {reporter_token}'},
    )
    assert create_report.status_code == 201

    queue = client.get(
        '/api/courts/reports?status=pending',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert queue.status_code == 200
    reports = json.loads(queue.data)['reports']
    assert len(reports) == 1
    assert reports[0]['reason'] == 'wrong_info'

    review = client.post(
        f'/api/courts/reports/{reports[0]["id"]}/review',
        json={'action': 'resolve'},
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert review.status_code == 200
    reviewed = json.loads(review.data)['report']
    assert reviewed['status'] == 'resolved'

    pending_after = client.get(
        '/api/courts/reports?status=pending',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert pending_after.status_code == 200
    assert json.loads(pending_after.data)['reports'] == []


def test_non_reviewer_cannot_access_report_queue(client):
    reporter_token = _auth_user(client, username='reporter2', email='reporter2@test.com')
    non_reviewer_token = _auth_user(client, username='plainuser2', email='plainuser2@test.com')
    reviewer_token = _auth_user(client, username='reviewer2', email='reviewer2@test.com')
    _set_admin(client, 'reviewer2@test.com')

    court = _seed_court(client, reporter_token)
    create_report = client.post(
        f'/api/courts/{court["id"]}/report',
        json={'reason': 'closed', 'description': 'Court was locked.'},
        headers={'Authorization': f'Bearer {reporter_token}'},
    )
    assert create_report.status_code == 201

    forbidden = client.get(
        '/api/courts/reports',
        headers={'Authorization': f'Bearer {non_reviewer_token}'},
    )
    assert forbidden.status_code == 403

    allowed = client.get(
        '/api/courts/reports',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert allowed.status_code == 200


def test_bulk_review_update_submissions(client):
    submitter_token = _auth_user(client, username='bulksubmit', email='bulksubmit@test.com')
    reviewer_token = _auth_user(client, username='bulkreviewer', email='bulkreviewer@test.com')
    _set_admin(client, 'bulkreviewer@test.com')

    court = _seed_court(client, submitter_token, name='Bulk Update Court')
    sub1 = client.post(
        f'/api/courts/{court["id"]}/updates',
        json={'summary': 'Updated court hours', 'hours': {'hours': 'Daily 6am-8pm'}},
        headers={'Authorization': f'Bearer {submitter_token}'},
    )
    assert sub1.status_code == 201
    sub2 = client.post(
        f'/api/courts/{court["id"]}/updates',
        json={'summary': 'Added parking note', 'community_notes': {'parking_notes': 'North lot reopened'}},
        headers={'Authorization': f'Bearer {submitter_token}'},
    )
    assert sub2.status_code == 201

    queue = client.get(
        '/api/courts/updates/review?status=pending',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert queue.status_code == 200
    pending = json.loads(queue.data)['submissions']
    pending_ids = [item['id'] for item in pending]
    assert len(pending_ids) == 2

    bulk = client.post(
        '/api/courts/updates/review/bulk',
        json={'ids': pending_ids, 'action': 'approve', 'reviewer_notes': 'Bulk approved'},
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert bulk.status_code == 200
    bulk_data = json.loads(bulk.data)
    assert bulk_data['processed_count'] == 2
    assert bulk_data['failed_count'] == 0

    pending_after = client.get(
        '/api/courts/updates/review?status=pending',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert pending_after.status_code == 200
    assert json.loads(pending_after.data)['submissions'] == []

    detail = client.get(f'/api/courts/{court["id"]}')
    assert detail.status_code == 200
    court_data = json.loads(detail.data)['court']
    assert court_data['hours'] == 'Daily 6am-8pm'
    assert court_data['community_info']['parking_notes'] == 'North lot reopened'


def test_bulk_review_court_reports(client):
    reporter_token = _auth_user(client, username='bulkreporter', email='bulkreporter@test.com')
    reviewer_token = _auth_user(client, username='bulkreportadmin', email='bulkreportadmin@test.com')
    _set_admin(client, 'bulkreportadmin@test.com')

    court = _seed_court(client, reporter_token, name='Bulk Report Court')
    rep1 = client.post(
        f'/api/courts/{court["id"]}/report',
        json={'reason': 'wrong_info', 'description': 'Address typo'},
        headers={'Authorization': f'Bearer {reporter_token}'},
    )
    assert rep1.status_code == 201
    rep2 = client.post(
        f'/api/courts/{court["id"]}/report',
        json={'reason': 'closed', 'description': 'Locked gate after 6pm'},
        headers={'Authorization': f'Bearer {reporter_token}'},
    )
    assert rep2.status_code == 201

    queue = client.get(
        '/api/courts/reports?status=pending',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert queue.status_code == 200
    pending = json.loads(queue.data)['reports']
    pending_ids = [item['id'] for item in pending]
    assert len(pending_ids) == 2

    bulk = client.post(
        '/api/courts/reports/review/bulk',
        json={'ids': pending_ids, 'action': 'resolve'},
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert bulk.status_code == 200
    bulk_data = json.loads(bulk.data)
    assert bulk_data['processed_count'] == 2
    assert bulk_data['failed_count'] == 0

    pending_after = client.get(
        '/api/courts/reports?status=pending',
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert pending_after.status_code == 200
    assert json.loads(pending_after.data)['reports'] == []


def test_non_reviewer_cannot_bulk_review(client):
    submitter_token = _auth_user(client, username='norbulksubmit', email='norbulksubmit@test.com')
    non_reviewer_token = _auth_user(client, username='norbulkuser', email='norbulkuser@test.com')
    reviewer_token = _auth_user(client, username='norbulkreviewer', email='norbulkreviewer@test.com')
    _set_admin(client, 'norbulkreviewer@test.com')

    court = _seed_court(client, submitter_token, name='No Reviewer Bulk Court')
    sub = client.post(
        f'/api/courts/{court["id"]}/updates',
        json={'summary': 'Fix court hours listing', 'hours': {'hours': '8am-8pm'}},
        headers={'Authorization': f'Bearer {submitter_token}'},
    )
    assert sub.status_code == 201

    report = client.post(
        f'/api/courts/{court["id"]}/report',
        json={'reason': 'wrong_info', 'description': 'Need correction'},
        headers={'Authorization': f'Bearer {submitter_token}'},
    )
    assert report.status_code == 201

    forbidden_updates = client.post(
        '/api/courts/updates/review/bulk',
        json={'ids': [1], 'action': 'approve'},
        headers={'Authorization': f'Bearer {non_reviewer_token}'},
    )
    assert forbidden_updates.status_code == 403

    forbidden_reports = client.post(
        '/api/courts/reports/review/bulk',
        json={'ids': [1], 'action': 'resolve'},
        headers={'Authorization': f'Bearer {non_reviewer_token}'},
    )
    assert forbidden_reports.status_code == 403

    # sanity: reviewer can still access endpoints
    allowed_updates = client.post(
        '/api/courts/updates/review/bulk',
        json={'ids': [json.loads(sub.data)['submission']['id']], 'action': 'approve'},
        headers={'Authorization': f'Bearer {reviewer_token}'},
    )
    assert allowed_updates.status_code == 200
