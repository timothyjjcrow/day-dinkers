"""Private venue locations stay unavailable for play until claim review."""
from difflib import SequenceMatcher
import math
import re

from flask import jsonify, request
from sqlalchemy import text

from backend.app import db
from backend.models import BusinessProfile, Court


def normalized_location_text(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()


def lock_venue_submission_review():
    # New location submissions/reviews are rare. Serializing this narrow path
    # prevents two PostgreSQL transactions passing the same duplicate check.
    if db.session.get_bind().dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(75490122)'))


def possible_venue_duplicates(values, exclude_id=None):
    lat, lng = values['latitude'], values['longitude']
    name = normalized_location_text(values['name'])
    address = normalized_location_text(values.get('address'))
    rows = Court.query.filter(Court.closed.is_(False), Court.latitude.between(lat - .03, lat + .03), Court.longitude.between(lng - .05, lng + .05))
    if exclude_id:
        rows = rows.filter(Court.id != exclude_id)
    result = []
    for row in rows.limit(500).all():
        miles = math.hypot((row.latitude - lat) * 69, (row.longitude - lng) * 69 * math.cos(math.radians(lat)))
        similar = SequenceMatcher(None, name, normalized_location_text(row.name)).ratio()
        same_address = address and address == normalized_location_text(row.address)
        if (miles < .15 and similar >= .65) or (miles < 2 and similar == 1) or (miles < .4 and same_address):
            result.append(row)
    return result


def can_preview_pending_court(court, user):
    if not user:
        return False
    if user.operator_role in {'reviewer', 'admin'}:
        return True
    business = BusinessProfile.query.filter_by(court_id=court.id).first()
    if not business:
        return False
    from backend.services.business_governance import business_access_role
    return bool(business_access_role(business, user.id))


def pending_court_request_guard():
    """Block references to private locations in ordinary player API requests."""
    if not request.path.startswith('/api/'):
        return None
    ids = set()
    for key, value in (request.view_args or {}).items():
        if key == 'court_id':
            ids.add(value)
    def collect(value, depth=0):
        if depth > 5:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'court_id' or key.endswith('_court_id'):
                    try:
                        ids.add(int(child))
                    except (ValueError, TypeError):
                        pass
                elif key.endswith('court_ids') and isinstance(child, list):
                    for court_id in child[:500]:
                        try:
                            ids.add(int(court_id))
                        except (ValueError, TypeError):
                            pass
                elif isinstance(child, (dict, list)):
                    collect(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:500]:
                collect(child, depth + 1)
    collect(request.args.to_dict())
    if request.is_json:
        collect(request.get_json(silent=True))
    ids = {value for value in ids if isinstance(value, int) and 0 < value < 2 ** 63}
    if not ids:
        return None
    pending = Court.query.filter(Court.id.in_(ids), Court.pending_submission.is_(True)).all()
    if not pending:
        return None
    from backend.routes.auth import optional_current_user
    user = optional_current_user()
    for court in pending:
        preview = request.method == 'GET' and request.path == f'/api/courts/{court.id}'
        continuing_claim = request.method == 'POST' and request.path == '/api/businesses/claims'
        if not (preview or continuing_claim) or not can_preview_pending_court(court, user):
            return jsonify({'error': 'court_not_found'}), 404
    return None
