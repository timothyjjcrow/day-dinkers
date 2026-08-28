"""Flask application bootstrap."""
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy

from backend.config import get_config

db = SQLAlchemy(session_options={'expire_on_commit': False})

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(PROJECT_ROOT, 'public')
BUNDLED_COURTS_FILE = os.path.join(PROJECT_ROOT, 'data', 'courts.json.gz')


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


def _upgrade_schema(app):
    """Tiny additive migrations for existing databases (create_all only builds
    brand-new tables, it never alters existing ones)."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()
        is_postgres = db.engine.dialect.name == 'postgresql'
        statements = []

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
            if 'league_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN league_id INTEGER')
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

        if 'user' in tables:
            user_cols = {c['name'] for c in inspector.get_columns('user')}
            for col, ddl in (
                ('last_lat', 'ALTER TABLE "user" ADD COLUMN last_lat DOUBLE PRECISION'),
                ('last_lng', 'ALTER TABLE "user" ADD COLUMN last_lng DOUBLE PRECISION'),
                ('last_location_at', 'ALTER TABLE "user" ADD COLUMN last_location_at TIMESTAMP'),
                ('last_active_at', 'ALTER TABLE "user" ADD COLUMN last_active_at TIMESTAMP'),
                ('last_games_digest_week', 'ALTER TABLE "user" ADD COLUMN last_games_digest_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('last_streak_nag_week', 'ALTER TABLE "user" ADD COLUMN last_streak_nag_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('home_lat', 'ALTER TABLE "user" ADD COLUMN home_lat DOUBLE PRECISION'),
                ('home_lng', 'ALTER TABLE "user" ADD COLUMN home_lng DOUBLE PRECISION'),
                ('home_area', 'ALTER TABLE "user" ADD COLUMN home_area VARCHAR(120)'),
                ('avatar_url', "ALTER TABLE \"user\" ADD COLUMN avatar_url VARCHAR(500) NOT NULL DEFAULT ''"),
                ('deleted_at', 'ALTER TABLE "user" ADD COLUMN deleted_at TIMESTAMP'),
                ('availability', 'ALTER TABLE "user" ADD COLUMN availability TEXT NOT NULL DEFAULT \'[]\''),
                ('last_recap_week', 'ALTER TABLE "user" ADD COLUMN last_recap_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('muted_notifications', 'ALTER TABLE "user" ADD COLUMN muted_notifications TEXT NOT NULL DEFAULT \'[]\''),
                ('notified_badges', 'ALTER TABLE "user" ADD COLUMN notified_badges TEXT NOT NULL DEFAULT \'[]\''),
                ('calendar_token', 'ALTER TABLE "user" ADD COLUMN calendar_token VARCHAR(64)'),
                ('best_rating', 'ALTER TABLE "user" ADD COLUMN best_rating INTEGER NOT NULL DEFAULT 1200'),
            ):
                if col not in user_cols:
                    # SQLite uses FLOAT/DATETIME; Postgres accepts these too.
                    statements.append(ddl if is_postgres else ddl
                                      .replace('DOUBLE PRECISION', 'FLOAT')
                                      .replace('TIMESTAMP', 'DATETIME'))

        if 'game' in tables:
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
            if 'club_id' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN club_id INTEGER')
            if 'preferred_level' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN preferred_level VARCHAR(16) NOT NULL DEFAULT 'any'"
                )
            if 'client_attempt_id' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN client_attempt_id VARCHAR(64)'
                )
            if 'client_attempt_fingerprint' not in game_cols:
                statements.append(
                    'ALTER TABLE game ADD COLUMN client_attempt_fingerprint VARCHAR(64)'
                )

        if 'court' in tables:
            court_cols = {c['name'] for c in inspector.get_columns('court')}
            if 'photo_data' not in court_cols:
                statements.append('ALTER TABLE court ADD COLUMN photo_data TEXT')
            if 'hours' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN hours VARCHAR(255) NOT NULL DEFAULT ''"
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

        if 'tournament_entry' in tables:
            te_cols = {c['name'] for c in inspector.get_columns('tournament_entry')}
            if 'checked_in_at' not in te_cols:
                statements.append(
                    'ALTER TABLE tournament_entry ADD COLUMN checked_in_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
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
                        'club_id', 'league_id',
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
    except Exception:
        db.session.rollback()
        app.logger.exception('Schema upgrade failed')


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


def create_app(config_name=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(get_config(config_name))

    # Never run production with a guessable signing key or ephemeral SQLite.
    from backend.config import DEV_FALLBACK_SECRET
    if app.config.get('APP_ENV') == 'production' and \
            app.config.get('SECRET_KEY') in (None, '', 'change-me', DEV_FALLBACK_SECRET):
        raise RuntimeError(
            'SECRET_KEY must be set to a strong value in production '
            '(the dev fallback is not allowed).'
        )
    if app.config.get('APP_ENV') == 'production' and \
            app.config.get('SQLALCHEMY_DATABASE_URI', '').startswith('sqlite:'):
        raise RuntimeError(
            'DATABASE_URL must point to PostgreSQL in production '
            '(hosted container filesystems are not persistent).'
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
        # A cheap DB ping keeps the hosting health check honest.
        from sqlalchemy import text
        try:
            db.session.execute(text('SELECT 1'))
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
        if game.visibility == 'private':
            # Don't leak details of invite-only games to link crawlers.
            return _share_page('A pickleball game on Third Shot',
                               'Open the link to see the details.', f'#game/{game_id}')
        court = game.court.name if game.court else 'the court'
        # A shared RESULT should preview as a result, not a stale invitation.
        if game.status == 'completed' and game.score_team1 is not None:
            return _share_page(f'Final: {game.score_team1}–{game.score_team2} at {court}',
                               'A pickleball match on Third Shot', f'#game/{game_id}')
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
        user = db.session.get(User, user_id)
        if not user or user.deleted_at:
            return 'not found', 404
        # #invite/<id> keeps the signup friend-request flow intact.
        return _share_page(f'Play pickleball with {user.display_name}',
                           'Join them on Third Shot — courts, players & games near you.',
                           f'#invite/{user_id}')

    @app.get('/cl/<int:club_id>')
    def share_club(club_id):
        from backend.models import Club
        club = db.session.get(Club, club_id)
        if not club:
            return 'not found', 404
        n = len(club.members)
        return _share_page(f'🏛 {club.name}',
                           f'{n} member{"" if n == 1 else "s"} — join the club on Third Shot',
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

    # Executable shell assets use release-specific pathnames. The previous
    # service worker ignored query strings during offline fallback, so a query
    # alone could mix an old bundle with new HTML during deployment.
    @app.get('/app-v13.js')
    def frontend_app_v13():
        return send_from_directory(PUBLIC_DIR, 'app-v13.js')

    @app.get('/styles-v13.css')
    def frontend_styles_v13():
        return send_from_directory(PUBLIC_DIR, 'styles-v13.css')

    @app.get('/<path:filename>')
    def frontend_assets(filename):
        return send_from_directory(PUBLIC_DIR, filename)

    return app


def _register_blueprints(app):
    from backend.routes.auth import auth_bp
    from backend.routes.chat import chat_bp
    from backend.routes.clubs import clubs_bp
    from backend.routes.leagues import leagues_bp
    from backend.routes.push import push_bp
    from backend.routes.courts import courts_bp
    from backend.routes.games import games_bp
    from backend.routes.social import social_bp
    from backend.routes.tournaments import tournaments_bp

    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(courts_bp, url_prefix='/api')
    app.register_blueprint(games_bp, url_prefix='/api')
    app.register_blueprint(social_bp, url_prefix='/api')
    app.register_blueprint(chat_bp, url_prefix='/api')
    app.register_blueprint(tournaments_bp, url_prefix='/api')
    app.register_blueprint(clubs_bp, url_prefix='/api')
    app.register_blueprint(push_bp, url_prefix='/api')
    app.register_blueprint(leagues_bp, url_prefix='/api')


app = create_app()
