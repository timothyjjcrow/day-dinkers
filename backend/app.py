"""Flask application bootstrap."""
import os
import mimetypes
import threading
import time

from flask import Flask, jsonify, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy

from backend.config import PG_SCHEMA, get_config

db = SQLAlchemy(session_options={'expire_on_commit': False})

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(PROJECT_ROOT, 'public')
BUNDLED_COURTS_FILE = os.path.join(PROJECT_ROOT, 'data', 'courts.json.gz')
FRONTEND_RELEASE = 'r58'
FRONTEND_RELEASE_FILES = frozenset({
    'app-v15.min.js',
    'app-v15.min.js.map',
    'crew-planner-v15.min.js',
    'crew-planner-v15.min.js.map',
    'styles-v15.min.css',
    'styles-v15.min.css.map',
})


_seed_thread_started = False


def _seed_courts_background(app):
    """Import the bundled court data on first boot (runs in a thread so deploys
    don't time out while ~18k rows insert)."""
    with app.app_context():
        try:
            from backend.models import Court
            from backend.seed import import_courts_file
            if Court.query.count() > 0:
                return
            count = import_courts_file(BUNDLED_COURTS_FILE)
            app.logger.info('Auto-seeded %s courts from bundled data', count)
        except Exception:
            app.logger.exception('Court auto-seed failed')


def _maybe_auto_seed(app):
    global _seed_thread_started
    if _seed_thread_started or not app.config.get('AUTO_SEED_COURTS'):
        return
    if not os.path.exists(BUNDLED_COURTS_FILE):
        app.logger.warning('AUTO_SEED_COURTS set but %s is missing', BUNDLED_COURTS_FILE)
        return
    from backend.models import Court
    try:
        if Court.query.count() > 0:
            return
    except Exception:
        app.logger.exception('Could not check court count for auto-seed')
        return
    _seed_thread_started = True
    threading.Thread(target=_seed_courts_background, args=(app,), daemon=True).start()


def _ensure_pg_schema(app):
    """On Postgres the app lives in its own schema (search_path is set via
    connect_args), fully isolated from legacy tables in `public`."""
    if db.engine.dialect.name != 'postgresql':
        return
    from sqlalchemy import text

    from backend.config import PG_SCHEMA
    try:
        with db.engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PG_SCHEMA}"'))
    except Exception:
        app.logger.exception('Could not ensure schema %s exists', PG_SCHEMA)
        if app.config.get('APP_ENV') == 'production':
            raise


def _migrate_legacy_schema(app):
    """If the database holds the pre-rebuild schema (user table without the new
    columns), rename every old table aside so create_all can build fresh ones.
    Old data is preserved under *_legacy_<timestamp> rather than dropped."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()
        if 'user' not in tables:
            return
        columns = {c['name'] for c in inspector.get_columns('user')}
        if {'password_hash', 'display_name', 'rating'} <= columns:
            return
        suffix = time.strftime('legacy_%Y%m%d%H%M%S')
        app.logger.warning(
            'Incompatible legacy schema detected — renaming %d tables to *_%s',
            len(tables), suffix,
        )
        with db.engine.begin() as conn:
            for table in tables:
                conn.execute(text(f'ALTER TABLE "{table}" RENAME TO "{table}_{suffix}"'))
    except Exception:
        app.logger.exception('Legacy schema migration failed')
        if app.config.get('APP_ENV') == 'production':
            raise


def _clear_conflicting_legacy_indexes(app):
    """Renaming a table does not rename its indexes (Postgres and SQLite), so
    indexes belonging to *_legacy_* tables can still shadow names that
    create_all needs. Move those aside too."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        existing_tables = inspector.get_table_names()
        model_tables = set(db.metadata.tables.keys())
        wanted = {
            idx.name
            for table in db.metadata.tables.values()
            for idx in table.indexes
        }
        conflicts = []
        for table in existing_tables:
            if table in model_tables:
                continue
            for idx in inspector.get_indexes(table):
                name = idx.get('name')
                if name in wanted:
                    conflicts.append(name)
        if not conflicts:
            return
        suffix = time.strftime('legacy_%Y%m%d%H%M%S')
        app.logger.warning('Moving %d legacy indexes out of the way', len(conflicts))
        dialect = db.engine.dialect.name
        with db.engine.begin() as conn:
            for name in conflicts:
                if dialect == 'postgresql':
                    conn.execute(text(f'ALTER INDEX "{name}" RENAME TO "{name}_{suffix}"'))
                else:
                    conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    except Exception:
        app.logger.exception('Legacy index cleanup failed')
        if app.config.get('APP_ENV') == 'production':
            raise


def _upgrade_schema(app):
    """Tiny additive migrations for existing databases (create_all only builds
    brand-new tables, it never alters existing ones)."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()
        is_postgres = db.engine.dialect.name == 'postgresql'
        statements = []

        # Business organizations are the parent of the new reusable venue-team
        # relationship, so install this additive table before adding the
        # organization_id reference to a long-lived BusinessProfile table.
        if 'user' in tables:
            from backend.models import (
                AccountActionToken,
                BusinessOrganization,
                ModerationAction,
                PlayerFeedback,
                PushOutbox,
                PushSubscription,
                UserReport,
            )
            BusinessOrganization.__table__.create(db.engine, checkfirst=True)
            AccountActionToken.__table__.create(db.engine, checkfirst=True)
            PushSubscription.__table__.create(db.engine, checkfirst=True)
            PushOutbox.__table__.create(db.engine, checkfirst=True)
            UserReport.__table__.create(db.engine, checkfirst=True)
            PlayerFeedback.__table__.create(db.engine, checkfirst=True)
            ModerationAction.__table__.create(db.engine, checkfirst=True)
            if 'business_organization' not in tables:
                tables.append('business_organization')
            if 'account_action_token' not in tables:
                tables.append('account_action_token')
            if 'push_subscription' not in tables:
                tables.append('push_subscription')
            if 'push_outbox' not in tables:
                tables.append('push_outbox')

            # A browser push endpoint must belong to exactly one account. Keep
            # the newest binding if an older release admitted a concurrent
            # duplicate, then make the invariant database-enforced.
            push_indexes = {
                index.get('name')
                for index in sa_inspect(db.engine).get_indexes('push_subscription')
            }
            if 'uq_push_subscription_endpoint' not in push_indexes:
                statements.extend([
                    'DELETE FROM push_subscription WHERE id NOT IN '
                    '(SELECT MAX(id) FROM push_subscription GROUP BY endpoint)',
                    'CREATE UNIQUE INDEX uq_push_subscription_endpoint '
                    'ON push_subscription (endpoint)',
                ])

        if 'message' in tables:
            columns = {c['name'] for c in inspector.get_columns('message')}
            if 'court_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN court_id INTEGER')
                if is_postgres:
                    statements.append('ALTER TABLE message ALTER COLUMN recipient_id DROP NOT NULL')
            if 'game_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN game_id INTEGER')
            if 'tournament_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN tournament_id INTEGER')
            if 'club_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN club_id INTEGER')
            if 'crew_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN crew_id INTEGER')
            if 'league_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN league_id INTEGER')
            if 'conversation_id' not in columns:
                statements.append(
                    'ALTER TABLE message ADD COLUMN conversation_id INTEGER'
                )
            if 'image_data' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN image_data TEXT')
            if 'hearted' not in columns:
                statements.append(
                    'ALTER TABLE message ADD COLUMN hearted BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )
            if 'client_attempt_id' not in columns:
                statements.append(
                    'ALTER TABLE message ADD COLUMN client_attempt_id VARCHAR(64)'
                )
            if 'client_attempt_fingerprint' not in columns:
                statements.append(
                    'ALTER TABLE message ADD COLUMN client_attempt_fingerprint VARCHAR(64)'
                )
            statements.append(
                'CREATE INDEX IF NOT EXISTS ix_message_crew_id ON message (crew_id)'
            )
            statements.append(
                'CREATE INDEX IF NOT EXISTS ix_message_conversation_id '
                'ON message (conversation_id)'
            )

        if 'user' in tables:
            user_cols = {c['name'] for c in inspector.get_columns('user')}
            for col, ddl in (
                ('last_lat', 'ALTER TABLE "user" ADD COLUMN last_lat DOUBLE PRECISION'),
                ('last_lng', 'ALTER TABLE "user" ADD COLUMN last_lng DOUBLE PRECISION'),
                ('last_location_at', 'ALTER TABLE "user" ADD COLUMN last_location_at TIMESTAMP'),
                ('last_active_at', 'ALTER TABLE "user" ADD COLUMN last_active_at TIMESTAMP'),
                ('nearby_visibility', "ALTER TABLE \"user\" ADD COLUMN nearby_visibility VARCHAR(16) NOT NULL DEFAULT 'everyone'"),
                ('last_games_digest_week', 'ALTER TABLE "user" ADD COLUMN last_games_digest_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('last_streak_nag_week', 'ALTER TABLE "user" ADD COLUMN last_streak_nag_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('home_lat', 'ALTER TABLE "user" ADD COLUMN home_lat DOUBLE PRECISION'),
                ('home_lng', 'ALTER TABLE "user" ADD COLUMN home_lng DOUBLE PRECISION'),
                ('home_area', 'ALTER TABLE "user" ADD COLUMN home_area VARCHAR(120)'),
                ('avatar_url', "ALTER TABLE \"user\" ADD COLUMN avatar_url VARCHAR(500) NOT NULL DEFAULT ''"),
                ('avatar_data', 'ALTER TABLE "user" ADD COLUMN avatar_data TEXT'),
                ('deleted_at', 'ALTER TABLE "user" ADD COLUMN deleted_at TIMESTAMP'),
                ('availability', 'ALTER TABLE "user" ADD COLUMN availability TEXT NOT NULL DEFAULT \'[]\''),
                ('last_recap_week', 'ALTER TABLE "user" ADD COLUMN last_recap_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('muted_notifications', 'ALTER TABLE "user" ADD COLUMN muted_notifications TEXT NOT NULL DEFAULT \'[]\''),
                ('notified_badges', 'ALTER TABLE "user" ADD COLUMN notified_badges TEXT NOT NULL DEFAULT \'[]\''),
                ('calendar_token', 'ALTER TABLE "user" ADD COLUMN calendar_token VARCHAR(64)'),
                ('best_rating', 'ALTER TABLE "user" ADD COLUMN best_rating INTEGER NOT NULL DEFAULT 1200'),
                ('skill_rating', 'ALTER TABLE "user" ADD COLUMN skill_rating DOUBLE PRECISION'),
                ('dupr_rating', 'ALTER TABLE "user" ADD COLUMN dupr_rating DOUBLE PRECISION'),
                ('dupr_id', "ALTER TABLE \"user\" ADD COLUMN dupr_id VARCHAR(80) NOT NULL DEFAULT ''"),
                ('auth_version', 'ALTER TABLE "user" ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 1'),
                ('email_verified_at', 'ALTER TABLE "user" ADD COLUMN email_verified_at TIMESTAMP'),
                ('operator_role', "ALTER TABLE \"user\" ADD COLUMN operator_role VARCHAR(20) NOT NULL DEFAULT ''"),
                ('mfa_secret_encrypted', "ALTER TABLE \"user\" ADD COLUMN mfa_secret_encrypted TEXT NOT NULL DEFAULT ''"),
                ('mfa_enabled', 'ALTER TABLE "user" ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE'),
                ('mfa_enabled_at', 'ALTER TABLE "user" ADD COLUMN mfa_enabled_at TIMESTAMP'),
                ('mfa_recovery_codes', "ALTER TABLE \"user\" ADD COLUMN mfa_recovery_codes TEXT NOT NULL DEFAULT '[]'"),
                ('onboarding_completed_at', 'ALTER TABLE "user" ADD COLUMN onboarding_completed_at TIMESTAMP'),
                ('invited_by_user_id', 'ALTER TABLE "user" ADD COLUMN invited_by_user_id INTEGER'),
                ('suspended_at', 'ALTER TABLE "user" ADD COLUMN suspended_at TIMESTAMP'),
                ('suspension_reason', "ALTER TABLE \"user\" ADD COLUMN suspension_reason VARCHAR(500) NOT NULL DEFAULT ''"),
                ('suspended_by_id', 'ALTER TABLE "user" ADD COLUMN suspended_by_id INTEGER'),
            ):
                if col not in user_cols:
                    # SQLite uses FLOAT/DATETIME; Postgres accepts these too.
                    statements.append(ddl if is_postgres else ddl
                                      .replace('DOUBLE PRECISION', 'FLOAT')
                                      .replace('TIMESTAMP', 'DATETIME'))
                    if col == 'onboarding_completed_at':
                        # This branch only runs while upgrading an established
                        # database. Brand-new accounts created after the column
                        # exists correctly start with a null completion time.
                        statements.append(
                            'UPDATE "user" SET onboarding_completed_at = CURRENT_TIMESTAMP '
                            'WHERE onboarding_completed_at IS NULL'
                        )
            statements.extend((
                'CREATE INDEX IF NOT EXISTS ix_user_operator_role ON "user" (operator_role)',
                'CREATE INDEX IF NOT EXISTS ix_user_mfa_enabled ON "user" (mfa_enabled)',
                'CREATE INDEX IF NOT EXISTS ix_user_invited_by_user_id ON "user" (invited_by_user_id)',
                'CREATE INDEX IF NOT EXISTS ix_user_suspended_at ON "user" (suspended_at)',
                'CREATE INDEX IF NOT EXISTS ix_user_nearby_visibility ON "user" (nearby_visibility)',
            ))

        if 'user_report' in tables:
            report_cols = {c['name'] for c in inspector.get_columns('user_report')}
            for col, ddl in (
                ('status', "ALTER TABLE user_report ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'open'"),
                ('assigned_operator_id', 'ALTER TABLE user_report ADD COLUMN assigned_operator_id INTEGER'),
                ('outcome', "ALTER TABLE user_report ADD COLUMN outcome VARCHAR(1000) NOT NULL DEFAULT ''"),
                ('resolved_at', 'ALTER TABLE user_report ADD COLUMN resolved_at TIMESTAMP'),
                ('details', "ALTER TABLE user_report ADD COLUMN details VARCHAR(2000) NOT NULL DEFAULT ''"),
                ('content_type', "ALTER TABLE user_report ADD COLUMN content_type VARCHAR(32) NOT NULL DEFAULT 'user'"),
                ('content_id', 'ALTER TABLE user_report ADD COLUMN content_id INTEGER'),
                ('content_snapshot', "ALTER TABLE user_report ADD COLUMN content_snapshot TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in report_cols:
                    statements.append(ddl if is_postgres else ddl.replace('TIMESTAMP', 'DATETIME'))
            statements.extend((
                'CREATE INDEX IF NOT EXISTS ix_user_report_status ON user_report (status)',
                'CREATE INDEX IF NOT EXISTS ix_user_report_assigned_operator_id ON user_report (assigned_operator_id)',
                'CREATE INDEX IF NOT EXISTS ix_user_report_content_type ON user_report (content_type)',
                'CREATE INDEX IF NOT EXISTS ix_user_report_content_id ON user_report (content_id)',
            ))

        if 'game' in tables:
            if 'user' in tables:
                from backend.models import GameRecurrenceRsvp, GameScoreLine
                GameRecurrenceRsvp.__table__.create(db.engine, checkfirst=True)
                GameScoreLine.__table__.create(db.engine, checkfirst=True)
            game_cols = {c['name'] for c in inspector.get_columns('game')}
            if is_postgres:
                status_col = next(
                    (c for c in inspector.get_columns('game') if c['name'] == 'status'), None,
                )
                if status_col is not None and getattr(status_col['type'], 'length', 32) < 32:
                    statements.append('ALTER TABLE game ALTER COLUMN status TYPE VARCHAR(32)')
            if 'visibility' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT 'open'"
                )
            if 'recurrence' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN recurrence VARCHAR(16) NOT NULL DEFAULT 'none'"
                )
            if 'recurrence_timezone' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN recurrence_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'"
                )
            if 'recurrence_local_time' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN recurrence_local_time VARCHAR(5) NOT NULL DEFAULT ''"
                )
            if 'recurrence_weekdays' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN recurrence_weekdays TEXT NOT NULL DEFAULT '[]'"
                )
            if 'recurrence_ends_on' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN recurrence_ends_on DATE'
                )
            if 'club_id' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN club_id INTEGER')
            if 'crew_id' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN crew_id INTEGER')
            if 'crew_roster_version' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN crew_roster_version INTEGER')
            if 'preferred_level' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN preferred_level VARCHAR(16) NOT NULL DEFAULT 'any'"
                )
            if 'level_min' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN level_min DOUBLE PRECISION')
            if 'level_max' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN level_max DOUBLE PRECISION')
            if 'is_challenge' not in game_cols:
                # Nullable by design for rolling upgrades: an older app
                # process that inserts after this DDL leaves NULL, which the
                # new code can recognize through the legacy marker. New code
                # always writes an explicit True/False value.
                statements.append(
                    'ALTER TABLE game ADD COLUMN is_challenge BOOLEAN'
                )
            if 'notes' in game_cols:
                # Convert every pre-field row to explicit semantic state. The
                # extra shape predicates are included whenever that legacy
                # schema has them, preventing an arbitrary sword note on a
                # normal game from being promoted to a direct challenge.
                challenge_predicates = ["notes LIKE '⚔%'"]
                for column, expected in (
                    ('game_type', "'ranked'"),
                    ('visibility', "'private'"),
                    ('max_players', '2'),
                ):
                    if column in game_cols:
                        challenge_predicates.append(f'{column} = {expected}')
                statements.extend((
                    'UPDATE game SET is_challenge = '
                    + ('TRUE' if is_postgres else '1')
                    + ' WHERE is_challenge IS NULL AND '
                    + ' AND '.join(challenge_predicates),
                    'UPDATE game SET is_challenge = '
                    + ('FALSE' if is_postgres else '0')
                    + ' WHERE is_challenge IS NULL',
                ))
            if 'is_instant' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN is_instant BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )
                # Provenance is server-owned. Notes have always been
                # user-editable, so no text heuristic may promote an ordinary
                # scheduled game into the instant-rally lifecycle.
            if 'assembly_closed_at' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN assembly_closed_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'client_attempt_id' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN client_attempt_id VARCHAR(64)'
                )
            if 'client_attempt_fingerprint' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN client_attempt_fingerprint VARCHAR(64)'
                )
            if 'title' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN title VARCHAR(120) NOT NULL DEFAULT ''"
                )
            if 'description' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN description VARCHAR(1000) NOT NULL DEFAULT ''"
                )
            if 'duration_minutes' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN duration_minutes INTEGER'
                )
            if 'cost_cents' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN cost_cents INTEGER'
                )
            if 'court_number' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN court_number VARCHAR(40) NOT NULL DEFAULT ''"
                )
            if 'court_count' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN court_count INTEGER'
                )
            if 'auto_fill_waitlist' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN auto_fill_waitlist BOOLEAN NOT NULL DEFAULT '
                    + ('TRUE' if is_postgres else '1')
                )
            if 'score_dispute_count' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN score_dispute_count INTEGER NOT NULL DEFAULT 0'
                )
            if 'score_dispute_reason' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN score_dispute_reason VARCHAR(500) NOT NULL DEFAULT ''"
                )
            if 'score_confirmation_kind' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN score_confirmation_kind VARCHAR(16) NOT NULL DEFAULT ''"
                )
            if 'score_confirmed_by_id' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN score_confirmed_by_id INTEGER'
                )
            if 'score_confirmation_reminded_at' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN score_confirmation_reminded_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            statements.append(
                'CREATE INDEX IF NOT EXISTS ix_game_crew_id ON game (crew_id)'
            )
            statements.append(
                'CREATE INDEX IF NOT EXISTS ix_game_is_instant ON game (is_instant)'
            )

        if 'court' in tables:
            court_cols = {c['name'] for c in inspector.get_columns('court')}
            if 'photo_data' not in court_cols:
                statements.append('ALTER TABLE court ADD COLUMN photo_data TEXT')
            if 'hours' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN hours VARCHAR(255) NOT NULL DEFAULT ''"
                )
            if 'structured_hours' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN structured_hours TEXT NOT NULL DEFAULT '{}'"
                )
            if 'open_play_schedule_rows' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN open_play_schedule_rows TEXT NOT NULL DEFAULT '[]'"
                )
            if 'hours_dawn_to_dusk' not in court_cols:
                statements.append(
                    'ALTER TABLE court ADD COLUMN hours_dawn_to_dusk BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )
            if 'reservation_url' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN reservation_url VARCHAR(500) NOT NULL DEFAULT ''"
                )
            if 'fee_type' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN fee_type VARCHAR(24) NOT NULL DEFAULT ''"
                )
            if 'closed' not in court_cols:
                statements.append(
                    'ALTER TABLE court ADD COLUMN closed BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )

        if 'court_photo' in tables:
            cp_cols = {c['name'] for c in inspector.get_columns('court_photo')}
            if 'caption' not in cp_cols:
                statements.append(
                    "ALTER TABLE court_photo ADD COLUMN caption VARCHAR(140) NOT NULL DEFAULT ''"
                )

        if 'tournament' in tables:
            t_cols = {c['name'] for c in inspector.get_columns('tournament')}
            for col in ('reminded_at', 'day_reminded_at'):
                if col not in t_cols:
                    statements.append(
                        f'ALTER TABLE tournament ADD COLUMN {col} '
                        + ('TIMESTAMP' if is_postgres else 'DATETIME')
                    )
            if 'ranked' not in t_cols:
                statements.append(
                    'ALTER TABLE tournament ADD COLUMN ranked BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )
            if 'club_id' not in t_cols:
                statements.append('ALTER TABLE tournament ADD COLUMN club_id INTEGER')
            tournament_columns = (
                ('division_name', "VARCHAR(80) NOT NULL DEFAULT 'Open'"),
                ('division_min_rating', 'FLOAT'),
                ('division_max_rating', 'FLOAT'),
                ('game_format', "VARCHAR(32) NOT NULL DEFAULT 'single_11'"),
                ('court_count', 'INTEGER NOT NULL DEFAULT 1'),
                ('match_minutes', 'INTEGER NOT NULL DEFAULT 30'),
            )
            for column, ddl in tournament_columns:
                if column not in t_cols:
                    statements.append(
                        f'ALTER TABLE tournament ADD COLUMN {column} {ddl}'
                    )

        if 'tournament_entry' in tables:
            te_cols = {c['name'] for c in inspector.get_columns('tournament_entry')}
            if 'checked_in_at' not in te_cols:
                statements.append(
                    'ALTER TABLE tournament_entry ADD COLUMN checked_in_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'partner_invitee_id' not in te_cols:
                statements.append(
                    'ALTER TABLE tournament_entry ADD COLUMN partner_invitee_id INTEGER'
                )
            if 'partner_status' not in te_cols:
                statements.append(
                    "ALTER TABLE tournament_entry ADD COLUMN partner_status "
                    "VARCHAR(20) NOT NULL DEFAULT 'accepted'"
                )
            if 'partner_pending_on' not in te_cols:
                statements.append(
                    "ALTER TABLE tournament_entry ADD COLUMN partner_pending_on "
                    "VARCHAR(20) NOT NULL DEFAULT ''"
                )

        if 'tournament_match' in tables:
            tm_cols = {c['name'] for c in inspector.get_columns('tournament_match')}
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            tournament_match_columns = (
                ('result_state', "VARCHAR(32) NOT NULL DEFAULT 'unreported'"),
                ('result_version', 'INTEGER NOT NULL DEFAULT 0'),
                ('reported_by_id', 'INTEGER'),
                ('reported_at', datetime_type),
                ('confirmed_by_id', 'INTEGER'),
                ('confirmed_at', datetime_type),
                ('disputed_by_id', 'INTEGER'),
                ('disputed_at', datetime_type),
                ('dispute_reason', "VARCHAR(500) NOT NULL DEFAULT ''"),
                ('resolution_kind', "VARCHAR(32) NOT NULL DEFAULT ''"),
                ('review_reminded_at', datetime_type),
                ('stall_alerted_at', datetime_type),
                ('last_nudged_at', datetime_type),
                ('scheduled_at', datetime_type),
                ('court_number', 'INTEGER'),
                ('game_scores_json', "TEXT NOT NULL DEFAULT '[]'"),
            )
            for column, ddl in tournament_match_columns:
                if column not in tm_cols:
                    statements.append(
                        f'ALTER TABLE tournament_match ADD COLUMN {column} {ddl}'
                    )
            # Label legacy results without replaying bracket advancement,
            # standings, ratings, titles, or notifications.
            statements.extend((
                "UPDATE tournament_match SET result_state = 'unreported' "
                "WHERE result_state IS NULL OR result_state = ''",
                "UPDATE tournament_match SET "
                "result_state = CASE "
                "WHEN score1 IS NULL AND score2 IS NULL "
                "AND (entry1_id IS NULL OR entry2_id IS NULL) THEN 'bye' "
                "ELSE 'confirmed' END, "
                "result_version = CASE WHEN result_version < 1 THEN 1 ELSE result_version END, "
                "resolution_kind = CASE WHEN resolution_kind = '' THEN 'legacy' ELSE resolution_kind END, "
                "reported_at = CASE WHEN reported_by_id IS NOT NULL "
                "THEN COALESCE(reported_at, updated_at) ELSE reported_at END, "
                "confirmed_at = CASE WHEN score1 IS NOT NULL OR score2 IS NOT NULL "
                "THEN COALESCE(confirmed_at, updated_at) ELSE confirmed_at END "
                "WHERE winner_entry_id IS NOT NULL "
                "AND result_state = 'unreported'",
                'CREATE INDEX IF NOT EXISTS ix_tournament_match_result_state '
                'ON tournament_match (result_state)',
                'CREATE INDEX IF NOT EXISTS '
                'ix_tournament_match_result_state_reported_at '
                'ON tournament_match (result_state, reported_at)',
            ))

        if 'notification' in tables:
            notif_cols = {c['name'] for c in inspector.get_columns('notification')}
            if 'related_tournament_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_tournament_id INTEGER'
                )
            if 'related_club_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_club_id INTEGER'
                )
            if 'related_crew_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_crew_id INTEGER'
                )
            if 'related_league_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_league_id INTEGER'
                )
            if 'action_url' not in notif_cols:
                statements.append(
                    "ALTER TABLE notification ADD COLUMN action_url "
                    "VARCHAR(500) NOT NULL DEFAULT ''"
                )
            if 'unread_dedupe_key' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN '
                    'unread_dedupe_key VARCHAR(160)'
                )
            statements.append(
                'CREATE UNIQUE INDEX IF NOT EXISTS '
                'uq_notification_user_unread_topic '
                'ON notification (user_id, unread_dedupe_key)'
            )
            statements.append(
                'CREATE INDEX IF NOT EXISTS ix_notification_related_crew_id '
                'ON notification (related_crew_id)'
            )

        if 'business_profile' in tables:
            profile_cols = {
                c['name'] for c in inspector.get_columns('business_profile')
            }
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            boolean_false = 'FALSE' if is_postgres else '0'
            for column, ddl in (
                (
                    'organization_id',
                    'INTEGER REFERENCES business_organization(id)',
                ),
                (
                    'governance_status',
                    "VARCHAR(20) NOT NULL DEFAULT 'active'",
                ),
                (
                    'suspension_reason',
                    "VARCHAR(500) NOT NULL DEFAULT ''",
                ),
                ('suspended_at', datetime_type),
                ('suspended_by', "VARCHAR(120) NOT NULL DEFAULT ''"),
                (
                    'content_review_status',
                    "VARCHAR(20) NOT NULL DEFAULT 'approved'",
                ),
                ('content_reviewed_at', datetime_type),
                ('logo_data', "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in profile_cols:
                    statements.append(
                        f'ALTER TABLE business_profile ADD COLUMN {column} {ddl}'
                    )
            statements.extend((
                'CREATE INDEX IF NOT EXISTS ix_business_profile_organization_id '
                'ON business_profile (organization_id)',
                'CREATE INDEX IF NOT EXISTS ix_business_profile_governance_status '
                'ON business_profile (governance_status)',
                'CREATE INDEX IF NOT EXISTS ix_business_profile_content_review_status '
                'ON business_profile (content_review_status)',
            ))

        if 'business_claim' in tables:
            claim_cols = {c['name'] for c in inspector.get_columns('business_claim')}
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            for column, ddl in (
                ('assigned_operator_id', 'INTEGER REFERENCES "user"(id)'),
                ('assigned_operator_identifier', "VARCHAR(120) NOT NULL DEFAULT ''"),
                ('due_at', datetime_type),
                ('claimant_feedback', "VARCHAR(1000) NOT NULL DEFAULT ''"),
            ):
                if column not in claim_cols:
                    statements.append(
                        f'ALTER TABLE business_claim ADD COLUMN {column} {ddl}'
                    )
            statements.extend((
                'UPDATE business_claim SET due_at = '
                + (
                    "COALESCE(due_at, created_at + INTERVAL '48 hours')"
                    if is_postgres else "COALESCE(due_at, datetime(created_at, '+48 hours'))"
                ),
                'CREATE INDEX IF NOT EXISTS ix_business_claim_assigned_operator_id '
                'ON business_claim (assigned_operator_id)',
                'CREATE INDEX IF NOT EXISTS ix_business_claim_due_at '
                'ON business_claim (due_at)',
            ))

        if 'business_schedule_item' in tables:
            schedule_cols = {
                c['name'] for c in inspector.get_columns('business_schedule_item')
            }
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            for column, ddl in (
                ('timezone', "VARCHAR(64) NOT NULL DEFAULT 'UTC'"),
                ('recurrence', "VARCHAR(24) NOT NULL DEFAULT 'weekly'"),
                ('start_date', 'DATE'),
                ('end_date', 'DATE'),
                ('event_date', 'DATE'),
                ('capacity', 'INTEGER'),
                ('spots_remaining', 'INTEGER'),
                ('status', "VARCHAR(24) NOT NULL DEFAULT 'scheduled'"),
                ('location_note', "VARCHAR(240) NOT NULL DEFAULT ''"),
                ('instructor', "VARCHAR(120) NOT NULL DEFAULT ''"),
                ('source_updated_at', datetime_type),
            ):
                if column not in schedule_cols:
                    statements.append(
                        f'ALTER TABLE business_schedule_item ADD COLUMN {column} {ddl}'
                    )
            statements.extend((
                'UPDATE business_schedule_item SET source_updated_at = '
                'COALESCE(source_updated_at, updated_at)',
                'CREATE INDEX IF NOT EXISTS ix_business_schedule_item_event_date '
                'ON business_schedule_item (event_date)',
                'CREATE INDEX IF NOT EXISTS ix_business_schedule_item_status '
                'ON business_schedule_item (status)',
            ))

        if 'business_verification_evidence' in tables:
            evidence_cols = {
                c['name'] for c in inspector.get_columns(
                    'business_verification_evidence'
                )
            }
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            for column, ddl in (
                ('domain_match', 'BOOLEAN'),
                ('challenge_failed_attempts', 'INTEGER NOT NULL DEFAULT 0'),
                ('challenge_locked_at', datetime_type),
            ):
                if column not in evidence_cols:
                    statements.append(
                        'ALTER TABLE business_verification_evidence '
                        f'ADD COLUMN {column} {ddl}'
                    )

        if 'business_profile_revision' in tables:
            revision_cols = {
                c['name'] for c in inspector.get_columns('business_profile_revision')
            }
            if 'previous_snapshot' not in revision_cols:
                statements.append(
                    "ALTER TABLE business_profile_revision ADD COLUMN "
                    "previous_snapshot TEXT NOT NULL DEFAULT '{}'"
                )

        if 'business_profile_report' in tables:
            report_cols = {
                c['name'] for c in inspector.get_columns('business_profile_report')
            }
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            for column, ddl in (
                ('assigned_operator_id', 'INTEGER REFERENCES "user"(id)'),
                ('assigned_operator_identifier', "VARCHAR(120) NOT NULL DEFAULT ''"),
                ('due_at', datetime_type),
            ):
                if column not in report_cols:
                    statements.append(
                        f'ALTER TABLE business_profile_report ADD COLUMN {column} {ddl}'
                    )
            statements.extend((
                'UPDATE business_profile_report SET due_at = '
                + (
                    "COALESCE(due_at, created_at + INTERVAL '48 hours')"
                    if is_postgres else "COALESCE(due_at, datetime(created_at, '+48 hours'))"
                ),
                'CREATE INDEX IF NOT EXISTS ix_business_profile_report_assigned_operator_id '
                'ON business_profile_report (assigned_operator_id)',
                'CREATE INDEX IF NOT EXISTS ix_business_profile_report_due_at '
                'ON business_profile_report (due_at)',
            ))

        if 'business_integration_request' in tables:
            request_cols = {
                c['name']
                for c in inspector.get_columns('business_integration_request')
            }
            if 'handled_by' not in request_cols:
                statements.append(
                    "ALTER TABLE business_integration_request ADD COLUMN "
                    "handled_by VARCHAR(120) NOT NULL DEFAULT ''"
                )
            if 'status_message' not in request_cols:
                statements.append(
                    "ALTER TABLE business_integration_request ADD COLUMN "
                    "status_message VARCHAR(1000) NOT NULL DEFAULT ''"
                )
            if 'status_changed_at' not in request_cols:
                statements.append(
                    'ALTER TABLE business_integration_request ADD COLUMN '
                    'status_changed_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            if 'assigned_operator_id' not in request_cols:
                statements.append(
                    'ALTER TABLE business_integration_request ADD COLUMN '
                    'assigned_operator_id INTEGER REFERENCES "user"(id)'
                )
            if 'assigned_operator_identifier' not in request_cols:
                statements.append(
                    'ALTER TABLE business_integration_request ADD COLUMN '
                    "assigned_operator_identifier VARCHAR(120) NOT NULL DEFAULT ''"
                )
            if 'due_at' not in request_cols:
                statements.append(
                    'ALTER TABLE business_integration_request ADD COLUMN '
                    f'due_at {datetime_type}'
                )
            statements.extend((
                'UPDATE business_integration_request SET due_at = '
                + (
                    "COALESCE(due_at, created_at + INTERVAL '72 hours')"
                    if is_postgres else "COALESCE(due_at, datetime(created_at, '+72 hours'))"
                ),
                'CREATE INDEX IF NOT EXISTS '
                'ix_business_integration_request_assigned_operator_id '
                'ON business_integration_request (assigned_operator_id)',
                'CREATE INDEX IF NOT EXISTS ix_business_integration_request_due_at '
                'ON business_integration_request (due_at)',
            ))

        if 'business_provider_connection' in tables:
            connection_cols = {
                c['name']
                for c in inspector.get_columns('business_provider_connection')
            }
            if 'operator_reconnect_required' not in connection_cols:
                statements.append(
                    'ALTER TABLE business_provider_connection ADD COLUMN '
                    'operator_reconnect_required BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )

        if 'league' in tables:
            league_cols = {c['name'] for c in inspector.get_columns('league')}
            if 'round_started_at' not in league_cols:
                statements.append(
                    'ALTER TABLE league ADD COLUMN round_started_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'champion_user_id' not in league_cols:
                statements.append('ALTER TABLE league ADD COLUMN champion_user_id INTEGER')
            if 'club_id' not in league_cols:
                statements.append('ALTER TABLE league ADD COLUMN club_id INTEGER')
            if 'deadline_alerted_round' not in league_cols:
                statements.append(
                    'ALTER TABLE league ADD COLUMN deadline_alerted_round '
                    'INTEGER NOT NULL DEFAULT 0'
                )

        if 'league_member' in tables:
            lm_cols = {c['name'] for c in inspector.get_columns('league_member')}
            if 'reminded_round' not in lm_cols:
                statements.append(
                    'ALTER TABLE league_member ADD COLUMN reminded_round INTEGER NOT NULL DEFAULT 0'
                )

        if 'league_match' in tables:
            league_match_cols = {
                c['name'] for c in inspector.get_columns('league_match')
            }
            datetime_type = 'TIMESTAMP' if is_postgres else 'DATETIME'
            league_match_columns = (
                ('result_state', "VARCHAR(32) NOT NULL DEFAULT 'unreported'"),
                ('result_version', 'INTEGER NOT NULL DEFAULT 0'),
                # Reuse the existing reporter column when present.
                ('reported_by_id', 'INTEGER'),
                ('reported_at', datetime_type),
                ('confirmed_by_id', 'INTEGER'),
                ('confirmed_at', datetime_type),
                ('disputed_by_id', 'INTEGER'),
                ('disputed_at', datetime_type),
                ('dispute_reason', "VARCHAR(500) NOT NULL DEFAULT ''"),
                ('resolution_kind', "VARCHAR(32) NOT NULL DEFAULT ''"),
                ('review_reminded_at', datetime_type),
                ('stall_alerted_at', datetime_type),
                ('last_nudged_at', datetime_type),
            )
            for column, ddl in league_match_columns:
                if column not in league_match_cols:
                    statements.append(
                        f'ALTER TABLE league_match ADD COLUMN {column} {ddl}'
                    )
            statements.extend((
                "UPDATE league_match SET result_state = 'unreported' "
                "WHERE result_state IS NULL OR result_state = ''",
                "UPDATE league_match SET result_state = 'confirmed', "
                "result_version = CASE WHEN result_version < 1 THEN 1 ELSE result_version END, "
                "resolution_kind = CASE WHEN resolution_kind = '' THEN 'legacy' ELSE resolution_kind END, "
                "reported_at = CASE WHEN reported_by_id IS NOT NULL "
                "THEN COALESCE(reported_at, updated_at) ELSE reported_at END, "
                "confirmed_at = COALESCE(confirmed_at, updated_at) "
                "WHERE winner_id IS NOT NULL AND result_state = 'unreported'",
                'CREATE INDEX IF NOT EXISTS ix_league_match_result_state '
                'ON league_match (result_state)',
                'CREATE INDEX IF NOT EXISTS '
                'ix_league_match_result_state_reported_at '
                'ON league_match (result_state, reported_at)',
            ))

        if 'club' in tables:
            club_cols = {c['name'] for c in inspector.get_columns('club')}
            if 'last_digest_at' not in club_cols:
                statements.append(
                    'ALTER TABLE club ADD COLUMN last_digest_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'announcement' not in club_cols:
                statements.append(
                    "ALTER TABLE club ADD COLUMN announcement VARCHAR(500) NOT NULL DEFAULT ''"
                )
            if 'announcement_author_id' not in club_cols:
                statements.append(
                    'ALTER TABLE club ADD COLUMN announcement_author_id INTEGER'
                )
            if 'announcement_posted_at' not in club_cols:
                statements.append(
                    'ALTER TABLE club ADD COLUMN announcement_posted_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'join_policy' not in club_cols:
                statements.append(
                    "ALTER TABLE club ADD COLUMN join_policy VARCHAR(16) "
                    "NOT NULL DEFAULT 'open'"
                )
            if 'archived_at' not in club_cols:
                statements.append(
                    'ALTER TABLE club ADD COLUMN archived_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
                statements.append(
                    'CREATE INDEX IF NOT EXISTS ix_club_archived_at '
                    'ON club (archived_at)'
                )

        if 'club_member' in tables:
            club_member_cols = {
                c['name'] for c in inspector.get_columns('club_member')
            }
            if 'notification_level' not in club_member_cols:
                statements.append(
                    "ALTER TABLE club_member ADD COLUMN notification_level "
                    "VARCHAR(16) NOT NULL DEFAULT 'all'"
                )

        if 'crew_chat_read' in tables:
            crew_chat_read_cols = {
                c['name'] for c in inspector.get_columns('crew_chat_read')
            }
            if 'notification_level' not in crew_chat_read_cols:
                statements.append(
                    "ALTER TABLE crew_chat_read ADD COLUMN notification_level "
                    "VARCHAR(16) NOT NULL DEFAULT 'all'"
                )

        if 'game_player' in tables:
            gp_cols = {c['name'] for c in inspector.get_columns('game_player')}
            if 'reminded_at' not in gp_cols:
                statements.append(
                    'ALTER TABLE game_player ADD COLUMN reminded_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'attending_at' not in gp_cols:
                statements.append(
                    'ALTER TABLE game_player ADD COLUMN attending_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'day_reminded_at' not in gp_cols:
                statements.append(
                    'ALTER TABLE game_player ADD COLUMN day_reminded_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )

        if statements:
            app.logger.warning('Applying schema upgrades: %s', statements)
            with db.engine.begin() as conn:
                for statement in statements:
                    conn.execute(text(statement))
        if {'user', 'game'} <= set(tables):
            _ensure_game_score_reference_foreign_keys(app)
        # Business integrations are wholly additive.  Production/serverless
        # deliberately runs with AUTO_CREATE_DB disabled, so the trusted
        # operator migration must be able to install these new tables without
        # attempting to create or modify unrelated application tables.
        if {'user', 'court'} <= set(tables):
            from backend.models import (
                BusinessClaim,
                BusinessClaimReviewEvent,
                BusinessGovernanceEvent,
                BusinessIntegrationRequest,
                BusinessOffering,
                BusinessOperatorAction,
                BusinessOrganization,
                BusinessOrganizationMember,
                BusinessProfile,
                BusinessProfileReport,
                BusinessProfileRevision,
                BusinessScheduleItem,
                BusinessStaffInvitation,
                BusinessVerificationEvidence,
                OperatorSecurityEvent,
            )
            BusinessOrganization.__table__.create(db.engine, checkfirst=True)
            BusinessProfile.__table__.create(db.engine, checkfirst=True)
            BusinessClaim.__table__.create(db.engine, checkfirst=True)
            BusinessClaimReviewEvent.__table__.create(db.engine, checkfirst=True)
            BusinessOffering.__table__.create(db.engine, checkfirst=True)
            BusinessScheduleItem.__table__.create(db.engine, checkfirst=True)
            BusinessIntegrationRequest.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessVerificationEvidence.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessOrganizationMember.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessStaffInvitation.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessProfileRevision.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessGovernanceEvent.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessProfileReport.__table__.create(
                db.engine, checkfirst=True,
            )
            BusinessOperatorAction.__table__.create(
                db.engine, checkfirst=True,
            )
            OperatorSecurityEvent.__table__.create(
                db.engine, checkfirst=True,
            )
            _ensure_business_governance_constraints(app)
        if {'user', 'club'} <= set(tables):
            from backend.models import ClubBan, ClubJoinRequest
            ClubJoinRequest.__table__.create(db.engine, checkfirst=True)
            ClubBan.__table__.create(db.engine, checkfirst=True)
        # New installations get this table from create_all below. Existing
        # installations may intentionally disable AUTO_CREATE_DB, so create the
        # additive audit table explicitly once its referenced user table exists.
        if 'user' in tables and 'competition_result_event' not in tables:
            from backend.models import CompetitionResultEvent
            CompetitionResultEvent.__table__.create(db.engine, checkfirst=True)
        if 'message' in tables:
            from backend.models import Message, MessageSendAttempt
            if 'message_send_attempt' not in tables:
                MessageSendAttempt.__table__.create(db.engine, checkfirst=True)

            # Backfill any keyed rows written by the additive Message columns
            # before the durable ledger shipped. This closes the delete/retry
            # race immediately on the first upgraded boot.
            from backend.routes.chat import _message_attempt_fingerprint
            missing_attempts = (
                Message.query
                .outerjoin(
                    MessageSendAttempt,
                    db.and_(
                        MessageSendAttempt.sender_id == Message.sender_id,
                        MessageSendAttempt.client_attempt_id
                        == Message.client_attempt_id,
                    ),
                )
                .filter(
                    Message.client_attempt_id.isnot(None),
                    MessageSendAttempt.id.is_(None),
                )
                .yield_per(200)
            )
            for message in missing_attempts:
                scope = {
                    column: getattr(message, column)
                    for column in (
                        'recipient_id', 'court_id', 'game_id', 'tournament_id',
                        'club_id', 'crew_id', 'league_id',
                    )
                    if getattr(message, column) is not None
                }
                fingerprint = message.client_attempt_fingerprint or (
                    _message_attempt_fingerprint(
                        scope,
                        str(message.body or '').strip()[:2000],
                        message.image_data,
                    )
                )
                db.session.add(MessageSendAttempt(
                    sender_id=message.sender_id,
                    client_attempt_id=message.client_attempt_id,
                    client_attempt_fingerprint=fingerprint,
                    message_id=message.id,
                ))
            db.session.commit()

        # Court chat follows explicit Join/Leave/Mute state. Existing read
        # markers and saved courts never become subscriptions implicitly.
        if 'user' in tables and 'court' in tables:
            from backend.models import CourtChatSubscription
            CourtChatSubscription.__table__.create(db.engine, checkfirst=True)

        # Direct-message alert muting is directional and per conversation.
        if 'user' in tables:
            from backend.models import DirectChatPreference
            DirectChatPreference.__table__.create(db.engine, checkfirst=True)

        # Crews are additive private-community tables. Explicitly create them
        # for installations that run schema upgrades without AUTO_CREATE_DB;
        # fresh databases still receive them through create_all below.
        if 'user' in tables and 'court' in tables:
            from backend.models import Crew, CrewChatRead, CrewInvite, CrewMember
            Crew.__table__.create(db.engine, checkfirst=True)
            CrewMember.__table__.create(db.engine, checkfirst=True)
            CrewInvite.__table__.create(db.engine, checkfirst=True)
            CrewChatRead.__table__.create(db.engine, checkfirst=True)
            # These nullable references were added to long-lived tables before
            # the Crew table existed, so create_all cannot retrofit their
            # foreign keys. Add them only after Crew has been created.
            _ensure_crew_reference_foreign_keys(app)

        # Arrival reservations retain an idempotency history, so they need a
        # dedicated table (including the partial active-user index) even when an
        # operator intentionally disables broad create_all behavior.
        if 'user' in tables and 'game' in tables:
            from backend.models import GameArrivalIntent
            GameArrivalIntent.__table__.create(db.engine, checkfirst=True)

        # Remote availability is neither CheckIn nor an arrival reservation.
        # Its ended rows form the publish/accept retry ledger, so installations
        # with broad create_all disabled still need the complete table and its
        # partial active-user uniqueness index.
        if {'user', 'court', 'game'} <= set(tables):
            from backend.models import PlayAvailabilityPulse
            PlayAvailabilityPulse.__table__.create(
                db.engine, checkfirst=True,
            )

        # A game open call is a durable recruiting ledger linked to one court
        # chat Message. Create it explicitly for production installations that
        # keep broad create_all disabled.
        if {'user', 'game', 'message'} <= set(tables):
            from backend.models import GameOpenCall
            GameOpenCall.__table__.create(db.engine, checkfirst=True)

        # Several nullable references were introduced on long-lived tables by
        # additive ``ADD COLUMN`` statements above.  ``create_all`` cannot
        # retrofit their model-declared foreign keys, and the moderation
        # tables may also predate a previously interrupted operator migration.
        # Converge those references explicitly before the release verifier
        # decides that production is ready.
        _ensure_additive_reference_foreign_keys(app)
    except Exception:
        db.session.rollback()
        app.logger.exception('Schema upgrade failed')
        if app.config.get('APP_ENV') == 'production':
            raise


def _ensure_business_governance_constraints(app):
    """Install model-defined governance constraints on upgraded PostgreSQL.

    ``create_all`` gives new tables their full constraints, but it cannot add
    checks or foreign keys to long-lived business tables. A locked, reflected
    pass makes the additive path converge to the same model contract and fails
    closed if a canonical name was reused with a different shape.
    """
    if db.engine.dialect.name != 'postgresql':
        return

    from sqlalchemy import (
        CheckConstraint,
        ForeignKeyConstraint,
        UniqueConstraint,
        inspect as sa_inspect,
        text,
    )
    from sqlalchemy.schema import AddConstraint
    from backend.models import (
        BusinessClaim,
        BusinessGovernanceEvent,
        BusinessIntegrationRequest,
        BusinessOperatorAction,
        BusinessOrganization,
        BusinessOrganizationMember,
        BusinessProfile,
        BusinessProfileReport,
        BusinessProfileRevision,
        BusinessScheduleItem,
        BusinessStaffInvitation,
        BusinessVerificationEvidence,
        OperatorSecurityEvent,
    )

    model_tables = [
        model.__table__ for model in (
            BusinessOrganization,
            BusinessProfile,
            BusinessClaim,
            BusinessScheduleItem,
            BusinessIntegrationRequest,
            BusinessVerificationEvidence,
            BusinessOrganizationMember,
            BusinessStaffInvitation,
            BusinessProfileRevision,
            BusinessGovernanceEvent,
            BusinessProfileReport,
            BusinessOperatorAction,
            OperatorSecurityEvent,
        )
    ]
    with db.engine.begin() as connection:
        connection.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext("
            "'third-shot:business-governance-constraints'))"
        ))
        for table in model_tables:
            inspector = sa_inspect(connection)
            if table.name not in set(inspector.get_table_names()):
                continue
            reflected_checks = {
                item.get('name')
                for item in inspector.get_check_constraints(table.name)
            }
            reflected_uniques = {
                item.get('name')
                for item in inspector.get_unique_constraints(table.name)
            }
            reflected_fks = inspector.get_foreign_keys(table.name)
            for constraint in table.constraints:
                name = constraint.name
                if not name:
                    continue
                missing = False
                if isinstance(constraint, CheckConstraint):
                    missing = name not in reflected_checks
                elif isinstance(constraint, UniqueConstraint):
                    missing = name not in reflected_uniques
                elif isinstance(constraint, ForeignKeyConstraint):
                    local_columns = tuple(
                        element.parent.name for element in constraint.elements
                    )
                    target_table = constraint.elements[0].column.table.name
                    target_columns = tuple(
                        element.column.name for element in constraint.elements
                    )
                    matches = any(
                        tuple(item.get('constrained_columns') or ())
                        == local_columns
                        and item.get('referred_table') == target_table
                        and tuple(item.get('referred_columns') or ())
                        == target_columns
                        for item in reflected_fks
                    )
                    if matches:
                        continue
                    if any(item.get('name') == name for item in reflected_fks):
                        raise RuntimeError(
                            f'Foreign key {name} exists with the wrong shape'
                        )
                    missing = True
                if missing:
                    # Building repair DDL must not mark the live model
                    # constraint as excluded from later ``CREATE TABLE`` calls.
                    connection.execute(AddConstraint(
                        constraint,
                        isolate_from_table=False,
                    ))
                    inspector = sa_inspect(connection)
                    reflected_checks = {
                        item.get('name')
                        for item in inspector.get_check_constraints(table.name)
                    }
                    reflected_uniques = {
                        item.get('name')
                        for item in inspector.get_unique_constraints(table.name)
                    }
                    reflected_fks = inspector.get_foreign_keys(table.name)


CREW_REFERENCE_FOREIGN_KEYS = (
    ('message', 'crew_id', 'message_crew_id_fkey'),
    ('game', 'crew_id', 'game_crew_id_fkey'),
    (
        'notification', 'related_crew_id',
        'notification_related_crew_id_fkey',
    ),
)

GAME_SCORE_REFERENCE_FOREIGN_KEYS = (
    (
        'game', 'score_confirmed_by_id',
        'game_score_confirmed_by_id_fkey',
    ),
)


ADDITIVE_REFERENCE_FOREIGN_KEYS = (
    (
        'user', 'invited_by_user_id', 'user', 'id',
        'user_invited_by_user_id_fkey',
    ),
    (
        'user', 'suspended_by_id', 'user', 'id',
        'user_suspended_by_id_fkey',
    ),
    (
        'user_report', 'assigned_operator_id', 'user', 'id',
        'user_report_assigned_operator_id_fkey',
    ),
    (
        'player_feedback', 'user_id', 'user', 'id',
        'player_feedback_user_id_fkey',
    ),
    (
        'player_feedback', 'assigned_operator_id', 'user', 'id',
        'player_feedback_assigned_operator_id_fkey',
    ),
    (
        'moderation_action', 'actor_id', 'user', 'id',
        'moderation_action_actor_id_fkey',
    ),
    (
        'moderation_action', 'target_user_id', 'user', 'id',
        'moderation_action_target_user_id_fkey',
    ),
    (
        'moderation_action', 'user_report_id', 'user_report', 'id',
        'moderation_action_user_report_id_fkey',
    ),
    (
        'moderation_action', 'feedback_id', 'player_feedback', 'id',
        'moderation_action_feedback_id_fkey',
    ),
    (
        'tournament_entry', 'partner_invitee_id', 'user', 'id',
        'tournament_entry_partner_invitee_id_fkey',
    ),
    (
        'club', 'announcement_author_id', 'user', 'id',
        'club_announcement_author_id_fkey',
    ),
)


def _foreign_key_matches(foreign_key, local_column, referred_table='crew',
                         referred_column='id', referred_schema=None):
    """Return whether an inspected FK has the exact single-column shape."""
    return (
        tuple(foreign_key.get('constrained_columns') or ()) == (local_column,)
        and foreign_key.get('referred_table') == referred_table
        and tuple(foreign_key.get('referred_columns') or ())
        == (referred_column,)
        and (
            foreign_key.get('referred_schema') is None
            or referred_schema is None
            or foreign_key.get('referred_schema') == referred_schema
        )
    )


def _missing_crew_reference_foreign_keys(inspector):
    """Return canonical Crew FKs that inspection proves are still absent."""
    tables = set(inspector.get_table_names())
    if 'crew' not in tables:
        return []
    expected_schema = getattr(inspector, 'default_schema_name', None)
    missing = []
    for table, local_column, constraint_name in CREW_REFERENCE_FOREIGN_KEYS:
        if table not in tables:
            continue
        columns = {column['name'] for column in inspector.get_columns(table)}
        if local_column not in columns:
            continue
        foreign_keys = inspector.get_foreign_keys(table)
        if any(
            _foreign_key_matches(
                foreign_key, local_column, referred_schema=expected_schema,
            )
            for foreign_key in foreign_keys
        ):
            continue
        if any(
            foreign_key.get('name') == constraint_name
            for foreign_key in foreign_keys
        ):
            raise RuntimeError(
                f'Foreign key {constraint_name} exists with the wrong shape'
            )
        missing.append((table, local_column, constraint_name))
    return missing


def _missing_additive_reference_foreign_keys(inspector):
    """Return release-added model references absent from a live schema."""
    tables = set(inspector.get_table_names())
    expected_schema = getattr(inspector, 'default_schema_name', None)
    missing = []
    for requirement in ADDITIVE_REFERENCE_FOREIGN_KEYS:
        (
            table, local_column, referred_table, referred_column,
            constraint_name,
        ) = requirement
        if table not in tables or referred_table not in tables:
            continue
        columns = {column['name'] for column in inspector.get_columns(table)}
        if local_column not in columns:
            continue
        foreign_keys = inspector.get_foreign_keys(table)
        if any(
            _foreign_key_matches(
                foreign_key,
                local_column,
                referred_table=referred_table,
                referred_column=referred_column,
                referred_schema=expected_schema,
            )
            for foreign_key in foreign_keys
        ):
            continue
        if any(
            foreign_key.get('name') == constraint_name
            for foreign_key in foreign_keys
        ):
            raise RuntimeError(
                f'Foreign key {constraint_name} exists with the wrong shape'
            )
        missing.append(requirement)
    return missing


def _add_reference_foreign_key(connection, requirement):
    """Install one already-inspected, single-column PostgreSQL reference."""
    from sqlalchemy import text

    (
        table, local_column, referred_table, referred_column, constraint_name,
    ) = requirement
    preparer = connection.dialect.identifier_preparer
    connection.execute(text(
        f'ALTER TABLE {preparer.quote(table)} '
        f'ADD CONSTRAINT {preparer.quote(constraint_name)} '
        f'FOREIGN KEY ({preparer.quote(local_column)}) '
        f'REFERENCES {preparer.quote(referred_table)} '
        f'({preparer.quote(referred_column)})'
    ))


def _ensure_additive_reference_foreign_keys(app):
    """Converge foreign keys omitted by release-time ``ADD COLUMN`` DDL."""
    from sqlalchemy import inspect as sa_inspect, text

    if db.engine.dialect.name != 'postgresql':
        return

    with db.engine.begin() as connection:
        connection.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext("
            "'third-shot:additive-reference-foreign-keys'))"
        ))
        inspector = sa_inspect(connection)
        for requirement in _missing_additive_reference_foreign_keys(inspector):
            _add_reference_foreign_key(connection, requirement)
            # Reflection is cached per Inspector.  Refresh after every DDL so
            # subsequent same-table requirements see the constraint just added.
            inspector = sa_inspect(connection)

        inspector = sa_inspect(connection)
        missing = [
            requirement[4]
            for requirement in _missing_additive_reference_foreign_keys(
                inspector,
            )
        ]
        if missing:
            raise RuntimeError(
                'Additive foreign-key verification failed: '
                + ', '.join(missing)
            )


def _ensure_crew_reference_foreign_keys(app):
    """Install the Crew references omitted by additive ``ADD COLUMN`` DDL.

    PostgreSQL has no ``ADD CONSTRAINT IF NOT EXISTS``. A transaction-scoped
    advisory lock serializes concurrent operator/app upgrade attempts, then a
    fresh inspection checks both the canonical name and constrained columns
    before issuing DDL. Re-running this function is therefore safe, while a
    legacy constraint that hijacks a canonical name with the wrong shape fails
    closed instead of being silently trusted.
    """
    from sqlalchemy import inspect as sa_inspect, text

    if db.engine.dialect.name != 'postgresql':
        return

    with db.engine.begin() as connection:
        connection.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext("
            "'third-shot:crew-reference-foreign-keys'))"
        ))
        inspector = sa_inspect(connection)
        for table, local_column, constraint_name in (
            _missing_crew_reference_foreign_keys(inspector)
        ):
            preparer = connection.dialect.identifier_preparer
            connection.execute(text(
                f'ALTER TABLE {preparer.quote(table)} '
                f'ADD CONSTRAINT {preparer.quote(constraint_name)} '
                f'FOREIGN KEY ({preparer.quote(local_column)}) '
                f'REFERENCES {preparer.quote("crew")} '
                f'({preparer.quote("id")})'
            ))
            # Inspector caches reflection results; refresh after each DDL so a
            # second requirement cannot reason from stale catalog state.
            inspector = sa_inspect(connection)

        inspector = sa_inspect(connection)
        missing = [
            requirement[2]
            for requirement in _missing_crew_reference_foreign_keys(inspector)
        ]
        if missing:
            raise RuntimeError(
                'Crew foreign-key verification failed: ' + ', '.join(missing)
            )


def _ensure_game_score_reference_foreign_keys(app):
    """Retrofit the nullable score confirmer reference on PostgreSQL."""
    from sqlalchemy import inspect as sa_inspect, text

    if db.engine.dialect.name != 'postgresql':
        return

    with db.engine.begin() as connection:
        connection.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext("
            "'third-shot:game-score-reference-foreign-keys'))"
        ))
        inspector = sa_inspect(connection)
        tables = set(inspector.get_table_names())
        if not {'user', 'game'} <= tables:
            return
        foreign_keys = inspector.get_foreign_keys('game')
        requirement = GAME_SCORE_REFERENCE_FOREIGN_KEYS[0]
        _, local_column, constraint_name = requirement
        if any(
            _foreign_key_matches(
                foreign_key,
                local_column,
                referred_table='user',
                referred_schema=getattr(inspector, 'default_schema_name', None),
            )
            for foreign_key in foreign_keys
        ):
            return
        if any(
            foreign_key.get('name') == constraint_name
            for foreign_key in foreign_keys
        ):
            raise RuntimeError(
                f'Foreign key {constraint_name} exists with the wrong shape'
            )
        preparer = connection.dialect.identifier_preparer
        connection.execute(text(
            f'ALTER TABLE {preparer.quote("game")} '
            f'ADD CONSTRAINT {preparer.quote(constraint_name)} '
            f'FOREIGN KEY ({preparer.quote(local_column)}) '
            f'REFERENCES {preparer.quote("user")} ({preparer.quote("id")})'
        ))


GAME_ATTEMPT_INDEX_NAME = 'uq_game_creator_attempt'
GAME_ATTEMPT_INDEX_COLUMNS = ('creator_id', 'client_attempt_id')
MESSAGE_ATTEMPT_INDEX_NAME = 'uq_message_sender_attempt'
MESSAGE_ATTEMPT_INDEX_COLUMNS = ('sender_id', 'client_attempt_id')
MESSAGE_SEND_ATTEMPT_COLUMNS = {
    'id', 'created_at', 'updated_at',
    'sender_id', 'client_attempt_id', 'client_attempt_fingerprint',
    'message_id', 'deleted_at',
}
MESSAGE_SEND_ATTEMPT_UNIQUE_COLUMNS = ('sender_id', 'client_attempt_id')
NOTIFICATION_DEDUPE_INDEX_NAME = 'uq_notification_user_unread_topic'
NOTIFICATION_DEDUPE_INDEX_COLUMNS = ('user_id', 'unread_dedupe_key')
FRIENDSHIP_PAIR_INDEX_NAME = 'uq_friendship_unordered_pair'
ACTIVE_CHECKIN_INDEX_NAME = 'uq_check_in_active_user'
OBSOLETE_GAME_ARRIVAL_INDEX_NAME = 'uq_game_arrival_active_game'


def _drop_obsolete_game_arrival_index(app):
    """Fail closed while removing the former one-traveler-per-rally index."""
    from sqlalchemy import inspect as sa_inspect, text

    def is_present():
        inspector = sa_inspect(db.engine)
        return bool(
            'game_arrival_intent' in inspector.get_table_names()
            and any(
                index.get('name') == OBSOLETE_GAME_ARRIVAL_INDEX_NAME
                for index in inspector.get_indexes('game_arrival_intent')
            )
        )

    if not is_present():
        return
    app.logger.warning(
        'Removing obsolete per-game arrival uniqueness index %s',
        OBSOLETE_GAME_ARRIVAL_INDEX_NAME,
    )
    try:
        with db.engine.begin() as connection:
            if db.engine.dialect.name == 'postgresql':
                connection.execute(text(
                    'LOCK TABLE game_arrival_intent '
                    'IN SHARE ROW EXCLUSIVE MODE'
                ))
            connection.execute(text(
                f'DROP INDEX IF EXISTS "{OBSOLETE_GAME_ARRIVAL_INDEX_NAME}"'
            ))
    except Exception:
        app.logger.exception('Obsolete game-arrival index removal failed')
        raise
    if is_present():
        raise RuntimeError(
            'Obsolete per-game arrival index is still installed'
        )


def _active_checkin_index_definition(connection):
    """Return the canonical active-presence index DDL, if installed."""
    from sqlalchemy import text

    if db.engine.dialect.name == 'postgresql':
        return connection.execute(text('''
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'check_in'
              AND indexname = :name
        '''), {'name': ACTIVE_CHECKIN_INDEX_NAME}).scalar() or ''
    return connection.execute(text('''
        SELECT sql FROM sqlite_master
        WHERE type = 'index' AND name = :name
    '''), {'name': ACTIVE_CHECKIN_INDEX_NAME}).scalar() or ''


def _active_checkin_index_is_exact(definition):
    normalized = ' '.join(
        str(definition or '').lower().replace('"', '').split()
    )
    where_parts = normalized.split(' where ')
    predicate = (
        ' '.join(
            where_parts[1].replace('(', ' ').replace(')', ' ').split()
        )
        if len(where_parts) == 2 else ''
    )
    return (
        normalized.startswith('create unique index')
        and ACTIVE_CHECKIN_INDEX_NAME in normalized
        and (
            ' on check_in ' in f' {normalized} '
            or '.check_in ' in f' {normalized} '
        )
        and where_parts[0].replace(' ', '').endswith('(user_id)')
        and predicate == 'checked_out_at is null'
    )


def _ensure_active_checkin_index(app):
    """Coalesce legacy duplicate presence and enforce one active row/user.

    PostgreSQL locks the table while repairing and installing the partial
    unique index. SQLite's write transaction provides the corresponding local
    migration safety. Checked-out history remains untouched.
    """
    from sqlalchemy import inspect as sa_inspect, text

    if 'check_in' not in sa_inspect(db.engine).get_table_names():
        return
    try:
        with db.engine.begin() as connection:
            if db.engine.dialect.name == 'postgresql':
                connection.execute(text(
                    'LOCK TABLE check_in IN SHARE ROW EXCLUSIVE MODE'
                ))

            rows = connection.execute(text('''
                SELECT id, user_id, last_presence_ping_at, checked_in_at
                FROM check_in
                WHERE checked_out_at IS NULL
                ORDER BY user_id ASC,
                         CASE WHEN last_presence_ping_at IS NULL
                                   AND checked_in_at IS NULL
                              THEN 1 ELSE 0 END ASC,
                         COALESCE(last_presence_ping_at, checked_in_at) DESC,
                         id DESC
            ''')).mappings().all()
            seen_users = set()
            duplicate_ids = []
            for row in rows:
                if row['user_id'] in seen_users:
                    duplicate_ids.append(row['id'])
                else:
                    seen_users.add(row['user_id'])
            for checkin_id in duplicate_ids:
                connection.execute(text('''
                    UPDATE check_in
                    SET checked_out_at = COALESCE(
                        last_presence_ping_at, checked_in_at, CURRENT_TIMESTAMP
                    )
                    WHERE id = :id AND checked_out_at IS NULL
                '''), {'id': checkin_id})

            definition = _active_checkin_index_definition(connection)
            if not _active_checkin_index_is_exact(definition):
                if definition:
                    connection.execute(text(
                        f'DROP INDEX "{ACTIVE_CHECKIN_INDEX_NAME}"'
                    ))
                connection.execute(text(
                    f'CREATE UNIQUE INDEX "{ACTIVE_CHECKIN_INDEX_NAME}" '
                    'ON check_in (user_id) WHERE checked_out_at IS NULL'
                ))

            duplicates = connection.execute(text('''
                SELECT COUNT(*) FROM (
                    SELECT user_id FROM check_in
                    WHERE checked_out_at IS NULL
                    GROUP BY user_id HAVING COUNT(*) > 1
                ) AS duplicate_active_checkins
            ''')).scalar()
            verified = _active_checkin_index_definition(connection)
            if duplicates or not _active_checkin_index_is_exact(verified):
                raise RuntimeError(
                    'Active CheckIn verification failed: expected one partial '
                    'unique row per user'
                )
    except Exception:
        app.logger.exception('Active CheckIn migration failed')
        raise


def _friendship_pair_expressions():
    low = (
        'CASE WHEN requester_id < addressee_id '
        'THEN requester_id ELSE addressee_id END'
    )
    high = (
        'CASE WHEN requester_id < addressee_id '
        'THEN addressee_id ELSE requester_id END'
    )
    return low, high


def _friendship_pair_index_definition(connection):
    """Return the database's canonical index DDL, or an empty string."""
    from sqlalchemy import text

    if db.engine.dialect.name == 'postgresql':
        return connection.execute(text('''
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'friendship'
              AND indexname = :name
        '''), {'name': FRIENDSHIP_PAIR_INDEX_NAME}).scalar() or ''
    return connection.execute(text('''
        SELECT sql FROM sqlite_master
        WHERE type = 'index' AND name = :name
    '''), {'name': FRIENDSHIP_PAIR_INDEX_NAME}).scalar() or ''


def _friendship_pair_index_is_exact(definition):
    normalized = ' '.join(
        str(definition or '').lower().replace('"', '')
        .replace('(', '').replace(')', '').split()
    )
    low, high = _friendship_pair_expressions()
    low = ' '.join(low.lower().replace('(', '').replace(')', '').split())
    high = ' '.join(high.lower().replace('(', '').replace(')', '').split())
    return (
        normalized.startswith('create unique index')
        and FRIENDSHIP_PAIR_INDEX_NAME in normalized
        and low in normalized
        and high in normalized
        and ' where ' not in normalized
    )


def _ensure_friendship_pair_index(app):
    """Coalesce legacy duplicates and enforce one unordered friendship pair.

    Reciprocal pending rows represent mutual intent, so migration promotes the
    survivor to accepted. Pairs involving a deleted account or an active block
    are removed rather than allowing a ghost relationship to reappear later.
    """
    from sqlalchemy import inspect as sa_inspect, text

    if 'friendship' not in sa_inspect(db.engine).get_table_names():
        return
    low_expr, high_expr = _friendship_pair_expressions()
    try:
        with db.engine.begin() as connection:
            if db.engine.dialect.name == 'postgresql':
                connection.execute(text('LOCK TABLE friendship IN SHARE ROW EXCLUSIVE MODE'))

            active_users = {
                row[0]
                for row in connection.execute(text(
                    'SELECT id FROM "user" WHERE deleted_at IS NULL'
                ))
            }
            blocked_pairs = {
                tuple(sorted((row[0], row[1])))
                for row in connection.execute(text(
                    'SELECT blocker_id, blocked_id FROM blocked_user'
                ))
                if row[0] != row[1]
            }
            grouped = {}
            for row in connection.execute(text('''
                SELECT id, requester_id, addressee_id, status
                FROM friendship ORDER BY id
            ''')).mappings():
                pair = tuple(sorted((row['requester_id'], row['addressee_id'])))
                grouped.setdefault(pair, []).append(dict(row))

            for pair, rows in grouped.items():
                invalid = (
                    pair[0] == pair[1]
                    or pair[0] not in active_users
                    or pair[1] not in active_users
                    or pair in blocked_pairs
                )
                if invalid:
                    for row in rows:
                        connection.execute(
                            text('DELETE FROM friendship WHERE id = :id'),
                            {'id': row['id']},
                        )
                    continue

                accepted = [row for row in rows if row['status'] == 'accepted']
                directions = {
                    (row['requester_id'], row['addressee_id']) for row in rows
                }
                keeper = min(accepted or rows, key=lambda row: row['id'])
                final_status = 'accepted' if accepted or len(directions) > 1 else 'pending'
                for row in rows:
                    if row['id'] != keeper['id']:
                        connection.execute(
                            text('DELETE FROM friendship WHERE id = :id'),
                            {'id': row['id']},
                        )
                connection.execute(text('''
                    UPDATE friendship SET status = :status
                    WHERE id = :id
                '''), {'status': final_status, 'id': keeper['id']})

            definition = _friendship_pair_index_definition(connection)
            if not _friendship_pair_index_is_exact(definition):
                if definition:
                    connection.execute(text(
                        f'DROP INDEX "{FRIENDSHIP_PAIR_INDEX_NAME}"'
                    ))
                # PostgreSQL requires non-function expression-index columns to
                # be wrapped individually.  SQLite accepts the unwrapped CASE
                # form used by the local migration path.
                index_columns = (
                    f'({low_expr}), ({high_expr})'
                    if db.engine.dialect.name == 'postgresql'
                    else f'{low_expr}, {high_expr}'
                )
                connection.execute(text(
                    f'CREATE UNIQUE INDEX "{FRIENDSHIP_PAIR_INDEX_NAME}" '
                    f'ON friendship ({index_columns})'
                ))

            duplicates = connection.execute(text(f'''
                SELECT COUNT(*) FROM (
                    SELECT pair_low, pair_high, COUNT(*) AS pair_count
                    FROM (
                        SELECT {low_expr} AS pair_low,
                               {high_expr} AS pair_high
                        FROM friendship
                    ) AS normalized_pairs
                    GROUP BY pair_low, pair_high
                ) AS grouped_pairs
                WHERE pair_count > 1 OR pair_low = pair_high
            ''')).scalar()
            verified = _friendship_pair_index_definition(connection)
            if duplicates or not _friendship_pair_index_is_exact(verified):
                raise RuntimeError(
                    'Friendship pair verification failed: expected one unique '
                    'unordered row per two users'
                )
    except Exception:
        app.logger.exception('Friendship pair migration failed')
        raise


def _game_attempt_index_is_exact(index):
    """Whether an inspected index enforces precisely the creator/key scope."""
    if not index or not index.get('unique'):
        return False
    if tuple(index.get('column_names') or ()) != GAME_ATTEMPT_INDEX_COLUMNS:
        return False
    # A partial unique index could leave some creator/key pairs unprotected.
    options = index.get('dialect_options') or {}
    if not all(
        options.get(option) is None
        for option in ('postgresql_where', 'sqlite_where')
    ):
        return False
    # Omitted client_attempt_id values must remain independently insertable.
    # PostgreSQL NULLS NOT DISTINCT would allow only one NULL key per creator.
    return not bool(options.get('postgresql_nulls_not_distinct'))


def _ensure_game_attempt_index(app):
    """Repair and then verify the exact game-create idempotency index.

    ``CREATE INDEX IF NOT EXISTS`` is intentionally avoided: an old index with
    the right name but the wrong shape must be replaced, not silently accepted.
    """
    from sqlalchemy import inspect as sa_inspect, text

    inspector = sa_inspect(db.engine)
    if 'game' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('game')}
    required_columns = {
        'creator_id', 'client_attempt_id', 'client_attempt_fingerprint',
    }
    missing = required_columns - columns
    if missing:
        raise RuntimeError(
            'Game idempotency schema is incomplete; missing columns: '
            + ', '.join(sorted(missing))
        )

    named_index = next(
        (
            index for index in inspector.get_indexes('game')
            if index.get('name') == GAME_ATTEMPT_INDEX_NAME
        ),
        None,
    )
    if not _game_attempt_index_is_exact(named_index):
        app.logger.warning(
            'Repairing game idempotency index %s', GAME_ATTEMPT_INDEX_NAME,
        )
        try:
            with db.engine.begin() as conn:
                if named_index is not None:
                    conn.execute(text(f'DROP INDEX "{GAME_ATTEMPT_INDEX_NAME}"'))
                conn.execute(text(
                    f'CREATE UNIQUE INDEX "{GAME_ATTEMPT_INDEX_NAME}" '
                    'ON game (creator_id, client_attempt_id)'
                ))
        except Exception:
            # Concurrent app boots can both observe a missing index. Accept the
            # DDL race only when a fresh inspection proves the other boot
            # installed exactly the invariant we require; otherwise fail boot.
            raced_index = next(
                (
                    index for index in sa_inspect(db.engine).get_indexes('game')
                    if index.get('name') == GAME_ATTEMPT_INDEX_NAME
                ),
                None,
            )
            if not _game_attempt_index_is_exact(raced_index):
                raise

    # Re-inspect rather than trusting successful DDL: this catches dialect or
    # legacy-schema surprises before the app accepts idempotent writes.
    verified = next(
        (
            index for index in sa_inspect(db.engine).get_indexes('game')
            if index.get('name') == GAME_ATTEMPT_INDEX_NAME
        ),
        None,
    )
    if not _game_attempt_index_is_exact(verified):
        raise RuntimeError(
            'Game idempotency index verification failed: expected unique '
            '(creator_id, client_attempt_id)'
        )


def _message_attempt_index_is_exact(index):
    """Whether the message retry index protects precisely one sender/key pair."""
    if not index or not index.get('unique'):
        return False
    if tuple(index.get('column_names') or ()) != MESSAGE_ATTEMPT_INDEX_COLUMNS:
        return False
    options = index.get('dialect_options') or {}
    if not all(
        options.get(option) is None
        for option in ('postgresql_where', 'sqlite_where')
    ):
        return False
    return not bool(options.get('postgresql_nulls_not_distinct'))


def _message_attempt_conflicting_uniques(inspector, table_name='message'):
    """Legacy uniqueness that would reject two senders using the same key."""
    candidates = [
        index for index in inspector.get_indexes(table_name)
        if index.get('unique')
    ]
    candidates.extend(inspector.get_unique_constraints(table_name))
    conflicts = []
    for candidate in candidates:
        columns = set(candidate.get('column_names') or ())
        if 'client_attempt_id' in columns and 'sender_id' not in columns:
            conflicts.append(candidate.get('name') or '<unnamed>')
    return sorted(set(conflicts))


def _ensure_message_attempt_index(app):
    """Install and verify durable message-send idempotency on every database."""
    from sqlalchemy import inspect as sa_inspect, text

    inspector = sa_inspect(db.engine)
    if 'message' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('message')}
    required_columns = {
        'sender_id', 'client_attempt_id', 'client_attempt_fingerprint',
    }
    missing = required_columns - columns
    if missing:
        raise RuntimeError(
            'Message idempotency schema is incomplete; missing columns: '
            + ', '.join(sorted(missing))
        )
    conflicts = _message_attempt_conflicting_uniques(inspector)
    if conflicts:
        raise RuntimeError(
            'Message idempotency schema has sender-global unique indexes or '
            'constraints on client_attempt_id: ' + ', '.join(conflicts)
        )

    named_index = next(
        (
            index for index in inspector.get_indexes('message')
            if index.get('name') == MESSAGE_ATTEMPT_INDEX_NAME
        ),
        None,
    )
    if not _message_attempt_index_is_exact(named_index):
        app.logger.warning(
            'Repairing message idempotency index %s', MESSAGE_ATTEMPT_INDEX_NAME,
        )
        try:
            with db.engine.begin() as conn:
                if named_index is not None:
                    conn.execute(text(f'DROP INDEX "{MESSAGE_ATTEMPT_INDEX_NAME}"'))
                conn.execute(text(
                    f'CREATE UNIQUE INDEX "{MESSAGE_ATTEMPT_INDEX_NAME}" '
                    'ON message (sender_id, client_attempt_id)'
                ))
        except Exception:
            raced_index = next(
                (
                    index for index in sa_inspect(db.engine).get_indexes('message')
                    if index.get('name') == MESSAGE_ATTEMPT_INDEX_NAME
                ),
                None,
            )
            if not _message_attempt_index_is_exact(raced_index):
                raise

    verified = next(
        (
            index for index in sa_inspect(db.engine).get_indexes('message')
            if index.get('name') == MESSAGE_ATTEMPT_INDEX_NAME
        ),
        None,
    )
    if not _message_attempt_index_is_exact(verified):
        raise RuntimeError(
            'Message idempotency index verification failed: expected unique '
            '(sender_id, client_attempt_id)'
        )


def _ensure_message_send_attempt_schema(app):
    """Fail boot unless the durable message ledger can reserve every key."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(db.engine)
    if 'message_send_attempt' not in inspector.get_table_names():
        raise RuntimeError('Message send-attempt ledger table is missing')
    columns = {
        column['name']
        for column in inspector.get_columns('message_send_attempt')
    }
    missing = MESSAGE_SEND_ATTEMPT_COLUMNS - columns
    if missing:
        raise RuntimeError(
            'Message send-attempt ledger is incomplete; missing columns: '
            + ', '.join(sorted(missing))
        )
    unique_shapes = {
        tuple(item.get('column_names') or ())
        for item in inspector.get_unique_constraints('message_send_attempt')
    }
    for index in inspector.get_indexes('message_send_attempt'):
        if not index.get('unique'):
            continue
        options = index.get('dialect_options') or {}
        if any(
            options.get(option) is not None
            for option in ('postgresql_where', 'sqlite_where')
        ):
            continue
        unique_shapes.add(tuple(index.get('column_names') or ()))
    if MESSAGE_SEND_ATTEMPT_UNIQUE_COLUMNS not in unique_shapes:
        raise RuntimeError(
            'Message send-attempt ledger verification failed: expected unique '
            '(sender_id, client_attempt_id)'
        )
    conflicts = _message_attempt_conflicting_uniques(
        inspector, 'message_send_attempt',
    )
    if conflicts:
        raise RuntimeError(
            'Message send-attempt ledger has sender-global unique indexes or '
            'constraints on client_attempt_id: ' + ', '.join(conflicts)
        )

    # A partial or failed backfill would reopen the delete/retry race for those
    # older keyed rows even though the table itself looks valid.
    from backend.models import Message, MessageSendAttempt
    unreserved = (
        db.session.query(Message.id)
        .outerjoin(
            MessageSendAttempt,
            db.and_(
                MessageSendAttempt.sender_id == Message.sender_id,
                MessageSendAttempt.client_attempt_id == Message.client_attempt_id,
            ),
        )
        .filter(
            Message.client_attempt_id.isnot(None),
            MessageSendAttempt.id.is_(None),
        )
        .first()
    )
    if unreserved:
        raise RuntimeError(
            'Message send-attempt ledger backfill is incomplete for keyed '
            f'message {unreserved[0]}'
        )


def _ensure_notification_unread_dedupe_index(app):
    """Verify concurrent room messages collapse to one unread notification."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(db.engine)
    if 'notification' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('notification')}
    if 'unread_dedupe_key' not in columns:
        raise RuntimeError('Notification unread dedupe column is missing')
    index = next(
        (
            item for item in inspector.get_indexes('notification')
            if item.get('name') == NOTIFICATION_DEDUPE_INDEX_NAME
        ),
        None,
    )
    if (
        not index
        or not index.get('unique')
        or tuple(index.get('column_names') or ())
        != NOTIFICATION_DEDUPE_INDEX_COLUMNS
    ):
        raise RuntimeError(
            'Notification unread dedupe verification failed: expected unique '
            '(user_id, unread_dedupe_key)'
        )


def _verify_database_health():
    """Prove the active connection can see this application's schema."""
    from sqlalchemy import text

    if db.engine.dialect.name == 'postgresql':
        active_schema = db.session.execute(
            text('SELECT current_schema()')
        ).scalar_one_or_none()
        if active_schema != PG_SCHEMA:
            raise RuntimeError('Application database schema is unavailable')
    # ``SELECT 1`` alone stays green for an empty or misrouted database.  A
    # bounded read from a required base table also verifies migration/search
    # path readiness without depending on that table containing any rows.
    db.session.execute(text('SELECT 1 FROM "user" LIMIT 1')).first()


def create_app(config_name=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(get_config(config_name))

    # Never run production with a guessable signing key or ephemeral SQLite.
    from backend.config import DEV_FALLBACK_SECRET
    production_secret = app.config.get('SECRET_KEY')
    production_secret_bytes = (
        production_secret.encode('utf8')
        if isinstance(production_secret, str) else b''
    )
    if app.config.get('APP_ENV') == 'production' and (
        production_secret in (None, '', 'change-me', DEV_FALLBACK_SECRET)
        or len(production_secret_bytes) < 32
    ):
        raise RuntimeError(
            'SECRET_KEY must be set to at least 32 UTF-8 bytes in production '
            '(known development defaults are not allowed).'
        )
    if app.config.get('APP_ENV') == 'production' and \
            app.config.get('SQLALCHEMY_DATABASE_URI', '').startswith('sqlite:'):
        raise RuntimeError(
            'DATABASE_URL must point to PostgreSQL in production '
            '(hosted container filesystems are not persistent).'
        )
    if app.config.get('APP_ENV') == 'production':
        from cryptography.fernet import Fernet
        mfa_key = str(app.config.get('MFA_ENCRYPTION_KEY') or '').strip()
        try:
            Fernet(mfa_key.encode('ascii'))
        except (TypeError, ValueError):
            raise RuntimeError(
                'MFA_ENCRYPTION_KEY must be a valid Fernet key in production.'
            )
    if (
        app.config.get('APP_ENV') == 'production'
        and app.config.get('SERVERLESS_RUNTIME')
    ):
        unsafe_schema_flags = [
            name for name in (
                'SCHEMA_MANAGEMENT_ENABLED', 'AUTO_CREATE_DB',
                'RESET_DB_ON_BOOT', 'AUTO_SEED_COURTS',
            )
            if app.config.get(name)
        ]
        if unsafe_schema_flags:
            raise RuntimeError(
                'Production serverless runtime forbids database startup '
                'mutations: ' + ', '.join(unsafe_schema_flags)
            )
        if (
            app.config.get('RATE_LIMIT_ENABLED', True)
            and app.config.get('RATE_LIMIT_BACKEND') != 'database'
        ):
            raise RuntimeError(
                'Production serverless runtime requires '
                'RATE_LIMIT_BACKEND=database.'
            )

    db.init_app(app)
    _register_blueprints(app)

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        return resp

    with app.app_context():
        if app.config.get('SCHEMA_MANAGEMENT_ENABLED'):
            _ensure_pg_schema(app)
            _migrate_legacy_schema(app)
            _clear_conflicting_legacy_indexes(app)
            _upgrade_schema(app)
            if app.config.get('RESET_DB_ON_BOOT'):
                # One-time escape hatch for migrating off an old schema:
                # set RESET_DB_ON_BOOT=true, deploy, then REMOVE the env var.
                app.logger.warning(
                    'RESET_DB_ON_BOOT set — dropping and recreating all tables'
                )
                db.drop_all()
                db.create_all()
            elif app.config.get('AUTO_CREATE_DB'):
                db.create_all()
            # Canonical Group/Conversation persistence is additive and
            # backfills the six legacy room scopes without removing any
            # rollback-compatible columns or marker tables.
            from backend.services.conversation_migration import (
                ensure_canonical_communication_schema,
            )
            ensure_canonical_communication_schema(app)
            _drop_obsolete_game_arrival_index(app)
            _ensure_active_checkin_index(app)
            _ensure_friendship_pair_index(app)
            _ensure_game_attempt_index(app)
            _ensure_message_attempt_index(app)
            _ensure_message_send_attempt_schema(app)
            _ensure_notification_unread_dedupe_index(app)
            _maybe_auto_seed(app)
        elif app.config.get('RESET_DB_ON_BOOT'):
            raise RuntimeError(
                'RESET_DB_ON_BOOT cannot be used when schema management is disabled.'
            )

    @app.get('/health')
    def health():
        try:
            _verify_database_health()
            db_ok = True
        except Exception:
            app.logger.exception('Health check: database unreachable')
            db_ok = False
        return jsonify({
            'status': 'ok' if db_ok else 'degraded',
            'db': db_ok,
            'env': app.config.get('APP_ENV'),
        }), (200 if db_ok else 503)

    @app.get('/')
    def index():
        return send_from_directory(PUBLIC_DIR, 'index.html')

    # Short share links with Open Graph tags: chat apps render a rich preview
    # (crawlers can't read #hash routes), humans get bounced into the app.
    def _share_page(title, description, target_hash):
        from markupsafe import escape
        icon = request.url_root.rstrip('/') + '/icon-512.png'
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title>'
            f'<meta property="og:title" content="{escape(title)}">'
            f'<meta property="og:description" content="{escape(description)}">'
            f'<meta property="og:image" content="{escape(icon)}">'
            '<meta property="og:site_name" content="Third Shot">'
            '<meta name="twitter:card" content="summary">'
            f'<meta http-equiv="refresh" content="0;url=/{escape(target_hash)}">'
            f'<script>location.replace("/{escape(target_hash)}")</script>'
            '</head><body>Opening Third Shot…</body></html>'
        )

    @app.get('/g/<int:game_id>')
    def share_game(game_id):
        from backend.models import Game
        game = db.session.get(Game, game_id)
        if not game:
            return 'not found', 404
        if game.visibility == 'private' or (
            game.is_instant and game.status != 'completed'
        ):
            # Don't leak invite-only details or a live rally's exact physical
            # location to anonymous link crawlers / enumerable share URLs.
            # Cancelled/expired instant IDs remain protected; only completed
            # results transition to ordinary historical visibility.
            return _share_page('A pickleball game on Third Shot',
                               'Open the link to see the details.', f'#game/{game_id}')
        court = game.court.name if game.court else 'the court'
        # A shared RESULT should preview as a result, not a stale invitation.
        if game.status == 'completed' and game.score_team1 is not None:
            return _share_page(f'Final: {game.score_team1}–{game.score_team2} at {court}',
                               'A pickleball match on Third Shot', f'#game/{game_id}')
        if game.status == 'completed':
            player_count = len(game.players)
            return _share_page(
                f'Play session complete at {court}',
                f'{player_count}-player pickleball session on Third Shot',
                f'#game/{game_id}',
            )
        if game.status == 'cancelled':
            return _share_page('A pickleball game on Third Shot',
                               'This game was cancelled.', f'#game/{game_id}')
        when = game.scheduled_at.strftime('%a, %b %-d · %-I:%M %p UTC') if game.scheduled_at else ''
        return _share_page(f'Pickleball at {court}',
                           f'{when} — join on Third Shot'.strip(' —'), f'#game/{game_id}')

    @app.get('/c/<int:court_id>')
    def share_court(court_id):
        from backend.models import Court
        court = db.session.get(Court, court_id)
        if not court:
            return 'not found', 404
        bits = [f'{court.num_courts} court{"" if court.num_courts == 1 else "s"}']
        if court.city:
            bits.insert(0, court.city)
        return _share_page(court.name, ' · '.join(bits) + ' — on Third Shot', f'#court/{court_id}')

    @app.get('/u/<int:user_id>')
    def share_invite(user_id):
        from backend.models import User
        from backend.services.player_invites import invite_destination_game
        user = db.session.get(User, user_id)
        if not user or user.deleted_at:
            return 'not found', 404
        game = invite_destination_game(user.id, request.args.get('game'))
        if game:
            court = game.court.name if game.court else 'the court'
            when = game.scheduled_at.strftime(
                '%a, %b %-d · %-I:%M %p UTC',
            )
            return _share_page(
                f'Play pickleball with {user.display_name}',
                f'Join them {when} at {court} on Third Shot.',
                f'#invite/{user_id}/game/{game.id}',
            )
        # Invalid, private, unrelated, or dead destinations safely retain the
        # inviter attribution and explicit friend-request consent flow.
        return _share_page(
            f'Play pickleball with {user.display_name}',
            'Join them on Third Shot — courts, players & games near you.',
            f'#invite/{user_id}',
        )

    @app.get('/cl/<int:club_id>')
    def share_club(club_id):
        from backend.models import Club
        club = db.session.get(Club, club_id)
        if not club or club.archived_at is not None:
            return 'not found', 404
        n = len(club.members)
        return _share_page(f'🏛 {club.name}',
                           f'{n} member{"" if n == 1 else "s"} — join the community on Third Shot',
                           f'#club/{club_id}')

    @app.get('/t/<int:tournament_id>')
    def share_tournament(tournament_id):
        from backend.models import Tournament
        t = db.session.get(Tournament, tournament_id)
        if not t:
            return 'not found', 404
        court = t.court.name if t.court else 'the court'
        return _share_page(f'🏆 {t.name}',
                           f'Pickleball tournament at {court} — register on Third Shot',
                           f'#tournament/{tournament_id}')

    @app.get('/l/<int:league_id>')
    def share_league(league_id):
        from backend.models import League
        league = db.session.get(League, league_id)
        if not league:
            return 'not found', 404
        court = league.court.name if league.court else 'the court'
        return _share_page(
            f'🏆 {league.name}',
            f'Pickleball league at {court} — join on Third Shot',
            f'#league/{league_id}',
        )

    @app.get('/api/share-preview')
    def share_preview():
        """Return a deliberately small, anonymous-safe preview for hash links.

        Fragment identifiers never reach the server, so the signed-out shell
        asks for this public summary after it has parsed the hash. Private game
        details and private room/group names remain hidden until authentication.
        """
        try:
            entity_id = int(request.args.get('id', ''))
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid share target'}), 400
        if entity_id <= 0:
            return jsonify({'error': 'invalid share target'}), 400
        kind = str(request.args.get('kind') or '').strip().lower()
        title = ''
        subtitle = ''
        cache_publicly = True

        if kind == 'court':
            from backend.models import Court
            court = db.session.get(Court, entity_id)
            if not court or court.closed:
                return jsonify({'error': 'share target not found'}), 404
            title = court.name
            facts = []
            if court.city:
                facts.append(court.city)
            facts.append(f'{court.num_courts} court{"" if court.num_courts == 1 else "s"}')
            if court.fee_type:
                facts.append(court.fee_type.replace('_', ' ').title())
            subtitle = ' · '.join(facts)
        elif kind == 'game':
            from backend.models import Game
            game = db.session.get(Game, entity_id)
            if not game:
                return jsonify({'error': 'share target not found'}), 404
            if game.visibility == 'private' or (game.is_instant and game.status != 'completed'):
                title = 'A private play session was shared with you'
                subtitle = 'Details stay private until you log in.'
                cache_publicly = False
            else:
                court_name = game.court.name if game.court else 'a local court'
                title = game.title or f'Pickleball at {court_name}'
                when = game.scheduled_at.strftime('%a, %b %d · %-I:%M %p UTC') if game.scheduled_at else ''
                state_label = {
                    'completed': 'Completed play session',
                    'cancelled': 'Cancelled play session',
                }.get(game.status, 'Pickup game')
                subtitle = ' · '.join(part for part in (state_label, court_name, when) if part)
        elif kind == 'tournament':
            from backend.models import Tournament
            tournament = db.session.get(Tournament, entity_id)
            if not tournament:
                return jsonify({'error': 'share target not found'}), 404
            title = tournament.name
            subtitle = ' · '.join(part for part in (
                'Tournament', tournament.court.name if tournament.court else '',
                tournament.starts_at.strftime('%a, %b %d') if tournament.starts_at else '',
            ) if part)
        elif kind == 'league':
            from backend.models import League
            league = db.session.get(League, entity_id)
            if not league:
                return jsonify({'error': 'share target not found'}), 404
            title = league.name
            subtitle = ' · '.join(part for part in (
                'League', league.court.name if league.court else '',
                league.starts_at.strftime('%a, %b %d') if league.starts_at else '',
            ) if part)
        elif kind == 'club':
            from backend.models import Club
            club = db.session.get(Club, entity_id)
            if not club or club.archived_at is not None:
                return jsonify({'error': 'share target not found'}), 404
            title = club.name
            subtitle = f'Community · {len(club.members)} member{"" if len(club.members) == 1 else "s"}'
            if club.home_court:
                subtitle += f' · {club.home_court.name}'
        elif kind == 'player':
            from backend.models import User
            user = db.session.get(User, entity_id)
            if not user or user.deleted_at or user.suspended_at:
                return jsonify({'error': 'share target not found'}), 404
            title = user.display_name
            subtitle = 'Player profile on Third Shot'
            if user.skill_rating is not None:
                subtitle += f' · Self-rated {user.skill_rating:.1f}'
        elif kind in {'crew', 'chat'}:
            # These are private, membership-scoped destinations. Do not expose
            # whether an enumerable id exists, let alone its participants.
            title = 'A private group was shared with you' if kind == 'crew' else 'A conversation was shared with you'
            subtitle = 'Details stay private until you log in.'
            cache_publicly = False
        else:
            return jsonify({'error': 'unsupported share target'}), 400

        response = jsonify({'title': title, 'subtitle': subtitle})
        response.headers['Cache-Control'] = (
            'public, max-age=60' if cache_publicly else 'private, no-store'
        )
        return response

    # Executable shell assets use release-specific pathnames. The previous
    # service worker ignored query strings during offline fallback, so a query
    # alone could mix an old bundle with new HTML during deployment.
    @app.get('/app-v15.js')
    def frontend_app_v15():
        return send_from_directory(PUBLIC_DIR, 'app-v15.js')

    @app.get('/styles-v15.css')
    def frontend_styles_v15():
        return send_from_directory(PUBLIC_DIR, 'styles-v15.css')

    @app.get('/assets/<release>/<path:filename>')
    def frontend_release_asset(release, filename):
        """Serve immutable release assets with the best precompressed body."""
        if release != FRONTEND_RELEASE or filename not in FRONTEND_RELEASE_FILES:
            return 'not found', 404
        release_dir = os.path.join(PUBLIC_DIR, 'assets', FRONTEND_RELEASE)
        selected_encoding = request.accept_encodings.best_match(
            ['br', 'gzip', 'identity'],
        ) or 'identity'
        served_name = filename
        content_encoding = None
        if selected_encoding == 'br' and os.path.isfile(os.path.join(release_dir, f'{filename}.br')):
            served_name = f'{filename}.br'
            content_encoding = 'br'
        elif selected_encoding == 'gzip' and os.path.isfile(os.path.join(release_dir, f'{filename}.gz')):
            served_name = f'{filename}.gz'
            content_encoding = 'gzip'
        response = send_from_directory(
            release_dir,
            served_name,
            mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
        )
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        response.headers['Vary'] = 'Accept-Encoding'
        if content_encoding:
            response.headers['Content-Encoding'] = content_encoding
        return response

    @app.get('/<path:filename>')
    def frontend_assets(filename):
        return send_from_directory(PUBLIC_DIR, filename)

    return app


def _register_blueprints(app):
    from backend.routes.auth import auth_bp
    from backend.routes.business_governance import business_governance_bp
    from backend.routes.business_integrations import (
        business_integration_cron_bp,
        business_integrations_bp,
    )
    from backend.routes.businesses import businesses_bp
    from backend.routes.chat import chat_bp
    from backend.routes.clubs import clubs_bp
    from backend.routes.crews import crews_bp
    from backend.routes.leagues import leagues_bp
    from backend.routes.maintenance import maintenance_bp
    from backend.routes.moderation import moderation_bp
    from backend.routes.push import push_bp
    from backend.routes.courts import courts_bp
    from backend.routes.games import games_bp
    from backend.routes.social import social_bp
    from backend.routes.tournaments import tournaments_bp

    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(businesses_bp, url_prefix='/api')
    app.register_blueprint(business_governance_bp, url_prefix='/api')
    app.register_blueprint(business_integrations_bp, url_prefix='/api')
    app.register_blueprint(business_integration_cron_bp, url_prefix='/api')
    app.register_blueprint(courts_bp, url_prefix='/api')
    app.register_blueprint(games_bp, url_prefix='/api')
    app.register_blueprint(social_bp, url_prefix='/api')
    app.register_blueprint(chat_bp, url_prefix='/api')
    app.register_blueprint(tournaments_bp, url_prefix='/api')
    app.register_blueprint(clubs_bp, url_prefix='/api')
    app.register_blueprint(crews_bp, url_prefix='/api')
    app.register_blueprint(push_bp, url_prefix='/api')
    app.register_blueprint(leagues_bp, url_prefix='/api')
    app.register_blueprint(maintenance_bp, url_prefix='/api')
    app.register_blueprint(moderation_bp, url_prefix='/api')


app = create_app()
