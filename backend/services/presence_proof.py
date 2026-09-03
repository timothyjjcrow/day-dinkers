"""Short-lived, court-bound proof for physical instant-rally starts.

The browser supplies a fresh device fix to the check-in endpoint.  The server
recomputes the conservative distance to the selected court and, only when the
whole accuracy circle fits inside the arrival radius, returns a signed token.
The rally endpoint can therefore require an on-site assertion without storing
precise device coordinates or trusting a client-side distance calculation.
"""

import math

from flask import current_app
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer


ARRIVAL_RADIUS_METERS = 150.0
MAX_LOCATION_ACCURACY_METERS = 65.0
PRESENCE_PROOF_MAX_AGE_SECONDS = 5 * 60
_TOKEN_SALT = 'instant-rally-presence-v1'


def _finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _distance_meters(first, second):
    """Great-circle distance for two ``(latitude, longitude)`` pairs."""
    radius = 6_371_008.8
    lat1, lng1 = (math.radians(value) for value in first)
    lat2, lng2 = (math.radians(value) for value in second)
    d_lat = lat2 - lat1
    d_lng = lng2 - lng1
    haversine = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(haversine)))


def validate_court_presence_location(court, location):
    """Validate a fresh device fix against ``court``.

    Returns ``(result, error_code)``.  The successful result intentionally
    contains only rounded distance/accuracy diagnostics; callers must not
    persist the precise coordinates.
    """
    if court.latitude is None or court.longitude is None:
        return None, 'court_location_unavailable'
    if not isinstance(location, dict):
        return None, 'invalid_presence_location'
    latitude = _finite_number(location.get('latitude'))
    longitude = _finite_number(location.get('longitude'))
    accuracy = _finite_number(location.get('accuracy_meters'))
    if (
        latitude is None or longitude is None or accuracy is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
        or accuracy <= 0
    ):
        return None, 'invalid_presence_location'
    if accuracy > MAX_LOCATION_ACCURACY_METERS:
        return None, 'location_accuracy_too_low'
    distance = _distance_meters(
        (latitude, longitude),
        (float(court.latitude), float(court.longitude)),
    )
    # The reported point alone is not enough: its entire uncertainty circle
    # must fit inside the same 150 m threshold used by automatic check-in.
    if distance + accuracy > ARRIVAL_RADIUS_METERS:
        return None, 'court_proximity_mismatch'
    return {
        'distance_meters': round(distance, 1),
        'accuracy_meters': round(accuracy, 1),
    }, None


def _serializer():
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'], salt=_TOKEN_SALT,
    )


def issue_instant_rally_presence_proof(user_id, court_id):
    return _serializer().dumps({
        'v': 1,
        'user_id': int(user_id),
        'court_id': int(court_id),
    })


def verify_instant_rally_presence_proof(token, user_id, court_id):
    if not isinstance(token, str) or not token:
        return False, 'presence_proof_required'
    try:
        value = _serializer().loads(
            token, max_age=PRESENCE_PROOF_MAX_AGE_SECONDS,
        )
    except SignatureExpired:
        return False, 'presence_proof_expired'
    except BadData:
        return False, 'invalid_presence_proof'
    valid = (
        isinstance(value, dict)
        and value.get('v') == 1
        and value.get('user_id') == int(user_id)
        and value.get('court_id') == int(court_id)
    )
    return (True, None) if valid else (False, 'invalid_presence_proof')
