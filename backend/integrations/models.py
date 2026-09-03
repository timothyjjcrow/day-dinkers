"""SQL models for provider connections and normalized business data.

Credential and webhook secret *values* are intentionally absent. Only opaque
references into a configured vault may be persisted.
"""
from __future__ import annotations

import uuid
from datetime import UTC

from backend.app import db
from backend.models import (
    TimestampMixin,
    iso,
    local_date_for_timezone,
    utcnow,
)
from backend.integrations.safety import safe_json_loads


CONNECTION_STATUSES = ('draft', 'connected', 'degraded', 'error', 'disconnected')
HEALTH_STATUSES = ('unknown', 'healthy', 'degraded', 'unreachable', 'unsafe', 'disabled')
SYNC_STATUSES = ('queued', 'running', 'succeeded', 'retry_scheduled', 'failed', 'cancelled')
SYNC_TRIGGERS = ('owner_push', 'webhook', 'manual', 'scheduled', 'reconcile')
OCCURRENCE_STATUSES = ('scheduled', 'cancelled', 'sold_out', 'completed')
BOOKING_EVENT_TYPES = ('click', 'conversion')


class BusinessCredentialSecret(TimestampMixin, db.Model):
    """Encrypted provider material addressed only through an opaque reference.

    API responses and audit records must never serialize ``ciphertext``.  A
    deleted row is retained as a tombstone so an old reference cannot later be
    reassigned, but its encrypted payload is erased.
    """
    __table_args__ = (
        db.CheckConstraint('key_version >= 1', name='ck_business_credential_key_version'),
        db.CheckConstraint(
            "purpose IN ('credential','webhook','cursor')",
            name='ck_business_credential_purpose',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    purpose = db.Column(db.String(24), nullable=False, index=True)
    ciphertext = db.Column(db.Text, nullable=False)
    key_version = db.Column(db.Integer, nullable=False, default=1, index=True)
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_credential_secret_created_by_id_fkey'),
        nullable=False,
        index=True,
    )
    last_accessed_at = db.Column(db.DateTime)
    deleted_at = db.Column(db.DateTime, index=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def reference(self):
        return f'vault://{self.public_id}'

    def safe_metadata(self):
        return {
            'reference': self.reference,
            'purpose': self.purpose,
            'key_version': self.key_version,
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
            'deleted_at': iso(self.deleted_at),
        }


class BusinessProviderConnection(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            'business_id', 'provider_key', name='uq_business_provider_connection',
        ),
        db.CheckConstraint(
            "status IN ('draft','connected','degraded','error','disconnected')",
            name='ck_business_provider_connection_status',
        ),
        db.CheckConstraint(
            "health_status IN ('unknown','healthy','degraded','unreachable','unsafe','disabled')",
            name='ck_business_provider_connection_health',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, index=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_provider_connection_business_id_fkey'),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_provider_connection_created_by_id_fkey'),
        nullable=False,
        index=True,
    )
    provider_key = db.Column(db.String(64), nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)
    external_account_id = db.Column(db.String(255), nullable=False, default='')
    status = db.Column(db.String(24), nullable=False, default='draft', index=True)
    health_status = db.Column(db.String(24), nullable=False, default='unknown', index=True)
    capabilities = db.Column(db.Text, nullable=False, default='[]')
    public_config = db.Column(db.Text, nullable=False, default='{}')
    # References only, e.g. env://BUSINESS_PROVIDER_SECRET_ACME.
    credential_ref = db.Column(db.String(255), nullable=False, default='')
    webhook_secret_ref = db.Column(db.String(255), nullable=False, default='')
    cursor_ref = db.Column(db.String(255), nullable=False, default='')
    last_sync_started_at = db.Column(db.DateTime)
    last_sync_succeeded_at = db.Column(db.DateTime)
    last_sync_failed_at = db.Column(db.DateTime)
    last_health_checked_at = db.Column(db.DateTime)
    last_pull_at = db.Column(db.DateTime)
    next_sync_at = db.Column(db.DateTime, index=True)
    # HTTP validators are non-secret protocol metadata used only for
    # conditional reads of a business-owned JSON feed.
    pull_etag = db.Column(db.String(500), nullable=False, default='')
    pull_last_modified = db.Column(db.String(120), nullable=False, default='')
    consecutive_failures = db.Column(db.Integer, nullable=False, default=0)
    last_error_code = db.Column(db.String(120), nullable=False, default='')
    last_error_message = db.Column(db.String(500), nullable=False, default='')
    disconnected_at = db.Column(db.DateTime)
    operator_reconnect_required = db.Column(
        db.Boolean, nullable=False, default=False,
    )
    version = db.Column(db.Integer, nullable=False, default=1)

    business = db.relationship('BusinessProfile', foreign_keys=[business_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    sync_runs = db.relationship(
        'BusinessIntegrationSyncRun', back_populates='connection',
        cascade='all, delete-orphan',
    )

    def capabilities_list(self):
        values = safe_json_loads(self.capabilities, [])
        return [str(item) for item in values] if isinstance(values, list) else []

    def config_dict(self):
        values = safe_json_loads(self.public_config, {})
        return values if isinstance(values, dict) else {}

    def to_owner_dict(self):
        return {
            'id': self.id,
            'public_id': self.public_id,
            'business_id': self.business_id,
            'provider_key': self.provider_key,
            'display_name': self.display_name,
            'external_account_id': self.external_account_id,
            'status': self.status,
            'health_status': self.health_status,
            'capabilities': self.capabilities_list(),
            'config': self.config_dict(),
            'credential_configured': bool(self.credential_ref),
            'webhook_configured': bool(self.webhook_secret_ref),
            'last_sync_started_at': iso(self.last_sync_started_at),
            'last_sync_succeeded_at': iso(self.last_sync_succeeded_at),
            'last_sync_failed_at': iso(self.last_sync_failed_at),
            'last_health_checked_at': iso(self.last_health_checked_at),
            'last_pull_at': iso(self.last_pull_at),
            'next_sync_at': iso(self.next_sync_at),
            'consecutive_failures': self.consecutive_failures,
            'last_error_code': self.last_error_code,
            'last_error_message': self.last_error_message,
            'disconnected_at': iso(self.disconnected_at),
            'reconnect_requires_operator': bool(self.operator_reconnect_required),
            'version': self.version,
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
        }


class BusinessIntegrationSyncRun(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            'connection_id', 'idempotency_key', name='uq_business_sync_run_idempotency',
        ),
        db.CheckConstraint(
            "status IN ('queued','running','succeeded','retry_scheduled','failed','cancelled')",
            name='ck_business_sync_run_status',
        ),
        db.CheckConstraint(
            "trigger IN ('owner_push','webhook','manual','scheduled','reconcile')",
            name='ck_business_sync_run_trigger',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('business_provider_connection.id', name='business_sync_run_connection_id_fkey'),
        nullable=False,
        index=True,
    )
    trigger = db.Column(db.String(24), nullable=False)
    status = db.Column(db.String(24), nullable=False, default='queued', index=True)
    idempotency_key = db.Column(db.String(64), nullable=False)
    attempt = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=5)
    scheduled_for = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    next_retry_at = db.Column(db.DateTime, index=True)
    source_version = db.Column(db.String(160), nullable=False, default='')
    reconciliation_hash = db.Column(db.String(64), nullable=False, default='')
    # Canonical, adapter-validated retry input. It contains catalog facts and
    # links only; raw webhook bodies and credentials are never persisted.
    payload_json = db.Column(db.Text, nullable=False, default='{}')
    error_code = db.Column(db.String(120), nullable=False, default='')
    error_message = db.Column(db.String(500), nullable=False, default='')
    metrics = db.Column(db.Text, nullable=False, default='{}')

    connection = db.relationship('BusinessProviderConnection', back_populates='sync_runs')

    def metrics_dict(self):
        values = safe_json_loads(self.metrics, {})
        return values if isinstance(values, dict) else {}

    def to_dict(self):
        return {
            'id': self.id,
            'connection_id': self.connection_id,
            'trigger': self.trigger,
            'status': self.status,
            'attempt': self.attempt,
            'max_attempts': self.max_attempts,
            'scheduled_for': iso(self.scheduled_for),
            'started_at': iso(self.started_at),
            'completed_at': iso(self.completed_at),
            'next_retry_at': iso(self.next_retry_at),
            'source_version': self.source_version,
            'error_code': self.error_code,
            'error_message': self.error_message,
            'metrics': self.metrics_dict(),
            'created_at': iso(self.created_at),
        }


class BusinessWebhookReceipt(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            'connection_id', 'idempotency_key', name='uq_business_webhook_receipt',
        ),
        db.CheckConstraint(
            "status IN ('received','processed','duplicate','rejected','failed')",
            name='ck_business_webhook_receipt_status',
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('business_provider_connection.id', name='business_webhook_receipt_connection_id_fkey'),
        nullable=False,
        index=True,
    )
    provider_event_id = db.Column(db.String(160), nullable=False, default='')
    idempotency_key = db.Column(db.String(64), nullable=False)
    signature_digest = db.Column(db.String(64), nullable=False)
    payload_digest = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(24), nullable=False, default='received', index=True)
    processed_at = db.Column(db.DateTime)
    error_code = db.Column(db.String(120), nullable=False, default='')

    connection = db.relationship('BusinessProviderConnection')


class BusinessScheduleOccurrence(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            'connection_id', 'external_id', name='uq_business_occurrence_external',
        ),
        db.CheckConstraint(
            "status IN ('scheduled','cancelled','sold_out','completed')",
            name='ck_business_occurrence_status',
        ),
        db.CheckConstraint(
            'capacity IS NULL OR capacity >= 0', name='ck_business_occurrence_capacity',
        ),
        db.CheckConstraint(
            'spots_remaining IS NULL OR spots_remaining >= 0',
            name='ck_business_occurrence_spots',
        ),
        db.CheckConstraint(
            'capacity IS NULL OR spots_remaining IS NULL OR spots_remaining <= capacity',
            name='ck_business_occurrence_spots_capacity',
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_occurrence_business_id_fkey'),
        nullable=False,
        index=True,
    )
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('business_provider_connection.id', name='business_occurrence_connection_id_fkey'),
        nullable=False,
        index=True,
    )
    external_id = db.Column(db.String(160), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(32), nullable=False, default='other', index=True)
    recurrence = db.Column(db.String(200), nullable=False, default='')
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    event_date = db.Column(db.Date, index=True)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    starts_at = db.Column(db.DateTime, index=True)
    ends_at = db.Column(db.DateTime)
    timezone = db.Column(db.String(64), nullable=False, default='UTC')
    capacity = db.Column(db.Integer)
    spots_remaining = db.Column(db.Integer)
    status = db.Column(db.String(24), nullable=False, default='scheduled', index=True)
    skill_level = db.Column(db.String(40), nullable=False, default='all')
    location_note = db.Column(db.String(240), nullable=False, default='')
    instructor = db.Column(db.String(120), nullable=False, default='')
    price_text = db.Column(db.String(120), nullable=False, default='')
    booking_url = db.Column(db.String(500), nullable=False, default='')
    source_updated_at = db.Column(db.DateTime)
    synced_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    payload_hash = db.Column(db.String(64), nullable=False)

    business = db.relationship('BusinessProfile')
    connection = db.relationship('BusinessProviderConnection')

    def is_current(self, as_of=None):
        """Use the occurrence's venue timezone for its calendar boundary."""
        today = local_date_for_timezone(self.timezone, as_of)
        if self.event_date:
            return self.event_date >= today
        if self.end_date:
            return self.end_date >= today
        if self.ends_at:
            instant = as_of or utcnow()
            if getattr(instant, 'tzinfo', None) is not None:
                instant = instant.astimezone(UTC).replace(tzinfo=None)
            return self.ends_at > instant
        return bool(self.recurrence)

    def to_dict(self):
        return {
            'id': self.id,
            'business_id': self.business_id,
            'connection_id': self.connection_id,
            'external_id': self.external_id,
            'title': self.title,
            'kind': self.kind,
            'recurrence': self.recurrence,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'event_date': self.event_date.isoformat() if self.event_date else None,
            'start_time': self.start_time.strftime('%H:%M') if self.start_time else None,
            'end_time': self.end_time.strftime('%H:%M') if self.end_time else None,
            'starts_at': iso(self.starts_at),
            'ends_at': iso(self.ends_at),
            'timezone': self.timezone,
            'capacity': self.capacity,
            'spots_remaining': self.spots_remaining,
            'status': self.status,
            'skill_level': self.skill_level,
            'location_note': self.location_note,
            'instructor': self.instructor,
            'price_text': self.price_text,
            'booking_url': self.booking_url,
            'source_updated_at': iso(self.source_updated_at),
            'synced_at': iso(self.synced_at),
            'updated_at': iso(self.updated_at),
        }


class BusinessBookingEvent(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('event_key', name='uq_business_booking_event_key'),
        db.CheckConstraint(
            "event_type IN ('click','conversion')",
            name='ck_business_booking_event_type',
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_booking_event_business_id_fkey'),
        nullable=False,
        index=True,
    )
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('business_provider_connection.id', name='business_booking_event_connection_id_fkey'),
        index=True,
    )
    occurrence_id = db.Column(
        db.Integer,
        db.ForeignKey('business_schedule_occurrence.id', name='business_booking_event_occurrence_id_fkey'),
        index=True,
    )
    event_type = db.Column(db.String(20), nullable=False, index=True)
    event_key = db.Column(db.String(64), nullable=False)
    external_event_id = db.Column(db.String(160), nullable=False, default='')
    action = db.Column(db.String(40), nullable=False, default='booking')
    occurred_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    value_minor = db.Column(db.Integer)
    currency = db.Column(db.String(3), nullable=False, default='')
    source = db.Column(db.String(40), nullable=False, default='third_shot')


class BusinessLinkHealthCheck(TimestampMixin, db.Model):
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('healthy','broken','unreachable','unsafe')",
            name='ck_business_link_health_status',
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_link_health_business_id_fkey'),
        nullable=False,
        index=True,
    )
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('business_provider_connection.id', name='business_link_health_connection_id_fkey'),
        index=True,
    )
    link_kind = db.Column(db.String(40), nullable=False)
    url_hash = db.Column(db.String(64), nullable=False, index=True)
    final_url_hash = db.Column(db.String(64), nullable=False, default='')
    status = db.Column(db.String(24), nullable=False, index=True)
    http_status = db.Column(db.Integer)
    latency_ms = db.Column(db.Integer)
    error_code = db.Column(db.String(120), nullable=False, default='')
    checked_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    next_check_at = db.Column(db.DateTime, index=True)

    connection = db.relationship('BusinessProviderConnection')
    business = db.relationship('BusinessProfile')

    def to_dict(self):
        return {
            'id': self.id,
            'business_id': self.business_id,
            'connection_id': self.connection_id,
            'link_kind': self.link_kind,
            'status': self.status,
            'http_status': self.http_status,
            'latency_ms': self.latency_ms,
            'error_code': self.error_code,
            'checked_at': iso(self.checked_at),
            'next_check_at': iso(self.next_check_at),
        }


class BusinessIntegrationAuditEvent(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_integration_audit_business_id_fkey'),
        nullable=False,
        index=True,
    )
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('business_provider_connection.id', name='business_integration_audit_connection_id_fkey'),
        index=True,
    )
    actor_kind = db.Column(db.String(24), nullable=False)
    actor_id = db.Column(db.String(120), nullable=False, default='')
    action = db.Column(db.String(80), nullable=False, index=True)
    metadata_json = db.Column(db.Text, nullable=False, default='{}')

    def metadata_dict(self):
        values = safe_json_loads(self.metadata_json, {})
        return values if isinstance(values, dict) else {}

    def to_dict(self):
        return {
            'id': self.id,
            'business_id': self.business_id,
            'connection_id': self.connection_id,
            'actor_kind': self.actor_kind,
            'actor_id': self.actor_id,
            'action': self.action,
            'metadata': self.metadata_dict(),
            'created_at': iso(self.created_at),
        }
