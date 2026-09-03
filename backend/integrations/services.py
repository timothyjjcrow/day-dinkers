"""Transactional connection, sync, reconciliation, and health operations."""
from __future__ import annotations

from datetime import timedelta
import json
import os
import posixpath
from urllib.parse import unquote, urlsplit

from sqlalchemy import case, func, select

from backend.app import db
from backend.models import BusinessProfile, utcnow
from backend.services.business_visibility import business_is_public
from backend.integrations import provider_registry
from backend.integrations.errors import IntegrationError, ValidationError
from backend.integrations.link_catalog import canonical_snapshot_payload
from backend.integrations.link_health import LinkProbeResult, probe_https_url
from backend.integrations.pull import pull_json_catalog
from backend.integrations.models import (
    BusinessBookingEvent,
    BusinessIntegrationAuditEvent,
    BusinessIntegrationSyncRun,
    BusinessLinkHealthCheck,
    BusinessProviderConnection,
    BusinessScheduleOccurrence,
)
from backend.integrations.safety import (
    safe_external_url,
    safe_json_dumps,
    safe_json_loads,
    stable_digest,
)


def _bounded_environment_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def link_health_validity_window():
    """Grace window for a last-known healthy destination.

    Production's Hobby cron runs daily.  A 72-hour validity window survives a
    delayed or missed daily run without keeping a known-bad connection live:
    every completed unhealthy probe still changes the connection status
    immediately and fails the publication gate above.
    """
    hours = _bounded_environment_int(
        'BUSINESS_LINK_HEALTH_VALID_HOURS', 72, 48, 168,
    )
    return timedelta(hours=hours)


def link_health_recheck_window():
    """Refresh healthy evidence before the next daily production cron."""
    hours = _bounded_environment_int(
        'BUSINESS_LINK_HEALTH_RECHECK_HOURS', 20, 6, 24,
    )
    return timedelta(hours=hours)


def _url_origin(value):
    """Return a comparison-safe HTTPS origin for an already validated URL."""
    parsed = urlsplit(safe_external_url(value, required=True))
    port = parsed.port or 443
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def _url_scope_path(value):
    """Normalize a URL path before checking that a deep link stays in scope."""
    path = urlsplit(safe_external_url(value, required=True)).path or '/'
    # Decode repeatedly so a double-encoded traversal cannot escape a reviewed
    # tenant path after another proxy or booking platform decodes it.
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.replace('\\', '/')
    normalized = posixpath.normpath('/' + path.lstrip('/'))
    return normalized if normalized.startswith('/') else f'/{normalized}'


def _approved_business_action_urls(business):
    """Links in the approved profile are the human-reviewed trust boundary."""
    return tuple(filter(None, (
        safe_external_url(getattr(business, 'website_url', '')),
        safe_external_url(getattr(business, 'booking_url', '')),
        safe_external_url(getattr(business, 'membership_url', '')),
    )))


def _url_within_base(base_url, candidate_url):
    base_origin = _url_origin(base_url)
    base_path = _url_scope_path(base_url).rstrip('/') or '/'
    item_path = _url_scope_path(candidate_url)
    return _url_origin(candidate_url) == base_origin and (
        base_path == '/'
        or item_path == base_path
        or item_path.startswith(f'{base_path}/')
    )


def _validate_booking_scope(business, public_config, booking_urls=()):
    """Keep imported booking links beneath a reviewed business-profile link.

    An editor may update inventory without opening a new content review, so the
    connection may only use a base URL that was already approved on the public
    business profile. Per-occurrence deep links must remain on that exact
    origin and beneath its approved path.
    """
    public_config = dict(public_config or {})
    base_url = safe_external_url(public_config.get('booking_base_url'))
    booking_urls = tuple(filter(None, booking_urls))
    if booking_urls and not base_url:
        raise ValidationError('booking_base_url_required_for_occurrence_links')
    if not base_url:
        return ''
    approved = _approved_business_action_urls(business)
    if base_url not in approved:
        raise ValidationError('booking_base_url_requires_approved_profile_link')
    for booking_url in booking_urls:
        if not _url_within_base(base_url, booking_url):
            raise ValidationError('occurrence_booking_url_outside_approved_base')
    return base_url


def validate_catalog_booking_scope(connection, snapshot=None, *, config=None):
    booking_urls = (
        tuple(item.booking_url for item in snapshot.occurrences)
        if snapshot else ()
    )
    return _validate_booking_scope(
        connection.business,
        config if config is not None else connection.config_dict(),
        booking_urls,
    )


def publication_ready_connection_ids(connections):
    """Batch the exact public-readiness gate for provider-backed inventory."""
    requirements = {}
    candidates = {}
    for connection in connections:
        if (
            not business_is_public(connection.business)
            or connection.status != 'connected'
            or connection.health_status != 'healthy'
        ):
            continue
        try:
            adapter = provider_registry.get(connection.provider_key)
            validate_catalog_booking_scope(connection)
            urls = adapter.health_urls(connection.config_dict())
        except IntegrationError:
            continue
        candidates[connection.id] = connection
        requirements[connection.id] = tuple(
            (kind, stable_digest(url)) for kind, url in urls
        )

    if not candidates:
        return set()
    cutoff = utcnow() - link_health_validity_window()
    latest = {}
    checks = BusinessLinkHealthCheck.query.filter(
        BusinessLinkHealthCheck.connection_id.in_(tuple(candidates)),
        BusinessLinkHealthCheck.checked_at > cutoff,
    ).order_by(
        BusinessLinkHealthCheck.checked_at.desc(),
        BusinessLinkHealthCheck.id.desc(),
    ).all()
    for check in checks:
        key = (check.connection_id, check.link_kind, check.url_hash)
        latest.setdefault(key, check)

    ready = set()
    for connection_id, required in requirements.items():
        if all(
            (current := latest.get((connection_id, kind, url_hash))) is not None
            and current.status == 'healthy'
            for kind, url_hash in required
        ):
            ready.add(connection_id)
    return ready


def connection_publication_ready(connection):
    """Return whether imported data has current approval and link evidence."""
    return connection.id in publication_ready_connection_ids((connection,))


def public_occurrence_payload(item, connection):
    """Serialize one occurrence without leaking an unapproved legacy link."""
    payload = item.to_dict()
    if not item.booking_url:
        payload['booking_available'] = False
        return payload
    try:
        _validate_booking_scope(
            connection.business, connection.config_dict(), (item.booking_url,),
        )
    except IntegrationError:
        payload['booking_url'] = ''
        payload['booking_available'] = False
        return payload
    payload['booking_available'] = True
    return payload


def _business_integration_active(connection):
    business = connection.business
    return bool(
        business
        and business.claim_status == 'verified'
        and business.verified_at is not None
        and business.governance_status == 'active'
    )


def _audit(business_id, action, *, connection_id=None, actor_kind='system',
           actor_id='', metadata=None):
    db.session.add(BusinessIntegrationAuditEvent(
        business_id=business_id,
        connection_id=connection_id,
        actor_kind=actor_kind,
        actor_id=str(actor_id or '')[:120],
        action=str(action)[:80],
        metadata_json=safe_json_dumps(metadata or {}),
    ))


def create_connection(*, business, user_id, provider_key, display_name='', config=None,
                      actor_kind='owner'):
    adapter = provider_registry.get(provider_key)
    public_config = adapter.validate_public_config(config or {})
    _validate_booking_scope(business, public_config)
    connection = BusinessProviderConnection(
        business_id=business.id,
        created_by_id=user_id,
        provider_key=adapter.descriptor.key,
        display_name=(str(display_name or '').strip() or adapter.descriptor.name)[:120],
        status='connected' if adapter.descriptor.auth_mode == 'owner_push_or_signed_webhook' else 'draft',
        health_status='unknown',
        capabilities=safe_json_dumps(list(adapter.descriptor.capabilities)),
        public_config=safe_json_dumps(public_config),
        next_sync_at=utcnow() if public_config.get('source_url') else None,
    )
    db.session.add(connection)
    db.session.flush()
    _audit(
        business.id, 'connection.created', connection_id=connection.id,
        actor_kind=actor_kind, actor_id=user_id,
        metadata={'provider_key': connection.provider_key},
    )
    return connection


def update_connection_config(connection, config, *, actor_kind='owner', actor_id=''):
    adapter = provider_registry.get(connection.provider_key)
    previous_config = connection.config_dict()
    previous_source_url = previous_config.get('source_url')
    public_config = adapter.validate_public_config(config or {})
    _validate_booking_scope(connection.business, public_config)
    connection.public_config = safe_json_dumps(public_config)
    connection.next_sync_at = utcnow() if public_config.get('source_url') else None
    if public_config.get('source_url') != previous_source_url:
        connection.pull_etag = ''
        connection.pull_last_modified = ''
    if public_config != previous_config and connection.status != 'disconnected':
        # A successful data parse is not a link-health check. Any destination
        # change must earn fresh, exact-digest health evidence before public use.
        connection.health_status = 'unknown'
        connection.last_health_checked_at = None
    connection.version += 1
    _audit(
        connection.business_id, 'connection.config_updated',
        connection_id=connection.id, actor_kind=actor_kind, actor_id=actor_id,
    )
    return connection


def attach_vault_references(connection, *, credential_ref='', webhook_secret_ref='',
                            cursor_ref='', actor_id='operator'):
    from backend.integrations.safety import validate_opaque_reference

    connection.credential_ref = validate_opaque_reference(credential_ref)
    connection.webhook_secret_ref = validate_opaque_reference(webhook_secret_ref)
    connection.cursor_ref = validate_opaque_reference(cursor_ref)
    connection.version += 1
    _audit(
        connection.business_id, 'connection.vault_references_updated',
        connection_id=connection.id, actor_kind='operator', actor_id=actor_id,
        metadata={
            'credential_configured': bool(connection.credential_ref),
            'webhook_configured': bool(connection.webhook_secret_ref),
            'cursor_configured': bool(connection.cursor_ref),
        },
    )
    return connection


def disconnect_connection(connection, *, actor_kind='owner', actor_id=''):
    if connection.status == 'disconnected':
        connection.credential_ref = ''
        connection.webhook_secret_ref = ''
        connection.cursor_ref = ''
        connection.next_sync_at = None
        return connection
    connection.operator_reconnect_required = bool(
        connection.operator_reconnect_required
        or connection.credential_ref
        or connection.webhook_secret_ref
        or connection.cursor_ref
    )
    connection.status = 'disconnected'
    connection.health_status = 'disabled'
    connection.disconnected_at = utcnow()
    connection.next_sync_at = None
    connection.credential_ref = ''
    connection.webhook_secret_ref = ''
    connection.cursor_ref = ''
    connection.version += 1
    for run in BusinessIntegrationSyncRun.query.filter(
        BusinessIntegrationSyncRun.connection_id == connection.id,
        BusinessIntegrationSyncRun.status.in_(('queued', 'retry_scheduled')),
    ).with_for_update().all():
        run.status = 'cancelled'
        run.completed_at = utcnow()
        run.next_retry_at = None
    _audit(
        connection.business_id, 'connection.disconnected',
        connection_id=connection.id, actor_kind=actor_kind, actor_id=actor_id,
    )
    return connection


def reconnect_connection(connection, *, actor_kind='operator', actor_id=''):
    if connection.status != 'disconnected':
        return connection
    adapter = provider_registry.get(connection.provider_key)
    connection.status = (
        'connected'
        if adapter.descriptor.auth_mode == 'owner_push_or_signed_webhook'
        else 'draft'
    )
    connection.health_status = 'unknown'
    connection.disconnected_at = None
    connection.consecutive_failures = 0
    connection.last_error_code = ''
    connection.last_error_message = ''
    connection.operator_reconnect_required = False
    connection.next_sync_at = (
        utcnow() if connection.config_dict().get('source_url') else None
    )
    connection.version += 1
    _audit(
        connection.business_id, 'connection.reconnected',
        connection_id=connection.id, actor_kind=actor_kind, actor_id=actor_id,
    )
    return connection


def retry_delay_seconds(attempt, *, base=60, maximum=6 * 3600):
    return min(maximum, base * (2 ** max(0, int(attempt) - 1)))


def _occurrence_values(item):
    values = {
        'title': item.title,
        'kind': item.kind,
        'recurrence': item.recurrence,
        'start_date': item.start_date,
        'end_date': item.end_date,
        'event_date': item.event_date,
        'start_time': item.start_time,
        'end_time': item.end_time,
        'starts_at': item.starts_at,
        'ends_at': item.ends_at,
        'timezone': item.timezone,
        'capacity': item.capacity,
        'spots_remaining': item.spots_remaining,
        'status': item.status,
        'skill_level': item.skill_level,
        'location_note': item.location_note,
        'instructor': item.instructor,
        'price_text': item.price_text,
        'booking_url': item.booking_url,
        'source_updated_at': item.source_updated_at,
    }
    values['payload_hash'] = stable_digest(values)
    return values


def _apply_snapshot(connection, snapshot):
    now = utcnow()
    existing = {
        row.external_id: row
        for row in BusinessScheduleOccurrence.query.filter_by(
            connection_id=connection.id,
        ).with_for_update().all()
    }
    metrics = {
        'occurrences_created': 0,
        'occurrences_updated': 0,
        'occurrences_unchanged': 0,
        'occurrences_cancelled': 0,
        'conversions_created': 0,
        'conversions_duplicate': 0,
    }
    seen = set()
    for normalized in snapshot.occurrences:
        seen.add(normalized.external_id)
        values = _occurrence_values(normalized)
        row = existing.get(normalized.external_id)
        if row is None:
            row = BusinessScheduleOccurrence(
                business_id=connection.business_id,
                connection_id=connection.id,
                external_id=normalized.external_id,
                **values,
            )
            db.session.add(row)
            metrics['occurrences_created'] += 1
        elif row.payload_hash == values['payload_hash']:
            metrics['occurrences_unchanged'] += 1
        else:
            for key, value in values.items():
                setattr(row, key, value)
            metrics['occurrences_updated'] += 1
        row.synced_at = now
    if snapshot.authoritative:
        for external_id, row in existing.items():
            if external_id not in seen and row.status not in {'cancelled', 'completed'}:
                row.status = 'cancelled'
                row.synced_at = now
                row.payload_hash = stable_digest({
                    'prior': row.payload_hash, 'status': 'cancelled',
                    'source_version': snapshot.source_version,
                })
                metrics['occurrences_cancelled'] += 1

    by_external_id = {
        row.external_id: row
        for row in BusinessScheduleOccurrence.query.filter_by(
            connection_id=connection.id,
        ).all()
    }
    for conversion in snapshot.conversions:
        event_key = stable_digest(
            f'conversion:{connection.id}:{conversion.external_event_id}'
        )
        if BusinessBookingEvent.query.filter_by(event_key=event_key).first():
            metrics['conversions_duplicate'] += 1
            continue
        occurrence = by_external_id.get(conversion.occurrence_external_id)
        db.session.add(BusinessBookingEvent(
            business_id=connection.business_id,
            connection_id=connection.id,
            occurrence_id=occurrence.id if occurrence else None,
            event_type='conversion',
            event_key=event_key,
            external_event_id=conversion.external_event_id,
            action='booking',
            occurred_at=conversion.occurred_at,
            value_minor=conversion.value_minor,
            currency=conversion.currency,
            source=connection.provider_key,
        ))
        metrics['conversions_created'] += 1
    return metrics


def process_sync_run(run):
    connection = (
        BusinessProviderConnection.query.filter_by(id=run.connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .first()
    )
    if (
        connection is None
        or connection.status == 'disconnected'
        or not _business_integration_active(connection)
    ):
        run.status = 'cancelled'
        run.completed_at = utcnow()
        return run
    if run.status == 'succeeded':
        return run
    now = utcnow()
    run.status = 'running'
    run.attempt += 1
    run.started_at = now
    run.next_retry_at = None
    connection.last_sync_started_at = now
    try:
        adapter = provider_registry.get(connection.provider_key)
        payload = safe_json_loads(run.payload_json, None)
        if not isinstance(payload, dict):
            raise ValidationError('sync_payload_unavailable')
        with db.session.begin_nested():
            snapshot = adapter.normalize_snapshot(payload)
            validate_catalog_booking_scope(connection, snapshot)
            metrics = _apply_snapshot(connection, snapshot)
            db.session.flush()
        run.status = 'succeeded'
        run.completed_at = utcnow()
        run.source_version = snapshot.source_version
        run.reconciliation_hash = stable_digest({
            'source_version': snapshot.source_version,
            'occurrence_ids': [item.external_id for item in snapshot.occurrences],
        })
        run.metrics = safe_json_dumps(metrics)
        run.error_code = ''
        run.error_message = ''
        health_urls = adapter.health_urls(connection.config_dict())
        if not health_urls:
            connection.health_status = 'healthy'
        connection.status = (
            'connected'
            if connection.health_status in {'unknown', 'healthy'}
            else 'degraded'
        )
        connection.last_sync_succeeded_at = run.completed_at
        connection.consecutive_failures = 0
        connection.last_error_code = ''
        connection.last_error_message = ''
        connection.version += 1
        _audit(
            connection.business_id, 'sync.succeeded', connection_id=connection.id,
            metadata={'run_id': run.id, **metrics},
        )
    except Exception as error:
        retryable = not isinstance(error, IntegrationError) or error.retryable
        code = error.code if isinstance(error, IntegrationError) else 'sync_internal_error'
        message = error.safe_message if isinstance(error, IntegrationError) else code
        run.error_code = str(code)[:120]
        run.error_message = str(message)[:500]
        connection.last_sync_failed_at = utcnow()
        connection.consecutive_failures += 1
        connection.last_error_code = run.error_code
        connection.last_error_message = run.error_message
        connection.status = 'degraded' if connection.last_sync_succeeded_at else 'error'
        connection.health_status = 'degraded'
        if retryable and run.attempt < run.max_attempts:
            run.status = 'retry_scheduled'
            run.next_retry_at = utcnow() + timedelta(
                seconds=retry_delay_seconds(run.attempt),
            )
        else:
            run.status = 'failed'
            run.completed_at = utcnow()
        _audit(
            connection.business_id, 'sync.failed', connection_id=connection.id,
            metadata={'run_id': run.id, 'error_code': run.error_code, 'retry': run.status == 'retry_scheduled'},
        )
    return run


def submit_catalog_sync(connection, payload, *, trigger='owner_push', idempotency_key='',
                        actor_kind='owner', actor_id=''):
    if connection.status == 'disconnected':
        raise ValidationError('connection_disconnected')
    if not _business_integration_active(connection):
        raise ValidationError('business_integration_inactive')
    adapter = provider_registry.get(connection.provider_key)
    snapshot = adapter.normalize_snapshot(payload)
    validate_catalog_booking_scope(connection, snapshot)
    canonical = canonical_snapshot_payload(snapshot)
    canonical_json = safe_json_dumps(canonical)
    key = stable_digest(
        str(idempotency_key or '') or f'{connection.id}:{canonical_json}'
    )
    existing = BusinessIntegrationSyncRun.query.filter_by(
        connection_id=connection.id, idempotency_key=key,
    ).first()
    if existing:
        return existing, True
    run = BusinessIntegrationSyncRun(
        connection_id=connection.id,
        trigger=trigger,
        status='queued',
        idempotency_key=key,
        payload_json=canonical_json,
        source_version=snapshot.source_version,
    )
    db.session.add(run)
    db.session.flush()
    _audit(
        connection.business_id, 'sync.submitted', connection_id=connection.id,
        actor_kind=actor_kind, actor_id=actor_id,
        metadata={'run_id': run.id, 'trigger': trigger},
    )
    process_sync_run(run)
    return run, False


def _pull_interval():
    # The interval is intentionally bounded even if configuration is malformed.
    import os
    try:
        minutes = int(os.getenv('BUSINESS_CATALOG_PULL_INTERVAL_MINUTES', '1200'))
    except (TypeError, ValueError):
        minutes = 1200
    return timedelta(minutes=min(max(minutes, 15), 24 * 60))


def mark_pull_failure(connection, error):
    code = error.code if isinstance(error, IntegrationError) else 'catalog_pull_failed'
    now = utcnow()
    connection.last_pull_at = now
    connection.last_sync_failed_at = now
    connection.consecutive_failures += 1
    connection.last_error_code = str(code)[:120]
    connection.last_error_message = connection.last_error_code
    connection.status = 'degraded' if connection.last_sync_succeeded_at else 'error'
    connection.health_status = 'degraded'
    connection.next_sync_at = now + timedelta(
        seconds=retry_delay_seconds(connection.consecutive_failures),
    )
    connection.version += 1
    _audit(
        connection.business_id, 'catalog_pull.failed',
        connection_id=connection.id,
        metadata={'error_code': connection.last_error_code},
    )


def pull_connection_catalog(connection, *, transport=None, resolver=None,
                            pull_timeout=8):
    """Conditionally fetch, validate, and reconcile a configured JSON feed."""
    if connection.status == 'disconnected':
        raise ValidationError('connection_disconnected')
    if not _business_integration_active(connection):
        raise ValidationError('business_integration_inactive')
    adapter = provider_registry.get(connection.provider_key)
    if not adapter.descriptor.supports_pull:
        raise ValidationError('provider_pull_not_supported')
    source_url = connection.config_dict().get('source_url')
    if not source_url:
        raise ValidationError('catalog_source_url_required')
    options = {'transport': transport} if transport is not None else {}
    if resolver is not None:
        options['resolver'] = resolver
    try:
        result = pull_json_catalog(
            source_url,
            etag=connection.pull_etag,
            last_modified=connection.pull_last_modified,
            timeout=pull_timeout,
            **options,
        )
        now = utcnow()
        connection.last_pull_at = now
        connection.pull_etag = result.etag
        connection.pull_last_modified = result.last_modified
        connection.next_sync_at = now + _pull_interval()
        if result.not_modified:
            if connection.status != 'disconnected':
                connection.status = (
                    'connected'
                    if connection.health_status in {'unknown', 'healthy'}
                    else 'degraded'
                )
            connection.last_error_code = ''
            connection.last_error_message = ''
            connection.consecutive_failures = 0
            connection.version += 1
            _audit(
                connection.business_id, 'catalog_pull.not_modified',
                connection_id=connection.id,
            )
            return None, True, True
        run, duplicate = submit_catalog_sync(
            connection,
            result.payload,
            trigger='scheduled',
            idempotency_key=f'pull:{stable_digest(result.payload)}',
            actor_kind='provider',
            actor_id='catalog_pull',
        )
        _audit(
            connection.business_id, 'catalog_pull.processed',
            connection_id=connection.id,
            metadata={'run_id': run.id, 'duplicate': duplicate},
        )
        return run, duplicate, False
    except IntegrationError as error:
        mark_pull_failure(connection, error)
        raise


def recheck_connection_links(connection, *, actor_kind='owner', actor_id='',
                             transport=None, resolver=None, probe_timeout=5):
    if not _business_integration_active(connection):
        raise ValidationError('business_integration_inactive')
    adapter = provider_registry.get(connection.provider_key)
    urls = adapter.health_urls(connection.config_dict())
    now = utcnow()
    results = []
    for kind, url in urls[:10]:
        options = {'transport': transport, 'timeout': probe_timeout}
        if resolver is not None:
            options['resolver'] = resolver
        result = probe_https_url(url, **options)
        if (
            kind == 'booking'
            and result.status == 'healthy'
            and result.final_url
            and not _url_within_base(url, result.final_url)
        ):
            result = LinkProbeResult(
                status='unsafe',
                http_status=result.http_status,
                latency_ms=result.latency_ms,
                error_code='booking_redirect_outside_approved_base',
                final_url=result.final_url,
            )
        row = BusinessLinkHealthCheck(
            business_id=connection.business_id,
            connection_id=connection.id,
            link_kind=kind,
            url_hash=stable_digest(url),
            final_url_hash=stable_digest(result.final_url) if result.final_url else '',
            status=result.status,
            http_status=result.http_status,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            checked_at=now,
            next_check_at=(
                now + link_health_recheck_window()
                if result.status == 'healthy' else now + timedelta(hours=1)
            ),
        )
        db.session.add(row)
        results.append(row)
    statuses = {item.status for item in results}
    if not results:
        # A push-only schedule with no public destinations has no external link
        # surface to probe, so its link-health requirement is vacuously met.
        connection.health_status = 'healthy'
    elif statuses == {'healthy'}:
        connection.health_status = 'healthy'
    elif 'unsafe' in statuses:
        connection.health_status = 'unsafe'
    elif 'unreachable' in statuses:
        connection.health_status = 'unreachable'
    else:
        connection.health_status = 'degraded'
    if connection.health_status != 'healthy' and connection.status == 'connected':
        connection.status = 'degraded'
    elif connection.health_status == 'healthy' and connection.status != 'disconnected':
        connection.status = 'connected'
    connection.last_health_checked_at = now
    connection.version += 1
    _audit(
        connection.business_id, 'connection.health_checked',
        connection_id=connection.id, actor_kind=actor_kind, actor_id=actor_id,
        metadata={'health_status': connection.health_status, 'links_checked': len(results)},
    )
    return results


def recheck_business_profile_links(business, *, actor_kind='operator', actor_id='',
                                   transport=None, resolver=None, probe_timeout=5):
    """Probe primary listing links without persisting or logging their values."""
    candidates = (
        ('website', business.website_url),
        ('booking', business.booking_url),
        ('membership', business.membership_url),
    )
    now = utcnow()
    results = []
    for kind, url in candidates:
        if not url:
            continue
        options = {'transport': transport, 'timeout': probe_timeout}
        if resolver is not None:
            options['resolver'] = resolver
        result = probe_https_url(url, **options)
        row = BusinessLinkHealthCheck(
            business_id=business.id,
            connection_id=None,
            link_kind=f'profile_{kind}',
            url_hash=stable_digest(url),
            final_url_hash=stable_digest(result.final_url) if result.final_url else '',
            status=result.status,
            http_status=result.http_status,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            checked_at=now,
            next_check_at=(
                now + link_health_recheck_window()
                if result.status == 'healthy' else now + timedelta(hours=1)
            ),
        )
        db.session.add(row)
        results.append(row)
    _audit(
        business.id, 'profile_links.health_checked',
        actor_kind=actor_kind, actor_id=actor_id,
        metadata={
            'links_checked': len(results),
            'problems': sum(item.status != 'healthy' for item in results),
        },
    )
    return results


def mark_connection_health_failure(connection, error_code='link_health_check_failed'):
    """Persist a bounded retry point for an unexpected health-check failure."""
    now = utcnow()
    connection.last_health_checked_at = now
    connection.health_status = 'degraded'
    if connection.status == 'connected':
        connection.status = 'degraded'
    connection.last_error_code = str(error_code or 'link_health_check_failed')[:120]
    connection.last_error_message = connection.last_error_code
    connection.version += 1
    _audit(
        connection.business_id, 'connection.health_failed',
        connection_id=connection.id, actor_kind='cron',
        actor_id='business-integrations',
        metadata={'error_code': connection.last_error_code},
    )
    return connection


def record_booking_click(*, business_id, connection_id=None, occurrence_id=None,
                         client_event_id, action='booking', occurred_at=None):
    key = stable_digest(f'click:{business_id}:{client_event_id}')
    existing = BusinessBookingEvent.query.filter_by(event_key=key).first()
    if existing:
        return existing, True
    item = BusinessBookingEvent(
        business_id=business_id,
        connection_id=connection_id,
        occurrence_id=occurrence_id,
        event_type='click',
        event_key=key,
        external_event_id='',
        action=str(action or 'booking')[:40],
        occurred_at=occurred_at or utcnow(),
        source='third_shot',
    )
    db.session.add(item)
    db.session.flush()
    return item, False


def due_sync_runs(*, limit=10, now=None, exclude_ids=()):
    now = now or utcnow()
    query = BusinessIntegrationSyncRun.query.join(
        BusinessProviderConnection,
        BusinessIntegrationSyncRun.connection_id == BusinessProviderConnection.id,
    ).join(
        BusinessProfile,
        BusinessProviderConnection.business_id == BusinessProfile.id,
    ).filter(
        BusinessIntegrationSyncRun.status == 'retry_scheduled',
        BusinessIntegrationSyncRun.next_retry_at <= now,
        BusinessProfile.claim_status == 'verified',
        BusinessProfile.verified_at.is_not(None),
        BusinessProfile.governance_status == 'active',
    ).order_by(BusinessIntegrationSyncRun.next_retry_at, BusinessIntegrationSyncRun.id)
    if exclude_ids:
        query = query.filter(
            BusinessIntegrationSyncRun.id.notin_(tuple(exclude_ids)),
        )
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    else:
        query = query.with_for_update()
    return query.limit(min(max(int(limit), 1), 25)).all()


def stale_connections(*, limit=10, now=None, stale_hours=None, exclude_ids=()):
    now = now or utcnow()
    stale_after = (
        link_health_recheck_window()
        if stale_hours is None else timedelta(hours=stale_hours)
    )
    cutoff = now - stale_after
    query = BusinessProviderConnection.query.join(
        BusinessProfile,
        BusinessProviderConnection.business_id == BusinessProfile.id,
    ).filter(
        BusinessProviderConnection.status.in_(('connected', 'degraded', 'error')),
        BusinessProfile.claim_status == 'verified',
        BusinessProfile.verified_at.is_not(None),
        BusinessProfile.governance_status == 'active',
        db.or_(
            BusinessProviderConnection.last_health_checked_at.is_(None),
            BusinessProviderConnection.last_health_checked_at <= cutoff,
        ),
    ).order_by(
        case(
            (BusinessProviderConnection.last_health_checked_at.is_(None), 0),
            else_=1,
        ),
        BusinessProviderConnection.last_health_checked_at.asc(),
        BusinessProviderConnection.id,
    )
    if exclude_ids:
        query = query.filter(
            BusinessProviderConnection.id.notin_(tuple(exclude_ids)),
        )
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    else:
        query = query.with_for_update()
    return query.limit(min(max(int(limit), 1), 25)).all()


def due_pull_connections(*, limit=10, now=None, exclude_ids=()):
    now = now or utcnow()
    query = BusinessProviderConnection.query.join(
        BusinessProfile,
        BusinessProviderConnection.business_id == BusinessProfile.id,
    ).filter(
        BusinessProviderConnection.provider_key == 'link_catalog',
        BusinessProviderConnection.status.in_(('connected', 'degraded', 'error')),
        BusinessProfile.claim_status == 'verified',
        BusinessProfile.verified_at.is_not(None),
        BusinessProfile.governance_status == 'active',
        BusinessProviderConnection.next_sync_at.is_not(None),
        BusinessProviderConnection.next_sync_at <= now,
    ).order_by(
        BusinessProviderConnection.next_sync_at,
        BusinessProviderConnection.id,
    )
    if exclude_ids:
        query = query.filter(
            BusinessProviderConnection.id.notin_(tuple(exclude_ids)),
        )
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    else:
        query = query.with_for_update()
    return query.limit(min(max(int(limit), 1), 25)).all()


def stale_business_profiles(*, limit=10, now=None, stale_hours=None,
                            exclude_ids=()):
    now = now or utcnow()
    stale_after = (
        link_health_recheck_window()
        if stale_hours is None else timedelta(hours=stale_hours)
    )
    cutoff = now - stale_after
    last_check = select(func.max(BusinessLinkHealthCheck.checked_at)).where(
        BusinessLinkHealthCheck.business_id == BusinessProfile.id,
        BusinessLinkHealthCheck.connection_id.is_(None),
    ).correlate(BusinessProfile).scalar_subquery()
    query = BusinessProfile.query.filter(
        BusinessProfile.claim_status == 'verified',
        BusinessProfile.verified_at.is_not(None),
        BusinessProfile.governance_status == 'active',
        BusinessProfile.content_review_status == 'approved',
        db.or_(
            BusinessProfile.website_url != '',
            BusinessProfile.booking_url != '',
            BusinessProfile.membership_url != '',
        ),
        db.or_(last_check.is_(None), last_check <= cutoff),
    ).order_by(
        case((last_check.is_(None), 0), else_=1),
        last_check.asc(),
        BusinessProfile.id,
    )
    if exclude_ids:
        query = query.filter(BusinessProfile.id.notin_(tuple(exclude_ids)))
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    else:
        query = query.with_for_update()
    return query.limit(min(max(int(limit), 1), 25)).all()
