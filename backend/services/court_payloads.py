"""Shared payload helpers for creating and updating Court records."""

_BOOL_TRUE = {'true', '1', 'yes', 'on'}
_BOOL_FALSE = {'false', '0', 'no', 'off'}
ALLOWED_COURT_TYPES = {'dedicated', 'converted', 'shared'}

COURT_WRITABLE_FIELDS = [
    'name', 'description', 'address', 'city', 'state', 'zip_code',
    'latitude', 'longitude', 'indoor', 'lighted', 'num_courts',
    'surface_type', 'hours', 'open_play_schedule', 'fees', 'phone',
    'website', 'email', 'photo_url',
    'has_restrooms', 'has_parking', 'has_water', 'has_pro_shop',
    'has_ball_machine', 'wheelchair_accessible', 'nets_provided',
    'paddle_rental', 'skill_levels', 'court_type', 'verified',
]

_STRING_LIMITS = {
    'name': 200,
    'description': 3000,
    'address': 500,
    'city': 100,
    'state': 2,
    'zip_code': 10,
    'surface_type': 50,
    'hours': 2000,
    'open_play_schedule': 2000,
    'fees': 200,
    'phone': 30,
    'website': 500,
    'email': 200,
    'photo_url': 500,
    'skill_levels': 100,
    'court_type': 50,
}
_FLOAT_FIELDS = {'latitude', 'longitude'}
_INT_FIELDS = {'num_courts'}
_BOOL_FIELDS = {
    'indoor', 'lighted',
    'has_restrooms', 'has_parking', 'has_water', 'has_pro_shop',
    'has_ball_machine', 'wheelchair_accessible', 'nets_provided',
    'paddle_rental', 'verified',
}


def _clean_text(value, max_len):
    if value is None:
        return ''
    text = str(value).strip()
    if len(text) > max_len:
        return text[:max_len]
    return text


def _parse_float(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in _BOOL_TRUE:
        return True
    if normalized in _BOOL_FALSE:
        return False
    return None


def normalize_court_payload(raw_data, partial=False):
    """Return normalized court payload and validation errors."""
    if not isinstance(raw_data, dict):
        return {}, ['Invalid JSON payload']

    errors = []
    court_data = {}

    for field in COURT_WRITABLE_FIELDS:
        if field not in raw_data:
            continue
        value = raw_data.get(field)

        if field in _STRING_LIMITS:
            cleaned = _clean_text(value, _STRING_LIMITS[field])
            if field == 'state':
                cleaned = cleaned.upper()[:2]
            if field == 'court_type':
                cleaned = cleaned.lower()
                if cleaned and cleaned not in ALLOWED_COURT_TYPES:
                    allowed = ', '.join(sorted(ALLOWED_COURT_TYPES))
                    errors.append(f'court_type must be one of: {allowed}.')
                    continue
            court_data[field] = cleaned
            continue

        if field in _FLOAT_FIELDS:
            parsed = _parse_float(value)
            if parsed is None:
                errors.append(f'{field} must be a number.')
                continue
            if field == 'latitude' and not -90 <= parsed <= 90:
                errors.append('Latitude must be between -90 and 90.')
                continue
            if field == 'longitude' and not -180 <= parsed <= 180:
                errors.append('Longitude must be between -180 and 180.')
                continue
            court_data[field] = parsed
            continue

        if field in _INT_FIELDS:
            parsed = _parse_int(value)
            if parsed is None:
                errors.append('num_courts must be an integer.')
                continue
            if parsed < 1 or parsed > 100:
                errors.append('num_courts must be between 1 and 100.')
                continue
            court_data[field] = parsed
            continue

        if field in _BOOL_FIELDS:
            parsed = _parse_bool(value)
            if parsed is None:
                errors.append(f'{field} must be true or false.')
                continue
            court_data[field] = parsed
            continue

    if 'name' in court_data and not court_data['name']:
        errors.append('name cannot be empty.')

    if not partial:
        if not court_data.get('name') or 'latitude' not in court_data or 'longitude' not in court_data:
            errors.append('Name, latitude, and longitude are required')

    return court_data, errors


def apply_court_changes(court, court_data):
    for field, value in court_data.items():
        setattr(court, field, value)
