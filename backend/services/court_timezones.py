"""A court's stored geographic zone is independent of its opening hours."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def valid_timezone(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        return ZoneInfo(value).key
    except (ZoneInfoNotFoundError, ValueError):
        return None


def court_timezone(court, venue=None):
    from backend.services.court_hours import hours_dict
    venue = venue or {}
    community = court.structured_hours_dict() if court else {}
    for value in (getattr(court, 'timezone', None), community.get('timezone'),
                  venue.get('timezone'), hours_dict(venue.get('structured_hours')).get('timezone')):
        zone = valid_timezone(value)
        if zone:
            return zone
    return None
