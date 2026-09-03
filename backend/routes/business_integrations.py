"""Provider connections, structured sync, webhooks, health, and analytics."""
from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta
import hmac
import json
import os
import time

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from backend.app import db
from backend.models import BusinessProfile, utcnow
from backend.routes.auth import login_required
from backend.services.business_governance import (
    BusinessGovernanceError,
    business_access_role,
    require_business_role,
)
from backend.services.business_visibility import public_business_query
from backend.security import rate_limit
from backend.integrations import provider_registry
from backend.integrations.errors import IntegrationError, ProviderNotAvailable
from backend.integrations.models import (
    BusinessBookingEvent,
    BusinessIntegrationSyncRun,
    BusinessLinkHealthCheck,
    BusinessProviderConnection,
    BusinessScheduleOccurrence,
    BusinessWebhookReceipt,
)
from backend.integrations.safety import stable_digest
from backend.integrations.services import (
    attach_vault_references,
    connection_publication_ready,
    create_connection,
    disconnect_connection,
    due_pull_connections,
    due_sync_runs,
    mark_connection_health_failure,
    mark_pull_failure,
    pull_connection_catalog,
    process_sync_run,
    public_occurrence_payload,
    record_booking_click,
    recheck_business_profile_links,
    recheck_connection_links,
    reconnect_connection,
    stale_business_profiles,
    stale_connections,
    submit_catalog_sync,
    update_connection_config,
)
from backend.integrations.vault import configured_vault
from backend.integrations.webhooks import (
    verify_signature,
    webhook_idempotency_key,
)
from backend.services.mfa import MFAError, verify_user_mfa


business_integrations_bp = Blueprint('business_integrations', __name__)
business_integration_cron_bp = Blueprint('business_integration_cron', __name__)


def _log_integration_event(event, started, *, connection_id=None, run_id=None,
                           error_code='', **aggregates):
    record = {
        'route': request.path,
        'event': event,
        'request_id': (
            request.headers.get('X-Vercel-Id')
            or request.headers.get('X-Vercel-Request-Id')
            or ''
        ),
        'duration_ms': max(0, round((time.monotonic() - started) * 1000)),
        'connection_id': connection_id,
        'run_id': run_id,
        'error_code': str(error_code or '')[:120],
        **{
            key: value for key, value in aggregates.items()
            if isinstance(value, (bool, int, float, type(None)))
        },
    }
    current_app.logger.info(
        'BUSINESS_INTEGRATION %s',
        json.dumps(record, sort_keys=True, separators=(',', ':')),
    )


def _object_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise IntegrationError('json_object_required')
    return payload


def _error(error, status=400):
    code = error.code if isinstance(error, IntegrationError) else str(error)
    return jsonify({'error': code}), status


def _integrity_error_identity(error):
    original = getattr(error, 'orig', None)
    diagnostics = getattr(original, 'diag', None)
    constraint = str(
        getattr(diagnostics, 'constraint_name', '') or ''
    ).strip()
    sqlstate = str(
        getattr(original, 'sqlstate', '')
        or getattr(original, 'pgcode', '')
        or ''
    ).strip()
    return constraint, sqlstate


def _is_duplicate_provider_connection(error):
    constraint, _sqlstate = _integrity_error_identity(error)
    if constraint == 'uq_business_provider_connection':
        return True
    # SQLite exposes the column tuple rather than the named constraint. Keep
    # this exact so unrelated persistence failures are never misreported as a
    # harmless duplicate connection.
    message = str(getattr(error, 'orig', '') or '').lower()
    return (
        'unique constraint failed: '
        'business_provider_connection.business_id, '
        'business_provider_connection.provider_key'
    ) in message


def _owned_business(business_id, *, lock=True, verified=False,
                    allowed_roles=('owner', 'admin', 'editor', 'viewer')):
    query = BusinessProfile.query.filter_by(id=business_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    business = query.first()
    if not business:
        return None, (jsonify({'error': 'business_not_found'}), 404)
    try:
        require_business_role(business, g.current_user.id, allowed_roles)
    except BusinessGovernanceError as exc:
        return None, (jsonify({'error': str(exc)}), 403)
    if verified and not (
        business.claim_status == 'verified' and business.verified_at is not None
    ):
        return None, (jsonify({'error': 'verified_business_required'}), 409)
    return business, None


def _owned_connection(business_id, connection_id, *, verified=False,
                      allowed_roles=('owner', 'admin', 'editor', 'viewer')):
    business, error = _owned_business(
        business_id, verified=verified, allowed_roles=allowed_roles,
    )
    if error:
        return None, None, error
    connection = (
        BusinessProviderConnection.query.filter_by(
            id=connection_id, business_id=business.id,
        ).with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection:
        return business, None, (jsonify({'error': 'connection_not_found'}), 404)
    return business, connection, None


def _public_business(business_id):
    return public_business_query().filter(BusinessProfile.id == business_id).first()


def _operator_role(*allowed):
    role = str(getattr(g.current_user, 'operator_role', '') or '')
    return role if role in set(allowed) else None


def _require_fresh_operator_mfa(payload):
    """Require a TOTP supplied for this exact privileged request."""
    code = str(payload.get('mfa_code') or '').strip()
    try:
        valid, _ = verify_user_mfa(
            g.current_user, code, allow_recovery=False,
        )
    except MFAError:
        valid = False
    if not valid:
        raise IntegrationError('operator_mfa_required')


def _require_https_for_secret_write():
    if current_app.config.get('APP_ENV') not in {'production', 'staging'}:
        return
    forwarded_https = (
        os.getenv('VERCEL') == '1'
        and str(request.headers.get('X-Forwarded-Proto') or '').lower() == 'https'
    )
    if not request.is_secure and not forwarded_https:
        raise IntegrationError('https_required')


def _delete_connection_vault_secrets(connection):
    vault = configured_vault()
    for reference in (
        connection.credential_ref,
        connection.webhook_secret_ref,
        connection.cursor_ref,
    ):
        if reference.startswith('vault://'):
            vault.delete(reference)


def _connection_payload(connection, *, details=False):
    data = connection.to_owner_dict()
    data['publication_ready'] = connection_publication_ready(connection)
    if details:
        data['recent_sync_runs'] = [
            item.to_dict()
            for item in BusinessIntegrationSyncRun.query.filter_by(
                connection_id=connection.id,
            ).order_by(BusinessIntegrationSyncRun.id.desc()).limit(10).all()
        ]
        data['recent_link_checks'] = [
            item.to_dict()
            for item in BusinessLinkHealthCheck.query.filter_by(
                connection_id=connection.id,
            ).order_by(BusinessLinkHealthCheck.id.desc()).limit(20).all()
        ]
    return data


def _link_check_is_current(check):
    if check.connection_id:
        connection = check.connection
        if not connection or connection.status == 'disconnected':
            return False
        try:
            current = dict(
                provider_registry.get(connection.provider_key).health_urls(
                    connection.config_dict(),
                )
            ).get(check.link_kind, '')
        except IntegrationError:
            return False
        return bool(current and stable_digest(current) == check.url_hash)
    field = {
        'profile_website': 'website_url',
        'profile_booking': 'booking_url',
        'profile_membership': 'membership_url',
    }.get(check.link_kind)
    business = check.business
    current = getattr(business, field, '') if business and field else ''
    return bool(current and stable_digest(current) == check.url_hash)


@business_integrations_bp.get('/business-integrations/providers')
def list_integration_providers():
    return jsonify({
        'items': [item.to_dict() for item in provider_registry.descriptors()],
        'truth_policy': (
            'Only providers with availability=active have executable adapters. '
            'Unavailable vendor names are not partnership or API-support claims.'
        ),
    })


@business_integrations_bp.get('/businesses/<int:business_id>/connections')
@login_required
def list_business_connections(business_id):
    business, error = _owned_business(business_id, lock=False)
    if error:
        return error
    items = BusinessProviderConnection.query.filter_by(
        business_id=business.id,
    ).order_by(BusinessProviderConnection.id).all()
    return jsonify({'items': [_connection_payload(item, details=True) for item in items]})


@business_integrations_bp.post('/businesses/<int:business_id>/connections')
@rate_limit(10, 86400)
@login_required
def connect_business_provider(business_id):
    business, error = _owned_business(
        business_id, verified=True, allowed_roles=('owner', 'admin'),
    )
    if error:
        return error
    actor_kind = business_access_role(business, g.current_user.id)
    try:
        payload = _object_payload()
        connection = create_connection(
            business=business,
            user_id=g.current_user.id,
            provider_key=payload.get('provider_key'),
            display_name=payload.get('display_name'),
            config=payload.get('config'),
            actor_kind=actor_kind,
        )
        db.session.commit()
    except ProviderNotAvailable as exc:
        db.session.rollback()
        return _error(exc, 409)
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc)
    except IntegrityError as exc:
        db.session.rollback()
        if _is_duplicate_provider_connection(exc):
            return jsonify({'error': 'provider_already_connected'}), 409
        constraint, sqlstate = _integrity_error_identity(exc)
        current_app.logger.error(
            'BUSINESS_INTEGRATION connection_create_persistence_failed '
            'constraint=%s sqlstate=%s',
            constraint or 'unknown',
            sqlstate or 'unknown',
        )
        return jsonify({'error': 'connection_create_failed'}), 500
    return jsonify({'connection': _connection_payload(connection, details=True)}), 201


@business_integrations_bp.patch(
    '/businesses/<int:business_id>/connections/<int:connection_id>'
)
@rate_limit(30, 3600)
@login_required
def edit_business_connection(business_id, connection_id):
    business, connection, error = _owned_connection(
        business_id, connection_id, verified=True,
        allowed_roles=('owner', 'admin'),
    )
    if error:
        return error
    actor_kind = business_access_role(business, g.current_user.id)
    try:
        payload = _object_payload()
        if set(payload) - {'display_name', 'config'}:
            raise IntegrationError('unsupported_connection_field')
        if 'display_name' in payload:
            name = str(payload.get('display_name') or '').strip()
            if not name or len(name) > 120:
                raise IntegrationError('invalid_connection_display_name')
            connection.display_name = name
        if 'config' in payload:
            update_connection_config(
                connection, payload.get('config'), actor_kind=actor_kind,
                actor_id=g.current_user.id,
            )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc)
    return jsonify({'connection': _connection_payload(connection, details=True)})


@business_integrations_bp.put(
    '/businesses/<int:business_id>/connections/<int:connection_id>/catalog'
)
@rate_limit(30, 3600)
@login_required
def push_business_catalog(business_id, connection_id):
    started = time.monotonic()
    business, connection, error = _owned_connection(
        business_id, connection_id, verified=True,
        allowed_roles=('owner', 'admin', 'editor'),
    )
    if error:
        return error
    actor_kind = business_access_role(business, g.current_user.id)
    try:
        payload = _object_payload()
        run, duplicate = submit_catalog_sync(
            connection,
            payload,
            trigger='owner_push',
            idempotency_key=request.headers.get('Idempotency-Key', ''),
            actor_kind=actor_kind,
            actor_id=g.current_user.id,
        )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        _log_integration_event(
            'sync_rejected', started, connection_id=connection_id,
            error_code=exc.code,
        )
        return _error(exc)
    _log_integration_event(
        'sync_processed', started, connection_id=connection.id, run_id=run.id,
        error_code=run.error_code, duplicate=duplicate,
    )
    return jsonify({
        'run': run.to_dict(),
        'duplicate': duplicate,
        'connection': _connection_payload(connection),
    }), (200 if duplicate else 202)


@business_integrations_bp.post(
    '/businesses/<int:business_id>/connections/<int:connection_id>/recheck'
)
@rate_limit(20, 3600)
@login_required
def recheck_business_connection(business_id, connection_id):
    business, connection, error = _owned_connection(
        business_id, connection_id, verified=True,
        allowed_roles=('owner', 'admin', 'editor'),
    )
    if error:
        return error
    actor_kind = business_access_role(business, g.current_user.id)
    try:
        checks = recheck_connection_links(
            connection, actor_kind=actor_kind, actor_id=g.current_user.id,
        )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc)
    return jsonify({
        'connection': _connection_payload(connection),
        'checks': [item.to_dict() for item in checks],
    })


@business_integrations_bp.delete(
    '/businesses/<int:business_id>/connections/<int:connection_id>'
)
@rate_limit(10, 3600)
@login_required
def disconnect_business_provider(business_id, connection_id):
    business, connection, error = _owned_connection(
        business_id, connection_id, allowed_roles=('owner', 'admin'),
    )
    if error:
        return error
    actor_kind = business_access_role(business, g.current_user.id)
    try:
        _delete_connection_vault_secrets(connection)
        disconnect_connection(
            connection, actor_kind=actor_kind, actor_id=g.current_user.id,
        )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc, 503 if 'vault' in exc.code else 400)
    return jsonify({'connection': _connection_payload(connection)})


@business_integrations_bp.post(
    '/businesses/<int:business_id>/connections/<int:connection_id>/reconnect'
)
@rate_limit(10, 3600)
@login_required
def reconnect_business_provider(business_id, connection_id):
    business, connection, error = _owned_connection(
        business_id, connection_id, verified=True,
        allowed_roles=('owner', 'admin'),
    )
    if error:
        return error
    actor_kind = business_access_role(business, g.current_user.id)
    if connection.status != 'disconnected':
        return jsonify({'error': 'connection_not_disconnected'}), 409
    if connection.provider_key != 'link_catalog':
        return jsonify({'error': 'operator_reconnect_required'}), 403
    adapter = provider_registry.get('link_catalog')
    if (
        adapter.descriptor.auth_mode != 'owner_push_or_signed_webhook'
        or connection.operator_reconnect_required
        or connection.credential_ref
        or connection.webhook_secret_ref
        or connection.cursor_ref
    ):
        return jsonify({'error': 'operator_reconnect_required'}), 403
    reconnect_connection(
        connection,
        actor_kind=actor_kind,
        actor_id=g.current_user.id,
    )
    db.session.commit()
    return jsonify({'connection': _connection_payload(connection, details=True)})


@business_integrations_bp.get('/businesses/<int:business_id>/integrated-schedule')
def integrated_business_schedule(business_id):
    business = _public_business(business_id)
    if not business:
        return jsonify({'error': 'business_not_found'}), 404
    query = BusinessScheduleOccurrence.query.join(
        BusinessProviderConnection,
        BusinessScheduleOccurrence.connection_id == BusinessProviderConnection.id,
    ).filter(
        BusinessScheduleOccurrence.business_id == business.id,
        BusinessProviderConnection.status != 'disconnected',
    )
    requested_dates = {}
    for parameter in ('from', 'to'):
        raw = str(request.args.get(parameter) or '').strip()
        if not raw:
            continue
        try:
            requested_dates[parameter] = date.fromisoformat(raw)
        except ValueError:
            return jsonify({'error': f'invalid_{parameter}_date'}), 400
    if (
        requested_dates.get('from')
        and requested_dates.get('to')
        and requested_dates['from'] > requested_dates['to']
    ):
        return jsonify({'error': 'invalid_schedule_range'}), 400
    if requested_dates:
        lower_date = requested_dates.get('from')
        upper_date = requested_dates.get('to')
        dated_conditions = [BusinessScheduleOccurrence.event_date.is_not(None)]
        timestamp_conditions = [
            BusinessScheduleOccurrence.event_date.is_(None),
            BusinessScheduleOccurrence.starts_at.is_not(None),
            BusinessScheduleOccurrence.ends_at.is_not(None),
        ]
        recurring_conditions = [
            BusinessScheduleOccurrence.recurrence != '',
            BusinessScheduleOccurrence.start_date.is_not(None),
        ]
        if lower_date:
            lower_utc = datetime.combine(lower_date, datetime_time.min)
            dated_conditions.append(BusinessScheduleOccurrence.event_date >= lower_date)
            timestamp_conditions.append(BusinessScheduleOccurrence.ends_at > lower_utc)
            recurring_conditions.append(or_(
                BusinessScheduleOccurrence.end_date.is_(None),
                BusinessScheduleOccurrence.end_date >= lower_date,
            ))
        if upper_date:
            upper_utc_exclusive = datetime.combine(
                upper_date + timedelta(days=1), datetime_time.min,
            )
            dated_conditions.append(BusinessScheduleOccurrence.event_date <= upper_date)
            timestamp_conditions.append(
                BusinessScheduleOccurrence.starts_at < upper_utc_exclusive
            )
            recurring_conditions.append(
                BusinessScheduleOccurrence.start_date <= upper_date
            )
        query = query.filter(or_(
            and_(*dated_conditions),
            and_(*timestamp_conditions),
            and_(*recurring_conditions),
        ))
    items = query.order_by(
        BusinessScheduleOccurrence.event_date,
        BusinessScheduleOccurrence.start_time,
        BusinessScheduleOccurrence.id,
    ).limit(250).all()
    connection_ids = sorted({item.connection_id for item in items})
    connections = {
        item.id: item
        for item in BusinessProviderConnection.query.filter(
            BusinessProviderConnection.id.in_(connection_ids),
        ).all()
    } if connection_ids else {}
    connections = {
        connection_id: connection
        for connection_id, connection in connections.items()
        if connection_publication_ready(connection)
    }
    items = [item for item in items if item.connection_id in connections]
    return jsonify({
        'items': [
            public_occurrence_payload(item, connections[item.connection_id])
            for item in items
        ],
        'sources': [{
            'connection_id': item.id,
            'provider_key': item.provider_key,
            'display_name': item.display_name,
            'status': item.status,
            'last_sync_succeeded_at': (
                item.last_sync_succeeded_at.isoformat() + 'Z'
                if item.last_sync_succeeded_at else None
            ),
        } for item in connections.values()],
    })


@business_integrations_bp.post('/businesses/<int:business_id>/booking-clicks')
@business_integrations_bp.post('/businesses/<int:business_id>/events')
@rate_limit(60, 3600)
def create_booking_click(business_id):
    business = _public_business(business_id)
    if not business:
        return jsonify({'error': 'business_not_found'}), 404
    try:
        payload = _object_payload()
        client_event_id = str(payload.get('client_event_id') or '').strip()
        if not client_event_id or len(client_event_id) > 160:
            raise IntegrationError('client_event_id_required')
        action = str(payload.get('action') or 'booking').strip().lower()
        if action not in {
            'profile_view', 'website', 'contact', 'schedule', 'booking',
            'lesson', 'membership', 'event', 'open_play',
        }:
            raise IntegrationError('invalid_booking_action')
        connection_id = payload.get('connection_id')
        occurrence_id = payload.get('occurrence_id')
        connection = None
        occurrence = None
        if connection_id not in (None, ''):
            connection = BusinessProviderConnection.query.filter_by(
                id=int(connection_id), business_id=business.id,
            ).first()
            if not connection or not connection_publication_ready(connection):
                raise IntegrationError('connection_not_found')
        if occurrence_id not in (None, ''):
            occurrence = BusinessScheduleOccurrence.query.filter_by(
                id=int(occurrence_id), business_id=business.id,
            ).first()
            if not occurrence or (connection and occurrence.connection_id != connection.id):
                raise IntegrationError('occurrence_not_found')
            if connection is None:
                connection = db.session.get(
                    BusinessProviderConnection, occurrence.connection_id,
                )
            if not connection or not connection_publication_ready(connection):
                raise IntegrationError('occurrence_not_found')
            if occurrence.booking_url and not public_occurrence_payload(
                occurrence, connection,
            ).get('booking_available'):
                raise IntegrationError('occurrence_not_found')
        item, duplicate = record_booking_click(
            business_id=business.id,
            connection_id=connection.id if connection else None,
            occurrence_id=occurrence.id if occurrence else None,
            client_event_id=client_event_id,
            action=action,
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrationError) as exc:
        db.session.rollback()
        return _error(exc if isinstance(exc, IntegrationError) else IntegrationError('invalid_booking_event'))
    return jsonify({'recorded': True, 'duplicate': duplicate, 'event_id': item.id}), (200 if duplicate else 201)


@business_integrations_bp.get('/businesses/<int:business_id>/analytics')
@login_required
def business_integration_analytics(business_id):
    business, error = _owned_business(business_id, lock=False)
    if error:
        return error
    raw_range = str(request.args.get('range') or '30d').lower()
    days = {'7d': 7, '30d': 30, '90d': 90}.get(raw_range)
    if days is None:
        return jsonify({'error': 'invalid_analytics_range'}), 400
    since = utcnow() - timedelta(days=days)
    rows = db.session.query(
        BusinessBookingEvent.event_type,
        func.count(BusinessBookingEvent.id),
    ).filter(
        BusinessBookingEvent.business_id == business.id,
        BusinessBookingEvent.occurred_at >= since,
    ).group_by(BusinessBookingEvent.event_type).all()
    by_type = {kind: int(count) for kind, count in rows}
    action_rows = db.session.query(
        BusinessBookingEvent.action, func.count(BusinessBookingEvent.id),
    ).filter(
        BusinessBookingEvent.business_id == business.id,
        BusinessBookingEvent.event_type == 'click',
        BusinessBookingEvent.occurred_at >= since,
    ).group_by(BusinessBookingEvent.action).all()
    actions = {action: count for action, count in action_rows}
    profile_views = actions.get('profile_view', 0)
    booking_clicks = sum(actions.get(action, 0) for action in (
        'booking', 'membership', 'event', 'open_play',
    ))
    lesson_clicks = actions.get('lesson', 0)
    schedule_opens = actions.get('schedule', 0)
    contact_clicks = actions.get('contact', 0)
    website_clicks = actions.get('website', 0)
    conversions = by_type.get('conversion', 0)
    value_rows = db.session.query(
        BusinessBookingEvent.currency,
        func.sum(BusinessBookingEvent.value_minor),
    ).filter(
        BusinessBookingEvent.business_id == business.id,
        BusinessBookingEvent.event_type == 'conversion',
        BusinessBookingEvent.occurred_at >= since,
        BusinessBookingEvent.currency != '',
        BusinessBookingEvent.value_minor.is_not(None),
    ).group_by(BusinessBookingEvent.currency).all()
    conversion_value_by_currency = {
        currency: int(value) for currency, value in value_rows
    }
    return jsonify({
        'range': raw_range,
        'since': since.isoformat() + 'Z',
        'profile_views': profile_views,
        'booking_clicks': booking_clicks,
        'lesson_clicks': lesson_clicks,
        'schedule_opens': schedule_opens,
        'contact_clicks': contact_clicks,
        'website_clicks': website_clicks,
        'conversions': conversions,
        'conversion_rate': round(conversions / booking_clicks, 4) if booking_clicks else None,
        'conversion_value_by_currency': conversion_value_by_currency,
        'privacy': 'Aggregates contain no player identity or raw destination URLs.',
    })


@business_integrations_bp.post(
    '/business-integrations/webhooks/<provider_key>/<connection_public_id>'
)
@rate_limit(120, 3600)
def provider_webhook(provider_key, connection_public_id):
    started = time.monotonic()
    connection = (
        BusinessProviderConnection.query.filter_by(
            public_id=connection_public_id, provider_key=provider_key,
        ).with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection or connection.status == 'disconnected':
        _log_integration_event('webhook_not_found', started, error_code='webhook_not_found')
        return jsonify({'error': 'webhook_not_found'}), 404
    if not connection.webhook_secret_ref:
        _log_integration_event(
            'webhook_rejected', started, connection_id=connection.id,
            error_code='webhook_not_configured',
        )
        return jsonify({'error': 'webhook_not_configured'}), 409
    raw = request.get_data(cache=True)
    try:
        secret = configured_vault().resolve(connection.webhook_secret_ref)
        verified = verify_signature(
            secret,
            raw,
            request.headers.get('X-Third-Shot-Signature'),
        )
        event_id = str(request.headers.get('X-Provider-Event-Id') or '').strip()[:160]
        idempotency = webhook_idempotency_key(
            provider_key, connection_public_id, event_id, raw,
        )
        existing = BusinessWebhookReceipt.query.filter_by(
            connection_id=connection.id, idempotency_key=idempotency,
        ).first()
        if existing:
            if existing.payload_digest != verified.payload_digest:
                raise IntegrationError('webhook_event_id_reused')
            _log_integration_event(
                'webhook_duplicate', started, connection_id=connection.id,
            )
            return jsonify({'accepted': True, 'duplicate': True}), 200
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise IntegrationError('json_object_required')
        receipt = BusinessWebhookReceipt(
            connection_id=connection.id,
            provider_event_id=event_id,
            idempotency_key=idempotency,
            signature_digest=verified.signature_digest,
            payload_digest=verified.payload_digest,
            status='received',
        )
        db.session.add(receipt)
        db.session.flush()
        run, duplicate = submit_catalog_sync(
            connection,
            payload,
            trigger='webhook',
            idempotency_key=idempotency,
            actor_kind='provider',
            actor_id=provider_key,
        )
        receipt.status = 'processed' if run.status in {'succeeded', 'retry_scheduled'} else 'failed'
        receipt.processed_at = utcnow()
        receipt.error_code = run.error_code
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        _log_integration_event(
            'webhook_rejected', started, connection_id=connection.id,
            error_code=exc.code,
        )
        status = 409 if exc.code == 'webhook_event_id_reused' else (
            401 if 'webhook' in exc.code else 400
        )
        return _error(exc, status)
    except IntegrityError:
        db.session.rollback()
        _log_integration_event(
            'webhook_duplicate', started, connection_id=connection.id,
        )
        return jsonify({'accepted': True, 'duplicate': True}), 200
    _log_integration_event(
        'webhook_processed', started, connection_id=connection.id, run_id=run.id,
        error_code=run.error_code, duplicate=duplicate,
    )
    return jsonify({
        'accepted': True,
        'duplicate': duplicate,
        'run': run.to_dict(),
    }), 202


@business_integrations_bp.patch('/operator/business/connections/<int:connection_id>')
@rate_limit(60, 3600)
@login_required
def operator_update_business_connection(connection_id):
    if not _operator_role('admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    connection = (
        BusinessProviderConnection.query.filter_by(id=connection_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection:
        return jsonify({'error': 'connection_not_found'}), 404
    try:
        payload = _object_payload()
        allowed = {
            'credential_ref', 'webhook_secret_ref', 'cursor_ref',
            'external_account_id', 'mfa_code',
        }
        if set(payload) - allowed:
            raise IntegrationError('unsupported_operator_connection_field')
        if set(payload) & {'credential_ref', 'webhook_secret_ref', 'cursor_ref'}:
            _require_fresh_operator_mfa(payload)
            if connection.status == 'disconnected':
                raise IntegrationError('connection_disconnected')
        attach_vault_references(
            connection,
            credential_ref=payload.get('credential_ref', connection.credential_ref),
            webhook_secret_ref=payload.get('webhook_secret_ref', connection.webhook_secret_ref),
            cursor_ref=payload.get('cursor_ref', connection.cursor_ref),
            actor_id=f'user:{g.current_user.id}',
        )
        if 'external_account_id' in payload:
            connection.external_account_id = str(payload.get('external_account_id') or '').strip()[:255]
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc, 403 if exc.code == 'operator_mfa_required' else 400)
    return jsonify({'connection': _connection_payload(connection, details=True)})


@business_integrations_bp.post(
    '/operator/business/connections/<int:connection_id>/credentials'
)
@rate_limit(20, 3600)
@login_required
def operator_store_business_credential(connection_id):
    """Encrypt a submitted secret and atomically attach only its opaque ref."""
    started = time.monotonic()
    if not _operator_role('admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    connection = (
        BusinessProviderConnection.query.filter_by(id=connection_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection:
        return jsonify({'error': 'connection_not_found'}), 404
    try:
        _require_https_for_secret_write()
        payload = _object_payload()
        if set(payload) - {'purpose', 'secret', 'mfa_code'}:
            raise IntegrationError('unsupported_credential_field')
        _require_fresh_operator_mfa(payload)
        if connection.status == 'disconnected':
            raise IntegrationError('connection_disconnected')
        purpose = str(payload.get('purpose') or '').strip().lower()
        reference_field = {
            'credential': 'credential_ref',
            'webhook': 'webhook_secret_ref',
            'cursor': 'cursor_ref',
        }.get(purpose)
        if not reference_field:
            raise IntegrationError('invalid_credential_purpose')
        vault = configured_vault()
        old_reference = getattr(connection, reference_field)
        new_reference = vault.put(
            payload.get('secret'),
            purpose=purpose,
            created_by_id=g.current_user.id,
        )
        references = {
            'credential_ref': connection.credential_ref,
            'webhook_secret_ref': connection.webhook_secret_ref,
            'cursor_ref': connection.cursor_ref,
        }
        references[reference_field] = new_reference
        attach_vault_references(
            connection,
            actor_id=f'user:{g.current_user.id}',
            **references,
        )
        if old_reference.startswith('vault://'):
            vault.delete(old_reference)
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        _log_integration_event(
            'credential_write_rejected', started,
            connection_id=connection_id, error_code=exc.code,
        )
        return _error(exc, 403 if exc.code == 'operator_mfa_required' else 400)
    _log_integration_event(
        'credential_stored', started, connection_id=connection.id,
    )
    return jsonify({'connection': _connection_payload(connection, details=True)}), 201


@business_integrations_bp.delete(
    '/operator/business/connections/<int:connection_id>'
)
@rate_limit(20, 3600)
@login_required
def operator_disconnect_business_connection(connection_id):
    if not _operator_role('admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    connection = (
        BusinessProviderConnection.query.filter_by(id=connection_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection:
        return jsonify({'error': 'connection_not_found'}), 404
    try:
        payload = _object_payload()
        if set(payload) - {'mfa_code'}:
            raise IntegrationError('unsupported_disconnect_field')
        _require_fresh_operator_mfa(payload)
        _delete_connection_vault_secrets(connection)
        disconnect_connection(
            connection,
            actor_kind='operator',
            actor_id=f'user:{g.current_user.id}',
        )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc, 403 if exc.code == 'operator_mfa_required' else 400)
    return jsonify({'connection': _connection_payload(connection, details=True)})


@business_integrations_bp.post(
    '/operator/business/connections/<int:connection_id>/reconnect'
)
@rate_limit(20, 3600)
@login_required
def operator_reconnect_business_connection(connection_id):
    if not _operator_role('admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    connection = (
        BusinessProviderConnection.query.filter_by(id=connection_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection:
        return jsonify({'error': 'connection_not_found'}), 404
    try:
        payload = _object_payload()
        if set(payload) - {'mfa_code'}:
            raise IntegrationError('unsupported_reconnect_field')
        _require_fresh_operator_mfa(payload)
        reconnect_connection(
            connection,
            actor_kind='operator',
            actor_id=f'user:{g.current_user.id}',
        )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc, 403 if exc.code == 'operator_mfa_required' else 400)
    return jsonify({'connection': _connection_payload(connection, details=True)})


@business_integrations_bp.post('/operator/business/connections/<int:connection_id>/recheck')
@rate_limit(60, 3600)
@login_required
def operator_recheck_business_connection(connection_id):
    if not _operator_role('reviewer', 'admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    connection = (
        BusinessProviderConnection.query.filter_by(id=connection_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not connection:
        return jsonify({'error': 'connection_not_found'}), 404
    try:
        checks = recheck_connection_links(
            connection,
            actor_kind='operator',
            actor_id=f'user:{g.current_user.id}',
        )
        db.session.commit()
    except IntegrationError as exc:
        db.session.rollback()
        return _error(exc, 409 if exc.code == 'business_integration_inactive' else 400)
    return jsonify({
        'connection': _connection_payload(connection),
        'checks': [item.to_dict() for item in checks],
    })


@business_integrations_bp.get('/operator/business/link-health')
@login_required
def operator_business_link_health_queue():
    if not _operator_role('reviewer', 'admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    status = str(request.args.get('status') or 'problems').strip().lower()
    if status not in {'problems', 'healthy', 'all'}:
        return jsonify({'error': 'invalid_link_health_status'}), 400
    try:
        limit = min(max(int(request.args.get('limit') or 100), 1), 250)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_limit'}), 400
    latest_ids = db.session.query(
        func.max(BusinessLinkHealthCheck.id).label('id'),
    ).group_by(
        BusinessLinkHealthCheck.business_id,
        BusinessLinkHealthCheck.connection_id,
        BusinessLinkHealthCheck.link_kind,
        BusinessLinkHealthCheck.url_hash,
    ).subquery()
    query = BusinessLinkHealthCheck.query.options(
        joinedload(BusinessLinkHealthCheck.business),
        joinedload(BusinessLinkHealthCheck.connection),
    ).filter(
        BusinessLinkHealthCheck.id.in_(select(latest_ids.c.id)),
    )
    if status == 'problems':
        query = query.filter(BusinessLinkHealthCheck.status != 'healthy')
    elif status == 'healthy':
        query = query.filter(BusinessLinkHealthCheck.status == 'healthy')
    candidates = query.order_by(
        BusinessLinkHealthCheck.checked_at.desc(),
        BusinessLinkHealthCheck.id.desc(),
    ).limit(min(limit * 3, 750)).all()
    checks = [item for item in candidates if _link_check_is_current(item)][:limit]
    items = []
    for check in checks:
        item = check.to_dict()
        item.update({
            'business_name': check.business.name if check.business else None,
            'source': 'connection' if check.connection_id else 'profile',
        })
        items.append(item)
    return jsonify({'items': items, 'status': status})


@business_integrations_bp.post(
    '/operator/businesses/<int:business_id>/link-health/recheck'
)
@rate_limit(60, 3600)
@login_required
def operator_recheck_business_profile_links(business_id):
    if not _operator_role('reviewer', 'admin'):
        return jsonify({'error': 'business_operator_required'}), 403
    business = BusinessProfile.query.filter_by(id=business_id).with_for_update().first()
    if not business:
        return jsonify({'error': 'business_not_found'}), 404
    checks = recheck_business_profile_links(
        business,
        actor_kind='operator',
        actor_id=f'user:{g.current_user.id}',
    )
    db.session.commit()
    return jsonify({'checks': [item.to_dict() for item in checks]})


def _cron_queue_order(now=None):
    """Rotate the daily first lane so a constrained run cannot starve a queue."""
    lanes = ('health', 'profile_health', 'sync', 'pull')
    offset = (now or utcnow()).toordinal() % len(lanes)
    return lanes[offset:] + lanes[:offset]


@business_integration_cron_bp.get('/cron/business-integrations')
def cron_business_integrations():
    started = time.monotonic()
    expected = os.getenv('CRON_SECRET', '')
    supplied = str(request.headers.get('Authorization') or '')
    if not expected or not hmac.compare_digest(supplied, f'Bearer {expected}'):
        _log_integration_event(
            'cron_rejected', started, error_code='cron_authentication_required',
        )
        return jsonify({'error': 'cron_authentication_required'}), 401
    try:
        configured_cap = int(os.getenv('BUSINESS_INTEGRATION_CRON_LIMIT', '40'))
    except (TypeError, ValueError):
        configured_cap = 40
    # The daily production schedule needs a generous candidate cap; the time
    # budget remains authoritative and stops slow work safely.
    cap = min(max(configured_cap, 40), 100)
    try:
        configured_budget = int(os.getenv(
            'BUSINESS_INTEGRATION_CRON_TIME_BUDGET_SECONDS', '45',
        ))
    except (TypeError, ValueError):
        configured_budget = 45
    time_budget_seconds = min(max(configured_budget, 45), 50)
    deadline = started + time_budget_seconds
    shutdown_headroom_seconds = 11
    stop_claiming_at = deadline - shutdown_headroom_seconds
    counts = {
        'sync_claimed': 0,
        'sync_succeeded': 0,
        'sync_retry_scheduled': 0,
        'sync_failed': 0,
        'pull_claimed': 0,
        'pull_succeeded': 0,
        'pull_not_modified': 0,
        'pull_failed': 0,
        'health_claimed': 0,
        'health_checked': 0,
        'health_failed': 0,
        'profile_health_claimed': 0,
        'profile_health_checked': 0,
        'profile_health_problems': 0,
        'profile_health_failed': 0,
    }
    processed_ids = {
        'sync': set(), 'pull': set(), 'health': set(), 'profile_health': set(),
    }
    remaining = cap
    time_budget_exhausted = False
    try:
        # Round-robin ordering protects the publication-critical health queues
        # while guaranteeing retries and pulls a slot whenever they are due.
        # Each claimed item commits independently so one slow or broken feed
        # cannot roll back unrelated progress or hold a batch lock for the
        # whole serverless invocation.
        queue_order = _cron_queue_order()
        while remaining:
            made_progress = False
            for queue_name in queue_order:
                if not remaining:
                    break
                if time.monotonic() >= stop_claiming_at:
                    time_budget_exhausted = True
                    break

                if queue_name == 'health':
                    items = stale_connections(
                        limit=1, exclude_ids=processed_ids['health'],
                    )
                elif queue_name == 'profile_health':
                    items = stale_business_profiles(
                        limit=1, exclude_ids=processed_ids['profile_health'],
                    )
                elif queue_name == 'sync':
                    items = due_sync_runs(
                        limit=1, exclude_ids=processed_ids['sync'],
                    )
                else:
                    items = due_pull_connections(
                        limit=1, exclude_ids=processed_ids['pull'],
                    )
                if not items:
                    continue

                item = items[0]
                processed_ids[queue_name].add(item.id)
                made_progress = True
                remaining -= 1
                operation_started = time.monotonic()

                if queue_name == 'sync':
                    counts['sync_claimed'] += 1
                    try:
                        process_sync_run(item)
                        db.session.commit()
                        if item.status == 'succeeded':
                            counts['sync_succeeded'] += 1
                        elif item.status == 'retry_scheduled':
                            counts['sync_retry_scheduled'] += 1
                        elif item.status in {'failed', 'cancelled'}:
                            counts['sync_failed'] += 1
                        _log_integration_event(
                            'cron_sync_processed', operation_started,
                            connection_id=item.connection_id, run_id=item.id,
                            error_code=item.error_code,
                        )
                    except Exception:
                        db.session.rollback()
                        counts['sync_failed'] += 1
                        _log_integration_event(
                            'cron_sync_failed', operation_started,
                            run_id=item.id, error_code='sync_internal_error',
                        )
                    continue

                if queue_name == 'pull':
                    counts['pull_claimed'] += 1
                    connection_id = item.id
                    try:
                        run, duplicate, not_modified = pull_connection_catalog(item)
                        db.session.commit()
                        if not_modified:
                            counts['pull_not_modified'] += 1
                        elif run and run.status == 'succeeded':
                            counts['pull_succeeded'] += 1
                        else:
                            counts['pull_failed'] += 1
                        _log_integration_event(
                            'cron_pull_processed', operation_started,
                            connection_id=connection_id,
                            run_id=run.id if run else None,
                            error_code=run.error_code if run else '',
                            duplicate=duplicate,
                        )
                    except IntegrationError as exc:
                        # pull_connection_catalog already records the bounded
                        # retry state for expected provider failures.
                        db.session.commit()
                        counts['pull_failed'] += 1
                        _log_integration_event(
                            'cron_pull_failed', operation_started,
                            connection_id=connection_id, error_code=exc.code,
                        )
                    except Exception:
                        db.session.rollback()
                        safe_error = IntegrationError('catalog_pull_failed')
                        connection = db.session.get(
                            BusinessProviderConnection, connection_id,
                        )
                        if connection is not None:
                            mark_pull_failure(connection, safe_error)
                            db.session.commit()
                        counts['pull_failed'] += 1
                        _log_integration_event(
                            'cron_pull_failed', operation_started,
                            connection_id=connection_id,
                            error_code=safe_error.code,
                        )
                    continue

                if queue_name == 'health':
                    counts['health_claimed'] += 1
                    connection_id = item.id
                    try:
                        recheck_connection_links(
                            item,
                            actor_kind='cron',
                            actor_id='business-integrations',
                            probe_timeout=3,
                        )
                        db.session.commit()
                        counts['health_checked'] += 1
                        _log_integration_event(
                            'cron_connection_health_checked', operation_started,
                            connection_id=connection_id,
                            error_code=item.last_error_code,
                        )
                    except Exception:
                        db.session.rollback()
                        connection = db.session.get(
                            BusinessProviderConnection, connection_id,
                        )
                        if connection is not None:
                            mark_connection_health_failure(connection)
                            db.session.commit()
                        counts['health_failed'] += 1
                        _log_integration_event(
                            'cron_connection_health_failed', operation_started,
                            connection_id=connection_id,
                            error_code='link_health_check_failed',
                        )
                    continue

                counts['profile_health_claimed'] += 1
                try:
                    checks = recheck_business_profile_links(
                        item,
                        actor_kind='cron',
                        actor_id='business-integrations',
                        probe_timeout=3,
                    )
                    db.session.commit()
                    counts['profile_health_checked'] += len(checks)
                    problems = sum(check.status != 'healthy' for check in checks)
                    counts['profile_health_problems'] += problems
                    _log_integration_event(
                        'cron_profile_health_checked', operation_started,
                        error_code='', links_checked=len(checks),
                        problems=problems,
                    )
                except Exception:
                    db.session.rollback()
                    counts['profile_health_failed'] += 1
                    _log_integration_event(
                        'cron_profile_health_failed', operation_started,
                        error_code='profile_link_health_check_failed',
                    )
            if time_budget_exhausted or not made_progress:
                break
    except Exception:
        db.session.rollback()
        _log_integration_event(
            'cron_failed', started,
            error_code='business_integration_cron_failed',
            **counts,
        )
        return jsonify({'error': 'business_integration_cron_failed'}), 503
    if time.monotonic() >= stop_claiming_at:
        time_budget_exhausted = True
    _log_integration_event(
        'cron_completed', started,
        time_budget_exhausted=time_budget_exhausted,
        processed=cap - remaining,
        **counts,
    )
    return jsonify({
        'ok': True,
        'limit': cap,
        'processed': cap - remaining,
        'time_budget_seconds': time_budget_seconds,
        'shutdown_headroom_seconds': shutdown_headroom_seconds,
        'time_budget_exhausted': time_budget_exhausted,
        **counts,
    })
