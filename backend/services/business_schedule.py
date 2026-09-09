"""Dated views of manager schedules, including explicit occurrence exceptions."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json


def overrides_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value or '{}')
        except (TypeError, ValueError):
            value = {}
    return value if isinstance(value, dict) else {}


def local_today(timezone='', now=None):
    try:
        zone = ZoneInfo(timezone or 'UTC')
    except (ValueError, ZoneInfoNotFoundError):
        zone = UTC
    instant = now or datetime.now(UTC)
    return instant.replace(tzinfo=UTC).astimezone(zone).date() if instant.tzinfo is None else instant.astimezone(zone).date()


def occurrence_fields(row, on):
    """Future changes apply in date order, then this date's explicit override."""
    result = dict(row)
    rules = overrides_dict(row.get('occurrence_overrides'))
    key = on.isoformat()
    for starts, changes in sorted((rules.get('following') or {}).items()):
        if starts <= key:
            result.update(changes)
    result.update((rules.get('dates') or {}).get(key, {}))
    return result


def occurs_on(row, on):
    recurrence = row.get('recurrence') or 'weekly'
    if recurrence == 'dated':
        return row.get('event_date') == on.isoformat()
    if recurrence == 'date_range' and not (
        (row.get('start_date') or '9999') <= on.isoformat() <= (row.get('end_date') or '')
    ):
        return False
    day = str(row.get('day_of_week') or '').lower()
    return day == on.strftime('%A').lower() or day == 'daily' or (
        day == 'weekdays' and on.weekday() < 5
    ) or (day == 'weekends' and on.weekday() >= 5)


def dated_occurrences(rows, start, end, *, include_hidden=False):
    items = []
    for offset in range((end - start).days + 1):
        on = start + timedelta(days=offset)
        for row in rows:
            effective = occurrence_fields(row, on)
            if not occurs_on(effective, on) or (not include_hidden and not effective.get('active', True)):
                continue
            item = {**effective, 'schedule_item_id': row.get('id'),
                    'occurrence_on': on.isoformat(), 'event_date': on.isoformat(),
                    'recurrence': 'dated', 'series_recurrence': row.get('recurrence', 'weekly')}
            item.pop('occurrence_overrides', None)
            item['is_exception'] = effective != row
            try:
                zone = ZoneInfo(item.get('timezone') or 'UTC')
            except (ValueError, ZoneInfoNotFoundError):
                zone = UTC
                item['time_warning'] = 'Venue time zone needs updating.'
            for clock_key, stamp_key in [('start_time', 'starts_at'), ('end_time', 'ends_at')]:
                try:
                    local = datetime.combine(on, time.fromisoformat(item[clock_key]), tzinfo=zone)
                    instant = local.astimezone(UTC)
                    if instant.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                        item[stamp_key] = None
                        item['time_warning'] = 'This time is skipped by daylight saving. Choose another time.'
                    else:
                        item[stamp_key] = instant.isoformat().replace('+00:00', 'Z')
                except (KeyError, ValueError):
                    item[stamp_key] = None
            updated = item.get('availability_updated_at')
            fresh = False
            if updated:
                try:
                    fresh = datetime.fromisoformat(updated.replace('Z', '+00:00')) >= datetime.now(UTC) - timedelta(days=2)
                except (TypeError, ValueError):
                    pass
            item['availability_fresh'] = fresh
            item['availability_as_of'] = datetime.fromisoformat(updated.replace('Z', '+00:00')).astimezone(zone).strftime('%b %d, %I:%M %p') if fresh else None
            spots = item.get('spots_remaining')
            item['availability_label'] = (
                'Cancelled' if item.get('status') == 'cancelled' else
                'Completed' if item.get('status') == 'completed' else
                'Check availability' if spots is None or not fresh else
                'Full' if spots == 0 else f'{spots} places · checked {item["availability_as_of"]}'
            )
            items.append(item)
    return sorted(items, key=lambda item: (item['event_date'], item.get('start_time') or '', item.get('title') or '', item.get('id') or 0))
