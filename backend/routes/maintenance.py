"""Scheduled maintenance for lifecycle and notification work.

The daily ``/cron/*`` routes need the cron secret. ``/tick`` is the frequent
heartbeat for time-sensitive work (hour-before reminders, stale presence,
queued phone alerts). It is public on purpose: anything may call it (the
GitHub scheduler, open apps), but a shared lease lets at most one caller per
window run the bounded, idempotent jobs; everyone else gets a no-op.
"""
from __future__ import annotations

import hmac
import os
import time

from flask import Blueprint, current_app, g, jsonify, request

from backend.app import db
from backend.models import User
from backend.security import rate_limit


maintenance_bp = Blueprint('maintenance', __name__)

TICK_INTERVAL_SECONDS = 300
TICK_BUDGET_SECONDS = 25


def _cron_authorized():
    expected = str(os.getenv('CRON_SECRET') or '').strip()
    supplied = str(request.headers.get('Authorization') or '')
    return bool(expected) and hmac.compare_digest(supplied, f'Bearer {expected}')


def _maintenance_jobs():
    # Imports stay request-local to avoid blueprint cycles at app startup.
    from backend.routes.clubs import send_club_digests
    from backend.routes.courts import cleanup_stale_presence
    from backend.routes.games import (
        auto_confirm_stale_scores,
        expire_abandoned_instant_rallies,
        expire_stale_unscored,
        roll_forward_recurring,
        maintain_game_consent,
        send_game_reminders,
        settle_game_time_votes,
    )
    from backend.routes.leagues import (
        advance_due_league_rounds,
        maintain_league_results,
        send_league_schedule_reminders,
    )
    from backend.routes.tournaments import (
        maintain_tournament_waitlists,
        maintain_tournament_results,
        send_tournament_reminders,
    )
    return [
        ('presence_cleanup', cleanup_stale_presence),
        ('score_auto_confirm', auto_confirm_stale_scores),
        ('recurring_games', roll_forward_recurring),
        ('game_consent', maintain_game_consent),
        ('instant_game_expiry', expire_abandoned_instant_rallies),
        ('unscored_game_expiry', expire_stale_unscored),
        ('game_time_votes', settle_game_time_votes),
        ('game_reminders', send_game_reminders),
        ('tournament_reminders', send_tournament_reminders),
        ('tournament_waitlists', maintain_tournament_waitlists),
        ('tournament_result_maintenance', maintain_tournament_results),
        ('league_result_maintenance', maintain_league_results),
        ('league_schedule_reminders', send_league_schedule_reminders),
        ('league_advancement', advance_due_league_rounds),
        ('club_digests', send_club_digests),
    ]


def _tick_jobs():
    """Time-sensitive work that cannot wait for the daily maintenance run."""
    from backend.routes.courts import cleanup_stale_presence
    from backend.routes.games import (
        expire_abandoned_instant_rallies,
        send_game_reminders,
        settle_game_time_votes,
    )
    from backend.routes.leagues import send_league_schedule_reminders
    from backend.routes.tournaments import send_tournament_reminders
    return [
        ('presence_cleanup', cleanup_stale_presence),
        ('instant_game_expiry', expire_abandoned_instant_rallies),
        # Before reminders, so a just-locked time gets its reminders.
        ('game_time_votes', settle_game_time_votes),
        ('game_reminders', send_game_reminders),
        ('tournament_reminders', send_tournament_reminders),
        ('league_schedule_reminders', send_league_schedule_reminders),
    ]


def _run_jobs(jobs, deadline):
    """Run each job in isolation, deferring the rest once time runs out."""
    outcomes = {}
    for name, job in jobs:
        if time.monotonic() >= deadline:
            outcomes[name] = 'deferred'
            continue
        try:
            job()
            outcomes[name] = 'ok'
        except Exception:
            db.session.rollback()
            outcomes[name] = 'failed'
            current_app.logger.exception('Scheduled maintenance job failed: %s', name)
    return outcomes


def _claim_tick_window(now):
    """True for exactly one caller per tick window, across every instance."""
    from backend.security import _increment_bucket

    window = int(now // TICK_INTERVAL_SECONDS)
    backend = current_app.config.get('RATE_LIMIT_BACKEND', 'memory')
    return _increment_bucket(
        'maintenance:tick', window, now, TICK_INTERVAL_SECONDS, backend,
    ) == 1


@maintenance_bp.post('/tick')
@rate_limit(20, 60)
def tick():
    try:
        claimed = _claim_tick_window(time.time())
    except Exception:
        current_app.logger.exception('Maintenance tick lease unavailable')
        return jsonify({'ok': False, 'ran': False}), 503
    if not claimed:
        return jsonify({'ok': True, 'ran': False})

    deadline = time.monotonic() + TICK_BUDGET_SECONDS
    outcomes = _run_jobs(_tick_jobs(), deadline)
    push_ok = True
    try:
        from backend.services.push import deliver_due_push_now

        deliver_due_push_now(limit=100, deadline=deadline)
    except Exception:
        db.session.rollback()
        push_ok = False
        current_app.logger.exception('Tick push delivery failed')
    # This tick already delivered what its reminders queued.
    g.pop('push_outbox_pending', None)
    failed = any(outcome == 'failed' for outcome in outcomes.values())
    return jsonify({'ok': push_ok and not failed, 'ran': True})


@maintenance_bp.get('/cron/push')
def drain_push():
    if not _cron_authorized():
        return jsonify({'error': 'cron_authentication_required'}), 401
    from backend.services.push import drain_push_outbox

    try:
        result = drain_push_outbox(
            limit=250,
            deadline=time.monotonic() + 50,
        )
        return jsonify({'ok': True, 'push': result})
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Scheduled push outbox drain failed')
        return jsonify({'ok': False, 'error': 'push_drain_failed'}), 500


def _run_user_nudges(deadline):
    """Run idempotent user-specific nudges within the function time budget."""
    from backend.routes.auth import (
        _maybe_nearby_games_digest,
        _maybe_streak_nag,
        _maybe_weekly_recap,
    )

    user_ids = [row[0] for row in db.session.query(User.id).filter(
        User.deleted_at.is_(None),
    ).order_by(User.id.asc()).limit(2000).all()]
    processed = 0
    for user_id in user_ids:
        if time.monotonic() >= deadline:
            break
        user = db.session.get(User, user_id)
        if not user:
            continue
        _maybe_weekly_recap(user)
        _maybe_nearby_games_digest(user)
        _maybe_streak_nag(user)
        processed += 1
    return {'processed': processed, 'remaining': max(0, len(user_ids) - processed)}


@maintenance_bp.get('/cron/maintenance')
def run_maintenance():
    if not _cron_authorized():
        return jsonify({'error': 'cron_authentication_required'}), 401

    started = time.monotonic()
    budget = min(max(int(os.getenv('MAINTENANCE_CRON_BUDGET_SECONDS', '50')), 5), 55)
    deadline = started + budget
    outcomes = _run_jobs(_maintenance_jobs(), deadline)

    nudges = {'processed': 0, 'remaining': 0}
    if time.monotonic() < deadline:
        try:
            nudges = _run_user_nudges(deadline)
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Scheduled user nudges failed')
            nudges = {'processed': 0, 'remaining': 0, 'failed': True}

    failed = sorted(name for name, outcome in outcomes.items() if outcome == 'failed')
    return jsonify({
        'ok': not failed,
        'jobs': outcomes,
        'failed': failed,
        'nudges': nudges,
        'duration_ms': round((time.monotonic() - started) * 1000),
    }), (207 if failed else 200)
