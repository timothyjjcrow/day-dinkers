"""End-to-end reviewed projection and conditional manager edits, isolated SQLite."""
import hashlib
import time

import pytest

from test_business_governance import (
    app, client, auth, create_profile, enable_mfa, make_operator, register,
)
from backend.app import db
from backend.models import BusinessOffering, BusinessProfile, BusinessProfileRevision, utcnow
from backend.integrations.models import BusinessLinkHealthCheck
from backend.services.business_governance import ensure_organization
from backend.models import BusinessOrganizationMember
from backend.services.mfa import _totp_at


@pytest.fixture()
def live(app, client):
    owner = register(client, 'live-owner@example.test')
    profile = create_profile(client, owner['token'], 1, name='Reviewed Club')
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        business.claim_status = 'verified'
        business.verified_at = utcnow()
        business.published = True
        business.booking_url = 'https://official.example/book'
        business.website_url = 'https://official.example'
        db.session.add(BusinessOffering(business=business, name='Reviewed lesson', category='lesson', booking_url='https://official.example/lesson'))
        db.session.commit()
    return owner, profile['id']


def current(client, owner, business_id):
    response = client.get(f'/api/businesses/{business_id}', headers=auth(owner['token']))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def versioned(owner, data):
    return {**auth(owner['token']), 'If-Match': f'"{data["content_version"]}"'}


def public(client, business_id):
    response = client.get(f'/api/businesses/{business_id}')
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_reviewed_listing_stays_live_and_approval_requires_explicit_publish(app, client, live):
    owner, bid = live
    original = current(client, owner, bid)
    saved = client.patch(f'/api/businesses/{bid}', json={
        'name': 'Proposed Club', 'booking_url': 'https://official.example/new-book',
    }, headers=versioned(owner, original))
    assert saved.status_code == 200, saved.get_json()
    draft = saved.get_json()
    assert draft['is_public'] and draft['published'] and draft['has_unpublished_changes']
    assert draft['content_review_status'] == 'pending'
    assert draft['name'] == 'Proposed Club'
    player = public(client, bid)
    assert player['name'] == 'Reviewed Club'
    assert client.get('/api/businesses?q=Proposed').get_json()['items'] == []
    assert client.get('/api/businesses?q=Reviewed').get_json()['items'][0]['name'] == 'Reviewed Club'
    assert player['booking_url'] == 'https://official.example/book'
    court = client.get('/api/courts/1').get_json()['business']
    assert court['name'] == 'Reviewed Club'
    with app.app_context():
        from backend.routes.courts import _public_business_summaries
        assert _public_business_summaries([1])[1]['name'] == 'Reviewed Club'
        from backend.integrations.services import _approved_business_action_urls
        assert 'https://official.example/book' in _approved_business_action_urls(db.session.get(BusinessProfile, bid))
        assert 'https://official.example/new-book' not in _approved_business_action_urls(db.session.get(BusinessProfile, bid))
        revision_id = BusinessProfileRevision.query.filter_by(business_id=bid).first().id
    reviewer = register(client, 'reviewer-live@example.test')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    secret, token, _ = enable_mfa(client, reviewer['token'])
    approved = client.post(f'/api/operator/business/revisions/{revision_id}/review', json={
        'decision': 'approve', 'review_note': 'Confirmed the new official booking page.',
        'mfa_code': _totp_at(secret, time.time()),
    }, headers=auth(token))
    assert approved.status_code == 200, approved.get_json()
    assert public(client, bid)['name'] == 'Reviewed Club'
    latest = current(client, owner, bid)
    assert latest['has_unpublished_changes'] and latest['content_review_status'] == 'approved'
    published = client.patch(f'/api/businesses/{bid}', json={'published': True}, headers=versioned(owner, latest))
    assert published.status_code == 200, published.get_json()
    assert public(client, bid)['name'] == 'Proposed Club'
    assert not published.get_json()['has_unpublished_changes']


def test_pending_collections_keep_approved_rows_and_unsafe_hold_is_link_scoped(app, client, live):
    owner, bid = live
    data = current(client, owner, bid)
    edited = client.put(f'/api/businesses/{bid}/offerings', json={'items': [{
        **data['offerings'][0], 'name': 'Proposed lesson', 'booking_url': 'https://official.example/new-lesson',
    }]}, headers=versioned(owner, data))
    assert edited.status_code == 200, edited.get_json()
    assert public(client, bid)['offerings'][0]['name'] == 'Reviewed lesson'
    with app.app_context():
        db.session.add(BusinessLinkHealthCheck(business_id=bid, link_kind='profile_booking',
            url_hash=hashlib.sha256(b'https://official.example/book').hexdigest(), status='unsafe'))
        db.session.commit()
    shown = public(client, bid)
    assert shown['booking_url'] == ''
    assert shown['website_url'] == 'https://official.example'
    assert shown['offerings'][0]['booking_url'] == 'https://official.example/lesson'


@pytest.mark.parametrize('suffix,method,body', [
    ('', 'patch', {'description': 'Updated'}),
    ('/offerings', 'put', {'items': []}),
    ('/schedule', 'put', {'items': []}),
    ('/logo', 'delete', None),
])
def test_content_writes_require_real_version(client, live, suffix, method, body):
    owner, bid = live
    response = getattr(client, method)(f'/api/businesses/{bid}{suffix}', json=body, headers=auth(owner['token']))
    assert response.status_code == 428
    response = getattr(client, method)(f'/api/businesses/{bid}{suffix}', json=body, headers={**auth(owner['token']), 'If-Match': '*'})
    assert response.status_code == 412


def test_two_managers_cannot_replace_each_others_saved_collection(app, client, live):
    owner, bid = live
    editor = register(client, 'editor-live@example.test')
    with app.app_context():
        business = db.session.get(BusinessProfile, bid)
        organization = ensure_organization(business)
        db.session.add(BusinessOrganizationMember(organization=organization, user_id=editor['user']['id'], role='editor'))
        db.session.commit()
    first = current(client, owner, bid)
    second = current(client, editor, bid)
    one = client.put(f'/api/businesses/{bid}/offerings', json={'items': [
        {**first['offerings'][0], 'description': 'Owner saved this useful detail'},
    ]}, headers=versioned(owner, first))
    assert one.status_code == 200, one.get_json()
    two = client.put(f'/api/businesses/{bid}/offerings', json={'items': []}, headers=versioned(editor, second))
    assert two.status_code == 412, two.get_json()
    assert current(client, editor, bid)['offerings'][0]['description'] == 'Owner saved this useful detail'
    assert two.get_json()['content_version'] == one.get_json()['content_version']


def test_revision_detail_and_restore_use_current_version_without_logo_bytes(client, live):
    owner, bid = live
    data = current(client, owner, bid)
    first = client.patch(f'/api/businesses/{bid}', json={'description': 'First version'}, headers=versioned(owner, data)).get_json()
    second = client.patch(f'/api/businesses/{bid}', json={'description': 'Second version'}, headers=versioned(owner, first)).get_json()
    history = client.get(f'/api/businesses/{bid}/revisions', headers=auth(owner['token'])).get_json()
    revision = history['items'][-1]
    assert revision['change_summary'] == 'Description'
    assert revision['after_snapshot']['profile']['description'] == 'First version'
    assert 'logo_data' not in revision['before_snapshot']['profile']
    assert 'logo_data' not in history['current_snapshot']['profile']
    stale = client.post(f'/api/businesses/{bid}/revisions/{revision["id"]}/restore', headers=versioned(owner, first))
    assert stale.status_code == 412
    restored = client.post(f'/api/businesses/{bid}/revisions/{revision["id"]}/restore', headers=versioned(owner, second))
    assert restored.status_code == 200, restored.get_json()
    assert restored.get_json()['business']['description'] == 'First version'
    assert public(client, bid)['description'] == 'Second version'


def test_analytics_distinguishes_unavailable_conversion_reporting_from_zero(client, live):
    owner, bid = live
    response = client.get(f'/api/businesses/{bid}/analytics', headers=auth(owner['token']))
    assert response.status_code == 200
    data = response.get_json()
    assert data['booking_clicks'] == 0
    assert data['conversions'] is None and data['conversion_rate'] is None
    assert data['conversion_reporting_available'] is False


def test_public_logo_never_uses_draft_bytes_even_for_owner(app, client, live):
    from io import BytesIO
    import base64
    from PIL import Image

    def png(color):
        output = BytesIO()
        Image.new('RGB', (2, 2), color).save(output, format='PNG')
        return output.getvalue()

    owner, bid = live
    before, after = png('blue'), png('red')
    with app.app_context():
        business = db.session.get(BusinessProfile, bid)
        business.logo_data = 'data:image/png;base64,' + base64.b64encode(before).decode()
        business.logo_url = f'/api/businesses/{bid}/logo'
        db.session.commit()
    uploaded = client.post(f'/api/businesses/{bid}/logo', json={
        'data': 'data:image/png;base64,' + base64.b64encode(after).decode(),
    }, headers=versioned(owner, current(client, owner, bid)))
    assert uploaded.status_code == 200, uploaded.get_json()
    assert uploaded.get_json()['business']['is_public']
    assert client.get(f'/api/businesses/{bid}/logo').data == before
    assert client.get(f'/api/businesses/{bid}/logo', headers=auth(owner['token'])).data == before
    draft = client.get(f'/api/businesses/{bid}/logo?draft=1', headers=auth(owner['token']))
    assert draft.data == after
    assert draft.headers['Cache-Control'] == 'private, no-store'
    assert client.get(f'/api/businesses/{bid}/logo?draft=1').data == before
