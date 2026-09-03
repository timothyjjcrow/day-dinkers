"""General player-safety and product-feedback moderation APIs."""
from __future__ import annotations

import base64
import json
from datetime import timedelta

from flask import Blueprint, g, jsonify, request

from backend.app import db
from backend.models import (
    CourtPhoto,
    CourtPhotoLike,
    CourtReview,
    GameOpenCall,
    Message,
    MessageHeart,
    MessageSendAttempt,
    ModerationAction,
    PlayerFeedback,
    User,
    UserReport,
    notify,
    utcnow,
)
from backend.routes.auth import login_required
from backend.security import rate_limit


moderation_bp = Blueprint('moderation', __name__)

_QUEUE_STATUSES = {'open', 'reviewing', 'resolved', 'dismissed'}
_CONTENT_REPORT_TYPES = {'message', 'court_photo', 'court_review'}


def _operator_error(*, admin=False, mutating=False):
    role = str(g.current_user.operator_role or '')
    allowed = {'admin'} if admin else {'reviewer', 'admin'}
    if role not in allowed:
        return jsonify({'error': 'moderation_operator_required'}), 403
    if not mutating:
        return None
    if not g.current_user.mfa_enabled:
        return jsonify({'error': 'operator_mfa_required'}), 403
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    from backend.services.mfa import MFAError, verify_user_mfa
    try:
        valid, _ = verify_user_mfa(
            g.current_user, payload.get('mfa_code'), allow_recovery=False,
        )
    except MFAError:
        return jsonify({'error': 'mfa_unavailable'}), 503
    if not valid:
        return jsonify({'error': 'operator_mfa_required'}), 403
    return None


def _bounded_text(payload, field, maximum):
    return str(payload.get(field) or '').strip()[:maximum]


def _encode_cursor(report_before, feedback_before):
    raw = json.dumps(
        {'r': report_before, 'f': feedback_before}, separators=(',', ':'),
    ).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _decode_cursor(value):
    if not value:
        return None, None
    try:
        raw = str(value).strip()
        raw += '=' * (-len(raw) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(raw).decode('utf-8'))
        report_before = int(parsed['r']) if parsed.get('r') is not None else None
        feedback_before = int(parsed['f']) if parsed.get('f') is not None else None
        if (report_before is not None and report_before < 1) or (
            feedback_before is not None and feedback_before < 1
        ):
            raise ValueError
        return report_before, feedback_before
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise ValueError('invalid_cursor') from None


def _record(action, *, target_user_id=None, user_report_id=None,
            feedback_id=None, reason=''):
    db.session.add(ModerationAction(
        actor_id=g.current_user.id,
        target_user_id=target_user_id,
        user_report_id=user_report_id,
        feedback_id=feedback_id,
        action=action,
        reason=str(reason or '').strip()[:1000],
    ))


@moderation_bp.post('/reports/content')
@rate_limit(15, 3600)
@login_required
def report_content():
    """Preserve and queue one visible message, court photo, or court review."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    content_type = str(payload.get('content_type') or '').strip().lower()
    if content_type not in _CONTENT_REPORT_TYPES:
        return jsonify({'error': 'invalid_content_type'}), 400
    try:
        content_id = int(payload.get('content_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_content_id'}), 400
    if content_id < 1:
        return jsonify({'error': 'invalid_content_id'}), 400
    reason = _bounded_text(payload, 'reason', 500)
    details = _bounded_text(payload, 'details', 2000)
    if len(reason) < 3:
        return jsonify({'error': 'reason_required'}), 400

    owner_id = None
    snapshot = {}
    if content_type == 'message':
        content = db.session.get(Message, content_id)
        if not content:
            return jsonify({'error': 'message_not_found'}), 404
        from backend.routes.chat import _can_read_message
        if not _can_read_message(content, g.current_user.id):
            return jsonify({'error': 'forbidden'}), 403
        owner_id = content.sender_id
        snapshot = {
            'body': content.body,
            'has_image': bool(content.image_data),
            'sender_id': content.sender_id,
            'recipient_id': content.recipient_id,
            'court_id': content.court_id,
            'game_id': content.game_id,
            'tournament_id': content.tournament_id,
            'club_id': content.club_id,
            'crew_id': content.crew_id,
            'league_id': content.league_id,
        }
    elif content_type == 'court_photo':
        content = db.session.get(CourtPhoto, content_id)
        if not content:
            return jsonify({'error': 'photo_not_found'}), 404
        owner_id = content.user_id
        snapshot = {
            'caption': content.caption,
            'court_id': content.court_id,
            'has_photo': True,
        }
    else:
        content = db.session.get(CourtReview, content_id)
        if not content:
            return jsonify({'error': 'review_not_found'}), 404
        owner_id = content.user_id
        snapshot = {
            'rating': content.rating,
            'comment': content.comment,
            'court_id': content.court_id,
        }
    if owner_id == g.current_user.id:
        return jsonify({'error': 'cannot_report_self'}), 400

    recent = UserReport.query.filter(
        UserReport.reporter_id == g.current_user.id,
        UserReport.content_type == content_type,
        UserReport.content_id == content_id,
        UserReport.created_at >= utcnow() - timedelta(hours=24),
    ).first()
    if recent:
        return jsonify({'reported': True, 'report_id': recent.id}), 200
    report = UserReport(
        reporter_id=g.current_user.id,
        reported_id=owner_id,
        reason=reason,
        details=details,
        content_type=content_type,
        content_id=content_id,
        content_snapshot=json.dumps(
            snapshot, ensure_ascii=False, separators=(',', ':'),
        )[:10000],
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({'reported': True, 'report_id': report.id}), 201


def _remove_reported_content(report):
    """Remove the live object while retaining the report's evidence snapshot."""
    if report.content_type == 'message':
        message = db.session.get(Message, report.content_id)
        if not message:
            return False
        for call in GameOpenCall.query.filter_by(court_message_id=message.id).all():
            if call.active:
                call.active = False
                call.ended_at = utcnow()
                call.end_reason = 'moderated'
            call.court_message_id = None
        MessageHeart.query.filter_by(message_id=message.id).delete(
            synchronize_session=False,
        )
        for attempt in MessageSendAttempt.query.filter_by(message_id=message.id).all():
            attempt.message_id = None
            attempt.deleted_at = utcnow()
        db.session.delete(message)
        return True
    if report.content_type == 'court_photo':
        photo = db.session.get(CourtPhoto, report.content_id)
        if not photo:
            return False
        CourtPhotoLike.query.filter_by(photo_id=photo.id).delete(
            synchronize_session=False,
        )
        db.session.delete(photo)
        return True
    if report.content_type == 'court_review':
        review = db.session.get(CourtReview, report.content_id)
        if not review:
            return False
        db.session.delete(review)
        return True
    return False


@moderation_bp.get('/admin/moderation/queue')
@rate_limit(120, 60)
@login_required
def moderation_queue():
    error = _operator_error()
    if error:
        return error
    kind = str(request.args.get('kind') or 'all').strip().lower()
    if kind not in {'all', 'reports', 'feedback'}:
        return jsonify({'error': 'invalid_kind'}), 400
    status = str(request.args.get('status') or 'open').strip().lower()
    if status != 'all' and status not in _QUEUE_STATUSES:
        return jsonify({'error': 'invalid_status'}), 400
    try:
        limit = max(1, min(int(request.args.get('limit') or 50), 100))
        report_before, feedback_before = _decode_cursor(request.args.get('cursor'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    fetch_limit = limit + 1
    reports = []
    feedback = []
    if kind in {'all', 'reports'}:
        query = UserReport.query
        if status != 'all':
            query = query.filter(UserReport.status == status)
        if report_before:
            query = query.filter(UserReport.id < report_before)
        reports = query.order_by(UserReport.id.desc()).limit(fetch_limit).all()
    if kind in {'all', 'feedback'}:
        query = PlayerFeedback.query
        if status != 'all':
            query = query.filter(PlayerFeedback.status == status)
        if feedback_before:
            query = query.filter(PlayerFeedback.id < feedback_before)
        feedback = query.order_by(PlayerFeedback.id.desc()).limit(fetch_limit).all()

    combined = [
        *(row.to_moderation_dict() for row in reports),
        *(row.to_moderation_dict() for row in feedback),
    ]
    combined.sort(
        key=lambda item: (item.get('created_at') or '', item['kind'], item['id']),
        reverse=True,
    )
    items = combined[:limit]
    more = len(combined) > limit or len(reports) > limit or len(feedback) > limit
    next_cursor = None
    if more and items:
        used_report_ids = [item['id'] for item in items if item['kind'] == 'user_report']
        used_feedback_ids = [item['id'] for item in items if item['kind'] == 'feedback']
        next_cursor = _encode_cursor(
            min(used_report_ids) if used_report_ids else report_before,
            min(used_feedback_ids) if used_feedback_ids else feedback_before,
        )
    return jsonify({
        'items': items,
        'next_cursor': next_cursor,
        'has_more': bool(next_cursor),
    })


@moderation_bp.get('/admin/moderation/actions')
@rate_limit(120, 60)
@login_required
def moderation_actions():
    error = _operator_error()
    if error:
        return error
    try:
        limit = max(1, min(int(request.args.get('limit') or 50), 100))
        before_id = int(request.args.get('before_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_cursor'}), 400
    query = ModerationAction.query
    if before_id > 0:
        query = query.filter(ModerationAction.id < before_id)
    rows = query.order_by(ModerationAction.id.desc()).limit(limit + 1).all()
    return jsonify({
        'items': [row.to_dict() for row in rows[:limit]],
        'next_cursor': rows[limit - 1].id if len(rows) > limit else None,
    })


@moderation_bp.patch('/admin/moderation/reports/<int:report_id>')
@rate_limit(60, 300)
@login_required
def update_user_report(report_id):
    error = _operator_error(mutating=True)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    status = str(payload.get('status') or '').strip().lower()
    action = str(payload.get('action') or 'none').strip().lower()
    if status not in {'reviewing', 'resolved', 'dismissed'}:
        return jsonify({'error': 'invalid_status'}), 400
    if action not in {'none', 'warn', 'suspend', 'remove_content'}:
        return jsonify({'error': 'invalid_moderation_action'}), 400
    if action == 'suspend' and g.current_user.operator_role != 'admin':
        return jsonify({'error': 'moderation_admin_required'}), 403
    report = (
        UserReport.query.filter_by(id=report_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not report:
        return jsonify({'error': 'report_not_found'}), 404
    if action == 'remove_content' and report.content_type not in _CONTENT_REPORT_TYPES:
        return jsonify({'error': 'content_action_not_available'}), 400
    outcome = _bounded_text(payload, 'outcome', 1000)
    report.status = status
    report.assigned_operator_id = g.current_user.id
    report.outcome = outcome
    report.resolved_at = utcnow() if status in {'resolved', 'dismissed'} else None
    if action == 'warn' and report.reported:
        notify(
            report.reported_id,
            'safety_notice',
            'A safety report was reviewed',
            outcome or 'Please review the Third Shot community guidelines.',
        )
    elif action == 'suspend' and report.reported:
        report.reported.suspended_at = utcnow()
        report.reported.suspension_reason = outcome or report.reason
        report.reported.suspended_by_id = g.current_user.id
        report.reported.auth_version = int(report.reported.auth_version or 1) + 1
    elif action == 'remove_content':
        _remove_reported_content(report)
        if report.reported:
            notify(
                report.reported_id,
                'safety_notice',
                'Content was removed after a safety review',
                outcome or 'Review the Third Shot community guidelines before posting again.',
                unread_dedupe_key=f'moderated-content:{report.id}',
            )
    if status in {'resolved', 'dismissed'}:
        notify(
            report.reporter_id,
            'safety_report_update',
            'Your report was reviewed',
            outcome or (
                'The Third Shot team reviewed the report and took appropriate action.'
                if status == 'resolved'
                else 'The Third Shot team reviewed the report and closed it.'
            ),
            unread_dedupe_key=f'report-outcome:{report.id}',
        )
    _record(
        f'report_{status}' if action == 'none' else action,
        target_user_id=report.reported_id,
        user_report_id=report.id,
        reason=outcome,
    )
    db.session.commit()
    return jsonify(report.to_moderation_dict())


@moderation_bp.patch('/admin/moderation/feedback/<int:feedback_id>')
@rate_limit(60, 300)
@login_required
def update_feedback(feedback_id):
    error = _operator_error(mutating=True)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    status = str(payload.get('status') or '').strip().lower()
    if status not in {'reviewing', 'resolved', 'dismissed'}:
        return jsonify({'error': 'invalid_status'}), 400
    feedback = (
        PlayerFeedback.query.filter_by(id=feedback_id)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not feedback:
        return jsonify({'error': 'feedback_not_found'}), 404
    feedback.status = status
    feedback.assigned_operator_id = g.current_user.id
    feedback.outcome = _bounded_text(payload, 'outcome', 1000)
    feedback.resolved_at = utcnow() if status in {'resolved', 'dismissed'} else None
    if status in {'resolved', 'dismissed'}:
        notify(
            feedback.user_id,
            'feedback_update',
            'Your feedback was reviewed',
            feedback.outcome or 'Thanks for helping improve Third Shot.',
            unread_dedupe_key=f'feedback-outcome:{feedback.id}',
        )
    _record(
        f'feedback_{status}', feedback_id=feedback.id, reason=feedback.outcome,
    )
    db.session.commit()
    return jsonify(feedback.to_moderation_dict())


@moderation_bp.post('/admin/moderation/users/<int:user_id>/suspend')
@rate_limit(30, 300)
@login_required
def suspend_user(user_id):
    error = _operator_error(admin=True, mutating=True)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    reason = _bounded_text(payload, 'reason', 500)
    if len(reason) < 3:
        return jsonify({'error': 'reason_required'}), 400
    if user_id == g.current_user.id:
        return jsonify({'error': 'cannot_suspend_self'}), 400
    user = (
        User.query.filter_by(id=user_id, deleted_at=None)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not user:
        return jsonify({'error': 'user_not_found'}), 404
    if user.operator_role == 'admin':
        return jsonify({'error': 'cannot_suspend_admin'}), 409
    user.suspended_at = user.suspended_at or utcnow()
    user.suspension_reason = reason
    user.suspended_by_id = g.current_user.id
    user.auth_version = int(user.auth_version or 1) + 1
    _record('suspend', target_user_id=user.id, reason=reason)
    db.session.commit()
    return jsonify({'suspended': True, 'user_id': user.id})


@moderation_bp.post('/admin/moderation/users/<int:user_id>/restore')
@rate_limit(30, 300)
@login_required
def restore_user(user_id):
    error = _operator_error(admin=True, mutating=True)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    reason = _bounded_text(payload, 'reason', 500)
    if len(reason) < 3:
        return jsonify({'error': 'reason_required'}), 400
    user = (
        User.query.filter_by(id=user_id, deleted_at=None)
        .with_for_update().execution_options(populate_existing=True).first()
    )
    if not user:
        return jsonify({'error': 'user_not_found'}), 404
    user.suspended_at = None
    user.suspension_reason = ''
    user.suspended_by_id = None
    user.auth_version = int(user.auth_version or 1) + 1
    _record('restore', target_user_id=user.id, reason=reason)
    db.session.commit()
    return jsonify({'suspended': False, 'user_id': user.id})


@moderation_bp.delete('/admin/moderation/court-photos/<int:photo_id>')
@rate_limit(60, 300)
@login_required
def moderate_court_photo(photo_id):
    error = _operator_error(mutating=True)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    reason = _bounded_text(payload, 'reason', 1000)
    photo = db.session.get(CourtPhoto, photo_id)
    if not photo:
        return jsonify({'error': 'photo_not_found'}), 404
    owner_id = photo.user_id
    CourtPhotoLike.query.filter_by(photo_id=photo.id).delete(synchronize_session=False)
    db.session.delete(photo)
    _record('remove_court_photo', target_user_id=owner_id, reason=reason)
    db.session.commit()
    return '', 204


@moderation_bp.delete('/admin/moderation/court-reviews/<int:review_id>')
@rate_limit(60, 300)
@login_required
def moderate_court_review(review_id):
    error = _operator_error(mutating=True)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    reason = _bounded_text(payload, 'reason', 1000)
    review = db.session.get(CourtReview, review_id)
    if not review:
        return jsonify({'error': 'review_not_found'}), 404
    owner_id = review.user_id
    db.session.delete(review)
    _record('remove_court_review', target_user_id=owner_id, reason=reason)
    db.session.commit()
    return '', 204
