"""One durable proof that the business platform works across its boundaries."""
from __future__ import annotations

import re
import time

import pytest

from backend.app import create_app, db
from backend.models import BusinessProfileRevision, Court, User
from backend.integrations.models import BusinessProviderConnection
from backend.integrations.services import recheck_connection_links
from backend.services.mfa import _totp_at


PASSWORD = 'strong-password-123'


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        # Flask-SQLAlchemy is shared across test app factories; start this
        # whole-story contract from a provably empty schema even when it runs
        # after another integration module in the same pytest process.
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Whole Story Pickleball',
            address='10 Integration Way',
            city='Austin',
            state='TX',
            county_slug='travis-county',
            latitude=30.25,
            longitude=-97.75,
            num_courts=8,
            website='https://whole-story.example/locations/austin',
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': PASSWORD,
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account_or_token):
    token = (
        account_or_token['token']
        if isinstance(account_or_token, dict) else account_or_token
    )
    return {'Authorization': f'Bearer {token}'}


def enable_mfa(client, token):
    setup = client.post('/api/auth/mfa/setup', json={
        'current_password': PASSWORD,
    }, headers=auth(token))
    assert setup.status_code == 200, setup.get_json()
    secret = setup.get_json()['secret']
    enabled = client.post('/api/auth/mfa/enable', json={
        'code': _totp_at(secret, time.time()),
    }, headers=auth(token))
    assert enabled.status_code == 200, enabled.get_json()
    return secret, enabled.get_json()['token']


def test_claim_review_publish_team_catalog_and_analytics_story(app, client):
    owner = register(client, 'owner@whole-story.example', 'Venue owner')
    reviewer = register(client, 'reviewer@example.com', 'Business reviewer')
    viewer = register(client, 'viewer@example.com', 'Location viewer')

    claimed = client.post('/api/businesses/claims', json={
        'court_id': 1,
        'role': 'Owner',
        'authorized_attestation': True,
    }, headers=auth(owner))
    assert claimed.status_code == 201, claimed.get_json()
    business_id = claimed.get_json()['business']['id']
    claim_id = claimed.get_json()['claim']['id']

    configured_profile = client.patch(
        f'/api/businesses/{business_id}',
        json={'booking_url': 'https://whole-story.example/book'},
        headers=auth(owner),
    )
    assert configured_profile.status_code == 200, configured_profile.get_json()

    evidence = client.post(
        f'/api/businesses/{business_id}/verification/evidence',
        json={
            'type': 'business_email',
            'value': 'operations@whole-story.example',
        },
        headers=auth(owner),
    )
    assert evidence.status_code == 201, evidence.get_json()
    evidence_id = evidence.get_json()['evidence']['id']
    delivered = app.extensions['email_outbox'][-1]
    code = re.search(r'\b(\d{6})\b', delivered['text']).group(1)
    verified = client.post(
        f'/api/businesses/{business_id}/verification/evidence/{evidence_id}/verify',
        json={'token': code},
        headers=auth(owner),
    )
    assert verified.status_code == 200, verified.get_json()
    assert verified.get_json()['evidence']['status'] == 'verified'

    with app.app_context():
        db.session.get(User, reviewer['user']['id']).operator_role = 'reviewer'
        db.session.commit()
    reviewer_secret, reviewer_token = enable_mfa(client, reviewer['token'])
    reviewed = client.post(
        f'/api/operator/business/claims/{claim_id}/review',
        json={
            'decision': 'approve',
            'verification_method': 'business_email',
            'review_note': 'The independently listed domain mailbox was verified.',
            'claimant_feedback': 'Control verified. Review and publish your listing.',
            'mfa_code': _totp_at(reviewer_secret, time.time()),
        },
        headers=auth(reviewer_token),
    )
    assert reviewed.status_code == 200, reviewed.get_json()

    with app.app_context():
        revision_id = BusinessProfileRevision.query.filter_by(
            business_id=business_id,
            review_status='pending',
        ).order_by(BusinessProfileRevision.id.desc()).one().id
    content_reviewed = client.post(
        f'/api/operator/business/revisions/{revision_id}/review',
        json={
            'decision': 'approve',
            'review_note': 'The booking destination matches the verified club domain.',
            'mfa_code': _totp_at(reviewer_secret, time.time()),
        },
        headers=auth(reviewer_token),
    )
    assert content_reviewed.status_code == 200, content_reviewed.get_json()

    published = client.patch(
        f'/api/businesses/{business_id}',
        json={'published': True},
        headers=auth(owner),
    )
    assert published.status_code == 200, published.get_json()
    assert published.get_json()['published'] is True

    invitation = client.post(
        f'/api/businesses/{business_id}/team/invitations',
        json={'email': 'viewer@example.com', 'role': 'viewer'},
        headers=auth(owner),
    )
    assert invitation.status_code == 201, invitation.get_json()
    invite_text = app.extensions['email_outbox'][-1]['text']
    invite_token = re.search(r'#business-invitation=([^\s]+)', invite_text).group(1)
    accepted = client.post(
        f'/api/business-invitations/{invite_token}/accept',
        headers=auth(viewer),
    )
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()['member']['role'] == 'viewer'
    with app.app_context():
        assert BusinessProviderConnection.query.filter_by(
            business_id=business_id,
        ).count() == 0

    connected = client.post(
        f'/api/businesses/{business_id}/connections',
        json={
            'provider_key': 'link_catalog',
            'display_name': 'Club schedule feed',
            'config': {
                'label': 'Live schedule',
                'booking_base_url': 'https://whole-story.example/book',
            },
        },
        headers=auth(owner),
    )
    assert connected.status_code == 201, connected.get_json()
    connection_id = connected.get_json()['connection']['id']
    catalog = {
        'schema_version': 1,
        'source_version': 'whole-story-v1',
        'generated_at': '2026-09-01T15:00:00Z',
        'authoritative': True,
        'occurrences': [{
            'external_id': 'clinic-2026-09-08',
            'title': 'Intermediate clinic',
            'kind': 'clinic',
            'event_date': '2026-09-08',
            'start_time': '18:00',
            'end_time': '20:00',
            'timezone': 'America/Chicago',
            'capacity': 16,
            'spots_remaining': 5,
            'status': 'scheduled',
            'booking_url': 'https://whole-story.example/book/clinic',
        }],
        'conversions': [],
    }
    synced = client.put(
        f'/api/businesses/{business_id}/connections/{connection_id}/catalog',
        json=catalog,
        headers={**auth(owner), 'Idempotency-Key': 'whole-story-v1'},
    )
    assert synced.status_code == 202, synced.get_json()
    assert synced.get_json()['run']['status'] == 'succeeded'

    with app.app_context():
        connection = db.session.get(BusinessProviderConnection, connection_id)
        recheck_connection_links(
            connection,
            resolver=lambda *_args, **_kwargs: [
                (2, 1, 6, '', ('93.184.216.34', 443)),
            ],
            transport=lambda _url, **_kwargs: (200, {}, 1),
        )
        db.session.commit()

    public_listing = client.get('/api/courts/1/business')
    assert public_listing.status_code == 200
    assert public_listing.get_json()['business']['name'] == 'Whole Story Pickleball'
    public_schedule = client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
        '?from=2026-09-01&to=2026-09-30'
    )
    assert public_schedule.status_code == 200, public_schedule.get_json()
    assert public_schedule.get_json()['items'][0]['spots_remaining'] == 5

    viewer_connections = client.get(
        f'/api/businesses/{business_id}/connections',
        headers=auth(viewer),
    )
    assert viewer_connections.status_code == 200
    forbidden_create = client.post(
        f'/api/businesses/{business_id}/connections',
        json={'provider_key': 'link_catalog', 'config': {}},
        headers=auth(viewer),
    )
    assert forbidden_create.status_code == 403

    event = client.post(f'/api/businesses/{business_id}/events', json={
        'client_event_id': 'whole-story-booking-click-1',
        'action': 'booking',
        'connection_id': connection_id,
        'occurrence_id': public_schedule.get_json()['items'][0]['id'],
    })
    assert event.status_code == 201, event.get_json()
    analytics = client.get(
        f'/api/businesses/{business_id}/analytics?range=30d',
        headers=auth(owner),
    )
    assert analytics.status_code == 200, analytics.get_json()
    assert analytics.get_json()['booking_clicks'] == 1
