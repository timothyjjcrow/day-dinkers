import base64
import binascii
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from backend.app import db
from backend.models import CourtCommunityInfo, CourtEvent, CourtImage
from backend.services.court_payloads import ALLOWED_COURT_TYPES
from backend.time_utils import utcnow_naive


_SPAM_TERMS = {
    'viagra', 'casino', 'crypto', 'bitcoin', 'airdrop', 'nft',
    'free money', 'loan', 'click here',
}

_BOOL_TRUE = {'true', '1', 'yes', 'on'}
_BOOL_FALSE = {'false', '0', 'no', 'off'}


def safe_json_loads(raw_value, fallback):
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError):
        return fallback


def parse_reviewer_list(raw_value):
    if not raw_value:
        return set()
    return {item.strip().lower() for item in raw_value.split(',') if item.strip()}


def is_reviewer(user, config):
    _ = config
    return bool(getattr(user, 'is_admin', False))


def should_auto_apply(config, analysis):
    enabled = bool(config.get('COURT_UPDATE_AUTO_APPLY', False))
    if not enabled:
        return False
    try:
        threshold = float(config.get('COURT_UPDATE_AUTO_APPLY_THRESHOLD', 0.92))
    except (TypeError, ValueError):
        threshold = 0.92
    return (
        analysis.get('recommendation') == 'high_confidence'
        and float(analysis.get('score', 0)) >= threshold
    )


def _clean_text(value, max_len=4000):
    if value is None:
        return ''
    text = str(value).strip()
    if len(text) > max_len:
        return text[:max_len]
    return text


def _clean_short(value, max_len=200):
    return _clean_text(value, max_len=max_len)


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


def _parse_int(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(raw_value):
    value = _clean_text(raw_value, max_len=64)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _valid_http_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def _is_data_image_url(url):
    return url.startswith('data:image/') and ';base64,' in url


def _data_url_size_bytes(data_url):
    try:
        encoded = data_url.split(';base64,', 1)[1]
    except IndexError:
        return None
    try:
        return len(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error):
        return None


def normalize_submission_payload(raw_data, max_images=8, max_events=6, max_image_bytes=2 * 1024 * 1024):
    errors = []
    if not isinstance(raw_data, dict):
        return None, ['Invalid payload']

    summary = _clean_text(raw_data.get('summary'), max_len=500)
    source_notes = _clean_text(raw_data.get('source_notes'), max_len=2000)
    confidence_level = _clean_short(raw_data.get('confidence_level'), max_len=20).lower()
    if confidence_level not in {'low', 'medium', 'high'}:
        confidence_level = 'medium'

    if len(summary) < 10:
        errors.append('Please include a short summary (at least 10 characters).')

    location = raw_data.get('location') if isinstance(raw_data.get('location'), dict) else {}
    normalized_location = {}
    for field in ['address', 'city', 'zip_code']:
        value = _clean_text(location.get(field), max_len=200)
        if value:
            normalized_location[field] = value

    state = _clean_text(location.get('state'), max_len=10).upper()
    if state:
        normalized_location['state'] = state[:2]

    latitude = _parse_float(location.get('latitude'))
    longitude = _parse_float(location.get('longitude'))
    if latitude is not None:
        if latitude < -90 or latitude > 90:
            errors.append('Latitude must be between -90 and 90.')
        else:
            normalized_location['latitude'] = latitude
    if longitude is not None:
        if longitude < -180 or longitude > 180:
            errors.append('Longitude must be between -180 and 180.')
        else:
            normalized_location['longitude'] = longitude

    court_info = raw_data.get('court_info') if isinstance(raw_data.get('court_info'), dict) else {}
    normalized_court_info = {}

    for field, max_len in [
        ('name', 200),
        ('description', 3000),
        ('surface_type', 80),
        ('fees', 300),
        ('phone', 40),
        ('website', 500),
        ('email', 200),
        ('skill_levels', 120),
        ('court_type', 80),
    ]:
        value = _clean_text(court_info.get(field), max_len=max_len)
        if value:
            normalized_court_info[field] = value

    if 'court_type' in normalized_court_info:
        normalized_court_info['court_type'] = normalized_court_info['court_type'].lower()
        if normalized_court_info['court_type'] not in ALLOWED_COURT_TYPES:
            allowed = ', '.join(sorted(ALLOWED_COURT_TYPES))
            errors.append(f'court_type must be one of: {allowed}.')
            normalized_court_info.pop('court_type', None)

    num_courts = _parse_int(court_info.get('num_courts'))
    if num_courts is not None:
        if num_courts < 1 or num_courts > 100:
            errors.append('Number of courts must be between 1 and 100.')
        else:
            normalized_court_info['num_courts'] = num_courts

    for bool_field in [
        'indoor', 'lighted',
        'has_restrooms', 'has_parking', 'has_water', 'has_pro_shop',
        'has_ball_machine', 'wheelchair_accessible', 'nets_provided', 'paddle_rental',
    ]:
        parsed = _parse_bool(court_info.get(bool_field))
        if parsed is not None:
            normalized_court_info[bool_field] = parsed

    hours_data = raw_data.get('hours') if isinstance(raw_data.get('hours'), dict) else {}
    normalized_hours = {}
    for field, max_len in [('hours', 1000), ('open_play_schedule', 1000), ('hours_notes', 1200)]:
        value = _clean_text(hours_data.get(field), max_len=max_len)
        if value:
            normalized_hours[field] = value

    community = raw_data.get('community_notes') if isinstance(raw_data.get('community_notes'), dict) else {}
    normalized_community = {}
    for field, max_len in [
        ('location_notes', 1200),
        ('parking_notes', 1200),
        ('access_notes', 1200),
        ('court_rules', 1200),
        ('best_times', 800),
        ('closure_notes', 1200),
        ('additional_info', 2000),
    ]:
        value = _clean_text(community.get(field), max_len=max_len)
        if value:
            normalized_community[field] = value

    images_in = raw_data.get('images') if isinstance(raw_data.get('images'), list) else []
    normalized_images = []
    seen_image_urls = set()
    for item in images_in:
        if not isinstance(item, dict):
            continue
        image_url = _clean_text(item.get('image_url') or item.get('url') or item.get('data_url'), max_len=4_000_000)
        caption = _clean_text(item.get('caption'), max_len=200)
        if not image_url:
            continue
        if _is_data_image_url(image_url):
            image_bytes = _data_url_size_bytes(image_url)
            if image_bytes is None:
                errors.append('One uploaded image is invalid base64 data.')
                continue
            if image_bytes > max_image_bytes:
                errors.append('One uploaded image exceeds the size limit.')
                continue
        elif not _valid_http_url(image_url):
            errors.append('Image links must start with http:// or https://')
            continue

        if image_url in seen_image_urls:
            continue
        seen_image_urls.add(image_url)
        normalized_images.append({
            'image_url': image_url,
            'caption': caption,
        })
        if len(normalized_images) >= max_images:
            break

    if len(images_in) > max_images:
        errors.append(f'You can submit up to {max_images} images at a time.')

    events_in = raw_data.get('events') if isinstance(raw_data.get('events'), list) else []
    normalized_events = []
    for event in events_in:
        if not isinstance(event, dict):
            continue
        title = _clean_text(event.get('title'), max_len=200)
        if not title:
            continue

        start_dt = _parse_iso_datetime(event.get('start_time'))
        end_dt = _parse_iso_datetime(event.get('end_time'))
        if not start_dt:
            errors.append(f'Event "{title}" is missing a valid start time.')
            continue
        if end_dt and end_dt < start_dt:
            errors.append(f'Event "{title}" has an end time before its start.')
            continue

        event_link = _clean_text(event.get('link'), max_len=500)
        if event_link and not _valid_http_url(event_link):
            errors.append(f'Event "{title}" has an invalid link.')
            event_link = ''

        normalized_events.append({
            'title': title,
            'start_time': start_dt.isoformat(),
            'end_time': end_dt.isoformat() if end_dt else None,
            'description': _clean_text(event.get('description'), max_len=2000),
            'organizer': _clean_text(event.get('organizer'), max_len=200),
            'contact': _clean_text(event.get('contact'), max_len=200),
            'link': event_link,
            'recurring': _clean_text(event.get('recurring'), max_len=80),
        })
        if len(normalized_events) >= max_events:
            break

    if len(events_in) > max_events:
        errors.append(f'You can submit up to {max_events} events at a time.')

    has_structured_update = any([
        normalized_location,
        normalized_court_info,
        normalized_hours,
        normalized_community,
        normalized_images,
        normalized_events,
    ])
    if not has_structured_update:
        errors.append('Add at least one update field, image, or event before submitting.')

    normalized_payload = {
        'summary': summary,
        'source_notes': source_notes,
        'confidence_level': confidence_level,
        'location': normalized_location,
        'court_info': normalized_court_info,
        'hours': normalized_hours,
        'community_notes': normalized_community,
        'images': normalized_images,
        'events': normalized_events,
    }
    return normalized_payload, errors


def analyze_submission(payload):
    score = 0.45
    flags = []
    notes = []

    summary = payload.get('summary', '')
    if len(summary) >= 40:
        score += 0.15
        notes.append('Detailed summary provided.')
    elif len(summary) >= 20:
        score += 0.08

    if payload.get('source_notes'):
        score += 0.05
        notes.append('Included source/context notes.')

    location = payload.get('location', {})
    if 'latitude' in location and 'longitude' in location:
        score += 0.08
        notes.append('Precise location coordinates included.')

    hours = payload.get('hours', {})
    if hours.get('hours'):
        score += 0.08

    image_count = len(payload.get('images', []))
    if image_count:
        score += min(image_count * 0.03, 0.12)
        notes.append(f'{image_count} image(s) attached.')

    event_count = len(payload.get('events', []))
    if event_count:
        score += min(event_count * 0.04, 0.12)
        notes.append(f'{event_count} event(s) included.')

    blob = ' '.join([
        payload.get('summary', ''),
        payload.get('source_notes', ''),
        payload.get('community_notes', {}).get('additional_info', ''),
    ]).lower()
    if any(term in blob for term in _SPAM_TERMS):
        flags.append('Contains potentially spammy language.')
        score -= 0.35

    if summary and re.fullmatch(r'[^a-z]*[A-Z\s\d\W]{12,}', summary):
        flags.append('Summary uses mostly all caps.')
        score -= 0.1

    data_url_count = sum(1 for img in payload.get('images', []) if _is_data_image_url(img.get('image_url', '')))
    if data_url_count >= 4:
        flags.append('Large image upload volume; manual review recommended.')
        score -= 0.05

    score = max(0.0, min(0.99, score))
    if flags:
        recommendation = 'needs_manual_review'
    elif score >= 0.86:
        recommendation = 'high_confidence'
    elif score >= 0.7:
        recommendation = 'review_recommended'
    else:
        recommendation = 'needs_manual_review'

    return {
        'score': round(score, 2),
        'flags': flags,
        'notes': notes,
        'recommendation': recommendation,
    }


def apply_payload_to_court(court, payload, submitted_by_user_id=None, source_submission_id=None):
    location = payload.get('location', {}) or {}
    for field in ['address', 'city', 'state', 'zip_code']:
        value = location.get(field)
        if value:
            setattr(court, field, value)
    if location.get('latitude') is not None:
        court.latitude = location['latitude']
    if location.get('longitude') is not None:
        court.longitude = location['longitude']

    court_info = payload.get('court_info', {}) or {}
    for field in [
        'name', 'description', 'surface_type', 'fees',
        'phone', 'website', 'email', 'skill_levels', 'court_type',
    ]:
        value = court_info.get(field)
        if value:
            setattr(court, field, value)

    if court_info.get('num_courts') is not None:
        court.num_courts = court_info['num_courts']

    for bool_field in [
        'indoor', 'lighted',
        'has_restrooms', 'has_parking', 'has_water', 'has_pro_shop',
        'has_ball_machine', 'wheelchair_accessible', 'nets_provided', 'paddle_rental',
    ]:
        if bool_field in court_info and court_info[bool_field] is not None:
            setattr(court, bool_field, bool(court_info[bool_field]))

    hours = payload.get('hours', {}) or {}
    if hours.get('hours'):
        court.hours = hours['hours']
    if hours.get('open_play_schedule'):
        court.open_play_schedule = hours['open_play_schedule']

    community_notes = payload.get('community_notes', {}) or {}
    community = CourtCommunityInfo.query.filter_by(court_id=court.id).first()
    if not community:
        community = CourtCommunityInfo(court_id=court.id)
        db.session.add(community)
    for field in [
        'location_notes', 'parking_notes', 'access_notes',
        'court_rules', 'best_times', 'closure_notes', 'additional_info',
    ]:
        value = community_notes.get(field)
        if value:
            setattr(community, field, value)
    if hours.get('hours_notes'):
        community.hours_notes = hours['hours_notes']
    community.last_updated_at = utcnow_naive()

    for image in payload.get('images', []):
        image_url = image.get('image_url')
        if not image_url:
            continue
        existing = CourtImage.query.filter_by(court_id=court.id, image_url=image_url).first()
        if existing:
            if image.get('caption'):
                existing.caption = image['caption']
            existing.approved = True
            continue
        db.session.add(CourtImage(
            court_id=court.id,
            submitted_by_user_id=submitted_by_user_id,
            source_submission_id=source_submission_id,
            image_url=image_url,
            caption=image.get('caption', ''),
            approved=True,
        ))

    for event in payload.get('events', []):
        title = event.get('title')
        start_dt = _parse_iso_datetime(event.get('start_time'))
        end_dt = _parse_iso_datetime(event.get('end_time'))
        if not title or not start_dt:
            continue
        duplicate = CourtEvent.query.filter_by(
            court_id=court.id,
            title=title,
            start_time=start_dt,
        ).first()
        if duplicate:
            duplicate.description = event.get('description', duplicate.description)
            duplicate.end_time = end_dt
            duplicate.organizer = event.get('organizer', duplicate.organizer)
            duplicate.contact = event.get('contact', duplicate.contact)
            duplicate.link = event.get('link', duplicate.link)
            duplicate.recurring = event.get('recurring', duplicate.recurring)
            duplicate.approved = True
            continue

        db.session.add(CourtEvent(
            court_id=court.id,
            submitted_by_user_id=submitted_by_user_id,
            source_submission_id=source_submission_id,
            title=title,
            description=event.get('description', ''),
            start_time=start_dt,
            end_time=end_dt,
            organizer=event.get('organizer', ''),
            contact=event.get('contact', ''),
            link=event.get('link', ''),
            recurring=event.get('recurring', ''),
            approved=True,
        ))


def submission_payload_preview(payload, redact_data_urls=False):
    if not isinstance(payload, dict):
        return {}
    preview = {
        'summary': payload.get('summary', ''),
        'source_notes': payload.get('source_notes', ''),
        'confidence_level': payload.get('confidence_level', 'medium'),
        'location': payload.get('location', {}),
        'court_info': payload.get('court_info', {}),
        'hours': payload.get('hours', {}),
        'community_notes': payload.get('community_notes', {}),
        'images': [],
        'events': payload.get('events', []),
    }

    for image in payload.get('images', []):
        image_url = image.get('image_url', '')
        if redact_data_urls and _is_data_image_url(image_url):
            image_url = 'data:image/<uploaded>'
        preview['images'].append({
            'image_url': image_url,
            'caption': image.get('caption', ''),
        })
    return preview
