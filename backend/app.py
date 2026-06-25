"""Flask application bootstrap."""
import os
import threading
import time

from flask import Flask, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy

from backend.config import get_config

db = SQLAlchemy(session_options={'expire_on_commit': False})

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
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

        if 'user' in tables:
            user_cols = {c['name'] for c in inspector.get_columns('user')}
            for col, ddl in (
                ('last_lat', 'ALTER TABLE "user" ADD COLUMN last_lat DOUBLE PRECISION'),
                ('last_lng', 'ALTER TABLE "user" ADD COLUMN last_lng DOUBLE PRECISION'),
                ('last_location_at', 'ALTER TABLE "user" ADD COLUMN last_location_at TIMESTAMP'),
                ('home_lat', 'ALTER TABLE "user" ADD COLUMN home_lat DOUBLE PRECISION'),
                ('home_lng', 'ALTER TABLE "user" ADD COLUMN home_lng DOUBLE PRECISION'),
                ('home_area', 'ALTER TABLE "user" ADD COLUMN home_area VARCHAR(120)'),
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

        if statements:
            app.logger.warning('Applying schema upgrades: %s', statements)
            with db.engine.begin() as conn:
                for statement in statements:
                    conn.execute(text(statement))
    except Exception:
        app.logger.exception('Schema upgrade failed')


def create_app(config_name=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(get_config(config_name))
    db.init_app(app)
    _register_blueprints(app)

    with app.app_context():
        _ensure_pg_schema(app)
        _migrate_legacy_schema(app)
        _clear_conflicting_legacy_indexes(app)
        _upgrade_schema(app)
        if app.config.get('RESET_DB_ON_BOOT'):
            # One-time escape hatch for migrating off an old schema:
            # set RESET_DB_ON_BOOT=true, deploy, then REMOVE the env var.
            app.logger.warning('RESET_DB_ON_BOOT set — dropping and recreating all tables')
            db.drop_all()
            db.create_all()
        elif app.config.get('AUTO_CREATE_DB'):
            db.create_all()
        _maybe_auto_seed(app)

    @app.get('/health')
    def health():
        return jsonify({'status': 'ok', 'env': app.config.get('APP_ENV')})

    @app.get('/')
    def index():
        return send_from_directory(FRONTEND_DIR, 'index.html')

    @app.get('/<path:filename>')
    def frontend_assets(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    return app


def _register_blueprints(app):
    from backend.routes.auth import auth_bp
    from backend.routes.chat import chat_bp
    from backend.routes.courts import courts_bp
    from backend.routes.games import games_bp
    from backend.routes.social import social_bp

    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(courts_bp, url_prefix='/api')
    app.register_blueprint(games_bp, url_prefix='/api')
    app.register_blueprint(social_bp, url_prefix='/api')
    app.register_blueprint(chat_bp, url_prefix='/api')


app = create_app()
