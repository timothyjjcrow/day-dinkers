"""Focused end-to-end coverage for business governance and account security."""
import base64
import hashlib
import re
import time
from types import SimpleNamespace

import pytest

from backend.app import create_app, db
from backend.models import (
    BusinessClaim,
    BusinessProfile,
    BusinessProfileRevision,
    BusinessOrganizationMember,
    BusinessVerificationEvidence,
    Court,
    User,
    utcnow,
)
from backend.services.mfa import _totp_at
from backend.services import mfa as mfa_service
from backend.services.business_governance import ensure_organization
from backend.services.businesses import _reset_profile_for_ownership_transfer
from backend.integrations.models import (
    BusinessCredentialSecret,
    BusinessLinkHealthCheck,
    BusinessProviderConnection,
)
from scripts.manage_business_operators import _require_mfa_before_grant


PASSWORD = 'secret123'


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add_all([
            Court(
                name='Official Pickle Club', address='1 Main St', city='Austin',
                state='TX', county_slug='travis', latitude=30.1, longitude=-97.1,
                website='https://official.example/locations/austin',
            ),
            Court(
                name='Official Pickle Club North', address='2 Main St', city='Austin',
                state='TX', county_slug='travis', latitude=30.2, longitude=-97.2,
                website='https://official.example/locations/north',
            ),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name='Business user'):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': PASSWORD,
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def enable_mfa(client, token):
    setup = client.post('/api/auth/mfa/setup', json={
        'current_password': PASSWORD,
    }, headers=auth(token))
    assert setup.status_code == 200, setup.get_json()
    secret = setup.get_json()['secret']
    code = _totp_at(secret, time.time())
    enabled = client.post('/api/auth/mfa/enable', json={
        'code': code,
    }, headers=auth(token))
    assert enabled.status_code == 200, enabled.get_json()
    return secret, enabled.get_json()['token'], enabled.get_json()['recovery_codes']


def create_claim(client, token, court_id):
    response = client.post('/api/businesses/claims', json={
        'court_id': court_id,
        'role': 'Owner',
        'authorized_attestation': True,
    }, headers=auth(token))
    assert response.status_code in {200, 201}, response.get_json()
    return response.get_json()


def create_profile(client, token, court_id, *, name):
    response = client.post('/api/businesses', json={
        'court_id': court_id,
        'name': name,
        'role': 'Owner',
        'authorized_attestation': True,
    }, headers=auth(token))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def make_operator(app, user_id, role):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.operator_role = role
        db.session.commit()


def challenge_code(app):
    message = app.extensions['email_outbox'][-1]
    match = re.search(r'\b(\d{6})\b', message['text'])
    assert match
    return match.group(1)


def test_initial_claim_fields_persist_as_private_operator_evidence(app, client):
    claimant = register(client, 'manager@official.example')
    payload = {
        'court_id': 1,
        'role': 'Manager',
        'authorized_attestation': True,
        'verification_contact_email': 'Manager@Official.Example',
        'evidence_url': 'https://official.example/team/manager',
        'evidence_notes': 'I manage programming; the front desk can confirm my role.',
    }
    created = client.post(
        '/api/businesses/claims', json=payload,
        headers=auth(claimant['token']),
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()['claim']['evidence_count'] == 3
    business_id = created.get_json()['business']['id']

    # A harmless retry does not multiply the same pending evidence.
    retried = client.post(
        '/api/businesses/claims', json=payload,
        headers=auth(claimant['token']),
    )
    assert retried.status_code == 200, retried.get_json()
    assert retried.get_json()['claim']['evidence_count'] == 3

    operator = register(client, 'reviewer@example.com')
    make_operator(app, operator['user']['id'], 'reviewer')
    verification = client.get(
        f'/api/businesses/{business_id}/verification',
        headers=auth(operator['token']),
    )
    assert verification.status_code == 200, verification.get_json()
    evidence = verification.get_json()['claims'][0]['evidence']
    by_type = {item['type']: item for item in evidence}
    assert by_type['business_email']['value'] == 'manager@official.example'
    assert by_type['website_domain']['value'] == 'https://official.example/team/manager'
    assert by_type['other']['note'] == payload['evidence_notes']
    assert all(item['status'] == 'submitted' for item in evidence)

    invalid = client.post('/api/businesses/claims', json={
        **payload, 'court_id': 2, 'verification_contact_email': 'not-an-email',
    }, headers=auth(claimant['token']))
    assert invalid.status_code == 400
    assert invalid.get_json()['error'] == 'invalid_verification_contact_email'


def test_totp_recovery_and_revoke_other_sessions(client):
    account = register(client, 'mfa@example.com')
    original = account['token']
    secret, enabled_token, recovery_codes = enable_mfa(client, original)

    assert client.get('/api/me', headers=auth(original)).status_code == 401
    required = client.post('/api/auth/login', json={
        'email': 'mfa@example.com', 'password': PASSWORD,
    })
    assert required.status_code == 401
    assert required.get_json()['error'] == 'mfa_required'

    login = client.post('/api/auth/login', json={
        'email': 'mfa@example.com', 'password': PASSWORD,
        'mfa_code': _totp_at(secret, time.time()),
    })
    assert login.status_code == 200
    login_token = login.get_json()['token']
    revoked = client.post('/api/auth/sessions/revoke-others', json={
        'current_password': PASSWORD,
        'mfa_code': _totp_at(secret, time.time()),
    }, headers=auth(login_token))
    assert revoked.status_code == 200
    assert client.get('/api/me', headers=auth(login_token)).status_code == 401
    assert client.get('/api/me', headers=auth(revoked.get_json()['token'])).status_code == 200

    recovery_login = client.post('/api/auth/login', json={
        'email': 'mfa@example.com', 'password': PASSWORD,
        'mfa_code': recovery_codes[0],
    })
    assert recovery_login.status_code == 200
    reused = client.post('/api/auth/login', json={
        'email': 'mfa@example.com', 'password': PASSWORD,
        'mfa_code': recovery_codes[0],
    })
    assert reused.status_code == 401


def test_wrong_totp_does_not_scan_expensive_recovery_hashes(monkeypatch):
    user = SimpleNamespace(
        mfa_enabled=True,
        mfa_secret_encrypted='encrypted',
        mfa_recovery_codes='["expensive-hash"]',
    )
    monkeypatch.setattr(mfa_service, 'decrypt_secret', lambda value: 'secret')
    monkeypatch.setattr(mfa_service, 'verify_totp', lambda *args, **kwargs: False)

    def unexpected_recovery_scan(*args, **kwargs):
        raise AssertionError('six-digit TOTP must not scan recovery hashes')

    monkeypatch.setattr(
        mfa_service, 'consume_recovery_code', unexpected_recovery_scan,
    )
    assert mfa_service.verify_user_mfa(user, '000000') == (False, False)


def test_operator_grant_requires_mfa():
    with pytest.raises(
        RuntimeError, match='operator_mfa_must_be_enabled_before_grant',
    ):
        _require_mfa_before_grant(SimpleNamespace(mfa_enabled=False), 'grant')
    _require_mfa_before_grant(SimpleNamespace(mfa_enabled=True), 'grant')
    _require_mfa_before_grant(SimpleNamespace(mfa_enabled=False), 'revoke')


def test_email_code_is_keyed_domain_scoped_and_operator_reviewed(app, client):
    owner = register(client, 'owner@example.com')
    court_id = 1
    created = create_claim(client, owner['token'], court_id)
    business_id = created['business']['id']
    claim_id = created['claim']['id']

    submitted = client.post(
        f'/api/businesses/{business_id}/verification/evidence',
        json={'type': 'business_email', 'value': 'ops@official.example'},
        headers=auth(owner['token']),
    )
    assert submitted.status_code == 201, submitted.get_json()
    evidence_id = submitted.get_json()['evidence']['id']
    code = challenge_code(app)
    with app.app_context():
        evidence = db.session.get(BusinessVerificationEvidence, evidence_id)
        assert evidence.challenge_token_hash != hashlib.sha256(code.encode()).hexdigest()
        assert evidence.domain_match is True

    confirmed = client.post(
        f'/api/businesses/{business_id}/verification/evidence/{evidence_id}/verify',
        json={'token': code}, headers=auth(owner['token']),
    )
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()['evidence']['status'] == 'verified'
    assert confirmed.get_json()['domain_match'] is True

    reviewer = register(client, 'reviewer@example.com')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    secret, reviewer_token, _ = enable_mfa(client, reviewer['token'])
    approved = client.post(
        f'/api/operator/business/claims/{claim_id}/review',
        json={
            'decision': 'approve',
            'verification_method': 'business_email',
            'review_note': 'Official domain mailbox challenge passed.',
            'claimant_feedback': 'Your venue control was verified.',
            'mfa_code': _totp_at(secret, time.time()),
        },
        headers=auth(reviewer_token),
    )
    assert approved.status_code == 200, approved.get_json()
    with app.app_context():
        assert db.session.get(BusinessClaim, claim_id).status == 'verified'


def test_operator_evidence_decision_is_immutable(app, client):
    owner = register(client, 'evidence-owner@example.com')
    created = create_claim(client, owner['token'], 1)
    submitted = client.post(
        f"/api/businesses/{created['business']['id']}/verification/evidence",
        json={
            'type': 'other',
            'value': 'Front desk confirmation reference',
            'note': 'Call the published venue number and ask for the manager.',
        },
        headers=auth(owner['token']),
    )
    assert submitted.status_code == 201, submitted.get_json()
    evidence_id = submitted.get_json()['evidence']['id']
    reviewer = register(client, 'evidence-reviewer@example.com')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    secret, reviewer_token, _ = enable_mfa(client, reviewer['token'])

    first = client.post(
        f'/api/operator/business/evidence/{evidence_id}/review',
        json={
            'decision': 'accept',
            'review_note': 'Confirmed using the independently published number.',
            'mfa_code': _totp_at(secret, time.time()),
        },
        headers=auth(reviewer_token),
    )
    assert first.status_code == 200, first.get_json()
    second = client.post(
        f'/api/operator/business/evidence/{evidence_id}/review',
        json={
            'decision': 'reject',
            'review_note': 'Attempted overwrite of the completed decision.',
            'mfa_code': _totp_at(secret, time.time()),
        },
        headers=auth(reviewer_token),
    )
    assert second.status_code == 409
    assert second.get_json()['error'] == 'evidence_already_reviewed'
    with app.app_context():
        evidence = db.session.get(BusinessVerificationEvidence, evidence_id)
        assert evidence.status == 'accepted'
        assert evidence.review_note == (
            'Confirmed using the independently published number.'
        )


def test_consumer_email_needs_manual_review_and_challenge_locks(app, client):
    owner = register(client, 'consumer-owner@example.com')
    created = create_claim(client, owner['token'], 1)
    business_id = created['business']['id']
    submitted = client.post(
        f'/api/businesses/{business_id}/verification/evidence',
        json={'type': 'business_email', 'value': 'clubowner@gmail.com'},
        headers=auth(owner['token']),
    )
    evidence_id = submitted.get_json()['evidence']['id']
    for _ in range(5):
        response = client.post(
            f'/api/businesses/{business_id}/verification/evidence/{evidence_id}/verify',
            json={'token': '999999'}, headers=auth(owner['token']),
        )
        assert response.status_code == 400
    locked = client.post(
        f'/api/businesses/{business_id}/verification/evidence/{evidence_id}/verify',
        json={'token': '999999'}, headers=auth(owner['token']),
    )
    assert locked.status_code == 423
    resent = client.post(
        f'/api/businesses/{business_id}/verification/evidence/{evidence_id}/resend',
        json={}, headers=auth(owner['token']),
    )
    assert resent.status_code == 200
    code = challenge_code(app)
    confirmed = client.post(
        f'/api/businesses/{business_id}/verification/evidence/{evidence_id}/verify',
        json={'token': code}, headers=auth(owner['token']),
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()['evidence']['status'] == 'submitted'
    assert confirmed.get_json()['requires_manual_review'] is True


def test_multi_location_team_invitation_and_role_scope(app, client):
    owner = register(client, 'owner@example.com')
    staff = register(client, 'staff@example.com')
    editor = register(client, 'editor@example.com')
    viewer = register(client, 'viewer@example.com')
    first = create_profile(
        client, owner['token'], 1, name='Official Pickle Club',
    )
    second = create_profile(
        client, owner['token'], 2, name='Official Pickle Club North',
    )
    assert client.get(
        f"/api/businesses/{first['id']}/team", headers=auth(owner['token']),
    ).status_code == 200
    attached = client.post(
        f"/api/businesses/{first['id']}/organization/locations",
        json={'business_id': second['id']}, headers=auth(owner['token']),
    )
    assert attached.status_code == 201, attached.get_json()
    invited = client.post(
        f"/api/businesses/{first['id']}/team/invitations",
        json={'email': 'staff@example.com', 'role': 'admin'},
        headers=auth(owner['token']),
    )
    assert invited.status_code == 201, invited.get_json()
    text = app.extensions['email_outbox'][-1]['text']
    token = re.search(r'#business-invitation=([^\s]+)', text).group(1)
    accepted = client.post(
        f'/api/business-invitations/{token}/accept',
        headers=auth(staff['token']),
    )
    assert accepted.status_code == 200, accepted.get_json()
    organization_id = accepted.get_json()['organization']['id']
    with app.app_context():
        db.session.add_all([
            BusinessOrganizationMember(
                organization_id=organization_id,
                user_id=editor['user']['id'], role='editor',
            ),
            BusinessOrganizationMember(
                organization_id=organization_id,
                user_id=viewer['user']['id'], role='viewer',
            ),
        ])
        for business_id in (first['id'], second['id']):
            business = db.session.get(BusinessProfile, business_id)
            business.claim_status = 'verified'
            business.verified_at = utcnow()
        db.session.commit()

    mine = client.get('/api/businesses/mine', headers=auth(staff['token']))
    assert {item['id'] for item in mine.get_json()['items']} == {first['id'], second['id']}
    changed = client.patch(
        f"/api/businesses/{second['id']}",
        json={'announcement': 'North courts open early.'},
        headers=auth(staff['token']),
    )
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()['manager_role'] == 'admin'
    assert changed.get_json()['is_owner'] is False
    editor_change = client.patch(
        f"/api/businesses/{second['id']}",
        json={'description': 'Edited by the location team.'},
        headers=auth(editor['token']),
    )
    assert editor_change.status_code == 200
    assert editor_change.get_json()['is_owner'] is False
    assert editor_change.get_json()['manager_role'] == 'editor'
    assert client.patch(
        f"/api/businesses/{second['id']}",
        json={'published': False}, headers=auth(editor['token']),
    ).status_code == 403
    assert client.patch(
        f"/api/businesses/{second['id']}",
        json={'announcement': 'Viewer should not write.'},
        headers=auth(viewer['token']),
    ).status_code == 403
    request_by_admin = client.post(
        f"/api/businesses/{second['id']}/integration-requests",
        json={'provider': 'Venue system'}, headers=auth(staff['token']),
    )
    assert request_by_admin.status_code == 201, request_by_admin.get_json()
    assert client.post(
        f"/api/businesses/{second['id']}/integration-requests",
        json={'provider': 'Editor system'}, headers=auth(editor['token']),
    ).status_code == 403
    assert client.get(
        f"/api/businesses/{second['id']}/integration-requests",
        headers=auth(editor['token']),
    ).status_code == 403
    assert client.get(
        f"/api/businesses/{second['id']}/integration-requests",
        headers=auth(viewer['token']),
    ).status_code == 403
    for limited_user in (editor, viewer):
        limited_mine = client.get(
            '/api/businesses/mine', headers=auth(limited_user['token']),
        )
        assert limited_mine.status_code == 200
        limited_profile = next(
            item for item in limited_mine.get_json()['items']
            if item['id'] == second['id']
        )
        assert 'integration_requests' not in limited_profile
        assert request_by_admin.get_json()['request']['contact_email'] \
            not in str(limited_profile)
        assert str(staff['user']['id']) not in str(
            limited_profile.get('integration_requests', ''),
        )
    admin_mine = client.get(
        '/api/businesses/mine', headers=auth(staff['token']),
    ).get_json()['items']
    admin_profile = next(item for item in admin_mine if item['id'] == second['id'])
    assert admin_profile['integration_requests'][0]['requested_by_id'] == staff['user']['id']
    owner_queue = client.get(
        f"/api/businesses/{second['id']}/integration-requests",
        headers=auth(owner['token']),
    )
    assert owner_queue.status_code == 200
    assert owner_queue.get_json()['items'][0]['requested_by_id'] == staff['user']['id']
    member_id = accepted.get_json()['member']['id']
    transferred = client.post(
        f"/api/businesses/{first['id']}/transfer",
        json={'member_id': member_id, 'current_password': PASSWORD},
        headers=auth(owner['token']),
    )
    assert transferred.status_code == 200, transferred.get_json()
    assert set(transferred.get_json()['business_ids']) == {first['id'], second['id']}
    assert transferred.get_json()['scope'] == 'organization_all_locations'
    with app.app_context():
        profiles = BusinessProfile.query.filter(
            BusinessProfile.id.in_([first['id'], second['id']]),
        ).all()
        assert {item.owner_id for item in profiles} == {staff['user']['id']}
        organization_id = profiles[0].organization_id
        owners = BusinessOrganizationMember.query.filter_by(
            organization_id=organization_id, role='owner',
        ).all()
        assert [item.user_id for item in owners] == [staff['user']['id']]
    # The promoted owner has no historical claim row, but owns the org and can
    # still release one location with step-up authentication.
    released = client.delete(
        f"/api/businesses/{first['id']}/claim",
        json={'current_password': PASSWORD},
        headers=auth(staff['token']),
    )
    assert released.status_code == 200, released.get_json()
    with app.app_context():
        assert db.session.get(BusinessProfile, first['id']).governance_status == 'relinquished'
        assert db.session.get(BusinessProfile, second['id']).owner_id == staff['user']['id']


def test_operator_forced_suspend_needs_fresh_mfa_and_second_admin(app, client):
    owner = register(client, 'owner@example.com')
    business = create_claim(client, owner['token'], 1)['business']
    with app.app_context():
        profile = db.session.get(BusinessProfile, business['id'])
        profile.claim_status = 'verified'
        profile.verified_at = utcnow()
        profile.published = True
        db.session.commit()

    first = register(client, 'first-admin@example.com')
    second = register(client, 'second-admin@example.com')
    make_operator(app, first['user']['id'], 'admin')
    make_operator(app, second['user']['id'], 'admin')
    first_secret, first_token, _ = enable_mfa(client, first['token'])
    second_secret, second_token, _ = enable_mfa(client, second['token'])

    no_step_up = client.post(
        f"/api/operator/businesses/{business['id']}/suspend",
        json={'reason': 'Credible ownership dispute.'}, headers=auth(first_token),
    )
    assert no_step_up.status_code == 403
    proposed = client.post(
        f"/api/operator/businesses/{business['id']}/suspend",
        json={
            'reason': 'Credible ownership dispute.',
            'mfa_code': _totp_at(first_secret, time.time()),
        },
        headers=auth(first_token),
    )
    assert proposed.status_code == 202, proposed.get_json()
    action_id = proposed.get_json()['action']['id']
    self_confirm = client.post(
        f'/api/operator/business/actions/{action_id}/confirm',
        json={'mfa_code': _totp_at(first_secret, time.time())},
        headers=auth(first_token),
    )
    assert self_confirm.status_code == 409
    confirmed = client.post(
        f'/api/operator/business/actions/{action_id}/confirm',
        json={'mfa_code': _totp_at(second_secret, time.time())},
        headers=auth(second_token),
    )
    assert confirmed.status_code == 200, confirmed.get_json()
    with app.app_context():
        profile = db.session.get(BusinessProfile, business['id'])
        assert profile.governance_status == 'suspended'
        assert profile.published is False


def test_editor_revision_restore_response_preserves_manager_role(app, client):
    owner = register(client, 'owner@example.com')
    editor = register(client, 'editor@example.com')
    profile = create_profile(
        client, owner['token'], 1, name='Official Pickle Club',
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        organization = ensure_organization(business, owner['user']['id'])
        db.session.add(BusinessOrganizationMember(
            organization_id=organization.id,
            user_id=editor['user']['id'],
            role='editor',
        ))
        db.session.commit()

    first = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'description': 'First editor version.'},
        headers=auth(editor['token']),
    )
    assert first.status_code == 200, first.get_json()
    assert first.get_json()['manager_role'] == 'editor'
    history = client.get(
        f"/api/businesses/{profile['id']}/revisions",
        headers=auth(editor['token']),
    )
    assert history.status_code == 200, history.get_json()
    first_revision_id = history.get_json()['items'][0]['id']
    assert client.patch(
        f"/api/businesses/{profile['id']}",
        json={'description': 'Second editor version.'},
        headers=auth(editor['token']),
    ).status_code == 200

    restored = client.post(
        f"/api/businesses/{profile['id']}/revisions/{first_revision_id}/restore",
        headers=auth(editor['token']),
    )
    assert restored.status_code == 200, restored.get_json()
    restored_business = restored.get_json()['business']
    assert restored_business['manager_role'] == 'editor'
    assert restored_business['is_manager'] is True
    assert restored_business['is_owner'] is False


def test_rich_manual_schedule_and_logo_revision_are_server_governed(app, client):
    owner = register(client, 'owner@example.com')
    profile = create_profile(
        client, owner['token'], 1, name='Official Pickle Club',
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        business.claim_status = 'verified'
        business.verified_at = utcnow()
        business.content_review_status = 'approved'
        db.session.commit()
    schedule = client.put(
        f"/api/businesses/{profile['id']}/schedule",
        json={'items': [{
            'title': 'Tuesday clinic', 'kind': 'clinic',
            'recurrence': 'date_range', 'day_of_week': 'tuesday',
            'start_date': '2026-09-01', 'end_date': '2026-10-01',
            'start_time': '18:00', 'end_time': '20:00',
            'timezone': 'America/Chicago', 'capacity': 16,
            'spots_remaining': 4, 'status': 'scheduled',
            'location_note': 'Courts 1-4', 'instructor': 'Pat Lee',
        }]}, headers=auth(owner['token']),
    )
    assert schedule.status_code == 200, schedule.get_json()
    item = schedule.get_json()['schedule'][0]
    assert item['timezone'] == 'America/Chicago'
    assert item['start_date'] == '2026-09-01'
    assert item['spots_remaining'] == 4
    assert item['freshness']['live'] is False

    # Valid 1x1 PNG; the API verifies both declared MIME and file magic.
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )
    upload = client.post(
        f"/api/businesses/{profile['id']}/logo",
        json={
            'mime_type': 'image/png',
            'data_base64': base64.b64encode(png).decode(),
        }, headers=auth(owner['token']),
    )
    assert upload.status_code == 200, upload.get_json()
    assert 'data_base64' not in str(upload.get_json())
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        assert business.content_review_status == 'pending'
        assert business.published is False
        revision = BusinessProfileRevision.query.filter_by(
            business_id=business.id, review_status='pending',
        ).order_by(BusinessProfileRevision.id.desc()).first()
        assert revision is not None
        assert revision.previous_snapshot


def test_single_location_reset_tombstones_vault_secrets_and_empty_team(app, client):
    owner = register(client, 'owner@example.com')
    profile = create_profile(
        client, owner['token'], 1, name='Official Pickle Club',
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        organization = ensure_organization(business, owner['user']['id'])
        db.session.flush()
        organization_id = organization.id
        secret = BusinessCredentialSecret(
            purpose='credential',
            ciphertext='encrypted-but-still-sensitive',
            key_version=1,
            created_by_id=owner['user']['id'],
        )
        db.session.add(secret)
        db.session.flush()
        secret_id = secret.id
        connection = BusinessProviderConnection(
            business_id=business.id,
            created_by_id=owner['user']['id'],
            provider_key='generic_json_feed',
            display_name='Old owner feed',
            credential_ref=secret.reference,
        )
        db.session.add(connection)
        db.session.add(BusinessLinkHealthCheck(
            business_id=business.id,
            connection_id=None,
            link_kind='booking',
            url_hash=hashlib.sha256(b'https://former-owner.example').hexdigest(),
            status='broken',
        ))
        db.session.commit()

        _reset_profile_for_ownership_transfer(business)
        db.session.commit()
        tombstone = db.session.get(BusinessCredentialSecret, secret_id)
        assert tombstone.ciphertext == ''
        assert tombstone.deleted_at is not None
        assert BusinessProviderConnection.query.filter_by(
            business_id=business.id,
        ).count() == 0
        assert BusinessLinkHealthCheck.query.filter_by(
            business_id=business.id,
        ).count() == 0
        assert business.organization_id is None
        from backend.models import BusinessOrganization
        assert db.session.get(BusinessOrganization, organization_id) is None


def test_sensitive_revision_chain_cannot_be_laundered_or_reviewed_out_of_order(
    app, client,
):
    owner = register(client, 'owner@example.com')
    profile = create_profile(
        client, owner['token'], 1, name='Official Pickle Club',
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        business.claim_status = 'verified'
        business.verified_at = utcnow()
        business.content_review_status = 'approved'
        db.session.commit()

    first_sensitive = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'booking_url': 'https://official.example/book'},
        headers=auth(owner['token']),
    )
    assert first_sensitive.status_code == 200
    harmless = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'description': 'Updated program details.'},
        headers=auth(owner['token']),
    )
    assert harmless.status_code == 200
    assert harmless.get_json()['content_review_status'] == 'pending'
    blocked_publish = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'published': True}, headers=auth(owner['token']),
    )
    assert blocked_publish.status_code == 400
    second_sensitive = client.patch(
        f"/api/businesses/{profile['id']}",
        json={'website_url': 'https://official.example/club'},
        headers=auth(owner['token']),
    )
    assert second_sensitive.status_code == 200

    reviewer = register(client, 'reviewer@example.com')
    make_operator(app, reviewer['user']['id'], 'reviewer')
    secret, reviewer_token, _ = enable_mfa(client, reviewer['token'])
    with app.app_context():
        pending = BusinessProfileRevision.query.filter_by(
            business_id=profile['id'], review_status='pending',
        ).order_by(BusinessProfileRevision.id).all()
        assert len(pending) == 2
        older_id, latest_id = pending[0].id, pending[1].id
    queued = client.get(
        '/api/operator/business/queue', headers=auth(reviewer_token),
    )
    assert queued.status_code == 200, queued.get_json()
    queued_revisions = queued.get_json()['revisions']
    assert [item['id'] for item in queued_revisions] == [latest_id]
    assert queued_revisions[0]['business_name'] == 'Official Pickle Club'
    assert queued_revisions[0]['before_snapshot']['profile']['website_url'] == ''
    assert queued_revisions[0]['after_snapshot']['profile']['website_url'] == (
        'https://official.example/club'
    )
    assert 'logo_data' not in queued_revisions[0]['before_snapshot']['profile']
    assert 'logo_data' not in queued_revisions[0]['after_snapshot']['profile']
    out_of_order = client.post(
        f'/api/operator/business/revisions/{older_id}/review',
        json={
            'decision': 'approve', 'review_note': 'Looks safe.',
            'mfa_code': _totp_at(secret, time.time()),
        }, headers=auth(reviewer_token),
    )
    assert out_of_order.status_code == 409
    assert out_of_order.get_json()['latest_revision_id'] == latest_id
    approved = client.post(
        f'/api/operator/business/revisions/{latest_id}/review',
        json={
            'decision': 'approve', 'review_note': 'Domains are official.',
            'mfa_code': _totp_at(secret, time.time()),
        }, headers=auth(reviewer_token),
    )
    assert approved.status_code == 200, approved.get_json()
    assert approved.get_json()['business']['content_review_status'] == 'approved'

    # A later rejection restores only its immediate predecessor; the older
    # pending sensitive change remains held for its own decision.
    assert client.patch(
        f"/api/businesses/{profile['id']}",
        json={'booking_url': 'https://official.example/new-book'},
        headers=auth(owner['token']),
    ).status_code == 200
    assert client.patch(
        f"/api/businesses/{profile['id']}",
        json={'membership_url': 'https://official.example/join'},
        headers=auth(owner['token']),
    ).status_code == 200
    with app.app_context():
        pending = BusinessProfileRevision.query.filter_by(
            business_id=profile['id'], review_status='pending',
        ).order_by(BusinessProfileRevision.id).all()
        earlier_id, newest_id = pending[0].id, pending[1].id
    rejected = client.post(
        f'/api/operator/business/revisions/{newest_id}/review',
        json={
            'decision': 'reject', 'review_note': 'Membership URL is not official.',
            'mfa_code': _totp_at(secret, time.time()),
        }, headers=auth(reviewer_token),
    )
    assert rejected.status_code == 200, rejected.get_json()
    assert rejected.get_json()['business']['content_review_status'] == 'pending'
    with app.app_context():
        business = db.session.get(BusinessProfile, profile['id'])
        assert business.booking_url == 'https://official.example/new-book'
        assert business.membership_url == ''
        assert db.session.get(
            BusinessProfileRevision, earlier_id,
        ).review_status == 'pending'
