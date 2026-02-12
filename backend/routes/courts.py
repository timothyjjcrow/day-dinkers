import json
import math
from datetime import timedelta
from flask import Blueprint, current_app, request, jsonify
from sqlalchemy import func
from backend.app import db
from backend.models import (
    User, Court, CourtReport, CheckIn, ActivityLog, Notification, PlaySession,
    CourtCommunityInfo, CourtImage, CourtEvent, CourtUpdateSubmission
)
from backend.auth_utils import login_required, admin_required
from backend.time_utils import utcnow_naive
from backend.services.california_counties import CALIFORNIA_COUNTIES
from backend.services.court_payloads import (
    normalize_court_payload, apply_court_changes,
    normalize_county_slug, DEFAULT_COUNTY_SLUG,
)
from backend.services.court_updates import (
    analyze_submission, apply_payload_to_court,
    normalize_submission_payload, safe_json_loads, should_auto_apply,
    submission_payload_preview,
)

courts_bp = Blueprint('courts', __name__)


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 3959
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _active_play_sessions_query(court_id=None):
    """Active sessions, excluding timed 'now' sessions that have ended."""
    now = utcnow_naive()
    query = PlaySession.query.filter(PlaySession.status == 'active')
    if court_id is not None:
        query = query.filter(PlaySession.court_id == court_id)
    return query.filter(
        (PlaySession.session_type != 'now')
        | PlaySession.end_time.is_(None)
        | (PlaySession.end_time > now)
    )


def _serialize_submission(submission, include_payload=False, redact_data_urls=False):
    data = submission.to_dict(include_payload=False)
    if include_payload:
        payload = safe_json_loads(submission.payload_json, {})
        data['payload'] = submission_payload_preview(
            payload,
            redact_data_urls=redact_data_urls,
        )
    return data


def _is_admin_user(user):
    return bool(getattr(user, 'is_admin', False))


def _county_name_from_slug(slug):
    cleaned = normalize_county_slug(slug, fallback=DEFAULT_COUNTY_SLUG)
    return ' '.join(part.capitalize() for part in cleaned.split('-') if part) or 'Unknown'


def _serialize_report(report):
    court = db.session.get(Court, report.court_id)
    reporter = db.session.get(User, report.user_id)
    return {
        'id': report.id,
        'court_id': report.court_id,
        'court_name': court.name if court else '',
        'court_city': court.city if court else '',
        'user_id': report.user_id,
        'reported_by': reporter.to_dict() if reporter else None,
        'reason': report.reason,
        'description': report.description,
        'status': report.status,
        'created_at': report.created_at.isoformat() if report.created_at else None,
    }


def _configured_reviewer_users():
    return User.query.filter_by(is_admin=True).all()


def _notify_reviewers_of_submission(submission):
    reviewers = _configured_reviewer_users()
    if not reviewers:
        return
    for reviewer in reviewers:
        if reviewer.id == submission.user_id:
            continue
        db.session.add(Notification(
            user_id=reviewer.id,
            notif_type='court_update_review',
            content=f'Court update pending review for {submission.court.name}',
            reference_id=submission.id,
        ))


def _notify_reviewers_of_report(report):
    reviewers = _configured_reviewer_users()
    if not reviewers:
        return
    court = db.session.get(Court, report.court_id)
    court_name = court.name if court else 'a court'
    for reviewer in reviewers:
        if reviewer.id == report.user_id:
            continue
        db.session.add(Notification(
            user_id=reviewer.id,
            notif_type='court_report_review',
            content=f'Court report pending review for {court_name}',
            reference_id=report.id,
        ))


def _normalize_id_list(raw_ids, max_ids=200):
    if not isinstance(raw_ids, list):
        return []
    ids = []
    seen = set()
    for raw in raw_ids:
        try:
            item_id = int(raw)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or item_id in seen:
            continue
        ids.append(item_id)
        seen.add(item_id)
        if len(ids) >= max_ids:
            break
    return ids


def _review_submission(submission, action, reviewer, reviewer_notes=''):
    if submission.status != 'pending':
        return False, 'Submission already reviewed'
    if action not in {'approve', 'reject'}:
        return False, 'Invalid action'

    if action == 'approve':
        payload = safe_json_loads(submission.payload_json, {})
        court = db.session.get(Court, submission.court_id)
        if not court:
            return False, 'Court not found'
        apply_payload_to_court(
            court,
            payload,
            submitted_by_user_id=submission.user_id,
            source_submission_id=submission.id,
        )
        submission.status = 'approved'
    else:
        submission.status = 'rejected'

    submission.reviewer_id = reviewer.id
    submission.reviewer_notes = str(reviewer_notes or '').strip()[:2000]
    submission.reviewed_at = utcnow_naive()

    status_word = 'approved' if submission.status == 'approved' else 'rejected'
    court_name = submission.court.name if submission.court else 'this court'
    db.session.add(Notification(
        user_id=submission.user_id,
        notif_type='court_update_result',
        content=f'Your court update for {court_name} was {status_word}.',
        reference_id=submission.court_id,
    ))
    return True, status_word


def _review_report(report, action):
    if report.status != 'pending':
        return False, 'Report already reviewed'
    if action not in {'resolve', 'dismiss'}:
        return False, 'Invalid action'

    report.status = 'resolved' if action == 'resolve' else 'dismissed'
    court = db.session.get(Court, report.court_id)
    court_name = court.name if court else 'a court'
    status_word = 'resolved' if report.status == 'resolved' else 'dismissed'
    db.session.add(Notification(
        user_id=report.user_id,
        notif_type='court_report_result',
        content=f'Your report for {court_name} was {status_word}.',
        reference_id=report.court_id,
    ))
    return True, status_word


@courts_bp.route('', methods=['GET'])
def get_courts():
    _cleanup_stale_checkins()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    radius = request.args.get('radius', 50, type=float)
    indoor = request.args.get('indoor', type=str)
    lighted = request.args.get('lighted', type=str)
    search = request.args.get('search', '')
    city = request.args.get('city', '')
    raw_county = (request.args.get('county_slug') or '').strip()
    county_slug = normalize_county_slug(raw_county, fallback=DEFAULT_COUNTY_SLUG)
    if raw_county.lower() == 'all':
        county_slug = ''

    query = Court.query
    if county_slug:
        query = query.filter(Court.county_slug == county_slug)
    if search:
        query = query.filter(
            Court.name.ilike(f'%{search}%') | Court.address.ilike(f'%{search}%')
            | Court.city.ilike(f'%{search}%') | Court.description.ilike(f'%{search}%')
        )
    if city:
        query = query.filter(Court.city.ilike(f'%{city}%'))
    if indoor == 'true':
        query = query.filter_by(indoor=True)
    elif indoor == 'false':
        query = query.filter_by(indoor=False)
    if lighted == 'true':
        query = query.filter_by(lighted=True)

    courts = query.all()

    results = []
    for court in courts:
        court_dict = court.to_dict()
        active_checkins = CheckIn.query.filter_by(
            court_id=court.id, checked_out_at=None
        ).count()
        court_dict['active_players'] = active_checkins

        # Count active open-to-play sessions at this court
        session_count = _active_play_sessions_query(court_id=court.id).count()
        court_dict['open_sessions'] = session_count

        if lat is not None and lng is not None:
            dist = haversine_distance(lat, lng, court.latitude, court.longitude)
            if dist <= radius:
                court_dict['distance'] = round(dist, 1)
                results.append(court_dict)
        else:
            results.append(court_dict)

    if lat is not None:
        results.sort(key=lambda c: c.get('distance', 9999))
    return jsonify({
        'courts': results,
        'county_slug': county_slug or 'all',
    })


@courts_bp.route('/counties', methods=['GET'])
def get_counties():
    rows = (
        db.session.query(
            Court.county_slug.label('county_slug'),
            func.count(Court.id).label('court_count'),
        )
        .filter(Court.county_slug.isnot(None))
        .group_by(Court.county_slug)
        .order_by(Court.county_slug.asc())
        .all()
    )
    counts_by_slug = {}
    for row in rows:
        slug = normalize_county_slug(row.county_slug, fallback='')
        if not slug:
            continue
        counts_by_slug[slug] = int(row.court_count or 0)

    counties = []
    seen = set()
    for county in CALIFORNIA_COUNTIES:
        slug = county['slug']
        count = counts_by_slug.get(slug, 0)
        counties.append({
            'slug': slug,
            'name': county['name'],
            'court_count': count,
            'has_courts': count > 0,
        })
        seen.add(slug)

    for slug in sorted(counts_by_slug):
        if slug in seen:
            continue
        count = counts_by_slug.get(slug, 0)
        counties.append({
            'slug': slug,
            'name': _county_name_from_slug(slug),
            'court_count': count,
            'has_courts': count > 0,
        })

    return jsonify({
        'counties': counties,
        'default_county_slug': DEFAULT_COUNTY_SLUG,
    })


@courts_bp.route('/resolve-county', methods=['GET'])
def resolve_county():
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng query parameters are required'}), 400

    candidates = (
        db.session.query(
            Court.id,
            Court.name,
            Court.county_slug,
            Court.latitude,
            Court.longitude,
        )
        .all()
    )
    if not candidates:
        return jsonify({'error': 'No courts available to resolve county'}), 404

    nearest = None
    nearest_dist = None
    for court in candidates:
        dist = haversine_distance(lat, lng, court.latitude, court.longitude)
        if nearest is None or dist < nearest_dist:
            nearest = court
            nearest_dist = dist

    county_slug = normalize_county_slug(getattr(nearest, 'county_slug', ''), fallback=DEFAULT_COUNTY_SLUG)
    return jsonify({
        'county_slug': county_slug,
        'county_name': _county_name_from_slug(county_slug),
        'nearest_court_id': nearest.id,
        'nearest_court_name': nearest.name,
        'distance_miles': round(nearest_dist or 0, 1),
    })


@courts_bp.route('/<int:court_id>', methods=['GET'])
def get_court(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    court_dict = court.to_dict()
    active_checkins = CheckIn.query.filter_by(
        court_id=court.id, checked_out_at=None
    ).all()
    court_dict['active_players'] = len(active_checkins)
    court_dict['checked_in_users'] = []
    for ci in active_checkins:
        user_data = ci.user.to_dict()
        user_data['looking_for_game'] = ci.looking_for_game
        user_data['checked_in_at'] = ci.checked_in_at.isoformat()
        court_dict['checked_in_users'].append(user_data)

    # Active play sessions at this court (replaces upcoming_games)
    active_sessions = _active_play_sessions_query(court_id=court_id) \
        .order_by(PlaySession.created_at.desc()) \
        .all()
    court_dict['play_sessions'] = [s.to_dict() for s in active_sessions]
    # Backward compatibility with legacy clients/tests expecting this key.
    court_dict['upcoming_games'] = court_dict['play_sessions']

    # Busyness data
    court_dict['busyness'] = _get_busyness(court_id)

    # Recent activity
    day_ago = utcnow_naive() - timedelta(hours=24)
    recent = CheckIn.query.filter(
        CheckIn.court_id == court_id,
        CheckIn.checked_in_at >= day_ago
    ).all()
    court_dict['recent_visitors'] = len(recent)

    # Community-maintained details (published/approved only)
    community_info = CourtCommunityInfo.query.filter_by(court_id=court_id).first()
    court_dict['community_info'] = community_info.to_dict() if community_info else {}

    approved_images = CourtImage.query.filter_by(
        court_id=court_id,
        approved=True,
    ).order_by(CourtImage.created_at.desc()).limit(24).all()
    court_dict['images'] = [img.to_dict() for img in approved_images]
    if not court_dict.get('photo_url') and approved_images:
        court_dict['photo_url'] = approved_images[0].image_url

    now_utc = utcnow_naive()
    upcoming_events = CourtEvent.query.filter(
        CourtEvent.court_id == court_id,
        CourtEvent.approved.is_(True),
        (
            ((CourtEvent.end_time.is_(None)) & (CourtEvent.start_time >= now_utc))
            | ((CourtEvent.end_time.isnot(None)) & (CourtEvent.end_time >= now_utc))
        )
    ).order_by(CourtEvent.start_time.asc()).limit(20).all()
    court_dict['upcoming_events'] = [event.to_dict() for event in upcoming_events]
    court_dict['pending_updates_count'] = CourtUpdateSubmission.query.filter_by(
        court_id=court_id,
        status='pending',
    ).count()

    return jsonify({'court': court_dict})


@courts_bp.route('', methods=['POST'])
@login_required
def add_court():
    data = request.get_json(silent=True) or {}
    court_data, errors = normalize_court_payload(data, partial=False)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 400

    court = Court(**court_data)
    db.session.add(court)
    db.session.commit()
    return jsonify({'court': court.to_dict()}), 201


@courts_bp.route('/<int:court_id>', methods=['PUT'])
@admin_required
def update_court(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    data = request.get_json(silent=True) or {}
    court_data, errors = normalize_court_payload(data, partial=True)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 400
    if not court_data:
        return jsonify({'error': 'No valid court fields were provided'}), 400

    apply_court_changes(court, court_data)
    db.session.commit()
    return jsonify({'court': court.to_dict()})


@courts_bp.route('/<int:court_id>/report', methods=['POST'])
@login_required
def report_court(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    data = request.get_json() or {}
    report = CourtReport(
        court_id=court_id, user_id=request.current_user.id,
        reason=data.get('reason', 'other'),
        description=data.get('description', ''),
    )
    db.session.add(report)
    db.session.flush()
    _notify_reviewers_of_report(report)
    db.session.commit()
    return jsonify({'message': 'Report submitted'}), 201


@courts_bp.route('/<int:court_id>/updates', methods=['POST'])
@login_required
def submit_court_update(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    data = request.get_json() or {}
    payload, errors = normalize_submission_payload(
        data,
        max_images=current_app.config.get('COURT_UPDATE_MAX_IMAGES', 8),
        max_events=current_app.config.get('COURT_UPDATE_MAX_EVENTS', 6),
        max_image_bytes=current_app.config.get('COURT_UPDATE_MAX_IMAGE_BYTES', 2 * 1024 * 1024),
    )
    if errors:
        return jsonify({'error': 'Invalid submission', 'errors': errors}), 400

    analysis = analyze_submission(payload)
    submission = CourtUpdateSubmission(
        court_id=court_id,
        user_id=request.current_user.id,
        status='pending',
        summary=payload.get('summary', ''),
        payload_json=json.dumps(payload),
        analysis_json=json.dumps(analysis),
    )
    db.session.add(submission)
    db.session.flush()

    if should_auto_apply(current_app.config, analysis):
        apply_payload_to_court(
            court,
            payload,
            submitted_by_user_id=request.current_user.id,
            source_submission_id=submission.id,
        )
        submission.status = 'approved'
        submission.auto_applied = True
        submission.reviewer_id = request.current_user.id
        submission.reviewer_notes = 'Auto-applied by configured policy.'
        submission.reviewed_at = utcnow_naive()
    else:
        _notify_reviewers_of_submission(submission)

    db.session.commit()
    return jsonify({
        'message': (
            'Update was auto-approved and published.'
            if submission.auto_applied
            else 'Update submitted for review.'
        ),
        'submission': _serialize_submission(
            submission,
            include_payload=True,
            redact_data_urls=True,
        ),
    }), 201


@courts_bp.route('/<int:court_id>/updates/mine', methods=['GET'])
@login_required
def get_my_court_updates(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    submissions = CourtUpdateSubmission.query.filter_by(
        court_id=court_id,
        user_id=request.current_user.id,
    ).order_by(CourtUpdateSubmission.created_at.desc()).limit(25).all()
    is_admin = _is_admin_user(request.current_user)
    return jsonify({
        'submissions': [_serialize_submission(s, include_payload=False) for s in submissions],
        'is_admin': is_admin,
        'is_reviewer': is_admin,
    })


def _admin_status_payload():
    is_admin = _is_admin_user(request.current_user)
    return {
        'is_admin': is_admin,
        # Backward compatibility for existing frontend code paths.
        'is_reviewer': is_admin,
        'auto_apply_enabled': bool(current_app.config.get('COURT_UPDATE_AUTO_APPLY', False)),
        'auto_apply_threshold': current_app.config.get('COURT_UPDATE_AUTO_APPLY_THRESHOLD', 0.92),
    }


@courts_bp.route('/updates/admin-status', methods=['GET'])
@login_required
def get_admin_status():
    return jsonify(_admin_status_payload())


@courts_bp.route('/updates/reviewer-status', methods=['GET'])
@login_required
def get_reviewer_status():
    return jsonify(_admin_status_payload())


@courts_bp.route('/reports', methods=['GET'])
@admin_required
def get_report_review_queue():
    status = (request.args.get('status', 'pending') or 'pending').strip().lower()
    if status not in {'pending', 'resolved', 'dismissed', 'all'}:
        return jsonify({'error': 'Invalid status filter'}), 400

    reason = (request.args.get('reason', '') or '').strip().lower()
    court_id = request.args.get('court_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100

    query = CourtReport.query
    if status != 'all':
        query = query.filter_by(status=status)
    if court_id is not None:
        query = query.filter_by(court_id=court_id)
    if reason:
        query = query.filter(CourtReport.reason.ilike(f'%{reason}%'))

    if status == 'pending':
        query = query.order_by(CourtReport.created_at.asc())
    else:
        query = query.order_by(CourtReport.created_at.desc())

    reports = query.limit(limit).all()
    return jsonify({'reports': [_serialize_report(report) for report in reports]})


@courts_bp.route('/reports/review/bulk', methods=['POST'])
@admin_required
def bulk_review_court_reports():
    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    if action not in {'resolve', 'dismiss'}:
        return jsonify({'error': 'Action must be resolve or dismiss'}), 400

    report_ids = _normalize_id_list(data.get('ids'), max_ids=200)
    if not report_ids:
        return jsonify({'error': 'Provide at least one report ID'}), 400

    processed_ids = []
    failed = []
    for report_id in report_ids:
        report = db.session.get(CourtReport, report_id)
        if not report:
            failed.append({'id': report_id, 'error': 'Report not found'})
            continue
        ok, reason = _review_report(report, action)
        if ok:
            processed_ids.append(report_id)
        else:
            failed.append({'id': report_id, 'error': reason})

    db.session.commit()
    return jsonify({
        'message': f'Bulk report action complete ({len(processed_ids)} processed, {len(failed)} failed)',
        'processed_count': len(processed_ids),
        'failed_count': len(failed),
        'processed_ids': processed_ids,
        'failed': failed,
    })


@courts_bp.route('/reports/<int:report_id>/review', methods=['POST'])
@admin_required
def review_court_report(report_id):
    report = db.session.get(CourtReport, report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    if report.status != 'pending':
        return jsonify({'error': 'Report already reviewed'}), 409

    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    ok, status_word = _review_report(report, action)
    if not ok:
        if status_word == 'Invalid action':
            return jsonify({'error': 'Action must be resolve or dismiss'}), 400
        return jsonify({'error': status_word}), 409

    db.session.commit()
    return jsonify({
        'message': f'Report {status_word}',
        'report': _serialize_report(report),
    })


@courts_bp.route('/updates/review', methods=['GET'])
@admin_required
def get_update_review_queue():
    status = (request.args.get('status', 'pending') or 'pending').strip().lower()
    if status not in {'pending', 'approved', 'rejected', 'all'}:
        return jsonify({'error': 'Invalid status filter'}), 400

    court_id = request.args.get('court_id', type=int)
    limit = request.args.get('limit', 30, type=int)
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100

    query = CourtUpdateSubmission.query
    if status != 'all':
        query = query.filter_by(status=status)
    if court_id is not None:
        query = query.filter_by(court_id=court_id)

    if status == 'pending':
        query = query.order_by(CourtUpdateSubmission.created_at.asc())
    else:
        query = query.order_by(CourtUpdateSubmission.created_at.desc())

    submissions = query.limit(limit).all()
    return jsonify({
        'submissions': [
            _serialize_submission(s, include_payload=True, redact_data_urls=True)
            for s in submissions
        ],
    })


@courts_bp.route('/updates/review/bulk', methods=['POST'])
@admin_required
def bulk_review_update_submissions():
    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    if action not in {'approve', 'reject'}:
        return jsonify({'error': 'Action must be approve or reject'}), 400

    submission_ids = _normalize_id_list(data.get('ids'), max_ids=200)
    if not submission_ids:
        return jsonify({'error': 'Provide at least one submission ID'}), 400

    reviewer_notes = str(data.get('reviewer_notes') or '').strip()[:2000]

    processed_ids = []
    failed = []
    for submission_id in submission_ids:
        submission = db.session.get(CourtUpdateSubmission, submission_id)
        if not submission:
            failed.append({'id': submission_id, 'error': 'Submission not found'})
            continue
        ok, reason = _review_submission(
            submission,
            action,
            reviewer=request.current_user,
            reviewer_notes=reviewer_notes,
        )
        if ok:
            processed_ids.append(submission_id)
        else:
            failed.append({'id': submission_id, 'error': reason})

    db.session.commit()
    return jsonify({
        'message': f'Bulk update action complete ({len(processed_ids)} processed, {len(failed)} failed)',
        'processed_count': len(processed_ids),
        'failed_count': len(failed),
        'processed_ids': processed_ids,
        'failed': failed,
    })


@courts_bp.route('/updates/<int:submission_id>/review', methods=['POST'])
@admin_required
def review_update_submission(submission_id):
    submission = db.session.get(CourtUpdateSubmission, submission_id)
    if not submission:
        return jsonify({'error': 'Submission not found'}), 404
    if submission.status != 'pending':
        return jsonify({'error': 'Submission already reviewed'}), 409

    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    reviewer_notes = str(data.get('reviewer_notes') or '').strip()[:2000]
    ok, status_word = _review_submission(
        submission,
        action,
        reviewer=request.current_user,
        reviewer_notes=reviewer_notes,
    )
    if not ok:
        if status_word == 'Invalid action':
            return jsonify({'error': 'Action must be approve or reject'}), 400
        if status_word == 'Court not found':
            return jsonify({'error': status_word}), 404
        return jsonify({'error': status_word}), 409

    db.session.commit()
    return jsonify({
        'message': f'Submission {status_word}',
        'submission': _serialize_submission(
            submission,
            include_payload=True,
            redact_data_urls=True,
        ),
    })


@courts_bp.route('/<int:court_id>/invite', methods=['POST'])
@login_required
def invite_to_court(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404

    data = request.get_json()
    friend_ids = data.get('friend_ids', [])
    if not friend_ids:
        return jsonify({'error': 'No friends selected'}), 400

    count = 0
    for fid in friend_ids:
        notif = Notification(
            user_id=fid, notif_type='court_invite',
            content=f'{request.current_user.username} invited you to play at {court.name}',
            reference_id=court_id,
        )
        db.session.add(notif)
        count += 1
    db.session.commit()
    return jsonify({'message': f'Invited {count} friend(s)', 'invited_count': count})


@courts_bp.route('/<int:court_id>/busyness', methods=['GET'])
def get_court_busyness(court_id):
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    return jsonify({'busyness': _get_busyness(court_id)})


def _cleanup_stale_checkins():
    cutoff = utcnow_naive() - timedelta(hours=4)
    stale = CheckIn.query.filter(
        CheckIn.checked_out_at.is_(None),
        CheckIn.checked_in_at < cutoff
    ).all()
    for ci in stale:
        ci.checked_out_at = utcnow_naive()
    if stale:
        db.session.commit()


def _get_busyness(court_id):
    thirty_days_ago = utcnow_naive().date() - timedelta(days=30)
    logs = ActivityLog.query.filter(
        ActivityLog.court_id == court_id,
        ActivityLog.date >= thirty_days_ago,
    ).all()

    if not logs:
        return {}

    agg = {}
    for log in logs:
        day = log.day_of_week
        hour = log.hour
        if day not in agg:
            agg[day] = {}
        if hour not in agg[day]:
            agg[day][hour] = []
        agg[day][hour].append(log.player_count)

    result = {}
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for day_num, day_name in enumerate(days):
        if day_num in agg:
            result[day_name] = {}
            for hour, counts in agg[day_num].items():
                result[day_name][str(hour)] = round(sum(counts) / len(counts), 1)
    return result
