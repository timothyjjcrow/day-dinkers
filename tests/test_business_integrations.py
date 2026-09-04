"""Business integration onboarding, ownership, publishing, and schema tests."""
from datetime import timedelta

import pytest
from flask import has_app_context
from sqlalchemy import event, inspect

from backend.app import _upgrade_schema, create_app, db
from backend.models import (
    BusinessClaim,
    BusinessClaimReviewEvent,
    BusinessIntegrationRequest,
    BusinessOffering,
    BusinessProfile,
    BusinessProfileRevision,
    BusinessScheduleItem,
    Court,
    Notification,
    utcnow,
)
from backend.services.businesses import (
    BusinessClaimReviewError,
    BusinessIntegrationRequestError,
    review_business_claim,
    update_business_integration_request_status,
)
from scripts.manage_business_integration_requests import (
    _target_database_url as integration_request_target_url,
)
from scripts.review_business_claim import (
    _operator_claim_payload,
    _parse_args as parse_claim_cli_args,
)
from scripts.migrate_production_schema import _schema_gaps


REVIEW_KWARGS = {
    'reviewer_identifier': 'reviewer@example.com',
    'verification_method': 'business_email',
    'review_note': 'Confirmed control through the venue public business email.',
}


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Third Shot Pickleball Club',
            address='123 Kitchen Way',
            city='Austin',
            state='TX',
            county_slug='travis-county',
            latitude=30.27,
            longitude=-97.74,
            num_courts=12,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name='Operator'):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(token):
    return {'Authorization': f'Bearer {token}'}


def court_id(client):
    return client.get('/api/courts?q=Third Shot').get_json()['items'][0]['id']


def test_explicit_venue_search_is_global_even_with_a_distant_player_location(client):
    response = client.get(
        '/api/courts?q=Third%20Shot&lat=47.6062&lng=-122.3321&limit=5',
    )
    assert response.status_code == 200
    assert [item['name'] for item in response.get_json()['items']] == [
        'Third Shot Pickleball Club',
    ]


def create_profile(client, token, court, **overrides):
    payload = {
        'court_id': court,
        'name': 'Third Shot Pickleball Club',
        'role': 'General manager',
        'authorized_attestation': True,
        'description': 'Twelve courts, coaching, leagues, and open play.',
        'phone': '(512) 555-0100',
        'email': 'play@thirdshot.example',
        'hours': 'Mon–Fri 6am–10pm; Sat–Sun 7am–9pm',
        'amenities': ['Pro shop', 'Locker rooms', 'Pro shop'],
        'website_url': 'thirdshot.example',
        'booking_url': 'https://book.thirdshot.example/reserve#courts',
        'membership_url': 'https://thirdshot.example/memberships',
        'logo_url': 'https://thirdshot.example/logo.png',
        'announcement': 'Fall leagues are now open.',
    }
    payload.update(overrides)
    return client.post('/api/businesses', json=payload, headers=headers(token))


def verify_profile(app, business_id, *, published=True):
    def update():
        profile = db.session.get(BusinessProfile, business_id)
        profile.claim_status = 'verified'
        profile.verified_at = utcnow()
        profile.published = published
        db.session.commit()
    if has_app_context():
        update()
    else:
        with app.app_context():
            update()


def approve_profile_content(app, business_id, *, published=False):
    """Model the separate operator content decision used after verification."""
    def update():
        profile = db.session.get(BusinessProfile, business_id)
        now = utcnow()
        for revision in BusinessProfileRevision.query.filter_by(
            business_id=business_id,
            review_status='pending',
        ):
            revision.review_status = 'approved'
            revision.reviewer_identifier = 'reviewer@example.com'
            revision.review_note = 'Reviewed sensitive business content.'
            revision.reviewed_at = now
        profile.content_review_status = 'approved'
        profile.content_reviewed_at = now
        profile.published = published
        db.session.commit()
    if has_app_context():
        update()
    else:
        with app.app_context():
            update()


def test_claim_creates_private_pending_draft_and_profile_completion(
    client, caplog,
):
    account = register(client, 'manager@example.com')
    court = court_id(client)

    unauthenticated = client.post('/api/businesses/claims', json={
        'court_id': court, 'role': 'Manager', 'authorized_attestation': True,
    })
    assert unauthenticated.status_code == 401

    unattested = client.post(
        '/api/businesses/claims',
        json={'court_id': court, 'role': 'General manager'},
        headers=headers(account['token']),
    )
    assert unattested.status_code == 400
    assert unattested.get_json() == {'error': 'authorized_attestation_required'}

    claim = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'General manager',
            'authorized_attestation': True,
        },
        headers=headers(account['token']),
    )
    assert claim.status_code == 201
    data = claim.get_json()
    assert data['claim']['status'] == 'pending'
    assert data['business']['claim_status'] == 'pending'
    assert data['business']['verified'] is False
    assert data['business']['published'] is False
    assert data['business']['is_owner'] is True
    claim_log = next(
        record.getMessage() for record in caplog.records
        if 'Business claim submitted' in record.getMessage()
    )
    assert f"id={data['claim']['id']}" in claim_log
    assert f'court_id={court}' in claim_log
    assert 'manager@example.com' not in claim_log
    assert 'General manager' not in claim_log

    # A pending draft and all its external actions are owner-only.
    assert client.get(f'/api/courts/{court}/business').get_json() == {
        'business': None,
    }
    assert client.get(
        f"/api/businesses/{data['business']['id']}"
    ).status_code == 404

    completed = create_profile(client, account['token'], court)
    assert completed.status_code == 200
    profile = completed.get_json()
    assert profile['verified'] is False
    assert profile['claim_status'] == 'pending'
    assert profile['amenities'] == ['Pro shop', 'Locker rooms']
    assert profile['website_url'] == 'https://thirdshot.example'
    assert profile['booking_url'].endswith('/reserve#courts')

    mine = client.get('/api/businesses/mine', headers=headers(account['token']))
    assert mine.status_code == 200
    assert [item['id'] for item in mine.get_json()['items']] == [profile['id']]
    assert mine.get_json()['claims'][0]['status'] == 'pending'


def test_direct_profile_creation_requires_role_and_attestation_and_is_unpublished(
    app, client,
):
    owner = register(client, 'direct-owner@example.com')
    court = court_id(client)

    missing_role = client.post('/api/businesses', json={
        'court_id': court,
        'name': 'Third Shot Pickleball Club',
        'authorized_attestation': True,
    }, headers=headers(owner['token']))
    assert missing_role.status_code == 400
    assert missing_role.get_json() == {'error': 'role_required'}

    missing_attestation = client.post('/api/businesses', json={
        'court_id': court,
        'name': 'Third Shot Pickleball Club',
        'role': 'Owner',
    }, headers=headers(owner['token']))
    assert missing_attestation.status_code == 400
    assert missing_attestation.get_json() == {
        'error': 'authorized_attestation_required',
    }

    created = create_profile(client, owner['token'], court)
    assert created.status_code == 201, created.get_json()
    assert created.get_json()['claim_status'] == 'pending'
    assert created.get_json()['published'] is False
    assert client.get('/api/businesses').get_json()['items'] == []
    pending_id = created.get_json()['id']
    blocked_publish = client.patch(
        f'/api/businesses/{pending_id}',
        json={'published': True}, headers=headers(owner['token']),
    )
    assert blocked_publish.status_code == 400
    assert blocked_publish.get_json() == {
        'error': 'business_verification_required',
    }
    insecure_link = client.patch(
        f'/api/businesses/{pending_id}',
        json={'booking_url': 'http://booking.example.com'},
        headers=headers(owner['token']),
    )
    assert insecure_link.status_code == 400
    assert insecure_link.get_json() == {'error': 'invalid_booking_url'}
    with app.app_context():
        assert BusinessProfile.query.one().published is False
        assert BusinessClaim.query.one().role == 'General manager'


def test_owner_manages_offerings_and_schedule_then_verified_profile_is_public(app, client):
    owner = register(client, 'owner@example.com')
    court = court_id(client)
    profile = create_profile(client, owner['token'], court).get_json()

    pending_request = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={'provider': 'CourtReserve'}, headers=headers(owner['token']),
    )
    assert pending_request.status_code == 409
    assert pending_request.get_json() == {
        'error': 'business_verification_required',
    }
    verify_profile(app, profile['id'], published=False)

    offerings = client.put(
        f"/api/businesses/{profile['id']}/offerings",
        json={'items': [
            {
                'name': 'Private lesson',
                'category': 'lesson',
                'description': 'One-on-one coaching.',
                'price_text': '$75',
                'duration_minutes': 60,
                'booking_url': 'book.thirdshot.example/lesson',
                'active': True,
            },
            {
                'name': 'Winter clinic',
                'category': 'clinic',
                'active': False,
            },
        ]},
        headers=headers(owner['token']),
    )
    assert offerings.status_code == 200, offerings.get_json()
    assert len(offerings.get_json()['offerings']) == 2  # owners see inactive drafts

    schedule = client.put(
        f"/api/businesses/{profile['id']}/schedule",
        json={'items': [
            {
                'title': 'Intermediate open play',
                'kind': 'open play',
                'day_of_week': 'Tuesday',
                'start_time': '18:00',
                'end_time': '20:00',
                'skill_level': 'intermediate',
                'booking_url': 'https://book.thirdshot.example/open-play',
                'active': True,
            },
            {
                'title': 'Paused league',
                'kind': 'league',
                'day_of_week': 'Thursday',
                'start_time': '19:00',
                'end_time': '21:00',
                'active': False,
            },
        ]},
        headers=headers(owner['token']),
    )
    assert schedule.status_code == 200, schedule.get_json()
    assert schedule.get_json()['schedule'][0]['kind'] == 'open_play'

    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None
    verify_profile(app, profile['id'])

    # Verifying business control does not approve sensitive booking links.
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None
    approve_profile_content(app, profile['id'], published=True)

    public = client.get(f'/api/courts/{court}/business')
    assert public.status_code == 200
    business = public.get_json()['business']
    assert business['verified'] is True
    assert business['is_owner'] is False
    assert business['email'] == 'play@thirdshot.example'
    assert [item['name'] for item in business['offerings']] == ['Private lesson']
    assert [item['title'] for item in business['schedule']] == [
        'Intermediate open play',
    ]
    public_business_detail = client.get(
        f"/api/businesses/{profile['id']}"
    ).get_json()
    public_business_list_item = client.get(
        '/api/businesses'
    ).get_json()['items'][0]
    assert public_business_detail == public_business_list_item == business
    for public_payload in (
        business, public_business_detail, public_business_list_item,
    ):
        for private_workflow_field in (
            'role', 'claim_status', 'published', 'created_at', 'updated_at',
            'owner_id', 'integration_requests',
        ):
            assert private_workflow_field not in public_payload

    owner_business_detail = client.get(
        f"/api/businesses/{profile['id']}", headers=headers(owner['token']),
    ).get_json()
    assert owner_business_detail['is_owner'] is True
    assert owner_business_detail['role'] == 'General manager'
    assert owner_business_detail['claim_status'] == 'verified'
    assert owner_business_detail['published'] is True
    owned = client.get(
        f'/api/courts/{court}/business', headers=headers(owner['token']),
    ).get_json()['business']
    assert owned['is_owner'] is True

    summary = client.get('/api/courts?q=Third Shot').get_json()['items'][0]
    assert summary['business'] == {
        'id': profile['id'],
        'name': 'Third Shot Pickleball Club',
        'logo_url': 'https://thirdshot.example/logo.png',
        'verified': True,
        'booking_available': True,
        'membership_available': True,
        'schedule_available': True,
        'programs_available': True,
    }
    detail = client.get(
        f'/api/courts/{court}', headers=headers(owner['token']),
    ).get_json()['business']
    assert detail['is_owner'] is True
    assert detail['verified'] is True
    assert detail['email'] == 'play@thirdshot.example'
    for private_workflow_field in (
        'role', 'claim_status', 'published', 'created_at', 'updated_at',
        'owner_id', 'integration_requests',
    ):
        assert private_workflow_field not in detail


def test_owner_can_preview_free_csv_schedule_import_without_saving(client):
    owner = register(client, 'csv-owner@example.com')
    viewer = register(client, 'csv-viewer@example.com')
    court = court_id(client)
    created = create_profile(client, owner['token'], court)
    assert created.status_code == 201, created.get_json()
    business_id = created.get_json()['id']
    csv_text = (
        'Name,Type,Day,Start,End,Time Zone,Audience,Capacity,Spots,'
        'Status,Location,Host,Booking Link,Visible,Date,Notes for staff\n'
        '"Beginner, Open Play",Open Play,Mon,6:00 PM,8:00 PM,'
        'America/Chicago,Beginners,24,8,Scheduled,Courts 1-4,Jamie,'
        'https://book.thirdshot.example/open-play,yes,,Bring loaner paddles\n'
        'Saturday clinic,Lesson or clinic,,9:30 AM,11:00 AM,,3.0+,12,0,'
        'Sold out,Center court,Alex,,no,09/12/2026,\n'
    )

    unauthorized = client.post(
        f'/api/businesses/{business_id}/schedule/import-preview',
        json={'csv': csv_text, 'timezone': 'America/Chicago'},
    )
    assert unauthorized.status_code == 401
    forbidden = client.post(
        f'/api/businesses/{business_id}/schedule/import-preview',
        json={'csv': csv_text, 'timezone': 'America/Chicago'},
        headers=headers(viewer['token']),
    )
    assert forbidden.status_code == 403

    response = client.post(
        f'/api/businesses/{business_id}/schedule/import-preview',
        json={'csv': csv_text, 'timezone': 'America/Chicago'},
        headers=headers(owner['token']),
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload['count'] == 2
    assert payload['ignored_columns'] == ['Notes for staff']
    first, second = payload['items']
    assert first == {
        'title': 'Beginner, Open Play',
        'kind': 'open_play',
        'day_of_week': 'monday',
        'start_time': '18:00',
        'end_time': '20:00',
        'timezone': 'America/Chicago',
        'recurrence': 'weekly',
        'start_date': None,
        'end_date': None,
        'event_date': None,
        'capacity': 24,
        'spots_remaining': 8,
        'status': 'scheduled',
        'location_note': 'Courts 1-4',
        'instructor': 'Jamie',
        'skill_level': 'Beginners',
        'booking_url': 'https://book.thirdshot.example/open-play',
        'active': True,
    }
    assert second['title'] == 'Saturday clinic'
    assert second['kind'] == 'lesson'
    assert second['day_of_week'] == 'saturday'
    assert second['event_date'] == '2026-09-12'
    assert second['recurrence'] == 'dated'
    assert second['timezone'] == 'America/Chicago'
    assert second['status'] == 'sold_out'
    assert second['active'] is False

    # Previewing is deliberately non-destructive. The existing schedule route
    # remains the only persistence boundary after the owner reviews the rows.
    mine = client.get('/api/businesses/mine', headers=headers(owner['token']))
    assert mine.status_code == 200
    assert mine.get_json()['items'][0]['schedule'] == []

    invalid = client.post(
        f'/api/businesses/{business_id}/schedule/import-preview',
        json={
            'csv': 'Title,Day,Start,End\nBad row,Monday,25:00,26:00\n',
            'timezone': 'America/Chicago',
        },
        headers=headers(owner['token']),
    )
    assert invalid.status_code == 400
    assert invalid.get_json() == {
        'error': 'schedule_csv_row_invalid',
        'row': 2,
        'detail': 'times_must_use_24_hour_hh_mm',
    }


def test_csv_schedule_import_rejects_ambiguous_or_oversized_files(client):
    owner = register(client, 'csv-validation-owner@example.com')
    created = create_profile(client, owner['token'], court_id(client))
    business_id = created.get_json()['id']
    endpoint = f'/api/businesses/{business_id}/schedule/import-preview'
    auth = headers(owner['token'])

    missing_columns = client.post(
        endpoint,
        json={'csv': 'Title,Day\nOpen play,Monday\n', 'timezone': 'UTC'},
        headers=auth,
    )
    assert missing_columns.status_code == 400
    assert missing_columns.get_json()['error'] == 'schedule_csv_required_columns'

    duplicate = client.post(
        endpoint,
        json={
            'csv': 'Title,Name,Start,End\nOpen play,Duplicate,09:00,10:00\n',
            'timezone': 'UTC',
        },
        headers=auth,
    )
    assert duplicate.status_code == 400
    assert duplicate.get_json()['error'] == 'schedule_csv_duplicate_column'

    oversized = client.post(
        endpoint,
        json={'csv': 'Title,Start,End\n' + ('x' * (256 * 1024)), 'timezone': 'UTC'},
        headers=auth,
    )
    assert oversized.status_code == 400
    assert oversized.get_json()['error'] == 'schedule_csv_too_large'


def test_map_business_signal_requires_verified_published_open_venue(app, client):
    owner = register(client, 'venue-matrix@example.com')
    now = utcnow()
    cases = [
        ('Public verified venue', 'verified', now, True, False, 'active', 'approved', True),
        ('Pending claim venue', 'pending', None, True, False, 'active', 'approved', False),
        ('Rejected claim venue', 'rejected', None, True, False, 'active', 'approved', False),
        ('Unpublished venue', 'verified', now, False, False, 'active', 'approved', False),
        ('Missing verification time venue', 'verified', None, True, False, 'active', 'approved', False),
        ('Suspended verified venue', 'verified', now, True, False, 'suspended', 'approved', False),
        ('Relinquished verified venue', 'verified', now, True, False, 'relinquished', 'approved', False),
        ('Pending content venue', 'verified', now, True, False, 'active', 'pending', False),
        ('Rejected content venue', 'verified', now, True, False, 'active', 'rejected', False),
        ('Closed verified venue', 'verified', now, True, True, 'active', 'approved', False),
    ]
    court_ids = {}
    business_ids = {}
    with app.app_context():
        for index, (
            name, status, verified_at, published, closed, governance_status,
            content_review_status, _,
        ) in enumerate(cases):
            court = Court(
                name=name,
                city='Austin', state='TX', county_slug='venue-matrix',
                latitude=30.30 + index * .01, longitude=-97.70,
                num_courts=4, closed=closed,
            )
            db.session.add(court)
            db.session.flush()
            court_ids[name] = court.id
            profile = BusinessProfile(
                owner_id=owner['user']['id'], court_id=court.id,
                name=f'{name} operator', claim_status=status,
                verified_at=verified_at, published=published,
                governance_status=governance_status,
                content_review_status=content_review_status,
                contact_email=f'private-{index}@example.com',
                booking_url='https://book.example.com' if index == 0 else '',
            )
            db.session.add(profile)
            db.session.flush()
            business_ids[name] = profile.id
        db.session.commit()

    statements = []
    with app.app_context():
        def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.lower())

        event.listen(db.engine, 'before_cursor_execute', record_statement)
        try:
            response = client.get('/api/courts?q=venue')
        finally:
            event.remove(db.engine, 'before_cursor_execute', record_statement)
    assert response.status_code == 200
    assert len([
        statement for statement in statements
        if 'from business_profile' in statement
    ]) == 1
    by_name = {item['name']: item for item in response.get_json()['items']}
    assert 'Closed verified venue' not in by_name
    public_directory = client.get('/api/businesses?q=venue').get_json()['items']
    public_directory_ids = {item['id'] for item in public_directory}
    for name, _, _, _, closed, _, _, visible in cases:
        if closed:
            continue
        compact = by_name[name]['business']
        assert bool(compact) is visible
        if compact:
            assert compact['verified'] is True
            assert compact['booking_available'] is True
            assert 'email' not in compact
            assert 'owner_id' not in compact
            assert 'claim_status' not in compact
        detail = client.get(f'/api/courts/{court_ids[name]}').get_json()
        assert bool(detail['business']) is visible
        court_business = client.get(
            f'/api/courts/{court_ids[name]}/business',
        ).get_json()['business']
        assert bool(court_business) is visible
        assert (business_ids[name] in public_directory_ids) is visible
        business_detail = client.get(
            f'/api/businesses/{business_ids[name]}',
        )
        assert business_detail.status_code == (200 if visible else 404)
        integrated_schedule = client.get(
            f'/api/businesses/{business_ids[name]}/integrated-schedule',
        )
        assert integrated_schedule.status_code == (200 if visible else 404)

    closed_detail = client.get(
        f"/api/courts/{court_ids['Closed verified venue']}",
    ).get_json()
    assert closed_detail['business'] is None


def test_schedule_booking_link_sets_compact_court_booking_signal(app, client):
    owner = register(client, 'schedule-owner@example.com')
    court = court_id(client)
    profile = create_profile(
        client, owner['token'], court, booking_url='', membership_url='',
    ).get_json()
    schedule = client.put(
        f"/api/businesses/{profile['id']}/schedule",
        json={'items': [{
            'title': 'Friday open play', 'kind': 'open_play',
            'day_of_week': 'friday', 'start_time': '18:00', 'end_time': '20:00',
            'booking_url': 'https://booking.example.com/friday',
        }]},
        headers=headers(owner['token']),
    )
    assert schedule.status_code == 200
    verify_profile(app, profile['id'])
    compact = client.get('/api/courts?q=Third Shot').get_json()['items'][0]['business']
    assert compact['booking_available'] is True
    assert compact['schedule_available'] is True
    assert compact['programs_available'] is True


def test_competing_claim_never_overwrites_owner_or_content(app, client):
    owner = register(client, 'owner@example.com', 'Owner')
    competitor = register(client, 'other@example.com', 'Other')
    court = court_id(client)
    profile = create_profile(client, owner['token'], court).get_json()

    claim = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'Assistant manager',
            'authorized_attestation': True,
        },
        headers=headers(competitor['token']),
    )
    assert claim.status_code == 201
    assert claim.get_json()['claim']['status'] == 'pending'
    assert claim.get_json()['business'] is None
    forbidden = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'name': 'Hijacked listing'},
        headers=headers(competitor['token']),
    )
    assert forbidden.status_code == 403
    assert forbidden.get_json()['error'] == 'business_owner_only'

    with app.app_context():
        persisted = db.session.get(BusinessProfile, profile['id'])
        assert persisted.name == 'Third Shot Pickleball Club'
        assert persisted.owner.email == 'owner@example.com'


def test_owner_cannot_self_verify_and_validation_is_atomic(app, client):
    owner = register(client, 'owner@example.com')
    court = court_id(client)
    profile = create_profile(client, owner['token'], court).get_json()

    verify_attempt = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'claim_status': 'verified', 'verified': True},
        headers=headers(owner['token']),
    )
    assert verify_attempt.status_code == 400
    assert verify_attempt.get_json()['error'] == 'verification_fields_are_server_managed'

    bad_url = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'name': 'Must not persist', 'booking_url': 'javascript:alert(1)'},
        headers=headers(owner['token']),
    )
    assert bad_url.status_code == 400
    with app.app_context():
        persisted = db.session.get(BusinessProfile, profile['id'])
        assert persisted.name == 'Third Shot Pickleball Club'
        assert persisted.claim_status == 'pending'
        assert persisted.verified_at is None

    bad_time = client.put(
        f"/api/businesses/{profile['id']}/schedule",
        json={'items': [{
            'title': 'Open play', 'kind': 'open_play',
            'day_of_week': 'Funday', 'start_time': '6pm', 'end_time': '20:00',
        }]},
        headers=headers(owner['token']),
    )
    assert bad_time.status_code == 400
    reversed_time = client.put(
        f"/api/businesses/{profile['id']}/schedule",
        json={'items': [{
            'title': 'Late open play', 'kind': 'open_play',
            'day_of_week': 'friday', 'start_time': '20:00', 'end_time': '18:00',
        }]},
        headers=headers(owner['token']),
    )
    assert reversed_time.status_code == 400
    assert reversed_time.get_json()['error'] == 'end_time_must_be_after_start_time'


def test_integration_requests_are_durable_private_and_owner_scoped(
    app, client, caplog,
):
    owner = register(client, 'owner@example.com')
    other = register(client, 'other@example.com')
    court = court_id(client)
    profile = create_profile(client, owner['token'], court).get_json()
    verify_profile(app, profile['id'], published=False)

    created = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={
            'provider': 'CourtReserve',
            'capabilities': ['bookings', 'lessons', 'schedule'],
            'details': 'Sync live availability and deep-link lesson checkout.',
        },
        headers=headers(owner['token']),
    )
    assert created.status_code == 201, created.get_json()
    request_data = created.get_json()['request']
    assert request_data['provider'] == 'CourtReserve'
    assert request_data['capabilities'] == ['bookings', 'lessons', 'schedule']
    assert request_data['contact_email'] == 'play@thirdshot.example'
    assert request_data['status'] == 'submitted'
    assert any(
        f'id={request_data["id"]}' in record.getMessage()
        and f'business_id={profile["id"]}' in record.getMessage()
        for record in caplog.records
    )
    assert all('CourtReserve' not in record.getMessage() for record in caplog.records)

    secret = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={'provider': 'Custom', 'details': 'api_key = do-not-store-this'},
        headers=headers(owner['token']),
    )
    assert secret.status_code == 400
    assert secret.get_json() == {'error': 'integration_request_may_contain_secret'}
    blank_contact = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={'provider': 'Custom', 'contact_email': ''},
        headers=headers(owner['token']),
    )
    assert blank_contact.status_code == 400
    assert blank_contact.get_json() == {'error': 'contact_email_required'}

    assert client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={'provider': 'Hijack'}, headers=headers(other['token']),
    ).status_code == 403
    invalid = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={'capabilities': ['arbitrary_database_access']},
        headers=headers(owner['token']),
    )
    assert invalid.status_code == 400
    assert invalid.get_json()['error'] == 'invalid_capability'

    history = client.get(
        f"/api/businesses/{profile['id']}/integration-requests",
        headers=headers(owner['token']),
    )
    assert history.status_code == 200
    assert [item['id'] for item in history.get_json()['items']] == [request_data['id']]
    assert client.get(
        f"/api/businesses/{profile['id']}/integration-requests",
        headers=headers(other['token']),
    ).status_code == 403
    mine = client.get('/api/businesses/mine', headers=headers(owner['token'])).get_json()
    assert mine['items'][0]['integration_requests'][0]['id'] == request_data['id']

    with app.app_context():
        contacted = update_business_integration_request_status(
            request_data['id'], 'contacted',
            operator_identifier='integrations@example.com',
            status_message='We emailed the business contact.',
        )
        db.session.commit()
        assert contacted['status'] == 'contacted'
        completed = update_business_integration_request_status(
            request_data['id'], 'completed',
            operator_identifier='integrations@example.com',
            status_message='Request handled; no automatic sync is active.',
        )
        db.session.commit()
        assert completed['status'] == 'completed'
        with pytest.raises(
            BusinessIntegrationRequestError,
            match='integration_request_is_closed',
        ):
            update_business_integration_request_status(
                request_data['id'], 'declined',
                operator_identifier='integrations@example.com',
                status_message='This request is closed.',
            )
        db.session.rollback()
        assert Notification.query.filter_by(
            user_id=owner['user']['id'], kind='business_integration',
        ).count() == 2

    verify_profile(app, profile['id'])
    owner_history = client.get(
        f"/api/businesses/{profile['id']}/integration-requests",
        headers=headers(owner['token']),
    ).get_json()['items'][0]
    assert owner_history['status_message'] == (
        'Request handled; no automatic sync is active.'
    )
    assert 'handled_by' not in owner_history
    public = client.get(f'/api/courts/{court}/business').get_json()['business']
    assert 'integration_requests' not in public


def test_integration_request_operator_cli_rejects_pooled_or_non_postgres_urls(
    monkeypatch,
):
    monkeypatch.setenv('TARGET_DATABASE_URL', 'sqlite:///wrong.db')
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        integration_request_target_url()
    monkeypatch.setenv(
        'TARGET_DATABASE_URL',
        'postgresql://user:pass@example-pooler.us-east-1.aws.neon.tech/db',
    )
    with pytest.raises(RuntimeError, match='direct/unpooled'):
        integration_request_target_url()


def test_claim_cli_preserves_review_syntax_and_has_pending_inbox():
    listing = parse_claim_cli_args(['list'])
    assert listing.command == 'list'
    assert listing.status == 'pending'
    assert listing.limit == 100

    review = parse_claim_cli_args([
        '123', 'approve', '--reviewer', 'reviewer@example.com',
        '--method', 'business_email', '--note', 'Confirmed by email.',
    ])
    assert review.command == 'review'
    assert review.claim_id == 123
    assert review.decision == 'approve'
    assert review.reviewer == 'reviewer@example.com'
    assert review.method == 'business_email'
    assert review.confirm_transfer is False


def test_closed_court_cannot_be_approved_or_exposed(app, client):
    owner = register(client, 'owner@example.com')
    court = court_id(client)
    claim = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'Owner',
            'authorized_attestation': True,
        },
        headers=headers(owner['token']),
    ).get_json()
    with app.app_context():
        db.session.get(Court, court).closed = True
        db.session.commit()
        with pytest.raises(BusinessClaimReviewError, match='court_closed'):
            review_business_claim(
                claim['claim']['id'], 'approve', **REVIEW_KWARGS,
            )
        db.session.rollback()
    assert client.get(f'/api/courts/{court}/business').status_code == 404
    assert client.get('/api/businesses').get_json()['items'] == []


def test_operator_review_is_explicit_and_does_not_silently_publish(app, client):
    owner = register(client, 'owner@example.com')
    court = court_id(client)
    claim_response = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'Owner',
            'authorized_attestation': True,
        },
        headers=headers(owner['token']),
    ).get_json()
    claim_id = claim_response['claim']['id']
    business_id = claim_response['business']['id']

    with app.app_context():
        result = review_business_claim(claim_id, 'approve', **REVIEW_KWARGS)
        db.session.commit()
        assert result['business']['verified'] is True
        assert result['business']['published'] is False
        assert result['verification_meaning'] == 'control_confirmed_not_endorsed'
        assert result['publication_requires_owner_review'] is True
        assert result['review_event']['reviewer_identifier'] == 'reviewer@example.com'
        assert BusinessClaimReviewEvent.query.count() == 1
        assert Notification.query.filter_by(
            user_id=owner['user']['id'], kind='business_claim',
        ).count() == 1
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None

    with app.app_context():
        with pytest.raises(
            BusinessClaimReviewError, match='claim_not_pending',
        ):
            review_business_claim(claim_id, 'reject', **REVIEW_KWARGS)
        profile = db.session.get(BusinessProfile, business_id)
        profile.published = True
        db.session.commit()
    assert client.get(f'/api/courts/{court}/business').get_json()['business']['id'] == business_id


def test_reopened_claim_preserves_immutable_review_history(app, client):
    owner = register(client, 'history-owner@example.com')
    court = court_id(client)
    submitted = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'Owner',
            'authorized_attestation': True,
        },
        headers=headers(owner['token']),
    ).get_json()
    claim_id = submitted['claim']['id']
    with app.app_context():
        review_business_claim(
            claim_id,
            'reject',
            **{
                **REVIEW_KWARGS,
                'review_note': 'Could not confirm control from the submitted account.',
            },
        )
        db.session.commit()

    reopened = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'Owner',
            'authorized_attestation': True,
        },
        headers=headers(owner['token']),
    )
    assert reopened.status_code == 200
    assert reopened.get_json()['claim']['status'] == 'pending'

    with app.app_context():
        review_business_claim(claim_id, 'approve', **REVIEW_KWARGS)
        db.session.commit()
        claim = db.session.get(BusinessClaim, claim_id)
        assert [event.decision for event in claim.review_events] == [
            'reject', 'approve',
        ]
        operator_payload = _operator_claim_payload(claim)
        assert [event['decision'] for event in operator_payload['review_history']] == [
            'reject', 'approve',
        ]
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None


def test_operator_takeover_demotes_old_claim_and_requires_fresh_pending_request(app, client):
    owner = register(client, 'owner@example.com')
    replacement = register(client, 'replacement@example.com')
    court = court_id(client)
    profile = create_profile(client, owner['token'], court).get_json()
    verify_profile(app, profile['id'], published=False)
    private_request = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={
            'provider': 'Prior owner system',
            'capabilities': ['memberships'],
            'details': 'Private workflow and contact notes.',
        },
        headers=headers(owner['token']),
    )
    assert private_request.status_code == 201
    assert client.put(
        f"/api/businesses/{profile['id']}/offerings",
        json={'items': [{
            'name': 'Prior owner lesson', 'category': 'lesson',
            'booking_url': 'https://prior-owner.example/lesson',
        }]},
        headers=headers(owner['token']),
    ).status_code == 200
    assert client.put(
        f"/api/businesses/{profile['id']}/schedule",
        json={'items': [{
            'title': 'Prior owner clinic', 'kind': 'clinic',
            'day_of_week': 'monday', 'start_time': '09:00',
            'end_time': '10:00',
            'booking_url': 'https://prior-owner.example/clinic',
        }]},
        headers=headers(owner['token']),
    ).status_code == 200
    competing = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'New owner',
            'authorized_attestation': True,
        },
        headers=headers(replacement['token']),
    ).get_json()['claim']

    with app.app_context():
        takeover_notice = Notification.query.filter_by(
            user_id=owner['user']['id'], kind='business_claim',
        ).one()
        assert 'awaiting review' in takeover_notice.body

    with app.app_context():
        inbox_item = _operator_claim_payload(
            db.session.get(BusinessClaim, competing['id'])
        )
        assert inbox_item['ownership_transfer'] is True
        assert inbox_item['approval_publication_policy'] == (
            'reset_unpublished_for_new_owner_review'
        )

    with app.app_context():
        original = BusinessClaim.query.filter_by(
            user_id=owner['user']['id'], court_id=court,
        ).first()
        review_business_claim(original.id, 'approve', **REVIEW_KWARGS)
        db.session.commit()
        assert db.session.get(BusinessClaim, competing['id']).status == 'rejected'
    approve_profile_content(app, profile['id'])
    published = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'published': True}, headers=headers(owner['token']),
    )
    assert published.status_code == 200
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is not None

    reopened = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'New owner',
            'authorized_attestation': True,
        },
        headers=headers(replacement['token']),
    )
    assert reopened.status_code == 200
    assert reopened.get_json()['claim']['status'] == 'pending'

    with app.app_context():
        with pytest.raises(
            BusinessClaimReviewError,
            match='ownership_transfer_requires_confirmation',
        ):
            review_business_claim(competing['id'], 'approve', **REVIEW_KWARGS)
        db.session.rollback()
        result = review_business_claim(
            competing['id'], 'approve', confirm_transfer=True, **REVIEW_KWARGS,
        )
        db.session.commit()
        assert result['business']['claim_status'] == 'verified'
        assert result['business']['published'] is False
        assert result['business']['integration_requests'] == []
        assert result['business']['offerings'] == []
        assert result['business']['schedule'] == []
        assert result['ownership_transferred'] is True
        assert result['publication_requires_owner_review'] is True
        for field in (
            'description', 'announcement', 'email', 'phone', 'hours',
            'website_url', 'booking_url', 'membership_url', 'logo_url',
        ):
            assert result['business'][field] == ''
        assert result['business']['amenities'] == []
        assert db.session.get(BusinessProfile, profile['id']).owner_id == replacement['user']['id']
        assert BusinessIntegrationRequest.query.filter_by(
            business_id=profile['id'],
        ).count() == 0
        assert BusinessClaim.query.filter_by(
            user_id=owner['user']['id'], court_id=court,
        ).first().status == 'rejected'

    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None
    republished = client.patch(
        f"/api/businesses/{profile['id']}",
        json={
            'description': 'Reviewed by the new owner.',
            'booking_url': 'https://replacement.example/book',
            'published': True,
        },
        headers=headers(replacement['token']),
    )
    assert republished.status_code == 200, republished.get_json()
    assert republished.get_json()['published'] is False
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None
    approve_profile_content(app, profile['id'])
    publish_after_review = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'published': True},
        headers=headers(replacement['token']),
    )
    assert publish_after_review.status_code == 200, publish_after_review.get_json()
    public = client.get(f'/api/courts/{court}/business').get_json()['business']
    assert public['booking_url'] == 'https://replacement.example/book'
    assert 'prior-owner.example' not in str(public)


def test_account_deletion_retires_listing_and_allows_pending_reclaim(app, client):
    owner = register(client, 'owner@example.com')
    replacement = register(client, 'replacement@example.com')
    court = court_id(client)
    profile = create_profile(client, owner['token'], court).get_json()
    verify_profile(app, profile['id'], published=False)
    request_result = client.post(
        f"/api/businesses/{profile['id']}/integration-requests",
        json={
            'provider': 'Private provider',
            'capabilities': ['bookings'],
            'details': 'Private integration notes.',
        },
        headers=headers(owner['token']),
    )
    assert request_result.status_code == 201
    verify_profile(app, profile['id'])
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is not None

    deleted = client.delete(
        '/api/me', json={'password': 'secret123'}, headers=headers(owner['token']),
    )
    assert deleted.status_code == 200, deleted.get_json()
    assert client.get(f'/api/courts/{court}/business').get_json()['business'] is None

    reclaim = client.post(
        '/api/businesses/claims',
        json={
            'court_id': court, 'role': 'New general manager',
            'authorized_attestation': True,
        },
        headers=headers(replacement['token']),
    )
    assert reclaim.status_code == 201, reclaim.get_json()
    assert reclaim.get_json()['business']['claim_status'] == 'pending'
    assert reclaim.get_json()['business']['verified'] is False
    assert reclaim.get_json()['business']['published'] is False
    assert reclaim.get_json()['business']['integration_requests'] == []
    with app.app_context():
        persisted = db.session.get(BusinessProfile, profile['id'])
        assert persisted.owner.email == 'replacement@example.com'
        assert persisted.contact_email == ''
        assert persisted.booking_url == ''
        assert persisted.offerings == []
        assert persisted.schedule_items == []
        assert BusinessIntegrationRequest.query.filter_by(
            business_id=profile['id'],
        ).count() == 0
        assert BusinessClaim.query.filter_by(user_id=owner['user']['id']).count() == 0


def test_additive_upgrade_and_release_verifier_cover_business_tables(app):
    with app.app_context():
        # Simulate the production operator path: core schema exists, the new
        # additive tables do not, and AUTO_CREATE_DB is unavailable.
        BusinessClaimReviewEvent.__table__.drop(db.engine)
        BusinessIntegrationRequest.__table__.drop(db.engine)
        BusinessScheduleItem.__table__.drop(db.engine)
        BusinessOffering.__table__.drop(db.engine)
        BusinessClaim.__table__.drop(db.engine)
        BusinessProfile.__table__.drop(db.engine)
        _upgrade_schema(app)

        tables = set(inspect(db.engine).get_table_names())
        assert {
            'business_profile', 'business_claim', 'business_offering',
            'business_claim_review_event', 'business_schedule_item',
            'business_integration_request',
        } <= tables
        business_gaps = [
            gap for gap in _schema_gaps(inspect(db.engine), schema=None)
            if gap.startswith('business_')
        ]
        assert business_gaps == []
