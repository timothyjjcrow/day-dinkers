"""Executable contracts for the honest provider integration foundation."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json

import pytest
from sqlalchemy import inspect, text

from backend.app import create_app, db
from backend.models import (
    BusinessOrganizationMember,
    BusinessProfile,
    Court,
    User,
    utcnow,
)
from backend.routes.business_integrations import (
    _cron_queue_order,
    business_integration_cron_bp,
    business_integrations_bp,
)
from backend.integrations import provider_registry
from backend.integrations.errors import (
    CredentialVaultUnavailable,
    ProviderNotAvailable,
    ValidationError,
    WebhookVerificationError,
)
from backend.integrations.link_health import probe_https_url
from backend.integrations.models import (
    BusinessBookingEvent,
    BusinessCredentialSecret,
    BusinessIntegrationAuditEvent,
    BusinessIntegrationSyncRun,
    BusinessLinkHealthCheck,
    BusinessProviderConnection,
    BusinessScheduleOccurrence,
    BusinessWebhookReceipt,
)
from backend.integrations.oauth import OAuthStateManager
from backend.integrations.pull import pull_json_catalog
from backend.integrations.safety import (
    safe_external_url,
    sanitize_public_config,
)
from backend.integrations.services import (
    due_pull_connections,
    due_sync_runs,
    process_sync_run,
    pull_connection_catalog,
    recheck_business_profile_links,
    recheck_connection_links,
    stale_business_profiles,
    stale_connections,
)
from backend.integrations.vault import configured_vault
from backend.integrations.webhooks import sign_payload, verify_signature
from backend.services.business_governance import ensure_organization
from scripts.migrate_business_integration_foundation import (
    REQUIRED_CHECKS,
    REQUIRED_COLUMNS,
    REQUIRED_FOREIGN_KEYS,
    REQUIRED_INDEXES,
    REQUIRED_PRIMARY_KEYS,
    REQUIRED_UNIQUES,
    _add_missing_columns,
    _foundation_model_tables,
    _repair_constraints,
    _repair_indexes,
    _repair_primary_keys,
    schema_gaps,
)


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv('CRON_SECRET', 'test-cron-secret')
    monkeypatch.setenv(
        'BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK', 'webhook-secret-value',
    )
    monkeypatch.setenv('BUSINESS_CREDENTIAL_VAULT', 'hybrid')
    monkeypatch.setenv(
        'BUSINESS_CREDENTIAL_ENCRYPTION_KEY',
        'oK7kHsDLUBHIwsysmvfGW-c-Z26V0FTHh6c4pSe3Z8M=',
    )
    app = create_app('testing')
    app.config.update({
        'BUSINESS_CREDENTIAL_VAULT': 'hybrid',
        'BUSINESS_CREDENTIAL_ENCRYPTION_KEY': (
            'oK7kHsDLUBHIwsysmvfGW-c-Z26V0FTHh6c4pSe3Z8M='
        ),
        'BUSINESS_CREDENTIAL_KEY_VERSION': 1,
    })
    if 'business_integrations' not in app.blueprints:
        app.register_blueprint(business_integrations_bp, url_prefix='/api')
    if 'business_integration_cron' not in app.blueprints:
        app.register_blueprint(business_integration_cron_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Foundation Pickleball Club',
            address='10 Integration Way', city='Austin', state='TX',
            county_slug='travis-county', latitude=30.3, longitude=-97.7,
            num_courts=8,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email, name='Business manager'):
    response = client.post('/api/auth/register', json={
        'email': email,
        'password': 'StrongPass123!',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def verified_business(app, owner_id):
    with app.app_context():
        court = Court.query.one()
        business = BusinessProfile(
            owner_id=owner_id,
            court_id=court.id,
            name='Foundation Pickleball Club',
            claimant_role='owner',
            claim_status='verified',
            verified_at=utcnow(),
            published=True,
            governance_status='active',
            content_review_status='approved',
            booking_url='https://book.example.com',
        )
        db.session.add(business)
        db.session.commit()
        return business.id


def catalog(*, source_version='v1', occurrences=None, conversions=None,
            authoritative=True):
    return {
        'schema_version': 1,
        'source_version': source_version,
        'generated_at': '2026-09-01T15:00:00Z',
        'authoritative': authoritative,
        'occurrences': occurrences if occurrences is not None else [{
            'external_id': 'open-play-2026-09-08',
            'title': 'Intermediate open play',
            'kind': 'open_play',
            'event_date': '2026-09-08',
            'start_time': '18:00',
            'end_time': '20:00',
            'timezone': 'America/Chicago',
            'capacity': 24,
            'spots_remaining': 7,
            'location_note': 'Courts 1–4',
            'instructor': 'Venue staff',
            'price_text': '$12',
            'booking_url': 'https://book.example.com/open-play',
            'updated_at': '2026-09-01T14:55:00Z',
        }],
        'conversions': conversions or [],
    }


def create_connection(client, account, business_id, *, source_url=''):
    response = client.post(
        f'/api/businesses/{business_id}/connections',
        json={
            'provider_key': 'link_catalog',
            'display_name': 'Club-owned schedule feed',
            'config': {
                'label': 'Live club catalog',
                'source_url': source_url,
                'booking_base_url': 'https://book.example.com',
            },
        },
        headers=auth(account),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()['connection']


def test_startup_upgrades_legacy_sqlite_connection_column_and_routes(
    tmp_path, monkeypatch,
):
    from backend.config import TestingConfig

    database_path = tmp_path / 'legacy-business-connections.db'
    monkeypatch.setattr(
        TestingConfig,
        'SQLALCHEMY_DATABASE_URI',
        f'sqlite:///{database_path}',
    )
    monkeypatch.setattr(TestingConfig, 'AUTO_CREATE_DB', True)

    legacy_app = create_app('testing')
    legacy_client = legacy_app.test_client()
    with legacy_app.app_context():
        db.session.add(Court(
            name='Legacy Pickleball Club',
            address='20 Compatibility Way', city='Austin', state='TX',
            county_slug='travis-county', latitude=30.31, longitude=-97.71,
            num_courts=6,
        ))
        db.session.commit()

    owner = register(legacy_client, 'legacy-connection-owner@example.com')
    operator = register(legacy_client, 'legacy-connection-operator@example.com')
    business_id = verified_business(legacy_app, owner['user']['id'])
    connection = create_connection(legacy_client, owner, business_id)

    with legacy_app.app_context():
        db.session.get(User, operator['user']['id']).operator_role = 'reviewer'
        persisted = db.session.get(
            BusinessProviderConnection, connection['id'],
        )
        persisted.status = 'degraded'
        db.session.commit()
        db.session.remove()
        with db.engine.begin() as database_connection:
            database_connection.execute(text(
                'ALTER TABLE business_provider_connection '
                'DROP COLUMN operator_reconnect_required'
            ))
        assert 'operator_reconnect_required' not in {
            item['name']
            for item in inspect(db.engine).get_columns(
                'business_provider_connection',
            )
        }
        db.engine.dispose()

    # Reboot against the existing file with broad create_all disabled. The
    # normal startup additive upgrader must make the legacy table queryable.
    monkeypatch.setattr(TestingConfig, 'AUTO_CREATE_DB', False)
    upgraded_app = create_app('testing')
    upgraded_client = upgraded_app.test_client()

    with upgraded_app.app_context():
        pragma_rows = {
            row.name: row
            for row in db.session.execute(text(
                'PRAGMA table_info(business_provider_connection)'
            )).all()
        }
        upgraded_column = pragma_rows['operator_reconnect_required']
        assert upgraded_column.type.upper() == 'BOOLEAN'
        assert upgraded_column.notnull == 1
        assert upgraded_column.dflt_value == '0'
        stored_default = db.session.execute(text(
            'SELECT operator_reconnect_required '
            'FROM business_provider_connection WHERE id = :connection_id'
        ), {'connection_id': connection['id']}).scalar_one()
        assert stored_default == 0

    owner_connections = upgraded_client.get(
        f'/api/businesses/{business_id}/connections', headers=auth(owner),
    )
    assert owner_connections.status_code == 200, owner_connections.get_json()
    assert owner_connections.get_json()['items'][0][
        'reconnect_requires_operator'
    ] is False

    operator_queue = upgraded_client.get(
        '/api/operator/business/queue', headers=auth(operator),
    )
    assert operator_queue.status_code == 200, operator_queue.get_json()
    assert operator_queue.get_json()['connection_alerts'][0][
        'reconnect_requires_operator'
    ] is False


def test_connection_create_distinguishes_duplicate_from_schema_failure(
    app, client, monkeypatch,
):
    from sqlalchemy.exc import IntegrityError
    from backend.routes import business_integrations as integration_routes

    owner = register(client, 'connection-errors@example.com')
    business_id = verified_business(app, owner['user']['id'])
    create_connection(client, owner, business_id)
    duplicate = client.post(
        f'/api/businesses/{business_id}/connections',
        json={'provider_key': 'link_catalog', 'config': {}},
        headers=auth(owner),
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()['error'] == 'provider_already_connected'

    with app.app_context():
        db.session.query(BusinessProviderConnection).delete()
        db.session.commit()

    def fail_persistence(**_kwargs):
        raise IntegrityError(
            'INSERT INTO business_provider_connection',
            {},
            Exception('NOT NULL constraint failed: business_provider_connection.id'),
        )

    monkeypatch.setattr(integration_routes, 'create_connection', fail_persistence)
    failed = client.post(
        f'/api/businesses/{business_id}/connections',
        json={'provider_key': 'link_catalog', 'config': {}},
        headers=auth(owner),
    )
    assert failed.status_code == 500
    assert failed.get_json()['error'] == 'connection_create_failed'


def test_provider_registry_and_secret_boundaries_are_honest():
    descriptors = {item.key: item for item in provider_registry.descriptors()}
    assert descriptors['link_catalog'].availability == 'active'
    assert descriptors['link_catalog'].supports_push is True
    assert descriptors['link_catalog'].supports_pull is True
    assert descriptors['courtreserve'].availability == 'not_available'
    assert descriptors['mindbody'].availability == 'not_available'
    with pytest.raises(ProviderNotAvailable):
        provider_registry.get('courtreserve')
    with pytest.raises(ValidationError, match='https_url_required'):
        safe_external_url('http://booking.example.com')
    with pytest.raises(ValidationError, match='secrets_must_use_credential_vault'):
        sanitize_public_config({'api_key': 'do-not-store-this'})
    with pytest.raises(ValidationError, match='secrets_must_use_credential_vault'):
        sanitize_public_config({'notes': 'Bearer abcdefghijklmnopqrstuvwxyz123456'})

    adapter = provider_registry.get('link_catalog')
    with pytest.raises(ValidationError, match='unsupported_catalog_field'):
        adapter.normalize_snapshot({
            'schema_version': 1, 'occurrences': [], 'conversions': [],
            'silently_ignored': True,
        })
    invalid_occurrence = catalog()
    invalid_occurrence['occurrences'][0]['silently_ignored'] = True
    with pytest.raises(ValidationError, match='unsupported_occurrence_field'):
        adapter.normalize_snapshot(invalid_occurrence)
    missing_timezone = catalog()
    missing_timezone['occurrences'][0].pop('timezone')
    with pytest.raises(ValidationError, match='timezone_required'):
        adapter.normalize_snapshot(missing_timezone)
    conflicting_time_shape = catalog()
    conflicting_time_shape['occurrences'][0].update({
        'starts_at': '2026-09-08T18:00:00Z',
        'ends_at': '2026-09-08T20:00:00Z',
    })
    with pytest.raises(ValidationError, match='occurrence_time_shape_conflict'):
        adapter.normalize_snapshot(conflicting_time_shape)


def test_oauth_state_and_webhook_signatures_are_tamper_evident():
    clock = lambda: 1_000
    manager = OAuthStateManager('x' * 32, ttl_seconds=120, clock=clock)
    state = manager.issue(
        business_id=42, provider_key='future_provider', redirect_path='/business',
    )
    verified = manager.verify(state)
    assert verified.business_id == 42
    assert verified.provider_key == 'future_provider'
    with pytest.raises(ValidationError, match='invalid_oauth_state'):
        manager.verify(state[:-1] + ('A' if state[-1] != 'A' else 'B'))

    body = b'{"schema_version":1}'
    signature = sign_payload('hook-secret', body, timestamp=1_000)
    result = verify_signature(
        'hook-secret', body, signature, tolerance=300, now=1_001,
    )
    assert result.payload_digest == hashlib.sha256(body).hexdigest()
    with pytest.raises(WebhookVerificationError):
        verify_signature('wrong', body, signature, tolerance=300, now=1_001)


def test_link_probe_rejects_private_dns_and_supports_validated_transport(monkeypatch):
    private_resolver = lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('127.0.0.1', 443)),
    ]
    closed = probe_https_url(
        'https://example.com/booking', resolver=private_resolver,
        transport=lambda url, timeout: (200, {}, 1),
    )
    assert closed.status == 'unsafe'
    assert closed.error_code == 'private_link_target_rejected'

    resolver = lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('93.184.216.34', 443)),
    ]
    transport = lambda url, timeout: (200, {}, 12)
    checked = probe_https_url(
        'https://example.com/booking', resolver=resolver, transport=transport,
    )
    assert checked.status == 'healthy'
    assert checked.http_status == 200
    assert checked.latency_ms == 12

    def redirect_resolver(host, *_args, **_kwargs):
        address = '127.0.0.1' if host == 'internal.example.com' else '93.184.216.34'
        return [(2, 1, 6, '', (address, 443))]

    redirected = probe_https_url(
        'https://example.com/booking', resolver=redirect_resolver,
        transport=lambda _url, **_kwargs: (
            302, {'location': 'https://internal.example.com/private'}, 1,
        ),
    )
    assert redirected.status == 'unsafe'
    assert redirected.error_code == 'private_link_target_rejected'

    timeouts = []
    clock = iter((100.0, 100.0, 104.0))
    monkeypatch.setattr(
        'backend.integrations.link_health.time.monotonic', lambda: next(clock),
    )

    def slow_redirect(url, *, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            return 302, {'location': '/next'}, 1
        return 200, {}, 1

    bounded = probe_https_url(
        'https://example.com/booking', timeout=5, resolver=resolver,
        transport=slow_redirect,
    )
    assert bounded.status == 'healthy'
    assert timeouts[0] == pytest.approx(5)
    assert timeouts[1] == pytest.approx(1)


def test_catalog_pull_is_bounded_conditional_and_reconciles(
    app, client, monkeypatch,
):
    owner = register(client, 'pull-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(
        client, owner, business_id,
        source_url='https://feeds.example.com/catalog.json',
    )
    resolver = lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('93.184.216.34', 443)),
    ]
    seen_headers = []

    def first_transport(_url, **options):
        seen_headers.append(options['request_headers'])
        return (
            200,
            {'Content-Type': 'application/json', 'ETag': '"catalog-v1"'},
            json.dumps(catalog()).encode('utf-8'),
            8,
        )

    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        run, duplicate, not_modified = pull_connection_catalog(
            row, resolver=resolver, transport=first_transport,
        )
        db.session.commit()
        assert run.status == 'succeeded'
        assert duplicate is False
        assert not_modified is False
        assert row.pull_etag == '"catalog-v1"'
        assert BusinessScheduleOccurrence.query.filter_by(
            connection_id=row.id,
        ).count() == 1

        def second_transport(_url, **options):
            seen_headers.append(options['request_headers'])
            return 304, {'ETag': '"catalog-v1"'}, b'', 3

        run, duplicate, not_modified = pull_connection_catalog(
            row, resolver=resolver, transport=second_transport,
        )
        db.session.commit()
        assert run is None
        assert duplicate is True
        assert not_modified is True
        assert seen_headers[-1]['If-None-Match'] == '"catalog-v1"'

    oversized = lambda _url, **_options: (
        200,
        {'Content-Type': 'application/json'},
        b'x' * 1_000_001,
        1,
    )
    with pytest.raises(ValidationError, match='catalog_response_too_large'):
        pull_json_catalog(
            'https://feeds.example.com/catalog.json',
            resolver=resolver,
            transport=oversized,
        )

    pull_timeouts = []
    clock = iter((200.0, 200.0, 207.0))
    monkeypatch.setattr(
        'backend.integrations.pull.time.monotonic', lambda: next(clock),
    )

    def redirect_transport(_url, **options):
        pull_timeouts.append(options['timeout'])
        if len(pull_timeouts) == 1:
            return 302, {'location': '/next'}, b'', 1
        return 200, {'Content-Type': 'application/json'}, b'{}', 1

    bounded = pull_json_catalog(
        'https://feeds.example.com/catalog.json', timeout=8,
        resolver=resolver, transport=redirect_transport,
    )
    assert bounded.payload == {}
    assert pull_timeouts[0] == pytest.approx(8)
    assert pull_timeouts[1] == pytest.approx(1)


def test_profile_link_health_is_hashed_and_visible_to_operator_queue(app, client):
    owner = register(client, 'profile-links-owner@example.com')
    reviewer = register(client, 'profile-links-reviewer@example.com')
    business_id = verified_business(app, owner['user']['id'])
    resolver = lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('93.184.216.34', 443)),
    ]
    transport = lambda _url, **_options: (500, {}, 4)
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.website_url = 'https://club.example.com'
        business.booking_url = 'https://book.example.com'
        business.membership_url = 'https://join.example.com'
        db.session.get(User, reviewer['user']['id']).operator_role = 'reviewer'
        db.session.commit()
        assert [item.id for item in stale_business_profiles(limit=10)] == [business_id]
        checks = recheck_business_profile_links(
            business, resolver=resolver, transport=transport,
        )
        db.session.commit()
        assert len(checks) == 3
        assert all(item.connection_id is None for item in checks)
        assert all(item.status == 'broken' for item in checks)
        assert all(len(item.url_hash) == 64 for item in checks)
        columns = set(BusinessLinkHealthCheck.__table__.columns.keys())
        assert 'url' not in columns

    queued = client.get(
        '/api/operator/business/link-health', headers=auth(reviewer),
    )
    assert queued.status_code == 200
    items = queued.get_json()['items']
    assert len(items) == 3
    assert all(item['business_id'] == business_id for item in items)
    assert all(item['source'] == 'profile' for item in items)
    assert 'club.example.com' not in json.dumps(items)
    combined_queue = client.get(
        '/api/operator/business/queue', headers=auth(reviewer),
    )
    assert combined_queue.status_code == 200, combined_queue.get_json()
    combined = combined_queue.get_json()
    assert len(combined['profile_link_alerts']) == 3
    assert combined['connection_alerts'] == []
    assert all(
        item['connection_id'] is None
        for item in combined['profile_link_alerts']
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.website_url = 'https://new-club.example.com'
        db.session.commit()
    refreshed_queue = client.get(
        '/api/operator/business/link-health', headers=auth(reviewer),
    ).get_json()['items']
    assert len(refreshed_queue) == 2
    refreshed_combined = client.get(
        '/api/operator/business/queue', headers=auth(reviewer),
    ).get_json()
    assert len(refreshed_combined['profile_link_alerts']) == 2
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        recheck_business_profile_links(
            business,
            resolver=resolver,
            transport=lambda _url, **_options: (200, {}, 1),
        )
        db.session.commit()
    cleared = client.get(
        '/api/operator/business/queue', headers=auth(reviewer),
    ).get_json()
    assert cleared['profile_link_alerts'] == []


def test_cron_processes_due_pull_and_profile_health_with_failure_isolation(
    app, client, monkeypatch,
):
    # A smaller configured cap is raised to the safe daily drain floor; only
    # due work is processed, so the cap does not manufacture extra work.
    monkeypatch.setenv('BUSINESS_INTEGRATION_CRON_LIMIT', '1')
    monkeypatch.setenv('BUSINESS_INTEGRATION_CRON_TIME_BUDGET_SECONDS', '1')
    owner = register(client, 'cron-foundation-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(
        client, owner, business_id,
        source_url='https://feeds.example.com/catalog.json',
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.website_url = 'https://club.example.com'
        business.booking_url = 'https://book.example.com'
        business.membership_url = 'https://join.example.com'
        row = db.session.get(BusinessProviderConnection, connection['id'])
        row.next_sync_at = utcnow() - timedelta(minutes=1)
        db.session.add(BusinessIntegrationSyncRun(
            connection_id=row.id,
            trigger='reconcile',
            status='retry_scheduled',
            idempotency_key='cron-fairness-retry',
            payload_json=json.dumps(catalog(source_version='retry-v1')),
            next_retry_at=utcnow() - timedelta(minutes=1),
        ))
        db.session.commit()

    resolver = lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('93.184.216.34', 443)),
    ]

    def cron_pull(row):
        return pull_connection_catalog(
            row,
            resolver=resolver,
            transport=lambda _url, **_options: (
                200,
                {'Content-Type': 'application/json', 'ETag': '"cron-v1"'},
                json.dumps(catalog(source_version='cron-v1')).encode(),
                2,
            ),
        )

    def cron_profile_health(business, **options):
        return recheck_business_profile_links(
            business,
            resolver=resolver,
            transport=lambda _url, **_transport_options: (200, {}, 1),
            **options,
        )

    monkeypatch.setattr(
        'backend.routes.business_integrations.pull_connection_catalog', cron_pull,
    )
    monkeypatch.setattr(
        'backend.routes.business_integrations.recheck_connection_links',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('test failure')),
    )
    monkeypatch.setattr(
        'backend.routes.business_integrations.recheck_business_profile_links',
        cron_profile_health,
    )
    response = client.get('/api/cron/business-integrations', headers={
        'Authorization': 'Bearer test-cron-secret',
    })
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['sync_claimed'] == 1
    assert data['sync_succeeded'] == 1
    assert data['pull_claimed'] == 1
    assert data['pull_succeeded'] == 1
    assert data['health_failed'] == 1
    assert data['profile_health_claimed'] == 1
    assert data['profile_health_checked'] == 3
    assert data['processed'] == 4
    assert data['limit'] == 40
    assert data['time_budget_seconds'] == 45
    assert data['time_budget_exhausted'] is False
    assert sum((
        data['sync_claimed'], data['pull_claimed'], data['health_claimed'],
        data['profile_health_claimed'],
    )) <= data['limit']
    assert 'example.com' not in json.dumps(data)


def test_daily_cron_rotates_the_first_queue_lane():
    start = utcnow()
    orders = [_cron_queue_order(start + timedelta(days=offset)) for offset in range(4)]
    lanes = {'health', 'profile_health', 'sync', 'pull'}
    assert all(set(order) == lanes for order in orders)
    assert {order[0] for order in orders} == lanes


def test_suspension_stops_queued_sync_pull_and_link_health_work(app, client):
    owner = register(client, 'suspended-work-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(
        client, owner, business_id,
        source_url='https://feeds.example.com/catalog.json',
    )
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.governance_status = 'suspended'
        row = db.session.get(BusinessProviderConnection, connection['id'])
        row.next_sync_at = utcnow() - timedelta(minutes=1)
        run = BusinessIntegrationSyncRun(
            connection_id=row.id,
            trigger='reconcile',
            status='retry_scheduled',
            idempotency_key='suspended-retry',
            payload_json=json.dumps(catalog()),
            next_retry_at=utcnow() - timedelta(minutes=1),
        )
        db.session.add(run)
        db.session.commit()

        assert due_sync_runs(limit=10) == []
        assert due_pull_connections(limit=10) == []
        assert stale_connections(limit=10) == []
        process_sync_run(run)
        db.session.commit()
        assert run.status == 'cancelled'
        assert BusinessScheduleOccurrence.query.filter_by(
            connection_id=row.id,
        ).count() == 0


def test_owner_push_reconciles_structured_schedule_and_is_idempotent(app, client):
    owner = register(client, 'owner-foundation@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(client, owner, business_id)

    first = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(),
        headers={**auth(owner), 'Idempotency-Key': 'catalog-v1'},
    )
    assert first.status_code == 202, first.get_json()
    assert first.get_json()['run']['status'] == 'succeeded'
    assert first.get_json()['run']['metrics']['occurrences_created'] == 1

    duplicate = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(),
        headers={**auth(owner), 'Idempotency-Key': 'catalog-v1'},
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()['duplicate'] is True

    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        recheck_connection_links(
            row,
            resolver=lambda *_args, **_kwargs: [
                (2, 1, 6, '', ('93.184.216.34', 443)),
            ],
            transport=lambda _url, **_kwargs: (200, {}, 1),
        )
        db.session.commit()

    public = client.get(f'/api/businesses/{business_id}/integrated-schedule')
    assert public.status_code == 200
    occurrence = public.get_json()['items'][0]
    assert occurrence['timezone'] == 'America/Chicago'
    assert occurrence['event_date'] == '2026-09-08'
    assert occurrence['capacity'] == 24
    assert occurrence['spots_remaining'] == 7
    assert occurrence['location_note'] == 'Courts 1–4'
    assert occurrence['instructor'] == 'Venue staff'
    assert occurrence['booking_url'].startswith('https://')

    removed = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(source_version='v2', occurrences=[]),
        headers={**auth(owner), 'Idempotency-Key': 'catalog-v2'},
    )
    assert removed.status_code == 202
    assert removed.get_json()['run']['metrics']['occurrences_cancelled'] == 1
    assert client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
    ).get_json()['items'][0]['status'] == 'cancelled'


def test_provider_only_schedule_is_discoverable_after_publication_gate(app, client):
    owner = register(client, 'provider-discovery@example.com')
    business_id = verified_business(app, owner['user']['id'])
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.booking_url = ''
        db.session.commit()
    created = client.post(
        f'/api/businesses/{business_id}/connections',
        json={
            'provider_key': 'link_catalog',
            'display_name': 'Schedule-only feed',
            'config': {'label': 'Schedule-only feed'},
        },
        headers=auth(owner),
    )
    assert created.status_code == 201, created.get_json()
    connection_id = created.get_json()['connection']['id']
    schedule = catalog(occurrences=[{
        'external_id': 'weekly-open-play',
        'title': 'Weekly open play',
        'kind': 'open_play',
        'recurrence': 'FREQ=WEEKLY;BYDAY=SA',
        'start_date': '2026-08-01',
        'start_time': '09:00',
        'end_time': '11:00',
        'timezone': 'America/Chicago',
    }])
    synced = client.put(
        f'/api/businesses/{business_id}/connections/{connection_id}/catalog',
        json=schedule,
        headers=auth(owner),
    )
    assert synced.status_code == 202, synced.get_json()
    with app.app_context():
        connection = db.session.get(BusinessProviderConnection, connection_id)
        recheck_connection_links(connection)
        db.session.commit()

    court = client.get('/api/courts?q=Foundation Pickleball Club')
    assert court.status_code == 200, court.get_json()
    compact = court.get_json()['items'][0]['business']
    assert compact['booking_available'] is False
    assert compact['schedule_available'] is True
    assert compact['programs_available'] is True

    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.court.closed = True
        db.session.commit()
    owner_connection = client.get(
        f'/api/businesses/{business_id}/connections', headers=auth(owner),
    ).get_json()['items'][0]
    assert owner_connection['publication_ready'] is False


def test_imported_booking_links_require_review_scope_and_current_health(app, client):
    owner = register(client, 'booking-boundary-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])

    unapproved = client.post(
        f'/api/businesses/{business_id}/connections',
        json={
            'provider_key': 'link_catalog',
            'display_name': 'Unapproved destination',
            'config': {
                'label': 'Schedule',
                'booking_base_url': 'https://unreviewed.example.com/book',
            },
        },
        headers=auth(owner),
    )
    assert unapproved.status_code == 400
    assert unapproved.get_json()['error'] == (
        'booking_base_url_requires_approved_profile_link'
    )

    connection = create_connection(client, owner, business_id)
    hostile = catalog(occurrences=[{
        'external_id': 'hostile-link',
        'title': 'Open play',
        'kind': 'open_play',
        'event_date': '2026-09-08',
        'start_time': '18:00',
        'end_time': '20:00',
        'timezone': 'UTC',
        'booking_url': 'https://attacker.example/phish',
    }])
    rejected = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=hostile,
        headers=auth(owner),
    )
    assert rejected.status_code == 400
    assert rejected.get_json()['error'] == (
        'occurrence_booking_url_outside_approved_base'
    )

    accepted = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(),
        headers=auth(owner),
    )
    assert accepted.status_code == 202, accepted.get_json()
    assert accepted.get_json()['connection']['health_status'] == 'unknown'
    assert accepted.get_json()['connection']['publication_ready'] is False
    assert client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
    ).get_json()['items'] == []

    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        redirected = recheck_connection_links(
            row,
            resolver=lambda *_args, **_kwargs: [
                (2, 1, 6, '', ('93.184.216.34', 443)),
            ],
            transport=lambda url, **_kwargs: (
                (302, {'location': 'https://attacker.example/phish'}, 1)
                if 'book.example.com' in url else (200, {}, 1)
            ),
        )
        assert redirected[0].status == 'unsafe'
        assert redirected[0].error_code == (
            'booking_redirect_outside_approved_base'
        )
        assert row.health_status == 'unsafe'
        db.session.flush()
        recheck_connection_links(
            row,
            resolver=lambda *_args, **_kwargs: [
                (2, 1, 6, '', ('93.184.216.34', 443)),
            ],
            transport=lambda _url, **_kwargs: (200, {}, 1),
        )
        db.session.commit()
    published = client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
    ).get_json()['items']
    assert len(published) == 1
    assert published[0]['booking_available'] is True
    assert 'attacker.example' not in json.dumps(published)
    owner_view = client.get(
        f'/api/businesses/{business_id}/connections', headers=auth(owner),
    ).get_json()['items'][0]
    assert owner_view['publication_ready'] is True

    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.booking_url = 'https://replacement.example.com/book'
        db.session.commit()
    assert client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
    ).get_json()['items'] == []


def test_daily_cron_cadence_does_not_hide_last_known_healthy_schedule(
    app, client,
):
    owner = register(client, 'daily-health-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(client, owner, business_id)
    pushed = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(), headers=auth(owner),
    )
    assert pushed.status_code == 202, pushed.get_json()

    resolver = lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('93.184.216.34', 443)),
    ]
    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        recheck_connection_links(
            row, resolver=resolver,
            transport=lambda _url, **_kwargs: (200, {}, 1),
        )
        db.session.commit()
        checked_at = utcnow() - timedelta(hours=24)
        row.last_health_checked_at = checked_at
        for check in BusinessLinkHealthCheck.query.filter_by(
            connection_id=row.id,
        ):
            check.checked_at = checked_at
            # This models rows created by the former six-hour contract. The
            # canonical validity window must not depend on that stale deadline.
            check.next_check_at = checked_at + timedelta(hours=6)
        db.session.commit()

        assert [item.id for item in stale_connections(limit=10)] == [row.id]

    still_public = client.get(
        f'/api/businesses/{business_id}/integrated-schedule',
    )
    assert still_public.status_code == 200
    assert len(still_public.get_json()['items']) == 1

    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        too_old = utcnow() - timedelta(hours=73)
        row.last_health_checked_at = too_old
        for check in BusinessLinkHealthCheck.query.filter_by(
            connection_id=row.id,
        ):
            check.checked_at = too_old
        db.session.commit()

    expired = client.get(
        f'/api/businesses/{business_id}/integrated-schedule',
    )
    assert expired.status_code == 200
    assert expired.get_json()['items'] == []


def test_public_schedule_range_keeps_timestamp_and_recurring_items(app, client):
    owner = register(client, 'schedule-range-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(client, owner, business_id)
    occurrences = [
        {
            'external_id': 'timestamp-only',
            'title': 'Dated clinic',
            'kind': 'clinic',
            'starts_at': '2026-09-12T17:00:00Z',
            'ends_at': '2026-09-12T18:00:00Z',
            'timezone': 'America/Chicago',
        },
        {
            'external_id': 'recurring-overlap',
            'title': 'Weekly open play',
            'kind': 'open_play',
            'recurrence': 'FREQ=WEEKLY;BYDAY=SA',
            'start_date': '2026-08-01',
            'end_date': '2026-10-31',
            'start_time': '09:00',
            'end_time': '11:00',
            'timezone': 'America/Chicago',
        },
        {
            'external_id': 'outside-range',
            'title': 'Old clinic',
            'kind': 'clinic',
            'event_date': '2026-08-01',
            'start_time': '09:00',
            'end_time': '10:00',
            'timezone': 'America/Chicago',
        },
    ]
    pushed = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(occurrences=occurrences),
        headers=auth(owner),
    )
    assert pushed.status_code == 202, pushed.get_json()
    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        recheck_connection_links(
            row,
            resolver=lambda *_args, **_kwargs: [
                (2, 1, 6, '', ('93.184.216.34', 443)),
            ],
            transport=lambda _url, **_kwargs: (200, {}, 1),
        )
        db.session.commit()
    response = client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
        '?from=2026-09-10&to=2026-09-20'
    )
    assert response.status_code == 200
    assert {item['external_id'] for item in response.get_json()['items']} == {
        'timestamp-only', 'recurring-overlap',
    }


def test_team_roles_and_governance_gate_connection_operations(app, client):
    owner = register(client, 'team-owner@example.com')
    editor = register(client, 'team-editor@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(client, owner, business_id)
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        organization = ensure_organization(business, owner['user']['id'])
        db.session.add(BusinessOrganizationMember(
            organization=organization,
            user_id=editor['user']['id'],
            role='editor',
        ))
        db.session.commit()

    pushed = client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(), headers=auth(editor),
    )
    assert pushed.status_code == 202, pushed.get_json()
    forbidden = client.delete(
        f"/api/businesses/{business_id}/connections/{connection['id']}",
        headers=auth(editor),
    )
    assert forbidden.status_code == 403
    assert client.delete(
        f"/api/businesses/{business_id}/connections/{connection['id']}",
        headers=auth(owner),
    ).status_code == 200
    assert client.post(
        f"/api/businesses/{business_id}/connections/{connection['id']}/reconnect",
        headers=auth(editor),
    ).status_code == 403
    assert client.post(
        f"/api/businesses/{business_id}/connections/{connection['id']}/reconnect",
        headers=auth(owner),
    ).status_code == 200
    with app.app_context():
        reconnect_audit = BusinessIntegrationAuditEvent.query.filter_by(
            business_id=business_id,
            connection_id=connection['id'],
            action='connection.reconnected',
        ).one()
        assert reconnect_audit.actor_kind == 'owner'

    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        business.governance_status = 'suspended'
        db.session.commit()
    assert client.put(
        f"/api/businesses/{business_id}/connections/{connection['id']}/catalog",
        json=catalog(source_version='blocked'), headers=auth(owner),
    ).status_code == 403
    assert client.get(
        f'/api/businesses/{business_id}/integrated-schedule'
    ).status_code == 404


def test_integration_audit_uses_the_actual_business_team_role(app, client):
    owner = register(client, 'audit-owner@example.com')
    admin = register(client, 'audit-admin@example.com')
    editor = register(client, 'audit-editor@example.com')
    business_id = verified_business(app, owner['user']['id'])
    with app.app_context():
        business = db.session.get(BusinessProfile, business_id)
        organization = ensure_organization(business, owner['user']['id'])
        db.session.add_all([
            BusinessOrganizationMember(
                organization=organization,
                user_id=admin['user']['id'],
                role='admin',
            ),
            BusinessOrganizationMember(
                organization=organization,
                user_id=editor['user']['id'],
                role='editor',
            ),
        ])
        db.session.commit()

    created = client.post(
        f'/api/businesses/{business_id}/connections',
        json={
            'provider_key': 'link_catalog',
            'display_name': 'Role-audited feed',
            'config': {'label': 'Initial feed'},
        },
        headers=auth(admin),
    )
    assert created.status_code == 201, created.get_json()
    connection_id = created.get_json()['connection']['id']
    edited = client.patch(
        f'/api/businesses/{business_id}/connections/{connection_id}',
        json={'config': {'label': 'Updated by admin'}},
        headers=auth(admin),
    )
    assert edited.status_code == 200, edited.get_json()
    pushed = client.put(
        f'/api/businesses/{business_id}/connections/{connection_id}/catalog',
        json=catalog(occurrences=[]),
        headers=auth(editor),
    )
    assert pushed.status_code == 202, pushed.get_json()
    rechecked = client.post(
        f'/api/businesses/{business_id}/connections/{connection_id}/recheck',
        headers=auth(editor),
    )
    assert rechecked.status_code == 200, rechecked.get_json()
    assert client.delete(
        f'/api/businesses/{business_id}/connections/{connection_id}',
        headers=auth(admin),
    ).status_code == 200
    assert client.post(
        f'/api/businesses/{business_id}/connections/{connection_id}/reconnect',
        headers=auth(admin),
    ).status_code == 200

    with app.app_context():
        actor_by_action = {
            item.action: item.actor_kind
            for item in BusinessIntegrationAuditEvent.query.filter_by(
                business_id=business_id,
                connection_id=connection_id,
            ).all()
        }
        assert actor_by_action['connection.created'] == 'admin'
        assert actor_by_action['connection.config_updated'] == 'admin'
        assert actor_by_action['sync.submitted'] == 'editor'
        assert actor_by_action['connection.health_checked'] == 'editor'
        assert actor_by_action['connection.disconnected'] == 'admin'
        assert actor_by_action['connection.reconnected'] == 'admin'


def test_signed_webhook_syncs_conversion_without_storing_raw_secret_or_payload(app, client):
    owner = register(client, 'webhook-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(client, owner, business_id)
    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        row.webhook_secret_ref = 'env://BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK'
        db.session.commit()
        public_id = row.public_id

    payload = catalog(
        source_version='webhook-v1',
        conversions=[{
            'external_event_id': 'booking-paid-1',
            'occurrence_external_id': 'open-play-2026-09-08',
            'occurred_at': '2026-09-01T15:05:00Z',
            'value_minor': 1200,
            'currency': 'USD',
        }],
    )
    raw = json.dumps(payload, separators=(',', ':')).encode()
    signature = sign_payload('webhook-secret-value', raw)
    headers = {
        'Content-Type': 'application/json',
        'X-Third-Shot-Signature': signature,
        'X-Provider-Event-Id': 'event-1',
    }
    response = client.post(
        f'/api/business-integrations/webhooks/link_catalog/{public_id}',
        data=raw, headers=headers,
    )
    assert response.status_code == 202, response.get_json()
    duplicate = client.post(
        f'/api/business-integrations/webhooks/link_catalog/{public_id}',
        data=raw, headers=headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()['duplicate'] is True
    conflicting_raw = json.dumps(
        catalog(source_version='different-body'), separators=(',', ':'),
    ).encode()
    conflict_headers = {
        **headers,
        'X-Third-Shot-Signature': sign_payload(
            'webhook-secret-value', conflicting_raw,
        ),
    }
    conflict = client.post(
        f'/api/business-integrations/webhooks/link_catalog/{public_id}',
        data=conflicting_raw, headers=conflict_headers,
    )
    assert conflict.status_code == 409
    assert conflict.get_json()['error'] == 'webhook_event_id_reused'

    with app.app_context():
        row = db.session.get(BusinessProviderConnection, connection['id'])
        assert row.webhook_secret_ref == 'env://BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK'
        assert 'webhook-secret-value' not in row.public_config
        assert BusinessWebhookReceipt.query.count() == 1
        receipt = BusinessWebhookReceipt.query.one()
        assert receipt.payload_digest == hashlib.sha256(raw).hexdigest()
        assert not hasattr(receipt, 'payload')
        assert BusinessBookingEvent.query.filter_by(event_type='conversion').count() == 1


def test_privacy_safe_events_and_business_analytics(app, client):
    owner = register(client, 'analytics-owner@example.com')
    business_id = verified_business(app, owner['user']['id'])
    for index, action in enumerate((
        'profile_view', 'website', 'contact', 'schedule', 'booking', 'lesson',
    )):
        response = client.post(f'/api/businesses/{business_id}/events', json={
            'client_event_id': f'event-{index}', 'action': action,
        })
        assert response.status_code == 201, response.get_json()
    duplicate = client.post(f'/api/businesses/{business_id}/events', json={
        'client_event_id': 'event-4', 'action': 'booking',
    })
    assert duplicate.status_code == 200
    with app.app_context():
        db.session.add_all([
            BusinessBookingEvent(
                business_id=business_id,
                event_type='conversion',
                event_key='analytics-conversion-usd',
                external_event_id='analytics-conversion-usd',
                action='booking',
                occurred_at=utcnow(),
                value_minor=1200,
                currency='USD',
                source='test',
            ),
            BusinessBookingEvent(
                business_id=business_id,
                event_type='conversion',
                event_key='analytics-conversion-eur',
                external_event_id='analytics-conversion-eur',
                action='booking',
                occurred_at=utcnow(),
                value_minor=900,
                currency='EUR',
                source='test',
            ),
        ])
        db.session.commit()
    analytics = client.get(
        f'/api/businesses/{business_id}/analytics?range=30d',
        headers=auth(owner),
    )
    assert analytics.status_code == 200
    data = analytics.get_json()
    assert data['profile_views'] == 1
    assert data['website_clicks'] == 1
    assert data['contact_clicks'] == 1
    assert data['schedule_opens'] == 1
    assert data['booking_clicks'] == 1
    assert data['lesson_clicks'] == 1
    assert data['conversions'] == 2
    assert data['conversion_value_by_currency'] == {'EUR': 900, 'USD': 1200}
    assert 'conversion_value_minor' not in data
    with app.app_context():
        columns = set(BusinessBookingEvent.__table__.columns.keys())
        assert {'user_id', 'ip', 'user_agent', 'destination_url'}.isdisjoint(columns)


def test_operator_role_mfa_vault_and_cron_are_bounded(app, client, monkeypatch):
    owner = register(client, 'operator-target@example.com')
    operator = register(client, 'operator@example.com', 'Operator')
    business_id = verified_business(app, owner['user']['id'])
    connection = create_connection(client, owner, business_id)
    with app.app_context():
        db.session.get(User, operator['user']['id']).operator_role = 'admin'
        db.session.commit()

    mfa_calls = []

    def verify_fresh_mfa(_user, code, *, allow_recovery):
        mfa_calls.append((code, allow_recovery))
        return code == '123456', False

    monkeypatch.setattr(
        'backend.routes.business_integrations.verify_user_mfa',
        verify_fresh_mfa,
    )

    unauthenticated = client.patch(
        f"/api/operator/business/connections/{connection['id']}",
        json={'webhook_secret_ref': 'env://BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK'},
    )
    assert unauthenticated.status_code == 401
    authenticated_non_operator = client.patch(
        f"/api/operator/business/connections/{connection['id']}",
        json={'webhook_secret_ref': 'env://BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK'},
        headers=auth(owner),
    )
    assert authenticated_non_operator.status_code == 403
    assert authenticated_non_operator.get_json()['error'] == 'business_operator_required'
    missing_mfa = client.patch(
        f"/api/operator/business/connections/{connection['id']}",
        json={'webhook_secret_ref': 'env://BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK'},
        headers=auth(operator),
    )
    assert missing_mfa.status_code == 403
    assert missing_mfa.get_json()['error'] == 'operator_mfa_required'
    configured = client.patch(
        f"/api/operator/business/connections/{connection['id']}",
        json={
            'webhook_secret_ref': 'env://BUSINESS_PROVIDER_SECRET_TEST_WEBHOOK',
            'mfa_code': '123456',
        },
        headers=auth(operator),
    )
    assert configured.status_code == 200, configured.get_json()
    assert configured.get_json()['connection']['webhook_configured'] is True
    assert 'webhook_secret_ref' not in configured.get_json()['connection']
    assert mfa_calls[-1] == ('123456', False)

    plaintext = 'provider-secret-that-must-not-be-stored'
    stored = client.post(
        f"/api/operator/business/connections/{connection['id']}/credentials",
        json={
            'purpose': 'credential',
            'secret': plaintext,
            'mfa_code': '123456',
        },
        headers=auth(operator),
    )
    assert stored.status_code == 201, stored.get_json()
    assert stored.get_json()['connection']['credential_configured'] is True
    assert plaintext not in json.dumps(stored.get_json())
    with app.app_context():
        secret_row = BusinessCredentialSecret.query.one()
        assert plaintext not in secret_row.ciphertext
        assert secret_row.ciphertext
        assert configured_vault().resolve(secret_row.reference) == plaintext.encode()
        assert secret_row.key_version == 1
        connection_row = db.session.get(BusinessProviderConnection, connection['id'])
        assert connection_row.credential_ref == secret_row.reference
        connection_row.last_health_checked_at = utcnow()
        db.session.commit()
        key = app.config['BUSINESS_CREDENTIAL_ENCRYPTION_KEY']
        app.config['BUSINESS_CREDENTIAL_ENCRYPTION_KEY'] = ''
        with pytest.raises(CredentialVaultUnavailable):
            configured_vault().put(
                'must-fail-closed', purpose='credential',
                created_by_id=operator['user']['id'],
            )
        app.config['BUSINESS_CREDENTIAL_ENCRYPTION_KEY'] = key

    assert client.get('/api/cron/business-integrations').status_code == 401
    cron = client.get('/api/cron/business-integrations', headers={
        'Authorization': 'Bearer test-cron-secret',
    })
    assert cron.status_code == 200, cron.get_json()
    assert cron.get_json()['limit'] <= 100
    assert set(cron.get_json()) >= {
        'sync_claimed', 'sync_succeeded', 'sync_retry_scheduled',
        'sync_failed', 'pull_claimed', 'pull_succeeded', 'pull_failed',
        'health_claimed', 'health_checked', 'profile_health_claimed',
    }

    owner_disconnected = client.delete(
        f"/api/businesses/{business_id}/connections/{connection['id']}",
        headers=auth(owner),
    )
    assert owner_disconnected.status_code == 200
    with app.app_context():
        secret_row = BusinessCredentialSecret.query.one()
        assert secret_row.deleted_at is not None
        assert secret_row.ciphertext == ''
    owner_reconnected = client.post(
        f"/api/businesses/{business_id}/connections/{connection['id']}/reconnect",
        headers=auth(owner),
    )
    assert owner_reconnected.status_code == 403
    assert owner_reconnected.get_json()['error'] == 'operator_reconnect_required'
    refused_reconnect = client.post(
        f"/api/operator/business/connections/{connection['id']}/reconnect",
        json={'mfa_code': '000000'},
        headers=auth(operator),
    )
    assert refused_reconnect.status_code == 403
    operator_reconnected = client.post(
        f"/api/operator/business/connections/{connection['id']}/reconnect",
        json={'mfa_code': '123456'},
        headers=auth(operator),
    )
    assert operator_reconnected.status_code == 200
    assert operator_reconnected.get_json()['connection']['status'] == 'connected'

    refused_disconnect = client.delete(
        f"/api/operator/business/connections/{connection['id']}",
        json={'mfa_code': '000000'},
        headers=auth(operator),
    )
    assert refused_disconnect.status_code == 403
    disconnected = client.delete(
        f"/api/operator/business/connections/{connection['id']}",
        json={'mfa_code': '123456'},
        headers=auth(operator),
    )
    assert disconnected.status_code == 200
    reconnected = client.post(
        f"/api/operator/business/connections/{connection['id']}/reconnect",
        json={'mfa_code': '123456'},
        headers=auth(operator),
    )
    assert reconnected.status_code == 200
    assert reconnected.get_json()['connection']['status'] == 'connected'


def test_schema_verifier_covers_every_foundation_table(app):
    from sqlalchemy import inspect
    with app.app_context():
        assert schema_gaps(inspect(db.engine), None) == []


class _MissingIntegrationSchemaObject:
    def __init__(self, wrapped, category, table, name, repaired=lambda: False):
        self.wrapped = wrapped
        self.category = category
        self.table = table
        self.name = name
        self.repaired = repaired

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def _filtered(self, category, table, values, key):
        if (
            self.category == category
            and self.table == table
            and not self.repaired()
        ):
            return [item for item in values if item.get(key) != self.name]
        return values

    def get_columns(self, table, **kwargs):
        return self._filtered(
            'column', table, self.wrapped.get_columns(table, **kwargs), 'name',
        )

    def get_indexes(self, table, **kwargs):
        return self._filtered(
            'index', table, self.wrapped.get_indexes(table, **kwargs), 'name',
        )

    def get_unique_constraints(self, table, **kwargs):
        return self._filtered(
            'unique', table,
            self.wrapped.get_unique_constraints(table, **kwargs), 'name',
        )

    def get_check_constraints(self, table, **kwargs):
        return self._filtered(
            'check', table,
            self.wrapped.get_check_constraints(table, **kwargs), 'name',
        )

    def get_foreign_keys(self, table, **kwargs):
        return self._filtered(
            'foreign_key', table,
            self.wrapped.get_foreign_keys(table, **kwargs), 'name',
        )

    def get_pk_constraint(self, table, **kwargs):
        item = self.wrapped.get_pk_constraint(table, **kwargs)
        if (
            self.category == 'primary_key'
            and self.table == table
            and not self.repaired()
        ):
            return {'name': item.get('name'), 'constrained_columns': []}
        return item


def _gaps_with_missing(app, category, table, name):
    from sqlalchemy import inspect

    with app.app_context():
        wrapped = _MissingIntegrationSchemaObject(
            inspect(db.engine), category, table, name,
        )
        return schema_gaps(wrapped, None)


def test_schema_verifier_detects_missing_integration_column(app):
    gaps = _gaps_with_missing(
        app, 'column', 'business_provider_connection', 'version',
    )
    assert any('missing columns' in gap and 'version' in gap for gap in gaps)


def test_schema_verifier_detects_missing_integration_index(app):
    gaps = _gaps_with_missing(
        app, 'index', 'business_provider_connection',
        'ix_business_provider_connection_status',
    )
    assert gaps == [
        'business_provider_connection missing index '
        'ix_business_provider_connection_status',
    ]


def test_schema_verifier_detects_missing_integration_foreign_key(app):
    gaps = _gaps_with_missing(
        app, 'foreign_key', 'business_provider_connection',
        'business_provider_connection_business_id_fkey',
    )
    assert gaps == [
        'business_provider_connection missing foreign key '
        'business_provider_connection_business_id_fkey',
    ]


def test_schema_verifier_detects_missing_integration_unique(app):
    gaps = _gaps_with_missing(
        app, 'unique', 'business_provider_connection',
        'uq_business_provider_connection',
    )
    assert gaps == [
        'business_provider_connection missing unique constraint '
        'uq_business_provider_connection',
    ]


def test_schema_verifier_detects_missing_integration_check(app):
    gaps = _gaps_with_missing(
        app, 'check', 'business_provider_connection',
        'ck_business_provider_connection_status',
    )
    assert gaps == [
        'business_provider_connection missing check constraint '
        'ck_business_provider_connection_status',
    ]


def test_schema_verifier_detects_missing_integration_primary_key(app):
    gaps = _gaps_with_missing(
        app, 'primary_key', 'business_provider_connection', 'id',
    )
    assert gaps == [
        'business_provider_connection primary key has wrong columns; '
        "expected ['id']",
    ]


def test_static_integration_contract_exactly_matches_live_models(app):
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

    with app.app_context():
        tables = {table.name: table for table in _foundation_model_tables()}
        assert set(tables) == set(REQUIRED_COLUMNS)
        for table_name, table in tables.items():
            assert REQUIRED_COLUMNS[table_name] == set(table.columns.keys())
            assert REQUIRED_PRIMARY_KEYS[table_name] == tuple(
                column.name for column in table.primary_key.columns
            )
            indexes = {
                item.name: (
                    tuple(column.name for column in item.columns),
                    bool(item.unique),
                )
                for item in table.indexes
            }
            assert REQUIRED_INDEXES[table_name] == indexes
            uniques = {
                item.name: tuple(column.name for column in item.columns)
                for item in table.constraints
                if isinstance(item, UniqueConstraint) and item.name
            }
            assert REQUIRED_UNIQUES.get(table_name, {}) == uniques
            checks = {
                item.name for item in table.constraints
                if isinstance(item, CheckConstraint) and item.name
            }
            assert set(REQUIRED_CHECKS.get(table_name, {})) == checks
            foreign_keys = {
                item.name: (
                    tuple(element.parent.name for element in item.elements),
                    item.elements[0].column.table.name,
                    tuple(element.column.name for element in item.elements),
                )
                for item in table.constraints
                if isinstance(item, ForeignKeyConstraint) and item.name
            }
            assert REQUIRED_FOREIGN_KEYS.get(table_name, {}) == foreign_keys


class _RecordingSchemaRepairConnection:
    def __init__(self, base_inspector, category, table, name):
        from sqlalchemy.dialects import postgresql

        self.dialect = postgresql.dialect()
        self.category = category
        self.table = table
        self.name = name
        self.repaired = False
        self.executed = []
        self.inspector = _MissingIntegrationSchemaObject(
            base_inspector, category, table, name,
            repaired=lambda: self.repaired,
        )

    def execute(self, statement):
        self.executed.append(statement)
        element = getattr(statement, 'element', None)
        repaired_name = getattr(element, 'name', None)
        if self.category == 'column':
            if 'ADD COLUMN' in str(statement) and self.name in str(statement):
                self.repaired = True
        elif self.category == 'primary_key':
            from sqlalchemy import PrimaryKeyConstraint

            if isinstance(element, PrimaryKeyConstraint):
                self.repaired = True
        elif repaired_name == self.name:
            self.repaired = True


@pytest.mark.parametrize(
    ('category', 'table', 'name', 'repair'),
    (
        (
            'column', 'business_provider_connection', 'version',
            _add_missing_columns,
        ),
        (
            'index', 'business_provider_connection',
            'ix_business_provider_connection_status', _repair_indexes,
        ),
        (
            'primary_key', 'business_provider_connection',
            'id', _repair_primary_keys,
        ),
        (
            'unique', 'business_provider_connection',
            'uq_business_provider_connection', _repair_constraints,
        ),
        (
            'check', 'business_provider_connection',
            'ck_business_provider_connection_status', _repair_constraints,
        ),
        (
            'foreign_key', 'business_provider_connection',
            'business_provider_connection_business_id_fkey',
            _repair_constraints,
        ),
    ),
)
def test_upgrade_planner_repairs_each_schema_object_category(
    app, monkeypatch, category, table, name, repair,
):
    import sqlalchemy
    from sqlalchemy import inspect

    with app.app_context():
        create_rules = {
            id(item): getattr(item, '_create_rule', None)
            for table_item in _foundation_model_tables()
            for item in table_item.constraints
        }
        connection = _RecordingSchemaRepairConnection(
            inspect(db.engine), category, table, name,
        )
        monkeypatch.setattr(sqlalchemy, 'inspect', lambda _value: connection.inspector)
        repair(connection, None, _foundation_model_tables())
        assert connection.repaired is True
        assert connection.executed
        assert all(
            getattr(item, '_create_rule', None) is create_rules[id(item)]
            for table_item in _foundation_model_tables()
            for item in table_item.constraints
        )
