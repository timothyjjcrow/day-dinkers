"""One public hours projection: reviewed venue override, community fallback."""
import json
import re
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
CLOCK = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')


def hours_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value or '{}')
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def normalize_hours(value, timezone=''):
    """Validate explicit weekly hours and bounded date exceptions; no guessing."""
    if not isinstance(value, dict):
        raise ValueError('invalid_structured_hours')
    if not value:
        return {}
    if set(value) - {*DAYS, 'timezone', 'exceptions'}:
        raise ValueError('invalid_hours_field')
    zone = str(value.get('timezone') or timezone or '').strip()
    try:
        if not zone:
            raise ValueError
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('hours_timezone_required')

    def windows(raw):
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 4:
            raise ValueError('invalid_hours_windows')
        result = []
        for row in raw:
            if not isinstance(row, dict) or set(row) != {'open', 'close'} or not all(isinstance(row[key], str) and CLOCK.fullmatch(row[key]) for key in ('open', 'close')):
                raise ValueError('invalid_hours_time')
            if row not in result:
                result.append(dict(row))
        return sorted(result, key=lambda row: row['open'])

    result = {'timezone': zone}
    # Missing legacy weekdays remain unknown, not implicitly closed.
    for day in DAYS:
        if day in value:
            result[day] = windows(value[day])
    exceptions = value.get('exceptions', {})
    if not isinstance(exceptions, dict) or len(exceptions) > 100:
        raise ValueError('invalid_hours_exceptions')
    result['exceptions'] = {}
    for on, raw in sorted(exceptions.items()):
        try:
            if date.fromisoformat(on).isoformat() != on:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('invalid_hours_date')
        result['exceptions'][on] = windows(raw)
    if not any(day in result for day in DAYS):
        raise ValueError('hours_week_required')
    return result


def hours_status(schedule, *, dawn=False, notes='', closed=False, as_of=None):
    if closed:
        return {'state': 'closed', 'is_open': False, 'label': 'Closed'}
    if dawn:
        return {'state': 'dawn_to_dusk', 'is_open': None, 'label': 'Dawn to dusk'}
    schedule = hours_dict(schedule)
    if not schedule:
        return {'state': 'unavailable', 'is_open': None, 'label': notes or 'Hours not listed'}
    zone_name = str(schedule.get('timezone') or '')
    try:
        if not zone_name:
            raise ValueError
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return {'state': 'unavailable', 'is_open': None, 'label': 'Hours time zone not listed'}
    instant = as_of or datetime.now(UTC)
    now = (instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant).astimezone(zone)
    exceptions = hours_dict(schedule.get('exceptions'))
    intervals = []
    for offset in range(-1, 8):
        on = now.date() + timedelta(days=offset)
        raw = exceptions.get(on.isoformat(), schedule.get(DAYS[on.weekday()]))
        for row in raw if isinstance(raw, list) else [raw]:
            if not isinstance(row, dict):
                continue
            try:
                start = datetime.combine(on, datetime.strptime(row['open'], '%H:%M').time(), zone)
                end = datetime.combine(on, datetime.strptime(row['close'], '%H:%M').time(), zone)
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                end += timedelta(days=1)
            # An exception replaces the entire calendar date, including a
            # normal overnight interval carried in from yesterday.
            if end.date() > on and end.date().isoformat() in exceptions:
                end = datetime.combine(on + timedelta(days=1), datetime.min.time(), zone)
            intervals.append((start, end))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    intervals = merged
    current = next(((start, end) for start, end in sorted(intervals) if start <= now < end), None)
    clock = lambda value: value.strftime('%-I:%M %p').replace(':00 ', ' ')
    if current:
        label = 'Open 24 hours' if current[1]-current[0] >= timedelta(days=7) else f'Open until {"" if current[1].date() == now.date() else current[1].strftime("%a ")}{clock(current[1])}'
        return {'state':'open','is_open':True,'label':label,'timezone':zone_name}
    today_known = now.date().isoformat() in exceptions or DAYS[now.weekday()] in schedule
    if not today_known:
        return {'state':'unavailable','is_open':None,'label':'Today’s hours not listed','timezone':zone_name}
    upcoming = next(((start, end) for start, end in sorted(intervals) if start > now), None)
    label = f'Opens {"" if upcoming[0].date() == now.date() else upcoming[0].strftime("%a ")}{clock(upcoming[0])}' if upcoming else 'Closed today'
    return {'state':'closed','is_open':False,'label':label,'timezone':zone_name}


def project_hours(court, venue=None, *, as_of=None):
    venue = venue or {}
    configured = bool(hours_dict(venue.get('structured_hours')) or venue.get('hours_dawn_to_dusk'))
    schedule = hours_dict(venue.get('structured_hours')) if configured else court.structured_hours_dict() if court else {}
    dawn = bool(venue.get('hours_dawn_to_dusk')) if configured else bool(getattr(court, 'hours_dawn_to_dusk', False))
    notes = str(venue.get('hours') or '') if configured else getattr(court, 'hours', '')
    return {'structured_hours':schedule, 'hours_dawn_to_dusk':dawn, 'hours':notes,
            'hours_source':'venue' if configured else 'community',
            'open_status':hours_status(schedule, dawn=dawn, notes=notes, closed=bool(getattr(court, 'closed', False)), as_of=as_of)}


def reviewed_hours(profile):
    if profile.reviewed_public_snapshot:
        return profile.reviewed_snapshot_dict().get('profile') or {}
    return {key:getattr(profile,key) for key in ('structured_hours','hours_dawn_to_dusk','hours')}


def public_hours_for(courts, *, as_of=None):
    from backend.services.business_visibility import public_business_query
    from backend.models import BusinessProfile
    courts = list(courts)
    overrides = {profile.court_id:reviewed_hours(profile) for profile in public_business_query().filter(BusinessProfile.court_id.in_([court.id for court in courts])).all()} if courts else {}
    return {court.id:project_hours(court, overrides.get(court.id), as_of=as_of) for court in courts}


def interval_hours_conflict(projection, starts_at, ends_at=None):
    """A known closed portion conflicts; unknown hours never mean closed.

    Check interval boundaries as well as each listed opening/closing boundary,
    so split hours, overnight carry and calendar exceptions agree with Open now.
    This is an information warning, not cancellation of an existing game.
    """
    schedule = hours_dict(projection.get('structured_hours'))
    if not schedule or projection.get('hours_dawn_to_dusk') or not starts_at:
        return False
    try:
        zone = ZoneInfo(schedule.get('timezone') or '')
        start = datetime.fromisoformat(starts_at.replace('Z', '+00:00')).astimezone(UTC)
        end = datetime.fromisoformat(ends_at.replace('Z', '+00:00')).astimezone(UTC) if ends_at else start + timedelta(seconds=1)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return False
    if end <= start:
        end = start + timedelta(seconds=1)
    probes = {start, end - timedelta(microseconds=1)}
    on, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    exceptions = hours_dict(schedule.get('exceptions'))
    while on <= last:
        probes.add(datetime.combine(on, datetime.min.time(), zone).astimezone(UTC))
        raw = exceptions.get(on.isoformat(), schedule.get(DAYS[on.weekday()]))
        for row in raw if isinstance(raw, list) else [raw]:
            if not isinstance(row, dict):
                continue
            for value in (row.get('open'), row.get('close')):
                try:
                    boundary = datetime.combine(on, datetime.strptime(value, '%H:%M').time(), zone).astimezone(UTC)
                    probes.update((boundary, boundary - timedelta(microseconds=1)))
                except (ValueError, TypeError):
                    pass
        on += timedelta(days=1)
    return any(hours_status(schedule, as_of=instant)['is_open'] is False
               for instant in probes if start <= instant < end)
