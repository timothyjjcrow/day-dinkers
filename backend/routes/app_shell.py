"""App shell bootstrap payloads for the React client."""
import jwt
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func

from backend.app import db
from backend.models import CheckIn, Court, Friendship, Message, Notification, User
from backend.routes.chat import _read_receipt_map, _user_session_ids
from backend.routes.sessions import _build_schedule_banner_payload
from backend.services.california_counties import CALIFORNIA_COUNTIES
from backend.services.court_payloads import DEFAULT_COUNTY_SLUG, normalize_county_slug
from backend.services.states import (
    STATE_BY_ABBR,
    SLUG_TO_ABBR,
    normalize_state_slug,
    state_name_for_abbr,
)

app_bp = Blueprint('app_shell', __name__)


def _optional_current_user():
    auth_header = str(request.headers.get('Authorization') or '').strip()
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=['HS256'],
        )
    except Exception:
        return None
    user_id = payload.get('user_id')
    if not user_id:
        return None
    return db.session.get(User, user_id)


def _friend_ids_for_user(user_id):
    if not user_id:
        return []
    friendships = Friendship.query.filter(
        ((Friendship.user_id == user_id) | (Friendship.friend_id == user_id))
        & (Friendship.status == 'accepted')
    ).all()
    return sorted({
        friendship.friend_id if friendship.user_id == user_id else friendship.user_id
        for friendship in friendships
    })


def _inbox_unread_count(user_id):
    if not user_id:
        return 0
    receipt_map = _read_receipt_map(user_id)
    total = 0
    dm_senders = db.session.query(Message.sender_id).filter(
        Message.msg_type == 'direct',
        Message.recipient_id == user_id,
    ).distinct().all()
    for (sender_id,) in dm_senders:
        last_read = receipt_map.get(('direct', sender_id), 0)
        total += Message.query.filter(
            Message.msg_type == 'direct',
            Message.sender_id == sender_id,
            Message.recipient_id == user_id,
            Message.id > last_read,
        ).count()

    for session_id in _user_session_ids(user_id):
        last_read = receipt_map.get(('session', session_id), 0)
        total += Message.query.filter(
            Message.msg_type == 'session',
            Message.session_id == session_id,
            Message.sender_id != user_id,
            Message.id > last_read,
        ).count()
    return total


def _resolve_state_abbr(raw_state):
    text = str(raw_state or '').strip()
    if not text:
        return 'CA'
    entry = STATE_BY_ABBR.get(text.upper())
    if entry:
        return entry['abbr']
    slug = normalize_state_slug(text)
    if slug:
        return SLUG_TO_ABBR.get(slug, 'CA')
    return 'CA'


def _bootstrap_states():
    rows = (
        db.session.query(
            Court.state.label('state'),
            func.count(Court.id).label('court_count'),
        )
        .filter(Court.state.isnot(None))
        .group_by(Court.state)
        .order_by(Court.state.asc())
        .all()
    )
    states = []
    for row in rows:
        abbr = str(row.state or '').strip().upper()
        if not abbr:
            continue
        states.append({
            'abbr': abbr,
            'name': state_name_for_abbr(abbr) or abbr,
            'court_count': int(row.court_count or 0),
        })
    states.sort(key=lambda item: item['name'])
    return states


def _county_name_from_slug(slug):
    cleaned = normalize_county_slug(slug, fallback=DEFAULT_COUNTY_SLUG)
    for county in CALIFORNIA_COUNTIES:
        if county['slug'] == cleaned:
            return county['name']
    return ' '.join(part.capitalize() for part in cleaned.split('-') if part) or 'Unknown'


def _bootstrap_counties(state_abbr):
    state_filter = _resolve_state_abbr(state_abbr)
    rows = (
        db.session.query(
            Court.county_slug.label('county_slug'),
            Court.state.label('state'),
            func.count(Court.id).label('court_count'),
        )
        .filter(Court.county_slug.isnot(None), Court.state == state_filter)
        .group_by(Court.county_slug, Court.state)
        .order_by(Court.county_slug.asc())
        .all()
    )

    counties = []
    seen = set()

    if state_filter == 'CA':
        counts_by_slug = {
            normalize_county_slug(row.county_slug, fallback=''): int(row.court_count or 0)
            for row in rows
        }
        for county in CALIFORNIA_COUNTIES:
            slug = county['slug']
            court_count = counts_by_slug.get(slug, 0)
            counties.append({
                'slug': slug,
                'name': county['name'],
                'state': 'CA',
                'state_name': 'California',
                'court_count': court_count,
                'has_courts': court_count > 0,
            })
            seen.add((slug, 'CA'))

    for row in rows:
        slug = normalize_county_slug(row.county_slug, fallback='')
        state = str(row.state or state_filter).upper()
        if not slug or (slug, state) in seen:
            continue
        court_count = int(row.court_count or 0)
        counties.append({
            'slug': slug,
            'name': _county_name_from_slug(slug),
            'state': state,
            'state_name': state_name_for_abbr(state) or state,
            'court_count': court_count,
            'has_courts': court_count > 0,
        })

    counties.sort(key=lambda item: item['name'])
    return counties


def _presence_summary(user_id):
    if not user_id:
        return None

    active_checkin = (
        CheckIn.query.filter_by(user_id=user_id, checked_out_at=None)
        .order_by(CheckIn.checked_in_at.desc(), CheckIn.id.desc())
        .first()
    )
    if not active_checkin:
        return {'checked_in': False}

    court = active_checkin.court
    return {
        'checked_in': True,
        'court_id': active_checkin.court_id,
        'court_name': court.name if court else 'Court',
        'court_photo_url': court.photo_url if court else '',
        'looking_for_game': bool(active_checkin.looking_for_game),
        'checked_in_at': active_checkin.checked_in_at.isoformat() if active_checkin.checked_in_at else None,
        'last_presence_ping_at': (
            active_checkin.last_presence_ping_at.isoformat()
            if active_checkin.last_presence_ping_at else None
        ),
    }


@app_bp.route('/bootstrap', methods=['GET'])
def get_bootstrap():
    from backend.routes.presence import _cleanup_stale_presence

    _cleanup_stale_presence()
    user = _optional_current_user()
    user_id = user.id if user else None
    friend_ids = _friend_ids_for_user(user_id) if user_id else []
    friends = []
    if friend_ids:
        friends = [
            friend.to_public_dict()
            for friend in User.query.filter(User.id.in_(friend_ids)).order_by(User.name.asc(), User.username.asc()).all()
        ]

    unread_notifications = Notification.query.filter_by(
        user_id=user_id,
        read=False,
    ).count() if user_id else 0
    unread_inbox = _inbox_unread_count(user_id) if user_id else 0

    state_abbr = _resolve_state_abbr(request.args.get('state'))
    county_slug = normalize_county_slug(
        request.args.get('county_slug'),
        fallback=DEFAULT_COUNTY_SLUG,
    )

    return jsonify({
        'authenticated': bool(user),
        'user': user.to_dict() if user else None,
        'friend_ids': friend_ids,
        'friends': friends,
        'unread_counts': {
            'notifications': unread_notifications,
            'inbox': unread_inbox,
            'total': unread_notifications + unread_inbox,
        },
        'location': {
            'selected_state_abbr': state_abbr,
            'selected_county_slug': county_slug,
            'default_county_slug': DEFAULT_COUNTY_SLUG,
            'states': _bootstrap_states(),
            'counties': _bootstrap_counties(state_abbr),
        },
        'schedule_banner': _build_schedule_banner_payload(
            current_user_id=user_id,
            county_slug=county_slug,
            user_only=False,
            days=7,
        ),
        'presence': _presence_summary(user_id),
    })
