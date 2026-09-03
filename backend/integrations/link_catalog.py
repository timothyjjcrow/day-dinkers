"""A real, no-secret push/pull adapter for a business-owned JSON catalog.

This adapter does not scrape or impersonate a booking vendor. An authenticated
venue owner supplies versioned JSON by authenticated push, signed webhook, or
an HTTPS ``source_url`` that is fetched through a pinned public-IP transport.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.integrations.base import (
    CatalogSnapshot,
    NormalizedConversion,
    NormalizedOccurrence,
    ProviderAdapter,
    ProviderDescriptor,
)
from backend.integrations.errors import ValidationError
from backend.integrations.safety import (
    safe_external_url,
    sanitize_public_config,
    stable_digest,
)


_TIME_RE = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
_KINDS = {
    'court_rental', 'lesson', 'open_play', 'clinic', 'league', 'tournament',
    'membership', 'event', 'hours', 'other',
}
_STATUSES = {'scheduled', 'cancelled', 'sold_out', 'completed'}
_ROOT_FIELDS = {
    '$schema', 'schema_version', 'source_version', 'generated_at',
    'authoritative', 'occurrences', 'conversions',
}
_OCCURRENCE_FIELDS = {
    'external_id', 'title', 'kind', 'timezone', 'recurrence', 'start_date',
    'end_date', 'event_date', 'start_time', 'end_time', 'starts_at', 'ends_at',
    'capacity', 'spots_remaining', 'status', 'skill_level', 'location_note',
    'instructor', 'price_text', 'booking_url', 'updated_at',
}
_CONVERSION_FIELDS = {
    'external_event_id', 'occurrence_external_id', 'occurred_at',
    'value_minor', 'currency',
}


def _iso(value):
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime('%H:%M')
    if isinstance(value, (date, datetime)):
        suffix = 'Z' if isinstance(value, datetime) and value.tzinfo is None else ''
        return value.isoformat() + suffix
    return value


def canonical_snapshot_payload(snapshot):
    """Serialize only normalized contract fields for a safe retry job."""
    occurrences = []
    for item in snapshot.occurrences:
        fields = (
            'external_id', 'title', 'kind', 'timezone', 'recurrence',
            'start_date', 'end_date', 'event_date', 'start_time', 'end_time',
            'starts_at', 'ends_at', 'capacity', 'spots_remaining', 'status',
            'skill_level', 'location_note', 'instructor', 'price_text',
            'booking_url',
        )
        if item.event_date is not None:
            # starts_at/ends_at are derived for querying dated local-time input.
            # Persist one unambiguous input shape so retry normalization cannot
            # mistake derived timestamps for a conflicting second shape.
            fields = tuple(
                key for key in fields if key not in {'starts_at', 'ends_at'}
            )
        occurrences.append({
            key: _iso(getattr(item, key))
            for key in fields
            if getattr(item, key) not in (None, '')
        })
        if item.source_updated_at is not None:
            occurrences[-1]['updated_at'] = _iso(item.source_updated_at)
    conversions = []
    for item in snapshot.conversions:
        conversions.append({
            key: _iso(getattr(item, key))
            for key in (
                'external_event_id', 'occurrence_external_id', 'occurred_at',
                'value_minor', 'currency',
            )
            if getattr(item, key) not in (None, '')
        })
    return {
        'schema_version': 1,
        'source_version': snapshot.source_version,
        'generated_at': _iso(snapshot.generated_at),
        'authoritative': snapshot.authoritative,
        'occurrences': occurrences,
        'conversions': conversions,
    }


def _short(value, field, maximum, *, required=False):
    text = str(value or '').strip()
    if required and not text:
        raise ValidationError(f'{field}_required')
    if len(text) > maximum:
        raise ValidationError(f'{field}_too_long')
    return text


def _date(value, field):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError(f'invalid_{field}')


def _time(value, field):
    if value in (None, ''):
        return None
    raw = str(value)
    if not _TIME_RE.fullmatch(raw):
        raise ValidationError(f'invalid_{field}')
    return time.fromisoformat(raw)


def _datetime(value, field, *, required=False):
    if value in (None, ''):
        if required:
            raise ValidationError(f'{field}_required')
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValidationError(f'invalid_{field}')
    if parsed.tzinfo is None:
        raise ValidationError(f'{field}_timezone_required')
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _integer(value, field, *, minimum=0, maximum=10_000_000):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise ValidationError(f'invalid_{field}')
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f'invalid_{field}')
    if number < minimum or number > maximum:
        raise ValidationError(f'invalid_{field}')
    return number


def _timezone(value):
    name = _short(value, 'timezone', 64, required=True)
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValidationError('invalid_timezone')
    return name


def _occurrence(raw):
    if not isinstance(raw, dict):
        raise ValidationError('occurrence_must_be_an_object')
    if set(raw) - _OCCURRENCE_FIELDS:
        raise ValidationError('unsupported_occurrence_field')
    external_id = _short(raw.get('external_id'), 'external_id', 160, required=True)
    title = _short(raw.get('title'), 'title', 120, required=True)
    kind = _short(raw.get('kind') or 'other', 'kind', 32).lower().replace('-', '_').replace(' ', '_')
    if kind not in _KINDS:
        raise ValidationError('invalid_occurrence_kind')
    timezone = _timezone(raw.get('timezone'))
    recurrence = _short(raw.get('recurrence'), 'recurrence', 200)
    start_date = _date(raw.get('start_date'), 'start_date')
    end_date = _date(raw.get('end_date'), 'end_date')
    event_date = _date(raw.get('event_date'), 'event_date')
    start_time = _time(raw.get('start_time'), 'start_time')
    end_time = _time(raw.get('end_time'), 'end_time')
    starts_at = _datetime(raw.get('starts_at'), 'starts_at')
    ends_at = _datetime(raw.get('ends_at'), 'ends_at')
    if raw.get('starts_at') not in (None, '') and event_date:
        raise ValidationError('occurrence_time_shape_conflict')
    if recurrence:
        if not start_date or not start_time or not end_time:
            raise ValidationError('recurrence_dates_and_times_required')
        if end_date and end_date < start_date:
            raise ValidationError('end_date_before_start_date')
    elif not starts_at and not (event_date and start_time and end_time):
        raise ValidationError('event_date_and_times_required')
    if start_time and end_time and end_time <= start_time:
        raise ValidationError('end_time_must_be_after_start_time')
    if starts_at and (not ends_at or ends_at <= starts_at):
        raise ValidationError('ends_at_must_be_after_starts_at')
    if event_date and start_time and not starts_at:
        zone = ZoneInfo(timezone)
        starts_at = datetime.combine(event_date, start_time, zone).astimezone(UTC).replace(tzinfo=None)
        ends_at = datetime.combine(event_date, end_time, zone).astimezone(UTC).replace(tzinfo=None)
    capacity = _integer(raw.get('capacity'), 'capacity', maximum=100_000)
    spots = _integer(raw.get('spots_remaining'), 'spots_remaining', maximum=100_000)
    if spots is not None and capacity is None:
        raise ValidationError('capacity_required_with_spots_remaining')
    if spots is not None and spots > capacity:
        raise ValidationError('spots_remaining_exceeds_capacity')
    status = _short(raw.get('status') or 'scheduled', 'status', 24).lower()
    if status not in _STATUSES:
        raise ValidationError('invalid_occurrence_status')
    if spots == 0 and status == 'scheduled':
        status = 'sold_out'
    return NormalizedOccurrence(
        external_id=external_id,
        title=title,
        kind=kind,
        timezone=timezone,
        recurrence=recurrence,
        start_date=start_date,
        end_date=end_date,
        event_date=event_date,
        start_time=start_time,
        end_time=end_time,
        starts_at=starts_at,
        ends_at=ends_at,
        capacity=capacity,
        spots_remaining=spots,
        status=status,
        skill_level=_short(raw.get('skill_level') or 'all', 'skill_level', 40),
        location_note=_short(raw.get('location_note'), 'location_note', 240),
        instructor=_short(raw.get('instructor'), 'instructor', 120),
        price_text=_short(raw.get('price_text'), 'price_text', 120),
        booking_url=safe_external_url(raw.get('booking_url')),
        source_updated_at=_datetime(raw.get('updated_at'), 'updated_at'),
    )


def _conversion(raw):
    if not isinstance(raw, dict):
        raise ValidationError('conversion_must_be_an_object')
    if set(raw) - _CONVERSION_FIELDS:
        raise ValidationError('unsupported_conversion_field')
    currency = _short(raw.get('currency'), 'currency', 3).upper()
    if currency and (len(currency) != 3 or not currency.isalpha()):
        raise ValidationError('invalid_currency')
    value_minor = _integer(
        raw.get('value_minor'), 'value_minor', maximum=1_000_000_000,
    )
    if value_minor is not None and not currency:
        raise ValidationError('currency_required_with_value_minor')
    return NormalizedConversion(
        external_event_id=_short(
            raw.get('external_event_id'), 'external_event_id', 160, required=True,
        ),
        occurrence_external_id=_short(
            raw.get('occurrence_external_id'), 'occurrence_external_id', 160,
        ),
        occurred_at=_datetime(raw.get('occurred_at'), 'occurred_at', required=True),
        value_minor=value_minor,
        currency=currency,
    )


class LinkCatalogAdapter(ProviderAdapter):
    descriptor = ProviderDescriptor(
        key='link_catalog',
        name='Structured link catalog',
        availability='active',
        auth_mode='owner_push_or_signed_webhook',
        capabilities=('schedule', 'inventory', 'booking_links', 'conversions'),
        supports_push=True,
        supports_pull=True,
        supports_webhooks=True,
        note='A Third Shot JSON contract; it is not a third-party vendor API.',
    )

    def validate_public_config(self, config):
        config = sanitize_public_config(config)
        allowed = {'label', 'source_url', 'booking_base_url'}
        if set(config) - allowed:
            raise ValidationError('unsupported_link_catalog_config')
        return {
            'label': _short(config.get('label') or 'Business catalog', 'label', 120),
            'source_url': safe_external_url(config.get('source_url')),
            'booking_base_url': safe_external_url(config.get('booking_base_url')),
        }

    def health_urls(self, config):
        values = []
        if config.get('source_url'):
            values.append(('catalog', config['source_url']))
        if config.get('booking_base_url'):
            values.append(('booking', config['booking_base_url']))
        return tuple(values)

    def normalize_snapshot(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError('catalog_must_be_an_object')
        if set(payload) - _ROOT_FIELDS:
            raise ValidationError('unsupported_catalog_field')
        if payload.get('schema_version') != 1:
            raise ValidationError('unsupported_catalog_schema_version')
        raw_occurrences = payload.get('occurrences', [])
        raw_conversions = payload.get('conversions', [])
        if not isinstance(raw_occurrences, list) or len(raw_occurrences) > 500:
            raise ValidationError('invalid_occurrence_collection')
        if not isinstance(raw_conversions, list) or len(raw_conversions) > 500:
            raise ValidationError('invalid_conversion_collection')
        occurrences = tuple(_occurrence(item) for item in raw_occurrences)
        conversions = tuple(_conversion(item) for item in raw_conversions)
        occurrence_ids = [item.external_id for item in occurrences]
        conversion_ids = [item.external_event_id for item in conversions]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValidationError('duplicate_occurrence_external_id')
        if len(conversion_ids) != len(set(conversion_ids)):
            raise ValidationError('duplicate_conversion_external_event_id')
        source_version = _short(
            payload.get('source_version'), 'source_version', 160,
        ) or stable_digest(payload)[:32]
        generated_at = _datetime(payload.get('generated_at'), 'generated_at') \
            or datetime.now(UTC).replace(tzinfo=None)
        authoritative = payload.get('authoritative', True)
        if not isinstance(authoritative, bool):
            raise ValidationError('authoritative_must_be_boolean')
        return CatalogSnapshot(
            schema_version=1,
            source_version=source_version,
            generated_at=generated_at,
            occurrences=occurrences,
            conversions=conversions,
            authoritative=authoritative,
        )
