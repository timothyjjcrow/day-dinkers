"""Private location submission is atomic and cannot leak into player activity."""
import time

from test_business_governance import app, client, register, auth, make_operator, enable_mfa
from test_business_reviewed_versions import current, versioned
from backend.app import db
from backend.models import Court, BusinessClaim, BusinessProfile, BusinessVerificationEvidence
from backend.services.mfa import _totp_at


def payload(**location):
    return {'location': {'name':'New Venue West', 'address':'100 New Lane', 'city':'Austin', 'state':'TX', 'latitude':30.4, 'longitude':-97.5, 'num_courts':4, 'indoor':True, **location}, 'role':'owner', 'authorized_attestation':True, 'verification_contact_email':'venue@example.test', 'evidence_notes':'Venue manager available on the official business phone.'}


def submit(client, owner, body=None):
    return client.post('/api/businesses/claims/new-location', json=body or payload(), headers=auth(owner['token']))


def test_missing_venue_is_private_and_cannot_be_used_for_player_actions(app, client):
    owner = register(client, 'missing-owner@example.test')
    stranger = register(client, 'missing-stranger@example.test')
    response = submit(client, owner)
    assert response.status_code == 201, response.get_json()
    data = response.get_json(); court_id = data['business']['court_id']; bid = data['business']['id']
    assert data['business']['location_pending_review'] is True
    assert data['business']['proposed_location']['address'] == '100 New Lane'
    assert client.get(f'/api/courts/{court_id}').status_code == 404
    assert client.get(f'/api/courts/{court_id}', headers=auth(stranger['token'])).status_code == 404
    assert client.get(f'/api/courts/{court_id}', headers=auth(owner['token'])).status_code == 200
    assert client.get('/api/courts?q=New%20Venue').get_json()['items'] == []
    for path, body in [(f'/api/courts/{court_id}/favorite', {}), ('/api/games', {'court_id':court_id}), ('/api/businesses/claims', {'court_id':court_id, **{k:v for k,v in payload().items() if k!='location'}})]:
        assert client.post(path, json=body, headers=auth(stranger['token'])).status_code == 404
    assert client.post('/api/games', json={'court_id':court_id}, headers=auth(owner['token'])).status_code == 404
    assert client.get(f'/api/businesses/{bid}').status_code == 404
    reviewer = register(client, 'location-reviewer@example.test')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    assert client.get(f'/api/courts/{court_id}', headers=auth(reviewer['token'])).status_code == 200


def test_duplicate_and_failed_claim_leave_no_orphan_location(app, client, monkeypatch):
    owner = register(client, 'missing-atomic@example.test')
    duplicate = submit(client, owner, payload(name='Official Pickle Club', address='1 Main St', latitude=30.1, longitude=-97.1))
    assert duplicate.status_code == 409
    assert duplicate.get_json()['items'][0]['id'] == 1
    import backend.routes.businesses as routes
    monkeypatch.setattr(routes, '_submit_business_claim', lambda data: ({'error':'claim_failed'}, 409))
    assert submit(client, owner).status_code == 409
    with app.app_context():
        assert Court.query.count() == 2
        assert BusinessClaim.query.count() == BusinessProfile.query.count() == 0


def test_pending_duplicate_does_not_expose_another_private_location(client):
    owner = register(client, 'missing-first@example.test')
    other = register(client, 'missing-second@example.test')
    assert submit(client, owner).status_code == 201
    duplicate = submit(client, other)
    assert duplicate.status_code == 409 and duplicate.get_json()['items'] == []


def test_pending_address_change_uses_conditional_version_and_owner_scope(client):
    owner = register(client, 'location-edit@example.test')
    other = register(client, 'location-other@example.test')
    response = submit(client, owner).get_json(); bid = response['business']['id']; original = current(client, owner, bid)
    target = f'/api/businesses/{bid}/location'
    proposed = {**payload()['location'], 'address':'101 New Lane'}
    assert client.patch(target, json=proposed, headers=auth(owner['token'])).status_code == 428
    assert client.patch(target, json=proposed, headers=versioned(other, original)).status_code == 403
    saved = client.patch(target, json=proposed, headers=versioned(owner, original))
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['content_version'] != original['content_version']
    assert client.patch(target, json=payload()['location'], headers=versioned(owner, original)).status_code == 412
    assert current(client, owner, bid)['proposed_location']['address'] == '101 New Lane'


def test_review_requires_location_check_and_publishes_only_court(app, client):
    owner = register(client, 'location-publish@example.test')
    data = submit(client, owner).get_json(); claim_id = data['claim']['id']; bid = data['business']['id']; court_id = data['business']['court_id']
    reviewer = register(client, 'location-operator@example.test')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    secret, token, _ = enable_mfa(client, reviewer['token'])
    evidence = client.post(f'/api/businesses/{bid}/verification/evidence', json={'type':'in_person', 'value':'Inspected venue location', 'note':'Owner present'}, headers=auth(owner['token']))
    assert evidence.status_code == 201, evidence.get_json()
    evidence_id = evidence.get_json()['evidence']['id']
    accepted = client.post(f'/api/operator/business/evidence/{evidence_id}/review', json={'decision':'accept', 'review_note':'Inspected venue and confirmed control', 'mfa_code':_totp_at(secret, time.time())}, headers=auth(token))
    assert accepted.status_code == 200, accepted.get_json()
    body = {'decision':'approve', 'verification_method':'in_person', 'review_note':'Confirmed address and owner.', 'claimant_feedback':'Your venue location and management role are approved.', 'mfa_code':_totp_at(secret, time.time())}
    target = f'/api/operator/business/claims/{claim_id}/review'
    missing_check = client.post(target, json=body, headers=auth(token))
    assert missing_check.status_code == 400, missing_check.get_json()
    assert missing_check.get_json()['error'] == 'venue_location_review_required'
    approved = client.post(target, json={**body, 'location_reviewed':True}, headers=auth(token))
    assert approved.status_code == 200, approved.get_json()
    assert client.get(f'/api/courts/{court_id}').status_code == 200
    assert current(client, owner, bid)['published'] is False
    assert client.get(f'/api/businesses/{bid}').status_code == 404


def test_ordinary_community_court_still_publishes_immediately(client):
    user = register(client, 'community-normal@example.test')
    response = client.post('/api/courts', json=payload()['location'], headers=auth(user['token']))
    assert response.status_code == 201
    assert response.get_json()['pending_submission'] is False
    assert client.get(f'/api/courts/{response.get_json()["id"]}').status_code == 200
