"""Ranked queue management routes."""
from flask import request, jsonify
from backend.app import db
from backend.models import RankedQueue, CheckIn, Court
from backend.auth_utils import login_required
from backend.routes.ranked import ranked_bp
from backend.routes.ranked.helpers import (
    _ALLOWED_MATCH_TYPES, _prune_queue_for_court, _expire_stale_items,
    _user_checked_in_at_court, _emit_ranked_update,
)


@ranked_bp.route('/queue/<int:court_id>', methods=['GET'])
def get_queue(court_id):
    """Return queue filtered to checked-in players (read-only, no DB writes)."""
    active_user_ids = db.session.query(CheckIn.user_id).filter(
        CheckIn.court_id == court_id,
        CheckIn.checked_out_at.is_(None),
    )
    entries = RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        RankedQueue.user_id.in_(active_user_ids),
    ).order_by(RankedQueue.joined_at.asc()).all()
    return jsonify({'queue': [e.to_dict() for e in entries]})


@ranked_bp.route('/queue/join', methods=['POST'])
@login_required
def join_queue():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    match_type = str(data.get('match_type', 'doubles')).strip().lower()

    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    if match_type not in _ALLOWED_MATCH_TYPES:
        return jsonify({'error': 'Invalid match type'}), 400
    court = db.session.get(Court, court_id)
    if not court:
        return jsonify({'error': 'Court not found'}), 404
    if not _user_checked_in_at_court(request.current_user.id, court_id):
        return jsonify({'error': 'Check in at this court before joining the ranked queue'}), 400

    _prune_queue_for_court(court_id)
    _expire_stale_items(court_id)

    existing = RankedQueue.query.filter_by(
        user_id=request.current_user.id, court_id=court_id
    ).first()
    if existing:
        return jsonify({'error': 'Already in queue at this court'}), 409

    RankedQueue.query.filter(
        RankedQueue.user_id == request.current_user.id,
        RankedQueue.court_id != court_id,
    ).delete(synchronize_session=False)

    entry = RankedQueue(
        user_id=request.current_user.id,
        court_id=court_id,
        match_type=match_type,
    )
    db.session.add(entry)
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='queue_join')
    return jsonify({'message': 'Joined queue', 'entry': entry.to_dict()}), 201


@ranked_bp.route('/queue/leave', methods=['POST'])
@login_required
def leave_queue():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid JSON payload'}), 400
    try:
        court_id = int(data.get('court_id'))
    except (TypeError, ValueError):
        court_id = None
    if not court_id:
        return jsonify({'error': 'Court ID required'}), 400
    RankedQueue.query.filter_by(
        user_id=request.current_user.id, court_id=court_id
    ).delete()
    db.session.commit()
    _emit_ranked_update(court_id=court_id, reason='queue_leave')
    return jsonify({'message': 'Left queue'})
